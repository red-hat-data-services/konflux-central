#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_buildargs_version_sync.py

Detect (and optionally fix / raise PRs for) hard-coded version build-args in
konflux-central pipelineruns that have drifted from the file's `rhoai-version`
param.

Why this exists
---------------
The z-stream bump flow (`script/rhoai_pipelinerun_manager.sh`, driven by the
"Apply Z-Stream Changes" workflow) updates the `rhoai-version` param of every
push/scheduled pipelinerun, but components may ALSO carry the product version
in a `build-args` entry. Known patterns in the wild:

  - VERSION=vX.Y.Z          (modelmesh, e.g. VERSION=v2.25.9)
  - VERSION=X.Y.Z           (ai-gateway-operator, e.g. VERSION=3.5.4)
  - OPERATOR_VERSION=...    (odh-dashboard, templated)
  - KEY=image:rhoai-X.Y...  (rhai-db-operator e2e, branch-name tag)

If the manager script does not rewrite these, the build-arg goes stale on the
next z-stream bump (exactly what happened to modelmesh on rhoai-2.25:
VERSION=v2.25.9 while rhoai-version is 2.25.13).

This script scans any set of branches (no checkout needed - it reads blobs
straight from git) and classifies every build-arg:

  OK            value exactly matches the file's rhoai-version
  STALE         same X.Y line (product line) but Z differs -> auto-fixable
  EA_MISMATCH   X.Y.Z matches but EA suffix differs -> needs human review
  INFO          version-like value on a different X.Y line (likely a
                dependency version, not the product version) or a
                branch-name image tag -> review, never auto-fixed
  TEMPLATED     value uses {{...}} placeholders -> nothing to do

Usage
-----
  # Audit all release branches + main (read-only)
  ./script/check_buildargs_version_sync.py

  # Audit specific branches only
  ./script/check_buildargs_version_sync.py --branches rhoai-2.25,rhoai-3.5,rhoai-3.6

  # Machine-readable output
  ./script/check_buildargs_version_sync.py --json

  # Apply fixes for STALE findings (in a temp worktree per branch)
  ./script/check_buildargs_version_sync.py --branches rhoai-2.25 --fix

  # Apply fixes, push a branch and open a PR per affected branch
  ./script/check_buildargs_version_sync.py --branches rhoai-2.25 --pr

Exit codes: 0 = no STALE findings, 1 = STALE findings present, 2 = usage error.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = "red-hat-data-services/konflux-central"
DEFAULT_PIPELINERUNS_DIR = "pipelineruns"

# vX.Y.Z / X.Y.Z, optionally with -ea.N or -ea.N.H suffix
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-ea\.(\d+)(?:\.(\d+))?)?$")
# image reference whose tag is a rhoai branch name, e.g. :rhoai-3.7-ea.1
BRANCH_TAG_RE = re.compile(r":[^\s]*rhoai-\d[\d.]*(?:-ea\.\d+)?\s*$")
PARAM_NAME_RE = re.compile(r"^(\s*)-\s*name:\s*(\S+)\s*$")
VALUE_RE = re.compile(r"^(\s*)value:\s*(.*)$")
LIST_ITEM_RE = re.compile(r"^(\s*)-\s*(\S.*)$")


def git(*args, check=True, cwd=None):
    """Run a git command and return stdout."""
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=check, cwd=cwd
    )
    return result.stdout


def remote_branches():
    """All remote branch names (without the origin/ prefix)."""
    out = git("branch", "-r", "--format=%(refname:short)")
    return [b for b in (line.strip() for line in out.splitlines()) if "/" not in b or b.startswith("origin/")]


def default_branches():
    """All remote rhoai-X.Y* release branches plus main."""
    out = git("branch", "-r", "--format=%(refname:short)")
    names = [line.strip().removeprefix("origin/") for line in out.splitlines()]
    branches = [b for b in names if re.match(r"^rhoai-\d", b)]
    if "main" in names:
        branches.append("main")
    return sorted(branches)


def list_pipelinerun_files(branch, pipelineruns_dir):
    """All .tekton yaml files under pipelineruns_dir on the given branch."""
    out = git("ls-tree", "-r", "--name-only", f"origin/{branch}", "--", f"{pipelineruns_dir}/")
    return [
        line.strip()
        for line in out.splitlines()
        if "/.tekton/" in line and line.strip().endswith((".yaml", ".yml"))
    ]


