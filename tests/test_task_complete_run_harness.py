"""Executable proof of the `task-complete` run boundary (SPEC-06-004).

`tests/test_run_boundary_contract.py` proves the configuration half of the
contract and pins the protocol and skill prose that the PM reads. What it
cannot show is what the boundary is *for*: that one initiated run carries one
task through however many activities that task needs, stops the moment the
task is `done`, and never touches the task standing next in the queue.

This module proves that half by running it. A deterministic driver
(:class:`TaskCompleteRun`) plays the PM's continuation loop over a real
temporary project, and every action it takes is a real product command —
`next-action` selects and orients, `move-task` transitions, `dispatch`
launches an assignee wrapper for real, `wait-handoff` observes the
publication, and `report-action`/`move-task` route the terminal result. The
driver contributes exactly one thing of its own: the boundary rule from
`protocol/CONVENTIONS.md` § Handoffs, applied to the `run_boundary` the
product resolved.

Two independent observations decide the no-cross-task requirement, and
neither is the driver's own bookkeeping:

* the effect log, which names the task identity of every command the driver
  issued; and
* the filesystem after the run — the other task's bytes, its status
  directory, and the absence of any prompt, report, review, or launch
  belonging to it.

What this does not claim: no language model is executed here, so this is
evidence about the run boundary's mechanics and containment, not about an
agent's willingness to follow prose. The prose itself is pinned separately.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest import mock

from cli import request_trace
from cli.main import EXIT_OK, build_parser

# The assignee stand-in. `dispatch` launches this for real, so the run crosses
# a genuine process boundary and publishes through the ordinary report slots.
# Which artifact it publishes is keyed on the role dispatch exported, and each
# body is supplied by the test as a template path — the stub invents nothing.
_STUB_SOURCE = '''#!/usr/bin/env python3
import os
import sys

prompt = sys.argv[1] if len(sys.argv) > 1 else None
role = os.environ.get("CARTOPIAN_ROLE", "")
if os.environ.get("HARNESS_SUPPRESS") == role or not prompt:
    sys.exit(0)

root = os.path.dirname(os.path.dirname(os.path.abspath(prompt)))
nn = os.path.basename(prompt)[len("PROMPT-"):-len(".md")]


def publish(relative, template_env):
    template = os.environ.get(template_env)
    if not template:
        return
    target = os.path.join(root, relative % nn)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(template, encoding="utf-8") as handle:
        body = handle.read()
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(body.replace("{nn}", nn).replace("{root}", root))


if role == os.environ.get("HARNESS_REVIEW_ROLE"):
    publish("reports/REPORT-%s-review.md", "HARNESS_REVIEW_REPORT")
    publish("reviews/REVIEW-%s.md", "HARNESS_REVIEW_FILE")
else:
    publish("reports/REPORT-%s.md", "HARNESS_TASK_REPORT")
'''

# Deliberately domain-neutral: no role name, activity, or artifact word here
# belongs to software work. `contributor` carries the assignment, `auditor`
# audits the outcome, and both are ordinary lifecycle activities.
_CONFIG = """[project]
id = "run-boundary-harness"
name = "Run Boundary Harness"
project_schema_version = "v0.13.0"

[automation]
initiation = "operator"
run_boundary = "{boundary}"

[roles.contributor]
description = "Carries out the assigned activity for a task."
agent = "{stub}"
auto_launch = ["task_run"]
timeout = "20s"

[roles.auditor]
description = "Audits a completed task before it closes."
agent = "{stub}"
auto_launch = ["task_review"]
timeout = "20s"

[reviews]
planning = "off"
task_closure = "required"
task_role = "auditor"
"""

_TASK = """# TASK-{nn}: {title}

Phase: PHASE-01
Plan ref: BUILD-01-001
Work root: n/a
Assignee: contributor
Depends on: none
Blocked by: none

## Goal

{title}.
"""

_PHASE = """# PHASE-01: Carry two tasks

## Exit criteria

- `TASK-01-001`
- `TASK-01-002`
"""

_TASK_REPORT = """# REPORT-{nn}

