"""Scan and git-auth workspace policy for the shared Tekton reconciliation command.

The shared caller owns safe detached data, context loading, rendering, phases,
receipts and publication. Callbacks neither read files nor control orchestration.
"""

from copy import deepcopy
from decimal import Decimal
from pathlib import Path, PurePosixPath
import re

from ruamel.yaml import YAML

from lib.tekton import _create_jinja_env, _deep_merge, classify_pipeline
from lib.tekton_config import load_config

SCAN = "rpms-signature-scan"
GIT_AUTH = "git-auth"
GIT_AUTH_SECRET = "{{ git_auth_secret }}"
# The common template stores the PaC expression Jinja-escaped; it must render to
# the literal GIT_AUTH_SECRET above after template rendering.
TEMPLATE_GIT_AUTH_SECRET = "{{ '{{ git_auth_secret }}' }}"
# Legacy files may spell the PaC expression without inner spaces; normalization
# treats it as the same owned field, but check() enforces the canonical spelling.
GIT_AUTH_SECRET_LEGACY = "{{git_auth_secret}}"


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
    if items is not None and (not isinstance(items, list) or any(not isinstance(item, dict) for item in items)):
        raise ValueError("keyed collection must contain mappings")
    matches = [item for item in items or [] if item.get(key) == target]
    if len(matches) > 1:
        raise ValueError(f"duplicate {key}: {target}")
    return matches[0] if matches else {}


def template_baseline():
    """Read the trusted literal baselines once at module load, never in callbacks."""
    path = Path(__file__).resolve().parents[1] / "tekton-templates/all-pipelineruns/container-scheduled.yaml.j2"
    template = YAML(typ="safe").load(path.read_text())
    task = unique(template["spec"]["taskRunSpecs"], "pipelineTaskName", SCAN)
    step = unique(task["stepSpecs"], "name", SCAN)
    baseline = step["computeResources"]
    for kind in ("requests", "limits"):
        for resource in ("cpu", "memory"):
            if quantity(baseline[kind][resource]) <= 0:
                raise ValueError("template baseline resources must be positive")
    workspace = unique(template["spec"].get("workspaces", []), "name", GIT_AUTH)
    if workspace != {"name": GIT_AUTH, "secret": {"secretName": TEMPLATE_GIT_AUTH_SECRET}}:
        raise ValueError("template must own the literal git-auth workspace")
    return deepcopy(baseline), {"name": GIT_AUTH, "secret": {"secretName": GIT_AUTH_SECRET}}


BASELINE, GIT_AUTH_WORKSPACE = template_baseline()

# Compile trusted routing policy once; callbacks do not read files.
CEL = "pipelinesascode.tekton.dev/on-cel-expression"
POLICY_DIRECTORY = Path(__file__).resolve().parents[1] / "tekton-templates/all-pipelineruns"
CONFIG = load_config(str(POLICY_DIRECTORY))
PUSH_TEMPLATE = YAML(typ="safe").load((POLICY_DIRECTORY / "push.yaml.j2").read_text())
# Container push copies the scheduled resource baseline and adds routing.
if PUSH_TEMPLATE.get("spec") != YAML(typ="safe").load((POLICY_DIRECTORY / "container-scheduled.yaml.j2").read_text())["spec"]:
    raise ValueError("push template must copy the container resource policy")
EXPECTED_PUSH_CEL = _create_jinja_env(str(POLICY_DIRECTORY), mode="fix").from_string(
    PUSH_TEMPLATE["metadata"]["annotations"][CEL])
PR_TEMPLATE = YAML(typ="safe").load((POLICY_DIRECTORY / "pull-request.yaml.j2").read_text())


def compile_pr_policy(template):
    """Compile the same merged template as rendering, without callback IO."""
    spec = deepcopy(template["spec"])
    platforms = unique(spec.pop("params"), "name", "build-platforms")["value"]
    if spec != PUSH_TEMPLATE["spec"]:
        raise ValueError("PR template must copy the container resource policy")
    env = _create_jinja_env(str(POLICY_DIRECTORY), mode="fix")
    expression = re.fullmatch(r"\s*{{\s*(.*?)\s*}}\s*", platforms, re.DOTALL).group(1)
    return (env.from_string(template["metadata"]["annotations"][CEL]),
            env.compile_expression(expression.replace("_?", "value")))


