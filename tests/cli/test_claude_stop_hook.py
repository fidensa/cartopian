"""Tests for the Claude Code completion adapter (``cli/claude_stop_hook.py``).

The adapter is a Claude Code **Stop** hook that refuses to let a ``claude -p``
handoff end its turn while the report slot named by
``CARTOPIAN_EXPECTED_REPORT_PATH`` is absent or unparseable. It closes the
observed failure mode where an assignee backgrounds its test suite, emits
``end_turn`` promising to report "later", and is terminated by print mode
before that later ever arrives.

Coverage follows the five handoff outcomes plus the guard's own bounds:

- **missing** report → block, with foreground-execution instructions
- **partial** report → block, naming what the file is missing
- **complete** / **blocked** / **failed** report → allow (all are *finished*
  handoffs; only the PM judges the verdict)
- **API-error-shaped loop** (report never appears) → the guard exhausts after
  ``CARTOPIAN_STOP_GUARD_MAX_BLOCKS`` and allows the stop, leaving
  ``exited-without-report`` as the final defensive backstop
- every failure path (no env, bad payload, unwritable counter, internal
  exception) fails **open**
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = REPO_ROOT / "cli" / "claude_stop_hook.py"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cli import claude_stop_hook  # noqa: E402
from cli.handoff_observer import observe_once  # noqa: E402


TASK_REPORT_COMPLETE = """# Completion report

Status: complete

## Identity

- Report path: reports/REPORT-01-001.md

## Completion evidence

Ran the canonical suite in the foreground; 1240 passed.

## Remaining risks

None.

## Ready to close

yes
"""

TASK_REPORT_PARTIAL = """# Completion report

## Identity

- Report path: reports/REPORT-01-001.md
"""

REVIEW_REPORT_COMPLETE = """# Review report

Status: complete

## Identity

- Review ID: REVIEW-01-001
- Prompt path: /p/prompts/PROMPT-01-001.md
- Task path: /p/tasks/done/TASK-01-001.md
- Review file path: /p/reviews/REVIEW-01-001.md

Request alignment: aligned
Request evidence: /p/requests/REQ-001.md

## Evidence reviewed

The diff and the completion report.

## Verdict

approve

## Blocking findings

None.
"""


def _payload(session_id: str = "sess-1", stop_hook_active: bool = False) -> dict:
    """A Stop payload shaped as Claude Code sends it."""
    return {
        "session_id": session_id,
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": "/tmp",
        "hook_event_name": "Stop",
        "stop_hook_active": stop_hook_active,
    }


class StopHookCase(unittest.TestCase):
    """Base fixture: a project-shaped reports dir and an isolated counter dir."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.reports = self.root / "project" / "reports"
        self.reports.mkdir(parents=True)
        self.report_path = self.reports / "REPORT-01-001.md"
        self.state_dir = self.root / "state"
        self.state_dir.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def env(self, **overrides) -> dict:
        env = {
            "CARTOPIAN_EXPECTED_REPORT_PATH": str(self.report_path),
            "CARTOPIAN_EXPECTED_REPORT_VARIANT": "task",
        }
        env.update(overrides)
        return env

    def evaluate(self, payload=None, **env_overrides):
        return claude_stop_hook.evaluate(
            payload if payload is not None else _payload(),
            environ=self.env(**env_overrides),
            tmpdir=str(self.state_dir),
        )


class TestActivation(StopHookCase):
    def test_no_expected_report_env_is_zero_footprint(self):
        """An interactive session (no dispatch env) is never touched."""
        decision = claude_stop_hook.evaluate(
            _payload(), environ={}, tmpdir=str(self.state_dir)
        )
        self.assertEqual(decision.action, "allow")
        self.assertIsNone(decision.reason)
        self.assertIsNone(decision.note)
        self.assertEqual(list(self.state_dir.iterdir()), [])

    def test_empty_expected_report_env_is_inactive(self):
        decision = claude_stop_hook.evaluate(
            _payload(),
            environ={"CARTOPIAN_EXPECTED_REPORT_PATH": "   "},
            tmpdir=str(self.state_dir),
        )
        self.assertEqual(decision.action, "allow")


