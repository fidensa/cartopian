"""Acceptance-to-source traceability: the accepted contract, measured.

Every byte count, body identity, and disposition asserted here is read off the
accepted contract's own printed evidence, not off this implementation. The
suite is deliberately built that way: if the serializer drifts, the bodies
stop reproducing and the anchor fails, rather than the expectations quietly
re-baselining onto whatever the code now emits.
"""
import argparse
import contextlib
import io
import json
import unittest
from pathlib import Path

from cli import acceptance_trace as at
from cli import trace_binding
from cli.commands import acceptance_trace as command
from tests.scaffold import project_scaffold

PM_DECISION = (
    "project-management-source "
    "sha256:223aec40379a1c8cfe7aabe536ea86c2828aecd68c61052a09b12ae898612ce4"
)
PM_PLAN = (
    "project-management-source "
    "sha256:6132460bc1f13d2f5b84bcc4a7e37b56f9087d4b782ac7844ad90510368f241d"
)
REQUIREMENTS = "Cartopian REQUIREMENTS.md and STANDARDS.md"
CONVENTIONS = "Cartopian protocol/CONVENTIONS.md and protocol/RISK_AND_PRACTICE.md"
REQ_001 = (
    "REQ-001 sha256:3587c427c1d9aeb2341afdb0ea11e2f67d1b3a2a38d13b4522e852507901a346"
)
REQ_002 = (
    "REQ-002 sha256:00691c510e84362c4ce4c235eb1656c73f92e803c7865e410aa250154704fd61"
)
REQ_003 = (
    "REQ-003 sha256:bd4708b405fcd06ff278f20ea1d648593731de0acef939836bfe41efc2927c83"
)
REQ_098 = (
    "REQ-098 sha256:addd51ca9c46f5162a217a6251de398e752d60eb68d82b3c145840c08b5c7d83"
)
REQ_099 = (
    "REQ-099 sha256:c3e61051df2485bf8d8772f2d4074cf3f45fc04667e90ad440451cf4d8357747"
)

CRITERIA = list(at._ANCHOR_CRITERIA)
RECORDS = list(at._ANCHOR_RECORDS)
SOURCES = list(at._ANCHOR_SOURCES)
EXCERPTS = list(at._ANCHOR_EXCERPTS)


def build(
    criteria=None,
    records=None,
    sources=None,
    excerpts=None,
    task=(),
    enforce_bounds=True,
):
    return at.build(
        spec_acceptance=list(CRITERIA if criteria is None else criteria),
        task_acceptance=list(task),
        record_set=at.parse_record_set(RECORDS if records is None else records),
        sources=list(SOURCES if sources is None else sources),
        excerpts=list(EXCERPTS if excerpts is None else excerpts),
        enforce_bounds=enforce_bounds,
    )


class ConformanceAnchorTests(unittest.TestCase):
    """The accepted 2,581-byte budget, reproduced from this serializer."""

    def test_reference_shape_reproduces_the_accepted_measurement(self):
        anchor = at.conformance_anchor()
        self.assertTrue(anchor["conforms"], anchor)
        self.assertEqual(anchor["shape"], at.REFERENCE_SHAPE)
        self.assertEqual(anchor["coder_bytes"], 143)
        self.assertEqual(anchor["reviewer_bytes"], 2438)
        self.assertEqual(anchor["routine_bytes"], 2581)

    def test_reference_bodies_carry_the_accepted_identities(self):
        trace = at.reference_trace()
        self.assertEqual(trace.trace_identity(), at.REFERENCE_TRACE_IDENTITY)
        self.assertEqual(
            at.body_identity(trace.reviewer_projection()),
            at.REFERENCE_REVIEWER_IDENTITY,
        )
        self.assertEqual(
            trace.criterion_body_identity(), at.REFERENCE_CRITERION_BODY_IDENTITY
        )
        self.assertEqual(at.measure(trace.criterion_body()), 441)
        self.assertEqual(at.measure(trace.trace_body()), 1025)

    def test_the_anchor_has_zero_headroom(self):
        bounds = at.reference_trace().bounds()
        self.assertEqual(bounds["b_routine"], 2581)
        self.assertEqual(bounds["head"], 0)

    def test_labeled_token_estimate_is_reported_and_never_enforced(self):
        bounds = at.reference_trace().bounds()
        self.assertEqual(bounds["est_tokens_coder"], 36)
        self.assertEqual(bounds["est_tokens_reviewer"], 610)

    def test_a_drifted_serializer_fails_closed_at_readiness(self):
        original = at.REFERENCE_REVIEWER_IDENTITY
        try:
            at.REFERENCE_REVIEWER_IDENTITY = "sha256:" + "0" * 64
            with self.assertRaises(at.TraceRefusal) as caught:
                at.assert_conformance_anchor()
            self.assertEqual(caught.exception.code, "trace-unparseable")
        finally:
            at.REFERENCE_REVIEWER_IDENTITY = original


class BoundFunctionTests(unittest.TestCase):
    def test_reference_shape_is_the_anchor(self):
        self.assertEqual(at.b_routine(*at.REFERENCE_SHAPE), 2581)

    def test_one_added_criterion_with_one_record_costs_242_bytes(self):
        for n in range(6, 11):
            self.assertEqual(
                at.b_routine(n, n + 2, 4, 2, 1, 0, 0), 2581 + 242 * (n - 5)
            )

    def test_a_merged_origin_costs_19_bytes(self):
        self.assertEqual(at.b_routine(5, 7, 4, 2, 1, 0, 1), 2600)

    def test_every_term_is_signed_so_a_smaller_shape_is_allowed_less(self):
        self.assertEqual(at.b_routine(1, 1, 1, 1, 0, 0, 0), 708)

    def test_coder_bound_is_an_exact_equality(self):
        for n, expected in ((1, 47), (3, 95), (5, 143), (19, 479)):
            self.assertEqual(at.b_coder(n), expected)


