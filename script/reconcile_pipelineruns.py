#!/usr/bin/env python3
"""Preview or locally apply template conventions without Git/network mutations."""

import argparse
from collections import Counter
from copy import deepcopy
from decimal import Decimal
import difflib
from io import StringIO
import json
from pathlib import Path
import re
import subprocess
import sys

VALIDATOR_REVISION = "38a4309d165c78d4485b9d54f83066d131761e1a"
SCAN = "rpms-signature-scan"
CENTRAL = "https://github.com/red-hat-data-services/konflux-central.git"
PIPELINES = {"pipelines/container-build.yaml", "pipelines/multi-arch-container-build.yaml"}


def quantity(value):
    """Parse nonnegative Kubernetes resource quantities using exact decimals."""
    match = re.fullmatch(r"([+]?(?:\d+(?:\.\d*)?|\.\d+))([eE][+-]?\d+|[numkKMGTPE]|[KMGTPE]i)?", str(value))
    if not match:
        raise ValueError(f"invalid resource quantity: {value!r}")
    number, suffix = match.groups()
    suffix = suffix or ""
    if suffix.endswith("i"):
        factor = Decimal(1024) ** ("KMGTPE".index(suffix[0]) + 1)
    elif re.fullmatch(r"[eE][+-]?\d+", suffix):
        factor = Decimal(10) ** int(suffix[1:])
    else:
        factor = Decimal(10) ** {"": 0, "n": -9, "u": -6, "m": -3, "k": 3, "K": 3,
                                   "M": 6, "G": 9, "T": 12, "P": 15, "E": 18}[suffix]
    return Decimal(number) * factor


def quantity_floor(value, minimum):
    """Return the original spelling when it already meets the template's floor."""
    from lib.tekton_filters import ValidationError
    if value in (None, ""):
        return minimum
    if quantity(value) < quantity(minimum):
        raise ValidationError(f"resource must be at least {minimum}", minimum)
    return value


def load_validator(path):
    """Import only the reviewed validator revision, registering our quantity filter."""
    path = Path(path).resolve()
    revision = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    if revision != VALIDATOR_REVISION:
        raise ValueError(f"validator must be pinned to {VALIDATOR_REVISION}; got {revision}")
    if subprocess.check_output(["git", "-C", str(path), "status", "--porcelain", "--untracked-files=no"], text=True).strip():
        raise ValueError("validator checkout has tracked modifications")
    sys.path.insert(0, str(path))
    from lib import tekton_filters
    tekton_filters.quantity_floor = tekton_filters.jinja_filter(quantity_floor)


def unique(items, key, target):
    """Find one keyed entry and reject ambiguous duplicate identities."""
    matches = [item for item in items or [] if item.get(key) == target]
    if len(matches) > 1:
        raise ValueError(f"duplicate {key}: {target}")
    return matches[0] if matches else {}


def applicability(data, root, branch, yaml):
    """Resolve branch-local shared Pipelines only; never fetch remote references."""
    spec = data.get("spec", {})
    pipeline = spec.get("pipelineSpec")
    if pipeline is None:
        ref = spec.get("pipelineRef", {})
        params = {item["name"]: item.get("value") for item in ref.get("params", [])}
        if (ref.get("resolver") != "git" or params.get("url") != CENTRAL
                or params.get("pathInRepo") not in PIPELINES
                or params.get("revision") not in (branch, "{{ target_branch }}", "{{target_branch}}")):
            return "unresolved", "Pipeline reference requires explicit resolution"
        path = root / params["pathInRepo"]
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            return "unresolved", "Branch-local Pipeline source is missing, a symlink, or outside checkout"
        definition = yaml.load(path.read_text())
        if not isinstance(definition, dict) or definition.get("kind") != "Pipeline":
            raise ValueError(f"invalid Pipeline source: {path}")
        pipeline = definition["spec"]
    tasks = pipeline.get("tasks", []) + pipeline.get("finally", [])
    if not unique(tasks, "name", SCAN):
        return "skipped", "Pipeline does not contain the scan task"
    return "applicable", ""


def scan_inputs(data):
    """Preserve larger step memory; leave task-level budgets to manual review."""
    task = unique(data.get("spec", {}).get("taskRunSpecs", []), "pipelineTaskName", SCAN)
    if task.get("computeResources") is not None:
        return None
    step = unique(task.get("stepSpecs", []), "name", SCAN)
    resources = step.get("computeResources") or {}
    memories = [(resources.get(kind) or {}).get("memory", "0") for kind in ("requests", "limits")]
    return {"scan_memory": max(memories, key=quantity)}


