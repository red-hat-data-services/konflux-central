#!/usr/bin/env python3
"""Local generation/check/report or explicit GitHub publication (separate step)."""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def run(*args, cwd=None, capture=False):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=capture)
    if result.returncode and capture:
        # Preserve validator diagnostics in the workflow log even on failure.
        print(result.stdout or "", end="", file=sys.stderr)
        print(result.stderr or "", end="", file=sys.stderr)
    result.check_returncode()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--tooling", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--repository", default="")
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"rhoai-\d+\.\d+(?:-ea\.\d+)?", args.branch):
        parser.error("invalid release branch")
    target = args.target.resolve()
    report = args.report_dir.resolve()
    report.mkdir(parents=True, exist_ok=True)
    manifest = report / "generation.json"
    if not args.publish:
        if run("git", "status", "--porcelain", cwd=target, capture=True).stdout:
            parser.error("target checkout must be clean")
        if args.repository and not re.fullmatch(r"[A-Za-z0-9_.-]+", args.repository):
            parser.error("invalid repository directory")
        command = ["uv", "run", str(args.tooling.resolve() / "scripts/validate-tekton"),
                   "--path", str(target), "--branch", args.branch, "--no-checkout", "--json",
                   "--config-dir", str(args.config_dir.resolve())]
        if args.repository:
            command += ["--pipeline-dir", f"pipelineruns/{args.repository}"]
        fixed = run(*command, "--fix", capture=True)
        (report / "fix.json").write_text(fixed.stdout)
        print(fixed.stderr or "", end="")
        # Independent validation invocation: re-read the files without --fix.
        checked = run(*command, capture=True)
        (report / "validation.json").write_text(checked.stdout)
        print(checked.stderr or "", end="")
        fixed_result = json.loads(fixed.stdout)
        checked_result = json.loads(checked.stdout)
        def relative(path):
            return str(Path(path).resolve().relative_to(target))
        result = {"branch": args.branch,
                  "changed": [relative(p) for p in fixed_result.get("fixed", [])],
                  "orphans": [relative(p) for p, item in checked_result["files"].items()
                              if any("orphan child" in warning for warning in item.get("warnings", []))]}
        manifest.write_text(json.dumps(result, indent=2) + "\n")
        # Intent-to-add includes new files in the diff without staging content.
        if result["changed"]:
            run("git", "add", "--intent-to-add", "--", *result["changed"], cwd=target)
        changed = run("git", "diff", "--name-only", cwd=target, capture=True).stdout.splitlines()
        if set(changed) != set(result["changed"]):
            parser.error("unexpected file changes outside generated PR scope")
        for path in changed:
            if not re.fullmatch(r"pipelineruns/[^/]+/\.tekton/[^/]+\.ya?ml", path):
                parser.error(f"unexpected generated path: {path}")
        (report / "changes.diff").write_text(run("git", "diff", cwd=target, capture=True).stdout)
        provenance = {"target_commit": run("git", "rev-parse", "HEAD", cwd=target, capture=True).stdout.strip(),
                      "tooling_commit": run("git", "rev-parse", "HEAD", cwd=args.tooling, capture=True).stdout.strip(),
                      "policy_commit": run("git", "rev-parse", "HEAD", cwd=args.config_dir, capture=True).stdout.strip(),
                      "branch": args.branch}
        (report / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
        return
    result = json.loads(manifest.read_text())
    if result["branch"] != args.branch:
        parser.error("report branch mismatch")
    if not result["changed"]:
        print("No changes; no PR created.")
        return
    branch = f"automation/release-pr-{args.branch}-{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"
    run("git", "switch", "--create", branch, cwd=target)
    run("git", "add", "--", *result["changed"], cwd=target)
    run("git", "config", "user.name", "github-actions[bot]", cwd=target)
    run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com", cwd=target)
    title = f"chore: maintain {args.branch} PR pipelines from release push pipelines"
    run("git", "commit", "-m", title, cwd=target)
    run("git", "push", "--set-upstream", "origin", branch, cwd=target)
    body = report / "pr.md"
    body.write_text("Regenerates PR PipelineRuns from this release's push build configuration. Push files are unchanged.\n\n"
                    + "Source revisions:\n```json\n" + (report / "provenance.json").read_text() + "```\n\n"
                    + "Orphans (not deleted):\n```json\n" + json.dumps(result["orphans"], indent=2) + "\n```\n")
    run("gh", "pr", "create", "--repo", os.environ["GITHUB_REPOSITORY"], "--base", args.branch,
        "--head", branch, "--title", title, "--body-file", str(body), cwd=target)


if __name__ == "__main__":
    main()
