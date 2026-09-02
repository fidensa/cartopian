"""Contract tests for the closed ``automation.run_boundary`` contract.

The operator-approved contract (DEC-073 / SPEC-06-004) replaces the old
``confirmation`` pacing setting with an explicit, closed statement of the unit
that ends an initiated run:

- ``handoff-complete`` — the run ends after one handoff reaches a terminal
  publication outcome.
- ``handoff-budget`` — the run continues through sequential handoffs until the
  required positive ``max_handoffs_per_run`` budget is exhausted.
- ``task-complete`` — the run binds one task at initiation and continues every
  configured and authorized assignment or lifecycle activity that same task
  needs to reach ``done``, then returns control before acting on another task.

These tests cover the executable half (schema, resolution, migration, effective
output) and the prose half (protocol and skill statements that own PM
orchestration behavior), because run continuation is decided by the PM reading
those surfaces, not by a Python loop.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from cli import config_migration
from cli.config_schema import (
    CONFIG_SCHEMA,
    ConfigDiagnostic,
    resolve_configuration,
    validate_authored_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"
CONVENTIONS = REPO_ROOT / "protocol" / "CONVENTIONS.md"
CHANGELOG = REPO_ROOT / "protocol" / "CHANGELOG.md"
SKILLS = REPO_ROOT / "skills"
GLOBAL_TEMPLATE = REPO_ROOT / "templates" / "global.cartopian.toml"

RUN_BOUNDARY_VALUES = ("handoff-complete", "handoff-budget", "task-complete")

_PROJECT_TABLE = (
    "[project]\n"
    'id = "demo"\n'
    'name = "Demo"\n'
    'project_schema_version = "v0.9.0"\n'
)


def _project(automation: str = "") -> dict:
    config = {
        "project": {
            "id": "demo",
            "name": "Demo",
            "project_schema_version": "v0.9.0",
        }
    }
    if automation:
        config["automation"] = json.loads(automation)
    return config


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _task_complete_section(text: str) -> str:
    """The `task-complete` subsection body, up to the next heading."""
    start = text.index("#### The `task-complete` run unit")
    rest = text[start + len("#### The `task-complete` run unit") :]
    end = rest.find("\n#")
    return rest if end == -1 else rest[:end]


class TestRunBoundarySchemaDomain(unittest.TestCase):
    """The closed domain and its conditional budget are schema-owned."""

    def test_schema_declares_the_closed_run_boundary_domain(self) -> None:
        field = CONFIG_SCHEMA["fields"]["automation.run_boundary"]
        self.assertEqual(field["values"], RUN_BOUNDARY_VALUES)
        self.assertEqual(field["default"], "handoff-complete")
        self.assertEqual(field["scopes"], ("global", "project"))

    def test_confirmation_is_no_longer_an_authored_field(self) -> None:
        self.assertNotIn("automation.confirmation", CONFIG_SCHEMA["fields"])

    def test_budget_field_declares_its_conditional_requirement(self) -> None:
        field = CONFIG_SCHEMA["fields"]["automation.max_handoffs_per_run"]
        self.assertEqual(field["type"], "positive-integer")
        self.assertEqual(field["required_when"], 'automation.run_boundary = "handoff-budget"')
        self.assertNotIn("default", field)

    def test_every_closed_value_is_accepted_authored(self) -> None:
        for value in RUN_BOUNDARY_VALUES:
            with self.subTest(value=value):
                automation = {"run_boundary": value}
                if value == "handoff-budget":
                    automation["max_handoffs_per_run"] = 3
                validate_authored_config(
                    {"automation": automation}, "global"
                )

    def test_value_outside_the_closed_domain_fails_closed(self) -> None:
        with self.assertRaises(ConfigDiagnostic) as caught:
            validate_authored_config(
                {"automation": {"run_boundary": "until-blocked"}}, "global"
            )
        self.assertEqual(caught.exception.code, "unknown-value")
        self.assertEqual(caught.exception.field, "automation.run_boundary")
        self.assertIn("handoff-complete", caught.exception.message)
        self.assertIn("handoff-budget", caught.exception.message)
        self.assertIn("task-complete", caught.exception.message)


class TestLegacyConfirmationIsMigrationSourceOnly(unittest.TestCase):
    def test_authored_confirmation_is_rejected_with_a_migration_recovery(
        self,
    ) -> None:
        with self.assertRaises(ConfigDiagnostic) as caught:
            validate_authored_config(
                {"automation": {"confirmation": "each-handoff"}}, "global"
            )
        self.assertEqual(caught.exception.code, "migration-source-only")
        self.assertEqual(caught.exception.field, "automation.confirmation")
        self.assertEqual(caught.exception.recovery, "run-approved-config-migration")

    def test_legacy_inventory_owns_the_retired_path(self) -> None:
        self.assertIn(
            "automation.confirmation",
            CONFIG_SCHEMA["legacy_vocabulary"]["authored_config_paths"],
        )


class TestConditionalHandoffBudget(unittest.TestCase):
    def _resolve(self, global_automation=None, project_automation=None):
        global_cfg = (
            {"automation": global_automation} if global_automation else {}
        )
        project_cfg = _project()
        if project_automation:
            project_cfg["automation"] = project_automation
        return resolve_configuration(global_cfg, project_cfg, {})

    def test_budget_is_required_under_handoff_budget(self) -> None:
        with self.assertRaises(ConfigDiagnostic) as caught:
            self._resolve(project_automation={"run_boundary": "handoff-budget"})
        self.assertEqual(caught.exception.code, "missing-required")
        self.assertEqual(
            caught.exception.field, "automation.max_handoffs_per_run"
        )
        self.assertEqual(caught.exception.scope, "resolved")

    def test_budget_is_rejected_beside_a_non_budget_boundary_in_one_scope(
        self,
    ) -> None:
        for scope in ("global", "project"):
            for boundary in ("handoff-complete", "task-complete"):
                with self.subTest(scope=scope, boundary=boundary):
                    config = _project() if scope == "project" else {}
                    config["automation"] = {
                        "run_boundary": boundary,
                        "max_handoffs_per_run": 2,
                    }
                    with self.assertRaises(ConfigDiagnostic) as caught:
                        validate_authored_config(config, scope)
                    self.assertEqual(
                        caught.exception.code, "invalid-combination"
                    )
                    self.assertEqual(
                        caught.exception.field,
                        "automation.max_handoffs_per_run",
                    )
                    self.assertEqual(
                        caught.exception.recovery,
                        "remove-budget-or-select-handoff-budget",
                    )

    def test_budget_is_rejected_against_the_resolved_boundary(self) -> None:
        # The budget and the boundary may be authored in different scopes; the
        # merged record is the authority that rejects the combination.
        with self.assertRaises(ConfigDiagnostic) as caught:
            self._resolve(
                global_automation={"max_handoffs_per_run": 2},
                project_automation={"run_boundary": "task-complete"},
            )
        self.assertEqual(caught.exception.code, "invalid-combination")
        self.assertEqual(caught.exception.scope, "resolved")

    def test_budget_defaults_reject_a_lone_budget(self) -> None:
        # No boundary authored anywhere resolves to handoff-complete, which
        # accepts no budget.
        with self.assertRaises(ConfigDiagnostic) as caught:
            self._resolve(project_automation={"max_handoffs_per_run": 2})
        self.assertEqual(caught.exception.code, "invalid-combination")

    def test_non_positive_and_non_integer_budgets_fail_closed(self) -> None:
        for value in (0, -1, True, "3", 2.5):
            with self.subTest(value=value):
                with self.assertRaises(ConfigDiagnostic) as caught:
                    validate_authored_config(
                        {
                            "automation": {
                                "run_boundary": "handoff-budget",
                                "max_handoffs_per_run": value,
                            }
                        },
                        "global",
                    )
                self.assertEqual(caught.exception.code, "invalid-type")


class TestResolvedAutomationRecord(unittest.TestCase):
    def test_protocol_default_is_handoff_complete_with_no_budget(self) -> None:
        record = resolve_configuration({}, _project(), {})
        self.assertEqual(
            record["automation"],
            {
                "initiation": "operator",
                "run_boundary": "handoff-complete",
                "max_handoffs_per_run": None,
                "attribution": {
                    "initiation": "protocol-default",
                    "run_boundary": "protocol-default",
                    "max_handoffs_per_run": None,
                },
            },
        )

    def test_handoff_budget_reports_value_and_attribution(self) -> None:
        project_cfg = _project()
        project_cfg["automation"] = {"max_handoffs_per_run": 5}
        record = resolve_configuration(
            {"automation": {"run_boundary": "handoff-budget"}},
            project_cfg,
            {},
        )
        self.assertEqual(
            record["automation"],
            {
                "initiation": "operator",
                "run_boundary": "handoff-budget",
                "max_handoffs_per_run": 5,
                "attribution": {
                    "initiation": "protocol-default",
                    "run_boundary": "global",
                    "max_handoffs_per_run": "project",
                },
            },
        )

    def test_task_complete_resolves_without_a_budget(self) -> None:
        project_cfg = _project()
        project_cfg["automation"] = {
            "initiation": "auto",
            "run_boundary": "task-complete",
        }
        record = resolve_configuration({}, project_cfg, {})
        self.assertEqual(
            record["automation"],
            {
                "initiation": "auto",
                "run_boundary": "task-complete",
                "max_handoffs_per_run": None,
                "attribution": {
                    "initiation": "project",
                    "run_boundary": "project",
                    "max_handoffs_per_run": None,
                },
            },
        )


class TestEffectiveConfigParity(unittest.TestCase):
    """``resolve-config`` reports exactly the resolved contract."""

    def _resolve_config(self, project_toml: str, global_toml: str = "") -> dict:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            home = root / "home"
            project = root / "proj"
            home.mkdir()
            project.mkdir()
            if global_toml:
                (home / ".cartopian").mkdir(parents=True)
                (home / ".cartopian" / "cartopian.toml").write_text(
                    global_toml, encoding="utf-8"
                )
            (project / "cartopian.toml").write_text(
                project_toml, encoding="utf-8"
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(ENTRYPOINT),
                    "resolve-config",
                    str(project),
                ],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                env={"HOME": str(home), "PATH": os.environ.get("PATH", "")},
            )
        return result

    def test_task_complete_is_reported_end_to_end(self) -> None:
        result = self._resolve_config(
            _PROJECT_TABLE + '\n[automation]\nrun_boundary = "task-complete"\n'
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(
            record["automation"],
            {
                "initiation": "operator",
                "run_boundary": "task-complete",
                "max_handoffs_per_run": None,
                "attribution": {
                    "initiation": "protocol-default",
                    "run_boundary": "project",
                    "max_handoffs_per_run": None,
                },
            },
        )

    def test_invalid_boundary_fails_closed_at_the_cli(self) -> None:
        result = self._resolve_config(
            _PROJECT_TABLE + '\n[automation]\nrun_boundary = "forever"\n'
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("automation.run_boundary", result.stderr)

    def test_budget_outside_handoff_budget_fails_closed_at_the_cli(self) -> None:
        result = self._resolve_config(
            _PROJECT_TABLE
            + '\n[automation]\nrun_boundary = "task-complete"\n'
            "max_handoffs_per_run = 2\n"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("automation.max_handoffs_per_run", result.stderr)


class _MigrationFixture:
    """A minimal registered project the configuration planner accepts."""

    def __init__(self, global_toml: str, project_automation: str) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.home = root / "home"
        self.project = root / "project"
        cartopian_home = self.home / ".cartopian"
        cartopian_home.mkdir(parents=True)
        self.project.mkdir()
        if global_toml:
            (cartopian_home / "cartopian.toml").write_text(
                global_toml, encoding="utf-8"
            )
        (self.project / "cartopian.toml").write_text(
            "[project]\n"
            'id = "boundary-fixture"\n'
            'name = "Boundary Fixture"\n'
            'project_schema_version = "v0.12.0"\n'
            f"{project_automation}",
            encoding="utf-8",
        )
        (cartopian_home / "projects.json").write_text(
            json.dumps([{"id": "boundary-fixture", "path": str(self.project)}]),
            encoding="utf-8",
        )

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self._tmp.cleanup()

    def plan(self):
        return config_migration.plan_configuration_migration(
            self.project, home_root=self.home
        )

    def apply(self):
        plan = self.plan()
        result = config_migration.execute_configuration_migration(
            self.project, plan, home_root=self.home
        )
        return plan, result

    def project_config(self) -> dict:
        return tomllib.loads(
            (self.project / "cartopian.toml").read_text(encoding="utf-8")
        )

    def global_config(self) -> dict:
        path = self.home / ".cartopian" / "cartopian.toml"
        if not path.exists():
            return {}
        return tomllib.loads(path.read_text(encoding="utf-8"))

    def resolved_automation(self) -> dict:
        """The merged `[automation]` record the migrated files actually mean."""
        return resolve_configuration(
            self.global_config(), self.project_config(), {}
        )["automation"]


class TestDeterministicLegacyMapping(unittest.TestCase):
    def test_each_handoff_maps_to_handoff_complete(self) -> None:
        with _MigrationFixture(
            "", '\n[automation]\nconfirmation = "each-handoff"\n'
        ) as fixture:
            plan, result = fixture.apply()
            self.assertEqual(plan.status, "planned")
            self.assertEqual(result["status"], "complete")
            automation = fixture.project_config()["automation"]
            self.assertEqual(automation["run_boundary"], "handoff-complete")
            self.assertNotIn("confirmation", automation)
            self.assertNotIn("max_handoffs_per_run", automation)

    def test_each_handoff_drops_the_inert_legacy_budget(self) -> None:
        with _MigrationFixture(
            "",
            '\n[automation]\nconfirmation = "each-handoff"\n'
            "max_handoffs_per_run = 4\n",
        ) as fixture:
            _plan, result = fixture.apply()
            self.assertEqual(result["status"], "complete")
            automation = fixture.project_config()["automation"]
            self.assertEqual(automation["run_boundary"], "handoff-complete")
            self.assertNotIn("max_handoffs_per_run", automation)

    def test_until_blocked_maps_to_handoff_budget_with_the_same_limit(
        self,
    ) -> None:
        with _MigrationFixture(
            "",
            '\n[automation]\ninitiation = "auto"\n'
            'confirmation = "until-blocked"\n'
            "max_handoffs_per_run = 7\n",
        ) as fixture:
            _plan, result = fixture.apply()
            self.assertEqual(result["status"], "complete")
            automation = fixture.project_config()["automation"]
            self.assertEqual(automation["initiation"], "auto")
            self.assertEqual(automation["run_boundary"], "handoff-budget")
            self.assertEqual(automation["max_handoffs_per_run"], 7)

    def test_mapping_applies_to_the_global_scope_too(self) -> None:
        with _MigrationFixture(
            '[automation]\nconfirmation = "until-blocked"\n'
            "max_handoffs_per_run = 2\n",
            "",
        ) as fixture:
            _plan, result = fixture.apply()
            self.assertEqual(result["status"], "complete")
            automation = fixture.global_config()["automation"]
            self.assertEqual(automation["run_boundary"], "handoff-budget")
            self.assertEqual(automation["max_handoffs_per_run"], 2)

    def test_migration_is_idempotent(self) -> None:
        with _MigrationFixture(
            "",
            '\n[automation]\nconfirmation = "until-blocked"\n'
            "max_handoffs_per_run = 3\n",
        ) as fixture:
            fixture.apply()
            first = fixture.project_config()
            repeat = fixture.plan()
            self.assertEqual(repeat.status, "noop")
            self.assertEqual(fixture.project_config(), first)

    def test_operator_comments_survive_the_rename(self) -> None:
        with _MigrationFixture(
            "",
            "\n[automation]\n"
            '# deliberate: this project runs in short bursts\n'
            'confirmation = "until-blocked" # keep chaining\n'
            "max_handoffs_per_run = 3\n",
        ) as fixture:
            fixture.apply()
            text = (fixture.project / "cartopian.toml").read_text(
                encoding="utf-8"
            )
            self.assertIn("# deliberate: this project runs in short bursts", text)
            self.assertIn('run_boundary = "handoff-budget" # keep chaining', text)

    def test_until_blocked_without_an_authored_limit_uses_the_legacy_default(
        self,
    ) -> None:
        # v0.12 resolved `max_handoffs_per_run` independently of
        # `confirmation`, so an unauthored budget was the protocol default of
        # 1 rather than an absent one. The effective legacy pair is therefore
        # `until-blocked` + 1, which maps deterministically.
        with _MigrationFixture(
            "", '\n[automation]\nconfirmation = "until-blocked"\n'
        ) as fixture:
            _plan, result = fixture.apply()
            self.assertEqual(result["status"], "complete")
            automation = fixture.project_config()["automation"]
            self.assertEqual(automation["run_boundary"], "handoff-budget")
            self.assertEqual(automation["max_handoffs_per_run"], 1)

    def test_lone_legacy_budget_is_inert_under_the_default_pace(self) -> None:
        # A budget with no authored pace ran under the legacy default
        # `each-handoff`, where it never applied. Preserving that behavior
        # means mapping to `handoff-complete` and dropping the inert budget.
        with _MigrationFixture(
            "", "\n[automation]\nmax_handoffs_per_run = 3\n"
        ) as fixture:
            _plan, result = fixture.apply()
            self.assertEqual(result["status"], "complete")
            self.assertNotIn(
                "max_handoffs_per_run", fixture.project_config()["automation"]
            )
            resolved = fixture.resolved_automation()
            self.assertEqual(resolved["run_boundary"], "handoff-complete")
            self.assertIsNone(resolved["max_handoffs_per_run"])

    def test_mixed_old_and_new_vocabulary_fails_closed(self) -> None:
        with _MigrationFixture(
            "",
            "\n[automation]\n"
            'confirmation = "each-handoff"\n'
            'run_boundary = "task-complete"\n',
        ) as fixture:
            plan = fixture.plan()
            # A file that authors both vocabularies states two intents; the
            # planner returns the operator's decision rather than guessing,
            # and writes nothing.
            self.assertEqual(plan.status, "pending")
            codes = {item["code"] for item in plan.diagnostics}
            self.assertIn("conflicting-definition", codes)
            self.assertEqual(
                fixture.project_config()["project"]["project_schema_version"],
                "v0.12.0",
            )

    def test_marker_advances_to_the_shipped_schema_version(self) -> None:
        with _MigrationFixture(
            "", '\n[automation]\nconfirmation = "each-handoff"\n'
        ) as fixture:
            plan, _result = fixture.apply()
            shipped = plan.current_schema_version
            self.assertEqual(
                fixture.project_config()["project"]["project_schema_version"],
                shipped,
            )
            self.assertEqual(plan.marker_update["from"], "v0.12.0")
            self.assertEqual(plan.marker_update["to"], shipped)


class TestSplitScopeLegacyMapping(unittest.TestCase):
    """v0.12 resolved the legacy pair across scopes, so the mapping must too.

    `confirmation` and `max_handoffs_per_run` were two independent
    `field-override` settings, each with its own protocol default. A project
    could therefore state the pace globally and the budget locally (or state
    neither and rely on the defaults) and still have one unambiguous effective
    behavior. Mapping one authored scope at a time refuses those configs even
    though nothing about them is ambiguous, so the mapping reads the effective
    pair and then rewrites every authored source to agree with it.
    """

    def test_global_pace_and_project_budget_map_together(self) -> None:
        with _MigrationFixture(
            '[automation]\nconfirmation = "until-blocked"\n',
            "\n[automation]\nmax_handoffs_per_run = 6\n",
        ) as fixture:
            plan, result = fixture.apply()
            self.assertEqual(plan.status, "planned")
            self.assertEqual(result["status"], "complete")
            self.assertEqual(
                fixture.global_config()["automation"]["run_boundary"],
                "handoff-budget",
            )
            # The budget stays in the scope that authored it.
            self.assertEqual(
                fixture.project_config()["automation"]["max_handoffs_per_run"],
                6,
            )
            resolved = fixture.resolved_automation()
            self.assertEqual(resolved["run_boundary"], "handoff-budget")
            self.assertEqual(resolved["max_handoffs_per_run"], 6)

    def test_global_pace_and_project_budget_migration_is_idempotent(
        self,
    ) -> None:
        with _MigrationFixture(
            '[automation]\nconfirmation = "until-blocked"\n',
            "\n[automation]\nmax_handoffs_per_run = 6\n",
        ) as fixture:
            fixture.apply()
            first_global = fixture.global_config()
            first_project = fixture.project_config()
            self.assertEqual(fixture.plan().status, "noop")
            self.assertEqual(fixture.global_config(), first_global)
            self.assertEqual(fixture.project_config(), first_project)

    def test_global_each_handoff_drops_an_inert_project_budget(self) -> None:
        with _MigrationFixture(
            '[automation]\nconfirmation = "each-handoff"\n',
            "\n[automation]\nmax_handoffs_per_run = 4\n",
        ) as fixture:
            _plan, result = fixture.apply()
            self.assertEqual(result["status"], "complete")
            self.assertEqual(
                fixture.global_config()["automation"]["run_boundary"],
                "handoff-complete",
            )
            self.assertNotIn(
                "max_handoffs_per_run", fixture.project_config()["automation"]
            )
            resolved = fixture.resolved_automation()
            self.assertEqual(resolved["run_boundary"], "handoff-complete")
            self.assertIsNone(resolved["max_handoffs_per_run"])

    def test_a_budget_relocates_to_the_scope_that_wins_the_merge(self) -> None:
        # Effective legacy pair: the project's `until-blocked` wins the pace,
        # the global budget of 5 is the only budget authored. The global scope
        # now maps to `handoff-complete`, which cannot keep that budget, so the
        # budget moves to the scope whose boundary owns it.
        with _MigrationFixture(
            '[automation]\nconfirmation = "each-handoff"\n'
            "max_handoffs_per_run = 5\n",
            '\n[automation]\nconfirmation = "until-blocked"\n',
        ) as fixture:
            _plan, result = fixture.apply()
            self.assertEqual(result["status"], "complete")
            global_automation = fixture.global_config()["automation"]
            self.assertEqual(
                global_automation["run_boundary"], "handoff-complete"
            )
            self.assertNotIn("max_handoffs_per_run", global_automation)
            project_automation = fixture.project_config()["automation"]
            self.assertEqual(
                project_automation["run_boundary"], "handoff-budget"
            )
            self.assertEqual(project_automation["max_handoffs_per_run"], 5)
            resolved = fixture.resolved_automation()
            self.assertEqual(resolved["run_boundary"], "handoff-budget")
            self.assertEqual(resolved["max_handoffs_per_run"], 5)

    def test_the_losing_scope_keeps_its_own_authored_pace(self) -> None:
        # Both scopes author a pace; each keeps the boundary its own value
        # mapped to, and the merged record still means what v0.12 meant.
        with _MigrationFixture(
            '[automation]\nconfirmation = "until-blocked"\n'
            "max_handoffs_per_run = 2\n",
            '\n[automation]\nconfirmation = "each-handoff"\n',
        ) as fixture:
            _plan, result = fixture.apply()
            self.assertEqual(result["status"], "complete")
            global_automation = fixture.global_config()["automation"]
            self.assertEqual(
                global_automation["run_boundary"], "handoff-budget"
            )
            self.assertNotIn("max_handoffs_per_run", global_automation)
            self.assertEqual(
                fixture.project_config()["automation"]["run_boundary"],
                "handoff-complete",
            )
            resolved = fixture.resolved_automation()
            self.assertEqual(resolved["run_boundary"], "handoff-complete")
            self.assertIsNone(resolved["max_handoffs_per_run"])

    def test_mixed_vocabulary_across_scopes_fails_closed(self) -> None:
        with _MigrationFixture(
            '[automation]\nconfirmation = "each-handoff"\n',
            '\n[automation]\nrun_boundary = "task-complete"\n',
        ) as fixture:
            plan = fixture.plan()
            self.assertEqual(plan.status, "pending")
            codes = {item["code"] for item in plan.diagnostics}
            self.assertIn("conflicting-definition", codes)
            self.assertEqual(
                fixture.project_config()["project"]["project_schema_version"],
                "v0.12.0",
            )

    def test_an_unknown_pace_in_the_losing_scope_fails_closed(self) -> None:
        # Every authored value is inventoried, not only the one that wins.
        with _MigrationFixture(
            '[automation]\nconfirmation = "sometimes"\n',
            '\n[automation]\nconfirmation = "each-handoff"\n',
        ) as fixture:
            plan = fixture.plan()
            self.assertEqual(plan.status, "refused")
            codes = {item["code"] for item in plan.diagnostics}
            self.assertIn("unknown-source-value", codes)

    def test_a_non_positive_effective_budget_fails_closed(self) -> None:
        for budget in ("0", "-2", "true", '"3"'):
            with self.subTest(budget=budget):
                with _MigrationFixture(
                    '[automation]\nconfirmation = "until-blocked"\n',
                    f"\n[automation]\nmax_handoffs_per_run = {budget}\n",
                ) as fixture:
                    plan = fixture.plan()
                    self.assertEqual(plan.status, "refused")
                    codes = {item["code"] for item in plan.diagnostics}
                    self.assertIn("ambiguous-source-value", codes)
                    self.assertEqual(
                        fixture.project_config()["project"][
                            "project_schema_version"
                        ],
                        "v0.12.0",
                    )


class _SharedGlobalFixture:
    """One operator-global scope shared by several registered projects.

    The registry is what makes the global scope shared, so every project here
    is registered against the same `~/.cartopian`. Each project authors only
    its `[automation]` table; nothing else varies, which keeps every assertion
    about the run boundary and its budget.
    """

    def __init__(self, global_toml: str, projects: "dict[str, str]") -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.home = root / "home"
        cartopian_home = self.home / ".cartopian"
        cartopian_home.mkdir(parents=True)
        self.roots: "dict[str, Path]" = {}
        registry = []
        for project_id, automation in projects.items():
            project = root / project_id
            project.mkdir()
            (project / "cartopian.toml").write_text(
                "[project]\n"
                f'id = "{project_id}"\n'
                f'name = "{project_id}"\n'
                'project_schema_version = "v0.12.0"\n'
                f"{automation}",
                encoding="utf-8",
            )
            self.roots[project_id] = project
            registry.append({"id": project_id, "path": str(project)})
        (cartopian_home / "cartopian.toml").write_text(
            global_toml, encoding="utf-8"
        )
        (cartopian_home / "projects.json").write_text(
            json.dumps(registry), encoding="utf-8"
        )

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self._tmp.cleanup()

    @property
    def global_path(self) -> Path:
        return self.home / ".cartopian" / "cartopian.toml"

    def snapshot(self) -> "dict[str, bytes]":
        files = {"<global>": self.global_path.read_bytes()}
        for project_id, root in self.roots.items():
            files[project_id] = (root / "cartopian.toml").read_bytes()
        return files

    def plan(self, project_id: str):
        return config_migration.plan_configuration_migration(
            self.roots[project_id], home_root=self.home
        )

    def apply(self, project_id: str):
        plan = self.plan(project_id)
        result = config_migration.execute_configuration_migration(
            self.roots[project_id], plan, home_root=self.home
        )
        return plan, result

    def _config(self, path: Path) -> dict:
        return tomllib.loads(path.read_text(encoding="utf-8"))

    def project_config(self, project_id: str) -> dict:
        return self._config(self.roots[project_id] / "cartopian.toml")

    def global_config(self) -> dict:
        return self._config(self.global_path)

    def legacy_pair(self, project_id: str) -> tuple:
        """The boundary and budget this project resolves to before migration."""
        source = config_migration.plan_configuration_migration(
            self.roots[project_id],
            home_root=self.home,
            _validate_global_inventory=False,
        ).source_effective["automation"]
        return source["run_boundary"], source["max_handoffs_per_run"]

    def resolved_pair(self, project_id: str) -> tuple:
        """The boundary and budget the migrated files actually mean."""
        automation = resolve_configuration(
            self.global_config(), self.project_config(project_id), {}
        )["automation"]
        return automation["run_boundary"], automation["max_handoffs_per_run"]


# The exact registry-level deadlock TASK-06-004's independent review found:
# the global authors the budgeted legacy pair, one project overrides the pace
# and one inherits it, so neither project could be migrated first.
_SHARED_GLOBAL = (
    '[automation]\nconfirmation = "until-blocked"\nmax_handoffs_per_run = 5\n'
)
_SHARED_PROJECTS = {
    "project-a": '\n[automation]\nconfirmation = "each-handoff"\n',
    "project-b": "",
}


class TestSharedGlobalRegistryMigration(unittest.TestCase):
    """The v0.12 pace pair is global, so its migration is registry-wide.

    Reading only the project being migrated makes the shared global rewrite a
    function of whichever project runs first. That is what deadlocked a
    supported registry: planning the overriding project left the inheriting
    project's `handoff-budget` with no budget, and planning the inheriting
    project left the overriding project's retired vocabulary beside a
    preferred global. Neither order was executable, and there was no manual
    repair inside the supported contract.
    """

    def test_both_projects_keep_their_effective_pair_in_either_order(
        self,
    ) -> None:
        for order in (("project-a", "project-b"), ("project-b", "project-a")):
            with self.subTest(order=order):
                with _SharedGlobalFixture(
                    _SHARED_GLOBAL, _SHARED_PROJECTS
                ) as fixture:
                    before = {
                        project_id: fixture.legacy_pair(project_id)
                        for project_id in fixture.roots
                    }
                    self.assertEqual(
                        before["project-a"], ("handoff-complete", None)
                    )
                    self.assertEqual(
                        before["project-b"], ("handoff-budget", 5)
                    )
                    for project_id in order:
                        plan, result = fixture.apply(project_id)
                        self.assertEqual(plan.status, "planned")
                        self.assertEqual(result["status"], "complete")
                    for project_id in fixture.roots:
                        self.assertEqual(
                            fixture.resolved_pair(project_id),
                            before[project_id],
                        )

    def test_a_registry_unaware_decision_still_deadlocks(self) -> None:
        """The load-bearing half of the correction, pinned as a negative.

        `_global_budget_shareable=True` is the pre-correction reading: the
        shared scope's budget decided from the one project being migrated and
        no peer preservation. Under it this registry has no executable order,
        which is exactly the deadlock the registry-wide decision removes. If
        this ever plans, the registry pass has stopped being what makes the
        sequence executable and the positive tests above prove less than they
        claim.
        """
        with _SharedGlobalFixture(
            _SHARED_GLOBAL, _SHARED_PROJECTS
        ) as fixture:
            for project_id in fixture.roots:
                plan = config_migration.plan_configuration_migration(
                    fixture.roots[project_id],
                    home_root=fixture.home,
                    _global_budget_shareable=True,
                )
                self.assertIn(plan.status, ("refused", "pending"))
                self.assertEqual(plan.steps, ())

    def test_the_first_migration_materializes_the_peer_it_would_break(
        self,
    ) -> None:
        with _SharedGlobalFixture(
            _SHARED_GLOBAL, _SHARED_PROJECTS
        ) as fixture:
            plan = fixture.plan("project-a")
            self.assertEqual(plan.status, "planned")
            self.assertEqual(
                [step.step_id for step in plan.steps],
                [
                    "write-project",
                    "materialize-project-b",
                    "write-global",
                    "update-marker",
                ],
            )
            peer_step = plan.steps[1]
            # The peer's own scope is written before the shared source it
            # depends on is retired, and only its automation pair changes.
            self.assertEqual(peer_step.scope, "registered-project:project-b")
            self.assertEqual(
                peer_step.path, fixture.roots["project-b"] / "cartopian.toml"
            )
            self.assertIn(
                "shared-global-automation-attribution",
                plan.equivalence["differences"],
            )
            config_migration.execute_configuration_migration(
                fixture.roots["project-a"], plan, home_root=fixture.home
            )
            peer = fixture.project_config("project-b")
            self.assertEqual(peer["automation"]["max_handoffs_per_run"], 5)
            # The peer's own migration still owns its schema marker.
            self.assertEqual(
                peer["project"]["project_schema_version"], "v0.12.0"
            )

    def test_both_orders_converge_on_the_same_migrated_bytes(self) -> None:
        outcomes = []
        for order in (("project-a", "project-b"), ("project-b", "project-a")):
            with _SharedGlobalFixture(
                _SHARED_GLOBAL, _SHARED_PROJECTS
            ) as fixture:
                for project_id in order:
                    fixture.apply(project_id)
                outcomes.append(fixture.snapshot())
        self.assertEqual(outcomes[0], outcomes[1])

    def test_migration_is_idempotent_for_every_registered_project(
        self,
    ) -> None:
        with _SharedGlobalFixture(
            _SHARED_GLOBAL, _SHARED_PROJECTS
        ) as fixture:
            fixture.apply("project-a")
            fixture.apply("project-b")
            settled = fixture.snapshot()
            for project_id in fixture.roots:
                self.assertEqual(fixture.plan(project_id).status, "noop")
            self.assertEqual(fixture.snapshot(), settled)

    def test_a_second_migration_only_advances_its_own_marker(self) -> None:
        with _SharedGlobalFixture(
            _SHARED_GLOBAL, _SHARED_PROJECTS
        ) as fixture:
            fixture.apply("project-a")
            plan, result = fixture.apply("project-b")
            self.assertEqual(result["status"], "complete")
            self.assertEqual(
                [step.kind for step in plan.steps], ["update-marker"]
            )

    def test_a_peer_with_mixed_vocabulary_refuses_without_writing(
        self,
    ) -> None:
        with _SharedGlobalFixture(
            _SHARED_GLOBAL,
            {
                "project-a": '\n[automation]\nconfirmation = "each-handoff"\n',
                "project-b": '\n[automation]\nrun_boundary = "task-complete"\n',
            },
        ) as fixture:
            before = fixture.snapshot()
            plan = fixture.plan("project-a")
            self.assertIn(plan.status, ("refused", "pending"))
            self.assertEqual(
                plan.diagnostics[0]["code"], "conflicting-definition"
            )
            self.assertEqual(
                plan.diagnostics[0]["scope"],
                "registered-project:project-b",
            )
            with self.assertRaises(config_migration.MigrationRefused):
                config_migration.execute_configuration_migration(
                    fixture.roots["project-a"], plan, home_root=fixture.home
                )
            self.assertEqual(fixture.snapshot(), before)

    def test_a_peer_with_an_ambiguous_budget_refuses_without_writing(
        self,
    ) -> None:
        with _SharedGlobalFixture(
            '[automation]\nconfirmation = "until-blocked"\n',
            {
                "project-a": '\n[automation]\nconfirmation = "each-handoff"\n',
                "project-b": "\n[automation]\nmax_handoffs_per_run = 0\n",
            },
        ) as fixture:
            before = fixture.snapshot()
            plan = fixture.plan("project-a")
            self.assertIn(plan.status, ("refused", "pending"))
            self.assertEqual(
                plan.diagnostics[0]["code"], "ambiguous-source-value"
            )
            self.assertEqual(
                plan.diagnostics[0]["scope"],
                "registered-project:project-b",
            )
            with self.assertRaises(config_migration.MigrationRefused):
                config_migration.execute_configuration_migration(
                    fixture.roots["project-a"], plan, home_root=fixture.home
                )
            self.assertEqual(fixture.snapshot(), before)

    def test_a_shared_budgeted_global_keeps_its_budget_for_every_project(
        self,
    ) -> None:
        # Nothing overrides the pace, so the shared scope is still the honest
        # owner of the pair and no peer needs a materialized copy of it.
        with _SharedGlobalFixture(
            _SHARED_GLOBAL,
            {"project-a": "", "project-b": "\n[automation]\ninitiation = \"auto\"\n"},
        ) as fixture:
            plan, result = fixture.apply("project-a")
            self.assertEqual(result["status"], "complete")
            self.assertEqual(
                [step.kind for step in plan.steps],
                ["write-global", "update-marker"],
            )
            automation = fixture.global_config()["automation"]
            self.assertEqual(automation["run_boundary"], "handoff-budget")
            self.assertEqual(automation["max_handoffs_per_run"], 5)
            for project_id in fixture.roots:
                self.assertEqual(
                    fixture.resolved_pair(project_id), ("handoff-budget", 5)
                )

    def test_an_inherited_budget_default_is_materialized_for_every_peer(
        self,
    ) -> None:
        # The global states the budgeted pace but no budget, so v0.12 resolved
        # the protocol default of 1 for every project that inherited it. The
        # shared scope carries that default rather than leaving a boundary its
        # peers cannot resolve.
        with _SharedGlobalFixture(
            '[automation]\nconfirmation = "until-blocked"\n',
            {"project-a": "", "project-b": "\n[automation]\nmax_handoffs_per_run = 6\n"},
        ) as fixture:
            fixture.apply("project-a")
            automation = fixture.global_config()["automation"]
            self.assertEqual(automation["run_boundary"], "handoff-budget")
            self.assertEqual(automation["max_handoffs_per_run"], 1)
            self.assertEqual(
                fixture.resolved_pair("project-a"), ("handoff-budget", 1)
            )
            self.assertEqual(
                fixture.resolved_pair("project-b"), ("handoff-budget", 6)
            )

    def test_a_peer_that_needs_no_change_is_not_written(self) -> None:
        with _SharedGlobalFixture(
            '[automation]\nconfirmation = "each-handoff"\n'
            "max_handoffs_per_run = 5\n",
            {
                "project-a": '\n[automation]\nconfirmation = "until-blocked"\n',
                "project-b": "",
            },
        ) as fixture:
            plan = fixture.plan("project-a")
            self.assertEqual(
                [step.kind for step in plan.steps],
                ["write-project", "write-global", "update-marker"],
            )
            fixture.apply("project-a")
            # The inert global budget retires; project-a owns the one budget
            # its own pace made effective, and project-b never needed one.
            self.assertEqual(
                fixture.resolved_pair("project-a"), ("handoff-budget", 5)
            )
            self.assertEqual(
                fixture.resolved_pair("project-b"), ("handoff-complete", None)
            )

    def test_an_interrupted_shared_migration_resumes_to_the_same_state(
        self,
    ) -> None:
        with _SharedGlobalFixture(
            _SHARED_GLOBAL, _SHARED_PROJECTS
        ) as fixture:
            plan = fixture.plan("project-a")
            with self.assertRaises(config_migration.MigrationInterrupted):
                config_migration.execute_configuration_migration(
                    fixture.roots["project-a"],
                    plan,
                    home_root=fixture.home,
                    interrupt_after_step="materialize-project-b",
                )
            self.assertEqual(
                fixture.project_config("project-b")["automation"][
                    "max_handoffs_per_run"
                ],
                5,
            )
            result = config_migration.execute_configuration_migration(
                fixture.roots["project-a"], plan, home_root=fixture.home
            )
            self.assertEqual(result["status"], "complete")
            fixture.apply("project-b")
            self.assertEqual(
                fixture.resolved_pair("project-a"), ("handoff-complete", None)
            )
            self.assertEqual(
                fixture.resolved_pair("project-b"), ("handoff-budget", 5)
            )

    def test_a_plan_may_not_write_an_unregistered_path(self) -> None:
        with _SharedGlobalFixture(
            _SHARED_GLOBAL, _SHARED_PROJECTS
        ) as fixture:
            plan = fixture.plan("project-a")
            registry = fixture.home / ".cartopian" / "projects.json"
            registry.write_text(
                json.dumps(
                    [
                        {
                            "id": "project-a",
                            "path": str(fixture.roots["project-a"]),
                        }
                    ]
                ),
                encoding="utf-8",
            )
            before = fixture.snapshot()
            with self.assertRaises(config_migration.MigrationRefused):
                config_migration.execute_configuration_migration(
                    fixture.roots["project-a"], plan, home_root=fixture.home
                )
            self.assertEqual(fixture.snapshot(), before)



class _ExactPlanPositions:
    """The exact shared-global plan's targets, shared by the preflight suites.

    The plan is ``write-project``, ``materialize-project-b``, ``write-global``,
    ``update-marker``. Every preflight guarantee has to hold at each position,
    so both suites below parameterize over the same three distinct targets.
    """

    #: The distinct configuration targets the exact plan touches, by the step
    #: that first reaches each one. ``update-marker`` shares the project file
    #: with ``write-project``, so mutating the project scope is what covers
    #: the last position.
    _POSITIONS = (
        (0, "write-project", "project-a"),
        (1, "materialize-project-b", "project-b"),
        (2, "write-global", "<global>"),
    )

    def _fixture(self) -> "_SharedGlobalFixture":
        return _SharedGlobalFixture(_SHARED_GLOBAL, _SHARED_PROJECTS)

    def _target(self, fixture: "_SharedGlobalFixture", key: str) -> Path:
        if key == "<global>":
            return fixture.global_path
        return fixture.roots[key] / "cartopian.toml"

    def _snapshot(self, fixture: "_SharedGlobalFixture") -> "dict[str, object]":
        """A snapshot that survives a deleted target, unlike the fixture's."""
        files: "dict[str, object]" = {}
        for key in ("<global>", *fixture.roots):
            path = self._target(fixture, key)
            try:
                files[key] = path.read_bytes()
            except OSError:
                files[key] = "<unreadable>"
        return files


