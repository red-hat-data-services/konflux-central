"""Offline regression tests for combined PipelineRun validation comments."""

import itertools
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from assemble_validation_comment import MARKER, build_comment, summarize_template_reports


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

    def test_template_summary_groups_errors_and_counts_warnings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "validate-main-pr.json").write_text(json.dumps({"files": {
                "a.yaml": {"errors": ["metadata.name: expected a", "spec.params: missing x"], "warnings": ["warning"]},
                "b.yaml": {"errors": ["metadata.name: expected b"]},
            }}))
            (root / "validate-all-pipelineruns.json").write_text('{"files": {}}')
            summary = summarize_template_reports(root)
            self.assertIn("**3 errors** across **2 files**; **1 warnings**", summary)
            self.assertIn("<code>metadata.name</code>: **2 errors** in **2 files**", summary)
            self.assertIn("<code>a.yaml</code>", summary)
            self.assertIn("expected a", summary)
            self.assertIn("**0 errors**", summary)

    def test_template_summary_bounds_and_escapes_examples(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {f"<script>{i}</script>.yaml": {
                "errors": [f"field{j}: <script>" + "x" * 1000 for j in range(15)]
            } for i in range(5)}
            (root / "validate-all-pipelineruns.json").write_text(json.dumps({"files": files}))
            summary = summarize_template_reports(root, "rhoai-3.6")
            self.assertIn("5 additional error groups omitted", summary)
            self.assertIn("and 2 more", summary)
            self.assertIn("&lt;script&gt;", summary)
            self.assertNotIn("<script>", summary)
            self.assertNotIn("x" * 241, summary)
            self.assertNotIn("#### main-pr", summary)
            self.assertLess(len(summary), 15000)

    def test_invalid_or_missing_reports_do_not_break_comment(self):
        for content in (None, "not json", "[]", '{"files": []}',
                        '{"files": {"a": {"errors": "invalid"}}}'):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                if content is not None:
                    (root / "validate-main-pr.json").write_text(content)
                summary = summarize_template_reports(root)
                self.assertEqual(summary.count("Structured report unavailable"), 2)

    def test_template_details_are_included_without_changing_job_status(self):
        comment = build_comment("success", "failure", "", "https://example.com/run", "failure details\n")
        self.assertIn("failure details", comment)
        self.assertIn("Failed or Incomplete", comment)

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
