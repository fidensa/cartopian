"""Tests for the composed `cartopian write-state` and situation notes.

While plan artifacts exist, write-state composes the canonical STATE.md body
in-process (the PM never authors it) and the only PM input is the bounded
`## Situation` section supplied via repeatable `--note` flags. Notes have a
one-delivery TTL: every write starts from zero notes and a byte-identical
re-pass is refused. On a no-plan project the body remains PM-authored via
`--content`, and `--note` is refused.
"""
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

from cli.main import build_parser
from tests.scaffold import project_scaffold

_TOML = (
    "[project]\n"
    'id = "test-proj"\n'
    'name = "Test Project"\n'
    'protocol_version = "v0.5.0"\n'
)


def run_cli(*argv):
    """Drive the real CLI parser in-process; return (exit_code, records, stderr)."""
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


class _PlannedFixture(unittest.TestCase):
    """A project with plan artifacts: one phase, one active task, one open task."""

    def setUp(self):
        self.scaffold = project_scaffold(cartopian_toml=_TOML)
        self.addCleanup(self.scaffold.cleanup)
        self.root = str(self.scaffold.project_root)
        self.scaffold.write(
            "phases/PHASE-01-foundation.md",
            "# PHASE-01-foundation: Foundation\n",
        )
        self.scaffold.write(
            "tasks/in-progress/TASK-01-001-build.md",
            "# TASK-01-001: Build\n\nPhase: PHASE-01-foundation\n",
        )
        self.scaffold.write(
            "tasks/open/TASK-01-002-polish.md",
            "# TASK-01-002: Polish\n\nPhase: PHASE-01-foundation\n",
        )

    def state_text(self) -> str:
        return self.scaffold.state.read_text(encoding="utf-8")


class TestComposedMode(_PlannedFixture):
    def test_composes_canonical_body_without_content(self):
        code, recs, err = run_cli("write-state", self.root)
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(recs[0]["details"]["mode"], "composed")
        self.assertEqual(recs[0]["details"]["notes"], 0)
        text = self.state_text()
        self.assertIn("# Test Project - State", text)
        self.assertIn("## Current phase", text)
        self.assertIn("TASK-01-001", text)
        self.assertNotIn("## Situation", text)

    def test_matches_compose_state_rendered_body(self):
        code, recs, err = run_cli("compose-state", self.root)
        self.assertEqual(code, 0, msg=err)
        rendered = recs[0]["rendered_body"]
        code, _, err = run_cli("write-state", self.root)
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(self.state_text().rstrip("\n"), rendered.rstrip("\n"))

    def test_authored_body_refused_while_plan_artifacts_exist(self):
        before = self.state_text()
        code, recs, err = run_cli("write-state", self.root, "--content", "# mine\n")
        self.assertEqual(code, 1)
        self.assertIn("state-body-is-composed", err)
        self.assertEqual(recs, [])
        self.assertEqual(self.state_text(), before)

    def test_content_file_equally_refused(self):
        body = self.scaffold.root / "body.md"
        body.write_text("# mine\n", encoding="utf-8")
        code, _, err = run_cli("write-state", self.root, "--content-file", str(body))
        self.assertEqual(code, 1)
        self.assertIn("state-body-is-composed", err)


