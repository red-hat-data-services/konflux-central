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


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Write a markdown summary to GITHUB_STEP_SUMMARY if available."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    stats = terminalreporter.stats
    passed = len(stats.get("passed", []))
    failed = len(stats.get("failed", []))
    skipped = len(stats.get("skipped", []))

    status = "PASSED" if exitstatus == 0 else "FAILED"
    emoji = "\u2705" if exitstatus == 0 else "\u274c"

    lines = [
        f"## {emoji} PipelineRun Validation: {status}\n\n",
        f"**{passed}** passed | **{failed}** failed | **{skipped}** skipped\n\n",
    ]

    if "failed" in stats:
        lines.append("### Failures\n\n")
        for report in stats["failed"]:
            lines.append(
                f"<details>\n<summary><code>{report.nodeid}</code></summary>\n\n"
            )
            lines.append(f"```\n{report.longreprtext[:1000]}\n```\n")
            lines.append("</details>\n\n")

    with open(summary_path, "a") as f:
        f.writelines(lines)
