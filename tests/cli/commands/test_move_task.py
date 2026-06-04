"""Tests for `cartopian move-task` (SPEC-01-001, FR-004, FR-014)."""
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

_MINIMAL_TOML = (
    '[project]\n'
    'id = "test"\n'
    'name = "Test"\n'
    'protocol_version = "v0.3.0"\n'
)


def _run(*cli_args, home, cwd=None):
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
    }
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), "move-task", *cli_args],
        cwd=str(cwd) if cwd is not None else str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _seed_task(tmp: Path, status: str, name: str = "TASK-01-007-demo.md") -> Path:
    """Create a minimal Cartopian project with the task in <status>."""
    project = tmp / "project"
    tasks_dir = project / "tasks"
    for s in STATUSES:
        (tasks_dir / s).mkdir(parents=True, exist_ok=True)
    (project / "phases").mkdir(parents=True, exist_ok=True)
    (project / "prompts").mkdir(parents=True, exist_ok=True)
    (project / "reports").mkdir(parents=True, exist_ok=True)
    (project / "reviews").mkdir(parents=True, exist_ok=True)
    (project / "cartopian.toml").write_text(_MINIMAL_TOML, encoding="utf-8")
    task_path = tasks_dir / status / name
    task_path.write_text("# task\n", encoding="utf-8")
    return task_path


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _seed_coder_report(project: Path, nn_nnn: str, task_id: str) -> Path:
    p = project / "reports" / f"REPORT-{nn_nnn}.md"
    _write(p, (
        f"# REPORT-{nn_nnn}\n\n"
        f"Status: complete\n\n"
        f"## Identity\n\n"
        f"- Task ID: {task_id}\n"
        f"- Prompt path: {project}/prompts/PROMPT-{nn_nnn}.md\n"
        f"- Task path: placeholder\n"
        f"- Work root: n/a\n"
    ))
    return p


def _seed_review(project: Path, nn_nnn: str, verdict: str) -> Path:
    p = project / "reviews" / f"REVIEW-{nn_nnn}.md"
    _write(p, (
        f"# REVIEW-{nn_nnn}\n\n"
        f"Target: TASK-{nn_nnn}-demo\n"
        f"Verdict: {verdict}\n"
    ))
    return p


class TestMoveTaskHelp(unittest.TestCase):
    def test_help_lists_subcommand(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, str(ENTRYPOINT), "move-task", "--help"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                env={"HOME": tmp, "PATH": os.environ.get("PATH", "")},
            )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("task_path", proc.stdout)
        self.assertIn("open", proc.stdout)
        self.assertIn("in-progress", proc.stdout)
        self.assertIn("in-review", proc.stdout)
        self.assertIn("done", proc.stdout)