class ShapeMatrixTests(unittest.TestCase):
    """Every row of the accepted evaluated-shape table, re-measured."""

    def assert_row(
        self,
        trace,
        *,
        shape,
        coder,
        reviewer,
        bound,
        head,
        trace_bytes,
        trace_prefix,
    ):
        bounds = trace.bounds()
        self.assertEqual(tuple(trace.shape().values()), shape)
        self.assertEqual(bounds["coder_bytes"], coder)
        self.assertEqual(bounds["reviewer_bytes"], reviewer)
        self.assertEqual(bounds["routine_bytes"], coder + reviewer)
        self.assertEqual(bounds["b_routine"], bound)
        self.assertEqual(bounds["head"], head)
        self.assertEqual(at.measure(trace.trace_body()), trace_bytes)
        self.assertTrue(
            trace.trace_identity().startswith(f"sha256:{trace_prefix}"),
            trace.trace_identity(),
        )

    def test_reference_shape(self):
        self.assert_row(
            at.reference_trace(),
            shape=(5, 7, 4, 2, 1, 0, 0),
            coder=143,
            reviewer=2438,
            bound=2581,
            head=0,
            trace_bytes=1025,
            trace_prefix="f5ae02d6",
        )

    def test_fewer_criteria_split_child(self):
        child = [r for r in RECORDS if r.startswith(("C01", "C02", "C03"))]
        trace = build(
            criteria=CRITERIA[:3],
            records=child,
            sources=[REQUIREMENTS, CONVENTIONS],
            excerpts=[REQ_002, REQ_003],
        )
        self.assert_row(
            trace,
            shape=(3, 4, 2, 2, 0, 0, 0),
            coder=95,
            reviewer=1420,
            bound=1544,
            head=29,
            trace_bytes=558,
            trace_prefix="44ac17f3",
        )
        self.assertTrue(
            trace.criterion_body_identity().startswith("sha256:6acd8a7d")
        )

    def test_multi_edge_criteria_pair_across_classes_without_conflict(self):
        added = [
            "C02|31600474ea01|spec|spec-clause sha256:90e9eb9adcea694bdbedaac7cd11c9e10f2e39c2e613d8f3d230ca46110540e7|locked 2026-08-14|1",
            f"C03|315b7c625696|standard|{CONVENTIONS}|installed Cartopian v1.6.40|1",
            "C05|ed1d221431c3|spec|spec-clause sha256:552be5d418d7cadd5cd26659308eb7806bdf0d1c9a62d625482c3286ab6f7a99|locked 2026-08-14|1",
        ]
        edges = sorted([r for r in RECORDS if not r.startswith("W|")] + added)
        trace = build(records=edges + [r for r in RECORDS if r.startswith("W|")])
        self.assert_row(
            trace,
            shape=(5, 10, 4, 2, 1, 0, 0),
            coder=143,
            reviewer=2819,
            bound=3019,
            head=57,
            trace_bytes=1401,
            trace_prefix="890647d7",
        )
        self.assertEqual(trace.conflicts(), [])

    def test_added_sources_leave_the_trace_body_untouched(self):
        trace = build(
            sources=SOURCES
            + [
                "project-management-source sha256:5ae0e8359e2c2b80c768a839e960b3f5de208c0ed7ad5077f23e5a4f70adae86",
                "project-management-source sha256:7ad9c7a4b9388fe146f0cc9a466419f279074e0a1647265162af19d9ee21ac6f",
            ]
        )
        self.assert_row(
            trace,
            shape=(5, 7, 6, 2, 1, 0, 0),
            coder=143,
            reviewer=2660,
            bound=2805,
            head=2,
            trace_bytes=1025,
            trace_prefix="f5ae02d6",
        )
        self.assertFalse(trace.coverage.source_complete)

    def test_added_request_excerpts_are_uncovered_not_out_of_scope(self):
        trace = build(excerpts=EXCERPTS + [REQ_098, REQ_099])
        self.assert_row(
            trace,
            shape=(5, 7, 4, 4, 1, 0, 0),
            coder=143,
            reviewer=2624,
            bound=2769,
            head=2,
            trace_bytes=1025,
            trace_prefix="f5ae02d6",
        )
        self.assertEqual(trace.coverage.uncovered_requests, [REQ_098, REQ_099])

    def test_multiple_waivers_do_not_count_toward_r(self):
        trace = build(
            records=RECORDS
            + [
                f"W|{REQ_098}|background-scope|listed as background context for the shape-matrix row; governs no criterion",
                f"W|{REQ_099}|procedural-authorization|authorizes matrix measurement only; states no product behavior",
            ],
            excerpts=EXCERPTS + [REQ_098, REQ_099],
        )
        self.assert_row(
            trace,
            shape=(5, 7, 4, 2, 3, 0, 0),
            coder=143,
            reviewer=2783,
            bound=2947,
            head=21,
            trace_bytes=1025,
            trace_prefix="f5ae02d6",
        )

    def test_an_exemption_never_increases_the_routine_body(self):
        records = [r for r in RECORDS if not r.startswith("C02|31600474ea01|requirement")]
        records.insert(2, "C02|31600474ea01|none:restates-parent|-|-|1")
        trace = build(records=records)
        self.assert_row(
            trace,
            shape=(5, 7, 4, 2, 1, 0, 0),
            coder=143,
            reviewer=2356,
            bound=2581,
            head=82,
            trace_bytes=943,
            trace_prefix="f6424ec7",
        )
        # The source the replaced edge had claimed turns uncovered in the same
        # body: an exemption is not a way to make a coverage finding disappear.
        self.assertEqual(trace.coverage.uncovered_sources, [REQUIREMENTS])
        self.assertIn("C02 31600474ea01 exempt", trace.coder_projection())

    def test_a_conflict_disposition_is_inside_the_hashed_body(self):
        records = list(RECORDS)
        records.insert(
            3,
            f"C02|31600474ea01|standard|{CONVENTIONS}|installed Cartopian v1.6.40|1",
        )
        records.append(
            "X|C02|precedence|current requirements govern behavior; the "
            "conventions standard does not narrow them"
        )
        trace = build(records=records)
        self.assert_row(
            trace,
            shape=(5, 8, 4, 2, 1, 1, 0),
            coder=143,
            reviewer=2664,
            bound=2841,
            head=34,
            trace_bytes=1250,
            trace_prefix="ee4f6a41",
        )

    def test_a_merged_origin_costs_nineteen_bytes_and_no_coder_line(self):
        origin = (
            "Request drift is visible to the reviewer and absent from raw coder "
            "context."
        )
        self.assertEqual(at.digest12(origin), "33022659f26a")
        trace = build(records=RECORDS + ["O|C03|33022659f26a"], task=[origin])
        self.assert_row(
            trace,
            shape=(5, 7, 4, 2, 1, 0, 1),
            coder=143,
            reviewer=2457,
            bound=2600,
            head=0,
            trace_bytes=1044,
            trace_prefix="0e4c5387",
        )
        self.assertNotIn("33022659f26a", trace.coder_projection())
        self.assertIn("O|C03|33022659f26a", trace.reviewer_projection())

    def test_wide_records_at_minimum_shape_exceed_their_bound(self):
        record = (
            f"C01|50ee216fd65a|requirement|{PM_PLAN}|operator-approved "
            "three-track Phase 05 decomposition as of 2026-08-14|999"
        )
        with self.assertRaises(at.TraceRefusal) as caught:
            build(
                criteria=CRITERIA[:1],
                records=[record],
                sources=[PM_PLAN],
                excerpts=[REQ_003],
            )
        self.assertEqual(caught.exception.code, "bound-exceeded")
        unbounded = build(
            criteria=CRITERIA[:1],
            records=[record],
            sources=[PM_PLAN],
            excerpts=[REQ_003],
            enforce_bounds=False,
        )
        self.assert_row(
            unbounded,
            shape=(1, 1, 1, 1, 0, 0, 0),
            coder=47,
            reviewer=798,
            bound=708,
            head=-137,
            trace_bytes=201,
            trace_prefix="1bc8227e",
        )