Status: __STATUS__

## Identity

- Work root: n/a

## Completion evidence

The assigned activity was carried out and its outcome recorded.

## Remaining risks

None.

## Ready to close

yes
"""

_REVIEW_REPORT = """# REPORT-{nn}-review

Status: complete
Request alignment: aligned
Request evidence: REQUEST-001

## Identity

- Review ID: REVIEW-{nn}
- Prompt path: {root}/prompts/PROMPT-{nn}.md
- Task path: {root}/tasks/in-review/TASK-{nn}.md
- Review file path: {root}/reviews/REVIEW-{nn}.md

## Evidence reviewed

The preserved completion report for the bound task.

## Verdict

__VERDICT__

## Blocking findings

none.
"""

_REVIEW_FILE = """# REVIEW-{nn}

Target: TASK-{nn}
Verdict: __VERDICT__
Request alignment: aligned
Request evidence: REQUEST-001
"""

BOUND = "TASK-01-001"
OTHER = "TASK-01-002"


@dataclass(frozen=True)
class Effect:
    """One product command the run issued, and the task it named."""

    kind: str
    task_id: str
    detail: str


@dataclass
class RunOutcome:
    boundary: str
    bound_task: Optional[str]
    activities: List[str] = field(default_factory=list)
    effects: List[Effect] = field(default_factory=list)
    terminal_reason: str = "not-run"

    def effects_for(self, task_id: str) -> List[Effect]:
        return [effect for effect in self.effects if effect.task_id == task_id]


class TaskCompleteRun:
    """A deterministic driver for one initiated run.

    The loop is the protocol's, not this class's invention: ask the product
    where the project stands, continue the next authorized activity for the
    bound task, and stop when the resolved `run_boundary` says the run is
    over. Every step below is a real command; the only judgment the driver
    makes is which lifecycle activity applies to the bound task's current
    status, which the protocol fixes and no work domain changes.
    """

    # The lifecycle activity each status hands to next. It names no artifact
    # kind and no domain — a policy brief, a field survey, and a code change
    # all pass through the same three statuses.
    _ACTIVITY_FOR_STATUS = {
        "open": "task_run",
        "in-progress": "task_run",
        "in-review": "task_review",
    }
    _MAX_ACTIVITIES = 6

    def __init__(
        self,
        project_root: Path,
        home: Path,
        *,
        wait_budget: str = "20s",
    ):
        self.project_root = project_root
        self.home = home
        self.wait_budget = wait_budget
        self.budget_spent = 0
        self.outcome = RunOutcome(boundary="", bound_task=None)

    # -- product command seams ---------------------------------------------
    def _cli(self, *argv: str) -> Tuple[int, List[Dict[str, Any]], str]:
        parser = build_parser()
        out, err = io.StringIO(), io.StringIO()
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("CARTOPIAN_MCP_")
        }
        env["HOME"] = str(self.home)
        code = 0
        with (
            mock.patch.dict(os.environ, env, clear=True),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            try:
                args = parser.parse_args(list(argv))
                handler = getattr(args, "_handler", None)
                code = handler(args) if handler is not None else 2
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 2
        records = [
            json.loads(line)
            for line in out.getvalue().splitlines()
            if line.strip()
        ]
        return code, records, err.getvalue()

    def _next_action(self) -> Optional[Dict[str, Any]]:
        code, records, _err = self._cli("next-action", str(self.project_root))
        return records[0] if code == EXIT_OK and records else None

    def _task_path(self, task_id: str) -> Optional[Path]:
        for status in ("open", "in-progress", "in-review", "done"):
            candidate = (
                self.project_root / "tasks" / status / f"{task_id}.md"
            )
            if candidate.is_file():
                return candidate
        return None

    def _status(self, task_id: str) -> Optional[str]:
        path = self._task_path(task_id)
        return None if path is None else path.parent.name

    def _assignee(self, task_id: str) -> Optional[str]:
        """The role the task itself names, so the driver invents no role."""
        path = self._task_path(task_id)
        if path is None:
            return None
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("Assignee:"):
                return line.partition(":")[2].strip() or None
        return None

    # -- one activity, end to end ------------------------------------------
    def _move(self, task_id: str, to_status: str) -> bool:
        path = self._task_path(task_id)
        code, _records, _err = self._cli("move-task", str(path), to_status)
        if code != EXIT_OK:
            return False
        self.outcome.effects.append(
            Effect("lifecycle-move", task_id, f"-> {to_status}")
        )
        return True

    def _compose_prompt(self, task_id: str, activity: str) -> bool:
        path = self._task_path(task_id)
        try:
            if activity == "task_review":
                context = request_trace.context_for_task(
                    self.project_root, path, require_completion_evidence=True
                )
                heading = f"# Audit TASK-{task_id.removeprefix('TASK-')}\n"
            else:
                context = request_trace.context_for_task_assignment(
                    self.project_root, path
                )
                heading = f"# Assignment for {task_id}\n\n## Your task\n\nCarry out the goal.\n"
        except request_trace.RequestRefusal:
            return False
        prompt = (
            self.project_root
            / "prompts"
            / f"PROMPT-{task_id.removeprefix('TASK-')}.md"
        )
        prompt.write_text(
            request_trace.upsert_request_sections(heading, context.section),
            encoding="utf-8",
        )
        self.outcome.effects.append(
            Effect("prompt-composition", task_id, prompt.name)
        )
        return True

    def _dispatch(self, task_id: str, role: str) -> bool:
        path = self._task_path(task_id)
        self.outcome.effects.append(Effect("dispatch", task_id, role))
        code, _records, _err = self._cli(
            "dispatch", str(path), "--role", role
        )
        return code == EXIT_OK

    def _wait(self, task_id: str, role: str) -> Optional[Dict[str, Any]]:
        path = self._task_path(task_id)
        _code, records, _err = self._cli(
            "wait-handoff",
            str(path),
            "--role",
            role,
            "--max-block",
            self.wait_budget,
            "--poll-interval",
            "0.05",
        )
        observation = records[-1] if records else None
        self.outcome.effects.append(
            Effect(
                "wait",
                task_id,
                "absent"
                if observation is None
                else str(observation.get("classification")),
            )
        )
        return observation

    def _route(
        self, task_id: str, observation: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Route the accepted publication through the product's own parser.

        The routing record — not the driver — decides which status the task
        moves to next, so the lifecycle step is product-computed too.
        """
        code, records, _err = self._cli(
            "report-action",
            observation["report_path"],
            "--expected-identity",
            observation["report_content_identity"],
        )
        record = records[-1] if records else None
        self.outcome.effects.append(
            Effect(
                "report-routing",
                task_id,
                "refused" if record is None else str(record.get("verdict")),
            )
        )
        if code != EXIT_OK or record is None:
            return None
        return record

    def _activity(self, task_id: str, activity: str, role: str) -> Optional[str]:
        """Run one authorized activity. Returns a stop reason, or None."""
        if activity == "task_run" and self._status(task_id) == "open":
            if not self._move(task_id, "in-progress"):
                return "lifecycle-move-refused"
        if not self._compose_prompt(task_id, activity):
            return "prompt-composition-refused"
        if not self._dispatch(task_id, role):
            return "dispatch-refused"
        observation = self._wait(task_id, role)
        if observation is None or not observation.get("terminal"):
            return "no-terminal-publication"
        if observation.get("classification") != "accepted":
            return f"handoff-{observation.get('classification')}"
        routing = self._route(task_id, observation)
        if routing is None or routing.get("verdict") != "accepted":
            return "report-routing-refused"
        forward = routing.get("target_task_status")
        if not forward:
            return "unrouted-terminal-result"
        if not self._move(task_id, forward):
            return f"closure-refused-at-{forward}"
        self.outcome.activities.append(activity)
        return None

    # -- the run ------------------------------------------------------------
    def run(self) -> RunOutcome:
        record = self._next_action()
        if record is None:
            self.outcome.terminal_reason = "orientation-failed"
            return self.outcome
        self.outcome.boundary = record["automation"]["run_boundary"]
        selected = record["active_task"] or record["next_open_task"]
        if selected is None:
            self.outcome.terminal_reason = "no-task-to-bind"
            return self.outcome
        # Initiation binds the task the product selected, and the binding is
        # never recomputed: this identity is the whole run from here.
        self.outcome.bound_task = selected["id"]
        self.outcome.effects.append(
            Effect("selection", selected["id"], "next-action")
        )
        # No default: an unnamed audit role is an unauthorized activity, not
        # one the run may pick a role for. The policy is read where the
        # product publishes it, not guessed.
        review_role = (record["reviews"].get("task_closure") or {}).get("role")

        for _ in range(self._MAX_ACTIVITIES):
            status = self._status(self.outcome.bound_task)
            if status == "done":
                # The boundary fires here, before anything is asked about the
                # queue: no selection, no move, no prompt, no dispatch.
                self.outcome.terminal_reason = "bound-task-done"
                return self.outcome
            activity = self._ACTIVITY_FOR_STATUS.get(status)
            if activity is None:
                self.outcome.terminal_reason = f"unroutable-status-{status}"
                return self.outcome
            # Both roles come from governed data: the assignment role from
            # the task's own `Assignee:`, the audit role from review policy.
            role = (
                self._assignee(self.outcome.bound_task)
                if activity == "task_run"
                else review_role
            )
            if role is None:
                self.outcome.terminal_reason = "no-authorized-assignee"
                return self.outcome
            stop = self._activity(self.outcome.bound_task, activity, role)
            if stop is not None:
                self.outcome.terminal_reason = stop
                return self.outcome
            self.budget_spent += 1
            # The resolved boundary, and nothing else, decides whether the run
            # is still open. `task-complete` is the only value that keeps it
            # open across activities of the same task.
            if self.outcome.boundary == "handoff-budget":
                if self.budget_spent >= (
                    record["automation"]["max_handoffs_per_run"]
                ):
                    self.outcome.terminal_reason = "handoff-budget-spent"
                    return self.outcome
            elif self.outcome.boundary != "task-complete":
                self.outcome.terminal_reason = "handoff-complete-boundary"
                return self.outcome
        self.outcome.terminal_reason = "activity-ceiling"
        return self.outcome


