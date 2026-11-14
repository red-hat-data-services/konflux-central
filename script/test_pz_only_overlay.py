"""Power/Z exceptions are explicit directory overlays, independent of default routing."""
import importlib.util
import os
from pathlib import Path
import shutil
import sys

import pytest
from ruamel.yaml import YAML

checkout = os.environ.get('LOCAL_SHARED_CANDIDATE') or os.environ.get('VALIDATOR_CHECKOUT')
if checkout:
    sys.path.insert(0, checkout)
from lib.tekton import render_and_merge, resolve_overlay
import scan_resource_policy as policy
from conform_main_pr_children import conform

ROOT = Path(__file__).resolve().parents[1]
VALUES = ['linux/ppc64le', 'linux/s390x', 'ppc64le', 's390x']


def document():
    return {'kind': 'PipelineRun', 'metadata': {'name': 'same-pull-request', 'annotations': {
        policy.CEL: 'event == "pull_request" && ("src/**".pathChanged() || skip_build)',
    }}, 'spec': {'pipelineSpec': {'tasks': [{'name': policy.SCAN}]},
                'params': [{'name': 'build-platforms', 'value': list(VALUES)}]}}


@pytest.mark.parametrize('destination', ['stone-prod-p01', 'stone-prod-p02'])
@pytest.mark.parametrize('component', ['vllm-cpu', 'vllm-cpu-other', 'other'])
def test_overlay_retains_platforms_and_p02_when_default_changes(tmp_path, destination, component):
    config = tmp_path/'tekton-templates/all-pipelineruns'
    shutil.copytree(policy.POLICY_DIRECTORY, config)
    base = config/'pull-request.yaml.j2'
    base.write_text(base.read_text().replace('stone-prod-p02', destination))
    module = tmp_path/'script/scan_resource_policy.py'
    module.parent.mkdir()
    shutil.copyfile(policy.__file__, module)
    spec = importlib.util.spec_from_file_location('pz_candidate', module)
    candidate_policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(candidate_policy)
    data = document()
    relative = f'pipelineruns/{component}/.tekton/same-pull-request.yaml'
    overlay = resolve_overlay(config, 'pull-request.yaml.j2', None, None,
                              pipeline_path=tmp_path/relative, repository_root=tmp_path,
                              mappings=candidate_policy.CONFIG.template_overlays)
    result = render_and_merge(str(config), 'pull-request.yaml.j2', data, 'same-pull-request.yaml',
                              branch='main', mode='fix', overlay_path=overlay)
    ctx = {'branch': 'main', 'pipeline_path': relative,
           'template_overlay': Path(overlay).relative_to(config).as_posix() if overlay else None}
    assert candidate_policy.check(data, result, ctx)
    exception = component == 'vllm-cpu'
    assert result['spec']['params'][0]['value'] == (VALUES if exception else [])
    cel = result['metadata']['annotations'][policy.CEL]
    assert f'konflux_cluster == "{"stone-prod-p02" if exception else destination}"' in cel
    assert cel.count('konflux_cluster') == 1
    assert render_and_merge(str(config), 'pull-request.yaml.j2', result, 'same-pull-request.yaml',
                            branch='main', mode='fix', overlay_path=overlay) == result
    assert policy.unique(result['spec']['workspaces'], 'name', policy.GIT_AUTH) == policy.GIT_AUTH_WORKSPACE
    assert policy.unique(result['spec']['taskRunSpecs'], 'pipelineTaskName', policy.SCAN)


@pytest.mark.parametrize('name', [None, '/pipelineruns/vllm-cpu/x.yaml',
    'pipelineruns/other/.tekton/same-pull-request.yaml', 'pipelineruns/vllm-cpu-other/x.yaml',
    'pipelineruns/vllm-cpu/../other/x.yaml', 'pipelineruns//vllm-cpu/x.yaml'])
def test_overlay_guard_requires_trusted_normalized_directory_path(name):
    data = document()
    result = render_and_merge(str(policy.POLICY_DIRECTORY), 'pull-request.yaml.j2', data, 'pr.yaml',
                              branch='main', mode='fix', overlay_path=str(policy.POLICY_DIRECTORY/'pz-only/pull-request.yaml.j2'))
    with pytest.raises(ValueError, match='trusted directory-mapped'):
        policy.check(data, result, {'branch': 'main', 'template_overlay': 'pz-only/pull-request.yaml.j2',
                                   'pipeline_path': name})