class TestMoveTaskHappyPath(unittest.TestCase):
    def _assert_success(self, proc, task_path, to_status):
        expected_after = task_path.parent.parent / to_status / task_path.name
        self.assertEqual(
            proc.returncode, 0,
            msg=f"stdout={proc.stdout!r} stderr={proc.stderr!r}",
        )
        self.assertEqual(proc.stderr, "")
        line = proc.stdout.strip()
        self.assertTrue(line, "expected one NDJSON line on stdout")
        self.assertEqual(line.count("\n"), 0, "must be exactly one NDJSON line")
        record = json.loads(line)
        self.assertEqual(record["action"], "move-task")
        details = record["details"]
        self.assertEqual(details["task_path_before"], str(task_path))
        self.assertEqual(details["task_path_after"], str(expected_after))
        self.assertEqual(details["to_status"], to_status)
        self.assertTrue(expected_after.is_file())
        self.assertFalse(task_path.exists())

    def test_open_to_in_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task_path = _seed_task(tmp_path, "open")
            proc = _run(str(task_path), "in-progress", home=tmp_path)
            self._assert_success(proc, task_path, "in-progress")

    def test_in_progress_to_in_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task_path = _seed_task(tmp_path, "in-progress")
            _seed_coder_report(tmp_path / "project", "01-007", "TASK-01-007")
            proc = _run(str(task_path), "in-review", home=tmp_path)
            self._assert_success(proc, task_path, "in-review")

    def test_in_review_to_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task_path = _seed_task(tmp_path, "in-review")
            _seed_review(tmp_path / "project", "01-007", "approve")
            proc = _run(str(task_path), "done", home=tmp_path)
            self._assert_success(proc, task_path, "done")

    def test_fast_forward_open_to_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task_path = _seed_task(tmp_path, "open")
            proc = _run(str(task_path), "done", home=tmp_path)
            self._assert_success(proc, task_path, "done")

    def test_fast_forward_open_to_in_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task_path = _seed_task(tmp_path, "open")
            proc = _run(str(task_path), "in-review", home=tmp_path)
            self._assert_success(proc, task_path, "in-review")

    def test_in_review_reject_to_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task_path = _seed_task(tmp_path, "in-review")
            _seed_review(tmp_path / "project", "01-007", "reject")
            proc = _run(str(task_path), "open", home=tmp_path)
            self._assert_success(proc, task_path, "open")

    def test_in_review_request_changes_to_in_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task_path = _seed_task(tmp_path, "in-review")
            _seed_review(tmp_path / "project", "01-007", "request-changes")
            proc = _run(str(task_path), "in-progress", home=tmp_path)
            self._assert_success(proc, task_path, "in-progress")

    def test_in_progress_fast_forward_to_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task_path = _seed_task(tmp_path, "in-progress")
            proc = _run(str(task_path), "done", home=tmp_path)
            self._assert_success(proc, task_path, "done")


class TestMoveTaskGuards(unittest.TestCase):
    """Tests for the pre-existing disallowed-transition guards."""

    def _expect_guard(self, from_status: str, to_status: str):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task_path = _seed_task(tmp_path, from_status)
            proc = _run(str(task_path), to_status, home=tmp_path)
            self.assertEqual(proc.returncode, 1, msg=proc.stderr)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(
                proc.stderr.startswith("[guard]"),
                msg=f"expected [guard] stderr, got: {proc.stderr!r}",
            )
            self.assertEqual(
                proc.stderr.count("\n"), 1,
                "expected exactly one stderr line",
            )
            self.assertIn(f"{from_status}", proc.stderr)
            self.assertIn(f"{to_status}", proc.stderr)
            self.assertTrue(task_path.is_file())

    def test_same_status_noop_open(self):
        self._expect_guard("open", "open")

    def test_same_status_noop_in_progress(self):
        self._expect_guard("in-progress", "in-progress")

    def test_terminal_done_to_in_progress(self):
        self._expect_guard("done", "in-progress")

    def test_terminal_done_to_open(self):
        self._expect_guard("done", "open")

    def test_terminal_done_to_in_review(self):
        self._expect_guard("done", "in-review")

    def test_terminal_done_to_done_noop(self):
        self._expect_guard("done", "done")

    def test_backward_in_progress_to_open(self):
        self._expect_guard("in-progress", "open")


