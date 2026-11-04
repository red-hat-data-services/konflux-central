"""Tests for local planning, resource floors, and preservation guardrails."""

from copy import deepcopy
import json
import os
from pathlib import Path

import pytest
from ruamel.yaml import YAML

import reconcile_pipelineruns as runner


@pytest.fixture(scope="module", autouse=True)
def validator():
    """Use an explicitly supplied checkout without fetching or installing."""
    path = os.environ.get("VALIDATOR_CHECKOUT")
    if not path:
        pytest.fail("Set VALIDATOR_CHECKOUT to the shared validator checkout")
    runner.load_validator(path)
    return path


@pytest.mark.parametrize("left,right", [("1Gi", "1024Mi"), ("250m", ".25"), ("1e3", "1k"), ("1u", "1000n")])
def test_quantity_equivalence(left, right):
    """Equivalent Kubernetes quantity spellings must compare equally."""
    assert runner.quantity(left) == runner.quantity(right)


@pytest.mark.parametrize("value", ["bad", "-1", "NaN", "1MiB", "", "1.2.3"])
def test_invalid_quantities_fail(value):
    """Reject malformed or negative resource quantities."""
    with pytest.raises(ValueError):
        runner.quantity(value)


def plr():
    """Build a minimal PipelineRun fixture with unrelated fields to preserve."""
    return {"apiVersion": "tekton.dev/v1", "kind": "PipelineRun", "metadata": {
        "name": "example-on-push", "annotations": {"example": "preserve"}}, "spec": {
        "params": [{"name": "custom", "value": "keep"}],
        "pipelineRef": {"resolver": "git", "params": [
            {"name": "url", "value": runner.CENTRAL},
            {"name": "revision", "value": "{{ target_branch }}"},
            {"name": "pathInRepo", "value": "pipelines/container-build.yaml"},
        ]}}}


def config():
    """Load the general template set used by reconciliation tests."""
    from lib.tekton_config import load_config
    return load_config(str(Path(__file__).resolve().parents[1] / "tekton-templates/pipelineruns"))


def test_baseline_preserves_unrelated_overrides_and_is_idempotent():
    """Apply baseline resources without changing unrelated tasks or repeated results."""
    data = plr()
    data["spec"]["taskRunSpecs"] = [{"pipelineTaskName": "build", "timeout": "3h"}]
    original = deepcopy(data)
    result = runner.reconcile(data, "example.yaml", "rhoai-3.5", config(), runner.scan_inputs(data))
    assert runner.unowned(result) == runner.unowned(original)
    assert data == original
    step = result["spec"]["taskRunSpecs"][1]["stepSpecs"][0]
    assert step["computeResources"] == {"requests": {"cpu": "250m", "memory": "512Mi"}, "limits": {"memory": "512Mi"}}
    assert runner.reconcile(result, "example.yaml", "rhoai-3.5", config(), runner.scan_inputs(result)) == result


def test_larger_step_reservations_are_preserved_and_cpu_limit_removed():
    """Preserve larger reservations while removing only the scan CPU ceiling."""
    data = plr()
    data["spec"]["taskRunSpecs"] = [{"pipelineTaskName": runner.SCAN, "stepSpecs": [
        {"name": "output-results", "computeResources": {"limits": {"memory": "64Mi"}}},
        {"name": runner.SCAN, "computeResources": {
            "requests": {"cpu": "2600m", "memory": "1Gi"}, "limits": {"cpu": "2600m", "memory": "2Gi"}}},
    ]}]
    result = runner.reconcile(data, "example.yaml", "rhoai-3.5", config(), runner.scan_inputs(data))
    steps = result["spec"]["taskRunSpecs"][0]["stepSpecs"]
    assert steps[0] == data["spec"]["taskRunSpecs"][0]["stepSpecs"][0]
    assert steps[1]["computeResources"] == {"requests": {"cpu": "2600m", "memory": "2Gi"}, "limits": {"memory": "2Gi"}}


def test_task_budget_requires_manual_review():
    """Leave task-level budgets for explicit review rather than mixing scopes."""
    data = plr()
    data["spec"]["taskRunSpecs"] = [{"pipelineTaskName": runner.SCAN, "computeResources": {"limits": {"memory": "8Gi"}}}]
    assert runner.scan_inputs(data) is None


def test_duplicate_scan_overrides_rejected():
    """Reject duplicate task identities instead of selecting an arbitrary override."""
    data = plr()
    data["spec"]["taskRunSpecs"] = [{"pipelineTaskName": runner.SCAN}] * 2
    with pytest.raises(ValueError, match="duplicate"):
        runner.scan_inputs(data)


def test_applicability_skips_missing_scan_and_reports_dynamic_ref(tmp_path):
    """Distinguish absent scan tasks from references needing explicit resolution."""
    data = plr()
    data["spec"]["pipelineSpec"] = {"tasks": [{"name": "build"}]}
    assert runner.applicability(data, tmp_path, "rhoai-3.5", YAML())[0] == "skipped"
    del data["spec"]["pipelineSpec"]
    data["spec"]["pipelineRef"]["params"][1]["value"] = "{{ cel: complex-expression }}"
    assert runner.applicability(data, tmp_path, "rhoai-3.5", YAML())[0] == "unresolved"


