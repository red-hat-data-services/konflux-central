"""Combine pytest details with job outcomes for the PipelineRun PR comment."""

import argparse
from pathlib import Path

MARKER = "<!-- pipelinerun-validation-comment -->"


def build_comment(pytest_result, template_result, pytest_comment, run_url):
    """Only report overall success when both validation jobs succeeded."""
    passed = pytest_result == template_result == "success"
    title = ":white_check_mark: PipelineRun Validation Passed" if passed else (
        ":x: PipelineRun Validation Failed or Incomplete"
    )
    lines = [
        f"## {title}\n\n",
        "| Validation | Result |\n| --- | --- |\n",
        f"| Pytest checks | {pytest_result} |\n",
        f"| Template checks (all-pipelineruns; main-pr on main) | {template_result} |\n\n",
        f"[View validation jobs and template logs]({run_url})\n\n",
    ]
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
    for name in ("pytest-result", "template-result"):
        parser.add_argument(
            f"--{name}", required=True,
            choices=("success", "failure", "cancelled", "skipped"),
        )
    parser.add_argument("--pytest-comment", type=Path, required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    details = args.pytest_comment.read_text() if args.pytest_comment.is_file() else ""
    args.output.write_text(build_comment(
        args.pytest_result, args.template_result, details, args.run_url
    ))


if __name__ == "__main__":
    main()
