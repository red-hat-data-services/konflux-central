#!/usr/bin/env python3

import argparse
import logging
from pathlib import Path
from ruyaml import YAML

# Configure logging
logging.basicConfig(
    format="%(levelname)s: %(message)s",
    level=logging.INFO
)

def main():
    parser = argparse.ArgumentParser(description="Update renovate-config options in a GitHub workflow YAML.")
    parser.add_argument(
        "--workflow-file",
        type=Path,
        default=Path(".github/workflows/sync-pipelineruns.yml"),
        help="Path to the GitHub Actions workflow YAML file"
    )
    parser.add_argument(
        "--pipelinerun-dir",
        type=Path,
        default=Path("pipelineruns"),
        help="Directory containing pipelinerun folders"
    )

    args = parser.parse_args()
    yaml_path = args.workflow_file
    pipelinerun_dir = args.pipelinerun_dir

    # Check file existence
    if not yaml_path.exists():
        logging.error(f"Workflow file not found: {yaml_path}")
        exit(1)
    if not pipelinerun_dir.exists():
        logging.error(f"Pipelinerun directory not found: {pipelinerun_dir}")
        exit(1)

    # Get sorted list of folder names
    options = sorted([
        f.name for f in pipelinerun_dir.iterdir() if f.is_dir()
    ])
    options.insert(0, "all")  # Prepend 'all'

    # Load YAML with formatting
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 4096  # Prevent line splitting

    logging.info(f"Reading workflow file: {yaml_path}")
    with yaml_path.open("r") as f:
        data = yaml.load(f)

    try:
        data["on"]["workflow_dispatch"]["inputs"]["repository"]["options"] = options
    except KeyError:
        logging.error("Failed to update: Path 'on.workflow_dispatch.inputs.repository.options' not found.")
        exit(1)

    # Write YAML back
    with yaml_path.open("w") as f:
        yaml.dump(data, f)

    logging.info("Updated repository list successfully.")

if __name__ == "__main__":
    main()
