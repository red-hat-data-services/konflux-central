"""Scan-only policy for the shared Tekton reconciliation command.

The shared caller owns safe detached data, context loading, rendering, phases,
receipts and publication. Callbacks neither read files nor control orchestration.
"""

from copy import deepcopy
from decimal import Decimal
import re

SCAN = "rpms-signature-scan"
CENTRAL = "https://github.com/red-hat-data-services/konflux-central.git"
MULTI_ARCH_PATH = "pipelines/multi-arch-container-build.yaml"
CONTEXT_PATHS = ("pipelines/container-build.yaml", MULTI_ARCH_PATH)


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

filters = {"quantity_floor": quantity_floor}


def unique(items, key, target):
    """Find one keyed entry and reject ambiguous duplicate identities."""
    matches = [item for item in items or [] if item.get(key) == target]
    if len(matches) > 1:
        raise ValueError(f"duplicate {key}: {target}")
    return matches[0] if matches else {}


def decision(data, context):
    """Select scan Pipelines by explicit multi-arch contract or parsed context."""
    spec = data.get("spec", {})
    pipeline = spec.get("pipelineSpec")
    if pipeline is None:
        ref = spec.get("pipelineRef", {})
        params = {item["name"]: item.get("value") for item in ref.get("params", [])}
        if (ref.get("resolver") != "git" or params.get("url") != CENTRAL
                or params.get("pathInRepo") not in CONTEXT_PATHS):
            return {"status": "unresolved", "reason": "Pipeline reference requires explicit resolution", "variables": {}}
        # This exact central path promises the scan task for every revision,
        # including CEL-selected revisions. Do not evaluate or rewrite CEL.
        if params["pathInRepo"] != MULTI_ARCH_PATH:
            if params.get("revision") not in (context["branch"], "{{ target_branch }}", "{{target_branch}}"):
                return {"status": "unresolved", "reason": "Pipeline reference requires explicit resolution", "variables": {}}
            definition = context["documents"].get(params["pathInRepo"])
            if definition is None:
                return {"status": "unresolved", "reason": "Branch-local Pipeline source is missing", "variables": {}}
            if not isinstance(definition, dict) or definition.get("kind") != "Pipeline":
                raise ValueError("invalid branch-local Pipeline source")
            pipeline = definition["spec"]
    if pipeline is not None and not unique(pipeline.get("tasks", []) + pipeline.get("finally", []), "name", SCAN):
        return {"status": "skipped", "reason": "Pipeline does not contain the scan task", "variables": {}}
    inputs = scan_inputs(data)
    if inputs is None:
        return {"status": "manual", "reason": "task-level budgets require manual review", "variables": {}}
    return {"status": "applicable", "reason": "", "variables": inputs}


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


def check(original, candidate, context):
    """Enforce scan scope and resource correctness during trusted planning."""
    if not isinstance(original, dict) or not isinstance(candidate, dict) or original.get("kind") != "PipelineRun":
        raise ValueError("artifact must modify an existing PipelineRun")
    if decision(original, context)["status"] != "applicable":
        raise ValueError("original scan Pipeline is unresolved, skipped, or requires manual review")
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
    return True
