#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["ruamel.yaml", "jinja2", "cel-python", "requests"]
# ///
"""Preview namespace cleanup locally; publish only via the explicit publish command."""
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

NAMESPACE = "open-data-hub-tenant"
ORG = "red-hat-data-services"
RELEASES = ("rhoai-2.25", "rhoai-3.4", "rhoai-3.5", "rhoai-3.6", "rhoai-3.7-ea.1")
JIRA = "https://redhat.atlassian.net/browse/RHAI-19583"
MARKER = "<!-- cleanup-odh-tenant -->"


def command(args, cwd=None, check=True):
    """Use argument arrays only; never execute repository-provided shell code."""
    return subprocess.run([str(a) for a in args], cwd=cwd, text=True,
                          capture_output=True, check=check)


def git(root, *args):
    return command(["git", "-C", root, *args]).stdout.strip()


def check_selection(release, repository):
    if release not in RELEASES or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", repository):
        raise ValueError("unsupported release or repository")


def matrix(source, release, repository):
    """Use the selected release's managed directories, as KC sync does."""
    check_selection(release, repository)
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


def snapshot(root):
    """Record all Tekton bytes/modes; strictly parse every YAML document first."""
    directory = root / ".tekton"
    if directory.is_symlink():
        raise ValueError("symlink .tekton directory")
    files, candidates = {}, []
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
        runs = [doc for doc in docs if isinstance(doc, dict) and doc.get("kind") == "PipelineRun"]
        if runs and len(docs) != 1:
            raise ValueError(f"bundled PipelineRun stream: {name}")
        if runs and runs[0].get("metadata", {}).get("namespace") == NAMESPACE:
            candidates.append(name)
    return files, candidates


def verify_deletions(before, after, expected):
    if set(before) - set(after) != set(expected) or set(after) - set(before):
        raise ValueError("deletions do not exactly match namespace inventory")
    if any(before[name] != value for name, value in after.items()):
        raise ValueError("retained file bytes or executable mode changed")


def validate(stage, tooling, policy, release, report, mode, flags):
    result = command([sys.executable, tooling / "scripts/validate-tekton",
                      "--path", stage, "--config-dir", policy, "--branch", release,
                      "--no-checkout", "--namespace", NAMESPACE, "--json", *flags], check=False)
    (report / f"{mode}.json").write_text(result.stdout)
    (report / f"{mode}.log").write_text(result.stderr)
    if result.returncode:
        raise ValueError(f"validator {mode} failed; inspect report")


