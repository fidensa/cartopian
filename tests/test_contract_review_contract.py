"""Separated contract review: the audit that precedes implementation review.

The accepted contract adds one step to the existing task-closure review and
nothing else — no artifact, handoff, role, panel, ledger, budget, or ceremony.
These tests hold that line: they check the seven checks, the two outcomes, the
`C<n>`/`F<n>` separation, and the placement rule, and they check that nothing
here scores the rubric.
"""
import unittest

from cli import contract_review as cq

REVIEW_HEAD = """# REVIEW-99-001

Target: TASK-99-001
Reviewer: independent reviewer
Verdict: {verdict}

## Summary

Two lines.

## Request comparison

Aligned.

"""

REVIEW_TAIL = """
## Implementation evidence

- Commit SHA — abc123

## Findings

{findings}

## Suggested actions

- none
"""


def review(section, *, verdict="approve", findings="- none"):
    return (
        REVIEW_HEAD.format(verdict=verdict)
        + section
        + REVIEW_TAIL.format(findings=findings)
    )


class RubricTests(unittest.TestCase):
    def test_the_check_set_is_closed_at_seven(self):
        self.assertEqual(len(cq.CHECKS), 7)
        self.assertEqual(len(set(cq.CHECK_CODES)), 7)

    def test_the_outcome_set_is_closed_at_two(self):
        self.assertEqual(cq.OUTCOMES, ("adequate", "needs changes"))

    def test_severity_is_the_reviews_existing_vocabulary(self):
        self.assertEqual(cq.SEVERITIES, ("blocker", "major", "minor", "nit"))

    def test_upstream_alignment_is_the_check_the_ledger_routes_to_omr(self):
        self.assertIn(cq.UPSTREAM_ALIGNMENT, cq.CHECK_CODES)


class ParsingTests(unittest.TestCase):
    def test_a_clean_contract_is_one_line(self):
        parsed = cq.evaluate(review("## Contract quality\n\nOutcome: adequate\n"))
        self.assertTrue(parsed.ok, parsed.violations)
        self.assertEqual(parsed.outcome, "adequate")
        self.assertEqual(parsed.gaps, [])

    def test_an_omitted_upstream_requirement_reads_needs_changes(self):
        section = (
            "## Contract quality\n\nOutcome: needs changes\n\n"
            "- C1. [major] Upstream alignment — the atomic-write-on-interrupt "
            "standard applies and is reached by no acceptance item.\n"
        )
        parsed = cq.evaluate(review(section))
        self.assertTrue(parsed.ok, parsed.violations)
        self.assertEqual(len(parsed.gaps), 1)
        gap = parsed.gaps[0]
        self.assertEqual(gap.ordinal, "C1")
        self.assertEqual(gap.severity, "major")
        self.assertEqual(gap.check, "upstream-alignment")
        self.assertIn("atomic-write", gap.detail)

    def test_an_internal_contradiction_is_a_blocker_gap(self):
        section = (
            "## Contract quality\n\nOutcome: needs changes\n\n"
            "- C1. [blocker] Internal coherence — `## Non-goals` contradicts "
            "`## Interface` and acceptance item 3.\n"
        )
        parsed = cq.evaluate(review(section))
        self.assertTrue(parsed.ok, parsed.violations)
        self.assertEqual(parsed.gaps[0].check, "internal-coherence")

    def test_the_kebab_spelling_the_ledger_uses_is_accepted(self):
        section = (
            "## Contract quality\n\nOutcome: needs changes\n\n"
            "- C1. [minor] factual-and-source-accuracy — the decision is cited "
            "with the wrong date.\n"
        )
        parsed = cq.evaluate(review(section))
        self.assertEqual(parsed.gaps[0].check, "factual-and-source-accuracy")

    def test_nits_do_not_force_needs_changes(self):
        section = (
            "## Contract quality\n\nOutcome: adequate\n\n"
            "- C1. [nit] Acceptance clarity — item 2 reads slightly long.\n"
        )
        parsed = cq.evaluate(review(section))
        self.assertTrue(parsed.ok, parsed.violations)