@pytest.mark.parametrize('attack', ['platforms', 'cluster', 'resources', 'unknown-overlay'])
def test_overlay_has_exact_owned_policy_not_guard_exemption(attack, monkeypatch):
    data = document()
    result = render_and_merge(str(policy.POLICY_DIRECTORY), 'pull-request.yaml.j2', data, 'pr.yaml',
                              branch='main', mode='fix', overlay_path=str(policy.POLICY_DIRECTORY/'pz-only/pull-request.yaml.j2'))
    ctx = {'branch': 'main', 'pipeline_path': 'pipelineruns/vllm-cpu/.tekton/pr.yaml',
           'template_overlay': 'pz-only/pull-request.yaml.j2'}
    if attack == 'platforms':
        result['spec']['params'][0]['value'] = []
    elif attack == 'cluster':
        result['metadata']['annotations'][policy.CEL] = result['metadata']['annotations'][policy.CEL].replace('stone-prod-p02', 'stone-prod-p01')
    elif attack == 'resources':
        result['spec']['taskRunSpecs'][0]['stepSpecs'][0]['computeResources']['limits']['cpu'] = '9'
    else:
        ctx['template_overlay'] = 'unknown/pull-request.yaml.j2'
    def forbidden(*args, **kwargs):
        raise AssertionError('callback performed filesystem IO')
    monkeypatch.setattr(Path, 'read_text', forbidden)
    with pytest.raises(ValueError):
        policy.check(data, result, ctx)


def test_missing_overlay_identity_cannot_silently_use_default_for_pz_path():
    data = document()
    data['metadata']['annotations']['template_overlay'] = 'pz-only/pull-request.yaml.j2'
    result = render_and_merge(str(policy.POLICY_DIRECTORY), 'pull-request.yaml.j2', data, 'pr.yaml',
                              branch='main', mode='fix')
    assert policy.check(data, result, {'branch': 'main', 'pipeline_path': 'pipelineruns/other/.tekton/pr.yaml',
                                      'template_overlay': None})
    with pytest.raises(ValueError, match='trusted directory-mapped'):
        policy.check(data, result, {'branch': 'main', 'pipeline_path': 'pipelineruns/vllm-cpu/.tekton/pr.yaml',
                                   'template_overlay': None})


@pytest.mark.parametrize('values', [None, [], ['linux/amd64', 'linux/ppc64le', 'linux/s390x']])
def test_overlay_preserves_missing_empty_and_mixed_arrays(values):
    data = document()
    data['spec']['params'] = [] if values is None else [{'name': 'build-platforms', 'value': values}]
    result = render_and_merge(str(policy.POLICY_DIRECTORY), 'pull-request.yaml.j2', data, 'pr.yaml',
                              branch='main', mode='fix', overlay_path=str(policy.POLICY_DIRECTORY/'pz-only/pull-request.yaml.j2'))
    assert result['spec']['params'] == data['spec']['params']
    assert policy.check(data, result, {'branch': 'main', 'pipeline_path': 'pipelineruns/vllm-cpu/.tekton/pr.yaml',
                                      'template_overlay': 'pz-only/pull-request.yaml.j2'})


def test_real_vllm_fixture_prepublication_driver_and_idempotence(tmp_path):
    source = ROOT/'pipelineruns/vllm-cpu/.tekton/vllm-cpu-pull-request.yaml'
    target = tmp_path/source.relative_to(ROOT)
    target.parent.mkdir(parents=True)
    shutil.copyfile(source, target)
    before = YAML(typ='safe').load(source.read_text())
    conform(tmp_path, Path(checkout))
    after = YAML(typ='safe').load(target.read_text())
    assert policy.unique(after['spec']['params'], 'name', 'build-platforms') == policy.unique(before['spec']['params'], 'name', 'build-platforms')
    assert 'konflux_cluster == "stone-prod-p02"' in after['metadata']['annotations'][policy.CEL]
    raw = target.read_bytes()
    assert conform(tmp_path, Path(checkout), check_only=True) == []
    assert target.read_bytes() == raw
