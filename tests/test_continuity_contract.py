"""Cross-surface parity for the optional continuity artifact.

A behavior that crosses the protocol document, the machine contract, the
templates, the closeout skill, and the CLI/MCP surfaces is only settled when
those surfaces agree. This module asserts the agreements and the four
"no change" guarantees the accepted design rests on.
"""
from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path

from cli import atomic_write, continuity as cy, prompt_evidence
from cli.commands import archive_plan, reset_plan
from cli.mediated_write import DEST_KINDS, ROOT_FILES
from tests.cli.test_continuity import SPECIMEN
from tests.continuity_support import (
    archive,
    continuity_scaffold,
    decision,
    run_cli,
    seed_evidence,
    write_continuity,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / "protocol" / "continuity-contract.json"
CONVENTIONS = (REPO_ROOT / "protocol" / "CONVENTIONS.md").read_text(encoding="utf-8")
CLOSE_PLAN = (REPO_ROOT / "skills" / "close-plan.md").read_text(encoding="utf-8")
DECISION_TEMPLATE = (REPO_ROOT / "templates" / "DECISION.md").read_text(encoding="utf-8")
CLOSEOUT_TEMPLATE = (
    REPO_ROOT / "templates" / "PLAN_CLOSEOUT.md"
).read_text(encoding="utf-8")


class TestMachineContract(unittest.TestCase):
    """`protocol/continuity-contract.json` is the machine authority."""

    def setUp(self):
        self.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    def test_the_bounds_are_the_ones_the_code_enforces(self):
        bounds = self.contract["bounds"]
        self.assertEqual(bounds["scope_characters"], cy.SCOPE_CHAR_MAX)
        self.assertEqual(bounds["scope_serialized_bytes"], cy.SCOPE_BYTE_MAX)
        self.assertEqual(bounds["ruling_characters"], cy.RULING_CHAR_MAX)
        self.assertEqual(bounds["ruling_serialized_bytes"], cy.RULING_BYTE_MAX)
        self.assertEqual(
            bounds["reference_header_serialized_bytes"], cy.REFERENCE_HEADER_BYTE_MAX
        )
        self.assertEqual(bounds["reference_list"], cy.REFERENCE_LIST_MAX)
        self.assertEqual(bounds["ledger_section_bytes"], cy.LEDGER_SECTION_MAX_BYTES)
        self.assertEqual(bounds["continuity_max_bytes"], cy.CONTINUITY_MAX_BYTES)
        self.assertEqual(
            bounds["continuity_projection_max_bytes"], cy.CONTINUITY_PROJECTION_MAX_BYTES
        )
        self.assertEqual(bounds["projection_live_rows"], cy.PROJECTION_LIVE_ROW_CAP)
        self.assertEqual(bounds["plan_counter_max"], cy.PLAN_COUNTER_MAX)

    def test_the_storage_and_format_declarations_match_the_writer(self):
        storage = self.contract["storage"]
        self.assertEqual(storage["dest_kind"], "continuity")
        self.assertTrue(storage["path"].endswith(cy.CONTINUITY_BASENAME))
        self.assertFalse(storage["archived"])
        self.assertTrue(storage["survives_reset"])
        self.assertEqual(self.contract["schema"]["format_header"], cy.FORMAT_VALUE)
        self.assertEqual(
            self.contract["reservation"]["index_row_summary"], cy.RESERVATION_SUMMARY
        )

    def test_the_five_reservation_shapes_are_the_ones_the_shape_test_names(self):
        declared = {shape["id"] for shape in self.contract["reservation"]["shapes"]}
        self.assertEqual(
            declared,
            {cy.SHAPE_MARKED, cy.SHAPE_MARKER_ONLY, cy.SHAPE_EMPTY,
             cy.SHAPE_INCOMPLETE, cy.SHAPE_UNMARKED},
        )
        releasable = {
            shape["id"] for shape in self.contract["reservation"]["shapes"]
            if shape["releasable"]
        }
        self.assertEqual(releasable, set(cy.RELEASABLE_SHAPES))

    def test_the_four_outcomes_and_their_command_orders_are_declared(self):
        outcomes = {
            entry["id"]: entry for entry in self.contract["activation"]["outcomes"]
        }
        self.assertEqual(set(outcomes), {"none", "archive", "archive+index", "ledger"})
        self.assertEqual(self.contract["activation"]["default"], "none")
        self.assertFalse(self.contract["activation"]["sticky"])
        self.assertIsNone(self.contract["activation"]["configuration_key"])
        self.assertEqual(outcomes["none"]["commands"], ["reset-plan"])
        self.assertEqual(outcomes["archive"]["commands"], ["archive-plan", "reset-plan"])
        for outcome in ("none", "archive"):
            self.assertFalse(outcomes[outcome]["continuity_artifact"])
            self.assertEqual(outcomes[outcome]["path"], "disabled")
        for outcome in ("archive+index", "ledger"):
            self.assertTrue(outcomes[outcome]["continuity_artifact"])
            self.assertEqual(outcomes[outcome]["path"], "enabled")

    def test_every_refusal_code_the_commands_can_raise_is_declared(self):
        declared = {entry["code"] for entry in self.contract["refusal_codes"]}
        sources = [
            (REPO_ROOT / "cli" / "continuity.py").read_text(encoding="utf-8"),
            (REPO_ROOT / "cli" / "commands" / "write_continuity.py").read_text(encoding="utf-8"),
            (REPO_ROOT / "cli" / "commands" / "release_reservation.py").read_text(encoding="utf-8"),
            (REPO_ROOT / "cli" / "commands" / "prune_continuity.py").read_text(encoding="utf-8"),
        ]
        raised = set()
        for source in sources:
            raised |= set(re.findall(r'"(continuity-[a-z-]+)"', source))
        raised.discard("continuity-v1")
        self.assertTrue(raised)
        self.assertEqual(
            raised - declared, set(),
            "a refusal the code can raise is not declared in the machine contract",
        )

    def test_the_read_contract_states_every_artifact_condition(self):
        states = {row["state"] for row in self.contract["read_contract"]["states"]}
        self.assertEqual(len(states), 6)
        never = set(self.contract["read_contract"]["never_read"])
        for command in ("archive-plan", "reset-plan", "release-reservation", "write-decision"):
            self.assertIn(command, never)


class TestProtocolProjection(unittest.TestCase):
    """`protocol/CONVENTIONS.md` projects the JSON authority."""

    def test_the_plan_continuity_section_exists_and_is_addressable(self):
        self.assertIn("\n## Plan Continuity\n", CONVENTIONS)
        # H2 titles slugify into the section-scoped protocol resource surface.
        from mcp_server.server import _section_slug, _split_h2_sections

        sections = _split_h2_sections(CONVENTIONS)
        self.assertIn(_section_slug("Plan Continuity"), sections)

    def test_the_projection_carries_the_declared_bounds_and_outcomes(self):
        for fragment in (
            "65,536 bytes", "1,024 bytes", "24 characters / 48 serialized bytes",
            "100 characters / 200 serialized bytes", "1,536 serialized",
            "`archive+index`", "`ledger`", "continuity-v1",
            "PLAN-NNN/DEC-NNN", "NOT-ARCHIVED.md", "not archived - ledger preservation outcome",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, CONVENTIONS)

    def test_the_preservation_question_and_its_default_are_stated(self):
        self.assertIn("How should this completed plan be preserved", CONVENTIONS)
        self.assertIn("The default is (1) at every closeout", CONVENTIONS)
        self.assertIn("no past answer is sticky", CONVENTIONS)

    def test_the_disabled_path_claim_is_scoped_where_it_is_made(self):
        self.assertIn(
            "zero continuity reads and zero continuity writes, unconditionally",
            CONVENTIONS,
        )
        self.assertIn(
            "Startup and status cost zero continuity bytes only where no root "
            "`CONTINUITY.md` exists",
            CONVENTIONS,
        )

    def test_the_naming_section_names_both_new_paths(self):
        naming = CONVENTIONS.split("## Naming", 1)[1].split("\n## ", 1)[0]
        self.assertIn("`CONTINUITY.md` at the project root", naming)
        self.assertIn("Plan-id reservation", naming)


class TestTemplates(unittest.TestCase):
    def test_the_decision_template_documents_the_optional_headers(self):
        for fragment in ("Scope:", "Ruling:", "Expires:", "PLAN-NNN/DEC-NNN"):
            self.assertIn(fragment, DECISION_TEMPLATE)
        # Its seeded body must itself pass the fail-closed validation, or every
        # decision authored from it would refuse.
        scaffold = continuity_scaffold()
        self.addCleanup(scaffold.cleanup)
        self.assertIsNone(
            cy.validate_decision_headers(
                DECISION_TEMPLATE, scaffold.project_root / "decisions"
            )
        )

    def test_the_closeout_template_records_the_preservation_outcome(self):
        self.assertIn("## Preservation outcome", CLOSEOUT_TEMPLATE)
        for outcome in ("none", "archive", "archive+index", "ledger"):
            self.assertIn(outcome, CLOSEOUT_TEMPLATE)
        self.assertIn("rulings survive and rationale does not", CLOSEOUT_TEMPLATE)


class TestCloseoutSkill(unittest.TestCase):
    def test_the_archive_question_is_replaced_by_the_preservation_question(self):
        self.assertNotIn(
            "Do you want to archive the completed plan before reset?", CLOSE_PLAN
        )
        self.assertIn("How should this completed plan be preserved", CLOSE_PLAN)
        self.assertIn("**(1), no preservation**, at every closeout", CLOSE_PLAN)

    def test_stage_3b_runs_the_continuity_write_for_the_enabled_outcomes(self):
        self.assertIn("## Stage 3b - Optional Continuity Write", CLOSE_PLAN)
        self.assertIn("cartopian write-continuity", CLOSE_PLAN)
        self.assertIn("--mode index --plan <archive_name>", CLOSE_PLAN)
        self.assertIn("--mode ledger --plan PLAN-NNN", CLOSE_PLAN)
        self.assertIn("`write-continuity` runs **before** `reset-plan`", CLOSE_PLAN)

    def test_the_release_precedes_a_closeout_that_is_not_outcome_four(self):
        self.assertIn("### 3.0 Release an abandoned `ledger` reservation first", CLOSE_PLAN)
        self.assertIn("cartopian release-reservation", CLOSE_PLAN)
        self.assertIn("continuity-reservation-unresolved", CLOSE_PLAN)
        self.assertIn("write-continuity --mode ledger --plan PLAN-NNN", CLOSE_PLAN)

    def test_the_ledger_trade_and_the_surviving_artifact_disclosure_are_stated(self):
        self.assertIn("rulings survive and rationale does not", CLOSE_PLAN)
        self.assertIn("persists and still reaches every session", CLOSE_PLAN)

    def test_stage_four_qualifies_who_closes_the_evidence_window(self):
        self.assertIn(
            "under outcome 4 `write-continuity --mode ledger` closed it", CLOSE_PLAN
        )
        self.assertIn("`CONTINUITY.md` (the project-root continuity artifact", CLOSE_PLAN)

    def test_the_capacity_recovery_is_named_rather_than_hand_edited(self):
        self.assertIn("cartopian prune-continuity", CLOSE_PLAN)
        self.assertIn("continuity-capacity-structural", CLOSE_PLAN)
        self.assertIn("Never resolve either refusal by hand-editing", CLOSE_PLAN)


class TestUnchangedSurfaces(unittest.TestCase):
    """The four guarantees the accepted design rests on."""

    def test_the_mediated_write_allowlist_is_tightened_not_loosened(self):
        self.assertEqual(DEST_KINDS["continuity"], "")
        self.assertEqual(ROOT_FILES["continuity"], cy.CONTINUITY_BASENAME)
        # A root dest_kind still writes exactly one basename.
        self.assertEqual(len(set(ROOT_FILES.values())), len(ROOT_FILES))
        for kind, subtree in DEST_KINDS.items():
            if subtree == "":
                self.assertIn(kind, ROOT_FILES)

    def test_archive_plan_never_snapshots_or_reads_the_artifact(self):
        self.assertNotIn(cy.CONTINUITY_BASENAME, archive_plan.ARCHIVE_ROOT_FILES)
        source = (REPO_ROOT / "cli" / "commands" / "archive_plan.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("CONTINUITY", source)
        self.assertNotIn("continuity", source)

    def test_reset_plan_never_removes_the_artifact_or_targets_the_archive(self):
        self.assertNotIn(cy.CONTINUITY_BASENAME, reset_plan.RESET_ROOT_FILES)
        self.assertNotIn("archive", reset_plan.RESET_CLEAR_DIRS)
        self.assertNotIn("archive", reset_plan.RESET_ENSURE_DIRS)
        source = (REPO_ROOT / "cli" / "commands" / "reset_plan.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("CONTINUITY", source)
        self.assertNotIn("continuity", source)

    def test_the_reset_ordering_the_plan_surface_guard_rests_on_is_intact(self):
        # IMPLEMENTATION_PLAN.md is removed strictly before the first
        # decisions/ entry, which is exactly what its presence proves.
        self.assertIn("IMPLEMENTATION_PLAN.md", reset_plan.RESET_ROOT_FILES)
        self.assertEqual(reset_plan.RESET_CLEAR_DIRS[-1], "decisions")

    def test_prompt_evidence_and_the_atomic_primitive_are_unchanged(self):
        for module, name in ((prompt_evidence, "prompt_evidence"), (atomic_write, "atomic_write")):
            source = (REPO_ROOT / "cli" / f"{name}.py").read_text(encoding="utf-8")
            with self.subTest(module=name):
                self.assertNotIn("CONTINUITY", source)
                self.assertNotIn("continuity", source)
        # The whole of the window witness is already public API: both entry
        # points already accept the window the caller names.
        import inspect

        for function in (prompt_evidence.close_plan_sequence, prompt_evidence.read_ledger):
            self.assertIn("plan_id", inspect.signature(function).parameters)


class TestConflictingLocation(unittest.TestCase):
    """`<project-root>/CONTINUITY.md` is the only continuity artifact."""

    def setUp(self):
        self.scaffold = continuity_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root

    def test_a_resources_decoy_is_never_resolved_as_continuity(self):
        decoy = self.root / "resources" / "CONTINUITY.md"
        decoy.write_text(SPECIMEN, encoding="utf-8")
        self.assertFalse(cy.read_artifact(self.root).present)
        # It *is* copied into an archive, because resources/ is archived — and
        # it is not continuity there either.
        decision(self.scaffold, "DEC-001", scope="delivery", ruling="Approval first.")
        archive(self.scaffold, "2026-08-14")
        self.assertTrue(
            (self.root / "archive/PLAN-001/resources/CONTINUITY.md").is_file()
        )
        self.assertFalse(cy.read_artifact(self.root).present)

    def test_the_root_artifact_is_never_snapshotted_into_an_archive(self):
        (self.root / "CONTINUITY.md").write_text(SPECIMEN, encoding="utf-8")
        before = (self.root / "CONTINUITY.md").read_bytes()
        decision(self.scaffold, "DEC-001", scope="delivery", ruling="Approval first.")
        code, records, err = run_cli(
            "archive-plan", str(self.root), "--closed", "2026-08-14",
            "--summary", "s", "--content", "# c\n",
        )
        self.assertEqual(code, 0, err)
        self.assertNotIn("CONTINUITY.md", records[0]["details"]["copied"])
        self.assertFalse((self.root / "archive/PLAN-001/CONTINUITY.md").exists())
        self.assertEqual((self.root / "CONTINUITY.md").read_bytes(), before)

    def test_the_reservation_files_are_not_continuity_artifacts(self):
        seed_evidence(self.scaffold, "PLAN-001")
        decision(self.scaffold, "DEC-014", scope="intake", ruling="Intake names a requester.")
        code, _, err = write_continuity(self.scaffold, "ledger", "2026-08-14")
        self.assertEqual(code, 0, err)
        sentinel = (
            self.root / "archive/PLAN-001" / cy.NOT_ARCHIVED_BASENAME
        ).read_text(encoding="utf-8")
        self.assertNotIn("Format:", sentinel)
        self.assertNotIn("## Live decisions", sentinel)


class TestMigrationCoupling(unittest.TestCase):
    """The canonical-name migration reaches the root artifact, and is bounded."""

    def setUp(self):
        self.scaffold = continuity_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root
        (self.root / "archive" / "PLAN-001-old-slug").mkdir(parents=True)
        (self.root / "archive" / "INDEX.md").write_text(
            "# Archive Index\n\n| Archive | Closed | Summary |\n| --- | --- | --- |\n"
            "| `PLAN-001-old-slug` | 2026-08-14 | s |\n",
            encoding="utf-8",
        )
        self.scaffold.write("decisions/DEC-001-descriptive-name.md", "# DEC-001\n")

    def _plan(self):
        from cli import migrations

        return migrations._canonical_name_migration(self.root, "v0.10.0")

    def test_a_valid_artifact_plans_no_write_naming_it(self):
        (self.root / "CONTINUITY.md").write_text(SPECIMEN, encoding="utf-8")
        before = (self.root / "CONTINUITY.md").read_bytes()
        plan = self._plan()
        self.assertNotIn(
            "CONTINUITY.md", [str(write.relative_target) for write in plan.writes]
        )
        self.assertEqual((self.root / "CONTINUITY.md").read_bytes(), before)

    def test_a_non_utf8_artifact_fail_closes_the_migration(self):
        from cli.atomic_write import GuardRefusal

        (self.root / "CONTINUITY.md").write_bytes(b"# Continuity\n\n\xff\xfe\n")
        with self.assertRaises(GuardRefusal) as caught:
            self._plan()
        self.assertEqual(caught.exception.rule, "invalid-utf8")

    def test_the_reservation_is_outside_every_planned_transform(self):
        from cli import migrations

        (self.root / "archive" / "PLAN-002").mkdir()
        for name, body in (
            (cy.NOT_ARCHIVED_BASENAME, cy.not_archived_body("PLAN-002")),
            (cy.LEDGER_FAILED_BASENAME, cy.ledger_failed_body("PLAN-002")),
        ):
            (self.root / "archive" / "PLAN-002" / name).write_text(body, encoding="utf-8")
        (self.root / "archive" / ".INDEX.md.tmp-abc").write_text("x", encoding="utf-8")
        reached = {str(path) for path in migrations._governance_markdown_paths(self.root)}
        for name in (cy.NOT_ARCHIVED_BASENAME, cy.LEDGER_FAILED_BASENAME, ".INDEX.md.tmp-abc"):
            self.assertFalse(
                any(path.endswith(name) for path in reached),
                f"{name} is inside the reference-rewrite surface",
            )
        # The reservation's own index row is reached, and is inert there: the
        # only replacement planned maps a descriptive name to a canonical one.
        cy.ensure_reservation_row(self.root, "PLAN-002", "2026-08-19")
        self.assertIn(str(self.root / "archive" / "INDEX.md"), {
            str(path) for path in migrations._governance_markdown_paths(self.root)
        })
        # The row is inert inside that surface: the only replacement planned
        # maps a descriptive name to a canonical one, and the fixed summary
        # contains no artifact name at all.
        (self.root / "CONTINUITY.md").write_text(SPECIMEN, encoding="utf-8")
        plan = self._plan()
        index_writes = [
            write for write in plan.writes if write.dest_kind == "archive-index"
        ]
        self.assertEqual(len(index_writes), 1)
        after = index_writes[0].after.decode("utf-8")
        self.assertIn(cy.reservation_row("PLAN-002", "2026-08-19"), after)
        self.assertNotIn(
            "CONTINUITY.md", [write.relative_target for write in plan.writes]
        )


class TestEnabledThenDisabledThenEnabled(unittest.TestCase):
    """A gap is legitimate and carries a defined meaning."""

    def setUp(self):
        self.scaffold = continuity_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root
        self.artifact = self.root / "CONTINUITY.md"

    def test_an_archive_only_plan_between_two_enabled_plans(self):
        decision(self.scaffold, "DEC-001", scope="delivery", ruling="Approval first.")
        archive(self.scaffold, "2026-08-14")
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-14", plan="PLAN-001"
        )
        self.assertEqual(code, 0, err)
        run_cli("reset-plan", str(self.root))
        before = self.artifact.read_bytes()

        # PLAN-002 closes `archive` only: no continuity command runs.
        self.scaffold.write("IMPLEMENTATION_PLAN.md", "# Plan\n")
        decision(self.scaffold, "DEC-002", scope="intake", ruling="Intake names a date.")
        archive(self.scaffold, "2026-08-19")
        self.assertEqual(self.artifact.read_bytes(), before)
        run_cli("reset-plan", str(self.root))
        # The artifact still projects across the disabled closeout.
        self.assertEqual(cy.read_artifact(self.root).model.live[0].id, "PLAN-001/DEC-001")

        # PLAN-003 closes archive+index: the ledger is non-contiguous.
        self.scaffold.write("IMPLEMENTATION_PLAN.md", "# Plan\n")
        decision(self.scaffold, "DEC-005", scope="review", ruling="Two reviewers.")
        archive(self.scaffold, "2026-08-24")
        code, _, err = write_continuity(
            self.scaffold, "index", "2026-08-24", plan="PLAN-003"
        )
        self.assertEqual(code, 0, err)
        model = cy.read_artifact(self.root).model
        self.assertEqual([s.plan for s in model.sections], ["PLAN-001", "PLAN-003"])
        self.assertEqual(model.plans_recorded, 2)
        self.assertEqual(model.highest_plan, 3)


class TestDecisionAuthoringValidation(unittest.TestCase):
    """`write-decision` validates the body it was handed and refuses fail-closed."""

    def setUp(self):
        self.scaffold = continuity_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root

    def write(self, body, dec_id="DEC-001"):
        return run_cli(
            "write-decision", str(self.root), "--dec-id", dec_id,
            "--title", "t", "--date", "2026-08-14", "--content", body,
        )

    def body(self, *headers):
        return (
            "# DEC-001: t\n\nDate: 2026-08-14\nStatus: locked\n"
            + "".join(f"{line}\n" for line in headers)
            + "\n## Context\n\nx\n"
        )

    def test_a_valid_body_writes_byte_for_byte_unchanged(self):
        body = self.body("Scope: delivery", "Ruling: Approval precedes publication.",
                         "Supersedes: none", "Expires: none")
        code, records, err = self.write(body)
        self.assertEqual(code, 0, err)
        self.assertEqual(
            (self.root / "decisions" / "DEC-001.md").read_text(encoding="utf-8"), body
        )
        # No new flags and no new index column.
        index = (self.root / "decisions" / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("| ID | Title | Date | Status | Supersedes |", index)
        self.assertNotIn("Scope", index)

    def test_a_body_that_breaks_a_field_rule_refuses_before_anything_lands(self):
        cases = [
            (("Scope: delivery", "Ruling: a | b"), "`|`"),
            (("Scope: " + "x" * 25, "Ruling: r."), "24-character"),
            (("Scope: delivery", "Ruling: "), "non-empty"),
            (("Scope: delivery", "Ruling: a\x01b"), "control"),
            (("Scope: " + "\U00020000" * 13, "Ruling: r."), "48-serialized-byte"),
        ]
        for headers, fragment in cases:
            with self.subTest(headers=headers):
                self.setUp()
                code, records, err = self.write(self.body(*headers))
                self.assertEqual(code, 2, err)
                self.assertEqual(records, [])
                self.assertIn(fragment, err)
                self.assertFalse((self.root / "decisions" / "DEC-001.md").exists())

    def test_a_body_that_breaks_the_reference_grammar_refuses(self):
        for header in ("Expires: DEC-1", "Supersedes: PLAN-001/DEC-001, PLAN-001/DEC-001",
                       "Expires: " + ", ".join(f"PLAN-001/DEC-{n:03d}" for n in range(1, 10))):
            with self.subTest(header=header):
                self.setUp()
                code, _, err = self.write(self.body(header))
                self.assertEqual(code, 2, err)
                self.assertFalse((self.root / "decisions" / "DEC-001.md").exists())

    def test_a_bare_reference_must_name_a_file_in_the_current_decisions(self):
        code, _, err = self.write(self.body("Expires: DEC-009"))
        self.assertEqual(code, 2)
        self.assertIn("DEC-009", err)
        self.scaffold.write("decisions/DEC-009.md", "# DEC-009\n\nStatus: locked\n")
        code, _, err = self.write(self.body("Expires: DEC-009"))
        self.assertEqual(code, 0, err)

    def test_authoring_reads_no_continuity_artifact(self):
        (self.root / "CONTINUITY.md").write_bytes(b"\xff\xfe")
        code, _, err = self.write(self.body("Scope: delivery", "Ruling: r."))
        self.assertEqual(code, 0, err)

    def test_a_commented_out_template_hint_is_not_a_header(self):
        body = (
            "# DEC-001: t\n\nDate: 2026-08-14\nStatus: locked\n"
            "<!--\nScope: a hint far longer than the twenty-four character bound\n-->\n"
            "\n## Context\n\nx\n"
        )
        code, _, err = self.write(body)
        self.assertEqual(code, 0, err)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
