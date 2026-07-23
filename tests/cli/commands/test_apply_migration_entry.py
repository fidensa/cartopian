"""Tests for the contained ``apply-migration-entry`` CLI surface."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cli.atomic_write import GuardRefusal
from cli import migrations
from cli.mediated_write import mediated_write


REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"

PLACEHOLDER = (
    "# Demo - Conventions\n\n"
    "This document extends the protocol-level conventions defined in `protocol/CONVENTIONS.md`. "
    "Rules here apply only to this project.\n\n"
    "## Project-specific conventions\n\n"
    "<!-- Add project-specific naming rules, workflow modifications, or\n"
    "     constraints here. Delete this comment when you add real content. -->\n"
)


def _seed(
    tmp: Path, *, registered: bool = True, marker: str = "v0.5.0"
) -> tuple[Path, Path]:
    home = tmp / "home"
    project = tmp / "project"
    (home / ".cartopian").mkdir(parents=True)
    project.mkdir()
    (project / "cartopian.toml").write_text(
        "[project]\n"
        'id = "demo"\n'
        'name = "Demo"\n'
        f'protocol_version = "{marker}"\n',
        encoding="utf-8",
    )
    registry = [{"id": "demo", "path": str(project), "label": "Demo"}] if registered else []
    (home / ".cartopian" / "projects.json").write_text(json.dumps(registry), encoding="utf-8")
    return home, project


def _run(home: Path, project: Path, version: str = "v0.6.0"):
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "")}
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), "apply-migration-entry", str(project), version],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _run_update(home: Path, project: Path, version: str):
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "")}
    return subprocess.run(
        [
            sys.executable,
            str(ENTRYPOINT),
            "update-config",
            str(project),
            "--set",
            f"project.protocol_version={version}",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _run_standards(home: Path, project: Path, content: str):
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "")}
    return subprocess.run(
        [
            sys.executable,
            str(ENTRYPOINT),
            "write-standards",
            str(project),
            "--content",
            content,
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


class TestV060Retirement(unittest.TestCase):
    def test_placeholder_is_retired_with_provenance_and_rerun_is_noop(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw))
            target = project / "CONVENTIONS.md"
            target.write_text(PLACEHOLDER, encoding="utf-8")

            result = _run(home, project)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertFalse(target.exists())
            record = json.loads(result.stdout)
            self.assertEqual(record["details"]["status"], "complete")
            self.assertEqual(record["details"]["operations"][0]["kind"], "retire")
            self.assertEqual(record["details"]["operations"][0]["target"], "CONVENTIONS.md")
            self.assertEqual(record["details"]["validation"]["status"], "passed")
            log = (project / ".cartopian" / "provenance.log").read_text(encoding="utf-8")
            tombstone = json.loads(log.splitlines()[-1])
            self.assertEqual(tombstone["relpath"], "CONVENTIONS.md")
            self.assertEqual(tombstone["hash"], "deleted")
            self.assertEqual(
                tombstone["action"], "migration-entry:v0.6.0:retire"
            )

            again = _run(home, project)
            self.assertEqual(again.returncode, 0, msg=again.stderr)
            op = json.loads(again.stdout)["details"]["operations"][0]
            self.assertEqual(op["status"], "skipped")

    def test_substantive_file_returns_pending_without_mutation(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw))
            target = project / "CONVENTIONS.md"
            target.write_text("# Local rules\n\nKeep this metadata.\n", encoding="utf-8")
            result = _run(home, project)
            self.assertEqual(result.returncode, 1)
            self.assertTrue(result.stderr.startswith("[guard]"))
            record = json.loads(result.stdout)
            self.assertEqual(record["details"]["status"], "pending")
            self.assertEqual(record["details"]["pending_actions"][0]["kind"], "salvage-conventions")
            self.assertTrue(target.exists())

    def test_substantive_file_retires_after_hash_pinned_mediated_salvage(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw))
            target = project / "CONVENTIONS.md"
            original = "# Local rules\n\nTool stack: Python.\n"
            target.write_text(original, encoding="utf-8")
            pending = _run(home, project)
            self.assertEqual(pending.returncode, 1)
            salvaged = _run_standards(
                home, project, "# Standards\n\nTool stack: Python.\n"
            )
            self.assertEqual(salvaged.returncode, 0, msg=salvaged.stderr)
            completed = _run(home, project)
            self.assertEqual(completed.returncode, 0, msg=completed.stderr)
            self.assertFalse(target.exists())
            operation = json.loads(completed.stdout)["details"]["operations"][0]
            self.assertEqual(operation["status"], "applied")

    def test_raw_salvage_or_changed_source_does_not_satisfy_pending_receipt(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw))
            target = project / "CONVENTIONS.md"
            target.write_text("# Local rules\n\nKeep this.\n", encoding="utf-8")
            first = _run(home, project)
            self.assertEqual(first.returncode, 1)
            (project / "STANDARDS.md").write_text("# Raw edit\n", encoding="utf-8")
            second = _run(home, project)
            self.assertEqual(second.returncode, 1)
            self.assertTrue(target.exists())
            mediated = _run_standards(home, project, "# Standards\n\nKeep this.\n")
            self.assertEqual(mediated.returncode, 0, msg=mediated.stderr)
            target.write_text("# Local rules\n\nChanged after review.\n", encoding="utf-8")
            changed = _run(home, project)
            self.assertEqual(changed.returncode, 1)
            self.assertTrue(target.exists())

    def test_marker_already_at_entry_is_noop_without_inspecting_retired_file(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), marker="v0.6.0")
            target = project / "CONVENTIONS.md"
            target.write_text("# Substantive historical content\n", encoding="utf-8")
            result = _run(home, project)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            operation = json.loads(result.stdout)["details"]["operations"][0]
            self.assertEqual(operation["kind"], "entry")
            self.assertEqual(operation["status"], "skipped")
            self.assertEqual(target.read_text(), "# Substantive historical content\n")

    def test_v050_to_v060_uses_migration_then_validated_marker_update(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), marker="v0.5.0")
            (project / "CONVENTIONS.md").write_text(PLACEHOLDER, encoding="utf-8")
            unrelated = project / "unrelated.txt"
            unrelated.write_text("unchanged\n", encoding="utf-8")
            applied = _run(home, project, "v0.6.0")
            self.assertEqual(applied.returncode, 0, msg=applied.stderr)
            bumped = _run_update(home, project, "v0.6.0")
            self.assertEqual(bumped.returncode, 0, msg=bumped.stderr)
            self.assertIn('protocol_version = "v0.6.0"', (project / "cartopian.toml").read_text())
            self.assertFalse((project / "CONVENTIONS.md").exists())
            self.assertEqual(unrelated.read_text(), "unchanged\n")


class TestContainment(unittest.TestCase):
    def test_unregistered_and_unknown_entries_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), registered=False)
            (project / "CONVENTIONS.md").write_text(PLACEHOLDER, encoding="utf-8")
            result = _run(home, project)
            self.assertEqual(result.returncode, 1)
            self.assertIn("unregistered-project", result.stderr)
            self.assertTrue((project / "CONVENTIONS.md").exists())
            unknown = _run(home, project, "v9.9.9")
            self.assertEqual(unknown.returncode, 1)
            self.assertIn("unknown-entry", unknown.stderr)
            blocked = json.loads(unknown.stdout)["details"]
            self.assertEqual(blocked["status"], "blocked")
            self.assertEqual(blocked["operations"][0]["status"], "blocked")

    def test_hardlink_retirement_is_refused(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), marker="v0.1.0")
            target = project / "CONVENTIONS.md"
            target.write_text(PLACEHOLDER, encoding="utf-8")
            os.link(target, project / "alias.md")
            result = _run(home, project)
            self.assertEqual(result.returncode, 1)
            self.assertIn("hardlink", result.stderr)
            self.assertTrue(target.exists())

    def test_directory_and_symlink_retirements_are_refused(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw))
            target = project / "CONVENTIONS.md"
            target.mkdir()
            directory_result = _run(home, project)
            self.assertEqual(directory_result.returncode, 1)
            self.assertIn("non-regular", directory_result.stderr)
            target.rmdir()
            outside = Path(raw) / "outside.md"
            outside.write_text(PLACEHOLDER, encoding="utf-8")
            target.symlink_to(outside)
            symlink_result = _run(home, project)
            self.assertEqual(symlink_result.returncode, 1)
            self.assertIn("symlink", symlink_result.stderr)
            self.assertEqual(outside.read_text(), PLACEHOLDER)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires FIFO support")
    def test_special_file_retirement_is_refused_without_opening_it(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw))
            target = project / "CONVENTIONS.md"
            os.mkfifo(target)
            result = _run(home, project)
            self.assertEqual(result.returncode, 1)
            self.assertIn("non-regular", result.stderr)
            self.assertTrue(target.exists())

    @unittest.skipUnless(migrations.DIR_FD_SUPPORTED, "requires POSIX dir-fd support")
    def test_concurrent_leaf_swap_cannot_delete_outside_target(self):
        with tempfile.TemporaryDirectory() as raw:
            _, project = _seed(Path(raw), marker="v0.1.0")
            target = project / "CONVENTIONS.md"
            outside = Path(raw) / "outside.md"
            target.write_text(PLACEHOLDER, encoding="utf-8")
            outside.write_text(PLACEHOLDER, encoding="utf-8")
            plan = migrations.plan_entry(project, "v0.6.0")

            def swap():
                target.unlink()
                target.symlink_to(outside)

            previous = migrations._delete_concurrent_swap_hook
            migrations._delete_concurrent_swap_hook = swap
            try:
                with self.assertRaises(GuardRefusal):
                    migrations.apply_plan(project, "v0.6.0", plan)
            finally:
                migrations._delete_concurrent_swap_hook = previous
            self.assertEqual(outside.read_text(encoding="utf-8"), PLACEHOLDER)


class TestOtherRegistryActions(unittest.TestCase):
    def test_v020_rename_and_anchored_substitution_are_idempotent(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), marker="v0.1.0")
            (project / "tasks").mkdir()
            (project / "reviews").mkdir()
            (project / "ENGINEERING.md").write_text("# Standards\n", encoding="utf-8")
            task = project / "tasks" / "TASK-01-001-demo.md"
            task.write_text("Test gate: required\nBody Test gate: unchanged\n", encoding="utf-8")
            first = _run(home, project, "v0.2.0")
            self.assertEqual(first.returncode, 0, msg=first.stderr)
            self.assertFalse((project / "ENGINEERING.md").exists())
            self.assertEqual((project / "STANDARDS.md").read_text(), "# Standards\n")
            self.assertEqual(
                task.read_text(),
                "Evidence gate: required\nBody Test gate: unchanged\n",
            )
            second = _run(home, project, "v0.2.0")
            self.assertEqual(second.returncode, 0, msg=second.stderr)
            self.assertTrue(
                all(
                    operation["status"] == "skipped"
                    for operation in json.loads(second.stdout)["details"]["operations"]
                )
            )

    def test_v020_both_rename_endpoints_block_even_when_bytes_match(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), marker="v0.1.0")
            (project / "ENGINEERING.md").write_text("# Same\n")
            (project / "STANDARDS.md").write_text("# Same\n")
            result = _run(home, project, "v0.2.0")
            self.assertEqual(result.returncode, 1)
            self.assertIn("rename-collision", result.stderr)
            self.assertEqual((project / "ENGINEERING.md").read_text(), "# Same\n")
            self.assertEqual((project / "STANDARDS.md").read_text(), "# Same\n")
            operation = json.loads(result.stdout)["details"]["operations"][0]
            self.assertEqual(operation["status"], "blocked")

    def test_v020_missing_source_requires_a_safe_destination(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), marker="v0.1.0")
            outside = Path(raw) / "outside.md"
            outside.write_text("outside\n")
            (project / "STANDARDS.md").symlink_to(outside)
            result = _run(home, project, "v0.2.0")
            self.assertEqual(result.returncode, 1)
            self.assertIn("symlink", result.stderr)
            self.assertEqual(outside.read_text(), "outside\n")

    def test_v030_ambiguous_value_blocks_all_writes(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw))
            config = project / "cartopian.toml"
            config.write_text(
                config.read_text().replace(
                    'protocol_version = "v0.5.0"\n',
                    'protocol_version = "v0.2.0"\nwork_roots = ["product"]\n',
                )
            )
            (project / "tasks").mkdir()
            safe = project / "tasks" / "TASK-01-001-safe.md"
            unsafe = project / "tasks" / "TASK-01-002-unsafe.md"
            safe.write_text("Repo subpath: n/a\n")
            unsafe.write_text("Repo subpath: team/product\n")
            result = _run(home, project, "v0.3.0")
            self.assertEqual(result.returncode, 1)
            self.assertEqual(safe.read_text(), "Repo subpath: n/a\n")
            self.assertEqual(unsafe.read_text(), "Repo subpath: team/product\n")
            pending = json.loads(result.stdout)["details"]["pending_actions"]
            self.assertEqual(pending[0]["kind"], "work-root-value")

    def test_v030_matching_text_still_requires_semantic_mapping(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), marker="v0.2.0")
            config = project / "cartopian.toml"
            config.write_text(config.read_text() + 'work_roots = ["product"]\n')
            (project / "tasks").mkdir()
            task = project / "tasks" / "TASK-01-001-demo.md"
            task.write_text("Repo subpath: product\n")
            result = _run(home, project, "v0.3.0")
            self.assertEqual(result.returncode, 1)
            self.assertEqual(task.read_text(), "Repo subpath: product\n")
            pending = json.loads(result.stdout)["details"]["pending_actions"]
            self.assertEqual(pending[0]["kind"], "work-root-value")

    def test_registry_cannot_redirect_a_write_to_config_or_outside(self):
        with tempfile.TemporaryDirectory() as raw:
            _, project = _seed(Path(raw), marker="v0.1.0")
            config = project / "cartopian.toml"
            original_config = config.read_bytes()
            config_plan = migrations.MigrationPlan(
                writes=(
                    migrations.PlannedWrite(
                        "anchored-substitution",
                        "standards",
                        "STANDARDS.md",
                        config,
                        original_config,
                        b"changed\n",
                        True,
                        config.stat().st_mode & 0o777,
                    ),
                )
            )
            with self.assertRaises(migrations.MigrationApplyError) as config_error:
                migrations.apply_plan(project, "v0.2.0", config_plan)
            self.assertEqual(config_error.exception.operations[-1]["status"], "blocked")
            self.assertEqual(config.read_bytes(), original_config)

            outside = Path(raw) / "outside.md"
            outside.write_text("outside\n")
            outside_plan = migrations.MigrationPlan(
                writes=(
                    migrations.PlannedWrite(
                        "anchored-substitution",
                        "task",
                        "outside.md",
                        outside,
                        b"outside\n",
                        b"changed\n",
                    ),
                )
            )
            with self.assertRaises(migrations.MigrationApplyError):
                migrations.apply_plan(project, "v0.2.0", outside_plan)
            self.assertEqual(outside.read_text(), "outside\n")

    @unittest.skipUnless(migrations.DIR_FD_SUPPORTED, "requires POSIX dir-fd support")
    def test_concurrent_write_target_swap_is_blocked(self):
        with tempfile.TemporaryDirectory() as raw:
            _, project = _seed(Path(raw), marker="v0.1.0")
            (project / "tasks").mkdir()
            (project / "STANDARDS.md").write_text("# Standards\n")
            task = project / "tasks" / "TASK-01-001-demo.md"
            task.write_text("Test gate: required\n")
            plan = migrations.plan_entry(project, "v0.2.0")

            def swap():
                task.unlink()
                task.write_text("unexpected\n")

            previous = migrations._write_concurrent_swap_hook
            migrations._write_concurrent_swap_hook = swap
            try:
                with self.assertRaises(migrations.MigrationApplyError) as caught:
                    migrations.apply_plan(project, "v0.2.0", plan)
            finally:
                migrations._write_concurrent_swap_hook = previous
            self.assertEqual(caught.exception.operations[-1]["status"], "blocked")
            self.assertEqual(task.read_text(), "unexpected\n")

    def test_unsafe_provenance_path_blocks_before_retirement(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), marker="v0.5.0")
            target = project / "CONVENTIONS.md"
            target.write_text(PLACEHOLDER)
            outside = Path(raw) / "outside-metadata"
            outside.mkdir()
            (project / ".cartopian").symlink_to(outside)
            result = _run(home, project, "v0.6.0")
            self.assertEqual(result.returncode, 1)
            self.assertIn("unsafe-provenance", result.stderr)
            self.assertTrue(target.exists())
            self.assertEqual(list(outside.iterdir()), [])

    def test_duplicate_anchored_header_fails_before_any_write(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), marker="v0.1.0")
            (project / "tasks").mkdir()
            (project / "reviews").mkdir()
            (project / "ENGINEERING.md").write_text("# Standards\n")
            task = project / "tasks" / "TASK-01-001-demo.md"
            task.write_text("Test gate: required\nTest gate: n/a\n")
            result = _run(home, project, "v0.2.0")
            self.assertEqual(result.returncode, 1)
            self.assertIn("ambiguous-anchor", result.stderr)
            self.assertFalse((project / "STANDARDS.md").exists())
            self.assertTrue((project / "ENGINEERING.md").exists())

    def test_registry_declared_wrapper_substitution_preserves_mode(self):
        with tempfile.TemporaryDirectory() as raw:
            _, project = _seed(Path(raw), marker="v0.2.0")
            wrapper = project / "wrappers" / "bin" / "custom-launcher"
            wrapper.parent.mkdir(parents=True)
            wrapper.write_bytes(b"#!/bin/sh\ncd \"$OLD_WORKSPACE_PARENT\"\n")
            wrapper.chmod(0o755)
            declaration = migrations.WrapperSubstitution(
                "bin/custom-launcher",
                b'cd "$OLD_WORKSPACE_PARENT"',
                b'cd "$CARTOPIAN_PROJECT_ROOT"',
            )
            previous = migrations.WRAPPER_SUBSTITUTIONS["v0.3.0"]
            migrations.WRAPPER_SUBSTITUTIONS["v0.3.0"] = (declaration,)
            try:
                plan = migrations.plan_entry(project, "v0.3.0")
                self.assertFalse(plan.pending)
                operations = migrations.apply_plan(project, "v0.3.0", plan)
            finally:
                migrations.WRAPPER_SUBSTITUTIONS["v0.3.0"] = previous
            self.assertEqual(
                wrapper.read_bytes(),
                b"#!/bin/sh\ncd \"$CARTOPIAN_PROJECT_ROOT\"\n",
            )
            self.assertEqual(wrapper.stat().st_mode & 0o777, 0o755)
            self.assertEqual(operations[0]["target"], "wrappers/bin/custom-launcher")
            self.assertEqual(operations[0]["status"], "applied")

    def test_hash_pinned_wrapper_review_clears_pending_state(self):
        with tempfile.TemporaryDirectory() as raw:
            _, project = _seed(Path(raw), marker="v0.2.0")
            wrapper = project / "wrappers" / "custom-launcher"
            wrapper.parent.mkdir()
            wrapper.write_text("already uses the project root\n")
            first = migrations.plan_entry(project, "v0.3.0")
            self.assertEqual(first.pending[0]["path"], "wrappers/custom-launcher")
            migrations.record_pending_actions(project, "v0.3.0", first)
            (project / "decisions").mkdir()
            mediated_write(
                project,
                "decision",
                "DEC-001-wrapper-review.md",
                "# Wrapper review\n\nThe launcher already conforms.\n",
            )
            second = migrations.plan_entry(project, "v0.3.0")
            self.assertFalse(second.pending)
            self.assertEqual(second.skipped[0]["kind"], "wrapper-migration")
            self.assertEqual(second.skipped[0]["status"], "skipped")

    @unittest.skipUnless(migrations.DIR_FD_SUPPORTED, "requires POSIX dir-fd support")
    def test_apply_error_reports_operations_that_landed_before_refusal(self):
        with tempfile.TemporaryDirectory() as raw:
            _, project = _seed(Path(raw), marker="v0.1.0")
            (project / "tasks").mkdir()
            (project / "reviews").mkdir()
            old = project / "ENGINEERING.md"
            old.write_text("# Standards\n", encoding="utf-8")
            plan = migrations.plan_entry(project, "v0.2.0")

            def swap():
                old.unlink()
                old.symlink_to(Path(raw) / "outside.md")

            previous = migrations._delete_concurrent_swap_hook
            migrations._delete_concurrent_swap_hook = swap
            try:
                with self.assertRaises(migrations.MigrationApplyError) as caught:
                    migrations.apply_plan(project, "v0.2.0", plan)
            finally:
                migrations._delete_concurrent_swap_hook = previous
            self.assertTrue(caught.exception.operations)
            self.assertEqual(caught.exception.operations[0]["target"], "STANDARDS.md")
            self.assertTrue((project / "STANDARDS.md").exists())

    @unittest.skipUnless(migrations.DIR_FD_SUPPORTED, "requires POSIX dir-fd support")
    def test_provenance_backed_partial_rename_resumes_safely(self):
        with tempfile.TemporaryDirectory() as raw:
            _, project = _seed(Path(raw), marker="v0.1.0")
            (project / "tasks").mkdir()
            (project / "reviews").mkdir()
            old = project / "ENGINEERING.md"
            old.write_text("# Standards\n", encoding="utf-8")
            plan = migrations.plan_entry(project, "v0.2.0")

            def interrupt():
                raise GuardRefusal("injected-interruption", "stop before retirement")

            previous = migrations._delete_concurrent_swap_hook
            migrations._delete_concurrent_swap_hook = interrupt
            try:
                with self.assertRaises(migrations.MigrationApplyError):
                    migrations.apply_plan(project, "v0.2.0", plan)
            finally:
                migrations._delete_concurrent_swap_hook = previous
            self.assertTrue(old.exists())
            self.assertTrue((project / "STANDARDS.md").exists())

            resumed = migrations.plan_entry(project, "v0.2.0")
            operations = migrations.apply_plan(project, "v0.2.0", resumed)
            self.assertFalse(old.exists())
            self.assertEqual(
                [operation["status"] for operation in operations],
                ["skipped", "applied"],
            )


if __name__ == "__main__":
    unittest.main()
