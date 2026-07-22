#!/usr/bin/env python3

import argparse
import json
import re
import sys
from collections import defaultdict


def truncate_digest(digest, length=12):
    """Truncate a hex digest for display, preserving sha256: prefix if present."""
    if digest.startswith("sha256:"):
        return f"sha256:{digest[7:7+length]}.."
    return f"{digest[:length]}.."


def format_version(dep, update):
    """Format the From and To columns for an update."""
    current_value = dep.get("currentValue", "")
    current_digest = dep.get("currentDigest", "")
    new_value = update.get("newValue", "")
    new_digest = update.get("newDigest", "")
    update_type = update.get("updateType", "")

    if update_type == "digest" and not new_value:
        from_str = truncate_digest(current_digest) if current_digest else "-"
        to_str = truncate_digest(new_digest) if new_digest else "-"
    elif new_value and new_value != current_value:
        from_str = current_value or "-"
        to_str = new_value or "-"
        if current_digest and new_digest:
            from_str += f" ({truncate_digest(current_digest)})"
            to_str += f" ({truncate_digest(new_digest)})"
    elif new_digest:
        from_str = current_value or ""
        to_str = from_str
        if current_digest:
            from_str += f" ({truncate_digest(current_digest)})"
        if new_digest:
            to_str += f" ({truncate_digest(new_digest)})"
    else:
        from_str = current_value or current_digest or "-"
        to_str = new_value or new_digest or "-"

    return from_str, to_str


def extract_updates_from_package_files(entry):
    """Extract detailed updates from a 'packageFiles with updates' log entry."""
    updates = []
    config = entry.get("config", {})
    base_branch = entry.get("baseBranch", "unknown")

    for manager_name, package_files in config.items():
        if not isinstance(package_files, list):
            continue
        for pf in package_files:
            package_file = pf.get("packageFile", "")
            for dep in pf.get("deps", []):
                if dep.get("skipReason"):
                    continue
                for update in dep.get("updates", []):
                    from_str, to_str = format_version(dep, update)
                    updates.append({
                        "depName": dep.get("depName", "unknown"),
                        "packageFile": package_file,
                        "updateType": update.get("updateType", "unknown"),
                        "from": from_str,
                        "to": to_str,
                        "baseBranch": base_branch,
                    })
    return updates


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

    dep_branches = defaultdict(set)
    detailed_updates = []

    try:
        with open(args.log) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = entry.get("msg", "")

                if msg == "packageFiles with updates":
                    detailed_updates.extend(extract_updates_from_package_files(entry))

                if "flattened updates found" in msg:
                    branch = entry.get("baseBranch", "unknown")
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

            if detailed_updates:
                branches_with_updates = sorted(
                    set(u["baseBranch"] for u in detailed_updates)
                )
                for branch in branches_with_updates:
                    branch_updates = [
                        u for u in detailed_updates if u["baseBranch"] == branch
                    ]
                    if len(branches_with_updates) > 1:
                        f.write(f"**{branch}**\n\n")

                    f.write("| Dependency | File | Update | From | To |\n")
                    f.write("|------------|------|--------|------|---------|\n")
                    for u in branch_updates:
                        dep = f"`{u['depName']}`"
                        pkg = f"`{u['packageFile']}`"
                        f.write(
                            f"| {dep} | {pkg} | {u['updateType']} "
                            f"| {u['from']} | {u['to']} |\n"
                        )
                    f.write("\n")
            else:
                f.write("| Dependency | Branches |\n")
                f.write("|------------|----------|\n")
                for dep in sorted(dep_branches):
                    branches = ", ".join(sorted(dep_branches[dep]))
                    f.write(f"| `{dep}` | {branches} |\n")
                f.write("\n")

            f.write("</details>\n")


if __name__ == "__main__":
    main()
