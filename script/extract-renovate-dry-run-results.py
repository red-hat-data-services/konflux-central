#!/usr/bin/env python3

import argparse
import json
import re
import sys
from collections import defaultdict


def main():
    parser = argparse.ArgumentParser(
        description="Extract dependency updates from a Renovate JSON log (dry-run mode)."
    )
    parser.add_argument("--repo", required=True, help="Short repo name")
    parser.add_argument("--config", required=True, help="Renovate config file path")
    parser.add_argument("--branches", required=True, help="JSON array of base branches")
    parser.add_argument("--log", required=True, help="Path to Renovate log file")
    parser.add_argument("--output", required=True, help="Path to write the markdown result")
    args = parser.parse_args()

    branches_display = ", ".join(json.loads(args.branches))

    # Parse JSON log lines looking for "flattened updates found" messages
    dep_branches = defaultdict(set)

    try:
        with open(args.log) as f:
            for line in f:
                line = line.strip()
                if "flattened updates found" not in line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                branch = entry.get("baseBranch", "unknown")
                msg = entry.get("msg", "")

                # msg format: "2 flattened updates found: dep1, dep2"
                match = re.match(r"\d+ flattened updates found: (.+)", msg)
                if match:
                    deps = [d.strip() for d in match.group(1).split(",")]
                    for dep in deps:
                        dep_branches[dep].add(branch)
    except FileNotFoundError:
        print(f"warning: log file not found: {args.log}", file=sys.stderr)

    if not dep_branches:
        summary = "No changes"
    else:
        summary = f"{len(dep_branches)} dependency update(s)"

    with open(args.output, "w") as f:
        f.write(f"| {args.repo} | `{args.config}` | {branches_display} | {summary} |\n")

        if dep_branches:
            f.write("\n")
            f.write(f"<details><summary>{args.repo} — updated dependencies</summary>\n\n")
            f.write("| Dependency | Branches |\n")
            f.write("|------------|----------|\n")
            for dep in sorted(dep_branches):
                branches = ", ".join(sorted(dep_branches[dep]))
                f.write(f"| `{dep}` | {branches} |\n")
            f.write("\n</details>\n")


if __name__ == "__main__":
    main()
