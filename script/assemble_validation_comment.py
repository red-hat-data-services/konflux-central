"""Combine pytest details with job outcomes for the PipelineRun PR comment."""

import argparse
from collections import defaultdict
from html import escape
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import quote, urlsplit

MARKER = "<!-- pipelinerun-validation-comment -->"


def _code(value, limit):
    """Bound and escape report content before embedding it in markdown."""
    text = " ".join(value.split())
    if len(text) > limit:
        text = text[:limit] + "…"
    return f"<code>{escape(text)}</code>"


def template_source_links(directory, config, repository_url):
    """Link source files at the recorded checkout revision, not a moving branch."""
    if not repository_url:
        return ""
    parsed = urlsplit(repository_url)
    if parsed.scheme not in ("https", "http") or not parsed.netloc:
        return ""
    try:
        revision = (directory / f"{config}-template-source-sha.txt").read_text().strip()
        manifest = (directory / f"{config}-template-files.txt").read_text().splitlines()
    except OSError:
        return ""
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision):
        return ""
    prefix = f"tekton-templates/{config}/"
    paths = sorted({
        path for path in manifest
        if path.startswith(prefix) and ".." not in PurePosixPath(path).parts
        and (path.endswith(".j2") or path == prefix + "config.yaml")
    })
    links = []
    base = quote(repository_url.rstrip("/"), safe="/:")
    for path in paths[:12]:
        label = escape(path[len(prefix):]).replace("[", "&#91;").replace("]", "&#93;")
        links.append(f"[{label}]({base}/blob/{revision}/{quote(path, safe='/')})")
    if not links:
        return ""
    suffix = f" ({len(paths) - 12} more source files omitted)" if len(paths) > 12 else ""
    return "Template sources: " + ", ".join(links) + suffix + ".\n\n"


def summarize_template_reports(directory, target_branch="main", repository_url=None):
    """Group structured validator errors, with bounded examples and safe fallback."""
    lines = ["### Template validation details\n\n"]
    configs = ["all-pipelineruns"] + (["main-pr"] if target_branch == "main" else [])
    for config in configs:
        lines.append(f"#### {config}\n\n")
        source_links = template_source_links(directory, config, repository_url)
        try:
            path = directory / f"validate-{config}.json"
            if path.stat().st_size > 10_000_000:
                raise ValueError("report too large")
            report = json.loads(path.read_text())
            files = report["files"]
            if not isinstance(files, dict):
                raise ValueError("invalid files")
            for name, result in files.items():
                if not isinstance(result, dict):
                    raise ValueError("invalid result")
                for key in ("errors", "warnings"):
                    entries = result.get(key, [])
                    if not isinstance(entries, list) or not all(isinstance(e, str) for e in entries):
                        raise ValueError("invalid diagnostics")
        except (OSError, ValueError, KeyError, TypeError):
            lines.append(source_links)
            lines.append("Structured report unavailable; see the workflow logs.\n\n")
            continue

        errors = sum(len(r.get("errors", [])) for r in files.values())
        warnings = sum(len(r.get("warnings", [])) for r in files.values())
        affected = sum(bool(r.get("errors")) for r in files.values())
        lines.append(f"**{errors} errors** across **{affected} files**; **{warnings} warnings**.\n\n")
        if errors or warnings:
            lines.append(source_links)
        groups = defaultdict(list)
        for filename, result in sorted(files.items()):
            for error in result.get("errors", []):
                field = error.split(":", 1)[0]
                groups[field].append((filename, error))
        ordered = sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
        for field, entries in ordered[:10]:
            filenames = sorted({name for name, _ in entries})
            examples = ", ".join(_code(name, 120) for name in filenames[:3])
            if len(filenames) > 3:
                examples += f", and {len(filenames) - 3} more"
            lines.append(
                f"- {_code(field, 120)}: **{len(entries)} errors** in **{len(filenames)} files**.\n"
                f"  - Files: {examples}\n"
                f"  - Example: {_code(entries[0][1], 240)}\n"
            )
        if len(ordered) > 10:
            lines.append(f"\n{len(ordered) - 10} additional error groups omitted.\n")
        if groups:
            lines.append(f"\nFull diagnostics are in the template-validation-{config} artifact.\n\n")
    return "".join(lines)


def build_comment(pytest_result, all_pipelineruns_result, main_pr_result,
                  pytest_comment, run_url, template_details="", target_branch="main"):
    """Only report success when all applicable validation jobs succeeded."""
    passed = pytest_result == all_pipelineruns_result == "success" and (
        main_pr_result == ("success" if target_branch == "main" else "skipped")
    )
    main_pr_display = main_pr_result
    if target_branch != "main" and main_pr_result == "skipped":
        main_pr_display = "skipped (not applicable to release branches)"
    title = ":white_check_mark: PipelineRun Validation Passed" if passed else (
        ":x: PipelineRun Validation Failed or Incomplete"
    )
    lines = [
        f"## {title}\n\n",
        "| Validation | Result | Description |\n| --- | --- | --- |\n",
        f"| Pytest checks | {pytest_result} | General validation checks. |\n",
        f"| all-pipelineruns | {all_pipelineruns_result} | "
        "Enforce PipelineRuns against standard templates. |\n",
        f"| main-pr | {main_pr_display} | "
        "Ensure main-branch pull-request PipelineRuns are consistent with "
        "the latest release branch's push PipelineRuns. |\n\n",
        f"[View validation jobs and template logs]({run_url})\n\n",
    ]
    if template_details:
        lines.append(template_details)
    if pytest_comment:
        # Keep detailed test output, but make its narrower scope explicit.
        details = pytest_comment.replace(MARKER, "").strip()
        details = details.replace(
            "PipelineRun Validation Passed", "Pytest Checks Passed", 1
        ).replace("PipelineRun Validation Failed", "Pytest Checks Failed", 1)
        lines.append(details + "\n\n")
    else:
        lines.append("Pytest details were not available; see the workflow logs.\n\n")
    lines.append(MARKER + "\n")
    return "".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("pytest-result", "all-pipelineruns-result", "main-pr-result"):
        parser.add_argument(
            f"--{name}", required=True,
            choices=("success", "failure", "cancelled", "skipped"),
        )
    parser.add_argument("--pytest-comment", type=Path, required=True)
    parser.add_argument("--template-reports", type=Path)
    parser.add_argument("--target-branch", default="main")
    parser.add_argument("--repository-url")
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    details = args.pytest_comment.read_text() if args.pytest_comment.is_file() else ""
    args.output.write_text(build_comment(
        args.pytest_result, args.all_pipelineruns_result, args.main_pr_result,
        details, args.run_url,
        summarize_template_reports(args.template_reports, args.target_branch, args.repository_url)
        if args.template_reports else "", target_branch=args.target_branch,
    ))


if __name__ == "__main__":
    main()
