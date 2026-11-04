"""Exercise shipped strict PR policy and Bash snapshot selection without network."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = "odh-foo-v3-6"
SHA = "a" * 40
URL = "https://github.com/red-hat-data-services/foo/pull/123"
REPO = "red-hat-data-services/foo"
META = {"quay_path": "rhoai/odh-foo-rhel9", "pr": "123", "sha": SHA, "pr_url": URL}


def check(conclusion="success", *, status="completed", ident=10):
    return {"id": ident, "name": "Konflux Production Internal / odh-foo-on-pull-request-123",
            "head_sha": SHA, "status": status, "conclusion": conclusion,
            "html_url": f"https://github.com/{REPO}/runs/{ident}",
            "app": {"slug": "konflux-internal-p02"}, "pull_requests": [{"number": 123}]}


@pytest.fixture
def policy(monkeypatch, tmp_path):
    task = yaml.safe_load((ROOT / "early-gate/tasks/generate-snapshot-for-group-testing.yaml").read_text())["spec"]
    source = next(s["script"] for s in task["steps"] if s["name"] == "resolve-pr-image-policy")
    ns = {"__name__": "test_policy"}
    exec(compile(source, "shipped-pr-policy", "exec"), ns)
    clock = [0]
    ns["time"] = SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda n: clock.__setitem__(0, clock[0] + n))
    monkeypatch.setenv("GATE_DATA", str(tmp_path))
    monkeypatch.setenv("GITHUB_CREDENTIALS_BOUND", "false")
    monkeypatch.setenv("PR_READINESS_TIMEOUT_SECONDS", "1")
    return ns


def run_policy(ns, tmp_path, *, state="open", checks=None, replacements=None,
               sha=SHA, failure=None, components=None):
    inputs = components if components is not None else {COMPONENT: dict(META)}
    (tmp_path / "group-components.json").write_text(json.dumps(inputs))
    requests = []
    def urlopen(request, timeout):
        requests.append(request.full_url)
        if failure:
            if failure == "http":
                raise HTTPError(request.full_url, 403, "forbidden", {}, None)
            raise OSError("secret response must not be echoed")
        if "/check-runs?" in request.full_url:
            data = {"check_runs": [check()] if checks is None else checks}
        elif "/pulls?" in request.full_url:
            data = [] if replacements is None else replacements
        else:
            data = {"number": 123, "state": state, "head": {"sha": sha}}
        return io.StringIO(json.dumps(data))
    ns["urlopen"] = urlopen
    ns["main"]()
    return json.loads((tmp_path / "pr-image-policy.json").read_text()), requests


def snapshot(tmp_path, *, image_sha=SHA, image_available=True, source_labels=True, retry_clock=False):
    from test_monitor_pac_build import task_script_environment
    _, script, env = task_script_environment("generate-snapshot-for-group-testing")
    script = script.replace("cat /etc/*release", ":").replace("/var/workdir/snapshot", str(tmp_path / "snapshot"))
    script = script.replace("$(results.omitted-component-count.path)", str(tmp_path / "omitted-count"))
    binary = tmp_path / "skopeo"
    binary.write_text(f"#!{sys.executable}\n" + r'''
import json, os, sys
from pathlib import Path
log = Path(os.environ["PROBE_LOG"])
with log.open("a") as out:
    out.write(sys.argv[-1] + "\n")
if os.environ["IMAGE_AVAILABLE"] != "true":
    sys.exit(1)
labels = {"git.commit": os.environ["IMAGE_SHA"]}
if os.environ["SOURCE_LABELS"] == "true":
    labels["git.url"] = "https://github.com/red-hat-data-services/foo"
print(json.dumps({"Digest": "sha256:" + "b" * 64, "Labels": labels}))
''')
    binary.chmod(0o755)
    env.update(PATH=f"{tmp_path}:{os.environ['PATH']}", GATE_DATA=str(tmp_path),
               RHOAI_VERSION="3.6", FALLBACK_TAG="", PR_PIPELINES_REPO="",
               WORKSPACE_BASIC_AUTH_DIRECTORY_BOUND="false", LEADER_PR_NUMBER="49",
               PR_READINESS_TIMEOUT_SECONDS="30", PROBE_LOG=str(tmp_path / "probes"),
               IMAGE_SHA=image_sha, IMAGE_AVAILABLE=str(image_available).lower(),
               SOURCE_LABELS=str(source_labels).lower())
    # Avoid a real sleep in the missing-image cases; use a deadline that is
    # already reached after the initial probes instead.
    if retry_clock:
        script = script.replace("IMAGE_DEADLINE=$((SECONDS + PR_READINESS_TIMEOUT_SECONDS))", "SECONDS=0; IMAGE_DEADLINE=4")
        script = script.replace("sleep 5", "SECONDS=$((SECONDS + 1))")
    elif not image_available or image_sha != SHA:
        script = script.replace("sleep 5", "IMAGE_DEADLINE=$SECONDS")
    result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    probes = (tmp_path / "probes").read_text().splitlines() if (tmp_path / "probes").exists() else []
    return result, probes


def test_current_head_build_and_image_are_used(policy, tmp_path):
    rules, _ = run_policy(policy, tmp_path)
    assert rules[COMPONENT]["required"] is True
    assert rules[COMPONENT]["build_ready"] is True
    result, probes = snapshot(tmp_path)
    assert result.returncode == 0, result.stderr
    image = json.loads((tmp_path / "snapshot/snapshot.json").read_text())[COMPONENT]
    assert image["git.commit"] == SHA
    assert image["image_tag"] == "on-pr-123"
    assert all("rhoai-3.6" not in p for p in probes)


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "skipped", "neutral", "action_required"])
def test_failed_build_blocks_even_when_pr_image_exists(policy, tmp_path, conclusion):
    rules, _ = run_policy(policy, tmp_path, checks=[check(conclusion)])
    assert conclusion in rules[COMPONENT]["error"]
    result, probes = snapshot(tmp_path)
    assert result.returncode != 0
    assert probes == []
    assert not (tmp_path / "snapshot/snapshot.json").exists()
    report = (tmp_path / "required-pr-image-failures.md").read_text()
    assert URL in report and "/runs/10" in report and conclusion in report


@pytest.mark.parametrize("checks", [[], [check(None, status="in_progress")]])
def test_missing_or_pending_check_hits_deadline_without_fallback(policy, tmp_path, checks):
    rules, requests = run_policy(policy, tmp_path, checks=checks)
    assert rules[COMPONENT]["required"] is True
    assert "error" in rules[COMPONENT]
    assert "deadline" in rules[COMPONENT]["error"].lower()
    assert ("pending" if checks else "No matching") in rules[COMPONENT]["error"]
    assert "could not be verified" not in rules[COMPONENT]["error"]
    assert len(requests) == 2  # No lookup is issued after the hard deadline.
    assert any("/check-runs?" in r for r in requests)
    result, probes = snapshot(tmp_path)
    assert result.returncode != 0 and probes == []


@pytest.mark.parametrize("failure", ["http", "network"])
def test_lookup_failure_is_not_evidence_of_no_active_pr(policy, tmp_path, failure):
    rules, _ = run_policy(policy, tmp_path, failure=failure)
    assert rules[COMPONENT]["required"] is None
    assert "secret response" not in rules[COMPONENT]["error"]
    result, probes = snapshot(tmp_path)
    assert result.returncode != 0 and probes == []


@pytest.mark.parametrize("mode", ["missing", "stale", "missing_source"])
def test_unusable_active_pr_image_cannot_fall_back_or_be_omitted(policy, tmp_path, mode):
    run_policy(policy, tmp_path)
    result, probes = snapshot(tmp_path, image_available=mode != "missing",
                              image_sha="c" * 40 if mode == "stale" else SHA,
                              source_labels=mode != "missing_source")
    assert result.returncode != 0
    assert not (tmp_path / "snapshot/snapshot.json").exists()
    assert all("rhoai-3.6" not in p for p in probes)
    assert (tmp_path / "required-pr-image-failures.md").read_text()


def test_closed_or_merged_pr_allows_release_only_after_absence_query(policy, tmp_path):
    rules, requests = run_policy(policy, tmp_path, state="closed")
    assert rules[COMPONENT]["required"] is False
    assert any("/pulls?state=open&base=stable" in r for r in requests)
    assert not any("/check-runs?" in r for r in requests)
    result, probes = snapshot(tmp_path)
    assert result.returncode == 0, result.stderr
    assert probes == ["docker://quay.io/rhoai/odh-foo-rhel9:rhoai-3.6"]
    assert json.loads((tmp_path / "snapshot/snapshot.json").read_text())[COMPONENT]["image_tag"] == "rhoai-3.6"


@pytest.mark.parametrize("branch", ["main", "master"])
def test_closed_link_cannot_hide_replacement_sync_pr(policy, tmp_path, branch):
    other = {"number": 456, "state": "open", "base": {"ref": "stable"},
             "head": {"ref": branch, "repo": {"full_name": REPO}}}
    rules, _ = run_policy(policy, tmp_path, state="closed", replacements=[other])
    assert "replacement sync PR #456" in rules[COMPONENT]["error"]
    result, probes = snapshot(tmp_path)
    assert result.returncode != 0 and probes == []


def test_unrelated_open_feature_pr_does_not_invalidate_confirmed_absence(policy, tmp_path):
    other = {"number": 456, "state": "open", "base": {"ref": "stable"},
             "head": {"ref": "feature", "repo": {"full_name": REPO}}}
    rules, _ = run_policy(policy, tmp_path, state="closed", replacements=[other])
    assert rules[COMPONENT]["required"] is False


def test_moved_pr_head_is_not_satisfied_by_old_image(policy, tmp_path):
    rules, _ = run_policy(policy, tmp_path, sha="d" * 40)
    assert "head moved" in rules[COMPONENT]["error"]
    result, probes = snapshot(tmp_path)
    assert result.returncode != 0 and probes == []


def test_newer_successful_retry_supersedes_old_failure(policy, tmp_path):
    rules, _ = run_policy(policy, tmp_path, checks=[check("failure", ident=1), check("success", ident=2)])
    assert rules[COMPONENT]["build_ready"] is True
    assert "error" not in rules[COMPONENT]


@pytest.mark.parametrize("change", ["other_component", "old_head", "wrong_app", "other_pr"])
def test_unrelated_check_cannot_authorize_current_pr(policy, tmp_path, change):
    candidate = check()
    if change == "other_component":
        candidate["name"] = "Konflux Production Internal / odh-unrelated-on-pull-request-123"
    elif change == "old_head":
        candidate["head_sha"] = "c" * 40
    elif change == "wrong_app":
        candidate["app"]["slug"] = "untrusted"
    else:
        candidate["pull_requests"] = [{"number": 999}]
    rules, _ = run_policy(policy, tmp_path, checks=[candidate])
    assert "error" in rules[COMPONENT]
    assert not rules[COMPONENT].get("build_ready")


def test_many_components_reuse_pr_and_check_api_reads(policy, tmp_path):
    components = {COMPONENT: dict(META), "odh-alias-v3-6": dict(META)}
    rules, requests = run_policy(policy, tmp_path, components=components)
    assert all(p["build_ready"] for p in rules.values())
    assert len(requests) == 2


def test_empty_group_needs_no_github_requests(policy, tmp_path):
    rules, requests = run_policy(policy, tmp_path, components={})
    assert rules == {} and requests == []


def test_final_failure_comment_contains_required_image_failure(policy, tmp_path):
    from test_monitor_pac_build import task_script_environment
    run_policy(policy, tmp_path, checks=[check("failure")])
    snapshot(tmp_path)
    _, script, env = task_script_environment("post-build-complete-comment")
    body = tmp_path / "comment.md"
    script = script.split("# --- Post ", 1)[0].replace("BODY=/tmp/comment.md", f"BODY={body}")
    curl = tmp_path / "curl"
    curl.write_text("#!/bin/bash\nprintf '{}'\n")
    curl.chmod(0o755)
    env.update(PATH=f"{tmp_path}:{os.environ['PATH']}", GATE_DATA=str(tmp_path),
               GATE_DATA_BOUND="true", HAS_GROUP_CONFIG="true", STATUS="failure")
    result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    comment = body.read_text()
    assert "Early Gate Build Failed" in comment
    assert "Required collaborator PR images blocked" in comment
    assert URL in comment and "/runs/10" in comment
    assert "No release-image substitution" in comment


@pytest.mark.parametrize("new_state,new_sha", [("open", "d" * 40), ("closed", SHA)])
def test_final_check_rejects_state_or_head_changes_after_image_selection(policy, tmp_path, monkeypatch, new_state, new_sha):
    run_policy(policy, tmp_path)
    result, _ = snapshot(tmp_path)
    assert result.returncode == 0
    monkeypatch.setenv("VERIFY_ONLY", "true")
    def updated(request, timeout):
        if "/pulls?" in request.full_url:
            return io.StringIO("[]")
        return io.StringIO(json.dumps({"number": 123, "state": new_state, "head": {"sha": new_sha}}))
    policy["urlopen"] = updated
    assert policy["main"]() is False
    assert (tmp_path / "required-pr-image-failures.md").read_text()
    # The task creates no trusted artifact until this final policy check passes.
    task = yaml.safe_load((ROOT / "early-gate/tasks/generate-snapshot-for-group-testing.yaml").read_text())["spec"]
    names = [s["name"] for s in task["steps"]]
    assert names.index("generate-snapshot") < names.index("verify-pr-image-policy") < names.index("create-trusted-artifact")


def test_final_check_preserves_the_generated_summary(policy, tmp_path, monkeypatch):
    run_policy(policy, tmp_path)
    result, _ = snapshot(tmp_path)
    assert result.returncode == 0
    table = (tmp_path / "component-table.tsv").read_bytes()
    monkeypatch.setenv("VERIFY_ONLY", "true")
    assert policy["main"]() is True
    assert (tmp_path / "component-table.tsv").read_bytes() == table


def test_repeated_components_do_not_repeat_an_inaccessible_pr_lookup(policy, tmp_path):
    rules, requests = run_policy(policy, tmp_path, failure="http",
                                components={COMPONENT: dict(META), "odh-alias-v3-6": dict(META)})
    assert all(p["required"] is None for p in rules.values())
    assert len(requests) == 1


@pytest.mark.parametrize("required", [None, "unknown", 0])
def test_incomplete_policy_cannot_authorize_release(tmp_path, required):
    (tmp_path / "group-components.json").write_text(json.dumps({COMPONENT: META}))
    (tmp_path / "pr-image-policy.json").write_text(json.dumps({COMPONENT: {"required": required}}))
    result, probes = snapshot(tmp_path)
    assert result.returncode != 0
    assert probes == []


def test_incomplete_open_pr_listing_cannot_authorize_release(policy, tmp_path):
    other = {"number": 456, "state": "open", "base": {"ref": "stable"},
             "head": {"ref": "main", "repo": {}}}
    rules, _ = run_policy(policy, tmp_path, state="closed", replacements=[other])
    assert rules[COMPONENT]["required"] is None
    assert "Incomplete" in rules[COMPONENT]["error"]


def test_image_retry_report_keeps_only_the_latest_probe_reason(policy, tmp_path):
    run_policy(policy, tmp_path)
    result, probes = snapshot(tmp_path, image_available=False, retry_clock=True)
    assert result.returncode != 0
    assert len(probes) >= 2
    report = (tmp_path / "required-pr-image-failures.md").read_text()
    assert report.count("PR image unreadable") == 1
    assert len(report) < 1200
