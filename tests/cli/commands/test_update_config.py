"""Tests for `cartopian update-config` — the mediated config editor."""
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"

# A base project config with a table-form role (multiline grants array, as
# generate-config / tomli_w emit) and a legacy string-form role.
BASE_CONFIG = """\
[project]
name = "Demo"
id = "demo"
protocol_version = "v0.5.0"

[roles.pm]
description = "Plans phases."
grants = [
    "pm-solo",
]

[roles.operator]
description = "Approves."
grants = []

# operator note: keep this comment
[roles.coder]
description = "Implements."
grants = [
    "coder-like",
]
"""


def _run(*cli_args, home):
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "")}
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), "update-config", *cli_args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        # Empty HOME so no global ~/.cartopian/cartopian.toml interferes.
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.proj = self.tmp / "proj"
        self.proj.mkdir()
        self.cfg = self.proj / "cartopian.toml"
        self.cfg.write_text(BASE_CONFIG, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def run_uc(self, *args):
        return _run(*args, home=self.home)

    def assertNoTempFiles(self):
        leftovers = [p.name for p in self.proj.iterdir() if ".cartmp." in p.name]
        self.assertEqual(leftovers, [], f"temp files left behind: {leftovers}")


class TestHelp(unittest.TestCase):
    def test_help_lists_flags(self):
        proc = subprocess.run(
            [sys.executable, str(ENTRYPOINT), "update-config", "--help"],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
            env={"PATH": os.environ.get("PATH", "")},
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        for flag in ("--set", "--unset", "--set-role", "--set-role-grants",
                     "--set-handoff", "--remove-role", "--remove-handoff",
                     "--local", "--set-work-root"):
            self.assertIn(flag, proc.stdout)


class TestExistenceGuards(_Base):
    def test_missing_project_config_is_guard(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        proc = self.run_uc(str(empty), "--set", "automation.initiation=auto")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("config-not-found", proc.stderr)

    def test_relative_path_rejected(self):
        proc = self.run_uc("relative/path", "--set", "automation.initiation=auto")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("absolute", proc.stderr)


class TestScalarEdits(_Base):
    def test_set_scalar_adds_key_and_records_change(self):
        proc = self.run_uc(str(self.proj), "--set", "automation.initiation=auto")
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        rec = json.loads(proc.stdout)
        self.assertEqual(rec["details"]["changed"], ["automation.initiation"])
        cfg = tomllib.loads(self.cfg.read_text())
        self.assertEqual(cfg["automation"]["initiation"], "auto")
        self.assertNoTempFiles()

    def test_comment_preserved_on_edit(self):
        self.run_uc(str(self.proj), "--set", "automation.initiation=auto")
        text = self.cfg.read_text()
        self.assertIn("# operator note: keep this comment", text)

    def test_idempotent_noop_is_byte_identical(self):
        self.run_uc(str(self.proj), "--set", "automation.initiation=auto")
        before = self.cfg.read_bytes()
        proc = self.run_uc(str(self.proj), "--set", "automation.initiation=auto")
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        rec = json.loads(proc.stdout)
        self.assertEqual(rec["details"]["changed"], [])
        self.assertEqual(self.cfg.read_bytes(), before)

    def test_unknown_key_fails_closed_unchanged(self):
        before = self.cfg.read_bytes()
        proc = self.run_uc(str(self.proj), "--set", "bogus.key=1")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown config key", proc.stderr)
        self.assertEqual(self.cfg.read_bytes(), before)

    def test_enum_validation(self):
        proc = self.run_uc(str(self.proj), "--set", "automation.initiation=sometimes")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("must be one of", proc.stderr)

    def test_posint_validation(self):
        proc = self.run_uc(str(self.proj), "--set", "automation.max_handoffs_per_run=0")
        self.assertEqual(proc.returncode, 2)

    def test_bool_validation(self):
        proc = self.run_uc(str(self.proj), "--set", "defaults.git_versioning=yes")
        self.assertEqual(proc.returncode, 2)

    def test_version_validation(self):
        proc = self.run_uc(str(self.proj), "--set", "project.protocol_version=1.0")
        self.assertEqual(proc.returncode, 2)
        good = self.run_uc(str(self.proj), "--set", "project.protocol_version=v0.9.0")
        self.assertEqual(good.returncode, 0, msg=good.stderr)
        self.assertEqual(tomllib.loads(self.cfg.read_text())["project"]["protocol_version"], "v0.9.0")

    def test_work_roots_list(self):
        proc = self.run_uc(str(self.proj), "--set", "project.work_roots=product,design")
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(
            tomllib.loads(self.cfg.read_text())["project"]["work_roots"], ["product", "design"]
        )

    def test_git_key_string_not_type_inferred(self):
        # A numeric-looking branch pattern stays a string (type from schema).
        proc = self.run_uc(
            str(self.proj),
            "--set", "defaults.git_versioning=true",
            "--set", "git.default_branch_pattern=123",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(
            tomllib.loads(self.cfg.read_text())["git"]["default_branch_pattern"], "123"
        )

    def test_unknown_git_key_rejected(self):
        proc = self.run_uc(str(self.proj), "--set", "git.anything=1")
        self.assertEqual(proc.returncode, 2)


class TestRemovals(_Base):
    def test_unset_scalar(self):
        self.run_uc(str(self.proj), "--set", "automation.initiation=auto")
        proc = self.run_uc(str(self.proj), "--unset", "automation.initiation")
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        cfg = tomllib.loads(self.cfg.read_text())
        self.assertNotIn("initiation", cfg.get("automation", {}))

    def test_remove_role(self):
        proc = self.run_uc(str(self.proj), "--remove-role", "coder")
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertNotIn("coder", tomllib.loads(self.cfg.read_text()).get("roles", {}))


class TestRoleAndHandoff(_Base):
    def test_add_role_then_grants_converts_preserving_description(self):
        # Repair a fresh role: string form then grants -> table form, desc kept.
        proc = self.run_uc(
            str(self.proj),
            "--set-role", 'reviewer="Reviews per evidence."',
            "--set-role-grants", "reviewer=reviewer-like",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        cfg = tomllib.loads(self.cfg.read_text())
        self.assertEqual(cfg["roles"]["reviewer"]["description"], "Reviews per evidence.")
        self.assertEqual(cfg["roles"]["reviewer"]["grants"], ["reviewer-like"])

    def test_edit_existing_table_role_grants_multiline_array(self):
        # coder starts with a multiline grants array; editing must not orphan lines.
        proc = self.run_uc(str(self.proj), "--set-role-grants", "coder=planner-like")
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        cfg = tomllib.loads(self.cfg.read_text())
        self.assertEqual(cfg["roles"]["coder"]["grants"], ["planner-like"])
        self.assertEqual(cfg["roles"]["coder"]["description"], "Implements.")

    def test_unknown_grant_rejected(self):
        proc = self.run_uc(str(self.proj), "--set-role-grants", "coder=not-a-grant")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown capability or preset", proc.stderr)

    def test_valid_handoff_for_declared_role(self):
        proc = self.run_uc(
            str(self.proj),
            "--set-handoff", "coder.agent=cartopian-claude",
            "--set-handoff", "coder.auto_start_tasks=true",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        cfg = tomllib.loads(self.cfg.read_text())
        self.assertEqual(cfg["handoffs"]["coder"]["agent"], "cartopian-claude")
        self.assertIs(cfg["handoffs"]["coder"]["auto_start_tasks"], True)

    def test_set_review_policy_and_arbitrary_assigned_role(self):
        proc = self.run_uc(
            str(self.proj),
            "--set-role", "reviewer=Checks work independently.",
            "--set-role-grants", "reviewer=reviewer-like",
            "--set", "reviews.planning=required",
            "--set", "reviews.planning_role=reviewer",
            "--set", "reviews.task_closure=off",
            "--set-handoff", "reviewer.auto_start_reviews=true",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        cfg = tomllib.loads(self.cfg.read_text())
        self.assertEqual(cfg["reviews"]["planning"], "required")
        self.assertEqual(cfg["reviews"]["planning_role"], "reviewer")
        self.assertEqual(cfg["reviews"]["task_closure"], "off")
        self.assertTrue(cfg["handoffs"]["reviewer"]["auto_start_reviews"])

    def test_cannot_remove_role_assigned_to_required_review(self):
        first = self.run_uc(
            str(self.proj),
            "--set-role", "reviewer=Checks work.",
            "--set-role-grants", "reviewer=reviewer-like",
            "--set", "reviews.task_closure=required",
            "--set", "reviews.task_role=reviewer",
        )
        self.assertEqual(first.returncode, 0, msg=first.stderr)
        before = self.cfg.read_bytes()
        result = self.run_uc(str(self.proj), "--remove-role", "reviewer")
        self.assertEqual(result.returncode, 1)
        self.assertIn("undeclared role", result.stderr)
        self.assertEqual(self.cfg.read_bytes(), before)

    def test_orphan_handoff_rejected_effective_layer(self):
        before = self.cfg.read_bytes()
        proc = self.run_uc(str(self.proj), "--set-handoff", "ghost.agent=cartopian-claude")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("orphan-handoff", proc.stderr)
        self.assertEqual(self.cfg.read_bytes(), before)

    def test_handoff_pm_forbidden(self):
        proc = self.run_uc(str(self.proj), "--set-handoff", "pm.agent=x")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("pm", proc.stderr)


class TestConflictsAndUsage(_Base):
    def test_no_ops_is_usage_error(self):
        proc = self.run_uc(str(self.proj))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("no operations", proc.stderr)

    def test_duplicate_set_key(self):
        proc = self.run_uc(
            str(self.proj),
            "--set", "automation.initiation=auto",
            "--set", "automation.initiation=operator",
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("more than once", proc.stderr)

    def test_set_and_remove_role_conflict(self):
        proc = self.run_uc(str(self.proj), "--set-role", 'coder="x"', "--remove-role", "coder")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("both set and removed", proc.stderr)


class TestUnsupportedToml(_Base):
    def test_multiline_string_fails_closed(self):
        self.cfg.write_text(BASE_CONFIG + '\n[extra]\nnote = """\nmulti\n"""\n')
        before = self.cfg.read_bytes()
        proc = self.run_uc(str(self.proj), "--set", "automation.initiation=auto")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("unsupported-toml", proc.stderr)
        self.assertEqual(self.cfg.read_bytes(), before)


class TestLocalTarget(_Base):
    def test_local_creates_with_mapping(self):
        proc = self.run_uc(
            str(self.proj), "--local", "--set-work-root", "product=/abs/product"
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        local = self.proj / "cartopian.local.toml"
        self.assertTrue(local.exists())
        self.assertTrue(local.read_text().endswith("\n"))
        self.assertEqual(tomllib.loads(local.read_text())["work_roots"]["product"], "/abs/product")

    def test_local_absent_without_mapping_is_guard(self):
        proc = self.run_uc(str(self.proj), "--local", "--unset-work-root", "product")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("config-not-found", proc.stderr)

    def test_local_rejects_non_absolute_path(self):
        proc = self.run_uc(str(self.proj), "--local", "--set-work-root", "product=rel/path")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("absolute", proc.stderr)

    def test_local_rejects_project_ops(self):
        proc = self.run_uc(str(self.proj), "--local", "--set", "automation.initiation=auto")
        self.assertEqual(proc.returncode, 2)

    def test_project_target_rejects_local_ops(self):
        proc = self.run_uc(str(self.proj), "--set-work-root", "product=/abs")
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