class SeededFixtureTests(unittest.TestCase):
    """F1–F5: the closed sufficiency bar, and the boundary each fails at."""

    def test_f1_clean_passes_with_no_false_positive(self):
        trace = build()
        self.assertTrue(trace.coverage.criterion_complete)
        self.assertTrue(trace.coverage.source_complete)
        self.assertTrue(trace.coverage.request_complete)
        self.assertEqual(trace.conflicts(), [])
        self.assertEqual(trace.closure_findings(), [])

    def test_f1_waiver_is_load_bearing(self):
        without = build(records=[r for r in RECORDS if not r.startswith("W|")])
        codes = [f.code for f in without.closure_findings()]
        self.assertIn("request-uncovered", codes)

    def test_f2_omitted_authority_blocks_at_readiness(self):
        records = [r for r in RECORDS if not r.startswith("C04|")]
        with self.assertRaises(at.TraceRefusal) as caught:
            build(records=records)
        self.assertEqual(caught.exception.code, "trace-incomplete")
        self.assertIn("C04", caught.exception.detail)

    def test_f3_contradictory_authority_fails_at_closure(self):
        records = list(RECORDS)
        records.insert(
            3,
            f"C02|31600474ea01|standard|{CONVENTIONS}|installed Cartopian v1.6.40|1",
        )
        trace = build(records=records)
        findings = trace.closure_findings()
        self.assertEqual([f.code for f in findings], ["unresolved-source-conflict"])
        self.assertEqual(findings[0].identity, "C02")
        self.assertEqual(findings[0].boundary, "closure")

    def test_f3_cross_class_pairs_stay_correctly_silent(self):
        # C01 pairs intent with behavior and C04 pairs boundary with contract;
        # neither is a conflict, and one body exhibits both halves of the rule.
        trace = build()
        self.assertEqual(trace.conflicts(), [])

    def test_f4_request_non_coverage_is_named_by_content_identity(self):
        records = [
            r
            for r in RECORDS
            if not r.startswith("C03|315b7c625696|operator-request")
        ]
        records.insert(
            3,
            f"C03|315b7c625696|standard|{CONVENTIONS}|installed Cartopian v1.6.40|1",
        )
        trace = build(records=sorted(r for r in records if not r.startswith("W|")) + [r for r in records if r.startswith("W|")])
        findings = trace.closure_findings()
        self.assertEqual([f.code for f in findings], ["request-uncovered"])
        self.assertEqual(findings[0].identity, REQ_002)
        self.assertNotIn("00691c51", trace.trace_body())

    def f5_records(self):
        repeated = (
            f"C05|ed1d221431c3|plan-item|{PM_PLAN}|operator-approved three-track "
            "Phase 05 decomposition as of 2026-08-14|2"
        )
        edges = [r for r in RECORDS if not r.startswith("W|")]
        edges.append(repeated)
        return sorted(edges) + [r for r in RECORDS if r.startswith("W|")]

    def test_f5_repeated_occurrence_stays_observable(self):
        trace = build(records=self.f5_records(), enforce_bounds=False)
        self.assertEqual(trace.shape()["e"], 8)
        # Repetition, cross-class authority, and same-class contradiction are
        # three different facts: a repeated derivation must not trip the
        # conflict check, which keys on *distinct* source identities.
        self.assertEqual(trace.conflicts(), [])
        self.assertEqual(trace.closure_findings(), [])
        self.assertEqual(
            [r.occurrence for r in trace.records if r.ordinal == "C05"], ["1", "2"]
        )

    def test_f5_overruns_the_accepted_budget_at_the_mean_record_rate(self):
        """The repeated edge is 197 B against a 146 B mean-record allowance.

        `B_routine`'s trace-record term is the reference shape's *mean* record
        width, floored. A single above-mean record therefore consumes more
        allowance than it is granted, and F5's shape (5,8,4,2,1,0,0) overruns
        its 2,727 B bound by 51 B. The accepted contract lists F5 as passing
        and never evaluates it in the shape matrix, so the two statements do
        not agree; the bound predicate is the operator-accepted, anchored one,
        so it governs and F5 blocks at readiness. Recorded here so the
        divergence is visible rather than absorbed.
        """
        with self.assertRaises(at.TraceRefusal) as caught:
            build(records=self.f5_records())
        self.assertEqual(caught.exception.code, "bound-exceeded")
        unbounded = build(records=self.f5_records(), enforce_bounds=False)
        bounds = unbounded.bounds()
        self.assertEqual(bounds["b_routine"], 2727)
        self.assertEqual(bounds["routine_bytes"], 2778)
        self.assertEqual(bounds["head"], -51)


