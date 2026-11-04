"""Publisher guardrails tested with fake Git/GitHub commands, never live writes."""

from copy import deepcopy
import hashlib
import json

import pytest

import publish_conformance_pr as publisher

FILE = "pipelineruns/example/.tekton/push.yaml"
BASE = "a" * 40
OLD = "b" * 40
TREE = "c" * 40
COMMIT = "d" * 40
REPO = "example/central"


@pytest.fixture
def reports(tmp_path):
    """Create matching applied and rechecked report fixtures for a local file."""
    path = tmp_path / FILE
    path.parent.mkdir(parents=True)
    path.write_text("kind: PipelineRun\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    plan = {"branch": "rhoai-3.5", "fixApplied": True, "validatorRevision": "1" * 40,
            "counts": {"planned": 1}, "files": [{"path": FILE, "status": "planned", "plannedSha256": digest}]}
    recheck = {"branch": "rhoai-3.5", "fixApplied": False, "validatorRevision": "1" * 40,
               "counts": {"conformant": 1}, "files": [{"path": FILE, "status": "conformant", "sha256": digest}]}
    return tmp_path, plan, recheck


class FakeCommands:
    """Model command outputs and record all proposed side effects."""

    def __init__(self, existing=False, pr=False, marked=True, same_tree=False):
        """Configure fake remote state and initialize command capture."""
        self.calls = []
        self.existing, self.pr, self.marked, self.same_tree = existing, pr, marked, same_tree

    def __call__(self, args, cwd, **kwargs):
        """Return modeled command results without executing Git or GitHub writes."""
        self.calls.append((args, kwargs))
        if args[0] == "gh":
            if args[1:3] == ["pr", "list"]:
                return json.dumps([{"number": 1, "url": "https://example/pr/1"}] if self.pr else [])
            return "https://example/pr/1"
        cmd = args[1:]
        if cmd == ["diff", "--cached", "--name-only"]:
            return ""
        if cmd == ["diff", "--name-only"]:
            return FILE
        if cmd[:2] in (["ls-files", "--others"], ["diff", "--name-only"]):
            return ""
        if cmd == ["remote", "get-url", "origin"]:
            return f"https://github.com/{REPO}.git"
        if cmd == ["rev-parse", "HEAD"]:
            return BASE
        if cmd[:2] == ["ls-remote", "--heads"]:
            if cmd[-1] == "refs/heads/rhoai-3.5":
                return f"{BASE}\t{cmd[-1]}"
            return f"{OLD}\t{cmd[-1]}" if self.existing else ""
        if cmd == ["rev-parse", "FETCH_HEAD"]:
            return OLD
        if cmd[:2] == ["show", "-s"]:
            return f"{publisher.MARKER}\nConformance-Base: {BASE}" if self.marked else "manual commit"
        if cmd == ["write-tree"]:
            return TREE
        if cmd == ["rev-parse", f"{OLD}^{{tree}}"]:
            return TREE if self.same_tree else "e" * 40
        if cmd[0] == "commit-tree":
            return COMMIT
        if cmd[0] in ("add", "fetch", "push") or cmd == ["diff", "--cached", "--check"]:
            return ""
        raise AssertionError(f"unexpected command: {args}")


@pytest.mark.parametrize("existing", [False, True])
def test_create_and_update_are_append_only(reports, existing):
    """Create or update a fix PR using normal pushes and retained parent history."""
    root, plan, recheck = reports
    fake = FakeCommands(existing=existing, pr=existing)
    assert publisher.publish(root, REPO, "rhoai-3.5", "source", plan, recheck, fake) == "https://example/pr/1"
    commit = next(args for args, _ in fake.calls if args[:2] == ["git", "commit-tree"])
    assert commit == ["git", "commit-tree", TREE, "-p", BASE] + (["-p", OLD] if existing else [])
    pushes = [args for args, _ in fake.calls if args[:2] == ["git", "push"]]
    assert pushes == [["git", "push", "origin", f"{COMMIT}:refs/heads/automation/pipelinerun-conformance/rhoai-3.5"]]
    assert any(args[:3] == ["gh", "pr", "edit" if existing else "create"] for args, _ in fake.calls)


def test_repeat_reuses_existing_branch_and_pr(reports):
    """Reuse an identical generated tree without publishing another commit."""
    root, plan, recheck = reports
    fake = FakeCommands(existing=True, pr=True, same_tree=True)
    publisher.publish(root, REPO, "rhoai-3.5", "source", plan, recheck, fake)
    assert not any(args[:2] in (["git", "push"], ["git", "commit-tree"]) for args, _ in fake.calls)
    assert any(args[:3] == ["gh", "pr", "edit"] for args, _ in fake.calls)


def test_non_generated_branch_is_not_updated(reports):
    """Refuse to publish over an automation branch lacking the generated marker."""
    root, plan, recheck = reports
    fake = FakeCommands(existing=True, marked=False)
    with pytest.raises(ValueError, match="not marked"):
        publisher.publish(root, REPO, "rhoai-3.5", "source", plan, recheck, fake)
    assert not any(args[:2] == ["git", "push"] for args, _ in fake.calls)


