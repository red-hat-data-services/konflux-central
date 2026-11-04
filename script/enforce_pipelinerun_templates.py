#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["ruamel.yaml", "jinja2", "cel-python", "requests"]
# ///
"""Preview template conformance in isolation; publish only with an explicit command."""
import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

ORG = "red-hat-data-services"
RELEASES = ("rhoai-2.25", "rhoai-3.4", "rhoai-3.5", "rhoai-3.6", "rhoai-3.7-ea.1")
MARKER = "<!-- pipelinerun-template-enforcement -->"


def command(args, cwd=None, check=True):
    """Use argument arrays; never execute repository-provided shell code."""
    return subprocess.run([str(a) for a in args], cwd=cwd, text=True,
                          capture_output=True, check=check)


def git(root, *args):
    """Run Git in a checkout and return trimmed stdout."""
    return command(["git", "-C", root, *args]).stdout.strip()


def check_selection(release, repository):
    """Require an allowlisted release and safe repository name."""
    if release not in RELEASES or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", repository):
        raise ValueError("unsupported release or repository")


def boolean_value(value):
    """Reject ambiguous booleans rather than silently enabling publication."""
    if value not in ("true", "false"):
        raise argparse.ArgumentTypeError("expected true or false")
    return value == "true"


def matrix(source, release, repository, dry_run=True, allow_batch_publication=False):
    """Select managed repositories, requiring extra consent for batch publication."""
    check_selection(release, repository)
    if not isinstance(dry_run, bool) or not isinstance(allow_batch_publication, bool):
        raise ValueError("publication inputs must be booleans")
    if repository == "all" and not dry_run and not allow_batch_publication:
        raise ValueError("batch publication requires explicit allow_batch_publication=true")
    root = source / "pipelineruns"
    if root.is_symlink() or not root.is_dir():
        raise ValueError("unsafe or missing managed-repository inventory")
    repos = sorted(p.name for p in root.iterdir() if p.is_dir() and not p.is_symlink())
    for repo in repos:
        check_selection(release, repo)
    if repository != "all":
        if repository not in repos:
            raise ValueError("repository is not managed by this KC release")
        repos = [repository]
    if not repos:
        raise ValueError("empty repository inventory")
    revision = git(source, "rev-parse", "HEAD")
    return {"include": [{"repository": repo, "inventory_revision": revision} for repo in repos]}


