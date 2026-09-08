"""Kickoff friction: planning readiness, one startup verdict, scoped coverage.

The acceptance test is the fresh-session rehearsal: finish planning, close the
session, reopen the project, and reach task-1 dispatch with no repeated
choices and no additional planning artifact. These tests build that project
from the real writers and assert that `next-action` reports exactly one of
`planning-incomplete` (with the exact remaining step), `ready` (with the exact
dispatch action), or `blocked` (with the failure, owner, and recovery) — and
that `compose-state` never describes an ungenerated phase as closeout.
"""
import argparse
import contextlib
import hashlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from cli import acceptance_trace as at
from cli import deliverable_defaults, dispatch_rehearsal, planning_status
from cli.commands import (
    acceptance_trace as trace_command,
    close_audit,
    compose_state,
    next_action,
    plan_audit,
    validate_task_readiness,
    write_task,
)
from cli.protocol_gate import read_shipped_project_schema_version
from tests.scaffold import project_scaffold

SCHEMA = read_shipped_project_schema_version()

_TOML = (
    "[project]\n"
    'id = "kickoff"\n'
    'name = "Kickoff Project"\n'
    f'project_schema_version = "{SCHEMA}"\n'
    "\n"
    "[roles.coder]\n"
    'description = "Implements tasks per spec."\n'
    'auto_launch = ["task_run"]\n'
    'agent = "cartopian-claude"\n'
    "\n"
    "[roles.reviewer]\n"
    'description = "Reviews checkpoints."\n'
)
_TOML_PLANNING_REVIEW = _TOML + (
    "\n[reviews]\n"
    'planning = "required"\n'
    'planning_role = "reviewer"\n'
    'task_closure = "off"\n'
)
_TOML_REVIEW_OFF = _TOML + '\n[reviews]\nplanning = "off"\ntask_closure = "off"\n'

REQUEST_TEXT = "Inventory the vendor catalog and record the findings."
PRICING_TEXT = "Pricing must stay under the current tier."


def _capture(project_root: Path, text: str, *, record_id: str, sequence: int, kind: str = "original", correction_of=None) -> str:
    identity = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    record = {
        "schema": "cartopian-original-request-v1",
        "record_id": record_id,
        "request_id": "REQUEST-001",
        "kind": kind,
        "sequence": sequence,
        "unit": {"kind": "project", "id": "project"},
        "text": text,
        "content_identity": identity,
        "captured_at": f"2026-08-0{sequence + 1}T12:00:00Z",
    }
    if correction_of:
        record["correction_of"] = correction_of
    (project_root / "requests").mkdir(exist_ok=True)
    (project_root / "requests" / f"{record_id}.json").write_text(
        json.dumps(record, sort_keys=True) + "\n", encoding="utf-8"
    )
    return identity


@contextlib.contextmanager
def _isolated_home():
    import tempfile

    with tempfile.TemporaryDirectory(prefix="cartopian-home-") as tmp:
        with mock.patch.object(Path, "home", return_value=Path(tmp)):
            yield Path(tmp)


def _run(handler, **kwargs):
    """Invoke a command handler in-process; return (code, records, stderr)."""
    args = argparse.Namespace(**kwargs)
    records = []
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = handler(args)
    for line in out.getvalue().splitlines():
        if line.startswith("{"):
            records.append(json.loads(line))
    return code, records, err.getvalue()


TASK_BODY = """# TASK-01-001: Vendor catalog inventory

Phase: PHASE-01
Plan ref: RESEARCH-01-001
Work root: n/a
Assignee: coder
Spec: none
Blocked by: none
Evidence gate: n/a
Source guidance: n/a
Upstream trace: n/a

## Goal

Inventory the vendor catalog.

## Evidence gate

n/a — inventory verified by inspection of the deliverable.

## Risk observations

- consequence-reach: project-internal; Fact: the inventory is a project resource
- reversibility: direct-undo; Fact: the deliverable is a single document
- authority: covered; Fact: approved plan item covers the inventory
- ambiguity: confirmed; Fact: the goal names the catalog and the record shape
- evidence-coverage: direct-observation; Fact: the inventory is inspected directly

## Judgment envelope

- lifecycle-boundaries: none
- open-failure-conditions: none

## Practice-pack envelope

- primary-outcomes: none
- artifact-kinds: none
- incidental-terms: none
- exclusions: none
- lifecycle-substrate-activities: none
- domain-scopes: none
- authorized-profile-hint: none

## Acceptance

- [ ] The catalog inventory lists every vendor with a current contract.
- [ ] Each vendor row records the contract end date.
- [ ] The inventory is written to the declared deliverable.
"""


