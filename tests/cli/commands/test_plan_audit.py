"""Tests for `cartopian plan-audit` command."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"

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
        [sys.executable, str(ENTRYPOINT), "plan-audit", *cli_args],
        cwd=str(cwd) if cwd is not None else str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _make_project(tmp: Path) -> Path:
    project = tmp / "project"
    project.mkdir(parents=True, exist_ok=True)
    (project / "cartopian.toml").write_text(_MINIMAL_TOML, encoding="utf-8")
    for sub in ("tasks/open", "tasks/in-progress", "tasks/in-review", "tasks/done",
                "phases", "prompts", "reports", "reviews"):
        (project / sub).mkdir(parents=True, exist_ok=True)
    return project


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestPlanAuditHelp(unittest.TestCase):
    def test_help_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, str(ENTRYPOINT), "plan-audit", "--help"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                env={"HOME": tmp, "PATH": os.environ.get("PATH", "")},
            )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("project_path", proc.stdout)


class TestPlanAuditUsage(unittest.TestCase):
    def test_relative_path_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = _run("projects/my-project", home=Path(tmp))
            self.assertEqual(proc.returncode, 2)
            self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)

    def test_missing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = _run("/nonexistent/path/that/does/not/exist", home=Path(tmp))
            self.assertEqual(proc.returncode, 1)
            self.assertTrue(proc.stderr.startswith("[error]"), msg=proc.stderr)

    def test_directory_without_toml(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = _run(tmp, home=Path(tmp))
            self.assertEqual(proc.returncode, 1)
            self.assertTrue(proc.stderr.startswith("[error]"), msg=proc.stderr)
            self.assertIn("cartopian.toml", proc.stderr)


class TestPlanAuditClean(unittest.TestCase):
    def test_empty_project_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            record = json.loads(proc.stdout.strip())
            self.assertEqual(record["action"], "plan-audit")
            self.assertTrue(record["clean"])
            self.assertEqual(record["blockers"], [])
            self.assertEqual(record["warnings"], [])

    def test_in_progress_task_with_prompt_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            _write(project / "tasks" / "in-progress" / "TASK-01-003-do-thing.md", "# task\n")
            _write(project / "prompts" / "PROMPT-01-003.md", "# prompt\n")
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            record = json.loads(proc.stdout.strip())
            self.assertTrue(record["clean"])
            self.assertEqual(record["warnings"], [])

    def test_in_review_task_with_review_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            _write(project / "tasks" / "in-review" / "TASK-01-004-review-me.md", "# task\n")
            _write(project / "reviews" / "REVIEW-01-004.md",
                   "# REVIEW-01-004\n\nVerdict: approve\n")
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            record = json.loads(proc.stdout.strip())
            self.assertTrue(record["clean"])
            self.assertEqual(record["warnings"], [])

    def test_non_canonical_task_names_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            # Non-canonical name — no prompt required
            _write(project / "tasks" / "in-progress" / "TASK-admin-only.md", "# task\n")
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            record = json.loads(proc.stdout.strip())
            self.assertTrue(record["clean"])
            self.assertEqual(record["warnings"], [])


class TestPlanAuditArtifactChainBlockers(unittest.TestCase):
    def test_in_progress_missing_prompt_is_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            _write(project / "tasks" / "in-progress" / "TASK-01-003-do-thing.md", "# task\n")
            # no prompt
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 1)
            record = json.loads(proc.stdout.strip())
            self.assertFalse(record["clean"])
            self.assertEqual(len(record["blockers"]), 1)
            b = record["blockers"][0]
            self.assertEqual(b["kind"], "missing-prompt")
            self.assertEqual(b["task_id"], "TASK-01-003")
            self.assertIn("PROMPT-01-003.md", b["expected"])

    def test_in_review_missing_review_artifact_is_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            _write(project / "tasks" / "in-review" / "TASK-02-005-thing.md", "# task\n")
            # no review file
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 1)
            record = json.loads(proc.stdout.strip())
            self.assertFalse(record["clean"])
            b = record["blockers"][0]
            self.assertEqual(b["kind"], "missing-review-artifact")
            self.assertEqual(b["task_id"], "TASK-02-005")

    def test_in_review_review_missing_verdict_is_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            _write(project / "tasks" / "in-review" / "TASK-02-005-thing.md", "# task\n")
            # review file exists but has no Verdict: field
            _write(project / "reviews" / "REVIEW-02-005.md",
                   "# REVIEW-02-005\n\nNo verdict here.\n")
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 1)
            record = json.loads(proc.stdout.strip())
            b = record["blockers"][0]
            self.assertEqual(b["kind"], "review-missing-verdict")

    def test_multiple_blockers_all_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            _write(project / "tasks" / "in-progress" / "TASK-01-001-a.md", "# task\n")
            _write(project / "tasks" / "in-progress" / "TASK-01-002-b.md", "# task\n")
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 1)
            record = json.loads(proc.stdout.strip())
            self.assertEqual(len(record["blockers"]), 2)
            kinds = {b["kind"] for b in record["blockers"]}
            self.assertEqual(kinds, {"missing-prompt"})

    def test_done_tasks_not_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            # Done task with no artifacts — should not trigger blockers
            _write(project / "tasks" / "done" / "TASK-01-001-finished.md", "# task\n")
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            record = json.loads(proc.stdout.strip())
            self.assertTrue(record["clean"])

    def test_open_tasks_not_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            _write(project / "tasks" / "open" / "TASK-01-001-waiting.md", "# task\n")
            proc = _run(str(project), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            record = json.loads(proc.stdout.strip())
            self.assertTrue(record["clean"])


class TestPlanAuditOutput(unittest.TestCase):
    @unittest.skipUnless(shutil.which("git"), "git required")
    def test_dirty_work_root_is_warning_not_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            (project / "cartopian.toml").write_text(
                _MINIMAL_TOML + 'work_roots = ["tool-repo"]\n',
                encoding="utf-8",
            )
            work_root = tmp_path / "tool-repo"
            work_root.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "init"],
                cwd=str(work_root),
                capture_output=True,
                text=True,
                check=True,
            )
            (project / "cartopian.local.toml").write_text(
                f"[work_roots]\ntool-repo = \"{work_root}\"\n",
                encoding="utf-8",
            )
            _write(work_root / "scratch.txt", "local changes\n")

            proc = _run(str(project), home=tmp_path)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            record = json.loads(proc.stdout.strip())
            self.assertFalse(record["clean"])
            self.assertEqual(record["blockers"], [])
            self.assertEqual(len(record["warnings"]), 1)
            self.assertEqual(record["warnings"][0]["kind"], "unattributed-work-root-changes")
            self.assertIn("[warning]", proc.stderr)

    def test_output_is_single_ndjson_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            proc = _run(str(project), home=tmp_path)
            lines = [l for l in proc.stdout.splitlines() if l.strip()]
            self.assertEqual(len(lines), 1, msg=f"expected one NDJSON line, got: {proc.stdout!r}")
            record = json.loads(lines[0])
            self.assertEqual(record["action"], "plan-audit")
            self.assertIn("project_path", record)
            self.assertIn("clean", record)
            self.assertIn("blockers", record)
            self.assertIn("warnings", record)

    def test_blockers_emit_audit_stderr_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = _make_project(tmp_path)
            _write(project / "tasks" / "in-progress" / "TASK-01-001-a.md", "# task\n")
            proc = _run(str(project), home=tmp_path)
            self.assertIn("[audit]", proc.stderr)


if __name__ == "__main__":
    unittest.main()
