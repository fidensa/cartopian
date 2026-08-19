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


def _invoke(report_path, variant=None):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = validate_report.handler(
            argparse.Namespace(report_path=str(report_path), variant=variant)
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
