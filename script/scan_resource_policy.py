"""Scan-only policy for the shared Tekton reconciliation command.

The shared caller owns safe detached data, context loading, rendering, phases,
receipts and publication. Callbacks neither read files nor control orchestration.
"""

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import re

from ruamel.yaml import YAML

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


def unique(items, key, target):
    """Find one keyed entry and reject ambiguous duplicate identities."""
    matches = [item for item in items or [] if item.get(key) == target]
    if len(matches) > 1:
        raise ValueError(f"duplicate {key}: {target}")
    return matches[0] if matches else {}


def template_baseline():
    """Read the trusted literal baseline once at module load, never in callbacks."""
    path = Path(__file__).resolve().parents[1] / "tekton-templates/pipelineruns/common.yaml.j2"
    template = YAML(typ="safe").load(path.read_text())
    task = unique(template["spec"]["taskRunSpecs"], "pipelineTaskName", SCAN)
    step = unique(task["stepSpecs"], "name", SCAN)
    baseline = step["computeResources"]
    for kind in ("requests", "limits"):
        for resource in ("cpu", "memory"):
            if quantity(baseline[kind][resource]) <= 0:
                raise ValueError("template baseline resources must be positive")
    return deepcopy(baseline)


BASELINE = template_baseline()


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
    return {"status": "applicable", "reason": "", "variables": scan_inputs(data)}


def scan_inputs(data):
    """Reject ambiguous overrides; resource values come only from the template."""
    task = unique(data.get("spec", {}).get("taskRunSpecs", []), "pipelineTaskName", SCAN)
    unique(task.get("stepSpecs", []), "name", SCAN)
    return {}


def unowned(data):
    """Normalize away scan task-budget removal and the four owned step fields."""
    data = deepcopy(data)
    spec = data.get("spec", {})
    for task in spec.get("taskRunSpecs", []) or []:
        if task.get("pipelineTaskName") != SCAN:
            continue
        task.pop("computeResources", None)
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
        raise ValueError("original scan Pipeline is unresolved or skipped")
    if unowned(original) != unowned(candidate):
        raise ValueError("artifact changed fields outside scan resource policy")
    task = unique(candidate.get("spec", {}).get("taskRunSpecs", []), "pipelineTaskName", SCAN)
    if "computeResources" in task:
        raise ValueError("scan task-level resources must be removed before step overrides")
    step = unique(task.get("stepSpecs", []), "name", SCAN)
    resources = step.get("computeResources", {})
    if any(quantity(resources.get(kind, {}).get(resource)) != quantity(BASELINE[kind][resource])
           for kind in ("requests", "limits") for resource in ("cpu", "memory")):
        raise ValueError("artifact differs from the fixed template resource baseline")
    return True