def select_policy(root, name):
    """Restrict policy selection to an existing reviewed template directory."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name):
        raise ValueError("invalid template set")
    path = root if name == "default" else root / name
    if root.is_symlink() or path.is_symlink() or not path.is_dir():
        raise ValueError("unsafe or missing template set")
    return path.resolve()


def snapshot(root):
    """Strictly inventory all Tekton files and all single-document PipelineRuns."""
    directory = root / ".tekton"
    if directory.is_symlink():
        raise ValueError("symlink .tekton directory")
    files, runs = {}, []
    yaml = YAML(typ="safe")
    for path in sorted(directory.rglob("*")) if directory.exists() else []:
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError(f"unsafe Tekton entry: {path.name}")
        if path.is_dir():
            continue
        name = path.relative_to(root).as_posix()
        files[name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "executable": bool(path.stat().st_mode & stat.S_IXUSR)}
        if path.suffix not in (".yaml", ".yml"):
            continue
        try:
            docs = list(yaml.load_all(path.read_text()))
        except (YAMLError, UnicodeError) as exc:
            raise ValueError(f"invalid YAML in {name}: {exc}") from exc
        found = [doc for doc in docs if isinstance(doc, dict) and doc.get("kind") == "PipelineRun"]
        if found and len(docs) != 1:
            raise ValueError(f"bundled PipelineRun stream: {name}")
        if found:
            runs.append(name)
    return files, runs


def verify_changes(before, after, selected, deletions):
    """Allow only selected PipelineRun updates and template-signalled deletions."""
    if set(after) - set(before) or set(before) - set(after) != set(deletions):
        raise ValueError("deletions/additions differ from the template plan")
    changes = {name: "D" for name in deletions}
    for name, value in after.items():
        if value != before[name]:
            if name not in selected or value["executable"] != before[name]["executable"]:
                raise ValueError("unselected file or executable mode changed")
            changes[name] = "M"
    return dict(sorted(changes.items()))


def validate(stage, tooling, policy, release, report, mode, flags, allow_drift=False):
    """Retain validator reports, accepting pre-fix drift only when requested."""
    result = command([sys.executable, tooling / "scripts/validate-tekton",
                      "--path", stage, "--config-dir", policy, "--branch", release,
                      "--no-checkout", "--json", *flags], check=False)
    (report / f"{mode}.json").write_text(result.stdout)
    (report / f"{mode}.log").write_text(result.stderr)
    # The CLI exits successfully without JSON when no PipelineRuns remain.
    if not result.stdout.strip() and result.returncode == 0:
        return {"files": {}}
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"validator {mode} returned no valid JSON; inspect report") from exc
    if not isinstance(parsed.get("files"), dict) or (result.returncode and not allow_drift):
        raise ValueError(f"validator {mode} failed; inspect report")
    return parsed


def policy_digest(policy):
    """Hash policy contents and reject unsupported layouts/parents/generation."""
    config = policy / "config.yaml"
    if config.is_symlink() or not config.is_file():
        raise ValueError("unsafe or missing template configuration")
    raw = YAML(typ="safe").load(config.read_text())
    if (not isinstance(raw, dict) or raw.get("tekton_layout", "flat") != "flat"
            or raw.get("parent_ref") or any("generate" in t for t in raw.get("templates", []))):
        raise ValueError("requires flat existing-file templates with working-tree parents")
    for template in raw.get("templates", []):
        path = policy / template["file"]
        if not path.resolve().is_relative_to(policy.resolve()) or path.is_symlink():
            raise ValueError("template file escapes the selected set")
    digest = hashlib.sha256()
    for path in sorted(policy.rglob("*")):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("unsafe template set entry")
        if path.is_file():
            digest.update(path.relative_to(policy).as_posix().encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def prepare(target, tooling, policy, release, repository, report, inventory_revision=None,
            tracking_issue=None, template_set=None):
    """Create a verified template-conformance patch without changing the target."""
    check_selection(release, repository)
    target, tooling, policy, report = (p.resolve() for p in (target, tooling, policy, report))
    template_set = template_set or policy.name
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", template_set):
        raise ValueError("invalid template set")
    if tracking_issue and not re.fullmatch(r"[A-Z][A-Z0-9]+-\d+", tracking_issue):
        raise ValueError("invalid tracking issue")
    if inventory_revision is not None and not re.fullmatch(r"[0-9a-f]{40,64}", inventory_revision):
        raise ValueError("invalid inventory revision")
    if report.is_relative_to(target) or target.is_relative_to(report):
        raise ValueError("report and target must be disjoint")
    if git(target, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("target checkout is not clean")
    before, runs = snapshot(target)
    tracked = set(command(["git", "-C", target, "ls-files", "-z", "--", ".tekton"]).stdout.split("\0")) - {""}
    if tracked != set(before):
        raise ValueError("Tekton files differ from the tracked checkout")
    digest = policy_digest(policy)
    report.mkdir(parents=True, exist_ok=False)
    stage = report / "stage"
    stage.mkdir()
    if (target / ".tekton").exists():
        shutil.copytree(target / ".tekton", stage / ".tekton")
    git(stage, "init", "-q")
    if before:
        git(stage, "add", "-f", "--", ".tekton")
    result = {"release": release, "repository": repository, "inventory_revision": inventory_revision,
              "base": git(target, "rev-parse", "HEAD"), "template_set": template_set,
              "policy_revision": git(policy, "rev-parse", "HEAD"), "tracking_issue": tracking_issue,
              "tooling_revision": git(tooling, "rev-parse", "HEAD"), "policy_digest": digest,
              "before": before}
    (report / "inventory.json").write_text(json.dumps(result, indent=2) + "\n")
    initial = validate(stage, tooling, policy, release, report, "initial", [], allow_drift=True)
    selected, deletions = [], []
    for name, details in initial["files"].items():
        relative = ".tekton/" + name
        if relative not in runs or Path(name).name != name:
            raise ValueError("validator reported a non-PipelineRun or unsafe path")
        selected.append(relative)
        if details.get("delete"):
            deletions.append(relative)
    validate(stage, tooling, policy, release, report, "preview", ["--fix", "--dry-run"])
    if snapshot(stage)[0] != before:
        raise ValueError("validator dry run modified files")
    validate(stage, tooling, policy, release, report, "fix", ["--fix"])
    after, remaining = snapshot(stage)
    changes = verify_changes(before, after, selected, deletions)
    if not set(name for name, status in changes.items() if status == "M") <= set(remaining):
        raise ValueError("template changed a PipelineRun into another resource kind")
    validate(stage, tooling, policy, release, report, "validation", [])
    validate(stage, tooling, policy, release, report, "idempotence", ["--fix"])
    if snapshot(stage)[0] != after:
        raise ValueError("template enforcement is not idempotent")
    if snapshot(target)[0] != before:
        raise ValueError("target changed during preparation")
    patch = command(["git", "-C", stage, "diff", "--binary", "--", ".tekton"]).stdout
    (report / "enforcement.patch").write_text(patch)
    result.update(status="ready" if changes else "no-changes", after=after, changes=changes,
                  selected_files=selected, deleted_files=sorted(deletions),
                  updated_files=[name for name, status in changes.items() if status == "M"],
                  patch_sha256=hashlib.sha256(patch.encode()).hexdigest(),
                  branch=f"automation/enforce-pipelineruns-{release}-{template_set}-{digest[:12]}")
    (report / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def existing_pr(repo, release, branch, template_set):
    """Reuse matching open policy PRs without resurrecting a closed branch."""
    prs = json.loads(command(["gh", "pr", "list", "--repo", repo, "--base", release,
                              "--state", "all", "--limit", "1000", "--json",
                              "url,state,headRefName,body"]).stdout)
    if len(prs) >= 1000:
        raise ValueError("PR inventory may be truncated; review before publication")
    marker = f"<!-- template-set:{template_set} -->"
    for pr in prs:
        if pr["state"] == "OPEN" and (pr["headRefName"] == branch or
                (MARKER in pr["body"] and marker in pr["body"])):
            return pr["url"]
    if any(pr["headRefName"] == branch for pr in prs):
        raise ValueError("enforcement branch has a prior PR; review its disposition explicitly")
    return None


def publish(target, report):
    """Recheck the preview's scope and exact bytes before any remote mutation."""
    target, report = target.resolve(), report.resolve()
    if report.is_relative_to(target) or target.is_relative_to(report):
        raise ValueError("report and target must be disjoint")
    data = json.loads((report / "result.json").read_text())
    check_selection(data["release"], data["repository"])
    if data["status"] != "ready":
        return {"status": "no-changes"}
    if (not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", data["template_set"])
            or not re.fullmatch(r"[0-9a-f]{64}", data["policy_digest"])
            or data["branch"] != f"automation/enforce-pipelineruns-{data['release']}-{data['template_set']}-{data['policy_digest'][:12]}"):
        raise ValueError("invalid publication branch identity")
    repo = f"{ORG}/{data['repository']}"
    existing = existing_pr(repo, data["release"], data["branch"], data["template_set"])
    if existing:
        return {"status": "existing-pr", "url": existing}
    if git(target, "status", "--porcelain", "--untracked-files=all") or git(target, "rev-parse", "HEAD") != data["base"]:
        raise ValueError("publication checkout differs from preview base")
    current = command(["gh", "api", f"repos/{repo}/git/ref/heads/{data['release']}", "--jq", ".object.sha"]).stdout.strip()
    if current != data["base"]:
        raise ValueError("remote release advanced; prepare again")
    if snapshot(target)[0] != data["before"]:
        raise ValueError("publication files differ from preview")
    patch = (report / "enforcement.patch").read_text()
    if hashlib.sha256(patch.encode()).hexdigest() != data["patch_sha256"]:
        raise ValueError("preview patch changed")
    git(target, "switch", "--create", data["branch"])
    git(target, "apply", "--index", str((report / "enforcement.patch").resolve()))
    after, _ = snapshot(target)
    changes = verify_changes(data["before"], after, data["selected_files"], data["deleted_files"])
    if changes != data["changes"] or after != data["after"]:
        raise ValueError("patch differs from validated template output")
    changed = command(["git", "-C", target, "diff", "--cached", "--name-status", "-z"]).stdout
    expected = "".join(status + "\0" + name + "\0" for name, status in sorted(changes.items()))
    if changed != expected:
        raise ValueError("staged patch contains changes outside the template manifest")
    git(target, "diff", "--cached", "--check")
    title = f"chore: enforce PipelineRun templates on {data['release']}"
    git(target, "commit", "--signoff", "-m", title)
    git(target, "push", f"https://github.com/{repo}.git", f"HEAD:refs/heads/{data['branch']}")
    body = f"{MARKER}\n<!-- template-set:{data['template_set']} -->\n\nApply template set `{data['template_set']}`.\n\n"
    if data.get("tracking_issue"):
        issue = data["tracking_issue"]
        body += f"Tracking: [{issue}](https://redhat.atlassian.net/browse/{issue})\n\n"
    body += "\n".join(f"- {status}: `{name}`" for name, status in changes.items())
    body += (f"\n\nIndependent validation and idempotence passed; unselected files unchanged."
             f"\n\nBase: `{data['base']}`\nPolicy: `{data['policy_revision']}`"
             f"\nInventory: `{data.get('inventory_revision') or 'not supplied'}`"
             f"\nPolicy digest: `{data['policy_digest']}`\nValidator: `{data['tooling_revision']}`\n")
    server, repository, run = (os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    if all((server, repository, run)):
        body += f"\n[Workflow run]({server}/{repository}/actions/runs/{run})\n"
    body_path = report / "pr.md"
    body_path.write_text(body)
    url = command(["gh", "pr", "create", "--repo", repo, "--base", data["release"],
                   "--head", data["branch"], "--title", title, "--body-file", body_path]).stdout.strip()
    return {"status": "published", "url": url}


def main():
    """Dispatch preview/publication and preserve safe failure/provenance receipts."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    selection = commands.add_parser("matrix")
    selection.add_argument("--source", type=Path, required=True)
    selection.add_argument("--dry-run", type=boolean_value, default=True)
    selection.add_argument("--allow-batch-publication", type=boolean_value, default=False)
    for sub in (selection, commands.add_parser("prepare")):
        sub.add_argument("--release", choices=RELEASES, required=True)
        sub.add_argument("--repository", required=True)
    prep = commands.choices["prepare"]
    prep.add_argument("--inventory-revision")
    prep.add_argument("--tracking-issue")
    prep.add_argument("--template-set", required=True)
    for name in ("target", "tooling", "policy-root", "report"):
        prep.add_argument("--" + name, type=Path, required=True)
    pub = commands.add_parser("publish")
    for name in ("target", "report"):
        pub.add_argument("--" + name, type=Path, required=True)
    args = vars(parser.parse_args())
    mode = args.pop("mode")
    error_report = args.get("report")
    if error_report is not None:
        error_report = error_report.resolve()
        target = args["target"].resolve()
        if (error_report.is_relative_to(target) or target.is_relative_to(error_report)
                or (mode == "prepare" and error_report.exists())):
            error_report = None
    try:
        if mode == "prepare":
            args["policy"] = select_policy(args.pop("policy_root"), args["template_set"])
        result = {"matrix": matrix, "prepare": prepare, "publish": publish}[mode](**args)
    except Exception as exc:
        if error_report is not None:
            error_report.mkdir(parents=True, exist_ok=True)
            diagnostic = {"error": str(exc), **{key: args[key] for key in
                          ("release", "repository", "inventory_revision", "template_set") if key in args}}
            for key, argument in (("base", "target"), ("policy_revision", "policy"), ("tooling_revision", "tooling")):
                if argument in args:
                    resolved = command(["git", "-C", args[argument], "rev-parse", "HEAD"], check=False)
                    diagnostic[key] = resolved.stdout.strip() if resolved.returncode == 0 else None
            (error_report / "error.json").write_text(json.dumps(diagnostic, indent=2) + "\n")
        raise
    if mode == "publish":
        (args["report"] / "publication.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    if mode == "prepare" and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            for key in ("base", "status"):
                stream.write(f"{key}={result[key]}\n")


if __name__ == "__main__":
    main()
