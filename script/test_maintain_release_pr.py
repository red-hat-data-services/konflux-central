"""Tests of standalone orchestration; all commands mocked, no network access."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

spec = importlib.util.spec_from_file_location("maintenance", Path(__file__).with_name("maintain_release_pr.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def invoke(tmp_path, changed=(), dirty=False, unexpected=False, publish=False):
    """Run orchestration against isolated reports with all external commands mocked."""
    target = tmp_path / "target"
    target.mkdir(exist_ok=True)
    report = tmp_path / "report"
    report.mkdir(exist_ok=True)
    manifest = {"branch": "rhoai-3.6", "changed": list(changed), "orphans": []}
    if publish:
        (report / "generation.json").write_text(json.dumps(manifest))
        (report / "provenance.json").write_text("{}")
    argv = ["maintain", "--target", str(target), "--tooling", str(tmp_path), "--config-dir", str(tmp_path),
            "--branch", "rhoai-3.6", "--report-dir", str(report)]
    if publish:
        argv.append("--publish")

    def fake(*args, **kwargs):
        """Model Git and validator outputs without executing subprocesses or publishing."""
        if args[:3] == ("git", "status", "--porcelain"):
            stdout = " M dirty.yaml" if dirty else ""
        elif args[:3] == ("git", "diff", "--name-only"):
            stdout = "unexpected.yaml" if unexpected else "\n".join(changed)
        elif args[:3] == ("git", "rev-parse", "HEAD"):
            stdout = "abc123\n"
        else:
            stdout = ""
        if args[0] == "uv":
            files = {str(target / path): {"errors": [], "warnings": []} for path in changed}
            stdout = json.dumps({"files": files, "fixed": [str(target / path) for path in changed] if "--fix" in args else []})
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    with patch.object(sys, "argv", argv), patch.object(module, "run", side_effect=fake) as commands:
        module.main()
    return report, [call.args for call in commands.call_args_list]


def test_prepare_does_not_publish(tmp_path):
    """Preparation must fix then validate without committing, pushing, or creating a PR."""
    path = "pipelineruns/widget/.tekton/widget-pull-request.yaml"
    report, calls = invoke(tmp_path, [path])
    assert (report / "changes.diff").exists()
    validation_commands = [command for command in calls if command[0] == "uv"]
    assert len(validation_commands) == 2
    assert "--fix" in validation_commands[0] and "--fix" not in validation_commands[1]
    assert all(command[2].endswith("scripts/validate-tekton") for command in validation_commands)
    assert not any(command[0] == "gh" or "commit" in command or "push" in command for command in calls)


@pytest.mark.parametrize("options", [{"dirty": True}, {"unexpected": True}])
def test_refuse_dirty_or_out_of_scope(tmp_path, options):
    """Reject dirty checkouts and unexpected generated paths."""
    with pytest.raises(SystemExit):
        invoke(tmp_path, **options)


def test_publish_no_changes_does_not_mutate_git(tmp_path):
    """An empty publication report must not invoke any Git or GitHub command."""
    _, calls = invoke(tmp_path, publish=True)
    assert not calls


def test_explicit_publish_targets_release(tmp_path, monkeypatch):
    """Explicit publication must create a PR against the requested release branch."""
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/kc")
    _, calls = invoke(tmp_path, ["pipelineruns/widget/.tekton/widget-pull-request.yaml"], publish=True)
    pr = next(command for command in calls if command[:3] == ("gh", "pr", "create"))
    assert pr[pr.index("--base") + 1] == "rhoai-3.6"
    assert any(command[:2] == ("git", "push") for command in calls)
