#!/usr/bin/env python3

import argparse
import json
import sys

import yaml


def resolve_source_config(dist_config_path):
    """Extract the local source config path from a distribution config's extends."""
    try:
        with open(dist_config_path) as f:
            parsed = json.loads(f.read())
        for ref in parsed.get("extends", []):
            if "konflux-central//" in ref:
                return ref.split("//", 1)[1]
    except Exception:
        pass
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Given a list of changed files, output which repos are affected."
    )
    parser.add_argument(
        "--config-file",
        default="config.yaml",
        help="Path to the Renovate sync config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "changed_files",
        nargs="*",
        help="Changed file paths (also reads from stdin if no args given)",
    )
    args = parser.parse_args()

    if args.changed_files:
        changed = set(args.changed_files)
    else:
        changed = set(line.strip() for line in sys.stdin if line.strip())

    if not changed:
        print("all")
        return

    # If config.yaml itself changed, all repos are affected
    if args.config_file in changed or "config.yaml" in changed:
        print("all")
        return

    with open(args.config_file) as f:
        config = yaml.safe_load(f)

    affected = set()

    for group in config:
        dist_config = group["renovate-config"]
        repos_in_group = {repo["name"].split("/")[-1] for repo in group["sync-repositories"]}

        # Check if the distribution config itself changed
        if dist_config in changed:
            affected.update(repos_in_group)
            continue

        # Check if the source config that this distribution extends changed
        source_path = resolve_source_config(dist_config)
        if source_path and source_path in changed:
            affected.update(repos_in_group)

    # Check if konflux-central's own config or its source changed
    if ".github/renovate.json" in changed:
        affected.add("konflux-central")
    source_path = resolve_source_config(".github/renovate.json")
    if source_path and source_path in changed:
        affected.add("konflux-central")

    if not affected:
        print("none")
    else:
        for repo in sorted(affected):
            print(repo)


if __name__ == "__main__":
    main()
