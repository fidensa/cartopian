"""Tests for `cartopian next-action`."""
import argparse
import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli.commands import next_action  # noqa: F401 — red stage: module must exist
from cli.commands import resolve_config
from tests.scaffold import project_scaffold, write_disagreement_layout

_TOML_BASE = (
    "[project]\n"
    'id = "test-proj"\n'
    'name = "Test Project"\n'
    'protocol_version = "v0.5.0"\n'
)


@contextlib.contextmanager
def _isolated_home(global_toml: str | None = None):
    """Point ``Path.home()`` at a fresh temp dir for the duration.

    Role resolution merges ``~/.cartopian/cartopian.toml``; without this, the
    developer's real global config would leak into the merge chain under test.
    Pass ``global_toml`` to write a controlled global config into the fake home.
    """
    with tempfile.TemporaryDirectory(prefix="cartopian-home-") as tmp:
        home = Path(tmp)
        if global_toml is not None:
            global_path = home / ".cartopian" / "cartopian.toml"
            global_path.parent.mkdir(parents=True)
            global_path.write_text(global_toml, encoding="utf-8")
        with mock.patch.object(Path, "home", return_value=home):
            yield home


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
                "next_unstarted_phase",
                "plan_complete",
                "pm_role",
                "pm_role_declared",
                "automation",
                "handoffs",
                "reviews",
                "blockers",
                "state_filesystem_disagreement",
            ):
                self.assertIn(field, rec, msg=f"missing field: {field}")


class TestNextActionAutomation(unittest.TestCase):
    """The record carries the resolved [automation] policy so the startup
    skill can gate initiation without a separate resolve-config call."""

    def test_automation_defaults_emitted(self) -> None:
        with _isolated_home():
            with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
                records, rc = _invoke(str(scaffold.project_root))
                self.assertEqual(rc, 0)
                self.assertEqual(
                    records[0]["automation"],
                    {
                        "initiation": "operator",
                        "confirmation": "each-handoff",
                        "max_handoffs_per_run": 1,
                    },
                )

    def test_automation_reflects_merged_config(self) -> None:
        global_toml = '[automation]\nconfirmation = "until-blocked"\n'
        project_toml = _TOML_BASE + '\n[automation]\ninitiation = "auto"\nmax_handoffs_per_run = 3\n'
        with _isolated_home(global_toml=global_toml):
            with project_scaffold(cartopian_toml=project_toml) as scaffold:
                records, rc = _invoke(str(scaffold.project_root))
                self.assertEqual(rc, 0)
                self.assertEqual(
                    records[0]["automation"],
                    {
                        "initiation": "auto",
                        "confirmation": "until-blocked",
                        "max_handoffs_per_run": 3,
                    },
                )


class TestNextActionResolvedWorkflowPolicy(unittest.TestCase):
    def test_reviews_and_explicit_handoff_launch_fields_are_emitted(self) -> None:
        project_toml = (
            _TOML_BASE
            + '\n[roles]\ncoder = "Implements work."\nreviewer = "Checks work."\n'
            + '\n[reviews]\nplanning = "required"\nplanning_role = "reviewer"\n'
            + 'task_closure = "required"\ntask_role = "reviewer"\n'
            + '\n[handoffs.coder]\nagent = "cartopian-claude"\n'
            + 'auto_start_tasks = true\n'
        )
        with _isolated_home():
            with project_scaffold(cartopian_toml=project_toml) as scaffold:
                records, rc = _invoke(str(scaffold.project_root))
        self.assertEqual(rc, 0)
        record = records[0]
        self.assertEqual(record["reviews"]["task_closure"]["mode"], "required")
        self.assertEqual(record["reviews"]["task_closure"]["role"], "reviewer")
        self.assertTrue(record["handoffs"]["coder"]["auto_start_tasks"])
        self.assertNotIn("auto_start", record["handoffs"]["coder"])


class TestNextActionHappyPath(unittest.TestCase):
    """Happy-path test: valid project fixture → all required fields populated."""

    def test_all_fr001_fields_populated(self) -> None:
        # This test exercises field population with a declared PM role.
        toml = _TOML_BASE + '\n[roles]\npm = "Plans the work."\n'
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
            self.assertTrue(rec["pm_role_declared"])

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


