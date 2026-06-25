"""Tests for `cartopian wait-report`.

`wait-report` filesystem-polls until a handoff report exists and reaches an
`accepted` outcome under the `report-action` aggregator's verdict semantics,
or until the `--max-block` budget elapses. It is read-only and stdlib-only.
"""
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from tests.scaffold import project_scaffold

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"


def _run(*args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    env = {
        "HOME": os.environ.get("HOME", ""),
        "PATH": os.environ.get("PATH", ""),
    }
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), "wait-report", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _parse_single_record(result: subprocess.CompletedProcess[str]) -> dict:
    lines = result.stdout.splitlines()
    if len(lines) != 1:
        raise AssertionError(
            f"expected 1 stdout line, got {len(lines)}: "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    return json.loads(lines[0])


def _task_report(*, report_stem: str, task_id: str, prompt_path: Path, task_path: Path, status: str) -> str:
    return (
        f"# {report_stem}\n\n"
        f"Status: {status}\n\n"
        "## Identity\n\n"
        f"- Task ID: {task_id}\n"
        f"- Prompt path: {prompt_path}\n"
        f"- Task path: {task_path}\n"
        "- Work root: tool-repo\n\n"
        "## Files changed\n\n"
        "- cli/commands/wait_report.py — added\n\n"
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
    )


def _snapshot_tree(root: Path) -> dict:
    snapshot: dict = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes()
        stat = path.stat()
        snapshot[rel] = (stat.st_size, hashlib.sha256(data).hexdigest(), stat.st_mtime_ns)
    return snapshot


class TestWaitReportTimeout(unittest.TestCase):
    """Evidence-gate target: blocking against a missing report ends in still_running."""

    def test_missing_report_blocks_then_emits_still_running(self) -> None:
        with project_scaffold() as scaffold:
            report_path = scaffold.reports / "REPORT-01-002.md"
            self.assertFalse(report_path.exists())

            start = time.monotonic()
            result = _run(str(report_path), "--max-block", "1s", "--poll-interval", "0.05")
            elapsed = time.monotonic() - start

        # It must have actually blocked for ~the full budget, not returned early.
        self.assertGreaterEqual(elapsed, 0.9, msg=f"returned too early: {elapsed:.3f}s")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        self.assertIs(record["still_running"], True)
        self.assertIs(record["accepted"], False)
        self.assertEqual(record["report_path"], str(report_path.resolve()))

    def test_report_created_during_wait_is_accepted(self) -> None:
        with project_scaffold() as scaffold:
            report_path = scaffold.reports / "REPORT-01-002.md"
            content = _task_report(
                report_stem="REPORT-01-002",
                task_id="TASK-01-002",
                prompt_path=scaffold.prompts / "PROMPT-01-002.md",
                task_path=scaffold.project_root / "tasks" / "in-progress" / "TASK-01-002-wait-report.md",
                status="complete",
            )

            def _create_later() -> None:
                time.sleep(0.4)
                report_path.write_text(content, encoding="utf-8")

            writer = threading.Thread(target=_create_later)
            writer.start()
            try:
                result = _run(str(report_path), "--max-block", "10s", "--poll-interval", "0.05")
            finally:
                writer.join()

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = _parse_single_record(result)
        self.assertIs(record["accepted"], True)
        self.assertIs(record["still_running"], False)
        self.assertEqual(record["verdict"], "accepted")


class TestWaitReportAccepted(unittest.TestCase):
    def test_existing_accepted_report_exits_zero_immediately(self) -> None:
        with project_scaffold() as scaffold:
            report_path = scaffold.write(
                "reports/REPORT-01-002.md",
                _task_report(
                    report_stem="REPORT-01-002",
                    task_id="TASK-01-002",
                    prompt_path=scaffold.prompts / "PROMPT-01-002.md",
                    task_path=scaffold.project_root / "tasks" / "in-progress" / "TASK-01-002-wait-report.md",
                    status="complete",
                ),
            )
            result = _run(str(report_path), "--max-block", "30s")

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        record = _parse_single_record(result)
        self.assertEqual(record["verdict"], "accepted")
        self.assertIs(record["accepted"], True)
        self.assertIs(record["still_running"], False)
        self.assertEqual(record["report_path"], str(report_path.resolve()))


class TestWaitReportGuard(unittest.TestCase):
    def test_blocked_report_exits_one_with_guard_prefix(self) -> None:
        with project_scaffold() as scaffold:
            report_path = scaffold.write(
                "reports/REPORT-01-002.md",
                _task_report(
                    report_stem="REPORT-01-002",
                    task_id="TASK-01-002",
                    prompt_path=scaffold.prompts / "PROMPT-01-002.md",
                    task_path=scaffold.project_root / "tasks" / "in-progress" / "TASK-01-002-wait-report.md",
                    status="blocked",
                ),
            )
            result = _run(str(report_path), "--max-block", "30s")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertTrue(
            result.stderr.startswith("[guard]"),
            msg=f"expected [guard] prefix, got: {result.stderr!r}",
        )
        self.assertIn("blocked", result.stderr)

    def test_unparseable_report_exits_one_with_guard_prefix(self) -> None:
        with project_scaffold() as scaffold:
            # Present but missing required sections → report-action verdict
            # is `failed-to-parse`, which is not `accepted`.
            report_path = scaffold.write(
                "reports/REPORT-01-002.md",
                "# REPORT-01-002\n\nStatus: complete\n\n## Identity\n\n- Task ID: TASK-01-002\n",
            )
            result = _run(str(report_path), "--max-block", "30s")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.startswith("[guard]"))


