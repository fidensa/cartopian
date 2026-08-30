"""Unit coverage for `cli.continuity` — the one authority every reader and
writer of the optional continuity artifact calls.

The schema assertions are byte-and-digest identities against the accepted
design's published specimen and its two single-mode variants, so a serializer
that drifts from the contract fails here rather than in a downstream record.
"""
from __future__ import annotations

import copy
import hashlib
import os
import unittest

from cli import atomic_write, continuity as cy
from tests.continuity_support import continuity_scaffold, decision, seed_evidence

# The § 5 specimen: a genuine three-plan file whose plans closed archive+index,
# ledger, and archive+index in that order. It is mixed-mode on purpose — a
# single-mode specimen would let a mode-global reading of Body and Retrieval
# pass unnoticed.
SPECIMEN = """# Continuity

Format: continuity-v1
Plans recorded: 3
Highest plan recorded: PLAN-003
Live decisions: 3
Cold decisions: 3

## Live decisions

| Decision | Scope | Ruling | Body |
| --- | --- | --- | --- |
| PLAN-003/DEC-002 | containment | A contained agent never receives a raw delete verb. | archive/PLAN-003/decisions/DEC-002.md |
| PLAN-002/DEC-014 | intake | Every intake record names its requester. | none |
| PLAN-001/DEC-001 | delivery | Approval precedes publication. | archive/PLAN-001/decisions/DEC-001.md |

## Cold decisions

- Superseded: 2; Expired: 1; Pruned: 0; Retrieval: mixed

## Cold index

| Decision | Scope | Ruling | Disposition | Removed by | Body |
| --- | --- | --- | --- | --- | --- |
| PLAN-002/DEC-009 | review | Every review names two reviewers. | expired | PLAN-003/DEC-005 | none |
| PLAN-002/DEC-003 | intake | Intake records may be anonymous. | superseded | PLAN-002/DEC-014 | none |
| PLAN-001/DEC-007 | delivery | Publication may precede approval for drafts. | superseded | PLAN-001/DEC-001 | archive/PLAN-001/decisions/DEC-007.md |

## Plan ledger

### PLAN-001 - closed 2026-08-14 - archive+index

- Outcome: Published the delivery approval rule; terminal state: closed; verification: outcome-verified
- Evidence: Approval recorded in archive/PLAN-001/CLOSEOUT.md; state: verified
- Decisions: 1 governing; 1 superseded; 0 expired
- Risks: none open
- Delivery: Delivery approval rule; target: publication owners; state: accepted 2026-08-14
- Follow-up: none

### PLAN-002 - closed 2026-08-19 - ledger

- Outcome: Replaced anonymous intake with attributed intake; terminal state: closed; verification: outcome-verified
- Evidence: Intake sample reviewed by the plan owner on 2026-08-19; state: verified
- Decisions: 2 governing; 1 superseded; 0 expired
- Risks: Reviewer coverage depends on one owner; disposition: accepted; owner: plan owner
- Delivery: Attributed intake record format; target: intake volunteers; state: accepted 2026-08-19
- Follow-up: none

### PLAN-003 - closed 2026-08-24 - archive+index

- Outcome: Published the revised intake guide; terminal state: closed; verification: outcome-verified
- Evidence: Recipient confirmation recorded in archive/PLAN-003/CLOSEOUT.md; state: verified
- Decisions: 1 governing; 0 superseded; 1 expired
- Risks: none open
- Delivery: Revised intake guide; target: field team; state: accepted 2026-08-24
- Follow-up: none
"""

SPECIMEN_DIGEST = "de866ec248bf5f5fea2cdb0731d4b4961f800fc5dbf80f3f7c71e49ff39db73c"
ALL_INDEX_DIGEST = "4c2eef02ff3645c7809c64865de472231956d5f7f03aa24dd55dc80423db5368"
ALL_LEDGER_DIGEST = "88b4c0141cdcb92eb95e3297497750d526ed9cbb59e658790262ac573e8db827"

