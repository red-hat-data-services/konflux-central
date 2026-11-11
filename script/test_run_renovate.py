"""Runner smoke tests with no network, credentials, or container execution."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("run-renovate.sh")


class RunRenovateTests(unittest.TestCase):
    def run_runner(self, addendum=None, branches="[]", dry_run=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture = root / "args.json"
            # Both external entry points are replaced; no real token is used.
            curl = root / "curl"
            curl.write_text("#!/bin/sh\nprintf '{}\\n'\n")
            podman = root / "podman"
            podman.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "from pathlib import Path\n"
                "args = sys.argv[1:]\n"
                "mount = next(arg for arg in args if arg.endswith(':/tmp/mintmaker-self-hosted.json:ro'))\n"
                "config = json.loads(Path(mount.split(':', 1)[0]).read_text())\n"
                "Path(os.environ['CAPTURE']).write_text(json.dumps([args, config]))\n"
            )
            curl.chmod(0o755)
            podman.chmod(0o755)
            # Only carry process essentials, not real Renovate credentials/config.
            env = {
                "PATH": f"{root}:{Path(sys.executable).parent}:/usr/bin:/bin",
                "HOME": directory,
                "TMPDIR": directory,
                "RENOVATE_TOKEN": "dummy-test-token",
                "CAPTURE": str(capture),
            }
            if addendum is not None:
                env["PR_BODY_ADDENDUM"] = addendum
            command = [
                "bash", str(SCRIPT), "--repo", "example/repo",
                "--config-file", "unused.json", "--branches", branches,
                "--no-pull",
            ]
            if dry_run:
                command.append("--dry-run")
            subprocess.run(command, env=env, check=True, capture_output=True, text=True)
            args, config = json.loads(capture.read_text())
            force = next(arg for arg in args if arg.startswith("RENOVATE_FORCE="))
            force = json.loads(force.split("=", 1)[1])
            # Regression: body notes must use normal mergeable config, never force.
            self.assertNotIn("prBodyNotes", force)
            self.assertTrue(config["allowShellExecutorForPostUpgradeCommands"])
            return force, config, args

    def test_unset_or_empty_leaves_notes_unchanged(self):
        for addendum in (None, ""):
            with self.subTest(addendum=addendum):
                force, config, _ = self.run_runner(addendum)
                self.assertEqual(force, {})
                self.assertNotIn("prBodyNotes", config)

    def test_addendum_without_branch_override(self):
        force, config, _ = self.run_runner("**Please review carefully**")
        self.assertEqual(force, {})
        self.assertEqual(config["prBodyNotes"], ["**Please review carefully**"])

    def test_branch_override_without_addendum(self):
        force, config, _ = self.run_runner(branches='["main", "rhoai-3.6"]')
        self.assertEqual(force, {"baseBranchPatterns": ["main", "rhoai-3.6"]})
        self.assertNotIn("prBodyNotes", config)

    def test_multiline_markdown_with_branches_and_dry_run(self):
        notes = '# Context\n\nReview "quotes", \'apostrophes\', and café.\n- [Issue](https://example.com)\n'
        force, config, args = self.run_runner(notes, '["main"]', dry_run=True)
        self.assertEqual(force, {"baseBranchPatterns": ["main"]})
        self.assertEqual(config["prBodyNotes"], [notes])
        self.assertIn("RENOVATE_DRY_RUN=full", args)

    def test_shell_metacharacters_are_not_evaluated(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "must-not-exist"
            notes = f'$(touch "{marker}") `touch "{marker}"`; $HOME \\ ${{{{ inputs.repository }}}}'
            force, config, _ = self.run_runner(notes)
            self.assertEqual(force, {})
            self.assertEqual(config["prBodyNotes"], [notes])
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
