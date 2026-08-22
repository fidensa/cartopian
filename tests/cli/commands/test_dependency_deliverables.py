"""Dependency-deliverable projection and preflight.

A task that declares ``Blocked by:`` consumes its dependencies' output. These
tests cover the three surfaces that make that input explicit:

- ``task-bundle`` projects each dependency's resolved deliverable, not only
  its id, path, and status.
- ``validate-task-readiness`` fails ``blocked-by-complete`` when a completed
  dependency's project-mode deliverable was never persisted.
- ``handoff-packet`` verifies, per dependency, that a governance-scoped
  upstream deliverable is readable by the role or curated verbatim into the
  prompt, and fails closed before launch otherwise.
"""
import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli.commands import handoff_packet, task_bundle, validate_task_readiness
from cli.main import EXIT_FAIL, EXIT_OK
from tests.scaffold import project_scaffold

_TOML = (
    "[project]\n"
    'id = "test-proj"\n'
    'name = "Test Project"\n'
    'project_schema_version = "v0.11.0"\n'
    "\n"
    "[roles.coder]\n"
    'description = "Implements tasks per spec."\n'
    'auto_launch = ["task_run"]\n'
    'agent = "cartopian-claude"\n'
    "\n"
    "[reviews]\n"
    'planning = "off"\n'
    'task_closure = "off"\n'
)

_DEP_TASK = (
    "# TASK-05-008: Upstream contract\n\n"
    "Phase: PHASE-05\n"
    "Plan ref: n/a\n"
    "Work root: n/a\n"
    "Assignee: coder\n"
    "Spec: none\n"
    "Blocked by: n/a\n"
    "Created: 2026-08-01\n"
    "Evidence gate: n/a\n"
    "Deliverable: project:resources/contracts/d2-interface.md\n\n"
    "## Goal\n\nDefine the D2 interface contract.\n"
)


def _dependent_task(blocked_by: str = "TASK-05-008") -> str:
    return (
        "# TASK-05-009: Consume upstream contract\n\n"
        "Phase: PHASE-05\n"
        "Plan ref: n/a\n"
        "Work root: n/a\n"
        "Assignee: coder\n"
        "Spec: none\n"
        f"Blocked by: {blocked_by}\n"
        "Created: 2026-08-02\n"
        "Evidence gate: n/a\n"
        "Deliverable: n/a\n\n"
        "## Goal\n\nImplement against the D2 interface.\n\n"
        "## Acceptance\n\n- [ ] Interface consumed as specified.\n"
    )