def _plan_project(scaffold, *, tasks: bool = True, checkpoints=()):
    root = scaffold.project_root
    _capture(root, REQUEST_TEXT, record_id="REQUEST-001", sequence=0)
    scaffold.write("REQUIREMENTS.md", "# Requirements\n\n- FR-001: inventory the catalog.\n")
    scaffold.write("STANDARDS.md", "# Standards\n")
    scaffold.write(
        "IMPLEMENTATION_PLAN.md",
        "# Plan\n\n## Phase 01: Inventory\n\n- `RESEARCH-01-001` — inventory.\n\n"
        "## Delivery contract\n\n- Delivery: not-applicable; Justification: internal research.\n",
    )
    scaffold.write("phases/PHASE-01.md", "# PHASE-01: Inventory\n\n- `RESEARCH-01-001` — inventory.\n")
    for checkpoint, plan_ref in checkpoints:
        scaffold.write(
            f"reviews/REVIEW-{checkpoint}.md",
            f"# REVIEW-{checkpoint}\n\nTarget: {checkpoint}\nPlan ref: {plan_ref}\n"
            "Verdict: approve\nRequest alignment: aligned\nRequest evidence: REQUEST-001\n",
        )
    if tasks:
        code, records, err = _run(
            write_task.handler,
            project_root=str(root),
            task_id="TASK-01-001",
            content=TASK_BODY,
            content_file=None,
            source=None,
        )
        assert code == 0, err
    return root


class DeliverableDefaultTests(unittest.TestCase):
    def test_document_work_gets_a_deterministic_resource_path(self):
        self.assertEqual(
            deliverable_defaults.default_project_deliverable(
                "RESEARCH", "TASK-01-001: Vendor catalog inventory"
            ),
            "project:resources/research/vendor-catalog-inventory.md",
        )

    def test_slug_carries_no_identifier(self):
        slug = deliverable_defaults.title_slug("Align with SPEC-01-002 and DEC-003")
        self.assertNotIn("spec", slug)
        self.assertNotIn("dec-003", slug)
        self.assertEqual(slug, "align-with-and")

    def test_write_task_stamps_the_default_for_research_work(self):
        with _isolated_home(), project_scaffold(cartopian_toml=_TOML_REVIEW_OFF) as scaffold:
            root = _plan_project(scaffold)
            text = (root / "tasks" / "open" / "TASK-01-001.md").read_text(encoding="utf-8")
            self.assertIn(
                "Deliverable: project:resources/research/vendor-catalog-inventory.md",
                text,
            )
            self.assertNotIn("Deliverable: n/a", text)

    def test_an_explicit_path_is_the_override(self):
        body = TASK_BODY.replace("Spec: none", "Deliverable: project:resources/notes/x.md\nSpec: none")
        stamped, value = deliverable_defaults.stamp_default(body)
        self.assertIsNone(value)
        self.assertEqual(stamped, body)

    def test_explicit_n_a_is_left_for_readiness_to_reject(self):
        body = TASK_BODY.replace("Spec: none", "Deliverable: n/a\nSpec: none")
        _stamped, value = deliverable_defaults.stamp_default(body)
        self.assertIsNone(value)

    def test_repeated_titles_get_distinct_default_paths(self):
        with _isolated_home(), project_scaffold(cartopian_toml=_TOML_REVIEW_OFF) as scaffold:
            root = _plan_project(scaffold)
            second = TASK_BODY.replace("TASK-01-001", "TASK-01-002").replace(
                "RESEARCH-01-001", "RESEARCH-01-002"
            )
            (root / "IMPLEMENTATION_PLAN.md").write_text(
                (root / "IMPLEMENTATION_PLAN.md").read_text(encoding="utf-8")
                + "- `RESEARCH-01-002` — inventory again.\n",
                encoding="utf-8",
            )
            code, _records, err = _run(
                write_task.handler, project_root=str(root), task_id="TASK-01-002",
                content=second, content_file=None, source=None,
            )
            self.assertEqual(code, 0, err)
            first = (root / "tasks/open/TASK-01-001.md").read_text(encoding="utf-8")
            second_text = (root / "tasks/open/TASK-01-002.md").read_text(encoding="utf-8")
            self.assertIn("Deliverable: project:resources/research/vendor-catalog-inventory.md", first)
            self.assertIn("Deliverable: project:resources/research/vendor-catalog-inventory-2.md", second_text)

    def test_existing_resource_from_an_earlier_plan_is_not_overwritten(self):
        self.assertEqual(
            deliverable_defaults.default_project_deliverable(
                "RESEARCH", "Vendor catalog inventory",
                exists=lambda rel: rel == "resources/research/vendor-catalog-inventory.md",
            ),
            "project:resources/research/vendor-catalog-inventory-2.md",
        )

    def test_non_ascii_and_empty_titles_slug_deterministically(self):
        self.assertEqual(deliverable_defaults.title_slug("Évaluation des fournisseurs"), "evaluation-des-fournisseurs")
        self.assertEqual(deliverable_defaults.title_slug("日本語"), "deliverable")

    def test_build_work_only_stamps_on_request(self):
        body = TASK_BODY.replace("RESEARCH-01-001", "BUILD-01-001")
        self.assertIsNone(deliverable_defaults.stamp_default(body)[1])
        body = body.replace("Spec: none", "Deliverable: default\nSpec: none")
        self.assertEqual(
            deliverable_defaults.stamp_default(body)[1],
            "project:resources/build/vendor-catalog-inventory.md",
        )


