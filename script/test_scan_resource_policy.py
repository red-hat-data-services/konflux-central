"""Focused scan policy/template tests; generic orchestration belongs to shared tests."""

from copy import deepcopy
import importlib.util
import os
import re
import shutil
from pathlib import Path
import sys

import pytest

checkout = os.environ.get("LOCAL_SHARED_CANDIDATE") or os.environ.get("VALIDATOR_CHECKOUT")
if checkout:
    sys.path.insert(0, checkout)

import scan_resource_policy as policy

CENTRAL = "https://github.com/red-hat-data-services/konflux-central.git"
MULTI_ARCH_PATH = "pipelines/multi-arch-container-build.yaml"
CONTEXT_PATHS = ("pipelines/container-build.yaml", MULTI_ARCH_PATH,
                 "pipelines/helm-chart-build.yaml", "pipelines/fbc-fragment-build.yaml")


from lib.tekton import classify_pipeline


@pytest.mark.parametrize("branch,allowed,accept", [
    ("main", "main", True), ("rhoai-3.6", "rhoai-3.6", True),
    ("rhoai-3.7-ea.1", "main,rhoai-3.7-ea.1", True),
    ("rhoai-", "rhoai-", False), ("master", "master", False),
    ("main-other", "main-other", False), ("feature/test", "feature/test", False),
    ("release-1", "release-1", False), ("main", None, False),
    ("rhoai-3.6", None, False), ("rhoai-3.6", "rhoai-*", False),
    ("main", "mainline", False),
])
def test_trusted_publication_policy_and_exact_run_scope(branch, allowed, accept):
    """Real KC config restricts PR bases independently of exact caller scope."""
    checkout = os.environ.get("LOCAL_SHARED_CANDIDATE") or os.environ.get("VALIDATOR_CHECKOUT")
    if not checkout:
        pytest.fail("Set LOCAL_SHARED_CANDIDATE or VALIDATOR_CHECKOUT")
    if checkout not in sys.path:
        sys.path.insert(0, checkout)
    from lib.tekton_config import load_config
    from lib.tekton_reconciliation import validate_publication_scope
    config = load_config(str(Path(__file__).resolve().parents[1] / "tekton-templates/all-pipelineruns"))
    assert config.publication_branches == ("main", "rhoai-*")
    if accept:
        validate_publication_scope(config, branch, allowed)
    else:
        with pytest.raises(ValueError):
            validate_publication_scope(config, branch, allowed)


def document():
    """Build an inline scan PipelineRun with unrelated fields to preserve."""
    return {"apiVersion": "tekton.dev/v1", "kind": "PipelineRun",
            "metadata": {"name": "example-on-schedule", "annotations": {"keep": "yes"}},
            "spec": {"pipelineSpec": {"tasks": [{"name": policy.SCAN}]},
                     "params": [{"name": "custom", "value": "keep"}]}}


def context():
    """Provide detached parsed documents in the frozen shared callback shape."""
    return {"branch": "rhoai-3.5", "documents": {}}


@pytest.mark.parametrize("branch", ["rhoai-3.6", "rhoai-3.7-ea.1"])
@pytest.mark.parametrize("signal", ["name", "annotation"])
@pytest.mark.parametrize("cel", [None, 'event == "pull_request"'])
def test_release_pr_branch_scope_excludes_legacy_and_cel_routing(branch, signal, cel):
    data, ctx = document(), context()
    ctx["branch"] = branch
    if signal == "name":
        data["metadata"]["name"] = "example-pull-request"
    else:
        data["metadata"]["annotations"]["pipelinesascode.tekton.dev/on-event"] = "[pull_request]"
    if cel is not None:
        data["metadata"]["annotations"][policy.CEL] = cel
    before = deepcopy((data, ctx))
    assert classify_pipeline("", data, policy.CONFIG) == "pull-request"
    assert policy.CONFIG.get_template("pull-request").excluded_on(branch)
    assert policy.check(data, deepcopy(data), ctx)
    assert (data, ctx) == before