def unowned(data):
    """Normalize away only the four resource fields owned by the initial rule."""
    data = deepcopy(data)
    spec = data.get("spec", {})
    for task in spec.get("taskRunSpecs", []) or []:
        if task.get("pipelineTaskName") != SCAN:
            continue
        steps = task.get("stepSpecs", [])
        for step in steps:
            if step.get("name") != SCAN:
                continue
            resources = step.get("computeResources", {})
            for kind in ("requests", "limits"):
                values = resources.get(kind, {})
                for key in ("cpu", "memory"):
                    values.pop(key, None)
                if not values:
                    resources.pop(kind, None)
            if not resources:
                step.pop("computeResources", None)
        task["stepSpecs"] = [step for step in steps if step != {"name": SCAN}]
        if not task["stepSpecs"]:
            task.pop("stepSpecs", None)
    if "taskRunSpecs" in spec:
        spec["taskRunSpecs"] = [task for task in spec["taskRunSpecs"] if task != {"pipelineTaskName": SCAN}]
        if not spec["taskRunSpecs"]:
            spec.pop("taskRunSpecs")
    return data


def reconcile(data, filename, branch, config, inputs):
    """Use the shared engine, then enforce scope and semantic idempotence."""
    from lib.tekton import classify_pipeline, extract_component_prefix, render_and_merge, resolve_overlay
    kind = classify_pipeline(filename, data, config=config)
    template = config.get_template(kind)
    if template is None:
        raise ValueError("PipelineRun type has no configured template")
    if template.parent:
        raise ValueError("parent inheritance is not enabled for this rollout")
    overlay = resolve_overlay(config.config_dir, template.file, extract_component_prefix(filename, data),
                              data.get("metadata", {}).get("labels", {}).get("appstudio.openshift.io/component"))
    kwargs = dict(branch=branch, mode="fix", cli_vars=inputs, overlay_path=overlay)
    result = render_and_merge(config.config_dir, template.file, data, filename, **kwargs)
    if unowned(result) != unowned(data):
        raise ValueError("template changed fields outside the initial scan-resource rule")
    kwargs["cli_vars"] = scan_inputs(result)
    if render_and_merge(config.config_dir, template.file, result, filename, **kwargs) != result:
        raise ValueError("template reconciliation is not idempotent")
    errors = []
    render_and_merge(config.config_dir, template.file, result, filename, errors=errors,
                     **{**kwargs, "mode": "validate"})
    if errors:
        raise ValueError(f"post-fix template validation failed: {errors}")
    return result


def main(argv=None):
    """Write reports by default; --fix changes local files only after planning."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--validator", type=Path, required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--config-dir", type=Path, default=Path(__file__).resolve().parents[1] / "tekton-templates/pipelineruns")
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--fix", action="store_true")
    args = parser.parse_args(argv)
    load_validator(args.validator)
    from ruamel.yaml import YAML
    from lib.tekton_config import load_config
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    config = load_config(str(args.config_dir.resolve()))
    root = args.path.resolve()
    report_dir = args.report_dir.resolve()
    if report_dir == root or root in report_dir.parents:
        parser.error("report directory must be outside the target checkout")
    rows, plans, patches = [], [], []
    paths = sorted(set(root.glob("pipelineruns/*/.tekton/*.yaml")) | set(root.glob("pipelineruns/*/.tekton/*.yml")))
    for path in paths:
        row = {"path": str(path.relative_to(root))}
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError("PipelineRun path escapes checkout or is a symlink")
            original = path.read_text()
            data = yaml.load(original)
            if not isinstance(data, dict) or data.get("kind") != "PipelineRun":
                row.update(status="skipped", reason="Not a PipelineRun")
            else:
                status, reason = applicability(data, root, args.branch, yaml)
                if status == "applicable":
                    inputs = scan_inputs(data)
                    if inputs is None:
                        status, reason = "manual", "Existing task-level computeResources budget; no automatic rewrite"
                    else:
                        result = reconcile(data, path.name, args.branch, config, inputs)
                        if result == data:
                            status = "conformant"
                        else:
                            buffer = StringIO()
                            yaml.dump(result, buffer)
                            updated = buffer.getvalue()
                            # Re-read serialized YAML rather than trusting in-memory equality alone.
                            if yaml.load(updated) != result:
                                raise ValueError("serialized YAML does not match planned result")
                            plans.append((path, updated))
                            patches.extend(difflib.unified_diff(original.splitlines(True), updated.splitlines(True),
                                                               fromfile="a/" + row["path"], tofile="b/" + row["path"]))
                            status = "planned"
                row.update(status=status, reason=reason)
        except Exception as exc:
            row.update(status="error", reason=str(exc))
        rows.append(row)
    if not paths:
        rows.append({"path": "pipelineruns/", "status": "error", "reason": "No PipelineRun files found"})
    counts = dict(Counter(row["status"] for row in rows))
    failed = bool(counts.get("error"))
    if args.fix and not failed:
        for path, updated in plans:
            path.write_text(updated)
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {"branch": args.branch, "validatorRevision": VALIDATOR_REVISION, "fixApplied": args.fix and not failed,
              "counts": counts, "files": rows}
    (report_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (report_dir / "changes.patch").write_text("".join(patches))
    print(json.dumps({"branch": args.branch, "counts": counts, "fixApplied": report["fixApplied"]}))
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
