"""Focused scan policy/template tests; generic orchestration belongs to shared tests."""

from copy import deepcopy
import os
from pathlib import Path
import sys

import pytest

import scan_resource_policy as policy


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
            "requests": {"cpu": "250m", "memory": "512Mi"}, "limits": {"memory": "512Mi"}}}]}]
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
    assert policy.decision(original, ctx) == {"status": "applicable", "reason": "", "variables": {"scan_memory": "0"}}
    assert policy.check(original, proposed, ctx) is True
    assert (original, proposed, ctx) == before


@pytest.mark.parametrize("location", ["tasks", "finally"])
def test_task_selection(location):
    """Only a real scan task in tasks or finally makes a document applicable."""
    data = document()
    data["spec"]["pipelineSpec"] = {location: [{"name": policy.SCAN}]}
    assert policy.decision(data, context())["status"] == "applicable"
    data["spec"]["pipelineSpec"] = {location: [{"name": "build"}]}
    assert policy.decision(data, context())["status"] == "skipped"


@pytest.mark.parametrize("revision", ["rhoai-3.5", "{{ target_branch }}", "{{target_branch}}", "main", "{{ cel: dynamic }}"])
def test_branch_local_reference(revision):
    """Resolve only allowlisted parsed branch-local context, never remote data."""
    data, ctx = document(), context()
    del data["spec"]["pipelineSpec"]
    path = policy.CONTEXT_PATHS[0]
    data["spec"]["pipelineRef"] = {"resolver": "git", "params": [
        {"name": "url", "value": policy.CENTRAL}, {"name": "pathInRepo", "value": path},
        {"name": "revision", "value": revision}]}
    assert policy.decision(data, ctx)["status"] == "unresolved"
    ctx["documents"][path] = {"kind": "Pipeline", "spec": {"tasks": [{"name": policy.SCAN}]}}
    expected = "applicable" if revision in (ctx["branch"], "{{ target_branch }}", "{{target_branch}}") else "unresolved"
    assert policy.decision(data, ctx)["status"] == expected
    data["spec"]["pipelineRef"]["params"][0]["value"] = "https://example.invalid/other.git"
    assert policy.decision(data, ctx)["status"] == "unresolved"


def test_task_budget_and_duplicate_override():
    """Task-level budgets remain manual and duplicate identities are errors."""
    data = document()
    task = {"pipelineTaskName": policy.SCAN, "computeResources": {"limits": {"memory": "8Gi"}}}
    data["spec"]["taskRunSpecs"] = [task]
    assert policy.decision(data, context())["status"] == "manual"
    with pytest.raises(ValueError):
        policy.check(data, candidate(data), context())
    data["spec"]["taskRunSpecs"] = [task, deepcopy(task)]
    with pytest.raises(ValueError, match="duplicate"):
        policy.decision(data, context())


@pytest.mark.parametrize("attack", ["timeout", "metadata", "image", "cpu-limit", "low-cpu", "low-memory", "unequal-memory", "inflated"])
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
    elif attack == "low-cpu":
        resources["requests"]["cpu"] = "100m"
    elif attack == "low-memory":
        resources["requests"]["memory"] = resources["limits"]["memory"] = "256Mi"
    elif attack == "unequal-memory":
        resources["limits"]["memory"] = "1Gi"
    else:
        resources["requests"]["cpu"] = "8"
    with pytest.raises(ValueError):
        policy.check(original, proposed, context())


@pytest.fixture
def render_template(monkeypatch):
    """Exercise shared rendering directly; explicit candidate env is test-only."""
    checkout = os.environ.get("LOCAL_SHARED_CANDIDATE") or os.environ.get("VALIDATOR_CHECKOUT")
    if not checkout:
        pytest.fail("Set VALIDATOR_CHECKOUT (reviewed source) or LOCAL_SHARED_CANDIDATE (local tests only)")
    sys.path.insert(0, checkout)
    from lib import tekton_filters
    from lib.tekton import render_and_merge
    monkeypatch.setattr(tekton_filters, "quantity_floor", tekton_filters.jinja_filter(policy.quantity_floor), raising=False)
    config = str(Path(__file__).resolve().parents[1] / "tekton-templates/pipelineruns")

    def render(data, mode="fix"):
        """Use the production shared template implementation, not local orchestration."""
        errors = []
        result = render_and_merge(config, "common.yaml.j2", data, "example-on-push.yaml",
                                  branch="rhoai-3.5", mode=mode,
                                  cli_vars=policy.decision(data, context())["variables"],
                                  errors=errors if mode == "validate" else None)
        assert not errors
        return result

    return render


@pytest.mark.parametrize("cpu,memory_request,memory_limit", [("200m", "256Mi", "256Mi"), ("2600m", "1Gi", "2Gi")])
def test_actual_template_preservation_and_idempotence(render_template, cpu, memory_request, memory_limit):
    """Actual templates preserve larger reservations and validate after one fix."""
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
    lowered = candidate(original)
    if cpu == "2600m":
        with pytest.raises(ValueError):
            policy.check(original, lowered, context())
