"""Tests for `cartopian report-action`."""
import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from tests.scaffold import project_scaffold

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"

_PROJECT_TOML = (
    "[project]\n"
    'id = "demo"\n'
    'name = "Demo Project"\n'
    'protocol_version = "v0.5.0"\n'
    "\n"
    "[git]\n"
    "pm_owns_product_branches = true\n"
    "\n"
    "[roles]\n"
    'reviewer = "Reviews completed work."\n'
    "\n"
    "[reviews]\n"
    'planning = "required"\n'
    'planning_role = "reviewer"\n'
    'task_closure = "required"\n'
    'task_role = "reviewer"\n'
)

_PROJECT_TOML_OFF = (
    "[project]\n"
    'id = "demo"\n'
    'name = "Demo Project"\n'
    'protocol_version = "v0.5.0"\n'
    "\n"
    "[git]\n"
    "pm_owns_product_branches = true\n"
    "\n"
    "[reviews]\n"
    'planning = "off"\n'
    'task_closure = "off"\n'
)


def _run(report_path: str, *, home: Path) -> subprocess.CompletedProcess[str]:
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
    }
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), "report-action", report_path],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _parse_single_record(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    lines = result.stdout.splitlines()
    if len(lines) != 1:
        raise AssertionError(
            f"expected 1 stdout line, got {len(lines)}: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    return json.loads(lines[0])


def _task_report(
    *,
    task_id: str,
    prompt_path: Path,
    task_path: Path,
    work_root: str,
    status: str,
    ready_for_review: str,
) -> str:
    suffix = task_id.removeprefix("TASK-")
    return (
        f"# REPORT-{suffix}\n\n"
        f"Status: {status}\n\n"
        "## Identity\n\n"
        f"- Task ID: {task_id}\n"
        f"- Prompt path: {prompt_path}\n"
        f"- Task path: {task_path}\n"
        f"- Work root: {work_root}\n\n"
        "## Files changed\n\n"
        "- cli/commands/report_action.py - exercised\n\n"
        "## Test evidence\n\n"
        "- Red test evidence: targeted red\n"
        "- Green test evidence: targeted green\n\n"
        "## Commit / PR\n\n"
        "- Commit SHA: n/a\n"
        "- PR URL: n/a\n\n"
        "## Remaining risks\n\n"
        "None.\n\n"
        "## Ready for review\n\n"
        f"{ready_for_review}\n"
    )


def _review_report(
    *,
    report_stem: str,
    review_id: str,
    prompt_path: Path,
    review_path: Path,
    status: str,
    verdict: str | None = None,
) -> str:
    verdict_body = verdict if verdict is not None else ""
    return (
        f"# {report_stem}\n\n"
        f"Status: {status}\n\n"
        "## Identity\n\n"
        f"- Review ID: {review_id}\n"
        f"- Prompt path: {prompt_path}\n"
        f"- Review file path: {review_path}\n\n"
        "## Evidence reviewed\n\n"
        "- report-action routing fields\n\n"
        "## Verdict\n\n"
        f"{verdict_body}\n\n"
        "## Blocking findings\n\n"
        "none.\n"
    )


def _snapshot_tree(root: Path) -> dict[str, tuple[int, str, int]]:
    snapshot: dict[str, tuple[int, str, int]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes()
        stat = path.stat()
        snapshot[rel] = (
            stat.st_size,
            hashlib.sha256(data).hexdigest(),
            stat.st_mtime_ns,
        )
    return snapshot


class TestReportActionHappyPath(unittest.TestCase):
    def test_emits_required_fields_for_task_report(self) -> None:
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            task_path = scaffold.write(
                "tasks/in-progress/TASK-01-006-demo.md",
                (
                    "# TASK-01-006: demo\n\n"
                    "Work root: tool-repo\n"
                ),
            )
            report_path = scaffold.write(
                "reports/REPORT-01-006.md",
                (
                    "# REPORT-01-006\n\n"
                    "Status: complete\n\n"
                    "## Identity\n\n"
                    "- Task ID: TASK-01-006\n"
                    f"- Prompt path: {scaffold.prompts / 'PROMPT-01-006.md'}\n"
                    f"- Task path: {task_path}\n"
                    "- Work root: tool-repo\n\n"
                    "## Files changed\n\n"
                    "- cli/commands/report_action.py — added\n\n"
                    "## Test evidence\n\n"
                    "- Red test evidence: targeted red\n"
                    "- Green test evidence: targeted green\n\n"
                    "## Commit / PR\n\n"
                    "- Commit SHA: n/a\n"
                    "- PR URL: n/a\n\n"
                    "## Remaining risks\n\n"
                    "None.\n\n"
                    "## Ready for review\n\n"
                    "yes\n"
                ),
            )

            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        record = _parse_single_record(result)

        for field in (
            "verdict",
            "variant",
            "report_path",
            "status",
            "review_verdict",
            "target_task_status",
            "requires_pr_step",
            "prompt_to_overwrite",
            "review_path",
            "declared_report_task_path",
            "path_mismatch",
        ):
            self.assertIn(field, record, msg=f"missing field: {field}")

        self.assertEqual(record["verdict"], "accepted")
        self.assertEqual(record["variant"], "task")
        self.assertEqual(record["report_path"], str(report_path.resolve()))
        self.assertEqual(record["status"], "complete")
        self.assertIsNone(record["review_verdict"])
        self.assertEqual(record["target_task_status"], "in-review")
        self.assertTrue(record["requires_pr_step"])
        self.assertEqual(record["prompt_to_overwrite"], str((scaffold.prompts / "PROMPT-01-006.md").resolve()))
        self.assertEqual(record["review_path"], str((scaffold.reviews / "REVIEW-01-006.md").resolve()))
        self.assertEqual(record["declared_report_task_path"], str(task_path.resolve()))
        self.assertFalse(record["path_mismatch"])


class TestReportActionReviewOff(unittest.TestCase):
    def test_accepted_task_routes_directly_to_done(self) -> None:
        with project_scaffold(cartopian_toml=_PROJECT_TOML_OFF) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            task_path = scaffold.write(
                "tasks/in-progress/TASK-01-006-demo.md",
                "# task\n\nWork root: tool-repo\n",
            )
            report_path = scaffold.write(
                "reports/REPORT-01-006.md",
                _task_report(
                    task_id="TASK-01-006",
                    prompt_path=scaffold.prompts / "PROMPT-01-006.md",
                    task_path=task_path,
                    work_root="tool-repo",
                    status="complete",
                    ready_for_review="yes",
                ),
            )
            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        self.assertEqual(record["target_task_status"], "done")
        self.assertTrue(record["requires_pr_step"])
        self.assertIsNone(record["prompt_to_overwrite"])
        self.assertIsNone(record["review_path"])
        self.assertEqual(record["recommended_action"], "prepare-pr-and-close-task")

    def test_neutral_completion_report_is_accepted(self) -> None:
        with project_scaffold(cartopian_toml=_PROJECT_TOML_OFF) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            scaffold.write(
                "tasks/in-progress/TASK-01-009-book-venue.md",
                "# task\n\nWork root: n/a\n",
            )
            report_path = scaffold.write(
                "reports/REPORT-01-009.md",
                "# REPORT-01-009\n\n"
                "Status: complete\n\n"
                "## Identity\n\n- Work root: n/a\n\n"
                "## Completion evidence\n\n"
                "- Venue confirmed; confirmation number ABC-123.\n\n"
                "## Remaining risks\n\n- Cancellation window closes Friday.\n\n"
                "## Ready to close\n\nyes\n",
            )
            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        self.assertEqual(record["verdict"], "accepted")
        self.assertEqual(record["target_task_status"], "done")
        self.assertEqual(record["recommended_action"], "close-task")

    def test_task_blocked_and_failed_route_back_to_in_progress(self) -> None:
        for status in ("blocked", "failed"):
            with self.subTest(status=status):
                with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
                    home = scaffold.root / "home"
                    home.mkdir()
                    task_path = scaffold.write(
                        "tasks/in-progress/TASK-01-006-demo.md",
                        (
                            "# TASK-01-006: demo\n\n"
                            "Work root: tool-repo\n"
                        ),
                    )
                    report_path = scaffold.write(
                        "reports/REPORT-01-006.md",
                        _task_report(
                            task_id="TASK-01-006",
                            prompt_path=scaffold.prompts / "PROMPT-01-006.md",
                            task_path=task_path,
                            work_root="tool-repo",
                            status=status,
                            ready_for_review="no",
                        ),
                    )

                    result = _run(str(report_path), home=home)

                self.assertEqual(result.returncode, 0, msg=result.stderr)
                record = _parse_single_record(result)
                self.assertEqual(record["verdict"], status)
                self.assertEqual(record["variant"], "task")
                self.assertEqual(record["status"], status)
                self.assertEqual(record["target_task_status"], "in-progress")
                self.assertFalse(record["requires_pr_step"])
                self.assertIsNone(record["prompt_to_overwrite"])
                self.assertIsNone(record["review_path"])
                self.assertFalse(record["path_mismatch"])
                self.assertEqual(record["recommended_action"], "return-control-to-operator")


class TestReportActionReviewVariants(unittest.TestCase):
    def test_review_accepts_valid_no_plan_state_with_nullable_task_fields(self) -> None:
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            review_path = scaffold.write("reviews/REVIEW-01-007.md", "# REVIEW-01-007\n")
            report_path = scaffold.write(
                "reports/REPORT-01-007.md",
                _review_report(
                    report_stem="REPORT-01-007",
                    review_id="REVIEW-01-007",
                    prompt_path=scaffold.prompts / "PROMPT-01-007.md",
                    review_path=review_path,
                    status="complete",
                    verdict="approve",
                ),
            )

            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        self.assertEqual(record["verdict"], "accepted")
        self.assertEqual(record["variant"], "review")
        self.assertEqual(record["status"], "complete")
        self.assertEqual(record["review_verdict"], "approve")
        self.assertEqual(record["target_task_status"], "done")
        self.assertFalse(record["requires_pr_step"])
        self.assertEqual(record["prompt_to_overwrite"], str((scaffold.prompts / "PROMPT-01-007.md").resolve()))
        self.assertEqual(record["review_path"], str(review_path.resolve()))
        self.assertIsNone(record["task_id"])
        self.assertIsNone(record["task_path"])
        self.assertIsNone(record["expected_task_path"])
        self.assertFalse(record["path_mismatch"])
        self.assertEqual(record["recommended_action"], "close-task")

    def test_review_blocked_and_failed_keep_task_in_review(self) -> None:
        for status in ("blocked", "failed"):
            with self.subTest(status=status):
                with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
                    home = scaffold.root / "home"
                    home.mkdir()
                    review_path = scaffold.write("reviews/REVIEW-01-008.md", "# REVIEW-01-008\n")
                    report_path = scaffold.write(
                        "reports/REPORT-01-008.md",
                        _review_report(
                            report_stem="REPORT-01-008",
                            review_id="REVIEW-01-008",
                            prompt_path=scaffold.prompts / "PROMPT-01-008.md",
                            review_path=review_path,
                            status=status,
                        ),
                    )

                    result = _run(str(report_path), home=home)

                self.assertEqual(result.returncode, 0, msg=result.stderr)
                record = _parse_single_record(result)
                self.assertEqual(record["verdict"], status)
                self.assertEqual(record["variant"], "review")
                self.assertEqual(record["status"], status)
                self.assertIsNone(record["review_verdict"])
                self.assertEqual(record["target_task_status"], "in-review")
                self.assertFalse(record["requires_pr_step"])
                self.assertIsNone(record["prompt_to_overwrite"])
                self.assertEqual(record["review_path"], str(review_path.resolve()))
                self.assertFalse(record["path_mismatch"])
                self.assertEqual(record["recommended_action"], "return-control-to-operator")

    def test_planning_review_request_changes_routes_back_to_in_progress(self) -> None:
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            review_path = scaffold.write("reviews/REVIEW-PLAN-001-demo.md", "# REVIEW-PLAN-001-demo\n")
            report_path = scaffold.write(
                "reports/REPORT-PLAN-001-demo.md",
                _review_report(
                    report_stem="REPORT-PLAN-001-demo",
                    review_id="REVIEW-PLAN-001-demo",
                    prompt_path=scaffold.prompts / "PROMPT-PLAN-001-demo.md",
                    review_path=review_path,
                    status="complete",
                    verdict="request-changes",
                ),
            )

            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        self.assertEqual(record["verdict"], "changes-requested")
        self.assertEqual(record["variant"], "planning-review")
        self.assertEqual(record["status"], "complete")
        self.assertEqual(record["review_verdict"], "request-changes")
        self.assertEqual(record["target_task_status"], "in-progress")
        self.assertFalse(record["requires_pr_step"])
        self.assertEqual(
            record["prompt_to_overwrite"],
            str((scaffold.prompts / "PROMPT-PLAN-001-demo.md").resolve()),
        )
        self.assertEqual(record["review_path"], str(review_path.resolve()))
        self.assertFalse(record["path_mismatch"])
        self.assertEqual(record["recommended_action"], "return-task-to-in-progress")


class TestReportActionPathMismatch(unittest.TestCase):
    def test_surfaces_path_mismatch_as_data(self) -> None:
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            scaffold.write("tasks/in-progress/TASK-01-006-demo.md", "# TASK-01-006: demo\n")
            report_path = scaffold.write(
                "reports/REPORT-01-006.md",
                (
                    "# REPORT-01-006\n\n"
                    "Status: complete\n\n"
                    "## Identity\n\n"
                    "- Task ID: TASK-01-006\n"
                    f"- Prompt path: {scaffold.prompts / 'PROMPT-99-999.md'}\n"
                    f"- Task path: {scaffold.project_root / 'tasks' / 'done' / 'TASK-99-999-wrong.md'}\n"
                    "- Work root: n/a\n\n"
                    "## Files changed\n\n"
                    "- cli/commands/report_action.py — added\n\n"
                    "## Test evidence\n\n"
                    "- Red test evidence: targeted red\n"
                    "- Green test evidence: targeted green\n\n"
                    "## Commit / PR\n\n"
                    "- Commit SHA: n/a\n"
                    "- PR URL: n/a\n\n"
                    "## Remaining risks\n\n"
                    "None.\n\n"
                    "## Ready for review\n\n"
                    "yes\n"
                ),
            )

            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        self.assertTrue(record["path_mismatch"])


class TestReportActionVariantInference(unittest.TestCase):
    def test_review_shaped_report_naming_task_id_infers_review(self) -> None:
        """A review-completion report at the shared REPORT-NN-NNN.md name that
        also cites the reviewed Task ID in its Identity block must infer
        ``variant: review`` from content, not error ``ambiguous variant``."""
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            review_path = scaffold.write("reviews/REVIEW-01-010.md", "# REVIEW-01-010\n")
            report_path = scaffold.write(
                "reports/REPORT-01-010.md",
                (
                    "# REPORT-01-010\n\n"
                    "Status: complete\n\n"
                    "## Identity\n\n"
                    "- Task ID: TASK-01-010\n"
                    "- Review ID: REVIEW-01-010\n"
                    f"- Prompt path: {scaffold.prompts / 'PROMPT-01-010.md'}\n"
                    f"- Task path: {scaffold.project_root / 'tasks' / 'in-review' / 'TASK-01-010-demo.md'}\n"
                    f"- Review file path: {review_path}\n\n"
                    "## Evidence reviewed\n\n"
                    "- routing fields\n\n"
                    "## Verdict\n\n"
                    "approve\n\n"
                    "## Blocking findings\n\n"
                    "none.\n"
                ),
            )

            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        record = _parse_single_record(result)
        self.assertEqual(record["variant"], "review")
        self.assertEqual(record["verdict"], "accepted")
        self.assertEqual(record["review_verdict"], "approve")
        self.assertEqual(record["review_path"], str(review_path.resolve()))

    def test_genuine_variant_conflict_still_errors(self) -> None:
        """A report carrying BOTH task structure (## Ready for review) and review
        structure (## Verdict) is genuinely ambiguous and must still error."""
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            report_path = scaffold.write(
                "reports/REPORT-01-011.md",
                (
                    "# REPORT-01-011\n\n"
                    "Status: complete\n\n"
                    "## Identity\n\n"
                    "- Task ID: TASK-01-011\n"
                    "- Review ID: REVIEW-01-011\n\n"
                    "## Verdict\n\n"
                    "approve\n\n"
                    "## Ready for review\n\n"
                    "yes\n"
                ),
            )

            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("[usage] ambiguous variant", result.stderr)


class TestReportActionPathNormalization(unittest.TestCase):
    def test_backtick_wrapped_task_path_is_not_a_false_mismatch(self) -> None:
        """A cosmetic markdown backtick wrap around an otherwise-correct
        ``Task path:`` must normalize away, not produce ``path_mismatch: true``."""
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            task_path = scaffold.write(
                "tasks/in-progress/TASK-01-012-demo.md",
                "# TASK-01-012: demo\n\nWork root: n/a\n",
            )
            report_path = scaffold.write(
                "reports/REPORT-01-012.md",
                (
                    "# REPORT-01-012\n\n"
                    "Status: complete\n\n"
                    "## Identity\n\n"
                    "- Task ID: TASK-01-012\n"
                    f"- Prompt path: {scaffold.prompts / 'PROMPT-01-012.md'}\n"
                    f"- Task path: `{task_path}`\n"
                    "- Work root: n/a\n\n"
                    "## Files changed\n\n"
                    "- cli/commands/report_action.py — added\n\n"
                    "## Test evidence\n\n"
                    "- Red test evidence: targeted red\n"
                    "- Green test evidence: targeted green\n\n"
                    "## Commit / PR\n\n"
                    "- Commit SHA: n/a\n"
                    "- PR URL: n/a\n\n"
                    "## Remaining risks\n\n"
                    "None.\n\n"
                    "## Ready for review\n\n"
                    "yes\n"
                ),
            )

            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        self.assertEqual(record["verdict"], "accepted")
        self.assertFalse(record["path_mismatch"])
        self.assertEqual(record["declared_report_task_path"], str(task_path.resolve()))


class TestReportActionFailedToParse(unittest.TestCase):
    def test_incomplete_report_emits_failed_to_parse_record(self) -> None:
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            task_path = scaffold.write(
                "tasks/in-progress/TASK-01-009-demo.md",
                "# TASK-01-009: demo\n\nWork root: n/a\n",
            )
            report_path = scaffold.write(
                "reports/REPORT-01-009.md",
                (
                    "# REPORT-01-009\n\n"
                    "Status: complete\n\n"
                    "## Identity\n\n"
                    "- Task ID: TASK-01-009\n"
                    f"- Prompt path: {scaffold.prompts / 'PROMPT-01-009.md'}\n"
                    f"- Task path: {task_path}\n"
                    "- Work root: n/a\n\n"
                    "## Ready for review\n\n"
                    "yes\n"
                ),
            )

            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        self.assertEqual(record["verdict"], "failed-to-parse")
        self.assertEqual(record["variant"], "task")
        self.assertIsNone(record["status"])
        self.assertIsNone(record["review_verdict"])
        self.assertIsNone(record["target_task_status"])
        self.assertFalse(record["requires_pr_step"])
        self.assertIsNone(record["prompt_to_overwrite"])
        self.assertIsNone(record["review_path"])
        self.assertFalse(record["path_mismatch"])
        self.assertEqual(record["recommended_action"], "stop-for-inspection")


class TestReportActionExitCodes(unittest.TestCase):
    def test_missing_project_config_exits_env(self) -> None:
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            scaffold.config.unlink()
            report_path = scaffold.write(
                "reports/REPORT-01-006.md",
                "# REPORT-01-006\n\nStatus: complete\n",
            )

            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, "")
        self.assertIn("[error] project config not found:", result.stderr)

    def test_unreadable_project_config_exits_env(self) -> None:
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            scaffold.config.write_text("[[not-valid-toml\x00", encoding="utf-8")
            report_path = scaffold.write(
                "reports/REPORT-01-006.md",
                "# REPORT-01-006\n\nStatus: complete\n",
            )

            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, "")
        self.assertIn("[error]", result.stderr)


class TestReportActionReadOnly(unittest.TestCase):
    def test_does_not_modify_project_tree(self) -> None:
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            task_path = scaffold.write(
                "tasks/in-progress/TASK-01-006-demo.md",
                (
                    "# TASK-01-006: demo\n\n"
                    "Work root: n/a\n"
                ),
            )
            report_path = scaffold.write(
                "reports/REPORT-01-006.md",
                (
                    "# REPORT-01-006\n\n"
                    "Status: complete\n\n"
                    "## Identity\n\n"
                    "- Task ID: TASK-01-006\n"
                    f"- Prompt path: {scaffold.prompts / 'PROMPT-01-006.md'}\n"
                    f"- Task path: {task_path}\n"
                    "- Work root: n/a\n\n"
                    "## Files changed\n\n"
                    "- none\n\n"
                    "## Test evidence\n\n"
                    "- Red test evidence: red\n"
                    "- Green test evidence: green\n\n"
                    "## Commit / PR\n\n"
                    "- Commit SHA: n/a\n"
                    "- PR URL: n/a\n\n"
                    "## Remaining risks\n\n"
                    "None.\n\n"
                    "## Ready for review\n\n"
                    "yes\n"
                ),
            )
            before = _snapshot_tree(scaffold.project_root)

            result = _run(str(report_path), home=home)
            after = _snapshot_tree(scaffold.project_root)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(before, after)
