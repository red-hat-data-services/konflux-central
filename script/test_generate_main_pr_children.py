"""Exercise the actual shared generator with synthetic release Git reads."""
import copy
import importlib.util
import os
from pathlib import Path
import shutil
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
    def generate(policy=ROOT / "tekton-templates/main-pr"):
        return module.generate(tmp_path, Path(tooling), policy)
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


@pytest.mark.parametrize("repository", ["RHOAI-Build-Config", "rhods-operator"])
def test_excluded_repo_does_not_create_children_or_modify_existing_files(scenario, repository, capsys):
    root, yaml, parent, parents, generate = scenario
    parents[f"pipelineruns/{repository}/.tekton/example-v3-7-ea-1-push.yaml"] = copy.deepcopy(parent)
    directory = root / "pipelineruns" / repository / ".tekton"
    directory.mkdir(parents=True)
    existing = copy.deepcopy(parent)
    existing["metadata"]["name"] = "existing-on-pull-request-{{pull_request_number}}"
    existing["metadata"]["annotations"]["pipelinesascode.tekton.dev/on-cel-expression"] = 'event == "pull_request"'
    path = directory / "existing-pull-request.yaml"
    with path.open("w") as stream:
        yaml.dump(existing, stream)
    before = path.read_bytes()
    [created] = generate()
    assert created.relative_to(root).parts[1] == "notebooks"
    assert list(directory.iterdir()) == [path]
    assert path.read_bytes() == before
    assert f"PR creation excluded for {repository}:" in capsys.readouterr().out


def test_all_release_parents_excluded_is_noop(scenario):
    root, yaml, parent, parents, generate = scenario
    parents.clear()
    parents["pipelineruns/rhods-operator/.tekton/operator-v3-7-ea-1-push.yaml"] = parent
    assert generate() == []
    assert not list(root.rglob("*.yaml"))


def test_operator_and_e2e_get_release_pr_children_without_touching_main_push(scenario):
    root, yaml, parent, parents, generate = scenario
    parents.clear()
    for component in ("odh-db-operator", "odh-db-operator-e2e"):
        release = copy.deepcopy(parent)
        release["metadata"]["name"] = component + "-v3-7-ea-1-on-push"
        release["metadata"]["labels"]["appstudio.openshift.io/component"] = component + "-v3-7-ea-1"
        release["spec"]["taskRunTemplate"]["serviceAccountName"] = "build-pipeline-" + component + "-v3-7-ea-1"
        if component.endswith("-e2e"):
            release["spec"]["params"].append({"name": "build-args", "value": [
                "ODH_E2E_OPERATOR_IMAGE=quay.io/rhoai/odh-db-operator-rhel9:rhoai-3.7-ea.1"]})
        parents[f"pipelineruns/rhai-db-operator/.tekton/{component}-v3-7-ea-1-push.yaml"] = release
    main_push = root / "pipelineruns/rhai-db-operator/.tekton/odh-db-operator-e2e-push.yaml"
    main_push.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "pipelineruns/rhai-db-operator/.tekton/odh-db-operator-e2e-push.yaml", main_push)
    before = main_push.read_bytes()
    assert {path.name for path in generate()} == {
        "odh-db-operator-pull-request.yaml", "odh-db-operator-e2e-pull-request.yaml"}
    assert main_push.read_bytes() == before
    e2e = yaml.load(main_push.with_name("odh-db-operator-e2e-pull-request.yaml").read_text())
    assert e2e["metadata"]["labels"]["appstudio.openshift.io/component"] == "odh-db-operator-e2e-v3-7-ea-1"
    assert e2e["spec"]["taskRunTemplate"]["serviceAccountName"] == "build-pipeline-odh-db-operator-e2e-v3-7-ea-1"
    params = {p["name"]: p["value"] for p in e2e["spec"]["params"]}
    assert params["build-args"] == ["ODH_E2E_OPERATOR_IMAGE=quay.io/rhoai/odh-db-operator-rhel9:rhoai-3.7-ea.1"]
    assert generate() == []
    assert main_push.read_bytes() == before


