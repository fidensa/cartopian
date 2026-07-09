"""Unit tests for `write-task` id-uniqueness and in-place update.

A task id lives in exactly one of ``tasks/{open,in-progress,in-review,done}/``.
Re-issuing ``write-task`` for an existing id must update that file in place in
its current status directory (renaming within it on a slug change) — never
create a second copy in ``tasks/open/``. Only a genuinely new id creates a
file, in ``tasks/open/``. A pre-existing multi-directory collision fails
closed, names every colliding path, and writes nothing.
"""
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

from cli.main import build_parser
from tests.scaffold import project_scaffold

_TOML = (
    "[project]\n"
    'id = "demo"\n'
    'name = "Demo Project"\n'
    'protocol_version = "v0.3.0"\n'
)

# A schema-valid task body: write-task refuses one that could never pass
# readiness, so these placement/rename/record tests must supply a body that
# clears the content-shape gate (Evidence gate header + Acceptance checkbox).
_BODY = "# v2\n\nEvidence gate: n/a\n\n## Acceptance\n\n- [ ] done\n"


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

    def all_task_files(self):
        """Every task file currently on disk, as tasks/-relative POSIX paths."""
        tasks = self.scaffold.project_root / "tasks"
        return sorted(
            str(p.relative_to(tasks)) for p in tasks.rglob("*.md") if p.is_file()
        )


class TestUpdateInPlace(_Fixture):
    def test_update_in_place_in_each_non_open_status(self):
        for status_dir, status in (
            (self.scaffold.tasks_in_progress, "in-progress"),
            (self.scaffold.tasks_in_review, "in-review"),
            (self.scaffold.tasks_done, "done"),
        ):
            with self.subTest(status=status):
                task_id = "TASK-01-001"
                existing = status_dir / f"{task_id}-do-thing.md"
                existing.write_text("# v1\n", encoding="utf-8")

                code, recs, err = run_cli(
                    "write-task", self.root, "--task-id", task_id,
                    "--slug", "do-thing", "--content", _BODY,
                )
                self.assertEqual(code, 0, msg=err)
                self.assertEqual(existing.read_text(encoding="utf-8"), _BODY)
                self.assertFalse(
                    (self.scaffold.tasks_open / f"{task_id}-do-thing.md").exists(),
                    msg=f"duplicate created in open/ for id residing in {status}/",
                )
                self.assertEqual(
                    self.all_task_files(), [f"{status}/{task_id}-do-thing.md"]
                )
                existing.unlink()  # reset for the next status

    def test_slug_change_renames_in_place_within_status_dir(self):
        old = self.scaffold.tasks_in_review / "TASK-01-002-old-slug.md"
        old.write_text("# v1\n", encoding="utf-8")

        code, recs, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-002",
            "--slug", "new-slug", "--content", _BODY,
        )
        self.assertEqual(code, 0, msg=err)
        renamed = self.scaffold.tasks_in_review / "TASK-01-002-new-slug.md"
        self.assertTrue(renamed.is_file())
        self.assertEqual(renamed.read_text(encoding="utf-8"), _BODY)
        self.assertFalse(old.exists(), msg="old-slug file left behind after rename")
        self.assertEqual(self.all_task_files(), ["in-review/TASK-01-002-new-slug.md"])

    def test_new_id_still_lands_in_open(self):
        code, recs, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-003",
            "--slug", "fresh", "--content", _BODY,
        )
        self.assertEqual(code, 0, msg=err)
        self.assertTrue(
            (self.scaffold.tasks_open / "TASK-01-003-fresh.md").is_file()
        )
        self.assertEqual(self.all_task_files(), ["open/TASK-01-003-fresh.md"])


class TestCollision(_Fixture):
    def test_multi_directory_collision_fails_closed_naming_all_paths(self):
        a = self.scaffold.tasks_open / "TASK-01-004-thing.md"
        b = self.scaffold.tasks_in_review / "TASK-01-004-thing.md"
        a.write_text("# copy-a\n", encoding="utf-8")
        b.write_text("# copy-b\n", encoding="utf-8")

        code, recs, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-004",
            "--slug", "thing", "--content", _BODY,
        )
        self.assertEqual(code, 1)
        self.assertEqual(recs, [])
        self.assertIn("[guard]", err)
        self.assertIn(str(a), err)
        self.assertIn(str(b), err)
        # Nothing written: both copies untouched.
        self.assertEqual(a.read_text(encoding="utf-8"), "# copy-a\n")
        self.assertEqual(b.read_text(encoding="utf-8"), "# copy-b\n")
        self.assertEqual(
            self.all_task_files(),
            ["in-review/TASK-01-004-thing.md", "open/TASK-01-004-thing.md"],
        )


class TestSchemaGate(_Fixture):
    """write-task refuses a body that could never pass validate-task-readiness,
    fail-closed, before touching disk."""

    def test_missing_evidence_gate_and_acceptance_refused(self):
        code, recs, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-007", "--slug", "bad",
            "--content", "# TASK-01-007: incomplete\n\nPhase: PHASE-01-x\n",
        )
        self.assertEqual(code, 1)
        self.assertEqual(recs, [])
        self.assertIn("[guard] task-schema-invalid", err)
        self.assertIn("Evidence gate", err)
        self.assertIn("## Acceptance", err)
        # Nothing written anywhere.
        self.assertEqual(self.all_task_files(), [])

    def test_missing_acceptance_alone_refused_without_writing(self):
        code, recs, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-008", "--slug", "bad",
            "--content", "# t\n\nEvidence gate: required\n",
        )
        self.assertEqual(code, 1)
        self.assertEqual(recs, [])
        self.assertIn("task-schema-invalid", err)
        self.assertNotIn("Evidence gate:", err)  # gate value was fine
        self.assertEqual(self.all_task_files(), [])

    def test_schema_refusal_does_not_rename_existing_task(self):
        # An in-place slug change whose new body is invalid must leave the
        # original file untouched (the refusal precedes the rename).
        existing = self.scaffold.tasks_in_progress / "TASK-01-010-old.md"
        existing.write_text("# original\n", encoding="utf-8")
        code, recs, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-010", "--slug", "new",
            "--content", "# no gate, no acceptance\n",
        )
        self.assertEqual(code, 1)
        self.assertIn("task-schema-invalid", err)
        self.assertTrue(existing.is_file(), msg="original renamed despite refusal")
        self.assertEqual(existing.read_text(encoding="utf-8"), "# original\n")
        self.assertEqual(self.all_task_files(), ["in-progress/TASK-01-010-old.md"])


class TestRecordDestination(_Fixture):
    def test_record_names_actual_destination_directory(self):
        existing = self.scaffold.tasks_done / "TASK-01-005-shipped.md"
        existing.write_text("# v1\n", encoding="utf-8")

        code, recs, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-005",
            "--slug", "shipped", "--content", _BODY,
        )
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(len(recs), 1)
        details = recs[0]["details"]
        self.assertEqual(details["relative_target"], "done/TASK-01-005-shipped.md")
        # mediated_write canonicalizes with realpath (macOS tempdirs are
        # symlinked under /private), so compare resolved paths.
        self.assertEqual(details["path"], str(existing.resolve()))

    def test_record_for_new_id_names_open(self):
        code, recs, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-006",
            "--slug", "brand-new", "--content", _BODY,
        )
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(
            recs[0]["details"]["relative_target"], "open/TASK-01-006-brand-new.md"
        )


if __name__ == "__main__":
    unittest.main()
