"""Tests for extract-renovate-dry-run-results.py."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = str(Path(__file__).parent / "extract-renovate-dry-run-results.py")


def run_extract(tmp_path, log_lines, repo="test-repo", config="renovate.json5", branches='["main"]'):
    log_file = tmp_path / "renovate.log"
    log_file.write_text("\n".join(json.dumps(l) for l in log_lines) + "\n")
    output_file = tmp_path / "result.md"

    result = subprocess.run(
        [
            sys.executable, SCRIPT,
            "--repo", repo,
            "--config", config,
            "--branches", branches,
            "--log", str(log_file),
            "--output", str(output_file),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    return output_file.read_text()


class TestNoChanges:
    def test_empty_log(self, tmp_path):
        output = run_extract(tmp_path, [])
        assert "No changes" in output
        assert "<details>" not in output

    def test_zero_updates(self, tmp_path):
        log = [{"msg": "packageFiles with updates", "baseBranch": "main", "config": {
            "tekton": [{"packageFile": "pipelines/build.yaml", "deps": [
                {"depName": "some-task", "updates": []}
            ]}]
        }}]
        output = run_extract(tmp_path, log)
        assert "No changes" in output


class TestDigestUpdate:
    def test_git_ref_digest(self, tmp_path):
        log = [
            {"msg": "packageFiles with updates", "baseBranch": "main", "config": {
                "regex": [{"packageFile": "pipelines/multi-arch.yaml", "deps": [
                    {
                        "depName": "org/repo",
                        "currentValue": "main",
                        "currentDigest": "aaaa1111bbbb2222cccc3333dddd4444eeee5555",
                        "updates": [{"updateType": "digest", "newValue": "main",
                                     "newDigest": "ffff6666aaaa7777bbbb8888cccc9999dddd0000"}],
                    }
                ]}]
            }},
            {"msg": "1 flattened updates found: org/repo", "baseBranch": "main"},
        ]
        output = run_extract(tmp_path, log)
        assert "1 dependency update(s)" in output
        assert "`org/repo`" in output
        assert "`pipelines/multi-arch.yaml`" in output
        assert "digest" in output
        assert "aaaa1111bbbb" in output
        assert "ffff6666aaaa" in output


class TestVersionUpdate:
    def test_patch_update(self, tmp_path):
        log = [
            {"msg": "packageFiles with updates", "baseBranch": "main", "config": {
                "tekton": [{"packageFile": "pipelines/build.yaml", "deps": [
                    {
                        "depName": "quay.io/org/task-foo",
                        "currentValue": "0.4.0",
                        "currentDigest": "sha256:aaa111",
                        "updates": [{"updateType": "patch", "newValue": "0.4.1",
                                     "newDigest": "sha256:bbb222"}],
                    }
                ]}]
            }},
            {"msg": "1 flattened updates found: quay.io/org/task-foo", "baseBranch": "main"},
        ]
        output = run_extract(tmp_path, log)
        assert "patch" in output
        assert "0.4.0" in output
        assert "0.4.1" in output


class TestMultiBranch:
    def test_separate_tables_per_branch(self, tmp_path):
        base = {
            "tekton": [{"packageFile": "pipelines/build.yaml", "deps": [
                {"depName": "task-a", "currentValue": "1.0", "updates": [
                    {"updateType": "patch", "newValue": "1.1"}
                ]}
            ]}]
        }
        log = [
            {"msg": "packageFiles with updates", "baseBranch": "main", "config": base},
            {"msg": "1 flattened updates found: task-a", "baseBranch": "main"},
            {"msg": "packageFiles with updates", "baseBranch": "rhoai-3.5", "config": base},
            {"msg": "1 flattened updates found: task-a", "baseBranch": "rhoai-3.5"},
        ]
        output = run_extract(tmp_path, log, branches='["main","rhoai-3.5"]')
        assert "**main**" in output
        assert "**rhoai-3.5**" in output


class TestFallback:
    def test_no_package_files_entry(self, tmp_path):
        """When only flattened-updates lines exist, fall back to dep-name-only table."""
        log = [
            {"msg": "1 flattened updates found: some-dep", "baseBranch": "main"},
        ]
        output = run_extract(tmp_path, log)
        assert "1 dependency update(s)" in output
        assert "| Dependency | Branches |" in output
        assert "`some-dep`" in output


class TestSkippedDeps:
    def test_skip_reason_excluded(self, tmp_path):
        log = [
            {"msg": "packageFiles with updates", "baseBranch": "main", "config": {
                "tekton": [{"packageFile": "pipelines/build.yaml", "deps": [
                    {"depName": "skipped-task", "skipReason": "invalid-value",
                     "updates": []},
                    {"depName": "good-task", "currentValue": "1.0",
                     "updates": [{"updateType": "patch", "newValue": "1.1"}]},
                ]}]
            }},
            {"msg": "1 flattened updates found: good-task", "baseBranch": "main"},
        ]
        output = run_extract(tmp_path, log)
        assert "skipped-task" not in output
        assert "good-task" in output