class TestMissingReport(StopHookCase):
    def test_absent_report_blocks(self):
        decision = self.evaluate()
        self.assertEqual(decision.action, "block")
        self.assertIn("has not been written", decision.reason)
        self.assertIn(str(self.report_path), decision.reason)

    def test_block_reason_forbids_background_completion(self):
        """The refusal must name the actual root cause, not just 'write it'."""
        reason = self.evaluate().reason
        self.assertIn("FOREGROUND", reason)
        self.assertIn("Background", reason)
        self.assertIn("cannot resume this session", reason)

    def test_block_reason_offers_the_blocked_escape_hatch(self):
        """An agent that truly cannot finish must still publish a report."""
        self.assertIn("Status: blocked", self.evaluate().reason)

    def test_block_reason_counts_the_attempt(self):
        reason = self.evaluate().reason
        self.assertIn("guard attempt 1 of 3", reason)
        self.assertIn("exited-without-report", reason)


class TestPartialReport(StopHookCase):
    def test_partial_report_blocks(self):
        self.report_path.write_text(TASK_REPORT_PARTIAL, encoding="utf-8")
        decision = self.evaluate()
        self.assertEqual(decision.action, "block")
        self.assertIn("does not parse as a", decision.reason)

    def test_partial_report_names_what_is_missing(self):
        self.report_path.write_text(TASK_REPORT_PARTIAL, encoding="utf-8")
        reason = self.evaluate().reason
        self.assertIn("`## Remaining risks`", reason)
        self.assertIn("Status: complete | blocked | failed", reason)

    def test_empty_file_blocks(self):
        self.report_path.write_text("", encoding="utf-8")
        self.assertEqual(self.evaluate().action, "block")

    def test_unfilled_template_placeholder_blocks(self):
        """`Status: <complete | blocked | failed>` is not a real status."""
        self.report_path.write_text(
            TASK_REPORT_COMPLETE.replace(
                "Status: complete", "Status: <complete | blocked | failed>"
            ),
            encoding="utf-8",
        )
        self.assertEqual(self.evaluate().action, "block")

    def test_symlinked_report_blocks_with_symlink_guidance(self):
        target = self.root / "elsewhere.md"
        target.write_text(TASK_REPORT_COMPLETE, encoding="utf-8")
        try:
            self.report_path.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable on this platform")
        decision = self.evaluate()
        self.assertEqual(decision.action, "block")
        self.assertIn("symlink", decision.reason)


class TestFinishedHandoffsAllowStop(StopHookCase):
    def test_complete_report_allows(self):
        self.report_path.write_text(TASK_REPORT_COMPLETE, encoding="utf-8")
        decision = self.evaluate()
        self.assertEqual(decision.action, "allow")
        self.assertIsNone(decision.reason)

    def test_blocked_report_allows(self):
        """A blocked report is a *finished* handoff; the PM judges the verdict."""
        self.report_path.write_text(
            TASK_REPORT_COMPLETE.replace("Status: complete", "Status: blocked"),
            encoding="utf-8",
        )
        self.assertEqual(self.evaluate().action, "allow")

    def test_failed_report_allows(self):
        self.report_path.write_text(
            TASK_REPORT_COMPLETE.replace("Status: complete", "Status: failed"),
            encoding="utf-8",
        )
        self.assertEqual(self.evaluate().action, "allow")

    def test_completion_clears_the_counter(self):
        first = self.evaluate()
        self.assertEqual(first.action, "block")
        self.assertTrue(any(self.state_dir.iterdir()))
        self.report_path.write_text(TASK_REPORT_COMPLETE, encoding="utf-8")
        self.assertEqual(self.evaluate().action, "allow")
        self.assertEqual(list(self.state_dir.iterdir()), [])


class TestReviewVariant(StopHookCase):
    def setUp(self) -> None:
        super().setUp()
        self.report_path = self.reports / "REPORT-01-001-review.md"

    def env(self, **overrides) -> dict:
        return super().env(
            **{
                "CARTOPIAN_EXPECTED_REPORT_PATH": str(self.report_path),
                "CARTOPIAN_EXPECTED_REPORT_VARIANT": "review",
                **overrides,
            }
        )

    def test_complete_review_report_allows(self):
        self.report_path.write_text(REVIEW_REPORT_COMPLETE, encoding="utf-8")
        self.assertEqual(self.evaluate().action, "allow")

    def test_review_report_without_verdict_blocks(self):
        self.report_path.write_text(
            REVIEW_REPORT_COMPLETE.replace("\napprove\n", "\n"), encoding="utf-8"
        )
        decision = self.evaluate()
        self.assertEqual(decision.action, "block")
        self.assertIn("approve / request-changes / reject", decision.reason)

    def test_task_shaped_report_in_the_review_slot_blocks(self):
        """Variant is pinned by dispatch; wrong-shape content is not evidence."""
        self.report_path.write_text(TASK_REPORT_COMPLETE, encoding="utf-8")
        self.assertEqual(self.evaluate().action, "block")