class TestMigrationPreflightAtomicity(_ExactPlanPositions, unittest.TestCase):
    """No plan may apply one step and then refuse a later one.

    Execution used to validate each target's content pin only when it reached
    that target's write. In the exact shared-global plan below the order is
    ``write-project``, ``materialize-project-b``, ``write-global``,
    ``update-marker``: a peer that changed after planning was not noticed
    until step two, by which time the current project was already rewritten
    and still carried the old schema marker. That is a partial write, and the
    operator was left with a half-migrated registry no replan could describe.

    Every condition decidable without mutating state — content pin,
    allowlist membership, target path validity, readability, and the target
    type safety covered by ``TestMigrationPreflightTargetSafety`` — is
    therefore decided across the whole executable plan before the first write.
    Marker validation and the atomic writer's last-moment pin stay where they
    are, because neither can be decided before the interim scopes exist.
    """

    def test_the_exact_plan_orders_the_peer_after_the_current_project(
        self,
    ) -> None:
        """The precondition the rest of this class depends on."""
        with self._fixture() as fixture:
            plan = fixture.plan("project-a")
            self.assertEqual(plan.status, "planned")
            self.assertEqual(
                [step.step_id for step in plan.steps],
                [
                    "write-project",
                    "materialize-project-b",
                    "write-global",
                    "update-marker",
                ],
            )
            for index, step_id, key in self._POSITIONS:
                self.assertEqual(plan.steps[index].step_id, step_id)
                self.assertEqual(
                    plan.steps[index].path, self._target(fixture, key)
                )
            # The last step shares the current project's file with step one,
            # so a stale project scope is what covers that position.
            self.assertEqual(plan.steps[3].path, plan.steps[0].path)

    def test_a_peer_stale_after_planning_writes_nothing(self) -> None:
        with self._fixture() as fixture:
            plan = fixture.plan("project-a")
            peer = self._target(fixture, "project-b")
            peer.write_text(
                peer.read_text(encoding="utf-8") + "\n# operator edit\n",
                encoding="utf-8",
            )
            pre_apply = self._snapshot(fixture)
            with self.assertRaises(config_migration.MigrationRefused) as ctx:
                config_migration.execute_configuration_migration(
                    fixture.roots["project-a"], plan, home_root=fixture.home
                )
            self.assertEqual(ctx.exception.rule, "stale-plan")
            # Every scope, including the current project written first under
            # the old contract, equals the post-edit pre-apply bytes.
            self.assertEqual(self._snapshot(fixture), pre_apply)
            self.assertEqual(
                fixture.project_config("project-a")["project"][
                    "project_schema_version"
                ],
                "v0.12.0",
            )

    def test_a_stale_shared_global_refuses_before_any_project_write(
        self,
    ) -> None:
        with self._fixture() as fixture:
            plan = fixture.plan("project-a")
            self.assertEqual(plan.steps[1].kind, "materialize-registered-project")
            global_path = fixture.global_path
            global_path.write_text(
                global_path.read_text(encoding="utf-8") + "\n# operator edit\n",
                encoding="utf-8",
            )
            pre_apply = self._snapshot(fixture)
            with self.assertRaises(config_migration.MigrationRefused) as ctx:
                config_migration.execute_configuration_migration(
                    fixture.roots["project-a"], plan, home_root=fixture.home
                )
            self.assertEqual(ctx.exception.rule, "stale-plan")
            self.assertEqual(self._snapshot(fixture), pre_apply)

    def test_a_stale_target_at_every_position_writes_nothing(self) -> None:
        for index, step_id, key in self._POSITIONS:
            with self.subTest(position=index, step=step_id):
                with self._fixture() as fixture:
                    plan = fixture.plan("project-a")
                    target = self._target(fixture, key)
                    target.write_text(
                        target.read_text(encoding="utf-8")
                        + "\n# operator edit\n",
                        encoding="utf-8",
                    )
                    pre_apply = self._snapshot(fixture)
                    with self.assertRaises(
                        config_migration.MigrationRefused
                    ) as ctx:
                        config_migration.execute_configuration_migration(
                            fixture.roots["project-a"],
                            plan,
                            home_root=fixture.home,
                        )
                    self.assertEqual(ctx.exception.rule, "stale-plan")
                    self.assertEqual(self._snapshot(fixture), pre_apply)

    def test_an_absent_target_at_every_position_writes_nothing(self) -> None:
        for index, step_id, key in self._POSITIONS:
            with self.subTest(position=index, step=step_id):
                with self._fixture() as fixture:
                    plan = fixture.plan("project-a")
                    self._target(fixture, key).unlink()
                    pre_apply = self._snapshot(fixture)
                    with self.assertRaises(
                        config_migration.MigrationRefused
                    ) as ctx:
                        config_migration.execute_configuration_migration(
                            fixture.roots["project-a"],
                            plan,
                            home_root=fixture.home,
                        )
                    self.assertEqual(ctx.exception.rule, "unreadable-target")
                    self.assertEqual(self._snapshot(fixture), pre_apply)

    def test_an_unreadable_target_at_every_position_writes_nothing(
        self,
    ) -> None:
        for index, step_id, key in self._POSITIONS:
            with self.subTest(position=index, step=step_id):
                with self._fixture() as fixture:
                    plan = fixture.plan("project-a")
                    target = self._target(fixture, key)
                    os.chmod(target, 0o000)
                    try:
                        try:
                            target.read_bytes()
                        except OSError:
                            pass
                        else:  # pragma: no cover - privileged runner
                            self.skipTest(
                                "this runner can read a mode-000 file"
                            )
                        pre_apply = self._snapshot(fixture)
                        with self.assertRaises(
                            config_migration.MigrationRefused
                        ) as ctx:
                            config_migration.execute_configuration_migration(
                                fixture.roots["project-a"],
                                plan,
                                home_root=fixture.home,
                            )
                        self.assertEqual(
                            ctx.exception.rule, "unreadable-target"
                        )
                        self.assertEqual(self._snapshot(fixture), pre_apply)
                    finally:
                        os.chmod(target, 0o644)

    def test_a_non_owned_path_still_refuses_before_the_content_pin(
        self,
    ) -> None:
        """Allowlist membership is decided first, and it too writes nothing."""
        with self._fixture() as fixture:
            plan = fixture.plan("project-a")
            (fixture.home / ".cartopian" / "projects.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "project-a",
                            "path": str(fixture.roots["project-a"]),
                        }
                    ]
                ),
                encoding="utf-8",
            )
            pre_apply = self._snapshot(fixture)
            with self.assertRaises(config_migration.MigrationRefused) as ctx:
                config_migration.execute_configuration_migration(
                    fixture.roots["project-a"], plan, home_root=fixture.home
                )
            self.assertEqual(ctx.exception.rule, "outside-allowlist")
            self.assertEqual(self._snapshot(fixture), pre_apply)

    def test_the_healthy_plan_still_applies_in_full(self) -> None:
        """The preflight refuses staleness; it must not refuse a good plan.

        ``write-project`` and ``update-marker`` share one file, so the pin for
        the marker step is the bytes the project write leaves behind rather
        than the bytes on disk when the preflight runs.
        """
        with self._fixture() as fixture:
            plan, result = fixture.apply("project-a")
            self.assertEqual(result["status"], "complete")
            self.assertEqual(
                [operation["status"] for operation in result["operations"]],
                ["applied"] * 4,
            )
            self.assertEqual(
                fixture.resolved_pair("project-a"), ("handoff-complete", None)
            )
            self.assertEqual(
                fixture.resolved_pair("project-b"), ("handoff-budget", 5)
            )

    def test_a_resumed_plan_recognizes_completed_steps_before_writing(
        self,
    ) -> None:
        """Resumable-completion identity is preflighted, not re-derived late.

        The interrupted run already applied the shared-file ``write-project``
        step, so the preflight must read that step as recognized-complete and
        still pin ``update-marker`` against the interim bytes.
        """
        with self._fixture() as fixture:
            plan = fixture.plan("project-a")
            with self.assertRaises(config_migration.MigrationInterrupted):
                config_migration.execute_configuration_migration(
                    fixture.roots["project-a"],
                    plan,
                    home_root=fixture.home,
                    interrupt_after_step="materialize-project-b",
                )
            result = config_migration.execute_configuration_migration(
                fixture.roots["project-a"], plan, home_root=fixture.home
            )
            self.assertEqual(result["status"], "complete")
            self.assertEqual(
                [operation["status"] for operation in result["operations"]],
                [
                    "recognized-complete",
                    "recognized-complete",
                    "applied",
                    "applied",
                ],
            )

    def test_a_resumed_plan_with_a_stale_peer_writes_nothing_further(
        self,
    ) -> None:
        with self._fixture() as fixture:
            plan = fixture.plan("project-a")
            with self.assertRaises(config_migration.MigrationInterrupted):
                config_migration.execute_configuration_migration(
                    fixture.roots["project-a"],
                    plan,
                    home_root=fixture.home,
                    interrupt_after_step="write-project",
                )
            peer = self._target(fixture, "project-b")
            peer.write_text(
                peer.read_text(encoding="utf-8") + "\n# operator edit\n",
                encoding="utf-8",
            )
            pre_apply = self._snapshot(fixture)
            with self.assertRaises(config_migration.MigrationRefused) as ctx:
                config_migration.execute_configuration_migration(
                    fixture.roots["project-a"], plan, home_root=fixture.home
                )
            self.assertEqual(ctx.exception.rule, "stale-plan")
            self.assertEqual(self._snapshot(fixture), pre_apply)


