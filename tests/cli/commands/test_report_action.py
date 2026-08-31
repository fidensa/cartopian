"""Tests for `cartopian report-action`."""
import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from cli import request_trace
from tests.scaffold import project_scaffold

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"

_PROJECT_TOML = (
    "[project]\n"
    'id = "demo"\n'
    'name = "Demo Project"\n'
    'project_schema_version = "v0.12.0"\n'
    "\n"
    "[git]\n"
    "pm_owns_product_branches = true\n"
    "\n"
    "[roles.reviewer]\n"
    'description = "Reviews completed work."\n'
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
    'project_schema_version = "v0.12.0"\n'
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
    task_path: Path | None,
    review_path: Path,
    status: str,
    verdict: str | None = None,
) -> str:
    verdict_body = verdict if verdict is not None else ""
    task_identity = f"- Task path: {task_path}\n" if task_path is not None else ""
    bound = prompt_path.is_file()
    alignment = "aligned" if bound else "unavailable-for-legacy"
    evidence = "REQUEST-001" if bound else "none"
    return (
        f"# {report_stem}\n\n"
        f"Status: {status}\n\n"
        f"Request alignment: {alignment}\n\n"
        f"Request evidence: {evidence}\n\n"
        "## Identity\n\n"
        f"- Review ID: {review_id}\n"
        f"- Prompt path: {prompt_path}\n"
        f"{task_identity}"
        f"- Review file path: {review_path}\n\n"
        "## Evidence reviewed\n\n"
        "- report-action routing fields\n\n"
        "## Verdict\n\n"
        f"{verdict_body}\n\n"
        "## Blocking findings\n\n"
        "none.\n"
    )


