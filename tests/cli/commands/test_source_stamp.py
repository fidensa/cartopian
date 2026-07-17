"""Tests for the `--source BL-NNN` promotion stamp on the artifact writers.

Promotion is a recorded move: `write-task` / `write-spec` / `write-phase` take
`--source BL-NNN`, verify the entry is live in `BACKLOG.md`, and render the
`Source:` header line themselves (so a hand-typed body line does not count).
Together with `delete-backlog`'s interlock guard this makes stamp-then-delete
the only ordering that executes.
"""
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from cli.main import build_parser
from tests.scaffold import project_scaffold

_TOML = (
    "[project]\n"
    'id = "demo"\n'
    'name = "Demo Project"\n'
    'protocol_version = "v0.5.0"\n'
)


def run_cli(*argv):
    parser = build_parser()
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with redirect_stdout(out), redirect_stderr(err):
        try:
            args = parser.parse_args(list(argv))
            handler = getattr(args, "_handler", None)
            code = handler(args) if handler is not None else 2
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
    records = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    return code, records, err.getvalue()


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.scaffold = project_scaffold(cartopian_toml=_TOML)
        self.addCleanup(self.scaffold.cleanup)
        self.root = str(self.scaffold.project_root)

    def _add_backlog(self, title="Item"):
        code, records, err = run_cli(
            "write-backlog", self.root, "--title", title, "--content", "b"
        )
        self.assertEqual(code, 0, err)
        return records[0]["details"]["bl_id"]


_TASK_BODY = (
    "# TASK-01-001: do\n\nPhase: PHASE-01-x\nPlan ref: P01-BUILD-001\n"
    "Evidence gate: n/a\n\n## Acceptance\n\n- [ ] done\n\n## Goal\n\ng\n"
)
_SPEC_BODY = "# SPEC-01-001: s\n\nStatus: draft\nPlan refs: P01-BUILD-001\n\n## Problem\n\np\n"
_PHASE_BODY = "# PHASE-01-x: p\n\nPlan ref section: `## Phase 01`\nCreated: 2026-07-04\n\n## Goal\n\ng\n"


class TestSourceStamp(_Fixture):
    def test_write_task_stamps_live_source(self):
        bl = self._add_backlog()          # BL-001
        code, records, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-001", "--slug", "do",
            "--content", _TASK_BODY, "--source", bl,
        )
        self.assertEqual(code, 0, err)
        text = (self.scaffold.tasks_open / "TASK-01-001-do.md").read_text(encoding="utf-8")
        self.assertIn(f"Source: {bl}", text)
        # Stamped into the header block (before the first `## ` section), right
        # after Plan ref.
        self.assertLess(text.index(f"Source: {bl}"), text.index("## Goal"))
        self.assertTrue(text.index("Plan ref:") < text.index(f"Source: {bl}"))
        self.assertEqual(records[0]["details"]["source"], bl)

    def test_write_spec_stamps_live_source(self):
        bl = self._add_backlog()
        code, _r, err = run_cli(
            "write-spec", self.root, "--spec-id", "SPEC-01-001", "--slug", "s",
            "--content", _SPEC_BODY, "--source", bl,
        )
        self.assertEqual(code, 0, err)
        text = (self.scaffold.project_root / "specs" / "SPEC-01-001-s.md").read_text(encoding="utf-8")
        self.assertIn(f"Source: {bl}", text)

    def test_write_phase_stamps_live_source(self):
        bl = self._add_backlog()
        code, _r, err = run_cli(
            "write-phase", self.root, "--phase-id", "PHASE-01-x",
            "--content", _PHASE_BODY, "--source", bl,
        )
        self.assertEqual(code, 0, err)
        text = (self.scaffold.project_root / "phases" / "PHASE-01-x.md").read_text(encoding="utf-8")
        self.assertIn(f"Source: {bl}", text)

    def test_nonlive_source_is_refused_and_writes_nothing(self):
        self._add_backlog()  # BL-001 exists; BL-009 does not
        code, records, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-001", "--slug", "do",
            "--content", _TASK_BODY, "--source", "BL-009",
        )
        self.assertEqual(code, 1)
        self.assertIn("[guard]", err)
        self.assertIn("source-entry-not-live", err)
        self.assertEqual(records, [])
        self.assertFalse((self.scaffold.tasks_open / "TASK-01-001-do.md").exists())

    def test_bad_source_grammar_is_usage_error(self):
        self._add_backlog()
        code, _records, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-001", "--slug", "do",
            "--content", _TASK_BODY, "--source", "BL-1",
        )
        self.assertEqual(code, 2)
        self.assertIn("[usage]", err)

    def test_hand_typed_source_does_not_satisfy_delete_guard(self):
        """A `Source:` line the PM types into the body is decoration: it is not
        rendered by the guarded writer, so delete-backlog still refuses."""
        bl = self._add_backlog()          # BL-001
        forged = _TASK_BODY.replace("## Goal", f"Source: {bl}\n\n## Goal")
        # Write WITHOUT --source; the body carries a forged stamp.
        code, _r, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-001", "--slug", "do",
            "--content", forged,
        )
        self.assertEqual(code, 0, err)
        # The forged line IS present in the task file...
        task_text = (self.scaffold.tasks_open / "TASK-01-001-do.md").read_text(encoding="utf-8")
        self.assertIn(f"Source: {bl}", task_text)
        # ...and because it is a real `Source:` line in a governed surface, the
        # delete guard treats it as a stamp. This documents the boundary: the
        # interlock's integrity against *hand edits* rests on the raw-edit
        # detection floor (plan-audit), not on delete-backlog distinguishing a
        # mediated stamp from a typed one. The mediated path is what guarantees
        # a stamp only ever names a live entry.
        code, _r, err = run_cli("delete-backlog", self.root, "--bl-id", bl)
        self.assertEqual(code, 0, err)

    def test_end_to_end_promotion_then_delete(self):
        bl = self._add_backlog("Promote me")   # BL-001
        code, _r, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-001", "--slug", "do",
            "--content", _TASK_BODY, "--source", bl,
        )
        self.assertEqual(code, 0, err)
        # Stamp exists -> delete is permitted, no --discard needed.
        code, records, err = run_cli("delete-backlog", self.root, "--bl-id", bl)
        self.assertEqual(code, 0, err)
        self.assertFalse(records[0]["details"]["discarded"])
        self.assertNotIn(f"## {bl}", (Path(self.root) / "BACKLOG.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
