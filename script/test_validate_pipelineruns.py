"""PipelineRun validation checks implemented as pytest tests.

Discovers all PipelineRun YAML files and runs each validation check as a
separate test case. See docs/validate-pipelineruns.md for check details.

Usage:
    pytest script/test_validate_pipelineruns.py --pipelinerun-dir pipelineruns/
    pytest script/test_validate_pipelineruns.py --pipelinerun-dir pipelineruns/ --branch rhoai-3.4

Environment variables:
    QUAY_RHOAI_READONLY_BOT_AUTH  Base64-encoded username:password for Quay API
    GITHUB_TOKEN                  GitHub API token for Dockerfile path checks
"""

import json
import re
import warnings
from pathlib import Path

import pytest
import yaml

import urllib.error
import urllib.request


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_param(spec, name):
    """Extract a parameter value from the PipelineRun spec."""
    for param in spec.get("params", []):
        if param.get("name") == name:
            return param.get("value")
    return None


def _detect_type(data):
    """Determine PipelineRun type: pull_request, push, scheduled, or None."""
    annotations = data.get("metadata", {}).get("annotations", {})
    cel_expr = annotations.get("pipelinesascode.tekton.dev/on-cel-expression", "")
    on_event = annotations.get("pipelinesascode.tekton.dev/on-event", "")
    name = data.get("metadata", {}).get("name", "")

    if "pull_request" in on_event:
        return "pull_request"
    if cel_expr and '"push"' in cel_expr:
        if "-on-schedule" in name:
            return "scheduled"
        return "push"
    return None


def _component_dir(filepath):
    """Extract component directory name from file path."""
    parts = Path(filepath).parts
    for i, part in enumerate(parts):
        if part == "pipelineruns" and i + 1 < len(parts):
            return parts[i + 1]
    return None


def _load(filepath):
    """Load PipelineRun YAML. Skip if unparseable (test_yaml_lint catches those)."""
    try:
        with open(filepath) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError:
        pytest.skip("YAML parse error (covered by test_yaml_lint)")
    if not isinstance(data, dict):
        pytest.skip("Not a YAML mapping (covered by test_yaml_lint)")
    if data.get("kind") != "PipelineRun":
        pytest.skip(f"Not a PipelineRun (kind={data.get('kind', 'missing')})")
    return data


# ---------------------------------------------------------------------------
# Quay API helpers
# ---------------------------------------------------------------------------

def _fetch_quay_catalog(quay_auth):
    """Fetch accessible repos via Docker v2 _catalog endpoint.

    Exchanges the base64 username:password for a no-scope bearer token via
    /v2/auth, then paginates through /v2/_catalog to collect all repo names.
    """
    try:
        auth_url = "https://quay.io/v2/auth?service=quay.io"
        headers = {"Authorization": f"Basic {quay_auth}"}
        req = urllib.request.Request(auth_url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=10)
        token = json.loads(resp.read().decode()).get("token", "")
        if not token:
            return None, "v2 auth returned empty token"
    except urllib.error.HTTPError as e:
        return None, f"v2 auth failed: HTTP {e.code}"
    except Exception as e:
        return None, f"v2 auth failed: {e}"

    repos = set()
    url = "https://quay.io/v2/_catalog?n=100"
    while url:
        try:
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {token}"}
            )
            resp = urllib.request.urlopen(req, timeout=30)
            data = json.loads(resp.read().decode())
            for name in data.get("repositories", []):
                repos.add(name)
            link = resp.headers.get("Link", "")
            if 'rel="next"' in link:
                next_path = link.split(">")[0].lstrip("<")
                if next_path.startswith("/"):
                    url = f"https://quay.io{next_path}"
                else:
                    url = next_path
            else:
                url = None
        except urllib.error.HTTPError as e:
            return None, f"_catalog failed: HTTP {e.code}"
        except Exception as e:
            err_str = str(e)
            if "next_page" in err_str:
                err_str = err_str.split("next_page")[0] + "next_page=<redacted>)"
            return None, f"_catalog failed: {err_str}"
    return repos, None


