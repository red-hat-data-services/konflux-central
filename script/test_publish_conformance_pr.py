"""Adapter integration with the pinned shared core; never live network writes."""

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import publish_conformance_pr as publisher
import reconciliation_tooling as tooling
from test_conformance_artifact import FILE, SOURCE, payload  # noqa: F401

REPO = "example/central"


def git(root, *args, input=None):
    """Operate only on disposable test repositories, never source or remotes."""
    return subprocess.check_output(["git", "-C", str(root), *args], input=input).decode().strip()


@pytest.fixture
def shared_path():
    """Require the explicitly selected reviewed core checkout for integration tests."""
    path = os.environ.get("PUBLISHER_CHECKOUT")
    if not path:
        pytest.skip("set PUBLISHER_CHECKOUT to the reviewed shared publisher checkout")
    return Path(path)


@pytest.fixture
def case(payload):
    """Use the shared-replay disposable Git fixture without source mutations."""
    return payload[:3]


def test_default_is_real_local_verification_only(case, shared_path, monkeypatch):
    """Run shared verification against actual fixture Git data without any network."""
    root, bundle, _ = case
    core = publisher.load_publisher(shared_path)
    calls = []

    def verify(path, request):
        """Disallow all network commands while retaining real local verification."""
        def run(args, cwd, **kwargs):
            """Record local Git reads and fail if publication is attempted."""
            calls.append(args)
            assert args[0] == "git" and args[1] not in ("push", "fetch", "ls-remote", "add", "commit-tree")
            return core.command(args, cwd, **kwargs)
        return core.verify(path, request, run)

    monkeypatch.setattr(publisher, "load_publisher", lambda path: SimpleNamespace(verify=verify))
    result = publisher.publish(root, REPO, "rhoai-3.5", SOURCE, bundle, shared_path)
    assert result == {"status": "verified", "changeCount": 1, "provenance": {
        "policyRevision": SOURCE, "validatorRevision": publisher.PUBLISHER_REVISION,
        "workerRevision": publisher.PUBLISHER_REVISION, "publisherRevision": publisher.PUBLISHER_REVISION, "branch": "rhoai-3.5"}}
    assert calls and git(root, "diff", "--cached", "--name-only") == ""


def test_explicit_publish_constructs_trusted_request(case, shared_path, monkeypatch):
    """Pass only a fresh M-only policy request to shared publishing, mocked here."""
    root, bundle, manifest = case
    core = publisher.load_publisher(shared_path)
    requests = []

    def publish(path, request):
        """Exercise real shared local checks but record rather than publish remotely."""
        core.verify(path, request)
        requests.append(request)
        return {"status": "published", "url": "https://example.invalid/pr/1"}

    monkeypatch.setattr(publisher, "load_publisher", lambda path: SimpleNamespace(publish=publish))
    assert publisher.publish(root, REPO, "rhoai-3.5", SOURCE, bundle, shared_path, do_publish=True)["status"] == "published"
    request, = requests
    assert request["lifecycle"] == "create-or-update"
    assert request["head"] == "automation/pipelinerun-conformance/rhoai-3.5"
    assert request["marker"] == "Conformance-Generated: true"
    assert request["base_oid"] == manifest["base_oid"]
    assert request["changes"][0]["status"] == "M"
    assert request["changes"][0]["mode"] == "100644"
    assert request["changes"][0]["after_sha256"] == manifest["changes"][0]["after_sha256"]
    assert request["changes"][0]["before_sha256"] != request["changes"][0]["after_sha256"]


@pytest.mark.parametrize("attack", ["staged", "untracked", "extra-change", "mode", "origin"])
def test_core_scope_checks_still_block_adapter(case, shared_path, attack):
    """Retain clean-index, exact-scope, original-mode and repository guards."""
    root, bundle, _ = case
    if attack == "staged":
        (root / "unexpected").write_text("bad")
        git(root, "add", "unexpected")
    elif attack == "untracked":
        (root / "unexpected").write_text("bad")
    elif attack == "extra-change":
        (root / "README.md").write_text("Unrelated edits\n")
    elif attack == "mode":
        (root / FILE).chmod(0o755)
    else:
        git(root, "remote", "set-url", "origin", "https://github.com/wrong/repo.git")
    with pytest.raises(ValueError):
        publisher.publish(root, REPO, "rhoai-3.5", SOURCE, bundle, shared_path)


@pytest.mark.parametrize("revision", [None, "main", "0" * 40])
def test_artifact_cannot_select_publisher(case, revision, monkeypatch, shared_path):
    """Reject artifact-selected revisions before loading any shared code."""
    root, bundle, manifest = case
    manifest["provenance"]["publisherRevision"] = revision
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(publisher, "load_publisher", lambda path: pytest.fail("must not load code"))
    with pytest.raises(ValueError, match="provenance"):
        publisher.publish(root, REPO, "rhoai-3.5", SOURCE, bundle, shared_path)