def _bound_task_prompt(scaffold, task_path: Path, suffix: str) -> Path:
    task_id = "-".join(task_path.stem.split("-")[:3])
    scaffold.capture_request(
        request_id="REQUEST-001",
        unit=f"task:{task_id}",
        text="Review this task outcome.",
    )
    context = request_trace.context_for_task(scaffold.project_root, task_path)
    return scaffold.write(
        f"prompts/PROMPT-{suffix}.md",
        request_trace.upsert_request_sections("# Review\n", context.section),
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
                "tasks/in-progress/TASK-01-006.md",
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


class TestReadyRoutingSemantics(unittest.TestCase):
    """Producer readiness routes into required review, never self-approval."""

    def _route(self, *, toml: str, heading: str, value: str) -> dict:
        import argparse
        import contextlib
        import io

        from cli.commands import report_action

        with project_scaffold(cartopian_toml=toml) as scaffold:
            scaffold.write(
                "tasks/in-progress/TASK-01-006.md",
                "# task\n\nWork root: n/a\n",
            )
            report_path = scaffold.write(
                "reports/REPORT-01-006.md",
                (
                    "# REPORT-01-006\n\n"
                    "Status: complete\n\n"
                    "## Identity\n\n- Work root: n/a\n\n"
                    "## Completion evidence\n\nThe outcome exists.\n\n"
                    "## Remaining risks\n\nNone.\n\n"
                    f"## {heading}\n\n{value}\n"
                ),
            )
            out, err = io.StringIO(), io.StringIO()
            with (
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(err),
            ):
                rc = report_action.handler(
                    argparse.Namespace(
                        report_path=str(report_path),
                        variant=None,
                        expected_identity=None,
                    )
                )
            self.assertEqual(rc, 0, err.getvalue())
            return json.loads(out.getvalue())

    def test_producer_completion_routes_into_required_review(self) -> None:
        for heading in ("Ready for review", "Ready to close"):
            with self.subTest(heading=heading):
                record = self._route(
                    toml=_PROJECT_TOML, heading=heading, value="yes"
                )
                self.assertEqual(record["verdict"], "accepted")
                self.assertEqual(record["target_task_status"], "in-review")
                self.assertEqual(record["recommended_action"], "assign-review")

    def test_producer_completion_with_review_off_routes_to_done(self) -> None:
        for heading in ("Ready for review", "Ready to close"):
            with self.subTest(heading=heading):
                record = self._route(
                    toml=_PROJECT_TOML_OFF, heading=heading, value="yes"
                )
                self.assertEqual(record["target_task_status"], "done")
                self.assertEqual(record["recommended_action"], "close-task")

    def test_no_keeps_incomplete_work_in_progress(self) -> None:
        record = self._route(
            toml=_PROJECT_TOML, heading="Ready for review", value="no"
        )
        self.assertEqual(record["target_task_status"], "in-progress")
        self.assertEqual(
            record["recommended_action"], "return-control-to-operator"
        )

    def test_rationale_after_token_still_parses(self) -> None:
        """The REPORT-05-010 form: `no — closure is not mine to certify`."""
        record = self._route(
            toml=_PROJECT_TOML,
            heading="Ready to close",
            value=(
                "no — the deliverable is complete but closure is not mine "
                "to certify."
            ),
        )
        self.assertEqual(record["target_task_status"], "in-progress")
        yes_record = self._route(
            toml=_PROJECT_TOML,
            heading="Ready for review",
            value="yes — work complete; entering required independent review.",
        )
        self.assertEqual(yes_record["target_task_status"], "in-review")

    def test_word_starting_with_no_is_not_a_value(self) -> None:
        record = self._route(
            toml=_PROJECT_TOML, heading="Ready to close", value="nothing yet"
        )
        self.assertIsNone(record["target_task_status"])

    def test_unfilled_placeholder_alternation_is_not_a_value(self) -> None:
        for placeholder in ("yes | no", "yes/no", "yes or no"):
            with self.subTest(placeholder=placeholder):
                record = self._route(
                    toml=_PROJECT_TOML,
                    heading="Ready to close",
                    value=placeholder,
                )
                self.assertIsNone(record["target_task_status"])


class TestExpectedIdentityBinding(unittest.TestCase):
    def _invoke(self, report_path, expected_identity):
        import argparse
        import contextlib
        import io

        from cli.commands import report_action

        out, err = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            rc = report_action.handler(
                argparse.Namespace(
                    report_path=str(report_path),
                    variant=None,
                    expected_identity=expected_identity,
                )
            )
        return rc, out.getvalue(), err.getvalue()

    def test_routing_consumes_only_the_accepted_publication(self) -> None:
        from cli import report_identity

        body = (
            "# REPORT-01-006\n\nStatus: complete\n\n"
            "## Identity\n\n- Work root: n/a\n\n"
            "## Completion evidence\n\nThe outcome exists.\n\n"
            "## Remaining risks\n\nNone.\n\n"
            "## Ready for review\n\nyes\n"
        )
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            scaffold.write(
                "tasks/in-progress/TASK-01-006.md",
                "# task\n\nWork root: n/a\n",
            )
            report_path = scaffold.write("reports/REPORT-01-006.md", body)
            accepted = report_identity.content_identity(body)

            rc, stdout, stderr = self._invoke(report_path, accepted)
            self.assertEqual(rc, 0, stderr)
            record = json.loads(stdout)
            self.assertEqual(record["verdict"], "accepted")
            self.assertEqual(record["report_content_identity"], accepted)

            # The report mutates after the wait accepted it: routing on the
            # new bytes is refused with a re-observe recommendation.
            report_path.write_text(
                body.replace("The outcome exists.", "Different bytes."),
                encoding="utf-8",
            )
            rc, stdout, stderr = self._invoke(report_path, accepted)
            self.assertEqual(rc, 1)
            record = json.loads(stdout)
            self.assertEqual(record["verdict"], "identity-mismatch")
            self.assertEqual(
                record["recommended_action"], "rerun-canonical-wait"
            )
            self.assertIn("report-identity-mismatch", stderr)

    def test_malformed_identity_is_usage_error(self) -> None:
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            report_path = scaffold.write(
                "reports/REPORT-01-006.md", "# REPORT-01-006\n"
            )
            rc, _stdout, stderr = self._invoke(report_path, "not-an-identity")
            self.assertEqual(rc, 2)
            self.assertIn("expected sha256:", stderr)


class TestReportActionReviewOff(unittest.TestCase):
    def test_accepted_task_routes_directly_to_done(self) -> None:
        with project_scaffold(cartopian_toml=_PROJECT_TOML_OFF) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            task_path = scaffold.write(
                "tasks/in-progress/TASK-01-006.md",
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
                "tasks/in-progress/TASK-01-009.md",
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
                        "tasks/in-progress/TASK-01-006.md",
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
    def test_review_accepts_and_resolves_task_fields(self) -> None:
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            task_path = scaffold.write(
                "tasks/in-review/TASK-02-004.md",
                "# TASK-02-004: demo\n\nWork root: n/a\n",
            )
            _bound_task_prompt(scaffold, task_path, "02-004")
            review_path = scaffold.write("reviews/REVIEW-02-004.md", "# REVIEW-02-004\n")
            report_path = scaffold.write(
                "reports/REPORT-02-004-review.md",
                _review_report(
                    report_stem="REPORT-02-004-review",
                    review_id="REVIEW-02-004",
                    prompt_path=scaffold.prompts / "PROMPT-02-004.md",
                    task_path=task_path,
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
        self.assertEqual(record["prompt_to_overwrite"], str((scaffold.prompts / "PROMPT-02-004.md").resolve()))
        self.assertEqual(record["review_path"], str(review_path.resolve()))
        self.assertEqual(record["task_id"], "TASK-02-004")
        self.assertEqual(record["task_path"], str(task_path.resolve()))
        self.assertEqual(record["expected_task_path"], str(task_path.resolve()))
        self.assertEqual(record["declared_report_task_path"], str(task_path.resolve()))
        self.assertFalse(record["path_mismatch"])
        self.assertEqual(record["recommended_action"], "close-task")

    def test_review_without_task_path_is_failed_to_parse(self) -> None:
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            scaffold.write(
                "tasks/in-review/TASK-02-004.md",
                "# TASK-02-004: demo\n\nWork root: n/a\n",
            )
            review_path = scaffold.write("reviews/REVIEW-02-004.md", "# REVIEW-02-004\n")
            report_path = scaffold.write(
                "reports/REPORT-02-004-review.md",
                _review_report(
                    report_stem="REPORT-02-004-review",
                    review_id="REVIEW-02-004",
                    prompt_path=scaffold.prompts / "PROMPT-02-004.md",
                    task_path=None,
                    review_path=review_path,
                    status="complete",
                    verdict="approve",
                ),
            )

            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        self.assertEqual(record["verdict"], "failed-to-parse")
        self.assertEqual(record["variant"], "review")
        self.assertIsNone(record["declared_report_task_path"])
        self.assertIsNone(record["target_task_status"])
        self.assertEqual(record["recommended_action"], "stop-for-inspection")

    def test_review_with_wrong_task_path_surfaces_path_mismatch(self) -> None:
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            expected_task_path = scaffold.write(
                "tasks/in-review/TASK-02-004.md",
                "# TASK-02-004: demo\n\nWork root: n/a\n",
            )
            wrong_task_path = scaffold.write(
                "tasks/in-review/TASK-02-005.md",
                "# TASK-02-005: other\n\nWork root: n/a\n",
            )
            _bound_task_prompt(scaffold, wrong_task_path, "02-004")
            review_path = scaffold.write("reviews/REVIEW-02-004.md", "# REVIEW-02-004\n")
            report_path = scaffold.write(
                "reports/REPORT-02-004-review.md",
                _review_report(
                    report_stem="REPORT-02-004-review",
                    review_id="REVIEW-02-004",
                    prompt_path=scaffold.prompts / "PROMPT-02-004.md",
                    task_path=wrong_task_path,
                    review_path=review_path,
                    status="complete",
                    verdict="approve",
                ),
            )

            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        self.assertEqual(record["verdict"], "accepted")
        self.assertEqual(record["declared_report_task_path"], str(wrong_task_path.resolve()))
        self.assertEqual(record["expected_task_path"], str(expected_task_path.resolve()))
        self.assertTrue(record["path_mismatch"])

    def test_review_blocked_and_failed_keep_task_in_review(self) -> None:
        for status in ("blocked", "failed"):
            with self.subTest(status=status):
                with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
                    home = scaffold.root / "home"
                    home.mkdir()
                    task_path = scaffold.write(
                        "tasks/in-review/TASK-01-008.md",
                        "# TASK-01-008: demo\n\nWork root: n/a\n",
                    )
                    review_path = scaffold.write("reviews/REVIEW-01-008.md", "# REVIEW-01-008\n")
                    report_path = scaffold.write(
                        "reports/REPORT-01-008-review.md",
                        _review_report(
                            report_stem="REPORT-01-008-review",
                            review_id="REVIEW-01-008",
                            prompt_path=scaffold.prompts / "PROMPT-01-008.md",
                            task_path=task_path,
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
            review_path = scaffold.write("reviews/REVIEW-PLAN-001.md", "# REVIEW-PLAN-001\n")
            report_path = scaffold.write(
                "reports/REPORT-PLAN-001.md",
                _review_report(
                    report_stem="REPORT-PLAN-001",
                    review_id="REVIEW-PLAN-001",
                    prompt_path=scaffold.prompts / "PROMPT-PLAN-001.md",
                    task_path=None,
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
            str((scaffold.prompts / "PROMPT-PLAN-001.md").resolve()),
        )
        self.assertEqual(record["review_path"], str(review_path.resolve()))
        self.assertFalse(record["path_mismatch"])
        self.assertEqual(record["recommended_action"], "return-task-to-in-progress")


class TestReportActionPathMismatch(unittest.TestCase):
    def test_surfaces_path_mismatch_as_data(self) -> None:
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            scaffold.write("tasks/in-progress/TASK-01-006.md", "# TASK-01-006: demo\n")
            report_path = scaffold.write(
                "reports/REPORT-01-006.md",
                (
                    "# REPORT-01-006\n\n"
                    "Status: complete\n\n"
                    "## Identity\n\n"
                    "- Task ID: TASK-01-006\n"
                    f"- Prompt path: {scaffold.prompts / 'PROMPT-99-999.md'}\n"
                    f"- Task path: {scaffold.project_root / 'tasks' / 'done' / 'TASK-99-999.md'}\n"
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
        """A review-completion report at its authoritative
        REPORT-NN-NNN-review.md name that also cites the reviewed Task ID in
        its Identity block resolves ``variant: review`` — the cited Task ID
        never makes the review report ambiguous."""
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            task_path = scaffold.write(
                "tasks/in-review/TASK-01-010.md",
                "# TASK-01-010: demo\n\nWork root: n/a\n",
            )
            _bound_task_prompt(scaffold, task_path, "01-010")
            review_path = scaffold.write("reviews/REVIEW-01-010.md", "# REVIEW-01-010\n")
            report_path = scaffold.write(
                "reports/REPORT-01-010-review.md",
                (
                    "# REPORT-01-010-review\n\n"
                    "Status: complete\n\n"
                    "Request alignment: aligned\n\n"
                    "Request evidence: REQUEST-001\n\n"
                    "## Identity\n\n"
                    "- Task ID: TASK-01-010\n"
                    "- Review ID: REVIEW-01-010\n"
                    f"- Prompt path: {scaffold.prompts / 'PROMPT-01-010.md'}\n"
                    f"- Task path: {task_path}\n"
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
                "tasks/in-progress/TASK-01-012.md",
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
                "tasks/in-progress/TASK-01-009.md",
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
        # Preserve a valid Status header for diagnostics even when a later
        # task-schema check fails.
        self.assertEqual(record["status"], "complete")
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
                "tasks/in-progress/TASK-01-006.md",
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


class TestBoundedProjections(unittest.TestCase):
    """`pm_summary` and `review_projection` bound what enters PM context.

    The artifacts themselves stay unbounded on disk; the projection is the
    capped convenience read the PM routes on instead of opening them whole.
    """

    def test_task_report_summary_is_projected_and_bounded(self) -> None:
        long_summary = "detail " * 600  # > PM_SUMMARY_MAX_CHARS
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            task_path = scaffold.write(
                "tasks/in-progress/TASK-03-001.md",
                "# TASK-03-001: demo\n\nWork root: n/a\n",
            )
            report_path = scaffold.write(
                "reports/REPORT-03-001.md",
                "# REPORT-03-001\n\n"
                "Status: complete\n\n"
                f"## Summary\n\n{long_summary}\n\n"
                "## Identity\n\n"
                f"- Task path: {task_path}\n"
                "- Work root: n/a\n\n"
                "## Files changed\n\n- cli/x.py - exercised\n\n"
                "## Remaining risks\n\nNone.\n\n"
                "## Ready for review\n\nyes\n",
            )
            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        from cli.commands.report_action import (
            PM_SUMMARY_MAX_CHARS,
            _TRUNCATION_MARK,
        )

        self.assertTrue(record["pm_summary_truncated"])
        self.assertTrue(record["pm_summary"].endswith(_TRUNCATION_MARK))
        # The truncation marker lives inside the advertised limit.
        self.assertLessEqual(len(record["pm_summary"]), PM_SUMMARY_MAX_CHARS)
        self.assertIsNone(record["review_projection"])

    def test_report_without_summary_projects_null(self) -> None:
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            task_path = scaffold.write(
                "tasks/in-progress/TASK-03-002.md",
                "# TASK-03-002: demo\n\nWork root: n/a\n",
            )
            report_path = scaffold.write(
                "reports/REPORT-03-002.md",
                _task_report(
                    task_id="TASK-03-002",
                    prompt_path=scaffold.prompts / "PROMPT-03-002.md",
                    task_path=task_path,
                    work_root="n/a",
                    status="complete",
                    ready_for_review="yes",
                ),
            )
            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        self.assertIsNone(record["pm_summary"])
        self.assertFalse(record["pm_summary_truncated"])

    def test_review_variant_projects_review_file_findings(self) -> None:
        from cli.commands.report_action import PROJECTED_FINDINGS_MAX

        finding_rows = "\n".join(
            f"- F{n}. [minor] — finding number {n}."
            for n in range(1, PROJECTED_FINDINGS_MAX + 3)
        )
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            task_path = scaffold.write(
                "tasks/in-review/TASK-03-003.md",
                "# TASK-03-003: demo\n\nWork root: n/a\n",
            )
            _bound_task_prompt(scaffold, task_path, "03-003")
            review_path = scaffold.write(
                "reviews/REVIEW-03-003.md",
                "# REVIEW-03-003\n\n"
                "Target: TASK-03-003\n"
                "Reviewer: test\n"
                "Verdict: request-changes\n\n"
                "## Summary\n\n"
                "Reviewed the demo change. Two hedges remain.\n\n"
                f"## Findings\n\n{finding_rows}\n\n"
                "## Reviewer notes\n\nLong supporting prose lives here.\n",
            )
            report_path = scaffold.write(
                "reports/REPORT-03-003-review.md",
                _review_report(
                    report_stem="REPORT-03-003-review",
                    review_id="REVIEW-03-003",
                    prompt_path=scaffold.prompts / "PROMPT-03-003.md",
                    task_path=task_path,
                    review_path=review_path,
                    status="complete",
                    verdict="request-changes",
                ),
            )
            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        projection = record["review_projection"]
        self.assertEqual(projection["source"], "review-file")
        self.assertEqual(projection["verdict"], "request-changes")
        self.assertEqual(
            projection["summary"],
            "Reviewed the demo change. Two hedges remain.",
        )
        self.assertEqual(len(projection["findings"]), PROJECTED_FINDINGS_MAX)
        self.assertEqual(
            projection["findings"][0], "- F1. [minor] — finding number 1."
        )
        self.assertEqual(projection["findings_omitted"], 2)

    def test_review_projection_includes_contract_quality_findings(self) -> None:
        # C<n> rows live under `## Contract quality`; a contract-only
        # request-changes verdict must still project its findings.
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            task_path = scaffold.write(
                "tasks/in-review/TASK-03-005.md",
                "# TASK-03-005: demo\n\nWork root: n/a\n",
            )
            _bound_task_prompt(scaffold, task_path, "03-005")
            review_path = scaffold.write(
                "reviews/REVIEW-03-005.md",
                "# REVIEW-03-005\n\n"
                "Target: TASK-03-005\n"
                "Reviewer: test\n"
                "Verdict: request-changes\n\n"
                "## Summary\n\n"
                "Contract-only defects; the implementation is sound.\n\n"
                "## Contract quality\n\n"
                "Outcome: needs changes\n\n"
                "- C1. [major] Acceptance clarity — item 2 has no pass "
                "condition.\n\n"
                "## Findings\n\n"
                "none.\n",
            )
            report_path = scaffold.write(
                "reports/REPORT-03-005-review.md",
                _review_report(
                    report_stem="REPORT-03-005-review",
                    review_id="REVIEW-03-005",
                    prompt_path=scaffold.prompts / "PROMPT-03-005.md",
                    task_path=task_path,
                    review_path=review_path,
                    status="complete",
                    verdict="request-changes",
                ),
            )
            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        projection = record["review_projection"]
        self.assertEqual(projection["source"], "review-file")
        self.assertEqual(
            projection["findings"],
            [
                "- C1. [major] Acceptance clarity — item 2 has no pass "
                "condition."
            ],
        )
        self.assertEqual(projection["findings_omitted"], 0)

    def test_projected_finding_rows_stay_within_the_advertised_limit(self) -> None:
        from cli.commands.report_action import (
            PROJECTED_FINDING_MAX_CHARS,
            _TRUNCATION_MARK,
        )

        long_row = "- F1. [major] — " + "detail " * 120  # > limit
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            task_path = scaffold.write(
                "tasks/in-review/TASK-03-006.md",
                "# TASK-03-006: demo\n\nWork root: n/a\n",
            )
            _bound_task_prompt(scaffold, task_path, "03-006")
            review_path = scaffold.write(
                "reviews/REVIEW-03-006.md",
                "# REVIEW-03-006\n\n"
                "Target: TASK-03-006\n"
                "Reviewer: test\n"
                "Verdict: request-changes\n\n"
                f"## Findings\n\n{long_row}\n",
            )
            report_path = scaffold.write(
                "reports/REPORT-03-006-review.md",
                _review_report(
                    report_stem="REPORT-03-006-review",
                    review_id="REVIEW-03-006",
                    prompt_path=scaffold.prompts / "PROMPT-03-006.md",
                    task_path=task_path,
                    review_path=review_path,
                    status="complete",
                    verdict="request-changes",
                ),
            )
            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        row = record["review_projection"]["findings"][0]
        self.assertTrue(row.endswith(_TRUNCATION_MARK))
        self.assertLessEqual(len(row), PROJECTED_FINDING_MAX_CHARS)

    def test_projection_never_reads_a_report_declared_path(self) -> None:
        """A malformed report cannot steer the projection at an arbitrary file.

        The declared `Review file path` is untrusted input; on a path
        mismatch the projection must not dereference any review file and
        falls back to the report's own `## Blocking findings` body.
        """
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            task_path = scaffold.write(
                "tasks/in-review/TASK-03-007.md",
                "# TASK-03-007: demo\n\nWork root: n/a\n",
            )
            _bound_task_prompt(scaffold, task_path, "03-007")
            # A readable Markdown file outside the reviews directory with
            # projection-matching headings.
            outside = scaffold.root / "outside-secret.md"
            outside.write_text(
                "# NOT-A-REVIEW\n\n"
                "Verdict: approve\n\n"
                "## Summary\n\nSECRET-CONTENT\n\n"
                "## Findings\n\n- F1. [minor] SECRET-FINDING.\n",
                encoding="utf-8",
            )
            # The expected review file also exists; it must not be read
            # either once the declared path mismatches.
            scaffold.write(
                "reviews/REVIEW-03-007.md",
                "# REVIEW-03-007\n\nTarget: TASK-03-007\n"
                "Reviewer: test\nVerdict: approve\n\n"
                "## Summary\n\nEXPECTED-REVIEW-SUMMARY\n",
            )
            report_path = scaffold.write(
                "reports/REPORT-03-007-review.md",
                _review_report(
                    report_stem="REPORT-03-007-review",
                    review_id="REVIEW-03-007",
                    prompt_path=scaffold.prompts / "PROMPT-03-007.md",
                    task_path=task_path,
                    review_path=outside,
                    status="complete",
                    verdict="approve",
                ),
            )
            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        self.assertTrue(record["path_mismatch"])
        projection = record["review_projection"]
        self.assertEqual(projection["source"], "report-blocking-findings")
        serialized = json.dumps(record)
        self.assertNotIn("SECRET-CONTENT", serialized)
        self.assertNotIn("SECRET-FINDING", serialized)
        self.assertNotIn("EXPECTED-REVIEW-SUMMARY", serialized)

    def test_symlinked_review_slot_is_never_projected(self) -> None:
        """A symlink planted at the canonical review slot cannot escape.

        With the slot symlinked outside the project, the declared and
        expected paths resolve to the same outside target — so path_mismatch
        stays false and only read-time containment stands between the
        projection and the outside file.
        """
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            task_path = scaffold.write(
                "tasks/in-review/TASK-03-008.md",
                "# TASK-03-008: demo\n\nWork root: n/a\n",
            )
            _bound_task_prompt(scaffold, task_path, "03-008")
            outside = scaffold.root / "outside-secret.md"
            outside.write_text(
                "# NOT-A-REVIEW\n\n"
                "Verdict: approve\n\n"
                "## Summary\n\nSECRET-CONTENT\n\n"
                "## Findings\n\n- F1. [minor] SECRET-FINDING.\n",
                encoding="utf-8",
            )
            slot = scaffold.project_root / "reviews" / "REVIEW-03-008.md"
            slot.symlink_to(outside)
            report_path = scaffold.write(
                "reports/REPORT-03-008-review.md",
                _review_report(
                    report_stem="REPORT-03-008-review",
                    review_id="REVIEW-03-008",
                    prompt_path=scaffold.prompts / "PROMPT-03-008.md",
                    task_path=task_path,
                    review_path=slot,
                    status="complete",
                    verdict="approve",
                ),
            )
            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        self.assertFalse(record["path_mismatch"])
        projection = record["review_projection"]
        self.assertEqual(projection["source"], "report-blocking-findings")
        serialized = json.dumps(record)
        self.assertNotIn("SECRET-CONTENT", serialized)
        self.assertNotIn("SECRET-FINDING", serialized)

    def test_hardlinked_review_slot_is_never_projected(self) -> None:
        # A hardlink at the canonical slot aliases an outside-project inode
        # without being a symlink; the multi-link refusal must catch it.
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            task_path = scaffold.write(
                "tasks/in-review/TASK-03-009.md",
                "# TASK-03-009: demo\n\nWork root: n/a\n",
            )
            _bound_task_prompt(scaffold, task_path, "03-009")
            outside = scaffold.root / "outside-secret.md"
            outside.write_text(
                "# NOT-A-REVIEW\n\n"
                "Verdict: approve\n\n"
                "## Summary\n\nSECRET-CONTENT\n",
                encoding="utf-8",
            )
            slot = scaffold.project_root / "reviews" / "REVIEW-03-009.md"
            os.link(outside, slot)
            report_path = scaffold.write(
                "reports/REPORT-03-009-review.md",
                _review_report(
                    report_stem="REPORT-03-009-review",
                    review_id="REVIEW-03-009",
                    prompt_path=scaffold.prompts / "PROMPT-03-009.md",
                    task_path=task_path,
                    review_path=slot,
                    status="complete",
                    verdict="approve",
                ),
            )
            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        projection = record["review_projection"]
        self.assertEqual(projection["source"], "report-blocking-findings")
        self.assertNotIn("SECRET-CONTENT", json.dumps(record))

class TestContainedTaskPaths(unittest.TestCase):
    """Filename-derived task paths are contained; declared paths are never read."""

    def test_declared_task_path_is_never_dereferenced(self) -> None:
        """No fallback to the report-declared Task path.

        The declared file is invalid UTF-8: any attempt to read it would
        crash routing, so a clean exit with `requires_pr_step: false` proves
        the declared path was recorded but never opened.
        """
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            outside = scaffold.root / "outside-task.md"
            outside.write_bytes(b"\xff\xfe\x00Work root: product\n")
            # No TASK-03-010 exists on disk in any status directory.
            report_path = scaffold.write(
                "reports/REPORT-03-010.md",
                _task_report(
                    task_id="TASK-03-010",
                    prompt_path=scaffold.prompts / "PROMPT-03-010.md",
                    task_path=outside,
                    work_root="product",
                    status="complete",
                    ready_for_review="yes",
                ),
            )
            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        self.assertTrue(record["path_mismatch"])
        self.assertIsNone(record["task_path"])
        self.assertFalse(record["requires_pr_step"])

    def test_symlinked_task_slot_is_not_resolved(self) -> None:
        # A symlink planted at a task slot must not become the expected task
        # path any consumer subsequently reads.
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            outside = scaffold.root / "outside-task.md"
            outside.write_text(
                "# TASK-03-011: demo\n\nWork root: product\n",
                encoding="utf-8",
            )
            slot = scaffold.project_root / "tasks" / "in-progress" / "TASK-03-011.md"
            slot.parent.mkdir(parents=True, exist_ok=True)
            slot.symlink_to(outside)
            report_path = scaffold.write(
                "reports/REPORT-03-011.md",
                _task_report(
                    task_id="TASK-03-011",
                    prompt_path=scaffold.prompts / "PROMPT-03-011.md",
                    task_path=slot,
                    work_root="product",
                    status="complete",
                    ready_for_review="yes",
                ),
            )
            result = _run(str(report_path), home=home)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        self.assertIsNone(record["task_path"])
        self.assertTrue(record["path_mismatch"])
        self.assertFalse(record["requires_pr_step"])


class TestReviewFallback(unittest.TestCase):
    def test_review_variant_falls_back_to_report_blocking_findings(self) -> None:
        with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
            home = scaffold.root / "home"
            home.mkdir()
            task_path = scaffold.write(
                "tasks/in-review/TASK-03-004.md",
                "# TASK-03-004: demo\n\nWork root: n/a\n",
            )
            _bound_task_prompt(scaffold, task_path, "03-004")
            review_path = scaffold.project_root / "reviews" / "REVIEW-03-004.md"
            report_path = scaffold.write(
                "reports/REPORT-03-004-review.md",
                _review_report(
                    report_stem="REPORT-03-004-review",
                    review_id="REVIEW-03-004",
                    prompt_path=scaffold.prompts / "PROMPT-03-004.md",
                    task_path=task_path,
                    review_path=review_path,
                    status="blocked",
                ),
            )
            result = _run(str(report_path), home=home)

        record = _parse_single_record(result)
        projection = record["review_projection"]
        self.assertEqual(projection["source"], "report-blocking-findings")
        self.assertEqual(projection["summary"], "none.")
