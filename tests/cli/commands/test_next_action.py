"""Tests for `cartopian next-action` (FR-014, DECISION-001)."""
import argparse
import unittest

from cli.commands import next_action  # noqa: F401 — red stage: module must exist
from tests.scaffold import project_scaffold, write_disagreement_layout

_TOML_BASE = (
    "[project]\n"
    'id = "test-proj"\n'
    'name = "Test Project"\n'
    'protocol_version = "v0.3.0"\n'
)


def _invoke(project_path: str):
    """Invoke handler with capture; return (records, exit_code).

    Patches next_action.emit_record (the module-level name) so the handler's
    global lookup hits the capture function rather than the real emitter.
    """
    args = argparse.Namespace(project_path=project_path)
    captured = []
    original = next_action.emit_record

    def _capture(record, *, out=None):
        captured.append(record)

    next_action.emit_record = _capture
    try:
        rc = next_action.handler(args)
    finally:
        next_action.emit_record = original
    return captured, rc


class TestNextActionRequiredFields(unittest.TestCase):
    def test_all_schema_fields_present(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            records, rc = _invoke(str(scaffold.project_root))
            self.assertEqual(rc, 0)
            self.assertEqual(len(records), 1)
            rec = records[0]
            for field in (
                "project_id",
                "project_path",
                "phase_id",
                "active_task",
                "next_open_task",
                "pm_role",
                "pm_dispatch_kind",
                "blockers",
                "state_filesystem_disagreement",
            ):
                self.assertIn(field, rec, msg=f"missing field: {field}")


class TestNextActionHappyPath(unittest.TestCase):
    """Happy-path test: valid project fixture → all FR-001 fields populated."""

    def test_all_fr001_fields_populated(self) -> None:
        # Use a constrained (tier-1/2) PM harness: this test exercises field
        # population, which is orthogonal to the FR-008 advisory gate. A tier-3
        # placeholder agent would (correctly) be blocked by that gate.
        toml = _TOML_BASE + '\n[roles]\npm = "Plans the work."\n\n[handoffs.pm]\nagent = "cartopian-claude-pm"\n'
        state_md = (
            "# test-proj — State\n\n"
            "## Current phase\n\nPHASE-01-foundation\n\n"
            "## Active work\n\nTASK-01-001 (build) is `in-progress`\n\n"
            "## Open Questions\n\nNone.\n"
        )
        with project_scaffold(cartopian_toml=toml, state_md=state_md) as scaffold:
            scaffold.write("phases/PHASE-01-foundation.md", "# Phase 01\n")
            scaffold.write(
                "tasks/in-progress/TASK-01-001-build.md",
                "# TASK-01-001: build\n\nPhase: PHASE-01-foundation\n",
            )
            scaffold.write(
                "tasks/open/TASK-01-002-pending.md",
                "# TASK-01-002: pending\n\nPhase: PHASE-01-foundation\n",
            )
            records, rc = _invoke(str(scaffold.project_root))

            self.assertEqual(rc, 0)
            self.assertEqual(len(records), 1)
            rec = records[0]

            self.assertEqual(rec["project_id"], "test-proj")
            self.assertEqual(rec["project_path"], str(scaffold.project_root.resolve()))
            self.assertEqual(rec["phase_id"], "PHASE-01-foundation")

            self.assertIsNotNone(rec["active_task"])
            self.assertEqual(rec["active_task"]["id"], "TASK-01-001")
            self.assertEqual(rec["active_task"]["status"], "in-progress")
            self.assertIn("TASK-01-001", rec["active_task"]["title"])
            self.assertTrue(rec["active_task"]["path"].endswith("TASK-01-001-build.md"))

            self.assertIsNotNone(rec["next_open_task"])
            self.assertEqual(rec["next_open_task"]["id"], "TASK-01-002")
            self.assertIn("TASK-01-002", rec["next_open_task"]["title"])
            self.assertTrue(rec["next_open_task"]["path"].endswith("TASK-01-002-pending.md"))

            self.assertEqual(rec["pm_role"], "Plans the work.")
            self.assertEqual(rec["pm_dispatch_kind"], "automated")

            self.assertEqual(rec["blockers"], [])
            self.assertIsNone(rec["state_filesystem_disagreement"])


class TestNextActionNullables(unittest.TestCase):
    def test_nullables_are_none_on_empty_project(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            records, rc = _invoke(str(scaffold.project_root))
            self.assertEqual(rc, 0)
            rec = records[0]
            self.assertIsNone(rec["phase_id"])
            self.assertIsNone(rec["active_task"])
            self.assertIsNone(rec["next_open_task"])
            self.assertIsNone(rec["state_filesystem_disagreement"])
            self.assertEqual(rec["blockers"], [])

    def test_project_id_from_toml(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            records, rc = _invoke(str(scaffold.project_root))
            self.assertEqual(rc, 0)
            self.assertEqual(records[0]["project_id"], "test-proj")

    def test_project_path_is_resolved(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            records, rc = _invoke(str(scaffold.project_root))
            self.assertEqual(rc, 0)
            self.assertEqual(records[0]["project_path"], str(scaffold.project_root.resolve()))


class TestNextActionActiveTask(unittest.TestCase):
    def test_active_task_detected_in_progress(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.write(
                "tasks/in-progress/TASK-01-001-do-stuff.md",
                "# TASK-01-001: Do Stuff\n\nSome content.\n",
            )
            records, rc = _invoke(str(scaffold.project_root))
            self.assertEqual(rc, 0)
            at = records[0]["active_task"]
            self.assertIsNotNone(at)
            self.assertEqual(at["id"], "TASK-01-001")
            self.assertEqual(at["status"], "in-progress")
            self.assertIn("TASK-01-001", at["title"])

    def test_active_task_detected_in_review(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.write(
                "tasks/in-review/TASK-02-003-review-work.md",
                "# TASK-02-003: Review Work\n",
            )
            records, rc = _invoke(str(scaffold.project_root))
            self.assertEqual(rc, 0)
            at = records[0]["active_task"]
            self.assertIsNotNone(at)
            self.assertEqual(at["id"], "TASK-02-003")
            self.assertEqual(at["status"], "in-review")

    def test_next_open_task_detected(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.write(
                "tasks/open/TASK-01-002-open-work.md",
                "# TASK-01-002: Open Work\n",
            )
            records, rc = _invoke(str(scaffold.project_root))
            self.assertEqual(rc, 0)
            nxt = records[0]["next_open_task"]
            self.assertIsNotNone(nxt)
            self.assertEqual(nxt["id"], "TASK-01-002")
            self.assertIn("path", nxt)
            self.assertNotIn("status", nxt)

    def test_next_open_task_sorted_first(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.write("tasks/open/TASK-01-003-third.md", "# TASK-01-003: Third\n")
            scaffold.write("tasks/open/TASK-01-001-first.md", "# TASK-01-001: First\n")
            scaffold.write("tasks/open/TASK-01-002-second.md", "# TASK-01-002: Second\n")
            records, rc = _invoke(str(scaffold.project_root))
            self.assertEqual(rc, 0)
            self.assertEqual(records[0]["next_open_task"]["id"], "TASK-01-001")

    def test_next_open_task_prefers_earlier_phase_over_filename_order(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.write("phases/PHASE-01-foundation.md", "# Phase 01\n")
            scaffold.write("phases/PHASE-02-expansion.md", "# Phase 02\n")
            scaffold.write(
                "tasks/open/TASK-00-999-later-phase.md",
                "# TASK-00-999: Later Phase\n\nPhase: PHASE-02-expansion\n",
            )
            scaffold.write(
                "tasks/open/TASK-99-001-earlier-phase.md",
                "# TASK-99-001: Earlier Phase\n\nPhase: PHASE-01-foundation\n",
            )
            records, rc = _invoke(str(scaffold.project_root))
            self.assertEqual(rc, 0)
            self.assertEqual(records[0]["phase_id"], "PHASE-01-foundation")
            self.assertEqual(records[0]["next_open_task"]["id"], "TASK-99-001")


class TestNextActionDispatchKind(unittest.TestCase):
    def test_manual_when_no_handoff(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            records, rc = _invoke(str(scaffold.project_root))
            self.assertEqual(rc, 0)
            self.assertEqual(records[0]["pm_dispatch_kind"], "manual")

    def test_automated_when_handoff_pm_configured(self) -> None:
        # A constrained (tier-1/2) harness keeps the dispatch-kind assertion
        # (configured PM agent → automated) independent of the FR-008 gate.
        toml = _TOML_BASE + "\n[handoffs.pm]\nagent = \"cartopian-claude-pm\"\n"
        with project_scaffold(cartopian_toml=toml) as scaffold:
            records, rc = _invoke(str(scaffold.project_root))
            self.assertEqual(rc, 0)
            self.assertEqual(records[0]["pm_dispatch_kind"], "automated")


class TestNextActionExitCodes(unittest.TestCase):
    def test_exit3_on_missing_config(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.config.unlink()
            args = argparse.Namespace(project_path=str(scaffold.project_root))
            rc = next_action.handler(args)
            self.assertEqual(rc, 3)

    def test_exit2_on_relative_path(self) -> None:
        args = argparse.Namespace(project_path="relative/path")
        rc = next_action.handler(args)
        self.assertEqual(rc, 2)

    def test_exit1_on_defaults_only_cartopian_toml(self) -> None:
        with project_scaffold(cartopian_toml='[defaults]\ngit_versioning = false\n') as scaffold:
            records, rc = _invoke(str(scaffold.project_root))
            self.assertEqual(rc, 1)
            self.assertEqual(records, [])


class TestNextActionDisagreement(unittest.TestCase):
    def test_disagreement_detected(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            write_disagreement_layout(
                scaffold,
                task_id="TASK-01-001",
                task_slug="demo",
                state_claims_status="in-progress",
                filesystem_actual_status="done",
            )
            records, rc = _invoke(str(scaffold.project_root))
            self.assertEqual(rc, 0)
            disagreement = records[0]["state_filesystem_disagreement"]
            self.assertIsNotNone(disagreement)
            self.assertIn("TASK-01-001", disagreement)

    def test_no_disagreement_when_state_matches_filesystem(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.write(
                "tasks/in-progress/TASK-01-001-demo.md",
                "# TASK-01-001: demo\n",
            )
            scaffold.write(
                "STATE.md",
                (
                    "# Test — State\n\n"
                    "## Active work\n\n"
                    "TASK-01-001 (demo) is `in-progress`\n"
                ),
            )
            records, rc = _invoke(str(scaffold.project_root))
            self.assertEqual(rc, 0)
            self.assertIsNone(records[0]["state_filesystem_disagreement"])


class TestNextActionUnreadableConfig(unittest.TestCase):
    def test_exit3_on_corrupt_toml(self) -> None:
        """exit 3 when cartopian.toml exists but is invalid TOML (NFR-004)."""
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.config.write_text("[[this is not valid toml\x00", encoding="utf-8")
            args = argparse.Namespace(project_path=str(scaffold.project_root))
            rc = next_action.handler(args)
            self.assertEqual(rc, 3)


class TestNextActionReadOnlyInvariant(unittest.TestCase):
    def test_no_files_created_or_modified(self) -> None:
        """Handler must not write, move, rename, or delete any file (NFR-001)."""
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.write("tasks/in-progress/TASK-01-001-demo.md", "# TASK-01-001: demo\n")
            scaffold.write("tasks/open/TASK-01-002-pending.md", "# TASK-01-002: pending\n")
            # Snapshot all files and their mtimes before invocation.
            before: dict = {
                p: p.stat().st_mtime_ns
                for p in scaffold.project_root.rglob("*")
                if p.is_file()
            }
            _invoke(str(scaffold.project_root))
            # Snapshot after invocation.
            after_paths = {p for p in scaffold.project_root.rglob("*") if p.is_file()}
            new_files = after_paths - set(before)
            self.assertSetEqual(new_files, set(), msg=f"handler created unexpected files: {new_files}")
            for path, mtime_before in before.items():
                self.assertEqual(
                    path.stat().st_mtime_ns,
                    mtime_before,
                    msg=f"handler modified: {path}",
                )


class TestNextActionBlockers(unittest.TestCase):
    def test_blockers_empty_on_clean_project(self) -> None:
        """No blockers on a project with a phase file and no open questions."""
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.write("phases/PHASE-01-foundation.md", "# Phase 01\n")
            scaffold.write(
                "tasks/open/TASK-01-001-demo.md",
                "# TASK-01-001: demo\n\nPhase: PHASE-01-foundation\n",
            )
            records, rc = _invoke(str(scaffold.project_root))
            self.assertEqual(rc, 0)
            self.assertEqual(records[0]["blockers"], [])

    def test_blocker_missing_phase_when_tasks_present(self) -> None:
        """Blocker reported when tasks exist but no phase is detected (FR-001)."""
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.write("tasks/open/TASK-01-001-demo.md", "# TASK-01-001: demo\n")
            records, rc = _invoke(str(scaffold.project_root))
            self.assertEqual(rc, 0)
            blockers = records[0]["blockers"]
            self.assertIsInstance(blockers, list)
            self.assertGreater(len(blockers), 0, msg="expected at least one blocker for missing phase")
            self.assertTrue(
                any("phase" in b.lower() for b in blockers),
                msg=f"expected a phase-related blocker; got: {blockers}",
            )

    def test_blocker_unresolved_open_question_in_state_md(self) -> None:
        """Blocker reported for each open question listed in STATE.md (FR-001)."""
        state_with_oqs = (
            "# test-proj — State\n\n"
            "## Current phase\n\nPhase 01\n\n"
            "## Open Questions\n\n"
            "- OQ-001: Should we use a single record or two?\n"
            "- OQ-002: What format for compose-state output?\n"
        )
        with project_scaffold(cartopian_toml=_TOML_BASE, state_md=state_with_oqs) as scaffold:
            scaffold.write("phases/PHASE-01-foundation.md", "# Phase 01\n")
            records, rc = _invoke(str(scaffold.project_root))
            self.assertEqual(rc, 0)
            blockers = records[0]["blockers"]
            self.assertIsInstance(blockers, list)
            self.assertGreaterEqual(len(blockers), 2, msg=f"expected 2 OQ blockers; got: {blockers}")
            oq_blockers = [b for b in blockers if "open question" in b.lower()]
            self.assertEqual(len(oq_blockers), 2, msg=f"expected 2 open-question blockers; got: {blockers}")

    def test_no_blocker_for_empty_open_questions_section(self) -> None:
        """No blocker when the Open Questions section exists but lists no items."""
        state_no_oqs = (
            "# test-proj — State\n\n"
            "## Current phase\n\nPhase 01\n\n"
            "## Open Questions\n\nNone.\n\n"
            "## What to do next\n\nContinue.\n"
        )
        with project_scaffold(cartopian_toml=_TOML_BASE, state_md=state_no_oqs) as scaffold:
            scaffold.write("phases/PHASE-01-foundation.md", "# Phase 01\n")
            records, rc = _invoke(str(scaffold.project_root))
            self.assertEqual(rc, 0)
            self.assertEqual(records[0]["blockers"], [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
