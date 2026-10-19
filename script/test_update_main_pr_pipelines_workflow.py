"""Check the on-demand main PR pipeline update workflow without contacting GitHub."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/update-main-pr-pipelines.yml"


@pytest.fixture
def workflow():
    # BaseLoader keeps GitHub's `on` key a string rather than a YAML 1.1 boolean.
    return yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)


def test_manual_only_and_pr_scope(workflow):
    assert workflow["name"] == "Update main PR pipelines from release branch push pipelines"
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["group"] == "update-main-pr-pipelines"
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    steps = workflow["jobs"]["update-main-pr-pipelines"]["steps"]
    checkouts = [step["with"] for step in steps if step.get("uses", "").startswith("actions/checkout@")]
    assert checkouts[0]["ref"] == "${{ github.sha }}"
    assert checkouts[0]["path"] == "source"
    assert checkouts[1]["repository"] == "red-hat-data-services/devops-shared-resources"
    assert checkouts[1]["ref"] == "main"
    assert checkouts[2]["ref"] == "main"
    assert checkouts[2]["path"] == "target"
    assert checkouts[2]["fetch-depth"] == "0"
    pr = next(step for step in steps if "gh pr create" in step.get("run", ""))
    assert pr["working-directory"] == "target"
    assert pr["env"]["GH_TOKEN"] == "${{ steps.app-token.outputs.token }}"
    assert pr["env"]["PR_BRANCH"] == "automation/update-main-pr-pipelines-${{ github.run_id }}-${{ github.run_attempt }}"
    assert "if" not in pr  # Default success() prevents PRs after failed validation.
    upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["if"] == "always()"
    assert upload["with"]["name"] == "update-main-pr-pipelines-log"
    assert upload["with"]["path"] == "${{ runner.temp }}/update-main-pr-pipelines.log"


def test_documentation_links_to_renamed_workflow(workflow):
    readme = (ROOT / "README.md").read_text()
    docs = (ROOT / "docs/update-main-pr-pipelines.md").read_text()
    assert f"[{workflow['name']}](.github/workflows/{WORKFLOW.name})" in readme
    assert "(docs/update-main-pr-pipelines.md)" in readme
    assert f"[{workflow['name']} workflow](../.github/workflows/{WORKFLOW.name})" in docs
    assert "`parent_ref`" in docs
    assert "not detected automatically" in " ".join(docs.split())


@pytest.mark.parametrize("fix_exit,validate_exit,expected_calls", [(0, 0, 2), (1, 0, 1), (0, 1, 2)])
def test_validator_commands_and_failure_propagation(
    workflow, tmp_path, fix_exit, validate_exit, expected_calls
):
    step = next(step for step in workflow["jobs"]["update-main-pr-pipelines"]["steps"] if "run" in step)
    assert step["working-directory"] == "target"
    assert step["shell"] == "bash"
    target = tmp_path / "target"
    target.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "with Path(os.environ['CALLS']).open('a') as calls:\n"
        "    calls.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "print('validator output')\n"
        "sys.exit(int(os.environ['FIX_EXIT' if '--fix' in sys.argv else 'VALIDATE_EXIT']))\n"
    )
    uv.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "CALLS": str(calls),
        "FIX_EXIT": str(fix_exit),
        "VALIDATE_EXIT": str(validate_exit),
    }
    # Match Actions' explicit `shell: bash` behavior, including pipefail.
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        cwd=target, env=env, capture_output=True, text=True,
    )
    assert result.returncode == (fix_exit or validate_exit)
    invocations = [json.loads(line) for line in calls.read_text().splitlines()]
    expected = [
        "run", "../validator/scripts/validate-tekton",
        "--config-dir", "../source/tekton-templates", "--path", ".",
        "-b", "main", "--no-checkout",
    ]
    assert invocations == ([expected + ["--fix"], expected][:expected_calls])
    assert (tmp_path / "update-main-pr-pipelines.log").read_text().count("validator output") == expected_calls


@pytest.mark.parametrize("changes", [False, True])
def test_gh_pr_creation_and_commit_scope(workflow, tmp_path, changes):
    step = next(step for step in workflow["jobs"]["update-main-pr-pipelines"]["steps"] if "gh pr create" in step.get("run", ""))
    target = tmp_path / "target"
    target.mkdir()

    def git(*args, cwd=target):
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()

    origin = tmp_path / "origin.git"
    git("init", "--bare", str(origin))
    git("init", "--initial-branch=main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    pipeline = target / "pipelineruns/model/.tekton/model-pull-request.yaml"
    pipeline.parent.mkdir(parents=True)
    pipeline.write_text("before\n")
    unrelated = target / "README.md"
    unrelated.write_text("before\n")
    git("add", ".")
    git("commit", "-m", "Initial state")
    main_sha = git("rev-parse", "HEAD")
    git("remote", "add", "origin", str(origin))
    git("push", "origin", "main")
    unrelated.write_text("unrelated change\n")
    if changes:
        pipeline.write_text("fixed\n")
        # The validator can also delete files. Check that deletions are committed.
        deleted = target / "pipelineruns/model/.tekton/deleted.yaml"
        deleted.write_text("obsolete\n")
        git("add", str(deleted))
        git("commit", "-m", "Add obsolete pipeline")
        main_sha = git("rev-parse", "HEAD")
        git("push", "origin", "main")
        deleted.unlink()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "gh-calls.json"
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "assert os.environ['GH_TOKEN'] == 'test-token'\n"
        "Path(os.environ['CALLS']).write_text(json.dumps(sys.argv[1:]))\n"
        "print('https://github.com/test/repo/pull/123')\n"
    )
    gh.chmod(0o755)
    branch = "automation/update-main-pr-pipelines-42-1"
    summary = tmp_path / "summary.md"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_STEP_SUMMARY": str(summary),
        "GITHUB_REPOSITORY": "test/repo",
        "GH_TOKEN": "test-token",
        "PR_BRANCH": branch,
        "WORKFLOW_URL": "https://github.com/test/repo/actions/runs/42",
        "TEMPLATES_URL": "https://github.com/test/repo/tree/abc/tekton-templates",
        "CALLS": str(calls),
    }
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        cwd=target, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert git("rev-parse", "main") == main_sha
    assert git("rev-parse", "refs/heads/main", cwd=origin) == main_sha
    assert unrelated.read_text() == "unrelated change\n"
    if not changes:
        assert not calls.exists()
        assert git("branch", "--show-current") == "main"
        assert "no PR created" in summary.read_text()
        return

    assert git("branch", "--show-current") == branch
    assert git("log", "-1", "--format=%s") == "chore: update main PR pipelines from release branch push pipelines"
    assert git("rev-parse", f"refs/heads/{branch}", cwd=origin) == git("rev-parse", "HEAD")
    assert set(git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines()) == {
        "pipelineruns/model/.tekton/model-pull-request.yaml",
        "pipelineruns/model/.tekton/deleted.yaml",
    }
    assert json.loads(calls.read_text()) == [
        "pr", "create", "--repo", "test/repo", "--base", "main", "--head", branch,
        "--title", "chore: update main PR pipelines from release branch push pipelines",
        "--body-file", str(tmp_path / "update-main-pr-pipelines-pr.md"),
    ]
    body = (tmp_path / "update-main-pr-pipelines-pr.md").read_text()
    assert "Updates `main` PR pipelines from release branch push pipelines" in body
    assert "release branch configured by `parent_ref`" in body
    assert "`tekton-templates/config.yaml`" in body
    assert "`validate-tekton`" in body
    assert "`--fix`" in body
    assert f"[Workflow run]({env['WORKFLOW_URL']})" in body
    assert "[Pull request](https://github.com/test/repo/pull/123)" in summary.read_text()
