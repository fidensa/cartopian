"""Tests for `cartopian delete-prompt` (SPEC-01-001, FR-005, FR-014)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"


def _run(*cli_args, home):
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
    }
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), "delete-prompt", *cli_args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _seed_registered_project(home: Path, root: Path) -> Path:
    project = root / "proj"
    (project / "prompts").mkdir(parents=True, exist_ok=True)
    registry = home / ".cartopian" / "projects.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps([{"id": "demo", "path": str(project), "label": "Demo"}]),
        encoding="utf-8",
    )
    return project


class TestDeletePromptHappyPath(unittest.TestCase):
    def test_deletes_prompt_and_emits_confirmation_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            project = _seed_registered_project(home, tmp_path)
            prompt = project / "prompts" / "PROMPT-01-001.md"
            prompt.write_text("# prompt\n", encoding="utf-8")

            proc = _run(str(prompt), home=home)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(proc.stderr, "")
            line = proc.stdout.strip()
            self.assertTrue(line)
            self.assertEqual(line.count("\n"), 0)
            rec = json.loads(line)
            self.assertEqual(rec["action"], "delete-prompt")
            self.assertEqual(rec["details"]["deleted_path"], str(prompt))
            self.assertFalse(prompt.exists())

    def test_planning_variant_filename_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            project = _seed_registered_project(home, tmp_path)
            prompt = project / "prompts" / "PROMPT-PLAN-005.md"
            prompt.write_text("# prompt\n", encoding="utf-8")

            proc = _run(str(prompt), home=home)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            rec = json.loads(proc.stdout.strip())
            self.assertEqual(rec["action"], "delete-prompt")

    def test_planning_variant_with_slug_suffix_accepted(self):
        # CONVENTIONS.md names planning-checkpoint prompts
        # PROMPT-PLAN-NNN-slug.md; the deleter must accept that canonical form.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            project = _seed_registered_project(home, tmp_path)
            prompt = (
                project / "prompts" / "PROMPT-PLAN-005-review-architecture.md"
            )
            prompt.write_text("# prompt\n", encoding="utf-8")

            proc = _run(str(prompt), home=home)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            rec = json.loads(proc.stdout.strip())
            self.assertEqual(rec["action"], "delete-prompt")
            self.assertEqual(rec["details"]["deleted_path"], str(prompt))
            self.assertFalse(prompt.exists())


class TestDeletePromptGuards(unittest.TestCase):
    def test_path_outside_registered_project_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            # Empty registry
            (home / ".cartopian").mkdir()
            (home / ".cartopian" / "projects.json").write_text("[]", encoding="utf-8")
            stray = tmp_path / "stray"
            stray.mkdir()
            prompt = stray / "PROMPT-01-001.md"
            prompt.write_text("# prompt\n", encoding="utf-8")

            proc = _run(str(prompt), home=home)
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
            # File must still exist
            self.assertTrue(prompt.is_file())

    def test_bad_filename_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            project = _seed_registered_project(home, tmp_path)
            prompt = project / "prompts" / "NOT-A-PROMPT.md"
            prompt.write_text("# prompt\n", encoding="utf-8")

            proc = _run(str(prompt), home=home)
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
            self.assertTrue(prompt.is_file())

    def test_missing_prompt_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            project = _seed_registered_project(home, tmp_path)
            prompt = project / "prompts" / "PROMPT-01-001.md"
            # do not create the file

            proc = _run(str(prompt), home=home)
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)

    def test_relative_path_rejected_with_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            proc = _run("prompts/PROMPT-01-001.md", home=home)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)
            self.assertIn("absolute path", proc.stderr)

    def test_outside_symlink_resolving_into_registered_project_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            project = _seed_registered_project(home, tmp_path)
            in_project = project / "prompts" / "PROMPT-88-010.md"
            in_project.write_text("# in-project prompt\n", encoding="utf-8")
            outside = tmp_path / "outside"
            outside.mkdir()
            symlink = outside / "PROMPT-88-010.md"
            symlink.symlink_to(in_project)

            proc = _run(str(symlink), home=home)
            self.assertEqual(proc.returncode, 1, msg=proc.stderr)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
            self.assertTrue(
                in_project.is_file(),
                "in-project prompt must not be unlinked via outside symlink",
            )
            self.assertTrue(
                symlink.is_symlink(),
                "outside symlink must remain (it is not the target of a "
                "validated delete)",
            )

    def test_inside_symlink_to_outside_target_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            project = _seed_registered_project(home, tmp_path)
            outside_target = tmp_path / "stray-PROMPT-88-011.md"
            outside_target.write_text("# stray\n", encoding="utf-8")
            inside_symlink = project / "prompts" / "PROMPT-88-011.md"
            inside_symlink.symlink_to(outside_target)

            proc = _run(str(inside_symlink), home=home)
            self.assertEqual(proc.returncode, 1, msg=proc.stderr)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
            self.assertTrue(
                inside_symlink.is_symlink(),
                "in-project symlink must remain — it is not a real prompt",
            )
            self.assertTrue(
                outside_target.exists(),
                "outside target must remain — never reachable through "
                "delete-prompt",
            )


if __name__ == "__main__":
    unittest.main()
