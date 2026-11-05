"""Untrusted planner artifacts must pass independent checks before publication."""

from copy import deepcopy
import hashlib
from io import StringIO
import json
from pathlib import Path

import pytest
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

import conformance_artifact as artifact

FILE = "pipelineruns/example/.tekton/push.yaml"
BASE, SOURCE = "3" * 40, "2" * 40


def encode(data):
    """Serialize fixture data without importing any shared validator code."""
    stream = StringIO()
    YAML().dump(data, stream)
    return stream.getvalue().encode()


def set_payload(bundle, manifest, contents):
    """Model an attacker who can forge both file bytes and reported hashes."""
    digest = hashlib.sha256(contents).hexdigest()
    manifest["plan"]["files"][0]["plannedSha256"] = digest
    manifest["recheck"]["files"][0]["sha256"] = digest
    (bundle / "files" / FILE).write_bytes(contents)
    (bundle / "manifest.json").write_text(json.dumps(manifest))


@pytest.fixture
def payload(tmp_path):
    """Create separate checkout/artifact trees and a read-only fake Git source."""
    root, bundle = tmp_path / "target", tmp_path / "artifact"
    (root / FILE).parent.mkdir(parents=True)
    (bundle / "files" / FILE).parent.mkdir(parents=True)
    original = {"apiVersion": "tekton.dev/v1", "kind": "PipelineRun", "metadata": {"name": "test"},
                "spec": {"pipelineSpec": {"tasks": [{"name": artifact.SCAN}]}}}
    original_bytes = encode(original)
    (root / FILE).write_bytes(original_bytes)
    candidate = deepcopy(original)
    candidate["spec"]["taskRunSpecs"] = [{"pipelineTaskName": artifact.SCAN, "stepSpecs": [
        {"name": artifact.SCAN, "computeResources": {
            "requests": {"cpu": "250m", "memory": "512Mi"}, "limits": {"memory": "512Mi"}}}]}]
    manifest = {"branch": "rhoai-3.5", "sourceCommit": SOURCE, "targetCommit": BASE,
                "plan": {"branch": "rhoai-3.5", "validatorRevision": "1" * 40,
                         "fixApplied": True, "counts": {"planned": 1},
                         "files": [{"path": FILE, "status": "planned"}]},
                "recheck": {"branch": "rhoai-3.5", "validatorRevision": "1" * 40,
                            "fixApplied": False, "counts": {"conformant": 1},
                            "files": [{"path": FILE, "status": "conformant"}]}}
    set_payload(bundle, manifest, encode(candidate))

    def read_git(where, *args):
        """Supply immutable original commit data rather than mutable checkout bytes."""
        assert Path(where) == root
        if args == ("rev-parse", "HEAD"):
            return BASE.encode()
        assert args == ("show", f"HEAD:{FILE}")
        return original_bytes

    return root, bundle, manifest, original, candidate, read_git


def test_valid_artifact_is_prepared_and_rechecked_idempotently(payload):
    """Accept policy-only changes and validate again against original Git data."""
    root, bundle, _, _, candidate, read_git = payload
    artifact.prepare(root, bundle, "rhoai-3.5", SOURCE, read_git)
    assert YAML(typ="safe").load((root / FILE).read_bytes()) == candidate
    first = (root / FILE).read_bytes()
    artifact.prepare(root, bundle, "rhoai-3.5", SOURCE, read_git)
    assert (root / FILE).read_bytes() == first


