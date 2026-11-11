import json
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "red-hat-data-services/NeMo-Guardrails"
POLICY = "renovate/nemo-guardrails-renovate.json5"
SHARED_PYTHON_POLICY = "renovate/uv-pip-compile-renovate.json5"


class NeMoGuardrailsRenovateConfigTests(unittest.TestCase):
    def test_nemo_has_one_dedicated_sync_mapping(self):
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

    def test_policy_preserves_existing_managers_and_adds_pip_compile(self):
        config = json.loads((ROOT / POLICY).read_text())

        self.assertEqual(
            config["enabledManagers"],
            [
                "dockerfile",
                "rpm",
                "rpm-lockfile",
                "custom.regex",
                "pip-compile",
            ],
        )

    def test_policy_updates_only_the_root_runtime_lockfile(self):
        config = json.loads((ROOT / POLICY).read_text())
        shared = json.loads((ROOT / SHARED_PYTHON_POLICY).read_text())
        manager = config["pip-compile"]

        self.assertEqual(manager["managerFilePatterns"], [r"/^requirements\.txt$/"])
        self.assertFalse(manager["lockFileMaintenance"]["enabled"])
        self.assertFalse(shared["pip_requirements"]["enabled"])
        self.assertFalse(shared["pip_setup"]["enabled"])

    def test_policy_reuses_the_shared_uv_pip_compile_preset(self):
        config = json.loads((ROOT / POLICY).read_text())

        self.assertIn(
            "github>red-hat-data-services/konflux-central//renovate/uv-pip-compile-renovate.json5",
            config["extends"],
        )

    def test_policy_gates_python_updates_to_rhoai_3_6(self):
        config = json.loads((ROOT / POLICY).read_text())
        rules = config["packageRules"]

        enabled_rule = rules[0]
        self.assertEqual(enabled_rule["matchManagers"], ["pip-compile"])
        self.assertEqual(enabled_rule["matchBaseBranches"], ["rhoai-3.6"])
        self.assertTrue(enabled_rule["enabled"])
        self.assertEqual(
            enabled_rule["postUpgradeTasks"],
            {
                "commands": ["python3 scripts/persist-security-constraints.py"],
                "fileFilters": ["dependencies/cve-constraints.txt", "requirements.txt"],
                "executionMode": "update",
            },
        )
        self.assertFalse(enabled_rule["automerge"])
        self.assertFalse(enabled_rule["platformAutomerge"])
        self.assertFalse(enabled_rule["ignoreTests"])

        indirect_rule = rules[1]
        self.assertEqual(indirect_rule["matchManagers"], ["pip-compile"])
        self.assertEqual(indirect_rule["matchDepTypes"], ["indirect"])
        self.assertEqual(indirect_rule["rangeStrategy"], "update-lockfile")

        disabled_rule = rules[2]
        self.assertEqual(disabled_rule["matchManagers"], ["pip-compile"])
        self.assertEqual(
            disabled_rule["matchJsonata"],
            ["baseBranch != 'rhoai-3.6'"],
        )
        self.assertFalse(disabled_rule["enabled"])

    def test_sync_menu_supports_nemo_only(self):
        workflow = yaml.load(
            (ROOT / ".github/workflows/sync-renovate-configs.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        options = workflow["on"]["workflow_dispatch"]["inputs"]["renovate-config"][
            "options"
        ]

        self.assertIn("nemo-guardrails-renovate.json5", options)
        self.assertIn("all", options)


if __name__ == "__main__":
    unittest.main()