FOUR_BYTE = "\U00020000"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TestSchemaIdentity(unittest.TestCase):
    """The serializer reproduces the accepted design's measured artifacts."""

    def setUp(self):
        self.model = cy.parse_continuity(SPECIMEN)

    def test_specimen_round_trips_byte_for_byte(self):
        rendered = cy.serialize_continuity(self.model)
        self.assertEqual(rendered, SPECIMEN)
        self.assertEqual(len(rendered.encode("utf-8")), 2414)
        self.assertEqual(digest(rendered), SPECIMEN_DIGEST)

    def _variant(self, *, body, preservation):
        model = copy.deepcopy(self.model)
        for row in model.live + model.cold:
            row.body = cy.locator_for(row.id) if body == "archive" else "none"
        for section in model.sections:
            section.preservation = preservation
        return cy.serialize_continuity(model)

    def test_single_mode_variants_reproduce(self):
        index = self._variant(body="archive", preservation=cy.PRESERVATION_INDEX)
        ledger = self._variant(body="none", preservation=cy.PRESERVATION_LEDGER)
        self.assertEqual((len(index.encode()), digest(index)), (2519, ALL_INDEX_DIGEST))
        self.assertEqual((len(ledger.encode()), digest(ledger)), (2302, ALL_LEDGER_DIGEST))

    def test_startup_read_prefix_is_bounded_by_the_section_order(self):
        # A projecting reader stops at ## Cold index, so startup parse cost does
        # not grow as cold rows or closed plans accumulate.
        def prefix(text):
            return len(text[: text.index("## Cold index")].encode("utf-8"))

        self.assertEqual(prefix(SPECIMEN), 597)
        self.assertEqual(prefix(self._variant(body="archive", preservation="archive+index")), 629)
        self.assertEqual(prefix(self._variant(body="none", preservation="ledger")), 532)

    def test_ledger_sections_measure_their_per_plan_increment(self):
        self.assertEqual(
            [section.unit_bytes() for section in self.model.sections], [412, 501, 414]
        )

    def test_mode_is_a_property_of_a_closeout_not_of_the_file(self):
        # PLAN-002/DEC-009 was written by a ledger closeout and moved to the
        # cold index by an archive+index one. Its Body stayed `none`: a later
        # mode cannot give a prior plan's decision an archive it never had.
        row = next(r for r in self.model.cold if r.id == "PLAN-002/DEC-009")
        self.assertEqual(row.body, "none")
        self.assertEqual(self.model.retrieval, "mixed")

    def test_counters_are_derived_and_pruned_is_carried(self):
        self.assertEqual(self.model.plans_recorded, 3)
        self.assertEqual(self.model.highest_plan, 3)
        self.assertEqual((self.model.superseded, self.model.expired), (2, 1))
        self.assertEqual(self.model.pruned, 0)


