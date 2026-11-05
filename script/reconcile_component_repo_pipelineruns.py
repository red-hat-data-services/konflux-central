#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["ruamel.yaml", "jinja2", "cel-python", "requests"]
# ///
"""Preview component repo PipelineRun reconciliation; publish only explicitly."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

ORG = "red-hat-data-services"
RELEASES = ("rhoai-2.25", "rhoai-3.4", "rhoai-3.5", "rhoai-3.6", "rhoai-3.7-ea.1")
# Keep legacy publication/report identities stable across terminology changes.
MARKER = "<!-- pipelinerun-template-enforcement -->"
PUBLISHER_REVISION = "4de549ff57e1e373424aee0ea21c4b997731e7f0"


def command(args, cwd=None, check=True, input=None):
    """Use argument arrays; never execute repository-provided shell code."""
    return subprocess.run([str(a) for a in args], cwd=cwd, text=True,
                          capture_output=True, check=check, input=input)


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


def snapshot(root, worker):
    """Classify strict YAML/resources over the shared worker's byte inventory."""
    files = {name: {"sha256": row["sha256"], "executable": row["mode"] == "100755"}
             for name, row in worker.snapshot(root, ".tekton").items()}
    runs, yaml = [], YAML(typ="safe")
    for name in files:
        path = root / name
        if path.suffix not in (".yaml", ".yml"):
            continue
        try:
            docs = list(yaml.load_all(path.read_text()))
        except (YAMLError, UnicodeError) as exc:
            raise ValueError(f"invalid YAML in {name}: {exc}") from exc
        if any(isinstance(doc, dict) and doc.get("kind") == "PipelineRun" for doc in docs):
            if len(docs) != 1:
                raise ValueError(f"bundled PipelineRun stream: {name}")
            runs.append(name)
    return files, runs


