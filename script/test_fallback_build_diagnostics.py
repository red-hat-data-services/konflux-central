"""Exercise the shipped fallback-report step without network access."""
import io
import json
from pathlib import Path
import os
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40
PR_URL = "https://github.com/red-hat-data-services/llm-d-kv-cache/pull/59"
COMPONENT = "odh-llm-d-kv-pvc-evictor"
META = {"pr": "59", "sha": SHA, "pr_url": PR_URL,
        "quay_path": "rhoai/odh-llm-d-kv-pvc-evictor-rhel9"}


@pytest.fixture
def shipped():
    task = yaml.safe_load((ROOT / "early-gate/tasks/generate-snapshot-for-group-testing.yaml").read_text())
    step = next(step for step in task["spec"]["steps"] if step["name"] == "describe-fallback-builds")
    namespace = {"__name__": "fallback_test"}
    exec(compile(step["script"], "<shipped-fallback-step>", "exec"), namespace)
    return namespace


def check(identifier=1, component=COMPONENT, status="completed", conclusion="failure", sha=SHA):
    return {
        "id": identifier, "name": f"Konflux Production Internal / {component}-on-pull-request-59",
        "head_sha": sha, "status": status, "conclusion": conclusion,
        "html_url": f"https://github.com/red-hat-data-services/llm-d-kv-cache/runs/{identifier}",
        "pull_requests": [{"number": 59}],
    }


@pytest.mark.parametrize("status,conclusion,state", [
    ("completed", "failure", "failed"), ("completed", "cancelled", "cancelled"),
    ("completed", "timed_out", "timed_out"), ("completed", "success", "unusable"),
    ("queued", None, "pending"), ("in_progress", None, "running"),
    ("completed", "skipped", "skipped"), ("completed", "neutral", "neutral"),
    ("completed", "action_required", "action_required"),
    ("completed", None, "unavailable"),
])
def test_observed_check_states(shipped, status, conclusion, state):
    result = shipped["classify"]([check(status=status, conclusion=conclusion)], COMPONENT + "-v3-6", META)
    assert result[0] == state
    assert result[2].endswith("/runs/1")


def test_missing_checks_are_not_reported_as_trigger_failure(shipped):
    state, message, _ = shipped["classify"]([], COMPONENT, META)
    assert state == "not_found"
    assert message == "No matching build check found for the requested head"
    assert "never triggered" not in message


def test_api_error_is_not_a_missing_check(shipped):
    assert shipped["classify"](None, COMPONENT, META)[0] == "unavailable"


def test_only_component_and_requested_head_checks_match(shipped):
    unrelated = check(3, component="another-component")
    old = check(4, sha="b" * 40)
    integration = dict(check(5), name=f"Red Hat Konflux / integration-policy / {COMPONENT}")
    wrong_pr = dict(check(6), pull_requests=[{"number": 123}])
    assert shipped["classify"]([unrelated, old, integration, wrong_pr], COMPONENT, META)[0] == "not_found"


def test_newer_pending_retry_supersedes_old_failure(shipped):
    checks = [check(10), check(11, status="queued", conclusion=None)]
    state, _, url = shipped["classify"](checks, COMPONENT, META)
    assert state == "pending" and url.endswith("/11")


def test_repository_alias_and_ea_suffix_match(shipped):
    meta = dict(META, quay_path="rhoai/odh-workbench-jupyter-pytorch-llmcompressor-cuda-py312-rhel9")
    name = "odh-workbench-jupyter-pytorch-llmcompressor-cuda-py312"
    assert shipped["classify"]([check(component=name)], "odh-wb-jupyter-pytorch-llmcompressor-cuda-py312-v3-7-ea-2", meta)[0] == "failed"


def test_check_links_reject_untrusted_urls(shipped):
    assert shipped["build_link"](dict(check(), html_url="https://example.com/stolen")) == ""


def test_pagination_and_shared_head_cache(shipped, monkeypatch):
    requests = []
    responses = [{"check_runs": [check(i, component="unrelated") for i in range(100)]},
                 {"check_runs": [check(101)]}]
    def urlopen(request, timeout):
        requests.append(request)
        return io.StringIO(json.dumps(responses[len(requests) - 1]))
    monkeypatch.setitem(shipped, "urlopen", urlopen)
    client = shipped["Checks"]("fake-token")
    first = client.for_head("red-hat-data-services/llm-d-kv-cache", SHA)
    assert shipped["classify"](first, COMPONENT, META)[0] == "failed"
    assert client.for_head("red-hat-data-services/llm-d-kv-cache", SHA) is first
    assert len(requests) == 2
    assert "page=2" in requests[1].full_url


