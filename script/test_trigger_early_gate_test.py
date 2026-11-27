"""Verify the optional early-gate test trigger preserves aggregate success."""
import os
from pathlib import Path
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "early-gate/tasks/trigger-early-gate-test.yaml"
PIPELINE = ROOT / "early-gate/early-gate-component-pipeline.yaml"


def task_script():
    task = yaml.safe_load(TASK.read_text())["spec"]
    return next(step["script"] for step in task["steps"] if step["name"] == "post-comment")


def run_trigger(tmp_path, trigger, comment_posted):
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    (credentials / "token").write_text("fake-token")
    calls = tmp_path / "calls.json"
    curl = tmp_path / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$CALL_LOG"\n'
        'printf \'201\'\n'
    )
    curl.chmod(0o755)
    env = dict(
        os.environ,
        PATH=f"{tmp_path}:{os.environ['PATH']}",
        CALL_LOG=str(calls),
        LEADER_PR_REPO="red-hat-data-services/gated-artifacts-promoter",
        LEADER_PR_NUMBER="153",
        TRIGGER_EG_TESTS=trigger,
        COMMENT_POSTED=comment_posted,
        GITHUB_CREDENTIALS_PATH=str(credentials),
    )
    result = subprocess.run(
        ["bash", "-c", task_script()],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    posted = calls.read_text().splitlines() if calls.exists() else []
    return result, posted


def test_pipeline_keeps_optional_trigger_out_of_when():
    spec = yaml.safe_load(PIPELINE.read_text())["spec"]
    task = next(task for task in spec["tasks"] if task["name"] == "trigger-early-gate-test")
    assert "when" not in task
    params = {param["name"]: param["value"] for param in task["params"]}
    assert params["trigger-eg-tests"] == "$(params.trigger-eg-tests)"
    assert params["comment-posted"] == "$(tasks.comment-on-leader-pr.results.comment-posted)"


def test_trigger_no_ops_when_disabled(tmp_path):
    result, posted = run_trigger(tmp_path, trigger="false", comment_posted="true")
    assert result.returncode == 0, result.stderr
    assert posted == []
    assert "Skipping /early-gate-tests" in result.stdout


def test_trigger_no_ops_when_verdict_comment_was_not_posted(tmp_path):
    result, posted = run_trigger(tmp_path, trigger="true", comment_posted="false")
    assert result.returncode == 0, result.stderr
    assert posted == []
    assert "Skipping /early-gate-tests" in result.stdout


def test_trigger_posts_when_enabled_and_verdict_posted(tmp_path):
    result, posted = run_trigger(tmp_path, trigger="true", comment_posted="true")
    assert result.returncode == 0, result.stderr
    assert len(posted) == 1
    assert "issues/153/comments" in posted[0]
