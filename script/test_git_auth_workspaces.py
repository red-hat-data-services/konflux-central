"""Check PipelineRun YAML and credential bindings without external services.

Run with:
    uv run --with pyyaml --with pytest pytest script/test_git_auth_workspaces.py
"""

import re
from pathlib import Path

import pytest
import yaml


PIPELINERUN_DIR = Path(__file__).resolve().parents[1] / "pipelineruns"
PIPELINERUN_FILES = sorted(
    path
    for extension in ("yaml", "yml")
    for path in PIPELINERUN_DIR.glob(f"**/.tekton/*.{extension}")
)
assert PIPELINERUN_FILES, "No Tekton PipelineRun definitions found"


@pytest.mark.parametrize(
    "path",
    PIPELINERUN_FILES,
    ids=lambda path: str(path.relative_to(PIPELINERUN_DIR)),
)
def test_pipelinerun_has_no_duplicate_yaml_keys(path):
    # Inspect nodes before safe_load silently overwrites duplicate keys.
    with path.open() as stream:
        pending = list(yaml.compose_all(stream))

    visited = set()
    while pending:
        node = pending.pop()
        if node is None or id(node) in visited:
            continue
        visited.add(id(node))
        if isinstance(node, yaml.MappingNode):
            keys = {}
            for key, value in node.value:
                assert isinstance(key, yaml.ScalarNode), "Expected scalar YAML key"
                identity = (key.tag, key.value)
                line = key.start_mark.line + 1
                assert identity not in keys, (
                    f"{path}:{line}: duplicate YAML key {key.value!r} "
                    f"(first defined at line {keys[identity]})"
                )
                keys[identity] = line
                pending.append(value)
        elif isinstance(node, yaml.SequenceNode):
            pending.extend(node.value)


@pytest.mark.parametrize(
    "path",
    PIPELINERUN_FILES,
    ids=lambda path: str(path.relative_to(PIPELINERUN_DIR)),
)
def test_git_auth_workspace_uses_pac_secret(path):
    with path.open() as stream:
        pipelinerun = yaml.safe_load(stream)

    assert pipelinerun["kind"] == "PipelineRun"
    bindings = [
        workspace
        for workspace in pipelinerun["spec"].get("workspaces", [])
        if workspace.get("name") == "git-auth"
    ]
    assert len(bindings) == 1, "Expected exactly one git-auth workspace binding"
    secret_name = bindings[0].get("secret", {}).get("secretName", "")
    assert re.fullmatch(r"\{\{\s*git_auth_secret\s*\}\}", secret_name), (
        "git-auth must use the Pipelines as Code git_auth_secret template"
    )