class TestMigrationPreflightTargetSafety(_ExactPlanPositions, unittest.TestCase):
    """A target whose *type* is unsafe must be refused before the first write.

    The whole-plan preflight pinned bytes with ``Path.read_bytes``, which
    follows a symlink and reads straight through a hardlink. A target replaced
    after planning by a byte-identical symlink or by a second link to the same
    inode therefore satisfied the content pin, and only ``_atomic_write``'s own
    symlink / regular-file / link-count guards refused it — at that target's
    own write, after every earlier planned write had already landed.

    In the exact shared-global plan that is a partial write under the old
    marker: an unsafe peer left the current project rewritten, and an unsafe
    global left both the current project and the peer rewritten. The same
    holds for the parent directory the writer pins. All of it is decidable
    from pre-execution state, so it is decided across the whole plan first,
    with the writer's own rule names and messages. The writer keeps its own
    guards as TOCTOU revalidation.
    """

    #: Each writer guard that is decidable before execution, paired with the
    #: byte-preserving substitution that violates it.
    _UNSAFE = (
        ("symlink", "migration target is a symlink"),
        ("unsafe-target", "migration target must be a single-link regular file"),
        ("unsafe-parent", "migration parent cannot be verified"),
    )

    def _elsewhere(self, fixture: "_SharedGlobalFixture", name: str) -> Path:
        """A path outside every governed root, on the fixture's filesystem."""
        parent = fixture.home.parent / "elsewhere"
        parent.mkdir(exist_ok=True)
        self._nonce = getattr(self, "_nonce", 0) + 1
        return parent / f"{self._nonce:02d}-{name}"

    def _make_unsafe(
        self, fixture: "_SharedGlobalFixture", path: Path, rule: str
    ) -> None:
        """Make ``path`` unsafe to write while its bytes stay identical."""
        if rule == "symlink":
            decoy = self._elsewhere(fixture, "decoy.toml")
            decoy.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(decoy)
        elif rule == "unsafe-target":
            # A second link to the same inode: the target path keeps its bytes
            # and its identity, and only its link count moves.
            os.link(path, self._elsewhere(fixture, "link.toml"))
        elif rule == "unsafe-parent":
            # The directory the writer pins is replaced by a symlink to the
            # real one, so the target still reads and still resolves inside
            # its own scope.
            parent = path.parent
            moved = self._elsewhere(fixture, f"real-{parent.name}")
            parent.rename(moved)
            parent.symlink_to(moved, target_is_directory=True)
        else:  # pragma: no cover - guards the table above
            raise AssertionError(f"unknown unsafe rule: {rule}")

    def _identities(
        self, fixture: "_SharedGlobalFixture"
    ) -> "dict[str, object]":
        """Per-target file identity, so a rewrite in place cannot hide."""
        identities: "dict[str, object]" = {}
        for key in ("<global>", *fixture.roots):
            try:
                st = os.lstat(self._target(fixture, key))
            except OSError:
                identities[key] = "<absent>"
            else:
                identities[key] = (
                    stat.S_ISLNK(st.st_mode),
                    stat.S_ISREG(st.st_mode),
                    st.st_dev,
                    st.st_ino,
                    st.st_nlink,
                    st.st_size,
                )
        return identities

    def _assert_no_evidence_advanced(
        self, fixture: "_SharedGlobalFixture"
    ) -> None:
        """No checkpoint, no evidence parent, and no marker advancement."""
        evidence = fixture.roots["project-a"] / ".cartopian"
        self.assertFalse(evidence.exists(), "migration evidence parent was created")
        self.assertFalse(
            (evidence / "config-migration.json").exists(),
            "a checkpoint was written",
        )
        for project_id in fixture.roots:
            self.assertEqual(
                fixture.project_config(project_id)["project"][
                    "project_schema_version"
                ],
                "v0.12.0",
                f"{project_id} advanced its marker",
            )

    def test_the_unsafe_substitutions_keep_the_planned_bytes(self) -> None:
        """The premise: neither substitution can be caught by the content pin.

        If a substitution changed one byte, the refusals below would prove
        nothing beyond the already-covered ``stale-plan`` path.
        """
        for rule, _detail in self._UNSAFE:
            for index, step_id, key in self._POSITIONS:
                with self.subTest(rule=rule, position=index, step=step_id):
                    with self._fixture() as fixture:
                        plan = fixture.plan("project-a")
                        target = self._target(fixture, key)
                        planned = plan.steps[index]
                        self.assertEqual(planned.path, target)
                        self._make_unsafe(fixture, target, rule)
                        self.assertEqual(target.read_bytes(), planned.before)

    def test_an_unsafe_target_at_every_position_writes_nothing(self) -> None:
        for rule, detail in self._UNSAFE:
            for index, step_id, key in self._POSITIONS:
                with self.subTest(rule=rule, position=index, step=step_id):
                    with self._fixture() as fixture:
                        plan = fixture.plan("project-a")
                        self._make_unsafe(
                            fixture, self._target(fixture, key), rule
                        )
                        pre_apply = self._snapshot(fixture)
                        pre_identities = self._identities(fixture)
                        with self.assertRaises(
                            config_migration.MigrationRefused
                        ) as ctx:
                            config_migration.execute_configuration_migration(
                                fixture.roots["project-a"],
                                plan,
                                home_root=fixture.home,
                            )
                        self.assertEqual(ctx.exception.rule, rule)
                        self.assertEqual(ctx.exception.detail, detail)
                        self.assertEqual(self._snapshot(fixture), pre_apply)
                        self.assertEqual(self._identities(fixture), pre_identities)
                        self._assert_no_evidence_advanced(fixture)

    def test_an_unsafe_peer_leaves_the_current_project_unwritten(self) -> None:
        """The peer sits at plan position two, behind the project write."""
        for rule, detail in self._UNSAFE:
            with self.subTest(rule=rule):
                with self._fixture() as fixture:
                    plan = fixture.plan("project-a")
                    self.assertEqual(
                        plan.steps[1].kind, "materialize-registered-project"
                    )
                    current = self._target(fixture, "project-a")
                    self._make_unsafe(
                        fixture, self._target(fixture, "project-b"), rule
                    )
                    pre_apply = self._snapshot(fixture)
                    pre_identities = self._identities(fixture)
                    with self.assertRaises(
                        config_migration.MigrationRefused
                    ) as ctx:
                        config_migration.execute_configuration_migration(
                            fixture.roots["project-a"],
                            plan,
                            home_root=fixture.home,
                        )
                    self.assertEqual((ctx.exception.rule, ctx.exception.detail), (rule, detail))
                    self.assertEqual(
                        current.read_bytes(), plan.steps[0].before
                    )
                    self.assertEqual(self._snapshot(fixture), pre_apply)
                    self.assertEqual(self._identities(fixture), pre_identities)
                    self._assert_no_evidence_advanced(fixture)

    def test_an_unsafe_global_leaves_both_projects_unwritten(self) -> None:
        """The shared global sits at plan position three, behind both."""
        for rule, detail in self._UNSAFE:
            with self.subTest(rule=rule):
                with self._fixture() as fixture:
                    plan = fixture.plan("project-a")
                    self.assertEqual(plan.steps[2].step_id, "write-global")
                    self._make_unsafe(fixture, fixture.global_path, rule)
                    pre_apply = self._snapshot(fixture)
                    pre_identities = self._identities(fixture)
                    with self.assertRaises(
                        config_migration.MigrationRefused
                    ) as ctx:
                        config_migration.execute_configuration_migration(
                            fixture.roots["project-a"],
                            plan,
                            home_root=fixture.home,
                        )
                    self.assertEqual((ctx.exception.rule, ctx.exception.detail), (rule, detail))
                    self.assertEqual(
                        self._target(fixture, "project-a").read_bytes(),
                        plan.steps[0].before,
                    )
                    self.assertEqual(
                        self._target(fixture, "project-b").read_bytes(),
                        plan.steps[1].before,
                    )
                    self.assertEqual(self._snapshot(fixture), pre_apply)
                    self.assertEqual(self._identities(fixture), pre_identities)
                    self._assert_no_evidence_advanced(fixture)

    def test_a_resumed_plan_refuses_an_unsafe_remaining_target(self) -> None:
        """Resume preflights type safety too, and advances no further step."""
        for rule, detail in self._UNSAFE:
            with self.subTest(rule=rule):
                with self._fixture() as fixture:
                    plan = fixture.plan("project-a")
                    with self.assertRaises(
                        config_migration.MigrationInterrupted
                    ):
                        config_migration.execute_configuration_migration(
                            fixture.roots["project-a"],
                            plan,
                            home_root=fixture.home,
                            interrupt_after_step="write-project",
                        )
                    self._make_unsafe(fixture, fixture.global_path, rule)
                    pre_apply = self._snapshot(fixture)
                    pre_identities = self._identities(fixture)
                    checkpoint = (
                        fixture.roots["project-a"]
                        / ".cartopian"
                        / "config-migration.json"
                    )
                    pre_checkpoint = checkpoint.read_bytes()
                    with self.assertRaises(
                        config_migration.MigrationRefused
                    ) as ctx:
                        config_migration.execute_configuration_migration(
                            fixture.roots["project-a"],
                            plan,
                            home_root=fixture.home,
                        )
                    self.assertEqual((ctx.exception.rule, ctx.exception.detail), (rule, detail))
                    self.assertEqual(self._snapshot(fixture), pre_apply)
                    self.assertEqual(self._identities(fixture), pre_identities)
                    self.assertEqual(checkpoint.read_bytes(), pre_checkpoint)
                    self.assertEqual(
                        fixture.project_config("project-a")["project"][
                            "project_schema_version"
                        ],
                        "v0.12.0",
                    )

    def test_the_writer_keeps_its_own_last_moment_type_guards(self) -> None:
        """Preflight is additive: the TOCTOU revalidation is unchanged.

        A target made unsafe *after* a clean preflight is still refused by
        ``_atomic_write`` itself, with the same rule and message.
        """
        for rule, detail in self._UNSAFE:
            with self.subTest(rule=rule):
                with self._fixture() as fixture:
                    target = self._target(fixture, "project-a")
                    before = target.read_bytes()
                    self._make_unsafe(fixture, target, rule)
                    with self.assertRaises(config_migration.GuardRefusal) as ctx:
                        config_migration._atomic_write(
                            target,
                            fixture.roots["project-a"],
                            before,
                            before + b"\n# rewritten\n",
                        )
                    self.assertEqual(ctx.exception.rule, rule)
                    self.assertEqual(ctx.exception.detail, detail)
                    self.assertEqual(target.read_bytes(), before)


