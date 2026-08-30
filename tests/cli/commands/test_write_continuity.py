"""Coverage for `cartopian write-continuity` — the enabled closeout path.

The decisive assertions are the ones the accepted design names: the four
closeout sequences, the plan-id collision the reservation closes, the
row-specific `Body` semantics across a mode change, the removal rules, and the
recovery behavior of a pass that is interrupted or refused.
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from cli import continuity as cy, prompt_evidence
from tests.continuity_support import (
    LEDGER_BODY,
    archive,
    continuity_scaffold,
    decision,
    release,
    run_cli,
    seed_evidence,
    write_continuity,
)


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.scaffold = continuity_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root
        self.artifact = self.root / "CONTINUITY.md"

    def details(self, records):
        self.assertTrue(records, "no record emitted")
        return records[0]["details"]

    def model(self):
        return cy.read_artifact(self.root).model


class TestRegistration(_Fixture):
    def test_registered_on_cli_and_mcp_surfaces(self):
        from cli.main import SUBCOMMANDS
        from mcp_server import server

        tools = {tool["name"] for tool in server.list_tools()}
        for cli_name in ("write-continuity", "prune-continuity", "release-reservation"):
            self.assertIn(cli_name, SUBCOMMANDS)
            self.assertIn(cli_name.replace("-", "_"), tools)


class TestArchivePlusIndex(_Fixture):
    def setUp(self):
        super().setUp()
        decision(self.scaffold, "DEC-001", scope="delivery",
                 ruling="Approval precedes publication.")

    def test_the_sequence_binds_the_write_to_the_archive_just_created(self):
        self.assertEqual(archive(self.scaffold, "2026-08-14"), "PLAN-001")
        code, records, err = write_continuity(
            self.scaffold, "index", "2026-08-14", plan="PLAN-001"
        )
        self.assertEqual(code, 0, err)
        detail = self.details(records)
        self.assertEqual(detail["plan_source"], "archive-bound")
        self.assertEqual(detail["publication"], "published")
        self.assertIsNone(detail["effectiveness_closeout"])
        row = self.model().live[0]
        self.assertEqual(row.id, "PLAN-001/DEC-001")
        self.assertEqual(row.body, "archive/PLAN-001/decisions/DEC-001.md")
        self.assertTrue((self.root / row.body).is_file())
        self.assertEqual(self.model().retrieval, "body")

    def test_the_artifact_survives_reset_byte_for_byte(self):
        archive(self.scaffold, "2026-08-14")
        write_continuity(self.scaffold, "index", "2026-08-14", plan="PLAN-001")
        before = self.artifact.read_bytes()
        code, records, err = run_cli("reset-plan", str(self.root))
        self.assertEqual(code, 0, err)
        self.assertIn(
            str(self.root / "decisions" / "DEC-001.md"), self.details(records)["removed"]
        )
        self.assertEqual(self.artifact.read_bytes(), before)

    def test_an_index_write_refuses_an_archive_it_is_not_bound_to(self):
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-14", plan="PLAN-001"
        )
        self.assertEqual(code, 1)
        self.assertIn("continuity-archive-unbound", err)
        self.assertFalse(self.artifact.exists())

    def test_an_index_write_refuses_a_reservation(self):
        seed_evidence(self.scaffold, "PLAN-001")
        write_continuity(self.scaffold, "ledger", "2026-08-14", plan="PLAN-001")
        self.artifact.unlink()
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-19", plan="PLAN-001"
        )
        self.assertEqual(code, 1)
        self.assertIn("continuity-archive-unbound", err)
        self.assertIn("no CLOSEOUT.md", err)

    def test_an_index_write_refuses_an_archive_below_the_highest_entry(self):
        archive(self.scaffold, "2026-08-14")
        archive(self.scaffold, "2026-08-19")
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-14", plan="PLAN-001"
        )
        self.assertEqual(code, 1)
        self.assertIn("continuity-archive-unbound", err)
        self.assertIn("highest archive entry is PLAN-002", err)

    def test_a_row_naming_a_missing_body_refuses(self):
        archive(self.scaffold, "2026-08-14")
        (self.root / "archive/PLAN-001/decisions/DEC-001.md").unlink()
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-14", plan="PLAN-001"
        )
        self.assertEqual(code, 1)
        self.assertIn("continuity-locator-absent", err)

    def test_a_non_canonical_decision_basename_refuses_the_compute_pass(self):
        self.scaffold.write("decisions/DEC-002-descriptive-name.md",
                            "# DEC-002\n\nStatus: locked\n")
        archive(self.scaffold, "2026-08-14")
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-14", plan="PLAN-001"
        )
        self.assertEqual(code, 1)
        self.assertIn("continuity-decision-name-noncanonical", err)


class TestLedgerOutcome(_Fixture):
    def setUp(self):
        super().setUp()
        decision(self.scaffold, "DEC-014", scope="intake",
                 ruling="Every intake record names its requester.")

    def test_the_ledger_pass_reserves_its_plan_id_without_archiving(self):
        seed_evidence(self.scaffold, "PLAN-001")
        code, records, err = write_continuity(self.scaffold, "ledger", "2026-08-19")
        self.assertEqual(code, 0, err)
        detail = self.details(records)
        self.assertEqual(detail["plan"], "PLAN-001")
        self.assertEqual(detail["plan_source"], "reservation-allocated")
        self.assertEqual(detail["window_witness"], "PLAN-001")
        reservation = self.root / "archive" / "PLAN-001"
        self.assertEqual(sorted(os.listdir(reservation)), [cy.NOT_ARCHIVED_BASENAME])
        self.assertFalse((reservation / "CLOSEOUT.md").exists())
        self.assertIn(
            cy.reservation_row("PLAN-001", "2026-08-19"),
            (self.root / "archive/INDEX.md").read_text(encoding="utf-8"),
        )
        self.assertEqual(self.model().live[0].body, "none")
        self.assertEqual(self.model().retrieval, "ruling")

    def test_the_ledger_pass_closes_its_own_evidence_window(self):
        seed_evidence(self.scaffold, "PLAN-001")
        code, records, err = write_continuity(self.scaffold, "ledger", "2026-08-19")
        self.assertEqual(code, 0, err)
        closeout = self.details(records)["effectiveness_closeout"]
        self.assertEqual(closeout["plan"], "PLAN-001")
        self.assertEqual(closeout.get("ledger_errors"), [])
        self.assertTrue(closeout["log_deleted"])
        self.assertEqual(closeout["closing_projection"]["matched"], 1)
        # reset-plan's own derived window is then inert.
        code, records, err = run_cli("reset-plan", str(self.root))
        self.assertEqual(code, 0, err)
        self.assertTrue(
            self.details(records)["effectiveness_closeout"]["already_closed"]
        )

    def test_reset_plan_alone_would_have_reported_the_window_foreign(self):
        # The negative the contract measures, asserted *not* to be the shipped
        # sequence: with the reservation present, reset-plan derives PLAN-002
        # while the log's records carry PLAN-001, and deletes the log anyway.
        seed_evidence(self.scaffold, "PLAN-001")
        write_continuity(self.scaffold, "ledger", "2026-08-19")
        seed_evidence(self.scaffold, "PLAN-001", unit="TASK-02-002")
        code, records, err = run_cli("reset-plan", str(self.root))
        self.assertEqual(code, 0, err)
        closeout = self.details(records)["effectiveness_closeout"]
        self.assertEqual(closeout["plan"], "PLAN-002")
        self.assertEqual(
            [entry["rule"] for entry in closeout["ledger_errors"]],
            ["foreign-plan-window"],
        )

    def test_the_plan_id_collision_is_closed_with_no_allocator_change(self):
        decision(self.scaffold, "DEC-001", scope="delivery", ruling="Approval first.")
        self.assertEqual(archive(self.scaffold, "2026-08-14"), "PLAN-001")
        code, _, err = write_continuity(
            self.scaffold, "ledger", "2026-08-19", plan="PLAN-002"
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(archive(self.scaffold, "2026-08-24"), "PLAN-003")
        self.assertEqual(prompt_evidence.current_plan_id(self.root), "PLAN-004")

    def test_without_the_reservation_the_same_fixture_collides(self):
        # The defect the reservation exists to prevent, reproduced against the
        # unmodified allocator.
        decision(self.scaffold, "DEC-001", scope="delivery", ruling="Approval first.")
        archive(self.scaffold, "2026-08-14")
        write_continuity(self.scaffold, "ledger", "2026-08-19", plan="PLAN-002")
        import shutil

        shutil.rmtree(self.root / "archive" / "PLAN-002")
        self.assertEqual(archive(self.scaffold, "2026-08-24"), "PLAN-002")
        self.assertEqual(prompt_evidence.current_plan_id(self.root), "PLAN-003")


class TestPassSelection(_Fixture):
    def setUp(self):
        super().setUp()
        decision(self.scaffold, "DEC-001", scope="delivery", ruling="Approval first.")
        archive(self.scaffold, "2026-08-14")
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-14", plan="PLAN-001"
        )
        self.assertEqual(code, 0, err)

    def test_a_second_write_for_the_same_plan_is_a_ledger_replace(self):
        before = self.artifact.read_bytes()
        code, records, err = write_continuity(
            self.scaffold, "index", "2026-08-14", plan="PLAN-001"
        )
        self.assertEqual(code, 0, err)
        detail = self.details(records)
        self.assertEqual(detail["plan_source"], "recorded")
        self.assertFalse(detail["recomputed"])
        self.assertTrue(detail["already_written"])
        self.assertEqual(self.artifact.read_bytes(), before)

    def test_a_replace_changes_only_the_six_group_body(self):
        corrected = LEDGER_BODY.replace("Risks: none open", "Risks: one open")
        code, records, err = write_continuity(
            self.scaffold, "index", "2026-08-14", plan="PLAN-001", content=corrected
        )
        self.assertEqual(code, 0, err)
        self.assertFalse(self.details(records)["already_written"])
        model = self.model()
        self.assertEqual(model.sections[0].preservation, "archive+index")
        self.assertEqual(model.sections[0].closed, "2026-08-14")
        self.assertIn("- Risks: one open", model.sections[0].body_lines)

    def test_a_replace_refuses_a_mode_or_date_that_disagrees(self):
        code, _, err = write_continuity(
            self.scaffold, "ledger", "2026-08-14", plan="PLAN-001"
        )
        self.assertEqual(code, 1)
        self.assertIn("continuity-mode-mismatch", err)
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-19", plan="PLAN-001"
        )
        self.assertEqual(code, 1)
        self.assertIn("continuity-closed-date-mismatch", err)

    def test_a_gap_id_is_never_retro_recorded(self):
        decision(self.scaffold, "DEC-002", scope="intake", ruling="Intake names a date.")
        archive(self.scaffold, "2026-08-19")   # PLAN-002, closed `archive` only
        archive(self.scaffold, "2026-08-24")   # PLAN-003
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-24", plan="PLAN-003"
        )
        self.assertEqual(code, 0, err)
        model = self.model()
        self.assertEqual([s.plan for s in model.sections], ["PLAN-001", "PLAN-003"])
        self.assertEqual(model.plans_recorded, 2)
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-19", plan="PLAN-002"
        )
        self.assertEqual(code, 1)
        self.assertIn("continuity-plan-id-mismatch", err)

    def test_a_closed_plan_below_the_highest_is_not_rewritten(self):
        decision(self.scaffold, "DEC-002", scope="intake", ruling="Intake names a date.")
        archive(self.scaffold, "2026-08-19")
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-19", plan="PLAN-002"
        )
        self.assertEqual(code, 0, err)
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-14", plan="PLAN-001"
        )
        self.assertEqual(code, 1)
        self.assertIn("continuity-plan-closed", err)

    def test_a_compute_pass_cannot_observe_a_destroyed_plan_surface(self):
        decision(self.scaffold, "DEC-002", scope="intake", ruling="Intake names a date.")
        archive(self.scaffold, "2026-08-19")
        (self.root / "IMPLEMENTATION_PLAN.md").unlink()
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-19", plan="PLAN-002"
        )
        self.assertEqual(code, 1)
        self.assertIn("continuity-plan-surface-absent", err)
        self.assertEqual(self.model().plans_recorded, 1)
        self.assertFalse((self.root / "archive" / "PLAN-003").exists())


class TestRowSemanticsAcrossModes(_Fixture):
    def test_prior_rows_are_carried_through_byte_for_byte(self):
        decision(self.scaffold, "DEC-001", scope="delivery", ruling="Approval first.")
        archive(self.scaffold, "2026-08-14")
        write_continuity(self.scaffold, "index", "2026-08-14", plan="PLAN-001")
        first_row = self.model().live[0]

        (self.root / "decisions" / "DEC-001.md").unlink()
        decision(self.scaffold, "DEC-014", scope="intake", ruling="Intake names a requester.")
        seed_evidence(self.scaffold, "PLAN-002")
        code, _, err = write_continuity(
            self.scaffold, "ledger", "2026-08-19", plan="PLAN-002"
        )
        self.assertEqual(code, 0, err)
        model = self.model()
        self.assertEqual(model.live[0].id, "PLAN-002/DEC-014")
        self.assertEqual(model.live[0].body, "none")
        self.assertEqual(model.live[1], first_row)     # byte-identical carry-through
        self.assertEqual(model.retrieval, "mixed")

    def test_a_later_mode_never_backfills_or_blanks_a_prior_rows_body(self):
        decision(self.scaffold, "DEC-014", scope="intake", ruling="Intake names a requester.")
        seed_evidence(self.scaffold, "PLAN-001")
        write_continuity(self.scaffold, "ledger", "2026-08-14", plan="PLAN-001")
        ledger_row = self.model().live[0]

        (self.root / "decisions" / "DEC-014.md").unlink()
        decision(self.scaffold, "DEC-002", scope="containment", ruling="No raw delete verb.")
        archive(self.scaffold, "2026-08-19")
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-19", plan="PLAN-002"
        )
        self.assertEqual(code, 0, err)
        model = self.model()
        self.assertEqual(model.live[0].body, "archive/PLAN-002/decisions/DEC-002.md")
        self.assertEqual(model.live[1], ledger_row)
        self.assertEqual(model.live[1].body, "none")
        self.assertEqual(model.retrieval, "mixed")


class TestSurvivalSupersessionAndExpiry(_Fixture):
    def _first_plan(self):
        decision(self.scaffold, "DEC-001", scope="delivery",
                 ruling="Publication may precede approval for drafts.")
        archive(self.scaffold, "2026-08-14")
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-14", plan="PLAN-001"
        )
        self.assertEqual(code, 0, err)
        (self.root / "decisions" / "DEC-001.md").unlink()

    def test_only_a_locked_scoped_decision_becomes_a_live_row(self):
        decision(self.scaffold, "DEC-001", scope="delivery", ruling="Approval first.")
        decision(self.scaffold, "DEC-002", scope="intake", ruling="Intake names a date.",
                 status="open")
        decision(self.scaffold, "DEC-003")
        archive(self.scaffold, "2026-08-14")
        code, records, err = write_continuity(
            self.scaffold, "index", "2026-08-14", plan="PLAN-001"
        )
        self.assertEqual(code, 0, err)
        self.assertEqual([row.id for row in self.model().live], ["PLAN-001/DEC-001"])
        self.assertEqual(self.details(records)["skipped_unscoped"], ["PLAN-001/DEC-003"])

    def test_supersession_moves_the_prior_row_and_names_who_replaced_it(self):
        self._first_plan()
        decision(self.scaffold, "DEC-001", scope="delivery",
                 ruling="Approval precedes publication.",
                 supersedes="PLAN-001/DEC-001")
        archive(self.scaffold, "2026-08-19")
        code, records, err = write_continuity(
            self.scaffold, "index", "2026-08-19", plan="PLAN-002"
        )
        self.assertEqual(code, 0, err)
        detail = self.details(records)
        self.assertEqual(detail["superseded"], ["PLAN-001/DEC-001"])
        model = self.model()
        self.assertEqual([row.id for row in model.live], ["PLAN-002/DEC-001"])
        cold = model.cold[0]
        self.assertEqual(cold.disposition, "superseded")
        self.assertEqual(cold.removed_by, "PLAN-002/DEC-001")
        # The cold row keeps its own locator — a property of the plan that
        # recorded it, never rewritten by this closeout's mode.
        self.assertEqual(cold.body, "archive/PLAN-001/decisions/DEC-001.md")

    def test_pure_expiry_removes_its_target_and_creates_no_row(self):
        self._first_plan()
        decision(self.scaffold, "DEC-005", expires="PLAN-001/DEC-001")
        archive(self.scaffold, "2026-08-19")
        code, records, err = write_continuity(
            self.scaffold, "index", "2026-08-19", plan="PLAN-002"
        )
        self.assertEqual(code, 0, err)
        detail = self.details(records)
        self.assertEqual(detail["expired"], ["PLAN-001/DEC-001"])
        self.assertEqual(detail["removal_only"], ["PLAN-002/DEC-005"])
        self.assertEqual(detail["skipped_unscoped"], [])
        model = self.model()
        self.assertEqual(model.live, [])
        self.assertEqual(model.cold[0].removed_by, "PLAN-002/DEC-005")
        self.assertEqual(model.cold[0].disposition, "expired")

    def test_supersession_without_a_replacement_refuses(self):
        self._first_plan()
        decision(self.scaffold, "DEC-005", supersedes="PLAN-001/DEC-001")
        archive(self.scaffold, "2026-08-19")
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-19", plan="PLAN-002"
        )
        self.assertEqual(code, 1)
        self.assertIn("continuity-supersession-without-replacement", err)
        self.assertEqual(len(self.model().live), 1)

    def test_removal_conflicts_refuse_the_whole_closeout(self):
        cases = {
            "duplicated": (
                dict(dec_id="DEC-002", scope="a", ruling="r one",
                     supersedes="PLAN-001/DEC-001"),
                dict(dec_id="DEC-003", scope="b", ruling="r two",
                     supersedes="PLAN-001/DEC-001"),
                "continuity-removal-target-duplicated",
            ),
            "conflicted": (
                dict(dec_id="DEC-002", scope="a", ruling="r one",
                     supersedes="PLAN-001/DEC-001"),
                dict(dec_id="DEC-003", expires="PLAN-001/DEC-001"),
                "continuity-removal-target-conflicted",
            ),
        }
        for label, (first, second, rule) in cases.items():
            with self.subTest(case=label):
                self.setUp()
                self._first_plan()
                decision(self.scaffold, first.pop("dec_id"), **first)
                decision(self.scaffold, second.pop("dec_id"), **second)
                archive(self.scaffold, "2026-08-19")
                before = self.artifact.read_bytes()
                code, _, err = write_continuity(
                    self.scaffold, "index", "2026-08-19", plan="PLAN-002"
                )
                self.assertEqual(code, 1)
                self.assertIn(rule, err)
                self.assertEqual(self.artifact.read_bytes(), before)

    def test_one_decision_naming_a_target_in_both_headers_conflicts(self):
        self._first_plan()
        decision(self.scaffold, "DEC-002", scope="a", ruling="r",
                 supersedes="PLAN-001/DEC-001", expires="PLAN-001/DEC-001")
        archive(self.scaffold, "2026-08-19")
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-19", plan="PLAN-002"
        )
        self.assertEqual(code, 1)
        self.assertIn("continuity-removal-target-conflicted", err)

    def test_a_self_reference_refuses(self):
        decision(self.scaffold, "DEC-002", scope="a", ruling="r", supersedes="DEC-002")
        archive(self.scaffold, "2026-08-14")
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-14", plan="PLAN-001"
        )
        self.assertEqual(code, 1)
        self.assertIn("continuity-reference-self", err)

    def test_an_unresolvable_reference_refuses(self):
        decision(self.scaffold, "DEC-002", scope="a", ruling="r",
                 supersedes="PLAN-009/DEC-001")
        archive(self.scaffold, "2026-08-14")
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-14", plan="PLAN-001"
        )
        self.assertEqual(code, 1)
        self.assertIn("continuity-reference-unresolved", err)

    def test_an_ambiguous_bare_reference_refuses_and_names_every_candidate(self):
        # A bare DEC-001 that matches both a prior plan's live row and this
        # plan's own candidate resolves to more than one row.
        self._first_plan()
        decision(self.scaffold, "DEC-001", scope="delivery", ruling="Approval first.")
        decision(self.scaffold, "DEC-002", scope="a", ruling="r", supersedes="DEC-001")
        archive(self.scaffold, "2026-08-19")
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-19", plan="PLAN-002"
        )
        self.assertEqual(code, 1)
        self.assertIn("continuity-reference-ambiguous", err)
        self.assertIn("PLAN-001/DEC-001", err)
        self.assertIn("PLAN-002/DEC-001", err)

    def test_a_bare_reference_cannot_reach_a_prior_plans_row(self):
        self._first_plan()
        decision(self.scaffold, "DEC-002", scope="a", ruling="r", supersedes="DEC-001")
        archive(self.scaffold, "2026-08-19")
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-19", plan="PLAN-002"
        )
        self.assertEqual(code, 1)
        self.assertIn("continuity-reference-unresolved", err)
        self.assertIn("PLAN-001/DEC-001", err)

    def test_a_hand_edited_field_is_revalidated_at_the_write(self):
        self.scaffold.write(
            "decisions/DEC-001.md",
            "# DEC-001: t\n\nDate: 2026-08-14\nStatus: locked\n"
            "Scope: delivery\nRuling: a | b\n\n## Context\n\nx\n",
        )
        archive(self.scaffold, "2026-08-14")
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-14", plan="PLAN-001"
        )
        self.assertEqual(code, 1)
        self.assertIn("continuity-field-unsafe", err)
        self.assertIn("Ruling", err)


class TestDisabledPath(_Fixture):
    """A `none` or `archive` closeout does zero continuity work, unconditionally."""

    def _archival_records(self):
        scaffold = continuity_scaffold()
        self.addCleanup(scaffold.cleanup)
        decision(scaffold, "DEC-001", scope="delivery", ruling="Approval first.")
        return scaffold

    def test_archive_and_reset_are_byte_identical_across_every_artifact_state(self):
        states = {
            "absent": None,
            "valid": "valid",
            "symlink": "symlink",
            "non-utf8": b"\xff\xfe",
            "format-unrecognized": "# Continuity\n\nFormat: continuity-v9\n",
        }
        baseline = None
        for label, state in states.items():
            with self.subTest(state=label):
                scaffold = self._archival_records()
                root = scaffold.project_root
                artifact = root / "CONTINUITY.md"
                if state == "valid":
                    from tests.cli.test_continuity import SPECIMEN

                    artifact.write_text(SPECIMEN, encoding="utf-8")
                elif state == "symlink":
                    target = root / "elsewhere.md"
                    target.write_text("x", encoding="utf-8")
                    os.symlink(target, artifact)
                elif isinstance(state, bytes):
                    artifact.write_bytes(state)
                elif isinstance(state, str):
                    artifact.write_text(state, encoding="utf-8")
                before = artifact.read_bytes() if artifact.is_file() else None

                code, records, err = run_cli(
                    "archive-plan", str(root), "--closed", "2026-08-14",
                    "--summary", "s", "--content", "# c\n",
                )
                self.assertEqual(code, 0, err)
                archived = records[0]["details"]
                code, records, err = run_cli("reset-plan", str(root))
                self.assertEqual(code, 0, err)
                reset = records[0]["details"]

                shape = (
                    archived["archive_name"], archived["copied"],
                    sorted(Path(p).name for p in reset["removed"]),
                    reset["recreated_dirs"],
                )
                if baseline is None:
                    baseline = shape
                self.assertEqual(shape, baseline)
                # The artifact is untouched, whatever state it was in, and is
                # never snapshotted into the archive.
                if before is not None:
                    self.assertEqual(artifact.read_bytes(), before)
                self.assertFalse(
                    (root / "archive/PLAN-001/CONTINUITY.md").exists()
                )

    def test_a_disabled_closeout_never_revokes_a_prior_enabled_artifact(self):
        decision(self.scaffold, "DEC-001", scope="delivery", ruling="Approval first.")
        archive(self.scaffold, "2026-08-14")
        write_continuity(self.scaffold, "index", "2026-08-14", plan="PLAN-001")
        before = self.artifact.read_bytes()
        model_before = self.model()
        run_cli("reset-plan", str(self.root))

        self.scaffold.write("IMPLEMENTATION_PLAN.md", "# Plan\n")
        decision(self.scaffold, "DEC-009", scope="review", ruling="Two reviewers.")
        archive(self.scaffold, "2026-08-19")     # outcome `archive`: no continuity write
        run_cli("reset-plan", str(self.root))
        self.assertEqual(self.artifact.read_bytes(), before)
        model_after = self.model()
        self.assertEqual(model_after.live, model_before.live)
        self.assertEqual(model_after.retrieval, model_before.retrieval)
        self.assertEqual(model_after.highest_plan, 1)

    def test_reset_plan_never_gains_the_artifact_and_archive_plan_never_copies_it(self):
        from cli.commands.archive_plan import ARCHIVE_ROOT_FILES
        from cli.commands.reset_plan import RESET_CLEAR_DIRS, RESET_ROOT_FILES

        self.assertNotIn("CONTINUITY.md", ARCHIVE_ROOT_FILES)
        self.assertNotIn("CONTINUITY.md", RESET_ROOT_FILES)
        self.assertNotIn("archive", RESET_CLEAR_DIRS)


class TestLedgerAllocation(_Fixture):
    """The § 4.3 table is total: every state binds an id or refuses by name."""

    def setUp(self):
        super().setUp()
        decision(self.scaffold, "DEC-001", scope="delivery", ruling="Approval first.")

    def test_no_archive_and_nothing_recorded_allocates_the_first_plan(self):
        code, records, err = write_continuity(self.scaffold, "ledger", "2026-08-14")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.details(records)["plan"], "PLAN-001")

    def test_a_silent_witness_over_a_real_archive_refuses_and_names_both_remedies(self):
        archive(self.scaffold, "2026-08-14")
        code, _, err = write_continuity(self.scaffold, "ledger", "2026-08-19")
        self.assertEqual(code, 1)
        self.assertIn("continuity-ledger-plan-ambiguous", err)
        self.assertIn("--plan PLAN-001", err)
        self.assertIn("--plan PLAN-002", err)
        self.assertFalse(self.artifact.exists())

    def test_naming_a_real_archive_in_ledger_mode_refuses(self):
        archive(self.scaffold, "2026-08-14")
        code, _, err = write_continuity(
            self.scaffold, "ledger", "2026-08-19", plan="PLAN-001"
        )
        self.assertEqual(code, 1)
        self.assertIn("continuity-mode-archive-present", err)

    def test_a_mismatched_plan_refuses_and_names_both_values(self):
        code, _, err = write_continuity(
            self.scaffold, "ledger", "2026-08-14", plan="PLAN-007"
        )
        self.assertEqual(code, 1)
        self.assertIn("continuity-plan-id-mismatch", err)
        self.assertIn("PLAN-007", err)

    def test_a_compute_pass_that_contradicts_the_witness_refuses(self):
        seed_evidence(self.scaffold, "PLAN-002")
        code, _, err = write_continuity(
            self.scaffold, "ledger", "2026-08-14", plan="PLAN-001"
        )
        self.assertEqual(code, 1)
        self.assertIn("continuity-ledger-window-foreign", err)
        self.assertFalse((self.root / "archive" / "PLAN-001").exists())

    def test_a_reservation_the_witness_cannot_place_is_ambiguous(self):
        seed_evidence(self.scaffold, "PLAN-001")
        write_continuity(self.scaffold, "ledger", "2026-08-14")
        prompt_evidence.log_path(self.root).parent.mkdir(parents=True, exist_ok=True)
        code, _, err = write_continuity(self.scaffold, "ledger", "2026-08-19")
        self.assertEqual(code, 1)
        self.assertIn("continuity-ledger-plan-ambiguous", err)

    def test_a_recorded_plans_removed_directory_never_re_mints_its_id(self):
        seed_evidence(self.scaffold, "PLAN-001")
        write_continuity(self.scaffold, "ledger", "2026-08-14")
        import shutil

        shutil.rmtree(self.root / "archive" / "PLAN-001")
        self.assertEqual(cy.highest_archive_number(self.root), 0)
        self.assertEqual(self.model().highest_plan, 1)
        seed_evidence(self.scaffold, "PLAN-002")
        code, records, err = write_continuity(self.scaffold, "ledger", "2026-08-19")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.details(records)["plan"], "PLAN-002")

    def test_a_stale_reservation_is_reported_and_never_adopted(self):
        seed_evidence(self.scaffold, "PLAN-001")
        write_continuity(self.scaffold, "ledger", "2026-08-14")
        self.artifact.unlink()                       # the attempt is abandoned
        import shutil

        shutil.rmtree(self.root / "archive" / "PLAN-001" )
        (self.root / "archive" / "PLAN-001").mkdir()
        (self.root / "archive/PLAN-001" / cy.NOT_ARCHIVED_BASENAME).write_text(
            cy.not_archived_body("PLAN-001"), encoding="utf-8"
        )
        seed_evidence(self.scaffold, "PLAN-002")
        code, records, err = write_continuity(self.scaffold, "ledger", "2026-08-19")
        self.assertEqual(code, 0, err)
        detail = self.details(records)
        self.assertEqual(detail["plan"], "PLAN-002")
        self.assertEqual(detail["stale_reservation"], "PLAN-001")
        self.assertTrue((self.root / "archive" / "PLAN-001").is_dir())

    def test_a_directory_that_is_neither_reservation_nor_archive_refuses(self):
        (self.root / "archive" / "PLAN-001").mkdir(parents=True)
        (self.root / "archive/PLAN-001/STRAY.md").write_text("x", encoding="utf-8")
        code, _, err = write_continuity(
            self.scaffold, "ledger", "2026-08-14", plan="PLAN-002"
        )
        self.assertEqual(code, 1)
        self.assertIn("continuity-reservation-malformed", err)

    def test_an_allocating_path_refuses_at_the_terminal_counter(self):
        from tests.cli.test_continuity import SPECIMEN

        # A recorded PLAN-999 whose directory was removed out of band: the
        # allocator takes max(M, H) + 1, which is past the terminal id.
        self.artifact.write_text(
            SPECIMEN.replace("PLAN-003", "PLAN-999"), encoding="utf-8"
        )
        before = self.artifact.read_bytes()
        code, _, err = write_continuity(self.scaffold, "ledger", "2026-08-14")
        self.assertEqual(code, 1)
        self.assertIn("continuity-plan-counter-exhausted", err)
        self.assertIn("new project", err)
        self.assertFalse((self.root / "archive").exists())
        self.assertEqual(self.artifact.read_bytes(), before)

    def test_a_reservation_at_the_terminal_id_names_only_its_reachable_remedy(self):
        (self.root / "archive" / "PLAN-999").mkdir(parents=True)
        (self.root / "archive/PLAN-999" / cy.NOT_ARCHIVED_BASENAME).write_text(
            cy.not_archived_body("PLAN-999"), encoding="utf-8"
        )
        cy.ensure_reservation_row(self.root, "PLAN-999", "2026-08-14")
        code, _, err = write_continuity(self.scaffold, "ledger", "2026-08-14")
        self.assertEqual(code, 1)
        self.assertIn("continuity-ledger-reservation-ambiguous", err)
        self.assertIn("--plan PLAN-999", err)
        self.assertNotIn("PLAN-1000", err)


class TestRetryAndRecovery(_Fixture):
    def setUp(self):
        super().setUp()
        decision(self.scaffold, "DEC-014", scope="intake", ruling="Intake names a requester.")
        seed_evidence(self.scaffold, "PLAN-001")

    def test_a_refused_compute_pass_marks_its_own_reservation(self):
        # Refuse at step 6 by naming an unresolvable reference; the reservation
        # already exists, and every step from 5 to 8 is not-published by
        # construction.
        decision(self.scaffold, "DEC-002", scope="a", ruling="r",
                 supersedes="PLAN-009/DEC-001")
        log_before = prompt_evidence.log_path(self.root).read_bytes()
        code, _, err = write_continuity(self.scaffold, "ledger", "2026-08-14")
        self.assertEqual(code, 1)
        self.assertIn("continuity-reference-unresolved", err)
        reservation = self.root / "archive" / "PLAN-001"
        self.assertEqual(
            sorted(os.listdir(reservation)),
            [cy.LEDGER_FAILED_BASENAME, cy.NOT_ARCHIVED_BASENAME],
        )
        self.assertEqual(
            len((reservation / cy.LEDGER_FAILED_BASENAME).read_bytes()), 334
        )
        self.assertFalse(self.artifact.exists())
        # The evidence close never ran, so the log is intact for the retry.
        self.assertEqual(prompt_evidence.log_path(self.root).read_bytes(), log_before)

    def test_a_retry_adopts_the_same_reservation_and_clears_its_marker(self):
        decision(self.scaffold, "DEC-002", scope="a", ruling="r",
                 supersedes="PLAN-009/DEC-001")
        write_continuity(self.scaffold, "ledger", "2026-08-14")
        (self.root / "decisions" / "DEC-002.md").unlink()
        code, records, err = write_continuity(self.scaffold, "ledger", "2026-08-14")
        self.assertEqual(code, 0, err)
        detail = self.details(records)
        self.assertEqual(detail["plan"], "PLAN-001")
        self.assertEqual(detail["plan_source"], "reservation-adopted")
        self.assertEqual(detail["reservation_repaired"], ["LEDGER-FAILED.md cleared"])
        self.assertEqual(cy.highest_archive_number(self.root), 1)
        self.assertEqual(
            sorted(os.listdir(self.root / "archive" / "PLAN-001")),
            [cy.NOT_ARCHIVED_BASENAME],
        )

    def test_an_adoption_completes_every_creation_prefix(self):
        prefixes = {
            "empty": (False, False),
            "incomplete": (True, False),
            "unmarked": (True, True),
        }
        expected = {
            "empty": [cy.NOT_ARCHIVED_BASENAME, "archive/INDEX.md row"],
            "incomplete": ["archive/INDEX.md row"],
            "unmarked": [],
        }
        for label, (sentinel, row) in prefixes.items():
            with self.subTest(prefix=label):
                self.setUp()
                reservation = self.root / "archive" / "PLAN-001"
                reservation.mkdir(parents=True)
                if sentinel:
                    (reservation / cy.NOT_ARCHIVED_BASENAME).write_text(
                        cy.not_archived_body("PLAN-001"), encoding="utf-8"
                    )
                if row:
                    cy.ensure_reservation_row(self.root, "PLAN-001", "2026-08-14")
                self.assertEqual(prompt_evidence.current_plan_id(self.root), "PLAN-002")
                code, records, err = write_continuity(
                    self.scaffold, "ledger", "2026-08-14"
                )
                self.assertEqual(code, 0, err)
                detail = self.details(records)
                self.assertEqual(detail["plan"], "PLAN-001")
                self.assertEqual(detail["reservation_repaired"], expected[label])
                self.assertEqual(
                    cy.classify_reservation(self.root, "PLAN-001").shape,
                    cy.SHAPE_UNMARKED,
                )

    def test_a_crash_between_the_write_and_the_close_is_completed_by_either_retry(self):
        for supply_plan in (True, False):
            with self.subTest(plan="supplied" if supply_plan else "omitted"):
                self.setUp()
                code, records, err = write_continuity(self.scaffold, "ledger", "2026-08-14")
                self.assertEqual(code, 0, err)
                # Simulate the crash: the artifact landed, the close did not.
                seed_evidence(self.scaffold, "PLAN-001")
                before = self.artifact.read_bytes()
                code, records, err = write_continuity(
                    self.scaffold, "ledger", "2026-08-14",
                    plan="PLAN-001" if supply_plan else None,
                )
                self.assertEqual(code, 0, err)
                detail = self.details(records)
                self.assertEqual(detail["plan_source"], "recorded")
                self.assertTrue(detail["already_written"])
                self.assertEqual(self.artifact.read_bytes(), before)
                closeout = detail["effectiveness_closeout"]
                self.assertEqual(closeout["plan"], "PLAN-001")
                self.assertEqual(closeout.get("ledger_errors"), [])
                self.assertTrue(closeout["log_deleted"])
                # A third run closes nothing twice.
                code, records, err = write_continuity(
                    self.scaffold, "ledger", "2026-08-14", plan="PLAN-001"
                )
                self.assertEqual(code, 0, err)
                self.assertTrue(
                    self.details(records)["effectiveness_closeout"]["already_closed"]
                )

    def test_a_replace_defers_a_close_that_would_name_a_foreign_window(self):
        code, _, err = write_continuity(self.scaffold, "ledger", "2026-08-14")
        self.assertEqual(code, 0, err)
        # A later window is now in flight.
        seed_evidence(self.scaffold, "PLAN-004", unit="TASK-04-001")
        log_before = prompt_evidence.log_path(self.root).read_bytes()
        code, records, err = write_continuity(
            self.scaffold, "ledger", "2026-08-14", plan="PLAN-001"
        )
        self.assertEqual(code, 0, err)
        closeout = self.details(records)["effectiveness_closeout"]
        self.assertEqual(closeout["deferred"], "foreign-window")
        self.assertEqual(closeout["witness"], "PLAN-004")
        self.assertEqual(prompt_evidence.log_path(self.root).read_bytes(), log_before)


class TestBoundsAndCeiling(_Fixture):
    def test_an_overlong_ledger_section_refuses(self):
        decision(self.scaffold, "DEC-001", scope="delivery", ruling="Approval first.")
        archive(self.scaffold, "2026-08-14")
        body = "\n".join(f"- Outcome: {'x' * 100}" for _ in range(12))
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-14", plan="PLAN-001", content=body
        )
        self.assertEqual(code, 1)
        self.assertIn("continuity-ledger-section-overlong", err)
        self.assertFalse(self.artifact.exists())

    def test_a_write_crossing_the_ceiling_names_the_prune(self):
        decision(self.scaffold, "DEC-001", scope="delivery", ruling="Approval first.")
        archive(self.scaffold, "2026-08-14")
        write_continuity(self.scaffold, "index", "2026-08-14", plan="PLAN-001")
        # Grow the artifact to just under the ceiling with cold rows whose
        # bodies exist, so a prune is still possible.
        model = self.model()
        padding = "y" * 90
        n = 1
        while True:
            n += 1
            model.cold.append(cy.ColdRow(
                f"PLAN-001/DEC-{n:03d}", "delivery", padding, "superseded",
                "PLAN-001/DEC-001", "archive/PLAN-001/decisions/DEC-001.md",
            ))
            if len(cy.serialize_continuity(model).encode()) > cy.CONTINUITY_MAX_BYTES - 400:
                break
        self.artifact.write_text(cy.serialize_continuity(model), encoding="utf-8")
        self.assertLess(self.artifact.stat().st_size, cy.CONTINUITY_MAX_BYTES)

        decision(self.scaffold, "DEC-002", scope="intake", ruling="Intake names a date.")
        archive(self.scaffold, "2026-08-19")
        long_body = "\n".join(f"- Outcome: {'z' * 90}" for _ in range(8))
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-19", plan="PLAN-002", content=long_body
        )
        self.assertEqual(code, 1)
        self.assertIn("continuity-ceiling-exceeded", err)
        self.assertIn("prune-continuity", err)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