class PlanningStatusTests(unittest.TestCase):
    def test_no_plan_names_the_first_planning_step(self):
        with project_scaffold(cartopian_toml=_TOML_REVIEW_OFF) as scaffold:
            record = planning_status.derive(scaffold.project_root, planning_review_required=False)
            self.assertEqual(record["stage"], "no-plan")
            self.assertFalse(record["complete"])
            self.assertIn("plan project", record["next"])

    def test_phases_without_tasks_names_task_generation(self):
        with _isolated_home(), project_scaffold(cartopian_toml=_TOML_REVIEW_OFF) as scaffold:
            root = _plan_project(scaffold, tasks=False)
            record = planning_status.derive(root, planning_review_required=False)
            self.assertEqual(record["stage"], "tasks")
            self.assertIn("Generate tasks and specs for PHASE-01", record["next"])

    def test_phases_approved_but_tasks_missing_names_stage_4_then_plan_004(self):
        checkpoints = [("PLAN-001", "n/a"), ("PLAN-002", "n/a"), ("PLAN-003", "n/a")]
        with _isolated_home(), project_scaffold(cartopian_toml=_TOML_PLANNING_REVIEW) as scaffold:
            root = _plan_project(scaffold, tasks=False, checkpoints=checkpoints)
            record = planning_status.derive(root, planning_review_required=True)
            self.assertEqual(record["stage"], "tasks")
            self.assertIn("PHASE-01", record["next"])
            self.assertIn("PLAN-004", record["next"])
            self.assertEqual(list(record["checkpoints"].values()), ["approved"] * 3)

    def test_phases_unreviewed_names_plan_003(self):
        with _isolated_home(), project_scaffold(cartopian_toml=_TOML_PLANNING_REVIEW) as scaffold:
            root = _plan_project(scaffold, tasks=False, checkpoints=[("PLAN-001", "n/a"), ("PLAN-002", "n/a")])
            record = planning_status.derive(root, planning_review_required=True)
            self.assertEqual(record["stage"], "phases-review")
            self.assertEqual(record["checkpoint"], "PLAN-003")

    def test_generated_tasks_without_covering_checkpoint_names_plan_004(self):
        checkpoints = [("PLAN-001", "n/a"), ("PLAN-002", "n/a"), ("PLAN-003", "n/a")]
        with _isolated_home(), project_scaffold(cartopian_toml=_TOML_PLANNING_REVIEW) as scaffold:
            root = _plan_project(scaffold, checkpoints=checkpoints)
            record = planning_status.derive(root, planning_review_required=True)
            self.assertEqual(record["stage"], "tasks-review")
            self.assertEqual(record["checkpoint"], "PLAN-004")
            self.assertIn("TASK-01-001", record["next"])

    def test_in_flight_checkpoint_names_the_wait(self):
        checkpoints = [("PLAN-001", "n/a"), ("PLAN-002", "n/a"), ("PLAN-003", "n/a")]
        with _isolated_home(), project_scaffold(cartopian_toml=_TOML_PLANNING_REVIEW) as scaffold:
            root = _plan_project(scaffold, checkpoints=checkpoints)
            scaffold.write("prompts/PROMPT-PLAN-004.md", "# PROMPT-PLAN-004\n")
            record = planning_status.derive(root, planning_review_required=True)
            self.assertEqual(record["checkpoint"], "PLAN-004")
            self.assertIn("wait-report", record["next"])
            scaffold.write("reports/REPORT-PLAN-004.md", "Status: complete\n")
            record = planning_status.derive(root, planning_review_required=True)
            self.assertIn("report-action", record["next"])

    def test_a_legacy_unscoped_checkpoint_does_not_approve_a_later_phase(self):
        checkpoints = [("PLAN-001", "n/a"), ("PLAN-002", "n/a"), ("PLAN-003", "n/a"), ("PLAN-004", "n/a")]
        with _isolated_home(), project_scaffold(cartopian_toml=_TOML_PLANNING_REVIEW) as scaffold:
            root = _plan_project(scaffold, checkpoints=checkpoints)
            # The unscoped PLAN-004 covers the initial generation (phase 1).
            self.assertTrue(planning_status.derive(root, planning_review_required=True)["complete"])
            # Phase 1 completes; phase 2's tasks are generated with no new checkpoint.
            (root / "tasks/open/TASK-01-001.md").rename(root / "tasks/done/TASK-01-001.md")
            scaffold.write("phases/PHASE-02.md", "# PHASE-02: Follow-up\n\n- `RESEARCH-02-001` — follow-up.\n")
            second = TASK_BODY.replace("TASK-01-001", "TASK-02-001").replace(
                "RESEARCH-01-001", "RESEARCH-02-001"
            ).replace("Phase: PHASE-01", "Phase: PHASE-02")
            scaffold.write("tasks/open/TASK-02-001.md", second)
            record = planning_status.derive(root, planning_review_required=True)
            self.assertFalse(record["complete"], record)
            self.assertEqual(record["checkpoint"], "PLAN-005")
            self.assertIn("--plan-ref", record["next"])
            # A scoped checkpoint covering phase 2 completes it.
            scaffold.write(
                "reviews/REVIEW-PLAN-005.md",
                "# REVIEW-PLAN-005\n\nTarget: PLAN-005\nPlan ref: RESEARCH-02-001\n"
                "Verdict: approve\nRequest alignment: aligned\nRequest evidence: REQUEST-001\n",
            )
            self.assertTrue(planning_status.derive(root, planning_review_required=True)["complete"])

    def test_an_executing_phase_is_not_sent_back_to_planning(self):
        """A legacy project whose reviews were cleared mid-plan keeps running."""
        with _isolated_home(), project_scaffold(cartopian_toml=_TOML_PLANNING_REVIEW) as scaffold:
            root = _plan_project(scaffold)
            second = TASK_BODY.replace("TASK-01-001", "TASK-01-002").replace(
                "RESEARCH-01-001", "RESEARCH-01-002"
            )
            scaffold.write("tasks/done/TASK-01-002.md", second)
            record = planning_status.derive(root, planning_review_required=True)
            self.assertTrue(record["complete"], record)

    def test_approved_plan_004_covering_the_plan_ref_completes_planning(self):
        checkpoints = [("PLAN-001", "n/a"), ("PLAN-002", "n/a"), ("PLAN-003", "n/a"), ("PLAN-004", "RESEARCH-01-001")]
        with _isolated_home(), project_scaffold(cartopian_toml=_TOML_PLANNING_REVIEW) as scaffold:
            root = _plan_project(scaffold, checkpoints=checkpoints)
            record = planning_status.derive(root, planning_review_required=True)
            self.assertTrue(record["complete"], record)

    def test_review_off_completes_on_generated_tasks(self):
        with _isolated_home(), project_scaffold(cartopian_toml=_TOML_REVIEW_OFF) as scaffold:
            root = _plan_project(scaffold)
            record = planning_status.derive(root, planning_review_required=False)
            self.assertTrue(record["complete"])
            self.assertEqual(record["checkpoints"], {})


