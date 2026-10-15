"""Tests for ``cli.backlog_revisit`` — when a deferred entry falls due.

Each ``BACKLOG.md`` entry carries a ``Revisit:`` trigger (``planning`` |
``PHASE-NN`` | ``TASK-NN-NNN``). ``evaluate`` sorts the live entries into
``due`` / ``pending`` / ``missed`` / ``unreadable`` from the plan surface alone,
so ``next-action`` can surface the due ones at the fork that consumes them and
``plan-audit`` can warn when a fork passed without the entry being handled.
"""
import unittest

from cli import backlog_revisit
from tests.scaffold import project_scaffold

_TOML = (
    "[project]\n"
    'id = "demo"\n'
    'name = "Demo"\n'
    'project_schema_version = "v0.14.0"\n'
)


def _backlog(*entries: str) -> str:
    return "# Backlog\n\nHighest id issued: BL-009\n\n" + "\n\n".join(entries) + "\n"


def _entry(bl_id: str, title: str, revisit: str | None, body: str = "Do it.") -> str:
    head = f"## {bl_id} — {title}\n\n"
    return head + (f"Revisit: {revisit}\n\n{body}" if revisit else body)


def _ids(records):
    return [record["id"] for record in records]


class TestGrammar(unittest.TestCase):
    def test_accepts_the_three_forms(self):
        for value in ("planning", "PHASE-02", "TASK-01-002"):
            self.assertIsNone(backlog_revisit.validate_revisit(value), value)

    def test_rejects_everything_else(self):
        for value in ("", "later", "PHASE-2", "TASK-1-2", "BL-001", "phase-01"):
            self.assertIsNotNone(backlog_revisit.validate_revisit(value), value)

    def test_render_body_puts_the_line_first_and_replaces_an_existing_one(self):
        self.assertEqual(
            backlog_revisit.render_body("PHASE-02", "Revisit: planning\n\nBody.\n"),
            "Revisit: PHASE-02\n\nBody.",
        )
        self.assertEqual(backlog_revisit.render_body("planning", ""), "Revisit: planning")


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.scaffold = project_scaffold(cartopian_toml=_TOML)
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root

    def plan(self, *phases: str):
        self.scaffold.write("IMPLEMENTATION_PLAN.md", "# Plan\n")
        for phase in phases:
            self.scaffold.write(f"phases/{phase}.md", f"# {phase}: Work\n")

    def task(self, task_id: str, status: str):
        phase = "PHASE-" + task_id.split("-")[1]
        self.scaffold.write(
            f"tasks/{status}/{task_id}.md", f"# {task_id}: t\n\nPhase: {phase}\n"
        )


class TestEntries(_Fixture):
    def test_absent_file_has_no_entries(self):
        self.assertEqual(backlog_revisit.entries(self.root), [])
        self.assertEqual(backlog_revisit.evaluate(self.root)["live"], 0)

    def test_legacy_entry_without_line_reads_as_planning(self):
        self.scaffold.write("BACKLOG.md", _backlog(_entry("BL-001", "Old", None)))
        [entry] = backlog_revisit.entries(self.root)
        self.assertEqual(entry["revisit"], "planning")
        self.assertFalse(entry["declared"])
        self.assertEqual(entry["title"], "Old")

    def test_unreadable_trigger_is_reported_not_guessed(self):
        self.scaffold.write("BACKLOG.md", _backlog(_entry("BL-001", "Odd", "someday")))
        self.plan("PHASE-01")
        record = backlog_revisit.evaluate(self.root)
        self.assertEqual(_ids(record["unreadable"]), ["BL-001"])
        self.assertEqual(record["due"], [])


class TestNoPlan(_Fixture):
    def test_every_entry_is_due_at_planning_when_no_plan_exists(self):
        self.scaffold.write("BACKLOG.md", _backlog(
            _entry("BL-001", "A", "planning"),
            _entry("BL-002", "B", "PHASE-03"),
            _entry("BL-003", "C", "TASK-01-002"),
        ))
        record = backlog_revisit.evaluate(self.root)
        self.assertEqual(record["live"], 3)
        self.assertEqual(_ids(record["due"]), ["BL-001", "BL-002", "BL-003"])
        self.assertIn("plan project Stage 1", record["due"][0]["reason"])
        self.assertEqual(record["missed"], [])


class TestPlanningTrigger(_Fixture):
    def test_pending_while_a_plan_is_live(self):
        self.plan("PHASE-01")
        self.task("TASK-01-001", "open")
        self.scaffold.write("BACKLOG.md", _backlog(_entry("BL-001", "A", "planning")))
        record = backlog_revisit.evaluate(self.root)
        self.assertEqual(_ids(record["pending"]), ["BL-001"])
        self.assertEqual(record["due"], [])


