#!/usr/bin/env python3
"""Tests for apply-base-branches.py and base-branches.json5 consistency."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import json5
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "script" / "apply-base-branches.py"
SOT = REPO_ROOT / "renovate" / "base-branches.json5"


def _load_apply_module():
    spec = importlib.util.spec_from_file_location("apply_base_branches", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def apply_mod():
    return _load_apply_module()


def test_sot_loads_and_has_required_keys():
    data = json5.loads(SOT.read_text(encoding="utf-8"))
    assert "postFreezeReleaseBranches" in data
    assert "pipelinesEaBranches" in data
    assert "presets" in data
    for name in (
        "default-renovate.json5",
        "custom-renovate.json5",
        "ogx-renovate.json5",
        "pipelines-renovate.json5",
    ):
        assert name in data["presets"]
        assert isinstance(data["presets"][name], list)
        assert data["presets"][name]


def test_ea_branches_only_on_pipelines():
    data = json5.loads(SOT.read_text(encoding="utf-8"))
    ea = set(data["pipelinesEaBranches"])
    for name, branches in data["presets"].items():
        if name == "pipelines-renovate.json5":
            assert ea.issubset(set(branches))
        else:
            assert ea.isdisjoint(set(branches)), f"{name} must not list EA branches"


def test_custom_excludes_main():
    data = json5.loads(SOT.read_text(encoding="utf-8"))
    assert "main" not in data["presets"]["custom-renovate.json5"]


def test_default_and_ogx_include_main():
    data = json5.loads(SOT.read_text(encoding="utf-8"))
    assert data["presets"]["default-renovate.json5"][0] == "main"
    assert data["presets"]["ogx-renovate.json5"][0] == "main"


def test_apply_check_passes_on_repo(apply_mod, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["apply-base-branches.py", "--check"])
    assert apply_mod.main() == 0


def test_replace_is_idempotent(apply_mod, tmp_path):
    sample = '{\n  "baseBranches": ["main", "rhoai-3.6"],\n  "automerge": true\n}\n'
    path = tmp_path / "sample.json5"
    path.write_text(sample, encoding="utf-8")
    assert apply_mod.apply_to_file(path, ["main", "rhoai-3.6"], dry_run=False) is False
    assert apply_mod.apply_to_file(path, ["main", "rhoai-3.6", "rhoai-3.7"], dry_run=False) is True
    text = path.read_text(encoding="utf-8")
    assert "rhoai-3.7" in text
    assert '"automerge": true' in text