class TestGuardBounds(StopHookCase):
    def test_repeated_report_less_stops_exhaust_the_guard(self):
        """The API-error / stuck-loop shape: the guard must not pin the session."""
        for attempt in range(1, 4):
            decision = self.evaluate(_payload(stop_hook_active=attempt > 1))
            self.assertEqual(decision.action, "block", f"attempt {attempt}")
            self.assertIn(f"guard attempt {attempt} of 3", decision.reason)

        final = self.evaluate(_payload(stop_hook_active=True))
        self.assertEqual(final.action, "allow")
        self.assertIn("stop guard exhausted", final.note)
        self.assertIn("exited-without-report", final.note)

    def test_exhausted_guard_leaves_the_backstop_classification_intact(self):
        """After the guard yields, the canonical observer still classifies."""
        for _ in range(3):
            self.assertEqual(self.evaluate().action, "block")
        self.assertEqual(self.evaluate().action, "allow")

        # The wrapper then exits 0 with no report, exactly as before the guard.
        Path(str(self.report_path) + ".status").write_text(
            "state=exited\nexit_code=0\nreason=clean\nexpected_variant=task\n",
            encoding="utf-8",
        )
        observation = observe_once(self.report_path, expected_variant="task")
        self.assertTrue(observation.terminal)
        self.assertEqual(observation.classification, "exited-without-report")

    def test_max_blocks_zero_disables_the_guard(self):
        decision = self.evaluate(CARTOPIAN_STOP_GUARD_MAX_BLOCKS="0")
        self.assertEqual(decision.action, "allow")
        self.assertIn("disabled", decision.note)

    def test_max_blocks_is_configurable(self):
        for attempt in (1, 2, 3, 4, 5):
            decision = self.evaluate(CARTOPIAN_STOP_GUARD_MAX_BLOCKS="5")
            self.assertEqual(decision.action, "block")
            self.assertIn(f"guard attempt {attempt} of 5", decision.reason)
        self.assertEqual(
            self.evaluate(CARTOPIAN_STOP_GUARD_MAX_BLOCKS="5").action, "allow"
        )

    def test_malformed_max_blocks_falls_back_to_the_default(self):
        """A typo must not silently disable the guard."""
        for value in ("not-a-number", "-1", ""):
            with self.subTest(value=value):
                decision = claude_stop_hook.evaluate(
                    _payload(session_id=f"sess-{value}"),
                    environ=self.env(CARTOPIAN_STOP_GUARD_MAX_BLOCKS=value),
                    tmpdir=str(self.state_dir),
                )
                self.assertEqual(decision.action, "block")
                self.assertIn("of 3", decision.reason)

    def test_stale_counter_from_another_session_does_not_pre_exhaust(self):
        for _ in range(3):
            self.evaluate(_payload(session_id="old-session"))
        decision = self.evaluate(_payload(session_id="fresh-session"))
        self.assertEqual(decision.action, "block")
        self.assertIn("guard attempt 1 of 3", decision.reason)

    def test_unpersistable_counter_allows_once_the_loop_is_already_active(self):
        """Without a bounded count, one lost report beats an unbounded loop."""
        with mock.patch.object(claude_stop_hook, "_write_blocks", return_value=False):
            active = self.evaluate(_payload(stop_hook_active=True))
            self.assertEqual(active.action, "allow")
            self.assertIn("cannot persist its counter", active.note)

            # The first stop of a session is still safely blockable.
            first = self.evaluate(_payload(stop_hook_active=False))
            self.assertEqual(first.action, "block")


