"""Offline regression tests for combined PipelineRun validation comments."""

import itertools
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from assemble_validation_comment import MARKER, build_comment


class ValidationCommentTests(unittest.TestCase):
    def test_only_both_successful_jobs_report_overall_success(self):
        outcomes = ("success", "failure", "cancelled", "skipped")
        for pytest_result, template_result in itertools.product(outcomes, repeat=2):
            with self.subTest(pytest=pytest_result, templates=template_result):
                comment = build_comment(pytest_result, template_result, "", "https://example.com/run")
                self.assertEqual(
                    "PipelineRun Validation Passed" in comment,
                    pytest_result == template_result == "success",
                )
                self.assertIn(f"| Pytest checks | {pytest_result} |", comment)
                self.assertIn(f"| {template_result} |", comment)

    def test_template_failure_does_not_reuse_pytest_success_heading(self):
        details = f"## :white_check_mark: PipelineRun Validation Passed\n\n42 passed\n{MARKER}\n"
        comment = build_comment("success", "failure", details, "https://example.com/run")
        self.assertNotIn("PipelineRun Validation Passed", comment)
        self.assertIn("Pytest Checks Passed", comment)
        self.assertIn("42 passed", comment)
        self.assertEqual(comment.count(MARKER), 1)

    def test_pytest_failure_retains_details_with_explicit_scope(self):
        details = f"## :x: PipelineRun Validation Failed\n\nInvalid YAML\n{MARKER}\n"
        comment = build_comment("failure", "success", details, "https://example.com/run")
        self.assertIn("Pytest Checks Failed", comment)
        self.assertIn("Invalid YAML", comment)
        self.assertEqual(comment.count(MARKER), 1)

    def test_cli_handles_missing_pytest_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "comment.md"
            subprocess.run([
                sys.executable, str(Path(__file__).with_name("assemble_validation_comment.py")),
                "--pytest-result", "failure", "--template-result", "success",
                "--pytest-comment", str(root / "missing.md"),
                "--run-url", "https://example.com/run", "--output", str(output),
            ], check=True)
            comment = output.read_text()
            self.assertIn("Failed or Incomplete", comment)
            self.assertIn("Pytest details were not available", comment)
            self.assertIn("https://example.com/run", comment)


if __name__ == "__main__":
    unittest.main()
