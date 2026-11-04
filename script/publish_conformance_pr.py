#!/usr/bin/env python3
"""Publish an already revalidated local plan as a release-branch PR (opt-in)."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from reconcile_pipelineruns import VALIDATOR_REVISION

MARKER = "Conformance-Generated: true"


def command(args, cwd, env=None, input=None):
    """Run a command without shell interpolation; propagate failures."""
    return subprocess.run(args, cwd=cwd, env=env, input=input, text=True,
                          check=True, stdout=subprocess.PIPE).stdout.strip()


def pr_body(base, source, report):
    """Describe generated changes, exceptions, and the runtime-validation gate."""
    counts = report["counts"]
    lines = [f"Reconcile `{base}` using templates/runner from `{source}`.", "",
             "## Validation", "",
             "Local fixes were reread and revalidated; the second pass planned no further changes.",
             "Only the expected PipelineRun paths are committed. No Konflux reruns or merges are requested; normal PR CI may run.",
             "", "## Cases left unchanged", "",
             f"Manual review: {counts.get('manual', 0)}; unresolved references: {counts.get('unresolved', 0)}."]
    for row in report["files"]:
        if row["status"] in ("manual", "unresolved"):
            lines.append(f"- `{row['path']}`: {row['reason']}")
    lines += ["", "## Before merging", "",
              "Verify the admitted scan pod has the intended memory/CPU request and no CPU ceiling,",
              "including bundled defaults and admission behavior. Review scan outcomes and scheduling/quota impact.",
              "The memory baseline is provisional. Do not auto-merge.", "",
              "Related: [RHAI-19093](https://redhat.atlassian.net/browse/RHAI-19093)."]
    return "\n".join(lines)


def publish(root, repository, base, source, plan, recheck, run=command):
    """Create/update one generated PR with append-only history and no force-push."""
    root = Path(root).resolve()
    if not re.fullmatch(r"rhoai-\d+\.\d+(?:-ea\.\d+)?", base):
        raise ValueError("base must be a release branch, not main")
    if plan.get("branch") != base or recheck.get("branch") != base:
        raise ValueError("report branch does not match requested base")
    if not plan.get("fixApplied") or plan["counts"].get("error"):
        raise ValueError("a successfully applied plan is required")
    if recheck.get("fixApplied") or any(recheck["counts"].get(key) for key in ("planned", "error")):
        raise ValueError("a clean report-only recheck is required")
    if any(report.get("validatorRevision") != VALIDATOR_REVISION for report in (plan, recheck)):
        raise ValueError("plan and recheck must use the pinned validator")
    planned = {row["path"]: row for row in plan["files"] if row["status"] == "planned"}
    checked = {row["path"]: row for row in recheck["files"]}
    expected = sorted(row["path"] for row in plan["files"] if row["status"] == "planned")
    if len(expected) != len(set(expected)):
        raise ValueError("duplicate planned file")
    for path in expected:
        parts = Path(path).parts
        if (len(parts) != 4 or parts[0] != "pipelineruns" or parts[2] != ".tekton"
                or Path(path).suffix not in (".yaml", ".yml") or ".." in parts):
            raise ValueError(f"unexpected planned path: {path}")
        file = root / path
        if file.is_symlink() or not file.resolve().is_relative_to(root):
            raise ValueError(f"planned path escapes checkout: {path}")
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        if (checked.get(path, {}).get("status") != "conformant"
                or digest != checked[path].get("sha256")
                or digest != planned[path].get("plannedSha256")):
            raise ValueError(f"planned file changed or was not revalidated: {path}")
    git = lambda *args: run(["git", *args], root)
    if git("diff", "--cached", "--name-only"):
        raise ValueError("index must be clean before publishing")
    changed = sorted(filter(None, git("diff", "--name-only").splitlines()))
    if changed != expected or git("ls-files", "--others", "--exclude-standard"):
        raise ValueError("worktree changes do not exactly match the applied plan")
    if git("diff", "--name-only", "--diff-filter=ADRTUXB"):
        raise ValueError("only modifications of existing PipelineRuns are allowed")
    if not expected:
        return "No fixes needed; no PR created."
    remote_url = git("remote", "get-url", "origin").removesuffix(".git")
    if remote_url not in (f"https://github.com/{repository}", f"git@github.com:{repository}"):
        raise ValueError("origin does not match the requested GitHub repository")
    head = git("rev-parse", "HEAD")
    base_ref = f"refs/heads/{base}"
    remote_base = git("ls-remote", "--heads", "origin", base_ref).split()
    if not remote_base or remote_base[0] != head:
        raise ValueError("target checkout is not the current remote release tip; regenerate the plan")
    branch = f"automation/pipelinerun-conformance/{base}"
    ref = f"refs/heads/{branch}"
    existing = git("ls-remote", "--heads", "origin", ref).split()
    old = existing[0] if existing else None
    if old:
        git("fetch", "--no-tags", "origin", ref)
        if git("rev-parse", "FETCH_HEAD") != old:
            raise ValueError("automation branch moved during inspection; retry from a fresh checkout")
        if MARKER not in git("show", "-s", "--format=%B", old).splitlines():
            raise ValueError("refusing to overwrite a branch not marked as generated")
    git("add", "--", *expected)
    git("diff", "--cached", "--check")
    tree = git("write-tree")
    # Build a fast-forward merge commit whose tree is the fresh base + validated plan.
    # Retain the prior automation tip as a parent: never rewrite published history.
    parents = [head] + ([old] if old and old != head else [])
    if (old and git("rev-parse", f"{old}^{{tree}}") == tree
            and f"Conformance-Base: {head}" in git("show", "-s", "--format=%B", old).splitlines()):
        commit = old
    else:
        args = ["git", "commit-tree", tree]
        for parent in parents:
            args += ["-p", parent]
        env = {**os.environ, "GIT_AUTHOR_NAME": "github-actions[bot]",
               "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
               "GIT_COMMITTER_NAME": "github-actions[bot]",
               "GIT_COMMITTER_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com"}
        message = f"chore: reconcile PipelineRuns for {base}\n\n{MARKER}\nConformance-Source: {source}\nConformance-Base: {head}\n"
        commit = run(args, root, env=env, input=message)
        # A concurrent update to this branch causes a normal push rejection.
        git("push", "origin", f"{commit}:{ref}")
    prs = json.loads(run(["gh", "pr", "list", "--repo", repository, "--base", base,
                          "--head", branch, "--state", "open", "--json", "number,url"], root))
    if len(prs) > 1:
        raise ValueError("multiple matching open PRs require manual review")
    title = f"chore: reconcile PipelineRun conventions for {base}"
    body = pr_body(base, source, recheck)
    if prs:
        run(["gh", "pr", "edit", str(prs[0]["number"]), "--repo", repository,
             "--title", title, "--body", body], root)
        return prs[0]["url"]
    return run(["gh", "pr", "create", "--repo", repository, "--base", base,
                "--head", branch, "--title", title, "--body", body], root)


def main():
    """Publish only when explicitly invoked; never used in preview mode."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--recheck", type=Path, required=True)
    args = parser.parse_args()
    print(publish(args.path, args.repository, args.base, args.source_sha,
                  json.loads(args.plan.read_text()), json.loads(args.recheck.read_text())))


if __name__ == "__main__":
    main()
