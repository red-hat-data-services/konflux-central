"""Tests for validate-pipelineruns.py using pytest."""

import json
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml

# Import the module under test
import importlib.util
spec = importlib.util.spec_from_file_location(
    "validate_pipelineruns",
    Path(__file__).parent / "validate-pipelineruns.py",
)
vp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vp)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_pipelinerun(
    name="odh-dashboard-on-push",
    kind="PipelineRun",
    on_event=None,
    cel_expr=None,
    component_label="odh-dashboard",
    application_label="rhoai",
    output_image="quay.io/rhoai/odh-dashboard:{{target_branch}}",
    dockerfile="Dockerfile",
    path_context=".",
    repo_url="https://github.com/red-hat-data-services/odh-dashboard?rev={{revision}}",
    prefetch_input=None,
    extra_params=None,
):
    """Build a minimal PipelineRun data dict for testing."""
    annotations = {
        "build.appstudio.openshift.io/repo": repo_url,
    }
    if on_event is not None:
        annotations["pipelinesascode.tekton.dev/on-event"] = on_event
    if cel_expr is not None:
        annotations["pipelinesascode.tekton.dev/on-cel-expression"] = cel_expr

    params = [
        {"name": "output-image", "value": output_image},
        {"name": "dockerfile", "value": dockerfile},
        {"name": "path-context", "value": path_context},
    ]
    if prefetch_input is not None:
        params.append({"name": "prefetch-input", "value": prefetch_input})
    if extra_params:
        params.extend(extra_params)

    return {
        "apiVersion": "tekton.dev/v1",
        "kind": kind,
        "metadata": {
            "name": name,
            "annotations": annotations,
            "labels": {
                "appstudio.openshift.io/component": component_label,
                "appstudio.openshift.io/application": application_label,
                "pipelines.appstudio.openshift.io/type": "build",
            },
        },
        "spec": {"params": params},
    }


def write_pipelinerun(tmp_path, data, filename="test-on-push.yaml"):
    """Write a PipelineRun dict to a YAML file and return the path."""
    filepath = tmp_path / "pipelineruns" / "test-component" / ".tekton" / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(yaml.dump(data))
    return str(filepath)


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------

class TestValidationResult:
    def test_empty_result_is_ok(self):
        r = vp.ValidationResult()
        assert r.ok
        assert r.errors == []
        assert r.warnings == []

    def test_error_makes_not_ok(self):
        r = vp.ValidationResult()
        r.error("check-1", "something broke")
        assert not r.ok
        assert "[check-1] something broke" in r.errors[0]
        assert "check-1" in r.checks_failed

    def test_warning_keeps_ok(self):
        r = vp.ValidationResult()
        r.warn("check-1", "heads up")
        assert r.ok
        assert "[check-1] heads up" in r.warnings[0]
        assert "check-1" in r.checks_warned

    def test_passed_tracks_check(self):
        r = vp.ValidationResult()
        r.passed("check-1")
        assert "check-1" in r.checks_run
        assert "check-1" not in r.checks_failed


# ---------------------------------------------------------------------------
# Check 1: YAML Linting (load_yaml)
# ---------------------------------------------------------------------------

class TestYamlLint:
    def test_valid_yaml(self, tmp_path):
        f = tmp_path / "good.yaml"
        f.write_text(yaml.dump({"kind": "PipelineRun", "metadata": {}}))
        data, result = vp.load_yaml(str(f))
        assert data is not None
        assert result.ok

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.yaml"
        f.write_text("")
        data, result = vp.load_yaml(str(f))
        assert data is None
        assert not result.ok
        assert any("empty" in e.lower() for e in result.errors)

    def test_invalid_yaml(self, tmp_path):
        f = tmp_path / "bad.yaml"
        f.write_text(":\n  - :\n  bad: [")
        data, result = vp.load_yaml(str(f))
        assert data is None
        assert not result.ok

    def test_non_mapping_yaml(self, tmp_path):
        f = tmp_path / "list.yaml"
        f.write_text("- item1\n- item2\n")
        data, result = vp.load_yaml(str(f))
        assert data is None
        assert any("mapping" in e.lower() for e in result.errors)


