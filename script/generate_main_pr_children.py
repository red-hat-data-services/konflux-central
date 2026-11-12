#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["ruamel.yaml", "jinja2", "cel-python", "requests"]
# ///
"""Create missing main PR children using the release flow's shared planner.

Read release parents through Git without checkout. Existing main children remain
owned by validate-tekton's normal cross-branch update pass, not regenerated.
No fetch, commit, push, or parent-file writes.
"""
import argparse
from io import StringIO
from pathlib import Path
import subprocess
import sys


def generate(repository, tooling, policy):
    sys.path.insert(0, str(tooling.resolve()))
    from ruamel.yaml import YAML
    from lib.tekton import classify_pipeline
    from lib.tekton_children import plan_children
    from lib.tekton_config import load_config
    from lib.tekton_filters import shorten_name

    config = load_config(str(policy.resolve()))
    if not config.parent_ref:
        raise ValueError("main PR policy requires parent_ref")
    # Reuse release generation's seed/field safety policy, with main's templates
    # and matching rules. Do not enable generation on the cross-branch validator.
    release = load_config(str(policy.resolve().parent / "release-pr"))
    config.get_template("pull-request").generate = dict(release.get_template("pull-request").generate)
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096

    def git(*args):
        return subprocess.run(["git", *args], cwd=repository, check=True,
                              capture_output=True, text=True).stdout

    pipelines = {}
    existing = set()
    for file in sorted((repository / "pipelineruns").glob("*/.tekton/*")):
        if file.suffix not in (".yaml", ".yml"):
            continue
        if file.is_symlink():
            raise ValueError(f"symlink PipelineRun: {file}")
        docs = list(yaml.load_all(file.read_text()))
        if any(isinstance(doc, dict) and doc.get("kind") == "PipelineRun" for doc in docs):
            if len(docs) != 1:
                raise ValueError(f"multi-document PipelineRun: {file}")
            if classify_pipeline(file.name, docs[0], config) == "pull-request":
                pipelines[file] = docs[0]
                existing.add(file)

    for name in git("ls-tree", "-r", "--name-only", config.parent_ref, "--", "pipelineruns/").splitlines():
        relative = Path(name)
        if (len(relative.parts) != 4 or relative.parts[2] != ".tekton"
                or relative.suffix not in (".yaml", ".yml")):
            continue
        docs = list(yaml.load_all(StringIO(git("show", f"{config.parent_ref}:{name}"))))
        parents = [doc for doc in docs if isinstance(doc, dict) and doc.get("kind") == "PipelineRun"
                   and classify_pipeline(relative.name, doc, config) == "push"]
        if parents:
            if len(docs) != 1:
                raise ValueError(f"multi-document parent: {name}")
            path = repository / relative
            if path in pipelines:
                raise ValueError(f"parent/child path collision: {name}")
            pipelines[path] = parents[0]

    expected, orphans = plan_children(pipelines, config, "main")
    # Legacy main-only PRs need not have release parents. Preserve them; the
    # normal validator retains its existing diagnostics for these files.
    for path in orphans:
        print(f"Preserving main-only PR without release parent: {path.relative_to(repository)}")
    pending = {}
    names = {data.get("metadata", {}).get("name") for path, data in pipelines.items() if path in existing}
    for path, data in expected.items():
        if path in existing:
            continue
        prefix = data["metadata"]["name"].split("-on-pull-request-")[0]
        name = shorten_name(prefix, 36) + "-on-pull-request-{{pull_request_number}}"
        if name in names:
            raise ValueError(f"duplicate PR PipelineRun name: {name}")
        names.add(name)
        data["metadata"]["name"] = name
        if path.exists() or path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise ValueError(f"unsafe generated target: {path}")
        pending[path] = data
    # Finish planning and collision checks before writing any child.
    for path, data in pending.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x") as stream:
            yaml.dump(data, stream)
        print(f"Created {path.relative_to(repository)}")
    return list(pending)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--tooling", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path, required=True)
    args = parser.parse_args()
    generate(args.path.resolve(), args.tooling, args.config_dir)


if __name__ == "__main__":
    main()
