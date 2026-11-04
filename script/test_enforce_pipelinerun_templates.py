"""Real file-safety tests and optional end-to-end shared-validator smoke tests."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

SPEC = importlib.util.spec_from_file_location("enforcer", Path(__file__).with_name("enforce_pipelinerun_templates.py"))
enforcer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(enforcer)
POLICY = Path(__file__).resolve().parents[1] / "tekton-templates/cleanup-odh-tenant"


def init_repo(path):
    """Commit fixture inputs in a disposable local repository for safety checks."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-qm", "fixture", "--allow-empty"], check=True)
    return path


def resource(root, name, namespace="open-data-hub-tenant", kind="PipelineRun"):
    """Write a minimal resource with an explicit or deliberately omitted namespace."""
    directory = root / ".tekton"
    directory.mkdir(parents=True, exist_ok=True)
    text = f"apiVersion: tekton.dev/v1\nkind: {kind}\nmetadata:\n  name: {name}\n"
    if namespace is not None:
        text += f"  namespace: {namespace}\n"
    (directory / (name + ".yaml")).write_text(text)


def test_snapshot_records_pipeline_runs_in_all_namespaces(tmp_path):
    """Select only the exact PipelineRun execution namespace, not naming hints."""
    resource(tmp_path, "arbitrary-group-test")
    resource(tmp_path, "odh-push", "rhoai-tenant")
    resource(tmp_path, "odh-tag", None)
    resource(tmp_path, "task", kind="Task")
    files, targets = enforcer.snapshot(tmp_path)
    assert len(files) == 4
    assert targets == [".tekton/arbitrary-group-test.yaml", ".tekton/odh-push.yaml", ".tekton/odh-tag.yaml"]


def test_resource_stream_is_preserved(tmp_path):
    """Keep resource-only YAML streams outside the PipelineRun deletion set."""
    resource(tmp_path, "tasks", kind="Task")
    path = tmp_path / ".tekton/tasks.yaml"
    path.write_text(path.read_text() + "---\nkind: Task\nmetadata:\n  name: second\n")
    assert enforcer.snapshot(tmp_path)[1] == []


@pytest.mark.parametrize("text", [
    "kind: PipelineRun\n---\nkind: Task\n",
    "kind: PipelineRun\nkind: Task\n",
    "kind: Task\n---\n[broken\n",
])
def test_bad_yaml_and_bundled_runs_block(tmp_path, text):
    """Reject malformed, duplicate-key, and bundled PipelineRun documents."""
    (tmp_path / ".tekton").mkdir()
    (tmp_path / ".tekton/file.yaml").write_text(text)
    with pytest.raises(ValueError, match=r"file\.yaml"):
        enforcer.snapshot(tmp_path)


@pytest.mark.parametrize("directory_link", [False, True])
def test_symlinks_block(tmp_path, directory_link):
    """Reject both a symlinked Tekton directory and symlink entries."""
    outside = tmp_path / "outside"
    outside.mkdir()
    if directory_link:
        (tmp_path / ".tekton").symlink_to(outside)
    else:
        (tmp_path / ".tekton").mkdir()
        (tmp_path / ".tekton/file.yaml").symlink_to(outside / "absent")
    with pytest.raises(ValueError, match="symlink|unsafe"):
        enforcer.snapshot(tmp_path)


@pytest.mark.parametrize("after,expected", [
    ({}, []), ({"retained": {"executable": True}}, ["deleted"]),
    ({"retained": 1, "added": 3}, ["deleted"]),
])
def test_scope_or_retained_mutations_block(after, expected):
    """Reject missing, added, or modified files beyond the approved deletions."""
    with pytest.raises(ValueError):
        enforcer.verify_changes({"retained": 1, "deleted": 2}, after, [], expected)