class ComposeStateNextStepTests(unittest.TestCase):
    def test_empty_queue_with_ungenerated_phase_is_not_closeout(self):
        with _isolated_home(), project_scaffold(cartopian_toml=_TOML_REVIEW_OFF) as scaffold:
            root = _plan_project(scaffold, tasks=False)
            record = compose_state.compose_record(root, "Kickoff Project")
            self.assertNotIn("closeout", record["what_to_do_next"])
            self.assertIn("Generate tasks and specs for PHASE-01", record["what_to_do_next"])
            self.assertIn("[tasks not generated]", record["current_phase"])

    def test_finished_phase_with_later_ungenerated_phase_names_generation(self):
        with _isolated_home(), project_scaffold(cartopian_toml=_TOML_REVIEW_OFF) as scaffold:
            root = _plan_project(scaffold)
            scaffold.write("phases/PHASE-02.md", "# PHASE-02: Follow-up\n")
            task = root / "tasks" / "open" / "TASK-01-001.md"
            task.rename(root / "tasks" / "done" / "TASK-01-001.md")
            record = compose_state.compose_record(root, "Kickoff Project")
            self.assertIn("PHASE-02", record["what_to_do_next"])
            self.assertNotIn("closeout", record["what_to_do_next"])

    def test_truly_finished_plan_still_reports_closeout(self):
        with _isolated_home(), project_scaffold(cartopian_toml=_TOML_REVIEW_OFF) as scaffold:
            root = _plan_project(scaffold)
            task = root / "tasks" / "open" / "TASK-01-001.md"
            task.rename(root / "tasks" / "done" / "TASK-01-001.md")
            record = compose_state.compose_record(root, "Kickoff Project")
            self.assertIn("closeout", record["what_to_do_next"])


