"""Tests for script/check_fips_check_blocking.py."""

from pathlib import Path

import pytest
import yaml

from check_fips_check_blocking import (
    Violation,
    collect_violations,
    filter_to_changed_scannable,
    filter_unapproved,
    load_allowed_exception_files,
    should_full_scan,
)


def _write_pipelinerun(base: Path, rel: str, fips_value: str | None) -> Path:
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    params = []
    if fips_value is not None:
        params.append({"name": "fips-check-blocking", "value": fips_value})
    doc = {
        "kind": "PipelineRun",
        "metadata": {"name": "example-on-push"},
        "spec": {"params": params},
    }
    path.write_text(yaml.safe_dump(doc))
    return path


def _write_pipeline(base: Path, name: str, default: str) -> Path:
    path = base / "pipelines" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "apiVersion": "tekton.dev/v1",
        "kind": "Pipeline",
        "spec": {
            "params": [
                {
                    "name": "fips-check-blocking",
                    "type": "string",
                    "default": default,
                }
            ]
        },
    }
    path.write_text(yaml.safe_dump(doc))
    return path


def test_collect_pipelinerun_false(tmp_path: Path):
    _write_pipelinerun(
        tmp_path,
        "pipelineruns/foo/.tekton/bar-on-push.yaml",
        "false",
    )
    violations = collect_violations(
        tmp_path, tmp_path / "pipelineruns", tmp_path / "pipelines"
    )
    assert len(violations) == 1
    assert violations[0].file == "pipelineruns/foo/.tekton/bar-on-push.yaml"


def test_collect_pipeline_default_false(tmp_path: Path):
    _write_pipeline(tmp_path, "multi-arch-container-build.yaml", "false")
    violations = collect_violations(
        tmp_path, tmp_path / "pipelineruns", tmp_path / "pipelines"
    )
    assert len(violations) == 1
    assert violations[0].file == "pipelines/multi-arch-container-build.yaml"


def test_exceptions_filter(tmp_path: Path):
    exc = tmp_path / "fips-check-exceptions.yaml"
    exc.write_text(
        yaml.safe_dump(
            [
                {
                    "branch": "rhoai-3.6",
                    "exceptions": [
                        {
                            "file": "pipelineruns/foo/.tekton/bar-on-push.yaml",
                            "references": ["RHAI-1"],
                        }
                    ],
                }
            ]
        )
    )
    allowed = load_allowed_exception_files(exc, "rhoai-3.6")
    violations = [
        Violation(
            file="pipelineruns/foo/.tekton/bar-on-push.yaml",
            reason="x",
        )
    ]
    assert filter_unapproved(violations, allowed) == []


def test_should_full_scan_on_exceptions_change():
    assert should_full_scan(None) is True
    assert should_full_scan({"docs/readme.md"}) is False
    assert should_full_scan({"fips-check-exceptions.yaml"}) is True
    assert should_full_scan({"early-gate/docs/README.md"}) is False


def test_filter_to_changed_scannable():
    violations = [
        Violation(file="pipelineruns/foo/.tekton/a.yaml", reason="x"),
        Violation(file="pipelines/multi-arch-container-build.yaml", reason="y"),
        Violation(file="pipelineruns/bar/.tekton/b.yaml", reason="z"),
    ]
    changed = {
        "pipelineruns/foo/.tekton/a.yaml",
        "early-gate/tasks/foo.yaml",
        "README.md",
    }
    filtered = filter_to_changed_scannable(violations, changed)
    assert [v.file for v in filtered] == ["pipelineruns/foo/.tekton/a.yaml"]