class TestNextActionPmRoleDeclared(unittest.TestCase):
    def test_declared_true_when_no_roles_table_via_default_roster(self) -> None:
        # Regression (live-hit): a project that declares no local [roles] and
        # relies on the protocol default roster resolves a `pm` role through
        # resolve-config's merge chain. next-action must apply the same
        # fallthrough — pm_role_declared=true, no false resume blocker.
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold, _isolated_home():
            records, rc = _invoke(str(scaffold.project_root))
            self.assertEqual(rc, 0)
            self.assertTrue(records[0]["pm_role_declared"])
            self.assertEqual(records[0]["pm_role"], next_action._DEFAULT_PM_ROLE)
            self.assertNotIn(
                "pm",
                " ".join(records[0]["blockers"]).lower(),
                msg=f"unexpected PM-role blocker: {records[0]['blockers']}",
            )

    def test_declared_true_via_default_roster_when_only_other_roles_declared(self) -> None:
        # A [roles] table that declares other roles but not pm still resolves
        # pm through the protocol-default backfill, matching resolve-config.
        toml = _TOML_BASE + '\n[roles]\ncoder = "Writes code."\n'
        with project_scaffold(cartopian_toml=toml) as scaffold, _isolated_home():
            records, rc = _invoke(str(scaffold.project_root))
            self.assertEqual(rc, 0)
            self.assertTrue(records[0]["pm_role_declared"])
            self.assertEqual(records[0]["pm_role"], next_action._DEFAULT_PM_ROLE)

    def test_declared_false_when_resolved_roster_lacks_pm(self) -> None:
        # The gate still catches a real absence: a resolved roster with no pm
        # key yields pm_role_declared=false and the placeholder description.
        pm_role, pm_role_declared = next_action._pm_settings_from_resolved(
            {"operator": "Approves things."}
        )
        self.assertFalse(pm_role_declared)
        self.assertEqual(pm_role, next_action._DEFAULT_PM_ROLE)

    def test_declared_true_even_when_description_equals_default(self) -> None:
        # Regression: a project may legitimately declare a pm role whose
        # description equals the default placeholder. The readiness gate keys on
        # role-KEY presence, so pm_role_declared must be True here.
        toml = _TOML_BASE + f'\n[roles]\npm = "{next_action._DEFAULT_PM_ROLE}"\n'
        with project_scaffold(cartopian_toml=toml) as scaffold:
            records, rc = _invoke(str(scaffold.project_root))
            self.assertEqual(rc, 0)
            self.assertTrue(records[0]["pm_role_declared"])
            self.assertEqual(records[0]["pm_role"], next_action._DEFAULT_PM_ROLE)


class TestNextActionResolveConfigParity(unittest.TestCase):
    """next-action and resolve-config must agree on the resolved [roles] table."""

    def _resolve_config_roles(self, project_path: str) -> dict:
        args = argparse.Namespace(project_path=project_path)
        captured = []
        original = resolve_config.emit_record

        def _capture(record, *, out=None):
            captured.append(record)

        resolve_config.emit_record = _capture
        try:
            rc = resolve_config.handler(args)
        finally:
            resolve_config.emit_record = original
        self.assertEqual(rc, 0)
        return captured[0]["roles"]

    def _assert_parity(self, cartopian_toml: str, global_toml: str | None) -> None:
        with project_scaffold(cartopian_toml=cartopian_toml) as scaffold, _isolated_home(global_toml):
            project_path = str(scaffold.project_root)
            roles = self._resolve_config_roles(project_path)
            records, rc = _invoke(project_path)
            self.assertEqual(rc, 0)
            self.assertEqual(records[0]["pm_role_declared"], "pm" in roles)
            if "pm" in roles:
                self.assertEqual(records[0]["pm_role"], roles["pm"])

    def test_parity_on_default_roster_fallthrough(self) -> None:
        # No [roles] anywhere: resolve-config emits the protocol default roster.
        self._assert_parity(_TOML_BASE, None)

    def test_parity_when_roles_declared_without_pm(self) -> None:
        # Local and global [roles] exist but neither declares pm: the protocol
        # default backfills it in resolve-config, and next-action must agree.
        toml = _TOML_BASE + '\n[roles]\ncoder = "Writes code."\n'
        self._assert_parity(toml, '[roles]\nreviewer = "Reviews changes."\n')

    def test_parity_when_pm_declared_locally(self) -> None:
        # Locally-declared pm: behavior unchanged, both report the local text.
        toml = _TOML_BASE + '\n[roles]\npm = "Plans the work."\n'
        self._assert_parity(toml, None)


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
        """Blocker reported when tasks exist but no phase is detected."""
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
        """Blocker reported for each open question listed in STATE.md."""
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

    def test_blocker_unresolved_situation_note_in_state_md(self) -> None:
        """Blocker reported per undelivered Situation note (one-delivery TTL)."""
        state_with_note = (
            "# test-proj — State\n\n"
            "## Current phase\n\nPhase 01\n\n"
            "## Situation\n\n"
            "- coder deploy failed mid-handoff; operator restarting the machine\n"
        )
        with project_scaffold(cartopian_toml=_TOML_BASE, state_md=state_with_note) as scaffold:
            scaffold.write("phases/PHASE-01-foundation.md", "# Phase 01\n")
            records, rc = _invoke(str(scaffold.project_root))
            self.assertEqual(rc, 0)
            note_blockers = [
                b for b in records[0]["blockers"]
                if "situation note" in b.lower()
            ]
            self.assertEqual(len(note_blockers), 1, msg=f"got: {records[0]['blockers']}")
            self.assertIn("coder deploy failed mid-handoff", note_blockers[0])
            self.assertIn("write-state", note_blockers[0])


