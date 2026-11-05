#!/usr/bin/env python3
"""Plan scan conventions in disposable stages; optionally replay checked changes."""

import argparse
from collections import Counter
from copy import deepcopy
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import re
import subprocess
import sys

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
    """Bind the actual renderer to clean reviewed source, rejecting stale imports."""
    from reconciliation_tooling import REVISION

    path = Path(path).resolve()
    revision = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    if revision != REVISION:
        raise ValueError("validator checkout differs from the reviewed corrected revision")
    if subprocess.check_output(["git", "-C", str(path), "status", "--porcelain", "--untracked-files=no"], text=True).strip():
        raise ValueError("validator checkout has tracked modifications")
    cached = {name: module for name, module in sys.modules.items() if name == "lib" or name.startswith("lib.")}
    if cached and getattr(cached.get("lib"), "_KC_VALIDATOR_REVISION", None) != revision:
        raise ValueError("unbound cached validator imports; start a fresh process")
    sys.path.insert(0, str(path))
    from lib import tekton, tekton_filters
    for module in {**cached, "renderer": tekton, "filters": tekton_filters}.values():
        filename = getattr(module, "__file__", None)
        if not filename or not Path(filename).resolve().is_relative_to(path):
            raise ValueError("cached validator module comes from another checkout")
    sys.modules["lib"]._KC_VALIDATOR_REVISION = revision
    tekton_filters.quantity_floor = tekton_filters.jinja_filter(quantity_floor)
    return revision


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


def reconcile(data, filename, branch, config, inputs, mode="fix"):
    """Interpret one scan template; the shared worker owns validation phases."""
    from lib.tekton import classify_pipeline, extract_component_prefix, render_and_merge, resolve_overlay
    kind = classify_pipeline(filename, data, config=config)
    template = config.get_template(kind)
    if template is None:
        raise ValueError("PipelineRun type has no configured template")
    if template.parent:
        raise ValueError("parent inheritance is not enabled for this rollout")
    overlay = resolve_overlay(config.config_dir, template.file, extract_component_prefix(filename, data),
                              data.get("metadata", {}).get("labels", {}).get("appstudio.openshift.io/component"))
    if overlay and not Path(overlay).resolve().is_relative_to(Path(config.config_dir).resolve()):
        raise ValueError("component overlay escapes trusted template directory")
    errors = []
    result = render_and_merge(config.config_dir, template.file, data, filename,
                              branch=branch, mode=mode, cli_vars=inputs,
                              overlay_path=overlay, errors=errors if mode == "validate" else None)
    if unowned(result) != unowned(data):
        raise ValueError("template changed fields outside the initial scan-resource rule")
    if errors:
        raise ValueError(f"scan template validation failed: {errors}")
    return result


def validate_phase(root, branch, config, yaml, phase, revision):
    """Interpret local scan semantics; shared tooling owns phase isolation/checks."""
    from conformance_artifact import detached_data, validate_change
    from ruamel.yaml import YAML

    safe = YAML(typ="safe", pure=True)
    rows, updates = [], []
    paths = sorted(set(root.glob("pipelineruns/*/.tekton/*.yaml")) |
                   set(root.glob("pipelineruns/*/.tekton/*.yml")))
    for path in paths:
        row = {"path": path.relative_to(root).as_posix()}
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError("PipelineRun path escapes checkout or is a symlink")
            text = path.read_text()
            detached_data(safe.load(text))
            data = yaml.load(text)
            if not isinstance(data, dict) or data.get("kind") != "PipelineRun":
                status, reason = "skipped", "Not a PipelineRun"
            else:
                status, reason = applicability(data, root, branch, safe)
                if status == "applicable":
                    inputs = scan_inputs(data)
                    if inputs is None:
                        status, reason = "manual", "Existing task-level computeResources budget"
                    else:
                        result = reconcile(data, path.name, branch, config, inputs,
                                           mode="validate" if phase == "validation" else "fix")
                        validate_change(data, result, root, branch, safe)
                        status = "conformant" if result == data else "planned"
                        if status == "planned":
                            buffer = StringIO()
                            yaml.dump(result, buffer)
                            updated = buffer.getvalue()
                            if yaml.load(updated) != result:
                                raise ValueError("serialized YAML differs from planned result")
                            updates.append((path, updated))
            row.update(status=status, reason=reason)
        except Exception as exc:
            row.update(status="error", reason=str(exc))
        rows.append(row)
    if not paths:
        rows.append({"path": "pipelineruns/", "status": "error", "reason": "No PipelineRun files found"})
    counts = dict(Counter(row["status"] for row in rows))
    report = {"branch": branch, "validatorRevision": revision,
              "fixApplied": phase == "fix" and not counts.get("error"), "counts": counts, "files": rows}
    if not counts.get("error") and phase == "fix":
        for path, updated in updates:
            path.write_text(updated)
    return report


def main(argv=None):
    """Run real shared phases; preview never modifies the caller's checkout."""
    from reconciliation_tooling import REVISION, load_tool

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--validator", type=Path, required=True)
    parser.add_argument("--publisher", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--config-dir", type=Path, default=Path(__file__).resolve().parents[1] / "tekton-templates/pipelineruns")
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--fix", action="store_true", help="Explicitly replay the validated bundle locally")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_sha):
        parser.error("source-sha must be the workflow commit")
    worker = load_tool(args.publisher, "reconciliation")
    revision = load_validator(args.validator)
    from ruamel.yaml import YAML
    from lib.tekton_config import load_config
    yaml = YAML()
    yaml.preserve_quotes, yaml.width = True, 4096
    config = load_config(str(args.config_dir.resolve()))
    root, report_dir = args.path.resolve(), args.report_dir.resolve()
    phases = {}

    def validate(stage, phase):
        """Run the consumer's semantic checks inside shared isolated phase handling."""
        report = validate_phase(stage, args.branch, config, yaml, phase, revision)
        phases[phase] = report
        logs = report_dir / "phases"
        logs.mkdir(exist_ok=True)
        (logs / f"{phase}.json").write_text(json.dumps(report, indent=2) + "\n")
        if report["counts"].get("error") or (phase in ("validation", "idempotence") and report["counts"].get("planned")):
            raise ValueError(f"{phase} scan validation failed; see {logs}")
        return report

    def select(initial, inventory):
        """Authorize only initially planned existing PipelineRun paths, never deletes."""
        return [row["path"] for row in initial["files"] if row["status"] == "planned"], []

    try:
        worker.reconcile(
            root, scope="pipelineruns", report=report_dir,
            provenance={"branch": args.branch, "policyRevision": args.source_sha,
                        "workerRevision": REVISION, "publisherRevision": REVISION, "validatorRevision": revision},
            validate=validate, select=select,
            context_paths=[name for name in sorted(PIPELINES) if (root / name).is_file()])
        reports = {"plan": phases["fix"], "recheck": phases["validation"]}
        (report_dir / "bundle/policy-report.json").write_text(json.dumps(reports, indent=2) + "\n")
        (report_dir / "report.json").write_text(json.dumps(phases["initial"], indent=2) + "\n")
        if args.fix:
            from publish_conformance_pr import replay
            replay(root, report_dir / "bundle", args.branch, args.source_sha, args.publisher)
        print(json.dumps({"branch": args.branch, "counts": phases["initial"]["counts"],
                          "fixApplied": args.fix, "publisherRevision": REVISION}))
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