@pytest.mark.parametrize("problem", ["platform-string", "platform-element", "duplicate-platform", "reference"])
def test_release_pr_untouched_with_bad_platforms_or_unsupported_ref(problem):
    data, ctx = document(), context()
    data["metadata"]["name"] = "example-pull-request"
    if problem == "reference":
        del data["spec"]["pipelineSpec"]
        data["spec"]["pipelineRef"] = {"resolver": "unknown"}
        assert classify_pipeline("", data, policy.CONFIG) == "default"
    else:
        value = "linux/amd64" if problem == "platform-string" else [123] if problem == "platform-element" else []
        data["spec"]["params"].append({"name": "build-platforms", "value": value})
        if problem == "duplicate-platform":
            data["spec"]["params"].append({"name": "build-platforms", "value": []})
        with pytest.raises(ValueError):
            policy.check(data, candidate(data), {**ctx, "branch": "main"})
    assert policy.check(data, deepcopy(data), ctx)


@pytest.mark.parametrize("branch", ["main", "feature/test", "rhoai-other"])
def test_pr_exclusion_is_scoped_to_rhoai_prefix(branch):
    data, ctx = document(), context()
    data["metadata"]["name"] = "example-pull-request"
    ctx["branch"] = branch
    assert classify_pipeline("", data, policy.CONFIG) == "pull-request"
    assert policy.CONFIG.get_template("pull-request").excluded_on(branch) == branch.startswith("rhoai-")


@pytest.mark.parametrize("name", ["example-on-push", "example-on-schedule"])
def test_release_non_pr_selection_stays_applicable(name):
    data = document()
    data["metadata"]["name"] = name
    assert classify_pipeline("", data, policy.CONFIG) == ("push" if name.endswith("push") else "scheduled")


@pytest.mark.parametrize("cel", [None, 'event == "pull_request"'])
def test_release_pr_all_phases_preserve_bytes_and_exclude_manifest(tmp_path, cel):
    from hashlib import sha256
    from ruamel.yaml import YAML
    from lib.reconciliation import verify_changes
    from lib.tekton_reconciliation import scan_phase

    data = document()
    data["metadata"]["name"] = "example-pull-request"
    if cel is not None:
        data["metadata"]["annotations"][policy.CEL] = cel
    relative = "pipelineruns/example/.tekton/example-pull-request.yaml"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    with path.open("w") as stream:
        stream.write("# Preserve this legacy file, including its formatting.\n")
        YAML().dump(data, stream)
    before = path.read_bytes()
    mode = path.stat().st_mode
    snapshot = {relative: {"mode": "100644", "sha256": sha256(before).hexdigest()}}
    for phase in ("initial", "preview", "fix", "validation", "idempotence"):
        result = scan_phase(tmp_path, phase, policy.CONFIG, policy, "rhoai-3.7-ea.1",
                            "pipelineruns", "nested", context={"branch": "rhoai-3.7-ea.1", "documents": {}})
        assert result["counts"] == {"skipped": 1}
        assert result["files"][0]["path"] == relative
        assert path.read_bytes() == before
        assert path.stat().st_mode == mode
        selected = [row["path"] for row in result["files"] if row["status"] == "planned"]
        assert not selected
        after = {relative: {"mode": "100644", "sha256": sha256(path.read_bytes()).hexdigest()}}
        assert verify_changes(snapshot, after, selected) == []
    # Main still attempts PR policy and fails for the missing-CEL legacy input.
    if cel is None:
        with pytest.raises(ValueError, match="on-cel-expression"):
            scan_phase(tmp_path, "initial", policy.CONFIG, policy, "main",
                       "pipelineruns", "nested", context={"branch": "main", "documents": {}})
        assert path.read_bytes() == before