@pytest.mark.parametrize("change", ["wrong-base", "failed-plan", "pending-fixes", "validator-mismatch", "missing-validator", "unresolved-validator", "changed-file", "extra-file"])
def test_invalid_publication_is_rejected(reports, change):
    """Reject invalid reports and changed files before any publication."""
    root, plan, recheck = reports
    plan, recheck = deepcopy(plan), deepcopy(recheck)
    fake = FakeCommands()
    if change == "wrong-base":
        plan["branch"] = "rhoai-3.4"
    elif change == "failed-plan":
        plan["counts"]["error"] = 1
    elif change == "pending-fixes":
        recheck["counts"]["planned"] = 1
    elif change == "validator-mismatch":
        recheck["validatorRevision"] = "2" * 40
    elif change == "missing-validator":
        plan.pop("validatorRevision")
    elif change == "unresolved-validator":
        plan["validatorRevision"] = recheck["validatorRevision"] = "main"
    elif change == "changed-file":
        (root / FILE).write_text("modified after validation\n")
    else:
        plan["files"][0]["path"] = "README.md"
    with pytest.raises(ValueError):
        publisher.publish(root, REPO, "rhoai-3.5", "source", plan, recheck, fake)
    assert not any(args[:2] == ["git", "push"] for args, _ in fake.calls)


@pytest.mark.parametrize("condition", ["stale-base", "untracked", "unexpected-change", "staged", "deleted"])
def test_git_state_guards_prevent_publication(reports, condition):
    """Prevent publication from stale, dirty, staged, or structurally changed trees."""
    root, plan, recheck = reports
    fake = FakeCommands()

    def run(args, cwd, **kwargs):
        """Model a specific command outcome without performing external operations."""
        overrides = {
            "stale-base": (["git", "ls-remote", "--heads", "origin", "refs/heads/rhoai-3.5"], "f" * 40),
            "untracked": (["git", "ls-files", "--others", "--exclude-standard"], "unexpected.txt"),
            "unexpected-change": (["git", "diff", "--name-only"], FILE + "\nREADME.md"),
            "staged": (["git", "diff", "--cached", "--name-only"], FILE),
            "deleted": (["git", "diff", "--name-only", "--diff-filter=ADRTUXB"], FILE),
        }
        expected, output = overrides[condition]
        if args == expected:
            return output
        return fake(args, cwd, **kwargs)

    with pytest.raises(ValueError):
        publisher.publish(root, REPO, "rhoai-3.5", "source", plan, recheck, run)
    assert not any(args[:2] == ["git", "push"] for args, _ in fake.calls)


def test_no_changes_do_not_create_a_branch_or_pr(reports):
    """Treat an empty applied plan as a publication no-op."""
    root, plan, recheck = reports
    plan, recheck = deepcopy(plan), deepcopy(recheck)
    plan["files"] = []
    plan["counts"] = {}
    recheck["files"] = []
    recheck["counts"] = {}
    fake = FakeCommands()

    def run(args, cwd, **kwargs):
        """Model a specific command outcome without performing external operations."""
        return "" if args == ["git", "diff", "--name-only"] else fake(args, cwd, **kwargs)

    assert "No fixes needed" in publisher.publish(root, REPO, "rhoai-3.5", "source", plan, recheck, run)
    assert not any(args[0] == "gh" or args[:2] == ["git", "push"] for args, _ in fake.calls)


def test_push_failure_does_not_open_a_pr(reports):
    """Stop before GitHub mutations when publishing the branch fails."""
    import subprocess

    root, plan, recheck = reports
    fake = FakeCommands()

    def run(args, cwd, **kwargs):
        """Model a specific command outcome without performing external operations."""
        if args[:2] == ["git", "push"]:
            raise subprocess.CalledProcessError(1, args)
        return fake(args, cwd, **kwargs)

    with pytest.raises(subprocess.CalledProcessError):
        publisher.publish(root, REPO, "rhoai-3.5", "source", plan, recheck, run)
    assert not any(args[0] == "gh" for args, _ in fake.calls)


def test_main_is_never_a_publication_base(reports):
    """Restrict fix PR publication to release branches rather than main."""
    root, plan, recheck = reports
    with pytest.raises(ValueError, match="not main"):
        publisher.publish(root, REPO, "main", "source", plan, recheck, FakeCommands())


def test_pr_description_lists_unmodified_exceptions(reports):
    """Include deferred cases and runtime-validation requirements in fix PRs."""
    _, _, recheck = reports
    recheck["counts"].update(manual=1, unresolved=1)
    recheck["files"] += [{"path": "manual.yaml", "status": "manual", "reason": "task budget"},
                         {"path": "dynamic.yaml", "status": "unresolved", "reason": "dynamic ref"}]
    body = publisher.pr_body("rhoai-3.5", "source", recheck)
    assert "manual.yaml" in body and "dynamic.yaml" in body
    assert "Before merging" in body and "Do not auto-merge" in body
