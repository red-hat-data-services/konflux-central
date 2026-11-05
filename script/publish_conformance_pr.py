#!/usr/bin/env python3
"""Adapt trusted scan plans to the pinned shared PR publisher."""

import argparse
import json
from pathlib import Path
import re

from conformance_artifact import check_reports, planned_paths
from reconciliation_tooling import REVISION, git_read, load_tool

MARKER = "Conformance-Generated: true"
PUBLISHER_REVISION = REVISION


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
    """Load the fixed standalone publisher without package/cache side effects."""
    return load_tool(path, "validated_pr_publisher")


def build_request(root, repository, base, source, plan, recheck):
    """Construct an M-only publication request from the checked trusted receipt."""
    if not re.fullmatch(r"rhoai-\d+\.\d+(?:-ea\.\d+)?", base):
        raise ValueError("base must be a release branch, not main")
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("source must be the workflow commit SHA")
    return {"version": 1, "repository": repository, "base": base,
            "base_oid": plan["base_oid"],
            "head": f"automation/pipelinerun-conformance/{base}", "lifecycle": "create-or-update",
            "marker": MARKER, "title": f"chore: reconcile PipelineRun conventions for {base}",
            "body": pr_body(base, source, recheck), "changes": plan["changes"],
            "provenance": plan["provenance"]}


def replay(root, bundle, branch, source, publisher_path):
    """Replay the trusted same-job plan, not independently rederive policy."""
    if not re.fullmatch(r"rhoai-\d+\.\d+(?:-ea\.\d+)?", branch):
        raise ValueError("base must be a release branch, not main")
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("source must be the workflow commit SHA")
    worker = load_tool(publisher_path, "reconciliation")
    receipt = worker.validate_manifest(json.loads(
        worker.read_file(bundle, "manifest.json"), object_pairs_hook=worker.unique_object))
    reports = json.loads(worker.read_file(bundle, "policy-report.json"),
                         object_pairs_hook=worker.unique_object)
    plan, recheck = reports["plan"], reports["recheck"]
    check_reports(plan, recheck, branch)
    if plan["validatorRevision"] != REVISION:
        raise ValueError("plan must use the reviewed corrected validator revision")
    expected = {"branch": branch, "policyRevision": source,
                "workerRevision": REVISION, "publisherRevision": REVISION, "validatorRevision": plan["validatorRevision"]}
    approved = {row["path"]: row for row in receipt["changes"]}

    def authorize(change, original, candidate):
        """Authorize only M rows from this trusted plan; hashes alone are not policy."""
        planned_paths({"files": [{"path": change["path"], "status": "planned"}]})
        return change == approved.get(change["path"]) and change["status"] == "M" and candidate is not None

    manifest = worker.prepare_bundle(
        root, bundle, scope="pipelineruns",
        base_oid=git_read(root, "rev-parse", "HEAD").decode().strip(),
        expected_provenance=expected, authorize=authorize,
        allowed_statuses=("M",), allow_applied=True)
    return manifest, recheck


def publication_ready(result):
    """Mint a token only for successful verification with nonempty trusted changes."""
    return result.get("status") == "verified" and result.get("changeCount", 0) > 0


def publish(root, repository, base, source, bundle, publisher_path, *, do_publish=False):
    """Replay the trusted plan and delegate exact publication correctness checks."""
    plan, recheck = replay(root, bundle, base, source, publisher_path)
    request = build_request(root, repository, base, source, plan, recheck)
    shared = load_publisher(publisher_path)
    if do_publish:
        return shared.publish(root, request)
    shared.verify(root, request)
    return {"status": "verified", "changeCount": len(request["changes"]), "provenance": request["provenance"]}


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
    parser.add_argument("--github-output", type=Path, help="Write verified/nonempty gate for workflow orchestration")
    args = parser.parse_args()
    result = publish(args.path, args.repository, args.base, args.source_sha,
                     args.artifact, args.publisher, do_publish=args.publish)
    if args.github_output:
        with args.github_output.open("a") as output:
            output.write(f"ready_to_publish={str(publication_ready(result)).lower()}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