PR_POLICIES = {None: compile_pr_policy(PR_TEMPLATE)}
for _directory in {entry["template_dir"] for entry in CONFIG.template_overlays}:
    _relative = f"{_directory}/pull-request.yaml.j2"
    _path = POLICY_DIRECTORY / _relative
    if _path.exists():
        PR_POLICIES[_relative] = compile_pr_policy(
            _deep_merge(YAML(typ="safe").load(_path.read_text()), deepcopy(PR_TEMPLATE)))
FAMILY_CEL = {}
for _family in ("charts", "fbc-fragment"):
    _template = YAML(typ="safe").load((POLICY_DIRECTORY / CONFIG.get_template(_family).file).read_text())
    if _template.get("spec") != {"workspaces": [{"name": GIT_AUTH, "secret": {"secretName": TEMPLATE_GIT_AUTH_SECRET}}]}:
        raise ValueError("family template must own only the common git-auth workspace")
    FAMILY_CEL[_family] = _create_jinja_env(str(POLICY_DIRECTORY), mode="fix").from_string(
        _template["metadata"]["annotations"][CEL])


def scan_inputs(data):
    """Reject ambiguous overrides; resource values come only from the template."""
    task = unique(data.get("spec", {}).get("taskRunSpecs", []), "pipelineTaskName", SCAN)
    unique(task.get("stepSpecs", []), "name", SCAN)
    unique(data.get("spec", {}).get("workspaces", []), "name", GIT_AUTH)
    return {}


def unowned(data, include_scan=True):
    """Normalize away scan task-budget removal, owned step fields and git-auth."""
    data = deepcopy(data)
    spec = data.get("spec", {})
    workspaces = spec.get("workspaces", [])
    if workspaces:
        owned = []
        for item in workspaces:
            secret_name = item.get("secret", {}).get("secretName") if item.get("name") == GIT_AUTH else None
            if secret_name in (GIT_AUTH_SECRET, GIT_AUTH_SECRET_LEGACY) and set(item) == {"name", "secret"}:
                continue
            owned.append(item)
        spec["workspaces"] = owned
        if not owned:
            spec.pop("workspaces")
    if not include_scan:
        return data
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


def check_pipeline_compatibility(original, context, family):
    """Assert evidence for the task/workspace writes, not scope or classification.

    Inline scan identity is checked by container matchers. Multi-arch references
    retain their existing arbitrary-revision path contract. Single/family paths
    still require a branch-local definition; no remote resolution is performed.
    """
    spec = original.get("spec", {})
    if "pipelineSpec" in spec:
        return
    ref = spec.get("pipelineRef", {})
    params = {item["name"]: item.get("value") for item in ref.get("params", [])}
    if params.get("url") != "https://github.com/red-hat-data-services/konflux-central.git":
        raise ValueError("unsupported Pipeline repository")
    path = params.get("pathInRepo")
    if path == "pipelines/multi-arch-container-build.yaml":
        return
    if params.get("revision") not in (context.get("branch"), "{{ target_branch }}", "{{target_branch}}"):
        raise ValueError("Pipeline source must be branch-local")
    definition = context.get("documents", {}).get(path)
    if (not isinstance(definition, dict) or definition.get("kind") != "Pipeline"
            or not isinstance(definition.get("spec"), dict)):
        raise ValueError("missing or invalid branch-local Pipeline source")
    pipeline = definition["spec"]
    if family:
        if not unique(pipeline.get("workspaces", []), "name", GIT_AUTH):
            raise ValueError("family Pipeline must declare git-auth workspace")
    else:
        tasks, final = pipeline.get("tasks", []), pipeline.get("finally", [])
        if not isinstance(tasks, list) or not isinstance(final, list):
            raise ValueError("Pipeline tasks must be lists")
        if not unique(tasks + final, "name", SCAN):
            raise ValueError("Pipeline must declare the scan task")