def prepare(target, tooling, policy, release, repository, report, inventory_revision=None):
    """Always preview: only a disposable stage changes, never the target checkout."""
    check_selection(release, repository)
    if inventory_revision is not None and not re.fullmatch(r"[0-9a-f]{40,64}", inventory_revision):
        raise ValueError("invalid inventory revision")
    target, tooling, policy, report = (p.resolve() for p in (target, tooling, policy, report))
    if report.is_relative_to(target) or target.is_relative_to(report):
        raise ValueError("report and target must be disjoint")
    if git(target, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("target checkout is not clean")
    before, expected = snapshot(target)
    tracked = set(command(["git", "-C", target, "ls-files", "-z", "--", ".tekton"]).stdout.split("\0")) - {""}
    if tracked != set(before):
        raise ValueError("Tekton files differ from the tracked checkout")
    report.mkdir(parents=True, exist_ok=False)
    stage = report / "stage"
    stage.mkdir()
    if (target / ".tekton").exists():
        shutil.copytree(target / ".tekton", stage / ".tekton")
    git(stage, "init", "-q")
    if before:
        git(stage, "add", "-f", "--", ".tekton")
    policy_files = sorted(policy.glob("*"))
    if {p.name for p in policy_files} != {"config.yaml", "cleanup.yaml.j2"}:
        raise ValueError("unexpected cleanup policy files")
    digest = hashlib.sha256()
    for path in policy_files:
        if path.is_symlink() or not path.is_file():
            raise ValueError("unsafe cleanup policy")
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    result = {"release": release, "repository": repository, "inventory_revision": inventory_revision,
              "base": git(target, "rev-parse", "HEAD"),
              "policy_revision": git(policy, "rev-parse", "HEAD"),
              "tooling_revision": git(tooling, "rev-parse", "HEAD"),
              "policy_digest": digest.hexdigest(), "before": before, "deleted_files": expected}
    (report / "inventory.json").write_text(json.dumps(result, indent=2) + "\n")
    validate(stage, tooling, policy, release, report, "preview", ["--fix", "--dry-run"])
    if snapshot(stage)[0] != before:
        raise ValueError("validator dry run modified files")
    validate(stage, tooling, policy, release, report, "fix", ["--fix"])
    after, remaining = snapshot(stage)
    verify_deletions(before, after, expected)
    if remaining:
        raise ValueError("unremoved target PipelineRuns")
    validate(stage, tooling, policy, release, report, "validation", [])
    validate(stage, tooling, policy, release, report, "idempotence", ["--fix"])
    if snapshot(stage)[0] != after:
        raise ValueError("cleanup is not idempotent")
    if snapshot(target)[0] != before:
        raise ValueError("target changed during preparation")
    patch = command(["git", "-C", stage, "diff", "--binary", "--", ".tekton"]).stdout
    (report / "cleanup.patch").write_text(patch)
    result.update(status="ready" if expected else "no-changes",
                  patch_sha256=hashlib.sha256(patch.encode()).hexdigest(),
                  branch=f"automation/cleanup-odh-tenant-{release}-{digest.hexdigest()[:12]}")
    (report / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def existing_pr(repo, release, branch):
    """Do not overwrite an open cleanup or silently resurrect a closed bot PR."""
    prs = json.loads(command(["gh", "pr", "list", "--repo", repo, "--base", release,
                              "--state", "all", "--limit", "1000", "--json",
                              "url,state,headRefName,body"]).stdout)
    if len(prs) >= 1000:
        raise ValueError("PR inventory may be truncated; review before publication")
    for pr in prs:
        if pr["state"] == "OPEN" and (MARKER in pr["body"] or pr["headRefName"] == branch):
            return pr["url"]
    if any(pr["headRefName"] == branch for pr in prs):
        raise ValueError("cleanup branch has a prior PR; review its disposition explicitly")
    # Superseded manual PRs use different branches; never reopen them.
    return None


def publish(target, report):
    """Explicit remote mutation; recheck base, patch, and retained bytes before push."""
    target, report = target.resolve(), report.resolve()
    if report.is_relative_to(target) or target.is_relative_to(report):
        raise ValueError("report and target must be disjoint")
    data = json.loads((report / "result.json").read_text())
    check_selection(data["release"], data["repository"])
    if data["status"] != "ready":
        return {"status": "no-changes"}
    if (not re.fullmatch(r"[0-9a-f]{64}", data["policy_digest"])
            or data["branch"] != f"automation/cleanup-odh-tenant-{data['release']}-{data['policy_digest'][:12]}"):
        raise ValueError("invalid publication branch identity")
    repo = f"{ORG}/{data['repository']}"
    existing = existing_pr(repo, data["release"], data["branch"])
    if existing:
        return {"status": "existing-pr", "url": existing}
    if git(target, "status", "--porcelain", "--untracked-files=all") or git(target, "rev-parse", "HEAD") != data["base"]:
        raise ValueError("publication checkout differs from preview base")
    current = command(["gh", "api", f"repos/{repo}/git/ref/heads/{data['release']}", "--jq", ".object.sha"]).stdout.strip()
    if current != data["base"]:
        raise ValueError("remote release advanced; prepare again")
    before, candidates = snapshot(target)
    if before != data["before"] or candidates != data["deleted_files"]:
        raise ValueError("publication files or namespace inventory differ from preview")
    patch = (report / "cleanup.patch").read_text()
    if hashlib.sha256(patch.encode()).hexdigest() != data["patch_sha256"]:
        raise ValueError("preview patch changed")
    git(target, "switch", "--create", data["branch"])
    git(target, "apply", "--index", str((report / "cleanup.patch").resolve()))
    after, remaining = snapshot(target)
    verify_deletions(data["before"], after, data["deleted_files"])
    if remaining:
        raise ValueError("target pipelines remain after patch")
    changed = command(["git", "-C", target, "diff", "--cached", "--name-status", "-z"]).stdout
    expected = "".join("D\0" + name + "\0" for name in sorted(data["deleted_files"]))
    if changed != expected:
        raise ValueError("staged patch contains changes outside the deletion manifest")
    git(target, "diff", "--cached", "--check")
    git(target, "commit", "--signoff", "-m", f"chore: remove ODH tenant pipelines from {data['release']}")
    git(target, "push", f"https://github.com/{repo}.git", f"HEAD:refs/heads/{data['branch']}")
    body = (f"{MARKER}\nTracking: [RHAI-19583]({JIRA})\n\n"
            f"Remove only PipelineRuns explicitly targeting `{NAMESPACE}`.\n\n" +
            "\n".join(f"- `{name}`" for name in data["deleted_files"]) +
            f"\n\nRetained bytes/modes unchanged; fix, independent validation and idempotence passed."
            f"\n\nBase: `{data['base']}`\nPolicy: `{data['policy_revision']}`"
            f"\nPolicy digest: `{data['policy_digest']}`\nValidator: `{data['tooling_revision']}`\n")
    server, repository, run = (os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    if all((server, repository, run)):
        body += f"\n[Workflow run]({server}/{repository}/actions/runs/{run})\n"
    body_path = report / "pr.md"
    body_path.write_text(body)
    url = command(["gh", "pr", "create", "--repo", repo, "--base", data["release"],
                   "--head", data["branch"], "--title", f"chore: remove ODH tenant pipelines from {data['release']}",
                   "--body-file", body_path]).stdout.strip()
    return {"status": "published", "url": url}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    selection = commands.add_parser("matrix")
    selection.add_argument("--source", type=Path, required=True)
    for sub in (selection, commands.add_parser("prepare")):
        sub.add_argument("--release", choices=RELEASES, required=True)
        sub.add_argument("--repository", required=True)
    prep = commands.choices["prepare"]
    prep.add_argument("--inventory-revision")
    for name in ("target", "tooling", "policy", "report"):
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
        result = {"matrix": matrix, "prepare": prepare, "publish": publish}[mode](**args)
    except Exception as exc:
        if error_report is not None:
            error_report.mkdir(parents=True, exist_ok=True)
            diagnostic = {"error": str(exc), **{key: args[key] for key in
                          ("release", "repository", "inventory_revision") if key in args}}
            for key, argument in (("base", "target"), ("policy_revision", "policy"),
                                  ("tooling_revision", "tooling")):
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
