"""Scan-policy correctness guards for the trusted planning phase."""

from pathlib import Path
import re

from reconcile_pipelineruns import SCAN, applicability, quantity, scan_inputs, unique, unowned


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
    """Enforce scan scope and resource correctness during trusted planning."""
    original, candidate = detached_data(original), detached_data(candidate)
    if not isinstance(original, dict) or not isinstance(candidate, dict) or original.get("kind") != "PipelineRun":
        raise ValueError("artifact must modify an existing PipelineRun")
    if applicability(original, root, branch, yaml)[0] != "applicable":
        raise ValueError("cannot resolve the original scan Pipeline")
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
