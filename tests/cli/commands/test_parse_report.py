"""Tests for `cartopian parse-report` (SPEC-01-001, FR-014)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"


def _run(*cli_args, home, cwd=None):
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
    }
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), "parse-report", *cli_args],
        cwd=str(cwd) if cwd is not None else str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


TASK_BODY = (
    "# REPORT-01-006\n"
    "\n"
    "Status: complete\n"
    "\n"
    "## Identity\n"
    "\n"
    "- Task ID: TASK-01-006\n"
    "- Prompt path: /tmp/prompt.md\n"
    "- Task path: /tmp/task.md\n"
    "- Repo subpath: n/a\n"
    "\n"
    "## Files changed\n"
    "\n"
    "- foo.py — added\n"
    "\n"
    "## Test evidence\n"
    "\n"
    "- Red: ...\n"
    "- Green: ...\n"
    "\n"
    "## Commit / PR\n"
    "\n"
    "- Commit SHA: n/a\n"
    "- PR URL: n/a\n"
    "\n"
    "## Remaining risks\n"
    "\n"
    "None.\n"
    "\n"
    "## Ready for review\n"
    "\n"
    "yes\n"
)


REVIEW_BODY = (
    "# REPORT-01-006\n"
    "\n"
    "Status: complete\n"
    "\n"
    "## Identity\n"
    "\n"
    "- Review ID: REVIEW-01-006\n"
    "- Prompt path: /tmp/prompt.md\n"
    "- Review file path: /tmp/review.md\n"
    "\n"
    "## Evidence reviewed\n"
    "\n"
    "Diff and tests.\n"
    "\n"
    "## Verdict\n"
    "\n"
    "approve\n"
    "\n"
    "## Blocking findings\n"
    "\n"
    "none.\n"
)


PLAN_REVIEW_BODY = (
    "# REPORT-PLAN-002-slug\n"
    "\n"
    "Status: complete\n"
    "\n"
    "## Identity\n"
    "\n"
    "- Review ID: REVIEW-PLAN-002-slug\n"
    "- Prompt path: /tmp/prompt.md\n"
    "- Review file path: /tmp/review.md\n"
    "\n"
    "## Evidence reviewed\n"
    "\n"
    "Plan and requirements.\n"
    "\n"
    "## Verdict\n"
    "\n"
    "approve\n"
    "\n"
    "## Blocking findings\n"
    "\n"
    "none.\n"
)


def _assert_record_schema(
    testcase,
    record,
    *,
    expected_verdict,
    expected_variant,
    expected_status,
    expected_report_path,
):
    testcase.assertEqual(
        set(record.keys()),
        {"verdict", "variant", "report_path", "status"},
    )
    testcase.assertEqual(record["verdict"], expected_verdict)
    testcase.assertEqual(record["variant"], expected_variant)
    testcase.assertEqual(record["report_path"], expected_report_path)
    testcase.assertEqual(record["status"], expected_status)


class _Sandbox:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.reports = self.root / "reports"
        self.home.mkdir()
        self.reports.mkdir()

    def write_report(self, filename: str, body: str) -> Path:
        path = self.reports / filename
        _write(path, body)
        return path

    def cleanup(self):
        self._tmp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.cleanup()


class TestTaskHappy(unittest.TestCase):
    def test_emits_accepted_for_task_report(self):
        with _Sandbox() as sb:
            report = sb.write_report("REPORT-01-006.md", TASK_BODY)
            result = _run(str(report), home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(list(record.keys()),
                         ["verdict", "variant", "report_path", "status"])
        _assert_record_schema(
            self,
            record,
            expected_verdict="accepted",
            expected_variant="task",
            expected_status="complete",
            expected_report_path=str(report.resolve()),
        )


class TestReviewHappy(unittest.TestCase):
    def test_emits_accepted_for_review_report(self):
        with _Sandbox() as sb:
            report = sb.write_report("REPORT-01-006.md", REVIEW_BODY)
            result = _run(str(report), home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = json.loads(result.stdout.splitlines()[0])
        _assert_record_schema(
            self,
            record,
            expected_verdict="accepted",
            expected_variant="review",
            expected_status="complete",
            expected_report_path=str(report.resolve()),
        )


class TestPlanningReviewHappy(unittest.TestCase):
    def test_emits_accepted_for_planning_review_report(self):
        with _Sandbox() as sb:
            report = sb.write_report("REPORT-PLAN-002-slug.md", PLAN_REVIEW_BODY)
            result = _run(str(report), home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = json.loads(result.stdout.splitlines()[0])
        _assert_record_schema(
            self,
            record,
            expected_verdict="accepted",
            expected_variant="planning-review",
            expected_status="complete",
            expected_report_path=str(report.resolve()),
        )


class TestMalformedSchema(unittest.TestCase):
    def test_missing_identity_section_emits_failed_to_parse(self):
        body = (
            "# REPORT-01-006\n"
            "Status: complete\n"
            "Task ID: TASK-01-006\n"
            "\n"
            "## Files changed\n"
            "- x\n"
            "\n"
            "## Test evidence\n"
            "n/a\n"
            "\n"
            "## Commit / PR\n"
            "n/a\n"
            "\n"
            "## Remaining risks\n"
            "none\n"
            "\n"
            "## Ready for review\n"
            "yes\n"
        )
        with _Sandbox() as sb:
            report = sb.write_report("REPORT-01-006.md", body)
            result = _run(str(report), "--variant", "task", home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = json.loads(result.stdout.splitlines()[0])
        self.assertEqual(record["verdict"], "failed-to-parse")
        self.assertEqual(record["variant"], "task")
        self.assertIsNone(record["status"])


class TestMissingStatusHeader(unittest.TestCase):
    def test_missing_status_header_emits_failed_to_parse(self):
        body = TASK_BODY.replace("Status: complete\n", "")
        with _Sandbox() as sb:
            report = sb.write_report("REPORT-01-006.md", body)
            result = _run(str(report), home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = json.loads(result.stdout.splitlines()[0])
        self.assertEqual(record["verdict"], "failed-to-parse")
        self.assertIsNone(record["status"])
        self.assertEqual(record["variant"], "task")


class TestUnknownStatusValue(unittest.TestCase):
    def test_unknown_status_value_emits_failed_to_parse(self):
        body = TASK_BODY.replace("Status: complete", "Status: weird")
        with _Sandbox() as sb:
            report = sb.write_report("REPORT-01-006.md", body)
            result = _run(str(report), home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = json.loads(result.stdout.splitlines()[0])
        self.assertEqual(record["verdict"], "failed-to-parse")
        self.assertIsNone(record["status"])
        self.assertEqual(record["variant"], "task")


class TestVariantAmbiguity(unittest.TestCase):
    def test_plan_filename_with_task_content_exits_usage(self):
        with _Sandbox() as sb:
            report = sb.write_report("REPORT-PLAN-002-slug.md", TASK_BODY)
            result = _run(str(report), home=sb.home)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("[usage] ambiguous variant", result.stderr)


class TestVariantUnresolvable(unittest.TestCase):
    def test_no_markers_exits_usage(self):
        body = (
            "# REPORT-01-006\n"
            "Status: complete\n"
            "\n"
            "## Some Section\n"
            "Nothing useful here.\n"
        )
        with _Sandbox() as sb:
            report = sb.write_report("REPORT-01-006.md", body)
            result = _run(str(report), home=sb.home)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("[usage] cannot infer variant", result.stderr)


class TestFileMissing(unittest.TestCase):
    def test_missing_file_exits_fail(self):
        with _Sandbox() as sb:
            missing = sb.reports / "REPORT-01-006.md"
            result = _run(str(missing), home=sb.home)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("[error] report not found:", result.stderr)


class TestRejectsRelativeReportPath(unittest.TestCase):
    def test_relative_report_path_exits_usage(self):
        with _Sandbox() as sb:
            report = sb.write_report("REPORT-task.md", TASK_BODY)
            result = _run("REPORT-task.md", home=sb.home, cwd=report.parent)
        self.assertEqual(result.returncode, 2, msg=result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn(
            "[usage] report_path must be an absolute path; got: REPORT-task.md",
            result.stderr,
        )


class TestExplicitVariantOverridesFilename(unittest.TestCase):
    def test_explicit_task_variant_against_plan_filename(self):
        with _Sandbox() as sb:
            report = sb.write_report("REPORT-PLAN-002-slug.md", TASK_BODY)
            result = _run(str(report), "--variant", "task", home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        record = json.loads(result.stdout.splitlines()[0])
        self.assertEqual(record["variant"], "task")
        self.assertEqual(record["verdict"], "accepted")
        self.assertEqual(record["status"], "complete")


if __name__ == "__main__":
    unittest.main()
