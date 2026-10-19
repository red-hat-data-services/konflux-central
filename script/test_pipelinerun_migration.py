"""Contract tests for the notebook component alias migration."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = (
    ROOT / "pipelineruns/notebooks/.tekton"
    / "odh-workbench-jupyter-pytorch-llmcompressor-cuda-py312-pull-request.yaml"
)


def test_llmcompressor_uses_release_component_alias():
    data = yaml.safe_load(PIPELINE.read_text())
    component = "odh-wb-jupyter-pytorch-llmcompressor-cuda-py312-v3-6"
    assert data["metadata"]["labels"]["appstudio.openshift.io/application"] == "rhoai-v3-6"
    assert data["metadata"]["labels"]["appstudio.openshift.io/component"] == component
    assert data["spec"]["taskRunTemplate"]["serviceAccountName"] == f"build-pipeline-{component}"
    params = {param["name"]: param["value"] for param in data["spec"]["params"]}
    assert params["output-image"] == (
        "quay.io/rhoai/odh-workbench-jupyter-pytorch-llmcompressor-cuda-py312-rhel9"
        ":on-pr-{{pull_request_number}}"
    )
    assert params["dockerfile"] == "jupyter/pytorch+llmcompressor/ubi9-python-3.12/Dockerfile.konflux.cuda"
    assert params["build-args-file"] == "jupyter/pytorch+llmcompressor/ubi9-python-3.12/build-args/konflux.cuda.conf"


def test_llmcompressor_keeps_source_filters_and_uses_pr_self_reference():
    data = yaml.safe_load(PIPELINE.read_text())
    annotations = data["metadata"]["annotations"]
    cel = annotations["pipelinesascode.tekton.dev/on-cel-expression"]
    assert 'event == "pull_request"' in cel
    assert 'event == "push"' not in cel
    assert f'".tekton/{PIPELINE.name}".pathChanged()' in cel
    assert "jupyter/pytorch+llmcompressor/ubi9-python-3.12/**" in cel
    assert "jupyter/utils/**" in cel
    assert '!("manifests/rhoai/base/params-latest.env".pathChanged())' in cel
    assert "target_branch" not in cel
    assert "pipelinesascode.tekton.dev/on-label" not in annotations
    assert "pipelinesascode.tekton.dev/on-event" not in annotations
