#!/usr/bin/env python3
"""Verify fips-check-blocking is not disabled without a tracked exception.

Scans PipelineRun YAML under pipelineruns/ and shared Tekton pipelines under
pipelines/ at the current checkout. Compares violations against
fips-check-exceptions.yaml for the pull request target branch.

See docs/fips-check-blocking.md (RHAI-15255).
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

COMMENT_MARKER = "<!-- fips-check-blocking-comment -->"
FIPS_PARAM = "fips-check-blocking"
FALSE_VALUES = {"false", "0", "no"}


@dataclass(frozen=True)
class Violation:
    file: str
    reason: str


def _find_pipelinerun_files(pipelinerun_dir: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in ("**/*.yaml", "**/*.yml"):
        for path in pipelinerun_dir.glob(pattern):
            if ".tekton" in path.parts:
                files.append(path)
    return sorted(files)


def _find_pipeline_files(pipelines_dir: Path) -> list[Path]:
    if not pipelines_dir.is_dir():
        return []
    return sorted(pipelines_dir.glob("*.yaml")) + sorted(pipelines_dir.glob("*.yml"))


def _param_value(params: list | None, name: str) -> str | None:
    if not params:
        return None
    for param in params:
        if not isinstance(param, dict):
            continue
        param_name = param.get("name")
        if not isinstance(param_name, str):
            continue
        if param_name.split("#", 1)[0].strip() != name:
            continue
        value = param.get("value")
        if value is None:
            return None
        return str(value)
    return None


def _is_false(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in FALSE_VALUES


def _pipeline_default_false(data: dict) -> bool:
    params = data.get("spec", {}).get("params") or data.get("params")
    if not isinstance(params, list):
        return False
    for param in params:
        if not isinstance(param, dict):
            continue
        param_name = param.get("name")
        if not isinstance(param_name, str):
            continue
        if param_name.split("#", 1)[0].strip() != FIPS_PARAM:
            continue
        default = param.get("default")
        if default is None:
            return False
        return str(default).strip().lower() in FALSE_VALUES
    return False


def _load_pipelinerun(path: Path) -> dict | None:
    try:
        with path.open() as handle:
            data = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("kind") != "PipelineRun":
        return None
    return data


def _load_pipeline(path: Path) -> dict | None:
    try:
        with path.open() as handle:
            data = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def _normalize_repo_path(path: Path, repo_root: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def collect_violations(
    repo_root: Path,
    pipelinerun_dir: Path,
    pipelines_dir: Path,
) -> list[Violation]:
    violations: list[Violation] = []

    for path in _find_pipelinerun_files(pipelinerun_dir):
        data = _load_pipelinerun(path)
        if data is None:
            continue
        value = _param_value(data.get("spec", {}).get("params"), FIPS_PARAM)
        if _is_false(value):
            rel = _normalize_repo_path(path, repo_root)
            violations.append(
                Violation(
                    file=rel,
                    reason=f"PipelineRun sets `{FIPS_PARAM}` to `{value}`",
                )
            )

    for path in _find_pipeline_files(pipelines_dir):
        data = _load_pipeline(path)
        if data is None:
            continue
        if _pipeline_default_false(data):
            rel = _normalize_repo_path(path, repo_root)
            violations.append(
                Violation(
                    file=rel,
                    reason=f"Pipeline default for `{FIPS_PARAM}` is `false`",
                )
            )

    return violations


def load_allowed_exception_files(exceptions_path: Path, target_branch: str) -> set[str]:
    with exceptions_path.open() as handle:
        entries = yaml.safe_load(handle)
    if not isinstance(entries, list):
        raise ValueError(f"{exceptions_path}: root must be a list")

    allowed: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        branch = entry.get("branch")
        if branch != target_branch:
            continue
        for exc in entry.get("exceptions") or []:
            if not isinstance(exc, dict):
                continue
            file_path = exc.get("file")
            if isinstance(file_path, str) and file_path.strip():
                allowed.add(file_path.strip())
    return allowed


def filter_unapproved(
    violations: list[Violation], allowed_files: set[str]
) -> list[Violation]:
    return [v for v in violations if v.file not in allowed_files]


def build_pr_comment(
    *,
    target_branch: str,
    unapproved: list[Violation],
    repo: str,
    run_url: str | None,
    blob_prefix: str | None,
    commit_sha: str | None,
) -> str:
    exceptions_path = "fips-check-exceptions.yaml"
    codeowners_path = ".github/CODEOWNERS"
    exceptions_url = (
        f"https://github.com/{repo}/blob/main/{exceptions_path}"
        if repo
        else exceptions_path
    )
    codeowners_url = (
        f"https://github.com/{repo}/blob/main/{codeowners_path}"
        if repo
        else codeowners_path
    )

    lines = [
        "## :x: FIPS check blocking validation failed\n\n",
        "Some PipelineRuns or shared pipelines disable the **FIPS check blocking** "
        "parameter (`fips-check-blocking: false`). That lets FIPS payload check "
        "failures pass without failing the Konflux pipeline, which weakens "
        "compliance guarantees for RHOAI/ODH container builds.\n\n",
        f"**Target branch:** `{target_branch}`\n\n",
        "### Files that must be fixed or excepted\n\n",
    ]

    for violation in sorted(unapproved, key=lambda v: v.file):
        if blob_prefix:
            url = f"{blob_prefix}/{violation.file}"
            lines.append(f"- [`{violation.file}`]({url}) — {violation.reason}\n")
        else:
            lines.append(f"- `{violation.file}` — {violation.reason}\n")

    lines.extend(
        [
            "\n### How to get an approved exception\n\n",
            "1. Open a Jira ticket explaining why FIPS blocking must be disabled "
            "for this PipelineRun or pipeline.\n",
            "2. Ask a code owner to add an entry for **this target branch** in "
            f"[`{exceptions_path}`]({exceptions_url}) on **main**, including the "
            "file path and Jira reference.\n",
            "3. Re-run CI after the exception is merged to main.\n\n",
            f"Future updates to `{exceptions_path}` will be restricted via "
            f"[`CODEOWNERS`]({codeowners_url}) (follow-up work).\n\n",
            "Reach out to the konflux-central code owners if you are unsure "
            "whether an exception is appropriate.\n\n",
        ]
    )

    footer: list[str] = []
    if run_url:
        footer.append(f"[View full logs]({run_url})")
    if commit_sha:
        short = commit_sha[:7]
        if blob_prefix:
            commit_url = blob_prefix.rsplit("/blob/", 1)[0] + f"/commit/{commit_sha}"
            footer.append(f"Commit [`{short}`]({commit_url})")
        else:
            footer.append(f"Commit `{short}`")
    footer.append(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
    lines.append(" | ".join(footer) + "\n\n")
    lines.append(COMMENT_MARKER + "\n")
    return "".join(lines)


def build_success_comment(
    *,
    target_branch: str,
    run_url: str | None,
    commit_sha: str | None,
    blob_prefix: str | None,
) -> str:
    lines = [
        "## :white_check_mark: FIPS check blocking validation passed\n\n",
        f"No unapproved `{FIPS_PARAM}: false` settings found for target branch "
        f"`{target_branch}`.\n\n",
    ]
    footer: list[str] = []
    if run_url:
        footer.append(f"[View full logs]({run_url})")
    if commit_sha:
        short = commit_sha[:7]
        if blob_prefix:
            commit_url = blob_prefix.rsplit("/blob/", 1)[0] + f"/commit/{commit_sha}"
            footer.append(f"Commit [`{short}`]({commit_url})")
        else:
            footer.append(f"Commit `{short}`")
    footer.append(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
    lines.append(" | ".join(footer) + "\n\n")
    lines.append(COMMENT_MARKER + "\n")
    return "".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help="Repository root (default: current directory)",
    )
    parser.add_argument(
        "--pipelinerun-dir",
        type=Path,
        default=Path("pipelineruns"),
        help="PipelineRun directory relative to repo root",
    )
    parser.add_argument(
        "--pipelines-dir",
        type=Path,
        default=Path("pipelines"),
        help="Shared pipelines directory relative to repo root",
    )
    parser.add_argument(
        "--exceptions-file",
        type=Path,
        required=True,
        help="Path to fips-check-exceptions.yaml (usually from main)",
    )
    parser.add_argument(
        "--target-branch",
        required=True,
        help="Pull request base branch (e.g. main, rhoai-3.6)",
    )
    parser.add_argument(
        "--comment-file",
        type=Path,
        default=None,
        help="Write PR comment markdown to this path",
    )
    parser.add_argument(
        "--github-repository",
        default=os.environ.get("GITHUB_REPOSITORY", ""),
        help="owner/repo for links in PR comments",
    )
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    pipelinerun_dir = (repo_root / args.pipelinerun_dir).resolve()
    pipelines_dir = (repo_root / args.pipelines_dir).resolve()
    exceptions_path = args.exceptions_file.resolve()

    violations = collect_violations(repo_root, pipelinerun_dir, pipelines_dir)
    allowed = load_allowed_exception_files(exceptions_path, args.target_branch)
    unapproved = filter_unapproved(violations, allowed)

    run_url = os.environ.get("GITHUB_RUN_URL")
    blob_prefix = os.environ.get("GITHUB_BLOB_URL_PREFIX")
    commit_sha = os.environ.get("GITHUB_COMMIT_SHA")

    if args.comment_file:
        if unapproved:
            body = build_pr_comment(
                target_branch=args.target_branch,
                unapproved=unapproved,
                repo=args.github_repository,
                run_url=run_url,
                blob_prefix=blob_prefix,
                commit_sha=commit_sha,
            )
        else:
            body = build_success_comment(
                target_branch=args.target_branch,
                run_url=run_url,
                commit_sha=commit_sha,
                blob_prefix=blob_prefix,
            )
        args.comment_file.write_text(body)

    if unapproved:
        for violation in unapproved:
            print(f"{violation.file}: {violation.reason}", file=sys.stderr)
        print(
            f"\n{len(unapproved)} file(s) disable `{FIPS_PARAM}` without an "
            f"approved exception for branch `{args.target_branch}`.",
            file=sys.stderr,
        )
        return 1

    print(
        f"FIPS check blocking validation passed for branch `{args.target_branch}`."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
