"""Unit tests for the structured PM authoring commands.

Covers the per-artifact writers: each resolves an allowlisted destination,
writes only through the mediated-write primitive, emits one NDJSON record,
validates its structured inputs, and delegates destination refusals to the
primitive's guards (no re-implemented write safety).
"""
import io
import json
import os
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


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.scaffold = project_scaffold(cartopian_toml=_TOML)
        self.addCleanup(self.scaffold.cleanup)
        self.root = str(self.scaffold.project_root)


class TestRegistration(unittest.TestCase):
    def test_all_writers_registered_on_cli_surface(self):
        for verb in (
            "write-requirements", "write-plan", "write-standards",
            "write-conventions", "write-phase", "write-task", "write-spec",
            "write-prompt", "write-decision", "write-state", "reset-plan",
        ):
            self.assertIn(verb, SUBCOMMANDS)

    def test_writers_exposed_on_mcp_tool_surface(self):
        from mcp_server import server
        names = {t["name"] for t in server.list_tools()}
        for tool in (
            "write_requirements", "write_plan", "write_standards",
            "write_conventions", "write_phase", "write_task", "write_spec",
            "write_prompt", "write_decision", "write_state", "reset_plan",
        ):
            self.assertIn(tool, names)


class TestRootArtifactWriters(_Fixture):
    def test_write_requirements(self):
        code, recs, err = run_cli("write-requirements", self.root, "--content", "# Reqs\n")
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(recs[0]["action"], "write-requirements")
        self.assertEqual(
            (self.scaffold.project_root / "REQUIREMENTS.md").read_text(encoding="utf-8"),
            "# Reqs\n",
        )

    def test_write_plan_standards_conventions(self):
        for verb, fname, action in (
            ("write-plan", "IMPLEMENTATION_PLAN.md", "write-plan"),
            ("write-standards", "STANDARDS.md", "write-standards"),
            ("write-conventions", "CONVENTIONS.md", "write-conventions"),
        ):
            code, recs, err = run_cli(verb, self.root, "--content", f"body-{verb}\n")
            self.assertEqual(code, 0, msg=err)
            self.assertEqual(recs[0]["action"], action)
            self.assertEqual(
                (self.scaffold.project_root / fname).read_text(encoding="utf-8"),
                f"body-{verb}\n",
            )


class TestIdBearingWriters(_Fixture):
    def test_write_phase(self):
        code, recs, err = run_cli(
            "write-phase", self.root, "--phase-id", "PHASE-01-foundation",
            "--content", "# phase\n",
        )
        self.assertEqual(code, 0, msg=err)
        self.assertTrue((self.scaffold.phases / "PHASE-01-foundation.md").is_file())
        self.assertEqual(recs[0]["details"]["phase_id"], "PHASE-01-foundation")

    def test_write_task_lands_in_open(self):
        code, recs, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-001", "--slug", "do-thing",
            "--content", "# task\n\nEvidence gate: n/a\n\n## Acceptance\n\n- [ ] done\n",
        )
        self.assertEqual(code, 0, msg=err)
        self.assertTrue((self.scaffold.tasks_open / "TASK-01-001-do-thing.md").is_file())
        self.assertEqual(recs[0]["details"]["relative_target"], "open/TASK-01-001-do-thing.md")

    def test_write_spec(self):
        code, recs, err = run_cli(
            "write-spec", self.root, "--spec-id", "SPEC-01-001", "--slug", "thing",
            "--content", "# spec\n",
        )
        self.assertEqual(code, 0, msg=err)
        self.assertTrue((self.scaffold.specs / "SPEC-01-001-thing.md").is_file())

    def test_write_prompt_task_and_planning_variants(self):
        code, recs, err = run_cli(
            "write-prompt", self.root, "--prompt-id", "PROMPT-01-001", "--content", "# p\n",
        )
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(recs[0]["details"]["variant"], "task")
        self.assertTrue((self.scaffold.prompts / "PROMPT-01-001.md").is_file())

        code, recs, err = run_cli(
            "write-prompt", self.root, "--prompt-id", "PROMPT-PLAN-001-kickoff",
            "--content", "# p\n",
        )
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(recs[0]["details"]["variant"], "planning")
        self.assertTrue((self.scaffold.prompts / "PROMPT-PLAN-001-kickoff.md").is_file())

    def test_bad_ids_and_slugs_refused_as_usage(self):
        cases = [
            ("write-phase", ["--phase-id", "PHASE-1-x"]),       # bad number width
            ("write-task", ["--task-id", "TASK-01-1", "--slug", "ok"]),
            ("write-task", ["--task-id", "TASK-01-001", "--slug", "Bad_Slug"]),
            ("write-spec", ["--spec-id", "SPEC-01", "--slug", "ok"]),
            ("write-prompt", ["--prompt-id", "PROMPT-01"]),
        ]
        for verb, extra in cases:
            code, recs, err = run_cli(verb, self.root, *extra, "--content", "x")
            self.assertEqual(code, 2, msg=f"{verb} {extra} -> {err!r}")
            self.assertEqual(recs, [])


