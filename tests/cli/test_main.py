"""Tests for bin/cartopian entrypoint dispatcher (FR-014 contract)."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENTRYPOINT = os.path.join(REPO_ROOT, "bin", "cartopian")

SUBCOMMANDS = [
    "resolve-config",
    "parse-report",
    "validate-task-readiness",
    "move-task",
    "discover-projects",
    "register-project",
    "unregister-project",
    "scaffold-project",
    "generate-config",
    "list-tasks",
    "delete-prompt",
    "delete-report",
]


def _run(*args):
    return subprocess.run(
        [sys.executable, ENTRYPOINT, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


class TestHelp(unittest.TestCase):
    def test_help_exits_zero(self):
        result = _run("--help")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        for name in SUBCOMMANDS:
            self.assertIn(name, result.stdout, msg=f"missing subcommand {name} in --help")


class TestUsageGuards(unittest.TestCase):
    def test_unknown_subcommand_exits_two_usage(self):
        result = _run("not-a-command")
        self.assertEqual(result.returncode, 2)
        self.assertTrue(
            result.stderr.startswith("[usage]"),
            msg=f"expected [usage] prefix, got: {result.stderr!r}",
        )

    def test_no_subcommand_exits_two_usage(self):
        result = _run()
        self.assertEqual(result.returncode, 2)
        self.assertTrue(
            result.stderr.startswith("[usage]"),
            msg=f"expected [usage] prefix, got: {result.stderr!r}",
        )


class TestInvalidChoiceMessageRouting(unittest.TestCase):
    """Shared `_UsageParser.error` must distinguish top-level subcommand
    `invalid choice` errors from per-command positional `invalid choice`
    errors (TASK-01-019)."""

    def test_top_level_unknown_subcommand_locked_phrasing(self):
        result = _run("bogus-cmd")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "[usage] unknown subcommand: bogus-cmd\n")

    def test_per_command_invalid_positional_choice_names_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task = tmp_path / "project" / "tasks" / "open" / "TASK-01-019-demo.md"
            task.parent.mkdir(parents=True, exist_ok=True)
            task.write_text("# task\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, ENTRYPOINT, "move-task", str(task), "archived"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            "[usage] invalid to_status: 'archived' (choose from 'open', "
            "'in-progress', 'in-review', 'done')\n",
        )

    def test_other_argparse_errors_pass_through(self):
        # `move-task` with no positional args triggers a non-`invalid choice`
        # argparse error (missing required positional). It must continue to
        # route through the existing `[usage] <message>` branch unchanged.
        result = _run("move-task")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertTrue(
            result.stderr.startswith("[usage]"),
            msg=f"expected [usage] prefix, got: {result.stderr!r}",
        )
        self.assertNotIn("invalid choice", result.stderr)
        self.assertNotIn("unknown subcommand", result.stderr)


if __name__ == "__main__":
    unittest.main()
