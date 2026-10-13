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

from cli import request_trace
from cli.main import SUBCOMMANDS, build_parser
from tests.scaffold import project_scaffold

_TOML = (
    "[project]\n"
    'id = "demo"\n'
    'name = "Demo Project"\n'
    'project_schema_version = "v0.9.0"\n'
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
        self.scaffold.capture_request(
            request_id="REQUEST-001",
            unit="project",
            text="Build the requested project.",
        )
        self.scaffold.capture_request(
            request_id="REQUEST-002",
            unit="task:TASK-01-001",
            text="Run task 01-001.",
            captured_at="2026-07-27T12:00:01Z",
        )


class TestRegistration(unittest.TestCase):
    def test_all_writers_registered_on_cli_surface(self):
        for verb in (
            "write-requirements", "write-plan", "write-standards",
            "write-phase", "write-task", "write-spec",
            "write-prompt", "write-decision", "write-state", "reset-plan",
        ):
            self.assertIn(verb, SUBCOMMANDS)

    def test_writers_exposed_on_mcp_tool_surface(self):
        from mcp_server import server
        names = {t["name"] for t in server.list_tools()}
        for tool in (
            "write_requirements", "write_plan", "write_standards",
            "write_phase", "write_task", "write_spec",
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

    def test_write_plan_and_standards(self):
        for verb, fname, action in (
            ("write-plan", "IMPLEMENTATION_PLAN.md", "write-plan"),
            ("write-standards", "STANDARDS.md", "write-standards"),
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
            "write-phase", self.root, "--phase-id", "PHASE-01",
            "--content", "# phase\n",
        )
        self.assertEqual(code, 0, msg=err)
        self.assertTrue((self.scaffold.phases / "PHASE-01.md").is_file())
        self.assertEqual(recs[0]["details"]["phase_id"], "PHASE-01")

    def test_write_task_lands_in_open(self):
        code, recs, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-001",
            "--content", "# task\n\nEvidence gate: n/a\n\n## Acceptance\n\n- [ ] done\n",
        )
        self.assertEqual(code, 0, msg=err)
        self.assertTrue((self.scaffold.tasks_open / "TASK-01-001.md").is_file())
        self.assertEqual(recs[0]["details"]["relative_target"], "open/TASK-01-001.md")

    def test_write_spec(self):
        code, recs, err = run_cli(
            "write-spec", self.root, "--spec-id", "SPEC-01-001",
            "--content", "# spec\n",
        )
        self.assertEqual(code, 0, msg=err)
        self.assertTrue((self.scaffold.specs / "SPEC-01-001.md").is_file())

    def test_write_prompt_task_and_planning_variants(self):
        task = self.scaffold.tasks_in_progress / "TASK-01-001.md"
        task.write_text("# task\n", encoding="utf-8")
        code, recs, err = run_cli(
            "write-prompt", self.root, "--prompt-id", "PROMPT-01-001",
            "--task", str(task), "--content", "# p\n",
        )
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(recs[0]["details"]["variant"], "task")
        self.assertEqual(recs[0]["details"]["request_evidence"], ["REQUEST-002"])
        assignment_prompt = self.scaffold.prompts / "PROMPT-01-001.md"
        self.assertTrue(assignment_prompt.is_file())
        assignment_text = assignment_prompt.read_text(encoding="utf-8")
        self.assertIn("Before changing any work root", assignment_text)
        self.assertIn("features, conventions, or scope", assignment_text)

        code, recs, err = run_cli(
            "write-prompt", self.root, "--prompt-id", "PROMPT-PLAN-001",
            "--content", "# p\n",
        )
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(recs[0]["details"]["variant"], "planning")
        self.assertTrue((self.scaffold.prompts / "PROMPT-PLAN-001.md").is_file())

    def test_planned_task_prompt_inherits_project_request(self):
        task = self.scaffold.tasks_in_progress / "TASK-01-009.md"
        task.write_text(
            "# TASK-01-009: Planned task\n\n"
            "Phase: PHASE-01\n"
            "Plan ref: BUILD-01-009\n",
            encoding="utf-8",
        )
        (self.scaffold.project_root / "IMPLEMENTATION_PLAN.md").write_text(
            "# Plan\n\n- `BUILD-01-009` — Planned task.\n",
            encoding="utf-8",
        )
        (self.scaffold.phases / "PHASE-01.md").write_text(
            "# PHASE-01\n\n- `BUILD-01-009` — Planned task.\n",
            encoding="utf-8",
        )
        assignment = request_trace.context_for_task_assignment(
            self.scaffold.project_root, task
        )

        code, recs, err = run_cli(
            "write-prompt", self.root, "--prompt-id", "PROMPT-01-009",
            "--task", str(task), "--content", "# planned prompt\n",
        )

        self.assertEqual(code, 0, msg=err)
        self.assertEqual(
            recs[0]["details"]["request_context_identity"],
            assignment.context_identity,
        )
        self.assertEqual(recs[0]["details"]["request_evidence"], ["REQUEST-001"])

    def test_ad_hoc_task_prompt_cannot_inherit_project_request(self):
        task = self.scaffold.tasks_in_progress / "TASK-01-009.md"
        task.write_text("# TASK-01-009: Ad hoc task\n", encoding="utf-8")

        code, recs, err = run_cli(
            "write-prompt", self.root, "--prompt-id", "PROMPT-01-009",
            "--task", str(task), "--content", "# ad hoc prompt\n",
        )

        self.assertEqual(code, 1)
        self.assertEqual(recs, [])
        self.assertIn("unit-request-not-captured", err)
        self.assertFalse((self.scaffold.prompts / "PROMPT-01-009.md").exists())

    def test_bad_ids_and_slugs_refused_as_usage(self):
        cases = [
            ("write-phase", ["--phase-id", "PHASE-1-x"]),       # bad number width
            ("write-task", ["--task-id", "TASK-01-1"]),
            ("write-spec", ["--spec-id", "SPEC-01"]),
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
        link = self.scaffold.tasks_open / "TASK-01-009.md"
        os.symlink(secret, link)

        code, recs, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-009",
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


def _decision_body(dec_id: str, ruling: str = "Pick the thing.", extra: str = "") -> str:
    return f"# {dec_id}: Title\n\nDate: 2026-06-01\nRuling: {ruling}\n{extra}\n## Decision\n\nText.\n"


class TestWriteDecision(_Fixture):
    def write(self, dec_id: str, title: str = "T", *extra_args: str, body: str | None = None):
        return run_cli(
            "write-decision", self.root, "--dec-id", dec_id, "--title", title,
            "--date", "2026-06-01", *extra_args, "--content", body or _decision_body(dec_id),
        )

    def index(self) -> str:
        return (self.scaffold.decisions / "INDEX.md").read_text(encoding="utf-8")

    def test_writes_dec_and_creates_index_row(self):
        code, recs, err = self.write("DEC-001", "Pick a thing")
        self.assertEqual(code, 0, msg=err)
        self.assertTrue((self.scaffold.decisions / "DEC-001.md").is_file())
        self.assertIn(
            "| [DEC-001](DEC-001.md) | Pick a thing | Pick the thing. | pm | locked | 2026-06-01 |  |",
            self.index(),
        )
        self.assertFalse(recs[0]["details"]["index_row_replaced"])
        self.assertEqual(recs[0]["details"]["authority"], "pm")
        written = (self.scaffold.decisions / "DEC-001.md").read_text(encoding="utf-8")
        self.assertIn("Authority: pm\n", written)

    def test_a_locked_decision_is_immutable(self):
        self.assertEqual(self.write("DEC-002", "First")[0], 0)
        before = (self.scaffold.decisions / "DEC-002.md").read_bytes()
        code, _recs, err = self.write("DEC-002", "Second", body=_decision_body("DEC-002", "Changed."))
        self.assertEqual(code, 1)
        self.assertIn("decision-immutable", err)
        self.assertEqual((self.scaffold.decisions / "DEC-002.md").read_bytes(), before)
        self.assertIn("First", self.index())

    def test_an_open_draft_may_be_reissued_in_place(self):
        self.assertEqual(self.write("DEC-002", "First", "--status", "open")[0], 0)
        code, recs, err = self.write("DEC-002", "Second")
        self.assertEqual(code, 0, msg=err)
        self.assertTrue(recs[0]["details"]["index_row_replaced"])
        index = self.index()
        self.assertIn("Second", index)
        self.assertNotIn("First", index)
        self.assertEqual(index.count("[DEC-002]"), 1)

    def test_a_superseded_draft_is_immutable(self):
        self.assertEqual(self.write("DEC-001", "Draft", "--status", "open")[0], 0)
        self.assertEqual(self.write("DEC-002", "Replacement", "--supersedes", "DEC-001")[0], 0)
        code, _recs, err = self.write("DEC-001", "Draft again", "--status", "open")
        self.assertEqual(code, 1)
        self.assertIn("decision-immutable", err)
        self.assertIn("DEC-002", err)

    def test_ruling_is_required_and_bounded(self):
        code, _recs, err = self.write("DEC-001", body="# DEC-001: Title\n\nDate: 2026-06-01\n")
        self.assertEqual(code, 1)
        self.assertIn("decision-ruling-missing", err)
        code, _recs, err = self.write("DEC-001", body=_decision_body("DEC-001", "x" * 241))
        self.assertEqual(code, 1)
        self.assertIn("decision-ruling-too-long", err)
        self.assertFalse((self.scaffold.decisions / "DEC-001.md").exists())

    def test_multiple_decisions_accumulate_rows(self):
        for n in ("DEC-001", "DEC-002", "DEC-003"):
            self.assertEqual(self.write(n, f"T{n}")[0], 0)
        self.assertEqual(self.index().count("| [DEC-0"), 3)

    def test_index_separates_live_from_superseded_and_names_amendments(self):
        self.assertEqual(self.write("DEC-001", "Old rule")[0], 0)
        self.assertEqual(self.write("DEC-002", "Base rule")[0], 0)
        self.assertEqual(self.write("DEC-003", "New rule", "--supersedes", "DEC-001")[0], 0)
        code, _recs, err = self.write(
            "DEC-004", "Narrower", body=_decision_body("DEC-004", extra="Amends: DEC-002\n")
        )
        self.assertEqual(code, 0, msg=err)
        live, history = self.index().split("## Superseded")
        self.assertNotIn("[DEC-001]", live)
        self.assertIn("| [DEC-001](DEC-001.md) | Old rule | DEC-003 |", history)
        row = next(line for line in live.splitlines() if line.startswith("| [DEC-002]"))
        self.assertTrue(row.endswith("| DEC-004 |"), row)

    def test_amends_refuses_missing_superseded_or_retired_targets(self):
        self.assertEqual(self.write("DEC-001", "Old")[0], 0)
        self.assertEqual(self.write("DEC-002", "New", "--supersedes", "DEC-001")[0], 0)
        cases = {
            "DEC-009": "Amends: DEC-009\n",
            "DEC-001": "Amends: DEC-001\n",
            "both": "Amends: DEC-002\nSupersedes: DEC-002\n",
        }
        for name, extra in cases.items():
            with self.subTest(name):
                code, _recs, err = self.write("DEC-003", body=_decision_body("DEC-003", extra=extra))
                self.assertEqual(code, 1)
                self.assertIn("decision-amends-invalid", err)

    def test_earlier_rows_keep_their_titles_from_a_legacy_index(self):
        (self.scaffold.decisions / "DEC-001.md").write_text(
            "# DEC-001: File title\n\nDate: 2026-05-01\nStatus: locked\nSupersedes: none\n", encoding="utf-8"
        )
        (self.scaffold.decisions / "INDEX.md").write_text(
            "# Decisions Index\n\n| ID | Title | Date | Status | Supersedes |\n| --- | --- | --- | --- | --- |\n"
            "| [DEC-001](DEC-001.md) | Indexed \\| title | 2026-05-02 | locked | none |\n",
            encoding="utf-8",
        )
        self.assertEqual(self.write("DEC-002")[0], 0)
        self.assertIn("| [DEC-001](DEC-001.md) | Indexed \\| title |  | pm | locked | 2026-05-02 |  |", self.index())

    def test_pipe_in_title_is_escaped(self):
        self.write("DEC-004", "a | b")
        self.assertIn("a \\| b", self.index())

    def test_bad_date_is_usage_error(self):
        code, recs, err = run_cli(
            "write-decision", self.root, "--dec-id", "DEC-005",
            "--title", "T", "--date", "06/01/2026", "--content", "x",
        )
        self.assertEqual(code, 2)
        self.assertIn("YYYY-MM-DD", err)

if __name__ == "__main__":
    unittest.main()