# ---------------------------------------------------------------------------
# detect_pipelinerun_type
# ---------------------------------------------------------------------------

class TestDetectType:
    def test_pull_request(self):
        data = make_pipelinerun(on_event="pull_request")
        assert vp.detect_pipelinerun_type(data) == "pull_request"

    def test_push(self):
        data = make_pipelinerun(
            name="foo-on-push",
            cel_expr='event == "push" && target_branch == "main"',
        )
        assert vp.detect_pipelinerun_type(data) == "push"

    def test_scheduled(self):
        data = make_pipelinerun(
            name="foo-on-schedule",
            cel_expr='event == "push" && target_branch == "main"',
        )
        assert vp.detect_pipelinerun_type(data) == "scheduled"

    def test_unknown(self):
        data = make_pipelinerun()
        # No on-event or cel-expression
        assert vp.detect_pipelinerun_type(data) is None


# ---------------------------------------------------------------------------
# Check 2: Name Convention
# ---------------------------------------------------------------------------

class TestNameConvention:
    def test_push_valid(self):
        data = make_pipelinerun(name="component-on-push")
        r = vp.ValidationResult()
        vp.check_name_convention(data, "push", r)
        assert r.ok

    def test_push_invalid(self):
        data = make_pipelinerun(name="component-on-pull-request")
        r = vp.ValidationResult()
        vp.check_name_convention(data, "push", r)
        assert not r.ok

    def test_schedule_valid(self):
        data = make_pipelinerun(name="component-on-schedule")
        r = vp.ValidationResult()
        vp.check_name_convention(data, "scheduled", r)
        assert r.ok

    def test_schedule_invalid(self):
        data = make_pipelinerun(name="component-on-push")
        r = vp.ValidationResult()
        vp.check_name_convention(data, "scheduled", r)
        assert not r.ok

    def test_pr_valid(self):
        data = make_pipelinerun(name="component-on-pull-request-12345")
        r = vp.ValidationResult()
        vp.check_name_convention(data, "pull_request", r)
        assert r.ok

    def test_pr_invalid(self):
        data = make_pipelinerun(name="component-on-push")
        r = vp.ValidationResult()
        vp.check_name_convention(data, "pull_request", r)
        assert not r.ok

    def test_missing_name(self):
        data = make_pipelinerun(name="")
        r = vp.ValidationResult()
        vp.check_name_convention(data, "push", r)
        assert not r.ok
        assert any("missing" in e.lower() for e in r.errors)


# ---------------------------------------------------------------------------
# Check 3: Name Consistency
# ---------------------------------------------------------------------------

class TestNameConsistency:
    def test_push_consistent(self):
        data = make_pipelinerun(
            name="odh-dashboard-v3-4-on-push",
            component_label="odh-dashboard-v3-4",
        )
        r = vp.ValidationResult()
        vp.check_name_consistency(data, "push", "odh-dashboard", r)
        assert r.ok

    def test_push_inconsistent(self):
        data = make_pipelinerun(
            name="wrong-name-on-push",
            component_label="odh-dashboard-v3-4",
        )
        r = vp.ValidationResult()
        vp.check_name_consistency(data, "push", "odh-dashboard", r)
        assert not r.ok

    def test_missing_component_label(self):
        data = make_pipelinerun(name="foo-on-push", component_label="")
        r = vp.ValidationResult()
        vp.check_name_consistency(data, "push", "foo", r)
        assert not r.ok
        assert any("missing" in e.lower() for e in r.errors)


# ---------------------------------------------------------------------------
# Check 4: Branch and Repo Targeting
# ---------------------------------------------------------------------------