class StartupVerdictTests(unittest.TestCase):
    def _next_action(self, root, reconcile=False):
        return _run(next_action.handler, project_path=str(root), reconcile=reconcile)

    def test_planning_incomplete_names_the_exact_remaining_step(self):
        checkpoints = [("PLAN-001", "n/a"), ("PLAN-002", "n/a"), ("PLAN-003", "n/a")]
        with _isolated_home(), project_scaffold(cartopian_toml=_TOML_PLANNING_REVIEW) as scaffold:
            root = _plan_project(scaffold, tasks=False, checkpoints=checkpoints)
            code, records, err = self._next_action(root)
            self.assertEqual(code, 0, err)
            startup = records[0]["startup"]
            self.assertEqual(startup["verdict"], "planning-incomplete")
            self.assertIn("PHASE-01", startup["action"])
            self.assertIn("PLAN-004", startup["action"])
            self.assertEqual(startup["owner"], "pm")
            self.assertFalse(records[0]["planning"]["complete"])

    def test_fresh_session_reaches_task_one_dispatch_with_no_planning_left(self):
        """The acceptance rehearsal: planning finished, reopen, dispatch is named."""
        checkpoints = [("PLAN-001", "n/a"), ("PLAN-002", "n/a"), ("PLAN-003", "n/a"), ("PLAN-004", "RESEARCH-01-001")]
        with _isolated_home(), project_scaffold(cartopian_toml=_TOML_PLANNING_REVIEW) as scaffold:
            root = _plan_project(scaffold, checkpoints=checkpoints)
            # Session close: STATE.md refreshed by the composer.
            body = compose_state.compose_record(root, "Kickoff Project")["rendered_body"]
            (root / "STATE.md").write_text(body + "\n", encoding="utf-8")
            # Session reopen.
            code, records, err = self._next_action(root)
            self.assertEqual(code, 0, err)
            record = records[0]
            self.assertTrue(record["planning"]["complete"], record["planning"])
            startup = record["startup"]
            self.assertEqual(startup["verdict"], "ready", startup)
            self.assertEqual(startup["task"], "TASK-01-001")
            self.assertEqual(startup["role"], "coder")
            self.assertEqual(startup["launch_mode"], "dispatch")
            self.assertIn("run task", startup["action"])
            self.assertIn("cartopian-claude", startup["action"])
            self.assertEqual(record["blockers"], [])
            self.assertIsNone(record["state_filesystem_disagreement"])

    def test_missing_launch_prerequisite_is_a_blocked_verdict_with_owner(self):
        toml = _TOML_REVIEW_OFF.replace('agent = "cartopian-claude"\n', "").replace(
            'auto_launch = ["task_run"]\n', ""
        )
        with _isolated_home(), project_scaffold(cartopian_toml=toml) as scaffold:
            root = _plan_project(scaffold)
            code, records, err = self._next_action(root)
            self.assertEqual(code, 0, err)
            startup = records[0]["startup"]
            # No agent means a manual handoff, which is still a valid ready
            # state — the action names it explicitly.
            self.assertEqual(startup["verdict"], "ready", startup)
            self.assertEqual(startup["launch_mode"], "manual")
            self.assertIn("manually", startup["action"])

    def test_missing_agent_executable_is_blocked_for_the_operator(self):
        toml = _TOML_REVIEW_OFF.replace(
            'agent = "cartopian-claude"', 'agent = "/definitely/missing/cartopian-agent"'
        )
        with _isolated_home(), project_scaffold(cartopian_toml=toml) as scaffold:
            root = _plan_project(scaffold)
            code, records, err = self._next_action(root)
            self.assertEqual(code, 0, err)
            startup = records[0]["startup"]
            self.assertEqual(startup["verdict"], "blocked", startup)
            self.assertEqual(startup["owner"], "operator")
            self.assertIn("not found on PATH", startup["detail"])

    def test_readiness_defect_is_blocked_with_pm_recovery(self):
        with _isolated_home(), project_scaffold(cartopian_toml=_TOML_REVIEW_OFF) as scaffold:
            root = _plan_project(scaffold)
            task = root / "tasks" / "open" / "TASK-01-001.md"
            task.write_text(
                task.read_text(encoding="utf-8").replace(
                    "Evidence gate: n/a", "Evidence gate: maybe"
                ),
                encoding="utf-8",
            )
            code, records, err = self._next_action(root)
            self.assertEqual(code, 0, err)
            startup = records[0]["startup"]
            self.assertEqual(startup["verdict"], "blocked")
            self.assertIn("evidence-gate-valid", startup["detail"])
            self.assertEqual(startup["owner"], "pm")
            self.assertIn("validate-task-readiness", startup["action"])

    def test_blocker_owner_and_recovery_are_named(self):
        with _isolated_home(), project_scaffold(cartopian_toml=_TOML_REVIEW_OFF) as scaffold:
            root = _plan_project(scaffold)
            body = compose_state.compose_record(root, "Kickoff Project")["rendered_body"]
            (root / "STATE.md").write_text(
                body + "\n\n## Situation\n\n- reviewer login expired; operator re-authenticating\n",
                encoding="utf-8",
            )
            code, records, err = self._next_action(root)
            startup = records[0]["startup"]
            self.assertEqual(startup["verdict"], "blocked")
            self.assertEqual(startup["owner"], "pm")
            self.assertIn("write-state", startup["action"])

    def test_plan_complete_verdict(self):
        with _isolated_home(), project_scaffold(cartopian_toml=_TOML_REVIEW_OFF) as scaffold:
            root = _plan_project(scaffold)
            task = root / "tasks" / "open" / "TASK-01-001.md"
            task.rename(root / "tasks" / "done" / "TASK-01-001.md")
            code, records, err = self._next_action(root)
            self.assertEqual(records[0]["startup"]["verdict"], "plan-complete")
            self.assertIn("close plan", records[0]["startup"]["action"])

    def test_reconcile_refreshes_a_stale_state_body_and_keeps_notes(self):
        with _isolated_home(), project_scaffold(cartopian_toml=_TOML_REVIEW_OFF) as scaffold:
            root = _plan_project(scaffold)
            (root / "STATE.md").write_text(
                "# Kickoff Project - State\n\n## Current phase\n\nPHASE-01\n\n"
                "## Active work\n\nTASK-01-001 is `in-progress`\n\n## Open work\n\nNone\n\n"
                "## What to do next\n\nNo active or open work remains; review closeout readiness.\n\n"
                "## Situation\n\n- keep me\n",
                encoding="utf-8",
            )
            code, records, err = self._next_action(root, reconcile=True)
            self.assertEqual(code, 0, err)
            self.assertTrue(records[0]["state_reconciled"])
            self.assertIsNone(records[0]["state_filesystem_disagreement"])
            text = (root / "STATE.md").read_text(encoding="utf-8")
            self.assertIn("Start TASK-01-001", text)
            self.assertIn("- keep me", text)
            code, records, err = self._next_action(root, reconcile=True)
            self.assertFalse(records[0]["state_reconciled"])


