"""Tests for `cartopian handoff-packet` (FR-003).

Evidence-gate test for TASK-01-003: asserts the command emits a single
serialized NDJSON record to stdout containing the required handoff-packet
fields (FR-014 machine surface, DEC-008). The broader test matrix lives
in TASK-01-006.
"""
import argparse
import contextlib
import io
import json
import unittest

from cli.commands import handoff_packet
from tests.scaffold import project_scaffold

_TOML = (
    "[project]\n"
    'id = "test-proj"\n'
    'name = "Test Project"\n'
    'protocol_version = "v0.3.0"\n'
    'work_roots = ["tool-repo"]\n'
    "\n"
    "[roles]\n"
    'coder = "Implements tasks per spec."\n'
    "\n"
    "[handoffs.coder]\n"
    'agent = "cartopian-claude"\n'
    "auto_start = true\n"
    'timeout = "30m"\n'
)


def _invoke(task_path: str, role: str):
    """Invoke handler and capture serialized stdout; return (stdout, exit_code).

    Captures real stdout bytes so assertions verify the FR-014 NDJSON
    machine surface rather than handler-internal Python objects.
    """
    args = argparse.Namespace(task_path=task_path, role=role)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = handoff_packet.handler(args)
    return buf.getvalue(), rc


class TestHandoffPacketHappyPath(unittest.TestCase):
    def test_emits_ndjson_record_to_stdout(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            work_root = scaffold.root / "tool-repo"
            work_root.mkdir()
            scaffold.write(
                "cartopian.local.toml",
                f"[work_roots]\ntool-repo = \"{work_root}\"\n",
            )
            task_path = scaffold.write(
                "tasks/open/TASK-01-002-example.md",
                (
                    "# TASK-01-002: Example\n\n"
                    "Phase: PHASE-01-foundation\n"
                    "Plan ref: n/a\n"
                    "Work root: tool-repo\n"
                    "Assignee: coder\n"
                    "Spec: none\n"
                    "Depends on: n/a\n"
                    "Blocked by: n/a\n"
                    "Created: 2026-05-18\n"
                    "Evidence gate: n/a\n\n"
                    "## Goal\n\nExample goal.\n"
                ),
            )

            stdout, rc = _invoke(str(task_path), "coder")

            self.assertEqual(rc, 0)
            # NDJSON contract: exactly one non-empty line terminated by `\n`.
            self.assertTrue(
                stdout.endswith("\n"),
                msg=f"expected trailing newline; got: {stdout!r}",
            )
            lines = [ln for ln in stdout.split("\n") if ln]
            self.assertEqual(
                len(lines), 1,
                msg=f"expected exactly one NDJSON line; got: {stdout!r}",
            )
            rec = json.loads(lines[0])

            for field in (
                "task_id",
                "task_title",
                "task_path",
                "role",
                "handoff_target",
                "auto_start",
                "work_roots",
                "expected_report_path",
                "git_versioning",
                "git_policy",
                "automation_policy",
            ):
                self.assertIn(field, rec, msg=f"missing field: {field}")

            self.assertEqual(rec["task_id"], "TASK-01-002")
            self.assertEqual(rec["task_title"], "TASK-01-002: Example")
            self.assertEqual(rec["role"], "coder")
            self.assertEqual(rec["handoff_target"], "cartopian-claude")
            self.assertTrue(rec["auto_start"])
            self.assertEqual(
                rec["work_roots"],
                [{"name": "tool-repo", "absolute_path": str(work_root)}],
            )
            self.assertTrue(rec["expected_report_path"].endswith("/reports/REPORT-01-002.md"))
            self.assertFalse(rec["git_versioning"])
            self.assertIsNone(rec["git_policy"])
            self.assertEqual(
                rec["automation_policy"],
                {"confirmation": "each-handoff", "max_handoffs_per_run": 1},
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
