"""Exercise the shipped release resolver without network or cloning."""
import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def resolve(tmp_path, branches='', version='', override='', fail=False):
    task = yaml.safe_load((ROOT / 'early-gate/tasks/clone-build-config-repo.yaml').read_text())
    script = task['spec']['steps'][0]['script']
    script = script.replace('microdnf install -y git >/dev/null', ':')
    script = script.split('git clone --depth')[0] + 'printf "SELECTED=%s\\n" "$BRANCH"\n'
    git = tmp_path / 'git'
    git.write_text('#!/bin/bash\nif [ "$FAIL" = 1 ]; then exit 7; fi\nprintf "%s\\n" "$BRANCHES"\n')
    git.chmod(0o755)
    env = dict(os.environ, PATH=f'{tmp_path}:{os.environ["PATH"]}',
               BRANCHES='\n'.join(f'abc\trefs/heads/{b}' for b in branches.split()),
               FAIL=str(int(fail)), REPO_URL='unused', RHOAI_VERSION=version,
               BRANCH_OVERRIDE=override)
    return subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True)


@pytest.mark.parametrize('branches,expected', [
    ('rhoai-3.5 rhoai-3.6', '3.6'),
    ('rhoai-3.9 rhoai-3.10', '3.10'),
    ('rhoai-3.6 rhoai-4.0', '4.0'),
    ('rhoai-3.6 rhoai-4.0-ea.1 main', '3.6'),
])
def test_discovery(tmp_path, branches, expected):
    result = resolve(tmp_path, branches)
    assert result.returncode == 0, result.stderr
    assert f'SELECTED=rhoai-{expected}' in result.stdout


@pytest.mark.parametrize('version,override', [('3.5', ''), ('3.5.2', ''), ('rhoai-3.5', ''), ('', 'rhoai-3.5'), ('3.5', 'rhoai-3.5')])
def test_override(tmp_path, version, override):
    result = resolve(tmp_path, 'rhoai-4.0', version, override)
    assert result.returncode == 0, result.stderr
    assert 'SELECTED=rhoai-3.5' in result.stdout


@pytest.mark.parametrize('version,override,branches,fail', [
    ('3.5', 'rhoai-3.6', '', False), ('bad', '', '', False),
    ('', 'main', '', False), ('', '', 'rhoai-4.0-ea.1', False),
    ('', '', 'rhoai-3.6', True),
])
def test_invalid_or_unavailable_release_fails(tmp_path, version, override, branches, fail):
    assert resolve(tmp_path, branches, version, override, fail).returncode != 0


def test_pipeline_resolves_before_consumers():
    spec = yaml.safe_load((ROOT / 'early-gate/early-gate-component-pipeline.yaml').read_text())['spec']
    tasks = {t['name']: t for t in spec['tasks']}
    assert tasks['clone-build-config']['runAfter'] == ['init']
    for task in spec['tasks'] + spec['finally']:
        for param in task.get('params', []):
            if param['name'] == 'rhoai-version' and task['name'] != 'comment-on-leader-pr-failure':
                assert param['value'] == '$(tasks.clone-build-config.results.rhoai-version)'
    failure = next(t for t in spec['finally'] if t['name'] == 'comment-on-leader-pr-failure')
    assert 'results.' not in str(failure)


def test_bundle_branch_waits_for_operator_build():
    spec = yaml.safe_load((ROOT / 'early-gate/early-gate-component-pipeline.yaml').read_text())['spec']
    task = next(t for t in spec['tasks'] if t['name'] == 'fork-bundle-branch')
    assert 'monitor-operator-build' in task['runAfter']

def test_pipeline_has_no_dependency_cycle():
    import re
    spec = yaml.safe_load((ROOT / 'early-gate/early-gate-component-pipeline.yaml').read_text())['spec']
    tasks = {t['name']: t for t in spec['tasks']}
    done = set()
    def visit(name, active):
        assert name not in active, f'Dependency cycle at {name}'
        if name in done:
            return
        task = tasks[name]
        deps = set(task.get('runAfter', []))
        deps.update(re.findall(r'\$\(tasks\.([^.]+)\.results\.', str(task)))
        for dep in deps:
            visit(dep, active | {name})
        done.add(name)
    for name in tasks:
        visit(name, set())
