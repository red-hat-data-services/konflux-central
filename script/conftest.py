"""Pytest configuration for PipelineRun validation.

Custom options:
    --pipelinerun-dir  Directory containing PipelineRun YAML files (default: pipelineruns)
    --branch           Expected target branch for push PipelineRuns (e.g., rhoai-3.4)

Environment variables:
    QUAY_RHOAI_READONLY_BOT_AUTH  Base64-encoded username:password for Quay API
    GITHUB_TOKEN                  GitHub API token for Dockerfile path checks
"""

import os
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption("--pipelinerun-dir", default="pipelineruns",
                     help="Directory containing PipelineRun YAML files")
    parser.addoption("--branch", default=None,
                     help="Expected target branch (e.g., rhoai-3.4)")
    parser.addoption("--validation-comment-file", default=None,
                     help="Path to write PR comment markdown body on failure")


def _find_pipelinerun_files(pipelinerun_dir):
    """Find all PipelineRun YAML files under the given directory."""
    base = Path(pipelinerun_dir)
    files = []
    for pattern in ("**/*.yaml", "**/*.yml"):
        for f in base.glob(pattern):
            if ".tekton" in f.parts:
                files.append(f)
    return sorted(files)


def pytest_generate_tests(metafunc):
    """Parametrize tests over discovered PipelineRun YAML files."""
    if "pipelinerun_file" in metafunc.fixturenames:
        pr_dir = metafunc.config.getoption("--pipelinerun-dir")
        files = _find_pipelinerun_files(pr_dir)
        if not files:
            return
        base = Path(pr_dir)
        ids = []
        for f in files:
            try:
                ids.append(str(f.relative_to(base)))
            except ValueError:
                ids.append(str(f))
        metafunc.parametrize("pipelinerun_file", [str(f) for f in files], ids=ids)


@pytest.fixture(scope="session")
def branch(request):
    return request.config.getoption("--branch")


@pytest.fixture(scope="session")
def quay_auth():
    return os.environ.get("QUAY_RHOAI_READONLY_BOT_AUTH", "")


@pytest.fixture(scope="session")
def github_token():
    return os.environ.get("GITHUB_TOKEN", "")


@pytest.fixture(scope="session")
def repo_access_cache():
    """Session-scoped cache for GitHub repo accessibility checks."""
    return {}


