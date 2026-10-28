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


def run_monitor(tmp_path, scenario, component='operator', **overrides):
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
    state_path = tmp_path / 'state.json'
    state_path.write_text(json.dumps({'events': [], 'posts': 0, 'reads': 0}))
    script_path = tmp_path / 'monitor.sh'
    script_path.write_text(script)
    syntax = subprocess.run(['bash', '-n', str(script_path)], capture_output=True, text=True)
    assert syntax.returncode == 0, syntax.stderr
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'],
               MOCK_STATE=str(state_path), SCENARIO=scenario, CHECK_NAME=params['check-name'])
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
