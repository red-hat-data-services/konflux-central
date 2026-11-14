"""PR conformance owns routing/platform filtering, never file deletion."""
from copy import deepcopy
import os
from pathlib import Path
import sys

import pytest

checkout = os.environ.get('LOCAL_SHARED_CANDIDATE') or os.environ.get('VALIDATOR_CHECKOUT')
if checkout:
    sys.path.insert(0, checkout)
from lib.tekton import render_and_merge
from lib.tekton_yaml import localized_yaml
import scan_resource_policy as policy
from conform_main_pr_children import conform

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def pr():
    return {'kind': 'PipelineRun', 'metadata': {'name': 'example-pull-request', 'annotations': {
        policy.CEL: 'event == "pull_request" && ("src/**".pathChanged() || skip_build)',
    }}, 'spec': {'pipelineSpec': {'tasks': [{'name': policy.SCAN}]}, 'params': [
        {'name': 'other', 'value': 'keep'},
    ]}}


def render(data):
    return render_and_merge(str(policy.POLICY_DIRECTORY), 'pull-request.yaml.j2', data,
                            'example-pull-request.yaml', branch='main', mode='fix')


@pytest.mark.parametrize('values,expected', [
    (['linux/amd64', 'linux/ppc64le', 'linux/arm64', 's390x'], ['linux/amd64', 'linux/arm64']),
    (['ppc64le', 'linux/s390x', 's390x', 'linux/ppc64le'], []),
    ([], []), (['linux/arm64', 'linux/amd64'], ['linux/arm64', 'linux/amd64']),
    (None, None),
])
def test_native_platform_filter_preserves_empty_missing_and_order(pr, values, expected):
    from ruamel.yaml import YAML
    if values is not None:
        pr['spec']['params'].append({'name': 'build-platforms', 'value': values})
    original = deepcopy(pr)
    proposed = render(pr)
    assert policy.check(pr, proposed, {'branch': 'main'})
    platforms = policy.unique(proposed['spec']['params'], 'name', 'build-platforms')
    assert platforms == ({} if expected is None else {'name': 'build-platforms', 'value': expected})
    assert proposed['spec']['params'][0] == {'name': 'other', 'value': 'keep'}
    assert render(proposed) == proposed
    from io import StringIO
    stream = StringIO()
    YAML().dump(pr, stream)
    text = localized_yaml(stream.getvalue(), proposed)
    assert YAML(typ='safe').load(text) == proposed
    if expected == []:
        assert platforms['value'] == []  # Native empty sequence, never a deleted parameter.
    assert pr == original


@pytest.mark.parametrize('value', ['linux/ppc64le', {'bad': 'array'}, 3, None])
def test_malformed_array_fails_closed(pr, value):
    pr['spec']['params'].append({'name': 'build-platforms', 'value': value})
    with pytest.raises((ValueError, TypeError)):
        candidate = render(pr)
        policy.check(pr, candidate, {'branch': 'main'})


@pytest.mark.parametrize('attack', ['supported', 'delete', 'unrelated', 'cluster', 'guard'])
def test_pr_guard_rejects_unowned_or_inexact_changes(pr, attack):
    pr['spec']['params'].append({'name': 'build-platforms', 'value': ['linux/amd64', 'ppc64le']})
    candidate = render(pr)
    if attack == 'supported':
        policy.unique(candidate['spec']['params'], 'name', 'build-platforms')['value'] = []
    elif attack == 'delete':
        candidate['spec']['params'] = candidate['spec']['params'][:1]
    elif attack == 'unrelated':
        candidate['spec']['params'][0]['value'] = 'changed'
    else:
        candidate['metadata']['annotations'][policy.CEL] = candidate['metadata']['annotations'][policy.CEL].replace(
            'stone-prod-p02' if attack == 'cluster' else 'skip_build', 'wrong')
    with pytest.raises(ValueError):
        policy.check(pr, candidate, {'branch': 'main'})


