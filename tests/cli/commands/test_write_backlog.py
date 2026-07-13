"""Tests for `cartopian write-backlog`.

The durable, CLI-supported home for PM/reviewer follow-up notes: one
``## BL-NNN — <title>`` section per entry in the project-root ``BACKLOG.md``,
written exclusively through the mediated-write primitive
(``backlog`` dest_kind → the allowlisted root file). Ids are **writer-allocated
and never reused** — omitting ``--bl-id`` mints the next id from the
``Highest id issued:`` preamble mark; supplying ``--bl-id`` is legal only to
revise a live entry. The hand-authored preamble survives; ``STATE.md`` is never
touched.
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
    'protocol_version = "v0.4.0"\n'
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

    def _add(self, title, body="body"):
        """Allocate a fresh entry (writer-assigned id); return the allocated id."""
        code, records, err = run_cli(
            "write-backlog", self.root, "--title", title, "--content", body
        )
        self.assertEqual(code, 0, err)
        return records[0]["details"]["bl_id"]


class TestRegistration(unittest.TestCase):
    def test_registered_on_cli_surface(self):
        self.assertIn("write-backlog", SUBCOMMANDS)

    def test_exposed_on_mcp_tool_surface(self):
        from mcp_server import server
        names = {t["name"] for t in server.list_tools()}
        self.assertIn("write_backlog", names)


class TestWriteBacklog(_Fixture):
    def test_allocates_first_id_and_records_mark(self):
        code, records, err = run_cli(
            "write-backlog", self.root,
            "--title", "Harden the thing", "--content", "Surfaced: 2026-06-04.\n\nBody.",
        )
        self.assertEqual(code, 0, err)
        text = self.backlog.read_text(encoding="utf-8")
        self.assertIn("# Backlog", text)
        self.assertIn("Highest id issued: BL-001", text)
        self.assertIn("## BL-001 — Harden the thing", text)
        self.assertIn("Surfaced: 2026-06-04.", text)
        details = records[0]["details"]
        self.assertEqual(details["bl_id"], "BL-001")
        self.assertTrue(details["allocated"])
        self.assertEqual(details["highest_id_issued"], "BL-001")
        self.assertFalse(details["entry_replaced"])
        self.assertEqual(details["entries"], 1)

    def test_allocates_monotonically(self):
        self.assertEqual(self._add("First"), "BL-001")
        self.assertEqual(self._add("Second"), "BL-002")
        self.assertEqual(self._add("Third"), "BL-003")
        self.assertIn("Highest id issued: BL-003", self.backlog.read_text(encoding="utf-8"))

    def test_ids_are_never_reused_after_deletion(self):
        self._add("First")            # BL-001
        self._add("Second")           # BL-002
        # Discard the highest entry, then allocate again: the mark does not
        # regress, so the next id is BL-003 — never the vacated BL-002.
        code, _r, err = run_cli("delete-backlog", self.root, "--bl-id", "BL-002", "--discard")
        self.assertEqual(code, 0, err)
        self.assertEqual(self._add("Third"), "BL-003")
        text = self.backlog.read_text(encoding="utf-8")
        self.assertIn("Highest id issued: BL-003", text)
        self.assertNotIn("## BL-002", text)

    def test_supplied_id_revises_live_entry_in_place(self):
        self._add("First")            # BL-001
        self._add("Second")           # BL-002
        code, records, err = run_cli(
            "write-backlog", self.root, "--bl-id", "BL-001",
            "--title", "First (revised)", "--content", "New body.",
        )
        self.assertEqual(code, 0, err)
        text = self.backlog.read_text(encoding="utf-8")
        self.assertIn("## BL-001 — First (revised)", text)
        self.assertIn("New body.", text)
        self.assertIn("## BL-002 — Second", text)      # untouched sibling
        self.assertIn("Highest id issued: BL-002", text)  # mark unchanged by a revise
        details = records[0]["details"]
        self.assertTrue(details["entry_replaced"])
        self.assertFalse(details["allocated"])
        self.assertEqual(details["entries"], 2)

    def test_supplied_id_for_nonlive_entry_is_refused(self):
        self._add("First")            # BL-001
        code, _records, err = run_cli(
            "write-backlog", self.root, "--bl-id", "BL-005",
            "--title", "invent", "--content", "b",
        )
        self.assertEqual(code, 1)
        self.assertIn("[guard]", err)
        self.assertIn("backlog-id-not-live", err)

    def test_regressed_mark_is_refused(self):
        # Only a raw hand-edit can drop the mark below a live id.
        self.backlog.write_text(
            "# Backlog\n\nHighest id issued: BL-001\n\n## BL-004 — Live\n\nBody.\n",
            encoding="utf-8",
        )
        code, _records, err = run_cli(
            "write-backlog", self.root, "--title", "New", "--content", "b"
        )
        self.assertEqual(code, 1)
        self.assertIn("[guard]", err)
        self.assertIn("backlog-mark-regressed", err)

    def test_legacy_file_without_mark_self_heals(self):
        # A file predating the field: adopt the highest live id, then allocate.
        self.backlog.write_text(
            "# Backlog\n\nContext.\n\n## BL-001 — One\n\nA.\n\n## BL-002 — Two\n\nB.\n",
            encoding="utf-8",
        )
        code, records, err = run_cli(
            "write-backlog", self.root, "--title", "Three", "--content", "C"
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(records[0]["details"]["bl_id"], "BL-003")
        text = self.backlog.read_text(encoding="utf-8")
        self.assertIn("Highest id issued: BL-003", text)
        self.assertIn("Context.", text)

    def test_invalid_bl_id_is_usage_error(self):
        for bad in ("BL-1", "bl-001", "BL-0001", "TASK-01-001"):
            code, _records, err = run_cli(
                "write-backlog", self.root, "--bl-id", bad,
                "--title", "t", "--content", "b",
            )
            self.assertEqual(code, 2, f"{bad}: {err}")
            self.assertIn("[usage]", err)

    def test_multiline_title_is_collapsed_to_one_heading_line(self):
        code, records, err = run_cli(
            "write-backlog", self.root,
            "--title", "line one\nline two", "--content", "b",
        )
        self.assertEqual(code, 0, err)
        text = self.backlog.read_text(encoding="utf-8")
        self.assertIn(f"## {records[0]['details']['bl_id']} — line one line two", text)

    def test_state_md_is_never_touched(self):
        state = Path(self.root) / "STATE.md"
        state.write_text("# State\n\ncomposed state only\n", encoding="utf-8")
        before = state.read_text(encoding="utf-8")
        self._add("t")
        self.assertEqual(state.read_text(encoding="utf-8"), before)

    def test_hand_authored_preamble_survives_append(self):
        self.backlog.write_text(
            "# Demo — Backlog\n\nHand-authored context paragraph.\n\n"
            "Highest id issued: BL-001\n\n## BL-001 — Existing\n\nOld body.\n",
            encoding="utf-8",
        )
        second = self._add("Second", "Body two.")
        self.assertEqual(second, "BL-002")
        text = self.backlog.read_text(encoding="utf-8")
        self.assertIn("Hand-authored context paragraph.", text)
        self.assertIn("## BL-001 — Existing", text)
        self.assertIn("Old body.", text)
        self.assertIn("## BL-002 — Second", text)
        self.assertLess(text.index("BL-001 —"), text.index("BL-002 —"))
        self.assertIn("Highest id issued: BL-002", text)

    def test_fenced_heading_in_body_round_trips_intact(self):
        """A `## BL-NNN` line inside a fenced code block is body content, not a
        section boundary: later writes must not shear the entry or register a
        phantom section."""
        body = (
            "Intro.\n\n```md\n## BL-099 example\nsome body text\n```\n\n"
            "More normal text."
        )
        run_cli("write-backlog", self.root, "--title", "First", "--content", body)
        code, records, err = run_cli(
            "write-backlog", self.root, "--title", "Second", "--content", "Body two.",
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(records[0]["details"]["entries"], 2)  # no phantom BL-099
        text = self.backlog.read_text(encoding="utf-8")
        self.assertIn(body, text)  # first entry's body intact, byte-for-byte
        # Re-issuing the live entry must replace the WHOLE entry, orphaning nothing.
        run_cli("write-backlog", self.root, "--bl-id", "BL-001",
                "--title", "First (revised)", "--content", "Short now.")
        text = self.backlog.read_text(encoding="utf-8")
        self.assertNotIn("BL-099", text)
        self.assertNotIn("More normal text.", text)
        self.assertIn("## BL-002 — Second", text)

    def test_body_content_is_preserved_byte_for_byte(self):
        """Bodies with consecutive blank lines or lines beginning `## BL-` must
        round-trip unaltered — the write is content-preserving."""
        body = (
            "```text\nline1\n\n\nline2\n```\n\n"
            "as discussed below:\n## BL-9 ref continued"
        )
        code, _records, err = run_cli(
            "write-backlog", self.root, "--title", "t", "--content", body,
        )
        self.assertEqual(code, 0, err)
        text = self.backlog.read_text(encoding="utf-8")
        self.assertIn(body, text)
        self.assertIn("line1\n\n\nline2", text)          # no blank-line collapse
        self.assertIn("below:\n## BL-9 ref", text)        # no injected blank line

    def test_unclosed_fence_degrades_without_shearing(self):
        """An unclosed fence in a body must not corrupt the file: everything
        after it stays in that entry (conservative), nothing is sheared."""
        run_cli("write-backlog", self.root,
                "--title", "First", "--content", "```\nunclosed fence\n## BL-099 quoted")
        code, _records, err = run_cli(
            "write-backlog", self.root, "--title", "Second", "--content", "Body two.",
        )
        self.assertEqual(code, 0, err)
        text = self.backlog.read_text(encoding="utf-8")
        self.assertIn("unclosed fence", text)
        self.assertIn("## BL-002 — Second", text)

    def test_writes_go_through_the_mediated_primitive(self):
        """A symlinked BACKLOG.md must be refused by the primitive's no-follow
        guard — proof the writer carries no raw-write bypass."""
        target = Path(self.root) / "elsewhere.md"
        target.write_text("x", encoding="utf-8")
        self.backlog.symlink_to(target)
        code, _records, err = run_cli(
            "write-backlog", self.root, "--title", "t", "--content", "b",
        )
        self.assertEqual(code, 1, err)
        self.assertIn("[guard]", err)
        self.assertEqual(target.read_text(encoding="utf-8"), "x")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