@pytest.mark.parametrize("failure", ["network", "malformed", "missing_token", "budget"])
def test_diagnostic_errors_degrade_and_are_cached(shipped, monkeypatch, failure):
    calls = []
    def urlopen(request, timeout):
        calls.append(request)
        if failure == "network":
            raise OSError("unavailable")
        return io.StringIO(json.dumps({"message": "not a check response"}))
    monkeypatch.setitem(shipped, "urlopen", urlopen)
    client = shipped["Checks"]("" if failure == "missing_token" else "fake-token")
    if failure == "budget":
        client.deadline = 0
    assert client.for_head("red-hat-data-services/llm-d-kv-cache", SHA) is None
    assert client.for_head("red-hat-data-services/llm-d-kv-cache", SHA) is None
    assert len(calls) == (0 if failure in {"missing_token", "budget"} else 1)


def test_report_uses_collaborator_links_and_keeps_snapshot_unchanged(shipped, monkeypatch, tmp_path):
    other = "odh-another"
    metadata = {COMPONENT + "-v3-6": META, other + "-v3-6": dict(
        META, pr_url="https://github.com/another-org/another-repo/pull/59",
        quay_path="rhoai/odh-another-rhel9")}
    (tmp_path / "group-components.json").write_text(json.dumps(metadata))
    (tmp_path / "component-table.tsv").write_text(
        f"59|{COMPONENT}-v3-6|rhoai-3.6|fallback|sha256:aaa|{PR_URL}\n"
        f"59|{other}-v3-6|rhoai-3.6|fallback|sha256:bbb|https://github.com/another-org/another-repo/pull/59\n"
        "59|odh-ready-v3-6|on-pr-59|ready|sha256:ccc|https://github.com/example/ready-repo/pull/59\n")
    reason = "PR image git.commit does not match the requested head."
    (tmp_path / "fallback-image-reasons.json").write_text(json.dumps({COMPONENT + "-v3-6": reason}))
    (tmp_path / "snapshot.json").write_text('{"unchanged": true}')
    original = {f.name: f.read_bytes() for f in tmp_path.iterdir()}
    calls = []
    def for_head(self, repo, sha):
        calls.append((repo, sha))
        return [check()] if repo.startswith("red-hat-data-services/") else []
    monkeypatch.setattr(shipped["Checks"], "for_head", for_head)
    monkeypatch.setenv("GATE_DATA", str(tmp_path))
    monkeypatch.setenv("GITHUB_CREDENTIALS_BOUND", "false")
    shipped["main"]()
    report = (tmp_path / "fallback-warning.md").read_text()
    assert f"[red-hat-data-services/llm-d-kv-cache#59]({PR_URL})" in report
    assert "[another-org/another-repo#59](https://github.com/another-org/another-repo/pull/59)" in report
    assert "**Build failed**" in report and "[Build check](" in report
    assert "No matching build check found" in report
    assert reason in report and "odh-ready" not in report
    assert COMPONENT + "-v3-6" not in report
    assert "gated-artifacts-promoter" not in report
    assert calls == [("red-hat-data-services/llm-d-kv-cache", SHA), ("another-org/another-repo", SHA)]
    assert all((tmp_path / name).read_bytes() == content for name, content in original.items())

    # Render the actual final comment from the enriched shared report.
    from test_monitor_pac_build import task_script_environment
    _, script, env = task_script_environment("post-build-complete-comment")
    body = tmp_path / "comment.md"
    script = script.split("# --- Post ", 1)[0].replace("BODY=/tmp/comment.md", f"BODY={body}")
    curl = tmp_path / "curl"
    curl.write_text("#!/bin/bash\nprintf '{}'\n")
    curl.chmod(0o755)
    env.update(PATH=f"{tmp_path}:{os.environ['PATH']}", GATE_DATA=str(tmp_path),
               GATE_DATA_BOUND="true", HAS_GROUP_CONFIG="true", STATUS="success")
    result = subprocess.run(["bash", "-c", script], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    comment = body.read_text()
    assert f"| — | {COMPONENT} |" in comment
    assert "| — | odh-another |" in comment
    assert "| [#59](https://github.com/example/ready-repo/pull/59) | odh-ready |" in comment
    assert f"[red-hat-data-services/llm-d-kv-cache#59]({PR_URL})" in comment
    assert "**Build failed**" in comment and "No matching build check found" in comment


def test_pipeline_binds_github_credentials_for_policy_and_diagnostics(shipped):
    task = yaml.safe_load((ROOT / "early-gate/tasks/generate-snapshot-for-group-testing.yaml").read_text())["spec"]
    assert next(w for w in task["workspaces"] if w["name"] == "github-credentials")["optional"]
    step = next(s for s in task["steps"] if s["name"] == "describe-fallback-builds")
    assert step["onError"] == "continue"
    pipeline = yaml.safe_load((ROOT / "early-gate/early-gate-component-pipeline.yaml").read_text())["spec"]
    generate = next(t for t in pipeline["tasks"] if t["name"] == "generate-snapshot")
    assert {"name": "github-credentials", "workspace": "github-credentials"} in generate["workspaces"]