def test_inventory_uses_selected_release_dirs(tmp_path):
    """Derive pairs from the selected source inventory and reject unknown choices."""
    (tmp_path / "pipelineruns/older-component").mkdir(parents=True)
    (tmp_path / "pipelineruns/newer-component").mkdir()
    init_repo(tmp_path)
    revision = enforcer.git(tmp_path, "rev-parse", "HEAD")
    assert enforcer.matrix(tmp_path, "rhoai-3.4", "older-component") == {"include": [{"repository": "older-component", "inventory_revision": revision}]}
    assert len(enforcer.matrix(tmp_path, "rhoai-3.4", "all")["include"]) == 2
    with pytest.raises(ValueError):
        enforcer.matrix(tmp_path, "rhoai-3.4", "unknown")
    with pytest.raises(ValueError):
        enforcer.matrix(tmp_path, "main", "all")


@pytest.mark.parametrize("repository", ["all", "component"])
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("allow_batch", [False, True])
def test_batch_publication_requires_additional_confirmation(tmp_path, repository, dry_run, allow_batch):
    """Keep batch previews free of confirmation while guarding batch publication."""
    (tmp_path / "pipelineruns/component").mkdir(parents=True)
    (tmp_path / "pipelineruns/other").mkdir()
    init_repo(tmp_path)
    if repository == "all" and not dry_run and not allow_batch:
        with pytest.raises(ValueError, match="allow_batch_publication=true"):
            enforcer.matrix(tmp_path, "rhoai-3.6", repository, dry_run, allow_batch)
    else:
        result = enforcer.matrix(tmp_path, "rhoai-3.6", repository, dry_run, allow_batch)
        assert len(result["include"]) == (2 if repository == "all" else 1)


def test_batch_guard_fails_before_inventory_access(tmp_path):
    """Reject unconfirmed fan-out before reading inventory or generating jobs."""
    with pytest.raises(ValueError, match="allow_batch_publication=true"):
        enforcer.matrix(tmp_path / "missing", "rhoai-3.6", "all", dry_run=False)


@pytest.mark.parametrize("value", ["False", "yes", "0", ""])
def test_boolean_workflow_inputs_reject_ambiguous_values(value):
    """Do not silently turn malformed workflow inputs into publication mode."""
    with pytest.raises(enforcer.argparse.ArgumentTypeError):
        enforcer.boolean_value(value)


@pytest.mark.parametrize("flags,expected_dry_run,expected_allow", [
    ([], True, False),
    (["--dry-run", "false"], False, False),
    (["--dry-run", "false", "--allow-batch-publication", "true"], False, True),
])
def test_cli_passes_explicit_publication_inputs(monkeypatch, flags, expected_dry_run, expected_allow):
    """Preserve safe CLI defaults and route both booleans through the tested guard."""
    def selected(**kwargs):
        """Inspect argparse conversion without reading a real repository."""
        assert kwargs["dry_run"] is expected_dry_run
        assert kwargs["allow_batch_publication"] is expected_allow
        return {"include": []}
    monkeypatch.setattr(enforcer, "matrix", selected)
    monkeypatch.setattr(enforcer.sys, "argv", ["enforcer", "matrix", "--source", "unused",
                        "--release", "rhoai-3.6", "--repository", "all", *flags])
    enforcer.main()


def test_existing_open_and_closed_bot_prs(monkeypatch):
    """Reuse open cleanup PRs without resurrecting closed automation branches."""
    branch = "automation/cleanup"
    def response(prs):
        """Supply bounded GitHub PR-list fixtures without performing network reads."""
        monkeypatch.setattr(enforcer, "command", lambda *a, **k: subprocess.CompletedProcess([], 0, json.dumps(prs), ""))
    response([{"state": "OPEN", "url": "existing", "headRefName": "other", "body": enforcer.MARKER + "<!-- template-set:cleanup-odh-tenant -->"}])
    assert enforcer.existing_pr("org/repo", "rhoai-3.6", branch, POLICY.name) == "existing"
    response([{"state": "CLOSED", "url": "old", "headRefName": branch, "body": enforcer.MARKER}])
    with pytest.raises(ValueError, match="prior PR"):
        enforcer.existing_pr("org/repo", "rhoai-3.6", branch, POLICY.name)
    # The deliberately superseded manual pilot is not an automation branch.
    response([{"state": "CLOSED", "url": "pilot57", "headRefName": "chore/manual-pilot", "body": ""}])
    assert enforcer.existing_pr("org/repo", "rhoai-3.6", branch, POLICY.name) is None