class _Harness:
    """A registered two-task project whose queue outlives the bound task."""

    def __init__(
        self,
        *,
        boundary: str = "task-complete",
        verdict: str = "approve",
        task_status: str = "complete",
        suppress: Optional[str] = None,
        review_auto_launch: bool = True,
        budget: Optional[int] = None,
        wait_budget: str = "20s",
    ) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="cartopian-run-boundary-")
        root = Path(self._tmp.name)
        self.home = root / "home"
        (self.home / ".cartopian").mkdir(parents=True)
        self.project_root = root / "project"
        for sub in (
            "decisions", "phases", "prompts", "reports", "reviews", "specs",
            "tasks/open", "tasks/in-progress", "tasks/in-review", "tasks/done",
        ):
            (self.project_root / sub).mkdir(parents=True)

        self.stub = root / "assignee-stub"
        self.stub.write_text(_STUB_SOURCE, encoding="utf-8")
        self.stub.chmod(
            self.stub.stat().st_mode
            | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )

        config = _CONFIG.format(stub=self.stub, boundary=boundary)
        if budget is not None:
            config = config.replace(
                f'run_boundary = "{boundary}"',
                f'run_boundary = "{boundary}"\n'
                f"max_handoffs_per_run = {budget}",
            )
        if not review_auto_launch:
            config = config.replace(
                'auto_launch = ["task_review"]', "auto_launch = []"
            )
        (self.project_root / "cartopian.toml").write_text(
            config, encoding="utf-8"
        )
        self.wait_budget = wait_budget
        (self.project_root / "STATE.md").write_text(
            "# STATE\n\n## Current position\n\nStarting.\n", encoding="utf-8"
        )
        (self.project_root / "phases" / "PHASE-01.md").write_text(
            _PHASE, encoding="utf-8"
        )
        for nn, title in (
            ("01-001", "Carry the bound task to done"),
            ("01-002", "Stay untouched in the queue"),
        ):
            (self.project_root / "tasks" / "open" / f"TASK-{nn}.md").write_text(
                _TASK.format(nn=nn, title=title), encoding="utf-8"
            )
        (self.home / ".cartopian" / "projects.json").write_text(
            json.dumps(
                [{"id": "run-boundary-harness", "path": str(self.project_root)}]
            ),
            encoding="utf-8",
        )

        from tests.scaffold import capture_request_for_test

        # One immutable operator record per task; both tasks are equally
        # authorized, so nothing about the queue explains the containment.
        for index, task_id in enumerate((BOUND, OTHER), start=1):
            capture_request_for_test(
                self.project_root,
                request_id=f"REQUEST-00{index}",
                unit=f"task:{task_id}",
                text=f"Carry {task_id} to its goal.",
            )

        templates = root / "templates"
        templates.mkdir()
        self.templates = {
            "task": templates / "task-report.md",
            "review-report": templates / "review-report.md",
            "review-file": templates / "review-file.md",
        }
        self.templates["task"].write_text(
            _TASK_REPORT.replace("__STATUS__", task_status), encoding="utf-8"
        )
        self.templates["review-report"].write_text(
            _REVIEW_REPORT.replace("__VERDICT__", verdict), encoding="utf-8"
        )
        self.templates["review-file"].write_text(
            _REVIEW_FILE.replace("__VERDICT__", verdict), encoding="utf-8"
        )
        self.suppress = suppress

    def __enter__(self) -> "_Harness":
        return self

    def __exit__(self, *_exc) -> None:
        self._tmp.cleanup()

    def run(self) -> RunOutcome:
        env = {
            "HARNESS_TASK_REPORT": str(self.templates["task"]),
            "HARNESS_REVIEW_REPORT": str(self.templates["review-report"]),
            "HARNESS_REVIEW_FILE": str(self.templates["review-file"]),
            "HARNESS_REVIEW_ROLE": "auditor",
        }
        if self.suppress:
            env["HARNESS_SUPPRESS"] = self.suppress
        with mock.patch.dict(os.environ, env, clear=False):
            return TaskCompleteRun(
                self.project_root, self.home, wait_budget=self.wait_budget
            ).run()

    # -- filesystem observation, independent of the driver's own log --------
    def artifacts_for(self, task_id: str) -> List[str]:
        nn = task_id.removeprefix("TASK-")
        found = []
        for folder, pattern in (
            ("prompts", f"PROMPT-{nn}*.md"),
            ("reports", f"REPORT-{nn}*.md"),
            ("reviews", f"REVIEW-{nn}*.md"),
        ):
            found.extend(
                str(path.relative_to(self.project_root))
                for path in sorted((self.project_root / folder).glob(pattern))
            )
        return found

    def status(self, task_id: str) -> Optional[str]:
        for status in ("open", "in-progress", "in-review", "done"):
            if (self.project_root / "tasks" / status / f"{task_id}.md").is_file():
                return status
        return None

    def task_bytes(self, task_id: str) -> bytes:
        status = self.status(task_id)
        path = self.project_root / "tasks" / status / f"{task_id}.md"
        return path.read_bytes()


