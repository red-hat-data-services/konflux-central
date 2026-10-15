"""Check writable checkout credentials and exercise push guards without GitHub.

Run with: uv run --with pytest --with pyyaml pytest script/test_workflow_git_auth.py
"""

import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
MIGRATED = {
    "apply-z-stream-changes.yml": "apply-z-stream-changes",
    "generate-arch-report.yml": "generate",
    "retrigger-builds.yml": "retrigger",
    "pipelinerun-replicator.yml": "replicate",
}
BRANCH = "rhoai-3.4"


def load_workflow(filename):
    # BaseLoader preserves GitHub's `on` key instead of treating it as a boolean.
    return yaml.load((WORKFLOWS / filename).read_text(), Loader=yaml.BaseLoader)


def steps_for(filename):
    return load_workflow(filename)["jobs"][MIGRATED[filename]]["steps"]


def named_step(filename, name):
    return next(step for step in steps_for(filename) if step.get("name") == name)


@pytest.mark.parametrize("filename", MIGRATED)
def test_writable_checkout_uses_repository_scoped_app_token(filename):
    workflow = load_workflow(filename)
    job = workflow["jobs"][MIGRATED[filename]]
    steps = job["steps"]
    tokens = [step for step in steps if step.get("id") == "app-token"]
    assert len(tokens) == 1
    token = tokens[0]
    checkouts = [step for step in steps if step.get("uses", "").startswith("actions/checkout@")]
    writable = [step for step in checkouts if not step.get("with", {}).get("path")]
    assert len(writable) == 1
    checkout = writable[0]
    assert steps.index(token) < steps.index(checkout)
    assert token.get("continue-on-error", "false") == "false"
    assert checkout["with"].get("persist-credentials", "true") == "true"
    assert checkout["with"]["ref"] == {
        "apply-z-stream-changes.yml": "${{ github.event.inputs.target_branch }}",
        "generate-arch-report.yml": "main",
        "retrigger-builds.yml": "${{ inputs.branch }}",
        "pipelinerun-replicator.yml": "${{ github.event.inputs.source_branch }}",
    }[filename]

    assert token["uses"] == "actions/create-github-app-token@v3"
    assert token["with"] == {
        "app-id": "${{ secrets.APP_ID }}",
        "private-key": "${{ secrets.PRIVATE_KEY }}",
        "owner": "${{ github.repository_owner }}",
        "repositories": "${{ github.event.repository.name }}",
        "permission-contents": "write",
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert 0 < int(job["timeout-minutes"]) < 60

    if filename == "generate-arch-report.yml":
        assert "if" not in token
        assert checkout["with"]["token"] == "${{ steps.app-token.outputs.token }}"
    else:
        assert token["if"] == "inputs.dry_run == false"
        assert checkout["with"]["token"] == "${{ steps.app-token.outputs.token || github.token }}"
        assert workflow["on"]["workflow_dispatch"]["inputs"]["dry_run"]["type"] == "boolean"

    for read_only in checkouts:
        if read_only is not checkout:
            assert read_only["with"]["path"] == "tmp"
            assert read_only["with"]["persist-credentials"] == "false"


@pytest.mark.parametrize("filename,job,path", [
    ("update-repository-list.yml", "sync-repository-list", "${{ github.ref_name }}"),
    ("sync-pipelineruns.yml", "sync", "target"),
    ("sync-renovate-configs.yml", "sync", "target-repo"),
])
def test_existing_sync_writable_checkouts_keep_their_app_token(filename, job, path):
    steps = load_workflow(filename)["jobs"][job]["steps"]
    token = next(step for step in steps if step.get("id") == "app-token")
    assert token["with"]["app-id"] == "${{ secrets.APP_ID }}"
    assert token["with"]["private-key"] == "${{ secrets.PRIVATE_KEY }}"
    checkouts = [
        step for step in steps
        if step.get("uses", "").startswith("actions/checkout@")
        and step.get("with", {}).get("path") == path
    ]
    assert len(checkouts) == 1
    assert steps.index(token) < steps.index(checkouts[0])
    assert checkouts[0]["with"]["token"] == "${{ steps.app-token.outputs.token }}"
    assert checkouts[0]["with"].get("persist-credentials", "true") == "true"


def test_replicator_checks_remote_after_checkout_and_before_branch_creation():
    steps = steps_for("pipelinerun-replicator.yml")
    names = [step.get("name") for step in steps]
    assert names.index("Checkout source branch") < names.index("Check if target branch already exists")
    assert names.index("Check if target branch already exists") < names.index("Create target branch")


def test_downstream_sync_keeps_skip_guard_and_excludes_its_own_workflow_updates():
    sync = load_workflow("sync-pipelineruns.yml")
    assert sync["jobs"]["setup"]["if"] == (
        "github.event_name == 'workflow_dispatch' || !contains(github.event.head_commit.message, 'skip-sync')"
    )
    assert sync["on"]["push"]["paths"] == ["pipelineruns/**"]
    repository_list = load_workflow("update-repository-list.yml")
    assert repository_list["on"]["push"]["paths"] == [
        "pipelineruns/**", "!pipelineruns/README.md.in", "config.yaml",
    ]


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, text=True, capture_output=True,
    ).stdout.strip()