class TestSituationNotes(_PlannedFixture):
    def test_notes_render_as_situation_section(self):
        code, recs, err = run_cli(
            "write-state", self.root,
            "--note", "coder deploy failed mid-handoff",
            "--note", "operator is restarting the development machine",
        )
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(recs[0]["details"]["notes"], 2)
        text = self.state_text()
        self.assertIn(
            "## Situation\n\n"
            "- coder deploy failed mid-handoff\n"
            "- operator is restarting the development machine",
            text,
        )

    def test_next_write_starts_from_zero_notes(self):
        run_cli("write-state", self.root, "--note", "transient fact")
        code, _, err = run_cli("write-state", self.root)
        self.assertEqual(code, 0, msg=err)
        self.assertNotIn("## Situation", self.state_text())
        self.assertNotIn("transient fact", self.state_text())

    def test_verbatim_carry_forward_refused(self):
        run_cli("write-state", self.root, "--note", "machine restart pending")
        before = self.state_text()
        code, recs, err = run_cli(
            "write-state", self.root, "--note", "machine restart pending",
        )
        self.assertEqual(code, 1)
        self.assertIn("note-carry-forward", err)
        self.assertEqual(recs, [])
        self.assertEqual(self.state_text(), before)

    def test_restated_note_is_accepted(self):
        run_cli("write-state", self.root, "--note", "machine restart pending")
        code, _, err = run_cli(
            "write-state", self.root,
            "--note", "machine restart still in progress after two sessions",
        )
        self.assertEqual(code, 0, msg=err)

    def test_too_many_notes_refused(self):
        notes = []
        for i in range(6):
            notes.extend(["--note", f"fact number {i}"])
        code, _, err = run_cli("write-state", self.root, *notes)
        self.assertEqual(code, 1)
        self.assertIn("too-many-notes", err)

    def test_overlong_note_refused(self):
        code, _, err = run_cli("write-state", self.root, "--note", "x" * 201)
        self.assertEqual(code, 1)
        self.assertIn("note-too-long", err)

    def test_multiline_note_refused(self):
        code, _, err = run_cli("write-state", self.root, "--note", "line one\nline two")
        self.assertEqual(code, 2)
        self.assertIn("multi-line", err)

    def test_empty_note_refused(self):
        code, _, err = run_cli("write-state", self.root, "--note", "   ")
        self.assertEqual(code, 2)
        self.assertIn("empty --note", err)


class TestNoPlanMode(unittest.TestCase):
    def setUp(self):
        self.scaffold = project_scaffold(cartopian_toml=_TOML)
        self.addCleanup(self.scaffold.cleanup)
        self.root = str(self.scaffold.project_root)

    def test_authored_body_accepted(self):
        code, recs, err = run_cli(
            "write-state", self.root, "--content",
            "# Test Project - State\n\n## Current phase\n\nNo active plan.\n",
        )
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(recs[0]["details"]["mode"], "no-plan")

    def test_note_refused_without_plan(self):
        code, recs, err = run_cli("write-state", self.root, "--note", "a fact")
        self.assertEqual(code, 1)
        self.assertIn("notes-require-plan", err)
        self.assertEqual(recs, [])

    def test_seeded_decisions_index_is_not_a_plan_artifact(self):
        # The scaffold seeds decisions/INDEX.md; the empty index must not flip
        # a never-planned project into composed mode.
        self.scaffold.write("decisions/INDEX.md", "# Decisions Index\n")
        code, _, err = run_cli(
            "write-state", self.root, "--content", "# no-plan body\n",
        )
        self.assertEqual(code, 0, msg=err)

    def test_real_decision_is_a_plan_artifact(self):
        self.scaffold.write("decisions/DEC-001-choice.md", "# DEC-001\n")
        code, _, err = run_cli(
            "write-state", self.root, "--content", "# authored\n",
        )
        self.assertEqual(code, 1)
        self.assertIn("state-body-is-composed", err)


class TestMcpToolSurface(unittest.TestCase):
    def test_notes_exposed_as_array_in_tool_schema(self):
        from mcp_server import server
        schema = next(
            t["inputSchema"] for t in server.list_tools() if t["name"] == "write_state"
        )
        notes = schema["properties"]["notes"]
        self.assertEqual(notes["type"], "array")
        self.assertEqual(notes["items"], {"type": "string"})

    def test_kwargs_rebuild_repeats_note_flag(self):
        from mcp_server import server
        entry = server._tool_registry()["write_state"]
        argv = server._kwargs_to_argv(
            entry["actions"],
            {"project_root": "/abs/proj", "notes": ["first fact", "second fact"]},
        )
        self.assertEqual(argv.count("--note"), 2)
        self.assertIn("first fact", argv)
        self.assertIn("second fact", argv)
        self.assertEqual(argv[-1], "/abs/proj")


if __name__ == "__main__":
    unittest.main()
