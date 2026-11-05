"""Run shipped snapshot admission against monitor evidence, without build API calls."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path

import pytest
import yaml

from test_required_pr_images import policy, run_policy, COMPONENT, META, SHA, URL, REPO

LEADER = "red-hat-data-services/gated-artifacts-promoter"
LEADER_SHA = "d" * 40
STATE_PATH = "GAP Leaders/2026-11-05_gap-example/state.json"
UI = "https://konflux-ui.apps.stone-prod-p02.hjvn.p1.openshiftapps.com"


def receipt(status="success"):
    return {
        "pull-requests": [{
            "repo": "foo", "pr-url": URL, "pr-status": "test-pending", "builds": [],
            "build-observations": {
                "source": "konflux", "repository": REPO, "pr": 123, "head-sha": SHA,
                "observed-at": datetime.now(timezone.utc).isoformat(), "pr-state": "OPEN",
                "expected-components": ["odh-foo-v3-6"],
                "runs": [{"component": "odh-foo-v3-6", "status": status, "reason": "From monitor",
                          "head_sha": SHA, "name": "foo-pr-r1", "uid": "uid-one",
                          "namespace": "rhoai-tenant", "created_at": "2026-11-05T10:00:00Z",
                          "url": UI + "/ns/rhoai-tenant/pipelinerun/foo-pr-r1"}],
            },
        }],
        "_evidence-state-commit": LEADER_SHA,
    }


def observe(ns, state):
    return ns["monitor_build_status"](state, COMPONENT, dict(META), 5400)


@pytest.mark.parametrize("status,expected", [
    ("success", "ready"), ("failure", "failed"), ("timed_out", "failed"),
    ("cancelled", "failed"), ("pending", "waiting"), ("missing", "waiting"),
    ("unknown", "waiting"),
])
def test_component_status_comes_from_monitor(policy, status, expected):
    state = receipt(status)
    assert observe(policy, state)[0] == expected


@pytest.mark.parametrize("key,value", [
    ("source", "github"), ("head-sha", "b" * 40), ("repository", "wrong/repo"),
    ("pr", 456), ("pr-state", "CLOSED"),
    ("observed-at", (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()),
    ("query-error", "Konflux unavailable"), ("query-skipped", "Conflict"),
])
def test_obsolete_or_unverified_receipts_wait_for_monitor(policy, key, value):
    state = receipt()
    state["pull-requests"][0]["build-observations"][key] = value
    assert observe(policy, state)[0] == "waiting"


@pytest.mark.parametrize("field,value", [
    ("expected-components", []), ("runs", []),
    ("observed-at", "invalid"), ("observed-at", "2026-11-05T00:00:00"),
    ("observed-at", (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()),
    ("expected-components", ["unrelated"]),
])
def test_invalid_or_incomplete_receipts_cannot_authorize_images(policy, field, value):
    state = receipt()
    state["pull-requests"][0]["build-observations"][field] = value
    with pytest.raises(RuntimeError):
        observe(policy, state)


@pytest.mark.parametrize("field,value", [
    ("head_sha", "b" * 40), ("namespace", "other-tenant"), ("uid", ""),
    ("url", "https://evil.example/ns/rhoai-tenant/pipelinerun/foo-pr-r1"),
    ("url", UI + "/ns/rhoai-tenant/pipelinerun/other-run"),
    ("status", "unsupported"),
])
def test_component_identity_is_checked(policy, field, value):
    state = receipt()
    state["pull-requests"][0]["build-observations"]["runs"][0][field] = value
    with pytest.raises(RuntimeError):
        observe(policy, state)


def test_duplicate_child_or_ambiguous_physical_component_is_rejected(policy):
    state = receipt()
    state["pull-requests"] *= 2
    with pytest.raises(RuntimeError, match="exactly one"):
        observe(policy, state)
    state = receipt()
    evidence = state["pull-requests"][0]["build-observations"]
    evidence["runs"].append(dict(evidence["runs"][0], component="odh-foo-v3-7"))
    evidence["expected-components"].append("odh-foo-v3-7")
    # An exact release-qualified match is allowed.
    assert observe(policy, state)[0] == "ready"
    evidence["runs"][0]["component"] = "odh-foo"
    evidence["expected-components"][0] = "odh-foo"
    with pytest.raises(RuntimeError, match="unique"):
        observe(policy, state)


def test_no_current_component_can_be_authorized_by_an_aggregate_success(policy):
    state = receipt()
    state["pull-requests"][0].pop("build-observations")
    state["pull-requests"][0]["pr-status"] = "build-success"
    assert observe(policy, state)[0] == "waiting"


@pytest.mark.parametrize("status", ["success", "failure", "pending"])
def test_monitor_mode_never_connects_to_either_build_api(policy, tmp_path, monkeypatch, status):
    state = receipt(status)
    calls = []
    def load(*args):
        calls.append(1)
        return copy.deepcopy(state)
    def forbidden(*args):
        pytest.fail("Monitor mode must not create a Konflux client")
    policy["load_monitor_state"] = load
    policy["load_konflux_client"] = forbidden
    monkeypatch.setenv("BUILD_STATUS_SOURCE", "monitor")
    rules, requests = run_policy(policy, tmp_path)
    assert calls
    assert all("/check-runs?" not in url for url in requests)
    assert rules[COMPONENT].get("build_ready", False) == (status == "success")
    assert rules[COMPONENT]["state_commit"] == LEADER_SHA
    if status != "success":
        assert rules[COMPONENT].get("error")


def test_final_admission_reads_newer_monitor_failure(policy, tmp_path, monkeypatch):
    state = receipt()
    policy["load_monitor_state"] = lambda *args: copy.deepcopy(state)
    monkeypatch.setenv("BUILD_STATUS_SOURCE", "monitor")
    rules, _ = run_policy(policy, tmp_path)
    assert rules[COMPONENT]["build_ready"]
    state["pull-requests"][0]["build-observations"]["runs"][0]["status"] = "failure"
    monkeypatch.setenv("VERIFY_ONLY", "true")
    assert policy["main"]() is False
    assert "failure" in (tmp_path / "required-pr-image-failures.md").read_text()


def test_final_admission_rechecks_age_without_requerying_builds(policy, tmp_path, monkeypatch):
    state = receipt()
    policy["load_monitor_state"] = lambda *args: copy.deepcopy(state)
    monkeypatch.setenv("BUILD_STATUS_SOURCE", "monitor")
    run_policy(policy, tmp_path)
    state["pull-requests"][0]["build-observations"]["observed-at"] = (
        datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    monkeypatch.setenv("VERIFY_ONLY", "true")
    assert policy["main"]() is False
    assert "stale" in (tmp_path / "required-pr-image-failures.md").read_text()


def test_monitor_can_refresh_pending_evidence_during_readiness_wait(policy, tmp_path, monkeypatch):
    states = [receipt("pending"), receipt("success")]
    policy["load_monitor_state"] = lambda *args: states.pop(0)
    monkeypatch.setenv("BUILD_STATUS_SOURCE", "monitor")
    rules, _ = run_policy(policy, tmp_path)
    assert not states
    assert rules[COMPONENT]["build_ready"]


def test_closed_pr_release_authorization_does_not_require_monitor_state(policy, tmp_path, monkeypatch):
    policy["load_monitor_state"] = lambda *args: pytest.fail("No required collaborator build")
    monkeypatch.setenv("BUILD_STATUS_SOURCE", "monitor")
    rules, requests = run_policy(policy, tmp_path, state="closed")
    assert rules[COMPONENT]["required"] is False
    assert all("/check-runs?" not in url for url in requests)


@pytest.mark.parametrize("age", ["0", "-1", "86401", "garbage"])
def test_invalid_maximum_age_is_rejected(policy, tmp_path, monkeypatch, age):
    monkeypatch.setenv("BUILD_STATUS_SOURCE", "monitor")
    monkeypatch.setenv("MONITOR_EVIDENCE_MAX_AGE_SECONDS", age)
    with pytest.raises(RuntimeError, match="maximum age"):
        run_policy(policy, tmp_path)


def github_state_client(ns, states):
    class Client:
        cache = {}
        def __init__(self):
            self.calls = []
        def get(self, path):
            self.calls.append(path)
            if "/pulls/80" in path:
                return {"state": "open", "head": {"sha": LEADER_SHA, "repo": {"full_name": LEADER}}}
            return {"type": "file", "encoding": "base64", "content":
                    base64.b64encode(json.dumps(states[-1]).encode()).decode()}
        def pages(self, path):
            self.calls.append(path)
            return [{"filename": STATE_PATH, "status": "added"}]
    return Client()


def test_monitor_state_is_loaded_from_current_same_repo_leader_head(policy, tmp_path, monkeypatch):
    monkeypatch.setenv("LEADER_PR_REPO", LEADER)
    monkeypatch.setenv("LEADER_PR_NUMBER", "80")
    client = github_state_client(policy, [receipt()])
    state = policy["load_monitor_state"](client, tmp_path)
    assert state["_evidence-state-commit"] == LEADER_SHA
    assert any("GAP%20Leaders/" in call and call.endswith("?ref=" + LEADER_SHA) for call in client.calls)
    assert (tmp_path / "monitor-state.json").exists()


@pytest.mark.parametrize("case", ["fork", "closed", "duplicate", "missing", "malformed"])
def test_monitor_state_transport_errors_cannot_authorize_images(policy, tmp_path, monkeypatch, case):
    monkeypatch.setenv("LEADER_PR_REPO", LEADER)
    monkeypatch.setenv("LEADER_PR_NUMBER", "80")
    client = github_state_client(policy, [receipt()])
    original = client.get
    def get(path):
        value = original(path)
        if "/pulls/80" in path:
            if case == "fork":
                value["head"]["repo"]["full_name"] = "outsider/fork"
            if case == "closed":
                value["state"] = "closed"
        elif case == "malformed":
            value["content"] = "invalid"
        return value
    client.get = get
    if case in {"duplicate", "missing"}:
        client.pages = lambda path: ([{"filename": STATE_PATH, "status": "added"}] * 2
                                    if case == "duplicate" else [])
    with pytest.raises(RuntimeError):
        policy["load_monitor_state"](client, tmp_path)


def test_pipeline_passes_monitor_identity_and_age():
    path = Path(__file__).resolve().parents[1] / "early-gate/early-gate-component-pipeline.yaml"
    pipeline = yaml.safe_load(path.read_text())["spec"]
    task = next(t for t in pipeline["tasks"] if t["name"] == "generate-snapshot")
    params = {p["name"]: p["value"] for p in task["params"]}
    assert params["leader-pr-repo"] == "$(params.leader-pr-repo)"
    assert params["monitor-evidence-max-age-seconds"] == "$(params.monitor-evidence-max-age-seconds)"


def test_aggregate_build_failure_cannot_be_hidden_by_one_successful_component(policy):
    state = receipt()
    state["pull-requests"][0]["pr-status"] = "build-failure"
    assert observe(policy, state)[0] == "failed"


def test_many_components_reuse_one_monitor_state_fetch(policy, tmp_path, monkeypatch):
    monkeypatch.setenv("BUILD_STATUS_SOURCE", "monitor")
    monkeypatch.setenv("LEADER_PR_REPO", LEADER)
    monkeypatch.setenv("LEADER_PR_NUMBER", "80")
    state = receipt()
    evidence = state["pull-requests"][0]["build-observations"]
    template = evidence["runs"][0]
    components = {f"odh-foo-{i}-v3-6": dict(META, quay_path=f"rhoai/odh-foo-{i}-rhel9") for i in range(20)}
    evidence["expected-components"] = list(components)
    evidence["runs"] = [dict(template, component=c) for c in components]
    requests = []
    def urlopen(request, timeout):
        url = request.full_url
        requests.append(url)
        if "/pulls/123" in url:
            data = {"number": 123, "state": "open", "head": {"sha": SHA}}
        elif "/pulls/80/files?" in url:
            data = [{"filename": STATE_PATH, "status": "added"}]
        elif "/pulls/80" in url:
            data = {"state": "open", "head": {"sha": LEADER_SHA, "repo": {"full_name": LEADER}}}
        elif "/contents/" in url:
            data = {"type": "file", "encoding": "base64",
                    "content": base64.b64encode(json.dumps(state).encode()).decode()}
        else:
            pytest.fail("Unexpected build API lookup: " + url)
        return io.StringIO(json.dumps(data))
    policy["urlopen"] = urlopen
    (tmp_path / "group-components.json").write_text(json.dumps(components))
    assert policy["main"]() is True
    assert len(requests) == 4
    policies = json.loads((tmp_path / "pr-image-policy.json").read_text())
    assert all(item["build_ready"] for item in policies.values())
    # Final verification fetches current Leader state instead of trusting the
    # workspace diagnostic copy or the initial monitor verdict.
    evidence["runs"][0]["status"] = "failure"
    monkeypatch.setenv("VERIFY_ONLY", "true")
    assert policy["main"]() is False
    assert len(requests) == 8


def test_monitor_refresh_does_not_reuse_a_stale_leader_file_listing(policy, tmp_path, monkeypatch):
    monkeypatch.setenv("LEADER_PR_REPO", LEADER)
    monkeypatch.setenv("LEADER_PR_NUMBER", "80")
    requests = []
    heads = [LEADER_SHA, "e" * 40]
    def urlopen(request, timeout):
        url = request.full_url
        requests.append(url)
        if "/files?" in url:
            data = [{"filename": STATE_PATH, "status": "added"}]
        elif "/pulls/80" in url:
            data = {"state": "open", "head": {"sha": heads.pop(0), "repo": {"full_name": LEADER}}}
        else:
            data = {"type": "file", "encoding": "base64",
                    "content": base64.b64encode(json.dumps(receipt()).encode()).decode()}
        return io.StringIO(json.dumps(data))
    policy["urlopen"] = urlopen
    client = policy["GitHub"]("", 100)
    first = policy["load_monitor_state"](client, tmp_path)
    second = policy["load_monitor_state"](client, tmp_path)
    assert first["_evidence-state-commit"] != second["_evidence-state-commit"]
    assert len([url for url in requests if "/files?" in url]) == 2
