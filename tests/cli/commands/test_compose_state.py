"""Tests for `cartopian compose-state`."""
import argparse
import unittest

from cli.commands import compose_state  # noqa: F401 - red stage: module must exist
from cli.main import SUBCOMMANDS, build_parser
from tests.scaffold import project_scaffold

_TOML_BASE = (
    "[project]\n"
    'id = "test-proj"\n'
    'name = "Test Project"\n'
    'protocol_version = "v0.3.0"\n'
)


def _invoke(project_path: str):
    """Invoke handler with emit capture; return (records, exit_code)."""
    args = argparse.Namespace(project_path=project_path)
    captured = []
    original = compose_state.emit_record

    def _capture(record, *, out=None):
        captured.append(record)

    compose_state.emit_record = _capture
    try:
        rc = compose_state.handler(args)
    finally:
        compose_state.emit_record = original
    return captured, rc


class TestComposeStateRegistration(unittest.TestCase):
    def test_cli_subcommand_registered(self) -> None:
        self.assertIn("compose-state", SUBCOMMANDS)
        args = build_parser().parse_args(["compose-state", "/tmp/project"])
        self.assertEqual(args.cmd, "compose-state")


class TestComposeStateRequiredFields(unittest.TestCase):
    def test_all_schema_fields_present(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.write(
                "phases/PHASE-01-foundation.md",
                "# PHASE-01-foundation: Foundation\n",
            )
            scaffold.write(
                "tasks/open/TASK-01-001-first.md",
                "# TASK-01-001: First\n\nPhase: PHASE-01-foundation\n",
            )

            records, rc = _invoke(str(scaffold.project_root))

            self.assertEqual(rc, 0)
            self.assertEqual(len(records), 1)
            rec = records[0]
            for field in (
                "current_phase",
                "active_work",
                "open_work",
                "what_to_do_next",
                "rendered_body",
            ):
                self.assertIn(field, rec, msg=f"missing field: {field}")


class TestComposeStateHappyPath(unittest.TestCase):
    def test_renders_state_body_from_filesystem(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.write(
                "phases/PHASE-01-foundation.md",
                "# PHASE-01-foundation: Foundation\n",
            )
            scaffold.write(
                "phases/PHASE-02-expansion.md",
                "# PHASE-02-expansion: Expansion\n",
            )
            scaffold.write(
                "tasks/in-progress/TASK-01-002-build-core.md",
                "# TASK-01-002: Build core\n\nPhase: PHASE-01-foundation\n",
            )
            scaffold.write(
                "tasks/open/TASK-01-003-polish-core.md",
                "# TASK-01-003: Polish core\n\nPhase: PHASE-01-foundation\n",
            )
            scaffold.write(
                "tasks/open/TASK-02-001-expand-scope.md",
                "# TASK-02-001: Expand scope\n\nPhase: PHASE-02-expansion\n",
            )

            records, rc = _invoke(str(scaffold.project_root))

            self.assertEqual(rc, 0)
            self.assertEqual(len(records), 1)
            rec = records[0]

            self.assertEqual(
                rec["current_phase"],
                "PHASE-01-foundation: Foundation (`phases/PHASE-01-foundation.md`)",
            )
            self.assertEqual(
                rec["active_work"],
                "- TASK-01-002: Build core (`tasks/in-progress/TASK-01-002-build-core.md`)",
            )
            self.assertEqual(
                rec["open_work"],
                "- TASK-01-003: Polish core (`tasks/open/TASK-01-003-polish-core.md`) [ready]\n"
                "- TASK-02-001: Expand scope (`tasks/open/TASK-02-001-expand-scope.md`) [ready]",
            )
            self.assertEqual(
                rec["what_to_do_next"],
                "Continue TASK-01-002 (`tasks/in-progress/TASK-01-002-build-core.md`).",
            )
            self.assertEqual(
                rec["rendered_body"],
                "# Test Project - State\n\n"
                "## Current phase\n\n"
                "PHASE-01-foundation: Foundation (`phases/PHASE-01-foundation.md`)\n\n"
                "## Active work\n\n"
                "- TASK-01-002: Build core (`tasks/in-progress/TASK-01-002-build-core.md`)\n\n"
                "## Open work\n\n"
                "- TASK-01-003: Polish core (`tasks/open/TASK-01-003-polish-core.md`) [ready]\n"
                "- TASK-02-001: Expand scope (`tasks/open/TASK-02-001-expand-scope.md`) [ready]\n\n"
                "## What to do next\n\n"
                "Continue TASK-01-002 (`tasks/in-progress/TASK-01-002-build-core.md`).",
            )


class TestComposeStateMultipleActiveTasks(unittest.TestCase):
    def test_all_active_tasks_surface_as_bullets(self) -> None:
        # Regression: every task in an active status directory must appear,
        # matching list-tasks. compose-state previously returned only the
        # first in-progress task and silently dropped concurrent siblings.
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.write(
                "phases/PHASE-01-foundation.md",
                "# PHASE-01-foundation: Foundation\n",
            )
            scaffold.write(
                "tasks/in-progress/TASK-01-002-build-core.md",
                "# TASK-01-002: Build core\n\nPhase: PHASE-01-foundation\n",
            )
            scaffold.write(
                "tasks/in-progress/TASK-01-005-wire-cli.md",
                "# TASK-01-005: Wire CLI\n\nPhase: PHASE-01-foundation\n",
            )
            scaffold.write(
                "tasks/in-review/TASK-01-004-review-me.md",
                "# TASK-01-004: Review me\n\nPhase: PHASE-01-foundation\n",
            )

            records, rc = _invoke(str(scaffold.project_root))

            self.assertEqual(rc, 0)
            self.assertEqual(len(records), 1)
            self.assertEqual(
                records[0]["active_work"],
                "- TASK-01-002: Build core (`tasks/in-progress/TASK-01-002-build-core.md`)\n"
                "- TASK-01-005: Wire CLI (`tasks/in-progress/TASK-01-005-wire-cli.md`)\n"
                "- TASK-01-004: Review me (`tasks/in-review/TASK-01-004-review-me.md`)",
            )


class TestComposeStateNoPlanState(unittest.TestCase):
    def test_no_plan_state_returns_nullables(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            records, rc = _invoke(str(scaffold.project_root))

            self.assertEqual(rc, 0)
            self.assertEqual(len(records), 1)
            rec = records[0]
            self.assertIsNone(rec["current_phase"])
            self.assertIsNone(rec["active_work"])
            self.assertIsNone(rec["open_work"])
            self.assertIsNone(rec["what_to_do_next"])
            self.assertIsNone(rec["rendered_body"])


class TestComposeStateExitCodes(unittest.TestCase):
    def test_exit3_on_missing_config(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.config.unlink()
            args = argparse.Namespace(project_path=str(scaffold.project_root))
            rc = compose_state.handler(args)
            self.assertEqual(rc, 3)

    def test_exit3_on_corrupt_toml(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.config.write_text("[[this is not valid toml\x00", encoding="utf-8")
            args = argparse.Namespace(project_path=str(scaffold.project_root))
            rc = compose_state.handler(args)
            self.assertEqual(rc, 3)


class TestComposeStateReadOnlyInvariant(unittest.TestCase):
    def test_no_files_created_or_modified(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            scaffold.write(
                "phases/PHASE-01-foundation.md",
                "# PHASE-01-foundation: Foundation\n",
            )
            scaffold.write(
                "tasks/open/TASK-01-001-first.md",
                "# TASK-01-001: First\n\nPhase: PHASE-01-foundation\n",
            )
            before = {
                path: path.stat().st_mtime_ns
                for path in scaffold.project_root.rglob("*")
                if path.is_file()
            }

            _invoke(str(scaffold.project_root))

            after_paths = {path for path in scaffold.project_root.rglob("*") if path.is_file()}
            self.assertSetEqual(after_paths, set(before))
            for path, mtime_before in before.items():
                self.assertEqual(path.stat().st_mtime_ns, mtime_before, msg=f"handler modified: {path}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