class DeduplicationTests(unittest.TestCase):
    def test_byte_identical_records_collapse_without_error(self):
        doubled = list(RECORDS)
        doubled.insert(1, RECORDS[0])
        trace = build(records=doubled)
        self.assertEqual(trace.shape()["e"], 7)
        self.assertEqual(trace.trace_identity(), at.REFERENCE_TRACE_IDENTITY)

    def test_out_of_order_records_fail_closed(self):
        shuffled = [RECORDS[2], RECORDS[0], RECORDS[1], *RECORDS[3:]]
        with self.assertRaises(at.TraceRefusal) as caught:
            build(records=shuffled)
        self.assertEqual(caught.exception.code, "trace-unparseable")


class ExemptionSpecimenTests(unittest.TestCase):
    """X1–X8: three positive and five negative specimens of the § 4.5 grammar."""

    TEXTS = (
        "A criterion governed by an upstream requirement.",
        "A criterion that restates its parent at finer grain.",
        "A criterion fixed by the report template.",
    )

    def test_specimen_criterion_digests(self):
        self.assertEqual(
            [at.digest12(text) for text in self.TEXTS],
            ["924ebb24d78c", "30284a7a3312", "1d18fe083156"],
        )

    def specimen_bytes(self, lines):
        return at.measure("".join(line + "\n" for line in lines))

    def test_x1_minimal_valid_exemption_is_44_bytes(self):
        self.assertEqual(
            self.specimen_bytes(["C02|30284a7a3312|none:restates-parent|-|-|1"]), 44
        )

    def test_x2_mixed_body_is_170_bytes_and_projects_at_the_same_width(self):
        lines = [
            f"C01|924ebb24d78c|requirement|{REQUIREMENTS}|active Product "
            "Refinement contract as of 2026-08-14|1",
            "C02|30284a7a3312|none:restates-parent|-|-|1",
        ]
        self.assertEqual(self.specimen_bytes(lines), 170)
        trace = at.build(
            spec_acceptance=list(self.TEXTS[:2]),
            task_acceptance=[],
            record_set=at.parse_record_set(lines),
            sources=[REQUIREMENTS],
            excerpts=[],
            enforce_bounds=False,
        )
        coder = trace.coder_projection()
        self.assertEqual(at.measure(coder), 23 + 24 * 2)
        self.assertIn("C01 924ebb24d78c traced", coder)
        self.assertIn("C02 30284a7a3312 exempt", coder)
        # The reason is a reviewer field: which upstream authority does or does
        # not govern a criterion is a governance judgment the coder cannot act on.
        self.assertNotIn("restates-parent", coder)
        self.assertIn("none:restates-parent", trace.reviewer_projection())

    def test_x3_all_three_reasons_measure_47_44_and_43(self):
        lines = [
            "C01|924ebb24d78c|none:derived-mechanical|-|-|1",
            "C02|30284a7a3312|none:restates-parent|-|-|1",
            "C03|1d18fe083156|none:template-fixed|-|-|1",
        ]
        self.assertEqual([len(line.encode()) + 1 for line in lines], [47, 44, 43])
        self.assertEqual(self.specimen_bytes(lines), 134)

    def test_x4_typed_edge_plus_exemption_is_an_exemption_conflict(self):
        lines = [
            "C02|30284a7a3312|none:restates-parent|-|-|1",
            f"C02|30284a7a3312|requirement|{REQUIREMENTS}|active Product "
            "Refinement contract as of 2026-08-14|1",
        ]
        self.assertEqual(self.specimen_bytes(lines), 170)
        with self.assertRaises(at.TraceRefusal) as caught:
            at.build(
                spec_acceptance=list(self.TEXTS[:2]),
                task_acceptance=[],
                record_set=at.parse_record_set(
                    ["C01|924ebb24d78c|none:template-fixed|-|-|1", *lines]
                ),
                sources=[],
                excerpts=[],
                enforce_bounds=False,
            )
        self.assertEqual(caught.exception.code, "exemption-conflict")

    def test_x5_reason_outside_the_closed_set_is_unknown_source_type(self):
        line = "C02|30284a7a3312|none:out-of-scope|-|-|1"
        self.assertEqual(len(line.encode()) + 1, 41)
        with self.assertRaises(at.TraceRefusal) as caught:
            at.parse_record_set([line])
        self.assertEqual(caught.exception.code, "unknown-source-type")

    def test_x6_occurrence_other_than_one_is_a_record_shape_violation(self):
        line = "C02|30284a7a3312|none:restates-parent|-|-|2"
        self.assertEqual(len(line.encode()) + 1, 44)
        with self.assertRaises(at.TraceRefusal) as caught:
            at.parse_record_set([line])
        self.assertEqual(caught.exception.code, "trace-unparseable")

    def test_x7_a_source_identity_on_an_exemption_contradicts_itself(self):
        line = f"C02|30284a7a3312|none:restates-parent|{REQUIREMENTS}|-|1"
        self.assertEqual(len(line.encode()) + 1, 85)
        with self.assertRaises(at.TraceRefusal) as caught:
            at.parse_record_set([line])
        self.assertEqual(caught.exception.code, "trace-unparseable")

    def test_x8_two_reasons_conflict_while_two_identical_ones_collapse(self):
        differing = [
            "C02|30284a7a3312|none:restates-parent|-|-|1",
            "C02|30284a7a3312|none:template-fixed|-|-|1",
        ]
        self.assertEqual(self.specimen_bytes(differing), 87)
        filler = "C01|924ebb24d78c|none:template-fixed|-|-|1"
        with self.assertRaises(at.TraceRefusal) as caught:
            at.build(
                spec_acceptance=list(self.TEXTS[:2]),
                task_acceptance=[],
                record_set=at.parse_record_set([filler, *differing]),
                sources=[],
                excerpts=[],
                enforce_bounds=False,
            )
        self.assertEqual(caught.exception.code, "exemption-conflict")
        # Had the two reasons matched, deduplication collapses them to X1.
        trace = at.build(
            spec_acceptance=list(self.TEXTS[:2]),
            task_acceptance=[],
            record_set=at.parse_record_set([filler, differing[0], differing[0]]),
            sources=[],
            excerpts=[],
            enforce_bounds=False,
        )
        self.assertEqual(trace.shape()["e"], 2)


