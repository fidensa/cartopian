import json
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from cli import config_migration, config_schema
from cli.commands import migrate_config
from mcp_server import server


FIXTURES = (
    Path(__file__).resolve().parents[2] / "fixtures" / "config_migration"
)


def _seed(raw: Path, case: str) -> tuple[Path, Path]:
    home = raw / "home"
    project = raw / "project"
    cartopian_home = home / ".cartopian"
    cartopian_home.mkdir(parents=True)
    project.mkdir()
    fixture = FIXTURES / case
    global_fixture = fixture / "global.toml"
    local_fixture = fixture / "local.toml"
    if global_fixture.exists():
        shutil.copyfile(global_fixture, cartopian_home / "cartopian.toml")
    if local_fixture.exists():
        shutil.copyfile(local_fixture, project / "cartopian.local.toml")
    shutil.copyfile(fixture / "project.toml", project / "cartopian.toml")
    project_id = tomllib.loads(
        (project / "cartopian.toml").read_text(encoding="utf-8")
    ).get("project", {}).get("id", "migration-fixture")
    (cartopian_home / "projects.json").write_text(
        json.dumps([{"id": project_id, "path": str(project)}]),
        encoding="utf-8",
    )
    return home, project


def _config_bytes(home: Path, project: Path) -> tuple[bytes, bytes, bytes | None]:
    local = project / "cartopian.local.toml"
    return (
        (home / ".cartopian" / "cartopian.toml").read_bytes()
        if (home / ".cartopian" / "cartopian.toml").exists()
        else b"",
        (project / "cartopian.toml").read_bytes(),
        local.read_bytes() if local.exists() else None,
    )


def _attribution_strings(record):
    values = []

    def walk(value, collecting=False):
        if isinstance(value, dict):
            for key, child in value.items():
                walk(
                    child,
                    collecting
                    or key == "attribution"
                    or key.endswith("_attribution"),
                )
        elif isinstance(value, list):
            for child in value:
                walk(child, collecting)
        elif collecting and isinstance(value, str):
            values.append(value)

    walk(record)
    return values


def _run_cli_migration(home: Path, project: Path):
    args = type(
        "Args",
        (),
        {"project_path": str(project), "apply": True},
    )()
    records = []
    with mock.patch.object(Path, "home", return_value=home), mock.patch.object(
        migrate_config, "emit_record", side_effect=records.append
    ):
        code = migrate_config.handler(args)
    return code, records


