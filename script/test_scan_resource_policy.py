"""Focused scan policy/template tests; generic orchestration belongs to shared tests."""

from copy import deepcopy
import importlib.util
import os
import shutil
from pathlib import Path
import sys

import pytest

import scan_resource_policy as policy

CENTRAL = "https://github.com/red-hat-data-services/konflux-central.git"
MULTI_ARCH_PATH = "pipelines/multi-arch-container-build.yaml"
CONTEXT_PATHS = ("pipelines/container-build.yaml", MULTI_ARCH_PATH)


def decision(data, ctx):
    """Exercise production Jinja eligibility, not a consumer Python selector."""
    checkout = os.environ.get("LOCAL_SHARED_CANDIDATE") or os.environ.get("VALIDATOR_CHECKOUT")
    if not checkout:
        pytest.fail("Set LOCAL_SHARED_CANDIDATE to the updated shared engine")
    if checkout not in sys.path:
        sys.path.insert(0, checkout)
    from lib.tekton_config import load_config
    from lib.tekton_validation import selection
    config = load_config(str(Path(__file__).resolve().parents[1] / "tekton-templates/pipelineruns"))
    result = selection(config, data, "example-on-push.yaml", ctx["branch"], ctx)
    assert set(result) == {"status", "reason"}
    assert isinstance(result["status"], str) and isinstance(result["reason"], str)
    # Legacy tests asserted an empty variables mapping; no selector variables exist.
    return {**result, "variables": {}}


def document():
    """Build an inline scan PipelineRun with unrelated fields to preserve."""
    return {"apiVersion": "tekton.dev/v1", "kind": "PipelineRun",
            "metadata": {"name": "example-on-push", "annotations": {"keep": "yes"}},
            "spec": {"pipelineSpec": {"tasks": [{"name": policy.SCAN}]},
                     "params": [{"name": "custom", "value": "keep"}]}}


def context():
    """Provide detached parsed documents in the frozen shared callback shape."""
    return {"branch": "rhoai-3.5", "documents": {}}


def candidate(data):
    """Build expected baseline resources without exercising any local renderer."""
    result = deepcopy(data)
    result["spec"]["taskRunSpecs"] = [{"pipelineTaskName": policy.SCAN, "stepSpecs": [
        {"name": policy.SCAN, "computeResources": {
            "requests": {"cpu": "500m", "memory": "1Gi"}, "limits": {"cpu": "500m", "memory": "1Gi"}}}]}]
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


def test_decision_and_check_do_not_mutate_inputs():
    """Policy callbacks only return decisions or authorize a candidate."""
    original, ctx = document(), context()
    proposed = candidate(original)
    before = deepcopy((original, proposed, ctx))
    assert decision(original, ctx) == {"status": "applicable", "reason": "", "variables": {}}
    assert policy.check(original, proposed, ctx) is True
    assert (original, proposed, ctx) == before


@pytest.mark.parametrize("location", ["tasks", "finally"])
def test_task_selection(location):
    """Only a real scan task in tasks or finally makes a document applicable."""
    data = document()
    data["spec"]["pipelineSpec"] = {location: [{"name": policy.SCAN}]}
    assert decision(data, context())["status"] == "applicable"
    data["spec"]["pipelineSpec"] = {location: [{"name": "build"}]}
    assert decision(data, context())["status"] == "skipped"


@pytest.mark.parametrize("revision", ["rhoai-3.5", "{{ target_branch }}", "{{target_branch}}", "main", "{{ cel: dynamic }}"])
def test_branch_local_reference(revision):
    """Resolve only allowlisted parsed branch-local context, never remote data."""
    data, ctx = document(), context()
    del data["spec"]["pipelineSpec"]
    path = CONTEXT_PATHS[0]
    data["spec"]["pipelineRef"] = {"resolver": "git", "params": [
        {"name": "url", "value": CENTRAL}, {"name": "pathInRepo", "value": path},
        {"name": "revision", "value": revision}]}
    assert decision(data, ctx)["status"] == "unresolved"
    ctx["documents"][path] = {"kind": "Pipeline", "spec": {"tasks": [{"name": policy.SCAN}]}}
    expected = "applicable" if revision in (ctx["branch"], "{{ target_branch }}", "{{target_branch}}") else "unresolved"
    assert decision(data, ctx)["status"] == expected
    data["spec"]["pipelineRef"]["params"][0]["value"] = "https://example.invalid/other.git"
    assert decision(data, ctx)["status"] == "unresolved"


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
    assert decision(data, ctx) == {"status": "applicable", "reason": "", "variables": {}}
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
    assert decision(data, context())["status"] == ("skipped" if change == "inline" else "unresolved")
    # The selector, not the postmerge owned-field guard, enforces eligibility.


