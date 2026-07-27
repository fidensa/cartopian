"""Authoritative configuration and version contract tests."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cli.config_schema import (
    AUTO_LAUNCH_ACTIVITIES,
    CONFIG_SCHEMA,
    ConfigDiagnostic,
    identity_contract,
    resolve_configuration,
    validate_authored_config,
)
from cli.version_identities import version_identities

REPO_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"


class TestClosedSchema(unittest.TestCase):
    def test_contract_owns_scopes_defaults_precedence_legacy_vocabulary_and_identities(self):
        self.assertEqual(
            CONFIG_SCHEMA["scopes"],
            ("global", "project", "machine-local"),
        )
        self.assertEqual(
            CONFIG_SCHEMA["precedence"],
            ("protocol-default", "global", "project", "machine-local"),
        )
        self.assertEqual(
            CONFIG_SCHEMA["legacy_vocabulary"],
            {
                "authored_config_paths": (
                    "project.protocol_version",
                    "protocol_version",
                    "roles.*.launch",
                    "roles.*.launch.target",
                    "roles.*.launch.model",
                    "roles.*.launch.effort",
                    "roles.*.launch.timeout",
                    "handoffs",
                    "handoffs.*",
                    "handoffs.*.auto_start",
                    "handoffs.*.auto_start_tasks",
                    "handoffs.*.auto_start_reviews",
                    "handoffs.*.planning_reviews",
                ),
                "retired_cli_flags": (
                    "--set-handoff",
                    "--remove-handoff",
                ),
            },
        )
        self.assertIn("git.default_merge_strategy", CONFIG_SCHEMA["fields"])
        self.assertEqual(
            CONFIG_SCHEMA["role_output"],
            (
                "description",
                "effective_grants",
                "assigned_work_types",
                "launch",
                "auto_launch",
                "attribution",
            ),
        )
        self.assertEqual(
            tuple(identity_contract()),
            (
                "release_version",
                "installed_content",
                "project_schema_version",
                "running_server",
                "mcp_protocol_version",
            ),
        )

    def test_unknown_and_migration_source_keys_fail_closed(self):
        base = {
            "project": {
                "id": "demo",
                "name": "Demo",
                "project_schema_version": "v0.8.0",
            }
        }
        with self.assertRaisesRegex(ConfigDiagnostic, "unknown-key.*project.mystery"):
            validate_authored_config(
                {"project": {**base["project"], "mystery": True}},
                "project",
            )
        with self.assertRaisesRegex(
            ConfigDiagnostic, "migration-source-only.*project.protocol_version"
        ):
            validate_authored_config(
                {
                    "project": {
                        "id": "demo",
                        "name": "Demo",
                        "protocol_version": "v0.7.0",
                    }
                },
                "project",
            )
        for legacy in (
            {"handoffs": {}},
            {"handoffs": {"coder": {"auto_start": True}}},
        ):
            with self.subTest(legacy=legacy):
                with self.assertRaisesRegex(
                    ConfigDiagnostic,
                    "migration-source-only.*handoffs.*"
                    "recovery=run-approved-config-migration",
                ):
                    validate_authored_config(
                        {**base, **legacy},
                        "project",
                    )

    def test_scope_and_machine_local_path_ownership(self):
        with self.assertRaisesRegex(ConfigDiagnostic, "scope.*work_roots"):
            validate_authored_config(
                {"work_roots": {"tool": "/tmp/tool"}},
                "project",
            )
        with self.assertRaisesRegex(ConfigDiagnostic, "absolute-path"):
            validate_authored_config(
                {"work_roots": {"tool": "relative/tool"}},
                "machine-local",
            )

    def test_closed_auto_launch_and_timeout_domains(self):
        project = {
            "project": {
                "id": "demo",
                "name": "Demo",
                "project_schema_version": "v0.8.0",
            },
            "roles": {
                "coder": {
                    "description": "Writes code.",
                    "auto_launch": ["task_run"],
                    "target": "cartopian-codex", "timeout": "45m",
                }
            },
        }
        validate_authored_config(project, "project")
        self.assertEqual(
            AUTO_LAUNCH_ACTIVITIES,
            ("task_run", "task_review", "planning_review"),
        )
        project["roles"]["coder"]["auto_launch"] = ["everything"]
        with self.assertRaisesRegex(ConfigDiagnostic, "unknown-value.*auto_launch"):
            validate_authored_config(project, "project")
        project["roles"]["coder"]["auto_launch"] = ["task_run"]
        project["roles"]["coder"]["timeout"] = "soon"
        with self.assertRaisesRegex(ConfigDiagnostic, "invalid-timeout"):
            validate_authored_config(project, "project")
        project["roles"]["coder"]["timeout"] = "45m"
        project["roles"]["coder"]["launch"] = {"target": "cartopian-codex"}
        with self.assertRaisesRegex(
            ConfigDiagnostic, "migration-source-only.*roles.coder.launch"
        ):
            validate_authored_config(project, "project")

    def test_legacy_launch_diagnostics_are_scoped_to_role_paths(self):
        role_base = {"description": "Writes code."}
        for legacy_key in ("launch", "launch.target", "launch.timeout"):
            with self.subTest(legacy_key=legacy_key):
                global_cfg = {
                    "roles": {
                        "coder": {
                            **role_base,
                            legacy_key: (
                                {"target": "cartopian-codex"}
                                if legacy_key == "launch"
                                else "probe"
                            ),
                        }
                    }
                }
                with self.assertRaises(ConfigDiagnostic) as caught:
                    validate_authored_config(global_cfg, "global")
                self.assertEqual(caught.exception.code, "migration-source-only")
                self.assertEqual(
                    caught.exception.recovery,
                    "run-approved-config-migration",
                )
                self.assertTrue(
                    caught.exception.field.startswith("roles.coder.launch")
                )

    def test_unrelated_launch_keys_are_actionable_ordinary_unknowns(self):
        for root in ("git", "automation"):
            with self.subTest(root=root):
                with self.assertRaises(ConfigDiagnostic) as caught:
                    validate_authored_config({root: {"launch": "probe"}}, "global")
                diagnostic = caught.exception
                self.assertEqual(diagnostic.code, "unknown-key")
                self.assertEqual(diagnostic.field, f"{root}.launch")
                self.assertEqual(
                    diagnostic.recovery,
                    "remove-or-correct-unknown-key",
                )
                self.assertNotIn("migration", diagnostic.message)
                self.assertNotEqual(
                    diagnostic.recovery,
                    "run-approved-config-migration",
                )


class TestCanonicalResolution(unittest.TestCase):
    def test_precedence_attribution_and_role_projection(self):
        global_cfg = {
            "automation": {"confirmation": "until-blocked"},
            "roles": {
                "coder": {
                    "description": "Global coder.",
                    "grants": ["coder-like"],
                    "target": "cartopian-claude",
                    "timeout": "30m",
                }
            },
        }
        project_cfg = {
            "project": {
                "id": "demo",
                "name": "Demo",
                "project_schema_version": "v0.8.0",
                "work_roots": ["tool"],
            },
            "roles": {
                "coder": {
                    "description": "Project coder.",
                    "auto_launch": ["task_run"],
                    "target": "cartopian-codex", "effort": "high",
                },
                "reviewer": {
                    "description": "Reviews work.",
                    "grants": ["reviewer-like"],
                    "auto_launch": ["task_review"],
                    "target": "cartopian-claude",
                },
            },
            "reviews": {
                "task_closure": "required",
                "task_role": "reviewer",
            },
        }
        record = resolve_configuration(
            global_cfg,
            project_cfg,
            {"work_roots": {"tool": "/tmp/tool"}},
        )
        self.assertEqual(tuple(record), CONFIG_SCHEMA["preferred_output"])
        self.assertEqual(record["project_schema_version"], "v0.8.0")
        self.assertNotIn("protocol_version", record)
        self.assertEqual(record["automation"]["initiation"], "operator")
        self.assertEqual(
            record["automation"]["attribution"],
            {
                "initiation": "protocol-default",
                "confirmation": "global",
                "max_handoffs_per_run": "protocol-default",
            },
        )
        coder = record["roles"]["coder"]
        self.assertEqual(tuple(coder), CONFIG_SCHEMA["role_output"])
        self.assertEqual(coder["description"], "Project coder.")
        self.assertEqual(coder["assigned_work_types"], ["task_run"])
        self.assertEqual(coder["launch"]["target"], "cartopian-codex")
        self.assertEqual(coder["launch"]["timeout"], "30m")
        self.assertEqual(coder["auto_launch"], ["task_run"])
        self.assertEqual(coder["attribution"]["description"], "project")
        self.assertEqual(coder["attribution"]["launch"]["timeout"], "global")
        self.assertEqual(
            coder["attribution"]["assigned_work_types"],
            {"task_run": "derived"},
        )
        self.assertEqual(coder["attribution"]["effective_grants"], "derived")
        self.assertEqual(record["work_roots"], {"tool": "/tmp/tool"})
        self.assertEqual(
            record["work_roots_attribution"],
            {"tool": {"declaration": "project", "mapping": "machine-local"}},
        )

    def test_review_permission_must_match_assignment(self):
        project_cfg = {
            "project": {
                "id": "demo",
                "name": "Demo",
                "project_schema_version": "v0.8.0",
            },
            "roles": {
                "reviewer": {
                    "description": "Reviews.",
                    "auto_launch": ["planning_review"],
                    "target": "cartopian-claude",
                }
            },
        }
        with self.assertRaisesRegex(
            ConfigDiagnostic, "inapplicable-permission.*planning_review"
        ):
            resolve_configuration({}, project_cfg, {})

    def test_pm_is_not_launchable_and_orphan_reviews_fail(self):
        project_cfg = {
            "project": {
                "id": "demo",
                "name": "Demo",
                "project_schema_version": "v0.8.0",
            },
            "roles": {
                "pm": {
                    "description": "Plans.",
                    "target": "cartopian-codex",
                }
            },
        }
        with self.assertRaisesRegex(ConfigDiagnostic, "pm-launch-forbidden"):
            resolve_configuration({}, project_cfg, {})
        project_cfg["roles"]["pm"].pop("target")
        project_cfg["reviews"] = {
            "planning": "required",
            "planning_role": "reviewer",
        }
        with self.assertRaisesRegex(ConfigDiagnostic, "orphan-reference"):
            resolve_configuration({}, project_cfg, {})

    def test_pm_cannot_declare_auto_launch_even_for_assigned_review(self):
        project_cfg = {
            "project": {
                "id": "demo",
                "name": "Demo",
                "project_schema_version": "v0.8.0",
            },
            "roles": {
                "pm": {
                    "description": "Plans.",
                    "auto_launch": ["task_review"],
                }
            },
            "reviews": {
                "task_closure": "required",
                "task_role": "pm",
            },
        }
        with self.assertRaisesRegex(
            ConfigDiagnostic, "pm-auto-launch-forbidden"
        ):
            resolve_configuration({}, project_cfg, {})

    def test_capability_activation_remains_least_authority(self):
        project_cfg = {
            "project": {
                "id": "demo",
                "name": "Demo",
                "project_schema_version": "v0.8.0",
            },
            "roles": {
                "coder": {
                    "description": "Writes.",
                    "grants": ["coder-like"],
                },
                "observer": {"description": "Observes."},
            },
        }
        record = resolve_configuration({}, project_cfg, {})
        self.assertTrue(record["capabilities"]["activated"])
        self.assertEqual(record["roles"]["observer"]["effective_grants"], [])


class TestCliMcpParity(unittest.TestCase):
    _PROJECT = (
        "[project]\n"
        'id = "demo"\n'
        'name = "Demo"\n'
        'project_schema_version = "v0.8.0"\n'
        "\n"
        "[roles.coder]\n"
        'description = "Writes code."\n'
        'grants = ["coder-like"]\n'
        'auto_launch = ["task_run"]\n'
        'target = "cartopian-codex"\n'
        'timeout = "45m"\n'
    )

    def test_cli_and_cli_backed_mcp_emit_same_canonical_record(self):
        from mcp_server import server

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            project = root / "project"
            home.mkdir()
            project.mkdir()
            (project / "cartopian.toml").write_text(
                self._PROJECT, encoding="utf-8"
            )
            env = {
                "HOME": str(home),
                "PATH": os.environ.get("PATH", ""),
            }
            cli = subprocess.run(
                [
                    sys.executable,
                    str(ENTRYPOINT),
                    "resolve-config",
                    str(project),
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(cli.returncode, 0, msg=cli.stderr)
            cli_record = json.loads(cli.stdout)
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = str(home)
            try:
                mcp = server.call_tool(
                    "resolve_config", {"project_path": str(project)}
                )
            finally:
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home
        self.assertFalse(mcp["isError"], msg=mcp)
        self.assertEqual(mcp["structuredContent"]["records"], [cli_record])
        self.assertNotIn("protocol_version", cli_record)
        self.assertNotIn("handoffs", cli_record)
        self.assertEqual(
            cli_record["roles"]["coder"]["auto_launch"], ["task_run"]
        )

    def test_generate_config_emits_only_preferred_form(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            project = root / "project"
            home.mkdir()
            project.mkdir()
            result = subprocess.run(
                [
                    sys.executable,
                    str(ENTRYPOINT),
                    "generate-config",
                    str(project),
                    "--name",
                    "Demo",
                    "--id",
                    "demo",
                    "--role",
                    "coder=Writes code.",
                    "--role-launch-target",
                    "coder=cartopian-codex",
                    "--role-auto-launch",
                    "coder=task_run",
                ],
                cwd=REPO_ROOT,
                env={
                    "HOME": str(home),
                    "PATH": os.environ.get("PATH", ""),
                },
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            text = (project / "cartopian.toml").read_text(encoding="utf-8")
        self.assertIn("project_schema_version", text)
        self.assertEqual(text.count("[roles.coder]"), 1)
        self.assertNotIn("[roles.coder.launch]", text)
        self.assertIn('target = "cartopian-codex"', text)
        self.assertIn('auto_launch = [', text)
        self.assertNotIn("protocol_version", text)
        self.assertNotIn("[handoffs", text)
        self.assertNotIn("auto_start", text)


class TestVersionIdentities(unittest.TestCase):
    def test_peer_identities_do_not_substitute_for_each_other(self):
        records = version_identities(
            REPO_ROOT,
            mcp_protocol_version="2024-11-05",
            include_running_server=True,
        )
        self.assertEqual(tuple(records), tuple(identity_contract()))
        self.assertEqual(records["release_version"]["state"], "unknown")
        self.assertIsNone(records["release_version"]["value"])
        self.assertIsNotNone(records["installed_content"]["revision"])
        self.assertNotEqual(
            records["release_version"].get("value"),
            records["installed_content"]["revision"],
        )
        self.assertEqual(
            records["mcp_protocol_version"]["value"], "2024-11-05"
        )
        self.assertIn(
            records["running_server"]["state"],
            ("current", "stale-runtime", "unknown"),
        )


if __name__ == "__main__":
    unittest.main()