def test_scope_guard_rejects_unrelated_template_change(tmp_path):
    """Prevent templates from changing fields outside the initial resource rule."""
    from lib.tekton_config import load_config
    (tmp_path / "config.yaml").write_text("templates:\n- name: push\n  file: push.yaml.j2\n  match_fn: is_push_pipeline\n")
    (tmp_path / "push.yaml.j2").write_text("spec:\n  timeouts:\n    pipeline: 24h\n")
    data = plr()
    with pytest.raises(ValueError, match="outside"):
        runner.reconcile(data, "example.yaml", "rhoai-3.5", load_config(str(tmp_path)), runner.scan_inputs(data))


def test_validator_reports_actual_checkout_revision(validator):
    """Record the resolved checkout SHA rather than a hard-coded revision."""
    import subprocess

    expected = subprocess.check_output(["git", "-C", validator, "rev-parse", "HEAD"], text=True).strip()
    assert runner.load_validator(validator) == expected


def test_cli_preview_fix_and_repeat(tmp_path, validator):
    """Verify preview is read-only and applied fixes survive a second CLI pass."""
    root = tmp_path / "target"
    path = root / "pipelineruns/example/.tekton/example.yaml"
    path.parent.mkdir(parents=True)
    data = plr()
    data["spec"]["pipelineSpec"] = {"tasks": [{"name": runner.SCAN}]}
    yaml = YAML()
    with path.open("w") as stream:
        yaml.dump(data, stream)
    original = path.read_text()
    report = tmp_path / "report"
    args = ["--path", str(root), "--validator", validator, "--branch", "rhoai-3.5", "--report-dir", str(report)]
    assert runner.main(args) == 0
    assert path.read_text() == original
    preview = json.loads((report / "report.json").read_text())
    assert preview["counts"] == {"planned": 1}
    assert preview["validatorRevision"] == runner.load_validator(validator)
    assert (report / "changes.patch").read_text()
    assert runner.main(args + ["--fix"]) == 0
    changed = path.read_text()
    assert changed != original
    assert runner.main(args) == 0
    assert path.read_text() == changed
    assert json.loads((report / "report.json").read_text())["counts"] == {"conformant": 1}
    assert (report / "changes.patch").read_text() == ""


def test_existing_small_step_is_raised_without_changing_task_timeout():
    """Raise undersized step resources without extending the task timeout."""
    data = plr()
    data["spec"]["taskRunSpecs"] = [{"pipelineTaskName": runner.SCAN, "timeout": "2h", "stepSpecs": [
        {"name": runner.SCAN, "computeResources": {
            "requests": {"cpu": "200m", "memory": "256Mi"},
            "limits": {"cpu": "200m", "memory": "256Mi"}}},
    ]}]
    result = runner.reconcile(data, "example.yaml", "rhoai-3.5", config(), runner.scan_inputs(data))
    task = result["spec"]["taskRunSpecs"][0]
    assert task["timeout"] == "2h"
    assert task["stepSpecs"][0]["computeResources"] == {
        "requests": {"cpu": "250m", "memory": "512Mi"}, "limits": {"memory": "512Mi"}}


def test_branch_local_pipeline_without_scan_is_skipped(tmp_path):
    """Do not insert scan overrides into a Pipeline that lacks that task."""
    data = plr()
    path = tmp_path / "pipelines/container-build.yaml"
    path.parent.mkdir()
    path.write_text("kind: Pipeline\nspec:\n  tasks:\n  - name: build\n")
    assert runner.applicability(data, tmp_path, "rhoai-3.5", YAML())[0] == "skipped"


def test_pipeline_source_cannot_escape_through_symlinked_parent(tmp_path):
    """Reject Pipeline source reads that escape through a symlinked directory."""
    root = tmp_path / "target"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "container-build.yaml").write_text("kind: Pipeline\nspec:\n  tasks: []\n")
    (root / "pipelines").symlink_to(outside, target_is_directory=True)
    assert runner.applicability(plr(), root, "rhoai-3.5", YAML())[0] == "unresolved"


def test_errors_prevent_all_fix_writes(tmp_path, validator):
    """Block all planned writes when any input fails validation."""
    root = tmp_path / "target"
    path = root / "pipelineruns/example/.tekton/good.yaml"
    path.parent.mkdir(parents=True)
    data = plr()
    data["spec"]["pipelineSpec"] = {"tasks": [{"name": runner.SCAN}]}
    with path.open("w") as stream:
        YAML().dump(data, stream)
    original = path.read_text()
    path.with_name("bad.yaml").write_text("spec: [\n")
    assert runner.main(["--path", str(root), "--validator", validator, "--branch", "rhoai-3.5",
                        "--report-dir", str(tmp_path / "report"), "--fix"]) == 1
    assert path.read_text() == original