class FailClosedTests(unittest.TestCase):
    def rules(self, text):
        return {v["rule"] for v in cq.evaluate(text).violations}

    def test_a_missing_section_is_a_violation(self):
        text = review("")
        self.assertIn("contract-quality-missing", self.rules(text))

    def test_an_unset_outcome_placeholder_is_not_an_outcome(self):
        text = review("## Contract quality\n\nOutcome: adequate | needs changes\n")
        self.assertIn("contract-quality-outcome-unset", self.rules(text))

    def test_an_outcome_outside_the_closed_set_is_a_violation(self):
        text = review("## Contract quality\n\nOutcome: mostly fine\n")
        self.assertIn("contract-quality-outcome-invalid", self.rules(text))

    def test_adequate_with_a_blocking_gap_is_incoherent(self):
        text = review(
            "## Contract quality\n\nOutcome: adequate\n\n"
            "- C1. [blocker] Testability — no observation would show it.\n"
        )
        self.assertIn("contract-quality-outcome-incoherent", self.rules(text))

    def test_needs_changes_with_no_gap_is_incoherent(self):
        text = review("## Contract quality\n\nOutcome: needs changes\n")
        self.assertIn("contract-quality-outcome-incoherent", self.rules(text))

    def test_a_gap_naming_no_check_is_a_violation(self):
        text = review(
            "## Contract quality\n\nOutcome: needs changes\n\n"
            "- C1. [major] Something feels off — somewhere in the spec.\n"
        )
        self.assertIn("contract-quality-check-unnamed", self.rules(text))

    def test_a_gap_with_no_locus_is_a_violation(self):
        text = review(
            "## Contract quality\n\nOutcome: needs changes\n\n"
            "- C1. [major] Upstream alignment\n"
        )
        self.assertIn("contract-quality-gap-unlocated", self.rules(text))

    def test_an_unknown_severity_is_a_violation(self):
        text = review(
            "## Contract quality\n\nOutcome: needs changes\n\n"
            "- C1. [critical] Upstream alignment — a requirement is unreached.\n"
        )
        self.assertIn("contract-quality-severity-invalid", self.rules(text))

    def test_a_duplicated_gap_ordinal_is_a_violation(self):
        text = review(
            "## Contract quality\n\nOutcome: needs changes\n\n"
            "- C1. [major] Upstream alignment — a requirement is unreached.\n"
            "- C1. [minor] Testability — no observation would show it.\n"
        )
        self.assertIn("contract-quality-ordinal-duplicated", self.rules(text))


class PlacementTests(unittest.TestCase):
    def test_the_audit_must_precede_implementation_evidence(self):
        misplaced = (
            REVIEW_HEAD.format(verdict="approve")
            + "## Implementation evidence\n\n- Commit SHA — abc123\n\n"
            + "## Contract quality\n\nOutcome: adequate\n\n"
            + "## Findings\n\n- none\n"
        )
        violations = cq.placement_violations(misplaced)
        self.assertEqual([v["rule"] for v in violations], ["review-order-violation"])
        self.assertIn("before implementation framing", violations[0]["detail"])

    def test_the_audit_must_follow_the_request_comparison(self):
        misplaced = (
            "# REVIEW-99-001\n\nReviewer: r\nVerdict: approve\n\n"
            "## Contract quality\n\nOutcome: adequate\n\n"
            "## Request comparison\n\nAligned.\n\n"
            "## Implementation evidence\n\n- n/a\n"
        )
        self.assertEqual(
            [v["rule"] for v in cq.placement_violations(misplaced)],
            ["review-order-violation"],
        )

    def test_the_canonical_placement_raises_nothing(self):
        text = review("## Contract quality\n\nOutcome: adequate\n")
        self.assertEqual(cq.placement_violations(text), [])

    def test_a_review_carrying_neither_anchor_is_not_correctly_ordered(self):
        # The defect this closes: "no anchor to compare against" once read as
        # "nothing out of order", so a review that placed the judgment nowhere
        # passed the placement rule.
        text = (
            "# REVIEW-99-001\n\nReviewer: r\nVerdict: approve\n\n"
            "## Contract quality\n\nOutcome: adequate\n\n"
            "## Findings\n\n- none\n"
        )
        violations = cq.placement_violations(text)
        self.assertEqual(
            [v["rule"] for v in violations],
            ["review-anchor-missing", "review-anchor-missing"],
        )
        self.assertFalse(cq.evaluate(text).ok)

    def test_each_missing_anchor_is_named_on_its_own(self):
        for missing, present in (
            (cq.PRECEDING_HEADING, cq.FOLLOWING_HEADING),
            (cq.FOLLOWING_HEADING, cq.PRECEDING_HEADING),
        ):
            with self.subTest(missing=missing):
                ordered = (
                    [present, cq.SECTION_HEADING]
                    if present == cq.PRECEDING_HEADING
                    else [cq.SECTION_HEADING, present]
                )
                text = "# REVIEW-99-001\n\nVerdict: approve\n\n" + "".join(
                    f"{heading}\n\nOutcome: adequate\n\n"
                    if heading == cq.SECTION_HEADING
                    else f"{heading}\n\nbody\n\n"
                    for heading in ordered
                )
                violations = cq.placement_violations(text)
                self.assertEqual(
                    [v["rule"] for v in violations], ["review-anchor-missing"]
                )
                self.assertIn(missing, violations[0]["detail"])

    def test_a_duplicated_anchor_is_ambiguous_placement(self):
        for heading in (cq.PRECEDING_HEADING, cq.FOLLOWING_HEADING):
            with self.subTest(heading=heading):
                text = review("## Contract quality\n\nOutcome: adequate\n")
                text += f"\n{heading}\n\nsecond copy\n"
                violations = cq.placement_violations(text)
                self.assertEqual(
                    [v["rule"] for v in violations], ["review-anchor-duplicated"]
                )
                self.assertIn(heading, violations[0]["detail"])

    def test_a_duplicated_contract_quality_section_is_ambiguous_placement(self):
        text = review(
            "## Contract quality\n\nOutcome: adequate\n\n"
            "## Contract quality\n\nOutcome: adequate\n"
        )
        self.assertEqual(
            [v["rule"] for v in cq.placement_violations(text)],
            ["review-anchor-duplicated"],
        )

    def test_reordered_anchors_are_reported_even_when_both_are_present(self):
        text = (
            "# REVIEW-99-001\n\nVerdict: approve\n\n"
            "## Implementation evidence\n\n- Commit SHA — abc123\n\n"
            "## Contract quality\n\nOutcome: adequate\n\n"
            "## Request comparison\n\nAligned.\n"
        )
        self.assertEqual(
            [v["rule"] for v in cq.placement_violations(text)],
            ["review-order-violation", "review-order-violation"],
        )

    def test_the_section_must_sit_strictly_between_the_two_anchors(self):
        text = (
            "# REVIEW-99-001\n\nVerdict: approve\n\n"
            "## Request comparison\n\nAligned.\n\n"
            "## Implementation evidence\n\n- Commit SHA — abc123\n\n"
            "## Contract quality\n\nOutcome: adequate\n"
        )
        self.assertEqual(
            [v["rule"] for v in cq.placement_violations(text)],
            ["review-order-violation"],
        )


