"""Chart/FBC policy tests exercise real selection, rendering and safety checks."""
from copy import deepcopy
import os
import sys

import pytest

checkout = os.environ.get('LOCAL_SHARED_CANDIDATE') or os.environ.get('VALIDATOR_CHECKOUT')
if checkout:
    sys.path.insert(0, checkout)
from lib.tekton import classify_pipeline, render_and_merge
from lib.tekton_validation import selection
import scan_resource_policy as policy


@pytest.fixture(params=[('charts', 'helm-chart-build'), ('fbc-fragment', 'fbc-fragment-build')])
def family(request):
    name, pipeline = request.param
    path = f'pipelines/{pipeline}.yaml'
    data = {'apiVersion': 'tekton.dev/v1', 'kind': 'PipelineRun', 'metadata': {
        'name': 'example-on-push', 'annotations': {
            policy.CEL: 'event == "push"\n&& target_branch == "rhoai-3.7-ea.1"\n&& ( "src/**".pathChanged()\n    || "Dockerfile".pathChanged() )\n',
            'keep': 'yes',
        }}, 'spec': {'pipelineRef': {'resolver': 'git', 'params': [
            {'name': 'url', 'value': 'https://github.com/red-hat-data-services/konflux-central.git'},
            {'name': 'revision', 'value': '{{ target_branch }}'},
            {'name': 'pathInRepo', 'value': path},
        ]}, 'taskRunSpecs': [{'pipelineTaskName': 'build', 'computeResources': {'requests': {'memory': '2Gi'}}}]}}
    ctx = {'branch': 'rhoai-3.7-ea.1', 'documents': {path: {
        'kind': 'Pipeline', 'spec': {'tasks': [{'name': 'build'}], 'workspaces': [{'name': 'git-auth'}]},
    }}}
    return name, path, data, ctx


def render(data, name, ctx):
    return render_and_merge(str(policy.POLICY_DIRECTORY), policy.CONFIG.get_template(name).file,
                            data, 'example.yaml', branch=ctx['branch'], mode='fix')


@pytest.mark.parametrize('scheduled', [False, True])
def test_family_routes_push_and_scheduled_without_scan_overrides(family, scheduled):
    name, path, data, ctx = family
    if scheduled:
        data['metadata']['name'] = 'example-on-schedule'
        data['metadata']['annotations']['pipelinesascode.tekton.dev/on-cron'] = '0 3 * * *'
    original = deepcopy(data)
    assert classify_pipeline('', data, policy.CONFIG) == name
    assert selection(policy.CONFIG, data, 'example.yaml', ctx['branch'], ctx)['status'] == 'applicable'
    proposed = render(data, name, ctx)
    assert policy.check(data, proposed, ctx)
    assert proposed['spec']['taskRunSpecs'] == original['spec']['taskRunSpecs']
    assert proposed['spec']['pipelineRef'] == original['spec']['pipelineRef']
    assert proposed['metadata']['annotations'][policy.CEL] == 'konflux_cluster == "stone-prod-p02"\n&& ' + original['metadata']['annotations'][policy.CEL]
    assert proposed['metadata']['annotations'].get('pipelinesascode.tekton.dev/on-cron') == original['metadata']['annotations'].get('pipelinesascode.tekton.dev/on-cron')
    assert render(proposed, name, ctx) == proposed
    assert data == original


@pytest.mark.parametrize('attack', ['scan', 'resources', 'schedule', 'branch', 'cluster'])
def test_family_guard_rejects_unowned_changes(family, attack):
    name, path, data, ctx = family
    proposed = render(data, name, ctx)
    if attack == 'scan':
        proposed['spec']['taskRunSpecs'].append({'pipelineTaskName': policy.SCAN})
    elif attack == 'resources':
        proposed['spec']['taskRunSpecs'][0]['computeResources']['requests']['memory'] = '8Gi'
    elif attack == 'schedule':
        proposed['metadata']['annotations']['pipelinesascode.tekton.dev/on-cron'] = 'changed'
    else:
        proposed['metadata']['annotations'][policy.CEL] = proposed['metadata']['annotations'][policy.CEL].replace('rhoai-3.7-ea.1' if attack == 'branch' else 'stone-prod-p02', 'wrong')
    with pytest.raises(ValueError):
        policy.check(data, proposed, ctx)


@pytest.mark.parametrize('revision', ['main', 'other', 'sha-unapproved'])
def test_family_revision_remains_branch_local(family, revision):
    name, path, data, ctx = family
    next(p for p in data['spec']['pipelineRef']['params'] if p['name'] == 'revision')['value'] = revision
    assert selection(policy.CONFIG, data, 'example.yaml', ctx['branch'], ctx)['status'] == 'unresolved'


def test_family_missing_context_and_unknown_reference_remain_unresolved(family):
    name, path, data, ctx = family
    assert selection(policy.CONFIG, data, 'example.yaml', ctx['branch'], {'documents': {}})['status'] == 'unresolved'
    next(p for p in data['spec']['pipelineRef']['params'] if p['name'] == 'pathInRepo')['value'] = 'pipelines/unknown.yaml'
    assert selection(policy.CONFIG, data, 'example.yaml', ctx['branch'], ctx)['status'] == 'unresolved'


@pytest.mark.parametrize('field,value', [('url', 'https://example.org/other.git'), ('resolver', 'bundles')])
def test_family_reference_trust_boundary(family, field, value):
    name, path, data, ctx = family
    ref = data['spec']['pipelineRef']
    if field == 'resolver':
        ref['resolver'] = value
    else:
        next(p for p in ref['params'] if p['name'] == field)['value'] = value
    assert selection(policy.CONFIG, data, 'example.yaml', ctx['branch'], ctx)['status'] == 'unresolved'


def test_family_pr_is_not_newly_eligible(family):
    name, path, data, ctx = family
    data['metadata']['name'] = 'example-pull-request'
    data['metadata']['annotations'][policy.CEL] = 'event == "pull_request"'
    assert selection(policy.CONFIG, data, 'example.yaml', ctx['branch'], ctx)['status'] == 'skipped'