class StructuralRefusalTests(unittest.TestCase):
    def test_field_wider_than_its_cap_names_the_field(self):
        line = (
            "C01|50ee216fd65a|requirement|" + ("x" * 98) + "|context|1"
        )
        with self.assertRaises(at.TraceRefusal) as caught:
            at.parse_record_set([line])
        self.assertEqual(caught.exception.code, "trace-unparseable")
        self.assertIn("source-identity", caught.exception.detail)

    def test_empty_field_fails_closed(self):
        with self.assertRaises(at.TraceRefusal) as caught:
            at.parse_record_set(["C01|50ee216fd65a|requirement||context|1"])
        self.assertEqual(caught.exception.code, "trace-unparseable")

    def test_unknown_source_type_fails_closed(self):
        with self.assertRaises(at.TraceRefusal) as caught:
            at.parse_record_set(
                ["C01|50ee216fd65a|rumour|somewhere|context|1"]
            )
        self.assertEqual(caught.exception.code, "unknown-source-type")

    def test_unknown_waiver_class_fails_closed(self):
        with self.assertRaises(at.TraceRefusal) as caught:
            at.parse_record_set([f"W|{REQ_001}|convenience|because"])
        self.assertEqual(caught.exception.code, "unknown-waiver-class")

    def test_criterion_digest_mismatch_detects_edited_text(self):
        edited = list(CRITERIA)
        edited[1] = edited[1] + " And one more clause."
        with self.assertRaises(at.TraceRefusal) as caught:
            build(criteria=edited)
        self.assertEqual(caught.exception.code, "criterion-digest-mismatch")

    def test_spec_self_reference_fails_closed(self):
        clause = at.full_identity(CRITERIA[0])
        records = [
            f"C01|50ee216fd65a|spec|spec-clause sha256:{clause}|locked 2026-08-14|1",
            *[r for r in RECORDS if not r.startswith("C01|")],
        ]
        with self.assertRaises(at.TraceRefusal) as caught:
            build(records=records)
        self.assertEqual(caught.exception.code, "spec-self-reference")

    def test_declared_identity_mismatch_fails_closed(self):
        records = ["Trace-identity: sha256:" + "0" * 64, *RECORDS]
        with self.assertRaises(at.TraceRefusal) as caught:
            build(records=records)
        self.assertEqual(caught.exception.code, "trace-identity-mismatch")

    def test_a_declared_identity_that_matches_is_accepted(self):
        records = [f"Trace-identity: {at.REFERENCE_TRACE_IDENTITY}", *RECORDS]
        self.assertEqual(build(records=records).trace_identity(), at.REFERENCE_TRACE_IDENTITY)

    def test_unresolved_disposition_fails_closed(self):
        with self.assertRaises(at.TraceRefusal) as caught:
            at.parse_record_set(["X|C02|unresolved|cannot settle"])
        self.assertEqual(caught.exception.code, "unresolved-source-conflict")


