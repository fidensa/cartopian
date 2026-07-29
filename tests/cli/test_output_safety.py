"""Synthetic evidence for the automated-handoff output safety boundary."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli import handoff_observer, output_safety


class OutputSafetySupervisorTests(unittest.TestCase):
    def _supervise_script(
        self,
        root: Path,
        source: str,
        *,
        limits: output_safety.OutputLimits,
        report_path: Path | None = None,
    ) -> output_safety.SupervisionResult:
        child = root / "child.py"
        child.write_text(source, encoding="utf-8")
        return output_safety.supervise(
            [sys.executable, str(child)],
            status_path=root / "REPORT-01-001.md.status",
            report_path=report_path or root / "REPORT-01-001.md",
            log_path=root / "REPORT-01-001.md.launch.log",
            launch_id="synthetic",
            expected_variant="task",
            limits=limits,
        )

    def test_continuous_output_is_contained_and_retention_is_independently_bounded(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="cartopian-output-overflow-") as tmp:
            root = Path(tmp)
            status_path = root / "REPORT-01-001.md.status"
            log_path = root / "REPORT-01-001.md.launch.log"
            child = root / "emit_forever.py"
            child.write_text(
                "import os\n"
                "chunk = b'0123456789abcdef\\n' * 64\n"
                "while True:\n"
                "    os.write(1, chunk)\n",
                encoding="utf-8",
            )

            result = output_safety.supervise(
                [sys.executable, str(child)],
                status_path=status_path,
                report_path=root / "REPORT-01-001.md",
                log_path=log_path,
                launch_id="synthetic-overflow",
                expected_variant="task",
                limits=output_safety.OutputLimits(
                    stream_bytes=4096,
                    stream_lines=1000,
                    log_bytes=512,
                    log_lines=20,
                ),
            )

            self.assertEqual(result.exit_code, output_safety.OUTPUT_OVERFLOW_EXIT)
            self.assertEqual(result.classification, "output-overflow")
            self.assertTrue(result.terminated)
            self.assertEqual(result.observed_bytes, 4097)
            self.assertLessEqual(result.observed_lines, 1000)
            self.assertLessEqual(log_path.stat().st_size, 512)
            self.assertLessEqual(
                output_safety.count_lines(log_path.read_bytes()),
                20,
            )
            if os.name != "nt":
                self.assertEqual(log_path.stat().st_mode & 0o777, 0o600)

            fields = {}
            for line in status_path.read_text(encoding="utf-8").splitlines():
                key, value = line.split("=", 1)
                fields[key] = value
            self.assertEqual(fields["classification"], "output-overflow")
            self.assertEqual(fields["stream_byte_limit"], "4096")
            self.assertEqual(fields["stream_line_limit"], "1000")
            self.assertEqual(fields["log_byte_limit"], "512")
            self.assertEqual(fields["log_line_limit"], "20")
            self.assertEqual(fields["observed_bytes"], "4097")
            self.assertEqual(fields["retained_log_path"], str(log_path))
            self.assertEqual(
                fields["guarantee_scope"],
                "observable-wrapper-stream",
            )
            self.assertEqual(fields["report_present"], "false")

            observation = handoff_observer.observe_once(
                root / "REPORT-01-001.md",
                expected_variant="task",
            )
            self.assertTrue(observation.terminal)
            self.assertEqual(observation.classification, "output-overflow")
            record = handoff_observer.record_fields(observation)
            self.assertEqual(
                record["output_overflow"]["observed_bytes"],
                4097,
            )
            self.assertFalse(
                record["output_overflow"]["pre_model_ingestion_guaranteed"]
            )

    def test_stdout_stderr_alternation_and_line_overflow_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cartopian-output-lines-") as tmp:
            root = Path(tmp)
            result = self._supervise_script(
                root,
                "import os\n"
                "for index in range(1000):\n"
                "    os.write(1 if index % 2 == 0 else 2, b'x\\r\\n')\n",
                limits=output_safety.OutputLimits(
                    stream_bytes=100_000,
                    stream_lines=5,
                    log_bytes=100,
                    log_lines=3,
                ),
            )
            self.assertEqual(result.classification, "output-overflow")
            self.assertEqual(result.observed_lines, 6)
            self.assertEqual(result.observed_bytes, 16)
            self.assertLessEqual(result.retained_lines, 3)

    def test_huge_stderr_and_invalid_multibyte_bytes_use_raw_byte_accounting(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="cartopian-output-stderr-") as tmp:
            root = Path(tmp)
            result = self._supervise_script(
                root,
                "import os\n"
                "payload = (b'\\xff\\xe2\\x82\\xac-no-newline' * 1024)\n"
                "while True:\n"
                "    os.write(2, payload)\n",
                limits=output_safety.OutputLimits(
                    stream_bytes=2048,
                    stream_lines=100,
                    log_bytes=256,
                    log_lines=2,
                ),
            )
            self.assertEqual(result.observed_bytes, 2049)
            self.assertEqual(result.observed_lines, 1)
            self.assertLessEqual(result.retained_bytes, 256)

    def test_success_failure_and_timeout_exit_codes_are_preserved_under_limits(
        self,
    ) -> None:
        cases = (
            ("import os\nos.write(1, b'ok\\n')\n", 0, b"ok\n"),
            ("import os\nos.write(2, b'ordinary failure\\n')\nraise SystemExit(7)\n", 7, b"ordinary failure\n"),
            ("raise SystemExit(124)\n", 124, b""),
        )
        for index, (source, expected_exit, expected_log) in enumerate(cases):
            with self.subTest(expected_exit=expected_exit), tempfile.TemporaryDirectory(
                prefix=f"cartopian-output-exit-{index}-"
            ) as tmp:
                root = Path(tmp)
                result = self._supervise_script(
                    root,
                    source,
                    limits=output_safety.OutputLimits(
                        stream_bytes=4096,
                        stream_lines=100,
                        log_bytes=1024,
                        log_lines=50,
                    ),
                )
                self.assertEqual(result.exit_code, expected_exit)
                self.assertIsNone(result.classification)
                self.assertEqual(
                    (root / "REPORT-01-001.md.launch.log").read_bytes(),
                    expected_log,
                )
                status_text = (
                    root / "REPORT-01-001.md.status"
                ).read_text(encoding="utf-8")
                self.assertIn(f"exit_code={expected_exit}\n", status_text)
                expected_reason = (
                    "clean"
                    if expected_exit == 0
                    else "timeout"
                    if expected_exit == 124
                    else "error"
                )
                self.assertIn(f"reason={expected_reason}\n", status_text)

    def test_wrapper_creation_failure_replaces_running_status(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cartopian-output-launch-") as tmp:
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
            self.assertEqual(result.termination_result, "wrapper-launch-failed")
            status_text = (
                root / "REPORT-01-001.md.status"
            ).read_text(encoding="utf-8")
            self.assertIn("state=exited\n", status_text)
            self.assertIn("reason=error\n", status_text)

    def test_log_retention_truncates_independently_without_stream_overflow(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cartopian-output-logcap-") as tmp:
            root = Path(tmp)
            result = self._supervise_script(
                root,
                "import os\n"
                "for index in range(100):\n"
                "    os.write(1, f'{index:03d} diagnostic line\\n'.encode())\n",
                limits=output_safety.OutputLimits(
                    stream_bytes=10_000,
                    stream_lines=200,
                    log_bytes=160,
                    log_lines=5,
                ),
            )
            retained = (root / "REPORT-01-001.md.launch.log").read_bytes()
            self.assertEqual(result.exit_code, 0)
            self.assertLessEqual(len(retained), 160)
            self.assertLessEqual(output_safety.count_lines(retained), 5)
            self.assertIn(b"truncated", retained)
            self.assertIn(b"099 diagnostic line", retained)

    def test_authoritative_early_report_wins_only_before_overflow_boundary(self) -> None:
        report_text = (
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
        with tempfile.TemporaryDirectory(prefix="cartopian-output-report-") as tmp:
            root = Path(tmp)
            report_path = root / "REPORT-01-001.md"
            status_path = Path(str(report_path) + ".status")
            status_path.write_text(
                "state=running\n"
                "launch_id=synthetic\n"
                "expected_variant=task\n",
                encoding="utf-8",
            )
            source = (
                "import os\n"
                f"open({str(report_path)!r}, 'w', encoding='utf-8').write({report_text!r})\n"
                "while True:\n"
                "    os.write(1, b'x' * 1024)\n"
            )
            result = self._supervise_script(
                root,
                source,
                report_path=report_path,
                limits=output_safety.OutputLimits(
                    stream_bytes=1024,
                    stream_lines=100,
                    log_bytes=256,
                    log_lines=10,
                ),
            )
            self.assertEqual(result.exit_code, 0)
            self.assertIsNone(result.classification)
            self.assertTrue(result.report_present)
            fields = dict(
                line.split("=", 1)
                for line in status_path.read_text(encoding="utf-8").splitlines()
            )
            self.assertEqual(fields["state"], "exited")
            self.assertEqual(fields["exit_code"], "0")
            self.assertEqual(fields["reason"], "clean")
            self.assertEqual(fields["launch_id"], "synthetic")
            self.assertEqual(fields["expected_variant"], "task")
            self.assertEqual(fields["observed_bytes"], str(result.observed_bytes))
            self.assertEqual(fields["observed_lines"], str(result.observed_lines))
            self.assertEqual(
                fields["retained_log_path"],
                str(root / "REPORT-01-001.md.launch.log"),
            )
            self.assertEqual(fields["retained_bytes"], str(result.retained_bytes))
            self.assertEqual(fields["retained_lines"], str(result.retained_lines))
            self.assertEqual(
                fields["termination_result"],
                result.termination_result,
            )
            self.assertEqual(fields["terminated"], str(result.terminated).lower())
            self.assertEqual(fields["report_present"], "true")
            self.assertEqual(
                fields["guarantee_scope"],
                output_safety.GUARANTEE_SCOPE,
            )

    def test_observer_does_not_read_launch_log_body(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cartopian-output-observer-") as tmp:
            root = Path(tmp)
            report = root / "REPORT-01-001.md"
            status = Path(str(report) + ".status")
            launch_log = Path(str(report) + ".launch.log")
            status.write_text(
                "state=exited\n"
                "exit_code=125\n"
                "reason=output-overflow\n"
                "classification=output-overflow\n"
                "expected_variant=task\n",
                encoding="utf-8",
            )
            report.write_text("# REPORT-01-001\n\nStatus: complete\n", encoding="utf-8")
            launch_log.write_bytes(b"x" * 100_000)
            original_read_text = Path.read_text

            def guarded_read_text(path: Path, *args, **kwargs):
                if str(path).endswith(".launch.log"):
                    raise AssertionError("observer read launch-log body")
                return original_read_text(path, *args, **kwargs)

            with mock.patch.object(Path, "read_text", guarded_read_text):
                observation = handoff_observer.observe_once(
                    report, expected_variant="task"
                )
            self.assertEqual(observation.classification, "output-overflow")


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

    def test_shipped_defaults_include_the_command_budget(self) -> None:
        self.assertEqual(output_safety.COMMAND_OUTPUT_LINE_LIMIT, 200)
        self.assertEqual(output_safety.COMMAND_OUTPUT_BYTE_LIMIT, 32 * 1024)
        self.assertIn("200 lines", output_safety.command_output_guidance())
        self.assertIn("32 KiB", output_safety.command_output_guidance())

    def test_command_guidance_is_rendered_from_both_authoritative_constants(
        self,
    ) -> None:
        with (
            mock.patch.object(output_safety, "COMMAND_OUTPUT_LINE_LIMIT", 17),
            mock.patch.object(output_safety, "COMMAND_OUTPUT_BYTE_LIMIT", 5 * 1024),
        ):
            guidance = output_safety.command_output_guidance()
        self.assertIn("17 lines", guidance)
        self.assertIn("5 KiB", guidance)
        self.assertNotIn("200 lines", guidance)
        self.assertNotIn("32 KiB", guidance)

    def test_invalid_limit_overrides_fail_before_launch(self) -> None:
        for raw in ("0", "-1", "nope"):
            with self.subTest(raw=raw):
                with self.assertRaises(output_safety.OutputSafetyError):
                    output_safety.limits_from_environment(
                        {output_safety.STREAM_BYTE_LIMIT_ENV: raw}
                    )

    def test_prompt_guidance_is_idempotently_generated(self) -> None:
        once = output_safety.upsert_command_output_guidance("# Assignment\n")
        twice = output_safety.upsert_command_output_guidance(once)
        self.assertEqual(once, twice)
        self.assertEqual(once.count("## Cartopian command-output budget"), 1)


class OutputSafetySurfaceParityTests(unittest.TestCase):
    def test_all_shipped_agent_surfaces_route_through_agent_neutral_dispatch(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        agents = ("codex", "claude", "gemini", "devin")
        for agent in agents:
            with self.subTest(agent=agent, surface="posix"):
                self.assertTrue((root / "wrappers" / "bin" / f"cartopian-{agent}").is_file())
            with self.subTest(agent=agent, surface="powershell"):
                self.assertTrue(
                    (root / "wrappers" / "ps1" / f"cartopian-{agent}.ps1").is_file()
                )
            with self.subTest(agent=agent, surface="cmd"):
                self.assertTrue(
                    (root / "wrappers" / "ps1" / f"cartopian-{agent}.cmd").is_file()
                )
        dispatch_source = (
            root / "cli" / "commands" / "dispatch.py"
        ).read_text(encoding="utf-8")
        self.assertIn("output_safety.__file__", dispatch_source)
        self.assertIn("supervisor_argv", dispatch_source)
        self.assertNotIn("if agent == ", dispatch_source)

    def test_normalized_environment_projects_every_limit(self) -> None:
        limits = output_safety.OutputLimits(
            stream_bytes=111,
            stream_lines=22,
            log_bytes=33,
            log_lines=4,
        )
        environ = {name: "stale" for name in output_safety.OUTPUT_SAFETY_ENV_VARS}
        output_safety.project_environment(environ, limits, None)
        self.assertEqual(environ[output_safety.STREAM_BYTE_LIMIT_ENV], "111")
        self.assertEqual(environ[output_safety.STREAM_LINE_LIMIT_ENV], "22")
        self.assertEqual(environ[output_safety.LOG_BYTE_LIMIT_ENV], "33")
        self.assertEqual(environ[output_safety.LOG_LINE_LIMIT_ENV], "4")
        self.assertNotIn(output_safety.LAUNCH_LOG_PATH_ENV, environ)


if __name__ == "__main__":
    unittest.main()