def read_blob(branch, path):
    return git("show", f"origin/{branch}:{path}")


def branch_xy(branch):
    """(major, minor) implied by a branch name like rhoai-3.5 or rhoai-3.5-ea.1."""
    m = re.match(r"^rhoai-(\d+)[.-](\d+)", branch)
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_version(value):
    """'v2.25.13-ea.1.2' -> (2, 25, 13, ea tuple or None); None if not a version."""
    m = VERSION_RE.match(value.strip())
    if not m:
        return None
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    suffix = m.group(4)
    ea = (int(suffix), int(m.group(5))) if suffix and m.group(5) else (int(suffix),) if suffix else None
    return (major, minor, patch, ea)


def extract_rhoai_version(content):
    m = re.search(
        r"-\s*name:\s*rhoai-version\s*\n\s*value:\s*[\"']?([^\"'\s]+)[\"']?",
        content,
    )
    return m.group(1) if m else None


def extract_buildargs(content):
    """
    Yield (line_index, key, value, inline) for every item of every
    `build-args` param in the file. inline=True means the item lives inside a
    flow-style list (value: [a=b, c=d]) on the value line itself.
    """
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        m = PARAM_NAME_RE.match(lines[i])
        if not m or m.group(2) != "build-args":
            i += 1
            continue
        # find the value: line for this param
        j = i + 1
        vm = VALUE_RE.match(lines[j]) if j < len(lines) else None
        if not vm:
            i += 1
            continue
        value_indent = len(vm.group(1))
        rest = vm.group(2).strip()
        if rest.startswith("["):
            # flow-style list on the value line itself
            for item in rest.strip("[]").split(","):
                item = item.strip().strip("\"'")
                if "=" in item:
                    key, _, val = item.partition("=")
                    yield (j, key, val, True)
            i = j + 1
            continue
        # block-style list: collect list items below until indentation drops
        k = j + 1
        while k < len(lines):
            lm = LIST_ITEM_RE.match(lines[k])
            if not lm or len(lm.group(1)) < value_indent:
                break
            item = lm.group(2).strip().strip("\"'")
            if "=" in item:
                key, _, val = item.partition("=")
                yield (k, key, val, False)
            k += 1
        i = k


def classify(key, value, rhoai_version, bxy):
    """Classify one build-arg item. Returns (status, detail)."""
    if "{{" in value:
        return "TEMPLATED", "uses template placeholders"

    ver = parse_version(value)
    if ver is None:
        if BRANCH_TAG_RE.search(value):
            return "INFO", "branch-name image tag (handled by create-mode branch rename)"
        return None, None  # not version-bearing at all

    def as_str(v):
        s = f"{v[0]}.{v[1]}.{v[2]}"
        if v[3]:
            s += "-ea." + ".".join(str(x) for x in v[3])
        return s

    if rhoai_version:
        rv = parse_version(rhoai_version)
        if rv is None:
            return "INFO", f"cannot parse rhoai-version {rhoai_version!r}"
        if ver[:3] == rv[:3] and ver[3] == rv[3]:
            return "OK", f"matches rhoai-version {rhoai_version}"
        if ver[:3] == rv[:3]:
            return "EA_MISMATCH", f"{as_str(ver)} vs rhoai-version {rhoai_version} (EA suffix differs - review)"
        if ver[:2] == rv[:2]:
            return "STALE", f"{as_str(ver)} vs rhoai-version {rhoai_version}"
        return "INFO", f"{as_str(ver)} is not on the {rv[0]}.{rv[1]} product line (likely a dependency version)"

    # no rhoai-version param in this file
    if bxy and ver[:2] == bxy:
        return "INFO", f"{as_str(ver)} tracks the {bxy[0]}.{bxy[1]} line but file has no rhoai-version param"
    return "INFO", f"{as_str(ver)} (file has no rhoai-version param)"


