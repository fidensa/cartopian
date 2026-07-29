"""Synthetic evidence for retention-only automated-handoff output safety."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli import handoff_observer, output_safety


REPORT_TEXT = (
    "# REPORT-01-001\n\n"
    "Status: complete\n\n"
    "## Identity\n\n"
    "- Work root: n/a\n\n"
    "## Completion evidence\n\n"
    "synthetic report complete\n\n"
    "## Remaining risks\n\n"
    "none\n\n"
    "## Ready to close\n\n"
    "yes\n"
)


class OutputSafetySupervisorTests(unittest.TestCase):
    def _supervise_script(
        self,
        root: Path,
        source: str,
        *,
        limits: output_safety.OutputLimits | None = None,
        report_path: Path | None = None,
        log_path: Path | None = None,
    ) -> output_safety.SupervisionResult:
        child = root / "child.py"
        child.write_text(source, encoding="utf-8")
        return output_safety.supervise(
            [sys.executable, str(child)],
            status_path=root / "REPORT-01-001.md.status",
            report_path=report_path or root / "REPORT-01-001.md",
            log_path=(
                log_path
                if log_path is not None
                else root / "REPORT-01-001.md.launch.log"
            ),
            launch_id="synthetic",
            expected_variant="task",
            limits=limits or output_safety.OutputLimits(),
        )

    def test_output_beyond_former_thresholds_publishes_report_and_truncates_log(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="cartopian-retention-large-") as tmp:
            root = Path(tmp)
            report = root / "REPORT-01-001.md"
            result = self._supervise_script(
                root,
                "import os\n"
                "for index in range(3000):\n"
                "    os.write(1 if index % 2 == 0 else 2, "
                "f'{index:04d}-'.encode() + b'x' * 96 + b'\\n')\n"
                f"open({str(report)!r}, 'w', encoding='utf-8').write({REPORT_TEXT!r})\n",
                report_path=report,
                limits=output_safety.OutputLimits(log_bytes=4096, log_lines=12),
            )

            retained = Path(str(report) + ".launch.log").read_bytes()
            self.assertEqual(result.exit_code, 0)
            self.assertTrue(result.report_present)
            self.assertEqual(result.retention_state, "truncated")
            self.assertLessEqual(len(retained), 4096)
            self.assertLessEqual(output_safety.count_lines(retained), 12)
            self.assertIn(b"retained launch log truncated", retained)
            self.assertEqual(
                handoff_observer.observe_report(report, "task").publication_state,
                "complete",
            )

            fields = dict(
                line.split("=", 1)
                for line in Path(str(report) + ".status")
                .read_text(encoding="utf-8")
                .splitlines()
            )
            self.assertEqual(fields["reason"], "clean")
            self.assertEqual(fields["log_byte_limit"], "4096")
            self.assertEqual(fields["log_line_limit"], "12")
            self.assertEqual(fields["retention_state"], "truncated")
            self.assertEqual(fields["guarantee_scope"], "retained-launch-log")
            self.assertNotIn("classification", fields)
            self.assertNotIn("observed_bytes", fields)
            self.assertNotIn("observed_lines", fields)

    def test_large_source_artifact_and_report_are_complete_and_unmodified(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cartopian-retention-artifacts-") as tmp:
            root = Path(tmp)
            artifact = root / "large-source.txt"
            report = root / "REPORT-01-001.md"
            artifact_payload = b"source-content-0123456789\n" * 12_000
            filler = "report-evidence-0123456789\n" * 12_000
            report_payload = REPORT_TEXT.replace(
                "synthetic report complete", filler
            )
            result = self._supervise_script(
                root,
                f"open({str(artifact)!r}, 'wb').write({artifact_payload!r})\n"
                f"open({str(report)!r}, 'w', encoding='utf-8').write({report_payload!r})\n"
                "print('published')\n",
                report_path=report,
                limits=output_safety.OutputLimits(log_bytes=256, log_lines=4),
            )

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(artifact.read_bytes(), artifact_payload)
            self.assertEqual(report.read_text(encoding="utf-8"), report_payload)
            self.assertGreater(artifact.stat().st_size, 256 * 1024)
            self.assertGreater(report.stat().st_size, 256 * 1024)
            self.assertEqual(
                handoff_observer.observe_report(report, "task").publication_state,
                "complete",
            )

    def test_verbose_timeout_remains_timeout_not_output_volume_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cartopian-retention-timeout-") as tmp:
            root = Path(tmp)
            result = self._supervise_script(
                root,
                "import os\n"
                "for _ in range(4000):\n"
                "    os.write(1, b'x' * 96 + b'\\n')\n"
                "raise SystemExit(124)\n",
                limits=output_safety.OutputLimits(log_bytes=512, log_lines=6),
            )
            self.assertEqual(result.exit_code, 124)
            self.assertEqual(result.retention_state, "truncated")
            status = (root / "REPORT-01-001.md.status").read_text(encoding="utf-8")
            self.assertIn("reason=timeout\n", status)
            self.assertNotIn("classification=", status)

    def test_huge_stderr_crlf_and_invalid_utf8_are_drained_safely(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cartopian-retention-bytes-") as tmp:
            root = Path(tmp)
            result = self._supervise_script(
                root,
                "import os\n"
                "payload = b'\\xff\\xe2\\x82\\xac' * 40 + b'\\r\\n'\n"
                "for index in range(3000):\n"
                "    os.write(1 if index % 2 == 0 else 2, payload)\n",
                limits=output_safety.OutputLimits(log_bytes=300, log_lines=3),
            )
            retained = (root / "REPORT-01-001.md.launch.log").read_bytes()
            self.assertEqual(result.exit_code, 0)
            self.assertLessEqual(len(retained), 300)
            self.assertLessEqual(output_safety.count_lines(retained), 3)
            self.assertIn(b"truncated", retained)

    def test_success_failure_and_timeout_exit_codes_are_preserved(self) -> None:
        cases = (
            ("print('ok')\n", 0, "clean"),
            ("raise SystemExit(7)\n", 7, "error"),
            ("raise SystemExit(124)\n", 124, "timeout"),
        )
        for index, (source, expected_exit, expected_reason) in enumerate(cases):
            with self.subTest(expected_exit=expected_exit), tempfile.TemporaryDirectory(
                prefix=f"cartopian-retention-exit-{index}-"
            ) as tmp:
                root = Path(tmp)
                result = self._supervise_script(root, source)
                self.assertEqual(result.exit_code, expected_exit)
                status = (root / "REPORT-01-001.md.status").read_text(encoding="utf-8")
                self.assertIn(f"reason={expected_reason}\n", status)

    def test_wrapper_creation_failure_replaces_running_status(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cartopian-retention-launch-") as tmp:
            root = Path(tmp)
            result = output_safety.supervise(
                [str(root / "missing-wrapper")],
                status_path=root / "REPORT-01-001.md.status",
                report_path=root / "REPORT-01-001.md",
                log_path=root / "REPORT-01-001.md.launch.log",
                launch_id="synthetic-launch-failure",
                expected_variant="task",
                limits=output_safety.OutputLimits(),
            )
            self.assertEqual(result.exit_code, 1)
            status = (root / "REPORT-01-001.md.status").read_text(encoding="utf-8")
            self.assertIn("state=exited\n", status)
            self.assertIn("reason=error\n", status)

    def test_unavailable_unsafe_log_destination_does_not_fail_child(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cartopian-retention-unsafe-") as tmp:
            root = Path(tmp)
            log_path = root / "REPORT-01-001.md.launch.log"
            log_path.mkdir()
            result = self._supervise_script(root, "print('ok')\n", log_path=log_path)
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.retention_state, "unavailable")
            self.assertIsNone(result.retained_log_path)
            self.assertTrue(log_path.is_dir())

    def test_authoritative_report_still_ends_verbose_post_report_process(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cartopian-retention-report-") as tmp:
            root = Path(tmp)
            report = root / "REPORT-01-001.md"
            result = self._supervise_script(
                root,
                f"open({str(report)!r}, 'w', encoding='utf-8').write({REPORT_TEXT!r})\n"
                "import os\n"
                "while True:\n"
                "    os.write(1, b'post-report output\\n')\n",
                report_path=report,
                limits=output_safety.OutputLimits(log_bytes=256, log_lines=4),
            )
            self.assertEqual(result.exit_code, 0)
            self.assertTrue(result.report_present)

    def test_observer_does_not_read_launch_log_body(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cartopian-retention-observer-") as tmp:
            root = Path(tmp)
            report = root / "REPORT-01-001.md"
            status = Path(str(report) + ".status")
            launch_log = Path(str(report) + ".launch.log")
            report.write_text(REPORT_TEXT, encoding="utf-8")
            status.write_text(
                "state=exited\nexit_code=0\nreason=clean\n"
                "expected_variant=task\nretention_state=truncated\n",
                encoding="utf-8",
            )
            launch_log.write_bytes(b"secret diagnostic body" * 10_000)
            original_read_text = Path.read_text

            def guarded_read_text(path: Path, *args, **kwargs):
                if str(path).endswith(".launch.log"):
                    raise AssertionError("observer read launch-log body")
                return original_read_text(path, *args, **kwargs)

            with mock.patch.object(Path, "read_text", guarded_read_text):
                observation = handoff_observer.observe_once(report, expected_variant="task")
                record = handoff_observer.record_fields(observation)
            self.assertTrue(observation.terminal)
            self.assertEqual(observation.classification, "accepted")
            self.assertNotIn("output_overflow", record)


class OutputSafetyAccountingTests(unittest.TestCase):
    def test_newline_rule_is_lf_based_and_counts_one_trailing_fragment(self) -> None:
        cases = {
            b"": 0,
            b"one": 1,
            b"one\n": 1,
            b"one\r\n": 1,
            b"one\ntwo": 2,
            b"\xff\n\xe2\x82\xac": 2,
        }
        for payload, expected in cases.items():
            with self.subTest(payload=payload):
                self.assertEqual(output_safety.count_lines(payload), expected)

    def test_invalid_retention_overrides_fail_before_launch(self) -> None:
        for name in (
            output_safety.LOG_BYTE_LIMIT_ENV,
            output_safety.LOG_LINE_LIMIT_ENV,
        ):
            for raw in ("0", "-1", "nope"):
                with self.subTest(name=name, raw=raw):
                    with self.assertRaises(output_safety.OutputSafetyError):
                        output_safety.limits_from_environment({name: raw})

    def test_retired_stream_settings_are_ignored(self) -> None:
        environ = {
            "CARTOPIAN_COMMAND_OUTPUT_GUIDANCE": "stale",
            "CARTOPIAN_STREAM_BYTE_LIMIT": "invalid",
            "CARTOPIAN_STREAM_LINE_LIMIT": "0",
        }
        limits = output_safety.limits_from_environment(environ)
        output_safety.project_environment(environ, limits, None)
        self.assertNotIn("CARTOPIAN_COMMAND_OUTPUT_GUIDANCE", environ)
        self.assertNotIn("CARTOPIAN_STREAM_BYTE_LIMIT", environ)
        self.assertNotIn("CARTOPIAN_STREAM_LINE_LIMIT", environ)
        self.assertEqual(environ[output_safety.LOG_BYTE_LIMIT_ENV], "65536")
        self.assertEqual(environ[output_safety.LOG_LINE_LIMIT_ENV], "400")


class OutputSafetySurfaceParityTests(unittest.TestCase):
    def test_all_shipped_agent_surfaces_route_through_agent_neutral_dispatch(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        for agent in ("codex", "claude", "gemini", "devin"):
            with self.subTest(agent=agent, surface="posix"):
                self.assertTrue((root / "wrappers" / "bin" / f"cartopian-{agent}").is_file())
            with self.subTest(agent=agent, surface="powershell"):
                self.assertTrue((root / "wrappers" / "ps1" / f"cartopian-{agent}.ps1").is_file())
            with self.subTest(agent=agent, surface="cmd"):
                self.assertTrue((root / "wrappers" / "ps1" / f"cartopian-{agent}.cmd").is_file())
        dispatch_source = (root / "cli" / "commands" / "dispatch.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("output_safety.__file__", dispatch_source)
        self.assertIn("supervisor_argv", dispatch_source)
        self.assertNotIn("if agent == ", dispatch_source)

    def test_active_surfaces_describe_retention_only(self) -> None:
        root = Path(__file__).resolve().parents[2]
        paths = (
            root / "cli" / "commands" / "dispatch.py",
            root / "cli" / "commands" / "wait_handoff.py",
            root / "cli" / "commands" / "wait_report.py",
            root / "protocol" / "CONVENTIONS.md",
            root / "skills" / "run-handoff.md",
            root / "templates" / "PROMPT.md",
            root / "wrappers" / "README.md",
        )
        retired = (
            "CARTOPIAN_STREAM_BYTE_LIMIT",
            "CARTOPIAN_STREAM_LINE_LIMIT",
            "classification=output-overflow",
            "command-output budget",
        )
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for phrase in retired:
                with self.subTest(path=path, phrase=phrase):
                    self.assertNotIn(phrase, text)

        runtime = (root / "cli" / "output_safety.py").read_text(encoding="utf-8")
        for retired_route in (
            "OUTPUT_OVERFLOW",
            "DEFAULT_STREAM",
            "--stream-bytes",
            "--stream-lines",
            "classification=output-overflow",
        ):
            with self.subTest(runtime_route=retired_route):
                self.assertNotIn(retired_route, runtime)


if __name__ == "__main__":
    unittest.main()