class TestPhaseTrigger(_Fixture):
    def test_due_when_the_phase_tasks_are_about_to_be_generated(self):
        self.plan("PHASE-01", "PHASE-02")
        self.task("TASK-01-001", "done")
        self.scaffold.write("BACKLOG.md", _backlog(_entry("BL-001", "A", "PHASE-02")))
        record = backlog_revisit.evaluate(self.root)
        self.assertEqual(_ids(record["due"]), ["BL-001"])
        self.assertIn("about to be generated", record["due"][0]["reason"])

    def test_due_while_the_phase_is_in_progress(self):
        self.plan("PHASE-01", "PHASE-02")
        self.task("TASK-01-001", "in-progress")
        self.scaffold.write("BACKLOG.md", _backlog(_entry("BL-001", "A", "PHASE-01")))
        record = backlog_revisit.evaluate(self.root)
        self.assertEqual(_ids(record["due"]), ["BL-001"])

    def test_pending_for_a_later_phase(self):
        self.plan("PHASE-01", "PHASE-02", "PHASE-03")
        self.task("TASK-01-001", "open")
        self.scaffold.write("BACKLOG.md", _backlog(_entry("BL-001", "A", "PHASE-03")))
        record = backlog_revisit.evaluate(self.root)
        self.assertEqual(_ids(record["pending"]), ["BL-001"])

    def test_missed_once_the_phase_is_finished(self):
        self.plan("PHASE-01", "PHASE-02")
        self.task("TASK-01-001", "done")
        self.task("TASK-02-001", "open")
        self.scaffold.write("BACKLOG.md", _backlog(_entry("BL-001", "A", "PHASE-01")))
        record = backlog_revisit.evaluate(self.root)
        self.assertEqual(_ids(record["missed"]), ["BL-001"])
        self.assertIn("finished", record["missed"][0]["reason"])

    def test_missed_when_the_phase_is_not_in_the_plan(self):
        self.plan("PHASE-01")
        self.task("TASK-01-001", "open")
        self.scaffold.write("BACKLOG.md", _backlog(_entry("BL-001", "A", "PHASE-07")))
        record = backlog_revisit.evaluate(self.root)
        self.assertEqual(_ids(record["missed"]), ["BL-001"])


class TestTaskTrigger(_Fixture):
    def test_due_when_the_task_is_open_or_active(self):
        self.plan("PHASE-01")
        self.task("TASK-01-001", "open")
        self.task("TASK-01-002", "in-review")
        self.scaffold.write("BACKLOG.md", _backlog(
            _entry("BL-001", "A", "TASK-01-001"),
            _entry("BL-002", "B", "TASK-01-002"),
        ))
        record = backlog_revisit.evaluate(self.root)
        self.assertEqual(_ids(record["due"]), ["BL-001", "BL-002"])
        self.assertIn("is open", record["due"][0]["reason"])

    def test_due_before_the_task_is_authored_when_its_phase_is_next(self):
        self.plan("PHASE-01", "PHASE-02")
        self.task("TASK-01-001", "done")
        self.scaffold.write("BACKLOG.md", _backlog(_entry("BL-001", "A", "TASK-02-003")))
        record = backlog_revisit.evaluate(self.root)
        self.assertEqual(_ids(record["due"]), ["BL-001"])
        self.assertIn("not authored yet", record["due"][0]["reason"])

    def test_pending_before_the_task_phase_is_reached(self):
        self.plan("PHASE-01", "PHASE-02", "PHASE-03")
        self.task("TASK-01-001", "open")
        self.scaffold.write("BACKLOG.md", _backlog(_entry("BL-001", "A", "TASK-03-001")))
        record = backlog_revisit.evaluate(self.root)
        self.assertEqual(_ids(record["pending"]), ["BL-001"])

    def test_missed_once_the_task_is_done(self):
        self.plan("PHASE-01")
        self.task("TASK-01-001", "done")
        self.task("TASK-01-002", "open")
        self.scaffold.write("BACKLOG.md", _backlog(_entry("BL-001", "A", "TASK-01-001")))
        record = backlog_revisit.evaluate(self.root)
        self.assertEqual(_ids(record["missed"]), ["BL-001"])
        self.assertIn("is done", record["missed"][0]["reason"])

    def test_startup_record_is_live_and_due_only(self):
        self.plan("PHASE-01")
        self.task("TASK-01-001", "open")
        self.scaffold.write("BACKLOG.md", _backlog(
            _entry("BL-001", "A", "TASK-01-001"),
            _entry("BL-002", "B", "planning"),
        ))
        record = backlog_revisit.startup_record(self.root)
        self.assertEqual(set(record), {"live", "due"})
        self.assertEqual(record["live"], 2)
        self.assertEqual(_ids(record["due"]), ["BL-001"])


if __name__ == "__main__":
    unittest.main()


class TestPhaseTriggerWaitsForGeneration(_Fixture):
    def test_pending_while_an_earlier_phase_still_has_live_work(self):
        self.plan("PHASE-01", "PHASE-02")
        self.task("TASK-01-001", "in-progress")
        self.scaffold.write("BACKLOG.md", _backlog(
            _entry("BL-001", "A", "PHASE-02"),
            _entry("BL-002", "B", "TASK-02-001"),
        ))
        record = backlog_revisit.evaluate(self.root)
        self.assertEqual(_ids(record["pending"]), ["BL-001", "BL-002"])
        self.assertIn("after the current phase completes", record["pending"][0]["reason"])