def test_old_validator_receipt_rejected(case, shared_path):
    """Reject internally consistent receipts from the obsolete floating renderer."""
    root, bundle, manifest = case
    reports = json.loads((bundle / "policy-report.json").read_text())
    for report in reports.values():
        report["validatorRevision"] = "1" * 40
    manifest["provenance"]["validatorRevision"] = "1" * 40
    (bundle / "policy-report.json").write_text(json.dumps(reports))
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="corrected validator"):
        publisher.publish(root, REPO, "rhoai-3.5", SOURCE, bundle, shared_path)


def test_wrong_publisher_checkout_rejected(monkeypatch, tmp_path):
    """Require the trusted fixed revision, independent of planner provenance."""
    monkeypatch.setattr(tooling, "git_read", lambda *args: b"0" * 40)
    with pytest.raises(ValueError, match="reviewed revision"):
        publisher.load_publisher(tmp_path)


def test_modified_publisher_source_is_not_executed(monkeypatch, tmp_path):
    """Verify the core source against its pinned Git object, not HEAD alone."""
    filename = tmp_path / "lib/validated_pr_publisher.py"
    filename.parent.mkdir()
    filename.write_text("raise AssertionError('must not execute')")
    monkeypatch.setattr(tooling, "git_read", lambda path, *args:
                        publisher.PUBLISHER_REVISION.encode() if args[0] == "rev-parse" else b"# reviewed bytes\n")
    with pytest.raises(ValueError, match="reviewed Git contents"):
        publisher.load_publisher(tmp_path)


def test_shared_loading_does_not_import_lib_initializer(shared_path):
    """Use a fresh process to prove shared-core imports avoid lib.__init__."""
    subprocess.run([sys.executable, "-c",
                    "import sys; sys.path.insert(0, sys.argv[1]); import publish_conformance_pr as p; "
                    "p.load_publisher(sys.argv[2]); assert 'lib' not in sys.modules",
                    str(Path(__file__).resolve().parent), str(shared_path)], check=True)


def test_no_changes_verify_without_network(case, shared_path):
    """Accept an empty exact plan without publishing or staging anything."""
    root, bundle, manifest = case
    manifest["changes"] = []
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    assert publisher.publish(root, REPO, "rhoai-3.5", SOURCE, bundle, shared_path)["status"] == "verified"
    assert git(root, "status", "--porcelain") == ""


def test_main_remains_forbidden(case):
    """Keep the release-only publication boundary in the policy adapter."""
    root, _, manifest = case
    with pytest.raises(ValueError, match="not main"):
        publisher.build_request(root, REPO, "main", SOURCE, manifest, {})


def test_pr_description_preserves_exceptions(case):
    """Keep deferred cases and runtime gates when mechanics move to shared code."""
    _, _, manifest = case
    report = json.loads((case[1] / "policy-report.json").read_text())["recheck"]
    report["counts"].update(manual=1, unresolved=1)
    report["files"] += [{"path": "manual.yaml", "status": "manual", "reason": "task budget"},
                        {"path": "dynamic.yaml", "status": "unresolved", "reason": "dynamic ref"}]
    body = publisher.pr_body("rhoai-3.5", SOURCE, report)
    assert "manual.yaml" in body and "dynamic.yaml" in body
    assert "Before merging" in body and "Do not auto-merge" in body


@pytest.mark.parametrize("result,ready", [
    ({"status": "verified", "changeCount": 1}, True),
    ({"status": "verified", "changeCount": 0}, False),
    ({"status": "failed", "changeCount": 1}, False),
    ({"status": "preview", "changeCount": 1}, False),
    ({"status": "published", "changeCount": 1}, False),
    ({}, False),
])
def test_publication_gate_requires_verified_nonempty_changes(result, ready):
    """No-op, preview/unverified or failed results must not request an App token."""
    assert publisher.publication_ready(result) is ready


@pytest.mark.parametrize("condition", ["changes", "empty", "failed"])
def test_cli_writes_token_gate_only_after_verification(case, shared_path, monkeypatch, tmp_path, condition):
    """Exercise standalone gate output without YAML structure mocks or live tokens."""
    root, bundle, manifest = case
    if condition == "empty":
        manifest["changes"] = []
        (bundle / "manifest.json").write_text(json.dumps(manifest))
    elif condition == "failed":
        (bundle / "files" / FILE).write_text("changed after planning\n")
    output = tmp_path / "github-output"
    monkeypatch.setattr(sys, "argv", ["publish", "--path", str(root), "--repository", REPO,
                        "--base", "rhoai-3.5", "--source-sha", SOURCE,
                        "--artifact", str(bundle), "--publisher", str(shared_path),
                        "--github-output", str(output)])
    if condition == "failed":
        with pytest.raises(ValueError):
            publisher.main()
        assert not output.exists()
    else:
        publisher.main()
        expected = "true" if condition == "changes" else "false"
        assert output.read_text() == f"ready_to_publish={expected}\n"
