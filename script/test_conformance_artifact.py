"""Scan correctness in the trusted planner; exact replay is tested separately."""

from copy import deepcopy
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import subprocess

import pytest
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

import conformance_artifact as artifact
from publish_conformance_pr import replay
from reconciliation_tooling import REVISION

FILE = "pipelineruns/example/.tekton/push.yaml"
SOURCE = "2" * 40


def encode(data):
    """Serialize fixture policy documents."""
    stream = StringIO()
    YAML().dump(data, stream)
    return stream.getvalue().encode()


def set_payload(bundle, manifest, contents):
    """Forge byte-integrity metadata as an untrusted planner could."""
    manifest["changes"][0]["after_sha256"] = hashlib.sha256(contents).hexdigest()
    (bundle / "files" / FILE).write_bytes(contents)
    (bundle / "manifest.json").write_text(json.dumps(manifest))


@pytest.fixture
def payload(tmp_path):
    """Create complete disposable Git and artifact fixtures, not source commits."""
    checkout = os.environ.get("PUBLISHER_CHECKOUT")
    if not checkout:
        pytest.skip("set PUBLISHER_CHECKOUT to the reviewed shared checkout")
    root, bundle = tmp_path / "target", tmp_path / "artifact"
    (root / FILE).parent.mkdir(parents=True)
    (bundle / "files" / FILE).parent.mkdir(parents=True)
    original = {"apiVersion": "tekton.dev/v1", "kind": "PipelineRun", "metadata": {"name": "test"},
                "spec": {"pipelineSpec": {"tasks": [{"name": artifact.SCAN}]}}}
    original_bytes = encode(original)
    (root / FILE).write_bytes(original_bytes)
    (root / "README.md").write_text("Fixture repository\n")
    env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}

    def git(*args, input=None):
        """Write only disposable Git fixture objects and metadata."""
        return subprocess.check_output(["git", "-C", str(root), *args], env=env, input=input).decode().strip()

    git("init", "-q", "--initial-branch=rhoai-3.5")
    git("remote", "add", "origin", "https://github.com/example/central.git")
    git("add", ".")
    commit = git("commit-tree", git("write-tree"), input=b"Disposable fixture\n")
    git("update-ref", "HEAD", commit)
    candidate = deepcopy(original)
    candidate["spec"]["taskRunSpecs"] = [{"pipelineTaskName": artifact.SCAN, "stepSpecs": [
        {"name": artifact.SCAN, "computeResources": {
            "requests": {"cpu": "250m", "memory": "512Mi"}, "limits": {"memory": "512Mi"}}}]}]
    manifest = {"version": 1, "base_oid": commit,
                "provenance": {"branch": "rhoai-3.5", "policyRevision": SOURCE,
                               "validatorRevision": REVISION, "workerRevision": REVISION, "publisherRevision": REVISION},
                "changes": [{"path": FILE, "status": "M", "mode": "100644",
                             "before_sha256": hashlib.sha256(original_bytes).hexdigest()}]}
    reports = {"plan": {"branch": "rhoai-3.5", "validatorRevision": REVISION,
                        "fixApplied": True, "counts": {"planned": 1}, "files": []},
               "recheck": {"branch": "rhoai-3.5", "validatorRevision": REVISION,
                           "fixApplied": False, "counts": {"conformant": 1}, "files": []}}
    (bundle / "policy-report.json").write_text(json.dumps(reports))
    set_payload(bundle, manifest, encode(candidate))
    return root, bundle, manifest, original, candidate, Path(checkout)


def test_scan_policy_accepts_baseline(payload):
    """Accept scoped baseline changes and their already-correct state."""
    root, _, _, original, candidate, _ = payload
    for before in (original, candidate):
        artifact.validate_change(before, candidate, root, "rhoai-3.5", YAML(typ="safe"))


@pytest.mark.parametrize("attack", ["timeout", "image", "metadata", "low-cpu", "low-memory", "cpu-limit", "unequal-memory", "inflated-resources"])
def test_scan_policy_rejects_invalid_candidates(payload, attack):
    """Retain scan field/resource correctness checks in the trusted planner."""
    root, bundle, manifest, original, candidate, tooling = payload
    resources = candidate["spec"]["taskRunSpecs"][0]["stepSpecs"][0]["computeResources"]
    if attack == "timeout":
        candidate["spec"]["timeouts"] = {"pipeline": "24h"}
    elif attack == "image":
        candidate["spec"]["pipelineSpec"]["tasks"][0]["taskSpec"] = {"steps": [{"name": "steal", "image": "evil"}]}
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
        artifact.validate_change(original, candidate, root, "rhoai-3.5", YAML(typ="safe"))
    assert (root / FILE).read_bytes() == before


@pytest.mark.parametrize("field", ["branch", "policyRevision", "workerRevision", "publisherRevision"])
def test_mismatched_provenance_rejected(payload, field):
    """Compare provenance to consumer expectations, never code-selecting artifact data."""
    root, bundle, manifest, _, _, tooling = payload
    manifest["provenance"][field] = "wrong"
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        replay(root, bundle, "rhoai-3.5", SOURCE, tooling)


def test_hash_mismatch_rejected(payload):
    """Do not replay mutated bytes even if the report claimed validation."""
    root, bundle, _, _, _, tooling = payload
    (bundle / "files" / FILE).write_text("changed\n")
    with pytest.raises(ValueError, match="mismatch"):
        replay(root, bundle, "rhoai-3.5", SOURCE, tooling)


@pytest.mark.parametrize("name", ["../escape.yaml", "/tmp/escape.yaml", ".github/workflows/bad.yaml", "pipelineruns/x/.tekton/evil.py"])
def test_artifact_paths_are_not_commands_or_arbitrary_files(payload, name):
    """Keep the local PipelineRun layout restriction in addition to generic safety."""
    root, bundle, manifest, _, _, tooling = payload
    manifest["changes"][0]["path"] = name
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        replay(root, bundle, "rhoai-3.5", SOURCE, tooling)


def test_yaml_object_tags_cannot_execute():
    """Reject executable tags when parsing target data in the planning phase."""
    with pytest.raises(YAMLError):
        YAML(typ="safe").load('!!python/object/apply:os.system ["echo must-not-run"]')



def test_unrelated_checkout_edits_are_preserved(payload):
    """Reject unrelated target bytes before any replay writes."""
    root, bundle, _, _, _, tooling = payload
    (root / FILE).write_text("local edits\n")
    with pytest.raises(ValueError):
        replay(root, bundle, "rhoai-3.5", SOURCE, tooling)
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