@pytest.mark.parametrize("content", [
    "excluded_repositories: []\n", "excluded_repositories: {rhods-operator: ''}\n",
    "excluded_repositories: {'rhods-*': reason}\n", "excluded_repositories: {'../repo': reason}\n",
    "excluded_repositories: {rhods-operator: reason}\nextra: true\n", "{}\n",
])
def test_invalid_creation_policy_fails_before_writes(scenario, tmp_path, content):
    root, yaml, parent, parents, generate = scenario
    policy = tmp_path / "policy/main-pr"
    shutil.copytree(ROOT / "tekton-templates/main-pr", policy)
    shutil.copytree(ROOT / "tekton-templates/release-pr", policy.parent / "release-pr")
    (policy / "generation.yaml").write_text(content)
    with pytest.raises(ValueError):
        generate(policy)
    assert not (root / "pipelineruns").exists()


def test_missing_creation_policy_fails_closed(scenario, tmp_path):
    root, yaml, parent, parents, generate = scenario
    policy = tmp_path / "policy/main-pr"
    shutil.copytree(ROOT / "tekton-templates/main-pr", policy)
    shutil.copytree(ROOT / "tekton-templates/release-pr", policy.parent / "release-pr")
    (policy / "generation.yaml").unlink()
    with pytest.raises(FileNotFoundError):
        generate(policy)
    assert not (root / "pipelineruns").exists()

@pytest.mark.parametrize("source,target,event,changed,expected", [
    ("main", "stable", "pull_request", ["params"], True),
    ("main", "stable", "pull_request", ["other"], False),
    ("feature", "stable", "pull_request", ["params"], False),
    ("main", "other", "pull_request", ["params"], False),
    ("feature", "stable", "pull_request", ["params", "src/**"], True),
    ("feature", "stable", "pull_request", ["src/**"], True),
    ("main", "stable", "push", ["params", "src/**"], False),
])
def test_notebooks_policy_creation_and_revalidation(scenario, source, target, event, changed, expected):
    import celpy
    import importlib.machinery

    root, yaml, parent, parents, generate = scenario
    key = "pipelinesascode.tekton.dev/on-cel-expression"
    params = "manifests/rhoai/base/params-latest.env"
    parent["metadata"]["annotations"][key] = (
        'event == "push" && target_branch == "rhoai-3.7-ea.1"'
        f' && !("{params}".pathChanged())'
        ' && ("src/**".pathChanged() || ".tekton/push.yaml".pathChanged())'
    )
    [path] = generate()
    child = yaml.load(path.read_text())
    expression = str(child["metadata"]["annotations"][key])
    changed = [params if p == "params" else p for p in changed]
    env = celpy.Environment()
    program = env.program(env.compile(expression), functions={
        "pathChanged": lambda path: celpy.celtypes.BoolType(str(path) in changed),
    })
    assert bool(program.evaluate(celpy.json_to_cel({
        "event": event, "source_branch": source, "target_branch": target,
        "files": {"all": changed},
    }))) == expected

    tooling = Path(os.environ["ENFORCEMENT_TEST_TOOLING"])
    loader = importlib.machinery.SourceFileLoader("notebooks_validator", str(tooling / "scripts/validate-tekton"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    validator = importlib.util.module_from_spec(spec)
    loader.exec_module(validator)
    config = validator.load_config(str(ROOT / "tekton-templates/main-pr"))
    component = parent["metadata"]["labels"]["appstudio.openshift.io/component"]
    result = validator.validate_tekton_dir(
        root, "main", None, config=config,
        external_parent_indexes={"push": {component: parent}}, layout="nested",
    )
    assert result and all(not entry["errors"] for entry in result.values())

    # Corrupt the existing child, then verify fix selects the same overlay.
    child["metadata"]["annotations"][key] = 'event == "push"'
    with path.open("w") as stream:
        yaml.dump(child, stream)
    validator.fix_tekton_dir(
        root, "main", None, config=config, dry_run=False,
        external_parent_indexes={"push": {component: parent}}, layout="nested",
    )
    assert str(yaml.load(path.read_text())["metadata"]["annotations"][key]) == expression
    result = validator.validate_tekton_dir(
        root, "main", None, config=config,
        external_parent_indexes={"push": {component: parent}}, layout="nested",
    )
    assert result and all(not entry["errors"] for entry in result.values())
    assert generate() == []


def test_non_notebooks_has_no_sync_bypass(scenario):
    root, yaml, parent, parents, generate = scenario
    key = next(iter(parents))
    parents[key.replace("/notebooks/", "/other/")] = parents.pop(key)
    [path] = generate()
    expression = str(yaml.load(path.read_text())["metadata"]["annotations"]["pipelinesascode.tekton.dev/on-cel-expression"])
    assert path.parent.parent.name == "other"
    assert "source_branch" not in expression