@pytest.mark.parametrize("scenario", ["no-scan", "unknown-reference", "family-pr"])
def test_unsupported_files_all_phases_are_true_noops(tmp_path, scenario):
    from lib.tekton_reconciliation import scan_phase
    from ruamel.yaml import YAML
    data = document()
    if scenario == "no-scan":
        data["spec"]["pipelineSpec"]["tasks"] = [{"name": "build"}]
    else:
        del data["spec"]["pipelineSpec"]
        data["spec"]["pipelineRef"] = {"resolver": "git", "params": [
            {"name": "url", "value": CENTRAL}, {"name": "revision", "value": "main"},
            {"name": "pathInRepo", "value": "pipelines/helm-chart-build.yaml" if scenario == "family-pr" else "unknown.yaml"}]}
        if scenario == "family-pr":
            data["metadata"]["name"] = "example-pull-request"
    path = tmp_path / "pipelineruns/example/.tekton/example.yaml"
    path.parent.mkdir(parents=True)
    with path.open("w") as stream:
        stream.write("# Unmatched content must retain every byte.\n")
        YAML().dump(data, stream)
    before = path.read_bytes()
    for phase in ("initial", "preview", "fix", "validation", "idempotence"):
        result = scan_phase(tmp_path, phase, policy.CONFIG, policy, "main", "pipelineruns", "nested", context={"branch": "main", "documents": {}})
        assert result["counts"] == {"conformant": 1}
        assert path.read_bytes() == before
    changed = deepcopy(data)
    changed["metadata"]["annotations"]["keep"] = "tampered"
    with pytest.raises(ValueError, match="out-of-scope"):
        policy.check(data, changed, {"branch": "main"})


def candidate(data):
    """Build expected baseline resources without exercising any local renderer."""
    result = deepcopy(data)
    result["spec"]["taskRunSpecs"] = [{"pipelineTaskName": policy.SCAN, "stepSpecs": [
        {"name": policy.SCAN, "computeResources": {
            "requests": {"cpu": "500m", "memory": "1Gi"}, "limits": {"cpu": "500m", "memory": "1Gi"}}}]}]
    result["spec"]["workspaces"] = [deepcopy(policy.GIT_AUTH_WORKSPACE)]
    return result


@pytest.mark.parametrize("left,right", [("1Gi", "1024Mi"), ("250m", ".25"), ("1e3", "1k"), ("1u", "1000n")])
def test_quantity_equivalence(left, right):
    """Resource comparisons use exact equivalent quantities."""
    assert policy.quantity(left) == policy.quantity(right)


@pytest.mark.parametrize("value", ["bad", "-1", "NaN", "1MiB", "", "1.2.3"])
def test_invalid_quantities(value):
    """Reject negative and malformed scan resource values."""
    with pytest.raises(ValueError):
        policy.quantity(value)


def test_classification_and_guard_do_not_mutate_inputs():
    """Normal matchers classify; the guard only authorizes a candidate."""
    original, ctx = document(), context()
    proposed = candidate(original)
    before = deepcopy((original, proposed, ctx))
    assert classify_pipeline("", original, policy.CONFIG) == "scheduled"
    assert policy.check(original, proposed, ctx) is True
    assert (original, proposed, ctx) == before


@pytest.mark.parametrize("location", ["tasks", "finally"])
def test_task_selection(location):
    """Only a real scan task in tasks or finally makes a document applicable."""
    data = document()
    data["spec"]["pipelineSpec"] = {location: [{"name": policy.SCAN}]}
    assert classify_pipeline("", data, policy.CONFIG) == "scheduled"
    data["spec"]["pipelineSpec"] = {location: [{"name": "build"}]}
    assert classify_pipeline("", data, policy.CONFIG) == "default"
    assert policy.check(data, deepcopy(data), context())


@pytest.mark.parametrize("revision", ["rhoai-3.5", "{{ target_branch }}", "{{target_branch}}", "main", "{{ cel: dynamic }}"])
def test_branch_local_reference(revision):
    """Resolve only allowlisted parsed branch-local context, never remote data."""
    data, ctx = document(), context()
    del data["spec"]["pipelineSpec"]
    path = CONTEXT_PATHS[0]
    data["spec"]["pipelineRef"] = {"resolver": "git", "params": [
        {"name": "url", "value": CENTRAL}, {"name": "pathInRepo", "value": path},
        {"name": "revision", "value": revision}]}
    with pytest.raises(ValueError):
        policy.check(data, candidate(data), ctx)
    ctx["documents"][path] = {"kind": "Pipeline", "spec": {"tasks": [{"name": policy.SCAN}]}}
    if revision in (ctx["branch"], "{{ target_branch }}", "{{target_branch}}"):
        assert policy.check(data, candidate(data), ctx)
    else:
        with pytest.raises(ValueError, match="branch-local"):
            policy.check(data, candidate(data), ctx)
    data["spec"]["pipelineRef"]["params"][0]["value"] = "https://example.invalid/other.git"
    with pytest.raises(ValueError, match="repository"):
        policy.check(data, candidate(data), ctx)


