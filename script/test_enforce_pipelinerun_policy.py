"""Generic template updates, policy boundaries, and receipt verification."""
import json
import shutil
import subprocess

import pytest
from ruamel.yaml import YAML

from test_enforce_pipelinerun_templates import enforcer, init_repo, resource, tooling


@pytest.fixture
def format_policy(tmp_path):
    """Create a local template set unrelated to tenant cleanup."""
    policy = tmp_path / "format-policy"
    policy.mkdir()
    (policy / "config.yaml").write_text('tekton_layout: flat\ntemplates:\n  - name: format\n    file: format.yaml.j2\n    match_fn: is_pipeline_run\n')
    (policy / "format.yaml.j2").write_text('apiVersion: tekton.dev/v1\nmetadata:\n  annotations:\n    example.com/format: "v1"\n')
    init_repo(policy)
    return policy


def test_generic_updates_are_not_namespace_specific(tmp_path, tooling, format_policy):
    """Apply ordinary template content rules across tenants and omitted namespaces."""
    target = tmp_path / "target"
    resource(target, "downstream", "rhoai-tenant")
    resource(target, "upstream", "open-data-hub-tenant")
    resource(target, "omitted", None)
    resource(target, "task", kind="Task")
    init_repo(target)
    before = enforcer.snapshot(target)[0]
    report = tmp_path / "report"
    result = enforcer.prepare(target, tooling, format_policy, "rhoai-3.6", "repo", report)
    assert result["deleted_files"] == []
    assert result["changes"] == {f".tekton/{name}.yaml": "M" for name in ("downstream", "omitted", "upstream")}
    assert result["template_set"] == "format-policy"
    assert enforcer.snapshot(target)[0] == before
    document = YAML(typ="safe").load((report / "stage/.tekton/omitted.yaml").read_text())
    assert "namespace" not in document["metadata"]
    assert document["metadata"]["annotations"]["example.com/format"] == "v1"
    assert result["before"][".tekton/task.yaml"] == result["after"][".tekton/task.yaml"]
    compliant = tmp_path / "compliant"
    shutil.copytree(report / "stage/.tekton", compliant / ".tekton")
    init_repo(compliant)
    assert enforcer.prepare(compliant, tooling, format_policy, "rhoai-3.6", "repo", tmp_path / "second")["status"] == "no-changes"


@pytest.mark.parametrize("name", ["../escape", "/absolute", "bad_name", "", "a" * 65])
def test_policy_selection_rejects_unsafe_names(tmp_path, name):
    """Do not accept arbitrary filesystem paths as workflow policy inputs."""
    with pytest.raises(ValueError, match="template set"):
        enforcer.select_policy(tmp_path, name)


def test_policy_selection_and_symlink_guard(tmp_path, format_policy):
    """Select a named set or root explicitly but reject symlinked aliases."""
    assert enforcer.select_policy(tmp_path, "format-policy") == format_policy
    assert enforcer.select_policy(format_policy, "default") == format_policy
    (tmp_path / "alias").symlink_to(format_policy)
    with pytest.raises(ValueError, match="unsafe"):
        enforcer.select_policy(tmp_path, "alias")


@pytest.mark.parametrize("setting", ['tekton_layout: nested\n', 'parent_ref: origin/main\n'])
def test_incompatible_policy_sets_fail_closed(format_policy, setting):
    """Keep nested KC migrations and cross-branch reads outside component enforcement."""
    path = format_policy / "config.yaml"
    content = path.read_text().replace('tekton_layout: flat\n', '')
    path.write_text(setting + content)
    with pytest.raises(ValueError, match="flat existing-file"):
        enforcer.policy_digest(format_policy)


def test_template_paths_cannot_escape_set(format_policy):
    """Prevent selected policies from referencing unhashed files outside their set."""
    path = format_policy / "config.yaml"
    path.write_text(path.read_text().replace('format.yaml.j2', '../outside.yaml.j2'))
    with pytest.raises(ValueError, match="escapes"):
        enforcer.policy_digest(format_policy)


def test_symlink_configuration_is_rejected_before_reading(format_policy, tmp_path, monkeypatch):
    """Never follow a repository-provided configuration symlink before validation."""
    config = format_policy / "config.yaml"
    config.rename(format_policy / "saved.yaml")
    outside = tmp_path / "outside.yaml"
    outside.write_text("not a policy")
    config.symlink_to(outside)
    original = type(config).read_text
    def guarded(path, *args, **kwargs):
        """Fail if the symlink is opened rather than rejected from its metadata."""
        assert path != config
        return original(path, *args, **kwargs)
    monkeypatch.setattr(type(config), "read_text", guarded)
    with pytest.raises(ValueError, match="unsafe"):
        enforcer.policy_digest(format_policy)


def test_unselected_files_and_modes_are_immutable():
    """Permit only selected byte changes, never executable-mode changes or additions."""
    before = {".tekton/run.yaml": {"sha256": "a", "executable": False}}
    for selected, after in [([], {".tekton/run.yaml": {"sha256": "b", "executable": False}}),
                            ([".tekton/run.yaml"], {".tekton/run.yaml": {"sha256": "b", "executable": True}})]:
        with pytest.raises(ValueError):
            enforcer.verify_changes(before, after, selected, [])


@pytest.mark.parametrize("other_set,expected", [("format-policy", "https://example.com/existing"), ("cleanup-odh-tenant", None)])
def test_open_pr_identity_is_scoped_to_template_set(monkeypatch, other_set, expected):
    """A different policy's open PR must not prevent this policy's publication."""
    def listed(*args, **kwargs):
        """Return an existing open PR without making any GitHub calls."""
        return subprocess.CompletedProcess([], 0, json.dumps([{
            "state": "OPEN", "headRefName": "another-branch",
            "body": f"{enforcer.MARKER}\n<!-- template-set:{other_set} -->",
            "url": "https://example.com/existing"}]), '')
    monkeypatch.setattr(enforcer, "command", listed)
    assert enforcer.existing_pr("org/repo", "rhoai-3.6", "new-branch", "format-policy") == expected


def test_update_publication_uses_exact_validated_bytes(tmp_path, tooling, format_policy, monkeypatch):
    """Allow a real template update patch while mocking every remote mutation."""
    target = tmp_path / "target"
    resource(target, "run", None)
    init_repo(target)
    report = tmp_path / "report"
    result = enforcer.prepare(target, tooling, format_policy, "rhoai-3.6", "repo", report,
                              tracking_issue="RHAI-19583")
    original = enforcer.command
    mutations = []
    def safe(args, **kwargs):
        """Keep API reads deterministic and prevent test commits/pushes/PR creation."""
        args = [str(a) for a in args]
        if args[0] == 'gh':
            if args[1] == 'api':
                return subprocess.CompletedProcess(args, 0, result['base'], '')
            assert args[1:3] == ['pr', 'create']
            mutations.append(args)
            return subprocess.CompletedProcess(args, 0, 'https://example.com/pr', '')
        if 'commit' in args or 'push' in args:
            mutations.append(args)
            return subprocess.CompletedProcess(args, 0, '', '')
        return original(args, **kwargs)
    monkeypatch.setattr(enforcer, 'command', safe)
    monkeypatch.setattr(enforcer, 'existing_pr', lambda *a: None)
    assert enforcer.publish(target, report)['status'] == 'published'
    assert len(mutations) == 3
    assert enforcer.snapshot(target)[0] == result['after']
    body = (report / 'pr.md').read_text()
    assert 'format-policy' in body and '[RHAI-19583]' in body
    assert 'M: `.tekton/run.yaml`' in body
