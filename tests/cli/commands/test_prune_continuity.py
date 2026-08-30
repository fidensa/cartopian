"""Coverage for `cartopian prune-continuity` — the one file-ceiling recovery.

Growth is bounded by a declared ceiling rather than by automatic deletion:
under `ledger` a closed plan's entry and its cold rows are the only surviving
record of that plan, so every preservation rule here refuses rather than
recovers.
"""
from __future__ import annotations

import unittest

from cli import continuity as cy
from tests.cli.test_continuity import SPECIMEN
from tests.continuity_support import continuity_scaffold, run_cli


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.scaffold = continuity_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root
        self.artifact = self.root / "CONTINUITY.md"
        self.artifact.write_text(SPECIMEN, encoding="utf-8")
        # The specimen's PLAN-001 rows name real archived bodies; make them so.
        for dec in ("DEC-001", "DEC-007"):
            path = self.root / "archive" / "PLAN-001" / "decisions" / f"{dec}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {dec}\n", encoding="utf-8")

    def prune(self, *args):
        return run_cli(
            "prune-continuity", str(self.root), "--pruned", "2026-09-02", *args
        )

    def details(self, records):
        self.assertTrue(records, "no record emitted")
        return records[0]["details"]

    def model(self):
        return cy.read_artifact(self.root).model


class TestPruneScopes(_Fixture):
    def test_a_plan_is_tombstoned_in_place_with_its_heading_preserved(self):
        before = self.artifact.stat().st_size
        code, records, err = self.prune("--plan", "PLAN-001")
        self.assertEqual(code, 0, err)
        detail = self.details(records)
        self.assertEqual(detail["plans_tombstoned"], ["PLAN-001"])
        self.assertEqual(detail["bytes_before"], before)
        self.assertLess(detail["bytes_after"], before)
        model = self.model()
        section = model.section("PLAN-001")
        self.assertEqual(section.closed, "2026-08-14")
        self.assertEqual(section.preservation, "archive+index")
        self.assertTrue(section.tombstoned)
        self.assertIn("2026-09-02", section.body_lines[0])
        # Plans recorded stays exact: a pruned plan is still distinguishable
        # from a plan that was never recorded.
        self.assertEqual(model.plans_recorded, 3)

    def test_an_archived_cold_row_is_removed_and_counted_as_pruned(self):
        code, records, err = self.prune("--cold", "PLAN-001/DEC-007")
        self.assertEqual(code, 0, err)
        detail = self.details(records)
        self.assertEqual(detail["cold_rows_removed"], ["PLAN-001/DEC-007"])
        model = self.model()
        self.assertEqual(model.pruned, 1)
        self.assertEqual(len(model.cold), 2)
        self.assertEqual(model.superseded, 1)
        # Live rows still carry locators, so the derived summary stays mixed:
        # `retrieval` is a property of the rows present, never of a mode.
        self.assertEqual(model.retrieval, "mixed")

    def test_both_scopes_compose_in_one_all_or_nothing_pass(self):
        code, records, err = self.prune(
            "--plan", "PLAN-001", "--cold", "PLAN-001/DEC-007"
        )
        self.assertEqual(code, 0, err)
        detail = self.details(records)
        self.assertEqual(detail["plans_tombstoned"], ["PLAN-001"])
        self.assertEqual(detail["cold_rows_removed"], ["PLAN-001/DEC-007"])
        self.assertEqual(detail["live_rows"], 3)


class TestPreservationRules(_Fixture):
    def _refuses(self, rule, *args):
        before = self.artifact.read_bytes()
        code, records, err = self.prune(*args)
        self.assertEqual(code, 1)
        self.assertEqual(records, [])
        self.assertIn(rule, err)
        self.assertEqual(self.artifact.read_bytes(), before)

    def test_a_cold_row_whose_body_was_never_archived_refuses(self):
        # PLAN-002 closed `ledger`, so its cold rows carry Body: none and are
        # the only surviving copy of their rulings.
        self._refuses("continuity-prune-unarchived-body", "--cold", "PLAN-002/DEC-003")

    def test_a_cold_row_whose_archive_has_since_gone_refuses(self):
        (self.root / "archive/PLAN-001/decisions/DEC-007.md").unlink()
        self._refuses("continuity-prune-unarchived-body", "--cold", "PLAN-001/DEC-007")

    def test_a_live_row_is_never_a_prune_target(self):
        self._refuses("continuity-prune-live-row", "--cold", "PLAN-001/DEC-001")

    def test_the_newest_plan_is_kept(self):
        self._refuses("continuity-prune-newest-plan", "--plan", "PLAN-003")

    def test_an_unrecorded_or_already_tombstoned_plan_refuses(self):
        self._refuses("continuity-prune-plan-unrecorded", "--plan", "PLAN-009")
        code, _, err = self.prune("--plan", "PLAN-001")
        self.assertEqual(code, 0, err)
        self._refuses("continuity-prune-plan-unrecorded", "--plan", "PLAN-001")

    def test_an_unknown_cold_identity_refuses(self):
        self._refuses("continuity-prune-plan-unrecorded", "--cold", "PLAN-009/DEC-001")

    def test_no_target_is_a_usage_refusal(self):
        code, _, err = self.prune()
        self.assertEqual(code, 2)
        self.assertIn("at least one --plan or --cold", err)


