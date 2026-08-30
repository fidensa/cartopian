"""End-to-end coverage for abandonment, allocation totality, and the four
closeout sequences run as sequences.

The scenarios here are the ones no single command owns: a failed `ledger`
attempt that is given up, the disabled closeout that follows it, and the later
plan that must not adopt what it left behind.
"""
from __future__ import annotations

import os
import unittest

from cli import continuity as cy, prompt_evidence
from tests.cli.test_continuity import SPECIMEN
from tests.continuity_support import (
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

    def refuse_a_ledger_pass(self, plan="PLAN-001"):
        """Leave a **marked** reservation: a pass proven to have recorded nothing."""
        decision(self.scaffold, "DEC-014", scope="intake",
                 ruling="Intake names a requester.")
        decision(self.scaffold, "DEC-002", scope="a", ruling="r",
                 supersedes="PLAN-009/DEC-001")
        code, _, err = write_continuity(self.scaffold, "ledger", "2026-08-14")
        self.assertEqual(code, 1)
        self.assertEqual(cy.classify_reservation(self.root, plan).shape, cy.SHAPE_MARKED)
        (self.root / "decisions" / "DEC-002.md").unlink()

    def mediated_deletes(self, fragment):
        from cli import provenance

        journal = self.root / provenance.LOG_RELPATH
        if not journal.is_file():
            return 0
        return sum(
            1
            for line in journal.read_text(encoding="utf-8").splitlines()
            if '"action":"mediated-delete"' in line.replace(" ", "") and fragment in line
        )


class TestAbandonmentWithTheRelease(_Fixture):
    def test_the_closing_plan_keeps_its_id_and_its_evidence_under_none(self):
        seed_evidence(self.scaffold, "PLAN-001")
        self.refuse_a_ledger_pass()
        self.artifact.write_text(SPECIMEN, encoding="utf-8")
        before = self.artifact.read_bytes()

        code, records, err = release(self.scaffold, "PLAN-001", closed="2026-08-14")
        self.assertEqual(code, 0, err)
        closeout = self.details(records)["effectiveness_closeout"]
        self.assertEqual(closeout["plan"], "PLAN-001")
        self.assertEqual(closeout["ledger_errors"], [])
        self.assertEqual(closeout["closing_projection"]["matched"], 1)
        self.assertTrue(closeout["log_deleted"])
        self.assertEqual(self.artifact.read_bytes(), before)

        self.assertEqual(prompt_evidence.current_plan_id(self.root), "PLAN-001")
        code, records, err = run_cli("reset-plan", str(self.root))
        self.assertEqual(code, 0, err)
        reset = self.details(records)["effectiveness_closeout"]
        self.assertEqual(reset["plan"], "PLAN-001")
        self.assertTrue(reset["already_closed"])
        self.assertEqual(self.artifact.read_bytes(), before)
        self.assertEqual(self.mediated_deletes("archive/PLAN-001"), 3)

    def test_the_closing_plan_keeps_its_id_and_its_evidence_under_archive(self):
        seed_evidence(self.scaffold, "PLAN-001")
        self.refuse_a_ledger_pass()
        code, _, err = release(self.scaffold, "PLAN-001", closed="2026-08-14")
        self.assertEqual(code, 0, err)
        self.assertEqual(archive(self.scaffold, "2026-08-14"), "PLAN-001")
        self.assertTrue((self.root / "archive/PLAN-001/CLOSEOUT.md").is_file())
        index = (self.root / "archive/INDEX.md").read_text(encoding="utf-8")
        self.assertNotIn(cy.RESERVATION_SUMMARY, index)
        self.assertFalse(self.artifact.exists())

    def test_a_later_ledger_closeout_then_has_nothing_stale_to_report(self):
        seed_evidence(self.scaffold, "PLAN-001")
        self.refuse_a_ledger_pass()
        release(self.scaffold, "PLAN-001", closed="2026-08-14")
        run_cli("reset-plan", str(self.root))

        self.scaffold.write("IMPLEMENTATION_PLAN.md", "# Plan\n")
        decision(self.scaffold, "DEC-001", scope="delivery", ruling="Approval first.")
        seed_evidence(self.scaffold, "PLAN-001")
        code, records, err = write_continuity(self.scaffold, "ledger", "2026-08-19")
        self.assertEqual(code, 0, err)
        detail = self.details(records)
        self.assertEqual(detail["plan"], "PLAN-001")
        self.assertEqual(detail["plan_source"], "reservation-allocated")
        self.assertIsNone(detail["stale_reservation"])


class TestAbandonmentWithTheReleaseSkipped(_Fixture):
    def test_the_orphan_costs_the_closing_plan_its_id_and_its_evidence(self):
        seed_evidence(self.scaffold, "PLAN-001")
        self.refuse_a_ledger_pass()
        # The operator archives anyway: the closing plan loses its id and its
        # evidence, exactly as measured — asserted *not* to be the shipped path.
        code, records, err = run_cli(
            "archive-plan", str(self.root), "--closed", "2026-08-14",
            "--summary", "s", "--content", "# c\n",
        )
        self.assertEqual(code, 0, err)
        detail = records[0]["details"]
        self.assertEqual(detail["archive_name"], "PLAN-002")
        self.assertEqual(
            [entry["rule"] for entry in detail["effectiveness_closeout"]["ledger_errors"]],
            ["foreign-plan-window"],
        )
        run_cli("reset-plan", str(self.root))

        # The next plan closes `ledger`. The witness names the window now
        # closing, so the orphan is reported rather than adopted.
        self.scaffold.write("IMPLEMENTATION_PLAN.md", "# Plan\n")
        decision(self.scaffold, "DEC-001", scope="delivery", ruling="Approval first.")
        seed_evidence(self.scaffold, "PLAN-003")
        code, records, err = write_continuity(self.scaffold, "ledger", "2026-08-19")
        self.assertEqual(code, 0, err)
        detail = self.details(records)
        self.assertEqual(detail["plan"], "PLAN-003")
        self.assertEqual(detail["plan_source"], "reservation-allocated")
        # The orphan is left exactly where it is: removing it would reopen the
        # allocation collision at the moment the writer knows least.
        self.assertTrue((self.root / "archive" / "PLAN-001").is_dir())
        self.assertEqual(cy.read_artifact(self.root).highest, 3)

    def test_asserting_the_orphans_id_refuses_the_foreign_window(self):
        seed_evidence(self.scaffold, "PLAN-001")
        self.refuse_a_ledger_pass()
        run_cli("reset-plan", str(self.root))
        self.scaffold.write("IMPLEMENTATION_PLAN.md", "# Plan\n")
        decision(self.scaffold, "DEC-001", scope="delivery", ruling="Approval first.")
        seed_evidence(self.scaffold, "PLAN-002")
        code, _, err = write_continuity(
            self.scaffold, "ledger", "2026-08-19", plan="PLAN-001"
        )
        self.assertEqual(code, 1)
        self.assertIn("continuity-ledger-window-foreign", err)

    def test_a_silent_witness_over_a_reservation_refuses_and_names_both_remedies(self):
        seed_evidence(self.scaffold, "PLAN-001")
        self.refuse_a_ledger_pass()
        prompt_evidence.log_path(self.root).unlink()
        code, _, err = write_continuity(self.scaffold, "ledger", "2026-08-19")
        self.assertEqual(code, 1)
        self.assertIn("continuity-ledger-reservation-ambiguous", err)
        self.assertIn("--plan PLAN-001", err)
        self.assertIn("--plan PLAN-002", err)


class TestUnmarkedResolvesOnlyThroughTheEnabledPath(_Fixture):
    """Three endings leave a complete, unmarked reservation; all three refuse."""

    def _unmarked(self, *, landed):
        decision(self.scaffold, "DEC-014", scope="intake",
                 ruling="Intake names a requester.")
        seed_evidence(self.scaffold, "PLAN-001")
        if landed:
            code, _, err = write_continuity(self.scaffold, "ledger", "2026-08-14")
            self.assertEqual(code, 0, err)
            seed_evidence(self.scaffold, "PLAN-001")   # the close did not run
        else:
            reservation = self.root / "archive" / "PLAN-001"
            reservation.mkdir(parents=True)
            (reservation / cy.NOT_ARCHIVED_BASENAME).write_text(
                cy.not_archived_body("PLAN-001"), encoding="utf-8"
            )
            cy.ensure_reservation_row(self.root, "PLAN-001", "2026-08-14")
        self.assertEqual(
            cy.classify_reservation(self.root, "PLAN-001").shape, cy.SHAPE_UNMARKED
        )

    def test_the_release_refuses_both_copies(self):
        for landed in (True, False):
            with self.subTest(landed=landed):
                self.setUp()
                self._unmarked(landed=landed)
                code, _, err = release(self.scaffold, "PLAN-001", closed="2026-08-14")
                self.assertEqual(code, 1)
                self.assertIn("continuity-reservation-unresolved", err)

    def test_the_retry_completes_a_record_that_landed(self):
        self._unmarked(landed=True)
        before = self.artifact.read_bytes()
        code, records, err = write_continuity(
            self.scaffold, "ledger", "2026-08-14", plan="PLAN-001"
        )
        self.assertEqual(code, 0, err)
        detail = self.details(records)
        self.assertEqual(detail["plan_source"], "recorded")
        self.assertTrue(detail["already_written"])
        self.assertTrue(detail["effectiveness_closeout"]["log_deleted"])
        self.assertEqual(self.artifact.read_bytes(), before)

    def test_the_retry_records_the_plan_where_nothing_landed(self):
        self._unmarked(landed=False)
        code, records, err = write_continuity(
            self.scaffold, "ledger", "2026-08-14", plan="PLAN-001"
        )
        self.assertEqual(code, 0, err)
        detail = self.details(records)
        self.assertEqual(detail["plan_source"], "reservation-adopted")
        self.assertEqual(cy.read_artifact(self.root).highest, 1)

    def test_forcing_that_retry_to_refuse_marks_it_and_unblocks_the_release(self):
        self._unmarked(landed=False)
        decision(self.scaffold, "DEC-002", scope="a", ruling="r",
                 supersedes="PLAN-009/DEC-001")
        code, _, err = write_continuity(
            self.scaffold, "ledger", "2026-08-14", plan="PLAN-001"
        )
        self.assertEqual(code, 1)
        self.assertEqual(
            cy.classify_reservation(self.root, "PLAN-001").shape, cy.SHAPE_MARKED
        )
        code, records, err = release(self.scaffold, "PLAN-001", closed="2026-08-14")
        self.assertEqual(code, 0, err)
        self.assertFalse((self.root / "archive" / "PLAN-001").exists())


class TestAllocationTotality(_Fixture):
    """Every combination of M, H, the shape at M, and the witness decides."""

    def setUp(self):
        super().setUp()
        decision(self.scaffold, "DEC-001", scope="delivery", ruling="Approval first.")

    def _reserve(self, plan):
        reservation = self.root / "archive" / plan
        reservation.mkdir(parents=True, exist_ok=True)
        (reservation / cy.NOT_ARCHIVED_BASENAME).write_text(
            cy.not_archived_body(plan), encoding="utf-8"
        )
        cy.ensure_reservation_row(self.root, plan, "2026-08-14")

    def test_a_reservation_with_the_reserving_window_open_is_adopted(self):
        self._reserve("PLAN-001")
        seed_evidence(self.scaffold, "PLAN-001")
        binding = cy.resolve_ledger_plan(self.root, None, None)
        self.assertEqual((binding.plan, binding.source), ("PLAN-001", cy.SOURCE_ADOPTED))

    def test_a_reservation_with_a_later_window_open_is_left_stale(self):
        self._reserve("PLAN-001")
        seed_evidence(self.scaffold, "PLAN-002")
        binding = cy.resolve_ledger_plan(self.root, None, None)
        self.assertEqual(binding.plan, "PLAN-002")
        self.assertEqual(binding.source, cy.SOURCE_ALLOCATED)
        self.assertEqual(binding.stale_reservation, "PLAN-001")

    def test_a_reservation_with_a_silent_witness_refuses(self):
        self._reserve("PLAN-001")
        with self.assertRaises(cy.ContinuityRefusal) as caught:
            cy.resolve_ledger_plan(self.root, None, None)
        self.assertEqual(
            caught.exception.rule, "continuity-ledger-reservation-ambiguous"
        )

    def test_a_real_archive_above_the_record_needs_the_witness_or_a_flag(self):
        archive(self.scaffold, "2026-08-14")
        with self.assertRaises(cy.ContinuityRefusal) as caught:
            cy.resolve_ledger_plan(self.root, None, None)
        self.assertEqual(caught.exception.rule, "continuity-ledger-plan-ambiguous")
        seed_evidence(self.scaffold, "PLAN-002")
        binding = cy.resolve_ledger_plan(self.root, None, None)
        self.assertEqual(binding.plan, "PLAN-002")

    def test_at_m_equals_h_the_witness_chooses_between_replace_and_allocate(self):
        seed_evidence(self.scaffold, "PLAN-001")
        code, _, err = write_continuity(self.scaffold, "ledger", "2026-08-14")
        self.assertEqual(code, 0, err)
        model = cy.read_artifact(self.root).model
        # The reserving window is still open: the retry completes the record.
        seed_evidence(self.scaffold, "PLAN-001")
        binding = cy.resolve_ledger_plan(self.root, model, None)
        self.assertEqual((binding.plan, binding.kind), ("PLAN-001", cy.PASS_REPLACE))
        # A later window is open: the next plan is allocated.
        prompt_evidence.log_path(self.root).unlink()
        seed_evidence(self.scaffold, "PLAN-002")
        binding = cy.resolve_ledger_plan(self.root, model, None)
        self.assertEqual((binding.plan, binding.kind), ("PLAN-002", cy.PASS_COMPUTE))
        # Silence is a named refusal, never a guess.
        prompt_evidence.log_path(self.root).unlink()
        with self.assertRaises(cy.ContinuityRefusal) as caught:
            cy.resolve_ledger_plan(self.root, model, None)
        self.assertEqual(caught.exception.rule, "continuity-ledger-plan-ambiguous")

    def test_a_ledger_replace_and_a_prune_still_work_at_the_terminal_id(self):
        # PLAN-999 recorded, its directory present: nothing further allocates,
        # but the two non-allocating operations remain available.
        self.artifact.write_text(SPECIMEN.replace("PLAN-003", "PLAN-999"), encoding="utf-8")
        (self.root / "archive" / "PLAN-999").mkdir(parents=True)
        (self.root / "archive/PLAN-999" / cy.NOT_ARCHIVED_BASENAME).write_text(
            cy.not_archived_body("PLAN-999"), encoding="utf-8"
        )
        cy.ensure_reservation_row(self.root, "PLAN-999", "2026-08-24")
        model = cy.read_artifact(self.root).model
        binding = cy.resolve_ledger_plan(self.root, model, "PLAN-999")
        self.assertEqual(binding.kind, cy.PASS_REPLACE)
        code, _, err = run_cli(
            "prune-continuity", str(self.root), "--pruned", "2026-09-02",
            "--plan", "PLAN-001",
        )
        self.assertEqual(code, 0, err)

    def test_an_allocating_path_refuses_at_the_terminal_counter(self):
        # A recorded PLAN-999 whose directory was removed out of band: the
        # allocator takes max(M, H) + 1, which is past the terminal id.
        self.artifact.write_text(
            SPECIMEN.replace("PLAN-003", "PLAN-999"), encoding="utf-8"
        )
        model = cy.read_artifact(self.root).model
        self.assertEqual(model.highest_plan, 999)
        with self.assertRaises(cy.ContinuityRefusal) as caught:
            cy.resolve_ledger_plan(self.root, model, None)
        self.assertEqual(caught.exception.rule, "continuity-plan-counter-exhausted")
        self.assertFalse((self.root / "archive").exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