def _escape_gh_actions(msg):
    return msg.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Emit GitHub Actions ::error annotations for failed tests."""
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" or not report.failed:
        return
    if not os.environ.get("GITHUB_ACTIONS"):
        return

    pipelinerun_file = ""
    if hasattr(item, "callspec"):
        pipelinerun_file = item.callspec.params.get("pipelinerun_file", "")

    msg = ""
    if report.longreprtext:
        lines = report.longreprtext.strip().split("\n")
        msg = lines[-1].replace("AssertionError: ", "").replace("Failed: ", "")

    test_name = item.originalname or item.name
    escaped = _escape_gh_actions(msg)
    if pipelinerun_file:
        print(f"\n::error file={pipelinerun_file}::{test_name}: {escaped}")
    else:
        print(f"\n::error::{test_name}: {escaped}")


# Maps check names to YAML keys/patterns to search for in the file.
# The first match found is used for the snippet.
_CHECK_SEARCH_KEYS = {
    "test_yaml_lint": [r"^\s*kind:"],
    "test_name_convention": [r"^\s+name:"],
    "test_name_consistency": [r"appstudio\.openshift\.io/component:"],
    "test_branch_repo_targeting": [
        r"on-cel-expression:",
        r"build\.appstudio\.openshift\.io/repo:",
    ],
    "test_cel_self_reference": [r"on-cel-expression:"],
    "test_quay_repo_existence": [r"output-image"],
    "test_quay_naming": [r"output-image"],
    "test_dockerfile_path": [r"dockerfile", r"path-context"],
    "test_prefetch_input": [r"prefetch-input"],
}


def _extract_snippet(filepath, check_name, context=1):
    """Read a YAML file and return lines around the key relevant to check_name.

    Returns (snippet_str, matched_line_number) where snippet_str has line
    numbers and a > marker, e.g.:
        3 |   kind: Deployment
    Returns ("", None) if the file can't be read or no match is found.
    """
    patterns = _CHECK_SEARCH_KEYS.get(check_name, [])
    if not patterns:
        return "", None
    try:
        file_lines = Path(filepath).read_text().splitlines()
    except OSError:
        return "", None

    for pattern in patterns:
        for i, line in enumerate(file_lines):
            if re.search(pattern, line, re.IGNORECASE):
                start = max(0, i - context)
                end = min(len(file_lines), i + context + 1)
                snippet_lines = []
                for j in range(start, end):
                    marker = ">" if j == i else " "
                    snippet_lines.append(
                        f"  {marker} {j + 1:4d} | {file_lines[j]}"
                    )
                return "\n".join(snippet_lines), i + 1
    return "", None


def _extract_error_message(longreprtext):
    """Extract a concise error message from pytest's longreprtext.

    For multi-line failures (e.g. test_dockerfile_path), returns all
    E-lines from the Failed:/AssertionError: line onward.
    """
    if not longreprtext:
        return ""
    text_lines = longreprtext.strip().split("\n")
    e_lines = []
    for line in text_lines:
        stripped = line.lstrip()
        if stripped.startswith("E "):
            e_lines.append(stripped[2:].strip())
    if not e_lines:
        return text_lines[-1].strip()

    # Find the index of the Failed:/AssertionError: line
    start_idx = None
    for i, e_line in enumerate(e_lines):
        if e_line.startswith(("Failed:", "AssertionError:", "AssertError:")):
            start_idx = i
            break

    if start_idx is not None:
        # Take lines from the assertion line, but stop at pytest diff noise
        msg_lines = [e_lines[start_idx]]
        for e_line in e_lines[start_idx + 1:]:
            if e_line.startswith("assert "):
                break
            msg_lines.append(e_line)
    else:
        msg_lines = e_lines[:1]

    # Strip prefix from the first line
    for prefix in ("AssertionError: ", "AssertError: ",
                   "Failed: ", "FAILED: "):
        msg_lines[0] = msg_lines[0].removeprefix(prefix)
    msg_lines[0] = msg_lines[0].strip()

    return "\n".join(msg_lines)


def _build_test_line_map(test_file):
    """Build a mapping of test function names to their line numbers."""
    line_map = {}
    try:
        for i, line in enumerate(Path(test_file).read_text().splitlines(), 1):
            m = re.match(r"def (test_\w+)\(", line)
            if m:
                line_map[m.group(1)] = i
    except OSError:
        pass
    return line_map


def _make_check_ref(check_name, blob_url_prefix, test_file, test_line_map):
    """Build a markdown reference for a check name, linked if possible."""
    line_no = test_line_map.get(check_name)
    if blob_url_prefix and test_file and line_no:
        url = f"{blob_url_prefix}/{test_file}#L{line_no}"
        return f"[`{check_name}`]({url})"
    return f"`{check_name}`"


def _make_file_ref(file_param, pipelinerun_dir, blob_url_prefix,
                   line_no=None, basename_only=False):
    """Build a markdown reference for a pipelinerun file, linked if possible."""
    display = os.path.basename(file_param) if basename_only else file_param
    if blob_url_prefix and file_param and pipelinerun_dir:
        file_path = os.path.join(pipelinerun_dir, file_param)
        file_url = f"{blob_url_prefix}/{file_path}"
        if line_no:
            file_url += f"#L{line_no}"
        return f"[`{display}`]({file_url})"
    return f"`{display}`"


def _build_failure_summary(stats, exitstatus, pipelinerun_dir,
                           run_url=None, blob_url_prefix=None,
                           commit_sha=None):
    """Build a markdown summary of validation results.

    Args:
        stats: pytest terminal reporter stats dict
        exitstatus: pytest exit status code
        pipelinerun_dir: path to the pipelinerun directory (for snippets)
        run_url: link to the full CI run logs
        blob_url_prefix: GitHub blob URL prefix for file links, e.g.
            https://github.com/owner/repo/blob/abc123

    Returns the summary lines as a list of strings, or None if there are
    no failures.
    """
    passed = len(stats.get("passed", []))
    failed = len(stats.get("failed", []))
    skipped = len(stats.get("skipped", []))

    if exitstatus == 0:
        return None

    # Discover the test source file from nodeids and build line map
    test_file = ""
    all_reports = stats.get("failed", []) + stats.get("skipped", [])
    for report in all_reports:
        if "::" in report.nodeid:
            test_file = report.nodeid.split("::")[0]
            break
    test_line_map = _build_test_line_map(test_file) if test_file else {}

    # Group failures by check name
    failures_by_check = {}
    for report in stats.get("failed", []):
        # nodeid format: script/test_validate_pipelineruns.py::test_name[param]
        parts = report.nodeid.split("::")
        test_part = parts[-1] if len(parts) > 1 else report.nodeid
        bracket_idx = test_part.find("[")
        if bracket_idx != -1:
            check_name = test_part[:bracket_idx]
            file_param = test_part[bracket_idx + 1:].rstrip("]")
        else:
            check_name = test_part
            file_param = ""

        msg = _extract_error_message(report.longreprtext)

        # Build the full file path to extract a snippet
        snippet = ""
        line_no = None
        if file_param and pipelinerun_dir:
            full_path = os.path.join(pipelinerun_dir, file_param)
            snippet, line_no = _extract_snippet(full_path, check_name)

        failures_by_check.setdefault(check_name, []).append(
            (file_param, msg, snippet, line_no)
        )

    lines = [
        "## :x: PipelineRun Validation Failed\n\n",
        f"**{passed}** passed | **{failed}** failed | **{skipped}** skipped\n\n",
    ]

    for check_name, file_failures in failures_by_check.items():
        check_ref = _make_check_ref(
            check_name, blob_url_prefix, test_file, test_line_map
        )
        lines.append(f"### {check_ref}\n\n")
        for file_param, msg, snippet, line_no in file_failures:
            file_ref = _make_file_ref(
                file_param, pipelinerun_dir, blob_url_prefix, line_no
            )
            lines.append(f"- {file_ref}\n\n")
            if "\n" in msg:
                # Multi-line message: render as a fenced block under the bullet
                lines.append(f"  ```\n")
                for msg_line in msg.split("\n"):
                    lines.append(f"  {msg_line}\n")
                lines.append(f"  ```\n\n")
            else:
                lines.append(f"  {msg}\n\n")
            if snippet:
                # Indent the details block so it nests under the bullet
                lines.append("  <details>\n")
                lines.append("  <summary>Details</summary>\n\n")
                lines.append(f"  ```yaml\n{snippet}\n  ```\n\n")
                lines.append("  </details>\n\n")

    # Skipped tests summary
    skipped_reports = stats.get("skipped", [])
    if skipped_reports:
        # Group by reason, collecting (check_name, file_param) per reason
        skips_by_reason = {}
        for report in skipped_reports:
            reason = ""
            if isinstance(report.longrepr, tuple) and len(report.longrepr) > 2:
                reason = report.longrepr[2]
            reason = reason.removeprefix("Skipped: ").strip() or "Unknown"

            parts = report.nodeid.split("::")
            test_part = parts[-1] if len(parts) > 1 else report.nodeid
            bracket_idx = test_part.find("[")
            if bracket_idx != -1:
                check_name = test_part[:bracket_idx]
                file_param = test_part[bracket_idx + 1:].rstrip("]")
            else:
                check_name = test_part
                file_param = ""
            skips_by_reason.setdefault(reason, []).append(
                (check_name, file_param)
            )

        lines.append("<details>\n")
        lines.append(f"<summary>Skipped tests ({skipped})</summary>\n\n")
        for reason, entries in skips_by_reason.items():
            lines.append(f"**{reason}** ({len(entries)})\n\n")
            for check_name, file_param in entries:
                check_ref = _make_check_ref(
                    check_name, blob_url_prefix, test_file, test_line_map
                )
                file_ref = _make_file_ref(
                    file_param, pipelinerun_dir, blob_url_prefix,
                    basename_only=True,
                )
                lines.append(f"- {check_ref} — {file_ref}\n")
            lines.append("\n")
        lines.append("</details>\n\n")

    # Footer with logs link, commit, and timestamp
    footer_parts = []
    if run_url:
        footer_parts.append(f"[View full logs]({run_url})")
    if commit_sha:
        short_sha = commit_sha[:7]
        if blob_url_prefix:
            # blob prefix is .../blob/<sha>, go up one level for commit link
            commit_url = blob_url_prefix.rsplit("/blob/", 1)[0]
            commit_url += f"/commit/{commit_sha}"
            footer_parts.append(f"Commit [`{short_sha}`]({commit_url})")
        else:
            footer_parts.append(f"Commit `{short_sha}`")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    footer_parts.append(timestamp)
    if footer_parts:
        lines.append(" | ".join(footer_parts) + "\n\n")

    lines.append(
        "<!-- pipelinerun-validation-comment -->\n"
    )

    return lines


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Write a markdown summary to GITHUB_STEP_SUMMARY and PR comment file."""
    stats = terminalreporter.stats
    passed = len(stats.get("passed", []))
    failed = len(stats.get("failed", []))
    skipped = len(stats.get("skipped", []))

    # Write GITHUB_STEP_SUMMARY
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        status = "PASSED" if exitstatus == 0 else "FAILED"
        emoji = "\u2705" if exitstatus == 0 else "\u274c"

        summary_lines = [
            f"## {emoji} PipelineRun Validation: {status}\n\n",
            f"**{passed}** passed | **{failed}** failed | **{skipped}** skipped\n\n",
        ]

        if "failed" in stats:
            summary_lines.append("### Failures\n\n")
            for report in stats["failed"]:
                summary_lines.append(
                    f"<details>\n<summary><code>{report.nodeid}</code></summary>\n\n"
                )
                summary_lines.append(f"```\n{report.longreprtext[:1000]}\n```\n")
                summary_lines.append("</details>\n\n")

        with open(summary_path, "a") as f:
            f.writelines(summary_lines)

    # Write PR comment body file
    comment_file = config.getoption("--validation-comment-file", default=None)
    if comment_file:
        run_url = os.environ.get("GITHUB_RUN_URL")
        blob_url_prefix = os.environ.get("GITHUB_BLOB_URL_PREFIX")
        commit_sha = os.environ.get("GITHUB_COMMIT_SHA")
        pr_dir = config.getoption("--pipelinerun-dir")
        comment_lines = _build_failure_summary(
            stats, exitstatus, pr_dir, run_url, blob_url_prefix, commit_sha
        )
        if comment_lines:
            with open(comment_file, "w") as f:
                f.writelines(comment_lines)
        elif os.path.exists(comment_file):
            os.remove(comment_file)
