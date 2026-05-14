"""P02-BUILD-011 acceptance scaffold helper coverage.

Exercises ``tests.scaffold.project_scaffold`` as a minimal sample-skill
walkthrough: a per-test temp directory shaped like a Cartopian project,
with predictable substructure and deterministic cleanup. This is the
seam that replaces the former ``projects/sample-project/`` dependency
for skill-rewrite acceptance walkthroughs.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from tests.scaffold import ProjectScaffold, project_scaffold


EXPECTED_SUBDIRS = (
    "decisions",
    "phases",
    "prompts",
    "reports",
    "reviews",
    "specs",
    "tasks/open",
    "tasks/in-progress",
    "tasks/in-review",
    "tasks/done",
)


class ScaffoldHelperTests(unittest.TestCase):
    def test_predictable_substructure_and_absolute_paths(self) -> None:
        scaffold = project_scaffold()
        self.addCleanup(scaffold.cleanup)

        self.assertIsInstance(scaffold, ProjectScaffold)
        self.assertTrue(scaffold.project_root.is_absolute())
        self.assertTrue(scaffold.project_root.is_dir())
        for sub in EXPECTED_SUBDIRS:
            with self.subTest(subdir=sub):
                target = scaffold.project_root / sub
                self.assertTrue(target.is_dir(), msg=f"missing scaffold subdir: {sub}")
        self.assertTrue(scaffold.config.is_file())
        self.assertTrue(scaffold.state.is_file())
        toml_text = scaffold.config.read_text(encoding="utf-8")
        self.assertIn("[project]", toml_text)
        self.assertIn("protocol_version", toml_text)

    def test_named_subdir_properties_match_layout(self) -> None:
        with project_scaffold() as scaffold:
            self.assertEqual(scaffold.tasks_open, scaffold.project_root / "tasks" / "open")
            self.assertEqual(
                scaffold.tasks_in_progress, scaffold.project_root / "tasks" / "in-progress"
            )
            self.assertEqual(
                scaffold.tasks_in_review, scaffold.project_root / "tasks" / "in-review"
            )
            self.assertEqual(scaffold.tasks_done, scaffold.project_root / "tasks" / "done")
            self.assertEqual(scaffold.prompts, scaffold.project_root / "prompts")
            self.assertEqual(scaffold.reports, scaffold.project_root / "reports")
            self.assertEqual(scaffold.reviews, scaffold.project_root / "reviews")
            self.assertEqual(scaffold.specs, scaffold.project_root / "specs")
            self.assertEqual(scaffold.phases, scaffold.project_root / "phases")
            self.assertEqual(scaffold.decisions, scaffold.project_root / "decisions")

    def test_write_helper_creates_file_under_project_root(self) -> None:
        with project_scaffold() as scaffold:
            written = scaffold.write(
                "tasks/open/TASK-99-001-demo.md",
                "# TASK-99-001\n\nDemo task.\n",
            )
            self.assertEqual(written, scaffold.tasks_open / "TASK-99-001-demo.md")
            self.assertTrue(written.is_file())
            self.assertIn("Demo task.", written.read_text(encoding="utf-8"))

    def test_each_scaffold_is_isolated(self) -> None:
        a = project_scaffold()
        b = project_scaffold()
        self.addCleanup(a.cleanup)
        self.addCleanup(b.cleanup)
        self.assertNotEqual(a.project_root, b.project_root)
        # Files written in one scaffold are invisible to the other.
        a.write("tasks/open/TASK-A.md", "a")
        self.assertFalse((b.tasks_open / "TASK-A.md").exists())

    def test_cleanup_removes_temp_directory(self) -> None:
        scaffold = project_scaffold()
        root = scaffold.root
        self.assertTrue(root.is_dir())
        scaffold.cleanup()
        self.assertFalse(root.exists(), msg="scaffold root should be removed on cleanup")
        # Idempotent: a second cleanup is a no-op.
        scaffold.cleanup()

    def test_context_manager_cleans_up_on_exit(self) -> None:
        with project_scaffold() as scaffold:
            root = scaffold.root
            self.assertTrue(root.is_dir())
        self.assertFalse(root.exists())

    def test_extra_dirs_are_created(self) -> None:
        with project_scaffold(extra_dirs=("docs", "specs/archive")) as scaffold:
            self.assertTrue((scaffold.project_root / "docs").is_dir())
            self.assertTrue((scaffold.project_root / "specs" / "archive").is_dir())

    def test_custom_project_name_drives_layout_root(self) -> None:
        with project_scaffold(project_name="walkthrough-fixture") as scaffold:
            self.assertEqual(scaffold.project_root.name, "walkthrough-fixture")
            self.assertEqual(scaffold.project_root.parent, scaffold.root)

    def test_does_not_depend_on_committed_sample_project(self) -> None:
        # A minimal sample-skill walkthrough: build a scaffold, write the
        # files a skill would expect, exercise them. The scaffold lives
        # entirely in a temp directory, so no committed fixture project
        # (e.g. projects/sample-project/) is required for this acceptance.
        with project_scaffold() as scaffold:
            scaffold.write(
                "tasks/open/TASK-01-001-demo.md",
                "# TASK-01-001\n\nEvidence gate: n/a\n",
            )
            scaffold.write(
                "prompts/PROMPT-01-001.md",
                "# PROMPT-01-001\n",
            )
            scaffold.write(
                "phases/PHASE-01-demo.md",
                "# PHASE-01: demo\n",
            )
            # Verify the skill-visible surface.
            self.assertTrue((scaffold.tasks_open / "TASK-01-001-demo.md").is_file())
            self.assertTrue((scaffold.prompts / "PROMPT-01-001.md").is_file())
            self.assertEqual(
                sorted(p.name for p in scaffold.phases.iterdir()),
                ["PHASE-01-demo.md"],
            )

    def test_scaffold_path_is_outside_repo(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        with project_scaffold() as scaffold:
            self.assertFalse(
                str(scaffold.root).startswith(str(repo_root)),
                msg="scaffold should live outside the repo to avoid polluting committed state",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
