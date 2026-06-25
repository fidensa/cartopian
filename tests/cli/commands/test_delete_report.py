"""Tests for `cartopian delete-report`."""
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
        [sys.executable, str(ENTRYPOINT), "delete-report", *cli_args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _seed_registered_project(home: Path, root: Path) -> Path:
    project = root / "proj"
    (project / "reports").mkdir(parents=True, exist_ok=True)
    registry = home / ".cartopian" / "projects.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps([{"id": "demo", "path": str(project), "label": "Demo"}]),
        encoding="utf-8",
    )
    return project


class TestDeleteReportHappyPath(unittest.TestCase):
    def test_deletes_report_and_emits_confirmation_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            project = _seed_registered_project(home, tmp_path)
            report = project / "reports" / "REPORT-01-001.md"
            report.write_text("# report\n", encoding="utf-8")

            proc = _run(str(report), home=home)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(proc.stderr, "")
            line = proc.stdout.strip()
            self.assertTrue(line)
            self.assertEqual(line.count("\n"), 0)
            rec = json.loads(line)
            self.assertEqual(rec["action"], "delete-report")
            self.assertEqual(rec["details"]["deleted_path"], str(report))
            self.assertFalse(report.exists())

    def test_planning_variant_filename_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            project = _seed_registered_project(home, tmp_path)
            report = project / "reports" / "REPORT-PLAN-005.md"
            report.write_text("# report\n", encoding="utf-8")

            proc = _run(str(report), home=home)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            rec = json.loads(proc.stdout.strip())
            self.assertEqual(rec["action"], "delete-report")

    def test_planning_variant_with_slug_suffix_accepted(self):
        # CONVENTIONS.md names planning-checkpoint reports
        # REPORT-PLAN-NNN-slug.md; the deleter must accept that canonical form.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            project = _seed_registered_project(home, tmp_path)
            report = (
                project / "reports" / "REPORT-PLAN-005-review-architecture.md"
            )
            report.write_text("# report\n", encoding="utf-8")

            proc = _run(str(report), home=home)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            rec = json.loads(proc.stdout.strip())
            self.assertEqual(rec["action"], "delete-report")
            self.assertEqual(rec["details"]["deleted_path"], str(report))
            self.assertFalse(report.exists())


class TestDeleteReportGuards(unittest.TestCase):
    def test_path_outside_registered_project_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            (home / ".cartopian").mkdir()
            (home / ".cartopian" / "projects.json").write_text("[]", encoding="utf-8")
            stray = tmp_path / "stray"
            stray.mkdir()
            report = stray / "REPORT-01-001.md"
            report.write_text("# report\n", encoding="utf-8")

            proc = _run(str(report), home=home)
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
            self.assertTrue(report.is_file())

    def test_bad_filename_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            project = _seed_registered_project(home, tmp_path)
            report = project / "reports" / "NOT-A-REPORT.md"
            report.write_text("# report\n", encoding="utf-8")

            proc = _run(str(report), home=home)
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
            self.assertTrue(report.is_file())

    def test_missing_report_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            project = _seed_registered_project(home, tmp_path)
            report = project / "reports" / "REPORT-01-001.md"
            # do not create the file

            proc = _run(str(report), home=home)
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)

    def test_relative_path_rejected_with_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            proc = _run("reports/REPORT-01-001.md", home=home)
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
            in_project = project / "reports" / "REPORT-88-010.md"
            in_project.write_text("# in-project report\n", encoding="utf-8")
            outside = tmp_path / "outside"
            outside.mkdir()
            symlink = outside / "REPORT-88-010.md"
            symlink.symlink_to(in_project)

            proc = _run(str(symlink), home=home)
            self.assertEqual(proc.returncode, 1, msg=proc.stderr)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
            self.assertTrue(
                in_project.is_file(),
                "in-project report must not be unlinked via outside symlink",
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
            outside_target = tmp_path / "stray-REPORT-88-011.md"
            outside_target.write_text("# stray\n", encoding="utf-8")
            inside_symlink = project / "reports" / "REPORT-88-011.md"
            inside_symlink.symlink_to(outside_target)

            proc = _run(str(inside_symlink), home=home)
            self.assertEqual(proc.returncode, 1, msg=proc.stderr)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
            self.assertTrue(
                inside_symlink.is_symlink(),
                "in-project symlink must remain — it is not a real report",
            )
            self.assertTrue(
                outside_target.exists(),
                "outside target must remain — never reachable through "
                "delete-report",
            )


if __name__ == "__main__":
    unittest.main()
