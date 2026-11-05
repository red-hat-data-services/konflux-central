"""Real file-safety tests and optional end-to-end shared-validator smoke tests."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

SPEC = importlib.util.spec_from_file_location("enforcer", Path(__file__).with_name("reconcile_component_repo_pipelineruns.py"))
enforcer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(enforcer)
POLICY = Path(__file__).resolve().parents[1] / "tekton-templates/cleanup-odh-tenant"


def _shared_root():
    """Select the explicit reviewed worker fixture, never a validator identity."""
    value = os.environ.get("PUBLISHER_TEST_CHECKOUT")
    if not value:
        pytest.skip("set PUBLISHER_TEST_CHECKOUT for worker integration")
    return Path(value)


def snapshot(root):
    """Exercise consumer resource checks over the real shared byte inventory."""
    return enforcer.snapshot(root, enforcer.load_shared(_shared_root(), "reconciliation"))


def prepare(*args, **kwargs):
    """Supply the reviewed shared worker to every real consumer planning test."""
    kwargs.setdefault("worker_root", _shared_root())
    return enforcer.prepare(*args, **kwargs)


def verify_changes(before, after, selected, deletions):
    """Exercise the delegated delta guard with legacy fixture metadata."""
    def rows(inventory):
        """Translate legacy fixture metadata into the shared byte/mode shape."""
        return {name: {"sha256": row.get("sha256", "changed") if isinstance(row, dict) else str(row),
                       "mode": "100755" if isinstance(row, dict) and row.get("executable") else "100644"}
                for name, row in inventory.items()}
    return enforcer.load_shared(_shared_root(), "reconciliation").verify_changes(
        rows(before), rows(after), selected, deletions)


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
    files, targets = snapshot(tmp_path)
    assert len(files) == 4
    assert targets == [".tekton/arbitrary-group-test.yaml", ".tekton/odh-push.yaml", ".tekton/odh-tag.yaml"]


def test_resource_stream_is_preserved(tmp_path):
    """Keep resource-only YAML streams outside the PipelineRun deletion set."""
    resource(tmp_path, "tasks", kind="Task")
    path = tmp_path / ".tekton/tasks.yaml"
    path.write_text(path.read_text() + "---\nkind: Task\nmetadata:\n  name: second\n")
    assert snapshot(tmp_path)[1] == []


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
        snapshot(tmp_path)


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
        snapshot(tmp_path)


@pytest.mark.parametrize("after,expected", [
    ({}, []), ({"retained": {"executable": True}}, ["deleted"]),
    ({"retained": 1, "added": 3}, ["deleted"]),
])
def test_scope_or_retained_mutations_block(after, expected):
    """Reject missing, added, or modified files beyond the approved deletions."""
    with pytest.raises(ValueError):
        verify_changes({"retained": 1, "deleted": 2}, after, [], expected)


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


@pytest.fixture
def tooling():
    """Enable real-validator tests only with an explicit existing tooling checkout."""
    value = os.environ.get("ENFORCEMENT_TEST_TOOLING")
    if not value:
        pytest.skip("set ENFORCEMENT_TEST_TOOLING for real validator integration")
    return Path(value)


@pytest.mark.parametrize("invalid_source", ["wrong-revision", "dirty"])
def test_validator_source_rejected_before_render(tmp_path, tooling, monkeypatch, invalid_source):
    """Never execute a stale or dirty renderer, even with a valid worker checkout."""
    target = tmp_path / "target"
    resource(target, "retained", "rhoai-tenant")
    init_repo(target)
    before = snapshot(target)
    candidate = init_repo(tmp_path / "wrong-validator") if invalid_source == "wrong-revision" else tooling
    original_git = enforcer.git
    with monkeypatch.context() as patch:
        if invalid_source == "dirty":
            def dirty_status(root, *args):
                """Simulate dirty validator metadata without editing shared-owned files."""
                if root == tooling and args[0] == "status":
                    return " M lib/tekton.py"
                return original_git(root, *args)
            patch.setattr(enforcer, "git", dirty_status)
        with pytest.raises(ValueError, match="validator must be the clean corrected shared revision"):
            prepare(target, candidate, POLICY, "rhoai-3.6", "repo", tmp_path / "report")
    assert snapshot(target) == before
    assert not (tmp_path / "report/initial.json").exists()


@pytest.mark.parametrize("field", ["label", "prefix"])
@pytest.mark.parametrize("escape", ["absolute", "traversal"])
def test_actual_renderer_rejects_target_overlay_escape(tmp_path, tooling, field, escape):
    """Exercise corrected source through the real consumer and isolated CLI phase."""
    policy = tmp_path / "policies/format"
    policy.mkdir(parents=True)
    (policy / "config.yaml").write_text("tekton_layout: flat\ntemplates:\n  - name: all\n    file: format.yaml.j2\n    match_fn: is_pipeline_run\n")
    (policy / "format.yaml.j2").write_text("kind: PipelineRun\n")
    init_repo(policy)
    outside = tmp_path / "policies/outside"
    outside.mkdir()
    inert = "metadata:\n  annotations:\n    example.com/outside: forbidden\n"
    (outside / "format.yaml.j2").write_text(inert)
    selector = str(outside) if escape == "absolute" else "../outside"
    target = tmp_path / "target"
    resource(target, "retained", "rhoai-tenant")
    data = {"apiVersion": "tekton.dev/v1", "kind": "PipelineRun", "metadata": {
        "name": selector + "-on-push" if field == "prefix" else "valid-on-push",
        "labels": {"appstudio.openshift.io/component": selector if field == "label" else "valid"}}}
    (target / ".tekton/retained.yaml").write_text(json.dumps(data))
    init_repo(target)
    before = snapshot(target)
    report = tmp_path / "report"
    with pytest.raises(ValueError):
        prepare(target, tooling, policy, "rhoai-3.6", "repo", report)
    assert "unsafe overlay component" in (report / "initial.log").read_text()
    assert snapshot(target) == before
    assert (outside / "format.yaml.j2").read_text() == inert
    assert not (report / "bundle/manifest.json").exists()


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
    before, original_runs = snapshot(target)
    expected = sorted(f".tekton/{name}.yaml" for name in ("tag", "group-test", "push", "e2e", "arbitrary")) if has_targets else []
    calls = []
    original = enforcer.command
    def observe(args, **kwargs):
        """Record real local commands to prove preview has no publication side effects."""
        calls.append([str(a) for a in args])
        return original(args, **kwargs)
    monkeypatch.setattr(enforcer, "command", observe)
    report = tmp_path / "report"
    result = prepare(target, tooling, POLICY, "rhoai-3.6", "ai-gateway-controller", report)
    assert result["status"] == ("ready" if has_targets else "no-changes")
    assert result["deleted_files"] == expected
    assert snapshot(target)[0] == before
    assert set(snapshot(report / "stage")[1]) == set(original_runs) - set(expected)
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
    before = snapshot(target)[0]
    result = prepare(target, tooling, POLICY, "rhoai-3.6", "repo", tmp_path / "report")
    assert result["status"] == "no-changes"
    assert result["after"] == before


def test_validator_failure_cannot_change_target(tmp_path, tooling, monkeypatch):
    """Keep the component intact when the validator rejects preparation."""
    target = tmp_path / "target"
    resource(target, "old")
    init_repo(target)
    before = snapshot(target)
    def fail(*args, **kwargs):
        """Simulate an unsuccessful validator phase before any target file changes."""
        raise ValueError("validator failed")
    monkeypatch.setattr(enforcer, "validate", fail)
    with pytest.raises(ValueError, match="validator failed"):
        prepare(target, tooling, POLICY, "rhoai-3.6", "ai-gateway-controller", tmp_path / "report")
    assert snapshot(target) == before


def test_cli_error_reporting_cannot_write_into_target(tmp_path, monkeypatch):
    """Keep CLI error artifacts outside the protected component checkout."""
    target = tmp_path / "target"
    resource(target, "untouched")
    before = snapshot(target)
    monkeypatch.setattr(enforcer.sys, "argv", ["enforcer", "prepare", "--worker-root", str(_shared_root()), "--target", str(target),
                        "--tooling", str(tmp_path), "--policy-root", str(POLICY.parent), "--template-set", POLICY.name,
                        "--release", "rhoai-3.6", "--repository", "ai-gateway-controller",
                        "--report", str(target / ".tekton")])
    with pytest.raises(ValueError, match="disjoint"):
        enforcer.main()
    assert snapshot(target) == before
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
    monkeypatch.setattr(enforcer.sys, "argv", ["enforcer", "prepare", "--worker-root", str(_shared_root()), "--target", str(target),
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


@pytest.mark.parametrize('attack', ['mode', 'added', 'kind', 'malformed'])
def test_actual_worker_rejects_invalid_postfix_output(tmp_path, tooling, monkeypatch, attack):
    """Exercise shared phases with real validator output plus a controlled bad fix."""
    target = tmp_path / 'target'
    resource(target, 'retained', 'rhoai-tenant')
    init_repo(target)
    before = snapshot(target)
    original = enforcer.validate
    def altered(stage, tooling, policy, release, report, mode, flags, **kwargs):
        """Alter disposable stage output only, never the source checkout."""
        result = original(stage, tooling, policy, release, report, mode, flags, **kwargs)
        if mode == 'fix':
            path = stage / '.tekton/retained.yaml'
            if attack == 'mode': path.chmod(0o755)
            elif attack == 'added': (stage / '.tekton/new.yaml').write_text('kind: Task\n')
            elif attack == 'kind': path.write_text(path.read_text().replace('kind: PipelineRun', 'kind: Task'))
            else: path.write_text(path.read_text() + 'kind: Task\n')
        return result
    monkeypatch.setattr(enforcer, 'validate', altered)
    with pytest.raises(ValueError):
        prepare(target, tooling, POLICY, 'rhoai-3.6', 'repo', tmp_path / 'report')
    assert snapshot(target) == before


def test_actual_worker_accepts_missing_tekton_scope(tmp_path, tooling):
    """A missing component scope is a real validated no-op, not a setup error."""
    target = tmp_path / 'target'
    init_repo(target)
    result = prepare(target, tooling, POLICY, 'rhoai-3.6', 'repo', tmp_path / 'report')
    assert result['status'] == 'no-changes'
    assert not (target / '.tekton').exists()


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
            path = stage / ".tekton/retained.yaml"
            path.write_text(path.read_text().replace("name: retained", "name: changed"))
        return result
    monkeypatch.setattr(enforcer, "validate", unstable)
    with pytest.raises(ValueError, match="idempotent"):
        prepare(target, tooling, POLICY, "rhoai-3.6", "ai-gateway-controller", tmp_path / "report")


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
    with pytest.raises(ValueError, match="additions/deletions"):
        prepare(target, tooling, POLICY, "rhoai-3.6", "ai-gateway-controller", tmp_path / "report")