class TestMigrationEntryRegistry(unittest.TestCase):
    def test_a_declared_entry_owns_the_run_boundary_transform(self) -> None:
        entries = {
            entry.identity: entry
            for entry in config_migration.CONFIGURATION_MIGRATION_ENTRIES
        }
        entry = entries["config-v0.12-to-v0.13"]
        self.assertEqual(entry.from_identities, ("v0.12.0",))
        self.assertEqual(entry.to_identity, "v0.13.0")
        self.assertIn("confirmation-to-run-boundary", entry.transforms)
        self.assertIn("effective-semantic-equivalence", entry.validation_gates)

    def test_changelog_head_declares_the_run_boundary_entry(self) -> None:
        _, _, body = _read(CHANGELOG).partition("\n## Entries\n")
        head = re.search(r"^###\s+(v\d+\.\d+\.\d+)\b", body, re.MULTILINE)
        self.assertIsNotNone(head)
        self.assertEqual(head.group(1), "v0.13.0")
        entry = body.partition("### v0.12.0")[0]
        self.assertIn("run_boundary", entry)
        self.assertIn("task-complete", entry)
        self.assertIn("handoff-complete", entry)
        self.assertIn("handoff-budget", entry)
        # The prior entry is preserved verbatim: the file is prepend-only.
        self.assertIn("### v0.12.0", body)

    def test_changelog_states_the_effective_pair_mapping(self) -> None:
        # The migration guidance and the planner have to describe one mapping.
        # Naming a per-scope pair here would tell an operator to expect a
        # refusal the planner does not issue.
        entry = _read(CHANGELOG).partition("### v0.12.0")[0]
        self.assertIn(
            "resolved independently across the global and project scopes",
            entry,
        )
        self.assertIn("maps the *effective* pair", entry)
        self.assertIn("wins the merge", entry)

    def test_the_entry_declares_the_registry_wide_transform(self) -> None:
        entries = {
            entry.identity: entry
            for entry in config_migration.CONFIGURATION_MIGRATION_ENTRIES
        }
        entry = entries["config-v0.12-to-v0.13"]
        self.assertIn("materialize-shared-global-pace-pair", entry.transforms)
        self.assertIn(
            "registry-wide-effective-pair-preservation",
            entry.validation_gates,
        )

    def test_changelog_states_the_shared_global_rule(self) -> None:
        # The shared scope's half of the mapping is a registry-level decision,
        # so an operator reading the entry has to be told that another
        # registered project's file can be written before the global one.
        entry = _read(CHANGELOG).partition("### v0.12.0")[0]
        self.assertIn(
            "decided across the whole registry rather than from", entry
        )
        self.assertIn("materialized in its own", entry)
        self.assertIn("can be migrated in\n     any order", entry)