def scan_branch(branch, pipelineruns_dir):
    """Scan one branch. Returns list of finding dicts."""
    findings = []
    bxy = branch_xy(branch)
    files = list_pipelinerun_files(branch, pipelineruns_dir)
    for path in files:
        content = read_blob(branch, path)
        rhoai_version = extract_rhoai_version(content)
        for lineno, key, value, inline in extract_buildargs(content):
            status, detail = classify(key, value, rhoai_version, bxy)
            if status is None:
                continue
            findings.append(
                {
                    "branch": branch,
                    "file": path,
                    "line": lineno + 1,
                    "key": key,
                    "value": value,
                    "rhoai_version": rhoai_version,
                    "status": status,
                    "detail": detail,
                }
            )
    return findings


# ----------------------------------------------------------------------------
# Fix / PR mode
# ----------------------------------------------------------------------------

def fixed_line(line, key, old_value, new_value):
    """Rewrite KEY=old_value -> KEY=new_value inside one line, preserving style."""
    v_prefix = "v" if old_value.startswith("v") else ""
    return line.replace(f"{key}={old_value}", f"{key}={v_prefix}{new_value}", 1)


def apply_fixes_in_worktree(branch, stale_findings, push_branch, pipelineruns_dir):
    """
    Check out `branch` into a temp worktree on a new branch, apply fixes for
    STALE findings, commit. Returns (worktree_path, n_files_changed).
    """
    tmp_base = Path("/tmp/opencode") if Path("/tmp/opencode").is_dir() else None
    tmp = tempfile.mkdtemp(prefix="buildargs-sync-", dir=tmp_base)
    git("worktree", "add", "-b", push_branch, tmp, f"origin/{branch}")
    changed = set()
    by_file = {}
    for f in stale_findings:
        by_file.setdefault(f["file"], []).append(f)

    for path, items in by_file.items():
        wt_path = Path(tmp) / path
        lines = wt_path.read_text().splitlines()
        for f in items:
            lines[f["line"] - 1] = fixed_line(
                lines[f["line"] - 1], f["key"], f["value"], f["rhoai_version"]
            )
        wt_path.write_text("\n".join(lines) + "\n")
        changed.add(path)

    git("-C", tmp, "add", "--", pipelineruns_dir)
    git(
        "-C", tmp, "commit", "-m",
        "[skip-sync] Sync stale build-arg versions with rhoai-version\n\n"
        "Auto-generated by script/check_buildargs_version_sync.py.\n\n"
        + "\n".join(
            f"- {f['file']}:{f['line']} {f['key']}={f['value']} -> "
            f"{f['key']}={f['rhoai_version']} (rhoai-version {f['rhoai_version']})"
            for f in sorted(stale_findings, key=lambda x: (x["file"], x["line"]))
        ),
    )
    return tmp, len(changed)


def cleanup_worktree(worktree, push_branch=None):
    """Remove a temp worktree and its local branch (safe to re-run)."""
    subprocess.run(["git", "worktree", "remove", "--force", worktree],
                   capture_output=True)
    if push_branch:
        subprocess.run(["git", "branch", "-D", push_branch], capture_output=True)


def push_and_pr(worktree, base_branch, push_branch, stale_findings):
    title = f"[skip-sync] Sync stale build-arg versions with rhoai-version on {base_branch}"
    body_lines = [
        "## What",
        "Some pipelineruns carry the product version in `build-args` entries that have "
        "drifted from the file's `rhoai-version` param. This PR syncs them "
        "(preserving each entry's `v`-prefix style).",
        "",
        "Detected by `script/check_buildargs_version_sync.py`.",
        "",
        "## Findings",
        "",
        "| File | Line | Build-arg | Current | Expected (rhoai-version) |",
        "|---|---|---|---|---|",
    ]
    for f in sorted(stale_findings, key=lambda x: (x["file"], x["line"])):
        body_lines.append(
            f"| `{f['file']}` | {f['line']} | `{f['key']}={f['value']}` "
            f"| `{f['value']}` | `{f['rhoai_version']}` |"
        )
    body = "\n".join(body_lines)

    # avoid duplicate PRs
    existing = subprocess.run(
        ["gh", "pr", "list", "--repo", REPO, "--state", "open",
         "--head", push_branch, "--json", "number"],
        capture_output=True, text=True,
    )
    if existing.returncode == 0 and json.loads(existing.stdout or "[]"):
        print(f"  open PR already exists for {push_branch}, skipping PR creation")
        return

    git("-C", worktree, "push", "origin", f"HEAD:refs/heads/{push_branch}")
    subprocess.run(
        ["gh", "pr", "create", "--repo", REPO,
         "--base", base_branch, "--head", push_branch,
         "--title", title, "--body", body],
        check=True,
    )