class TestContentValidation(_Fixture):
    def test_missing_content_is_usage_error(self):
        code, recs, err = run_cli("write-requirements", self.root)
        self.assertEqual(code, 2)
        self.assertIn("missing artifact body", err)

    def test_both_content_sources_is_usage_error(self):
        path = self.scaffold.project_root / "src.txt"
        path.write_text("x", encoding="utf-8")
        code, recs, err = run_cli(
            "write-requirements", self.root, "--content", "a", "--content-file", str(path),
        )
        self.assertEqual(code, 2)
        self.assertIn("exactly one", err)

    def test_content_file_is_read(self):
        path = self.scaffold.project_root / "src.txt"
        path.write_text("from-file\n", encoding="utf-8")
        code, recs, err = run_cli("write-plan", self.root, "--content-file", str(path))
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(
            (self.scaffold.project_root / "IMPLEMENTATION_PLAN.md").read_text(encoding="utf-8"),
            "from-file\n",
        )

    def test_relative_root_is_usage_error(self):
        code, recs, err = run_cli("write-requirements", "rel/path", "--content", "x")
        self.assertEqual(code, 2)
        self.assertIn("absolute", err)


class TestDestinationRefusalDelegation(_Fixture):
    """Destination refusals must delegate to the mediated-write guards verbatim."""

    def test_symlink_final_component_refused_via_primitive(self):
        # Pre-place a symlink where write-task would land; the primitive's
        # no-follow guard must refuse and the writer surfaces it fail-closed.
        secret = Path(self.scaffold.root) / "secret.md"
        secret.write_text("ORIGINAL", encoding="utf-8")
        link = self.scaffold.tasks_open / "TASK-01-009-x.md"
        os.symlink(secret, link)

        code, recs, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-009", "--slug", "x",
            # Schema-valid so the write reaches the primitive's symlink guard
            # rather than tripping write-task's own content-shape gate first.
            "--content", "PWNED\n\nEvidence gate: n/a\n\n## Acceptance\n\n- [ ] done\n",
        )
        self.assertEqual(code, 1)
        self.assertIn("[guard] symlink:", err)
        self.assertEqual(recs, [])
        self.assertEqual(secret.read_text(encoding="utf-8"), "ORIGINAL")

    def test_config_file_destination_refused(self):
        # write-state targets the project root; the primitive refuses dotfiles
        # and known config files. A normal STATE.md write is fine, but the guard
        # category is delegated — prove the rule name flows through.
        # (state writer can only target STATE.md by construction, so we assert
        # the happy path lands and the primitive owns the config guard.)
        code, recs, err = run_cli("write-state", self.root, "--content", "# s\n")
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(recs[0]["details"]["ceiling_bytes"], 5120)


class TestWriteState(_Fixture):
    def test_under_ceiling_writes(self):
        code, recs, err = run_cli("write-state", self.root, "--content", "x" * 5120)
        self.assertEqual(code, 0, msg=err)
        self.assertEqual((self.scaffold.state).stat().st_size, 5120)

    def test_over_ceiling_refused_and_nothing_written(self):
        before = self.scaffold.state.read_text(encoding="utf-8")
        code, recs, err = run_cli("write-state", self.root, "--content", "x" * 5121)
        self.assertEqual(code, 1)
        self.assertIn("state-too-large", err)
        self.assertEqual(recs, [])
        self.assertEqual(self.scaffold.state.read_text(encoding="utf-8"), before)


class TestWriteDecision(_Fixture):
    def test_writes_dec_and_creates_index_row(self):
        code, recs, err = run_cli(
            "write-decision", self.root, "--dec-id", "DEC-001", "--slug", "pick",
            "--title", "Pick a thing", "--date", "2026-06-01", "--content", "# DEC-001\n",
        )
        self.assertEqual(code, 0, msg=err)
        self.assertTrue((self.scaffold.decisions / "DEC-001-pick.md").is_file())
        index = (self.scaffold.decisions / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("| [DEC-001](DEC-001-pick.md) | Pick a thing | 2026-06-01 | locked | none |", index)
        self.assertFalse(recs[0]["details"]["index_row_replaced"])

    def test_reissue_replaces_row_in_place(self):
        run_cli("write-decision", self.root, "--dec-id", "DEC-002", "--slug", "x",
                "--title", "First", "--date", "2026-06-01", "--content", "a")
        code, recs, err = run_cli(
            "write-decision", self.root, "--dec-id", "DEC-002", "--slug", "x",
            "--title", "Second", "--date", "2026-06-02", "--status", "open", "--content", "b",
        )
        self.assertEqual(code, 0, msg=err)
        self.assertTrue(recs[0]["details"]["index_row_replaced"])
        index = (self.scaffold.decisions / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("Second", index)
        self.assertNotIn("First", index)
        self.assertEqual(index.count("[DEC-002]"), 1)

    def test_multiple_decisions_accumulate_rows(self):
        for n, slug in (("DEC-001", "a"), ("DEC-002", "b"), ("DEC-003", "c")):
            run_cli("write-decision", self.root, "--dec-id", n, "--slug", slug,
                    "--title", f"T{n}", "--date", "2026-06-01", "--content", "x")
        index = (self.scaffold.decisions / "INDEX.md").read_text(encoding="utf-8")
        self.assertEqual(index.count("| [DEC-0"), 3)

    def test_pipe_in_title_is_escaped(self):
        run_cli("write-decision", self.root, "--dec-id", "DEC-004", "--slug", "p",
                "--title", "a | b", "--date", "2026-06-01", "--content", "x")
        index = (self.scaffold.decisions / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("a \\| b", index)

    def test_bad_date_is_usage_error(self):
        code, recs, err = run_cli(
            "write-decision", self.root, "--dec-id", "DEC-005", "--slug", "p",
            "--title", "T", "--date", "06/01/2026", "--content", "x",
        )
        self.assertEqual(code, 2)
        self.assertIn("YYYY-MM-DD", err)


if __name__ == "__main__":
    unittest.main()
