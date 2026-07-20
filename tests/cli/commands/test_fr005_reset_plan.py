"""Unit tests for `cartopian reset-plan`.

The destructive close-surface reset: removes live plan artifacts, recreates
the empty lifecycle directories, and conditionally reseeds STANDARDS.md per
the carry-forward flag — all behind fail-closed allowlist
guards (a symlink / foreign subdir / non-project target → refuse, remove
nothing).
"""
import os
import unittest
from pathlib import Path

from tests.cli.commands.test_fr005_structured_writers import run_cli, _TOML
from tests.scaffold import project_scaffold


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.scaffold = project_scaffold(cartopian_toml=_TOML)
        self.addCleanup(self.scaffold.cleanup)
        self.root = str(self.scaffold.project_root)
        # Seed a full live plan surface.
        self.scaffold.write("REQUIREMENTS.md", "# reqs\n")
        self.scaffold.write("IMPLEMENTATION_PLAN.md", "# plan\n")
        self.scaffold.write("phases/PHASE-01-x.md", "# phase\n")
        self.scaffold.write("tasks/open/TASK-01-001-a.md", "# t\n")
        self.scaffold.write("tasks/done/TASK-01-002-b.md", "# t\n")
        self.scaffold.write("specs/SPEC-01-001-s.md", "# s\n")
        self.scaffold.write("reviews/REVIEW-01-001.md", "# r\n")
        self.scaffold.write("decisions/DEC-001-d.md", "# d\n")
        self.scaffold.write("decisions/INDEX.md", "# Decisions Index\n")
        self.scaffold.write("prompts/PROMPT-01-001.md", "# p\n")
        self.scaffold.write("reports/REPORT-01-001.md", "# rep\n")
        self.scaffold.write("STANDARDS.md", "CARRIED STANDARDS\n")


class TestResetRemovesAndRecreates(_Fixture):
    def test_full_reset(self):
        code, recs, err = run_cli("reset-plan", self.root)
        self.assertEqual(code, 0, msg=err)
        pr = self.scaffold.project_root
        # G13 — live artifacts gone.
        for gone in (
            "REQUIREMENTS.md", "IMPLEMENTATION_PLAN.md", "phases/PHASE-01-x.md",
            "tasks/open/TASK-01-001-a.md", "tasks/done/TASK-01-002-b.md",
            "specs/SPEC-01-001-s.md", "reviews/REVIEW-01-001.md",
            "decisions/DEC-001-d.md", "decisions/INDEX.md",
        ):
            self.assertFalse((pr / gone).exists(), msg=f"{gone} should be removed")
        # G14 — empty lifecycle dirs recreated.
        for d in ("phases", "prompts", "reports", "tasks/open", "tasks/in-progress",
                  "tasks/in-review", "tasks/done", "specs", "reviews", "decisions"):
            self.assertTrue((pr / d).is_dir(), msg=f"{d} should exist")
        # Prompts/reports preserved (cleared via delete-prompt/delete-report, not here).
        self.assertTrue((pr / "prompts" / "PROMPT-01-001.md").is_file())
        self.assertTrue((pr / "reports" / "REPORT-01-001.md").is_file())
        # cartopian.toml + STATE.md preserved.
        self.assertTrue((pr / "cartopian.toml").is_file())
        self.assertTrue((pr / "STATE.md").is_file())

    def test_reseeds_standards_by_default(self):
        code, recs, err = run_cli("reset-plan", self.root)
        self.assertEqual(code, 0, msg=err)
        std = (self.scaffold.project_root / "STANDARDS.md").read_text(encoding="utf-8")
        self.assertNotIn("CARRIED", std)
        self.assertEqual(recs[0]["details"]["reseeded"], ["STANDARDS.md"])

    def test_carry_forward_keeps_files(self):
        code, recs, err = run_cli(
            "reset-plan", self.root, "--carry-standards",
        )
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(
            (self.scaffold.project_root / "STANDARDS.md").read_text(encoding="utf-8"),
            "CARRIED STANDARDS\n",
        )
        self.assertEqual(recs[0]["details"]["reseeded"], [])


