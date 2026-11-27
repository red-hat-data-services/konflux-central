#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["ruamel.yaml", "jinja2", "cel-python", "requests"]
# ///
"""Prepare component .tekton sync locally; never commit, push, fetch, or delete."""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def preserved_pr_policy(policy, yaml, branch, repository):
    """Load exact, branch-scoped ownership exceptions; never infer them from names."""
    path = policy / "ownership.yaml"
    if not path.exists():
        return {}
    data = yaml.load(path.read_text())
    if not isinstance(data, dict) or set(data) != {"branches", "preserve_prs"}:
        raise ValueError("ownership policy requires branches and preserve_prs")
    branches, repos = data["branches"], data["preserve_prs"]
    if (not isinstance(branches, list) or not branches
            or any(not isinstance(item, str) or not re.fullmatch(r"rhoai-\d+\.\d+(?:-ea\.\d+)?", item) for item in branches)
            or not isinstance(repos, dict)):
        raise ValueError("invalid ownership policy scope")
    fields = {"reason", "application", "component", "pipeline_url", "image_repository",
              "dockerfile", "path_context"}
    for repo, files in repos.items():
        if (not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", repo)
                or not isinstance(files, dict)):
            raise ValueError("invalid ownership repository")
        for filename, identity in files.items():
            if (not isinstance(filename, str) or Path(filename).name != filename
                    or not filename.endswith((".yaml", ".yml"))
                    or not isinstance(identity, dict) or set(identity) - {"by_branch"} != fields
                    or any(not isinstance(identity[key], str) or not identity[key].strip() for key in fields)):
                raise ValueError("invalid preserved PR identity")
            overrides = identity.get("by_branch", {})
            if not isinstance(overrides, dict):
                raise ValueError("invalid ownership branch override")
            for release, changes in overrides.items():
                if (release not in branches or not isinstance(changes, dict) or not changes
                        or not set(changes) <= fields - {"reason"}
                        or any(not isinstance(value, str) or not value.strip() for value in changes.values())):
                    raise ValueError("invalid ownership branch override")
    if branch not in branches:
        return {}
    return {filename: {**{key: rule[key] for key in fields}, **rule.get("by_branch", {}).get(branch, {})}
            for filename, rule in repos.get(repository, {}).items()}


def pr_identity(data):
    """Extract the exact build identity used to guard preservation."""
    labels = data.get("metadata", {}).get("labels", {})
    spec = data.get("spec", {})
    build = {p["name"]: p.get("value") for p in spec.get("params", [])}
    output = build.get("output-image", "")
    pipeline = spec.get("pipelineRef", {}).get("params", [])
    url = next((p.get("value") for p in pipeline if p.get("name") == "url"), "")
    return {"application": labels.get("appstudio.openshift.io/application"),
            "component": labels.get("appstudio.openshift.io/component"),
            "pipeline_url": url, "dockerfile": build.get("dockerfile"),
            "path_context": build.get("path-context", "."),
            "image_repository": re.split(r"[:@]", output, maxsplit=1)[0] if isinstance(output, str) else None}