class TestMoveTaskLifecycleGuards(unittest.TestCase):
    """Lifecycle artifact guards: missing artifacts block guarded transitions.

    `open -> in-progress` is deliberately unguarded — see the success cases
    below; prompt existence is enforced at the dispatch boundary instead.
    """

    def _expect_guard(self, proc):
        self.assertEqual(proc.returncode, 1, msg=f"stderr={proc.stderr!r}")
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)

    def test_open_to_in_progress_without_prompt_succeeds(self):
        """The move precedes prompt authoring; prompt existence is enforced
        fail-closed at dispatch time, not here."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task_path = _seed_task(tmp_path, "open")
            # no prompt seeded
            proc = _run(str(task_path), "in-progress", home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)

    def test_in_progress_to_in_review_missing_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task_path = _seed_task(tmp_path, "in-progress")
            proc = _run(str(task_path), "in-review", home=tmp_path)
            self._expect_guard(proc)
            self.assertIn("missing coder report", proc.stderr)
            self.assertTrue(task_path.is_file())

    def test_in_progress_to_in_review_report_wrong_task_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task_path = _seed_task(tmp_path, "in-progress")
            # Report references wrong task ID
            _seed_coder_report(tmp_path / "project", "01-007", "TASK-99-999")
            proc = _run(str(task_path), "in-review", home=tmp_path)
            self._expect_guard(proc)
            self.assertIn("does not reference", proc.stderr)
            self.assertTrue(task_path.is_file())

    def test_in_progress_to_in_review_report_not_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task_path = _seed_task(tmp_path, "in-progress")
            # Report has status=blocked
            p = tmp_path / "project" / "reports" / "REPORT-01-007.md"
            _write(p, (
                "# REPORT-01-007\n\nStatus: blocked\n\n"
                "## Identity\n\n- Task ID: TASK-01-007\n- Prompt path: x\n- Task path: x\n"
            ))
            proc = _run(str(task_path), "in-review", home=tmp_path)
            self._expect_guard(proc)
            self.assertIn("not 'complete'", proc.stderr)
            self.assertTrue(task_path.is_file())

    def test_in_review_to_done_missing_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task_path = _seed_task(tmp_path, "in-review")
            proc = _run(str(task_path), "done", home=tmp_path)
            self._expect_guard(proc)
            self.assertIn("missing review artifact", proc.stderr)
            self.assertTrue(task_path.is_file())

    def test_in_review_to_done_wrong_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task_path = _seed_task(tmp_path, "in-review")
            _seed_review(tmp_path / "project", "01-007", "request-changes")
            proc = _run(str(task_path), "done", home=tmp_path)
            self._expect_guard(proc)
            self.assertIn("expected 'approve'", proc.stderr)
            self.assertTrue(task_path.is_file())

    def test_in_review_to_in_progress_missing_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task_path = _seed_task(tmp_path, "in-review")
            proc = _run(str(task_path), "in-progress", home=tmp_path)
            self._expect_guard(proc)
            self.assertIn("missing review artifact", proc.stderr)
            self.assertTrue(task_path.is_file())

    def test_in_review_to_in_progress_wrong_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task_path = _seed_task(tmp_path, "in-review")
            _seed_review(tmp_path / "project", "01-007", "approve")
            proc = _run(str(task_path), "in-progress", home=tmp_path)
            self._expect_guard(proc)
            self.assertIn("expected 'request-changes'", proc.stderr)
            self.assertTrue(task_path.is_file())

    def test_in_review_to_open_missing_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task_path = _seed_task(tmp_path, "in-review")
            proc = _run(str(task_path), "open", home=tmp_path)
            self._expect_guard(proc)
            self.assertIn("missing review artifact", proc.stderr)
            self.assertTrue(task_path.is_file())

    def test_in_review_to_open_wrong_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task_path = _seed_task(tmp_path, "in-review")
            _seed_review(tmp_path / "project", "01-007", "approve")
            proc = _run(str(task_path), "open", home=tmp_path)
            self._expect_guard(proc)
            self.assertIn("expected 'reject'", proc.stderr)
            self.assertTrue(task_path.is_file())

    def test_no_project_root_blocks_guarded_transition(self):
        """A TASK-NN-NNN file outside any project root cannot advance."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Create task without cartopian.toml anywhere above it
            tasks_dir = tmp_path / "tasks"
            for s in STATUSES:
                (tasks_dir / s).mkdir(parents=True, exist_ok=True)
            task = tasks_dir / "in-progress" / "TASK-01-007-demo.md"
            task.write_text("# task\n", encoding="utf-8")
            proc = _run(str(task), "in-review", home=tmp_path)
            self.assertEqual(proc.returncode, 1)
            self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
            self.assertIn("project root not found", proc.stderr)
            self.assertTrue(task.is_file())

    def test_no_project_root_unguarded_transition_succeeds(self):
        """open -> in-progress is unguarded, so no project root is required."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tasks_dir = tmp_path / "tasks"
            for s in STATUSES:
                (tasks_dir / s).mkdir(parents=True, exist_ok=True)
            task = tasks_dir / "open" / "TASK-01-007-demo.md"
            task.write_text("# task\n", encoding="utf-8")
            proc = _run(str(task), "in-progress", home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertTrue((tasks_dir / "in-progress" / task.name).is_file())

    def test_non_canonical_name_no_guard(self):
        """Tasks with non-canonical names (no NN-NNN) skip lifecycle guards."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # No project root, non-canonical name — guard is skipped, rename happens
            tasks_dir = tmp_path / "tasks"
            for s in STATUSES:
                (tasks_dir / s).mkdir(parents=True, exist_ok=True)
            task = tasks_dir / "in-progress" / "TASK-admin-cleanup.md"
            task.write_text("# task\n", encoding="utf-8")
            proc = _run(str(task), "in-review", home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)


