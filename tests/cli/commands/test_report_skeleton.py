"""`cartopian report-skeleton` — machine-owned report boilerplate.

The skeleton must carry exactly the sections applicable to the task, and its
prefilled source-evidence rows must be the same canonical assignee projection
report validation compares against, so a mechanical fill of the skeleton is
already valid evidence.
"""
import argparse
import contextlib
import io
import json
import unittest

from cli import source_guidance
from cli.commands import report_skeleton
from cli.main import EXIT_FAIL, EXIT_OK
from tests.scaffold import project_scaffold

_TOML = (
    "[project]\n"
    'id = "skeleton-proj"\n'
    'name = "Skeleton Project"\n'
    'project_schema_version = "v0.11.0"\n'
)

_SOURCE_SECTION = (
    "## Source guidance\n\n"
    "### Authoritative sources\n\n"
    "- Identity: D2 interface contract; Applicable context: revision 2 per "
    "TASK-05-008, effective 2026-08-10; Status: current; Scope: adapter "
    "call direction\n\n"
    "### Conflict resolution\n\n"
    "- Status: none; Rule: no conflict among the sources applied to this "
    "task; Decision: n/a\n\n"
    "### Unverified claims\n\n"
    "- none\n"
)


def _task_body(
    *,
    source_owner: str = "task",
    deliverable: str = "project:resources/contracts/d3-adapter.md",
    evidence_gate: str = "required",
    with_source_section: bool = True,
) -> str:
    return (
        "# TASK-05-009: Consume upstream contract\n\n"
        "Phase: PHASE-05\nPlan ref: n/a\nWork root: n/a\nAssignee: coder\n"
        "Spec: none\nBlocked by: none\nCreated: 2026-08-02\n"
        f"Evidence gate: {evidence_gate}\n"
        f"Deliverable: {deliverable}\n"
        f"Source guidance: {source_owner}\n\n"
        "## Goal\n\nImplement against the D2 interface.\n\n"
        + (_SOURCE_SECTION + "\n" if with_source_section else "")
        + "## Acceptance\n\n- [ ] Adapter matches the D2 contract.\n"
    )


def _invoke(task_path, variant=None):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = report_skeleton.handler(
            argparse.Namespace(task_path=str(task_path), variant=variant)
        )
    return rc, out.getvalue(), err.getvalue()


