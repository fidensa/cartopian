"""v0.10 identifier-only artifact naming regression coverage."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cli import config_migration, migrations
from cli.atomic_write import GuardRefusal
from cli.commands import next_action


class CanonicalArtifactNameMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "cartopian.toml").write_text(
            "[project]\n"
            'id = "demo"\n'
            'name = "Demo"\n'
            'project_schema_version = "v0.9.0"\n',
            encoding="utf-8",
        )
        for rel in (
            "phases",
            "specs",
            "decisions",
            "prompts",
            "reports",
            "reviews",
            "tasks/open",
            "tasks/in-progress",
            "tasks/in-review",
            "tasks/done",
            "archive",
        ):
            (self.root / rel).mkdir(parents=True, exist_ok=True)

    def test_migration_renames_live_artifacts_and_rewrites_references(self) -> None:
        phase = self.root / "phases/PHASE-04-risk-aware.md"
        task = self.root / "tasks/open/TASK-04-002-source-guidance.md"
        spec = self.root / "specs/SPEC-04-002-source-guidance.md"
        decision = self.root / "decisions/DEC-043-canonical-names.md"
        review = self.root / "reviews/REVIEW-PLAN-015-canonical-names.md"
        phase.write_text(
            "# PHASE-04-risk-aware: Risk aware\n\n"
            "- BUILD-04-001\n",
            encoding="utf-8",
        )
        task.write_text(
            "# TASK-04-002: Source guidance\n\n"
            "Phase: PHASE-04-risk-aware\n"
            "Plan ref: BUILD-04-001\n\n"
            "See `specs/SPEC-04-002-source-guidance.md`.\n",
            encoding="utf-8",
        )
        spec.write_text("# SPEC-04-002: Source guidance\n", encoding="utf-8")
        decision.write_text("# DEC-043: Canonical names\n", encoding="utf-8")
        review.write_text(
            "# REVIEW-PLAN-015-canonical-names\n\n"
            "Target: PLAN-015-canonical-names\n",
            encoding="utf-8",
        )
        (self.root / "STATE.md").write_text(
            "PHASE-04-risk-aware (`phases/PHASE-04-risk-aware.md`)\n"
            "TASK-04-002-source-guidance.md\n",
            encoding="utf-8",
        )

        plan = migrations.plan_entry(self.root, "v0.10.0")
        operations = migrations.apply_plan(self.root, "v0.10.0", plan)
        self.assertTrue(any(item["status"] == "applied" for item in operations))

        self.assertFalse(phase.exists())
        self.assertFalse(task.exists())
        self.assertTrue((self.root / "phases/PHASE-04.md").is_file())
        canonical_task = self.root / "tasks/open/TASK-04-002.md"
        self.assertTrue(canonical_task.is_file())
        self.assertTrue((self.root / "specs/SPEC-04-002.md").is_file())
        self.assertTrue((self.root / "decisions/DEC-043.md").is_file())
        self.assertTrue((self.root / "reviews/REVIEW-PLAN-015.md").is_file())
        self.assertIn("Phase: PHASE-04", canonical_task.read_text(encoding="utf-8"))
        self.assertIn(
            "specs/SPEC-04-002.md", canonical_task.read_text(encoding="utf-8")
        )
        self.assertIn(
            "Target: PLAN-015",
            (self.root / "reviews/REVIEW-PLAN-015.md").read_text(encoding="utf-8"),
        )
        state = (self.root / "STATE.md").read_text(encoding="utf-8")
        self.assertIn("PHASE-04 (`phases/PHASE-04.md`)", state)
        self.assertIn("TASK-04-002.md", state)

        rerun = migrations.plan_entry(self.root, "v0.10.0")
        self.assertFalse(rerun.writes)
        self.assertFalse(rerun.deletes)
        self.assertEqual(rerun.skipped[0]["status"], "skipped")

    def test_migration_refuses_canonical_destination_collision(self) -> None:
        (self.root / "phases/PHASE-04-old.md").write_text("old\n", encoding="utf-8")
        (self.root / "phases/PHASE-04.md").write_text("new\n", encoding="utf-8")
        with self.assertRaises(GuardRefusal) as raised:
            migrations.plan_entry(self.root, "v0.10.0")
        self.assertEqual(raised.exception.rule, "artifact-name-collision")

    def test_config_marker_cannot_advance_before_name_migration(self) -> None:
        (self.root / "phases/PHASE-04-old.md").write_text(
            "# PHASE-04-old\n", encoding="utf-8"
        )

        plan = config_migration.plan_configuration_migration(
            self.root, home_root=self.root / "home"
        )

        self.assertEqual(plan.status, "refused")
        self.assertEqual(plan.diagnostics[0]["code"], "filesystem-migration-required")
        self.assertEqual(plan.detected_schema_version, "v0.9.0")

    def test_current_marker_partial_repair_still_runs_name_migration(self) -> None:
        config = self.root / "cartopian.toml"
        config.write_text(
            config.read_text(encoding="utf-8").replace("v0.9.0", "v0.10.0"),
            encoding="utf-8",
        )
        old = self.root / "phases/PHASE-04-old.md"
        old.write_text("# PHASE-04-old\n", encoding="utf-8")

        plan = migrations.plan_entry(self.root, "v0.10.0")
        migrations.apply_plan(self.root, "v0.10.0", plan)

        self.assertFalse(old.exists())
        self.assertTrue((self.root / "phases/PHASE-04.md").is_file())

    def test_migration_renames_plan_archive_and_rewrites_index(self) -> None:
        old = self.root / "archive/PLAN-001-first-plan"
        old.mkdir()
        (old / "CLOSEOUT.md").write_text("# Closeout\n", encoding="utf-8")
        index = self.root / "archive/INDEX.md"
        index.write_text("| `PLAN-001-first-plan` | closed |\n", encoding="utf-8")

        plan = migrations.plan_entry(self.root, "v0.10.0")
        migrations.apply_plan(self.root, "v0.10.0", plan)

        self.assertFalse(old.exists())
        self.assertTrue((self.root / "archive/PLAN-001/CLOSEOUT.md").is_file())
        self.assertIn("`PLAN-001`", index.read_text(encoding="utf-8"))

    def test_migration_refuses_multiple_descriptive_plan_archives(self) -> None:
        (self.root / "archive/PLAN-001-first").mkdir()
        (self.root / "archive/PLAN-001-second").mkdir()

        with self.assertRaises(GuardRefusal) as raised:
            migrations.plan_entry(self.root, "v0.10.0")

        self.assertEqual(raised.exception.rule, "artifact-name-collision")


class CanonicalReaderTests(unittest.TestCase):
    def test_next_action_recognizes_identifier_only_phase_and_task(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "phases").mkdir()
            (root / "tasks/open").mkdir(parents=True)
            (root / "phases/PHASE-04.md").write_text("# PHASE-04: Build\n", encoding="utf-8")
            (root / "tasks/open/TASK-04-001.md").write_text(
                "# TASK-04-001: Build\n\nPhase: PHASE-04\n",
                encoding="utf-8",
            )
            self.assertEqual(next_action._find_phase_id(root), "PHASE-04")

    def test_next_action_does_not_read_descriptive_phase_or_task_names(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "phases").mkdir()
            (root / "tasks/open").mkdir(parents=True)
            (root / "phases/PHASE-04-old.md").write_text("# Phase\n", encoding="utf-8")
            (root / "tasks/open/TASK-04-001-old.md").write_text(
                "Phase: PHASE-04-old\n", encoding="utf-8"
            )
            self.assertIsNone(next_action._find_phase_id(root))


if __name__ == "__main__":
    unittest.main()
