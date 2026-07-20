"""Regression coverage for PM-owned mediated plan archival."""
import os
import unittest
from pathlib import Path

from tests.cli.commands.test_fr005_structured_writers import _TOML, run_cli
from tests.scaffold import project_scaffold


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.scaffold = project_scaffold(cartopian_toml=_TOML)
        self.addCleanup(self.scaffold.cleanup)
        self.root = str(self.scaffold.project_root)
        self.scaffold.write("REQUIREMENTS.md", "# Requirements\n")
        self.scaffold.write("IMPLEMENTATION_PLAN.md", "# Completed Plan\n")
        self.scaffold.write("tasks/done/TASK-01-001-done.md", "# Done\n")
        self.scaffold.write("reports/REPORT-01-001.md", "# Report\n")
        self.closeout = Path(self.scaffold.root) / "closeout.md"
        self.closeout.write_text("# Plan Closeout: Demo\n", encoding="utf-8")

    def archive(self, slug="completed-plan"):
        return run_cli(
            "archive-plan",
            self.root,
            "--slug", slug,
            "--closed", "2026-07-20",
            "--summary", "Completed the plan",
            "--content-file", str(self.closeout),
        )


class TestArchivePlan(_Fixture):
    def test_registered_on_cli_and_mcp_surfaces(self):
        from cli.main import SUBCOMMANDS
        from mcp_server import server

        self.assertIn("archive-plan", SUBCOMMANDS)
        self.assertIn("archive_plan", {tool["name"] for tool in server.list_tools()})

    def test_accepts_inline_closeout_body_for_contained_pm(self):
        code, records, err = run_cli(
            "archive-plan",
            self.root,
            "--slug", "inline-body",
            "--closed", "2026-07-20",
            "--summary", "Completed the plan",
            "--content", "# Inline Closeout\n",
        )
        self.assertEqual(code, 0, msg=err)
        archive = Path(records[0]["details"]["archive_path"])
        self.assertEqual(
            (archive / "CLOSEOUT.md").read_text(encoding="utf-8"),
            "# Inline Closeout\n",
        )

    def test_creates_snapshot_closeout_and_index(self):
        code, records, err = self.archive()
        self.assertEqual(code, 0, msg=err)
        archive = self.scaffold.project_root / "archive" / "PLAN-001-completed-plan"
        self.assertEqual(records[0]["details"]["archive_path"], str(archive))
        self.assertEqual(
            (archive / "CLOSEOUT.md").read_text(encoding="utf-8"),
            "# Plan Closeout: Demo\n",
        )
        self.assertTrue((archive / "REQUIREMENTS.md").is_file())
        self.assertTrue((archive / "tasks/done/TASK-01-001-done.md").is_file())
        self.assertTrue((archive / "reports/REPORT-01-001.md").is_file())
        self.assertFalse((archive / "prompts").exists())
        self.assertFalse((archive / "CONVENTIONS.md").exists())
        index = (self.scaffold.project_root / "archive/INDEX.md").read_text(encoding="utf-8")
        self.assertIn("| `PLAN-001-completed-plan` | 2026-07-20 | Completed the plan |", index)

    def test_allocates_next_archive_number(self):
        code, _, err = self.archive("first")
        self.assertEqual(code, 0, msg=err)
        code, records, err = self.archive("second")
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(records[0]["details"]["archive_name"], "PLAN-002-second")

    def test_symlink_in_source_tree_refuses_before_snapshot(self):
        outside = Path(self.scaffold.root) / "outside.md"
        outside.write_text("secret", encoding="utf-8")
        os.symlink(outside, self.scaffold.project_root / "tasks/done/linked.md")

        code, records, err = self.archive()

        self.assertEqual(code, 1)
        self.assertEqual(records, [])
        self.assertIn("[guard] source-tree", err)
        self.assertFalse((self.scaffold.project_root / "archive").exists())

    def test_rejects_free_form_archive_path_syntax(self):
        code, records, err = self.archive("../escape")
        self.assertEqual(code, 2)
        self.assertEqual(records, [])
        self.assertIn("lowercase kebab-case", err)

    def test_rejects_non_canonical_date(self):
        code, records, err = run_cli(
            "archive-plan",
            self.root,
            "--slug", "completed-plan",
            "--closed", "20260720",
            "--summary", "Completed the plan",
            "--content-file", str(self.closeout),
        )
        self.assertEqual(code, 2)
        self.assertEqual(records, [])
        self.assertIn("YYYY-MM-DD", err)


if __name__ == "__main__":
    unittest.main()
