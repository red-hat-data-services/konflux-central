"""Exercise the actual shared generator with synthetic release Git reads."""
import copy
import importlib.util
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    tooling = os.environ.get("ENFORCEMENT_TEST_TOOLING")
    if not tooling:
        pytest.skip("set ENFORCEMENT_TEST_TOOLING to generator-capable shared tooling")
    monkeypatch.syspath_prepend(tooling)
    from ruamel.yaml import YAML
    yaml = YAML()
    spec = importlib.util.spec_from_file_location("generate_main", ROOT / "script/generate_main_pr_children.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parent = yaml.load((ROOT / "pipelineruns/notebooks/.tekton/odh-pipeline-runtime-pytorch-llmcompressor-cuda-py312-pull-request.yaml").read_text())
    component = "odh-wb-jupyter-pytorch-llmcompressor-cuda-py312"
    parent["metadata"]["labels"]["appstudio.openshift.io/component"] = component + "-v3-7-ea-1"
    parent["metadata"]["annotations"]["pipelinesascode.tekton.dev/on-cel-expression"] = 'event == "push" && target_branch == "rhoai-3.7-ea.1"'
    parent["metadata"]["name"] = component + "-v3-7-ea-1-on-push"
    parents = {f"pipelineruns/notebooks/.tekton/{component}-v3-7-ea-1-push.yaml": parent}

    def run(command, **kwargs):
        from io import StringIO
        if command[1] == "ls-tree":
            return subprocess.CompletedProcess(command, 0, "\n".join(parents), "")
        assert command[1] == "show"
        output = StringIO()
        yaml.dump(parents[command[2].split(":", 1)[1]], output)
        return subprocess.CompletedProcess(command, 0, output.getvalue(), "")

    monkeypatch.setattr(module.subprocess, "run", run)
    def generate():
        return module.generate(tmp_path, Path(tooling), ROOT / "tekton-templates/main-pr")
    return tmp_path, yaml, parent, parents, generate


def test_missing_child_main_semantics_and_idempotence(scenario):
    root, yaml, parent, parents, generate = scenario
    before_parents = copy.deepcopy(parents)
    [path] = generate()
    assert parents == before_parents
    child = yaml.load(path.read_text())
    annotations = child["metadata"]["annotations"]
    assert "pull_request" in annotations["pipelinesascode.tekton.dev/on-cel-expression"]
    assert annotations["pipelinesascode.tekton.dev/on-comment"] == "^/build-konflux (odh-wb-jupyter-pytorch-llmcompressor-cuda-py312|all)$"
    refs = {p["name"]: p["value"] for p in child["spec"]["pipelineRef"]["params"]}
    assert '"main"' in refs["revision"] and "pac.target_branch" in refs["revision"]
    assert len(child["metadata"]["name"].replace("{{pull_request_number}}", "1234567890")) <= 63
    before = path.read_bytes()
    assert generate() == []
    assert path.read_bytes() == before
    assert not any(root.glob("pipelineruns/*/.tekton/*push.yaml"))


def test_existing_alias_preserved(scenario):
    root, yaml, parent, parents, generate = scenario
    [created] = generate()
    data = yaml.load(created.read_text())
    alias = "odh-workbench-jupyter-pytorch-llmcompressor-cuda-py312"
    data["metadata"]["labels"]["appstudio.openshift.io/component"] = alias
    data["metadata"]["name"] = alias + "-on-pull-request-{{pull_request_number}}"
    path = created.with_name(alias + "-pull-request.yaml")
    created.rename(path)
    with path.open("w") as stream:
        yaml.dump(data, stream)
    before = path.read_bytes()
    assert generate() == []
    assert path.read_bytes() == before


def test_duplicate_parents_fail_before_writes(scenario):
    root, yaml, parent, parents, generate = scenario
    parents["pipelineruns/notebooks/.tekton/duplicate-push.yaml"] = copy.deepcopy(parent)
    with pytest.raises(ValueError, match="ambiguous parent"):
        generate()
    assert not list(root.rglob("*.yaml"))


def test_filename_collision_fails_without_overwrite(scenario):
    root, yaml, parent, parents, generate = scenario
    path = root / "pipelineruns/notebooks/.tekton/odh-wb-jupyter-pytorch-llmcompressor-cuda-py312-pull-request.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("kind: ConfigMap\n")
    with pytest.raises(ValueError, match="collision"):
        generate()
    assert path.read_text() == "kind: ConfigMap\n"


def test_orphan_and_other_component_directory_are_preserved(scenario):
    root, yaml, parent, parents, generate = scenario
    path = root / "pipelineruns/other/.tekton/existing-pull-request.yaml"
    path.parent.mkdir(parents=True)
    orphan = copy.deepcopy(parent)
    orphan["metadata"]["name"] = "other-on-pull-request-{{pull_request_number}}"
    orphan["metadata"]["annotations"]["pipelinesascode.tekton.dev/on-cel-expression"] = 'event == "pull_request"'
    with path.open("w") as stream:
        yaml.dump(orphan, stream)
    before = path.read_bytes()
    assert len(generate()) == 1
    assert path.read_bytes() == before


def test_symlink_destination_rejected(scenario, tmp_path):
    root, yaml, parent, parents, generate = scenario
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "pipelineruns").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe generated target"):
        generate()
    assert not list(outside.iterdir())