class TestDeclaredBounds(unittest.TestCase):
    """Every capacity figure the contract quotes is reproduced from the shapes."""

    def _envelope(self, counters):
        text = cy.serialize_continuity(cy.Continuity()) + "\n"
        for old, new in counters:
            self.assertIn(old, text)
            text = text.replace(old, new, 1)
        return len(text.encode("utf-8"))

    def test_file_envelopes_and_budgets(self):
        widest = self._envelope([
            ("Plans recorded: 0", "Plans recorded: 999"),
            ("Highest plan recorded: none", "Highest plan recorded: PLAN-999"),
            ("Live decisions: 0", "Live decisions: 9999"),
            ("Cold decisions: 0", "Cold decisions: 9999"),
            ("- Superseded: 0; Expired: 0; Pruned: 0; Retrieval: none",
             "- Superseded: 9999; Expired: 9999; Pruned: 999999; Retrieval: ruling"),
        ])
        narrowest = self._envelope([
            ("Plans recorded: 0", "Plans recorded: 1"),
            ("Highest plan recorded: none", "Highest plan recorded: PLAN-001"),
            ("Live decisions: 0", "Live decisions: 1"),
            ("Cold decisions: 0", "Cold decisions: 1"),
            ("- Superseded: 0; Expired: 0; Pruned: 0; Retrieval: none",
             "- Superseded: 1; Expired: 1; Pruned: 0; Retrieval: body"),
        ])
        self.assertEqual(widest, 437)
        self.assertEqual(cy.CONTINUITY_MAX_BYTES - widest, 65099)
        self.assertEqual(narrowest, 416)
        self.assertEqual(cy.CONTINUITY_MAX_BYTES - narrowest, 65120)

    def test_worst_case_rows_per_outcome(self):
        scope, ruling = FOUR_BYTE * 12, FOUR_BYTE * 50
        self.assertEqual(cy.serialized_bytes(scope), cy.SCOPE_BYTE_MAX)
        self.assertEqual(cy.serialized_bytes(ruling), cy.RULING_BYTE_MAX)

        def live(body):
            row = cy.LiveRow("PLAN-999/DEC-999", scope, ruling, body)
            return len(f"| {row.id} | {row.scope} | {row.ruling} | {row.body} |\n".encode())

        def cold(body):
            row = cy.ColdRow(
                "PLAN-999/DEC-999", scope, ruling, "superseded", "PLAN-999/DEC-998", body
            )
            return len(
                f"| {row.id} | {row.scope} | {row.ruling} | {row.disposition} | "
                f"{row.removed_by} | {row.body} |\n".encode()
            )

        locator = cy.locator_for("PLAN-999/DEC-999")
        self.assertEqual(len(locator.encode()), 37)
        self.assertEqual((live(locator), cold(locator)), (315, 347))
        self.assertEqual((live("none"), cold("none")), (282, 314))

    def test_minimum_width_rows_and_section(self):
        live = cy.LiveRow("PLAN-001/DEC-001", "a", "a", "none")
        cold = cy.ColdRow("PLAN-001/DEC-001", "a", "a", "expired", "PLAN-001/DEC-002", "none")
        self.assertEqual(
            len(f"| {live.id} | {live.scope} | {live.ruling} | {live.body} |\n".encode()), 36
        )
        self.assertEqual(
            len(
                f"| {cold.id} | {cold.scope} | {cold.ruling} | {cold.disposition} | "
                f"{cold.removed_by} | {cold.body} |\n".encode()
            ),
            65,
        )
        minimum = cy.LedgerSection("PLAN-001", "2026-08-14", "ledger", [
            "- Outcome: a; terminal state: closed; verification: outcome-verified",
            "- Evidence: a; state: missing",
            "- Decisions: 0 governing; 0 superseded; 0 expired",
            "- Risks: none open",
            "- Delivery: none",
            "- Follow-up: none",
        ])
        self.assertEqual(minimum.unit_bytes(), 247)

    def test_tombstones_measure_per_preservation_value(self):
        for preservation, want in ((cy.PRESERVATION_INDEX, 174), (cy.PRESERVATION_LEDGER, 167)):
            section = cy.LedgerSection("PLAN-001", "2026-08-14", preservation, ["- x"])
            self.assertEqual(
                cy.tombstone_section(section, "2026-09-02").unit_bytes(), want
            )

    def test_reservation_texts_are_fixed_width_apart_from_the_plan_id(self):
        self.assertEqual(len(cy.not_archived_body("PLAN-002").encode()), 245)
        self.assertEqual(len(cy.ledger_failed_body("PLAN-002").encode()), 334)
        self.assertEqual(
            len((cy.reservation_row("PLAN-002", "2026-08-26") + "\n").encode()), 73
        )


