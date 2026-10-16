"""Tests for `cartopian close-audit`."""
import argparse
import unittest
from unittest import mock

from cli.commands import close_audit  # noqa: F401 - red stage: module must exist
from cli.commands.resolve_config import _CliError
from cli.main import SUBCOMMANDS, build_parser
from tests.scaffold import (
    project_scaffold,
    write_stale_prompt_layout,
    write_unresolved_report_layout,
)

_TOML_BASE = (
    "[project]\n"
    'id = "test-proj"\n'
    'name = "Test Project"\n'
    'project_schema_version = "v0.14.0"\n'
)


def _invoke(project_path: str):
    """Invoke handler with emit capture; return (records, exit_code)."""
    args = argparse.Namespace(project_path=project_path)
    captured = []
    original = close_audit.emit_record

    def _capture(record, *, out=None):
        captured.append(record)

    close_audit.emit_record = _capture
    try:
        rc = close_audit.handler(args)
    finally:
        close_audit.emit_record = original
    return captured, rc


class TestCloseAuditRequiredFields(unittest.TestCase):
    def test_cli_subcommand_registered(self) -> None:
        self.assertIn("close-audit", SUBCOMMANDS)
        args = build_parser().parse_args(["close-audit", "/tmp/project"])
        self.assertEqual(args.cmd, "close-audit")

    def test_all_schema_fields_present_for_closable_project(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.write(
                "phases/PHASE-01.md",
                (
                    "# PHASE-01: Foundation\n\n"
                    "## Exit criteria\n\n"
                    "- `TASK-01-001`\n"
                ),
            )
            scaffold.write(
                "tasks/done/TASK-01-001.md",
                "# TASK-01-001: finished\n",
            )
            # A closable plan satisfies the delivery gate. This fixture plan
            # reaches no target outside itself, which is a declaration the
            # record has to carry — an absent section is undeclared, not
            # not-applicable, and undeclared blocks closeout.
            scaffold.write(
                "IMPLEMENTATION_PLAN.md",
                (
                    "# Implementation Plan: Test Project\n\n"
                    "## Delivery contract\n\n"
                    "- Delivery: not-applicable; Justification: the plan changes "
                    "only this fixture project and reaches no target outside it\n"
                ),
            )

            records, rc = _invoke(str(scaffold.project_root))

            self.assertEqual(rc, 0)
            self.assertEqual(len(records), 1)
            rec = records[0]
            for field in (
                "project_id",
                "project_path",
                "closable",
                "open_count",
                "in_progress_count",
                "in_review_count",
                "open_tasks",
                "stale_prompts",
                "unresolved_reports",
                "unmet_exit_criteria",
                "delivery",
                "blocking_reasons",
            ):
                self.assertIn(field, rec, msg=f"missing field: {field}")

            self.assertEqual(rec["project_id"], "test-proj")
            self.assertEqual(rec["project_path"], str(scaffold.project_root.resolve()))
            self.assertTrue(rec["closable"])
            self.assertEqual(rec["open_count"], 0)
            self.assertEqual(rec["in_progress_count"], 0)
            self.assertEqual(rec["in_review_count"], 0)
            self.assertEqual(rec["open_tasks"], [])
            self.assertEqual(rec["stale_prompts"], [])
            self.assertEqual(rec["unresolved_reports"], [])
            self.assertEqual(rec["unmet_exit_criteria"], [])
            self.assertEqual(rec["delivery"]["gate"], "pass")
            self.assertEqual(rec["blocking_reasons"], [])


class TestCloseAuditNoPlanState(unittest.TestCase):
    def test_no_plan_state_returns_nullables(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            records, rc = _invoke(str(scaffold.project_root))

            self.assertEqual(rc, 0)
            self.assertEqual(len(records), 1)
            rec = records[0]
            self.assertEqual(rec["project_id"], "test-proj")
            self.assertEqual(rec["project_path"], str(scaffold.project_root.resolve()))
            self.assertIsNone(rec["closable"])
            self.assertIsNone(rec["open_count"])
            self.assertIsNone(rec["in_progress_count"])
            self.assertIsNone(rec["in_review_count"])
            self.assertIsNone(rec["open_tasks"])
            self.assertIsNone(rec["stale_prompts"])
            self.assertIsNone(rec["unresolved_reports"])
            self.assertIsNone(rec["unmet_exit_criteria"])
            self.assertIsNone(rec["unplanned_phases"])
            self.assertIsNone(rec["unverified_exit_criteria"])
            self.assertIsNone(rec["delivery"])
            self.assertIsNone(rec["blocking_reasons"])


class TestCloseAuditBlockingStates(unittest.TestCase):
    def test_active_tasks_block_closeout_and_counts_are_reported_by_status(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            open_task = scaffold.write(
                "tasks/open/TASK-01-002.md",
                "# TASK-01-002: open work\n",
            )
            in_progress_task = scaffold.write(
                "tasks/in-progress/TASK-01-003.md",
                "# TASK-01-003: active work\n",
            )
            in_review_task = scaffold.write(
                "tasks/in-review/TASK-01-004.md",
                "# TASK-01-004: review work\n",
            )

            records, rc = _invoke(str(scaffold.project_root))

            self.assertEqual(rc, 0)
            rec = records[0]
            self.assertFalse(rec["closable"])
            self.assertEqual(rec["open_count"], 1)
            self.assertEqual(rec["in_progress_count"], 1)
            self.assertEqual(rec["in_review_count"], 1)
            self.assertEqual(
                rec["open_tasks"],
                [
                    {"task_id": "TASK-01-002", "path": str(open_task.resolve()), "status": "open"},
                    {
                        "task_id": "TASK-01-003",
                        "path": str(in_progress_task.resolve()),
                        "status": "in-progress",
                    },
                    {
                        "task_id": "TASK-01-004",
                        "path": str(in_review_task.resolve()),
                        "status": "in-review",
                    },
                ],
            )
            self.assertTrue(
                any("open" in reason.lower() and "TASK-01-002" in reason for reason in rec["blocking_reasons"])
            )
            self.assertTrue(
                any(
                    "in-progress" in reason.lower() and "TASK-01-003" in reason
                    for reason in rec["blocking_reasons"]
                )
            )
            self.assertTrue(
                any(
                    "in-review" in reason.lower() and "TASK-01-004" in reason
                    for reason in rec["blocking_reasons"]
                )
            )

    def test_stale_prompt_blocks_closeout(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            prompt_path = write_stale_prompt_layout(scaffold, task_id="TASK-01-003", slug="stale")

            records, rc = _invoke(str(scaffold.project_root))

            self.assertEqual(rc, 0)
            rec = records[0]
            self.assertFalse(rec["closable"])
            self.assertEqual(
                rec["stale_prompts"],
                [{"path": str(prompt_path.resolve()), "task_id": "TASK-01-003"}],
            )
            self.assertTrue(
                any("stale prompt" in reason.lower() and "TASK-01-003" in reason for reason in rec["blocking_reasons"])
            )

    def test_prompt_with_only_open_task_is_stale(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.write(
                "tasks/open/TASK-01-005.md",
                "# TASK-01-005: not started\n",
            )
            prompt_path = scaffold.write(
                "prompts/PROMPT-01-005.md",
                "# PROMPT-01-005\n\n## Your task\n\nRun TASK-01-005.\n",
            )

            records, rc = _invoke(str(scaffold.project_root))

            self.assertEqual(rc, 0)
            rec = records[0]
            self.assertFalse(rec["closable"])
            self.assertEqual(
                rec["stale_prompts"],
                [{"path": str(prompt_path.resolve()), "task_id": "TASK-01-005"}],
            )
            self.assertTrue(
                any("stale prompt" in reason.lower() and "TASK-01-005" in reason for reason in rec["blocking_reasons"])
            )

    def test_unresolved_report_blocks_closeout(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            report_path, _task_path, _prompt_path = write_unresolved_report_layout(
                scaffold,
                task_id="TASK-01-004",
                slug="pending-report",
                task_status="in-progress",
            )

            records, rc = _invoke(str(scaffold.project_root))

            self.assertEqual(rc, 0)
            rec = records[0]
            self.assertFalse(rec["closable"])
            self.assertEqual(
                rec["unresolved_reports"],
                [{"path": str(report_path.resolve())}],
            )
            self.assertTrue(
                any("unresolved report" in reason.lower() and "REPORT-01-004" in reason for reason in rec["blocking_reasons"])
            )

    def test_unmet_exit_criteria_blocks_closeout(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.write(
                "phases/PHASE-02.md",
                (
                    "# PHASE-02: Closeout\n\n"
                    "## Exit criteria\n\n"
                    "- `TASK-02-001`\n"
                    "- `DEC-002`\n"
                ),
            )
            scaffold.write(
                "tasks/done/TASK-02-001.md",
                "# TASK-02-001: finished\n",
            )

            records, rc = _invoke(str(scaffold.project_root))

            self.assertEqual(rc, 0)
            rec = records[0]
            self.assertFalse(rec["closable"])
            self.assertEqual(len(rec["unmet_exit_criteria"]), 1)
            self.assertIn("DEC-002", rec["unmet_exit_criteria"][0])
            self.assertTrue(
                any("exit criteria" in reason.lower() and "DEC-002" in reason for reason in rec["blocking_reasons"])
            )



_TWO_PHASE_PLAN = (
    "# Implementation Plan: Test Project\n\n"
    "## Delivery contract\n\n"
    "- Delivery: not-applicable; Justification: the plan changes "
    "only this fixture project and reaches no target outside it\n\n"
    "## Phase sequence\n\n"
    "### Phase 01: Foundation\n\n"
    "| Plan ref | Kind | Description |\n"
    "| --- | --- | --- |\n"
    "| BUILD-01-001 | build | first |\n\n"
    "### Phase 02: Build\n\n"
    "| Plan ref | Kind | Description |\n"
    "| --- | --- | --- |\n"
    "| BUILD-02-001 | build | second |\n\n"
    "## Open questions by phase\n\n"
    "### Phase 03\n\n"
    "- OQ-001: not a phase of the plan\n"
)


class TestCloseAuditPlanPhaseSequence(unittest.TestCase):
    """The plan's phase sequence, not phases/, is what closeout must cover."""

    def test_plan_phase_without_phase_file_blocks_closeout(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.write("IMPLEMENTATION_PLAN.md", _TWO_PHASE_PLAN)
            scaffold.write(
                "phases/PHASE-01.md",
                "# PHASE-01: Foundation\n\n## Exit criteria\n\n- `TASK-01-001`\n",
            )
            scaffold.write(
                "tasks/done/TASK-01-001.md",
                "# TASK-01-001: finished\n\nPhase: PHASE-01\n",
            )

            records, rc = _invoke(str(scaffold.project_root))

            self.assertEqual(rc, 0)
            rec = records[0]
            self.assertFalse(rec["closable"])
            # Only Phase sequence headings count; "### Phase 03" under the
            # open questions section is not a plan phase.
            self.assertEqual(len(rec["unplanned_phases"]), 1)
            self.assertIn("PHASE-02", rec["unplanned_phases"][0])
            self.assertIn(
                "plan phase not planned: " + rec["unplanned_phases"][0],
                rec["blocking_reasons"],
            )

    def test_every_plan_phase_with_a_file_does_not_block(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.write("IMPLEMENTATION_PLAN.md", _TWO_PHASE_PLAN)
            scaffold.write("phases/PHASE-01.md", "# PHASE-01: Foundation\n")
            scaffold.write("phases/PHASE-02.md", "# PHASE-02: Build\n")

            records, rc = _invoke(str(scaffold.project_root))

            self.assertEqual(rc, 0)
            self.assertEqual(records[0]["unplanned_phases"], [])
            self.assertTrue(records[0]["closable"])

    def test_early_close_lists_unplanned_phases_as_unfinished(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.write("IMPLEMENTATION_PLAN.md", _TWO_PHASE_PLAN)
            scaffold.write("phases/PHASE-01.md", "# PHASE-01: Foundation\n")
            with mock.patch.object(
                close_audit.trace_binding, "early_close_decision", return_value="DEC-009"
            ):
                records, rc = _invoke(str(scaffold.project_root))

            self.assertEqual(rc, 0)
            rec = records[0]
            self.assertTrue(rec["closable"])
            self.assertEqual(rec["blocking_reasons"], [])
            self.assertTrue(any("PHASE-02" in item for item in rec["left_unfinished"]))


class TestCloseAuditUnverifiedExitCriteria(unittest.TestCase):
    """A prose exit criterion is listed, never silently counted as met."""

    def test_prose_criteria_are_listed_and_id_criteria_are_not(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.write(
                "phases/PHASE-01.md",
                (
                    "# PHASE-01: Foundation\n\n"
                    "## Exit criteria\n\n"
                    "- `TASK-01-001`\n"
                    "- Operator decision recorded for OQ-002.\n"
                ),
            )
            scaffold.write("tasks/done/TASK-01-001.md", "# TASK-01-001: finished\n")

            records, rc = _invoke(str(scaffold.project_root))

            self.assertEqual(rc, 0)
            rec = records[0]
            self.assertEqual(
                rec["unverified_exit_criteria"],
                ["PHASE-01: `Operator decision recorded for OQ-002.`"],
            )
            self.assertEqual(rec["unmet_exit_criteria"], [])
            self.assertFalse(
                any("OQ-002" in reason for reason in rec["blocking_reasons"])
            )

class TestCloseAuditExitCodes(unittest.TestCase):
    def test_exit3_on_missing_config(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.config.unlink()
            args = argparse.Namespace(project_path=str(scaffold.project_root))
            rc = close_audit.handler(args)
            self.assertEqual(rc, 3)

    def test_exit3_on_unreadable_config(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            args = argparse.Namespace(project_path=str(scaffold.project_root))
            unreadable = _CliError(3, "error", f"project config unreadable: {scaffold.config}")
            with mock.patch.object(close_audit, "_load_toml", side_effect=unreadable):
                rc = close_audit.handler(args)
            self.assertEqual(rc, 3)


class TestCloseAuditReadOnlyInvariant(unittest.TestCase):
    def test_no_files_created_or_modified(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.write(
                "phases/PHASE-01.md",
                (
                    "# PHASE-01: Foundation\n\n"
                    "## Exit criteria\n\n"
                    "- `TASK-01-001`\n"
                ),
            )
            scaffold.write(
                "tasks/done/TASK-01-001.md",
                "# TASK-01-001: finished\n",
            )
            before = {
                path: path.stat().st_mtime_ns
                for path in scaffold.project_root.rglob("*")
                if path.is_file()
            }

            _invoke(str(scaffold.project_root))

            after_paths = {path for path in scaffold.project_root.rglob("*") if path.is_file()}
            self.assertSetEqual(after_paths, set(before))
            for path, mtime_before in before.items():
                self.assertEqual(path.stat().st_mtime_ns, mtime_before, msg=f"handler modified: {path}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
