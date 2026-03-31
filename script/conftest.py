"""Pytest configuration for PipelineRun validation.

Custom options:
    --pipelinerun-dir  Directory containing PipelineRun YAML files (default: pipelineruns)
    --branch           Expected target branch for push PipelineRuns (e.g., rhoai-3.4)

Environment variables:
    QUAY_RHOAI_READONLY_BOT_AUTH  Base64-encoded username:password for Quay API
    GITHUB_TOKEN                  GitHub API token for Dockerfile path checks
"""

import os
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


def _build_failure_summary(stats, exitstatus, run_url=None):
    """Build a markdown summary of validation results.

    Returns the summary lines as a list of strings, or None if there are
    no failures.
    """
    passed = len(stats.get("passed", []))
    failed = len(stats.get("failed", []))
    skipped = len(stats.get("skipped", []))

    if exitstatus == 0:
        return None

    # Group failures by check name
    failures_by_check = {}
    for report in stats.get("failed", []):
        # nodeid format: script/test_validate_pipelineruns.py::test_name[param]
        parts = report.nodeid.split("::")
        test_part = parts[-1] if len(parts) > 1 else report.nodeid
        # Extract test name and file parameter
        bracket_idx = test_part.find("[")
        if bracket_idx != -1:
            check_name = test_part[:bracket_idx]
            file_param = test_part[bracket_idx + 1:].rstrip("]")
        else:
            check_name = test_part
            file_param = ""

        # Extract concise error message from longreprtext
        msg = ""
        if report.longreprtext:
            text_lines = report.longreprtext.strip().split("\n")
            # Collect all "E " lines, prefer the one with Failed:/AssertionError:
            e_lines = []
            for line in text_lines:
                stripped = line.lstrip()
                if stripped.startswith("E "):
                    e_lines.append(stripped[2:].strip())
            # Prefer lines starting with "Failed:" or "AssertionError:"
            for e_line in reversed(e_lines):
                if e_line.startswith(("Failed:", "AssertionError:",
                                      "AssertError:")):
                    msg = e_line
                    break
            if not msg and e_lines:
                msg = e_lines[0]
            if not msg:
                msg = text_lines[-1].strip()
            for prefix in ("AssertionError: ", "AssertError: ",
                           "Failed: ", "FAILED: "):
                msg = msg.removeprefix(prefix)
            msg = msg.strip()

        failures_by_check.setdefault(check_name, []).append(
            (file_param, msg)
        )

    lines = [
        "## :x: PipelineRun Validation Failed\n\n",
        f"**{passed}** passed | **{failed}** failed | **{skipped}** skipped\n\n",
    ]

    for check_name, file_failures in failures_by_check.items():
        lines.append(f"### `{check_name}`\n\n")
        lines.append("| File | Error |\n")
        lines.append("|------|-------|\n")
        for file_param, msg in file_failures:
            # Escape pipe characters for markdown table
            escaped_msg = msg.replace("|", "\\|")
            # Truncate very long messages
            if len(escaped_msg) > 200:
                escaped_msg = escaped_msg[:197] + "..."
            lines.append(f"| `{file_param}` | {escaped_msg} |\n")
        lines.append("\n")

    if run_url:
        lines.append(f"[View full logs]({run_url})\n\n")

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
        comment_lines = _build_failure_summary(stats, exitstatus, run_url)
        if comment_lines:
            with open(comment_file, "w") as f:
                f.writelines(comment_lines)
        elif os.path.exists(comment_file):
            os.remove(comment_file)
