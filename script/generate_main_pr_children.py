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
import re
import subprocess
import sys


def load_exclusions(policy, yaml):
    """Require a reviewed creation policy; malformed/missing policy fails closed."""
    data = yaml.load((policy / "generation.yaml").read_text())
    if not isinstance(data, dict) or set(data) != {"excluded_repositories"}:
        raise ValueError("generation policy requires only excluded_repositories")
    exclusions = data["excluded_repositories"]
    if not isinstance(exclusions, dict):
        raise ValueError("excluded_repositories must map exact repository names to reasons")
    for repository, reason in exclusions.items():
        if (not isinstance(repository, str)
                or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", repository)
                or not isinstance(reason, str) or not reason.strip()):
            raise ValueError("exclusions require exact repository names and nonempty reasons")
    return exclusions


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
    exclusions = load_exclusions(policy, yaml)
    for name, reason in sorted(exclusions.items()):
        print(f"PR creation excluded for {name}: {reason}")

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
        if relative.parts[1] in exclusions:
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

    if not any(classify_pipeline(path.name, data, config) == "push"
               for path, data in pipelines.items()):
        print("No eligible release push parents; no PR children created.")
        return []
    expected, orphans = plan_children(pipelines, config, "main")
    # Legacy main-only PRs need not have release parents. Preserve them; the
    # normal validator retains its existing diagnostics for these files.
    for path in orphans:
        relative = path.relative_to(repository)
        if relative.parts[1] in exclusions:
            print(f"Preserving existing PR in excluded repository: {relative}")
        else:
            print(f"Preserving main-only PR without release parent: {relative}")
    pending = {}
    names = {data.get("metadata", {}).get("name") for path, data in pipelines.items() if path in existing}
    for path, data in expected.items():
        if path in existing:
            continue
        if path.relative_to(repository).parts[1] in exclusions:
            raise ValueError(f"planner attempted PR creation in excluded repository: {path}")
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
