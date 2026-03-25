#!/usr/bin/env python3
"""Validate PipelineRun YAML files in the konflux-central repository.

See docs/validate-pipelineruns.md for full documentation on each check,
the security model, and instructions for adding new checks.

Implements automated checks per RHOAIENG-55175:
1. YAML linting
2. PipelineRun name convention
3. PipelineRun name consistency with component
4. Branch and repo targeting (push only)
5. CEL self-reference (push only)
6. Quay repo existence
7. Quay repo naming convention
8. Dockerfile context path existence in component repo
9. Prefetch input validation

Environment variables:
    QUAY_RHOAI_READONLY_BOT_AUTH  Base64-encoded username:password for Quay API
    GITHUB_TOKEN                  GitHub API token for Dockerfile path checks

Usage:
    python validate-pipelineruns.py --pipelinerun-dir pipelineruns/
    python validate-pipelineruns.py --pipelinerun-dir pipelineruns/ --branch rhoai-3.4
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml


class ValidationResult:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, check, message):
        self.errors.append(f"[{check}] {message}")

    def warn(self, check, message):
        self.warnings.append(f"[{check}] {message}")

    @property
    def ok(self):
        return len(self.errors) == 0


def load_yaml(filepath):
    """Check 1: YAML linting — validate the file is parseable YAML."""
    result = ValidationResult()
    try:
        with open(filepath) as f:
            data = yaml.safe_load(f)
        if data is None:
            result.error("yaml-lint", "File is empty")
            return None, result
        if not isinstance(data, dict):
            result.error("yaml-lint", "File does not contain a YAML mapping")
            return None, result
        return data, result
    except yaml.YAMLError as e:
        result.error("yaml-lint", f"Invalid YAML: {e}")
        return None, result


def get_param(spec, name):
    """Extract a parameter value from the PipelineRun spec."""
    for param in spec.get("params", []):
        if param.get("name") == name:
            return param.get("value")
    return None


def detect_pipelinerun_type(data):
    """Determine if this is a push, scheduled, or pull-request PipelineRun."""
    annotations = data.get("metadata", {}).get("annotations", {})
    cel_expr = annotations.get("pipelinesascode.tekton.dev/on-cel-expression", "")
    on_event = annotations.get("pipelinesascode.tekton.dev/on-event", "")
    name = data.get("metadata", {}).get("name", "")

    if "pull_request" in on_event:
        return "pull_request"
    if cel_expr and '"push"' in cel_expr:
        # Scheduled PipelineRuns also use event=="push" but have "-on-schedule" names
        if "-on-schedule" in name:
            return "scheduled"
        return "push"
    return None


def check_name_convention(data, pr_type, result):
    """Check 2: PipelineRun name matches expected pattern."""
    name = data.get("metadata", {}).get("name", "")
    if not name:
        result.error("name-convention", "metadata.name is missing")
        return

    if pr_type == "push":
        if not name.endswith("-on-push"):
            result.error("name-convention",
                         f"Push PipelineRun name '{name}' must end with '-on-push'")
    elif pr_type == "scheduled":
        if not name.endswith("-on-schedule"):
            result.error("name-convention",
                         f"Scheduled PipelineRun name '{name}' must end with "
                         f"'-on-schedule'")
    elif pr_type == "pull_request":
        if "-on-pull-request" not in name:
            result.error("name-convention",
                         f"PR PipelineRun name '{name}' must contain '-on-pull-request'")


def check_name_consistency(data, pr_type, component_dir, result):
    """Check 3: PipelineRun name is consistent with the component name."""
    name = data.get("metadata", {}).get("name", "")
    labels = data.get("metadata", {}).get("labels", {})
    component_label = labels.get("appstudio.openshift.io/component", "")

    if not component_label:
        result.error("name-consistency",
                     "Label 'appstudio.openshift.io/component' is missing")
        return

    if pr_type in ("push", "scheduled"):
        # Push/scheduled: component label should appear in the name
        # e.g., name=odh-dashboard-v3-4-on-push, component=odh-dashboard-v3-4
        suffix = "-on-push" if pr_type == "push" else "-on-schedule"
        name_base = name.removesuffix(suffix)
        if not name_base.startswith(component_label):
            result.error("name-consistency",
                         f"{pr_type.capitalize()} name '{name}' should start with "
                         f"component label '{component_label}'")
    elif pr_type == "pull_request":
        # PR: component label is "pull-request-pipelines-{base}" or "pull-request-pipelines"
        base_name = name.split("-on-pull-request")[0]
        # Remove template variables like {{target_branch}}
        base_name = re.sub(r"\{\{.*?\}\}", "", base_name).strip("-")

        if component_label == "pull-request-pipelines":
            # Generic component label is acceptable (e.g., distributed-workloads)
            pass
        elif component_label.startswith("pull-request-pipelines-"):
            label_suffix = component_label[len("pull-request-pipelines-"):]
            # The label suffix may abbreviate the name or use the directory name
            # for components with many PipelineRuns (e.g., notebooks).
            # Accept if: suffix is in the name, name is in the suffix, or suffix
            # matches the component directory.
            if (label_suffix not in base_name
                    and base_name not in label_suffix
                    and label_suffix != component_dir):
                result.warn("name-consistency",
                            f"PR name base '{base_name}' may not match component "
                            f"label suffix '{label_suffix}'")
        else:
            result.error("name-consistency",
                         f"PR component label '{component_label}' should start with "
                         f"'pull-request-pipelines'")


def check_branch_repo_targeting(data, branch, component_dir, result):
    """Check 4: Push PipelineRun targets the correct branch and repository.

    Only applies to push PipelineRuns.
    """
    annotations = data.get("metadata", {}).get("annotations", {})
    cel_expr = annotations.get("pipelinesascode.tekton.dev/on-cel-expression", "")

    if not cel_expr:
        result.error("branch-repo-targeting",
                     "Push PipelineRun missing on-cel-expression annotation")
        return

    # Check branch targeting
    if branch:
        branch_pattern = f'target_branch == "{branch}"'
        if branch_pattern not in cel_expr:
            # Extract the actual target_branch from the CEL expression
            actual_match = re.search(r'target_branch\s*==\s*"([^"]+)"', cel_expr)
            actual = actual_match.group(1) if actual_match else "not found"
            result.error("branch-repo-targeting",
                         f"CEL expression does not target branch '{branch}'. "
                         f"Found target_branch='{actual}', "
                         f"expected '{branch_pattern}' in expression")

        # Check that the PipelineRun name contains the correct version
        # e.g., branch "rhoai-3.4" -> version "v3-4" in the name
        # Must be an exact version match: "v3-4" should match but not "v3-4-ea-2"
        name = data.get("metadata", {}).get("name", "")
        branch_match = re.match(r"rhoai-(\d+)\.(\d+)$", branch)
        if branch_match and name:
            expected_version = f"v{branch_match.group(1)}-{branch_match.group(2)}"
            # Check the version appears in the name followed by -on-push/-on-schedule
            # (not by -ea, -rc, or other suffixes)
            version_pattern = re.escape(expected_version) + r"(?=-on-(?:push|schedule))"
            if not re.search(version_pattern, name):
                # Extract what version is actually in the name
                actual_ver = re.search(r"(v\d+-\d+(?:-[a-z]+[\d.-]*)*)-on-", name)
                actual_str = actual_ver.group(1) if actual_ver else "none"
                result.error("branch-repo-targeting",
                             f"PipelineRun name '{name}' has version '{actual_str}', "
                             f"expected '{expected_version}' for branch '{branch}'")

    # Check repo annotation
    repo_url = annotations.get("build.appstudio.openshift.io/repo", "")
    if not repo_url:
        result.error("branch-repo-targeting",
                     "Annotation 'build.appstudio.openshift.io/repo' is missing")
    elif "?rev={{revision}}" not in repo_url:
        result.error("branch-repo-targeting",
                     f"Repo annotation '{repo_url}' missing '?rev={{{{revision}}}}'")


def check_cel_self_reference(data, filepath, result):
    """Check 5: CEL expression references the pipeline file itself via pathChanged().

    Only applies to push PipelineRuns. If the CEL expression filters on
    .tekton/** paths, it must include a self-reference so the pipeline
    triggers when its own definition changes.
    """
    annotations = data.get("metadata", {}).get("annotations", {})
    cel_expr = annotations.get("pipelinesascode.tekton.dev/on-cel-expression", "")

    if not cel_expr:
        # Already reported by check_branch_repo_targeting
        return

    # Only check self-reference when the CEL expression filters .tekton paths.
    # Some PipelineRuns trigger on all pushes without path filtering — that's valid.
    if ".tekton" not in cel_expr:
        return

    filename = Path(filepath).name
    expected_ref = f'".tekton/{filename}".pathChanged()'
    if expected_ref not in cel_expr:
        result.error("cel-self-reference",
                     f"CEL expression filters .tekton paths but does not "
                     f"reference itself. Expected '{expected_ref}' in expression")


def check_quay_repo_existence(data, pr_type, quay_auth, result):
    """Check 6: Referenced Quay repository exists."""
    output_image = get_param(data.get("spec", {}), "output-image")
    if not output_image:
        result.error("quay-repo-existence", "Parameter 'output-image' is missing")
        return

    # Extract the quay.io repo path (without tag)
    # e.g., quay.io/rhoai/pull-request-pipelines:tag -> rhoai/pull-request-pipelines
    match = re.match(r"quay\.io/([^:]+)", output_image)
    if not match:
        result.error("quay-repo-existence",
                     f"output-image '{output_image}' does not match quay.io pattern")
        return

    repo_path = match.group(1)

    if not quay_auth:
        return

    # For PR images, the repo is always "rhoai/pull-request-pipelines"
    # For push images, the repo is "rhoai/{component-image-name}"
    api_url = f"https://quay.io/api/v1/repository/{repo_path}"

    headers = {"Authorization": f"Basic {quay_auth}"}

    req = urllib.request.Request(api_url, headers=headers)
    try:
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            result.error("quay-repo-existence",
                         f"Quay repository '{repo_path}' does not exist")
        elif e.code in (401, 403):
            result.warn("quay-repo-existence",
                        f"Cannot verify Quay repository '{repo_path}': "
                        f"authentication error ({e.code})")
        else:
            result.warn("quay-repo-existence",
                        f"Cannot verify Quay repository '{repo_path}': "
                        f"HTTP {e.code}")
    except urllib.error.URLError as e:
        result.warn("quay-repo-existence",
                    f"Cannot reach Quay API to verify '{repo_path}': {e.reason}")


def check_quay_naming_convention(data, pr_type, result):
    """Check 7: Quay repository name follows expected naming convention."""
    output_image = get_param(data.get("spec", {}), "output-image")
    if not output_image:
        # Already reported by check_quay_repo_existence
        return

    if pr_type == "pull_request":
        # PR images should use the pull-request-pipelines repo
        if "quay.io/rhoai/pull-request-pipelines:" not in output_image:
            result.error("quay-naming",
                         f"PR output-image '{output_image}' should use "
                         f"'quay.io/rhoai/pull-request-pipelines:' prefix")
        # Tag should include {{revision}}
        if "{{revision}}" not in output_image:
            result.error("quay-naming",
                         f"PR output-image '{output_image}' tag should include "
                         f"'{{{{revision}}}}'")

    elif pr_type in ("push", "scheduled"):
        # Push/scheduled images should be under quay.io/rhoai/
        if not output_image.startswith("quay.io/rhoai/"):
            result.error("quay-naming",
                         f"Push output-image '{output_image}' should be under "
                         f"'quay.io/rhoai/'")
        # Push/scheduled images should NOT use pull-request-pipelines
        if "pull-request-pipelines" in output_image:
            result.error("quay-naming",
                         f"Push output-image '{output_image}' should not use "
                         f"'pull-request-pipelines' repo")
        # Tag should reference {{target_branch}}
        tag_part = output_image.split(":")[-1] if ":" in output_image else ""
        if "{{target_branch}}" not in tag_part:
            result.warn("quay-naming",
                        f"Push output-image tag '{tag_part}' typically includes "
                        f"'{{{{target_branch}}}}'")


def check_prefetch_input(data, result):
    """Check 9: Validate prefetch-input param is valid JSON or a YAML sub-object."""
    spec = data.get("spec", {})
    prefetch_value = get_param(spec, "prefetch-input")

    if prefetch_value is None:
        # Parameter not present — not all PipelineRuns use prefetch
        return

    # YAML sub-object (parsed as dict or list by the YAML loader) is valid
    if isinstance(prefetch_value, (dict, list)):
        return

    if not isinstance(prefetch_value, str):
        result.error("prefetch-input",
                     f"prefetch-input has unexpected type '{type(prefetch_value).__name__}'")
        return

    # Empty string is not valid
    if not prefetch_value.strip():
        result.error("prefetch-input", "prefetch-input is empty")
        return

    # String value — must be valid JSON
    try:
        parsed = json.loads(prefetch_value)
    except json.JSONDecodeError as e:
        result.error("prefetch-input",
                     f"prefetch-input is not valid JSON: {e}")
        return

    # Must be a JSON object or array of objects
    if isinstance(parsed, dict):
        pass  # single prefetch config
    elif isinstance(parsed, list):
        for i, item in enumerate(parsed):
            if not isinstance(item, dict):
                result.error("prefetch-input",
                             f"prefetch-input[{i}] should be an object, "
                             f"got {type(item).__name__}")
    else:
        result.error("prefetch-input",
                     f"prefetch-input should be a JSON object or array, "
                     f"got {type(parsed).__name__}")


def _github_repo_accessible(repo_full, github_token):
    """Check if a GitHub repo is accessible. Returns True, False, or None (error)."""
    api_url = f"https://api.github.com/repos/{repo_full}"
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    req = urllib.request.Request(api_url, headers=headers)
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False  # private or doesn't exist
        return None
    except urllib.error.URLError:
        return None


# Cache repo accessibility checks to avoid redundant API calls
_repo_access_cache = {}


def _check_repo_access(repo_full, github_token):
    """Check repo accessibility with caching. Returns True/False/None."""
    if repo_full not in _repo_access_cache:
        _repo_access_cache[repo_full] = _github_repo_accessible(
            repo_full, github_token
        )
    return _repo_access_cache[repo_full]


def _github_file_exists(repo_full, filepath, github_token, ref=None):
    """Check if a file exists in a GitHub repo. Returns True, False, or None (unknown)."""
    api_url = f"https://api.github.com/repos/{repo_full}/contents/{filepath}"
    if ref:
        api_url += f"?ref={ref}"
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    req = urllib.request.Request(api_url, headers=headers)
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        return None  # auth or other error — can't determine
    except urllib.error.URLError:
        return None


def _list_dockerfiles(repo_full, directory, github_token, ref=None):
    """List Dockerfile-like files in a GitHub repo directory. Returns a list of names."""
    path = directory.rstrip("/") if directory and directory != "." else ""
    api_url = f"https://api.github.com/repos/{repo_full}/contents/{path}"
    if ref:
        api_url += f"?ref={ref}"
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    req = urllib.request.Request(api_url, headers=headers)
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


def check_dockerfile_context_path(data, component_dir, github_token, branch, result):
    """Check 8: Dockerfile context path exists in the component repository.

    The dockerfile param may be relative to the repo root or to path-context,
    depending on the PipelineRun. We check both resolutions.
    For push/scheduled PipelineRuns, we also check the target branch.
    """
    spec = data.get("spec", {})
    dockerfile = get_param(spec, "dockerfile")
    path_context = get_param(spec, "path-context") or "."

    if not dockerfile:
        result.error("dockerfile-path", "Parameter 'dockerfile' is missing")
        return

    annotations = data.get("metadata", {}).get("annotations", {})
    repo_url = annotations.get("build.appstudio.openshift.io/repo", "")

    match = re.match(r"https://github\.com/([^/?]+/[^/?]+)", repo_url)
    if not match:
        result.warn("dockerfile-path",
                    f"Cannot extract repo from annotation: '{repo_url}'")
        return

    repo_full = match.group(1)
    if not github_token:
        return

    # Check if the repo is accessible before checking files
    accessible = _check_repo_access(repo_full, github_token)
    if accessible is False:
        result.warn("dockerfile-path",
                    f"Repo '{repo_full}' is not accessible (private or does not "
                    f"exist) — skipping Dockerfile path check")
        return
    if accessible is None:
        result.warn("dockerfile-path",
                    f"Cannot verify repo '{repo_full}' accessibility — "
                    f"skipping Dockerfile path check")
        return

    # Normalize the dockerfile path (strip leading ./)
    dockerfile_normalized = re.sub(r"^\./", "", dockerfile)

    # Build candidate paths to check:
    # 1. path-context/dockerfile (dockerfile relative to build context) — preferred
    # 2. dockerfile as-is (relative to repo root) — fallback
    if path_context != ".":
        context_normalized = path_context.rstrip("/")
        combined = f"{context_normalized}/{dockerfile_normalized}"
        candidates = [combined, dockerfile_normalized]
    else:
        candidates = [dockerfile_normalized]

    # Determine which branch(es) to check:
    # - Always check default branch
    # - For release-branch PipelineRuns, also check the target branch
    refs_to_check = [None]  # None = default branch
    if branch:
        refs_to_check.append(branch)

    for candidate in candidates:
        for ref in refs_to_check:
            exists = _github_file_exists(repo_full, candidate, github_token, ref=ref)
            if exists is True:
                return  # Found it
            if exists is None:
                result.warn("dockerfile-path",
                            f"Cannot verify Dockerfile path in '{repo_full}': "
                            f"API error")
                return

    # List available Dockerfiles to help the user pick the right one
    search_dir = path_context if path_context != "." else "."
    search_ref = branch if branch else None
    available = _list_dockerfiles(repo_full, search_dir, github_token, ref=search_ref)
    if not available and search_dir != ".":
        available = _list_dockerfiles(repo_full, ".", github_token, ref=search_ref)
        if available:
            search_dir = "."

    lines = [f"Dockerfile not found in repo '{repo_full}'"]
    if path_context != ".":
        lines.append(f"  path-context: {path_context}")
    lines.append(f"  dockerfile:    {dockerfile}")
    if branch:
        lines.append(f"  branches checked: default, {branch}")
    lines.append(f"  paths checked:")
    for c in candidates:
        lines.append(f"    - {c}")
    if available:
        lines.append(f"  available Dockerfiles in '{search_dir}':")
        for name in available:
            lines.append(f"    - {name}")

    result.error("dockerfile-path", "\n".join(lines))


def validate_pipelinerun(filepath, branch, quay_auth, github_token):
    """Run all applicable checks on a single PipelineRun file."""
    data, result = load_yaml(filepath)
    if data is None:
        return result

    # Verify this is actually a PipelineRun
    kind = data.get("kind", "")
    if kind != "PipelineRun":
        result.error("yaml-lint", f"Expected kind 'PipelineRun', got '{kind}'")
        return result

    pr_type = detect_pipelinerun_type(data)
    if pr_type is None:
        result.warn("yaml-lint",
                     "Cannot determine PipelineRun type (push vs pull_request)")
        # Default to checking common checks only
        pr_type = "unknown"

    # Extract component directory name from file path
    # e.g., pipelineruns/odh-dashboard/.tekton/foo.yaml -> odh-dashboard
    path_parts = Path(filepath).parts
    component_dir = None
    for i, part in enumerate(path_parts):
        if part == "pipelineruns" and i + 1 < len(path_parts):
            component_dir = path_parts[i + 1]
            break

    # Check 2: Name convention (all types)
    check_name_convention(data, pr_type, result)

    # Check 3: Name consistency (push and scheduled only)
    if pr_type in ("push", "scheduled"):
        check_name_consistency(data, pr_type, component_dir, result)

    # Check 4: Branch/repo targeting (push and scheduled)
    if pr_type in ("push", "scheduled"):
        check_branch_repo_targeting(data, branch, component_dir, result)

    # Check 5: CEL self-reference (push and scheduled — only if .tekton filtering used)
    if pr_type in ("push", "scheduled"):
        check_cel_self_reference(data, filepath, result)

    # Check 6: Quay repo existence (all types)
    check_quay_repo_existence(data, pr_type, quay_auth, result)

    # Check 7: Quay naming convention (all types)
    check_quay_naming_convention(data, pr_type, result)

    # Check 8: Dockerfile context path (all types)
    check_dockerfile_context_path(data, component_dir, github_token, branch, result)

    # Check 9: Prefetch input validation (all types)
    check_prefetch_input(data, result)

    # Prepend filepath to all messages so they are self-contained
    result.errors = [f"{filepath}: {e}" for e in result.errors]
    result.warnings = [f"{filepath}: {w}" for w in result.warnings]

    return result


def find_pipelinerun_files(pipelinerun_dir):
    """Find all PipelineRun YAML files under the given directory."""
    files = []
    base = Path(pipelinerun_dir)
    for pattern in ("**/*.yaml", "**/*.yml"):
        for f in base.glob(pattern):
            # Skip non-.tekton files (e.g., README.md.in)
            if ".tekton" in f.parts:
                files.append(f)
    return sorted(files)


def _escape_gh_actions(msg):
    """Escape a message for GitHub Actions workflow commands."""
    return msg.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _output_github_actions(all_results, files, total_errors, total_warnings):
    """Output results as GitHub Actions annotations and a job summary."""
    # Emit ::error and ::warning annotations (show inline on PR files)
    for path, r in all_results.items():
        for err in r.errors:
            print(f"::error file={path}::{_escape_gh_actions(err)}")
        for warn in r.warnings:
            print(f"::warning file={path}::{_escape_gh_actions(warn)}")

    # Write a markdown summary to GITHUB_STEP_SUMMARY (shows on the checks page)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if summary_path:
        lines = []
        status = "PASSED" if total_errors == 0 else "FAILED"
        emoji = "\u2705" if total_errors == 0 else "\u274c"
        lines.append(f"## {emoji} PipelineRun Validation: {status}\n")
        lines.append(f"**{len(files)}** files checked | "
                     f"**{total_errors}** error(s) | "
                     f"**{total_warnings}** warning(s)\n")

        # List files with issues
        problem_files = {p: r for p, r in all_results.items()
                         if r.errors or r.warnings}
        if problem_files:
            lines.append("### Issues\n")
            for path, r in problem_files.items():
                lines.append(f"<details>\n<summary><code>{path}</code> — "
                             f"{len(r.errors)} error(s), "
                             f"{len(r.warnings)} warning(s)</summary>\n")
                if r.errors:
                    lines.append("**Errors:**\n")
                    for err in r.errors:
                        lines.append(f"- {err}\n")
                if r.warnings:
                    lines.append("**Warnings:**\n")
                    for warn in r.warnings:
                        lines.append(f"- {warn}\n")
                lines.append("</details>\n")

        with open(summary_path, "a") as f:
            f.writelines(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Validate PipelineRun files in konflux-central"
    )
    parser.add_argument(
        "--pipelinerun-dir",
        default="pipelineruns",
        help="Directory containing PipelineRun files (default: pipelineruns/)",
    )
    parser.add_argument(
        "--branch",
        default=None,
        help="Expected target branch for push PipelineRuns (e.g., rhoai-3.4)",
    )
    parser.add_argument(
        "--output",
        choices=["text", "json", "github-actions"],
        default="text",
        help="Output format (default: text)",
    )
    args = parser.parse_args()

    # Auth tokens from environment
    quay_auth = os.environ.get("QUAY_RHOAI_READONLY_BOT_AUTH", "")
    github_token = os.environ.get("GITHUB_TOKEN", "")

    if not quay_auth:
        msg = "QUAY_RHOAI_READONLY_BOT_AUTH not set — Quay repo existence checks will be skipped"
        if args.output == "github-actions":
            print(f"::warning::{msg}\n")
        else:
            print(f"WARNING: {msg}\n")
    if not github_token:
        msg = "GITHUB_TOKEN not set — Dockerfile path checks will be skipped"
        if args.output == "github-actions":
            print(f"::warning::{msg}\n")
        else:
            print(f"WARNING: {msg}\n")

    files = find_pipelinerun_files(args.pipelinerun_dir)
    if not files:
        print(f"No PipelineRun files found in {args.pipelinerun_dir}")
        sys.exit(1)

    all_results = {}
    total_errors = 0
    total_warnings = 0

    for filepath in files:
        result = validate_pipelinerun(
            str(filepath), args.branch, quay_auth, github_token
        )
        rel_path = str(filepath)
        all_results[rel_path] = result
        total_errors += len(result.errors)
        total_warnings += len(result.warnings)

    if args.output == "json":
        output = {
            "summary": {
                "files_checked": len(files),
                "total_errors": total_errors,
                "total_warnings": total_warnings,
                "passed": total_errors == 0,
            },
            "results": {
                path: {"errors": r.errors, "warnings": r.warnings}
                for path, r in all_results.items()
            },
        }
        print(json.dumps(output, indent=2))
    elif args.output == "github-actions":
        _output_github_actions(all_results, files, total_errors, total_warnings)
    else:
        print(f"Validating {len(files)} PipelineRun file(s)...\n")
        for path, r in all_results.items():
            for err in r.errors:
                print(f"  ERROR: {err}")
            for warn in r.warnings:
                print(f"  WARNING: {warn}")
        if total_errors or total_warnings:
            print()

        print(f"Summary: {len(files)} files checked, "
              f"{total_errors} error(s), {total_warnings} warning(s)")

        if total_errors > 0:
            print("\nValidation FAILED")
        else:
            print("\nValidation PASSED")

    sys.exit(1 if total_errors > 0 else 0)


if __name__ == "__main__":
    main()