class TestPruneResidue(_Fixture):
    """What a fully pruned plan leaves, per outcome."""

    def test_the_bounded_residues_and_capacities_follow_the_shapes(self):
        four_byte = "\U00020000"
        scope, ruling = four_byte * 12, four_byte * 50
        section = cy.LedgerSection("PLAN-001", "2026-08-14", "archive+index", ["- x"])
        tomb_index = cy.tombstone_section(section, "2026-09-02").unit_bytes()
        section.preservation = "ledger"
        tomb_ledger = cy.tombstone_section(section, "2026-09-02").unit_bytes()

        def live(body):
            return len(f"| PLAN-999/DEC-999 | {scope} | {ruling} | {body} |\n".encode())

        def cold(body):
            return len(
                f"| PLAN-999/DEC-999 | {scope} | {ruling} | superseded | "
                f"PLAN-999/DEC-998 | {body} |\n".encode()
            )

        locator = cy.locator_for("PLAN-999/DEC-999")
        budget = cy.CONTINUITY_MAX_BYTES - 437

        # archive+index: the ledger section and the archived cold row prune.
        unpruned_index = cy.LEDGER_SECTION_MAX_BYTES + live(locator) + cold(locator)
        residue_index = live(locator) + tomb_index
        self.assertEqual((unpruned_index, residue_index), (1686, 489))
        self.assertEqual(budget // unpruned_index, 38)
        self.assertEqual(budget // residue_index, 133)
        self.assertEqual(
            round(100 * (unpruned_index - residue_index) / unpruned_index, 1), 71.0
        )

        # ledger: only the section prunes, because its cold row is the only
        # surviving copy of its ruling.
        unpruned_ledger = cy.LEDGER_SECTION_MAX_BYTES + live("none") + cold("none")
        residue_ledger = live("none") + cold("none") + tomb_ledger
        self.assertEqual((unpruned_ledger, residue_ledger), (1620, 763))
        self.assertEqual(budget // unpruned_ledger, 40)
        self.assertEqual(budget // residue_ledger, 85)
        self.assertEqual(
            round(100 * (unpruned_ledger - residue_ledger) / unpruned_ledger, 1), 52.9
        )
        # The limiting fully pruned capacity is the ledger one, not the larger.
        self.assertLess(budget // residue_ledger, budget // residue_index)

    def test_a_ceiling_with_nothing_prunable_left_names_the_structural_limit(self):
        model = self.model()
        # Every cold row unarchived, every section tombstoned but the newest:
        # nothing further is prunable.
        for row in model.cold:
            row.body = "none"
        model.sections = [
            cy.tombstone_section(section, "2026-09-02")
            if section.plan != "PLAN-003"
            else section
            for section in model.sections
        ]
        padding = "y" * 90
        n = 0
        while len(cy.serialize_continuity(model).encode()) <= cy.CONTINUITY_MAX_BYTES:
            n += 1
            model.live.append(cy.LiveRow(f"PLAN-003/DEC-{n:03d}", "s", padding, "none"))
        self.artifact.write_text(cy.serialize_continuity(model), encoding="utf-8")

        with self.assertRaises(cy.ContinuityRefusal) as caught:
            cy.enforce_ceiling(
                self.root, model, cy.serialize_continuity(model)
            )
        self.assertEqual(caught.exception.rule, "continuity-capacity-structural")
        self.assertIn("no further mediated recovery", caught.exception.detail)


class TestPruneReadContract(_Fixture):
    def test_a_defective_artifact_refuses_the_prune_too(self):
        self.artifact.write_bytes(b"# Continuity\n\n\xff\xfe\n")
        code, _, err = self.prune("--plan", "PLAN-001")
        self.assertEqual(code, 1)
        self.assertIn("continuity-encoding-invalid", err)

    def test_an_absent_artifact_refuses_rather_than_creating_one(self):
        self.artifact.unlink()
        code, _, err = self.prune("--plan", "PLAN-001")
        self.assertEqual(code, 1)
        self.assertIn("continuity-prune-plan-unrecorded", err)
        self.assertFalse(self.artifact.exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
