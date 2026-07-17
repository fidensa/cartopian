import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Dict

from tests.scaffold import project_scaffold

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"

_TOML_BASE = (
    "[project]\n"
    'id = "test-proj"\n'
    'name = "Test Project"\n'
    'protocol_version = "v0.5.0"\n'
    'work_roots = ["tool-repo"]\n'
)


def _run(task_path: str, home: Path):
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
    }
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), "task-bundle", task_path],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _snapshot_tree(root: Path) -> Dict[str, str]:
    """Capture a hash of every file plus a marker for every directory under ``root``.

    Used by the read-only invariant test to detect any filesystem mutation
    caused by ``task-bundle`` invocation (NFR-001).
    """
    snap: Dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            snap[rel] = f"<symlink:{os.readlink(path)}>"
        elif path.is_file():
            snap[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif path.is_dir():
            snap[rel + "/"] = "<dir>"
    return snap


def _parse_single_record(result):
    lines = result.stdout.splitlines()
    if len(lines) != 1:
        raise AssertionError(
            f"expected 1 stdout line, got {len(lines)}: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    return json.loads(lines[0])


class TestTaskBundleHappyPath(unittest.TestCase):
    def test_emits_expected_fields_for_valid_task(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            work_root = scaffold.root / "tool-repo"
            work_root.mkdir()
            scaffold.write(
                "cartopian.local.toml",
                f"[work_roots]\ntool-repo = \"{work_root}\"\n",
            )
            scaffold.write("IMPLEMENTATION_PLAN.md", "P01-BUILD-002\n")
            scaffold.write("phases/PHASE-01-foundation.md", "# Phase 01\n")
            scaffold.write("specs/SPEC-01-001-demo.md", "# Demo Spec\n")
            scaffold.write(
                "tasks/done/TASK-01-001-prereq.md",
                "# TASK-01-001: Prereq\n",
            )
            task_path = scaffold.write(
                "tasks/open/TASK-01-002-example.md",
                (
                    "# TASK-01-002: Example\n\n"
                    "Phase: PHASE-01-foundation\n"
                    "Plan ref: P01-BUILD-002\n"
                    "Work root: tool-repo\n"
                    "Assignee: coder\n"
                    "Spec: SPEC-01-001-demo.md\n"
                    "Depends on: n/a\n"
                    "Blocked by: TASK-01-001\n"
                    "Created: 2026-05-18\n"
                    "Evidence gate: required\n\n"
                    "## Goal\n\n"
                    "Example goal.\n\n"
                    "## Acceptance\n\n"
                    "- [ ] Example acceptance.\n"
                ),
            )
            result = _run(str(task_path), scaffold.root)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        for field in (
            "task_id",
            "task_title",
            "task_path",
            "task_status",
            "spec_path",
            "dependencies",
            "work_roots_resolved",
            "ready",
            "validator_blockers",
            "expected_prompt_path",
            "expected_report_path",
        ):
            self.assertIn(field, record, msg=f"missing field: {field}")
        self.assertEqual(record["task_id"], "TASK-01-002")
        self.assertEqual(record["task_title"], "TASK-01-002: Example")
        self.assertEqual(record["task_path"], str(task_path.resolve()))
        self.assertEqual(record["task_status"], "open")
        self.assertEqual(
            record["spec_path"],
            str((scaffold.project_root / "specs" / "SPEC-01-001-demo.md").resolve()),
        )
        self.assertEqual(
            record["dependencies"],
            [
                {
                    "task_id": "TASK-01-001",
                    "title": "TASK-01-001: Prereq",
                    "path": str((scaffold.project_root / "tasks" / "done" / "TASK-01-001-prereq.md").resolve()),
                    "status": "done",
                }
            ],
        )
        self.assertEqual(
            record["work_roots_resolved"],
            [
                {
                    "name": "tool-repo",
                    "absolute_path": str(work_root.resolve()),
                    "exists": True,
                }
            ],
        )
        self.assertTrue(record["ready"])
        self.assertEqual(record["validator_blockers"], [])
        self.assertEqual(
            record["expected_prompt_path"],
            str((scaffold.project_root / "prompts" / "PROMPT-01-002.md").resolve()),
        )
        self.assertEqual(
            record["expected_report_path"],
            str((scaffold.project_root / "reports" / "REPORT-01-002.md").resolve()),
        )


class TestTaskBundleNoPlan(unittest.TestCase):
    """F1: Plan ref: n/a must be accepted as a valid no-plan state (exit 0, ready=true)."""

    def test_no_plan_accepted_as_valid(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            work_root = scaffold.root / "tool-repo"
            work_root.mkdir()
            scaffold.write(
                "cartopian.local.toml",
                f'[work_roots]\ntool-repo = "{work_root}"\n',
            )
            scaffold.write("phases/PHASE-01-foundation.md", "# Phase 01\n")
            task_path = scaffold.write(
                "tasks/open/TASK-01-003-no-plan.md",
                (
                    "# TASK-01-003: No Plan\n\n"
                    "Phase: PHASE-01-foundation\n"
                    "Plan ref: n/a\n"
                    "Work root: tool-repo\n"
                    "Assignee: coder\n"
                    "Spec: none\n"
                    "Depends on: n/a\n"
                    "Blocked by: n/a\n"
                    "Created: 2026-05-18\n"
                    "Evidence gate: n/a\n\n"
                    "## Goal\n\nNo plan goal.\n\n"
                    "## Acceptance\n\n"
                    "- [ ] Some acceptance item.\n"
                ),
            )
            result = _run(str(task_path), scaffold.root)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        self.assertTrue(record["ready"], msg=f"validator_blockers={record.get('validator_blockers')}")
        self.assertEqual(record["validator_blockers"], [])
        self.assertIsNone(record["spec_path"])


class TestTaskBundleMissingConfig(unittest.TestCase):
    """F2: Missing cartopian.toml must return exit code 3 (EXIT_ENV), not 1."""

    def test_missing_cartopian_toml_exits_3(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            (scaffold.project_root / "cartopian.toml").unlink()
            scaffold.write("phases/PHASE-01-foundation.md", "# Phase 01\n")
            task_path = scaffold.write(
                "tasks/open/TASK-01-004-no-config.md",
                (
                    "# TASK-01-004: No Config\n\n"
                    "Phase: PHASE-01-foundation\n"
                    "Plan ref: n/a\n"
                    "Work root: n/a\n"
                    "Assignee: coder\n"
                    "Spec: none\n"
                    "Depends on: n/a\n"
                    "Blocked by: n/a\n"
                    "Created: 2026-05-18\n"
                    "Evidence gate: n/a\n\n"
                    "## Goal\n\nNo config goal.\n\n"
                    "## Acceptance\n\n"
                    "- [ ] Some acceptance item.\n"
                ),
            )
            result = _run(str(task_path), scaffold.root)
        self.assertEqual(result.returncode, 3, msg=f"stderr={result.stderr!r}")


class TestTaskBundleUnmetReadiness(unittest.TestCase):
    """Blocked-by task not in done/ must set ready=false and surface an explanation."""

    def test_blocked_by_open_marks_not_ready_with_blocker_reason(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            work_root = scaffold.root / "tool-repo"
            work_root.mkdir()
            scaffold.write(
                "cartopian.local.toml",
                f'[work_roots]\ntool-repo = "{work_root}"\n',
            )
            scaffold.write("IMPLEMENTATION_PLAN.md", "P01-BUILD-002\n")
            scaffold.write("phases/PHASE-01-foundation.md", "# Phase 01\n")
            scaffold.write(
                "tasks/open/TASK-01-001-prereq.md",
                "# TASK-01-001: Prereq\n",
            )
            task_path = scaffold.write(
                "tasks/open/TASK-01-002-example.md",
                (
                    "# TASK-01-002: Example\n\n"
                    "Phase: PHASE-01-foundation\n"
                    "Plan ref: P01-BUILD-002\n"
                    "Work root: tool-repo\n"
                    "Assignee: coder\n"
                    "Spec: none\n"
                    "Depends on: n/a\n"
                    "Blocked by: TASK-01-001\n"
                    "Created: 2026-05-18\n"
                    "Evidence gate: required\n\n"
                    "## Goal\n\nGoal.\n\n"
                    "## Acceptance\n\n"
                    "- [ ] Example acceptance.\n"
                ),
            )
            result = _run(str(task_path), scaffold.root)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        self.assertFalse(record["ready"])
        self.assertTrue(
            any("blocked-by-complete" in b for b in record["validator_blockers"]),
            msg=f"expected blocked-by-complete blocker, got {record['validator_blockers']!r}",
        )
        self.assertTrue(
            any("TASK-01-001" in b for b in record["validator_blockers"]),
            msg=f"expected TASK-01-001 mentioned in blockers, got {record['validator_blockers']!r}",
        )
        self.assertEqual(
            record["dependencies"],
            [
                {
                    "task_id": "TASK-01-001",
                    "title": "TASK-01-001: Prereq",
                    "path": str(
                        (scaffold.project_root / "tasks" / "open" / "TASK-01-001-prereq.md").resolve()
                    ),
                    "status": "open",
                }
            ],
        )


class TestTaskBundleReadOnly(unittest.TestCase):
    """NFR-001: task-bundle must not mutate the project tree."""

    def test_invocation_leaves_fixture_tree_unchanged(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            work_root = scaffold.root / "tool-repo"
            work_root.mkdir()
            scaffold.write(
                "cartopian.local.toml",
                f'[work_roots]\ntool-repo = "{work_root}"\n',
            )
            scaffold.write("IMPLEMENTATION_PLAN.md", "P01-BUILD-002\n")
            scaffold.write("phases/PHASE-01-foundation.md", "# Phase 01\n")
            scaffold.write("specs/SPEC-01-001-demo.md", "# Demo Spec\n")
            scaffold.write(
                "tasks/done/TASK-01-001-prereq.md",
                "# TASK-01-001: Prereq\n",
            )
            task_path = scaffold.write(
                "tasks/open/TASK-01-002-example.md",
                (
                    "# TASK-01-002: Example\n\n"
                    "Phase: PHASE-01-foundation\n"
                    "Plan ref: P01-BUILD-002\n"
                    "Work root: tool-repo\n"
                    "Assignee: coder\n"
                    "Spec: SPEC-01-001-demo.md\n"
                    "Depends on: n/a\n"
                    "Blocked by: TASK-01-001\n"
                    "Created: 2026-05-18\n"
                    "Evidence gate: required\n\n"
                    "## Goal\n\nGoal.\n\n"
                    "## Acceptance\n\n"
                    "- [ ] Example acceptance.\n"
                ),
            )
            before = _snapshot_tree(scaffold.root)
            result = _run(str(task_path), scaffold.root)
            after = _snapshot_tree(scaffold.root)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            before,
            after,
            msg="task-bundle invocation mutated the fixture tree (NFR-001 violation)",
        )


if __name__ == "__main__":
    unittest.main()