def check(original, candidate, context):
    """Enforce scan scope and resource correctness during trusted planning."""
    if not isinstance(original, dict) or not isinstance(candidate, dict) or original.get("kind") != "PipelineRun":
        raise ValueError("artifact must modify an existing PipelineRun")
    template = classify_pipeline("", original, CONFIG)
    configured = CONFIG.get_template(template)
    if template == "default" or (configured and configured.excluded_on(context.get("branch"))):
        if original != candidate:
            raise ValueError("out-of-scope PipelineRun must remain unchanged")
        return True
    if configured is None:
        raise ValueError("no supported Pipeline template")
    family = template in FAMILY_CEL
    check_pipeline_compatibility(original, context, family)
    if family:
        unique(original.get("spec", {}).get("workspaces", []), "name", GIT_AUTH)
    else:
        scan_inputs(original)
    normalized = deepcopy(candidate)
    if template == "pull-request":
        overlay = context.get("template_overlay")
        if overlay not in PR_POLICIES:
            raise ValueError("unrecognized trusted PR template overlay")
        name = context.get("pipeline_path")
        if (not isinstance(name, str) or not name or "\\" in name
                or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts
                or PurePosixPath(name).as_posix() != name):
            raise ValueError("PR overlay requires its trusted directory-mapped pipeline path")
        expected_overlay = next((f'{entry["template_dir"]}/pull-request.yaml.j2'
                                 for entry in CONFIG.template_overlays
                                 if PurePosixPath(name).is_relative_to(entry["pipeline_dir"])
                                 and f'{entry["template_dir"]}/pull-request.yaml.j2' in PR_POLICIES), None)
        if overlay != expected_overlay:
            raise ValueError("PR overlay requires its trusted directory-mapped pipeline path")
        pr_routing, pr_platforms = PR_POLICIES[overlay]
        before = unique(original.get("spec", {}).get("params", []), "name", "build-platforms")
        if before and (not isinstance(before.get("value"), list)
                       or any(not isinstance(value, str) for value in before["value"])):
            raise ValueError("build-platforms must be an array of strings")
        after = unique(candidate.get("spec", {}).get("params", []), "name", "build-platforms")
        desired = deepcopy(before)
        if before:
            desired["value"] = pr_platforms(value=before["value"])
        if after != desired:
            raise ValueError("build-platforms differs from the PR template policy")
        if before:
            normalized_param = unique(normalized["spec"]["params"], "name", "build-platforms")
            normalized_param.clear()
            normalized_param.update(deepcopy(before))
    if template in ("push", "pull-request") or family:
        annotations = original.get("metadata", {}).get("annotations", {})
        routing = FAMILY_CEL[template] if family else pr_routing if template == "pull-request" else EXPECTED_PUSH_CEL
        expected = routing.render(annotations=annotations)
        if candidate.get("metadata", {}).get("annotations", {}).get(CEL) != expected:
            raise ValueError("push CEL differs from the template routing policy")
        normalized["metadata"]["annotations"][CEL] = annotations[CEL]
    if unowned(original, include_scan=not family) != unowned(normalized, include_scan=not family):
        raise ValueError("artifact changed fields outside scan resource policy")
    if unique(candidate.get("spec", {}).get("workspaces", []), "name", GIT_AUTH) != GIT_AUTH_WORKSPACE:
        raise ValueError("git-auth workspace must bind the template git_auth_secret")
    if family:
        return True
    task = unique(candidate.get("spec", {}).get("taskRunSpecs", []), "pipelineTaskName", SCAN)
    if "computeResources" in task:
        raise ValueError("scan task-level resources must be removed before step overrides")
    step = unique(task.get("stepSpecs", []), "name", SCAN)
    resources = step.get("computeResources", {})
    if any(quantity(resources.get(kind, {}).get(resource)) != quantity(BASELINE[kind][resource])
           for kind in ("requests", "limits") for resource in ("cpu", "memory")):
        raise ValueError("artifact differs from the fixed template resource baseline")
    return True
