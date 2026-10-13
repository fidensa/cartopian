"""An in-progress task that replanning made wait on a prerequisite is a valid state.

RED: replanning gave in-progress TASK-01-001 a new ``Blocked by:`` prerequisite
that was still open. ``plan-audit`` demanded a prompt for every in-progress
task, while prompt composition correctly refused one until the prerequisite's
deliverable existed, so the project carried a global ``missing-prompt``
blocker it could not clear; ``next-action`` and ``STATE.md`` both said to
continue the task that could not move.
GREEN: a valid wait (every prerequisite a real task, no cycle) defers the
prompt with a ``task-waiting-on-prerequisite`` warning, and next-action and
compose-state route to the prerequisite. An invalid wait still blocks, and the
parent stays unready until its prerequisites are done with their deliverables.
"""
from __future__ import annotations

import unittest

from cli import task_dependencies
from cli.commands import compose_state, plan_audit
from cli.commands.validate_task_readiness import _check_blocked_by
from tests.cli.commands.test_next_action import _TOML_BASE, _invoke
from tests.scaffold import project_scaffold


def task(task_id: str, blocked_by: str = "none", extra: str = "") -> str:
    return f"# {task_id}: Work {task_id}\n\nPhase: PHASE-01\nBlocked by: {blocked_by}\n{extra}\n## Goal\n\nDo it.\n"


class WaitCase(unittest.TestCase):
    def setUp(self) -> None:
        self._cm = project_scaffold(cartopian_toml=_TOML_BASE)
        self.scaffold = self._cm.__enter__()
        self.addCleanup(self._cm.__exit__, None, None, None)
        self.root = self.scaffold.project_root
        self.scaffold.write("tasks/done/TASK-01-000.md", task("TASK-01-000"))

    def parent(self, blocked_by: str) -> None:
        self.scaffold.write("tasks/in-progress/TASK-01-001.md", task("TASK-01-001", blocked_by))

    def chain_findings(self):
        return plan_audit._check_artifact_chains(self.root)  # noqa: SLF001

    def kinds(self, findings) -> list:
        return [finding["kind"] for finding in findings]


class ValidWaitTests(WaitCase):
    def setUp(self) -> None:
        super().setUp()
        self.parent("TASK-01-000, TASK-01-002")
        self.scaffold.write("tasks/open/TASK-01-002.md", task("TASK-01-002", "TASK-01-000"))

    def test_a_valid_wait_defers_the_prompt_and_names_the_prerequisite(self) -> None:
        blockers, warnings = self.chain_findings()
        self.assertNotIn("missing-prompt", self.kinds(blockers))
        warning = next(w for w in warnings if w["kind"] == "task-waiting-on-prerequisite")
        self.assertEqual(warning["waiting_on"], ["TASK-01-002"])
        self.assertEqual(warning["prerequisite"], "TASK-01-002")

    def test_the_parent_stays_unready(self) -> None:
        check = _check_blocked_by(self.root, {"Blocked by": "TASK-01-000, TASK-01-002"})
        self.assertFalse(check["pass"])
        self.assertIn("TASK-01-002", check["reason"])

    def test_state_and_next_action_agree_on_the_wait(self) -> None:
        records, rc = _invoke(str(self.root))
        self.assertEqual(rc, 0)
        record = records[0]
        self.assertIsNone(record["active_task"])
        self.assertEqual(record["next_open_task"]["id"], "TASK-01-002")
        self.assertEqual(record["waiting_task"]["id"], "TASK-01-001")
        self.assertEqual(record["startup"]["waiting_task"], "TASK-01-001")
        self.assertIn("TASK-01-001 waits on TASK-01-002; Start TASK-01-002 first", record["startup"]["detail"])
        self.assertFalse(record["plan_complete"])

        state = compose_state.compose_record(self.root, "Test Project")
        self.assertEqual(
            state["what_to_do_next"],
            "TASK-01-001 waits on TASK-01-002; Start TASK-01-002 first (`tasks/open/TASK-01-002.md`).",
        )
        self.assertIn("[waiting on: TASK-01-002]", state["active_work"])

    def test_an_active_prerequisite_is_continued(self) -> None:
        moved = self.root / "tasks/in-progress/TASK-01-002.md"
        (self.root / "tasks/open/TASK-01-002.md").rename(moved)
        wait = task_dependencies.dependency_wait(self.root, self.root / "tasks/in-progress/TASK-01-001.md")
        self.assertEqual(wait.prerequisite["status"], "in-progress")
        self.assertIn("Continue TASK-01-002 first", task_dependencies.describe("TASK-01-001", wait))

    def test_the_deepest_ready_prerequisite_moves_first(self) -> None:
        self.scaffold.write("tasks/open/TASK-01-002.md", task("TASK-01-002", "TASK-01-003"))
        self.scaffold.write("tasks/open/TASK-01-003.md", task("TASK-01-003"))
        wait = task_dependencies.dependency_wait(self.root, self.root / "tasks/in-progress/TASK-01-001.md")
        self.assertEqual(wait.waiting_on, ["TASK-01-002"])
        self.assertEqual(wait.prerequisite["id"], "TASK-01-003")


