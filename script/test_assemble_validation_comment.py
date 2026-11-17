"""Offline regression tests for combined PipelineRun validation comments."""

import itertools
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from assemble_validation_comment import (
    MARKER, build_comment, summarize_template_reports, template_source_links,
)


class ValidationCommentTests(unittest.TestCase):
    def test_only_all_successful_jobs_report_overall_success(self):
        outcomes = ("success", "failure", "cancelled", "skipped")
        for pytest_result, all_result, main_result in itertools.product(outcomes, repeat=3):
            with self.subTest(pytest=pytest_result, all=all_result, main=main_result):
                comment = build_comment(pytest_result, all_result, main_result, "", "https://example.com/run")
                self.assertEqual(
                    "PipelineRun Validation Passed" in comment,
                    pytest_result == all_result == main_result == "success",
                )
                self.assertIn(f"| Pytest checks | {pytest_result} |", comment)
                self.assertIn(f"| all-pipelineruns | {all_result} |", comment)
                self.assertIn(f"| main-pr | {main_result} |", comment)

    def test_comment_describes_each_check(self):
        comment = build_comment("success", "success", "failure", "", "https://example.com/run")
        self.assertIn("| Validation | Result | Description |", comment)
        self.assertIn("| Pytest checks | success | General validation checks. |", comment)
        self.assertIn(
            "| all-pipelineruns | success | Enforce PipelineRuns against standard templates. |",
            comment,
        )
        self.assertIn(
            "| main-pr | failure | Ensure main-branch pull-request PipelineRuns are "
            "consistent with the latest release branch's push PipelineRuns. |",
            comment,
        )

    def test_release_allows_only_the_expected_main_pr_skip(self):
        for all_result in ("success", "failure", "cancelled", "skipped"):
            comment = build_comment("success", all_result, "skipped", "", "https://example.com/run", target_branch="rhoai-3.6")
            self.assertEqual("PipelineRun Validation Passed" in comment, all_result == "success")
            self.assertIn("| main-pr | skipped (not applicable to release branches) |", comment)

    def test_template_outcomes_remain_separate_in_both_directions(self):
        for all_result, main_result in (("success", "failure"), ("failure", "success")):
            comment = build_comment("success", all_result, main_result, "", "https://example.com/run")
            self.assertIn(f"| all-pipelineruns | {all_result} |", comment)
            self.assertIn(f"| main-pr | {main_result} |", comment)
            self.assertNotIn("PipelineRun Validation Passed", comment)

    def test_template_failure_does_not_reuse_pytest_success_heading(self):
        details = f"## :white_check_mark: PipelineRun Validation Passed\n\n42 passed\n{MARKER}\n"
        comment = build_comment("success", "success", "failure", details, "https://example.com/run")
        self.assertNotIn("PipelineRun Validation Passed", comment)
        self.assertIn("Pytest Checks Passed", comment)
        self.assertIn("42 passed", comment)
        self.assertEqual(comment.count(MARKER), 1)

    def test_pytest_failure_retains_details_with_explicit_scope(self):
        details = f"## :x: PipelineRun Validation Failed\n\nInvalid YAML\n{MARKER}\n"
        comment = build_comment("failure", "success", "success", details, "https://example.com/run")
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
        comment = build_comment("success", "success", "failure", "", "https://example.com/run", "failure details\n")
        self.assertIn("failure details", comment)
        self.assertIn("Failed or Incomplete", comment)

    def test_failure_links_pin_recorded_template_revision_and_include_overlays(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            revision = "a" * 40
            (root / "main-pr-template-source-sha.txt").write_text(revision + "\n")
            (root / "main-pr-template-files.txt").write_text(
                "tekton-templates/main-pr/config.yaml\n"
                "tekton-templates/main-pr/pull-request.yaml.j2\n"
                "tekton-templates/main-pr/notebooks/pull-request.yaml.j2\n"
                "tekton-templates/all-pipelineruns/default.yaml.j2\n"
            )
            (root / "validate-main-pr.json").write_text(json.dumps({"files": {
                "a.yaml": {"errors": ["metadata.name: wrong"]},
            }}))
            summary = summarize_template_reports(root, repository_url="https://github.com/org/repo")
            self.assertIn(
                f"[pull-request.yaml.j2](https://github.com/org/repo/blob/{revision}/tekton-templates/main-pr/pull-request.yaml.j2)",
                summary,
            )
            self.assertIn("[notebooks/pull-request.yaml.j2]", summary)
            self.assertIn("[config.yaml]", summary)
            self.assertNotIn("/blob/main/", summary)
            # Each job carries its own source revision and manifest.
            (root / "all-pipelineruns-template-source-sha.txt").write_text("b" * 40)
            (root / "all-pipelineruns-template-files.txt").write_text(
                "tekton-templates/all-pipelineruns/default.yaml.j2\n"
            )
            release = summarize_template_reports(root, "rhoai-3.6", "https://github.com/org/repo")
            self.assertIn(f"/blob/{'b' * 40}/tekton-templates/all-pipelineruns/default.yaml.j2", release)
            self.assertNotIn("tekton-templates/main-pr/", release)

    def test_source_links_ignore_invalid_metadata_and_out_of_scope_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(template_source_links(root, "main-pr", "https://github.com/org/repo"), "")
            (root / "main-pr-template-source-sha.txt").write_text("main")
            (root / "main-pr-template-files.txt").write_text("tekton-templates/main-pr/a.yaml.j2\n")
            self.assertEqual(template_source_links(root, "main-pr", "https://github.com/org/repo"), "")
            (root / "main-pr-template-source-sha.txt").write_text("b" * 40)
            (root / "main-pr-template-files.txt").write_text(
                "tekton-templates/main-pr/../other/template.j2\n"
                "tekton-templates/main-pr/ignored.txt\n"
                "tekton-templates/main-pr/a space.yaml.j2\n"
            )
            links = template_source_links(root, "main-pr", "https://github.com/org/repo")
            self.assertIn("a%20space.yaml.j2", links)
            self.assertNotIn("other/template", links)
            self.assertNotIn("ignored.txt", links)
            self.assertEqual(template_source_links(root, "main-pr", "javascript:alert(1)"), "")

    def test_source_links_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main-pr-template-source-sha.txt").write_text("c" * 40)
            (root / "main-pr-template-files.txt").write_text("\n".join(
                f"tekton-templates/main-pr/{i}.yaml.j2" for i in range(15)
            ))
            links = template_source_links(root, "main-pr", "https://github.com/org/repo")
            self.assertEqual(links.count("]("), 12)
            self.assertIn("3 more source files omitted", links)

    def test_cli_handles_missing_pytest_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "comment.md"
            subprocess.run([
                sys.executable, str(Path(__file__).with_name("assemble_validation_comment.py")),
                "--pytest-result", "failure", "--all-pipelineruns-result", "success",
                "--main-pr-result", "success",
                "--pytest-comment", str(root / "missing.md"),
                "--run-url", "https://example.com/run", "--output", str(output),
            ], check=True)
            comment = output.read_text()
            self.assertIn("Failed or Incomplete", comment)
            self.assertIn("Pytest details were not available", comment)
            self.assertIn("https://example.com/run", comment)


if __name__ == "__main__":
    unittest.main()