class UnionBarAndMergeTests(unittest.TestCase):
    """The material set is two enumerations and one mechanical merge relation."""

    SPEC = "The projection is complete and carries no governance identity."
    TASK_RESTATEMENT = "The projection is complete, and carries no governance identity."
    TASK_OTHER = "Recovery is owned by the PM and never repaired in place."

    def edge(self, ordinal, text):
        return (
            f"{ordinal}|{at.digest12(text)}|requirement|{REQUIREMENTS}|active "
            "Product Refinement contract as of 2026-08-14|1"
        )

    def test_the_union_orders_spec_items_before_task_items(self):
        trace = at.build(
            spec_acceptance=[self.SPEC],
            task_acceptance=[self.TASK_OTHER],
            record_set=at.parse_record_set(
                [self.edge("C01", self.SPEC), self.edge("C02", self.TASK_OTHER)]
            ),
            sources=[REQUIREMENTS],
            excerpts=[],
            enforce_bounds=False,
        )
        self.assertEqual([c.ordinal for c in trace.criteria], ["C01", "C02"])
        self.assertEqual(
            [c.origin_list for c in trace.criteria], ["spec", "task"]
        )

    def test_an_unclaimed_acceptance_item_is_origin_side_incomplete(self):
        with self.assertRaises(at.TraceRefusal) as caught:
            at.build(
                spec_acceptance=[self.SPEC],
                task_acceptance=[self.TASK_OTHER],
                record_set=at.parse_record_set([self.edge("C01", self.SPEC)]),
                sources=[REQUIREMENTS],
                excerpts=[],
                enforce_bounds=False,
            )
        self.assertEqual(caught.exception.code, "trace-incomplete")

    def test_a_merge_removes_the_origin_ordinal_and_records_its_digest(self):
        trace = at.build(
            spec_acceptance=[self.SPEC],
            task_acceptance=[self.TASK_RESTATEMENT],
            record_set=at.parse_record_set(
                [
                    self.edge("C01", self.SPEC),
                    f"O|C01|{at.digest12(self.TASK_RESTATEMENT)}",
                ]
            ),
            sources=[REQUIREMENTS],
            excerpts=[],
            enforce_bounds=False,
        )
        self.assertEqual([c.ordinal for c in trace.criteria], ["C01"])
        self.assertEqual(trace.shape()["o"], 1)

    def test_a_merge_may_not_run_from_a_spec_item_onto_a_task_item(self):
        with self.assertRaises(at.TraceRefusal) as caught:
            at.build(
                spec_acceptance=[self.SPEC],
                task_acceptance=[self.TASK_OTHER],
                record_set=at.parse_record_set(
                    [
                        self.edge("C01", self.SPEC),
                        self.edge("C02", self.TASK_OTHER),
                        f"O|C02|{at.digest12(self.SPEC)}",
                    ]
                ),
                sources=[REQUIREMENTS],
                excerpts=[],
                enforce_bounds=False,
            )
        self.assertEqual(caught.exception.code, "criterion-digest-mismatch")

    def test_a_merged_origin_may_not_also_carry_its_own_ordinal(self):
        with self.assertRaises(at.TraceRefusal) as caught:
            at.build(
                spec_acceptance=[self.SPEC],
                task_acceptance=[self.SPEC],
                record_set=at.parse_record_set(
                    [self.edge("C01", self.SPEC), f"O|C01|{at.digest12(self.SPEC)}"]
                ),
                sources=[REQUIREMENTS],
                excerpts=[],
                enforce_bounds=False,
            )
        self.assertEqual(caught.exception.code, "trace-unparseable")

    def test_a_merge_onto_an_exempt_criterion_is_an_exemption_conflict(self):
        with self.assertRaises(at.TraceRefusal) as caught:
            at.build(
                spec_acceptance=[self.SPEC],
                task_acceptance=[self.TASK_RESTATEMENT],
                record_set=at.parse_record_set(
                    [
                        f"C01|{at.digest12(self.SPEC)}|none:template-fixed|-|-|1",
                        f"O|C01|{at.digest12(self.TASK_RESTATEMENT)}",
                    ]
                ),
                sources=[],
                excerpts=[],
                enforce_bounds=False,
            )
        self.assertEqual(caught.exception.code, "exemption-conflict")


class PrivacyTests(unittest.TestCase):
    """Progressive disclosure: each tier carries only what its reader can act on."""

    FORBIDDEN = (
        PM_DECISION,
        PM_PLAN,
        REQUIREMENTS,
        CONVENTIONS,
        "spec-clause",
        "sha256:",
        "REQ-",
        "operator-request",
        "requirement",
        "decision",
        "plan-item",
        "procedural-authorization",
        "locked 2026",
        "installed Cartopian",
    )

    def test_the_coder_projection_carries_no_governance_identity(self):
        coder = build().coder_projection()
        for token in self.FORBIDDEN:
            self.assertNotIn(token, coder, f"coder projection leaked {token!r}")

    def test_the_coder_projection_is_complete(self):
        trace = build()
        coder = trace.coder_projection()
        for criterion in trace.criteria:
            self.assertIn(
                f"{criterion.ordinal} {criterion.digest} ", coder
            )
        self.assertEqual(len(coder.splitlines()), 1 + len(trace.criteria))

    def test_the_reviewer_projection_retains_the_full_typed_record_set(self):
        trace = build()
        reviewer = trace.reviewer_projection()
        self.assertIn("Computed-by: pm", reviewer)
        for record in trace.records:
            self.assertIn(record.line(), reviewer)
        self.assertIn(f"S|{REQUIREMENTS}|claimed:C02", reviewer)
        self.assertIn(f"R|{REQ_002}|claimed:C03", reviewer)
        self.assertIn(f"W|{REQ_001}|procedural-authorization|", reviewer)

    def test_the_hashed_body_is_recoverable_from_the_reviewer_body(self):
        trace = build(records=RECORDS + ["O|C03|33022659f26a"], task=[
            "Request drift is visible to the reviewer and absent from raw coder context."
        ])
        reviewer = trace.reviewer_projection()
        for line in trace.trace_body().splitlines():
            self.assertEqual(reviewer.count(line + "\n"), 1)

    def test_the_diagnostic_body_carries_what_routine_context_dropped(self):
        records = list(RECORDS)
        records.insert(
            3,
            f"C02|31600474ea01|standard|{CONVENTIONS}|installed Cartopian v1.6.40|1",
        )
        trace = build(records=records)
        finding = trace.closure_findings()[0]
        body = at.diagnostic_body(
            trace,
            finding,
            scopes={REQUIREMENTS: "privacy, context, compatibility"},
            statuses={REQUIREMENTS: "current"},
            rationale="both sources govern the same behavior at equal scope",
        )
        self.assertIn("Error: unresolved-source-conflict", body)
        self.assertIn("Precedence-class: behavior", body)
        self.assertIn("Source-1-scope:", body)
        self.assertIn("Edge-rationale:", body)
        self.assertIn("Recovery-owner: pm", body)


