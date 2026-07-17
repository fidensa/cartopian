"""Tests for `cartopian delete-backlog`.

The mediated removal counterpart to ``write-backlog``: it strips exactly the
``## BL-NNN — <title>`` section named by ``--bl-id`` from the project-root
``BACKLOG.md`` and re-renders the file back through the mediated-write
primitive. Removal is section-exact — the preamble (including its
``Highest id issued:`` mark, which delete never touches) and every surviving
entry round-trip byte-for-byte. Deletion is **interlocked with promotion**: a
live entry is removed only when a governed artifact carries a matching
``Source: BL-NNN`` stamp, or when the operator passes ``--discard``.
"""
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from cli.main import SUBCOMMANDS, build_parser
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
        self.backlog = Path(self.root) / "BACKLOG.md"

    def _seed(self, *titles):
        """Allocate one entry per title via the real write-backlog path;
        return the list of writer-assigned ids."""
        ids = []
        for title in titles:
            code, records, err = run_cli(
                "write-backlog", self.root, "--title", title, "--content", f"{title} body."
            )
            self.assertEqual(code, 0, err)
            ids.append(records[0]["details"]["bl_id"])
        return ids

    def _stamp(self, bl_id, rel="tasks/open/TASK-01-001-x.md"):
        """Write a governed durable artifact carrying a `Source: BL-NNN` stamp."""
        path = Path(self.root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# TASK-01-001: x\n\nPlan ref: P01-BUILD-001\nSource: {bl_id}\n",
            encoding="utf-8",
        )
        return path


class TestRegistration(unittest.TestCase):
    def test_registered_on_cli_surface(self):
        self.assertIn("delete-backlog", SUBCOMMANDS)

    def test_exposed_on_mcp_tool_surface(self):
        from mcp_server import server
        names = {t["name"] for t in server.list_tools()}
        self.assertIn("delete_backlog", names)