class TestFieldGrammar(unittest.TestCase):
    """Refusal, not escaping: the cell and the JSON value carry the same bytes."""

    def test_each_rule_refuses_and_names_itself(self):
        cases = {
            "a | b": "`|`",
            "a\rb": "control",
            "a\nb": "control",
            "a\x01b": "control",
            " leading": "spaces",
            "trailing ": "spaces",
            "": "non-empty",
            "x" * 25: "24-character",
            # Inside the character bound, over the serialized-byte bound: the
            # exact case a character bound alone would have admitted.
            FOUR_BYTE * 13: "48-serialized-byte",
        }
        for value, fragment in cases.items():
            with self.subTest(value=repr(value)):
                violation = cy.cell_violation(value, "Scope")
                self.assertIsNotNone(violation)
                self.assertIn(fragment, violation)

    def test_a_character_bound_alone_would_not_hold_the_byte_bound(self):
        # Twelve four-byte characters meet both bounds exactly; a value inside
        # the character bound can still break the serialized-byte bound.
        self.assertIsNone(cy.cell_violation(FOUR_BYTE * 12, "Scope"))
        self.assertIsNone(cy.cell_violation(FOUR_BYTE * 50, "Ruling"))
        overlong = FOUR_BYTE * 51
        self.assertEqual(len(overlong), 51)
        self.assertIn("200-serialized-byte", cy.cell_violation(overlong, "Ruling"))

    def test_serialized_bytes_matches_the_record_encoder(self):
        import json

        for value in ("plain", 'a "quoted" word', "back\\slash", FOUR_BYTE * 3):
            encoded = json.dumps({"k": value}, ensure_ascii=False, separators=(",", ":"))
            self.assertEqual(
                cy.serialized_bytes(value),
                len(encoded.encode("utf-8")) - len('{"k":""}'.encode("utf-8")),
            )


class TestReferenceGrammar(unittest.TestCase):
    def test_none_and_both_reference_forms_parse(self):
        self.assertEqual(cy.parse_reference_header("none", "Supersedes"), ([], None))
        self.assertEqual(
            cy.parse_reference_header("DEC-001, PLAN-002/DEC-014", "Supersedes"),
            (["DEC-001", "PLAN-002/DEC-014"], None),
        )

    def test_eight_references_resolve_and_a_ninth_refuses(self):
        eight = ", ".join(f"PLAN-001/DEC-{n:03d}" for n in range(1, 9))
        refs, violation = cy.parse_reference_header(eight, "Expires")
        self.assertEqual((len(refs), violation), (8, None))
        self.assertLessEqual(cy.serialized_bytes(eight), cy.REFERENCE_HEADER_BYTE_MAX)
        nine = ", ".join(f"PLAN-001/DEC-{n:03d}" for n in range(1, 10))
        self.assertIn("at most 8", cy.parse_reference_header(nine, "Expires")[1])

    def test_a_repeat_inside_one_header_refuses_at_authoring_only(self):
        value = "DEC-001, DEC-001"
        self.assertIn("repeats", cy.parse_reference_header(value, "Supersedes")[1])
        # It never reaches the removal multimap: the continuity write collapses
        # a hand-edited repeat to one reference rather than reading two removals.
        self.assertEqual(
            cy.parse_reference_header(value, "Supersedes", allow_repeat=True),
            (["DEC-001"], None),
        )

    def test_malformed_shapes_refuse(self):
        for value in ("DEC-1", "PLAN-1/DEC-001", "DEC-001 DEC-002", ""):
            with self.subTest(value=value):
                self.assertIsNotNone(cy.parse_reference_header(value, "Expires")[1])


class TestDerivedRetrieval(unittest.TestCase):
    def test_the_summary_is_over_the_rows_present_never_a_mode(self):
        with_body = cy.LiveRow("PLAN-001/DEC-001", "s", "r", "archive/x.md")
        without = cy.LiveRow("PLAN-002/DEC-001", "s", "r", "none")
        self.assertEqual(cy.derived_retrieval([], []), "none")
        self.assertEqual(cy.derived_retrieval([with_body], []), "body")
        self.assertEqual(cy.derived_retrieval([without], []), "ruling")
        self.assertEqual(cy.derived_retrieval([with_body, without], []), "mixed")


