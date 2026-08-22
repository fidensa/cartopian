"""`cartopian correct-report` — hash-bound in-place mechanical correction.

The minimal containment-safe correction path: no correction handoff, no
report retransmission, no broad read grants. Stale identities, substantive
findings, missing inputs, out-of-scope edits, and non-validating corrections
all fail closed with nothing written.
"""
import argparse
import contextlib
import io
import json
import unittest

from cli import report_identity
from cli.commands import correct_report
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE
from tests.scaffold import project_scaffold

_TOML = (
    "[project]\n"
    'id = "correct-proj"\n'
    'name = "Correct Project"\n'
    'project_schema_version = "v0.11.0"\n'
)

_TASK = (
    "# TASK-05-010: Correct the evidence contract\n\n"
    "Phase: PHASE-05\nPlan ref: n/a\nWork root: n/a\nAssignee: coder\n"
    "Spec: none\nBlocked by: none\nCreated: 2026-08-02\n"
    "Evidence gate: n/a\nDeliverable: n/a\nSource guidance: task\n\n"
    "## Goal\n\nApply the bounded correction.\n\n"
    "## Source guidance\n\n"
    "### Authoritative sources\n\n"
    "- Identity: evidence contract; Applicable context: revision 2, "
    "effective 2026-08-10; Status: current; Scope: the evidence schema\n\n"
    "### Conflict resolution\n\n"
    "- Status: none; Rule: no conflict among the sources applied to this "
    "task; Decision: n/a\n\n"
    "### Unverified claims\n\n"
    "- none\n\n"
    "## Acceptance\n\n- [ ] Correction lands.\n"
)


def _report(claims_row: str, evidence: str = "The correction is applied.") -> str:
    return (
        "Status: complete\n\n"
        "## Identity\n\n- Work root: n/a\n\n"
        f"## Completion evidence\n\n{evidence}\n\n"
        "## Source evidence\n\n"
        "### Authoritative sources\n\n"
        "- Identity: evidence contract; Applicable context: revision 2, "
        "effective 2026-08-10; Status: current; Scope: the evidence schema\n\n"
        "### Conflict resolution\n\n"
        "- Status: none; Rule: no conflict among the sources applied to "
        "this task; Decision: n/a\n\n"
        "### Unverified claims\n\n"
        f"{claims_row}\n\n"
        "## Remaining risks\n\nnone.\n\n"
        "## Ready to close\n\nyes\n"
    )


# The REPORT-05-010 shape: a natural-language bullet instead of the grammar.
_MALFORMED = _report(
    "- The revised wording may misstate the boundary and should be "
    "reviewed independently."
)
_STRUCTURED = _report(
    "- Claim: the revised wording states the boundary at the right width; "
    "Decisiveness: non-decisive; Missing: independent review; Consequence: "
    "possible misstatement; Next: reviewer verdict on the revised artifact."
)


def _invoke(report_path, *, expected_identity, corrected_content,
            variant=None):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = correct_report.handler(
            argparse.Namespace(
                report_path=str(report_path),
                expected_identity=expected_identity,
                corrected_content=corrected_content,
                corrected_file=None,
                variant=variant,
            )
        )
    return rc, out.getvalue(), err.getvalue()


