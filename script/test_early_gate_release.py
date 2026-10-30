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
    ('rhoai-3.6 rhoai-4.0-ea.1 main', '4.0-ea.1'),
    ('rhoai-3.6-ea.2 rhoai-3.6-ea.10', '3.6-ea.10'),
    ('rhoai-3.6-ea.10 rhoai-3.6', '3.6'),
    ('rhoai-3.6-ea.2 rhoai-3.6-ea.2-dry-run-nightly-bundle', '3.6-ea.2'),
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
    ('', 'main', '', False), ('', '', 'rhoai-4.0-ea.1-extra', False),
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
                result_name = 'operator-version' if task['name'] == 'trigger-operator-processor-on-ephemeral-branch' else 'rhoai-version'
                assert param['value'] == f'$(tasks.clone-build-config.results.{result_name})'
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


@pytest.mark.parametrize('override,expected', [('3.5.2', '3.5.2'), ('rhoai-3.5.2', '3.5.2'), ('', '3.6.0'), ('3.5-ea.2', '3.5.0-ea.2'), ('3.5.2-ea.2', '3.5.2-ea.2'), ('', '3.6.0-ea.2')])
def test_final_operator_version_preserves_patch(tmp_path, override, expected):
    task = yaml.safe_load((ROOT / 'early-gate/tasks/clone-build-config-repo.yaml').read_text())
    script = task['spec']['steps'][0]['script'].replace('microdnf install -y git >/dev/null', ':')
    script = script.replace('/var/workdir/source', str(tmp_path / 'source'))
    for result in task['spec']['results']:
        script = script.replace('$(results.' + result['name'] + '.path)', str(tmp_path / result['name']))
    git = tmp_path / 'git'
    discovered = '3.6-ea.2' if not override and '-ea.' in expected else '3.6'
    git.write_text(f'#!/bin/bash\nif [ "$1" = ls-remote ]; then printf "abc\\trefs/heads/rhoai-{discovered}\\n"; elif [ "$1" = -C ]; then echo abc; fi\n')
    git.chmod(0o755)
    env = dict(os.environ, PATH=f'{tmp_path}:{os.environ["PATH"]}', REPO_URL='unused', RHOAI_VERSION=override, BRANCH_OVERRIDE='')
    subprocess.run(['bash', '-c', script], env=env, check=True, capture_output=True, text=True)
    minor = ('3.5-ea.2' if '-ea.' in override else '3.5') if override else discovered
    assert (tmp_path / 'release-branch').read_text() == f'rhoai-{minor}'
    assert (tmp_path / 'rhoai-version').read_text() == minor
    assert (tmp_path / 'component-suffix').read_text() == 'v' + minor.replace('.', '-')
    operator_version = (tmp_path / 'operator-version').read_text()
    pipeline = yaml.safe_load((ROOT / 'early-gate/early-gate-component-pipeline.yaml').read_text())
    trigger = next(t for t in pipeline['spec']['tasks'] if t['name'] == 'trigger-operator-processor-on-ephemeral-branch')
    assert next(p['value'] for p in trigger['params'] if p['name'] == 'rhoai-version') == '$(tasks.clone-build-config.results.operator-version)'
    pin, dispatch = trigger['taskSpec']['steps']
    assert next(e['value'] for e in pin['env'] if e['name'] == 'RHOAI_VERSION') == '$(params.rhoai-version)'
    assert 'RHOAI_VERSION="$(params.rhoai-version)"' in dispatch['script']
    code = pin['script'].split('<<\'PY\'\n', 1)[1].split('\nPY', 1)[0]
    target = tmp_path / 'pipeline.yaml'
    target.write_text(yaml.safe_dump({'spec': {'params': [], 'pipelineRef': {'resolver': 'git', 'params': [
        {'name': 'url', 'value': 'https://github.com/red-hat-data-services/konflux-central.git'},
        {'name': 'pathInRepo', 'value': 'pipelines/multi-arch-container-build.yaml'}]}}}))
    import sys
    subprocess.run([sys.executable, '-', str(target)], input=code, text=True, check=True, capture_output=True,
                   env=dict(os.environ, RHOAI_VERSION=operator_version, OUTPUT_IMAGE='quay.io/example/operator:eg-test'))
    pinned = yaml.safe_load(target.read_text())['spec']
    assert next(p['value'] for p in pinned['params'] if p['name'] == 'rhoai-version') == expected
    assert next(p['value'] for p in pinned['pipelineRef']['params'] if p['name'] == 'revision') == f'rhoai-{minor}'


@pytest.mark.parametrize('version,branch', [('3.6-ea.2', 'rhoai-3.6-ea.2'), ('3.6.0-ea.2', 'rhoai-3.6-ea.2'), ('rhoai-3.6-ea.2', 'rhoai-3.6-ea.2')])
def test_explicit_ea_override(tmp_path, version, branch):
    result = resolve(tmp_path, version=version)
    assert result.returncode == 0, result.stderr
    assert f'SELECTED={branch}' in result.stdout