class TestBranchRepoTargeting:
    def test_correct_branch(self):
        data = make_pipelinerun(
            name="comp-v3-4-on-push",
            cel_expr='event == "push" && target_branch == "rhoai-3.4"',
        )
        r = vp.ValidationResult()
        vp.check_branch_repo_targeting(data, "rhoai-3.4", "comp", r)
        assert r.ok

    def test_wrong_branch(self):
        data = make_pipelinerun(
            name="comp-v3-4-on-push",
            cel_expr='event == "push" && target_branch == "rhoai-3.3"',
        )
        r = vp.ValidationResult()
        vp.check_branch_repo_targeting(data, "rhoai-3.4", "comp", r)
        assert not r.ok
        assert any("rhoai-3.4" in e for e in r.errors)

    def test_no_branch_skips_check(self):
        data = make_pipelinerun(
            name="comp-on-push",
            cel_expr='event == "push"',
        )
        r = vp.ValidationResult()
        vp.check_branch_repo_targeting(data, None, "comp", r)
        # Should not error on branch targeting when --branch not set
        branch_errors = [e for e in r.errors if "target branch" in e.lower()
                         or "target_branch" in e.lower()]
        assert len(branch_errors) == 0

    def test_missing_cel_expression(self):
        data = make_pipelinerun(name="comp-on-push")
        r = vp.ValidationResult()
        vp.check_branch_repo_targeting(data, "rhoai-3.4", "comp", r)
        assert not r.ok
        assert any("on-cel-expression" in e for e in r.errors)

    def test_missing_repo_annotation(self):
        data = make_pipelinerun(
            name="comp-on-push",
            cel_expr='event == "push"',
            repo_url="",
        )
        r = vp.ValidationResult()
        vp.check_branch_repo_targeting(data, None, "comp", r)
        assert not r.ok
        assert any("repo" in e.lower() for e in r.errors)

    def test_repo_missing_revision(self):
        data = make_pipelinerun(
            name="comp-on-push",
            cel_expr='event == "push"',
            repo_url="https://github.com/org/repo",
        )
        r = vp.ValidationResult()
        vp.check_branch_repo_targeting(data, None, "comp", r)
        assert not r.ok
        assert any("revision" in e for e in r.errors)

    def test_ea_version_rejected(self):
        data = make_pipelinerun(
            name="comp-v3-4-ea-2-on-push",
            cel_expr='event == "push" && target_branch == "rhoai-3.4"',
        )
        r = vp.ValidationResult()
        vp.check_branch_repo_targeting(data, "rhoai-3.4", "comp", r)
        assert any("version" in e.lower() and "v3-4-ea-2" in e for e in r.errors)

    def test_correct_version_accepted(self):
        data = make_pipelinerun(
            name="comp-v3-4-on-push",
            cel_expr='event == "push" && target_branch == "rhoai-3.4"',
        )
        r = vp.ValidationResult()
        vp.check_branch_repo_targeting(data, "rhoai-3.4", "comp", r)
        version_errors = [e for e in r.errors if "version" in e.lower()]
        assert len(version_errors) == 0

    def test_schedule_version_check(self):
        data = make_pipelinerun(
            name="comp-v3-4-on-schedule",
            cel_expr='event == "push" && target_branch == "rhoai-3.4"',
        )
        r = vp.ValidationResult()
        vp.check_branch_repo_targeting(data, "rhoai-3.4", "comp", r)
        version_errors = [e for e in r.errors if "version" in e.lower()]
        assert len(version_errors) == 0


# ---------------------------------------------------------------------------
# Check 5: CEL Self-Reference
# ---------------------------------------------------------------------------

