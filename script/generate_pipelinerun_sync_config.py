#!/usr/bin/env python3

import argparse
from pathlib import Path
from ruyaml import YAML

def generate_sync_config(pipelinerun_dir: Path, output_file: Path, github_org: str):
    if not pipelinerun_dir.exists() or not pipelinerun_dir.is_dir():
        raise FileNotFoundError(f"Directory not found: {pipelinerun_dir}")

    repos = sorted([
        f.name for f in pipelinerun_dir.iterdir() if f.is_dir()
    ])

    yaml = YAML()
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.preserve_quotes = True

    sync_config = [{"repo": f"{github_org}/{repo}"} for repo in repos]

    with output_file.open("w") as f:
        yaml.dump(sync_config, f)

    print(f"Sync config generated successfully in {output_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate sync-config.yaml from pipelineruns folder structure.")
    parser.add_argument(
        "--pipelinerun-dir",
        type=Path,
        default=Path("pipelineruns"),
        help="Path to pipelineruns directory"
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=Path("sync-config.yaml"),
        help="Output YAML file path"
    )
    parser.add_argument(
        "--github-org",
        type=str,
        default="red-hat-data-services",
        help="GitHub organization name"
    )
    args = parser.parse_args()
    print("Generating Sync Config...")
    generate_sync_config(args.pipelinerun_dir, args.output_file, args.github_org)