@pytest.fixture(scope="session")
def quay_catalog(quay_auth):
    """Fetch Quay catalog once per test session."""
    if not quay_auth:
        return None
    repos, err = _fetch_quay_catalog(quay_auth)
    if err:
        warnings.warn(f"Failed to fetch Quay catalog: {err}")
        return None
    return repos


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

def _github_headers(token):
    """Build GitHub API headers, with optional auth."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def _github_repo_accessible(repo_full, token):
    """Check if a GitHub repo is accessible."""
    api_url = f"https://api.github.com/repos/{repo_full}"
    req = urllib.request.Request(api_url, headers=_github_headers(token))
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except urllib.error.HTTPError as e:
        if e.code in (404, 403):
            return False
        return None
    except urllib.error.URLError:
        return None


def _github_file_exists(repo_full, filepath, token, ref=None):
    """Check if a file exists in a GitHub repo."""
    api_url = f"https://api.github.com/repos/{repo_full}/contents/{filepath}"
    if ref:
        api_url += f"?ref={ref}"
    req = urllib.request.Request(api_url, headers=_github_headers(token))
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        return None
    except urllib.error.URLError:
        return None


def _list_dockerfiles(repo_full, directory, token, ref=None):
    """List Dockerfile-like files in a GitHub repo directory."""
    path = directory.rstrip("/") if directory and directory != "." else ""
    api_url = f"https://api.github.com/repos/{repo_full}/contents/{path}"
    if ref:
        api_url += f"?ref={ref}"
    req = urllib.request.Request(api_url, headers=_github_headers(token))
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        entries = json.loads(resp.read().decode())
        if not isinstance(entries, list):
            return []
        return sorted(
            e["name"] for e in entries
            if e.get("type") == "file"
            and "dockerfile" in e.get("name", "").lower()
        )
    except (urllib.error.HTTPError, urllib.error.URLError):
        return []


# ---------------------------------------------------------------------------
# Check 1: YAML Linting
# ---------------------------------------------------------------------------

def test_yaml_lint(pipelinerun_file):
    """Validate file is parseable YAML containing a PipelineRun."""
    try:
        with open(pipelinerun_file) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        pytest.fail(f"Invalid YAML: {e}")

    assert data is not None, "File is empty"
    assert isinstance(data, dict), "File does not contain a YAML mapping"
    assert data.get("kind") == "PipelineRun", \
        f"Expected kind 'PipelineRun', got '{data.get('kind', 'missing')}'"


# ---------------------------------------------------------------------------
# Check 2: Name Convention
# ---------------------------------------------------------------------------

def test_name_convention(pipelinerun_file):
    """Validate PipelineRun name follows naming pattern."""
    data = _load(pipelinerun_file)
    pr_type = _detect_type(data)
    if pr_type is None:
        warnings.warn("Cannot determine PipelineRun type")
        return

    name = data.get("metadata", {}).get("name", "")
    assert name, "metadata.name is missing"

    if pr_type == "push":
        assert name.endswith("-on-push"), \
            f"Push PipelineRun name '{name}' must end with '-on-push'"
    elif pr_type == "scheduled":
        assert name.endswith("-on-schedule"), \
            f"Scheduled PipelineRun name '{name}' must end with '-on-schedule'"
    elif pr_type == "pull_request":
        assert "-on-pull-request" in name, \
            f"PR PipelineRun name '{name}' must contain '-on-pull-request'"


# ---------------------------------------------------------------------------
# Check 3: Name Consistency
# ---------------------------------------------------------------------------

def test_name_consistency(pipelinerun_file):
    """Validate name is consistent with component label."""
    data = _load(pipelinerun_file)
    pr_type = _detect_type(data)
    if pr_type is None:
        pytest.skip("Cannot determine PipelineRun type")

    name = data.get("metadata", {}).get("name", "")
    labels = data.get("metadata", {}).get("labels", {})
    component_label = labels.get("appstudio.openshift.io/component", "")
    comp_dir = _component_dir(pipelinerun_file)

    assert component_label, \
        "Label 'appstudio.openshift.io/component' is missing"

    if pr_type in ("push", "scheduled"):
        suffix = "-on-push" if pr_type == "push" else "-on-schedule"
        name_base = name.removesuffix(suffix)
        assert name_base.startswith(component_label), \
            f"{pr_type.capitalize()} name '{name}' should start with " \
            f"component label '{component_label}'"

    elif pr_type == "pull_request":
        base_name = name.split("-on-pull-request")[0]
        base_name = re.sub(r"\{\{.*?\}\}", "", base_name).strip("-")

        if component_label == "pull-request-pipelines":
            pass  # Generic label is acceptable
        elif component_label.startswith("pull-request-pipelines-"):
            label_suffix = component_label[len("pull-request-pipelines-"):]
            if (label_suffix not in base_name
                    and base_name not in label_suffix
                    and label_suffix != comp_dir):
                warnings.warn(
                    f"PR name base '{base_name}' may not match "
                    f"component label suffix '{label_suffix}'"
                )
        else:
            pytest.fail(
                f"PR component label '{component_label}' should start with "
                f"'pull-request-pipelines'"
            )


# ---------------------------------------------------------------------------
# Check 4: Branch and Repo Targeting
# ---------------------------------------------------------------------------

def test_branch_repo_targeting(pipelinerun_file, branch):
    """Validate push/scheduled PipelineRuns target correct branch and repo."""
    data = _load(pipelinerun_file)
    pr_type = _detect_type(data)
    if pr_type not in ("push", "scheduled"):
        pytest.skip(f"Not applicable for {pr_type} PipelineRuns")

    annotations = data.get("metadata", {}).get("annotations", {})
    cel_expr = annotations.get(
        "pipelinesascode.tekton.dev/on-cel-expression", ""
    )

    assert cel_expr, "Push PipelineRun missing on-cel-expression annotation"

    # Branch targeting
    if branch:
        branch_pattern = f'target_branch == "{branch}"'
        if branch_pattern not in cel_expr:
            actual_match = re.search(
                r'target_branch\s*==\s*"([^"]+)"', cel_expr
            )
            actual = actual_match.group(1) if actual_match else "not found"
            pytest.fail(
                f"CEL expression does not target branch '{branch}'. "
                f"Found target_branch='{actual}', "
                f"expected '{branch_pattern}' in expression"
            )

        # Version in name
        name = data.get("metadata", {}).get("name", "")
        branch_match = re.match(r"rhoai-(\d+)\.(\d+)$", branch)
        if branch_match and name:
            expected_version = (
                f"v{branch_match.group(1)}-{branch_match.group(2)}"
            )
            version_pattern = (
                re.escape(expected_version) + r"(?=-on-(?:push|schedule))"
            )
            if not re.search(version_pattern, name):
                actual_ver = re.search(
                    r"(v\d+-\d+(?:-[a-z]+[\d.-]*)*)-on-", name
                )
                actual_str = actual_ver.group(1) if actual_ver else "none"
                pytest.fail(
                    f"PipelineRun name '{name}' has version '{actual_str}', "
                    f"expected '{expected_version}' for branch '{branch}'"
                )

    # Repo annotation
    repo_url = annotations.get("build.appstudio.openshift.io/repo", "")
    assert repo_url, \
        "Annotation 'build.appstudio.openshift.io/repo' is missing"
    assert "?rev={{revision}}" in repo_url, \
        f"Repo annotation '{repo_url}' missing '?rev={{{{revision}}}}'"


# ---------------------------------------------------------------------------
# Check 5: CEL Self-Reference
# ---------------------------------------------------------------------------

def test_cel_self_reference(pipelinerun_file):
    """Validate CEL expression includes self-reference when filtering .tekton paths."""
    data = _load(pipelinerun_file)
    pr_type = _detect_type(data)
    if pr_type not in ("push", "scheduled"):
        pytest.skip(f"Not applicable for {pr_type} PipelineRuns")

    annotations = data.get("metadata", {}).get("annotations", {})
    cel_expr = annotations.get(
        "pipelinesascode.tekton.dev/on-cel-expression", ""
    )

    if not cel_expr or ".tekton" not in cel_expr:
        pytest.skip("CEL expression does not filter .tekton paths")

    filename = Path(pipelinerun_file).name
    expected_ref = f'".tekton/{filename}".pathChanged()'
    assert expected_ref in cel_expr, \
        f"CEL expression filters .tekton paths but does not reference itself. " \
        f"Expected '{expected_ref}' in expression"


# ---------------------------------------------------------------------------
# Check 6: Quay Repo Existence
# ---------------------------------------------------------------------------

def test_quay_repo_existence(pipelinerun_file, quay_auth, quay_catalog):
    """Validate output-image Quay repository exists."""
    data = _load(pipelinerun_file)

    output_image = _get_param(data.get("spec", {}), "output-image")
    assert output_image, "Parameter 'output-image' is missing"

    match = re.match(r"quay\.io/([^:]+)", output_image)
    assert match, \
        f"output-image '{output_image}' does not match quay.io pattern"

    repo_path = match.group(1)

    if not quay_auth:
        pytest.skip("QUAY_RHOAI_READONLY_BOT_AUTH not set")

    if quay_catalog is None:
        pytest.skip("Quay catalog not available")

    assert repo_path in quay_catalog, \
        f"Quay repository '{repo_path}' does not exist"


# ---------------------------------------------------------------------------
# Check 7: Quay Naming Convention
# ---------------------------------------------------------------------------

def test_quay_naming(pipelinerun_file):
    """Validate output-image naming convention."""
    data = _load(pipelinerun_file)
    pr_type = _detect_type(data)
    if pr_type is None:
        pytest.skip("Cannot determine PipelineRun type")

    output_image = _get_param(data.get("spec", {}), "output-image")
    if not output_image:
        pytest.skip("No output-image (covered by test_quay_repo_existence)")

    if pr_type == "pull_request":
        assert "quay.io/rhoai/pull-request-pipelines:" in output_image, \
            f"PR output-image '{output_image}' should use " \
            f"'quay.io/rhoai/pull-request-pipelines:' prefix"
        assert "{{revision}}" in output_image, \
            f"PR output-image '{output_image}' tag should include " \
            f"'{{{{revision}}}}'"

    elif pr_type in ("push", "scheduled"):
        assert output_image.startswith("quay.io/rhoai/"), \
            f"Push output-image '{output_image}' should be under " \
            f"'quay.io/rhoai/'"
        assert "pull-request-pipelines" not in output_image, \
            f"Push output-image '{output_image}' should not use " \
            f"'pull-request-pipelines' repo"
        tag_part = output_image.split(":")[-1] if ":" in output_image else ""
        if "{{target_branch}}" not in tag_part:
            warnings.warn(
                f"Push output-image tag '{tag_part}' typically includes "
                f"'{{{{target_branch}}}}'"
            )


# ---------------------------------------------------------------------------
# Check 8: Dockerfile Context Path
# ---------------------------------------------------------------------------

def test_dockerfile_path(pipelinerun_file, github_token, branch,
                         repo_access_cache):
    """Validate Dockerfile exists in the component's GitHub repository."""
    data = _load(pipelinerun_file)

    spec = data.get("spec", {})
    dockerfile = _get_param(spec, "dockerfile")
    path_context = _get_param(spec, "path-context") or "."

    assert dockerfile, "Parameter 'dockerfile' is missing"

    annotations = data.get("metadata", {}).get("annotations", {})
    repo_url = annotations.get("build.appstudio.openshift.io/repo", "")

    match = re.match(r"https://github\.com/([^/?]+/[^/?]+)", repo_url)
    if not match:
        warnings.warn(f"Cannot extract repo from annotation: '{repo_url}'")
        return

    repo_full = match.group(1)

    # Check repo accessibility (cached per session)
    if repo_full not in repo_access_cache:
        repo_access_cache[repo_full] = _github_repo_accessible(
            repo_full, github_token
        )

    accessible = repo_access_cache[repo_full]
    if accessible is False:
        warnings.warn(
            f"Repo '{repo_full}' is not accessible — skipping"
        )
        return
    if accessible is None:
        warnings.warn(
            f"Cannot verify repo '{repo_full}' — skipping"
        )
        return

    # Build candidate paths
    dockerfile_normalized = re.sub(r"^\./", "", dockerfile)
    if path_context != ".":
        context_normalized = path_context.rstrip("/")
        candidates = [
            f"{context_normalized}/{dockerfile_normalized}",
            dockerfile_normalized,
        ]
    else:
        candidates = [dockerfile_normalized]

    refs_to_check = [None]  # None = default branch
    if branch:
        refs_to_check.append(branch)

    for candidate in candidates:
        for ref in refs_to_check:
            exists = _github_file_exists(
                repo_full, candidate, github_token, ref=ref
            )
            if exists is True:
                return
            if exists is None:
                warnings.warn(
                    f"Cannot verify Dockerfile path in '{repo_full}': "
                    f"API error"
                )
                return

    # Not found — build helpful error message
    search_dir = path_context if path_context != "." else "."
    search_ref = branch if branch else None
    available = _list_dockerfiles(
        repo_full, search_dir, github_token, ref=search_ref
    )
    if not available and search_dir != ".":
        available = _list_dockerfiles(
            repo_full, ".", github_token, ref=search_ref
        )
        if available:
            search_dir = "."

    lines = [f"Dockerfile not found in repo '{repo_full}'"]
    if path_context != ".":
        lines.append(f"  path-context: {path_context}")
    lines.append(f"  dockerfile:    {dockerfile}")
    if branch:
        lines.append(f"  branches checked: default, {branch}")
    lines.append("  paths checked:")
    for c in candidates:
        lines.append(f"    - {c}")
    if available:
        lines.append(f"  available Dockerfiles in '{search_dir}':")
        for name in available:
            lines.append(f"    - {name}")

    pytest.fail("\n".join(lines))


