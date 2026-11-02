"""Offline regression tests for legacy and release-aware PR validation contracts."""
import importlib.util
from pathlib import Path

import pytest
import yaml

spec = importlib.util.spec_from_file_location(
    "pr_contract_validator", Path(__file__).with_name("test_validate_pipelineruns.py"))
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    """Ensure contract tests never use credentials or make external requests."""
    def unexpected_request(*args, **kwargs):
        """Fail immediately if a supposedly local check attempts network access."""
        raise AssertionError("network access is forbidden in contract tests")
    monkeypatch.setattr(validator.urllib.request, "urlopen", unexpected_request)


def write_pr(tmp_path, *, name="widget-on-pull-request-{{pull_request_number}}",
             component="widget-v3-6", image="quay.io/rhoai/widget:on-pr-{{pull_request_number}}",
             cel='event == "pull_request" && target_branch == "rhoai-3.6"', legacy=False):
    """Write a minimal PR fixture with either modern CEL or legacy event annotations."""
    path = tmp_path / "pipelineruns/widget/.tekton/widget-pull-request.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    annotations = {"pipelinesascode.tekton.dev/on-event": "[pull_request]"} if legacy else {
        "pipelinesascode.tekton.dev/on-cel-expression": cel}
    data = {"apiVersion": "tekton.dev/v1", "kind": "PipelineRun", "metadata": {
        "name": name, "labels": {"appstudio.openshift.io/component": component},
        "annotations": annotations}, "spec": {"params": [{"name": "output-image", "value": image}]}}
    path.write_text(yaml.safe_dump(data))
    return path


@pytest.mark.parametrize("cel, expected", [
    ('event == "pull_request"', "pull_request"),
    ("event=='pull_request'", "pull_request"),
    ('event == "push"', "push"),
    ("event == 'push'", "push"),
    ('"push".pathChanged()', None),
    ('"pull_request".pathChanged()', None),
])
def test_event_detection_uses_cel_equality(cel, expected):
    """Recognize explicit CEL event predicates rather than unrelated event strings."""
    data = {"metadata": {"annotations": {"pipelinesascode.tekton.dev/on-cel-expression": cel}}}
    assert validator._detect_type(data) == expected


def test_cel_event_overrides_legacy_annotation_and_keeps_scheduled_detection():
    """PaC CEL takes precedence, while scheduled push runs retain their classification."""
    data = {"metadata": {"name": "widget-on-schedule", "annotations": {
        "pipelinesascode.tekton.dev/on-event": "[pull_request]",
        "pipelinesascode.tekton.dev/on-cel-expression": 'event == "push"'}}}
    assert validator._detect_type(data) == "scheduled"


@pytest.mark.parametrize("name, component", [
    ("widget-on-pull-request", "widget-v3-6"),
    ("widget-on-pull-request", "widget-v3-6-ea-2"),
    ("widget-on-pull-request", "odh-widget-v3-6"),
    ("widget-ci-on-pull-request", "widget-ci"),
    ("odh-workbench-codeserver-minimal-cpu-py312-on-pull-request-{{pull_request_number}}",
     "odh-workbench-codeserver-minimal-cpu-py312-v3-6"),
    ("odh-guardrails-detector-hf-runtime-on-pull-request", "odh-guardrails-detector-huggingface-runtime-v3-6"),
])
def test_release_component_identity_is_accepted(tmp_path, name, component):
    """Accept GA, EA, CI, optional odh prefixes, and configured component aliases."""
    path = write_pr(tmp_path, name=name, component=component)
    validator.test_name_convention(path)
    validator.test_name_consistency(path)
    validator.test_quay_naming(path)


@pytest.mark.parametrize("component", ["unrelated-v3-6", "pull-request-pipelines", "pr-pipelines"])
def test_wrong_or_generic_release_component_is_rejected(tmp_path, component):
    """Do not weaken modern checks to permit arbitrary or legacy component identities."""
    with pytest.raises(AssertionError, match="must match release component"):
        validator.test_name_consistency(write_pr(tmp_path, component=component))


@pytest.mark.parametrize("image", [
    "quay.io/rhoai/widget:on-pr-{{pull_request_number}}",
    "quay.io/rhoai/widget:on-pr-{{ pull_request_number }}",
])
def test_release_delivery_image_is_accepted(tmp_path, image):
    """Accept actual delivery repositories with PR-number tags, including spaced variables."""
    validator.test_quay_naming(write_pr(tmp_path, image=image))


@pytest.mark.parametrize("image", [
    "quay.io/rhoai/pull-request-pipelines:on-pr-{{pull_request_number}}",
    "quay.io/rhoai/widget:{{revision}}",
    "quay.io/rhoai/widget:rhoai-3.6",
    "quay.io/rhoai/widget:on-pr-{{revision}}",
    "quay.io/other/widget:on-pr-{{pull_request_number}}",
])
def test_wrong_release_image_or_tag_is_rejected(tmp_path, image):
    """Reject generic destinations, release/revision tags, and foreign namespaces."""
    with pytest.raises(AssertionError):
        validator.test_quay_naming(write_pr(tmp_path, image=image))


@pytest.mark.parametrize("component", ["pull-request-pipelines", "pr-pipelines", "pull-request-pipelines-widget"])
def test_legacy_pr_contract_remains_supported(tmp_path, component):
    """Annotation-only PR files retain the old component and revision-tag checks."""
    path = write_pr(tmp_path, legacy=True, component=component,
                    image="quay.io/rhoai/pull-request-pipelines:widget-{{revision}}")
    assert validator._detect_type(yaml.safe_load(path.read_text())) == "pull_request"
    validator.test_name_consistency(path)
    validator.test_quay_naming(path)


def test_legacy_contract_does_not_silently_accept_new_image_policy(tmp_path):
    """Selecting legacy annotations must not bypass its legacy image validation."""
    with pytest.raises(AssertionError):
        validator.test_quay_naming(write_pr(tmp_path, legacy=True, component="pull-request-pipelines"))