class RehearsalTests(unittest.TestCase):
    def test_readiness_flag_adds_a_rehearsal_record(self):
        with _isolated_home(), project_scaffold(cartopian_toml=_TOML_REVIEW_OFF) as scaffold:
            root = _plan_project(scaffold)
            task = root / "tasks" / "open" / "TASK-01-001.md"
            code, records, err = _run(
                validate_task_readiness.handler,
                task_path=str(task),
                rehearse_dispatch=True,
                role=None,
            )
            self.assertEqual(code, 0, err)
            rehearsal = records[0]["rehearsal"]
            self.assertTrue(rehearsal["ok"], rehearsal)
            self.assertEqual(rehearsal["role"], "coder")
            self.assertEqual(rehearsal["role_source"], "assignee-header")
            self.assertEqual(rehearsal["compose"]["outcome"], "composed")
            self.assertEqual(rehearsal["launch"]["mode"], "dispatch")
            self.assertNotIn("rehearsal", _run(
                validate_task_readiness.handler, task_path=str(task),
                rehearse_dispatch=False, role=None,
            )[1][0])

    def test_unknown_role_fails_the_rehearsal_not_readiness(self):
        with _isolated_home(), project_scaffold(cartopian_toml=_TOML_REVIEW_OFF) as scaffold:
            root = _plan_project(scaffold)
            task = root / "tasks" / "open" / "TASK-01-001.md"
            code, records, err = _run(
                validate_task_readiness.handler,
                task_path=str(task),
                rehearse_dispatch=True,
                role="ghost",
            )
            self.assertNotEqual(code, 0)
            self.assertTrue(records[0]["ready"])
            self.assertFalse(records[0]["rehearsal"]["ok"])
            self.assertIn("not declared", err)

    def test_role_resolution_order(self):
        roles = {
            "pm": {"auto_launch": []},
            "coder": {"auto_launch": ["task_run"]},
            "reviewer": {"auto_launch": []},
        }
        self.assertEqual(
            dispatch_rehearsal.resolve_role(roles, "Assignee: reviewer\n")["source"],
            "assignee-header",
        )
        self.assertEqual(
            dispatch_rehearsal.resolve_role(roles, "Assignee: someone\n")["role"], "coder"
        )
        roles["reviewer"]["auto_launch"] = ["task_run"]
        self.assertIsNone(dispatch_rehearsal.resolve_role(roles, "")["role"])


