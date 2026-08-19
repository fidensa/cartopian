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
    'project_schema_version = "v0.10.0"\n'
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

    def test_invalid_source_guidance_fails_closed(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            task = scaffold.write(
                "tasks/in-progress/TASK-05-009.md",
                _task_body(with_source_section=False),
            )
            rc, _stdout, stderr = _invoke(task)
            self.assertEqual(rc, EXIT_FAIL)
            self.assertIn("missing-source-guidance-section", stderr)


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