class TestNextUnstartedPhaseHelper(unittest.TestCase):
    """Pure logic of `_next_unstarted_phase` (FR-012)."""

    _STEMS = ["PHASE-00-rulings", "PHASE-01-foundation", "PHASE-02-build"]

    def test_picks_first_phase_after_last_with_tasks(self):
        self.assertEqual(
            next_action._next_unstarted_phase(self._STEMS, {
                "PHASE-00-rulings": True,
                "PHASE-01-foundation": True,
                "PHASE-02-build": False,
            }),
            "PHASE-02-build",
        )

    def test_skips_earlier_task_less_phase(self):
        # Phase 00 task-less (a completed rulings phase) is behind us, not "next".
        self.assertEqual(
            next_action._next_unstarted_phase(self._STEMS, {
                "PHASE-00-rulings": False,
                "PHASE-01-foundation": True,
                "PHASE-02-build": False,
            }),
            "PHASE-02-build",
        )

    def test_none_when_every_phase_has_tasks(self):
        self.assertIsNone(next_action._next_unstarted_phase(self._STEMS, {
            "PHASE-00-rulings": True,
            "PHASE-01-foundation": True,
            "PHASE-02-build": True,
        }))

    def test_none_when_no_phases(self):
        self.assertIsNone(next_action._next_unstarted_phase([], {}))


class TestPlanCompletionTruth(unittest.TestCase):
    """FR-012: a finished phase with a later un-generated phase is NOT plan
    completion — `next_unstarted_phase` is surfaced and `plan_complete` is False,
    so orientation proposes generating the next phase rather than closeout."""

    def test_phase_done_next_phase_ungenerated_is_not_complete(self):
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.write("phases/PHASE-01-foundation.md", "# Phase 01\n")
            scaffold.write("phases/PHASE-02-build.md", "# Phase 02\n")
            # Phase 01's only task is DONE; Phase 02 exists but has no tasks yet.
            scaffold.write(
                "tasks/done/TASK-01-001-build.md",
                "# TASK-01-001: build\n\nPhase: PHASE-01-foundation\n",
            )
            records, rc = _invoke(str(scaffold.project_root))
            self.assertEqual(rc, 0)
            rec = records[0]
            self.assertIsNone(rec["active_task"])
            self.assertIsNone(rec["next_open_task"])
            self.assertEqual(rec["next_unstarted_phase"], "PHASE-02-build")
            self.assertFalse(rec["plan_complete"])

    def test_all_phases_done_is_complete(self):
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.write("phases/PHASE-01-foundation.md", "# Phase 01\n")
            scaffold.write(
                "tasks/done/TASK-01-001-build.md",
                "# TASK-01-001: build\n\nPhase: PHASE-01-foundation\n",
            )
            records, rc = _invoke(str(scaffold.project_root))
            self.assertEqual(rc, 0)
            rec = records[0]
            self.assertIsNone(rec["next_unstarted_phase"])
            self.assertTrue(rec["plan_complete"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