class ScopedApplicabilityTests(unittest.TestCase):
    CRITERIA = list(at._ANCHOR_CRITERIA)
    SOURCES = list(at._ANCHOR_SOURCES)
    EXCERPTS = list(at._ANCHOR_EXCERPTS)

    def _records(self, *tail):
        base = [r for r in at._ANCHOR_RECORDS if not r.startswith("W|")]
        return base + list(tail)

    def _build(self, records, excerpts=None):
        return at.build(
            spec_acceptance=self.CRITERIA,
            task_acceptance=[],
            record_set=at.parse_record_set(records),
            sources=self.SOURCES,
            excerpts=self.EXCERPTS if excerpts is None else excerpts,
        )

    def test_outside_scope_record_covers_without_operator_authority(self):
        trace = self._build(self._records(
            f"A|{self.EXCERPTS[0]}|outside-scope|documentation access grant; governs no criterion of this task"
        ))
        self.assertTrue(trace.coverage.request_complete)
        self.assertEqual(trace.closure_findings(), [])
        self.assertEqual(len(trace.applicability), 1)
        reviewer = trace.reviewer_projection()
        self.assertIn("Applicability: 1", reviewer)
        self.assertIn("A|REQ-001", reviewer)
        self.assertNotIn(f"R|{self.EXCERPTS[0]}", reviewer)
        self.assertLessEqual(trace.bounds()["routine_bytes"], trace.bounds()["b_routine"])

    def test_an_applicability_record_changes_the_trace_identity(self):
        before = self._build(self._records())
        after = self._build(self._records(
            f"A|{self.EXCERPTS[0]}|outside-scope|governs no criterion of this task"
        ))
        self.assertNotEqual(before.trace_identity(), after.trace_identity())
        self.assertIn(f"A|{self.EXCERPTS[0]}", after.trace_body())
        # A review recorded against the earlier identity is stale for the scoped trace.
        review = (
            "## Closure determinations\n\n"
            f"Trace-identity: {before.trace_identity()}\n"
            + "".join(
                f"D1 {c.ordinal}: pass reason:-\nD2 {c.ordinal}: pass reason:-\n"
                for c in after.criteria
            )
            + "D2 task: pass reason:-\n"
        )
        closure = at.evaluate_closure(after, review, attributed_to="reviewer")
        self.assertIn("trace-identity-mismatch", [b.code for b in closure.blockers])

    def test_governing_constraint_is_the_other_class(self):
        trace = self._build(self._records(
            f"A|{self.EXCERPTS[0]}|governing-constraint|carried as a constraint in the task body"
        ))
        self.assertTrue(trace.coverage.request_complete)
        self.assertEqual(trace.as_record()["applicability"][0]["class"], "governing-constraint")

    def test_unscoped_excerpt_is_still_uncovered(self):
        trace = self._build(self._records())
        self.assertFalse(trace.coverage.request_complete)
        self.assertEqual(
            [f.code for f in trace.closure_findings()], ["request-uncovered"]
        )

    def test_unknown_class_fails_closed(self):
        with self.assertRaises(at.TraceRefusal) as ctx:
            at.parse_record_set([f"A|{self.EXCERPTS[0]}|irrelevant|x"])
        self.assertEqual(ctx.exception.code, "trace-unparseable")

    def test_applicability_and_waiver_on_one_identity_conflict(self):
        with self.assertRaises(at.TraceRefusal) as ctx:
            self._build(self._records(
                f"W|{self.EXCERPTS[0]}|background-scope|x",
                f"A|{self.EXCERPTS[0]}|outside-scope|y",
            ))
        self.assertEqual(ctx.exception.code, "trace-unparseable")

    def test_anchor_is_unchanged(self):
        self.assertTrue(at.conformance_anchor()["conforms"])

    def test_compose_from_mapping_reproduces_the_anchor(self):
        mapping = {
            "edges": [
                {"criterion": "C01", "type": "operator-request", "source": "REQ-003", "context": "evidence order 3, observed 2026-08-14"},
                {"criterion": "C01", "type": "standard", "source": self.SOURCES[1], "context": "installed Cartopian v1.6.40"},
                {"criterion": self.CRITERIA[1], "type": "requirement", "source": self.SOURCES[0], "context": "active Product Refinement contract as of 2026-08-14"},
                {"criterion": "C03", "type": "operator-request", "source": "REQ-002", "context": "evidence order 2, observed 2026-08-14"},
                {"criterion": "C04", "type": "decision", "source": self.SOURCES[2], "context": "locked 2026-08-14"},
                {"criterion": "C04", "type": "spec", "source": "spec-clause sha256:a9a822e60b9a1d42ed2d69239bb783d5dc60ab96ecc6671dbbcc69b37faeb91a", "context": "locked 2026-08-14"},
                {"criterion": "C05", "type": "plan-item", "source": self.SOURCES[3], "context": "operator-approved three-track Phase 05 decomposition as of 2026-08-14"},
            ],
            "waivers": [{"identity": "REQ-001", "class": "procedural-authorization", "scope": "grants documentation access for this assignment; states no product behavior"}],
        }
        lines = at.compose_records(
            spec_acceptance=self.CRITERIA, task_acceptance=[], mapping=mapping, excerpts=self.EXCERPTS
        )
        self.assertEqual(lines, list(at._ANCHOR_RECORDS))

    def test_compose_rejects_an_unknown_criterion(self):
        with self.assertRaises(at.TraceRefusal):
            at.compose_records(
                spec_acceptance=self.CRITERIA,
                task_acceptance=[],
                mapping={"edges": [{"criterion": "C09", "type": "requirement", "source": "x", "context": "y"}]},
            )


