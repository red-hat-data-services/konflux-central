"""Real file-safety tests and optional end-to-end shared-validator smoke tests."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

SPEC = importlib.util.spec_from_file_location("cleanup", Path(__file__).with_name("cleanup_odh_tenant_pipelines.py"))
cleanup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cleanup)
POLICY = Path(__file__).resolve().parents[1] / "tekton-templates/cleanup-odh-tenant"


def init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-qm", "fixture", "--allow-empty"], check=True)
    return path


def resource(root, name, namespace="open-data-hub-tenant", kind="PipelineRun"):
    directory = root / ".tekton"
    directory.mkdir(parents=True, exist_ok=True)
    text = f"apiVersion: tekton.dev/v1\nkind: {kind}\nmetadata:\n  name: {name}\n"
    if namespace is not None:
        text += f"  namespace: {namespace}\n"
    (directory / (name + ".yaml")).write_text(text)


def test_namespace_not_names_or_images(tmp_path):
    resource(tmp_path, "arbitrary-group-test")
    resource(tmp_path, "odh-push", "rhoai-tenant")
    resource(tmp_path, "odh-tag", None)
    resource(tmp_path, "task", kind="Task")
    files, targets = cleanup.snapshot(tmp_path)
    assert len(files) == 4
    assert targets == [".tekton/arbitrary-group-test.yaml"]


def test_resource_stream_is_preserved(tmp_path):
    resource(tmp_path, "tasks", kind="Task")
    path = tmp_path / ".tekton/tasks.yaml"
    path.write_text(path.read_text() + "---\nkind: Task\nmetadata:\n  name: second\n")
    assert cleanup.snapshot(tmp_path)[1] == []


@pytest.mark.parametrize("text", [
    "kind: PipelineRun\n---\nkind: Task\n",
    "kind: PipelineRun\nkind: Task\n",
    "kind: Task\n---\n[broken\n",
])
def test_bad_yaml_and_bundled_runs_block(tmp_path, text):
    (tmp_path / ".tekton").mkdir()
    (tmp_path / ".tekton/file.yaml").write_text(text)
    with pytest.raises(ValueError, match=r"file\.yaml"):
        cleanup.snapshot(tmp_path)


@pytest.mark.parametrize("directory_link", [False, True])
def test_symlinks_block(tmp_path, directory_link):
    outside = tmp_path / "outside"
    outside.mkdir()
    if directory_link:
        (tmp_path / ".tekton").symlink_to(outside)
    else:
        (tmp_path / ".tekton").mkdir()
        (tmp_path / ".tekton/file.yaml").symlink_to(outside / "absent")
    with pytest.raises(ValueError, match="symlink|unsafe"):
        cleanup.snapshot(tmp_path)


@pytest.mark.parametrize("after,expected", [
    ({}, []), ({"retained": 2}, ["deleted"]),
    ({"retained": 1, "added": 3}, ["deleted"]),
])
def test_scope_or_retained_mutations_block(after, expected):
    with pytest.raises(ValueError):
        cleanup.verify_deletions({"retained": 1, "deleted": 2}, after, expected)


def test_inventory_uses_selected_release_dirs(tmp_path):
    (tmp_path / "pipelineruns/older-component").mkdir(parents=True)
    (tmp_path / "pipelineruns/newer-component").mkdir()
    init_repo(tmp_path)
    revision = cleanup.git(tmp_path, "rev-parse", "HEAD")
    assert cleanup.matrix(tmp_path, "rhoai-3.4", "older-component") == {"include": [{"repository": "older-component", "inventory_revision": revision}]}
    assert len(cleanup.matrix(tmp_path, "rhoai-3.4", "all")["include"]) == 2
    with pytest.raises(ValueError):
        cleanup.matrix(tmp_path, "rhoai-3.4", "unknown")
    with pytest.raises(ValueError):
        cleanup.matrix(tmp_path, "main", "all")


def test_existing_open_and_closed_bot_prs(monkeypatch):
    branch = "automation/cleanup"
    def response(prs):
        monkeypatch.setattr(cleanup, "command", lambda *a, **k: subprocess.CompletedProcess([], 0, json.dumps(prs), ""))
    response([{"state": "OPEN", "url": "existing", "headRefName": "other", "body": cleanup.MARKER}])
    assert cleanup.existing_pr("org/repo", "rhoai-3.6", branch) == "existing"
    response([{"state": "CLOSED", "url": "old", "headRefName": branch, "body": cleanup.MARKER}])
    with pytest.raises(ValueError, match="prior PR"):
        cleanup.existing_pr("org/repo", "rhoai-3.6", branch)
    # The deliberately superseded manual pilot is not an automation branch.
    response([{"state": "CLOSED", "url": "pilot57", "headRefName": "chore/manual-pilot", "body": ""}])
    assert cleanup.existing_pr("org/repo", "rhoai-3.6", branch) is None


@pytest.fixture
def tooling():
    value = os.environ.get("CLEANUP_TEST_TOOLING")
    if not value:
        pytest.skip("set CLEANUP_TEST_TOOLING for real validator integration")
    return Path(value)


@pytest.mark.parametrize("has_targets", [False, True])
def test_actual_validator_preview_is_isolated(tmp_path, tooling, has_targets, monkeypatch):
    target = tmp_path / "target"
    for name in ("tag", "group-test", "push", "e2e", "arbitrary") if has_targets else []:
        resource(target, name)
    resource(target, "retained", "rhoai-tenant")
    resource(target, "unspecified", None)
    resource(target, "tasks", kind="Task")
    stream = target / ".tekton/tasks.yaml"
    stream.write_text(stream.read_text() + "---\nkind: Task\nmetadata:\n  name: second\n")
    init_repo(target)
    before, expected = cleanup.snapshot(target)
    calls = []
    original = cleanup.command
    def observe(args, **kwargs):
        calls.append([str(a) for a in args])
        return original(args, **kwargs)
    monkeypatch.setattr(cleanup, "command", observe)
    report = tmp_path / "report"
    result = cleanup.prepare(target, tooling, POLICY, "rhoai-3.6", "ai-gateway-controller", report)
    assert result["status"] == ("ready" if has_targets else "no-changes")
    assert result["deleted_files"] == expected
    assert cleanup.snapshot(target)[0] == before
    assert cleanup.snapshot(report / "stage")[1] == []
    assert not any("gh" == c[0] or any(x in c for x in ("commit", "push")) for c in calls)
    assert all((report / (mode + ".json")).exists() for mode in ("preview", "fix", "validation", "idempotence"))


def test_validator_failure_cannot_change_target(tmp_path, tooling, monkeypatch):
    target = tmp_path / "target"
    resource(target, "old")
    init_repo(target)
    before = cleanup.snapshot(target)
    def fail(*args, **kwargs):
        raise ValueError("validator failed")
    monkeypatch.setattr(cleanup, "validate", fail)
    with pytest.raises(ValueError, match="validator failed"):
        cleanup.prepare(target, tooling, POLICY, "rhoai-3.6", "ai-gateway-controller", tmp_path / "report")
    assert cleanup.snapshot(target) == before


def test_cli_error_reporting_cannot_write_into_target(tmp_path, monkeypatch):
    target = tmp_path / "target"
    resource(target, "untouched")
    before = cleanup.snapshot(target)
    monkeypatch.setattr(cleanup.sys, "argv", ["cleanup", "prepare", "--target", str(target),
                        "--tooling", str(tmp_path), "--policy", str(POLICY),
                        "--release", "rhoai-3.6", "--repository", "ai-gateway-controller",
                        "--report", str(target / ".tekton")])
    with pytest.raises(ValueError, match="disjoint"):
        cleanup.main()
    assert cleanup.snapshot(target) == before
    assert not (target / ".tekton/error.json").exists()


def test_cli_parser_error_report_identifies_file_and_revisions(tmp_path, tooling, monkeypatch):
    target = tmp_path / "target"
    resource(target, "broken")
    path = target / ".tekton/broken.yaml"
    path.write_text(path.read_text() + "kind: Task\n")
    init_repo(target)
    before = path.read_bytes()
    report = tmp_path / "report"
    monkeypatch.setattr(cleanup.sys, "argv", ["cleanup", "prepare", "--target", str(target),
                        "--tooling", str(tooling), "--policy", str(POLICY),
                        "--release", "rhoai-3.4", "--repository", "distributed-workloads",
                        "--inventory-revision", "a" * 40, "--report", str(report)])
    with pytest.raises(ValueError, match=r"invalid YAML in \.tekton/broken\.yaml"):
        cleanup.main()
    diagnostic = json.loads((report / "error.json").read_text())
    assert diagnostic["base"] == cleanup.git(target, "rev-parse", "HEAD")
    assert diagnostic["inventory_revision"] == "a" * 40
    assert diagnostic["release"] == "rhoai-3.4"
    assert diagnostic["repository"] == "distributed-workloads"
    assert diagnostic["policy_revision"] and diagnostic["tooling_revision"]
    assert path.read_bytes() == before


def test_non_idempotent_validator_blocks(tmp_path, tooling, monkeypatch):
    target = tmp_path / "target"
    resource(target, "old")
    resource(target, "retained", "rhoai-tenant")
    init_repo(target)
    original = cleanup.validate
    def unstable(stage, tooling, policy, release, report, mode, flags):
        original(stage, tooling, policy, release, report, mode, flags)
        if mode == "idempotence":
            (stage / ".tekton/retained.yaml").write_text("changed")
    monkeypatch.setattr(cleanup, "validate", unstable)
    with pytest.raises(ValueError, match="idempotent"):
        cleanup.prepare(target, tooling, POLICY, "rhoai-3.6", "ai-gateway-controller", tmp_path / "report")


def test_incomplete_validator_coverage_blocks(tmp_path, tooling, monkeypatch):
    target = tmp_path / "target"
    resource(target, "unknown-tag")
    init_repo(target)
    monkeypatch.setattr(cleanup, "validate", lambda *args, **kwargs: None)
    with pytest.raises(ValueError, match="exactly match"):
        cleanup.prepare(target, tooling, POLICY, "rhoai-3.6", "ai-gateway-controller", tmp_path / "report")
