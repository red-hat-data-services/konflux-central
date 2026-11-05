"""Regression tests run the shipped Bash resolver with registry-shaped fixtures."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_monitor_pac_build import task_script_environment
from test_required_pr_images import SHA, URL

FAKE_SKOPEO = r"""
import hashlib, json, os, sys
from pathlib import Path
args = sys.argv[1:]
if args[0] == "manifest-digest":
    print("sha256:" + hashlib.sha256(Path(args[1]).read_bytes()).hexdigest())
    sys.exit(0)
ref = args[-1].removeprefix("docker://")
repo = ref.split("@")[0].split(":")[0]
scenarios = json.loads(os.environ["REGISTRY_SCENARIOS"])
scenario = scenarios.get(repo, {})
with Path(os.environ["PROBE_LOG"]).open("a") as out:
    out.write(json.dumps({"ref": ref, "args": args}) + "\n")
mode = scenario.get("mode", "index")
if mode in {"missing", "auth", "network"}:
    print({"missing": "manifest unknown", "auth": "unauthorized: access denied",
           "network": "connection reset by peer"}[mode], file=sys.stderr)
    sys.exit(1)
counter_path = Path(os.environ["PROBE_LOG"] + ".counts")
counts = json.loads(counter_path.read_text()) if counter_path.exists() else {}
if "--raw" not in args and "@" not in ref:
    counts[repo] = counts.get(repo, 0) + 1
    counter_path.write_text(json.dumps(counts))
    if counts[repo] <= scenario.get("missing_rounds", 0):
        print("manifest unknown", file=sys.stderr)
        sys.exit(1)
arches = scenario.get("arches", ["amd64"])
digests = {a: "sha256:" + hashlib.sha256((repo + a).encode()).hexdigest() for a in arches}
index = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
         "manifests": [{"mediaType": "application/vnd.oci.image.manifest.v1+json",
                       "digest": digests[a], "platform": {"os": "linux", "architecture": a}}
                      for a in arches]}
if scenario.get("attestation"):
    index["manifests"].append({"digest": "sha256:" + "f" * 64,
                              "platform": {"os": "unknown", "architecture": "unknown"}})
if scenario.get("bad_descriptor"):
    index["manifests"][0]["digest"] = "invalid"
raw = json.dumps(index) + "\n"
index_digest = "sha256:" + hashlib.sha256(raw.encode()).hexdigest()
if "--raw" in args:
    print(raw, end="")
    sys.exit(0)
if "@" in ref:
    requested = ref.split("@", 1)[1]
    arch = next((a for a, d in digests.items() if d == requested), None)
    if arch is None:
        print("unexpected digest", file=sys.stderr)
        sys.exit(1)
    digest = requested
else:
    arch = arches[0] if mode == "single" else "amd64"
    if arch not in arches:
        print('no image found in manifest list for architecture "amd64", variant "", OS "linux"',
              file=sys.stderr)
        sys.exit(1)
    digest = digests[arch] if mode == "single" else index_digest
labels = {"git.commit": scenario.get("sha_by_arch", {}).get(arch, os.environ["EXPECTED_SHA"]),
          "git.url": "https://github.com/red-hat-data-services/foo"}
if scenario.get("missing_source"):
    labels.pop("git.url")
if scenario.get("bad_digest"):
    digest = "invalid"