class TestTaskCompleteContinuation(unittest.TestCase):
    """One initiation, one task, however many activities that task needs."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.harness = _Harness()
        cls.outcome = cls.harness.run()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.harness.__exit__()

    def test_the_bound_task_is_the_one_the_product_selected(self) -> None:
        self.assertEqual(self.outcome.boundary, "task-complete")
        self.assertEqual(self.outcome.bound_task, BOUND)
        selections = [
            effect for effect in self.outcome.effects
            if effect.kind == "selection"
        ]
        # Selection happens once, at initiation. A run that re-selects has
        # already left the task it was bound to.
        self.assertEqual([effect.task_id for effect in selections], [BOUND])

    def test_the_run_continues_more_than_one_activity_for_that_task(
        self,
    ) -> None:
        self.assertEqual(self.outcome.activities, ["task_run", "task_review"])
        # Two distinct authorized activities, two distinct assignees, one run.
        dispatched = [
            effect.detail for effect in self.outcome.effects
            if effect.kind == "dispatch"
        ]
        self.assertEqual(dispatched, ["contributor", "auditor"])

    def test_the_continued_activities_name_no_work_domain(self) -> None:
        # The contract is domain-neutral: nothing the run continued on is a
        # coding, review-of-code, or artifact-kind assumption. The activity
        # names are lifecycle names and the roles are ordinary role labels.
        self.assertEqual(
            set(self.outcome.activities), {"task_run", "task_review"}
        )
        for word in ("code", "coder", "commit", "build", "test", "deploy"):
            self.assertNotIn(
                word,
                (self.harness.project_root / "cartopian.toml")
                .read_text(encoding="utf-8")
                .lower(),
            )

    def test_the_run_ends_when_the_bound_task_reaches_done(self) -> None:
        self.assertEqual(self.outcome.terminal_reason, "bound-task-done")
        self.assertEqual(self.harness.status(BOUND), "done")


class TestNoCrossTaskEffect(unittest.TestCase):
    """`done` returns control before anything happens to the next task."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.harness = _Harness()
        cls.outcome = cls.harness.run()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.harness.__exit__()

    def test_the_other_task_was_ready_the_whole_time(self) -> None:
        # Containment only means something if the other task could have been
        # started: it is open, unblocked, and the product names it as next.
        run = TaskCompleteRun(self.harness.project_root, self.harness.home)
        record = run._next_action()
        self.assertIsNotNone(record)
        self.assertIsNone(record["active_task"])
        self.assertEqual(record["next_open_task"]["id"], OTHER)
        self.assertEqual(record["blockers"], [])

    def test_no_command_in_the_run_named_the_other_task(self) -> None:
        self.assertEqual(self.outcome.effects_for(OTHER), [])
        self.assertEqual(
            {effect.task_id for effect in self.outcome.effects}, {BOUND}
        )

    def test_no_selection_move_prompt_or_dispatch_reached_the_other_task(
        self,
    ) -> None:
        # Observed on the filesystem, independently of the run's own log.
        self.assertEqual(self.harness.status(OTHER), "open")
        self.assertEqual(self.harness.artifacts_for(OTHER), [])
        self.assertEqual(
            self.harness.artifacts_for(BOUND),
            [
                "prompts/PROMPT-01-001.md",
                "reports/REPORT-01-001-review.md",
                "reports/REPORT-01-001.md",
                "reviews/REVIEW-01-001.md",
            ],
        )

    def test_the_other_task_file_is_byte_identical(self) -> None:
        expected = _TASK.format(
            nn="01-002", title="Stay untouched in the queue"
        ).encode("utf-8")
        self.assertEqual(self.harness.task_bytes(OTHER), expected)