class TestCelSelfReference:
    def test_correct_self_ref(self):
        data = make_pipelinerun(
            cel_expr=(
                'event == "push" && target_branch == "main"'
                ' && ( !".tekton/**".pathChanged()'
                ' || ".tekton/comp-on-push.yaml".pathChanged() )'
            ),
        )
        r = vp.ValidationResult()
        vp.check_cel_self_reference(
            data, "pipelineruns/comp/.tekton/comp-on-push.yaml", r
        )
        assert r.ok

    def test_missing_self_ref(self):
        data = make_pipelinerun(
            cel_expr=(
                'event == "push" && target_branch == "main"'
                ' && ( !".tekton/**".pathChanged()'
                ' || ".tekton/other-file.yaml".pathChanged() )'
            ),
        )
        r = vp.ValidationResult()
        vp.check_cel_self_reference(
            data, "pipelineruns/comp/.tekton/comp-on-push.yaml", r
        )
        assert not r.ok
        assert any("self-reference" in e.lower() or "pathChanged" in e for e in r.errors)

    def test_no_tekton_filter_skips(self):
        data = make_pipelinerun(
            cel_expr='event == "push" && target_branch == "main"',
        )
        r = vp.ValidationResult()
        vp.check_cel_self_reference(
            data, "pipelineruns/comp/.tekton/comp-on-push.yaml", r
        )
        assert r.ok

    def test_no_cel_expr_skips(self):
        data = make_pipelinerun()
        r = vp.ValidationResult()
        vp.check_cel_self_reference(
            data, "pipelineruns/comp/.tekton/comp-on-push.yaml", r
        )
        assert r.ok


# ---------------------------------------------------------------------------
# Check 6: Quay Repo Existence
# ---------------------------------------------------------------------------

class TestQuayRepoExistence:
    def setup_method(self):
        # Reset the global cache before each test
        vp._quay_repos_cache = None

    def test_missing_output_image(self):
        data = make_pipelinerun(output_image="")
        data["spec"]["params"] = [p for p in data["spec"]["params"]
                                  if p["name"] != "output-image"]
        r = vp.ValidationResult()
        vp.check_quay_repo_existence(data, "push", "fake-auth", r)
        assert not r.ok
        assert any("output-image" in e for e in r.errors)

    def test_non_quay_image(self):
        data = make_pipelinerun(output_image="docker.io/library/nginx:latest")
        r = vp.ValidationResult()
        vp.check_quay_repo_existence(data, "push", "fake-auth", r)
        assert not r.ok
        assert any("quay.io" in e for e in r.errors)

    def test_no_auth_skips(self):
        data = make_pipelinerun(output_image="quay.io/rhoai/test:tag")
        r = vp.ValidationResult()
        vp.check_quay_repo_existence(data, "push", "", r)
        assert r.ok  # no errors, just skipped

    @patch.object(vp, "_fetch_quay_catalog")
    def test_repo_exists(self, mock_catalog):
        mock_catalog.return_value = ({"rhoai/odh-dashboard"}, None)
        data = make_pipelinerun(output_image="quay.io/rhoai/odh-dashboard:tag")
        r = vp.ValidationResult()
        vp.check_quay_repo_existence(data, "push", "fake-auth", r)
        assert r.ok

    @patch.object(vp, "_fetch_quay_catalog")
    def test_repo_not_found(self, mock_catalog):
        mock_catalog.return_value = ({"rhoai/other-repo"}, None)
        data = make_pipelinerun(output_image="quay.io/rhoai/nonexistent:tag")
        r = vp.ValidationResult()
        vp.check_quay_repo_existence(data, "push", "fake-auth", r)
        assert not r.ok
        assert any("does not exist" in e for e in r.errors)

    @patch.object(vp, "_fetch_quay_catalog")
    def test_catalog_failure_warns(self, mock_catalog):
        mock_catalog.return_value = (None, "v2 auth failed: HTTP 401")
        data = make_pipelinerun(output_image="quay.io/rhoai/test:tag")
        r = vp.ValidationResult()
        vp.check_quay_repo_existence(data, "push", "fake-auth", r)
        assert r.ok  # warning, not error
        assert any("catalog" in w.lower() for w in r.warnings)


# ---------------------------------------------------------------------------
# Check 7: Quay Naming Convention
# ---------------------------------------------------------------------------

