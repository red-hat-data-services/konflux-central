"""Component-specific retrigger semantics of the main PR PipelineRun template.

Run with ENFORCEMENT_TEST_TOOLING pointing to devops-shared-resources and
pytest in an environment containing that validator's dependencies.
"""
import os
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
ANNOTATION = "pipelinesascode.tekton.dev/on-comment"


@pytest.fixture
def render_comment(monkeypatch):
    tooling = os.environ.get("ENFORCEMENT_TEST_TOOLING")
    if not tooling:
        pytest.skip("set ENFORCEMENT_TEST_TOOLING for actual template tests")
    monkeypatch.syspath_prepend(tooling)
    from lib.tekton import render_and_merge

    def render(component="odh-model-serving-api-v3-7-ea-1", previous=None,
               filename="unrelated-filename-pull-request.yaml"):
        annotations = {} if previous is None else {ANNOTATION: previous}
        existing = {
            "metadata": {"name": filename.removesuffix(".yaml"),
                         "annotations": annotations},
        }
        parent = {
            "metadata": {
                "labels": {"appstudio.openshift.io/component": component},
                "annotations": {
                    "pipelinesascode.tekton.dev/on-cel-expression":
                        'event == "push" && target_branch == "rhoai-3.7-ea.1"',
                },
            },
        }
        result = render_and_merge(
            str(ROOT / "tekton-templates"), "pull-request.yaml.j2", existing,
            filename, branch="main", parent_data=parent, mode="fix",
        )
        return result["metadata"]["annotations"][ANNOTATION]

    return render


@pytest.mark.parametrize("previous", [
    None, "", "^/build-konflux", "^/build-konflux odh-model-serving-api", "^/custom$",
])
def test_replaces_existing_patterns_with_exact_component_command(render_comment, previous):
    pattern = render_comment(previous=previous)
    assert pattern == "^/build-konflux (odh-model-serving-api|all)$"
    assert re.search(pattern, "/build-konflux odh-model-serving-api")
    for comment in (
        "/build-konflux", "/build-konflux odh-model-controller",
        "/build-konflux odh-model-serving-api-extra",
        "/build-konflux odh-model-serving-api extra",
        "prefix /build-konflux odh-model-serving-api",
        "/build-konflux all-extra", "/build-konflux all extra",
        "prefix /build-konflux all", "/build-konflux alloy",
    ):
        assert not re.search(pattern, comment)
    assert render_comment(previous=pattern) == pattern


def test_sibling_commands_trigger_only_their_own_pipeline(render_comment):
    components = ["odh-model-controller", "odh-model-serving-api"]
    patterns = {component: render_comment(f"{component}-v3-7-ea-1")
                for component in components}
    for component in components:
        triggered = [name for name, pattern in patterns.items()
                     if re.search(pattern, f"/build-konflux {component}")]
        assert triggered == [component]
    triggered_all = [name for name, pattern in patterns.items()
                     if re.search(pattern, "/build-konflux all")]
    assert triggered_all == components


@pytest.mark.parametrize("component", [
    "odh-model-controller-v3-6", "odh-model-controller-v3-7-ea-1",
    "odh-model-controller-v3-7-ea-2", "odh-model-controller",
])
def test_command_uses_unversioned_parent_component_not_filename(render_comment, component):
    pattern = render_comment(component=component, filename="different-pull-request.yaml")
    assert pattern == "^/build-konflux (odh-model-controller|all)$"
    assert not re.search(pattern, "/build-konflux different")
    if component != "odh-model-controller":
        assert not re.search(pattern, f"/build-konflux {component}")