class TestConfigurationMigration(unittest.TestCase):
    def test_legacy_first_run_preserves_semantics_attribution_and_scope(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), "legacy")
            plan = config_migration.plan_configuration_migration(
                project, home_root=home
            )
            self.assertEqual(plan.status, "planned")
            self.assertEqual(plan.compatibility_state, "legacy")
            self.assertEqual(plan.equivalence["status"], "passed")
            self.assertEqual(
                [step.kind for step in plan.steps],
                ["write-project", "write-global", "update-marker"],
            )
            result = config_migration.execute_configuration_migration(
                project, plan, home_root=home
            )
            self.assertEqual(result["status"], "complete")

            global_cfg = tomllib.loads(
                (home / ".cartopian" / "cartopian.toml").read_text()
            )
            project_cfg = tomllib.loads(
                (project / "cartopian.toml").read_text()
            )
            local_cfg = tomllib.loads(
                (project / "cartopian.local.toml").read_text()
            )
            self.assertNotIn("handoffs", global_cfg)
            self.assertNotIn("handoffs", project_cfg)
            self.assertEqual(
                global_cfg["roles"]["reviewer"]["auto_launch"],
                ["task_run"],
            )
            self.assertEqual(
                project_cfg["roles"]["reviewer"]["auto_launch"],
                ["task_run", "task_review", "planning_review"],
            )
            self.assertEqual(
                project_cfg["roles"]["coder"]["auto_launch"], ["task_run"]
            )
            self.assertEqual(
                project_cfg["project"]["project_schema_version"], "v0.7.0"
            )
            for migrated_text in (
                (home / ".cartopian" / "cartopian.toml").read_text(),
                (project / "cartopian.toml").read_text(),
            ):
                self.assertNotIn("# migrated legacy:", migrated_text)
                self.assertNotIn("[handoffs", migrated_text)
                self.assertNotRegex(
                    migrated_text, r"(?m)^\[roles\.[^.]+\.launch\]"
                )
            self.assertEqual(
                project_cfg["project"]["work_roots"], ["product"]
            )
            self.assertEqual(local_cfg["work_roots"]["product"], "/fixture/product")

            effective = plan.target_effective
            self.assertEqual(
                effective["automation"]["attribution"]["initiation"], "global"
            )
            self.assertEqual(
                effective["roles"]["coder"]["attribution"]["grants"], "project"
            )
            self.assertEqual(
                effective["work_roots_attribution"]["product"],
                {"declaration": "project", "mapping": "machine-local"},
            )
            source_semantics = dict(plan.source_effective)
            target_semantics = dict(plan.target_effective)
            source_semantics.pop("project_schema_version")
            target_semantics.pop("project_schema_version")
            self.assertEqual(source_semantics, target_semantics)
            self.assertTrue(effective["capabilities"]["activated"])
            self.assertEqual(
                effective["roles"]["coder"]["effective_grants"],
                ["read:prompts", "read:work-roots", "write:worktree"],
            )

    def test_completed_migration_and_canonical_input_are_byte_stable(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), "legacy")
            first = config_migration.plan_configuration_migration(
                project, home_root=home
            )
            config_migration.execute_configuration_migration(
                project, first, home_root=home
            )
            before = _config_bytes(home, project)
            second = config_migration.plan_configuration_migration(
                project, home_root=home
            )
            self.assertEqual(second.status, "noop")
            result = config_migration.execute_configuration_migration(
                project, second, home_root=home
            )
            self.assertEqual(result["status"], "noop")
            self.assertEqual(_config_bytes(home, project), before)

    def test_transitional_split_permissions_migrate_through_shipped_entry(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), "transitional")
            plan = config_migration.plan_configuration_migration(
                project, home_root=home
            )
            self.assertEqual(plan.status, "planned")
            self.assertEqual(plan.compatibility_state, "transitional")
            self.assertEqual(
                [entry.identity for entry in plan.entries],
                ["config-v0.5-to-v0.6", "config-v0.6-to-v0.7"],
            )
            config_migration.execute_configuration_migration(
                project, plan, home_root=home
            )
            migrated = tomllib.loads(
                (project / "cartopian.toml").read_text()
            )
            self.assertEqual(
                migrated["roles"]["coder"]["auto_launch"], ["task_run"]
            )
            self.assertNotIn("auto_launch", migrated["roles"]["manual"])
            self.assertEqual(
                migrated["roles"]["manual"]["target"],
                "cartopian-manual",
            )
            self.assertEqual(
                migrated["project"]["project_schema_version"], "v0.7.0"
            )
            self.assertFalse((project / ".cartopian").exists())

        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), "canonical")
            before = _config_bytes(home, project)
            plan = config_migration.plan_configuration_migration(
                project, home_root=home
            )
            self.assertEqual(plan.status, "noop")
            self.assertEqual(_config_bytes(home, project), before)

    def test_interruption_resumes_and_converges_on_uninterrupted_bytes(self):
        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right:
            home_a, project_a = _seed(Path(left), "legacy")
            home_b, project_b = _seed(Path(right), "legacy")
            clean_plan = config_migration.plan_configuration_migration(
                project_a, home_root=home_a
            )
            config_migration.execute_configuration_migration(
                project_a, clean_plan, home_root=home_a
            )

            interrupted_plan = config_migration.plan_configuration_migration(
                project_b, home_root=home_b
            )
            with self.assertRaises(config_migration.MigrationInterrupted):
                config_migration.execute_configuration_migration(
                    project_b,
                    interrupted_plan,
                    home_root=home_b,
                    interrupt_after_step="write-project",
                )
            partial_project = tomllib.loads(
                (project_b / "cartopian.toml").read_text()
            )
            self.assertNotIn("protocol_version", partial_project["project"])
            self.assertEqual(
                partial_project["project"]["project_schema_version"], "v0.4.0"
            )
            evidence = json.loads(
                (project_b / ".cartopian" / "config-migration.json").read_text()
            )
            self.assertEqual(evidence["status"], "in-progress")
            self.assertEqual(
                [item["id"] for item in evidence["completed_steps"]],
                ["write-project"],
            )
            self.assertTrue(
                all(
                    "input_identity" in item and "output_identity" in item
                    for item in evidence["completed_steps"]
                )
            )
            resumed = config_migration.plan_configuration_migration(
                project_b, home_root=home_b
            )
            self.assertEqual(resumed.compatibility_state, "partial")
            config_migration.execute_configuration_migration(
                project_b, resumed, home_root=home_b
            )
            self.assertEqual(
                _config_bytes(home_a, project_a),
                _config_bytes(home_b, project_b),
            )
            self.assertFalse(
                (project_b / ".cartopian" / "config-migration.json").exists()
            )
            self.assertFalse((project_b / ".cartopian").exists())

    def test_stale_checkpoint_is_diagnosed_and_safely_replaced(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), "transitional")
            evidence_dir = project / ".cartopian"
            evidence_dir.mkdir()
            evidence_path = evidence_dir / "config-migration.json"
            evidence_path.write_text('{"schema_identity":"wrong"}\n')
            plan = config_migration.plan_configuration_migration(
                project, home_root=home
            )
            self.assertEqual(plan.status, "planned")
            self.assertEqual(plan.checkpoint_state, "stale")
            config_migration.execute_configuration_migration(
                project, plan, home_root=home
            )
            self.assertFalse(evidence_path.exists())

    def test_global_migration_stops_before_narrowing_other_projects(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            home, project = _seed(root, "scope-ownership")
            other = root / "other-project"
            other.mkdir()
            shutil.copyfile(
                FIXTURES / "scope-ownership" / "other-project.toml",
                other / "cartopian.toml",
            )
            registry_path = home / ".cartopian" / "projects.json"
            registry = json.loads(registry_path.read_text())
            registry.append({"id": "other-project", "path": str(other)})
            registry_path.write_text(json.dumps(registry))
            bytes_before = {
                "global": (home / ".cartopian" / "cartopian.toml").read_bytes(),
                "project": (project / "cartopian.toml").read_bytes(),
                "other": (other / "cartopian.toml").read_bytes(),
            }
            other_source_plan = config_migration.plan_configuration_migration(
                other,
                home_root=home,
                _validate_global_inventory=False,
            )
            other_before = other_source_plan.source_effective
            self.assertEqual(
                other_before["roles"]["reviewer"]["auto_launch"],
                ["task_run", "task_review", "planning_review"],
            )
            self.assertEqual(
                other_before["roles"]["reviewer"]["attribution"]["auto_launch"],
                "project",
            )
            plan = config_migration.plan_configuration_migration(
                project, home_root=home
            )
            self.assertEqual(plan.status, "pending")
            self.assertEqual(
                plan.diagnostics[0]["code"],
                "cross-project-semantic-change",
            )
            self.assertEqual(
                plan.diagnostics[0]["affected_projects"],
                [
                    {
                        "id": "other-project",
                        "changed_activities": [
                            "task_run",
                            "task_review",
                            "planning_review",
                        ],
                    }
                ],
            )
            with self.assertRaises(config_migration.MigrationRefused):
                config_migration.execute_configuration_migration(
                    project, plan, home_root=home
                )
            rerun = config_migration.plan_configuration_migration(
                project, home_root=home
            )
            self.assertEqual(
                rerun.diagnostics, plan.diagnostics
            )
            self.assertEqual(
                (home / ".cartopian" / "cartopian.toml").read_bytes(),
                bytes_before["global"],
            )
            self.assertEqual(
                (project / "cartopian.toml").read_bytes(),
                bytes_before["project"],
            )
            self.assertEqual(
                (other / "cartopian.toml").read_bytes(),
                bytes_before["other"],
            )

            other_attempt = config_migration.plan_configuration_migration(
                other, home_root=home
            )
            self.assertEqual(other_attempt.status, "pending")
            with self.assertRaises(config_migration.MigrationRefused):
                config_migration.execute_configuration_migration(
                    other, other_attempt, home_root=home
                )
            other_after = config_migration.plan_configuration_migration(
                other,
                home_root=home,
                _validate_global_inventory=False,
            ).source_effective
            self.assertEqual(other_after, other_before)

            later = root / "later-project"
            later.mkdir()
            shutil.copyfile(
                FIXTURES / "scope-ownership" / "later-project.toml",
                later / "cartopian.toml",
            )
            later_before = config_migration.plan_configuration_migration(
                later,
                home_root=home,
                _validate_global_inventory=False,
            ).source_effective
            self.assertEqual(
                later_before["roles"]["reviewer"]["auto_launch"],
                ["task_run", "task_review", "planning_review"],
            )
            registry.append({"id": "later-project", "path": str(later)})
            registry_path.write_text(json.dumps(registry))
            later_attempt = config_migration.plan_configuration_migration(
                later, home_root=home
            )
            self.assertEqual(later_attempt.status, "pending")
            self.assertEqual(
                config_migration.plan_configuration_migration(
                    later,
                    home_root=home,
                    _validate_global_inventory=False,
                ).source_effective["roles"]["reviewer"]["auto_launch"],
                ["task_run", "task_review", "planning_review"],
            )

    def test_unresolvable_registry_entry_blocks_global_impact_analysis(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            home, project = _seed(root, "scope-ownership")
            registry_path = home / ".cartopian" / "projects.json"
            registry = json.loads(registry_path.read_text())
            registry.append(
                {"id": "missing-project", "path": str(root / "missing")}
            )
            registry_path.write_text(json.dumps(registry))
            plan = config_migration.plan_configuration_migration(
                project, home_root=home
            )
            self.assertEqual(plan.status, "refused")
            self.assertEqual(
                plan.diagnostics[0]["code"], "registry-coverage-incomplete"
            )
            self.assertEqual(
                plan.diagnostics[0]["scope"],
                "registered-project:missing-project",
            )

    def test_invalid_registered_project_reports_its_own_diagnostic(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            home, project = _seed(root, "scope-ownership")
            other = root / "broken-project"
            other.mkdir()
            (other / "cartopian.toml").write_text(
                """
[project]
id = "broken-project"
name = "Broken Project"
project_schema_version = "not-a-version"
""".lstrip()
            )
            registry_path = home / ".cartopian" / "projects.json"
            registry = json.loads(registry_path.read_text())
            registry.append({"id": "broken-project", "path": str(other)})
            registry_path.write_text(json.dumps(registry))
            before = _config_bytes(home, project)
            plan = config_migration.plan_configuration_migration(
                project, home_root=home
            )
            self.assertEqual(plan.status, "refused")
            self.assertEqual(plan.diagnostics[0]["code"], "malformed-marker")
            self.assertEqual(
                plan.diagnostics[0]["scope"],
                "registered-project:broken-project",
            )
            self.assertIn("broken-project", plan.diagnostics[0]["message"])
            self.assertEqual(_config_bytes(home, project), before)

    def test_semantic_gate_uses_independent_compatibility_reader(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), "transitional")
            with mock.patch.object(
                config_migration,
                "_target_permission_activities",
                return_value=(),
            ):
                plan = config_migration.plan_configuration_migration(
                    project, home_root=home
                )
            self.assertEqual(plan.status, "refused")
            self.assertEqual(plan.diagnostics[0]["code"], "semantic-drift")

    def test_compatibility_permission_mapping_is_independent_when_planning_is_false(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), "annotated")
            with mock.patch.object(
                config_migration,
                "_permission_flags",
                return_value=(True, True),
            ):
                plan = config_migration.plan_configuration_migration(
                    project, home_root=home
                )
            self.assertEqual(plan.status, "refused")
            self.assertEqual(plan.diagnostics[0]["code"], "semantic-drift")

    def test_empty_legacy_handoff_table_is_removed_as_a_real_change(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), "empty-handoffs")
            plan = config_migration.plan_configuration_migration(
                project, home_root=home
            )
            self.assertEqual(plan.status, "planned")
            self.assertEqual(
                [step.kind for step in plan.steps],
                ["write-project", "update-marker"],
            )
            config_migration.execute_configuration_migration(
                project, plan, home_root=home
            )
            migrated = tomllib.loads(
                (project / "cartopian.toml").read_text()
            )
            self.assertNotIn("handoffs", migrated)
            self.assertEqual(
                config_migration.plan_configuration_migration(
                    project, home_root=home
                ).status,
                "noop",
            )

    def test_public_migration_omits_empty_roles_parent_and_rerun_is_byte_stable(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), "empty-handoffs")
            path = project / "cartopian.toml"
            path.write_text(
                """
[project]
id = "migration-fixture"
name = "Migration Fixture"
project_schema_version = "v0.6.0"

[roles]

[handoffs]
""".lstrip()
            )

            code, records = _run_cli_migration(home, project)
            self.assertEqual(code, 0)
            self.assertEqual(records[0]["details"]["result"]["status"], "complete")
            migrated = path.read_text()
            self.assertNotRegex(migrated, r"(?m)^\s*\[roles\]\s*$")
            self.assertNotIn("roles", tomllib.loads(migrated))

            before_rerun = path.read_bytes()
            code, records = _run_cli_migration(home, project)
            self.assertEqual(code, 0)
            self.assertEqual(records[0]["details"]["plan"]["status"], "noop")
            self.assertEqual(records[0]["details"]["result"]["status"], "noop")
            self.assertEqual(path.read_bytes(), before_rerun)

    def test_public_migration_preserves_nonempty_roles_table_in_meaning(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), "empty-handoffs")
            path = project / "cartopian.toml"
            path.write_text(
                """
[project]
id = "migration-fixture"
name = "Migration Fixture"
project_schema_version = "v0.6.0"

[roles.reviewer]
description = "Reviews completed work."
grants = ["reviewer-like"]

[handoffs]
""".lstrip()
            )
            expected_role = tomllib.loads(path.read_text())["roles"]["reviewer"]

            code, records = _run_cli_migration(home, project)
            self.assertEqual(code, 0)
            self.assertEqual(records[0]["details"]["result"]["status"], "complete")
            migrated = tomllib.loads(path.read_text())
            self.assertEqual(migrated["roles"]["reviewer"], expected_role)

    def test_public_v060_migration_flattens_role_launch_and_is_byte_stable(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), "canonical")
            path = project / "cartopian.toml"
            path.write_text(
                """
# Unrelated operator heading remains.
[project]
id = "migration-fixture"
name = "Migration Fixture"
project_schema_version = "v0.6.0"
work_roots = ["product"]

[roles.coder]
description = "Implements approved work."
grants = ["coder-like"]
auto_launch = ["task_run"]

# Retired launch grouping disappears with its table.
[roles.coder.launch] # retired header note
target = "cartopian-codex" # target attribution note
model = "example-model"
effort = "high"
timeout = "45m"
""".lstrip()
            )

            plan = config_migration.plan_configuration_migration(
                project, home_root=home
            )
            self.assertEqual(plan.status, "planned")
            self.assertEqual(
                [entry.identity for entry in plan.entries],
                ["config-v0.6-to-v0.7"],
            )
            source = dict(plan.source_effective)
            target = dict(plan.target_effective)
            source.pop("project_schema_version")
            target.pop("project_schema_version")
            self.assertEqual(source, target)

            code, records = _run_cli_migration(home, project)
            self.assertEqual(code, 0)
            self.assertEqual(records[0]["details"]["result"]["status"], "complete")
            migrated_text = path.read_text()
            migrated = tomllib.loads(migrated_text)
            coder = migrated["roles"]["coder"]
            self.assertEqual(
                {
                    key: coder[key]
                    for key in ("target", "model", "effort", "timeout")
                },
                {
                    "target": "cartopian-codex",
                    "model": "example-model",
                    "effort": "high",
                    "timeout": "45m",
                },
            )
            self.assertNotIn("launch", coder)
            self.assertEqual(migrated_text.count("[roles.coder]"), 1)
            self.assertNotIn("[roles.coder.launch]", migrated_text)
            self.assertNotIn("# retired header note", migrated_text)
            self.assertNotIn("# Retired launch grouping", migrated_text)
            self.assertIn("# target attribution note", migrated_text)
            self.assertIn("# Unrelated operator heading remains.", migrated_text)
            self.assertNotIn("# migrated legacy:", migrated_text)
            self.assertEqual(
                migrated["project"]["project_schema_version"], "v0.7.0"
            )

            before_rerun = path.read_bytes()
            code, records = _run_cli_migration(home, project)
            self.assertEqual(code, 0)
            self.assertEqual(records[0]["details"]["plan"]["status"], "noop")
            self.assertEqual(records[0]["details"]["result"]["status"], "noop")
            self.assertEqual(path.read_bytes(), before_rerun)

    def test_current_marker_repairs_preexisting_generated_tombstones(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), "canonical")
            global_path = home / ".cartopian" / "cartopian.toml"
            project_path = project / "cartopian.toml"
            global_path.write_text(
                global_path.read_text()
                + "\n# migrated legacy: [handoffs.reviewer]\n"
                + '# migrated legacy: agent = "cartopian-review"\n'
            )
            project_path.write_text(
                project_path.read_text()
                + "\n# migrated legacy: [roles.coder.launch]\n"
                + '# migrated legacy: target = "cartopian-codex"\n'
            )

            code, records = _run_cli_migration(home, project)
            self.assertEqual(code, 0)
            self.assertEqual(records[0]["details"]["result"]["status"], "complete")
            for path in (global_path, project_path):
                self.assertNotIn("# migrated legacy:", path.read_text())
            self.assertEqual(
                records[0]["details"]["plan"]["entries"][0]["identity"],
                "config-v0.7-partial-repair",
            )

            before_rerun = _config_bytes(home, project)
            code, records = _run_cli_migration(home, project)
            self.assertEqual(code, 0)
            self.assertEqual(records[0]["details"]["plan"]["status"], "noop")
            self.assertEqual(_config_bytes(home, project), before_rerun)

    def test_current_marker_global_nested_launch_metadata_matches_public_apply(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), "canonical")
            global_path = home / ".cartopian" / "cartopian.toml"
            global_path.write_text(
                """
[automation]
initiation = "operator"

[roles.reviewer]
description = "Reviews completed work."
grants = ["reviewer-like"]

[roles.reviewer.launch]
target = "cartopian-review"
timeout = "30m"
""".lstrip()
            )

            code, records = _run_cli_migration(home, project)
            self.assertEqual(code, 0)
            public_plan = records[0]["details"]["plan"]
            public_result = records[0]["details"]["result"]
            self.assertEqual(public_plan["status"], "planned")
            self.assertEqual(public_result["status"], "complete")
            self.assertEqual(
                public_plan["entries"][0]["identity"],
                "config-v0.7-partial-repair",
            )
            self.assertIn(
                "superseded-role-launch",
                public_plan["entries"][0]["supported_forms"],
            )
            self.assertIn(
                "flatten-role-launch-fields",
                public_plan["entries"][0]["transforms"],
            )
            nested_facts = [
                fact
                for fact in public_plan["source_facts"]
                if fact["scope"] == "global"
                and fact["field"].startswith("roles.reviewer.launch.")
            ]
            self.assertTrue(nested_facts)
            self.assertEqual(
                {fact["form"] for fact in nested_facts},
                {"superseded-role-launch"},
            )
            self.assertIn(
                nested_facts[0]["form"],
                public_plan["entries"][0]["supported_forms"],
            )
            migrated_text = global_path.read_text()
            migrated = tomllib.loads(migrated_text)
            self.assertEqual(
                migrated["roles"]["reviewer"]["target"],
                "cartopian-review",
            )
            self.assertEqual(migrated["roles"]["reviewer"]["timeout"], "30m")
            self.assertNotIn("launch", migrated["roles"]["reviewer"])
            self.assertNotIn("[roles.reviewer.launch]", migrated_text)

            before_rerun = _config_bytes(home, project)
            code, records = _run_cli_migration(home, project)
            self.assertEqual(code, 0)
            self.assertEqual(records[0]["details"]["plan"]["status"], "noop")
            self.assertEqual(records[0]["details"]["result"]["status"], "noop")
            self.assertEqual(_config_bytes(home, project), before_rerun)

    def test_empty_roles_parent_removal_keeps_surviving_role_subtable(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), "canonical")
            path = project / "cartopian.toml"
            path.write_text(
                """
[project]
id = "migration-fixture"
name = "Migration Fixture"
project_schema_version = "v0.6.0"
work_roots = ["product"]

[roles]

[roles.reviewer]
description = "Reviews completed work."
grants = ["reviewer-like"]
target = "cartopian-review"
""".lstrip()
            )
            code, records = _run_cli_migration(home, project)
            self.assertEqual(code, 0)
            self.assertEqual(records[0]["details"]["result"]["status"], "complete")
            migrated_text = path.read_text()
            migrated = tomllib.loads(migrated_text)
            self.assertNotRegex(migrated_text, r"(?m)^\s*\[roles\]\s*$")
            self.assertEqual(
                migrated["roles"]["reviewer"]["target"], "cartopian-review"
            )

    def test_annotated_global_and_project_files_preserve_comments_and_order(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), "annotated")
            fixture_text = (
                FIXTURES / "annotated" / "project.toml"
            ).read_text()
            self.assertIn(
                "# Retired handoff grouping and its attached comments "
                "are removed during migration.",
                fixture_text,
            )
            plan = config_migration.plan_configuration_migration(
                project, home_root=home
            )
            self.assertEqual(plan.status, "planned")
            config_migration.execute_configuration_migration(
                project, plan, home_root=home
            )
            global_text = (
                home / ".cartopian" / "cartopian.toml"
            ).read_text()
            project_text = (project / "cartopian.toml").read_text()
            for comment in (
                "# Operator global configuration. Keep this heading.",
                "# Starts only after the operator's explicit global opt-in.",
                "# deliberate",
                "# Review role grouping.",
                "# role note",
                "# Reference only: auto_launch = [\"task_run\"]",
                "# target note",
                "# task permission note",
            ):
                self.assertIn(comment, global_text)
            self.assertNotIn("# Legacy launch grouping", global_text)
            self.assertNotIn("# planning intentionally manual", global_text)
            for comment in (
                "# Project configuration maintained by the operator.",
                "# stable identity",
                "# marker note",
                "# Reference only: work_roots = [\"product\"]",
                "# Lightweight documentation role.",
                "# writer note",
                "# coder note",
                "# coder target",
                "# tasks start automatically",
                "[reviews] # review policy",
            ):
                self.assertIn(comment, project_text)
            self.assertNotIn("# Retired handoff grouping", project_text)
            self.assertNotIn("# reviews remain manual", project_text)
            self.assertNotIn("# migrated legacy:", global_text)
            self.assertNotIn("# migrated legacy:", project_text)
            self.assertNotIn("\n\n\n", global_text)
            self.assertNotIn("\n\n\n", project_text)
            self.assertLess(
                global_text.index("[automation]"),
                global_text.index("[roles.reviewer]"),
            )
            self.assertLess(
                project_text.index('id = "migration-fixture"'),
                project_text.index('name = "Migration Fixture"'),
            )
            before_rerun = _config_bytes(home, project)
            rerun_plan = config_migration.plan_configuration_migration(
                project, home_root=home
            )
            self.assertEqual(rerun_plan.status, "noop")
            rerun_result = config_migration.execute_configuration_migration(
                project, rerun_plan, home_root=home
            )
            self.assertEqual(rerun_result["status"], "noop")
            self.assertEqual(_config_bytes(home, project), before_rerun)
            self.assertLess(
                project_text.index('name = "Migration Fixture"'),
                project_text.index("project_schema_version"),
            )
            self.assertLess(
                global_text.index('auto_launch = ["task_run"]'),
                global_text.index('target = "cartopian-review"'),
            )
            self.assertLess(
                project_text.index('auto_launch = ["task_run"]'),
                project_text.index('target = "cartopian-codex"'),
            )
            self.assertEqual(
                config_migration.plan_configuration_migration(
                    project, home_root=home
                ).status,
                "noop",
            )

    def test_migration_omits_default_empty_permission_and_preserves_existing_evidence_dir(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), "canonical")
            (project / "cartopian.toml").write_text(
                """
[project]
id = "migration-fixture"
name = "Migration Fixture"
project_schema_version = "v0.6.0"
work_roots = ["product"]

[roles.reviewer]
description = "Reviews work."

[handoffs.reviewer]
agent = "cartopian-review"
auto_start = false
planning_reviews = false
""".lstrip()
            )
            evidence_dir = project / ".cartopian"
            evidence_dir.mkdir()
            plan = config_migration.plan_configuration_migration(
                project, home_root=home
            )
            self.assertEqual(plan.status, "planned")
            config_migration.execute_configuration_migration(
                project, plan, home_root=home
            )
            migrated = tomllib.loads(
                (project / "cartopian.toml").read_text()
            )
            self.assertNotIn(
                "auto_launch", migrated["roles"]["reviewer"]
            )
            self.assertTrue(evidence_dir.is_dir())
            self.assertEqual(list(evidence_dir.iterdir()), [])

    def test_split_task_launch_plus_legacy_planning_review_is_pending(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), "split-planning")
            before = _config_bytes(home, project)
            plan = config_migration.plan_configuration_migration(
                project, home_root=home
            )
            self.assertEqual(plan.status, "pending")
            self.assertEqual(
                plan.diagnostics[0]["classification"],
                "pending-operator-decision",
            )
            self.assertEqual(
                plan.diagnostics[0]["code"],
                "ambiguous-planning-review-permission",
            )
            self.assertEqual(_config_bytes(home, project), before)

    def test_migration_authorities_are_cross_validated_and_dead_aliases_are_absent(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), "canonical")
            plan = config_migration.plan_configuration_migration(
                project, home_root=home
            )
            authority = plan.as_record()["migration_authority"]
            self.assertEqual(
                authority["configuration"],
                "cli.config_migration.CONFIGURATION_MIGRATION_ENTRIES",
            )
            self.assertEqual(authority["cross_validation"], "passed")
            with mock.patch(
                "cli.migrations.ENTRY_VERSIONS",
                ("v0.2.0", "v0.3.0", "v9.0.0"),
            ):
                divergent = config_migration.plan_configuration_migration(
                    project, home_root=home
                )
            self.assertEqual(divergent.status, "refused")
            self.assertEqual(
                divergent.diagnostics[0]["code"],
                "migration-authority-divergence",
            )
            self.assertEqual(
                divergent.as_record()["migration_authority"][
                    "cross_validation"
                ],
                "failed",
            )
        for name in (
            "MIGRATION_ENTRIES",
            "plan_migration",
            "execute_migration",
            "migrate_configuration",
        ):
            self.assertFalse(hasattr(config_migration, name), name)

    def test_conflict_malformed_newer_and_unknown_grant_refuse_without_writes(self):
        for case, code, status in (
            ("conflict", "conflicting-definition", "pending"),
            ("malformed", "malformed-marker", "refused"),
            ("newer", "newer-marker", "refused"),
        ):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as raw:
                home, project = _seed(Path(raw), case)
                before = _config_bytes(home, project)
                plan = config_migration.plan_configuration_migration(
                    project, home_root=home
                )
                self.assertEqual(plan.status, status)
                self.assertEqual(plan.diagnostics[0]["code"], code)
                with self.assertRaises(config_migration.MigrationRefused):
                    config_migration.execute_configuration_migration(
                        project, plan, home_root=home
                    )
                self.assertEqual(_config_bytes(home, project), before)

        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), "unknown-grant")
            before = _config_bytes(home, project)
            plan = config_migration.plan_configuration_migration(
                project, home_root=home
            )
            self.assertEqual(plan.status, "refused")
            self.assertEqual(plan.diagnostics[0]["code"], "unknown-grant")
            self.assertEqual(_config_bytes(home, project), before)

    def test_implicit_pre_v050_review_is_migrated_only_when_deterministic(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), "canonical")
            path = project / "cartopian.toml"
            path.write_text(
                """
[project]
id = "migration-fixture"
name = "Migration Fixture"
project_schema_version = "v0.4.0"
work_roots = ["product"]

[roles]
reviewer = "Reviews plans and tasks."
""".lstrip()
            )
            plan = config_migration.plan_configuration_migration(
                project, home_root=home
            )
            self.assertEqual(plan.status, "planned")
            self.assertIn(
                {
                    "field": "reviews.planning",
                    "before": "legacy-pre-v0.5",
                    "after": "project",
                },
                plan.attribution_changes,
            )
            config_migration.execute_configuration_migration(
                project, plan, home_root=home
            )
            migrated = tomllib.loads(path.read_text())
            self.assertEqual(migrated["reviews"]["planning"], "required")
            self.assertEqual(migrated["reviews"]["planning_role"], "reviewer")
            self.assertEqual(migrated["reviews"]["task_closure"], "required")
            self.assertEqual(migrated["reviews"]["task_role"], "reviewer")
            allowed = set(config_schema.ATTRIBUTION_VALUES)
            self.assertTrue(
                set(_attribution_strings(plan.source_effective)) <= allowed
            )
            self.assertTrue(
                set(_attribution_strings(plan.target_effective)) <= allowed
            )

    def test_cli_and_mcp_use_the_same_handler_contract(self):
        with tempfile.TemporaryDirectory() as raw:
            home, project = _seed(Path(raw), "legacy")
            args = type(
                "Args",
                (),
                {"project_path": str(project), "apply": False},
            )()
            cli_records = []
            with mock.patch.object(Path, "home", return_value=home), mock.patch.object(
                migrate_config, "emit_record", side_effect=cli_records.append
            ):
                cli_code = migrate_config.handler(args)
            self.assertEqual(cli_code, 0)

            server._TOOL_CACHE = None
            with mock.patch.object(Path, "home", return_value=home):
                mcp_result = server.call_tool(
                    "migrate_config",
                    {"project_path": str(project)},
                )
            self.assertFalse(mcp_result["isError"])
            mcp_record = mcp_result["structuredContent"]["records"][0]
            self.assertEqual(mcp_record, cli_records[0])
            self.assertEqual(mcp_record["action"], "migrate-config")

        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right:
            home_a, project_a = _seed(Path(left), "transitional")
            home_b, project_b = _seed(Path(right), "transitional")
            args = type(
                "Args",
                (),
                {"project_path": str(project_a), "apply": True},
            )()
            cli_records = []
            with mock.patch.object(Path, "home", return_value=home_a), mock.patch.object(
                migrate_config, "emit_record", side_effect=cli_records.append
            ):
                cli_code = migrate_config.handler(args)
            self.assertEqual(cli_code, 0)
            server._TOOL_CACHE = None
            with mock.patch.object(Path, "home", return_value=home_b):
                mcp_result = server.call_tool(
                    "migrate_config",
                    {"project_path": str(project_b), "apply": True},
                )
            self.assertFalse(mcp_result["isError"])
            self.assertEqual(
                mcp_result["structuredContent"]["records"][0],
                cli_records[0],
            )
            self.assertEqual(
                cli_records[0]["details"]["result"]["status"], "complete"
            )

        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right:
            home_a, project_a = _seed(Path(left), "conflict")
            home_b, project_b = _seed(Path(right), "conflict")
            args = type(
                "Args",
                (),
                {"project_path": str(project_a), "apply": False},
            )()
            cli_records = []
            with mock.patch.object(Path, "home", return_value=home_a), mock.patch.object(
                migrate_config, "emit_record", side_effect=cli_records.append
            ):
                cli_code = migrate_config.handler(args)
            self.assertEqual(cli_code, 1)
            server._TOOL_CACHE = None
            with mock.patch.object(Path, "home", return_value=home_b):
                mcp_result = server.call_tool(
                    "migrate_config",
                    {"project_path": str(project_b)},
                )
            self.assertTrue(mcp_result["isError"])
            self.assertEqual(
                mcp_result["structuredContent"]["records"][0],
                cli_records[0],
            )
            self.assertEqual(
                cli_records[0]["details"]["plan"]["status"], "pending"
            )


if __name__ == "__main__":
    unittest.main()
