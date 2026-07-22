"""Project-level conventions retirement — static surface checks.

The tool-owned ``protocol/CONVENTIONS.md`` is the only conventions layer.
No shipped surface creates, preserves, archives, or writes a project-level
``CONVENTIONS.md``; the topmost CHANGELOG entry migrates existing projects
off the file; and every shipped description of ``STANDARDS.md`` treats it
as project metadata (tools or stack, working standards, cycle constraints),
not as a governance contract.
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = REPO_ROOT / "protocol" / "CHANGELOG.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class ScaffoldSurfaceTest(unittest.TestCase):
    def test_scaffold_never_seeds_project_conventions(self) -> None:
        from cli.commands import scaffold_project

        for group in (
            scaffold_project.ALLOWED_TOP_FILES,
            scaffold_project.REQUIRED_FILES,
            tuple(scaffold_project.SEED_CONTENTS),
        ):
            self.assertNotIn("CONVENTIONS.md", group)


class ResetAndCloseoutSurfaceTest(unittest.TestCase):
    def test_reset_plan_has_no_conventions_target_or_seed(self) -> None:
        import inspect

        from cli.commands import reset_plan

        self.assertNotIn("CONVENTIONS.md", inspect.getsource(reset_plan))

    def test_archive_allowlist_excludes_project_conventions(self) -> None:
        from cli.commands import archive_plan

        self.assertNotIn("CONVENTIONS.md", archive_plan.ARCHIVE_ROOT_FILES)


class MediatedWriterSurfaceTest(unittest.TestCase):
    def test_no_conventions_dest_kind(self) -> None:
        from cli import mediated_write

        self.assertNotIn("conventions", mediated_write.DEST_KINDS)
        self.assertNotIn("CONVENTIONS.md", mediated_write.ROOT_FILES.values())

    def test_no_write_conventions_subcommand(self) -> None:
        from cli import main

        self.assertNotIn("write-conventions", main.SUBCOMMANDS)
        self.assertIn("write-standards", main.SUBCOMMANDS)

    def test_governed_artifact_set_excludes_project_conventions(self) -> None:
        from cli import provenance

        self.assertNotIn("CONVENTIONS.md", provenance.GOVERNED_ROOT_FILES)


class MigrationEntryTest(unittest.TestCase):
    """The newest CHANGELOG entry retires the project-level file."""

    def _topmost_entry(self) -> str:
        text = _read(CHANGELOG)
        _, _, body = text.partition("\n## Entries\n")
        headings = list(
            re.finditer(r"^###\s+(v\d+\.\d+\.\d+)\b", body, flags=re.MULTILINE)
        )
        self.assertGreaterEqual(len(headings), 2)
        return body[headings[0].start() : headings[1].start()]

    def test_topmost_entry_retires_project_conventions(self) -> None:
        entry = self._topmost_entry()
        self.assertIn("v0.6.0", entry)
        # The migration ends with no project-level CONVENTIONS.md on disk.
        self.assertIn('test ! -e "$PROJECT_ROOT/CONVENTIONS.md"', entry)
        self.assertIn("superseded", entry)
        # STANDARDS.md is finalized as metadata, never a governance carrier.
        self.assertIn("project metadata", entry)

    def test_shipped_version_gate_follows_topmost_entry(self) -> None:
        from cli.protocol_gate import read_shipped_protocol_version

        self.assertEqual(read_shipped_protocol_version(), "v0.6.0")


class StandardsMetadataWordingTest(unittest.TestCase):
    """Shipped STANDARDS seeds describe metadata, not governance."""

    def test_seed_texts_use_metadata_wording(self) -> None:
        from cli.commands.reset_plan import STANDARDS_SEED

        template = _read(REPO_ROOT / "templates" / "STANDARDS.md")
        for label, text in (("template", template), ("reset seed", STANDARDS_SEED)):
            self.assertIn("metadata", text, msg=label)
            self.assertNotIn("govern execution", text, msg=label)

    def test_no_project_conventions_template_ships(self) -> None:
        self.assertFalse((REPO_ROOT / "templates" / "CONVENTIONS.md").exists())


if __name__ == "__main__":
    unittest.main()