@pytest.mark.parametrize("attack", ["timeout", "image", "metadata", "low-cpu", "low-memory", "cpu-limit", "unequal-memory", "inflated-resources"])
def test_forged_hashes_cannot_authorize_other_changes(payload, attack):
    """Reject malicious content even when both reports carry matching hashes."""
    root, bundle, manifest, _, candidate, read_git = payload
    resources = candidate["spec"]["taskRunSpecs"][0]["stepSpecs"][0]["computeResources"]
    if attack == "timeout":
        candidate["spec"]["timeouts"] = {"pipeline": "24h"}
    elif attack == "image":
        candidate["spec"]["pipelineSpec"]["tasks"][0]["taskSpec"] = {"steps": [{"name": "steal", "image": "evil", "script": "echo bad"}]}
    elif attack == "metadata":
        candidate["metadata"]["annotations"] = {"unexpected": "value"}
    elif attack == "low-cpu":
        resources["requests"]["cpu"] = "1m"
    elif attack == "low-memory":
        resources["requests"]["memory"] = resources["limits"]["memory"] = "1Mi"
    elif attack == "cpu-limit":
        resources["limits"]["cpu"] = "200m"
    elif attack == "inflated-resources":
        resources["requests"]["cpu"] = "1000000"
        resources["requests"]["memory"] = resources["limits"]["memory"] = "1Ei"
    else:
        resources["limits"]["memory"] = "1Gi"
    set_payload(bundle, manifest, encode(candidate))
    before = (root / FILE).read_bytes()
    with pytest.raises(ValueError):
        artifact.prepare(root, bundle, "rhoai-3.5", SOURCE, read_git)
    assert (root / FILE).read_bytes() == before


@pytest.mark.parametrize("field", ["branch", "sourceCommit", "targetCommit"])
def test_mismatched_provenance_rejected(payload, field):
    """Do not publish artifacts from another branch, workflow commit, or base tip."""
    root, bundle, manifest, _, _, read_git = payload
    manifest[field] = "wrong"
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="mismatched"):
        artifact.prepare(root, bundle, "rhoai-3.5", SOURCE, read_git)


def test_hash_mismatch_rejected(payload):
    """Reject bytes changed after reporting before any checkout writes."""
    root, bundle, _, _, _, read_git = payload
    (bundle / "files" / FILE).write_text("changed\n")
    with pytest.raises(ValueError, match="hash"):
        artifact.prepare(root, bundle, "rhoai-3.5", SOURCE, read_git)


@pytest.mark.parametrize("name", ["../escape.yaml", "/tmp/escape.yaml", ".github/workflows/bad.yaml", "pipelineruns/x/.tekton/evil.py"])
def test_artifact_paths_are_not_commands_or_arbitrary_files(payload, name):
    """Restrict received files to relative PipelineRun paths, not executable code."""
    root, bundle, manifest, _, _, read_git = payload
    manifest["plan"]["files"][0]["path"] = name
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="invalid artifact path"):
        artifact.prepare(root, bundle, "rhoai-3.5", SOURCE, read_git)


def test_yaml_object_tags_cannot_execute(payload):
    """Parse payload YAML with safe constructors, never arbitrary Python objects."""
    root, bundle, manifest, _, _, read_git = payload
    set_payload(bundle, manifest, b'!!python/object/apply:os.system ["echo must-not-run"]\n')
    with pytest.raises(YAMLError):
        artifact.prepare(root, bundle, "rhoai-3.5", SOURCE, read_git)


def test_unrelated_checkout_edits_are_preserved(payload):
    """Refuse artifact preparation over local changes not equal to the planned bytes."""
    root, bundle, _, _, _, read_git = payload
    (root / FILE).write_text("local edits\n")
    with pytest.raises(ValueError, match="unrelated"):
        artifact.prepare(root, bundle, "rhoai-3.5", SOURCE, read_git)
    assert (root / FILE).read_text() == "local edits\n"


def test_yaml_aliases_cannot_hide_changes_to_other_tasks(payload):
    """Do not let alias sharing erase unrelated changes during scope comparison."""
    root, _, _, original, _, _ = payload
    shared = {"requests": {"cpu": "200m", "memory": "256Mi"},
              "limits": {"cpu": "200m", "memory": "256Mi"}}
    original["spec"]["taskRunSpecs"] = [
        {"pipelineTaskName": artifact.SCAN, "stepSpecs": [{"name": artifact.SCAN, "computeResources": shared}]},
        {"pipelineTaskName": "build", "stepSpecs": [{"name": "build", "computeResources": shared}]},
    ]
    candidate = deepcopy(original)
    resources = candidate["spec"]["taskRunSpecs"][0]["stepSpecs"][0]["computeResources"]
    resources["requests"] = {"cpu": "250m", "memory": "512Mi"}
    resources["limits"] = {"memory": "512Mi"}
    with pytest.raises(ValueError, match="outside"):
        artifact.validate_change(original, candidate, root, "rhoai-3.5", YAML(typ="safe"))


