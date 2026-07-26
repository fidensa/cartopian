"""Integration tests for ``cartopian resolve-config`` preferred-form config."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"


def _run(project_path, *, home):
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "")}
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), "resolve-config", str(project_path)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class _Sandbox:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.project = self.root / "proj"
        self.home.mkdir()
        self.project.mkdir()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self._tmp.cleanup()


_PROJECT = (
    '[project]\n'
    'id = "demo"\n'
    'name = "Demo"\n'
    'project_schema_version = "v0.7.0"\n'
)


class TestHappyPathPreferredResolution(unittest.TestCase):
    def _record(self, result):
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def test_emits_canonical_role_authority_and_attribution(self):
        with _Sandbox() as sb:
            _write(
                sb.home / ".cartopian" / "cartopian.toml",
                '[automation]\nconfirmation = "until-blocked"\n'
                'max_handoffs_per_run = 3\n\n'
                '[roles.coder]\ndescription = "Global coder."\n'
                'grants = ["coder-like"]\n',
            )
            _write(
                sb.project / "cartopian.toml",
                _PROJECT
                + '\n[roles.coder]\ndescription = "Writes code."\n'
                'auto_launch = ["task_run"]\n'
                'target = "claude"\n'
                'model = "sonnet"\ntimeout = "60m"\n',
            )
            record = self._record(_run(sb.project, home=sb.home))

        self.assertEqual(record["schema_identity"], "cartopian-authoritative-config-v2")
        self.assertEqual(record["project_id"], "demo")
        coder = record["roles"]["coder"]
        self.assertEqual(coder["description"], "Writes code.")
        self.assertEqual(
            coder["effective_grants"],
            ["read:prompts", "read:work-roots", "write:worktree"],
        )
        self.assertEqual(coder["assigned_work_types"], ["task_run"])
        self.assertEqual(coder["auto_launch"], ["task_run"])
        self.assertEqual(coder["launch"]["target"], "claude")
        self.assertEqual(coder["attribution"]["description"], "project")
        self.assertEqual(coder["attribution"]["grants"], "global")
        self.assertEqual(
            record["automation"],
            {
                "initiation": "operator",
                "confirmation": "until-blocked",
                "max_handoffs_per_run": 3,
                "attribution": {
                    "initiation": "protocol-default",
                    "confirmation": "global",
                    "max_handoffs_per_run": "global",
                },
            },
        )

    def test_review_assignments_make_auto_launch_applicable(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                _PROJECT
                + '\n[roles.reviewer]\ndescription = "Checks work."\n'
                'auto_launch = ["task_review", "planning_review"]\n'
                'target = "codex"\n\n'
                '[reviews]\nplanning = "required"\n'
                'planning_role = "reviewer"\n'
                'task_closure = "required"\ntask_role = "reviewer"\n',
            )
            record = self._record(_run(sb.project, home=sb.home))
        self.assertEqual(
            record["roles"]["reviewer"]["assigned_work_types"],
            ["task_run", "task_review", "planning_review"],
        )
        self.assertEqual(record["reviews"]["planning"]["role"], "reviewer")
        self.assertEqual(record["reviews"]["task_closure"]["role"], "reviewer")

    def test_project_can_disable_global_review_policy(self):
        with _Sandbox() as sb:
            _write(
                sb.home / ".cartopian" / "cartopian.toml",
                '[roles.reviewer]\ndescription = "Checks work."\n\n'
                '[reviews]\nplanning = "required"\nplanning_role = "reviewer"\n'
                'task_closure = "required"\ntask_role = "reviewer"\n',
            )
            _write(
                sb.project / "cartopian.toml",
                _PROJECT + '\n[reviews]\nplanning = "off"\ntask_closure = "off"\n',
            )
            record = self._record(_run(sb.project, home=sb.home))
        self.assertEqual(record["reviews"]["planning"]["mode"], "off")
        self.assertEqual(record["reviews"]["task_closure"]["mode"], "off")

    def test_git_defaults_are_explicit_and_attributed(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                _PROJECT + '\n[defaults]\ngit_versioning = true\n',
            )
            record = self._record(_run(sb.project, home=sb.home))
        self.assertEqual(
            record["git"],
            {
                "pm_owns_product_branches": False,
                "default_branch_pattern": "task/{task_id}-{slug}",
                "default_merge_strategy": "merge",
            },
        )
        self.assertEqual(record["defaults_attribution"]["git_versioning"], "project")
        self.assertEqual(
            record["defaults_attribution"]["git"]["default_merge_strategy"],
            "protocol-default",
        )

    def test_work_root_declaration_and_machine_mapping_are_attributed(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                _PROJECT.replace(
                    'project_schema_version = "v0.7.0"\n',
                    'project_schema_version = "v0.7.0"\nwork_roots = ["site"]\n',
                ),
            )
            _write(
                sb.project / "cartopian.local.toml",
                '[work_roots]\nsite = "/tmp/site-dir"\n',
            )
            record = self._record(_run(sb.project, home=sb.home))
        self.assertEqual(record["work_roots"], {"site": "/tmp/site-dir"})
        self.assertEqual(
            record["work_roots_attribution"]["site"],
            {"declaration": "project", "mapping": "machine-local"},
        )


class TestFailClosedDiagnostics(unittest.TestCase):
    def test_legacy_handoffs_are_migration_source_only(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                _PROJECT + '\n[handoffs.coder]\nagent = "claude"\n',
            )
            result = _run(sb.project, home=sb.home)
        self.assertEqual(result.returncode, 1)
        self.assertIn("migration-source-only", result.stderr)
        self.assertIn("handoffs", result.stderr)
        self.assertIn("recovery=run-approved-config-migration", result.stderr)

    def test_string_role_is_rejected(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                _PROJECT + '\n[roles]\ncoder = "Writes code."\n',
            )
            result = _run(sb.project, home=sb.home)
        self.assertEqual(result.returncode, 1)
        self.assertIn("roles.coder: must be a table", result.stderr)

    def test_unknown_enum_is_rejected_without_fallback(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                _PROJECT + '\n[automation]\ninitiation = "always"\n',
            )
            result = _run(sb.project, home=sb.home)
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown-value", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_inapplicable_permission_is_rejected(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                _PROJECT
                + '\n[roles.reviewer]\ndescription = "Checks."\n'
                'auto_launch = ["planning_review"]\n'
                'target = "codex"\n',
            )
            result = _run(sb.project, home=sb.home)
        self.assertEqual(result.returncode, 1)
        self.assertIn("inapplicable-permission", result.stderr)

    def test_required_review_rejects_orphan_role(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                _PROJECT
                + '\n[reviews]\nplanning = "required"\n'
                'planning_role = "reviewer"\n',
            )
            result = _run(sb.project, home=sb.home)
        self.assertEqual(result.returncode, 1)
        self.assertIn("orphan-reference", result.stderr)

    def test_pm_launch_is_forbidden(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                _PROJECT
                + '\n[roles.pm]\ndescription = "PM."\n'
                'target = "codex"\n',
            )
            result = _run(sb.project, home=sb.home)
        self.assertEqual(result.returncode, 1)
        self.assertIn("pm-launch-forbidden", result.stderr)

    def test_relative_work_root_mapping_is_rejected(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                _PROJECT.replace(
                    'project_schema_version = "v0.7.0"\n',
                    'project_schema_version = "v0.7.0"\nwork_roots = ["site"]\n',
                ),
            )
            _write(
                sb.project / "cartopian.local.toml",
                '[work_roots]\nsite = "relative/path"\n',
            )
            result = _run(sb.project, home=sb.home)
        self.assertEqual(result.returncode, 1)
        self.assertIn("absolute path", result.stderr)


class TestInvocationGuards(unittest.TestCase):
    def test_missing_cartopian_toml_fails(self):
        with _Sandbox() as sb:
            result = _run(sb.project, home=sb.home)
        self.assertEqual(result.returncode, 1)
        self.assertIn("[error] project config not found:", result.stderr)

    def test_relative_project_path_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            env = {"HOME": str(home), "PATH": os.environ.get("PATH", "")}
            result = subprocess.run(
                [sys.executable, str(ENTRYPOINT), "resolve-config", "projects/demo"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("project_path must be an absolute path", result.stderr)

    def test_workspace_config_is_not_a_project(self):
        with _Sandbox() as sb:
            _write(sb.project / "cartopian.toml", '[defaults]\ngit_versioning = false\n')
            result = _run(sb.project, home=sb.home)
        self.assertEqual(result.returncode, 1)
        self.assertIn("workspace config, not a project config", result.stderr)


if __name__ == "__main__":
    unittest.main()