print(json.dumps({"Digest": digest, "Architecture": arch, "Os": "linux", "Labels": labels}))
"""


def execute(tmp_path, scenarios, *, required=True, expire_before_first=False, shared=False):
    _, script, env = task_script_environment("generate-snapshot-for-group-testing")
    script = script.replace("cat /etc/*release", ":").replace(
        "/var/workdir/snapshot", str(tmp_path / "snapshot")).replace(
        "$(results.omitted-component-count.path)", str(tmp_path / "omitted-count"))
    script = script.replace("IMAGE_DEADLINE=$((SECONDS + PR_READINESS_TIMEOUT_SECONDS))",
                            "SECONDS=0; IMAGE_DEADLINE=" + ("0" if expire_before_first else "20"))
    # Advance the parent shell's clock without real retry sleeps.
    script = script.replace('sleep "' + chr(36) + '{retry_delay}"',
                            "SECONDS=$((SECONDS + 10))")
    components = {
        name: {"quay_path": "rhoai/" + name, "pr": "123", "sha": SHA, "pr_url": URL}
        for name in scenarios
    }
    rules = {name: {"required": required, "build_ready": True, "reason": "No active sync PR"}
             for name in scenarios}
    (tmp_path / "group-components.json").write_text(json.dumps(components))
    (tmp_path / "pr-image-policy.json").write_text(json.dumps(rules))
    skopeo = tmp_path / "skopeo"
    skopeo.write_text("#!" + sys.executable + "\n" + FAKE_SKOPEO)
    skopeo.chmod(0o755)
    registry = {"quay.io/rhoai/" + name: spec for name, spec in scenarios.items()}
    if shared:
        registry["quay.io/rhoai/pull-request-pipelines"] = {"mode": "missing"}
    env.update(PATH=str(tmp_path) + os.pathsep + os.environ["PATH"], GATE_DATA=str(tmp_path),
               RHOAI_VERSION="3.7-ea.1", FALLBACK_TAG="", PR_NUMBER="80", LEADER_PR_NUMBER="80",
               PR_PIPELINES_REPO="rhoai/pull-request-pipelines" if shared else "",
               PR_READINESS_TIMEOUT_SECONDS="120", WORKSPACE_BASIC_AUTH_DIRECTORY_BOUND="false",
               PROBE_LOG=str(tmp_path / "probes"), REGISTRY_SCENARIOS=json.dumps(registry),
               EXPECTED_SHA=SHA)
    result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=20)
    probes = [json.loads(x) for x in (tmp_path / "probes").read_text().splitlines()] if (
        tmp_path / "probes").exists() else []
    return result, probes


def snapshot(tmp_path):
    return json.loads((tmp_path / "snapshot/snapshot.json").read_text())


@pytest.mark.parametrize("arches", [["amd64"], ["s390x"], ["ppc64le"], ["s390x", "ppc64le"]])
def test_valid_platforms_keep_published_index_digest(tmp_path, arches):
    result, probes = execute(tmp_path, {"cpu": {"arches": arches}})
    assert result.returncode == 0, result.stderr
    image = snapshot(tmp_path)["cpu"]
    descriptors = [{"mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": "sha256:" + hashlib.sha256(("quay.io/rhoai/cpu" + a).encode()).hexdigest(),
                    "platform": {"os": "linux", "architecture": a}} for a in arches]
    raw = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
                      "manifests": descriptors}) + "\n"
    expected = "sha256:" + hashlib.sha256(raw.encode()).hexdigest()
    assert image["image"] == "quay.io/rhoai/cpu@" + expected
    assert image["git.commit"] == SHA and image["image_tag"] == "on-pr-123"
    if "amd64" in arches:
        assert len(probes) == 1  # Preserve the existing healthy path.
    else:
        children = [p for p in probes if "@" in p["ref"]]
        assert len(children) == len(arches)
        assert all(p["ref"].startswith("quay.io/rhoai/cpu@sha256:") for p in children)
        assert "Snapshot retains" in (tmp_path / "image-platform-notes.md").read_text()


@pytest.mark.parametrize("arch", ["s390x", "ppc64le"])
def test_single_architecture_manifests_are_supported(tmp_path, arch):
    result, probes = execute(tmp_path, {"cpu": {"mode": "single", "arches": [arch]}})
    assert result.returncode == 0, result.stderr
    assert len(probes) == 1
    assert arch in (tmp_path / "image-platform-notes.md").read_text()


@pytest.mark.parametrize("required", [True, False])
def test_platform_handling_also_applies_to_authorized_release_images(tmp_path, required):
    result, probes = execute(tmp_path, {"cpu": {"arches": ["ppc64le", "s390x"]}}, required=required)
    assert result.returncode == 0, result.stderr
    assert snapshot(tmp_path)["cpu"]["image_tag"] == ("on-pr-123" if required else "rhoai-3.7-ea.1")
    assert probes[0]["ref"].endswith(":on-pr-123" if required else ":rhoai-3.7-ea.1")


def test_missing_first_image_does_not_starve_later_healthy_images(tmp_path):
    scenarios = {"missing": {"mode": "missing"}, **{"healthy" + str(i): {} for i in range(8)}}
    result, probes = execute(tmp_path, scenarios)
    assert result.returncode != 0
    assert not (tmp_path / "snapshot/snapshot.json").exists()
    rows = (tmp_path / "component-table.tsv").read_text().splitlines()
    assert len(rows) == 8 and all("|ready|" in row for row in rows)
    first_retry = next(i for i, p in enumerate(probes[1:], 1) if "/missing:" in p["ref"])
    assert first_retry >= 9
    report = (tmp_path / "required-pr-image-failures.md").read_text()
    assert "missing" in report and "after inspection" in report
    assert not any("healthy" + str(i) in report for i in range(8))
    assert "before the first probe" not in report


def test_transient_missing_image_can_succeed_on_later_round(tmp_path):
    result, probes = execute(tmp_path, {"late": {"missing_rounds": 1}, "healthy": {}})
    assert result.returncode == 0, result.stderr
    assert list(snapshot(tmp_path)) == ["healthy", "late"]
    assert "/healthy:" in probes[1]["ref"]
    assert "/late:" in probes[2]["ref"]


@pytest.mark.parametrize("failure,expected", [
    ({"mode": "auth"}, "authentication/authorization"),
    ({"bad_digest": True}, "invalid published digest"),
    ({"missing_source": True}, "missing source labels"),
    ({"sha_by_arch": {"amd64": "c" * 40}}, "does not match"),
])
def test_permanent_failure_is_reported_once_and_later_images_are_inspected(tmp_path, failure, expected):
    result, probes = execute(tmp_path, {"bad": failure, "healthy": {}})
    assert result.returncode != 0
    assert len(probes) == 2
    report = (tmp_path / "required-pr-image-failures.md").read_text()
    assert expected in report and "rejected after inspection" in report
    assert "healthy" not in report
    assert "|healthy|" in (tmp_path / "component-table.tsv").read_text()


def test_non_amd64_index_does_not_hide_a_wrong_commit_on_another_platform(tmp_path):
    result, probes = execute(tmp_path, {"cpu": {
        "arches": ["s390x", "ppc64le"], "sha_by_arch": {"ppc64le": "c" * 40}}})
    assert result.returncode != 0
    assert not (tmp_path / "snapshot/snapshot.json").exists()
    assert "linux/ppc64le" in (tmp_path / "required-pr-image-failures.md").read_text()


def test_attestation_descriptors_are_not_treated_as_runnable_platforms(tmp_path):
    result, probes = execute(tmp_path, {"cpu": {"arches": ["s390x"], "attestation": True}})
    assert result.returncode == 0, result.stderr
    assert len([p for p in probes if "@" in p["ref"]]) == 1


def test_invalid_platform_descriptor_blocks_without_retries(tmp_path):
    result, probes = execute(tmp_path, {"cpu": {"arches": ["s390x"], "bad_descriptor": True}})
    assert result.returncode != 0
    assert len(probes) == 2
    assert "invalid image index" in (tmp_path / "required-pr-image-failures.md").read_text()


def test_deadline_before_first_probe_is_reported_as_not_inspected(tmp_path):
    result, probes = execute(tmp_path, {"one": {}, "two": {}}, expire_before_first=True)
    assert result.returncode != 0 and probes == []
    report = (tmp_path / "required-pr-image-failures.md").read_text()
    assert report.count("Not inspected:") == 2
    assert "unavailable after inspection" not in report


def test_compatibility_probe_does_not_turn_permanent_failure_into_retries(tmp_path):
    result, probes = execute(tmp_path, {"cpu": {"mode": "auth"}, "healthy": {}}, shared=True)
    assert result.returncode != 0
    assert len([p for p in probes if "/cpu:" in p["ref"]]) == 1
    assert "|healthy|" in (tmp_path / "component-table.tsv").read_text()


def test_required_pr_wait_budget_does_not_omit_authorized_release_images(tmp_path):
    result, probes = execute(tmp_path, {"release": {}}, required=False, expire_before_first=True)
    assert result.returncode == 0, result.stderr
    assert len(probes) == 1 and probes[0]["ref"].endswith(":rhoai-3.7-ea.1")
    assert snapshot(tmp_path)["release"]["image_tag"] == "rhoai-3.7-ea.1"


@pytest.mark.parametrize("status", ["success", "failure"])
def test_platform_notes_appear_in_both_leader_comment_paths(tmp_path, status):
    result, _ = execute(tmp_path, {"cpu": {"arches": ["s390x", "ppc64le"]}})
    assert result.returncode == 0, result.stderr
    _, script, env = task_script_environment("post-build-complete-comment")
    body = tmp_path / "comment.md"
    script = script.split("# --- Post ", 1)[0].replace("BODY=/tmp/comment.md", "BODY=" + str(body))
    curl = tmp_path / "curl"
    curl.write_text("#!/bin/bash\\nprintf '{}'\\n")
    curl.chmod(0o755)
    env.update(PATH=str(tmp_path) + os.pathsep + os.environ["PATH"], GATE_DATA=str(tmp_path),
               GATE_DATA_BOUND="true", HAS_GROUP_CONFIG="true", STATUS=status)
    posted = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    assert posted.returncode == 0, posted.stderr
    text = body.read_text()
    assert "Image platform inspection notes" in text
    assert "linux/s390x" in text and "linux/ppc64le" in text
    assert "Snapshot retains the published image-index digest" in text