class TestWaitReportArgParsing(unittest.TestCase):
    def test_duration_units_parse(self) -> None:
        # Validate that each documented unit (s/m/h) is accepted by the
        # duration parser. An already-accepted report returns immediately, so
        # the large budgets never actually elapse — we only exercise parsing.
        with project_scaffold() as scaffold:
            content = _task_report(
                report_stem="REPORT-OK",
                task_id="TASK-01-002",
                prompt_path=scaffold.prompts / "PROMPT-01-002.md",
                task_path=scaffold.project_root / "tasks" / "in-progress" / "TASK-01-002-wait-report.md",
                status="complete",
            )
            ok_path = scaffold.write("reports/REPORT-OK.md", content)
            for budget in ("30s", "1m", "5h"):
                with self.subTest(budget=budget):
                    result = _run(str(ok_path), "--max-block", budget)
                    self.assertEqual(result.returncode, 0, msg=result.stderr)
                    record = _parse_single_record(result)
                    self.assertIs(record["accepted"], True)

    def test_invalid_duration_exits_usage(self) -> None:
        with project_scaffold() as scaffold:
            report_path = scaffold.reports / "REPORT-01-002.md"
            result = _run(str(report_path), "--max-block", "later")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.startswith("[usage]"))

    def test_relative_report_path_exits_usage(self) -> None:
        result = _run("reports/REPORT-01-002.md", "--max-block", "1s")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.startswith("[usage]"))

    def test_missing_max_block_exits_usage(self) -> None:
        with project_scaffold() as scaffold:
            report_path = scaffold.reports / "REPORT-01-002.md"
            result = _run(str(report_path))
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stderr.startswith("[usage]"))


class TestWaitReportReadOnly(unittest.TestCase):
    def test_does_not_modify_project_tree(self) -> None:
        with project_scaffold() as scaffold:
            report_path = scaffold.write(
                "reports/REPORT-01-002.md",
                _task_report(
                    report_stem="REPORT-01-002",
                    task_id="TASK-01-002",
                    prompt_path=scaffold.prompts / "PROMPT-01-002.md",
                    task_path=scaffold.project_root / "tasks" / "in-progress" / "TASK-01-002-wait-report.md",
                    status="complete",
                ),
            )
            before = _snapshot_tree(scaffold.project_root)
            result = _run(str(report_path), "--max-block", "30s")
            after = _snapshot_tree(scaffold.project_root)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
