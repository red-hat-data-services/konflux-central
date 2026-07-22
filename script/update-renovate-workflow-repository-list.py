#!/usr/bin/env python3

import argparse
import logging
from pathlib import Path

import yaml
from ruyaml import YAML as RuYAML

logging.basicConfig(
    format="%(levelname)s: %(message)s",
    level=logging.INFO
)


def main():
    parser = argparse.ArgumentParser(
        description="Update the repository choice list in the run-renovate workflow from config.yaml."
    )
    parser.add_argument(
        "--config-file",
        type=Path,
        default=Path("config.yaml"),
        help="Path to the Renovate sync config.yaml",
    )
    parser.add_argument(
        "--workflow-file",
        type=Path,
        default=Path(".github/workflows/run-renovate.yml"),
        help="Path to the run-renovate GitHub Actions workflow YAML file",
    )
    args = parser.parse_args()

    if not args.config_file.exists():
        logging.error(f"Config file not found: {args.config_file}")
        exit(1)
    if not args.workflow_file.exists():
        logging.error(f"Workflow file not found: {args.workflow_file}")
        exit(1)

    with args.config_file.open() as f:
        config = yaml.safe_load(f)

    repos = set()
    for group in config:
        for repo in group["sync-repositories"]:
            short_name = repo["name"].split("/")[-1]
            repos.add(short_name)
    repos.add("konflux-central")

    options = sorted(repos)
    options.insert(0, "all")

    ruyaml = RuYAML()
    ruyaml.preserve_quotes = True
    ruyaml.indent(mapping=2, sequence=4, offset=2)
    ruyaml.width = 4096

    logging.info(f"Reading workflow file: {args.workflow_file}")
    with args.workflow_file.open("r") as f:
        data = ruyaml.load(f)

    try:
        data["on"]["workflow_dispatch"]["inputs"]["repository"]["options"] = options
    except KeyError:
        logging.error("Failed to update: Path 'on.workflow_dispatch.inputs.repository.options' not found.")
        exit(1)

    with args.workflow_file.open("w") as f:
        ruyaml.dump(data, f)

    logging.info(f"Updated repository list with {len(options)} entries (including 'all').")


if __name__ == "__main__":
    main()