def multiarch_document(revision="{{ cel: dynamic }}"):
    """Reference the exact central multi-arch path without resolving its revision."""
    data = document()
    del data["spec"]["pipelineSpec"]
    data["spec"]["pipelineRef"] = {"resolver": "git", "params": [
        {"name": "url", "value": CENTRAL},
        {"name": "pathInRepo", "value": MULTI_ARCH_PATH},
        {"name": "revision", "value": revision}]}
    return data


@pytest.mark.parametrize("revision", ["rhoai-3.5", "{{ target_branch }}", "main", "{{ cel: dynamic }}"])
@pytest.mark.parametrize("definition", [None, {"kind": "Pipeline", "spec": {"tasks": []}},
                                       {"kind": "Task"}])
def test_multiarch_path_contract_does_not_resolve_revision(revision, definition):
    """The explicit path contract does not claim to verify any runtime revision."""
    data, ctx = multiarch_document(revision), context()
    if definition is not None:
        ctx["documents"][MULTI_ARCH_PATH] = definition
    proposed = candidate(data)
    before = deepcopy((data, proposed, ctx))
    assert classify_pipeline("", data, policy.CONFIG) == "scheduled"
    assert policy.check(data, proposed, ctx) is True
    assert (data, proposed, ctx) == before


@pytest.mark.parametrize("change", ["resolver", "repository", "path", "path-suffix", "inline"])
def test_multiarch_contract_is_exact_and_does_not_override_inline_pipeline(change):
    """The contract cannot admit another repository/path or contradict inline data."""
    data = multiarch_document()
    ref = data["spec"]["pipelineRef"]
    if change == "resolver":
        ref["resolver"] = "bundles"
    elif change == "repository":
        ref["params"][0]["value"] = "https://example.invalid/other.git"
    elif change == "path":
        ref["params"][1]["value"] = "pipelines/helm-chart-build.yaml"
    elif change == "path-suffix":
        ref["params"][1]["value"] += ".other"
    else:
        data["spec"]["pipelineSpec"] = {"tasks": [{"name": "other"}]}
    with pytest.raises(ValueError):
        policy.check(data, candidate(data), context())


def test_multiarch_contract_normalizes_task_budgets():
    """Applicable multi-arch references may replace task resources with step ones."""
    data = multiarch_document()
    data["spec"]["taskRunSpecs"] = [{"pipelineTaskName": policy.SCAN,
                                     "computeResources": {"limits": {"memory": "8Gi"}}}]
    assert classify_pipeline("", data, policy.CONFIG) == "scheduled"
    assert policy.check(data, candidate(data), context()) is True


def test_task_budget_and_duplicate_override():
    """Task-level resources are replaced but duplicate identities remain errors."""
    data = document()
    task = {"pipelineTaskName": policy.SCAN, "computeResources": {"limits": {"memory": "8Gi"}}}
    data["spec"]["taskRunSpecs"] = [task]
    assert classify_pipeline("", data, policy.CONFIG) == "scheduled"
    assert policy.check(data, candidate(data), context()) is True
    data["spec"]["taskRunSpecs"] = [task, deepcopy(task)]
    with pytest.raises(ValueError, match="duplicate"):
        policy.check(data, candidate(data), context())