class TestReadErrorContract(unittest.TestCase):
    """Absence is a successful disabled state; presence with any defect refuses."""

    def setUp(self):
        self.scaffold = continuity_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root
        self.path = self.root / "CONTINUITY.md"

    def test_absent_is_not_a_refusal(self):
        found = cy.read_artifact(self.root)
        self.assertFalse(found.present)
        self.assertIsNone(found.model)
        self.assertEqual(found.highest, 0)

    def test_valid_artifact_parses(self):
        self.path.write_text(SPECIMEN, encoding="utf-8")
        self.assertEqual(cy.read_artifact(self.root).highest, 3)

    def _refusal(self, rule, detail_fragment=None):
        with self.assertRaises(cy.ContinuityRefusal) as caught:
            cy.read_artifact(self.root)
        self.assertEqual(caught.exception.rule, rule)
        if detail_fragment is not None:
            self.assertIn(detail_fragment, caught.exception.detail)

    def test_symlink_refuses_unsafe_artifact(self):
        target = self.root / "elsewhere.md"
        target.write_text(SPECIMEN, encoding="utf-8")
        os.symlink(target, self.path)
        self._refusal("continuity-unsafe-artifact", "symlink")

    def test_hardlink_refuses_unsafe_artifact(self):
        other = self.root / "other.md"
        other.write_text(SPECIMEN, encoding="utf-8")
        os.link(other, self.path)
        self._refusal("continuity-unsafe-artifact", "st_nlink=2")

    def test_non_regular_refuses_unsafe_artifact(self):
        self.path.mkdir()
        self._refusal("continuity-unsafe-artifact", "not a regular file")

    def test_non_utf8_refuses_encoding_invalid(self):
        self.path.write_bytes(b"# Continuity\n\n\xff\xfe\n")
        self._refusal("continuity-encoding-invalid")

    def test_unrecognized_format_refuses(self):
        self.path.write_text(
            SPECIMEN.replace("Format: continuity-v1", "Format: continuity-v2"),
            encoding="utf-8",
        )
        self._refusal("continuity-format-unrecognized", "continuity-v2")

    def test_a_malformed_section_refuses_and_names_it(self):
        broken = SPECIMEN.replace("## Cold index", "## Cold rows")
        self.path.write_text(broken, encoding="utf-8")
        self._refusal("continuity-parse-failed", "cold index")

    def test_a_counter_that_disagrees_with_the_rows_refuses(self):
        self.path.write_text(
            SPECIMEN.replace("Live decisions: 3", "Live decisions: 2"), encoding="utf-8"
        )
        self._refusal("continuity-parse-failed", "header block")


class _Injector:
    """A delegating ``os`` proxy that fails one boundary of the atomic write.

    Every guard in the primitive still runs; the proxy can only inject an
    action at a fixed point. ``link`` and ``unlink`` are carried in
    ``supports_dir_fd`` so the ``expect_absent`` branch is still taken.
    """

    def __init__(self, boundary):
        self.boundary = boundary
        self.fsyncs = 0
        self.linked = False
        self.supports_dir_fd = {self.open, self.rename, self.unlink, self.link}

    def __getattr__(self, name):
        return getattr(os, name)

    def _fail(self):
        raise OSError(5, "injected boundary failure")

    def open(self, *args, **kwargs):
        # Keyed on the primitive's own fixed temp-name shape, so the boundary is
        # the same one on the dir-fd branch and on the path-based fallback.
        if self.boundary == "tmp-open" and args and ".cartmp." in str(args[0]):
            self._fail()
        return os.open(*args, **kwargs)

    def write(self, *args, **kwargs):
        if self.boundary == "tmp-write":
            self._fail()
        return os.write(*args, **kwargs)

    def fsync(self, fd):
        self.fsyncs += 1
        if self.boundary == "tmp-fsync" and self.fsyncs == 1:
            self._fail()
        if self.boundary == "dir-fsync" and self.linked:
            self._fail()
        return os.fsync(fd)

    def link(self, *args, **kwargs):
        if self.boundary == "link":
            self._fail()
        if self.boundary == "link-eexist":
            raise FileExistsError(17, "injected")
        result = os.link(*args, **kwargs)
        self.linked = True
        return result

    def unlink(self, *args, **kwargs):
        if self.boundary == "post-link-unlink" and self.linked:
            self._fail()
        return os.unlink(*args, **kwargs)

    def replace(self, *args, **kwargs):
        if self.boundary == "replace":
            self._fail()
        result = os.replace(*args, **kwargs)
        self.linked = True
        return result


