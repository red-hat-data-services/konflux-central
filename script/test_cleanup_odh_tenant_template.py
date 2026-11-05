"""Actual included cleanup-template semantics; generic coordinator tests live in shared."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

POLICY = Path(__file__).resolve().parents[1] / "tekton-templates/cleanup-odh-tenant"


@pytest.fixture
def validator():
    """Use explicitly supplied shared source without installing dependencies."""
    root = os.environ.get("ENFORCEMENT_TEST_TOOLING")
    if not root:
        pytest.skip("set ENFORCEMENT_TEST_TOOLING for actual template tests")
    return Path(root) / "scripts/validate-tekton"


def preview(validator, target):
    """Run ordinary preview in a disposable Git fixture, without any commits."""
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    result = subprocess.run([sys.executable, str(validator), "--path", str(target),
        "--config-dir", str(POLICY), "--branch", "rhoai-3.6", "--no-checkout",
        "--fix", "--dry-run", "--json"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout) if result.stdout.strip() else {"files": {}}


@pytest.mark.parametrize("namespace,deleted", [
    ("open-data-hub-tenant", True), ("rhoai-tenant", False), (None, False),
    ("omitted", False), ("", False), ("open-data-hub-tenant-extra", False),
    ("Open-Data-Hub-Tenant", False),
])
@pytest.mark.parametrize("name", ["arbitrary-group-test", "odh-looking-push"])
def test_only_exact_explicit_execution_namespace_is_retired(tmp_path, validator, namespace, deleted, name):
    """Names/events/labels do not replace the explicit namespace retirement rule."""
    folder = tmp_path / ".tekton"
    folder.mkdir()
    path = folder / f"{name}.yaml"
    metadata = {"name": name, "labels": {"example.com/tenant": "open-data-hub-tenant"},
                "annotations": {"pipelinesascode.tekton.dev/on-event": "[pull_request]"}}
    if namespace != "omitted":
        metadata["namespace"] = namespace
    path.write_text(json.dumps({"apiVersion": "tekton.dev/v1", "kind": "PipelineRun",
                               "metadata": metadata}))
    original = path.read_bytes()
    report = preview(validator, tmp_path)
    assert report.get("fixed", []) == ([path.name] if deleted else [])
    assert (path.name not in report["files"]) is deleted
    assert path.read_bytes() == original


def test_cleanup_leaves_resource_only_stream_unchanged(tmp_path, validator):
    """The cleanup policy selects PipelineRuns, not other ODH-namespaced resources."""
    folder = tmp_path / ".tekton"
    folder.mkdir()
    path = folder / "resources.yaml"
    path.write_text("kind: Task\nmetadata:\n  name: one\n  namespace: open-data-hub-tenant\n"
                    "---\nkind: ConfigMap\nmetadata:\n  name: two\n  namespace: open-data-hub-tenant\n")
    original = path.read_bytes()
    assert not preview(validator, tmp_path)["files"]
    assert path.read_bytes() == original
