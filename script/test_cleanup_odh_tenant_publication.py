"""Publication gates use real local patches but never remote mutations."""
import hashlib
import json
import subprocess

import pytest

from test_cleanup_odh_tenant_pipelines import cleanup, init_repo, resource, tooling, POLICY


@pytest.mark.parametrize("failure", ["remote-advanced", "patch-tampered", "out-of-scope", "namespace-changed"])
def test_publication_stops_before_commit_or_push(tmp_path, tooling, monkeypatch, failure):
    target = tmp_path / "target"
    resource(target, "old")
    (target / "app.txt").write_text("keep\n")
    init_repo(target)
    report = tmp_path / "report"
    data = cleanup.prepare(target, tooling, POLICY, "rhoai-3.6", "ai-gateway-controller", report)
    patch = report / "cleanup.patch"
    if failure == "patch-tampered":
        patch.write_text(patch.read_text() + "tampered")
    if failure == "out-of-scope":
        # Even a recomputed receipt digest must not permit non-Tekton changes.
        patch.write_text(patch.read_text() + "diff --git a/app.txt b/app.txt\n"
                         "--- a/app.txt\n+++ b/app.txt\n@@ -1 +1 @@\n-keep\n+changed\n")
        data["patch_sha256"] = hashlib.sha256(patch.read_bytes()).hexdigest()
    if failure == "namespace-changed":
        data["deleted_files"] = []
    (report / "result.json").write_text(json.dumps(data))
    calls = []
    original = cleanup.command
    def safe(args, **kwargs):
        args = [str(a) for a in args]
        calls.append(args)
        assert not any(word in args for word in ("commit", "push"))
        if args[0] == "gh":
            assert args[1] == "api"
            return subprocess.CompletedProcess(args, 0, "0" * 40 if failure == "remote-advanced" else data["base"], "")
        return original(args, **kwargs)
    monkeypatch.setattr(cleanup, "command", safe)
    monkeypatch.setattr(cleanup, "existing_pr", lambda *a: None)
    with pytest.raises(ValueError):
        cleanup.publish(target, report)
    assert not any("commit" in c or "push" in c for c in calls)


def test_publication_happy_path_with_mocked_remote_mutations(tmp_path, tooling, monkeypatch):
    target = tmp_path / "target"
    resource(target, "old")
    resource(target, "retained", "rhoai-tenant")
    init_repo(target)
    report = tmp_path / "report"
    data = cleanup.prepare(target, tooling, POLICY, "rhoai-3.6", "ai-gateway-controller", report)
    original = cleanup.command
    mutations = []
    def safe(args, **kwargs):
        args = [str(a) for a in args]
        if args[0] == "gh":
            if args[1] == "api":
                return subprocess.CompletedProcess(args, 0, data["base"], "")
            assert args[1:3] == ["pr", "create"]
            mutations.append(args)
            return subprocess.CompletedProcess(args, 0, "https://example.com/new-pr", "")
        if "commit" in args or "push" in args:
            mutations.append(args)
            return subprocess.CompletedProcess(args, 0, "", "")
        return original(args, **kwargs)
    monkeypatch.setattr(cleanup, "command", safe)
    monkeypatch.setattr(cleanup, "existing_pr", lambda *a: None)
    assert cleanup.publish(target, report) == {"status": "published", "url": "https://example.com/new-pr"}
    assert len(mutations) == 3
    assert "--signoff" in mutations[0]
    assert "--force" not in str(mutations)
    assert cleanup.MARKER in (report / "pr.md").read_text()
    assert cleanup.snapshot(target)[1] == []


def test_existing_pr_is_reported_without_touching_checkout(tmp_path, monkeypatch):
    report = tmp_path / "report"
    report.mkdir()
    data = {"release": "rhoai-3.6", "repository": "ai-gateway-controller", "status": "ready",
            "policy_digest": "a" * 64, "branch": "automation/cleanup-odh-tenant-rhoai-3.6-" + "a" * 12}
    (report / "result.json").write_text(json.dumps(data))
    monkeypatch.setattr(cleanup, "existing_pr", lambda *a: "https://example.com/existing")
    assert cleanup.publish(tmp_path / "absent", report) == {"status": "existing-pr", "url": "https://example.com/existing"}