class TestPublicationStep(unittest.TestCase):
    """Plan-id closure never rests on "a refusal was raised"."""

    def setUp(self):
        self.scaffold = continuity_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root
        self.path = self.root / "CONTINUITY.md"

    def _publish(self, boundary, prior):
        from cli.mediated_write import mediated_write

        intended = SPECIMEN.encode("utf-8")
        injector = _Injector(boundary)
        real_os = atomic_write.os
        atomic_write.os = injector
        try:
            return cy.publish(
                self.root,
                self.path,
                prior,
                intended,
                lambda: mediated_write(self.root, "continuity", "CONTINUITY.md", intended),
            )
        finally:
            atomic_write.os = real_os

    def test_first_publication_classifies_at_every_boundary(self):
        for boundary in ("tmp-open", "tmp-write", "tmp-fsync", "link", "link-eexist"):
            with self.subTest(boundary=boundary):
                result = self._publish(boundary, None)
                self.assertEqual(result.outcome, cy.NOT_PUBLISHED)
                self.assertFalse(os.path.lexists(self.path))

    def test_the_post_commit_cleanup_failure_is_published_and_collapses(self):
        # The defect the contract measures: a named refusal comes back from a
        # call whose commit already landed. A marker rule keyed on the refusal
        # would mark a recorded plan's reservation.
        result = self._publish("post-link-unlink", None)
        self.assertEqual(result.outcome, cy.PUBLISHED)
        self.assertIsNotNone(result.refusal)
        self.assertTrue(result.residue_collapsed)
        self.assertEqual(result.surviving, [])
        self.assertEqual(self.path.stat().st_nlink, 1)
        self.assertEqual(self.path.read_text(encoding="utf-8"), SPECIMEN)
        self.assertTrue(result.clean)

    def test_a_swallowed_directory_fsync_is_published(self):
        result = self._publish("dir-fsync", None)
        self.assertEqual(result.outcome, cy.PUBLISHED)
        self.assertIsNone(result.refusal)
        self.assertEqual(self.path.stat().st_nlink, 1)

    def test_clean_control_is_published_with_one_link(self):
        result = self._publish(None, None)
        self.assertTrue(result.clean)
        self.assertFalse(result.residue_collapsed)
        self.assertEqual(self.path.stat().st_nlink, 1)

    def test_replacement_path_keeps_the_previous_bytes_before_the_commit(self):
        self.path.write_text("# Continuity\n\nold\n", encoding="utf-8")
        prior = self.path.read_bytes()
        for boundary in ("tmp-open", "tmp-write", "tmp-fsync", "replace"):
            with self.subTest(boundary=boundary):
                result = self._publish(boundary, prior)
                self.assertEqual(result.outcome, cy.NOT_PUBLISHED)
                self.assertEqual(self.path.read_bytes(), prior)

    def test_replacement_has_no_post_commit_step_to_fail(self):
        self.path.write_text("# Continuity\n\nold\n", encoding="utf-8")
        prior = self.path.read_bytes()
        result = self._publish("post-link-unlink", prior)
        self.assertEqual(result.outcome, cy.PUBLISHED)
        self.assertEqual(self.path.stat().st_nlink, 1)

    def test_states_that_settle_neither_question_are_unresolved(self):
        foreign = b"# Continuity\n\nforeign\n"
        cases = {
            "foreign bytes with no prior": (None, foreign),
            "foreign bytes over a prior": (b"# Continuity\n\nprior\n", foreign),
        }
        for label, (prior, landed) in cases.items():
            with self.subTest(case=label):
                self.path.write_bytes(landed)
                result = cy.publish(
                    self.root, self.path, prior, SPECIMEN.encode("utf-8"), lambda: None
                )
                self.assertEqual(result.outcome, cy.UNRESOLVED)
        # Vanished where one existed.
        self.path.unlink()
        result = cy.publish(
            self.root, self.path, b"# Continuity\n\nprior\n", SPECIMEN.encode(), lambda: None
        )
        self.assertEqual(result.outcome, cy.UNRESOLVED)

    def test_an_uncollapsible_residue_leaves_the_artifact_unreadable(self):
        from cli.mediated_write import GuardRefusal, mediated_write

        self.path.write_text(SPECIMEN, encoding="utf-8")
        sibling = self.root / f"{cy.CONTINUITY_BASENAME}.cartmp.1.abcdef"
        os.link(self.path, sibling)
        # Both guards the contract measures fire while the residue survives.
        with self.assertRaises(cy.ContinuityRefusal) as reader:
            cy.read_artifact(self.root)
        self.assertEqual(reader.exception.rule, "continuity-unsafe-artifact")
        with self.assertRaises(GuardRefusal) as writer:
            mediated_write(self.root, "continuity", "CONTINUITY.md", "x")
        self.assertEqual(writer.exception.rule, "hardlink")
        # The collapse is what makes the state transient.
        collapse = cy.collapse_residue(self.root, self.path)
        self.assertTrue(collapse.hardlinked)
        self.assertEqual(collapse.surviving, [])
        self.assertEqual(self.path.stat().st_nlink, 1)
        self.assertEqual(cy.read_artifact(self.root).highest, 3)

    def test_residue_removal_is_recorded_as_a_mediated_delete(self):
        from cli import provenance

        self.path.write_text(SPECIMEN, encoding="utf-8")
        sibling = self.root / f"{cy.CONTINUITY_BASENAME}.cartmp.1.abcdef"
        os.link(self.path, sibling)
        cy.collapse_residue(self.root, self.path)
        journal = (self.root / provenance.LOG_RELPATH).read_text(encoding="utf-8")
        self.assertIn("mediated-delete", journal)
        self.assertIn(sibling.name, journal)

    def test_an_unrelated_writers_temp_is_never_touched(self):
        self.path.write_text(SPECIMEN, encoding="utf-8")
        foreign = self.root / "STATE.md.cartmp.1.abcdef"
        foreign.write_text("someone else's in-flight write", encoding="utf-8")
        cy.collapse_residue(self.root, self.path)
        self.assertTrue(foreign.is_file())


