#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["ruamel.yaml", "jinja2", "cel-python", "requests"]
# ///
"""Apply central conformance to KC main PR children before publication.

Only PR files are eligible; plan and validate every change before writing.
No fetch, commit, push, deletion, or release-pr template changes.
"""
import argparse
from pathlib import Path
import sys


def conform(repository, tooling, check_only=False):
    sys.path.insert(0, str(tooling.resolve()))
    from ruamel.yaml import YAML
    from lib.tekton import classify_pipeline, render_and_merge
    from lib.tekton_validation import selection
    from lib.tekton_yaml import localized_yaml
    import scan_resource_policy as policy

    yaml = YAML(typ="safe")
    context = {"branch": "main", "documents": {}}
    for relative in policy.CONFIG.context_paths:
        path = repository / relative
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise ValueError(f"symlink context: {path}")
        if path.exists():
            context["documents"][relative] = yaml.load(path.read_text())
    pending = {}
    for path in sorted((repository / "pipelineruns").glob("*/.tekton/*")):
        if path.suffix not in (".yaml", ".yml"):
            continue
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise ValueError(f"symlink PipelineRun: {path}")
        source = path.read_text()
        data = yaml.load(source)
        if not isinstance(data, dict) or data.get("kind") != "PipelineRun":
            continue
        if classify_pipeline(path.name, data, policy.CONFIG) != "pull-request":
            continue
        decision = selection(policy.CONFIG, data, path.name, "main", context)
        if decision["status"] != "applicable":
            raise ValueError(f"PR conformance requires eligibility: {path}: {decision}")
        template = policy.CONFIG.get_template("pull-request").file
        candidate = render_and_merge(str(policy.POLICY_DIRECTORY), template, data, path.name,
                                     branch="main", mode="fix")
        policy.check(data, candidate, context)
        errors = []
        render_and_merge(str(policy.POLICY_DIRECTORY), template, candidate, path.name,
                         branch="main", mode="validate", errors=errors)
        repeated = render_and_merge(str(policy.POLICY_DIRECTORY), template, candidate, path.name,
                                    branch="main", mode="fix")
        if errors or repeated != candidate:
            raise ValueError(f"PR conformance failed validation/idempotence: {path}: {errors}")
        text = localized_yaml(source, candidate)
        if yaml.load(text) != candidate:
            raise ValueError(f"PR conformance serialization mismatch: {path}")
        if text != source:
            pending[path] = text
    if check_only and pending:
        raise ValueError(f"PR conformance still requires changes: {list(pending)}")
    for path, text in pending.items():
        path.write_text(text)
        print(f"Conformed {path.relative_to(repository)}")
    return list(pending)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--tooling", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    conform(args.path.resolve(), args.tooling, args.check)


if __name__ == "__main__":
    main()