@pytest.mark.parametrize("attack", ["timeout", "metadata", "image", "cpu-limit", "missing-cpu-limit", "task-budget", "low-cpu", "low-memory", "unequal-memory", "inflated", "drop-git-auth", "tamper-secret", "duplicate-git-auth"])
def test_candidate_guard(attack):
    """Only exact policy resources may change; unrelated fields are protected."""
    original = document()
    proposed = candidate(original)
    resources = proposed["spec"]["taskRunSpecs"][0]["stepSpecs"][0]["computeResources"]
    if attack == "timeout":
        proposed["spec"]["timeouts"] = {"pipeline": "24h"}
    elif attack == "metadata":
        proposed["metadata"]["annotations"]["keep"] = "changed"
    elif attack == "image":
        proposed["spec"]["params"][0]["value"] = "changed"
    elif attack == "cpu-limit":
        resources["limits"]["cpu"] = "1"
    elif attack == "missing-cpu-limit":
        resources["limits"].pop("cpu")
    elif attack == "task-budget":
        proposed["spec"]["taskRunSpecs"][0]["computeResources"] = {"limits": {"memory": "8Gi"}}
    elif attack == "low-cpu":
        resources["requests"]["cpu"] = "100m"
    elif attack == "low-memory":
        resources["requests"]["memory"] = resources["limits"]["memory"] = "256Mi"
    elif attack == "unequal-memory":
        resources["limits"]["memory"] = "2Gi"
    elif attack == "drop-git-auth":
        proposed["spec"].pop("workspaces")
    elif attack == "tamper-secret":
        proposed["spec"]["workspaces"][0]["secret"]["secretName"] = "{{ other_secret }}"
    elif attack == "duplicate-git-auth":
        original["spec"]["workspaces"] = [deepcopy(policy.GIT_AUTH_WORKSPACE)] * 2
        proposed["spec"]["workspaces"] = [deepcopy(policy.GIT_AUTH_WORKSPACE)] * 2
    else:
        resources["requests"]["cpu"] = "8"
    with pytest.raises(ValueError):
        policy.check(original, proposed, context())


def test_git_auth_workspace_added_and_enforced(render_template):
    """The template adds the git-auth workspace; only the exact literal passes."""
    original = document()
    before = deepcopy(original)
    result = render_template(original)
    assert original == before
    assert result["spec"]["workspaces"] == [deepcopy(policy.GIT_AUTH_WORKSPACE)]
    assert result["spec"]["workspaces"][0]["secret"]["secretName"] == "{{ git_auth_secret }}"
    assert policy.check(original, result, context()) is True
    assert render_template(result) == result
    assert render_template(result, mode="validate") == result


def test_legacy_git_auth_spelling_is_normalized_to_canonical(render_template):
    """Legacy no-space spelling counts as the owned field and is standardized."""
    original = document()
    original["spec"]["workspaces"] = [{"name": "git-auth", "secret": {"secretName": "{{git_auth_secret}}"}}]
    before = deepcopy(original)
    result = render_template(original)
    assert original == before
    assert result["spec"]["workspaces"] == [deepcopy(policy.GIT_AUTH_WORKSPACE)]
    assert policy.check(original, result, context()) is True
    assert render_template(result) == result
    assert render_template(result, mode="validate") == result


@pytest.fixture
def render_template():
    """Exercise shared rendering directly; explicit candidate env is test-only."""
    checkout = os.environ.get("LOCAL_SHARED_CANDIDATE") or os.environ.get("VALIDATOR_CHECKOUT")
    if not checkout:
        pytest.fail("Set VALIDATOR_CHECKOUT (reviewed source) or LOCAL_SHARED_CANDIDATE (local tests only)")
    sys.path.insert(0, checkout)
    from lib.tekton import render_and_merge
    config = str(Path(__file__).resolve().parents[1] / "tekton-templates/all-pipelineruns")

    def render(data, mode="fix"):
        """Use the production shared template implementation, not local orchestration."""
        errors = []
        result = render_and_merge(config, "container-scheduled.yaml.j2", data, "example-on-push.yaml",
                                  branch="rhoai-3.5", mode=mode,
                                  errors=errors if mode == "validate" else None)
        assert not errors
        return result

    return render