def validate(stage, tooling, policy, release, report, mode, flags, allow_drift=False, worker=None):
    """Retain validator reports, accepting pre-fix drift only when requested."""
    result = worker.run_validation([sys.executable, str(tooling / "scripts/validate-tekton"),
                      "--path", str(stage), "--config-dir", str(policy), "--branch", release,
                      "--no-checkout", "--json", *flags], stage, report, mode)
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
            tracking_issue=None, template_set=None, worker_root=None):
    """Apply component policy through the shared isolated reconciliation worker."""
    check_selection(release, repository)
    target, tooling, policy, report = (p.resolve() for p in (target, tooling, policy, report))
    if report.is_relative_to(target) or target.is_relative_to(report):
        raise ValueError("report and target must be disjoint")
    if worker_root is None:
        raise ValueError("worker-root is required")
    if (git(tooling, "rev-parse", "HEAD") != PUBLISHER_REVISION
            or git(tooling, "status", "--porcelain", "--untracked-files=all")):
        raise ValueError("validator must be the clean corrected shared revision")
    worker = load_shared(worker_root.resolve(), "reconciliation")
    template_set = template_set or policy.name
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", template_set):
        raise ValueError("invalid template set")
    if tracking_issue and not re.fullmatch(r"[A-Z][A-Z0-9]+-\d+", tracking_issue):
        raise ValueError("invalid tracking issue")
    if inventory_revision is not None and not re.fullmatch(r"[0-9a-f]{40,64}", inventory_revision):
        raise ValueError("invalid inventory revision")
    before, runs = snapshot(target, worker)
    digest = policy_digest(policy)
    result = {"release": release, "repository": repository, "inventory_revision": inventory_revision,
              "base": git(target, "rev-parse", "HEAD"), "template_set": template_set,
              "policy_revision": git(policy, "rev-parse", "HEAD"), "tracking_issue": tracking_issue,
              "tooling_revision": git(tooling, "rev-parse", "HEAD"), "policy_digest": digest,
              "worker_revision": PUBLISHER_REVISION, "publisher_revision": PUBLISHER_REVISION}
    selected, deletions = [], []
    def check(stage, phase):
        """Interpret validator outcomes and enforce PipelineRun kind preservation."""
        flags = {"initial": [], "preview": ["--fix", "--dry-run"], "fix": ["--fix"],
                 "validation": [], "idempotence": ["--fix"]}[phase]
        output = validate(stage, tooling, policy, release, report, phase, flags,
                          allow_drift=phase == "initial", worker=worker)
        after, remaining = snapshot(stage, worker)
        if any(name in after and before[name] != after[name] and name not in remaining for name in runs):
            raise ValueError("template changed a PipelineRun into another resource kind")
        return output
    def select(initial, inventory):
        """Derive exact matched and explicitly deleted PipelineRuns from policy."""
        for name, details in initial["files"].items():
            relative = ".tekton/" + name
            if relative not in runs or Path(name).name != name:
                raise ValueError("validator reported a non-PipelineRun or unsafe path")
            selected.append(relative)
            if details.get("delete"):
                deletions.append(relative)
        return selected, deletions
    manifest = worker.reconcile(target, scope=".tekton", report=report, provenance=result.copy(),
                                validate=check, select=select)
    after, _ = snapshot(report / "stage", worker)
    rows = manifest["changes"]
    patch = (report / "enforcement.patch").read_bytes()
    result.update(status="ready" if rows else "no-changes", before=before, after=after,
                  changes={row["path"]: row["status"] for row in rows}, planned_changes=rows,
                  provenance=manifest["provenance"], selected_files=selected,
                  deleted_files=sorted(deletions), updated_files=[r["path"] for r in rows if r["status"] == "M"],
                  patch_sha256=hashlib.sha256(patch).hexdigest(),
                  branch=f"automation/enforce-pipelineruns-{release}-{template_set}-{digest[:12]}")
    (report / "inventory.json").write_text(json.dumps({**manifest["provenance"], "before": before}, indent=2) + "\n")
    (report / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def load_shared(publisher_root, name):
    """Load only the workflow-selected standalone publisher, not lib.__init__."""
    import types
    if git(publisher_root, "rev-parse", "HEAD") != PUBLISHER_REVISION:
        raise ValueError("publisher checkout is not the independently trusted revision")
    if git(publisher_root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("publisher checkout must be clean")
    path = publisher_root / f"lib/{name}.py"
    if path.is_symlink() or not path.is_file():
        raise ValueError("shared publisher unavailable; shared-resources PR #53 must land")
    expected = subprocess.run(["git", "-C", str(publisher_root), "show",
                               f"{PUBLISHER_REVISION}:lib/{name}.py"],
                              capture_output=True, check=True).stdout
    if path.read_bytes() != expected:
        raise ValueError("publisher bytes differ from trusted Git object")
    module = types.ModuleType(f"component_{name}")
    module.__file__ = str(path)
    sys.modules[module.__name__] = module
    exec(compile(expected, str(path), "exec"), module.__dict__)
    return module


def publication_body(data):
    """Keep component policy wording outside generic publication mechanics."""
    body = f"{MARKER}\n<!-- template-set:{data['template_set']} -->\n\nApply template set `{data['template_set']}`.\n\n"
    if data.get("tracking_issue"):
        issue = data["tracking_issue"]
        body += f"Tracking: [{issue}](https://redhat.atlassian.net/browse/{issue})\n\n"
    body += "\n".join(f"- {status}: `{name}`" for name, status in data["changes"].items())
    body += (f"\n\nNon-fix validation and idempotence passed; unselected files unchanged."
             f"\n\nBase: `{data['base']}`\nPolicy: `{data['policy_revision']}`"
             f"\nInventory: `{data.get('inventory_revision') or 'not supplied'}`"
             f"\nPublisher: `{data['publisher_revision']}`"
             f"\nPolicy digest: `{data['policy_digest']}`\nValidator: `{data['tooling_revision']}`\n")
    server, repository, run = (os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    if all((server, repository, run)):
        body += f"\n[Workflow run]({server}/{repository}/actions/runs/{run})\n"
    return body


def publish(target, report, publisher_root):
    """Check component-specific receipts, then delegate publication to shared code."""
    target, report, publisher_root = target.resolve(), report.resolve(), publisher_root.resolve()
    if report.is_relative_to(target) or target.is_relative_to(report):
        raise ValueError("report and target must be disjoint")
    data = json.loads((report / "result.json").read_text())
    check_selection(data["release"], data["repository"])
    if data["status"] != "ready":
        return {"status": "no-changes"}
    if any(data.get(role) != PUBLISHER_REVISION for role in
           ("tooling_revision", "worker_revision", "publisher_revision")):
        raise ValueError("preview source revisions are stale; regenerate with corrected shared source")
    if (not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", data["template_set"])
            or not re.fullmatch(r"[0-9a-f]{64}", data["policy_digest"])
            or data["branch"] != f"automation/enforce-pipelineruns-{data['release']}-{data['template_set']}-{data['policy_digest'][:12]}"):
        raise ValueError("invalid publication branch identity")
    if data.get("tracking_issue") and not re.fullmatch(r"[A-Z][A-Z0-9]+-\d+", data["tracking_issue"]):
        raise ValueError("invalid tracking issue")
    publisher = load_shared(publisher_root, "validated_pr_publisher")
    worker = load_shared(publisher_root, "reconciliation")
    if snapshot(target, worker)[0] != data["before"]:
        raise ValueError("publication files differ from preview")
    if hashlib.sha256((report / "enforcement.patch").read_bytes()).hexdigest() != data["patch_sha256"]:
        raise ValueError("preview patch changed")
    expected = {row["path"]: row for row in data["planned_changes"]}
    if ({name: row["status"] for name, row in expected.items()} != data["changes"]
            or {name for name, row in expected.items() if row["status"] == "D"} != set(data["deleted_files"])
            or not expected.keys() <= set(data["selected_files"])):
        raise ValueError("trusted plan selection differs from receipt")
    manifest = json.loads((report / "bundle/manifest.json").read_text())
    if manifest["changes"] != data["planned_changes"]:
        raise ValueError("bundle differs from trusted same-job plan")
    def authorize(row, original, candidate):
        """Authorize exact replay of our trusted same-job plan, not hostile planners."""
        return row == expected.get(row["path"])
    manifest = worker.prepare_bundle(target, report / "bundle", scope=".tekton",
                    base_oid=data["base"], expected_provenance=data["provenance"],
                    authorize=authorize, allowed_statuses=("M", "D"))
    after, remaining = snapshot(target, worker)
    if (after != data["after"] or not
            {name for name, row in expected.items() if row["status"] == "M"} <= set(remaining)):
        raise ValueError("bundle differs from validated PipelineRun template output")
    body = publication_body(data)
    (report / "pr.md").write_text(body)
    request = {"version": 1, "repository": f"{ORG}/{data['repository']}",
               "base": data["release"], "base_oid": data["base"], "head": data["branch"],
               "lifecycle": "create-or-report",
               "marker": f"<!-- template-set:{data['template_set']} -->",
               "title": f"chore: reconcile component repo PipelineRuns on {data['release']}",
               "body": body, "provenance": {key: data.get(key) for key in
               ("policy_revision", "tooling_revision", "worker_revision", "publisher_revision", "inventory_revision", "policy_digest", "template_set")},
               "changes": manifest["changes"]}
    (report / "publication-request.json").write_text(json.dumps(request, indent=2) + "\n")
    def run(args, cwd, **kwargs):
        """Adapt the shared command boundary without importing validator packages."""
        return command(args, cwd=cwd, **kwargs).stdout.strip()
    return publisher.publish(target, request, run=run)


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
    for name in ("target", "tooling", "policy-root", "report", "worker-root"):
        prep.add_argument("--" + name, type=Path, required=True)
    pub = commands.add_parser("publish")
    for name in ("target", "report", "publisher-root"):
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