class InvalidWaitTests(WaitCase):
    def assert_still_blocked(self, reason: str) -> None:
        blockers, warnings = self.chain_findings()
        blocker = next(b for b in blockers if b["kind"] == "missing-prompt")
        self.assertIn(reason, blocker["detail"])
        self.assertNotIn("task-waiting-on-prerequisite", self.kinds(warnings))
        records, _rc = _invoke(str(self.root))
        self.assertEqual(records[0]["active_task"]["id"], "TASK-01-001")
        self.assertIsNone(records[0]["waiting_task"])

    def test_an_unblocked_task_without_a_prompt_is_blocked(self) -> None:
        self.parent("TASK-01-000")
        blockers, _warnings = self.chain_findings()
        self.assertIn("missing-prompt", self.kinds(blockers))

    def test_a_missing_prerequisite_blocks(self) -> None:
        self.parent("TASK-01-009")
        self.assert_still_blocked("TASK-01-009, which is not a task in tasks/")

    def test_a_cycle_blocks(self) -> None:
        self.parent("TASK-01-002")
        self.scaffold.write("tasks/open/TASK-01-002.md", task("TASK-01-002", "TASK-01-001"))
        self.assert_still_blocked("dependency cycle: TASK-01-001 -> TASK-01-002 -> TASK-01-001")

    def test_a_cycle_among_prerequisites_blocks(self) -> None:
        self.parent("TASK-01-002")
        self.scaffold.write("tasks/open/TASK-01-002.md", task("TASK-01-002", "TASK-01-003"))
        self.scaffold.write("tasks/open/TASK-01-003.md", task("TASK-01-003", "TASK-01-002"))
        self.assert_still_blocked("dependency cycle")

    def test_an_unreadable_prerequisite_blocks(self) -> None:
        self.parent("TASK-01-002")
        prerequisite = self.scaffold.write("tasks/open/TASK-01-002.md", task("TASK-01-002"))
        prerequisite.chmod(0)
        self.addCleanup(prerequisite.chmod, 0o644)
        self.assert_still_blocked("TASK-01-002 is unreadable")


class PrerequisiteDoneTests(WaitCase):
    def test_the_prompt_requirement_resumes_and_the_deliverable_guard_holds(self) -> None:
        self.parent("TASK-01-002")
        self.scaffold.write(
            "tasks/done/TASK-01-002.md",
            task("TASK-01-002", extra="Deliverable: project:resources/design/proof.md\n"),
        )
        self.assertIsNone(
            task_dependencies.dependency_wait(self.root, self.root / "tasks/in-progress/TASK-01-001.md")
        )
        blockers, _warnings = self.chain_findings()
        self.assertIn("missing-prompt", self.kinds(blockers))
        check = _check_blocked_by(self.root, {"Blocked by": "TASK-01-002"})
        self.assertFalse(check["pass"])
        self.assertIn("deliverable not persisted", check["reason"])

        self.scaffold.write("resources/design/proof.md", "# Proof design\n")
        self.assertTrue(_check_blocked_by(self.root, {"Blocked by": "TASK-01-002"})["pass"])