def test_invalid_later_file_blocks_all_artifact_copies(payload):
    """Validate the entire bundle before copying even the first valid change."""
    root, bundle, manifest, original, candidate, _ = payload
    second = "pipelineruns/example/.tekton/second.yaml"
    original_bytes = encode(original)
    (root / second).write_bytes(original_bytes)
    bad = deepcopy(candidate)
    bad["spec"]["timeouts"] = {"pipeline": "24h"}
    bad_bytes = encode(bad)
    (bundle / "files" / second).write_bytes(bad_bytes)
    digest = hashlib.sha256(bad_bytes).hexdigest()
    manifest["plan"]["files"].append({"path": second, "status": "planned", "plannedSha256": digest})
    manifest["recheck"]["files"].append({"path": second, "status": "conformant", "sha256": digest})
    (bundle / "manifest.json").write_text(json.dumps(manifest))

    def read_git(where, *args):
        """Return originals for both files while leaving the checkout untouched."""
        return BASE.encode() if args == ("rev-parse", "HEAD") else original_bytes

    with pytest.raises(ValueError, match="outside"):
        artifact.prepare(root, bundle, "rhoai-3.5", SOURCE, read_git)
    assert (root / FILE).read_bytes() == (root / second).read_bytes() == original_bytes


def test_cyclic_yaml_graph_is_rejected():
    """Fail closed on recursive YAML aliases rather than expanding forever."""
    data = []
    data.append(data)
    with pytest.raises(ValueError, match="cyclic"):
        artifact.detached_data(data)


@pytest.mark.parametrize("resource,value", [("cpu", "2600m"), ("memory", "2Gi")])
def test_original_larger_reservations_cannot_be_reduced(payload, resource, value):
    """Derive minimum accepted reservations from original Git data, not reports."""
    root, _, _, original, candidate, _ = payload
    original["spec"]["taskRunSpecs"] = deepcopy(candidate["spec"]["taskRunSpecs"])
    original["spec"]["taskRunSpecs"][0]["stepSpecs"][0]["computeResources"]["requests"][resource] = value
    with pytest.raises(ValueError, match="preservation"):
        artifact.validate_change(original, candidate, root, "rhoai-3.5", YAML(typ="safe"))


def test_task_level_budget_cannot_be_bypassed_by_artifact(payload):
    """Keep deferred task-level budgets out of the automatically published subset."""
    root, _, _, original, candidate, _ = payload
    original["spec"]["taskRunSpecs"] = [{"pipelineTaskName": artifact.SCAN,
                                        "computeResources": {"limits": {"memory": "8Gi"}}}]
    with pytest.raises(ValueError, match="manual review"):
        artifact.validate_change(original, candidate, root, "rhoai-3.5", YAML(typ="safe"))


def test_publisher_imports_do_not_load_shared_validator_code():
    """Keep fresh publisher processes independent of the shared lib package."""
    import subprocess
    import sys

    subprocess.run([sys.executable, "-c",
                    "import sys; sys.path.insert(0, sys.argv[1]); "
                    "import conformance_artifact, publish_conformance_pr; "
                    "assert not any(name == 'lib' or name.startswith('lib.') for name in sys.modules)",
                    str(Path(__file__).resolve().parent)], check=True)


def test_export_is_data_only(payload, monkeypatch, tmp_path):
    """Export JSON and YAML bytes, not a workspace or executable publisher."""
    root, bundle, manifest, _, candidate, read_git = payload
    (root / FILE).write_bytes(encode(candidate))
    monkeypatch.setattr(artifact, "git_read", read_git)
    dest = tmp_path / "exported"
    artifact.export(root, dest, "rhoai-3.5", SOURCE, manifest["plan"], manifest["recheck"])
    assert sorted(str(path.relative_to(dest)) for path in dest.rglob("*") if path.is_file()) == ["files/" + FILE, "manifest.json"]
    artifact.prepare(root, dest, "rhoai-3.5", SOURCE, read_git)