class TestMoveTaskUsage(unittest.TestCase):
    def test_relative_task_path_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proc = _run("tasks/open/TASK-01-007-demo.md", "in-progress", home=tmp_path)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)
            self.assertIn("absolute path", proc.stderr)

    def test_parent_not_canonical_status_dir_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bad = tmp_path / "project" / "tasks" / "archived"
            bad.mkdir(parents=True, exist_ok=True)
            task = bad / "TASK-01-007-demo.md"
            task.write_text("# task\n", encoding="utf-8")
            proc = _run(str(task), "in-progress", home=tmp_path)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)

    def test_invalid_to_status_choice_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task_path = _seed_task(tmp_path, "open")
            proc = _run(str(task_path), "archived", home=tmp_path)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stdout, "")
            self.assertEqual(
                proc.stderr,
                "[usage] invalid to_status: 'archived' (choose from 'open', "
                "'in-progress', 'in-review', 'done')\n",
            )


class TestMoveTaskMissingAndCollision(unittest.TestCase):
    def test_missing_source_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tasks_dir = tmp_path / "project" / "tasks"
            for s in STATUSES:
                (tasks_dir / s).mkdir(parents=True, exist_ok=True)
            missing = tasks_dir / "open" / "TASK-01-007-missing.md"
            proc = _run(str(missing), "in-progress", home=tmp_path)
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(proc.stdout, "")
            self.assertNotEqual(proc.stderr, "")

    def test_existing_target_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task_path = _seed_task(tmp_path, "open")
            dest = task_path.parent.parent / "in-progress" / task_path.name
            dest.write_text("preexisting\n", encoding="utf-8")
            original_dest_bytes = dest.read_bytes()
            proc = _run(str(task_path), "in-progress", home=tmp_path)
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
            self.assertEqual(dest.read_bytes(), original_dest_bytes)
            self.assertTrue(task_path.is_file())


class TestMoveTaskRoundTrip(unittest.TestCase):
    def test_round_trip_in_review_to_open_and_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task_path = _seed_task(tmp_path, "in-review")
            _seed_review(tmp_path / "project", "01-007", "reject")
            # in-review → open (reject)
            proc1 = _run(str(task_path), "open", home=tmp_path)
            self.assertEqual(proc1.returncode, 0, msg=proc1.stderr)
            rec1 = json.loads(proc1.stdout.strip())
            after1 = Path(rec1["details"]["task_path_after"])
            self.assertTrue(after1.is_file())
            # open → in-review is a fast-forward (no guard); restore prior state
            proc2 = _run(str(after1), "in-review", home=tmp_path)
            self.assertEqual(proc2.returncode, 0, msg=proc2.stderr)
            rec2 = json.loads(proc2.stdout.strip())
            self.assertEqual(rec2["details"]["task_path_after"], str(task_path))
            self.assertTrue(task_path.is_file())
            self.assertEqual(rec1["details"]["from_status"], "in-review")
            self.assertEqual(rec1["details"]["to_status"], "open")
            self.assertEqual(rec2["details"]["from_status"], "open")
            self.assertEqual(rec2["details"]["to_status"], "in-review")


if __name__ == "__main__":
    unittest.main()
