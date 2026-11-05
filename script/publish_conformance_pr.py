#!/usr/bin/env python3
"""Adapt independently validated scan fixes to the pinned shared PR publisher."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re

from conformance_artifact import git_read, planned_paths, prepare

MARKER = "Conformance-Generated: true"
PUBLISHER_REVISION = "b8fe79fa5784260b32712bd6730d162d132852c8"


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


def load_publisher(path):
    """Load only the fixed standalone core, never lib.__init__ or planner code."""
    path = Path(path).resolve()
    revision = git_read(path, "rev-parse", "HEAD").decode().strip()
    if revision != PUBLISHER_REVISION:
        raise ValueError("publisher checkout differs from the reviewed revision")
    filename = path / "lib/validated_pr_publisher.py"
    expected = git_read(path, "show", f"{revision}:lib/validated_pr_publisher.py")
    if (filename.is_symlink() or not filename.resolve().is_relative_to(path)
            or filename.read_bytes() != expected):
        raise ValueError("publisher source differs from the reviewed Git contents")
    spec = importlib.util.spec_from_file_location("kc_validated_pr_publisher", filename)
    module = importlib.util.module_from_spec(spec)
    # Execute the checked Git bytes, not an ignored/stale __pycache__ file.
    exec(compile(expected, str(filename), "exec"), module.__dict__)
    return module


def build_request(root, repository, base, source, plan, recheck):
    """Construct an M-only request from already independently validated bytes."""
    if not re.fullmatch(r"rhoai-\d+\.\d+(?:-ea\.\d+)?", base):
        raise ValueError("base must be a release branch, not main")
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("source must be the workflow commit SHA")
    root = Path(root).resolve()
    changes = []
    for name in planned_paths(plan):
        mode = git_read(root, "ls-tree", "HEAD", "--", name).decode().split()[0]
        before = git_read(root, "show", f"HEAD:{name}")
        changes.append({"path": name, "status": "M", "mode": mode,
                        "before_sha256": hashlib.sha256(before).hexdigest(),
                        "after_sha256": hashlib.sha256((root / name).read_bytes()).hexdigest()})
    return {"version": 1, "repository": repository, "base": base,
            "base_oid": git_read(root, "rev-parse", "HEAD").decode().strip(),
            "head": f"automation/pipelinerun-conformance/{base}", "lifecycle": "create-or-update",
            "marker": MARKER, "title": f"chore: reconcile PipelineRun conventions for {base}",
            "body": pr_body(base, source, recheck), "changes": changes,
            "provenance": {"policyRevision": source, "validatorRevision": plan["validatorRevision"],
                           "publisherRevision": PUBLISHER_REVISION}}


def publish(root, repository, base, source, bundle, publisher_path, *, do_publish=False):
    """Repeat independent validation before handing a fresh request to shared code."""
    plan, recheck = prepare(root, bundle, base, source, publisher_revision=PUBLISHER_REVISION)
    request = build_request(root, repository, base, source, plan, recheck)
    shared = load_publisher(publisher_path)
    if do_publish:
        return shared.publish(root, request)
    shared.verify(root, request)
    return {"status": "verified", "provenance": request["provenance"]}


def main():
    """Verify by default; only explicit --publish permits repository/GitHub writes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--publisher", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    print(json.dumps(publish(args.path, args.repository, args.base, args.source_sha,
                             args.artifact, args.publisher, do_publish=args.publish)))


if __name__ == "__main__":
    main()
