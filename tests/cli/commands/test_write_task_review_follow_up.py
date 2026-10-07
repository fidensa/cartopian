"""`write-task` refuses follow-up tasks spawned from a failed review.

CONVENTIONS § Status Through Directory: a failed task-closure review returns
the original task for rework, which stays the unit of work, so failed reviews
do not spawn replacement or follow-up tasks. RED: nothing enforced it — a PM
could mint a corrective task for a ``request-changes`` finding while the
reviewed task was still live. GREEN: ``write-task`` refuses a write that newly
cites a retained failed review of a different, not-yet-done task, and leaves
every legitimate write alone.
"""
from tests.cli.commands.test_write_task_uniqueness import _Fixture, run_cli

_FAILED_REVIEW = (
    "# REVIEW-01-001\n\nTarget: TASK-01-001\nVerdict: {verdict}\n\n"
    "## Findings\n\n- F1. [major] The export drops the trailing row.\n"
)


def _body(extra: str = "") -> str:
    return (
        "# Fix export\n\nEvidence gate: n/a\n\n"
        f"## Goal\n\nFix the export.{extra}\n\n"
        "## Acceptance\n\n- [ ] done\n"
    )


class TestReviewFollowUpGuard(_Fixture):
    def setUp(self):
        super().setUp()
        self.target = self.scaffold.write(
            "tasks/in-progress/TASK-01-001.md", _body()
        )
        self.review = self.scaffold.write(
            "reviews/REVIEW-01-001.md", _FAILED_REVIEW.format(verdict="request-changes")
        )

    def write(self, task_id, body):
        return run_cli("write-task", self.root, "--task-id", task_id, "--content", body)

    def test_new_task_for_a_live_failed_review_is_refused(self):
        for verdict in ("request-changes", "reject"):
            with self.subTest(verdict=verdict):
                self.review.write_text(_FAILED_REVIEW.format(verdict=verdict))
                code, records, err = self.write(
                    "TASK-01-002", _body(" Addresses REVIEW-01-001 F1.")
                )
                self.assertEqual(code, 1)
                self.assertEqual(records, [])
                self.assertIn("review-follow-up-task", err)
                self.assertIn("TASK-01-001", err)
                self.assertIn("tasks/in-progress/", err)
                self.assertEqual(self.all_task_files(), ["in-progress/TASK-01-001.md"])

    def test_target_task_can_record_its_own_review(self):
        code, _records, err = self.write(
            "TASK-01-001", _body(" Rework for REVIEW-01-001 F1.")
        )
        self.assertEqual(code, 0, err)

    def test_existing_citation_does_not_block_unrelated_revision(self):
        cited = _body(" See REVIEW-01-001.")
        self.scaffold.write("tasks/open/TASK-01-002.md", cited)
        code, _records, err = self.write("TASK-01-002", cited.replace("Fix export", "Fix export v2"))
        self.assertEqual(code, 0, err)

    def test_approved_review_and_done_target_do_not_block(self):
        self.review.write_text(_FAILED_REVIEW.format(verdict="approve"))
        code, _records, err = self.write("TASK-01-002", _body(" Follows REVIEW-01-001."))
        self.assertEqual(code, 0, err)

        self.review.write_text(_FAILED_REVIEW.format(verdict="request-changes"))
        self.target.rename(self.scaffold.tasks_done / "TASK-01-001.md")
        code, _records, err = self.write("TASK-01-003", _body(" Follows REVIEW-01-001."))
        self.assertEqual(code, 0, err)

    def test_missing_review_and_planning_review_do_not_block(self):
        code, _records, err = self.write("TASK-01-002", _body(" Cites REVIEW-01-009."))
        self.assertEqual(code, 0, err)
        code, _records, err = self.write(
            "TASK-01-003", _body(" Cites REVIEW-PLAN-BUILD-01-001.")
        )
        self.assertEqual(code, 0, err)