class TestWindowWitness(unittest.TestCase):
    """The durable fact that separates a retry, an abandonment, and a later plan."""

    def setUp(self):
        self.scaffold = continuity_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root

    def test_one_windows_records_make_the_witness_hold_for_exactly_that_id(self):
        seed_evidence(self.scaffold, "PLAN-002")
        self.assertTrue(cy.witness_holds(self.root, "PLAN-002"))
        self.assertFalse(cy.witness_holds(self.root, "PLAN-003"))
        self.assertEqual(cy.window_witness(self.root), "PLAN-002")
        self.assertTrue(cy.witness_contradicts(self.root, "PLAN-003"))
        self.assertFalse(cy.witness_contradicts(self.root, "PLAN-002"))

    def test_an_absent_log_leaves_the_witness_silent(self):
        self.assertIsNone(cy.window_witness(self.root))
        self.assertFalse(cy.witness_contradicts(self.root, "PLAN-002"))

    def test_an_empty_log_leaves_the_witness_silent(self):
        from cli import prompt_evidence

        path = prompt_evidence.log_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        self.assertIsNone(cy.window_witness(self.root))

    def test_an_unparseable_record_leaves_the_witness_silent(self):
        from cli import prompt_evidence

        path = prompt_evidence.log_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json\n", encoding="utf-8")
        self.assertIsNone(cy.window_witness(self.root))

    def test_a_log_mixing_two_windows_leaves_the_witness_silent(self):
        seed_evidence(self.scaffold, "PLAN-002", unit="TASK-02-001")
        seed_evidence(self.scaffold, "PLAN-003", unit="TASK-03-001")
        self.assertIsNone(cy.window_witness(self.root))
        self.assertTrue(cy.witness_contradicts(self.root, "PLAN-002"))
        self.assertTrue(cy.witness_contradicts(self.root, "PLAN-003"))


