"""The shipped snapshot policy must use Konflux build truth when enabled."""
import base64
import io
import json
from types import SimpleNamespace

import pytest

from test_required_pr_images import policy, run_policy, COMPONENT, META, URL


class Client:
    def __init__(self, status):
        self.status, self.calls, self._cache = status, [], {}
    def clear_cache(self):
        self._cache.clear()
    def observe(self, **query):
        self.calls.append(query)
        if self.status == "error":
            raise RuntimeError("Konflux archive unavailable")
        return SimpleNamespace(status=self.status, reason="Observed from Konflux",
                               url="https://konflux.example/ns/rhoai-tenant/pipelinerun/foo-pr-r1")


@pytest.mark.parametrize("status", ["success", "failure", "timed_out", "cancelled", "pending", "missing", "unknown", "error"])
def test_konflux_policy_never_reads_github_build_checks(policy, tmp_path, monkeypatch, status):
    client = Client(status)
    monkeypatch.setenv("BUILD_STATUS_SOURCE", "konflux")
    policy["load_konflux_client"] = lambda *args: (None, client)
    rules, requests = run_policy(policy, tmp_path)
    assert all("/check-runs?" not in url for url in requests)
    assert rules[COMPONENT].get("build_ready", False) is (status == "success")
    if status != "success":
        assert rules[COMPONENT].get("error")
        assert URL in (tmp_path / "required-pr-image-failures.md").read_text()
    assert client.calls[0]["components"] == {"odh-foo"}
    assert client.calls[0]["base_names"] is True


def test_closed_pr_fallback_uses_pr_state_not_a_build_status_mirror(policy, tmp_path, monkeypatch):
    client = Client("failure")
    monkeypatch.setenv("BUILD_STATUS_SOURCE", "konflux")
    policy["load_konflux_client"] = lambda *args: (None, client)
    rules, requests = run_policy(policy, tmp_path, state="closed")
    assert rules[COMPONENT]["required"] is False
    assert client.calls == []
    assert any("/pulls?state=open&base=stable" in url for url in requests)


def test_final_konflux_check_blocks_a_new_failed_attempt(policy, tmp_path, monkeypatch):
    client = Client("success")
    monkeypatch.setenv("BUILD_STATUS_SOURCE", "konflux")
    policy["load_konflux_client"] = lambda *args: (None, client)
    run_policy(policy, tmp_path)
    client.status = "failure"
    monkeypatch.setenv("VERIFY_ONLY", "true")
    assert policy["main"]() is False
    assert "Konflux PipelineRun failure" in (tmp_path / "required-pr-image-failures.md").read_text()


def test_loader_handles_real_github_wrapped_base64_and_explicit_revision(policy, tmp_path, monkeypatch):
    source = b"""KONFLUX_BUILD_API_VERSION = 1
class KonfluxClient:
    @classmethod
    def from_environment(cls, **kwargs):
        return kwargs
"""
    seen = []
    def request(req, timeout):
        seen.append(req.full_url)
        return io.StringIO(json.dumps({"type": "file", "encoding": "base64",
                                      "content": base64.encodebytes(source).decode()}))
    policy["urlopen"] = request
    monkeypatch.setenv("DEVOPS_SHARED_RESOURCES_REVISION", "fix/reviewed-helper")
    monkeypatch.setenv("KONFLUX_CREDENTIALS_BOUND", "true")
    monkeypatch.setenv("KONFLUX_CREDENTIALS_PATH", str(tmp_path))
    (tmp_path / "token").write_text("fake-test-token")
    module, client = policy["load_konflux_client"]("", tmp_path, 42)
    assert client["deadline"] == 42
    assert "ref=fix%2Freviewed-helper" in seen[0]
    assert module.KONFLUX_BUILD_API_VERSION == 1


def test_unknown_helper_contract_blocks_loading(policy, tmp_path, monkeypatch):
    source = b"KONFLUX_BUILD_API_VERSION = 999"
    policy["urlopen"] = lambda *args, **kwargs: io.StringIO(json.dumps({
        "type": "file", "encoding": "base64", "content": base64.b64encode(source).decode()}))
    with pytest.raises(RuntimeError, match="contract"):
        policy["load_konflux_client"]("", tmp_path, 42)

def test_no_active_pr_needs_no_cluster_credential_or_helper(policy, tmp_path, monkeypatch):
    monkeypatch.setenv("BUILD_STATUS_SOURCE", "konflux")
    def forbidden(*args):
        pytest.fail("Closed-PR fallback must not initialize a cluster lookup")
    policy["load_konflux_client"] = forbidden
    rules, _ = run_policy(policy, tmp_path, state="closed")
    assert rules[COMPONENT]["required"] is False


def test_initialization_error_keeps_component_pr_in_final_failure_report(policy, tmp_path, monkeypatch):
    monkeypatch.setenv("BUILD_STATUS_SOURCE", "konflux")
    def unavailable(*args):
        raise RuntimeError("Konflux read-credential workspace is required")
    policy["load_konflux_client"] = unavailable
    rules, _ = run_policy(policy, tmp_path)
    assert "read-credential" in rules[COMPONENT]["error"]
    report = (tmp_path / "required-pr-image-failures.md").read_text()
    assert URL in report
    assert COMPONENT in report