def prepare(source, target, tooling, policy, branch, repository, report):
    """Stage authoritative push files plus live PR identity, validate, then copy.

    All generation and safety checks finish before changing the target. Orphans
    block publication rather than leaving duplicate PR triggers unnoticed.
    """
    if not re.fullmatch(r"rhoai-\d+\.\d+(?:-ea\.\d+)?", branch):
        raise ValueError("invalid release branch")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", repository) or repository in (".", ".."):
        raise ValueError("invalid repository")
    sys.path.insert(0, str(tooling.resolve()))
    from ruamel.yaml import YAML
    from lib.tekton import classify_pipeline
    from lib.tekton_config import load_config
    config = load_config(str(policy.resolve()))
    yaml = YAML(typ="safe")
    report.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="stage-", dir=report))
    directory = stage / "pipelineruns" / repository / ".tekton"
    directory.mkdir(parents=True)
    component_source = source / "pipelineruns" / repository
    if not component_source.is_dir():
        raise ValueError(f"missing KC component directory: {component_source}")
    origin = component_source / ".tekton"
    destination = target / ".tekton"
    if origin.is_symlink() or destination.is_symlink():
        raise ValueError("symlink .tekton directory is unsupported")

    def copy_staged():
        """Copy only after all checks finish, rejecting unsafe destination entries."""
        destination.mkdir(parents=True, exist_ok=True)
        for file in directory.iterdir():
            if file.name in exclusions:
                raise ValueError(f"generated file collides with separately managed PR: {file.name}")
            output = destination / file.name
            if (output.exists() and not output.is_file()) or output.is_symlink():
                raise ValueError(f"unsafe target entry: {output}")
        for file in directory.iterdir():
            shutil.copyfile(file, destination / file.name)

    def documents(path):
        """Read strict YAML, accepting resource streams but never bundled PipelineRuns."""
        for file in sorted(path.iterdir()) if path.exists() else []:
            if file.suffix not in (".yaml", ".yml"):
                continue
            if file.is_symlink() or not file.is_file():
                raise ValueError(f"unsafe pipeline file: {file}")
            loaded = list(yaml.load_all(file.read_text()))
            if len(loaded) > 1:
                if any(isinstance(doc, dict) and doc.get("kind") == "PipelineRun" for doc in loaded):
                    raise ValueError(f"multi-document PipelineRun file is unsupported: {file}")
                # Resource-only streams are copied/preserved verbatim, not classified.
                yield file, {}
            else:
                data = loaded[0] if loaded else None
                yield file, data if isinstance(data, dict) else {}

    exclusions = preserved_pr_policy(policy, yaml, branch, repository)
    preserved = {}
    preserved_names = set()

    def check_identity(file, data, rule):
        """Fail closed if an explicitly owned PR changes kind or build identity."""
        identity = pr_identity(data)
        if (data.get("kind") != "PipelineRun"
                or classify_pipeline(file.name, data, config) != "pull-request"
                or any(identity[key] != rule[key] for key in identity)):
            raise ValueError(f"preserved PR identity changed; review ownership: {file.name}")
        return identity

    for file, data in documents(origin):
        if file.name in exclusions:
            raise ValueError(f"KC source conflicts with separately managed PR: {file.name}")
        shutil.copyfile(file, directory / file.name)
    for file, data in documents(destination):
        if file.name in exclusions:
            rule = exclusions[file.name]
            identity = check_identity(file, data, rule)
            preserved[file.name] = {"reason": rule["reason"], "identity": identity}
            preserved_names.add(data.get("metadata", {}).get("name"))
            continue
        if data.get("kind") == "PipelineRun" and classify_pipeline(file.name, data, config) == "pull-request":
            staged = directory / file.name
            if staged.exists():
                old = yaml.load(staged.read_text()) or {}
                if classify_pipeline(file.name, old, config) != "pull-request":
                    raise ValueError(f"live PR filename collides with KC non-PR file: {file.name}")
            shutil.copyfile(file, staged)
    before = {file.name: file.read_bytes() for file, data in documents(directory)
              if classify_pipeline(file.name, data, config) != "pull-request"}
    parents = [file for file, data in documents(directory)
               if data.get("kind") == "PipelineRun" and classify_pipeline(file.name, data, config) == "push"]
    result = {"repository": repository, "branch": branch, "parents": len(parents),
              "stage": str(stage), "preserved_prs": preserved}
    (report / "ownership.json").write_text(json.dumps({"preserved_prs": preserved}, indent=2) + "\n")
    if not parents:
        if any(classify_pipeline(file.name, data, config) == "pull-request"
               for file, data in documents(directory)):
            raise ValueError("PR pipelines exist without any KC push parent")
        copy_staged()
        result["status"] = "no-push-pipelines"
        (report / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
    subprocess.run(["git", "init", "-q", str(stage)], check=True)
    command = [sys.executable, str(tooling.resolve() / "scripts/validate-tekton"),
               "--path", str(stage), "--config-dir", str(policy.resolve()),
               "--branch", branch, "--no-checkout", "--json"]
    for mode, flags in (("fix", ["--fix"]), ("validation", []), ("idempotence", ["--fix"])):
        completed = subprocess.run(command + flags, text=True, capture_output=True)
        (report / (mode + ".json")).write_text(completed.stdout)
        (report / (mode + ".log")).write_text(completed.stderr)
        if completed.returncode:
            raise ValueError(f"{mode} failed; see {report}")
        parsed = json.loads(completed.stdout)
        if mode == "idempotence" and parsed.get("fixed"):
            raise ValueError("generation is not idempotent")
        if any("orphan child" in warning for item in parsed["files"].values()
               for warning in item.get("warnings", [])):
            raise ValueError(f"orphan PR pipeline requires migration review; see {report}")
    for name, content in before.items():
        if (directory / name).read_bytes() != content:
            raise ValueError(f"generation modified non-PR file: {name}")
    names = set()
    children = []
    for file, data in documents(directory):
        if classify_pipeline(file.name, data, config) != "pull-request":
            continue
        name = data.get("metadata", {}).get("name", "")
        rendered = re.sub(r"\{\{\s*pull_request_number\s*\}\}", "1234567890", name)
        if len(rendered) > 63 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", rendered):
            raise ValueError(f"unsafe rendered PipelineRun name ({len(rendered)} chars): {name}")
        if name in names or name in preserved_names:
            raise ValueError(f"duplicate PR PipelineRun name: {name}")
        names.add(name)
        children.append(file.name)
    if len(children) != len(parents):
        raise ValueError("PR coverage does not match push coverage")
    # No cleanup: unknown target files survive, and unclassified orphans block above.
    copy_staged()
    result.update(status="validated", children=children)
    (report / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    """Prepare one matrix repository; publishing remains the existing sync step."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "target", "tooling", "policy", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--repository", required=True)
    args = parser.parse_args()
    try:
        result = prepare(**vars(args))
    except Exception as exc:
        args.report.mkdir(parents=True, exist_ok=True)
        (args.report / "error.json").write_text(json.dumps({
            "repository": args.repository, "branch": args.branch,
            "status": "failed", "error": str(exc)}, indent=2) + "\n")
        raise
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