class TestMechanicalCorrection(unittest.TestCase):
    def test_one_line_schema_repair_needs_no_handoff(self) -> None:
        """The quarter-megabyte correction prompt becomes one bounded call."""
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-010.md", _TASK)
            report = scaffold.write("reports/REPORT-05-010.md", _MALFORMED)
            identity = report_identity.content_identity(_MALFORMED)

            rc, stdout, stderr = _invoke(
                report,
                expected_identity=identity,
                corrected_content=_STRUCTURED,
            )
            self.assertEqual(rc, EXIT_OK, stderr)
            record = json.loads(stdout)
            self.assertTrue(record["ok"])
            self.assertEqual(record["previous_content_identity"], identity)
            self.assertEqual(
                record["report_content_identity"],
                report_identity.content_identity(_STRUCTURED),
            )
            self.assertEqual(
                record["corrected_regions"],
                ["Source evidence/Unverified claims row 1"],
            )
            self.assertIn(
                "source-evidence:unhandled-unverified-claim",
                record["resolved_checks"],
            )
            self.assertTrue(record["revalidated"])
            self.assertEqual(
                report.read_text(encoding="utf-8"), _STRUCTURED
            )

    def test_stale_identity_fails_closed(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-010.md", _TASK)
            report = scaffold.write("reports/REPORT-05-010.md", _MALFORMED)
            stale = report_identity.content_identity("some earlier bytes")

            rc, stdout, stderr = _invoke(
                report,
                expected_identity=stale,
                corrected_content=_STRUCTURED,
            )
            self.assertEqual(rc, EXIT_FAIL)
            record = json.loads(stdout)
            self.assertFalse(record["ok"])
            self.assertEqual(record["rule"], "stale-report-identity")
            self.assertIn("stale-report-identity", stderr)
            self.assertEqual(report.read_text(encoding="utf-8"), _MALFORMED)

    def test_substantive_findings_are_never_edited_into_compliance(self) -> None:
        decisive = _report(
            "- Claim: the boundary is the required one; Decisiveness: "
            "decisive; Missing: the governing ruling; Consequence: wrong "
            "boundary ships; Next: obtain the ruling."
        )
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-010.md", _TASK)
            report = scaffold.write("reports/REPORT-05-010.md", decisive)

            rc, stdout, _stderr = _invoke(
                report,
                expected_identity=report_identity.content_identity(decisive),
                corrected_content=_STRUCTURED,
            )
            self.assertEqual(rc, EXIT_FAIL)
            record = json.loads(stdout)
            self.assertEqual(record["rule"], "non-mechanical-findings-present")
            self.assertEqual(report.read_text(encoding="utf-8"), decisive)

    def test_missing_input_findings_fail_closed(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            # No task on disk matches the report filename: a missing-input
            # defect the correction surface must not paper over.
            report = scaffold.write("reports/REPORT-05-010.md", _MALFORMED)

            rc, stdout, _stderr = _invoke(
                report,
                expected_identity=report_identity.content_identity(_MALFORMED),
                corrected_content=_STRUCTURED,
            )
            self.assertEqual(rc, EXIT_FAIL)
            record = json.loads(stdout)
            self.assertEqual(record["rule"], "non-mechanical-findings-present")
            self.assertIn("missing-input", record["detail"])

    def test_report_outside_a_project_reports_slot_fails_closed(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            stray = scaffold.root / "REPORT-05-010.md"
            stray.write_text(_MALFORMED, encoding="utf-8")

            rc, stdout, _stderr = _invoke(
                stray,
                expected_identity=report_identity.content_identity(_MALFORMED),
                corrected_content=_STRUCTURED,
            )
            self.assertEqual(rc, EXIT_FAIL)
            self.assertEqual(
                json.loads(stdout)["rule"], "report-outside-project"
            )
            self.assertEqual(stray.read_text(encoding="utf-8"), _MALFORMED)

    def test_edits_outside_the_defective_region_fail_closed(self) -> None:
        """A mechanical correction may not touch substantive evidence."""
        widened = _report(
            "- Claim: the revised wording states the boundary at the right "
            "width; Decisiveness: non-decisive; Missing: independent "
            "review; Consequence: possible misstatement; Next: reviewer "
            "verdict on the revised artifact.",
            evidence="Completely rewritten completion evidence.",
        )
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-010.md", _TASK)
            report = scaffold.write("reports/REPORT-05-010.md", _MALFORMED)

            rc, stdout, _stderr = _invoke(
                report,
                expected_identity=report_identity.content_identity(_MALFORMED),
                corrected_content=widened,
            )
            self.assertEqual(rc, EXIT_FAIL)
            record = json.loads(stdout)
            self.assertEqual(record["rule"], "correction-outside-defect-scope")
            self.assertIn("Completion evidence", record["detail"])
            self.assertEqual(report.read_text(encoding="utf-8"), _MALFORMED)

    def test_claim_fix_cannot_also_alter_a_valid_source_row(self) -> None:
        """The reviewer's repro: fix the claim row, quietly change a scope.

        Authorization is per defective row, so a correction that also edits
        an unaffected authoritative-source row's Scope is refused and
        nothing is written.
        """
        widened = _STRUCTURED.replace(
            "Scope: the evidence schema", "Scope: the entire product"
        )
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-010.md", _TASK)
            report = scaffold.write("reports/REPORT-05-010.md", _MALFORMED)

            rc, stdout, _stderr = _invoke(
                report,
                expected_identity=report_identity.content_identity(_MALFORMED),
                corrected_content=widened,
            )
            self.assertEqual(rc, EXIT_FAIL)
            record = json.loads(stdout)
            self.assertEqual(
                record["rule"], "correction-outside-defect-scope"
            )
            self.assertIn("Authoritative sources", record["detail"])
            self.assertEqual(report.read_text(encoding="utf-8"), _MALFORMED)

    def test_claim_fix_cannot_also_invent_a_conflict_rule(self) -> None:
        widened = _STRUCTURED.replace(
            "Rule: no conflict among the sources applied to "
            "this task; Decision: n/a",
            "Rule: the newest source always wins; Decision: applied the "
            "newest source",
        )
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-010.md", _TASK)
            report = scaffold.write("reports/REPORT-05-010.md", _MALFORMED)

            rc, stdout, _stderr = _invoke(
                report,
                expected_identity=report_identity.content_identity(_MALFORMED),
                corrected_content=widened,
            )
            self.assertEqual(rc, EXIT_FAIL)
            record = json.loads(stdout)
            self.assertEqual(
                record["rule"], "correction-outside-defect-scope"
            )
            self.assertIn("Conflict resolution", record["detail"])
            self.assertEqual(report.read_text(encoding="utf-8"), _MALFORMED)

    def test_missing_section_cannot_gain_arbitrary_content(self) -> None:
        """A missing heading is repaired only as a body-identical rename.

        Supplying the absent section's content would put PM-authored words
        into producer evidence — that is a rework, not a correction.
        """
        without_risks = _STRUCTURED.replace(
            "## Remaining risks\n\nnone.\n\n", ""
        )
        with_invented = _STRUCTURED.replace(
            "## Remaining risks\n\nnone.\n\n",
            "## Remaining risks\n\nNo risks worth mentioning remain.\n\n",
        )
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-010.md", _TASK)
            report = scaffold.write("reports/REPORT-05-010.md", without_risks)

            rc, stdout, _stderr = _invoke(
                report,
                expected_identity=report_identity.content_identity(
                    without_risks
                ),
                corrected_content=with_invented,
            )
            self.assertEqual(rc, EXIT_FAIL)
            record = json.loads(stdout)
            self.assertEqual(
                record["rule"], "correction-outside-defect-scope"
            )
            self.assertIn("body-identical rename", record["detail"])
            self.assertEqual(
                report.read_text(encoding="utf-8"), without_risks
            )

    def test_heading_typo_repair_is_a_body_identical_rename(self) -> None:
        typoed = _STRUCTURED.replace(
            "## Remaining risks", "## Remaining Risks"
        )
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-010.md", _TASK)
            report = scaffold.write("reports/REPORT-05-010.md", typoed)

            rc, stdout, stderr = _invoke(
                report,
                expected_identity=report_identity.content_identity(typoed),
                corrected_content=_STRUCTURED,
            )
            self.assertEqual(rc, EXIT_OK, stderr)
            record = json.loads(stdout)
            self.assertTrue(record["ok"])
            self.assertEqual(record["corrected_regions"], ["Remaining risks"])
            self.assertEqual(
                report.read_text(encoding="utf-8"), _STRUCTURED
            )

    def test_rename_that_alters_the_body_is_refused(self) -> None:
        typoed = _STRUCTURED.replace(
            "## Remaining risks", "## Remaining Risks"
        )
        edited = _STRUCTURED.replace(
            "## Remaining risks\n\nnone.",
            "## Remaining risks\n\nnone worth noting.",
        )
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-010.md", _TASK)
            report = scaffold.write("reports/REPORT-05-010.md", typoed)

            rc, stdout, _stderr = _invoke(
                report,
                expected_identity=report_identity.content_identity(typoed),
                corrected_content=edited,
            )
            self.assertEqual(rc, EXIT_FAIL)
            self.assertEqual(
                json.loads(stdout)["rule"], "correction-outside-defect-scope"
            )
            self.assertEqual(report.read_text(encoding="utf-8"), typoed)

    def _review_report(self, scaffold, verdict_body: str) -> str:
        scaffold.write("tasks/in-review/TASK-05-010.md", _TASK)
        prompt = scaffold.write(
            "prompts/PROMPT-05-010.md", "# Prompt\n\nReview the task.\n"
        )
        review = scaffold.write(
            "reviews/REVIEW-05-010.md", "# REVIEW-05-010\n\nFindings.\n"
        )
        task_path = scaffold.tasks_in_review / "TASK-05-010.md"
        return (
            "# REPORT-05-010-review\n\nStatus: complete\n\n"
            "## Identity\n\n"
            "- Review ID: REVIEW-05-010\n"
            f"- Prompt path: {prompt.resolve()}\n"
            f"- Task path: {task_path.resolve()}\n"
            f"- Review file path: {review.resolve()}\n\n"
            "## Evidence reviewed\n\nCompletion report and code.\n\n"
            f"## Verdict\n\n{verdict_body}\n\n"
            "## Blocking findings\n\nnone.\n"
        )

    def test_verdict_token_fix_preserves_the_rationale(self) -> None:
        rationale = (
            "The evidence is thorough and the boundary is stated correctly."
        )
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            malformed = self._review_report(
                scaffold, f"Approve.\n\n{rationale}"
            )
            corrected = malformed.replace(
                "## Verdict\n\nApprove.", "## Verdict\n\napprove"
            )
            report = scaffold.write(
                "reports/REPORT-05-010-review.md", malformed
            )
            rc, stdout, stderr = _invoke(
                report,
                expected_identity=report_identity.content_identity(malformed),
                corrected_content=corrected,
            )
            self.assertEqual(rc, EXIT_OK, stderr)
            record = json.loads(stdout)
            self.assertTrue(record["ok"])
            self.assertEqual(record["corrected_regions"], ["Verdict"])
            self.assertIn(rationale, report.read_text(encoding="utf-8"))

    def test_verdict_rationale_may_not_change_with_the_token(self) -> None:
        """Fixing the token is mechanical; rewriting the rationale is not."""
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            malformed = self._review_report(
                scaffold, "Approve.\n\nThe evidence is thorough."
            )
            corrected = malformed.replace(
                "## Verdict\n\nApprove.\n\nThe evidence is thorough.",
                "## Verdict\n\napprove\n\nThe evidence is thorough and the "
                "reviewer had no reservations whatsoever.",
            )
            report = scaffold.write(
                "reports/REPORT-05-010-review.md", malformed
            )
            rc, stdout, _stderr = _invoke(
                report,
                expected_identity=report_identity.content_identity(malformed),
                corrected_content=corrected,
            )
            self.assertEqual(rc, EXIT_FAIL)
            record = json.loads(stdout)
            self.assertEqual(
                record["rule"], "correction-outside-defect-scope"
            )
            self.assertIn("Verdict body", record["detail"])
            self.assertEqual(report.read_text(encoding="utf-8"), malformed)

    def test_identity_repair_rejects_contradictory_duplicate_bullets(
        self,
    ) -> None:
        """Probe regression: fixing a mismatched Review ID may not leave two
        contradictory `- Review ID:` bullets in the Identity section."""
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            malformed = self._review_report(scaffold, "approve").replace(
                "- Review ID: REVIEW-05-010", "- Review ID: REVIEW-05-999"
            )
            duplicated = malformed.replace(
                "- Review ID: REVIEW-05-999",
                "- Review ID: REVIEW-05-010\n- Review ID: REVIEW-05-999",
            )
            report = scaffold.write(
                "reports/REPORT-05-010-review.md", malformed
            )
            rc, stdout, _stderr = _invoke(
                report,
                expected_identity=report_identity.content_identity(malformed),
                corrected_content=duplicated,
            )
            self.assertEqual(rc, EXIT_FAIL)
            record = json.loads(stdout)
            self.assertEqual(
                record["rule"], "correction-outside-defect-scope"
            )
            self.assertIn("duplicate", record["detail"])
            self.assertEqual(report.read_text(encoding="utf-8"), malformed)

    def test_claim_repair_cannot_reorder_unrelated_sections(self) -> None:
        """Probe regression: a claim-row fix may not also swap unrelated H2
        sections; section order and heading bytes are part of the preserved
        span, and the audit must not silently bless the reorder."""
        reordered = _STRUCTURED.replace(
            "## Identity\n\n- Work root: n/a\n\n"
            "## Completion evidence\n\nThe correction is applied.\n\n",
            "## Completion evidence\n\nThe correction is applied.\n\n"
            "## Identity\n\n- Work root: n/a\n\n",
        )
        assert reordered != _STRUCTURED
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-010.md", _TASK)
            report = scaffold.write("reports/REPORT-05-010.md", _MALFORMED)

            rc, stdout, _stderr = _invoke(
                report,
                expected_identity=report_identity.content_identity(_MALFORMED),
                corrected_content=reordered,
            )
            self.assertEqual(rc, EXIT_FAIL)
            record = json.loads(stdout)
            self.assertEqual(
                record["rule"], "correction-outside-defect-scope"
            )
            self.assertIn("section order", record["detail"])
            self.assertEqual(report.read_text(encoding="utf-8"), _MALFORMED)

    def test_identity_fix_cannot_touch_other_identity_bullets(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            malformed = self._review_report(scaffold, "approve").replace(
                "- Review ID: REVIEW-05-010", "- Review ID: REVIEW-05-999"
            )
            report = scaffold.write(
                "reports/REPORT-05-010-review.md", malformed
            )
            # Fixing the mismatched Review ID alone is allowed…
            fixed = malformed.replace(
                "- Review ID: REVIEW-05-999", "- Review ID: REVIEW-05-010"
            )
            rc, stdout, stderr = _invoke(
                report,
                expected_identity=report_identity.content_identity(malformed),
                corrected_content=fixed,
            )
            self.assertEqual(rc, EXIT_OK, stderr)
            self.assertEqual(
                json.loads(stdout)["corrected_regions"], ["Identity"]
            )

            # …but not while also rewriting an aligned bullet.
            report.write_text(malformed, encoding="utf-8")
            tampered = fixed.replace(
                "## Evidence reviewed\n\nCompletion report and code.",
                "## Evidence reviewed\n\nEverything imaginable.",
            )
            rc, stdout, _stderr = _invoke(
                report,
                expected_identity=report_identity.content_identity(malformed),
                corrected_content=tampered,
            )
            self.assertEqual(rc, EXIT_FAIL)
            self.assertEqual(
                json.loads(stdout)["rule"], "correction-outside-defect-scope"
            )

    def test_mixed_none_claims_permit_only_deleting_the_none_row(self) -> None:
        """Probe: mixed `- none` + valid claim must not open the claim row.

        The only content-free repair of a mixed claims list is deleting the
        contradictory `- none` row; every claim record stays byte-identical.
        """
        valid_claim = (
            "- Claim: the naming matches upstream style; Decisiveness: "
            "non-decisive; Missing: maintainer confirmation; Consequence: "
            "a rename later; Next: ask in review."
        )
        mixed = _report("- none\n" + valid_claim)
        repaired = _report(valid_claim)
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-010.md", _TASK)
            report = scaffold.write("reports/REPORT-05-010.md", mixed)

            rc, stdout, stderr = _invoke(
                report,
                expected_identity=report_identity.content_identity(mixed),
                corrected_content=repaired,
            )
            self.assertEqual(rc, EXIT_OK, stderr)
            record = json.loads(stdout)
            self.assertTrue(record["ok"])
            self.assertEqual(
                record["corrected_regions"],
                ["Source evidence/Unverified claims row 1 (deleted)"],
            )
            self.assertEqual(report.read_text(encoding="utf-8"), repaired)

    def test_mixed_none_repair_cannot_replace_the_valid_claim(self) -> None:
        """Probe regression: deleting `- none` must not authorize swapping
        the surviving claim record for unrelated substantive content."""
        valid_claim = (
            "- Claim: the naming matches upstream style; Decisiveness: "
            "non-decisive; Missing: maintainer confirmation; Consequence: "
            "a rename later; Next: ask in review."
        )
        unrelated_claim = (
            "- Claim: the entire architecture is sound; Decisiveness: "
            "non-decisive; Missing: nothing of note; Consequence: none; "
            "Next: none."
        )
        mixed = _report("- none\n" + valid_claim)
        tampered = _report(unrelated_claim)
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-010.md", _TASK)
            report = scaffold.write("reports/REPORT-05-010.md", mixed)

            rc, stdout, _stderr = _invoke(
                report,
                expected_identity=report_identity.content_identity(mixed),
                corrected_content=tampered,
            )
            self.assertEqual(rc, EXIT_FAIL)
            record = json.loads(stdout)
            self.assertEqual(
                record["rule"], "correction-outside-defect-scope"
            )
            self.assertEqual(report.read_text(encoding="utf-8"), mixed)

    def test_status_repair_rejects_duplicate_status_lines(self) -> None:
        """Probe regression: a Status repair is single and in place — it may
        not leave both `Status: complete` and `Status: failed` behind."""
        bad_status = _STRUCTURED.replace(
            "Status: complete", "Status: done", 1
        )
        duplicated = bad_status.replace(
            "Status: done", "Status: complete\nStatus: failed", 1
        )
        fixed = bad_status.replace("Status: done", "Status: complete", 1)
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-010.md", _TASK)
            report = scaffold.write("reports/REPORT-05-010.md", bad_status)

            rc, stdout, _stderr = _invoke(
                report,
                expected_identity=report_identity.content_identity(
                    bad_status
                ),
                corrected_content=duplicated,
            )
            self.assertEqual(rc, EXIT_FAIL)
            record = json.loads(stdout)
            self.assertEqual(
                record["rule"], "correction-outside-defect-scope"
            )
            self.assertIn("duplicate", record["detail"])
            self.assertEqual(report.read_text(encoding="utf-8"), bad_status)

            rc, stdout, stderr = _invoke(
                report,
                expected_identity=report_identity.content_identity(
                    bad_status
                ),
                corrected_content=fixed,
            )
            self.assertEqual(rc, EXIT_OK, stderr)
            self.assertEqual(
                json.loads(stdout)["corrected_regions"], ["<preamble>"]
            )
            self.assertEqual(report.read_text(encoding="utf-8"), fixed)

    def _assert_evidence_laundering_refused(self, recorded: str) -> None:
        """`recorded` carries a substantive evidence state; the "corrected"
        body is the fully clean report — writing it would erase evidence."""
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-010.md", _TASK)
            report = scaffold.write("reports/REPORT-05-010.md", recorded)

            rc, stdout, _stderr = _invoke(
                report,
                expected_identity=report_identity.content_identity(recorded),
                corrected_content=_STRUCTURED,
            )
            self.assertEqual(rc, EXIT_FAIL)
            record = json.loads(stdout)
            self.assertEqual(record["rule"], "non-mechanical-findings-present")
            self.assertIn("substantive", record["detail"])
            self.assertEqual(report.read_text(encoding="utf-8"), recorded)

    def test_unresolved_conflict_cannot_be_rewritten_to_no_conflict(
        self,
    ) -> None:
        """P1 probe: an unresolved source conflict is a recorded evidence
        state that needs the named decision — never a report edit."""
        self._assert_evidence_laundering_refused(
            _STRUCTURED.replace(
                "- Status: none; Rule: no conflict among the sources applied "
                "to this task; Decision: n/a",
                "- Status: unresolved; Rule: the newest edition governs; "
                "Decision: n/a",
            )
        )

    def test_stale_source_cannot_be_rewritten_to_current(self) -> None:
        """P1 probe: a recorded stale applicability needs a current source
        or named authority — flipping the token would falsify evidence."""
        self._assert_evidence_laundering_refused(
            _STRUCTURED.replace("Status: current; Scope:", "Status: stale; Scope:")
        )

    def test_out_of_guidance_source_cannot_be_swapped_to_governed(
        self,
    ) -> None:
        """P1 probe: an applied source outside the governing guidance is
        producer rework (or a guidance amendment) — never a PM row swap."""
        self._assert_evidence_laundering_refused(
            _STRUCTURED.replace(
                "revision 2, effective 2026-08-10",
                "revision 9, effective 2026-08-19",
            )
        )

    def test_valid_report_has_nothing_to_correct(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-010.md", _TASK)
            report = scaffold.write("reports/REPORT-05-010.md", _STRUCTURED)

            rc, stdout, _stderr = _invoke(
                report,
                expected_identity=report_identity.content_identity(
                    _STRUCTURED
                ),
                corrected_content=_STRUCTURED.replace("none.", "None."),
            )
            self.assertEqual(rc, EXIT_FAIL)
            self.assertEqual(
                json.loads(stdout)["rule"], "no-defect-to-correct"
            )

    def test_correction_must_fully_validate(self) -> None:
        still_malformed = _report(
            "- Claim: the revised wording states the boundary at the right "
            "width; Decisiveness: perhaps; Missing: independent review; "
            "Consequence: possible misstatement; Next: reviewer verdict."
        )
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-010.md", _TASK)
            report = scaffold.write("reports/REPORT-05-010.md", _MALFORMED)

            rc, stdout, _stderr = _invoke(
                report,
                expected_identity=report_identity.content_identity(_MALFORMED),
                corrected_content=still_malformed,
            )
            self.assertEqual(rc, EXIT_FAIL)
            record = json.loads(stdout)
            self.assertEqual(record["rule"], "correction-does-not-validate")
            self.assertEqual(report.read_text(encoding="utf-8"), _MALFORMED)

    def test_identical_correction_is_refused(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-010.md", _TASK)
            report = scaffold.write("reports/REPORT-05-010.md", _MALFORMED)

            rc, stdout, _stderr = _invoke(
                report,
                expected_identity=report_identity.content_identity(_MALFORMED),
                corrected_content=_MALFORMED,
            )
            self.assertEqual(rc, EXIT_FAIL)
            self.assertEqual(
                json.loads(stdout)["rule"], "correction-identical"
            )

    def test_malformed_expected_identity_is_usage(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            report = scaffold.write("reports/REPORT-05-010.md", _MALFORMED)
            rc, _stdout, stderr = _invoke(
                report,
                expected_identity="sha256:short",
                corrected_content=_STRUCTURED,
            )
            self.assertEqual(rc, EXIT_USAGE)
            self.assertIn("expected sha256:", stderr)


if __name__ == "__main__":
    unittest.main()