class TestQuayNaming:
    def test_pr_valid(self):
        data = make_pipelinerun(
            output_image="quay.io/rhoai/pull-request-pipelines:comp-{{revision}}",
        )
        r = vp.ValidationResult()
        vp.check_quay_naming_convention(data, "pull_request", r)
        assert r.ok

    def test_pr_wrong_repo(self):
        data = make_pipelinerun(
            output_image="quay.io/rhoai/odh-dashboard:{{revision}}",
        )
        r = vp.ValidationResult()
        vp.check_quay_naming_convention(data, "pull_request", r)
        assert not r.ok
        assert any("pull-request-pipelines" in e for e in r.errors)

    def test_pr_missing_revision(self):
        data = make_pipelinerun(
            output_image="quay.io/rhoai/pull-request-pipelines:latest",
        )
        r = vp.ValidationResult()
        vp.check_quay_naming_convention(data, "pull_request", r)
        assert not r.ok
        assert any("revision" in e for e in r.errors)

    def test_push_valid(self):
        data = make_pipelinerun(
            output_image="quay.io/rhoai/odh-dashboard:{{target_branch}}",
        )
        r = vp.ValidationResult()
        vp.check_quay_naming_convention(data, "push", r)
        assert r.ok

    def test_push_wrong_namespace(self):
        data = make_pipelinerun(
            output_image="quay.io/other-org/foo:{{target_branch}}",
        )
        r = vp.ValidationResult()
        vp.check_quay_naming_convention(data, "push", r)
        assert not r.ok
        assert any("quay.io/rhoai/" in e for e in r.errors)

    def test_push_uses_pr_repo(self):
        data = make_pipelinerun(
            output_image="quay.io/rhoai/pull-request-pipelines:{{target_branch}}",
        )
        r = vp.ValidationResult()
        vp.check_quay_naming_convention(data, "push", r)
        assert not r.ok
        assert any("pull-request-pipelines" in e for e in r.errors)

    def test_push_missing_target_branch_warns(self):
        data = make_pipelinerun(
            output_image="quay.io/rhoai/odh-dashboard:latest",
        )
        r = vp.ValidationResult()
        vp.check_quay_naming_convention(data, "push", r)
        assert r.ok  # warning only
        assert any("target_branch" in w for w in r.warnings)


# ---------------------------------------------------------------------------
# Check 8: Dockerfile Context Path
# ---------------------------------------------------------------------------

class TestDockerfilePath:
    def test_missing_dockerfile_param(self):
        data = make_pipelinerun(dockerfile="")
        data["spec"]["params"] = [p for p in data["spec"]["params"]
                                  if p["name"] != "dockerfile"]
        r = vp.ValidationResult()
        vp.check_dockerfile_context_path(data, "comp", "fake-token", None, r)
        assert not r.ok
        assert any("dockerfile" in e.lower() for e in r.errors)

    def test_no_github_token_skips(self):
        data = make_pipelinerun(dockerfile="Dockerfile")
        r = vp.ValidationResult()
        vp.check_dockerfile_context_path(data, "comp", "", None, r)
        assert r.ok  # skipped

    @patch.object(vp, "_check_repo_access", return_value=False)
    def test_private_repo_warns(self, mock_access):
        data = make_pipelinerun(dockerfile="Dockerfile")
        r = vp.ValidationResult()
        vp.check_dockerfile_context_path(data, "comp", "fake-token", None, r)
        assert r.ok  # warning only
        assert any("not accessible" in w for w in r.warnings)

    @patch.object(vp, "_check_repo_access", return_value=True)
    @patch.object(vp, "_github_file_exists", return_value=True)
    def test_dockerfile_found(self, mock_exists, mock_access):
        data = make_pipelinerun(dockerfile="Dockerfile.konflux")
        r = vp.ValidationResult()
        vp.check_dockerfile_context_path(data, "comp", "fake-token", None, r)
        assert r.ok

    @patch.object(vp, "_check_repo_access", return_value=True)
    @patch.object(vp, "_github_file_exists", return_value=False)
    @patch.object(vp, "_list_dockerfiles", return_value=["Dockerfile", "Dockerfile.konflux"])
    def test_dockerfile_not_found(self, mock_list, mock_exists, mock_access):
        data = make_pipelinerun(dockerfile="Dockerfile.typo")
        r = vp.ValidationResult()
        vp.check_dockerfile_context_path(data, "comp", "fake-token", None, r)
        assert not r.ok
        assert any("not found" in e.lower() for e in r.errors)

    @patch.object(vp, "_check_repo_access", return_value=True)
    @patch.object(vp, "_github_file_exists")
    def test_path_context_checked(self, mock_exists, mock_access):
        """When path-context is set, checks context/dockerfile first."""
        mock_exists.side_effect = lambda repo, path, token, ref=None: path == "subdir/Dockerfile"
        data = make_pipelinerun(dockerfile="Dockerfile", path_context="subdir")
        r = vp.ValidationResult()
        vp.check_dockerfile_context_path(data, "comp", "fake-token", None, r)
        assert r.ok