@pytest.fixture
def tooling():
    """Enable real-validator tests only with an explicit existing tooling checkout."""
    value = os.environ.get("ENFORCEMENT_TEST_TOOLING")
    if not value:
        pytest.skip("set ENFORCEMENT_TEST_TOOLING for real validator integration")
    return Path(value)


@pytest.mark.parametrize("has_targets", [False, True])
def test_actual_validator_preview_is_isolated(tmp_path, tooling, has_targets, monkeypatch):
    """Exercise real cleanup phases without commits, remote calls, or target changes."""
    target = tmp_path / "target"
    for name in ("tag", "group-test", "push", "e2e", "arbitrary") if has_targets else []:
        resource(target, name)
    resource(target, "retained", "rhoai-tenant")
    resource(target, "unspecified", None)
    resource(target, "tasks", kind="Task")
    stream = target / ".tekton/tasks.yaml"
    stream.write_text(stream.read_text() + "---\nkind: Task\nmetadata:\n  name: second\n")
    init_repo(target)
    before, original_runs = enforcer.snapshot(target)
    expected = sorted(f".tekton/{name}.yaml" for name in ("tag", "group-test", "push", "e2e", "arbitrary")) if has_targets else []
    calls = []
    original = enforcer.command
    def observe(args, **kwargs):
        """Record real local commands to prove preview has no publication side effects."""
        calls.append([str(a) for a in args])
        return original(args, **kwargs)
    monkeypatch.setattr(enforcer, "command", observe)
    report = tmp_path / "report"
    result = enforcer.prepare(target, tooling, POLICY, "rhoai-3.6", "ai-gateway-controller", report)
    assert result["status"] == ("ready" if has_targets else "no-changes")
    assert result["deleted_files"] == expected
    assert enforcer.snapshot(target)[0] == before
    assert set(enforcer.snapshot(report / "stage")[1]) == set(original_runs) - set(expected)
    assert result["updated_files"] == []
    assert not any("gh" == c[0] or any(x in c for x in ("commit", "push")) for c in calls)
    assert all((report / (mode + ".json")).exists() for mode in ("preview", "fix", "validation", "idempotence"))


def test_cleanup_example_preserves_explicit_null_namespace(tmp_path, tooling):
    """Treat null as nonmatching without removing or rewriting its namespace key."""
    target = tmp_path / "target"
    resource(target, "nullable", None)
    path = target / ".tekton/nullable.yaml"
    path.write_text(path.read_text() + "  namespace: null\n")
    init_repo(target)
    before = enforcer.snapshot(target)[0]
    result = enforcer.prepare(target, tooling, POLICY, "rhoai-3.6", "repo", tmp_path / "report")
    assert result["status"] == "no-changes"
    assert result["after"] == before


def test_validator_failure_cannot_change_target(tmp_path, tooling, monkeypatch):
    """Keep the component intact when the validator rejects preparation."""
    target = tmp_path / "target"
    resource(target, "old")
    init_repo(target)
    before = enforcer.snapshot(target)
    def fail(*args, **kwargs):
        """Simulate an unsuccessful validator phase before any target file changes."""
        raise ValueError("validator failed")
    monkeypatch.setattr(enforcer, "validate", fail)
    with pytest.raises(ValueError, match="validator failed"):
        enforcer.prepare(target, tooling, POLICY, "rhoai-3.6", "ai-gateway-controller", tmp_path / "report")
    assert enforcer.snapshot(target) == before