class TestBoundaryDecidesContinuation(unittest.TestCase):
    """The same authorized project stops differently per resolved boundary."""

    def test_handoff_complete_returns_control_after_one_handoff(self) -> None:
        with _Harness(boundary="handoff-complete") as harness:
            outcome = harness.run()
            self.assertEqual(outcome.activities, ["task_run"])
            self.assertEqual(
                outcome.terminal_reason, "handoff-complete-boundary"
            )
            self.assertEqual(harness.status(BOUND), "in-review")

    def test_handoff_budget_returns_control_when_the_budget_is_spent(
        self,
    ) -> None:
        with _Harness(boundary="handoff-budget", budget=1) as harness:
            outcome = harness.run()
            self.assertEqual(outcome.activities, ["task_run"])
            self.assertEqual(outcome.terminal_reason, "handoff-budget-spent")
            self.assertEqual(harness.status(BOUND), "in-review")

    def test_only_task_complete_carries_the_task_past_one_handoff(self) -> None:
        with _Harness(boundary="task-complete") as harness:
            outcome = harness.run()
            self.assertEqual(len(outcome.activities), 2)
            self.assertEqual(harness.status(BOUND), "done")


class TestFailClosedStopsEndTheRun(unittest.TestCase):
    """Every existing stop condition still terminates a task-complete run."""

    def _assert_stopped_safely(self, harness, outcome, reason, status) -> None:
        self.assertEqual(outcome.terminal_reason, reason)
        self.assertEqual(outcome.boundary, "task-complete")
        # Governed state is preserved: the bound task did not close, and the
        # stop did not spill onto the task standing next in the queue.
        self.assertEqual(harness.status(BOUND), status)
        self.assertNotEqual(harness.status(BOUND), "done")
        self.assertEqual(outcome.effects_for(OTHER), [])
        self.assertEqual(harness.status(OTHER), "open")
        self.assertEqual(harness.artifacts_for(OTHER), [])

    def test_an_assignee_that_publishes_nothing_stops_the_run(self) -> None:
        with _Harness(suppress="contributor", wait_budget="1s") as harness:
            outcome = harness.run()
            self._assert_stopped_safely(
                harness,
                outcome,
                "handoff-exited-without-report",
                "in-progress",
            )
            self.assertEqual(outcome.activities, [])

    def test_a_blocked_report_stops_the_run(self) -> None:
        with _Harness(task_status="blocked") as harness:
            outcome = harness.run()
            self._assert_stopped_safely(
                harness, outcome, "handoff-blocked", "in-progress"
            )

    def test_an_unauthorized_activity_stops_the_run(self) -> None:
        # The audit is required by review policy but not permitted to launch.
        # Task-complete continuation never widens an authorization.
        with _Harness(review_auto_launch=False) as harness:
            outcome = harness.run()
            self._assert_stopped_safely(
                harness, outcome, "dispatch-refused", "in-review"
            )
            self.assertEqual(outcome.activities, ["task_run"])

    def test_a_silent_second_assignee_stops_the_run_mid_task(self) -> None:
        # The stop condition applies to the second activity exactly as it does
        # to the first: continuation is not a commitment to reach `done`.
        with _Harness(suppress="auditor", wait_budget="1s") as harness:
            outcome = harness.run()
            self._assert_stopped_safely(
                harness,
                outcome,
                "handoff-exited-without-report",
                "in-review",
            )
            self.assertEqual(outcome.activities, ["task_run"])


