"""Tests for `cartopian delete-backlog` (BL-002).

The mediated removal counterpart to ``write-backlog``: it strips exactly the
``## BL-NNN — <title>`` section named by ``--bl-id`` from the project-root
``BACKLOG.md`` and re-renders the file back through the SPEC-01-002
mediated-write primitive. Removal is section-exact — the preamble and every
surviving entry round-trip byte-for-byte — and reuses ``write-backlog``'s
fence-aware parser, so a ``## BL-NNN`` heading quoted inside a code fence is
never mistaken for a boundary. Removing an absent id, or a missing
``BACKLOG.md``, fails cleanly with a non-zero exit.
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
    'protocol_version = "v0.3.0"\n'
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

    def _seed(self, *entries):
        """Write each ``(bl_id, title, body)`` via the real write-backlog path."""
        for bl_id, title, body in entries:
            code, _records, err = run_cli(
                "write-backlog", self.root, "--bl-id", bl_id,
                "--title", title, "--content", body,
            )
            self.assertEqual(code, 0, err)


class TestRegistration(unittest.TestCase):
    def test_registered_on_cli_surface(self):
        self.assertIn("delete-backlog", SUBCOMMANDS)

    def test_exposed_on_mcp_tool_surface(self):
        from mcp_server import server
        names = {t["name"] for t in server.list_tools()}
        self.assertIn("delete_backlog", names)


class TestDeleteBacklog(_Fixture):
    def test_removes_entry_and_preserves_remaining_bytes(self):
        self._seed(
            ("BL-001", "First", "First body."),
            ("BL-002", "Second", "Second body."),
            ("BL-003", "Third", "Third body."),
        )

        # Reference: the canonical file with ONLY the survivors, written fresh
        # in their original order. The post-deletion file must equal it byte
        # for byte — same preamble, same surviving sections, same spacing.
        other = project_scaffold(cartopian_toml=_TOML)
        self.addCleanup(other.cleanup)
        for bl_id, title, body in (("BL-001", "First", "First body."),
                                   ("BL-003", "Third", "Third body.")):
            run_cli("write-backlog", str(other.project_root), "--bl-id", bl_id,
                    "--title", title, "--content", body)
        expected = (Path(other.project_root) / "BACKLOG.md").read_text(encoding="utf-8")

        code, records, err = run_cli("delete-backlog", self.root, "--bl-id", "BL-002")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.backlog.read_text(encoding="utf-8"), expected)
        details = records[0]["details"]
        self.assertEqual(details["bl_id"], "BL-002")
        self.assertEqual(details["entries"], 2)

    def test_preamble_survives_when_last_entry_removed(self):
        self.backlog.write_text(
            "# Demo — Backlog\n\nHand-authored context paragraph.\n\n"
            "## BL-001 — Only\n\nOnly body.\n",
            encoding="utf-8",
        )
        code, records, err = run_cli("delete-backlog", self.root, "--bl-id", "BL-001")
        self.assertEqual(code, 0, err)
        text = self.backlog.read_text(encoding="utf-8")
        self.assertIn("Hand-authored context paragraph.", text)
        self.assertNotIn("BL-001", text)
        self.assertEqual(records[0]["details"]["entries"], 0)

    def test_fenced_heading_is_not_sheared_on_removal(self):
        """Removing a sibling must leave an entry whose body quotes a
        `## BL-NNN` heading inside a fence intact byte-for-byte: the fenced
        line is body, not a section boundary."""
        body = (
            "Intro.\n\n```md\n## BL-002 example\nsome body text\n```\n\n"
            "More normal text."
        )
        self._seed(("BL-001", "First", body), ("BL-002", "Second", "Body two."))
        text_before = self.backlog.read_text(encoding="utf-8")

        code, records, err = run_cli("delete-backlog", self.root, "--bl-id", "BL-002")
        self.assertEqual(code, 0, err)
        text = self.backlog.read_text(encoding="utf-8")
        self.assertIn(body, text)                       # fenced quote intact
        self.assertNotIn("## BL-002 — Second", text)     # real BL-002 gone
        self.assertEqual(records[0]["details"]["entries"], 1)
        # BL-001's section (preamble + heading + fenced body) is unchanged from
        # before the delete — only the trailing BL-002 block was removed.
        self.assertTrue(text_before.startswith(text.rstrip("\n")))

    def test_missing_id_fails_cleanly(self):
        self._seed(("BL-001", "First", "First body."))
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
