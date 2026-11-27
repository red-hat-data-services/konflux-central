"""Static checks for Tekton Pipeline syntax that admission validates remotely."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PIPELINES = sorted((ROOT / "early-gate").glob("*-pipeline.yaml"))


def test_all_early_gate_pipelines_are_discovered():
    assert PIPELINES


def test_finally_tasks_do_not_order_or_read_other_finally_tasks():
    for path in PIPELINES:
        spec = yaml.safe_load(path.read_text())["spec"]
        finally_tasks = {task["name"]: task for task in spec.get("finally", [])}
        for task in spec.get("finally", []):
            assert "runAfter" not in task, f"{path}: {task['name']} has runAfter in finally"
            serialized = str(task)
            for other in finally_tasks:
                if other == task["name"]:
                    continue
                pattern = rf"\$\(tasks\.{re.escape(other)}\.results\."
                assert not re.search(pattern, serialized), (
                    f"{path}: finally task {task['name']} reads result from finally task {other}"
                )


def test_normal_tasks_do_not_read_finally_results_and_dependencies_exist():
    result_reference = re.compile(r"\$\(tasks\.([^.]+)\.results\.")
    for path in PIPELINES:
        spec = yaml.safe_load(path.read_text())["spec"]
        normal = {task["name"]: task for task in spec["tasks"]}
        finally_names = {task["name"] for task in spec.get("finally", [])}
        for task in spec["tasks"]:
            serialized = str(task)
            for referenced in result_reference.findall(serialized):
                assert referenced in normal, (
                    f"{path}: task {task['name']} reads non-normal task result {referenced}"
                )
            for dependency in task.get("runAfter", []):
                assert dependency in normal, (
                    f"{path}: task {task['name']} has unknown runAfter dependency {dependency}"
                )
        assert not (set(normal) & finally_names), f"{path}: duplicate task names across sections"
