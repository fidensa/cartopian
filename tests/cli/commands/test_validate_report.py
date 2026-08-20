"""`cartopian validate-report` — draft validation with routed corrections.

Instead of collapsing to ``failed-to-parse``, every defect is enumerated
with an actionable recovery and a failure class (mechanical, missing-input,
substantive) so a schema defect becomes a bounded correction rather than a
full rework loop.
"""
import argparse
import contextlib
import io
import json
import unittest

from cli.commands import validate_report
from cli.main import EXIT_FAIL, EXIT_OK
from tests.scaffold import project_scaffold

_TOML = (
    "[project]\n"
    'id = "validate-proj"\n'
    'name = "Validate Project"\n'
    'project_schema_version = "v0.10.0"\n'
)

_TASK = (
    "# TASK-05-009: Consume upstream contract\n\n"
    "Phase: PHASE-05\nPlan ref: n/a\nWork root: n/a\nAssignee: coder\n"
    "Spec: none\nBlocked by: none\nCreated: 2026-08-02\n"
    "Evidence gate: n/a\nDeliverable: n/a\nSource guidance: task\n\n"
    "## Goal\n\nImplement against the D2 interface.\n\n"
    "## Source guidance\n\n"
    "### Authoritative sources\n\n"
    "- Identity: D2 interface contract; Applicable context: revision 2, "
    "effective 2026-08-10; Status: current; Scope: adapter call direction\n\n"
    "### Conflict resolution\n\n"
    "- Status: none; Rule: no conflict among the sources applied to this "
    "task; Decision: n/a\n\n"
    "### Unverified claims\n\n"
    "- none\n\n"
    "## Acceptance\n\n- [ ] Adapter matches the D2 contract.\n"
)

_GOOD_REPORT = (
    "Status: complete\n\n"
    "## Identity\n\n- Work root: n/a\n\n"
    "## Completion evidence\n\nAdapter verified against the contract.\n\n"
    "## Source evidence\n\n"
    "### Authoritative sources\n\n"
    "- Identity: D2 interface contract; Applicable context: revision 2, "
    "effective 2026-08-10; Status: current; Scope: adapter call direction\n\n"
    "### Conflict resolution\n\n"
    "- Status: none; Rule: no conflict among the sources applied to this "
    "task; Decision: n/a\n\n"
    "### Unverified claims\n\n"
    "- none\n\n"
    "## Remaining risks\n\nnone.\n\n"
    "## Ready to close\n\nyes\n"
)


def _invoke(report_path, variant=None, expected_identity=None):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = validate_report.handler(
            argparse.Namespace(
                report_path=str(report_path),
                variant=variant,
                expected_identity=expected_identity,
            )
        )
    return rc, out.getvalue(), err.getvalue()


def _failed_checks(stdout: str) -> dict:
    record = json.loads(stdout)
    return {
        item["name"]: item for item in record["checks"] if not item["pass"]
    }