@pytest.fixture
def local_repository(tmp_path, monkeypatch):
    # Never change the developer's global Git configuration or contact GitHub.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    remote = tmp_path / "origin.git"
    remote.mkdir()
    git(remote, "init", "--bare")
    repo = tmp_path / "checkout"
    repo.mkdir()
    git(repo, "init", "-b", BRANCH)
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.com")
    pipelinerun = repo / "pipelineruns/example/.tekton/example-v3-4-push.yaml"
    pipelinerun.parent.mkdir(parents=True)
    pipelinerun.write_text("# initial PipelineRun\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Initial state")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "origin", BRANCH)
    return repo, remote, pipelinerun


def run_step(repo, step, values=None):
    values = values or {}

    def replace(match):
        # Fail closed if a tested script starts using an unhandled expression.
        return values[match.group(1).strip()]

    script = re.sub(r"\$\{\{(.*?)\}\}", replace, step["run"])
    env = {
        **os.environ,
        "GITHUB_SERVER_URL": "https://github.example.com",
        "GITHUB_REPOSITORY": "example/konflux-central",
        "GITHUB_RUN_ID": "123",
        "GITHUB_STEP_SUMMARY": str(repo / "summary.md"),
        "PIPELINERUNS_DIR": "pipelineruns",
        "target_branch": BRANCH,
    }
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        cwd=repo, env=env, text=True, capture_output=True,
    )


@pytest.mark.parametrize("filename", ["apply-z-stream-changes.yml", "pipelinerun-replicator.yml"])
@pytest.mark.parametrize("dry_run", [True, False])
def test_branch_update_push_respects_dry_run(local_repository, filename, dry_run):
    repo, remote, pipelinerun = local_repository
    initial = git(repo, "rev-parse", "HEAD")
    target = BRANCH
    if filename == "pipelinerun-replicator.yml":
        target = "rhoai-3.5"
        git(repo, "checkout", "-b", target)
    pipelinerun.write_text("# updated PipelineRun\n")
    result = run_step(repo, named_step(filename, "Commit Changes"), {
        "env.PIPELINERUNS_DIR": "pipelineruns",
        "env.rhoai_version": "3.4.1",
        "env.target_branch": target,
        "github.event.inputs.target_branch": target,
        "github.event.inputs.dry_run": str(dry_run).lower(),
    })
    assert result.returncode == 0, result.stderr
    remote_ref = git(remote, "for-each-ref", "--format=%(objectname)", f"refs/heads/{target}")
    if dry_run:
        assert remote_ref == (initial if target == BRANCH else "")
    else:
        assert remote_ref == git(repo, "rev-parse", "HEAD")
        assert remote_ref != initial
    assert "[skip-sync]" in git(repo, "log", "-1", "--format=%s")


