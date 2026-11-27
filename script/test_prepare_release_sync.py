"""Offline tests for staging, validation gating, and component sync preparation."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml
from ruamel.yaml.error import YAMLError

spec = importlib.util.spec_from_file_location("prepare_release_sync", Path(__file__).with_name("prepare_release_sync.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def preparation(tmp_path, monkeypatch):
    """Model the external validator while exercising real staging and file copying."""
    source, target = tmp_path / "source", tmp_path / "target"
    origin = source / "pipelineruns/widget/.tekton"
    origin.mkdir(parents=True)
    (target / ".tekton").mkdir(parents=True)
    parent = {"kind": "PipelineRun", "metadata": {"name": "widget-on-push"}, "spec": {"params": []}}
    (origin / "widget-push.yaml").write_text(yaml.safe_dump(parent))
    calls, behavior = [], {}
    tekton, config = ModuleType("lib.tekton"), ModuleType("lib.tekton_config")
    tekton.classify_pipeline = lambda name, data, config: "pull-request" if "pull-request" in name else "push"
    config.load_config = lambda path: object()
    monkeypatch.setitem(sys.modules, "lib.tekton", tekton)
    monkeypatch.setitem(sys.modules, "lib.tekton_config", config)

    def run(command, **kwargs):
        """Create deterministic validator output without Git or network side effects."""
        calls.append(command)
        if command[0] == "git":
            return subprocess.CompletedProcess(command, 0)
        stage = Path(command[command.index("--path") + 1])
        directory = stage / "pipelineruns/widget/.tekton"
        existing = list(directory.glob("*pull-request*.yaml"))
        child = existing[0] if existing else directory / "widget-pull-request.yaml"
        fresh = not child.exists()
        if "--fix" in command and not behavior.get("failure"):
            child.write_text(yaml.safe_dump({"kind": "PipelineRun", "metadata": {
                "name": behavior.get("name", "widget-on-pull-request-{{pull_request_number}}")}}))
        if behavior.get("mutate_parent"):
            (directory / "widget-push.yaml").write_text("changed")
        result = {"files": {str(child): {"errors": [], "warnings": ["orphan child: retained"] if behavior.get("orphan") else []}}}
        if "--fix" in command:
            result["fixed"] = [str(child)] if fresh or behavior.get("non_idempotent") else []
        return subprocess.CompletedProcess(command, int(bool(behavior.get("failure"))), json.dumps(result), "")

    monkeypatch.setattr(module.subprocess, "run", run)
    args = dict(source=source, target=target, tooling=tmp_path / "tooling", policy=tmp_path / "policy",
                branch="rhoai-3.6", repository="widget", report=tmp_path / "reports")
    return args, origin, calls, behavior


def test_prepare_copies_push_and_created_pr_after_validation(preparation):
    """Run fix, independent validation, and idempotence before copying both files."""
    args, origin, calls, _ = preparation
    before = (origin / "widget-push.yaml").read_bytes()
    result = module.prepare(**args)
    assert result["status"] == "validated" and result["parents"] == 1
    assert (args["target"] / ".tekton/widget-push.yaml").read_bytes() == before
    assert (args["target"] / ".tekton/widget-pull-request.yaml").exists()
    commands = [command for command in calls if command[0] != "git"]
    assert ["--fix" in command for command in commands] == [True, False, True]
    assert not any("commit" in command or "push" in command for command in calls)


def test_existing_component_pr_filename_and_unmanaged_files_survive(preparation):
    """Seed from the live PR filename while keeping unrelated target files intact."""
    args, _, _, _ = preparation
    directory = args["target"] / ".tekton"
    (directory / "legacy-pull-request.yaml").write_text("kind: PipelineRun\n")
    (directory / "README.md").write_text("keep")
    result = module.prepare(**args)
    assert result["children"] == ["legacy-pull-request.yaml"]
    assert (directory / "README.md").read_text() == "keep"
    assert not (directory / "widget-pull-request.yaml").exists()


@pytest.mark.parametrize("behavior, message", [
    ({"failure": True}, "fix failed"),
    ({"orphan": True}, "orphan PR"),
    ({"non_idempotent": True}, "not idempotent"),
    ({"mutate_parent": True}, "modified non-PR"),
    ({"name": "x" * 64}, "unsafe rendered"),
    ({"name": "invalid_name"}, "unsafe rendered"),
])
def test_failed_generation_never_changes_component(preparation, behavior, message):
    """Stop publication before touching the component when any safety check fails."""
    args, _, _, controls = preparation
    controls.update(behavior)
    directory = args["target"] / ".tekton"
    (directory / "README.md").write_text("unchanged")
    before = {file.name: file.read_bytes() for file in directory.iterdir()}
    with pytest.raises(ValueError, match=message):
        module.prepare(**args)
    assert {file.name: file.read_bytes() for file in directory.iterdir()} == before


@pytest.mark.parametrize("field, value", [("branch", "main"), ("repository", ".."), ("repository", "x/y")])
def test_invalid_scope_is_rejected(preparation, field, value):
    """Reject unsupported branches and paths before reading pipeline contents."""
    args, _, _, _ = preparation
    args[field] = value
    with pytest.raises(ValueError):
        module.prepare(**args)


def test_no_push_repository_is_reported_not_counted_as_generated(preparation):
    """A genuinely empty KC pipeline directory has distinct coverage status."""
    args, origin, calls, _ = preparation
    (origin / "widget-push.yaml").unlink()
    result = module.prepare(**args)
    assert result["status"] == "no-push-pipelines" and result["parents"] == 0
    assert not calls


def test_missing_kc_repository_is_an_error(preparation):
    """A typo or missing source cannot be mistaken for a legitimate empty repo."""
    args, _, _, _ = preparation
    args["repository"] = "missing"
    with pytest.raises(ValueError, match="missing KC component"):
        module.prepare(**args)


def test_live_pr_without_push_parent_blocks_sync(preparation):
    """An empty KC input must not silently bless an unmanaged live PR."""
    args, origin, _, _ = preparation
    (origin / "widget-push.yaml").unlink()
    path = args["target"] / ".tekton/live-pull-request.yaml"
    path.write_text("kind: PipelineRun\n")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="without any KC push parent"):
        module.prepare(**args)
    assert path.read_bytes() == before


@pytest.fixture
def separately_owned(preparation):
    """Declare one exact upstream-owned PR and retain its original bytes."""
    args, _, _, _ = preparation
    args["policy"].mkdir()
    rule = {"reason": "Independent upstream CI build", "application": "opendatahub-builds",
            "component": "independent-ci", "pipeline_url": "https://example.com/upstream.git",
            "image_repository": "quay.io/opendatahub/independent", "dockerfile": "Dockerfile.ci",
            "path_context": "ci/build"}
    policy = {"branches": ["rhoai-3.6"], "preserve_prs": {"widget": {"independent-pull-request.yaml": rule}}}
    (args["policy"] / "ownership.yaml").write_text(yaml.safe_dump(policy))
    data = {"kind": "PipelineRun", "metadata": {"name": "independent-on-pull-request",
            "labels": {"appstudio.openshift.io/application": rule["application"],
                       "appstudio.openshift.io/component": rule["component"]}},
            "spec": {"params": [{"name": "output-image", "value": rule["image_repository"] + ":pr"},
                                {"name": "dockerfile", "value": rule["dockerfile"]},
                                {"name": "path-context", "value": rule["path_context"]}],
                     "pipelineRef": {"params": [{"name": "url", "value": rule["pipeline_url"]}]}}}
    path = args["target"] / ".tekton/independent-pull-request.yaml"
    path.write_text(yaml.safe_dump(data))
    return preparation, policy, path


def test_separately_owned_pr_is_preserved_and_reported(separately_owned):
    """Exclude only the reviewed identity, preserving exact bytes and managed coverage."""
    (args, _, _, _), _, path = separately_owned
    before = path.read_bytes()
    result = module.prepare(**args)
    assert path.read_bytes() == before
    assert result["children"] == ["widget-pull-request.yaml"]
    assert result["preserved_prs"][path.name]["reason"] == "Independent upstream CI build"
    assert not (Path(result["stage"]) / "pipelineruns/widget/.tekton" / path.name).exists()
    assert (args["report"] / "ownership.json").exists()


@pytest.mark.parametrize("field", ["application", "component", "pipeline_url", "image_repository", "dockerfile", "path_context"])
def test_changed_preserved_identity_requires_review(separately_owned, field):
    """A stale filename exception cannot hide an identity change."""
    (args, _, _, _), policy, path = separately_owned
    policy["preserve_prs"]["widget"][path.name][field] = "different"
    (args["policy"] / "ownership.yaml").write_text(yaml.safe_dump(policy))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="identity changed"):
        module.prepare(**args)
    assert path.read_bytes() == before


def test_generated_filename_cannot_overwrite_separately_owned_pr(separately_owned):
    """Detect a collision before copying any generated or push files."""
    (args, _, _, _), policy, path = separately_owned
    policy["preserve_prs"]["widget"]["widget-pull-request.yaml"] = policy["preserve_prs"]["widget"].pop(path.name)
    (args["policy"] / "ownership.yaml").write_text(yaml.safe_dump(policy))
    renamed = path.with_name("widget-pull-request.yaml")
    path.rename(renamed)
    before = renamed.read_bytes()
    with pytest.raises(ValueError, match="collides with separately managed"):
        module.prepare(**args)
    assert renamed.read_bytes() == before
    assert not (args["target"] / ".tekton/widget-push.yaml").exists()


@pytest.mark.parametrize("mutation", ["unknown-key", "missing-identity", "unsafe-path", "bad-branches"])
def test_malformed_ownership_policy_fails_closed(separately_owned, mutation):
    """Reject misspelled or unsafe exceptions rather than silently exempting files."""
    (args, _, _, _), policy, path = separately_owned
    if mutation == "unknown-key":
        policy["preserve_pr"] = {}
    elif mutation == "missing-identity":
        del policy["preserve_prs"]["widget"][path.name]["component"]
    elif mutation == "unsafe-path":
        policy["preserve_prs"]["widget"]["../escape.yaml"] = policy["preserve_prs"]["widget"].pop(path.name)
    else:
        policy["branches"] = "rhoai-3.6"
    (args["policy"] / "ownership.yaml").write_text(yaml.safe_dump(policy))
    with pytest.raises(ValueError, match="ownership|preserved PR identity"):
        module.prepare(**args)


def test_release_specific_ownership_identity_is_exact(separately_owned):
    """Allow a reviewed per-release build change without a wildcard identity guard."""
    (args, _, _, _), policy, path = separately_owned
    policy["preserve_prs"]["widget"][path.name]["by_branch"] = {"rhoai-3.6": {"dockerfile": "Dockerfile.release-ci"}}
    (args["policy"] / "ownership.yaml").write_text(yaml.safe_dump(policy))
    with pytest.raises(ValueError, match="identity changed"):
        module.prepare(**args)
    data = yaml.safe_load(path.read_text())
    data["spec"]["params"][1]["value"] = "Dockerfile.release-ci"
    path.write_text(yaml.safe_dump(data))
    before = path.read_bytes()
    result = module.prepare(**args)
    assert result["preserved_prs"][path.name]["identity"]["dockerfile"] == "Dockerfile.release-ci"
    assert path.read_bytes() == before


@pytest.mark.parametrize("override", [
    {"rhoai-3.8": {"dockerfile": "other"}},
    {"rhoai-3.6": {"unknown": "other"}},
    {"rhoai-3.6": {"dockerfile": None}},
    {"rhoai-3.6": {}},
])
def test_invalid_ownership_branch_override_is_rejected(separately_owned, override):
    """Reject misspelled identities and overrides outside the audited branch scope."""
    (args, _, _, _), policy, path = separately_owned
    policy["preserve_prs"]["widget"][path.name]["by_branch"] = override
    (args["policy"] / "ownership.yaml").write_text(yaml.safe_dump(policy))
    with pytest.raises(ValueError, match="branch override"):
        module.prepare(**args)


def test_ownership_exceptions_do_not_apply_to_other_releases(separately_owned):
    """An audited release exception cannot silently expand to future releases."""
    (args, _, _, _), _, _ = separately_owned
    from ruamel.yaml import YAML
    assert module.preserved_pr_policy(args["policy"], YAML(typ="safe"), "rhoai-3.8", "widget") == {}


@pytest.mark.parametrize("location", ["source", "target"])
def test_multi_document_non_pipelinerun_resources_are_byte_preserved(preparation, location):
    """Validate all resource documents without treating Task streams as PRs."""
    args, origin, _, _ = preparation
    directory = origin if location == "source" else args["target"] / ".tekton"
    content = "# original formatting\n" + "---\n".join(
        f"apiVersion: tekton.dev/v1\nkind: Task\nmetadata:\n  name: task-{i}\n" for i in range(7))
    (directory / "tasks.yaml").write_text(content)
    assert module.prepare(**args)["status"] == "validated"
    assert (args["target"] / ".tekton/tasks.yaml").read_text() == content


@pytest.mark.parametrize("location", ["source", "target"])
@pytest.mark.parametrize("tail", [
    "kind: PipelineRun\nmetadata:\n  name: hidden-on-pull-request\n",
    "kind: Task\nmetadata:\n  name: one\n  name: duplicate\n",
    "kind: [invalid\n",
])
def test_unsafe_multi_document_streams_fail_before_target_changes(preparation, location, tail):
    """Do not hide PipelineRuns, duplicate keys, or invalid trailing YAML."""
    args, origin, _, _ = preparation
    directory = origin if location == "source" else args["target"] / ".tekton"
    (directory / "tasks.yaml").write_text("kind: Task\nmetadata:\n  name: first\n---\n" + tail)
    target = args["target"] / ".tekton"
    before = {p.name: p.read_bytes() for p in target.iterdir()}
    with pytest.raises((ValueError, YAMLError)):
        module.prepare(**args)
    assert before == {p.name: p.read_bytes() for p in target.iterdir()}


def test_target_directory_symlink_is_rejected(preparation, tmp_path):
    """Never traverse a symlinked component .tekton directory."""
    args, _, _, _ = preparation
    directory = args["target"] / ".tekton"
    directory.rmdir()
    directory.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink .tekton"):
        module.prepare(**args)