class TestTaskSkeleton(unittest.TestCase):
    def test_carries_exactly_the_applicable_sections(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            task = scaffold.write(
                "tasks/in-progress/TASK-05-009.md", _task_body()
            )
            rc, stdout, stderr = _invoke(task)
            self.assertEqual(rc, EXIT_OK, stderr)
            record = json.loads(stdout)
            skeleton = record["skeleton"]
            self.assertIn("## Source evidence", skeleton)
            self.assertIn("## Deliverable content", skeleton)
            self.assertIn("## Test evidence", skeleton)
            self.assertIn("## Ready to close", skeleton)
            self.assertTrue(record["source_backed"])

    def test_inapplicable_sections_are_omitted(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            task = scaffold.write(
                "tasks/in-progress/TASK-05-009.md",
                _task_body(
                    source_owner="n/a",
                    deliverable="n/a",
                    evidence_gate="n/a",
                    with_source_section=False,
                ),
            )
            rc, stdout, stderr = _invoke(task)
            self.assertEqual(rc, EXIT_OK, stderr)
            skeleton = json.loads(stdout)["skeleton"]
            self.assertNotIn("## Source evidence", skeleton)
            self.assertNotIn("## Deliverable", skeleton)
            self.assertNotIn("## Test evidence", skeleton)
            self.assertIn("## Ready to close", skeleton)

    def test_prefilled_evidence_rows_validate_against_the_task(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            task = scaffold.write(
                "tasks/in-progress/TASK-05-009.md", _task_body()
            )
            rc, stdout, stderr = _invoke(task)
            self.assertEqual(rc, EXIT_OK, stderr)
            skeleton = json.loads(stdout)["skeleton"]
            # The scrubbed projection is what the assignee sees; identifiers
            # never leak into the skeleton.
            self.assertNotIn("TASK-05-008", skeleton)
            evidence_section = skeleton[skeleton.index("## Source evidence"):]
            evidence_section = evidence_section[
                : evidence_section.index("## Deliverable")
            ]
            report = "Status: complete\n\n" + evidence_section
            result = source_guidance.resolve_report_evidence(task, report)
            self.assertEqual(result["outcome"], "valid", result["blockers"])

    def test_context_marker_only_inside_identifier_fails_before_skeleton(self) -> None:
        """Regression: a context whose only date/version marker sits inside a
        PM identifier projects without one, so the machine-generated evidence
        row could never validate. Skeleton generation fails closed with the
        owner-side recovery instead of emitting an unvalidatable row."""
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            task = scaffold.write(
                "tasks/in-progress/TASK-05-009.md",
                _task_body().replace(
                    "Applicable context: revision 2 per "
                    "TASK-05-008, effective 2026-08-10",
                    "Applicable context: per TASK-05-008",
                ),
            )
            rc, _stdout, stderr = _invoke(task)
            self.assertEqual(rc, EXIT_FAIL)
            self.assertIn("missing-applicable-context", stderr)
            self.assertIn("project-management identifier", stderr)

    def test_invalid_source_guidance_fails_closed(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            task = scaffold.write(
                "tasks/in-progress/TASK-05-009.md",
                _task_body(with_source_section=False),
            )
            rc, _stdout, stderr = _invoke(task)
            self.assertEqual(rc, EXIT_FAIL)
            self.assertIn("missing-source-guidance-section", stderr)


_TOML_REVIEW_REQUIRED = (
    _TOML
    + "\n[roles.reviewer]\n"
    'description = "Reviews completed work."\n'
    "\n[reviews]\n"
    'planning = "off"\n'
    'task_closure = "required"\n'
    'task_role = "reviewer"\n'
)


class TestReadinessSemantics(unittest.TestCase):
    def test_review_required_project_gets_ready_for_review_heading(self) -> None:
        """The producer declares readiness *for review*, never closure.

        Under required task-closure review the skeleton uses the
        `## Ready for review` heading and states that `yes` routes into the
        independent review without approving closure, so a producer whose
        work is complete knows which value to write.
        """
        with project_scaffold(cartopian_toml=_TOML_REVIEW_REQUIRED) as scaffold:
            task = scaffold.write(
                "tasks/in-progress/TASK-05-009.md", _task_body()
            )
            rc, stdout, stderr = _invoke(task)
            self.assertEqual(rc, EXIT_OK, stderr)
            record = json.loads(stdout)
            skeleton = record["skeleton"]
            self.assertIn("## Ready for review", skeleton)
            self.assertNotIn("## Ready to close", skeleton)
            self.assertIn("does not approve closure", skeleton)
            self.assertIn("incomplete or blocked", skeleton)
            self.assertNotIn("certify", skeleton.split("does not")[0])
            self.assertTrue(record["machine_fields"]["task_review_required"])
            self.assertEqual(
                record["machine_fields"]["ready_heading"], "Ready for review"
            )

    def test_review_off_project_keeps_ready_to_close_heading(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            task = scaffold.write(
                "tasks/in-progress/TASK-05-009.md", _task_body()
            )
            rc, stdout, stderr = _invoke(task)
            self.assertEqual(rc, EXIT_OK, stderr)
            record = json.loads(stdout)
            self.assertIn("## Ready to close", record["skeleton"])
            self.assertNotIn("## Ready for review", record["skeleton"])
            self.assertFalse(record["machine_fields"]["task_review_required"])


class TestUnverifiedClaimGrammar(unittest.TestCase):
    def test_skeleton_exposes_the_exact_non_empty_claim_grammar(self) -> None:
        """The non-empty grammar is discoverable before report writing."""
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            task = scaffold.write(
                "tasks/in-progress/TASK-05-009.md", _task_body()
            )
            rc, stdout, stderr = _invoke(task)
            self.assertEqual(rc, EXIT_OK, stderr)
            skeleton = json.loads(stdout)["skeleton"]
            self.assertIn(
                "`- Claim: <unverified claim>; Decisiveness: <decisive | "
                "non-decisive>; Missing: <authority or evidence>; "
                "Consequence: <consequence of proceeding>; Next: <decision "
                "or proof required>`",
                skeleton,
            )
            # `- none` stays the valid default row.
            claims = skeleton[skeleton.index("### Unverified claims"):]
            self.assertIn("- none", claims)

    def test_instructional_grammar_text_is_not_parsed_as_evidence(self) -> None:
        """The exemplar is prose, never a `- ` row a validator would consume."""
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            task = scaffold.write(
                "tasks/in-progress/TASK-05-009.md", _task_body()
            )
            rc, stdout, stderr = _invoke(task)
            self.assertEqual(rc, EXIT_OK, stderr)
            skeleton = json.loads(stdout)["skeleton"]
            evidence_section = skeleton[skeleton.index("## Source evidence"):]
            evidence_section = evidence_section[
                : evidence_section.index("## Deliverable")
            ]
            # Instructions left in place with `- none` still validate: the
            # grammar exemplar is not misparsed as a malformed claim record.
            report = "Status: complete\n\n" + evidence_section
            result = source_guidance.resolve_report_evidence(task, report)
            self.assertEqual(result["outcome"], "valid", result["blockers"])
            self.assertEqual(result["evidence"]["unverified_claims"], [])

    def test_grammar_conformant_non_decisive_claim_validates(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            task = scaffold.write(
                "tasks/in-progress/TASK-05-009.md", _task_body()
            )
            rc, stdout, stderr = _invoke(task)
            self.assertEqual(rc, EXIT_OK, stderr)
            skeleton = json.loads(stdout)["skeleton"]
            evidence_section = skeleton[skeleton.index("## Source evidence"):]
            evidence_section = evidence_section[
                : evidence_section.index("## Deliverable")
            ]
            filled = evidence_section.replace(
                "- none",
                "- Claim: the revised wording states the boundary at the "
                "right width; Decisiveness: non-decisive; Missing: "
                "independent review; Consequence: possible misstatement; "
                "Next: reviewer verdict on the revised artifact.",
            )
            report = "Status: complete\n\n" + filled
            result = source_guidance.resolve_report_evidence(task, report)
            self.assertEqual(result["outcome"], "valid", result["blockers"])
            self.assertEqual(len(result["evidence"]["unverified_claims"]), 1)


class TestReviewSkeleton(unittest.TestCase):
    def test_machine_identity_values_are_exact(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            task = scaffold.write(
                "tasks/in-review/TASK-05-009.md", _task_body()
            )
            rc, stdout, stderr = _invoke(task)
            self.assertEqual(rc, EXIT_OK, stderr)
            record = json.loads(stdout)
            self.assertEqual(record["variant"], "review")
            fields = record["machine_fields"]
            self.assertEqual(fields["review_id"], "REVIEW-05-009")
            self.assertEqual(
                fields["prompt_path"],
                str((scaffold.prompts / "PROMPT-05-009.md").resolve()),
            )
            self.assertEqual(fields["task_path"], str(task.resolve()))
            self.assertEqual(
                fields["review_path"],
                str((scaffold.reviews / "REVIEW-05-009.md").resolve()),
            )
            self.assertIn("- Review ID: REVIEW-05-009", record["skeleton"])
            self.assertIn(
                "Target: TASK-05-009", record["review_file_skeleton"]
            )
            self.assertEqual(
                record["expected_report_path"],
                str((scaffold.reports / "REPORT-05-009-review.md").resolve()),
            )


if __name__ == "__main__":
    unittest.main()
