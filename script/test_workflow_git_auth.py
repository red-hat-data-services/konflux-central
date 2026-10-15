"""Static checkout-wiring checks, not a GitHub Actions runtime simulation.

Workflow syntax belongs to actionlint. App authorization, dry runs, and push
behavior must be validated with authorized workflow runs.
"""

from pathlib import Path

import pytest
import yaml


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"


def expression(value):
    """Ignore formatting in the simple credential references checked here."""
    return "".join(value.split())


@pytest.mark.parametrize("filename", [
    "apply-z-stream-changes.yml",
    "generate-arch-report.yml",
    "retrigger-builds.yml",
    "pipelinerun-replicator.yml",
])
def test_writable_checkout_uses_preceding_app_token(filename):
    workflow = yaml.load((WORKFLOWS / filename).read_text(), Loader=yaml.BaseLoader)
    checked = 0
    for job in workflow["jobs"].values():
        steps = job.get("steps", [])
        for index, checkout in enumerate(steps):
            if checkout.get("uses", "").partition("@")[0] != "actions/checkout":
                continue
            options = checkout.get("with", {})
            # These workflows write from the workspace root. Nested checkouts
            # only supply scripts; their names and paths are not part of this test.
            if options.get("path", ".") != ".":
                continue

            credential = expression(options.get("token", ""))
            producer = None
            for step in steps[:index]:
                if step.get("uses", "").partition("@")[0] != "actions/create-github-app-token":
                    continue
                if "id" not in step:
                    continue
                reference = f"steps.{step['id']}.outputs.token"
                # Accept an App token or App-first fallback for dry runs.
                # Deliberately do not interpret arbitrary Actions expressions.
                accepted = {"${{" + reference + "}}", "${{" + reference + "||github.token}}"}
                if credential in accepted:
                    producer = step
                    break

            assert producer is not None, f"{filename}: checkout must use an earlier App token"
            assert options.get("persist-credentials", "true") == "true"
            inputs = producer.get("with", {})
            assert expression(inputs.get("app-id", "")) == "${{secrets.APP_ID}}"
            assert expression(inputs.get("private-key", "")) == "${{secrets.PRIVATE_KEY}}"
            checked += 1

    assert checked, f"{filename}: no writable checkout was checked"
