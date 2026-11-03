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
             cel='event == "pull_request" && target_branch == "rhoai-3.6"', legacy=False,
             application=None):
    """Write a PR fixture with independent trigger syntax and application identity."""
    path = tmp_path / "pipelineruns/widget/.tekton/widget-pull-request.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    annotations = {"pipelinesascode.tekton.dev/on-event": "[pull_request]"} if legacy else {
        "pipelinesascode.tekton.dev/on-cel-expression": cel}
    if application is None:
        application = "automation" if legacy else "rhoai-v3-6"
    data = {"apiVersion": "tekton.dev/v1", "kind": "PipelineRun", "metadata": {
        "name": name, "labels": {"appstudio.openshift.io/component": component,
                                 "appstudio.openshift.io/application": application},
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
    with pytest.raises(AssertionError, match="must match release component|requires the automation application"):
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
@pytest.mark.parametrize("legacy", [False, True])
def test_wrong_release_image_or_tag_is_rejected(tmp_path, image, legacy):
    """Reject invalid release images independently of event annotation syntax."""
    with pytest.raises(AssertionError):
        validator.test_quay_naming(write_pr(
            tmp_path, image=image, legacy=legacy, application="rhoai-v3-6"))


@pytest.mark.parametrize("component", ["pull-request-pipelines", "pr-pipelines", "pull-request-pipelines-widget"])
def test_legacy_pr_contract_remains_supported(tmp_path, component):
    """Annotation-only PR files retain the old component and revision-tag checks."""
    path = write_pr(tmp_path, legacy=True, component=component,
                    image="quay.io/rhoai/pull-request-pipelines:widget-{{revision}}")
    assert validator._detect_type(yaml.safe_load(path.read_text())) == "pull_request"
    validator.test_name_consistency(path)
    validator.test_quay_naming(path)


def test_legacy_contract_does_not_silently_accept_new_image_policy(tmp_path):
    """Legacy identity must not bypass its revision-tag image validation."""
    with pytest.raises(AssertionError):
        validator.test_quay_naming(write_pr(tmp_path, legacy=True, component="pull-request-pipelines"))


@pytest.mark.parametrize("component", ["automl", "autorag"])
def test_legacy_cel_release_prs_remain_supported(tmp_path, component):
    """Reproduce the two rhoai-3.4 PRs that use CEL with legacy build identity."""
    path = write_pr(
        tmp_path, name=f"odh-{component}-v3-4-on-pull-request",
        component=f"pull-request-pipelines-odh-{component}", application="automation",
        image=f"quay.io/rhoai/pull-request-pipelines:odh-{component}-{{{{revision}}}}",
        cel='event == "pull_request" && target_branch == "rhoai-3.4"',
    )
    data = yaml.safe_load(path.read_text())
    assert validator._detect_type(data) == "pull_request"
    assert validator._pr_build_contract(data) == "legacy"
    validator.test_name_convention(path)
    validator.test_name_consistency(path)
    validator.test_quay_naming(path)


def test_release_identity_with_legacy_trigger_syntax(tmp_path):
    """Event-annotation syntax must not force a release-identity PR onto legacy rules."""
    path = write_pr(tmp_path, legacy=True, application="rhoai-v3-6")
    validator.test_name_consistency(path)
    validator.test_quay_naming(path)


@pytest.mark.parametrize("application, component, image", [
    ("rhoai-v3-6", "pull-request-pipelines-widget", "quay.io/rhoai/pull-request-pipelines:widget-{{revision}}"),
    ("automation", "widget-v3-6", "quay.io/rhoai/widget:on-pr-{{pull_request_number}}"),
    ("", "widget-v3-6", "quay.io/rhoai/widget:on-pr-{{pull_request_number}}"),
    ("rhoai-v3-6", "", "quay.io/rhoai/widget:on-pr-{{pull_request_number}}"),
])
@pytest.mark.parametrize("legacy", [False, True])
def test_mixed_or_incomplete_identity_is_rejected(tmp_path, application, component, image, legacy):
    """Both checks must reject inconsistent identity under either trigger syntax."""
    path = write_pr(tmp_path, application=application, component=component, image=image, legacy=legacy)
    for check in (validator.test_name_consistency, validator.test_quay_naming):
        with pytest.raises(AssertionError):
            check(path)


def write_push(pr_path, *, component="widget-v3-6", release="rhoai-3.6",
               image="quay.io/rhoai/widget:release", filename="widget-push.yaml",
               application="rhoai-v3-6"):
    """Create a local push parent without assuming component and repo names coincide."""
    data = yaml.safe_load(pr_path.read_text())
    data["metadata"]["name"] = component + "-on-push"
    data["metadata"]["labels"] = {"appstudio.openshift.io/component": component,
                                  "appstudio.openshift.io/application": application}
    data["metadata"]["annotations"] = {"pipelinesascode.tekton.dev/on-cel-expression":
        f"event == 'push' && target_branch == '{release}'"}
    data["spec"]["params"] = [{"name": "output-image", "value": image}]
    path = pr_path.with_name(filename)
    path.write_text(yaml.safe_dump(data))
    return path


@pytest.mark.parametrize("component, parent_component", [
    ("widget-v3-6", "widget-v3-6"),
    ("widget-v3-6", "odh-widget-v3-6"),
    ("odh-guardrails-detector-hf-runtime-v3-6", "odh-guardrails-detector-huggingface-runtime-v3-6"),
])
@pytest.mark.parametrize("release", ["rhoai-3.6", "rhoai-3.6-ea.2"])
def test_release_pr_image_uses_actual_push_repo(tmp_path, component, parent_component, release):
    """Honor aliases and actual push repositories rather than guessing from labels."""
    path = write_pr(tmp_path, component=component, image="quay.io/rhoai/delivery:on-pr-{{pull_request_number}}",
                    cel=f"event == 'pull_request' && target_branch == '{release}'")
    write_push(path, component=parent_component, image="quay.io/rhoai/delivery:release", release=release)
    validator.test_release_pr_image_matches_push(path, None)


@pytest.mark.parametrize("legacy_trigger", [False, True])
def test_release_pr_cannot_publish_to_unrelated_repo(tmp_path, legacy_trigger):
    """A valid namespace/tag cannot hide a repository mismatch under either trigger syntax."""
    path = write_pr(tmp_path, legacy=legacy_trigger, application="rhoai-v3-6",
                    image="quay.io/rhoai/unrelated:on-pr-{{pull_request_number}}")
    write_push(path)
    validator.test_quay_naming(path)  # Format alone deliberately does not establish identity.
    with pytest.raises(AssertionError, match="must match push parent"):
        validator.test_release_pr_image_matches_push(path, "rhoai-3.6")


@pytest.mark.parametrize("problem", ["missing", "wrong-release", "wrong-component", "wrong-application", "ambiguous", "missing-image"])
def test_release_pr_requires_unambiguous_same_release_push(tmp_path, problem):
    """Never select a different release/application or silently accept missing parents."""
    path = write_pr(tmp_path)
    if problem != "missing":
        write_push(path, release="rhoai-3.5" if problem == "wrong-release" else "rhoai-3.6",
                   component="other-v3-6" if problem == "wrong-component" else "widget-v3-6",
                   application="rhoai-v3-5" if problem == "wrong-application" else "rhoai-v3-6",
                   image=None if problem == "missing-image" else "quay.io/rhoai/widget:release")
    if problem == "ambiguous":
        write_push(path, filename="duplicate-push.yml")
    with pytest.raises(AssertionError, match="exactly one matching push|must define output-image"):
        validator.test_release_pr_image_matches_push(path, None)


def test_release_pr_prefers_exact_component_over_alias(tmp_path):
    """An alias candidate must not displace an exact component match."""
    path = write_pr(tmp_path)
    write_push(path)
    write_push(path, component="odh-widget-v3-6", filename="alias-push.yaml",
               image="quay.io/rhoai/other:release")
    validator.test_release_pr_image_matches_push(path, None)


def test_release_pr_does_not_search_other_directories(tmp_path):
    """Another repository's push parent must not satisfy a missing local parent."""
    path = write_pr(tmp_path)
    parent = write_push(path)
    other = path.parent.parent.parent / "other/.tekton"
    other.mkdir(parents=True)
    parent.rename(other / parent.name)
    with pytest.raises(AssertionError, match="found 0"):
        validator.test_release_pr_image_matches_push(path, None)


@pytest.mark.parametrize("legacy", [False, True])
def test_legacy_pr_repository_rules_do_not_require_push_parent(tmp_path, legacy):
    """Legacy identities remain exempt, including legacy CEL PRs on release branches."""
    path = write_pr(tmp_path, legacy=legacy, application="automation", component="pull-request-pipelines")
    validator.test_release_pr_image_matches_push(path, "rhoai-3.6")


def test_main_multibranch_pr_has_no_local_release_parent(tmp_path):
    """Do not apply release-local lookup to main's cross-branch PR configuration."""
    path = write_pr(tmp_path, cel='event == "pull_request" && target_branch.matches(".*")')
    validator.test_release_pr_image_matches_push(path, None)


@pytest.mark.parametrize("cel_branch, supplied_branch", [
    ("rhoai-3.5", "rhoai-3.6"),
    ("rhoai-3.6-ea.2", "rhoai-3.6"),
    ("rhoai-3.6", "rhoai-3.6-ea.2"),
])
@pytest.mark.parametrize("quote", ['"', "'"])
def test_pr_target_cannot_be_overridden_by_branch_option(tmp_path, cel_branch, supplied_branch, quote):
    """A matching image/parent on the supplied branch cannot hide a wrong PR target."""
    path = write_pr(tmp_path, cel=f'event == "pull_request" && target_branch == {quote}{cel_branch}{quote}')
    write_push(path, release=supplied_branch)
    with pytest.raises(AssertionError, match="does not match --branch"):
        validator.test_release_pr_image_matches_push(path, supplied_branch)


@pytest.mark.parametrize("legacy_trigger, supplied_branch", [
    (False, None), (False, "rhoai-3.6"), (True, "rhoai-3.6"),
])
def test_pr_target_branch_fallbacks_remain_supported(tmp_path, legacy_trigger, supplied_branch):
    """Accept matching explicit targets and either CLI-only or CEL-only branch selection."""
    path = write_pr(tmp_path, legacy=legacy_trigger, application="rhoai-v3-6")
    write_push(path)
    validator.test_release_pr_image_matches_push(path, supplied_branch)


@pytest.mark.parametrize("sibling", [
    "kind: ConfigMap\nmetadata:\n  name: config\n",
    "kind: Task\nmetadata:\n  name: task\n",
    "", "[]\n", "plain scalar\n", "[malformed YAML",
])
@pytest.mark.parametrize("matching_image", [False, True])
def test_non_pipeline_sibling_cannot_skip_image_validation(tmp_path, sibling, matching_image):
    """Unrelated documents before the push parent must not suppress pass/fail results."""
    image = "widget" if matching_image else "unrelated"
    path = write_pr(tmp_path, image=f"quay.io/rhoai/{image}:on-pr-{{{{pull_request_number}}}}")
    write_push(path)
    path.with_name("000-unrelated.yaml").write_text(sibling)
    try:
        if matching_image:
            validator.test_release_pr_image_matches_push(path, None)
        else:
            with pytest.raises(AssertionError, match="must match push parent"):
                validator.test_release_pr_image_matches_push(path, None)
    except pytest.skip.Exception as exc:
        pytest.fail(f"Sibling document skipped the entire PR check: {exc}")


def test_legacy_cel_identity_cannot_use_release_image(tmp_path):
    """Recognizing legacy CEL PRs must not allow a mixed legacy/release image contract."""
    path = write_pr(tmp_path, application="automation", component="pull-request-pipelines-widget")
    with pytest.raises(AssertionError):
        validator.test_quay_naming(path)