class TestResetFailClosed(_Fixture):
    def test_non_project_dir_refused(self):
        other = Path(self.scaffold.root) / "not-a-project"
        other.mkdir()
        code, recs, err = run_cli("reset-plan", str(other))
        self.assertEqual(code, 1)
        self.assertIn("no cartopian.toml", err)

    def test_symlinked_reset_dir_refused_and_nothing_removed(self):
        # Replace decisions/ with a symlink to an outside dir; the scan must
        # refuse before any unlink, leaving the live surface intact.
        outside = Path(self.scaffold.root) / "outside"
        outside.mkdir()
        (outside / "evil.md").write_text("KEEP", encoding="utf-8")
        decisions = self.scaffold.project_root / "decisions"
        for child in decisions.iterdir():
            child.unlink()
        decisions.rmdir()
        os.symlink(outside, decisions)

        code, recs, err = run_cli("reset-plan", self.root)
        self.assertEqual(code, 1)
        self.assertIn("[guard]", err)
        # Fail-closed: nothing removed — REQUIREMENTS.md still present.
        self.assertTrue((self.scaffold.project_root / "REQUIREMENTS.md").is_file())
        self.assertEqual((outside / "evil.md").read_text(encoding="utf-8"), "KEEP")

    def test_foreign_subdir_in_reset_dir_refused(self):
        (self.scaffold.specs / "nested").mkdir()
        code, recs, err = run_cli("reset-plan", self.root)
        self.assertEqual(code, 1)
        self.assertIn("foreign-subdir", err)
        self.assertTrue((self.scaffold.project_root / "REQUIREMENTS.md").is_file())

    def test_symlinked_reseed_dest_refused_and_nothing_removed(self):
        # A guarded reseed destination must be caught in preflight, before any
        # destructive work — otherwise reset-plan partial-resets (removes live
        # artifacts) and only then refuses the reseed. STANDARDS.md is a symlink
        # to an outside file; with no carry-forward a reseed is attempted, which
        # the mediated-write primitive would refuse (final-component symlink).
        # The full plan must preflight that refusal so nothing is removed.
        outside = Path(self.scaffold.root) / "outside-standards.md"
        outside.write_text("OUTSIDE", encoding="utf-8")
        standards = self.scaffold.project_root / "STANDARDS.md"
        standards.unlink()
        os.symlink(outside, standards)

        code, recs, err = run_cli("reset-plan", self.root)
        self.assertEqual(code, 1)
        self.assertIn("[guard]", err)

        # Fail-closed preflight: every live artifact remains intact.
        pr = self.scaffold.project_root
        for live in (
            "REQUIREMENTS.md", "IMPLEMENTATION_PLAN.md", "phases/PHASE-01-x.md",
            "tasks/open/TASK-01-001-a.md", "tasks/done/TASK-01-002-b.md",
            "specs/SPEC-01-001-s.md", "reviews/REVIEW-01-001.md",
            "decisions/DEC-001-d.md", "decisions/INDEX.md",
        ):
            self.assertTrue((pr / live).exists(), msg=f"{live} must remain intact")
        # The symlink and its outside target are untouched; no reseed landed.
        self.assertTrue(standards.is_symlink())
        self.assertEqual(outside.read_text(encoding="utf-8"), "OUTSIDE")
        self.assertEqual(recs, [])

    def test_carry_forward_skips_guarded_reseed_dest(self):
        # With carry-forward, no reseed is attempted, so a symlinked STANDARDS.md
        # is not a reseed target and the reset proceeds normally.
        outside = Path(self.scaffold.root) / "outside-standards.md"
        outside.write_text("OUTSIDE", encoding="utf-8")
        standards = self.scaffold.project_root / "STANDARDS.md"
        standards.unlink()
        os.symlink(outside, standards)

        code, recs, err = run_cli(
            "reset-plan", self.root, "--carry-standards",
        )
        self.assertEqual(code, 0, msg=err)
        self.assertFalse((self.scaffold.project_root / "REQUIREMENTS.md").exists())
        self.assertEqual(recs[0]["details"]["reseeded"], [])


if __name__ == "__main__":
    unittest.main()
