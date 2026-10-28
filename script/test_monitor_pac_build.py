"""Execute the shipped Bash monitor with fake GitHub/registry responses and clock.

No network requests or real PaC comments are made by these tests.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "early-gate/tasks/monitor-pac-build.yaml"
PIPELINE = ROOT / "early-gate/early-gate-component-pipeline.yaml"

MOCK_CURL = r'''
import json, os, sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse
args = sys.argv[1:]
url = args[-1]
state_path = Path(os.environ["MOCK_STATE"])
s = json.loads(state_path.read_text())
t = int(os.environ.get("TEST_TIME", "0"))
mode = os.environ["SCENARIO"]
check = os.environ["CHECK_NAME"]
entry = {"time": t, "url": url}
def emit(value, code=0):
    s["events"].append(entry)
    state_path.write_text(json.dumps(s))
    print(value if isinstance(value, str) else json.dumps(value))
    sys.exit(code)
if url.endswith('/comments'):
    body = json.loads(args[args.index('--data') + 1])
    entry['body'] = body['body']
    s['posts'] += 1
    emit({'id': s['posts']}, 28 if mode == 'lost_response' else (22 if mode == 'post_failure' and s['posts'] == 1 else 0))
if '/git/ref/heads/' in url:
    if mode == 'head_error' and t == 120:
        emit({'message': 'Unavailable'}, 22)
    emit({'object': {'sha': 'different' if mode == 'moved' else 'abc1234'}})
if '/check-runs?' in url:
    s['reads'] += 1
    if mode == 'outage' or (mode == 'transient' and s['reads'] <= 2) or (mode == 'recheck_error' and s['reads'] == 6):
        emit({'message': 'Unavailable'}, 22)
    if mode == 'malformed':
        emit({'message': 'Bad response'})
    target = {'id': 5, 'name': 'Konflux Production Internal / ' + check,
              'status': 'completed', 'conclusion': 'success',
              'details_url': 'https://konflux.example/run', 'html_url': 'https://github.example/check'}
    if mode == 'paginated' and parse_qs(urlparse(url).query)['page'] == ['1']:
        emit({'check_runs': [{'id': i, 'name': 'unrelated'} for i in range(100)]})
    if mode in ('queued', 'in_progress', 'newest_queued'):
        target.update(status='queued' if mode == 'newest_queued' else mode,
                      conclusion=None, started_at=None)
        runs = [target]
        if mode == 'newest_queued':
            runs.insert(0, dict(target, id=4, status='completed', conclusion='success', started_at='2026-10-01'))
        emit({'check_runs': runs})
    if mode in ('failure', 'cancelled'):
        target['conclusion'] = mode
    missing = mode in ('never', 'moved', 'disabled')
    if mode in ('recover', 'lost_response', 'transient', 'head_error', 'recheck_error'):
        missing = s['posts'] < 1
    if mode in ('retry', 'post_failure'):
        missing = s['posts'] < 2
    if mode == 'race':
        missing = s['reads'] < 6
    emit({'check_runs': [] if missing else [target]})
if '/v2/auth?' in url:
    emit({'token': 'registry-token'})
if '/manifests/' in url:
    emit('Docker-Content-Digest: sha256:feedface\r\n')
raise RuntimeError(url)
'''


MOCK_SKOPEO = r'''
import json, os, sys
from pathlib import Path
state_path = Path(os.environ["MOCK_STATE"])
s = json.loads(state_path.read_text())
args = sys.argv[1:]
s["events"].append({"tool": "skopeo", "args": args, "time": int(os.environ.get("TEST_TIME", "0"))})
state_path.write_text(json.dumps(s))
if os.environ["SCENARIO"] == "registry_failure":
    sys.exit(1)
if "--authfile" in args:
    authfile = Path(args[args.index("--authfile") + 1])
    assert json.loads(authfile.read_text())["auths"]
assert args[args.index("--format") + 1] == "{{.Digest}}"
assert "--no-tags" in args
print("sha256:feedface")
'''


def run_monitor(tmp_path, scenario, component='operator', registry_auth='valid', **overrides):
    spec = yaml.safe_load(TASK.read_text())['spec']
    params = {p['name']: p.get('default', '') for p in spec['params']}
    names = {'operator': 'rhods-operator-eg-on-push',
             'bundle': 'rhods-operator-bundle-eg-on-push',
             'fbc': 'rhoai-fbc-fragment-eg-on-push'}
    params.update({'repo': 'org/repo', 'commit': 'abc1234', 'check-name': names[component],
                   'image-tag': 'eg-49', 'output-image': 'quay.io/org/image',
                   'branch-name': f'eg-{component}-49-uuid', 'timeout-minutes': '10'})
    params.update(overrides)
    credentials = tmp_path / 'credentials'
    credentials.mkdir()
    (credentials / 'token').write_text('fake-token')
    registry = tmp_path / 'registry'
    registry.mkdir()
    if registry_auth == 'valid':
        (registry / '.dockerconfigjson').write_text(json.dumps({'auths': {'quay.io': {'auth': 'test'}}}))
    script = spec['steps'][0]['script']
    script = re.sub(r'\$\(params\.([^)]+)\)', lambda m: params[m[1]], script)
    script = script.replace('$(workspaces.github-credentials.path)', str(credentials))
    script = re.sub(r'\$\(results\.([^.]+)\.path\)', lambda m: str(tmp_path / m[1]), script)
    # Replace Bash's clock in this test only, so real process time cannot add jitter.
    script = re.sub(r'\bSECONDS\b', 'TEST_TIME', script)
    script = script.replace('set -euo pipefail', '''set -euo pipefail
TEST_TIME=0
export TEST_TIME
microdnf() { :; }
sleep() { TEST_TIME=$(( TEST_TIME + $1 )); export TEST_TIME; }
''', 1)
    executable = tmp_path / 'curl'
    executable.write_text(f'#!{sys.executable}\n' + MOCK_CURL)
    executable.chmod(0o755)
    skopeo = tmp_path / 'skopeo'
    skopeo.write_text(f'#!{sys.executable}\n' + MOCK_SKOPEO)
    skopeo.chmod(0o755)
    state_path = tmp_path / 'state.json'
    state_path.write_text(json.dumps({'events': [], 'posts': 0, 'reads': 0}))
    script_path = tmp_path / 'monitor.sh'
    script_path.write_text(script)
    syntax = subprocess.run(['bash', '-n', str(script_path)], capture_output=True, text=True)
    assert syntax.returncode == 0, syntax.stderr
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'],
               MOCK_STATE=str(state_path), SCENARIO=scenario, CHECK_NAME=params['check-name'],
               REGISTRY_AUTH_BOUND='false' if registry_auth == 'unbound' else 'true',
               REGISTRY_AUTH_DIRECTORY=str(registry))
    result = subprocess.run(['bash', str(script_path)], env=env, capture_output=True, text=True, timeout=30)
    state = json.loads(state_path.read_text())
    status = (tmp_path / 'build-status').read_text()
    return result, state, status, params


@pytest.mark.parametrize('component', ['operator', 'bundle', 'fbc'])
def test_missing_push_recovers_on_correct_commit_and_branch(tmp_path, component):
    result, state, status, params = run_monitor(tmp_path, 'recover', component)
    assert result.returncode == 0, result.stderr
    assert status == 'success'
    posts = [e for e in state['events'] if 'body' in e]
    assert len(posts) == 1
    assert posts[0]['time'] == 120
    assert posts[0]['url'] == 'https://api.github.com/repos/org/repo/commits/abc1234/comments'
    assert posts[0]['body'] == f"/test {params['check-name']} branch:{params['branch-name']}"
    assert (tmp_path / 'image-ref-by-digest').read_text() == 'quay.io/org/image@sha256:feedface'
    # A fresh check read must immediately precede every POST.
    index = state['events'].index(posts[0])
    assert '/check-runs?' in state['events'][index - 1]['url']


@pytest.mark.parametrize('scenario,exit_code,status,posts', [
    ('immediate', 0, 'success', 0),
    ('retry', 0, 'success', 2),
    ('post_failure', 0, 'success', 2),
    ('head_error', 0, 'success', 1),
    ('recheck_error', 0, 'success', 1),
    ('lost_response', 0, 'success', 1),
    ('never', 1, 'failure', 3),
    ('queued', 1, 'timeout', 0),
    ('in_progress', 1, 'timeout', 0),
    ('newest_queued', 1, 'timeout', 0),
    ('failure', 1, 'failure', 0),
    ('cancelled', 1, 'failure', 0),
    ('outage', 1, 'timeout', 0),
    ('malformed', 1, 'timeout', 0),
    ('transient', 0, 'success', 1),
    ('paginated', 0, 'success', 0),
    ('race', 0, 'success', 0),
    ('moved', 1, 'failure', 0),
])
def test_monitor_scenarios(tmp_path, scenario, exit_code, status, posts):
    result, state, actual, _ = run_monitor(tmp_path, scenario)
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert actual == status
    assert state['posts'] == posts
    times = [e['time'] for e in state['events'] if 'body' in e]
    assert all(b - a >= 120 for a, b in zip(times, times[1:]))
    if scenario == 'never':
        assert times == [120, 240, 360]
        assert 'after 3 trigger attempts' in result.stderr


def test_short_timeout_does_not_post(tmp_path):
    result, state, status, _ = run_monitor(tmp_path, 'never', **{'timeout-minutes': '1'})
    assert result.returncode == 1
    assert status == 'timeout'
    assert state['posts'] == 0


@pytest.mark.parametrize('options', [{'branch-name': ''}, {'max-trigger-attempts': '0'}])
def test_recovery_can_be_disabled(tmp_path, options):
    result, state, status, _ = run_monitor(tmp_path, 'disabled', **options)
    assert result.returncode == 1
    assert status == 'timeout'
    assert state['posts'] == 0


def test_all_monitors_use_shared_task_and_their_own_branch():
    tasks = {t['name']: t for t in yaml.safe_load(PIPELINE.read_text())['spec']['tasks']}
    for component in ('operator', 'bundle', 'fbc'):
        task = tasks[f'monitor-{component}-build']
        ref = {p['name']: p['value'] for p in task['taskRef']['params']}
        assert ref['pathInRepo'] == 'early-gate/tasks/monitor-pac-build.yaml'
        assert ref['revision'] == '$(params.konflux-central-revision)'
        params = {p['name']: p['value'] for p in task['params']}
        assert params['branch-name'] == f'$(tasks.fork-{component}-branch.results.branch-name)'
        assert {'name': 'basic-auth', 'workspace': 'quay-credentials'} in task['workspaces']


@pytest.mark.parametrize('component', ['operator', 'bundle', 'fbc'])
def test_private_image_reads_use_mounted_authfile(tmp_path, component):
    result, state, status, _ = run_monitor(tmp_path, 'immediate', component)
    assert result.returncode == 0, result.stderr
    assert status == 'success'
    calls = [event for event in state['events'] if event.get('tool') == 'skopeo']
    assert len(calls) == 1
    args = calls[0]['args']
    assert args[args.index('--authfile') + 1] == str(tmp_path / 'registry/.dockerconfigjson')
    assert args[-1] == 'docker://quay.io/org/image:eg-49'
    assert '--creds' not in args


def test_public_image_reads_remain_supported(tmp_path):
    result, state, status, _ = run_monitor(tmp_path, 'immediate', registry_auth='unbound')
    assert result.returncode == 0, result.stderr
    assert status == 'success'
    calls = [event for event in state['events'] if event.get('tool') == 'skopeo']
    assert '--authfile' not in calls[0]['args']


@pytest.mark.parametrize('scenario,auth', [('immediate', 'missing'), ('registry_failure', 'valid')])
def test_missing_or_rejected_credentials_fail_required_digest(tmp_path, scenario, auth):
    result, _, status, _ = run_monitor(tmp_path, scenario, registry_auth=auth, **{'require-digest': 'true'})
    assert result.returncode == 1
    assert status == 'failure'
    assert (tmp_path / 'image-digest').read_text() == ''


@pytest.mark.parametrize('auth', ['valid', 'missing', 'unbound'])
def test_fbc_registry_setup_uses_same_pipeline_credential(tmp_path, auth):
    task = yaml.safe_load((ROOT / 'early-gate/tasks/fbc-processor.yaml').read_text())['spec']
    step = next(step for step in task['steps'] if step['name'] == 'process-fbc')
    script = step['script']
    start = script.index('AUTH_DIR=/var/workdir/.docker')
    end = script.index('# --- Declare paths', start)
    script = script[start:end].replace('/var/workdir/.docker', str(tmp_path / 'docker'))
    registry = tmp_path / 'registry'
    registry.mkdir()
    contents = json.dumps({'auths': {'quay.io': {'auth': 'test'}}})
    if auth == 'valid':
        (registry / '.dockerconfigjson').write_text(contents)
    env = dict(os.environ, REGISTRY_AUTH_BOUND='false' if auth == 'unbound' else 'true',
               REGISTRY_AUTH_DIRECTORY=str(registry), RHOAI_QUAY_RO_USERNAME='', RHOAI_QUAY_RO_TOKEN='')
    result = subprocess.run(['bash', '-e', '-c', script], env=env, capture_output=True, text=True)
    if auth == 'missing':
        assert result.returncode == 1
        assert '.dockerconfigjson is missing or empty' in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        config = tmp_path / 'docker/config.json'
        assert json.loads(config.read_text()) == (json.loads(contents) if auth == 'valid' else {'auths': {}})
        assert config.stat().st_mode & 0o777 == 0o600
    tasks = {t['name']:t for t in yaml.safe_load(PIPELINE.read_text())['spec']['tasks']}
    assert {'name':'basic-auth', 'workspace':'quay-credentials'} in tasks['fbc-processor']['workspaces']



def test_build_destinations_are_separate_from_intermediate_artifacts():
    spec = yaml.safe_load(PIPELINE.read_text())['spec']
    params = {param['name']: param for param in spec['params']}
    expected = {'operator-output-image': 'quay.io/rhoai/odh-rhel9-operator',
                'bundle-output-image': 'quay.io/rhoai/odh-operator-bundle',
                'fbc-output-image': 'quay.io/rhoai/rhoai-fbc-fragment'}
    for name, repository in expected.items():
        assert params[name]['default'] == repository
    assert params['trusted-artifacts-repository']['default'] == 'quay.io/rhoai/pull-request-pipelines'
    artifacts = [p['value'] for task in spec['tasks'] for p in task.get('params', []) if p['name'] == 'ociStorage']
    assert artifacts
    assert all(value.startswith('$(params.trusted-artifacts-repository):eg-') for value in artifacts)
    assert not next(w for w in spec['workspaces'] if w['name'] == 'quay-credentials').get('optional', False)