class SeparationTests(unittest.TestCase):
    """Contract defects and implementation defects fail separately."""

    def test_findings_are_read_from_their_own_section(self):
        text = review(
            "## Contract quality\n\nOutcome: adequate\n",
            verdict="request-changes",
            findings=(
                "- F1. [blocker] — cli/x.py:10 leaks a path.\n"
                "- F2. [minor] — a stale comment."
            ),
        )
        parsed = cq.evaluate(text)
        self.assertEqual([f.ordinal for f in parsed.findings], ["F1", "F2"])
        self.assertEqual([f.severity for f in parsed.findings], ["blocker", "minor"])
        self.assertEqual(parsed.gaps, [])

    def test_the_unfilled_template_row_is_not_a_finding(self):
        text = review(
            "## Contract quality\n\nOutcome: adequate\n",
            findings=(
                "- F1. [blocker | major | minor | nit] — Description with file "
                "path and line range or section reference.\n- F2. …"
            ),
        )
        self.assertEqual(cq.evaluate(text).findings, [])

    def test_a_faithful_implementation_of_a_deficient_contract_says_both(self):
        text = review(
            "## Contract quality\n\nOutcome: needs changes\n\n"
            "- C1. [major] Completeness — the interface stops before its edge "
            "cases.\n",
            verdict="approve",
        )
        parsed = cq.evaluate(text)
        self.assertTrue(parsed.ok, parsed.violations)
        self.assertEqual(parsed.outcome, "needs changes")
        self.assertEqual(parsed.findings, [])


class NoScoreTests(unittest.TestCase):
    def test_the_parsed_record_carries_no_score_count_or_grade(self):
        text = review(
            "## Contract quality\n\nOutcome: needs changes\n\n"
            "- C1. [major] Upstream alignment — a requirement is unreached.\n"
            "- C2. [nit] Testability — wording.\n"
        )
        record = cq.evaluate(text).as_record()
        keys = set(record)
        self.assertEqual(
            keys, {"present", "outcome", "gaps", "findings", "violations", "ok"}
        )
        for forbidden in ("score", "count", "grade", "index", "rank", "total"):
            self.assertNotIn(forbidden, keys)


class TemplateTests(unittest.TestCase):
    def test_the_rendered_section_round_trips(self):
        gaps = [
            cq.Gap("C1", "major", "upstream-alignment", "a requirement is unreached")
        ]
        rendered = cq.section_template("needs changes", gaps)
        parsed = cq.parse(rendered)
        self.assertEqual(parsed.outcome, "needs changes")
        self.assertEqual(parsed.gaps, gaps)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