def test_multiarch_contract_normalizes_task_budgets():
    """Applicable multi-arch references may replace task resources with step ones."""
    data = multiarch_document()
    data["spec"]["taskRunSpecs"] = [{"pipelineTaskName": policy.SCAN,
                                     "computeResources": {"limits": {"memory": "8Gi"}}}]
    assert decision(data, context())["status"] == "applicable"
    assert policy.check(data, candidate(data), context()) is True


def test_task_budget_and_duplicate_override():
    """Task-level resources are replaced but duplicate identities remain errors."""
    data = document()
    task = {"pipelineTaskName": policy.SCAN, "computeResources": {"limits": {"memory": "8Gi"}}}
    data["spec"]["taskRunSpecs"] = [task]
    assert decision(data, context())["status"] == "applicable"
    assert policy.check(data, candidate(data), context()) is True
    data["spec"]["taskRunSpecs"] = [task, deepcopy(task)]
    with pytest.raises(ValueError, match="duplicate"):
        decision(data, context())


@pytest.mark.parametrize("attack", ["timeout", "metadata", "image", "cpu-limit", "missing-cpu-limit", "task-budget", "low-cpu", "low-memory", "unequal-memory", "inflated"])
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
    else:
        resources["requests"]["cpu"] = "8"
    with pytest.raises(ValueError):
        policy.check(original, proposed, context())


@pytest.fixture
def render_template():
    """Exercise shared rendering directly; explicit candidate env is test-only."""
    checkout = os.environ.get("LOCAL_SHARED_CANDIDATE") or os.environ.get("VALIDATOR_CHECKOUT")
    if not checkout:
        pytest.fail("Set VALIDATOR_CHECKOUT (reviewed source) or LOCAL_SHARED_CANDIDATE (local tests only)")
    sys.path.insert(0, checkout)
    from lib.tekton import render_and_merge
    config = str(Path(__file__).resolve().parents[1] / "tekton-templates/pipelineruns")

    def render(data, mode="fix"):
        """Use the production shared template implementation, not local orchestration."""
        errors = []
        result = render_and_merge(config, "common.yaml.j2", data, "example-on-push.yaml",
                                  branch="rhoai-3.5", mode=mode,
                                  cli_vars=decision(data, context())["variables"],
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
        decision(data, context())


def test_template_is_the_only_baseline_authority(tmp_path, render_template):
    """A template-only baseline edit updates both rendering and the policy guard."""
    from ruamel.yaml import YAML
    from lib.tekton import render_and_merge
    module_path = tmp_path / "script/scan_resource_policy.py"
    module_path.parent.mkdir()
    shutil.copyfile(policy.__file__, module_path)
    template = tmp_path / "tekton-templates/pipelineruns/common.yaml.j2"
    template.parent.mkdir(parents=True)
    yaml = YAML()
    tree = yaml.load((Path(policy.__file__).resolve().parents[1] / "tekton-templates/pipelineruns/common.yaml.j2").read_text())
    resources = tree["spec"]["taskRunSpecs"][0]["stepSpecs"][0]["computeResources"]
    for kind in ("requests", "limits"):
        resources[kind].update(cpu="750m", memory="2Gi")
    with template.open("w") as stream:
        yaml.dump(tree, stream)
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
def test_selection_rejects_malformed_context(definition):
    data, ctx = multiarch_document("rhoai-3.5"), context()
    data["spec"]["pipelineRef"]["params"][1]["value"] = CONTEXT_PATHS[0]
    ctx["documents"][CONTEXT_PATHS[0]] = definition
    with pytest.raises(ValueError, match="invalid branch-local Pipeline source"):
        decision(data, ctx)


@pytest.mark.parametrize("location", ["tasks", "finally", "both"])
def test_selection_rejects_duplicate_pipeline_scan(location):
    data = document()
    task = {"name": policy.SCAN}
    data["spec"]["pipelineSpec"] = ({"tasks": [task], "finally": [task]}
        if location == "both" else {location: [task, task]})
    with pytest.raises(ValueError, match="duplicate"):
        decision(data, context())


def test_migration_config_and_guard_have_no_eligibility_callback():
    assert not hasattr(policy, "decision")
    assert not hasattr(policy, "CONTEXT_PATHS")
    from lib.tekton_config import load_config
    config = load_config(str(Path(__file__).resolve().parents[1] / "tekton-templates/pipelineruns"))
    assert config.selection_template == "selection.json.j2"
    assert tuple(config.context_paths) == CONTEXT_PATHS
