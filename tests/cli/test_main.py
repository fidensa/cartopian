"""Tests for bin/cartopian entrypoint dispatcher (FR-014 contract)."""
import os
import subprocess
import sys
import unittest

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


class TestPlaceholderHandlers(unittest.TestCase):
    def test_placeholder_handler_emits_not_implemented(self):
        # Pick any still-unimplemented subcommand to assert the placeholder shape.
        result = _run("move-task")
        self.assertEqual(result.returncode, 1)
        self.assertIn("[error] not yet implemented", result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