class TestHarnessIsNotVacuous(unittest.TestCase):
    """The observations above can actually fail.

    A harness that passes no matter what the product resolves proves nothing.
    This injects one product-side fault — every resolution reports
    `handoff-complete` whatever the operator authored — and shows the same
    authored `task-complete` project then stops after one handoff with its
    task still open. Every positive observation above rests on that
    difference.
    """

    def test_a_run_that_ignores_the_resolved_boundary_is_caught(self) -> None:
        from cli.commands import resolve_config

        real = resolve_config.resolve_project_configuration

        def ignore_boundary(*args, **kwargs):
            resolved = real(*args, **kwargs)
            resolved["automation"]["run_boundary"] = "handoff-complete"
            return resolved

        with _Harness(boundary="task-complete") as harness:
            with mock.patch(
                "cli.commands.next_action.resolve_project_configuration",
                ignore_boundary,
            ):
                outcome = harness.run()
            self.assertEqual(outcome.activities, ["task_run"])
            self.assertEqual(
                outcome.terminal_reason, "handoff-complete-boundary"
            )
            self.assertNotEqual(harness.status(BOUND), "done")
        # And unmutated, the identical project reaches `done`.
        with _Harness(boundary="task-complete") as harness:
            outcome = harness.run()
            self.assertEqual(outcome.terminal_reason, "bound-task-done")
            self.assertEqual(harness.status(BOUND), "done")


if __name__ == "__main__":
    unittest.main()