@pytest.mark.parametrize('version,suffix,tag', [
    ('3.6', '-v3-6', 'rhoai-3.6'),
    ('3.6.2', '-v3-6', 'rhoai-3.6'),
    ('3.6-ea.2', '-v3-6-ea-2', 'rhoai-3.6-ea.2'),
    ('3.6.0-ea.2', '-v3-6-ea-2', 'rhoai-3.6-ea.2'),
])
def test_ea_component_suffix_and_snapshot_fallback(version, suffix, tag):
    resolver = yaml.safe_load((ROOT / 'early-gate/tasks/resolve-group-configuration.yaml').read_text())
    script = resolver['spec']['steps'][0]['script']
    script = script[script.index('VERSION_SUFFIX=""'):script.index('if [[ -n "${VERSION_SUFFIX}" ]]')]
    result = subprocess.run(['bash', '-euc', script + '\nprintf "%s" "$VERSION_SUFFIX"'],
                            env=dict(os.environ, RHOAI_VERSION=version), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == suffix
    snapshot = yaml.safe_load((ROOT / 'early-gate/tasks/generate-snapshot-for-group-testing.yaml').read_text())
    script = snapshot['spec']['steps'][0]['script']
    script = script[script.index('# --- Resolve the fallback tag'):script.index('# The basic-auth workspace')]
    result = subprocess.run(['bash', '-euc', script + '\nprintf "%s" "$FALLBACK_TAG"'],
                            env=dict(os.environ, RHOAI_VERSION=version, FALLBACK_TAG=''), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == tag


@pytest.mark.parametrize('component,version', [
    ('odh-foo-v3-6', '3.6'),
    ('odh-foo-v3-6-ea-2', '3.6-ea.2'),
    ('odh-foo-v3-6-ea-10', '3.6-ea.10'),
    ('odh-foo-v4-0-ea-1', '4.0-ea.1'),
    ('odh-foo', '3.6'),
])
def test_shared_pr_image_selected_before_release_fallback(tmp_path, component, version):
    import json
    import sys
    sha = 'a' * 40
    digest = 'sha256:' + 'b' * 64
    task = yaml.safe_load((ROOT / 'early-gate/tasks/generate-snapshot-for-group-testing.yaml').read_text())
    script = task['spec']['steps'][0]['script']
    # The task logs Linux distribution metadata; hosts may run macOS.
    script = script.replace('cat /etc/*release', ':')
    script = script.replace('/var/workdir/snapshot', str(tmp_path / 'snapshot'))
    script = script.replace('$(results.omitted-component-count.path)', str(tmp_path / 'omitted-count'))
    (tmp_path / 'group-components.json').write_text(json.dumps({
        component: {'quay_path': 'rhoai/odh-foo', 'pr': '123', 'sha': sha}}))
    shared_ref = f'docker://quay.io/rhoai/pull-request-pipelines:odh-foo-{sha}'
    skopeo = tmp_path / 'skopeo'
    skopeo.write_text(f'#!{sys.executable}\n' + '''import json, os, sys
with open(os.environ['PROBE_LOG'], 'a') as log:
    log.write(sys.argv[-1] + '\\n')
if sys.argv[-1] != os.environ['SHARED_REF']:
    print('image not found', file=sys.stderr)
    sys.exit(1)
print(json.dumps({'Digest': os.environ['DIGEST'], 'Labels': {
    'git.commit': os.environ['SHA'], 'git.url': 'https://github.com/example/odh-foo'}}))
''')
    skopeo.chmod(0o755)
    env = dict(os.environ, PATH=f'{tmp_path}:{os.environ["PATH"]}', GATE_DATA=str(tmp_path),
               RHOAI_VERSION=version, FALLBACK_TAG='', PR_PIPELINES_REPO='rhoai/pull-request-pipelines',
               WORKSPACE_BASIC_AUTH_DIRECTORY_BOUND='false', LEADER_PR_NUMBER='49', PR_NUMBER='',
               PROBE_LOG=str(tmp_path / 'probes'), SHARED_REF=shared_ref, SHA=sha, DIGEST=digest)
    result = subprocess.run(['bash', '-c', script], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'probes').read_text().splitlines() == [
        'docker://quay.io/rhoai/odh-foo:on-pr-123', shared_ref]
    snapshot = json.loads((tmp_path / 'snapshot/snapshot.json').read_text())
    assert snapshot[component]['image'] == f'quay.io/rhoai/pull-request-pipelines@{digest}'
    assert snapshot[component]['git.commit'] == sha
    assert snapshot[component]['image_tag'] == f'odh-foo-{sha}'
    assert '|ready|' in (tmp_path / 'component-table.tsv').read_text()
    assert (tmp_path / 'fallback-warning.md').read_text() == ''
    assert (tmp_path / 'omitted-count').read_text() == '0'