class TestHookIO(unittest.TestCase):
    """End-to-end through the real script, as Claude Code invokes it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.reports = self.root / "reports"
        self.reports.mkdir()
        self.report_path = self.reports / "REPORT-01-001.md"
        self.addCleanup(self._tmp.cleanup)

    def _run(self, stdin: str, env_extra=None):
        env = dict(os.environ)
        env.pop("CARTOPIAN_EXPECTED_REPORT_PATH", None)
        env.pop("CARTOPIAN_EXPECTED_REPORT_VARIANT", None)
        env.pop("CARTOPIAN_STOP_GUARD_MAX_BLOCKS", None)
        env["TMPDIR"] = str(self.root / "tmp")
        (self.root / "tmp").mkdir(exist_ok=True)
        env.update(env_extra or {})
        return subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input=stdin,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_block_emits_the_documented_stop_decision(self):
        result = self._run(
            json.dumps(_payload()),
            {
                "CARTOPIAN_EXPECTED_REPORT_PATH": str(self.report_path),
                "CARTOPIAN_EXPECTED_REPORT_VARIANT": "task",
            },
        )
        self.assertEqual(result.returncode, 0)
        emitted = json.loads(result.stdout)
        self.assertEqual(emitted["decision"], "block")
        self.assertIn("FOREGROUND", emitted["reason"])
        self.assertIn(str(self.report_path), result.stderr)

    def test_allow_writes_nothing_to_stdout(self):
        self.report_path.write_text(TASK_REPORT_COMPLETE, encoding="utf-8")
        result = self._run(
            json.dumps(_payload()),
            {
                "CARTOPIAN_EXPECTED_REPORT_PATH": str(self.report_path),
                "CARTOPIAN_EXPECTED_REPORT_VARIANT": "task",
            },
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_inactive_session_writes_nothing_at_all(self):
        result = self._run(json.dumps(_payload()))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_unparseable_payload_fails_open(self):
        result = self._run(
            "not json",
            {"CARTOPIAN_EXPECTED_REPORT_PATH": str(self.report_path)},
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("unreadable hook payload", result.stderr)

    def test_empty_stdin_fails_open(self):
        result = self._run(
            "", {"CARTOPIAN_EXPECTED_REPORT_PATH": str(self.report_path)}
        )
        self.assertEqual(result.returncode, 0)
        # An empty payload still evaluates; a missing report still blocks.
        self.assertIn("decision", result.stdout)

    def test_internal_error_fails_open(self):
        import io
        import contextlib

        with mock.patch.object(
            claude_stop_hook, "evaluate", side_effect=RuntimeError("boom")
        ):
            stdout, stderr = io.StringIO(), io.StringIO()
            stdin = mock.Mock()
            stdin.buffer.read.return_value = json.dumps(_payload()).encode()
            with mock.patch.object(sys, "stdin", stdin), contextlib.redirect_stdout(
                stdout
            ), contextlib.redirect_stderr(stderr):
                code = claude_stop_hook.main()
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("allowing stop", stderr.getvalue())


class TestInstallerRegistration(unittest.TestCase):
    """Both hooks are written by the one canonical registration.

    ``cli/claude_hooks.apply_project`` is the single definition the required
    ``project-hooks`` install surface and ``register-project`` both call, so a
    project can never end up with one hook and not the other.
    """

    def test_registers_the_stop_hook(self):
        from cli import claude_hooks

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            claude_hooks.apply_project(project, Path(tmp) / "root")
            settings = json.loads(
                (project / ".claude" / "settings.json").read_text(encoding="utf-8")
            )
            stop = settings["hooks"]["Stop"]
            self.assertEqual(len(stop), 1)
            self.assertNotIn("matcher", stop[0])
            self.assertIn("claude_stop_hook.py", stop[0]["hooks"][0]["command"])

    def test_registration_is_idempotent(self):
        from cli import claude_hooks

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            claude_hooks.apply_project(project, Path(tmp) / "root")
            claude_hooks.apply_project(project, Path(tmp) / "root")
            settings = json.loads(
                (project / ".claude" / "settings.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(settings["hooks"]["Stop"]), 1)

    def test_operator_stop_hooks_are_preserved(self):
        from cli import claude_hooks

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            (project / ".claude").mkdir(parents=True)
            (project / ".claude" / "settings.json").write_text(
                json.dumps(
                    {
                        "hooks": {
                            "Stop": [
                                {"hooks": [{"type": "command", "command": "notify.sh"}]}
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            claude_hooks.apply_project(project, Path(tmp) / "root")
            settings = json.loads(
                (project / ".claude" / "settings.json").read_text(encoding="utf-8")
            )
            commands = [
                h["command"] for item in settings["hooks"]["Stop"] for h in item["hooks"]
            ]
            self.assertIn("notify.sh", commands)
            self.assertEqual(
                sum("claude_stop_hook.py" in c for c in commands), 1
            )

    def test_both_hooks_coexist_in_one_settings_file(self):
        from cli import claude_hooks

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            claude_hooks.apply_project(project, Path(tmp) / "root")
            settings = json.loads(
                (project / ".claude" / "settings.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(settings["hooks"]["PreToolUse"]), 1)
            self.assertEqual(len(settings["hooks"]["Stop"]), 1)
            self.assertIn(
                "claude_hook.py",
                settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"],
            )

    def test_a_malformed_settings_file_is_refused_not_overwritten(self):
        from cli import claude_hooks

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            (project / ".claude").mkdir(parents=True)
            settings_path = project / ".claude" / "settings.json"
            settings_path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                claude_hooks.apply_project(project, Path(tmp) / "root")
            self.assertEqual(settings_path.read_text(encoding="utf-8"), "{not json")


if __name__ == "__main__":
    unittest.main()