class TestTaskBundleProjectsDependencyDeliverable(unittest.TestCase):
    def _bundle(self, task_path: Path) -> dict:
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = task_bundle.handler(
                argparse.Namespace(task_path=str(task_path))
            )
        self.assertEqual(rc, EXIT_OK, err.getvalue())
        return json.loads(out.getvalue())

    def test_dependency_carries_its_resolved_deliverable(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/done/TASK-05-008.md", _DEP_TASK)
            task = scaffold.write(
                "tasks/open/TASK-05-009.md", _dependent_task()
            )
            record = self._bundle(task)
            dependency = record["dependencies"][0]
            self.assertEqual(dependency["task_id"], "TASK-05-008")
            self.assertEqual(
                dependency["deliverable"]["logical"],
                "project:resources/contracts/d2-interface.md",
            )
            self.assertEqual(dependency["deliverable"]["mode"], "project")
            self.assertFalse(dependency["deliverable"]["exists"])

            scaffold.write(
                "resources/contracts/d2-interface.md", "# D2 interface\n"
            )
            record = self._bundle(task)
            self.assertTrue(
                record["dependencies"][0]["deliverable"]["exists"]
            )

    def test_dependency_without_deliverable_projects_null(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write(
                "tasks/done/TASK-05-008.md",
                _DEP_TASK.replace(
                    "Deliverable: project:resources/contracts/d2-interface.md",
                    "Deliverable: n/a",
                ),
            )
            task = scaffold.write(
                "tasks/open/TASK-05-009.md", _dependent_task()
            )
            record = self._bundle(task)
            self.assertIsNone(record["dependencies"][0]["deliverable"])


class TestReadinessBlocksMissingDependencyDeliverable(unittest.TestCase):
    def test_done_dependency_with_unpersisted_deliverable_fails(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/done/TASK-05-008.md", _DEP_TASK)
            headers = {"Blocked by": "TASK-05-008"}
            check = validate_task_readiness._check_blocked_by(
                scaffold.project_root, headers
            )
            self.assertFalse(check["pass"])
            self.assertIn("TASK-05-008", check["reason"])
            self.assertIn("not persisted", check["reason"])

            scaffold.write(
                "resources/contracts/d2-interface.md", "# D2 interface\n"
            )
            check = validate_task_readiness._check_blocked_by(
                scaffold.project_root, headers
            )
            self.assertTrue(check["pass"], check["reason"])

    def test_work_root_dependency_deliverable_is_not_gated_here(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write(
                "tasks/done/TASK-05-008.md",
                _DEP_TASK.replace(
                    "Deliverable: project:resources/contracts/d2-interface.md",
                    "Deliverable: tool-repo:docs/spec.md",
                ),
            )
            check = validate_task_readiness._check_blocked_by(
                scaffold.project_root, {"Blocked by": "TASK-05-008"}
            )
            self.assertTrue(check["pass"], check["reason"])


class TestDependencyDeliverableInputs(unittest.TestCase):
    def _inputs(self, scaffold, grants, prompt_text=None):
        return handoff_packet._dependency_deliverable_inputs(
            scaffold.project_root,
            {"project": {}},
            _dependent_task(),
            grants,
            prompt_text=prompt_text,
        )

    def test_missing_upstream_deliverable_fails_whatever_the_grants(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/done/TASK-05-008.md", _DEP_TASK)
            for grants in (["read:governance"], []):
                (record,) = self._inputs(scaffold, grants)
                self.assertTrue(record["required"])
                self.assertIs(record["ok"], False)
                self.assertEqual(
                    record["reason"], "dependency-deliverable-missing"
                )

    def test_governance_reader_needs_no_curation(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/done/TASK-05-008.md", _DEP_TASK)
            scaffold.write(
                "resources/contracts/d2-interface.md", "# D2 interface\n"
            )
            (record,) = self._inputs(scaffold, ["read:governance"])
            self.assertFalse(record["required"])
            self.assertEqual(record["reason"], "role-can-read-governance")
            self.assertIs(record["ok"], True)

    def test_contained_role_requires_curated_prompt_content(self) -> None:
        content = "# D2 interface\n\ninterface D2 { apply(a, b): b applied to a }\n"
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/done/TASK-05-008.md", _DEP_TASK)
            scaffold.write("resources/contracts/d2-interface.md", content)

            (unverifiable,) = self._inputs(scaffold, [])
            self.assertTrue(unverifiable["required"])
            self.assertIsNone(unverifiable["ok"])

            (missing,) = self._inputs(
                scaffold, [], prompt_text="prompt without the contract"
            )
            self.assertIs(missing["ok"], False)

            (curated,) = self._inputs(
                scaffold, [], prompt_text=f"## Upstream contract\n\n{content}"
            )
            self.assertIs(curated["ok"], True)
            self.assertTrue(curated["prompt_contains_current_content"])
            self.assertEqual(curated["content_bytes"], len(content.encode()))

    def test_non_project_deliverables_are_not_applicable(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write(
                "tasks/done/TASK-05-008.md",
                _DEP_TASK.replace(
                    "Deliverable: project:resources/contracts/d2-interface.md",
                    "Deliverable: n/a",
                ),
            )
            (record,) = self._inputs(scaffold, [])
            self.assertFalse(record["required"])
            self.assertEqual(record["reason"], "not-applicable")

    def test_unlocatable_dependency_task_is_reported_not_fatal(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            (record,) = self._inputs(scaffold, [])
            self.assertEqual(record["reason"], "dependency-task-not-found")
            self.assertIs(record["ok"], True)


class TestHandoffPacketGatesOnDependencyDeliverable(unittest.TestCase):
    def _invoke(self, task_path: Path):
        args = argparse.Namespace(task_path=str(task_path), role="coder")
        out = io.StringIO()
        err = io.StringIO()
        with tempfile.TemporaryDirectory(prefix="cartopian-fake-home-") as home:
            with mock.patch(
                "cli.commands.handoff_packet.Path.home",
                return_value=Path(home),
            ):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    rc = handoff_packet.handler(args)
        return out.getvalue(), err.getvalue(), rc

    def test_missing_upstream_deliverable_refuses_before_launch(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/done/TASK-05-008.md", _DEP_TASK)
            task = scaffold.write(
                "tasks/open/TASK-05-009.md", _dependent_task()
            )
            stdout, stderr, rc = self._invoke(task)
            self.assertEqual(rc, EXIT_FAIL)
            self.assertIn("dependency-deliverable-input-unavailable", stderr)
            record = json.loads(stdout.splitlines()[0])
            (dependency,) = record["dependency_deliverable_inputs"]
            self.assertEqual(
                dependency["reason"], "dependency-deliverable-missing"
            )

    def test_persisted_upstream_deliverable_passes(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            scaffold.write("tasks/done/TASK-05-008.md", _DEP_TASK)
            scaffold.write(
                "resources/contracts/d2-interface.md", "# D2 interface\n"
            )
            task = scaffold.write(
                "tasks/open/TASK-05-009.md", _dependent_task()
            )
            stdout, stderr, rc = self._invoke(task)
            self.assertEqual(rc, EXIT_OK, stderr)
            record = json.loads(stdout.splitlines()[0])
            (dependency,) = record["dependency_deliverable_inputs"]
            self.assertIs(dependency["ok"], True)


if __name__ == "__main__":
    unittest.main()