def test_cli_error_reporting_cannot_write_into_target(tmp_path, monkeypatch):
    """Keep CLI error artifacts outside the protected component checkout."""
    target = tmp_path / "target"
    resource(target, "untouched")
    before = enforcer.snapshot(target)
    monkeypatch.setattr(enforcer.sys, "argv", ["enforcer", "prepare", "--target", str(target),
                        "--tooling", str(tmp_path), "--policy-root", str(POLICY.parent), "--template-set", POLICY.name,
                        "--release", "rhoai-3.6", "--repository", "ai-gateway-controller",
                        "--report", str(target / ".tekton")])
    with pytest.raises(ValueError, match="disjoint"):
        enforcer.main()
    assert enforcer.snapshot(target) == before
    assert not (target / ".tekton/error.json").exists()


def test_cli_parser_error_report_identifies_file_and_revisions(tmp_path, tooling, monkeypatch):
    """Retain offending filenames and source revisions for pre-staging YAML failures."""
    target = tmp_path / "target"
    resource(target, "broken")
    path = target / ".tekton/broken.yaml"
    path.write_text(path.read_text() + "kind: Task\n")
    init_repo(target)
    before = path.read_bytes()
    report = tmp_path / "report"
    monkeypatch.setattr(enforcer.sys, "argv", ["enforcer", "prepare", "--target", str(target),
                        "--tooling", str(tooling), "--policy-root", str(POLICY.parent), "--template-set", POLICY.name,
                        "--release", "rhoai-3.4", "--repository", "distributed-workloads",
                        "--inventory-revision", "a" * 40, "--report", str(report)])
    with pytest.raises(ValueError, match=r"invalid YAML in \.tekton/broken\.yaml"):
        enforcer.main()
    diagnostic = json.loads((report / "error.json").read_text())
    assert diagnostic["base"] == enforcer.git(target, "rev-parse", "HEAD")
    assert diagnostic["inventory_revision"] == "a" * 40
    assert diagnostic["release"] == "rhoai-3.4"
    assert diagnostic["repository"] == "distributed-workloads"
    assert diagnostic["policy_revision"] and diagnostic["tooling_revision"]
    assert path.read_bytes() == before


def test_non_idempotent_validator_blocks(tmp_path, tooling, monkeypatch):
    """Reject a validator that changes retained files on the second fix pass."""
    target = tmp_path / "target"
    resource(target, "old")
    resource(target, "retained", "rhoai-tenant")
    init_repo(target)
    original = enforcer.validate
    def unstable(stage, tooling, policy, release, report, mode, flags, **kwargs):
        """Inject a second-pass mutation to exercise the idempotence guard."""
        result = original(stage, tooling, policy, release, report, mode, flags, **kwargs)
        if mode == "idempotence":
            (stage / ".tekton/retained.yaml").write_text("changed")
        return result
    monkeypatch.setattr(enforcer, "validate", unstable)
    with pytest.raises(ValueError, match="idempotent"):
        enforcer.prepare(target, tooling, POLICY, "rhoai-3.6", "ai-gateway-controller", tmp_path / "report")


def test_incomplete_validator_coverage_blocks(tmp_path, tooling, monkeypatch):
    """Reject successful validator exits that leave eligible files undeleted."""
    target = tmp_path / "target"
    resource(target, "unknown-tag")
    init_repo(target)
    original = enforcer.validate
    def incomplete(stage, tooling, policy, release, report, mode, flags, **kwargs):
        """Preserve the real selection plan but deliberately skip the deletion."""
        if mode == "initial":
            return original(stage, tooling, policy, release, report, mode, flags, **kwargs)
        return {"files": {}}
    monkeypatch.setattr(enforcer, "validate", incomplete)
    with pytest.raises(ValueError, match="template plan"):
        enforcer.prepare(target, tooling, POLICY, "rhoai-3.6", "ai-gateway-controller", tmp_path / "report")
