#!/usr/bin/env python3
"""Transfer data-only plans between isolated planning and publishing runners."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from reconcile_pipelineruns import SCAN, applicability, quantity, scan_inputs, unique, unowned

MAX_FILE_BYTES = 2 * 1024 * 1024


def git_read(root, *args):
    """Read repository data without invoking validator code or writing Git state."""
    return subprocess.check_output(["git", "-C", str(root), *args])


def planned_paths(plan):
    """Allow only unique, relative existing PipelineRun filenames."""
    paths = [row["path"] for row in plan["files"] if row["status"] == "planned"]
    if len(paths) != len(set(paths)):
        raise ValueError("duplicate planned paths")
    for name in paths:
        path = Path(name)
        if (len(path.parts) != 4 or path.parts[0] != "pipelineruns" or path.parts[2] != ".tekton"
                or path.suffix not in (".yaml", ".yml") or ".." in path.parts
                or name != path.as_posix()):
            raise ValueError(f"invalid artifact path: {name}")
    return paths


def contained_file(root, name):
    """Reject symlinks and path escapes rather than following artifact metadata."""
    path = root / name
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"artifact/target path is not a contained file: {name}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"oversized PipelineRun: {name}")
    return path


def check_reports(plan, recheck, branch):
    """Require applied/rechecked reports, but do not treat reports as authorization."""
    if plan.get("branch") != branch or recheck.get("branch") != branch:
        raise ValueError("artifact branch mismatch")
    revision = plan.get("validatorRevision", "")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("artifact lacks a resolved validator revision")
    if revision != recheck.get("validatorRevision"):
        raise ValueError("artifact validator revisions differ")
    if not plan.get("fixApplied") or plan["counts"].get("error"):
        raise ValueError("artifact plan was not successfully applied")
    if recheck.get("fixApplied") or any(recheck["counts"].get(key) for key in ("error", "planned")):
        raise ValueError("artifact recheck is not clean")


def detached_data(value, ancestors=(), budget=None):
    """Expand aliases independently, rejecting cycles and excessive YAML graphs."""
    budget = [100000] if budget is None else budget
    budget[0] -= 1
    if budget[0] < 0 or len(ancestors) > 128:
        raise ValueError("YAML graph exceeds validation limits")
    if isinstance(value, (dict, list)):
        if id(value) in ancestors:
            raise ValueError("cyclic YAML aliases are not supported")
        ancestors = ancestors + (id(value),)
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise ValueError("YAML mapping keys must be strings")
            return {key: detached_data(item, ancestors, budget) for key, item in value.items()}
        return [detached_data(item, ancestors, budget) for item in value]
    if value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("YAML values must be JSON-compatible scalars")
    return value


def validate_change(original, candidate, root, branch, yaml):
    """Independently enforce the initial rule using only trusted central code."""
    original, candidate = detached_data(original), detached_data(candidate)
    if not isinstance(original, dict) or not isinstance(candidate, dict) or original.get("kind") != "PipelineRun":
        raise ValueError("artifact must modify an existing PipelineRun")
    if applicability(original, root, branch, yaml)[0] != "applicable":
        raise ValueError("cannot independently resolve the original scan Pipeline")
    inputs = scan_inputs(original)
    if inputs is None:
        raise ValueError("task-level budgets require manual review")
    if unowned(original) != unowned(candidate):
        raise ValueError("artifact changed fields outside scan resource policy")
    task = unique(candidate.get("spec", {}).get("taskRunSpecs", []), "pipelineTaskName", SCAN)
    step = unique(task.get("stepSpecs", []), "name", SCAN)
    resources = step.get("computeResources", {})
    requests, limits = resources.get("requests", {}), resources.get("limits", {})
    old_task = unique(original.get("spec", {}).get("taskRunSpecs", []), "pipelineTaskName", SCAN)
    old_step = unique(old_task.get("stepSpecs", []), "name", SCAN)
    old_cpu = (old_step.get("computeResources") or {}).get("requests", {}).get("cpu", "0")
    if ("cpu" in limits or quantity(requests.get("cpu")) != max(quantity("250m"), quantity(old_cpu))
            or quantity(requests.get("memory")) != quantity(limits.get("memory"))
            or quantity(requests.get("memory")) != max(quantity("512Mi"), quantity(inputs["scan_memory"]))):
        raise ValueError("artifact violates resource floors, preservation, or CPU-limit policy")


def export(root, bundle, branch, source, plan, recheck):
    """Package reports and changed file bytes; the receiver must distrust both."""
    root, bundle = Path(root).resolve(), Path(bundle).resolve()
    check_reports(plan, recheck, branch)
    paths = planned_paths(plan)
    payload = bundle / "files"
    for name in paths:
        contents = contained_file(root, name).read_bytes()
        dest = payload / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(contents)
    manifest = {"branch": branch, "sourceCommit": source,
                "targetCommit": git_read(root, "rev-parse", "HEAD").decode().strip(),
                "plan": plan, "recheck": recheck}
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def prepare(root, bundle, branch, source, read_git=git_read):
    """Validate all artifact bytes against Git HEAD before copying any YAML."""
    from ruamel.yaml import YAML

    root, bundle = Path(root).resolve(), Path(bundle).resolve()
    manifest_file = contained_file(bundle, "manifest.json")
    manifest = json.loads(manifest_file.read_text())
    head = read_git(root, "rev-parse", "HEAD").decode().strip()
    if (manifest.get("branch") != branch or manifest.get("sourceCommit") != source
            or manifest.get("targetCommit") != head):
        raise ValueError("artifact source, branch, or base commit is stale/mismatched")
    plan, recheck = manifest["plan"], manifest["recheck"]
    check_reports(plan, recheck, branch)
    paths = planned_paths(plan)
    planned = {row["path"]: row for row in plan["files"] if row["status"] == "planned"}
    checked = {row["path"]: row for row in recheck["files"]}
    yaml = YAML(typ="safe", pure=True)
    copies = []
    for name in paths:
        target = contained_file(root, name)
        contents = contained_file(bundle / "files", name).read_bytes()
        digest = hashlib.sha256(contents).hexdigest()
        if (digest != planned[name].get("plannedSha256") or digest != checked.get(name, {}).get("sha256")
                or checked.get(name, {}).get("status") != "conformant"):
            raise ValueError(f"artifact content hash/status mismatch: {name}")
        original = read_git(root, "show", f"HEAD:{name}")
        validate_change(yaml.load(original), yaml.load(contents), root, branch, yaml)
        if target.read_bytes() not in (original, contents):
            raise ValueError(f"target has unrelated local edits: {name}")
        copies.append((target, contents))
    for target, contents in copies:
        target.write_bytes(contents)
    return plan, recheck


def main():
    """Export without publication credentials, or validate/prepare in a fresh job."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["export", "prepare"])
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--recheck", type=Path)
    args = parser.parse_args()
    if args.mode == "export":
        if not args.plan or not args.recheck:
            parser.error("export requires --plan and --recheck")
        export(args.path, args.bundle, args.branch, args.source_sha,
               json.loads(args.plan.read_text()), json.loads(args.recheck.read_text()))
    else:
        prepare(args.path, args.bundle, args.branch, args.source_sha)
    print(f"Artifact {args.mode} complete for {args.branch}")


if __name__ == "__main__":
    main()