def test_multiarch_cel_template_preserves_reference_and_unrelated_fields(render_template):
    """The actual renderer enforces resources without evaluating or rewriting CEL."""
    original = multiarch_document()
    original["spec"]["taskRunSpecs"] = [{"pipelineTaskName": policy.SCAN, "timeout": "3h", "stepSpecs": [
        {"name": policy.SCAN, "computeResources": {"requests": {"cpu": "2600m", "memory": "1Gi"},
                                                     "limits": {"cpu": "4", "memory": "2Gi"}}}]}]
    before = deepcopy(original)
    result = render_template(original)
    assert original == before
    assert result["spec"]["pipelineRef"] == original["spec"]["pipelineRef"]
    assert policy.check(original, result, context()) is True
    resources = result["spec"]["taskRunSpecs"][0]["stepSpecs"][0]["computeResources"]
    assert resources == {"requests": {"cpu": "500m", "memory": "1Gi"}, "limits": {"cpu": "500m", "memory": "1Gi"}}
    assert render_template(result) == result
    assert render_template(result, mode="validate") == result
    tampered = deepcopy(result)
    tampered["spec"]["pipelineRef"]["params"][2]["value"] = "main"
    with pytest.raises(ValueError, match="outside scan resource policy"):
        policy.check(original, tampered, context())


@pytest.mark.parametrize("cpu,memory_request,memory_limit", [("200m", "256Mi", "256Mi"), ("2600m", "1Gi", "2Gi")])
def test_actual_template_standardization_and_idempotence(render_template, cpu, memory_request, memory_limit):
    """Replace both smaller and larger reservations while retaining other fields."""
    original = document()
    original["spec"]["taskRunSpecs"] = [{"pipelineTaskName": policy.SCAN, "timeout": "3h", "stepSpecs": [
        {"name": "other", "computeResources": {"limits": {"memory": "64Mi"}}},
        {"name": policy.SCAN, "computeResources": {"requests": {"cpu": cpu, "memory": memory_request},
                                                     "limits": {"cpu": "4", "memory": memory_limit}}}]}]
    before = deepcopy(original)
    result = render_template(original)
    assert original == before
    assert policy.check(original, result, context()) is True
    assert render_template(result) == result
    assert render_template(result, mode="validate") == result
    task = result["spec"]["taskRunSpecs"][0]
    assert task["timeout"] == "3h"
    assert task["stepSpecs"][0] == original["spec"]["taskRunSpecs"][0]["stepSpecs"][0]
    assert task["stepSpecs"][1]["computeResources"] == {
        "requests": {"cpu": "500m", "memory": "1Gi"}, "limits": {"cpu": "500m", "memory": "1Gi"}}
    # Replacing the whole task entry would lose its timeout and other step.
    with pytest.raises(ValueError, match="outside scan resource policy"):
        policy.check(original, candidate(original), context())


@pytest.mark.parametrize("budget", [None,
    {"requests": {"memory": "8Gi"}, "limits": {"memory": "8Gi"}},
    {"requests": {"cpu": "2", "memory": "4Gi"}, "limits": {"cpu": "4", "memory": "8Gi"}},
    {"requests": {"cpu": "2600m", "memory": "512Mi"}, "limits": {"cpu": "2600m", "memory": "512Mi"}},
])
def test_task_budget_removal_preserves_deep_merge(render_template, budget):
    """Delete only the conflicting budget; retain other tasks and scan settings."""
    original = document()
    other = {"pipelineTaskName": "build-images", "computeResources": {"limits": {"memory": "32Gi"}}}
    original["spec"]["taskRunSpecs"] = [other, {"pipelineTaskName": policy.SCAN,
        "timeout": "3h", "computeResources": budget, "stepSpecs": [
            {"name": "output-results", "computeResources": {"limits": {"memory": "32Mi"}}}]}]
    before = deepcopy(original)
    result = render_template(original)
    assert original == before
    assert result["spec"]["taskRunSpecs"][0] == other
    scan = result["spec"]["taskRunSpecs"][1]
    assert "computeResources" not in scan
    assert scan["timeout"] == "3h"
    assert scan["stepSpecs"][0] == original["spec"]["taskRunSpecs"][1]["stepSpecs"][0]
    assert scan["stepSpecs"][1]["computeResources"] == {
        "requests": {"cpu": "500m", "memory": "1Gi"}, "limits": {"cpu": "500m", "memory": "1Gi"}}
    assert policy.check(original, result, context()) is True
    assert render_template(result) == result
    assert render_template(result, mode="validate") == result
    tampered = deepcopy(result)
    tampered["spec"]["taskRunSpecs"][0]["computeResources"]["limits"]["memory"] = "1Gi"
    with pytest.raises(ValueError, match="outside scan resource policy"):
        policy.check(original, tampered, context())
    tampered = deepcopy(result)
    tampered["spec"]["taskRunSpecs"][1]["computeResources"] = budget
    with pytest.raises(ValueError, match="task-level resources"):
        policy.check(original, tampered, context())