# ---------------------------------------------------------------------------
# Check 9: Prefetch Input
# ---------------------------------------------------------------------------

def test_prefetch_input(pipelinerun_file):
    """Validate prefetch-input parameter is valid JSON or YAML sub-object."""
    data = _load(pipelinerun_file)

    spec = data.get("spec", {})
    prefetch_value = _get_param(spec, "prefetch-input")

    if prefetch_value is None:
        pytest.skip("No prefetch-input parameter")

    # YAML sub-object (parsed as dict or list by the YAML loader) is valid
    if isinstance(prefetch_value, (dict, list)):
        return

    assert isinstance(prefetch_value, str), \
        f"prefetch-input has unexpected type '{type(prefetch_value).__name__}'"

    assert prefetch_value.strip(), "prefetch-input is empty"

    try:
        parsed = json.loads(prefetch_value)
    except json.JSONDecodeError as e:
        pytest.fail(f"prefetch-input is not valid JSON: {e}")

    if isinstance(parsed, dict):
        pass
    elif isinstance(parsed, list):
        for i, item in enumerate(parsed):
            assert isinstance(item, dict), \
                f"prefetch-input[{i}] should be an object, " \
                f"got {type(item).__name__}"
    else:
        pytest.fail(
            f"prefetch-input should be a JSON object or array, "
            f"got {type(parsed).__name__}"
        )
