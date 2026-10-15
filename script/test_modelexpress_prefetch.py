"""Check the dependency inputs for hermetic modelexpress release builds.

Run with:
    uv run --with pyyaml --with pytest pytest script/test_modelexpress_prefetch.py
"""

import json
from pathlib import Path

import pytest
import yaml


PIPELINERUN_DIR = (
    Path(__file__).resolve().parents[1] / "pipelineruns" / "modelexpress" / ".tekton"
)


@pytest.mark.parametrize(
    "image, component, dockerfile, output_image",
    [
        (
            "server",
            "odh-modelexpress-v3-6",
            "docker/Dockerfile.konflux",
            "quay.io/rhoai/odh-modelexpress-rhel9:{{target_branch}}",
        ),
        (
            "operator",
            "odh-modelexpress-operator-v3-6",
            "docker/Dockerfile.konflux.operator",
            "quay.io/rhoai/odh-modelexpress-operator-rhel9:{{target_branch}}",
        ),
    ],
    ids=["server", "operator"],
)
def test_modelexpress_hermetic_build_inputs(image, component, dockerfile, output_image):
    path = PIPELINERUN_DIR / f"{component}-push.yaml"
    with path.open() as stream:
        pipelinerun = yaml.safe_load(stream)

    metadata = pipelinerun["metadata"]
    spec = pipelinerun["spec"]
    params = {param["name"]: param["value"] for param in spec["params"]}

    assert metadata["labels"]["appstudio.openshift.io/component"] == component
    assert params["dockerfile"] == dockerfile
    assert params["output-image"] == output_image
    assert spec["taskRunTemplate"]["serviceAccountName"] == f"build-pipeline-{component}"
    assert params["hermetic"] is True
    assert "prefetch-input" in params, "Hermetic builds must prefetch their dependencies"
    assert json.loads(params["prefetch-input"]) == [
        {"type": "cargo", "path": "."},
        {"type": "rpm", "path": f"docker/konflux/{image}"},
        {
            "type": "generic",
            "path": f"docker/konflux/{image}",
            "lockfile": "generic-fetcher.yaml",
        },
    ]