# ---------------------------------------------------------------------------
# Check 9: Prefetch Input
# ---------------------------------------------------------------------------

class TestPrefetchInput:
    def test_no_prefetch_skips(self):
        data = make_pipelinerun()
        r = vp.ValidationResult()
        vp.check_prefetch_input(data, r)
        assert r.ok

    def test_valid_json_object(self):
        data = make_pipelinerun(prefetch_input='{"type": "gomod", "path": "."}')
        r = vp.ValidationResult()
        vp.check_prefetch_input(data, r)
        assert r.ok

    def test_valid_json_array(self):
        data = make_pipelinerun(
            prefetch_input='[{"type": "gomod"}, {"type": "rpm"}]'
        )
        r = vp.ValidationResult()
        vp.check_prefetch_input(data, r)
        assert r.ok

    def test_yaml_dict(self):
        data = make_pipelinerun(prefetch_input={"type": "gomod", "path": "."})
        r = vp.ValidationResult()
        vp.check_prefetch_input(data, r)
        assert r.ok

    def test_yaml_list(self):
        data = make_pipelinerun(prefetch_input=[{"type": "gomod"}])
        r = vp.ValidationResult()
        vp.check_prefetch_input(data, r)
        assert r.ok

    def test_empty_string(self):
        data = make_pipelinerun(prefetch_input="")
        r = vp.ValidationResult()
        vp.check_prefetch_input(data, r)
        assert not r.ok
        assert any("empty" in e for e in r.errors)

    def test_invalid_json(self):
        data = make_pipelinerun(prefetch_input="not json at all")
        r = vp.ValidationResult()
        vp.check_prefetch_input(data, r)
        assert not r.ok
        assert any("not valid JSON" in e for e in r.errors)

    def test_json_array_with_non_objects(self):
        data = make_pipelinerun(prefetch_input='["string", "values"]')
        r = vp.ValidationResult()
        vp.check_prefetch_input(data, r)
        assert not r.ok
        assert any("should be an object" in e for e in r.errors)

    def test_json_scalar(self):
        data = make_pipelinerun(prefetch_input="42")
        r = vp.ValidationResult()
        vp.check_prefetch_input(data, r)
        assert not r.ok


# ---------------------------------------------------------------------------
# validate_pipelinerun (integration)
# ---------------------------------------------------------------------------