def test_duplicate_scan_steps_rejected_even_with_task_budget():
    data = document()
    data["spec"]["taskRunSpecs"] = [{"pipelineTaskName": policy.SCAN,
        "computeResources": {"limits": {"memory": "8Gi"}},
        "stepSpecs": [{"name": policy.SCAN}, {"name": policy.SCAN}]}]
    with pytest.raises(ValueError, match="duplicate"):
        policy.check(data, candidate(data), context())


def test_template_is_the_only_baseline_authority(tmp_path, render_template):
    """A template-only baseline edit updates both rendering and the policy guard."""
    from ruamel.yaml import YAML
    from lib.tekton import render_and_merge
    module_path = tmp_path / "script/scan_resource_policy.py"
    module_path.parent.mkdir()
    shutil.copyfile(policy.__file__, module_path)
    template = tmp_path / "tekton-templates/all-pipelineruns/container-scheduled.yaml.j2"
    shutil.copytree(Path(policy.__file__).resolve().parents[1] / "tekton-templates/all-pipelineruns", template.parent)
    yaml = YAML()
    tree = yaml.load((Path(policy.__file__).resolve().parents[1] / "tekton-templates/all-pipelineruns/container-scheduled.yaml.j2").read_text())
    resources = tree["spec"]["taskRunSpecs"][0]["stepSpecs"][0]["computeResources"]
    for kind in ("requests", "limits"):
        resources[kind].update(cpu="750m", memory="2Gi")
    with template.open("w") as stream:
        yaml.dump(tree, stream)
    push = template.parent / "push.yaml.j2"
    push_tree = yaml.load(push.read_text())
    push_tree["spec"] = deepcopy(tree["spec"])
    with push.open("w") as stream:
        yaml.dump(push_tree, stream)
    pr_template = template.parent / "pull-request.yaml.j2"
    pr_tree = yaml.load(pr_template.read_text())
    for key, value in tree["spec"].items():
        pr_tree["spec"][key] = deepcopy(value)
    with pr_template.open("w") as stream:
        yaml.dump(pr_tree, stream)
    spec = importlib.util.spec_from_file_location("changed_scan_policy", module_path)
    changed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(changed)
    original = document()
    result = render_and_merge(str(template.parent), template.name, original, "example-on-push.yaml", branch="rhoai-3.5", mode="fix")
    assert changed.check(original, result, context()) is True
    assert result["spec"]["taskRunSpecs"][0]["stepSpecs"][0]["computeResources"] == {
        "requests": {"cpu": "750m", "memory": "2Gi"}, "limits": {"cpu": "750m", "memory": "2Gi"}}
    with pytest.raises(ValueError, match="fixed template resource baseline"):
        changed.check(original, candidate(original), context())


