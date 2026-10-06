"""Contract tests for the Garak Renovate policy and pipeline migration."""

import json
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "red-hat-data-services/llama-stack-provider-trustyai-garak"
POLICY = "renovate/garak-renovate.json"
PIPELINE = (
    ROOT
    / "pipelineruns/llama-stack-provider-trustyai-garak/.tekton"
    / "odh-trustyai-garak-lls-provider-dsp-pull-request.yaml"
)


class GarakConfigTests(unittest.TestCase):
    def test_garak_has_one_dedicated_sync_mapping(self):
        groups = yaml.safe_load((ROOT / "config.yaml").read_text())
        mappings = [
            (group["renovate-config"], repository)
            for group in groups
            for repository in group["sync-repositories"]
            if repository["name"] == REPOSITORY
        ]
        self.assertEqual(len(mappings), 1)
        self.assertEqual(mappings[0][0], POLICY)
        self.assertEqual(mappings[0][1]["targetFilePath"], ".github/renovate.json")

    def test_policy_waits_for_reported_checks(self):
        config = json.loads((ROOT / POLICY).read_text())
        self.assertTrue(config["automerge"])
        self.assertFalse(config["ignoreTests"])
        self.assertFalse(config["platformAutomerge"])
        self.assertEqual(config["automergeType"], "pr")
        self.assertEqual(config["automergeStrategy"], "squash")
        self.assertEqual(config["rebaseWhen"], "behind-base-branch")
        self.assertTrue(config["pinDigests"])

    def test_policy_preserves_mintmaker_defaults(self):
        config = json.loads((ROOT / POLICY).read_text())
        for key in [
            "extends",
            "enabledManagers",
            "branchPrefix",
            "baseBranches",
            "baseBranchPatterns",
        ]:
            self.assertNotIn(key, config)

    def test_pipeline_uses_the_dockerfile_default(self):
        pipeline = yaml.safe_load(PIPELINE.read_text())
        parameters = {
            parameter["name"]: parameter["value"]
            for parameter in pipeline["spec"]["params"]
        }
        self.assertEqual(parameters["dockerfile"], "Dockerfile.konflux")
        self.assertNotIn("build-args-file", parameters)
        self.assertNotIn("build-args", parameters)

    def test_pipeline_retains_all_architectures(self):
        pipeline = yaml.safe_load(PIPELINE.read_text())
        parameters = {
            parameter["name"]: parameter["value"]
            for parameter in pipeline["spec"]["params"]
        }
        self.assertEqual(
            set(parameters["build-platforms"]),
            {
                "linux/x86_64",
                "linux-d160-m2xlarge/arm64",
                "linux/ppc64le",
                "linux/s390x",
            },
        )

    def test_sync_menu_supports_garak_only(self):
        workflow = yaml.load(
            (ROOT / ".github/workflows/sync-renovate-configs.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        options = workflow["on"]["workflow_dispatch"]["inputs"]["renovate-config"][
            "options"
        ]
        self.assertIn("garak-renovate.json", options)
        self.assertIn("all", options)


if __name__ == "__main__":
    unittest.main()