class TestDeleteBacklog(_Fixture):
    def test_refuses_undocumented_deletion(self):
        self._seed("First")           # BL-001, unstamped
        before = self.backlog.read_text(encoding="utf-8")
        code, _records, err = run_cli("delete-backlog", self.root, "--bl-id", "BL-001")
        self.assertEqual(code, 1)
        self.assertIn("[guard]", err)
        self.assertIn("undocumented-deletion", err)
        self.assertEqual(self.backlog.read_text(encoding="utf-8"), before)  # untouched

    def test_deletes_when_source_stamp_present(self):
        self._seed("First", "Second")     # BL-001, BL-002
        stamp = self._stamp("BL-002")
        code, records, err = run_cli("delete-backlog", self.root, "--bl-id", "BL-002")
        self.assertEqual(code, 0, err)
        text = self.backlog.read_text(encoding="utf-8")
        self.assertNotIn("## BL-002", text)
        self.assertIn("## BL-001 — First", text)
        details = records[0]["details"]
        self.assertEqual(details["bl_id"], "BL-002")
        self.assertFalse(details["discarded"])
        self.assertEqual(details["source_stamp"], str(stamp))

    def test_stamp_recognized_in_each_governed_surface(self):
        surfaces = [
            "tasks/open/TASK-01-001-a.md",
            "tasks/in-progress/TASK-01-002-b.md",
            "tasks/in-review/TASK-01-003-c.md",
            "tasks/done/TASK-01-004-d.md",
            "specs/SPEC-01-001-e.md",
            "phases/PHASE-01-f.md",
            "decisions/DEC-001-g.md",
            "IMPLEMENTATION_PLAN.md",
        ]
        for rel in surfaces:
            with self.subTest(surface=rel):
                self.setUp()  # fresh project per surface
                self._seed("Only")        # BL-001
                self._stamp("BL-001", rel=rel)
                code, _records, err = run_cli(
                    "delete-backlog", self.root, "--bl-id", "BL-001"
                )
                self.assertEqual(code, 0, f"{rel}: {err}")
                self.assertNotIn("## BL-001", self.backlog.read_text(encoding="utf-8"))

    def test_discard_overrides_and_records(self):
        self._seed("First")          # BL-001, unstamped
        code, records, err = run_cli(
            "delete-backlog", self.root, "--bl-id", "BL-001", "--discard"
        )
        self.assertEqual(code, 0, err)
        self.assertNotIn("## BL-001", self.backlog.read_text(encoding="utf-8"))
        details = records[0]["details"]
        self.assertTrue(details["discarded"])
        self.assertIsNone(details["source_stamp"])

    def test_removes_entry_and_preserves_survivors_and_mark(self):
        self._seed("First", "Second", "Third")   # BL-001..003
        self._stamp("BL-002")
        code, records, err = run_cli("delete-backlog", self.root, "--bl-id", "BL-002")
        self.assertEqual(code, 0, err)
        text = self.backlog.read_text(encoding="utf-8")
        self.assertIn("## BL-001 — First", text)
        self.assertIn("## BL-003 — Third", text)
        self.assertNotIn("## BL-002", text)
        self.assertIn("Highest id issued: BL-003", text)  # mark never regresses
        self.assertEqual(records[0]["details"]["entries"], 2)

    def test_mark_is_untouched_by_delete(self):
        self._seed("First", "Second", "Third")   # mark -> BL-003
        code, _r, err = run_cli(
            "delete-backlog", self.root, "--bl-id", "BL-003", "--discard"
        )
        self.assertEqual(code, 0, err)
        self.assertIn("Highest id issued: BL-003", self.backlog.read_text(encoding="utf-8"))

    def test_preamble_survives_when_last_entry_removed(self):
        self.backlog.write_text(
            "# Demo — Backlog\n\nHand-authored context paragraph.\n\n"
            "Highest id issued: BL-001\n\n## BL-001 — Only\n\nOnly body.\n",
            encoding="utf-8",
        )
        code, records, err = run_cli(
            "delete-backlog", self.root, "--bl-id", "BL-001", "--discard"
        )
        self.assertEqual(code, 0, err)
        text = self.backlog.read_text(encoding="utf-8")
        self.assertIn("Hand-authored context paragraph.", text)
        self.assertIn("Highest id issued: BL-001", text)  # mark preserved
        self.assertNotIn("## BL-001", text)
        self.assertEqual(records[0]["details"]["entries"], 0)

    def test_fenced_heading_is_not_sheared_on_removal(self):
        """Removing a sibling must leave an entry whose body quotes a
        `## BL-NNN` heading inside a fence intact byte-for-byte."""
        body = (
            "Intro.\n\n```md\n## BL-002 example\nsome body text\n```\n\n"
            "More normal text."
        )
        run_cli("write-backlog", self.root, "--title", "First", "--content", body)
        run_cli("write-backlog", self.root, "--title", "Second", "--content", "Body two.")
        self._stamp("BL-002")
        code, records, err = run_cli("delete-backlog", self.root, "--bl-id", "BL-002")
        self.assertEqual(code, 0, err)
        text = self.backlog.read_text(encoding="utf-8")
        self.assertIn(body, text)                       # fenced quote intact
        self.assertNotIn("## BL-002 — Second", text)     # real BL-002 gone
        self.assertEqual(records[0]["details"]["entries"], 1)

    def test_missing_id_fails_cleanly(self):
        self._seed("First")
        before = self.backlog.read_text(encoding="utf-8")
        code, _records, err = run_cli("delete-backlog", self.root, "--bl-id", "BL-099")
        self.assertEqual(code, 1)
        self.assertIn("[guard]", err)
        self.assertIn("BL-099", err)
        self.assertEqual(self.backlog.read_text(encoding="utf-8"), before)  # untouched

    def test_missing_backlog_file_fails_cleanly(self):
        self.assertFalse(self.backlog.exists())
        code, _records, err = run_cli("delete-backlog", self.root, "--bl-id", "BL-001")
        self.assertEqual(code, 1)
        self.assertIn("[guard]", err)
        self.assertFalse(self.backlog.exists())

    def test_invalid_bl_id_is_usage_error(self):
        for bad in ("BL-1", "bl-001", "BL-0001", "TASK-01-001"):
            code, _records, err = run_cli("delete-backlog", self.root, "--bl-id", bad)
            self.assertEqual(code, 2, f"{bad}: {err}")
            self.assertIn("[usage]", err)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
