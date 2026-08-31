"""The plain project-root ``CONTINUITY.md`` summary and its explicit retrieval.

Two behaviors are proven here and they are deliberately kept apart:

* what the summary *is* — plain Markdown a plan close writes, that one explicit
  command reads, whose absence is an ordinary success; and
* what the summary is *not* — an input to any automatic lifecycle surface.

The second is the load-bearing one. Every high-frequency reader is exercised
twice, with the summary present and with it absent, and the two records must be
identical: no key, no value, no rendered line, and no ordering may differ.
"""
import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli import continuity
from cli.commands import compose_state, next_action, read_continuity, write_continuity
from cli.main import SUBCOMMANDS, build_parser
from tests.scaffold import project_scaffold

_TOML_BASE = (
    "[project]\n"
    'id = "test-proj"\n'
    'name = "Test Project"\n'
    'project_schema_version = "v0.12.0"\n'
)

_SUMMARY = (
    "# Project summary\n"
    "\n"
    "The first plan delivered the config loader and left the migration path\n"
    "to the next plan. Nothing here is machine-read.\n"
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CLI_DIR = _REPO_ROOT / "cli"

# The two commands that are allowed to touch the artifact at all.
_SUMMARY_MODULES = {
    "cli/continuity.py",
    "cli/commands/write_continuity.py",
    "cli/commands/read_continuity.py",
}


def _write(project_root, content=_SUMMARY):
    args = argparse.Namespace(
        project_root=str(project_root), content=content, content_file=None
    )
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = write_continuity.handler(args)
    records = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    return code, records, err.getvalue()


def _read(project_root):
    args = argparse.Namespace(project_root=str(project_root))
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = read_continuity.handler(args)
    records = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    return code, records, err.getvalue()


def _capture(module, project_path):
    args = argparse.Namespace(project_path=str(project_path))
    captured = []
    original = module.emit_record

    def _sink(record, *, out=None):
        captured.append(record)

    module.emit_record = _sink
    try:
        code = module.handler(args)
    finally:
        module.emit_record = original
    return code, captured


@contextlib.contextmanager
def _isolated_home():
    """``next-action`` merges ``~/.cartopian/cartopian.toml`` during role
    resolution; keep the developer's real global config out of the record."""
    with tempfile.TemporaryDirectory(prefix="cartopian-home-") as tmp:
        with mock.patch.object(Path, "home", return_value=Path(tmp)):
            yield


class TestPlanCloseWritesPlainSummary(unittest.TestCase):
    def test_commands_are_registered_on_the_cli(self) -> None:
        self.assertIn("write-continuity", SUBCOMMANDS)
        self.assertIn("read-continuity", SUBCOMMANDS)
        parser = build_parser()
        self.assertEqual(
            parser.parse_args(["write-continuity", "/tmp/p", "--content", "x"]).cmd,
            "write-continuity",
        )
        self.assertEqual(
            parser.parse_args(["read-continuity", "/tmp/p"]).cmd, "read-continuity"
        )

    def test_write_creates_the_plain_artifact_byte_for_byte(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            code, records, err = _write(scaffold.project_root)
            self.assertEqual(code, 0, err)
            artifact = scaffold.project_root / "CONTINUITY.md"
            self.assertEqual(artifact.read_text(encoding="utf-8"), _SUMMARY)
            self.assertEqual(records[0]["action"], "write-continuity")
            self.assertFalse(records[0]["details"]["replaced"])
            # Plain Markdown: no format header, no schema, no table grammar.
            self.assertNotIn("Format:", artifact.read_text(encoding="utf-8"))

    def test_write_adds_no_bytes_of_its_own(self) -> None:
        """"Byte for byte" is a claim, so it is tested where it can fail: a
        body with no trailing newline is written exactly as supplied, and the
        writer normalizes nothing."""
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            body = "# Close\n\nNo trailing newline."
            code, records, err = _write(scaffold.project_root, body)
            self.assertEqual(code, 0, err)
            artifact = scaffold.project_root / "CONTINUITY.md"
            self.assertEqual(artifact.read_bytes(), body.encode("utf-8"))
            self.assertEqual(records[0]["details"]["bytes"], len(body.encode("utf-8")))

    def test_rewrite_replaces_the_summary_in_place(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            _write(scaffold.project_root)
            code, records, err = _write(scaffold.project_root, "# Second close\n")
            self.assertEqual(code, 0, err)
            self.assertTrue(records[0]["details"]["replaced"])
            self.assertEqual(
                (scaffold.project_root / "CONTINUITY.md").read_text(encoding="utf-8"),
                "# Second close\n",
            )

    def test_empty_summary_is_refused(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            code, _records, err = _write(scaffold.project_root, "   \n")
            self.assertEqual(code, 2)
            self.assertIn("non-empty", err)
            self.assertFalse((scaffold.project_root / "CONTINUITY.md").exists())

    def test_write_leaves_ordinary_archive_state_untouched(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            archive = scaffold.project_root / "archive"
            archive.mkdir()
            (archive / "INDEX.md").write_text("# Archive index\n", encoding="utf-8")
            before = sorted(path.name for path in archive.iterdir())
            index_before = (archive / "INDEX.md").read_text(encoding="utf-8")

            self.assertEqual(_write(scaffold.project_root)[0], 0)

            self.assertEqual(sorted(path.name for path in archive.iterdir()), before)
            self.assertEqual(
                (archive / "INDEX.md").read_text(encoding="utf-8"), index_before
            )


class TestOrdinaryCloseoutBehaviorIsUnchanged(unittest.TestCase):
    """The summary sits beside the archive lifecycle, never inside it."""

    def test_summary_survives_reset_and_is_never_archived(self) -> None:
        from tests.cli.commands.test_fr005_structured_writers import _TOML, run_cli

        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.capture_request(
                request_id="REQUEST-001",
                unit="project",
                text="Build the requested project.",
            )
            scaffold.write("REQUIREMENTS.md", "# reqs\n")
            scaffold.write("IMPLEMENTATION_PLAN.md", "# plan\n")
            scaffold.write("STANDARDS.md", "# standards\n")
            self.assertEqual(_write(scaffold.project_root)[0], 0)

            code, records, err = run_cli(
                "archive-plan",
                str(scaffold.project_root),
                "--closed",
                "2026-08-31",
                "--summary",
                "first plan",
                "--content",
                "# CLOSEOUT\n",
            )
            self.assertEqual(code, 0, err)
            archive = Path(records[-1]["details"]["archive_path"])
            # The summary is not plan content: the snapshot never gains it.
            self.assertFalse((archive / "CONTINUITY.md").exists())

            code, _records, err = run_cli("reset-plan", str(scaffold.project_root))
            self.assertEqual(code, 0, err)
            # …and reset does not name it either.
            self.assertEqual(
                (scaffold.project_root / "CONTINUITY.md").read_text(encoding="utf-8"),
                _SUMMARY,
            )
            self.assertFalse((scaffold.project_root / "REQUIREMENTS.md").exists())


class TestExplicitRetrieval(unittest.TestCase):
    def test_present_summary_is_returned_verbatim(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            _write(scaffold.project_root)
            code, records, err = _read(scaffold.project_root)
            self.assertEqual(code, 0, err)
            self.assertTrue(records[0]["present"])
            self.assertEqual(records[0]["content"], _SUMMARY)
            self.assertEqual(records[0]["path"], "CONTINUITY.md")

    def test_absent_summary_is_a_success_that_adds_nothing(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            code, records, err = _read(scaffold.project_root)
            self.assertEqual(code, 0, err)
            self.assertEqual(err, "")
            self.assertFalse(records[0]["present"])
            # No body key at all — "no summary" cannot be read as content.
            self.assertNotIn("content", records[0])

    def test_symlinked_artifact_is_refused_by_name(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            outside = scaffold.root / "elsewhere.md"
            outside.write_text("# not the project's summary\n", encoding="utf-8")
            os.symlink(outside, scaffold.project_root / "CONTINUITY.md")
            code, records, err = _read(scaffold.project_root)
            self.assertEqual(code, 1)
            self.assertEqual(records, [])
            self.assertIn("continuity-not-regular", err)

    def test_undecodable_artifact_is_refused_by_name(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            (scaffold.project_root / "CONTINUITY.md").write_bytes(b"\xff\xfe\x00bad")
            code, _records, err = _read(scaffold.project_root)
            self.assertEqual(code, 1)
            self.assertIn("continuity-not-utf8", err)

    def test_module_read_reports_absence_rather_than_failing(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            self.assertIsNone(continuity.read_summary(scaffold.project_root))


class TestAutomaticSurfacesCarryNoSummaryContent(unittest.TestCase):
    """The same record, summary or no summary, on every automatic reader."""

    def _next_action_record(self, scaffold):
        with _isolated_home():
            code, records = _capture(next_action, scaffold.project_root)
        self.assertEqual(code, 0)
        return records[0]

    def _compose_state_record(self, scaffold):
        code, records = _capture(compose_state, scaffold.project_root)
        self.assertEqual(code, 0)
        return records[0]

    def _seed_live_plan(self, scaffold) -> None:
        scaffold.write("phases/PHASE-01.md", "# PHASE-01: Foundation\n")
        scaffold.write(
            "tasks/open/TASK-01-001.md", "# TASK-01-001: First\n\nPhase: PHASE-01\n"
        )

    def test_next_action_is_identical_with_and_without_a_summary(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            self._seed_live_plan(scaffold)
            without = self._next_action_record(scaffold)
            _write(scaffold.project_root)
            with_summary = self._next_action_record(scaffold)

            self.assertEqual(with_summary, without)
            self.assertNotIn("continuity", with_summary)
            self.assertNotIn("Project summary", json.dumps(with_summary))
            self.assertNotIn("CONTINUITY", json.dumps(with_summary))

    def test_compose_state_is_identical_with_and_without_a_summary(self) -> None:
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            self._seed_live_plan(scaffold)
            without = self._compose_state_record(scaffold)
            _write(scaffold.project_root)
            with_summary = self._compose_state_record(scaffold)

            self.assertEqual(with_summary, without)
            self.assertNotIn("continuity", with_summary)
            # Including the prose STATE.md is composed from.
            self.assertNotIn("CONTINUITY", with_summary["rendered_body"] or "")
            self.assertNotIn(
                "config loader", (with_summary["rendered_body"] or "").lower()
            )

    def test_compose_state_no_plan_shape_is_identical_too(self) -> None:
        """The post-reset shape is where an automatic injection would be most
        tempting: it is the record a session sees right after a plan closed."""
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            without = self._compose_state_record(scaffold)
            _write(scaffold.project_root)
            with_summary = self._compose_state_record(scaffold)

            self.assertEqual(with_summary, without)
            self.assertIsNone(with_summary["rendered_body"])
            self.assertNotIn("continuity", with_summary)

    def test_a_damaged_summary_cannot_block_an_automatic_surface(self) -> None:
        """Fail-closed reading belongs to the explicit path only. A project
        whose artifact is unreadable still starts and still composes state."""
        with project_scaffold(cartopian_toml=_TOML_BASE) as scaffold:
            self._seed_live_plan(scaffold)
            clean_next = self._next_action_record(scaffold)
            clean_state = self._compose_state_record(scaffold)

            (scaffold.project_root / "CONTINUITY.md").write_bytes(b"\xff\xfe\x00bad")

            self.assertEqual(self._next_action_record(scaffold), clean_next)
            self.assertEqual(self._compose_state_record(scaffold), clean_state)


class TestNoAutomaticInjectionRemains(unittest.TestCase):
    """Static proof that the removed machinery has no surviving consumer."""

    def _cli_sources(self):
        for path in sorted(_CLI_DIR.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            relative = path.relative_to(_CLI_DIR.parents[0]).as_posix()
            yield relative, path.read_text(encoding="utf-8")

    def test_only_the_summary_commands_reference_the_artifact(self) -> None:
        offenders = [
            relative
            for relative, text in self._cli_sources()
            if relative not in _SUMMARY_MODULES
            and ("cli.continuity" in text or "CONTINUITY.md" in text)
            # The mediated-write allowlist names the destination; it performs
            # no read and derives nothing from the body.
            and relative != "cli/mediated_write.py"
        ]
        self.assertEqual(offenders, [])

    def test_projection_and_compact_index_machinery_is_absent(self) -> None:
        removed = (
            "continuity-v1",
            "PROJECTION_KEY",
            "read_projection",
            "CONTINUITY_PROJECTION_MAX_BYTES",
            "PROJECTION_LIVE_ROW_CAP",
            "Cold index",
            "prune-continuity",
            "release-reservation",
            "archive+index",
        )
        offenders = []
        for relative, text in self._cli_sources():
            for token in removed:
                if token in text:
                    offenders.append(f"{relative}: {token}")
        self.assertEqual(offenders, [])

    def test_the_mcp_server_reads_no_summary_of_its_own(self) -> None:
        for path in sorted((_REPO_ROOT / "mcp_server").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("continuity", text, msg=path.name)
            self.assertNotIn("CONTINUITY", text, msg=path.name)

    def test_startup_guidance_does_not_mention_the_summary(self) -> None:
        """A normal startup neither reads the summary nor speaks about it."""
        startup = (_REPO_ROOT / "skills" / "start-session.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("CONTINUITY", startup)
        self.assertNotIn("continuity", startup)

    def test_no_projection_only_bound_survives(self) -> None:
        source = (_CLI_DIR / "continuity.py").read_text(encoding="utf-8")
        for token in ("MAX_BYTES", "ROW_CAP", "CHAR_MAX", "BYTE_MAX", "LIST_MAX"):
            self.assertNotIn(token, source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
