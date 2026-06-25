#!/usr/bin/env python3

import argparse
import json
import sys

import json5
import yaml


def main():
    parser = argparse.ArgumentParser(
        description="Generate a Renovate run matrix from config.yaml."
    )
    parser.add_argument(
        "--config-file",
        default="config.yaml",
        help="Path to the Renovate sync config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--org",
        default="red-hat-data-services",
        help="GitHub org prefix (default: red-hat-data-services)",
    )
    parser.add_argument(
        "--repository",
        default="all",
        help="Repository short name to filter to, or 'all' (default: all)",
    )
    parser.add_argument(
        "--branches",
        default="",
        help="Comma-separated branch override (default: use baseBranches from config)",
    )
    parser.add_argument(
        "--github-output",
        default=None,
        help="Path to GITHUB_OUTPUT file (omit for standalone use)",
    )
    args = parser.parse_args()

    with open(args.config_file) as f:
        config = yaml.safe_load(f)

    repo_configs = {}
    for group in config:
        renovate_config = group["renovate-config"]
        for repo in group["sync-repositories"]:
            full_name = repo["name"]
            short_name = full_name.split("/")[-1]
            repo_configs[short_name] = {
                "repo": full_name,
                "config_file": renovate_config,
            }

    repo_configs["konflux-central"] = {
        "repo": f"{args.org}/konflux-central",
        "config_file": ".github/renovate.json",
    }

    if args.repository != "all":
        if args.repository not in repo_configs:
            print(f"error: repository '{args.repository}' not found in {args.config_file}", file=sys.stderr)
            sys.exit(1)
        repo_configs = {args.repository: repo_configs[args.repository]}

    matrix = []
    for short_name, info in sorted(repo_configs.items()):
        config_path = info["config_file"]

        base_branches = []
        try:
            with open(config_path) as f:
                content = f.read()
            if config_path.endswith(".json5"):
                parsed = json5.loads(content)
            else:
                parsed = json.loads(content)
            base_branches = parsed.get("baseBranches", [])
        except Exception as e:
            print(f"warning: could not parse baseBranches from {config_path}: {e}", file=sys.stderr)

        if args.branches.strip():
            base_branches = [b.strip() for b in args.branches.split(",") if b.strip()]

        matrix.append({
            "repo": info["repo"],
            "short_name": short_name,
            "config_file": config_path,
            "base_branches": base_branches,
        })

    print(f"{len(matrix)} repo(s) will be processed", file=sys.stderr)
    for entry in matrix:
        print(f"  {entry['short_name']}: branches={entry['base_branches']}", file=sys.stderr)

    output = json.dumps(matrix)

    if args.github_output:
        with open(args.github_output, "a") as f:
            f.write(f"config={output}\n")

    print(output)


if __name__ == "__main__":
    main()
