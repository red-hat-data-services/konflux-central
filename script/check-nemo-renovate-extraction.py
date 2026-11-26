#!/usr/bin/env python3
"""Check NeMo's pip-compile file selection with a real Renovate extraction run.

This projects only the Python policy, since upstream Renovate does not include
MintMaker's RPM managers. It does not verify deployed command permissions.
"""

import argparse
import copy
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def extract(command, fixture, config, log_path, base_dir):
    (fixture / "renovate.json").write_text(json.dumps(config, indent=2) + "\n")
    env = os.environ.copy()
    env.update(
        LOG_LEVEL="debug", LOG_FORMAT="json",
        RENOVATE_BASE_DIR=str(base_dir), RENOVATE_PRINT_CONFIG="true",
    )
    with log_path.open("w") as log:
        subprocess.run(
            [*command, "--platform=local", "--dry-run=extract"],
            cwd=fixture, env=env, stdout=log, stderr=subprocess.STDOUT, check=True,
        )
    records = []
    for line in log_path.read_text().splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    errors = [r for r in records if r.get("errors") or r.get("configError")
              or r.get("level", 0) >= 50]
    if errors:
        raise RuntimeError(f"Renovate reported errors; inspect {log_path}")
    results = [r for r in records if "packageFiles" in r]
    if not results:
        raise RuntimeError(f"No extraction result; inspect {log_path}")
    files = results[-1]["packageFiles"].get("pip-compile", [])
    return {
        "lockFiles": sorted({lock for f in files for lock in f.get("lockFiles", [])}),
        "depCount": sum(len(f["deps"]) for f in files),
        "selection": [r["msg"] for r in records
                      if r.get("msg", "").startswith("Matched ")],
        "warnings": sorted({r.get("msg", "") for r in records if r.get("level") == 40}),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="NeMo counterpart checkout")
    parser.add_argument("--output", type=Path, required=True, help="Directory for logs and summary")
    parser.add_argument("--renovate", default="renovate", help="Renovate executable or JS entry point")
    parser.add_argument("--node", help="Node executable, if --renovate is a JS entry point")
    parser.add_argument("--preset-repository", default="red-hat-data-services/konflux-central")
    parser.add_argument("--preset-ref", default="rhoai-3.6")
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    policy = json.loads((ROOT / "renovate/nemo-guardrails-renovate.json5").read_text())
    # Keep Python settings unchanged; omit presets for unrelated RPM/image managers.
    config = {key: policy[key] for key in ("pip-compile", "packageRules")}
    shared = "renovate/uv-pip-compile-renovate.json5"
    config.update(
        extends=[f"github>{args.preset_repository}//{shared}#{args.preset_ref}"],
        enabledManagers=["pip-compile"], baseBranchPatterns=["rhoai-3.6"],
    )
    command = ([args.node] if args.node else []) + [args.renovate]
    version = subprocess.check_output([*command, "--version"], text=True).strip()
    with tempfile.TemporaryDirectory(prefix="nemo-extraction-") as temporary:
        fixture = Path(temporary) / "source"
        fixture.mkdir()
        for name in ("pyproject.toml", "requirements.txt"):
            shutil.copyfile(args.source / name, fixture / name)
        shutil.copytree(args.source / "dependencies", fixture / "dependencies")
        # Valid decoys establish that a narrower managerFilePatterns is insufficient.
        (fixture / "requirements-dev.txt").write_text(
            (fixture / "requirements.txt").read_text().replace(
                "--output-file=requirements.txt", "--output-file=requirements-dev.txt"
            )
        )
        nested = fixture / "nested"
        nested.mkdir()
        for name in ("pyproject.toml", "requirements.txt"):
            shutil.copyfile(fixture / name, nested / name)
        shutil.copytree(fixture / "dependencies", nested / "dependencies")
        selected = extract(command, fixture, config, args.output / "restricted.log",
                           Path(temporary) / "restricted-cache")
        if (selected["lockFiles"] != ["requirements.txt"] or selected["depCount"] == 0
                or selected["selection"] != ["Matched 1 file(s) for manager pip-compile"]):
            raise RuntimeError(f"Unexpected restricted extraction: {selected}")
        control = copy.deepcopy(config)
        del control["pip-compile"]["includePaths"]
        unrestricted = extract(command, fixture, control, args.output / "control.log",
                               Path(temporary) / "control-cache")
        expected = ["nested/requirements.txt", "requirements-dev.txt", "requirements.txt"]
        if (unrestricted["lockFiles"] != expected
                or unrestricted["selection"] != ["Matched 3 file(s) for manager pip-compile"]):
            raise RuntimeError(f"Control failed to extract the decoys: {unrestricted}")
    summary = {"renovateVersion": version, "extends": config["extends"],
               "restricted": selected, "withoutIncludePaths": unrestricted}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