class TraceCommandAuthoringTests(unittest.TestCase):
    """`acceptance-trace --enumerate` and `--compose-from` on a real project."""

    def _project(self, scaffold):
        root = scaffold.project_root
        _capture(root, REQUEST_TEXT, record_id="REQUEST-001", sequence=0)
        _capture(
            root, PRICING_TEXT, record_id="REQUEST-001-CORRECTION-001", sequence=1,
            kind="correction", correction_of="REQUEST-001",
        )
        scaffold.write("IMPLEMENTATION_PLAN.md", "# Plan\n\n- `RESEARCH-01-001` — inventory.\n")
        scaffold.write("phases/PHASE-01.md", "# PHASE-01\n\n- `RESEARCH-01-001` — inventory.\n")
        body = TASK_BODY.replace("Upstream trace: n/a", "Upstream trace: required").replace(
            "Spec: none", "Deliverable: project:resources/research/inventory.md\nSpec: none"
        )
        task = scaffold.write("tasks/open/TASK-01-001.md", body)
        return root, task

    def test_enumerate_lists_criteria_sources_and_excerpts(self):
        with _isolated_home(), project_scaffold(cartopian_toml=_TOML_REVIEW_OFF) as scaffold:
            root, task = self._project(scaffold)
            code, records, err = _run(
                trace_command.handler, project_root=str(root), task=str(task),
                projection=None, anchor=False, enumerate_inputs=True, compose_from=None,
            )
            self.assertEqual(code, 0, err)
            record = records[0]
            self.assertEqual(record["mode"], "enumerate")
            self.assertEqual(record["criteria"][0]["ordinal"], "C01")
            self.assertEqual(record["criteria"][0]["digest12"], at.digest12(
                "The catalog inventory lists every vendor with a current contract."
            ))
            aliases = [e["alias"] for e in record["excerpts"]]
            self.assertEqual(aliases, ["REQ-001", "REQ-002"])
            self.assertIn("Pricing", record["excerpts"][1]["preview"])

    def test_compose_from_renders_a_validated_block_with_scoping(self):
        with _isolated_home(), project_scaffold(cartopian_toml=_TOML_REVIEW_OFF) as scaffold:
            root, task = self._project(scaffold)
            mapping = scaffold.root / "mapping.json"
            mapping.write_text(json.dumps({
                "edges": [{
                    "criterion": "C01", "type": "operator-request", "source": "REQ-001",
                    "context": "evidence order 1",
                }],
                "exemptions": [
                    {"criterion": "C02", "reason": "derived-mechanical"},
                    {"criterion": "C03", "reason": "template-fixed"},
                ],
                "applicability": [{
                    "identity": "REQ-002", "class": "outside-scope",
                    "scope": "pricing statement; this task inventories vendors only",
                }],
            }), encoding="utf-8")
            code, records, err = _run(
                trace_command.handler, project_root=str(root), task=str(task),
                projection=None, anchor=False, enumerate_inputs=False,
                compose_from=str(mapping),
            )
            self.assertEqual(code, 0, err)
            record = records[0]
            self.assertEqual(record["mode"], "compose")
            self.assertTrue(record["block"].startswith("```trace\n"))
            self.assertIn("A|REQ-002 sha256:", record["block"])
            self.assertEqual(record["trace"]["request_coverage"], "complete")
            self.assertEqual(record["trace"]["closure_findings"], [])
            # The block round-trips through readiness once pasted into the task.
            text = task.read_text(encoding="utf-8") + "\n## Upstream trace\n\n" + record["block"]
            task.write_text(text, encoding="utf-8")
            code, records, err = _run(
                validate_task_readiness.handler, task_path=str(task),
                rehearse_dispatch=False, role=None,
            )
            self.assertEqual(code, 0, err)
            trace_check = [c for c in records[0]["checks"] if c["name"] == "upstream-trace-valid"][0]
            self.assertTrue(trace_check["pass"], trace_check)
            # Plan-level coverage: the scoped-out excerpt is reported, not lost.
            code, records, err = _run(plan_audit.handler, project_path=str(root))
            kinds = [w["kind"] for w in records[0]["warnings"]]
            self.assertIn("request-outside-every-task", kinds)
            # Closeout is gated on the same fact: an unclaimed excerpt blocks.
            task.rename(root / "tasks" / "done" / "TASK-01-001.md")
            scaffold.write("STATE.md", "# state\n")
            code, records, err = _run(close_audit.handler, project_path=str(root))
            self.assertEqual(code, 0, err)
            self.assertFalse(records[0]["closable"])
            self.assertTrue(
                any("unclaimed operator request" in r for r in records[0]["blocking_reasons"]),
                records[0]["blocking_reasons"],
            )
            # An authorized plan-level disposition in a decision clears both.
            digest = records[0]["unclaimed_requests"][0]["content_identity"]
            scaffold.write(
                "decisions/DEC-001.md",
                "# DEC-001: Pricing statement is out of this plan\n\nDate: 2026-09-07\n"
                f"Status: locked\nSupersedes: none\nOut-of-plan request: {digest}\n\n"
                "## Context\n\nPricing is governed by the next plan.\n",
            )
            code, records, err = _run(close_audit.handler, project_path=str(root))
            self.assertNotIn(
                "unclaimed operator request",
                " ".join(records[0]["blocking_reasons"]),
            )
            code, records, err = _run(plan_audit.handler, project_path=str(root))
            self.assertNotIn(
                "request-outside-every-task", [w["kind"] for w in records[0]["warnings"]]
            )

            def blocked() -> bool:
                _code, recs, _err = _run(close_audit.handler, project_path=str(root))
                return any(
                    "unclaimed operator request" in r for r in recs[0]["blocking_reasons"]
                )

            # An open decision is a proposal, not authorization.
            decision = root / "decisions" / "DEC-001.md"
            locked_text = decision.read_text(encoding="utf-8")
            decision.write_text(
                locked_text.replace("Status: locked", "Status: open"), encoding="utf-8"
            )
            self.assertTrue(blocked())
            decision.write_text(locked_text, encoding="utf-8")
            self.assertFalse(blocked())
            # A later locked decision that supersedes the exclusion retires it.
            scaffold.write(
                "decisions/DEC-002.md",
                "# DEC-002: Pricing is back in scope\n\nDate: 2026-09-08\n"
                "Status: locked\nSupersedes: DEC-001\n\n## Context\n\nRestored.\n",
            )
            self.assertTrue(blocked())


if __name__ == "__main__":
    unittest.main()