@pytest.mark.parametrize("skip_sync", [True, False])
def test_retrigger_push_preserves_skip_sync_choice(local_repository, skip_sync):
    repo, remote, pipelinerun = local_repository
    pipelinerun.write_text("# konflux build manual retrigger\n")
    step = named_step("retrigger-builds.yml", "Commit and push")
    assert step["if"] == "inputs.dry_run == false"
    result = run_step(repo, step, {
        "steps.app-token.outputs.app-slug": "test-ci-app",
        "steps.get-user-id.outputs.user-id": "12345",
        "inputs.component": "example",
        "inputs.branch": BRANCH,
        "inputs.skip_sync": str(skip_sync).lower(),
    })
    assert result.returncode == 0, result.stderr
    assert git(remote, "rev-parse", f"refs/heads/{BRANCH}") == git(repo, "rev-parse", "HEAD")
    assert ("[skip-sync]" in git(repo, "log", "-1", "--format=%s")) == skip_sync


def test_retrigger_dry_run_does_not_commit_or_push(local_repository):
    repo, remote, pipelinerun = local_repository
    initial = git(repo, "rev-parse", "HEAD")
    pipelinerun.write_text("# would retrigger\n")
    step = named_step("retrigger-builds.yml", "Show diff (dry run)")
    assert step["if"] == "inputs.dry_run == true"
    result = run_step(repo, step, {"steps.update.outputs.count": "1"})
    assert result.returncode == 0, result.stderr
    assert git(repo, "rev-parse", "HEAD") == initial
    assert git(remote, "rev-parse", f"refs/heads/{BRANCH}") == initial
    assert "No changes were pushed" in result.stdout


@pytest.mark.parametrize("state", ["exists", "missing", "unreachable"])
def test_replicator_target_guard(local_repository, state):
    repo, _, _ = local_repository
    if state == "missing":
        git(repo, "push", "origin", f":{BRANCH}")
    elif state == "unreachable":
        git(repo, "remote", "set-url", "origin", str(repo / "no-such-remote.git"))
    result = run_step(repo, named_step("pipelinerun-replicator.yml", "Check if target branch already exists"))
    assert (result.returncode == 0) == (state == "missing")
    if state == "unreachable":
        assert "Unable to check target branch" in result.stdout
    elif state == "exists":
        assert "already exists" in result.stdout


def test_replicator_guard_ignores_suffix_matches(local_repository):
    repo, _, _ = local_repository
    git(repo, "push", "origin", f"HEAD:refs/heads/konflux/references/{BRANCH}")
    git(repo, "push", "origin", f":{BRANCH}")
    result = run_step(repo, named_step("pipelinerun-replicator.yml", "Check if target branch already exists"))
    assert result.returncode == 0, result.stderr


def test_architecture_report_keeps_manual_dispatch_and_push_filters():
    workflow = load_workflow("generate-arch-report.yml")
    assert workflow["on"]["workflow_dispatch"]["inputs"]["branch"]["required"] == "true"
    assert workflow["on"]["push"] == {
        "branches": ["rhoai-*"],
        "paths": ["pipelineruns/**", "pipelines/**", "script/multi-arch-tracking/exceptions.toml"],
    }
    assert "git diff --cached --quiet" in named_step("generate-arch-report.yml", "Commit report to target branch")["run"]


@pytest.mark.parametrize("changed", [True, False])
def test_architecture_report_pushes_only_when_changed(local_repository, changed):
    repo, remote, _ = local_repository
    report = repo / "multi-arch-report.yaml"
    report.write_text("components: []\n")
    git(repo, "add", "multi-arch-report.yaml")
    git(repo, "commit", "-m", "Initial report")
    git(repo, "push", "origin", BRANCH)
    initial = git(repo, "rev-parse", "HEAD")
    generated = repo / "generated-report.yaml"
    generated.write_text("components: [example]\n" if changed else report.read_text())
    step = dict(named_step("generate-arch-report.yml", "Commit report to target branch"))
    step["run"] = step["run"].replace("/tmp/multi-arch-report.yaml", shlex.quote(str(generated)))
    result = run_step(repo, step, {"steps.branch.outputs.name": BRANCH})
    assert result.returncode == 0, result.stderr
    head = git(repo, "rev-parse", "HEAD")
    assert git(remote, "rev-parse", f"refs/heads/{BRANCH}") == head
    assert (head != initial) == changed
    assert report.read_text() == generated.read_text()
    if changed:
        assert "[skip ci]" in git(repo, "log", "-1", "--format=%s")
