"""Tests for `cartopian report-action` (FR-004, DECISION-003)."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.scaffold import project_scaffold

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"

_PROJECT_TOML = (
    "[project]\n"
    'id = "demo"\n'
    'name = "Demo Project"\n'
    'protocol_version = "v0.3.0"\n'
    "\n"
    "[git]\n"
    "pm_owns_product_branches = true\n"
)


def _run(report_path: str, *, home: Path) -> subprocess.CompletedProcess[str]:
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
    }
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), "report-action", report_path],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _snapshot_tree(root: Path) -> dict[str, tuple[int, str, int]]:
    snapshot: dict[str, tuple[int, str, int]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes()
        stat = path.stat()
        snapshot[rel] = (
            stat.st_size,
            hashlib.sha256(data).hexdigest(),
            stat.st_mtime_ns,
        )
    return snapshot


class TestReportActionHappyPath(unittest.TestCase):
    def test_emits_required_fields_for_task_report(self) -> None:
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            task_path = scaffold.write(
                "tasks/in-progress/TASK-01-006-demo.md",
                (
                    "# TASK-01-006: demo\n\n"
                    "Work root: tool-repo\n"
                ),
            )
            report_path = scaffold.write(
                "reports/REPORT-01-006.md",
                (
                    "# REPORT-01-006\n\n"
                    "Status: complete\n\n"
                    "## Identity\n\n"
                    "- Task ID: TASK-01-006\n"
                    f"- Prompt path: {scaffold.prompts / 'PROMPT-01-006.md'}\n"
                    f"- Task path: {task_path}\n"
                    "- Work root: tool-repo\n\n"
                    "## Files changed\n\n"
                    "- cli/commands/report_action.py — added\n\n"
                    "## Test evidence\n\n"
                    "- Red test evidence: targeted red\n"
                    "- Green test evidence: targeted green\n\n"
                    "## Commit / PR\n\n"
                    "- Commit SHA: n/a\n"
                    "- PR URL: n/a\n\n"
                    "## Remaining risks\n\n"
                    "None.\n\n"
                    "## Ready for review\n\n"
                    "yes\n"
                ),
            )

            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])

        for field in (
            "verdict",
            "variant",
            "report_path",
            "status",
            "review_verdict",
            "target_task_status",
            "requires_pr_step",
            "prompt_to_overwrite",
            "review_path",
            "declared_report_task_path",
            "path_mismatch",
        ):
            self.assertIn(field, record, msg=f"missing field: {field}")

        self.assertEqual(record["verdict"], "accepted")
        self.assertEqual(record["variant"], "task")
        self.assertEqual(record["report_path"], str(report_path.resolve()))
        self.assertEqual(record["status"], "complete")
        self.assertIsNone(record["review_verdict"])
        self.assertEqual(record["target_task_status"], "in-review")
        self.assertTrue(record["requires_pr_step"])
        self.assertEqual(record["prompt_to_overwrite"], str((scaffold.prompts / "PROMPT-01-006.md").resolve()))
        self.assertEqual(record["review_path"], str((scaffold.reviews / "REVIEW-01-006.md").resolve()))
        self.assertEqual(record["declared_report_task_path"], str(task_path.resolve()))
        self.assertFalse(record["path_mismatch"])


class TestReportActionPathMismatch(unittest.TestCase):
    def test_surfaces_path_mismatch_as_data(self) -> None:
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            scaffold.write("tasks/in-progress/TASK-01-006-demo.md", "# TASK-01-006: demo\n")
            report_path = scaffold.write(
                "reports/REPORT-01-006.md",
                (
                    "# REPORT-01-006\n\n"
                    "Status: complete\n\n"
                    "## Identity\n\n"
                    "- Task ID: TASK-01-006\n"
                    f"- Prompt path: {scaffold.prompts / 'PROMPT-99-999.md'}\n"
                    f"- Task path: {scaffold.project_root / 'tasks' / 'done' / 'TASK-99-999-wrong.md'}\n"
                    "- Work root: n/a\n\n"
                    "## Files changed\n\n"
                    "- cli/commands/report_action.py — added\n\n"
                    "## Test evidence\n\n"
                    "- Red test evidence: targeted red\n"
                    "- Green test evidence: targeted green\n\n"
                    "## Commit / PR\n\n"
                    "- Commit SHA: n/a\n"
                    "- PR URL: n/a\n\n"
                    "## Remaining risks\n\n"
                    "None.\n\n"
                    "## Ready for review\n\n"
                    "yes\n"
                ),
            )

            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = json.loads(result.stdout.splitlines()[0])
        self.assertTrue(record["path_mismatch"])


class TestReportActionExitCodes(unittest.TestCase):
    def test_missing_project_config_exits_env(self) -> None:
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            scaffold.config.unlink()
            report_path = scaffold.write(
                "reports/REPORT-01-006.md",
                "# REPORT-01-006\n\nStatus: complete\n",
            )

            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, "")
        self.assertIn("[error] project config not found:", result.stderr)


class TestReportActionReadOnly(unittest.TestCase):
    def test_does_not_modify_project_tree(self) -> None:
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            task_path = scaffold.write(
                "tasks/in-progress/TASK-01-006-demo.md",
                (
                    "# TASK-01-006: demo\n\n"
                    "Work root: n/a\n"
                ),
            )
            report_path = scaffold.write(
                "reports/REPORT-01-006.md",
                (
                    "# REPORT-01-006\n\n"
                    "Status: complete\n\n"
                    "## Identity\n\n"
                    "- Task ID: TASK-01-006\n"
                    f"- Prompt path: {scaffold.prompts / 'PROMPT-01-006.md'}\n"
                    f"- Task path: {task_path}\n"
                    "- Work root: n/a\n\n"
                    "## Files changed\n\n"
                    "- none\n\n"
                    "## Test evidence\n\n"
                    "- Red test evidence: red\n"
                    "- Green test evidence: green\n\n"
                    "## Commit / PR\n\n"
                    "- Commit SHA: n/a\n"
                    "- PR URL: n/a\n\n"
                    "## Remaining risks\n\n"
                    "None.\n\n"
                    "## Ready for review\n\n"
                    "yes\n"
                ),
            )
            before = _snapshot_tree(scaffold.project_root)

            result = _run(str(report_path), home=home)
            after = _snapshot_tree(scaffold.project_root)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(before, after)
