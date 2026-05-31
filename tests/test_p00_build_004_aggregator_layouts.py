"""P00-BUILD-004 acceptance: aggregator-scenario scaffold helpers.

Verifies that four helper functions in tests.scaffold create the expected
filesystem layouts for aggregator test scenarios:

  - write_disagreement_layout    (filesystem-vs-STATE.md disagreement)
  - write_phase_ordering_layout  (multi-task phase ordering)
  - write_stale_prompt_layout    (stale prompt, no active task)
  - write_unresolved_report_layout (report exists, task not done, prompt not cleared)
"""
from __future__ import annotations

import unittest

from tests.scaffold import (
    project_scaffold,
    write_disagreement_layout,
    write_phase_ordering_layout,
    write_stale_prompt_layout,
    write_unresolved_report_layout,
)


class DisagreementLayoutTests(unittest.TestCase):
    def test_task_file_is_in_filesystem_actual_dir(self) -> None:
        with project_scaffold() as scaffold:
            task_path = write_disagreement_layout(
                scaffold,
                task_id="TASK-01-001",
                task_slug="orientation",
                state_claims_status="in-progress",
                filesystem_actual_status="done",
            )
            self.assertTrue(task_path.is_file())
            self.assertEqual(task_path.parent, scaffold.tasks_done)

    def test_state_md_claims_different_status(self) -> None:
        with project_scaffold() as scaffold:
            write_disagreement_layout(
                scaffold,
                task_id="TASK-01-001",
                task_slug="orientation",
                state_claims_status="in-progress",
                filesystem_actual_status="done",
            )
            state_text = scaffold.state.read_text(encoding="utf-8")
            self.assertIn("in-progress", state_text)

    def test_task_absent_from_claimed_dir(self) -> None:
        with project_scaffold() as scaffold:
            write_disagreement_layout(
                scaffold,
                task_id="TASK-01-001",
                task_slug="orientation",
                state_claims_status="in-progress",
                filesystem_actual_status="done",
            )
            # The task is in done/, NOT in in-progress/.
            matches = list(scaffold.tasks_in_progress.glob("TASK-01-001-*.md"))
            self.assertEqual(matches, [])

    def test_default_parameters_produce_disagreement(self) -> None:
        with project_scaffold() as scaffold:
            task_path = write_disagreement_layout(scaffold)
            self.assertTrue(task_path.is_file())
            state_text = scaffold.state.read_text(encoding="utf-8")
            # state and filesystem must disagree: state mentions one status,
            # task lives in another
            self.assertNotIn(
                task_path.parent.name,
                [s for s in state_text.split() if s.startswith("`") and s.endswith("`")],
            )


class PhaseOrderingLayoutTests(unittest.TestCase):
    def test_default_layout_creates_tasks_in_all_status_dirs(self) -> None:
        with project_scaffold() as scaffold:
            paths = write_phase_ordering_layout(scaffold)
            self.assertGreater(len(paths), 1, "expected multiple task files")
            # All returned paths must exist.
            for p in paths:
                self.assertTrue(p.is_file(), msg=f"missing task file: {p}")

    def test_tasks_span_multiple_status_directories(self) -> None:
        with project_scaffold() as scaffold:
            paths = write_phase_ordering_layout(scaffold)
            statuses = {p.parent.name for p in paths}
            self.assertGreater(len(statuses), 1, "expected tasks in more than one status dir")

    def test_custom_task_list_is_respected(self) -> None:
        tasks = [
            ("TASK-02-001", "alpha", "open"),
            ("TASK-02-002", "beta", "in-progress"),
            ("TASK-02-003", "gamma", "done"),
        ]
        with project_scaffold() as scaffold:
            paths = write_phase_ordering_layout(scaffold, tasks=tasks)
            self.assertEqual(len(paths), 3)
            self.assertEqual(paths[0].parent, scaffold.tasks_open)
            self.assertEqual(paths[1].parent, scaffold.tasks_in_progress)
            self.assertEqual(paths[2].parent, scaffold.tasks_done)

    def test_task_files_contain_task_id(self) -> None:
        tasks = [("TASK-01-007", "feature", "open")]
        with project_scaffold() as scaffold:
            paths = write_phase_ordering_layout(scaffold, tasks=tasks)
            content = paths[0].read_text(encoding="utf-8")
            self.assertIn("TASK-01-007", content)


