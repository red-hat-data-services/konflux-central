"""Component receipt checks integrated with the pinned, real shared publisher."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from test_reconcile_component_repo_pipelineruns import enforcer, init_repo, resource, tooling, POLICY


@pytest.fixture
def publisher_root():
    """Use an explicitly supplied checkout of the independently pinned publisher."""
    value = os.environ.get('PUBLISHER_TEST_CHECKOUT')
    if not value:
        pytest.skip('set PUBLISHER_TEST_CHECKOUT for shared publication integration')
    return Path(value)


def publication_remote(monkeypatch, target, data, prs=None, advanced=False):
    """Execute real local adapter/core commands while mocking all remote effects."""
    enforcer.git(target, 'remote', 'add', 'origin', f"https://github.com/{enforcer.ORG}/{data['repository']}.git")
    enforcer.git(target, 'config', 'user.name', 'Publisher Test')
    enforcer.git(target, 'config', 'user.email', 'test@example.com')
    original = enforcer.command
    mutations = []
    def safe(args, **kwargs):
        """Never allow actual remote reads/writes or publication commits in tests."""
        args = [str(a) for a in args]
        if args[:2] == ['git', 'ls-remote']:
            value = ('0' * 40 if advanced else data['base']) if args[-1] == f"refs/heads/{data['release']}" else ''
            return subprocess.CompletedProcess(args, 0, value, '')
        if args[:3] == ['gh', 'pr', 'list']:
            return subprocess.CompletedProcess(args, 0, json.dumps(prs or []), '')
        if args[:2] in (['git', 'push'], ['git', 'commit-tree']) or args[:3] == ['gh', 'pr', 'create']:
            mutations.append(args)
            output = 'f' * 40 if args[:2] == ['git', 'commit-tree'] else 'https://example.com/new-pr'
            return subprocess.CompletedProcess(args, 0, output, '')
        assert args[0] != 'gh' and not any(word in args for word in ('push', 'fetch', 'commit'))
        return original(args, **kwargs)
    monkeypatch.setattr(enforcer, 'command', safe)
    return mutations


@pytest.mark.parametrize('failure', ['remote-advanced', 'patch-tampered', 'out-of-scope', 'namespace-changed'])
def test_publication_stops_before_commit_or_push(tmp_path, tooling, publisher_root, monkeypatch, failure):
    """Block stale/tampered/out-of-scope receipts before any shared publisher write."""
    target = tmp_path / 'target'
    resource(target, 'old')
    (target / 'app.txt').write_text('keep\n')
    init_repo(target)
    report = tmp_path / 'report'
    data = enforcer.prepare(target, tooling, POLICY, 'rhoai-3.6', 'ai-gateway-controller', report)
    patch = report / 'enforcement.patch'
    if failure == 'patch-tampered': patch.write_text(patch.read_text() + 'tampered')
    if failure == 'out-of-scope':
        patch.write_text(patch.read_text() + 'diff --git a/app.txt b/app.txt\n--- a/app.txt\n+++ b/app.txt\n@@ -1 +1 @@\n-keep\n+changed\n')
        data['patch_sha256'] = hashlib.sha256(patch.read_bytes()).hexdigest()
    if failure == 'namespace-changed': data['deleted_files'] = []
    (report / 'result.json').write_text(json.dumps(data))
    mutations = publication_remote(monkeypatch, target, data, advanced=failure == 'remote-advanced')
    with pytest.raises(ValueError): enforcer.publish(target, report, publisher_root)
    assert not mutations


def test_publication_happy_path_with_mocked_remote_mutations(tmp_path, tooling, publisher_root, monkeypatch):
    """Delegate a real deletion to pinned shared code, with remote mutations mocked."""
    target = tmp_path / 'target'
    resource(target, 'old')
    resource(target, 'retained', 'rhoai-tenant')
    init_repo(target)
    report = tmp_path / 'report'
    data = enforcer.prepare(target, tooling, POLICY, 'rhoai-3.6', 'ai-gateway-controller', report)
    mutations = publication_remote(monkeypatch, target, data)
    receipt = enforcer.publish(target, report, publisher_root)
    assert receipt['status'] == 'published' and receipt['url'] == 'https://example.com/new-pr'
    assert receipt['provenance']['publisher_revision'] == enforcer.PUBLISHER_REVISION
    assert receipt['provenance']['tooling_revision'] == data['tooling_revision']
    assert len(mutations) == 3 and mutations[0][:2] == ['git', 'commit-tree']
    assert '--force' not in str(mutations)
    assert enforcer.MARKER in (report / 'pr.md').read_text()
    assert enforcer.snapshot(target)[1] == ['.tekton/retained.yaml']


@pytest.mark.parametrize('same_policy', [True, False])
def test_existing_pr_identity_is_policy_scoped(tmp_path, tooling, publisher_root, monkeypatch, same_policy):
    """The shared create-or-report mode preserves same-policy PRs only."""
    target = tmp_path / 'target'
    resource(target, 'old')
    init_repo(target)
    report = tmp_path / 'report'
    data = enforcer.prepare(target, tooling, POLICY, 'rhoai-3.6', 'repo', report)
    name = POLICY.name if same_policy else 'another-policy'
    prs = [{'state': 'OPEN', 'headRefName': 'another-digest-branch', 'number': 7,
            'body': f'{enforcer.MARKER}\n<!-- template-set:{name} -->', 'url': 'https://example.com/existing'}]
    mutations = publication_remote(monkeypatch, target, data, prs=prs)
    result = enforcer.publish(target, report, publisher_root)
    assert result['status'] == ('existing-pr' if same_policy else 'published')
    assert bool(mutations) != same_policy


@pytest.mark.parametrize('prior', ['automation', 'manual', 'truncated'])
def test_prior_pr_disposition_uses_shared_guards(tmp_path, tooling, publisher_root, monkeypatch, prior):
    """Keep superseded manual pilots distinct from closed automation identities."""
    target = tmp_path / 'target'
    resource(target, 'old')
    init_repo(target)
    report = tmp_path / 'report'
    data = enforcer.prepare(target, tooling, POLICY, 'rhoai-3.6', 'repo', report)
    pr = {'state': 'CLOSED', 'headRefName': data['branch'] if prior == 'automation' else 'chore/manual-pilot',
          'body': '', 'number': 57, 'url': 'https://example.com/pr/57'}
    mutations = publication_remote(monkeypatch, target, data, prs=[pr] * (1000 if prior == 'truncated' else 1))
    if prior == 'manual':
        assert enforcer.publish(target, report, publisher_root)['status'] == 'published'
    else:
        with pytest.raises(ValueError):
            enforcer.publish(target, report, publisher_root)
        assert not mutations


def test_loader_executes_pinned_source_without_python_cache(publisher_root, monkeypatch):
    """Do not let ignored/stale pycache override the approved publisher source."""
    import importlib.util
    def forbidden(*args, **kwargs):
        """Fail if a cache-capable source loader is used."""
        raise AssertionError('must compile verified bytes instead')
    monkeypatch.setattr(importlib.util, 'spec_from_file_location', forbidden)
    module = enforcer.load_publisher(publisher_root)
    assert callable(module.publish) and callable(module.verify)
    assert not hasattr(module, '__cached__')


def test_wrong_publisher_revision_rejected_before_patch(tmp_path, tooling, monkeypatch):
    """A planner/validator revision cannot select credential-bearing publisher code."""
    target = tmp_path / 'target'
    resource(target, 'old')
    init_repo(target)
    report = tmp_path / 'report'
    enforcer.prepare(target, tooling, POLICY, 'rhoai-3.6', 'repo', report)
    before = enforcer.snapshot(target)
    with pytest.raises(ValueError, match='trusted revision'):
        enforcer.publish(target, report, tooling)
    assert enforcer.snapshot(target) == before