# ----------------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------------

STATUS_ORDER = {"STALE": 0, "EA_MISMATCH": 1, "INFO": 2, "OK": 3, "TEMPLATED": 4}
STATUS_ICON = {
    "STALE": "❌",
    "EA_MISMATCH": "⚠️ ",
    "INFO": "ℹ️ ",
    "OK": "✅",
    "TEMPLATED": "✅",
}


def print_report(findings, verbose):
    actionable = [f for f in findings if f["status"] in ("STALE", "EA_MISMATCH", "INFO")]
    if verbose:
        shown = findings
    else:
        shown = actionable
    shown = sorted(
        shown, key=lambda f: (f["branch"], STATUS_ORDER[f["status"]], f["file"], f["line"])
    )

    current_branch = None
    for f in shown:
        if f["branch"] != current_branch:
            current_branch = f["branch"]
            print(f"\n── {current_branch} " + "─" * max(1, 60 - len(current_branch)))
        print(
            f"  {STATUS_ICON[f['status']]} {f['status']:<12} "
            f"{f['file']}:{f['line']}  {f['key']}={f['value']}"
        )
        print(f"     └ {f['detail']}")

    counts = {s: 0 for s in STATUS_ORDER}
    for f in findings:
        counts[f["status"]] += 1
    print("\nSummary:")
    for status in ("STALE", "EA_MISMATCH", "INFO", "OK", "TEMPLATED"):
        if counts[status]:
            print(f"  {STATUS_ICON[status]} {status:<12} {counts[status]}")
    print(f"  total build-arg entries scanned: {len(findings)}")


def main():
    parser = argparse.ArgumentParser(
        description="Check/fix/PR hard-coded version build-args in pipelineruns"
    )
    parser.add_argument(
        "--branches",
        help="comma-separated branch list (default: all remote rhoai-* branches + main)",
    )
    parser.add_argument(
        "--dir", default=DEFAULT_PIPELINERUNS_DIR,
        help=f"pipelineruns directory (default: {DEFAULT_PIPELINERUNS_DIR})",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--all", action="store_true",
                        help="also list OK/TEMPLATED entries in the report")
    parser.add_argument("--fix", action="store_true",
                        help="fix STALE findings (commit on a new branch per target)")
    parser.add_argument("--pr", action="store_true",
                        help="with --fix: push the branch and open a PR per affected branch")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --fix/--pr: show what would be done, change nothing")
    args = parser.parse_args()

    branches = (
        [b.strip() for b in args.branches.split(",") if b.strip()]
        if args.branches
        else default_branches()
    )
    for b in branches:
        git("rev-parse", "--verify", "-q", f"origin/{b}")  # raises if missing

    findings = []
    for branch in branches:
        findings.extend(scan_branch(branch, args.dir))

    if args.json:
        print(json.dumps(findings, indent=2))
    else:
        print_report(findings, verbose=args.all)

    stale_by_branch = {}
    for f in findings:
        if f["status"] == "STALE":
            stale_by_branch.setdefault(f["branch"], []).append(f)

    if stale_by_branch and (args.fix or args.pr):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        for branch, items in sorted(stale_by_branch.items()):
            push_branch = f"fix/buildargs-version-sync/{branch}"
            print(f"\n→ fixing {len(items)} stale entr{'y' if len(items) == 1 else 'ies'} on {branch}")
            worktree, n_files = apply_fixes_in_worktree(branch, items, push_branch, args.dir)
            try:
                diff = git("-C", worktree, "show", "--stat", "--oneline", "HEAD")
                print("  committed:\n" + "\n".join("    " + l for l in diff.splitlines()))
                if args.dry_run:
                    print("  [dry-run] nothing pushed, worktree discarded")
                    cleanup_worktree(worktree, push_branch)
                elif args.pr:
                    push_and_pr(worktree, branch, push_branch, items)
                    cleanup_worktree(worktree, push_branch)
                else:
                    print(f"  branch {push_branch} left in worktree {worktree} for review (not pushed)")
            except Exception:
                cleanup_worktree(worktree, push_branch)
                raise

    sys.exit(1 if stale_by_branch else 0)


if __name__ == "__main__":
    main()