class ClosureDeterminationTests(unittest.TestCase):
    def review(self, lines, *, identity=None):
        identity = identity or at.REFERENCE_TRACE_IDENTITY
        body = "\n".join([f"Trace-identity: {identity}", *lines])
        return f"## Summary\n\ntext\n\n## Closure determinations\n\n{body}\n\n## Findings\n\n- none\n"

    def all_pass(self):
        out = []
        for index in range(1, 6):
            out.append(f"D1 C{index:02d}: pass reason:-")
            out.append(f"D2 C{index:02d}: pass reason:-")
        return out

    def test_a_complete_passing_block_clears_closure(self):
        result = at.evaluate_closure(
            at.reference_trace(), self.review(self.all_pass()), attributed_to="reviewer"
        )
        self.assertTrue(result.ok, result.blockers)

    def test_a_missing_determination_blocks_and_never_defaults_to_pass(self):
        lines = [line for line in self.all_pass() if not line.startswith("D2 C04")]
        result = at.evaluate_closure(
            at.reference_trace(), self.review(lines), attributed_to="reviewer"
        )
        self.assertFalse(result.ok)
        self.assertIn(
            "upstream-intent-uncovered", [b.code for b in result.blockers]
        )

    def test_d1_can_pass_while_d2_fails(self):
        lines = [
            line if line != "D2 C04: pass reason:-"
            else "D2 C04: fail reason:upstream-intent-uncovered"
            for line in self.all_pass()
        ]
        result = at.evaluate_closure(
            at.reference_trace(), self.review(lines), attributed_to="reviewer"
        )
        self.assertFalse(result.ok)
        codes = [b.code for b in result.blockers]
        self.assertEqual(codes, ["upstream-intent-uncovered"])

    def test_d2_can_pass_while_d1_fails(self):
        lines = [
            line if line != "D1 C02: pass reason:-"
            else "D1 C02: fail reason:acceptance-item-unmet"
            for line in self.all_pass()
        ]
        result = at.evaluate_closure(
            at.reference_trace(), self.review(lines), attributed_to="reviewer"
        )
        self.assertEqual([b.code for b in result.blockers], ["acceptance-item-unmet"])

    def test_contradictory_records_for_one_criterion_block(self):
        lines = self.all_pass() + ["D1 C01: fail reason:acceptance-item-unmet"]
        result = at.evaluate_closure(
            at.reference_trace(), self.review(lines), attributed_to="reviewer"
        )
        self.assertFalse(result.ok)
        self.assertTrue(
            any("contradictory" in b.detail for b in result.blockers), result.blockers
        )

    def test_a_reason_code_outside_its_determination_blocks(self):
        lines = [
            line if line != "D1 C03: pass reason:-"
            else "D1 C03: fail reason:upstream-intent-uncovered"
            for line in self.all_pass()
        ]
        result = at.evaluate_closure(
            at.reference_trace(), self.review(lines), attributed_to="reviewer"
        )
        self.assertFalse(result.ok)

    def test_a_stale_trace_identity_blocks_at_closure(self):
        result = at.evaluate_closure(
            at.reference_trace(),
            self.review(self.all_pass(), identity="sha256:" + "0" * 64),
            attributed_to="reviewer",
        )
        self.assertIn("trace-identity-mismatch", [b.code for b in result.blockers])

    def test_an_unattributed_determination_block_is_not_independent(self):
        result = at.evaluate_closure(
            at.reference_trace(), self.review(self.all_pass()), attributed_to="  "
        )
        self.assertFalse(result.ok)
        self.assertTrue(
            any("self-certified" in b.detail for b in result.blockers), result.blockers
        )

    def test_task_scoped_coverage_failures_must_be_recorded(self):
        trace = build(records=[r for r in RECORDS if not r.startswith("W|")])
        result = at.evaluate_closure(
            trace, self.review(self.all_pass()), attributed_to="reviewer"
        )
        self.assertIn("request-uncovered", [b.code for b in result.blockers])
        recorded = at.evaluate_closure(
            trace,
            self.review(self.all_pass() + ["D2 task: fail reason:request-uncovered"]),
            attributed_to="reviewer",
        )
        self.assertEqual(
            [b.code for b in recorded.blockers], ["request-uncovered"]
        )

    def test_completion_evidence_is_the_identity_plus_the_determination_lines(self):
        trace = at.reference_trace()
        lines = [line.replace("<pass|fail>", "pass").replace("<code|->", "-")
                 for line in trace.determination_template()]
        body = trace.completion_evidence(lines)
        self.assertEqual(at.measure(body), 308)
        self.assertTrue(body.startswith("Trace-identity: sha256:f5ae02d6"))