class TestValidatePipelinerun:
    def setup_method(self):
        vp._quay_repos_cache = None
        vp._repo_access_cache.clear()

    def test_valid_push_pipelinerun(self, tmp_path):
        data = make_pipelinerun(
            name="odh-dashboard-v3-4-on-push",
            cel_expr=(
                'event == "push" && target_branch == "rhoai-3.4"'
                ' && ( !".tekton/**".pathChanged()'
                ' || ".tekton/odh-dashboard-v3-4-on-push.yaml".pathChanged() )'
            ),
            component_label="odh-dashboard-v3-4",
            output_image="quay.io/rhoai/odh-dashboard:{{target_branch}}",
        )
        filepath = write_pipelinerun(
            tmp_path, data, "odh-dashboard-v3-4-on-push.yaml"
        )
        result = vp.validate_pipelinerun(filepath, "rhoai-3.4", "", "")
        assert result.ok, f"Unexpected errors: {result.errors}"

    def test_valid_pr_pipelinerun(self, tmp_path):
        data = make_pipelinerun(
            name="odh-dashboard-on-pull-request",
            on_event="pull_request",
            component_label="pull-request-pipelines",
            output_image="quay.io/rhoai/pull-request-pipelines:odh-dashboard-{{revision}}",
        )
        filepath = write_pipelinerun(
            tmp_path, data, "odh-dashboard-on-pull-request.yaml"
        )
        result = vp.validate_pipelinerun(filepath, None, "", "")
        assert result.ok, f"Unexpected errors: {result.errors}"

    def test_wrong_kind(self, tmp_path):
        data = make_pipelinerun(kind="Pipeline")
        filepath = write_pipelinerun(tmp_path, data)
        result = vp.validate_pipelinerun(filepath, None, "", "")
        assert not result.ok
        assert any("PipelineRun" in e for e in result.errors)

    def test_filepath_prepended_to_messages(self, tmp_path):
        data = make_pipelinerun(
            name="bad-name",
            on_event="pull_request",
            component_label="pull-request-pipelines",
            output_image="quay.io/rhoai/pull-request-pipelines:{{revision}}",
        )
        filepath = write_pipelinerun(tmp_path, data, "bad-name.yaml")
        result = vp.validate_pipelinerun(filepath, None, "", "")
        for e in result.errors:
            assert filepath in e


# ---------------------------------------------------------------------------
# Quay catalog fetching
# ---------------------------------------------------------------------------

class TestFetchQuayCatalog:
    @patch("urllib.request.urlopen")
    def test_successful_fetch(self, mock_urlopen):
        # Mock v2 auth response
        auth_resp = MagicMock()
        auth_resp.read.return_value = json.dumps({"token": "test-token"}).encode()

        # Mock catalog response
        catalog_resp = MagicMock()
        catalog_resp.read.return_value = json.dumps({
            "repositories": ["rhoai/repo1", "rhoai/repo2"]
        }).encode()
        catalog_resp.headers = MagicMock()
        catalog_resp.headers.get.return_value = ""

        mock_urlopen.side_effect = [auth_resp, catalog_resp]

        repos, err = vp._fetch_quay_catalog("dGVzdDp0ZXN0")  # base64 "test:test"
        assert err is None
        assert repos == {"rhoai/repo1", "rhoai/repo2"}

    @patch("urllib.request.urlopen")
    def test_auth_failure(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "url", 401, "Unauthorized", {}, None
        )
        repos, err = vp._fetch_quay_catalog("dGVzdDp0ZXN0")
        assert repos is None
        assert "401" in err

    @patch("urllib.request.urlopen")
    def test_pagination(self, mock_urlopen):
        auth_resp = MagicMock()
        auth_resp.read.return_value = json.dumps({"token": "tok"}).encode()

        page1_resp = MagicMock()
        page1_resp.read.return_value = json.dumps({
            "repositories": ["rhoai/repo1"]
        }).encode()
        page1_resp.headers = MagicMock()
        page1_resp.headers.get.return_value = '</v2/_catalog?n=100&next_page=abc>; rel="next"'

        page2_resp = MagicMock()
        page2_resp.read.return_value = json.dumps({
            "repositories": ["rhoai/repo2"]
        }).encode()
        page2_resp.headers = MagicMock()
        page2_resp.headers.get.return_value = ""

        mock_urlopen.side_effect = [auth_resp, page1_resp, page2_resp]

        repos, err = vp._fetch_quay_catalog("dGVzdDp0ZXN0")
        assert err is None
        assert repos == {"rhoai/repo1", "rhoai/repo2"}
        # Verify the pagination URL was constructed correctly
        calls = mock_urlopen.call_args_list
        assert "quay.io/v2/_catalog" in calls[2][0][0].full_url