class TestTaskReportValidation(unittest.TestCase):
    def test_valid_report_passes_every_check(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-009.md", _TASK)
            report = scaffold.write("reports/REPORT-05-009.md", _GOOD_REPORT)
            rc, stdout, stderr = _invoke(report)
            self.assertEqual(rc, EXIT_OK, stderr)
            self.assertTrue(json.loads(stdout)["ok"])

    def test_mechanical_defects_are_enumerated_with_recovery(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-009.md", _TASK)
            report = scaffold.write(
                "reports/REPORT-05-009.md",
                "Status: done\n\n## Identity\n\n- Work root: n/a\n\n"
                "## Completion evidence\n\nDid the work.\n\n"
                "## Ready to close\n\nyes\n",
            )
            rc, stdout, _stderr = _invoke(report, variant="task")
            self.assertEqual(rc, EXIT_FAIL)
            failed = _failed_checks(stdout)
            self.assertIn("status-valid", failed)
            self.assertEqual(failed["status-valid"]["failure_class"], "mechanical")
            self.assertIn("required-sections-present", failed)
            self.assertIn(
                "Remaining risks", failed["required-sections-present"]["reason"]
            )
            for item in failed.values():
                self.assertTrue(item["recovery"])

    def test_evidence_outside_guidance_is_a_named_failure(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-009.md", _TASK)
            report = scaffold.write(
                "reports/REPORT-05-009.md",
                _GOOD_REPORT.replace(
                    "revision 2, effective 2026-08-10",
                    "revision 3, effective 2026-08-12",
                ),
            )
            rc, stdout, _stderr = _invoke(report)
            self.assertEqual(rc, EXIT_FAIL)
            failed = _failed_checks(stdout)
            self.assertIn(
                "source-evidence:source-evidence-not-in-guidance", failed
            )


class TestUnverifiedClaimValidation(unittest.TestCase):
    """The exact non-empty claim grammar, exercised case by case."""

    def _validate(self, claims_block: str):
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-009.md", _TASK)
            report = scaffold.write(
                "reports/REPORT-05-009.md",
                _GOOD_REPORT.replace(
                    "### Unverified claims\n\n- none\n",
                    "### Unverified claims\n\n" + claims_block,
                ),
            )
            return _invoke(report)

    def test_none_is_valid(self) -> None:
        rc, stdout, stderr = self._validate("- none\n")
        self.assertEqual(rc, EXIT_OK, stderr)
        self.assertTrue(json.loads(stdout)["ok"])

    def test_valid_non_decisive_claim_passes(self) -> None:
        rc, stdout, stderr = self._validate(
            "- Claim: the naming matches upstream style; Decisiveness: "
            "non-decisive; Missing: maintainer confirmation; Consequence: "
            "a rename later; Next: ask in review.\n"
        )
        self.assertEqual(rc, EXIT_OK, stderr)
        self.assertTrue(json.loads(stdout)["ok"])

    def test_valid_decisive_claim_is_a_substantive_block(self) -> None:
        rc, stdout, _stderr = self._validate(
            "- Claim: the adapter direction is the required one; "
            "Decisiveness: decisive; Missing: the governing contract "
            "section; Consequence: wrong call direction ships; Next: "
            "obtain the contract ruling.\n"
        )
        self.assertEqual(rc, EXIT_FAIL)
        failed = _failed_checks(stdout)
        self.assertIn("source-evidence:decisive-claim-unverified", failed)
        self.assertEqual(
            failed["source-evidence:decisive-claim-unverified"]["failure_class"],
            "substantive",
        )

    def test_natural_language_bullet_is_a_mechanical_failure(self) -> None:
        """The REPORT-05-010 shape: a prose bullet instead of the grammar."""
        rc, stdout, _stderr = self._validate(
            "- The revised wording may misstate the accepted boundary and "
            "should be reviewed independently.\n"
        )
        self.assertEqual(rc, EXIT_FAIL)
        failed = _failed_checks(stdout)
        self.assertIn("source-evidence:unhandled-unverified-claim", failed)
        self.assertEqual(
            failed["source-evidence:unhandled-unverified-claim"][
                "failure_class"
            ],
            "mechanical",
        )
        self.assertTrue(
            failed["source-evidence:unhandled-unverified-claim"]["recovery"]
        )

    def test_missing_field_row_is_a_mechanical_failure(self) -> None:
        rc, stdout, _stderr = self._validate(
            "- Claim: the naming matches upstream style; Decisiveness: "
            "non-decisive; Missing: maintainer confirmation\n"
        )
        self.assertEqual(rc, EXIT_FAIL)
        failed = _failed_checks(stdout)
        self.assertIn("source-evidence:unhandled-unverified-claim", failed)
        reason = failed["source-evidence:unhandled-unverified-claim"]["reason"]
        self.assertIn("missing", reason)


class TestSourceBlockerClassification(unittest.TestCase):
    """Source-evidence blocker routing is explicit and fail-closed.

    The `mechanical` class authorizes the PM's in-place correction, so a
    blocker lands there only by explicit audit; recorded evidence states
    route to the producer/review loop and unknown codes never default in.
    """

    def _class_of(self, report_text: str, blocker_code: str) -> str:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-009.md", _TASK)
            report = scaffold.write("reports/REPORT-05-009.md", report_text)
            rc, stdout, _stderr = _invoke(report)
            self.assertEqual(rc, EXIT_FAIL)
            failed = _failed_checks(stdout)
            name = f"source-evidence:{blocker_code}"
            self.assertIn(name, failed)
            return failed[name]["failure_class"]

    def test_unresolved_conflict_is_substantive(self) -> None:
        self.assertEqual(
            self._class_of(
                _GOOD_REPORT.replace(
                    "- Status: none; Rule: no conflict among the sources "
                    "applied to this task; Decision: n/a",
                    "- Status: unresolved; Rule: the newest edition "
                    "governs; Decision: n/a",
                ),
                "unresolved-source-conflict",
            ),
            "substantive",
        )

    def test_stale_applicability_is_substantive(self) -> None:
        self.assertEqual(
            self._class_of(
                _GOOD_REPORT.replace(
                    "Status: current; Scope:", "Status: stale; Scope:"
                ),
                "stale-applicable-context",
            ),
            "substantive",
        )

    def test_out_of_guidance_source_is_substantive(self) -> None:
        self.assertEqual(
            self._class_of(
                _GOOD_REPORT.replace(
                    "revision 2, effective 2026-08-10",
                    "revision 3, effective 2026-08-12",
                ),
                "source-evidence-not-in-guidance",
            ),
            "substantive",
        )

    def test_unknown_blocker_codes_fail_closed_to_substantive(self) -> None:
        self.assertEqual(
            validate_report.source_blocker_class("some-future-blocker"),
            "substantive",
        )

    def test_every_emitted_blocker_code_is_explicitly_audited(self) -> None:
        """No source-guidance blocker code may reach classification
        unmapped: every `_blocker(` code in the authority module must have
        an explicit entry, so a future code cannot silently default."""
        import re
        from pathlib import Path

        import cli.source_guidance as source_guidance_module

        source = Path(source_guidance_module.__file__).read_text(
            encoding="utf-8"
        )
        emitted = set(re.findall(r'_blocker\(\s*\n?\s*"([a-z-]+)"', source))
        self.assertTrue(emitted)
        unmapped = emitted - set(validate_report._SOURCE_BLOCKER_CLASSES)
        self.assertFalse(
            unmapped,
            f"blocker codes without an audited failure class: {sorted(unmapped)}",
        )

    def test_claim_grammar_defects_remain_mechanical(self) -> None:
        self.assertEqual(
            validate_report.source_blocker_class("unhandled-unverified-claim"),
            "mechanical",
        )


class TestExpectedIdentityBinding(unittest.TestCase):
    def test_matching_identity_validates_and_is_echoed(self) -> None:
        from cli import report_identity

        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-009.md", _TASK)
            report = scaffold.write("reports/REPORT-05-009.md", _GOOD_REPORT)
            identity = report_identity.content_identity(_GOOD_REPORT)
            rc, stdout, stderr = _invoke(report, expected_identity=identity)
            self.assertEqual(rc, EXIT_OK, stderr)
            record = json.loads(stdout)
            self.assertTrue(record["ok"])
            self.assertEqual(record["report_content_identity"], identity)

    def test_mutated_report_fails_closed_on_identity_mismatch(self) -> None:
        """Immediate post-wait validation must observe the accepted bytes."""
        from cli import report_identity

        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-009.md", _TASK)
            report = scaffold.write("reports/REPORT-05-009.md", _GOOD_REPORT)
            accepted = report_identity.content_identity(_GOOD_REPORT)
            # The report is rewritten after the wait accepted it.
            report.write_text(
                _GOOD_REPORT.replace("Adapter verified", "Adapter mutated"),
                encoding="utf-8",
            )
            rc, stdout, stderr = _invoke(report, expected_identity=accepted)
            self.assertEqual(rc, EXIT_FAIL)
            record = json.loads(stdout)
            self.assertFalse(record["ok"])
            self.assertTrue(record["identity_mismatch"])
            self.assertEqual(record["expected_content_identity"], accepted)
            self.assertNotEqual(record["report_content_identity"], accepted)
            self.assertIn("report-identity-mismatch", stderr)

    def test_malformed_identity_is_usage_error(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/in-progress/TASK-05-009.md", _TASK)
            report = scaffold.write("reports/REPORT-05-009.md", _GOOD_REPORT)
            rc, _stdout, stderr = _invoke(
                report, expected_identity="sha256:nothex"
            )
            self.assertEqual(rc, 2)
            self.assertIn("expected sha256:", stderr)


class TestReviewReportValidation(unittest.TestCase):
    def _write_review_fixture(self, scaffold, *, review_id="REVIEW-05-009",
                              write_review_file=True):
        scaffold.write("tasks/in-review/TASK-05-009.md", _TASK)
        prompt = scaffold.write(
            "prompts/PROMPT-05-009.md", "# Prompt\n\nReview the task.\n"
        )
        if write_review_file:
            scaffold.write(
                "reviews/REVIEW-05-009.md", "# REVIEW-05-009\n\nFindings.\n"
            )
        task_path = scaffold.tasks_in_review / "TASK-05-009.md"
        review_path = scaffold.reviews / "REVIEW-05-009.md"
        return scaffold.write(
            "reports/REPORT-05-009-review.md",
            (
                "# REPORT-05-009-review\n\nStatus: complete\n\n"
                "## Identity\n\n"
                f"- Review ID: {review_id}\n"
                f"- Prompt path: {prompt.resolve()}\n"
                f"- Task path: {task_path.resolve()}\n"
                f"- Review file path: {review_path.resolve()}\n\n"
                "## Evidence reviewed\n\nCompletion report and code.\n\n"
                "## Verdict\n\napprove\n\n"
                "## Blocking findings\n\nnone.\n"
            ),
        )

    def test_valid_review_report_passes(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            report = self._write_review_fixture(scaffold)
            rc, stdout, stderr = _invoke(report)
            self.assertEqual(rc, EXIT_OK, stderr)
            self.assertTrue(json.loads(stdout)["ok"])

    def test_transcribed_identity_defects_are_named_field_by_field(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            report = self._write_review_fixture(
                scaffold, review_id="REVIEW-05-999"
            )
            rc, stdout, _stderr = _invoke(report)
            self.assertEqual(rc, EXIT_FAIL)
            failed = _failed_checks(stdout)
            self.assertIn("identity-values-aligned", failed)
            self.assertIn("Review ID", failed["identity-values-aligned"]["reason"])
            self.assertEqual(
                failed["identity-values-aligned"]["failure_class"], "mechanical"
            )

    def test_missing_review_file_is_a_named_failure(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            report = self._write_review_fixture(
                scaffold, write_review_file=False
            )
            rc, stdout, _stderr = _invoke(report)
            self.assertEqual(rc, EXIT_FAIL)
            failed = _failed_checks(stdout)
            self.assertIn("identity-values-aligned", failed)
            self.assertIn(
                "review file does not exist",
                failed["identity-values-aligned"]["reason"],
            )


if __name__ == "__main__":
    unittest.main()