class TestProtocolContract(unittest.TestCase):
    """The PM's run-continuation behavior is owned by protocol prose."""

    def setUp(self) -> None:
        self.text = _read(CONVENTIONS)

    def test_closed_domain_is_documented(self) -> None:
        self.assertIn("Supported `run_boundary` values are:", self.text)
        for value in RUN_BOUNDARY_VALUES:
            self.assertIn(f"`{value}`", self.text)
        self.assertIn('run_boundary = "handoff-complete"', self.text)

    def test_defaults_sentence_names_the_boundary_default(self) -> None:
        self.assertIn(
            'Defaults are `initiation = "operator"` and '
            '`run_boundary = "handoff-complete"`',
            self.text,
        )

    def test_budget_validity_is_stated_as_conditional(self) -> None:
        self.assertIn(
            "`max_handoffs_per_run` is a positive integer that is required and "
            'valid only under `run_boundary = "handoff-budget"`',
            self.text,
        )

    def test_task_complete_binds_exactly_one_task(self) -> None:
        self.assertIn("the run binds exactly one task identity", self.text)
        self.assertIn(
            "It is ephemeral run state: not a task field, not a status "
            "directory, and never written to the filesystem.",
            self.text,
        )

    def test_task_complete_continuation_is_domain_neutral(self) -> None:
        self.assertIn(
            "The contract names no activity list, no artifact kind, and no "
            "role names",
            self.text,
        )
        self.assertIn(
            "assignment, review, evidence collection, rework, reassignment, "
            "delivery, or any other lifecycle activity a Cartopian-governed "
            "workflow configures",
            self.text,
        )
        self.assertIn("widens no authority", self.text)

    def test_task_complete_stops_before_any_other_task(self) -> None:
        self.assertIn(
            "The run ends the moment the bound task reaches `done`, before "
            "any selection, lifecycle move, prompt composition, or dispatch "
            "that belongs to another task.",
            self.text,
        )

    def test_task_complete_preserves_existing_fail_closed_conditions(
        self,
    ) -> None:
        self.assertIn(
            "Every existing fail-closed condition remains terminal for the "
            "run",
            self.text,
        )

    def test_task_complete_carries_no_handoff_budget(self) -> None:
        self.assertIn(
            "`task-complete` neither consumes nor honors a handoff budget",
            self.text,
        )

    def test_task_complete_names_no_domain_specific_activity_or_role(
        self,
    ) -> None:
        """The regression DEC-073 was raised against.

        The backlog item this task corrects described `task-complete` as a
        coding/review loop. A contract that names those activities, those
        artifacts, or any role label is narrower than the approved one, and a
        research, marketing, operations, or policy workflow reads itself out
        of it.
        """
        section = _task_complete_section(self.text).lower()
        for narrowing in (
            "coder",
            "reviewer",
            "code",
            "implement",
            "pull request",
            "commit",
            "test suite",
        ):
            self.assertNotIn(
                narrowing,
                section,
                msg=(
                    "the task-complete contract must not be defined in terms "
                    f"of {narrowing!r}; it is domain-neutral"
                ),
            )

    def test_task_complete_names_multiple_workflow_domains(self) -> None:
        section = _task_complete_section(self.text)
        for domain in ("research", "marketing", "operations", "policy"):
            self.assertIn(domain, section)

    def test_retired_vocabulary_is_gone_from_the_live_contract(self) -> None:
        for retired in ('confirmation = "', "each-handoff", "until-blocked"):
            self.assertNotIn(
                retired,
                self.text,
                msg=f"retired automation vocabulary still present: {retired}",
            )