class StalePromptLayoutTests(unittest.TestCase):
    def test_prompt_file_is_created(self) -> None:
        with project_scaffold() as scaffold:
            prompt_path = write_stale_prompt_layout(scaffold)
            self.assertTrue(prompt_path.is_file())
            self.assertEqual(prompt_path.parent, scaffold.prompts)

    def test_no_corresponding_active_task(self) -> None:
        with project_scaffold() as scaffold:
            write_stale_prompt_layout(
                scaffold, task_id="TASK-01-003", slug="stale-feature"
            )
            # No task in in-progress/ or in-review/.
            ip_matches = list(scaffold.tasks_in_progress.glob("TASK-01-003-*.md"))
            ir_matches = list(scaffold.tasks_in_review.glob("TASK-01-003-*.md"))
            self.assertEqual(ip_matches, [])
            self.assertEqual(ir_matches, [])

    def test_prompt_filename_matches_task_id(self) -> None:
        with project_scaffold() as scaffold:
            prompt_path = write_stale_prompt_layout(
                scaffold, task_id="TASK-02-005", slug="orphan"
            )
            self.assertEqual(prompt_path.name, "PROMPT-02-005.md")

    def test_prompt_content_references_task(self) -> None:
        with project_scaffold() as scaffold:
            prompt_path = write_stale_prompt_layout(
                scaffold, task_id="TASK-01-009", slug="stale"
            )
            content = prompt_path.read_text(encoding="utf-8")
            self.assertIn("TASK-01-009", content)


class UnresolvedReportLayoutTests(unittest.TestCase):
    def test_report_prompt_and_task_files_created(self) -> None:
        with project_scaffold() as scaffold:
            report_path, task_path, prompt_path = write_unresolved_report_layout(scaffold)
            self.assertTrue(report_path.is_file())
            self.assertTrue(task_path.is_file())
            self.assertTrue(prompt_path.is_file())

    def test_report_is_in_reports_dir(self) -> None:
        with project_scaffold() as scaffold:
            report_path, _, _ = write_unresolved_report_layout(scaffold)
            self.assertEqual(report_path.parent, scaffold.reports)

    def test_task_is_not_in_done(self) -> None:
        with project_scaffold() as scaffold:
            _, task_path, _ = write_unresolved_report_layout(scaffold)
            self.assertNotEqual(task_path.parent, scaffold.tasks_done)

    def test_prompt_is_not_cleared(self) -> None:
        with project_scaffold() as scaffold:
            _, _, prompt_path = write_unresolved_report_layout(scaffold)
            self.assertTrue(prompt_path.is_file(), "prompt must still exist (not cleared)")
            self.assertEqual(prompt_path.parent, scaffold.prompts)

    def test_filenames_share_nn_nnn_identifier(self) -> None:
        with project_scaffold() as scaffold:
            report_path, task_path, prompt_path = write_unresolved_report_layout(
                scaffold, task_id="TASK-01-004", slug="incomplete", task_status="in-progress"
            )
            self.assertIn("01-004", report_path.name)
            self.assertIn("01-004", prompt_path.name)
            self.assertIn("TASK-01-004", task_path.name)

    def test_custom_task_status_is_honoured(self) -> None:
        with project_scaffold() as scaffold:
            _, task_path, _ = write_unresolved_report_layout(
                scaffold,
                task_id="TASK-01-005",
                slug="review",
                task_status="in-review",
            )
            self.assertEqual(task_path.parent, scaffold.tasks_in_review)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