@pytest.mark.parametrize('notebook', [False, True])
@pytest.mark.parametrize('parent_cluster', [None, 'stone-prod-p01', 'stone-prod-p02'])
def test_generation_then_conformance_gates_entire_notebook_bypass(pr, notebook, parent_cluster):
    import celpy
    from ruamel.yaml import YAML
    parent = YAML(typ='safe').load((ROOT/'pipelineruns/notebooks/.tekton/odh-pipeline-runtime-pytorch-llmcompressor-cuda-py312-pull-request.yaml').read_text())
    params = 'manifests/rhoai/base/params-latest.env'
    parent['metadata']['annotations'][policy.CEL] = ('event == "push" && target_branch == "rhoai-3.7-ea.1"'
        + (f' && konflux_cluster == "{parent_cluster}"' if parent_cluster else '')
        + f' && !("{params}".pathChanged()) && "src/**".pathChanged()')
    directory = ROOT/'tekton-templates/main-pr'
    # Use the CEL field renderer so generation's unrelated metadata fields do not
    # require synthetic component labels. This is the actual trusted template.
    path = directory/('notebooks/pull-request.yaml.j2' if notebook else 'pull-request.yaml.j2')
    source = YAML(typ='safe').load(path.read_text())['metadata']['annotations'][policy.CEL]
    child = render_and_merge(str(directory), 'pull-request.yaml.j2', deepcopy(parent),
                             'pr.yaml', branch='main', parent_data=parent, mode='fix',
                             overlay_path=str(path) if notebook else None)
    generated = child['metadata']['annotations'][policy.CEL]
    assert 'stone-prod-p02' not in source
    pr['metadata']['annotations'][policy.CEL] = generated
    conformed = render(pr)
    assert policy.check(pr, conformed, {'branch': 'main'})
    expression = conformed['metadata']['annotations'][policy.CEL]
    assert expression.count('konflux_cluster') == 1
    assert render(conformed) == conformed
    env = celpy.Environment()
    for changed in [['src/**'], [params]]:
        program = env.program(env.compile(expression), functions={
            'pathChanged': lambda path: celpy.celtypes.BoolType(str(path) in changed),
        })
        for cluster in ['stone-prod-p01', 'stone-prod-p02', '', 'disabled']:
            actual = bool(program.evaluate(celpy.json_to_cel({
                'event': 'pull_request', 'source_branch': 'main', 'target_branch': 'stable',
                'konflux_cluster': cluster, 'files': {'all': changed},
            })))
            assert actual == (cluster == 'stone-prod-p02' and (changed == ['src/**'] or notebook))


def test_pr_callback_uses_compiled_policy_without_io(monkeypatch, pr):
    candidate = render(pr)
    def forbidden(*args, **kwargs):
        raise AssertionError('policy callback attempted filesystem IO')
    monkeypatch.setattr(Path, 'read_text', forbidden)
    assert policy.check(pr, candidate, {'branch': 'main'})


def test_duplicate_platform_params_fail_closed(pr):
    from lib.tekton_validation import selection
    pr['spec']['params'] += [{'name': 'build-platforms', 'value': []}] * 2
    with pytest.raises(ValueError, match='duplicate'):
        selection(policy.CONFIG, pr, 'pr.yaml', 'main', {'documents': {}})


def test_prepublication_driver_filters_only_prs_and_is_idempotent(tmp_path, pr):
    from ruamel.yaml import YAML
    directory = tmp_path/'pipelineruns/example/.tekton'
    directory.mkdir(parents=True)
    pr['spec']['params'].append({'name': 'build-platforms', 'value': ['s390x']})
    path = directory/'example-pull-request.yaml'
    with path.open('w') as stream:
        YAML().dump(pr, stream)
    push = directory/'push.yaml'
    push.write_text('kind: PipelineRun\nmetadata:\n  name: example-on-push\n')
    assert conform(tmp_path, Path(checkout)) == [path]
    before = path.read_bytes()
    assert YAML(typ='safe').load(before)['spec']['params'][-1]['value'] == []
    assert conform(tmp_path, Path(checkout), check_only=True) == []
    assert path.read_bytes() == before
    assert push.read_text() == 'kind: PipelineRun\nmetadata:\n  name: example-on-push\n'


def test_driver_plans_all_before_writing_and_rejects_symlinks(tmp_path, pr):
    from ruamel.yaml import YAML
    directory = tmp_path/'pipelineruns/example/.tekton'
    directory.mkdir(parents=True)
    first = directory/'a-pull-request.yaml'
    with first.open('w') as stream:
        YAML().dump(pr, stream)
    second = directory/'z-pull-request.yaml'
    invalid = deepcopy(pr)
    invalid['spec']['params'].append({'name': 'build-platforms', 'value': 42})
    with second.open('w') as stream:
        YAML().dump(invalid, stream)
    before = first.read_bytes()
    with pytest.raises((ValueError, TypeError)):
        conform(tmp_path, Path(checkout))
    assert first.read_bytes() == before
    second.unlink()
    second.symlink_to(first)
    with pytest.raises(ValueError, match='symlink'):
        conform(tmp_path, Path(checkout))
    assert first.read_bytes() == before