class TestSkillContract(unittest.TestCase):
    def _skill(self, name: str) -> str:
        return _read(SKILLS / name)

    def test_run_handoff_boundary_stage_uses_the_closed_vocabulary(self) -> None:
        text = self._skill("run-handoff.md")
        for value in RUN_BOUNDARY_VALUES:
            self.assertIn(f'run_boundary = "{value}"', text)
        self.assertNotIn("each-handoff", text)
        self.assertNotIn("until-blocked", text)

    def test_run_task_stops_at_done_under_task_complete(self) -> None:
        text = self._skill("run-task.md")
        self.assertIn('run_boundary = "task-complete"', text)
        self.assertIn(
            "return control without selecting, moving, prompting, or "
            "dispatching another task",
            text,
        )
        # The continuation instruction stays activity-agnostic: it names what
        # the bound task needs, not a coding/review loop.
        self.assertIn(
            "keep continuing whichever configured and authorized activity "
            "this same task needs next, whatever kind of work it is",
            text,
        )
        self.assertNotIn("until-blocked", text)

    def test_init_skills_offer_the_closed_boundary_choice(self) -> None:
        for name in ("init-workspace.md", "init-project.md"):
            text = self._skill(name)
            for value in RUN_BOUNDARY_VALUES:
                self.assertIn(
                    value,
                    text,
                    msg=f"{name} must offer the {value} boundary",
                )
            self.assertNotIn("each-handoff", text)
            self.assertNotIn("until-blocked", text)

    def test_migrate_project_names_the_registered_project_step(self) -> None:
        # The PM has to be able to tell the operator, before applying, that a
        # run touches another registered project's file.
        text = self._skill("migrate-project.md")
        self.assertIn("materialize-registered-project", text)
        self.assertIn(
            "never advance another project's schema marker", text
        )

    def test_global_template_documents_the_boundary(self) -> None:
        text = _read(GLOBAL_TEMPLATE)
        self.assertIn('# run_boundary = "handoff-complete"', text)
        self.assertIn('# run_boundary = "task-complete"', text)
        self.assertNotIn("each-handoff", text)
        self.assertNotIn("until-blocked", text)


class TestUnchangedAuthorities(unittest.TestCase):
    """The boundary decides run length only; nothing else moves."""

    def test_initiation_domain_is_untouched(self) -> None:
        self.assertEqual(
            CONFIG_SCHEMA["fields"]["automation.initiation"]["values"],
            ("operator", "auto"),
        )
        self.assertEqual(
            CONFIG_SCHEMA["fields"]["automation.initiation"]["default"],
            "operator",
        )

    def test_review_and_launch_domains_are_untouched(self) -> None:
        fields = CONFIG_SCHEMA["fields"]
        self.assertEqual(fields["reviews.planning"]["values"], ("required", "off"))
        self.assertEqual(
            fields["reviews.task_closure"]["values"], ("required", "off")
        )
        self.assertEqual(
            fields["roles.*.auto_launch"]["values"],
            ("task_run", "task_review", "planning_review"),
        )

    def test_boundary_does_not_gate_initiation(self) -> None:
        text = _read(CONVENTIONS)
        self.assertIn(
            "`run_boundary` describes how far an initiated run continues, "
            "not whether one starts",
            text,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
