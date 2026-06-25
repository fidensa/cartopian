"""Tests for `cartopian list-tasks`."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"

STATUSES = ("open", "in-progress", "in-review", "done")


def _run(*cli_args, home):
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
    }
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), "list-tasks", *cli_args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _seed_project(root: Path) -> Path:
    project = root / "proj"
    tasks = project / "tasks"
    for s in STATUSES:
        (tasks / s).mkdir(parents=True, exist_ok=True)
    (project / "phases").mkdir(parents=True, exist_ok=True)
    (project / "phases" / "PHASE-01-build.md").write_text("# PHASE-01\n", encoding="utf-8")
    (project / "phases" / "PHASE-02-ship.md").write_text("# PHASE-02\n", encoding="utf-8")
    return project


def _seed_task(
    project: Path, status: str, task_id: str, phase: str, plan_ref: str, title: str
) -> Path:
    body = (
        f"# {task_id}: {title}\n"
        "\n"
        f"Phase: {phase}\n"
        f"Plan ref: {plan_ref}\n"
        "Work root: n/a\n"
        "Evidence gate: n/a\n"
    )
    task_path = project / "tasks" / status / f"{task_id}-demo.md"
    task_path.write_text(body, encoding="utf-8")
    return task_path


class TestListTasksHappyPath(unittest.TestCase):
    def test_orders_by_phase_then_status_then_task_id_with_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _seed_project(tmp_path)
            _seed_task(project, "in-review", "TASK-02-001", "PHASE-02-ship", "P02-1", "ship-1")
            _seed_task(project, "done", "TASK-01-002", "PHASE-01-build", "P01-2", "build-2")
            _seed_task(project, "open", "TASK-01-001", "PHASE-01-build", "P01-1", "build-1")
            _seed_task(project, "in-progress", "TASK-01-003", "PHASE-01-build", "P01-3", "build-3")
            _seed_task(project, "open", "TASK-02-002", "PHASE-02-ship", "P02-2", "ship-2")

            proc = _run("--project", str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(proc.stderr, "")
            lines = proc.stdout.splitlines()
            self.assertEqual(len(lines), 5)
            recs = [json.loads(line) for line in lines]
            # phase ASC, status order open→in-progress→in-review→done, task_id ASC
            self.assertEqual(
                [(r["phase"], r["status"], r["task_id"]) for r in recs],
                [
                    ("PHASE-01-build", "open", "TASK-01-001"),
                    ("PHASE-01-build", "in-progress", "TASK-01-003"),
                    ("PHASE-01-build", "done", "TASK-01-002"),
                    ("PHASE-02-ship", "open", "TASK-02-002"),
                    ("PHASE-02-ship", "in-review", "TASK-02-001"),
                ],
            )
            # exact field set per spec
            first = recs[0]
            self.assertEqual(
                sorted(first.keys()),
                ["phase", "plan_ref", "status", "task_id", "task_path", "title"],
            )
            self.assertEqual(first["plan_ref"], "P01-1")
            self.assertEqual(first["title"], "TASK-01-001: build-1")
            self.assertTrue(Path(first["task_path"]).is_file())

    def test_phase_and_status_filters_and_combine(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _seed_project(tmp_path)
            _seed_task(project, "open", "TASK-01-001", "PHASE-01-build", "P01-1", "t1")
            _seed_task(project, "open", "TASK-02-001", "PHASE-02-ship", "P02-1", "t2")
            _seed_task(project, "done", "TASK-01-002", "PHASE-01-build", "P01-2", "t3")

            proc = _run(
                "--project", str(project),
                "--phase", "PHASE-01-build",
                "--status", "open",
                home=tmp_path,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            lines = proc.stdout.splitlines()
            self.assertEqual(len(lines), 1)
            rec = json.loads(lines[0])
            self.assertEqual(rec["task_id"], "TASK-01-001")


class TestListTasksEmptyAndZeroRecords(unittest.TestCase):
    def test_empty_filter_result_empty_stdout_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _seed_project(tmp_path)
            _seed_task(project, "open", "TASK-01-001", "PHASE-01-build", "P01-1", "t1")
            proc = _run(
                "--project", str(project),
                "--status", "done",
                home=tmp_path,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(proc.stdout, "")
            self.assertEqual(proc.stderr, "")


class TestListTasksUsageGuards(unittest.TestCase):
    def test_missing_project_flag_exits_two_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proc = _run(home=tmp_path)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)

    def test_unknown_phase_id_exits_two_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _seed_project(tmp_path)
            proc = _run(
                "--project", str(project),
                "--phase", "PHASE-99-nope",
                home=tmp_path,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)
            self.assertIn("PHASE-99-nope", proc.stderr)

    def test_bad_status_value_exits_two_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _seed_project(tmp_path)
            proc = _run(
                "--project", str(project),
                "--status", "archived",
                home=tmp_path,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)

    def test_repeated_status_flag_exits_two_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _seed_project(tmp_path)
            proc = _run(
                "--project", str(project),
                "--status", "open",
                "--status", "done",
                home=tmp_path,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)

    def test_relative_project_path_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proc = _run("--project", "./relative/proj", home=tmp_path)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)

    def test_short_form_phase_two_digits_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _seed_project(tmp_path)
            proc = _run(
                "--project", str(project),
                "--phase", "01",
                home=tmp_path,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)


if __name__ == "__main__":
    unittest.main()