def test_callbacks_do_not_read_template_again(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("policy callback attempted filesystem IO")
    monkeypatch.setattr(Path, "read_text", forbidden)
    original = document()
    assert policy.check(original, candidate(original), context()) is True


@pytest.mark.parametrize("definition", [{"kind": "Task", "spec": {}},
    {"kind": "Pipeline"}, {"kind": "Pipeline", "spec": []}, "invalid"])
def test_compatibility_guard_rejects_malformed_context(definition):
    data, ctx = multiarch_document("rhoai-3.5"), context()
    data["spec"]["pipelineRef"]["params"][1]["value"] = CONTEXT_PATHS[0]
    ctx["documents"][CONTEXT_PATHS[0]] = definition
    with pytest.raises(ValueError, match="invalid branch-local Pipeline source"):
        policy.check(data, candidate(data), ctx)


@pytest.mark.parametrize("location", ["tasks", "finally", "both"])
def test_container_matchers_reject_duplicate_pipeline_scan(location):
    data = document()
    task = {"name": policy.SCAN}
    data["spec"]["pipelineSpec"] = ({"tasks": [task], "finally": [task]}
        if location == "both" else {location: [task, task]})
    with pytest.raises(ValueError, match="duplicate"):
        policy.check(data, candidate(data), context())


def test_migration_config_and_guard_have_no_eligibility_callback():
    assert not hasattr(policy, "decision")
    assert not hasattr(policy, "CONTEXT_PATHS")
    from lib.tekton_config import load_config
    config = load_config(str(Path(__file__).resolve().parents[1] / "tekton-templates/all-pipelineruns"))
    assert config.selection_template is None
    assert not (Path(config.config_dir) / "selection.json.j2").exists()
    assert tuple(config.context_paths) == CONTEXT_PATHS


def test_template_mapping_and_push_cel_prerequisite():
    from lib.tekton import render_and_merge
    assert policy.CONFIG.get_template("push").file == "push.yaml.j2"
    assert policy.CONFIG.get_template("pull-request").file == "pull-request.yaml.j2"
    assert policy.CONFIG.get_template("scheduled").file == "container-scheduled.yaml.j2"
    data = document()
    data["metadata"]["name"] = "example-on-push"
    with pytest.raises(ValueError, match="existing on-cel-expression"):
        render_and_merge(str(policy.POLICY_DIRECTORY), "push.yaml.j2", data, "example-push.yaml", mode="fix")


@pytest.mark.parametrize("cluster", [None, "stone-prod-p01", "stone-prod-p02"])
def test_push_copies_default_and_adds_routing(cluster):
    from lib.tekton import render_and_merge
    original = document()
    original["metadata"]["name"] = "example-on-push"
    expression = 'event == "push" && target_branch == "rhoai-3.5" && ("src/**".pathChanged() || "docs/**".pathChanged())'
    if cluster:
        expression += f' && konflux_cluster == "{cluster}"'
    original["metadata"]["annotations"][policy.CEL] = expression
    before = deepcopy(original)
    def render(data, mode="fix", errors=None):
        return render_and_merge(str(policy.POLICY_DIRECTORY), policy.CONFIG.get_template("push").file,
                                data, "example-push.yaml", branch="rhoai-3.5", mode=mode, errors=errors)
    result = render(original)
    assert original == before
    assert result["spec"] == candidate(original)["spec"]
    actual = result["metadata"]["annotations"][policy.CEL]
    assert actual.count('konflux_cluster == "stone-prod-p02"') == 1
    def unrelated(value):
        return ''.join(re.sub(r'konflux_cluster\s*==\s*"[^"]*"', '', value).split()).strip('&')
    assert unrelated(actual) == unrelated(expression)
    assert not actual.startswith("\n")
    assert policy.check(original, result, context()) is True
    assert render(result) == result
    errors = []
    assert render(result, mode="validate", errors=errors) == result
    assert not errors
    errors = []
    render(original, mode="validate", errors=errors)
    assert any(policy.CEL in error for error in errors) == (cluster != "stone-prod-p02")
    for attack in ["cel", "metadata", "params", "resources"]:
        tampered = deepcopy(result)
        if attack == "cel":
            tampered["metadata"]["annotations"][policy.CEL] = 'konflux_cluster == "stone-prod-p02"'
        elif attack == "metadata":
            tampered["metadata"]["annotations"]["keep"] = "changed"
        elif attack == "params":
            tampered["spec"]["params"][0]["value"] = "changed"
        else:
            tampered["spec"]["taskRunSpecs"][0]["stepSpecs"][0]["computeResources"]["limits"]["cpu"] = "8"
        with pytest.raises(ValueError):
            policy.check(original, tampered, context())


@pytest.mark.parametrize("name", ["example-on-schedule"])
def test_default_preserves_nonpush_cel(name, render_template):
    data = document()
    data["metadata"]["name"] = name
    data["metadata"]["annotations"][policy.CEL] = 'event == "push" && konflux_cluster == "stone-prod-p01"'
    result = render_template(data)
    assert result["metadata"] == data["metadata"]
    assert policy.check(data, result, context()) is True
    result["metadata"]["annotations"][policy.CEL] += ' && true'
    with pytest.raises(ValueError, match="outside scan"):
        policy.check(data, result, context())