class CommandSurfaceTests(unittest.TestCase):
    """CLI and MCP share one handler, so parity is structural, not asserted."""

    SPEC = """# SPEC-99-001: Trace fixture

Status: locked
Profile: general
Source guidance: required

## Source guidance

### Authoritative sources

- Identity: Cartopian REQUIREMENTS.md and STANDARDS.md; Applicable context: active Product Refinement contract as of 2026-08-13; Status: current; Scope: runtime and containment constraints

### Conflict resolution

- Status: resolved; Rule: current requirements and standards govern product boundaries; Decision: implement only the bounded reviewed design

### Unverified claims

- none

## Examples / acceptance

- The fixture passes under the declared contract.
- The fixture reports its measured bodies.
- The fixture fails closed on a drifted criterion.
"""

    SPEC_ITEMS = (
        "The fixture passes under the declared contract.",
        "The fixture reports its measured bodies.",
        "The fixture fails closed on a drifted criterion.",
    )
    TASK_ITEMS = ("The fixture records its own outcome.",)

    def task(self, trace_block, *, declaration="required"):
        block = "\n".join(trace_block)
        return f"""# TASK-99-001: Trace fixture

Phase: PHASE-99
Plan ref: BUILD-99-001
Work root: n/a
Deliverable: n/a
Spec: SPEC-99-001.md
Evidence gate: n/a
Source guidance: spec
Upstream trace: {declaration}

## Goal

Fixture.

## Acceptance

- [ ] The fixture records its own outcome.

## Upstream trace

```trace
{block}
```
"""

    def scaffold(self, trace_block, **kwargs):
        scaffold = project_scaffold()
        self.addCleanup(scaffold.cleanup)
        scaffold.write("specs/SPEC-99-001.md", self.SPEC)
        scaffold.capture_request(
            request_id="REQUEST-001",
            unit="task:TASK-99-001",
            text="Build the trace fixture.",
        )
        task = scaffold.write(
            "tasks/open/TASK-99-001.md", self.task(trace_block, **kwargs)
        )
        return scaffold, task

    def run_command(self, root, task, projection=None):
        args = argparse.Namespace(
            project_root=str(root),
            task=str(task),
            projection=projection,
            anchor=False,
        )
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = command.handler(args)
        payload = out.getvalue().strip()
        return code, json.loads(payload) if payload else None, err.getvalue()

    def valid_block(self, root):
        request = json.loads(
            (root / "requests" / "REQUEST-001.json").read_text(encoding="utf-8")
        )
        identity = request["content_identity"]
        lines = [
            f"C{index:02d}|{at.digest12(text)}|requirement|{REQUIREMENTS}|active "
            "Product Refinement contract as of 2026-08-13|1"
            for index, text in enumerate(self.SPEC_ITEMS, start=1)
        ]
        lines.append(
            f"C04|{at.digest12(self.TASK_ITEMS[0])}|operator-request|"
            f"REQ-001 {identity}|evidence order 1, observed 2026-07-27|1"
        )
        return sorted(lines)

    def test_a_valid_trace_projects_both_bodies(self):
        scaffold, task = self.scaffold([])
        block = self.valid_block(scaffold.project_root)
        scaffold.write("tasks/open/TASK-99-001.md", self.task(block))
        code, record, _ = self.run_command(
            scaffold.project_root, task, projection="coder"
        )
        self.assertEqual(code, 0, record)
        self.assertTrue(record["ok"])
        self.assertEqual(record["trace"]["criterion_coverage"], "complete")
        self.assertEqual(record["projection"]["bytes"], at.b_coder(4))
        self.assertEqual(len(record["trace"]["criteria"]), 4)
        self.assertEqual(
            [c["origin_list"] for c in record["trace"]["criteria"]],
            ["spec", "spec", "spec", "task"],
        )
        self.assertIn("trace ", record["projection"]["body"])

        code, reviewer, _ = self.run_command(
            scaffold.project_root, task, projection="reviewer"
        )
        self.assertEqual(code, 0)
        self.assertIn("Computed-by: pm", reviewer["projection"]["body"])
        self.assertIn(REQUIREMENTS, reviewer["projection"]["body"])

    def test_an_undeclared_task_is_read_not_enforced(self):
        scaffold, task = self.scaffold([], declaration="n/a")
        scaffold.write(
            "tasks/open/TASK-99-001.md",
            self.task([], declaration="n/a").replace(
                "## Upstream trace\n\n```trace\n\n```\n", ""
            ),
        )
        code, record, _ = self.run_command(scaffold.project_root, task)
        self.assertEqual(code, 0)
        self.assertEqual(record["declaration"], "n/a")
        self.assertIsNone(record.get("trace"))

    def test_a_missing_record_block_blocks_at_readiness(self):
        scaffold, task = self.scaffold([])
        scaffold.write(
            "tasks/open/TASK-99-001.md",
            self.task([]).replace("## Upstream trace\n\n```trace\n\n```\n", ""),
        )
        code, record, err = self.run_command(scaffold.project_root, task)
        self.assertEqual(code, 1)
        self.assertEqual(record["refusal"]["code"], "trace-missing")
        self.assertIn("trace-missing", err)

    def test_an_omitted_criterion_blocks_despite_a_parseable_body(self):
        scaffold, task = self.scaffold([])
        block = self.valid_block(scaffold.project_root)[:1]
        scaffold.write("tasks/open/TASK-99-001.md", self.task(block))
        code, record, err = self.run_command(scaffold.project_root, task)
        self.assertEqual(code, 1)
        self.assertEqual(record["refusal"]["code"], "trace-incomplete")

    def test_the_anchor_mode_reports_the_accepted_measurement(self):
        args = argparse.Namespace(
            project_root="/", task=None, projection=None, anchor=True
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = command.handler(args)
        self.assertEqual(code, 0)
        record = json.loads(out.getvalue().strip())
        self.assertTrue(record["conforms"])
        self.assertEqual(record["routine_bytes"], 2581)


class BindingSectionTests(unittest.TestCase):
    def test_upsert_replaces_one_section_and_leaves_the_rest(self):
        text = "# Title\n\n## A\n\nalpha\n\n## B\n\nbeta\n"
        updated = trace_binding.upsert_section(text, "## A", "## A\n\nnew\n")
        self.assertIn("## A\n\nnew\n", updated)
        self.assertIn("## B\n\nbeta\n", updated)
        self.assertEqual(updated.count("## A"), 1)

    def test_upsert_appends_when_the_section_is_absent(self):
        text = "# Title\n\n## B\n\nbeta\n"
        updated = trace_binding.upsert_section(text, "## A", "## A\n\nnew\n")
        self.assertTrue(updated.endswith("## A\n\nnew\n"))

    def test_the_coder_section_is_the_projection_and_nothing_else(self):
        section = trace_binding.coder_section(at.reference_trace())
        self.assertIn("```trace-projection", section)
        for token in (PM_PLAN, REQUIREMENTS, "sha256:"):
            self.assertNotIn(token, section.split("```trace-projection")[1])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