class TestReservationShapes(unittest.TestCase):
    """Five shapes, total over the directory's entries and this plan's index row."""

    def setUp(self):
        self.scaffold = continuity_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root
        self.reservation = self.root / "archive" / "PLAN-002"

    def _make(self, *, sentinel=False, marker=False, row=False, exists=True):
        if exists:
            self.reservation.mkdir(parents=True)
        if sentinel:
            (self.reservation / cy.NOT_ARCHIVED_BASENAME).write_text(
                cy.not_archived_body("PLAN-002"), encoding="utf-8"
            )
        if marker:
            (self.reservation / cy.LEDGER_FAILED_BASENAME).write_text(
                cy.ledger_failed_body("PLAN-002"), encoding="utf-8"
            )
        if row:
            cy.ensure_reservation_row(self.root, "PLAN-002", "2026-08-26")
        return cy.classify_reservation(self.root, "PLAN-002")

    def test_the_index_row_is_the_only_discriminator_that_matters(self):
        # Both shapes hold NOT-ARCHIVED.md and nothing else. What separates
        # them is whether archive/INDEX.md carries the fixed reservation row.
        self.assertEqual(self._make(sentinel=True, row=False).shape, cy.SHAPE_INCOMPLETE)
        state = self._make(sentinel=True, row=True, exists=False)
        self.assertEqual(state.shape, cy.SHAPE_UNMARKED)
        self.assertFalse(state.releasable)

    def test_every_other_shape_is_named_and_releasable(self):
        cases = [
            (dict(sentinel=True, marker=True, row=True), cy.SHAPE_MARKED),
            (dict(marker=True, row=True), cy.SHAPE_MARKER_ONLY),
            (dict(row=True), cy.SHAPE_EMPTY),
            (dict(sentinel=True, row=False), cy.SHAPE_INCOMPLETE),
        ]
        for kwargs, shape in cases:
            with self.subTest(shape=shape):
                scaffold = continuity_scaffold()
                self.addCleanup(scaffold.cleanup)
                self.root = scaffold.project_root
                self.reservation = self.root / "archive" / "PLAN-002"
                state = self._make(**kwargs)
                self.assertEqual(state.shape, shape)
                self.assertTrue(state.releasable)

    def test_a_closeout_makes_it_an_archive_not_a_reservation(self):
        self.reservation.mkdir(parents=True)
        (self.reservation / "CLOSEOUT.md").write_text("# c\n", encoding="utf-8")
        state = cy.classify_reservation(self.root, "PLAN-002")
        self.assertEqual(state.shape, cy.SHAPE_ARCHIVE)
        self.assertFalse(state.releasable)

    def test_a_foreign_entry_is_malformed_but_publication_residue_is_not(self):
        self.reservation.mkdir(parents=True)
        sentinel = self.reservation / cy.NOT_ARCHIVED_BASENAME
        sentinel.write_text(cy.not_archived_body("PLAN-002"), encoding="utf-8")
        residue = self.reservation / f"{cy.NOT_ARCHIVED_BASENAME}.cartmp.1.abcdef"
        os.link(sentinel, residue)
        state = cy.classify_reservation(self.root, "PLAN-002")
        self.assertEqual(state.shape, cy.SHAPE_INCOMPLETE)
        self.assertEqual(len(state.residue_collapsed), 1)
        self.assertIn("(residue)", state.residue_collapsed[0])
        self.assertEqual(sentinel.stat().st_nlink, 1)

        (self.reservation / "STRAY.md").write_text("x", encoding="utf-8")
        self.assertEqual(
            cy.classify_reservation(self.root, "PLAN-002").shape, cy.SHAPE_MALFORMED
        )

    def test_every_shape_occupies_its_id_identically(self):
        from cli import prompt_evidence

        for kwargs in (dict(), dict(sentinel=True), dict(sentinel=True, marker=True)):
            with self.subTest(kwargs=kwargs):
                scaffold = continuity_scaffold()
                self.addCleanup(scaffold.cleanup)
                (scaffold.project_root / "archive" / "PLAN-002").mkdir(parents=True)
                for name, body in (
                    (cy.NOT_ARCHIVED_BASENAME, kwargs.get("sentinel")),
                    (cy.LEDGER_FAILED_BASENAME, kwargs.get("marker")),
                ):
                    if body:
                        (scaffold.project_root / "archive" / "PLAN-002" / name).write_text(
                            "x", encoding="utf-8"
                        )
                self.assertEqual(
                    prompt_evidence.current_plan_id(scaffold.project_root), "PLAN-003"
                )
                self.assertEqual(
                    cy.highest_archive_number(scaffold.project_root), 2
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
