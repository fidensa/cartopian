"""The four integration seams, exercised end to end.

The traceability contract declares four seams and designs no mechanism behind
them: the assignment seam (the coder projection in the assignment prompt), the
review-context seam (the provenance block in review context), the
determination seam (D1/D2 in the review), and a reserved addressability seam
the effectiveness ledger counts over. These tests hold the first two and the
fourth, and hold the two properties that make them safe to add at all — that
routine surfaces gain nothing for an undeclared task, and that no evidence
emission can block a lifecycle transition.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli import acceptance_trace as at
from cli import prompt_evidence as pe
from cli import trace_binding
from cli.commands import capture_request
from cli.main import build_parser

CONFIG = """[project]
name = "Seams"
id = "seams"
project_schema_version = "v0.10.0"

[reviews]
planning = "required"
planning_role = "reviewer"
task_closure = "required"
task_role = "reviewer"

[roles.pm]
description = "PM"
grants = ["pm-solo"]

[roles.reviewer]
description = "Configured reviewer"
grants = ["reviewer-like"]
agent = "cartopian-codex"
"""

REQUIREMENTS = "Cartopian REQUIREMENTS.md and STANDARDS.md"
CONTEXT = "active Product Refinement contract as of 2026-08-13"

SPEC = """# SPEC-02-010: Seam fixture

Status: locked
Profile: general
Source guidance: required

## Source guidance

### Authoritative sources

- Identity: Cartopian REQUIREMENTS.md and STANDARDS.md; Applicable context: active Product Refinement contract as of 2026-08-13; Status: current; Scope: runtime and containment constraints

### Conflict resolution

- Status: resolved; Rule: current requirements and standards govern product boundaries; Decision: implement only the bounded reviewed design

### Unverified claims

- none

## Examples / acceptance

- The seam fixture passes under the declared contract.
- The seam fixture reports its measured bodies.
- The seam fixture fails closed on a drifted criterion.
"""

SPEC_ITEMS = (
    "The seam fixture passes under the declared contract.",
    "The seam fixture reports its measured bodies.",
    "The seam fixture fails closed on a drifted criterion.",
)
TASK_ITEM = "The seam fixture records its own outcome."


class SeamFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "cartopian.toml").write_text(CONFIG, encoding="utf-8")
        for directory in (
            "tasks/open",
            "tasks/in-progress",
            "tasks/in-review",
            "tasks/done",
            "prompts",
            "reviews",
            "reports",
            "specs",
            "phases",
            "decisions",
        ):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        (self.root / "specs/SPEC-02-010.md").write_text(SPEC, encoding="utf-8")
        (self.root / "IMPLEMENTATION_PLAN.md").write_text(
            "# Plan\n\n- `BUILD-02-010` — Seam work.\n", encoding="utf-8"
        )
        (self.root / "phases/PHASE-02.md").write_text(
            "# PHASE-02\n\n- `BUILD-02-010` — Seam work.\n", encoding="utf-8"
        )
        self.capture("Build the seam fixture.")
        self.identity = json.loads(
            (self.root / "requests" / "REQUEST-001.json").read_text(encoding="utf-8")
        )["content_identity"]
        self.task = self.root / "tasks/in-progress/TASK-02-010.md"
        self.write_task()

    def capture(self, text: str) -> None:
        source = self.root / "request.txt"
        source.write_text(text, encoding="utf-8")
        args = argparse.Namespace(
            project_root=str(self.root),
            request_id="REQUEST-001",
            unit="task:TASK-02-010",
            content_file=str(source),
            correction_of=None,
            captured_at="2026-07-27T12:00:00Z",
        )
        fixture_env = {
            key: value
            for key, value in os.environ.items()
            if key not in capture_request.NON_OPERATOR_MARKERS
        }
        with mock.patch.dict(os.environ, fixture_env, clear=True):
            self.assertEqual(capture_request.handler(args), 0)

    def trace_block(self, *, drift: bool = False):
        lines = [
            f"C{index:02d}|{at.digest12(text)}|requirement|{REQUIREMENTS}|{CONTEXT}|1"
            for index, text in enumerate(SPEC_ITEMS, start=1)
        ]
        digest = at.digest12(TASK_ITEM if not drift else "something else entirely")
        lines.append(
            f"C04|{digest}|operator-request|REQ-001 {self.identity}|evidence order 1, "
            "observed 2026-07-27|1"
        )
        return sorted(lines)

    def write_task(self, *, declaration="required", drift=False, section=True):
        block = "\n".join(self.trace_block(drift=drift))
        trace_section = (
            f"\n## Upstream trace\n\n```trace\n{block}\n```\n" if section else ""
        )
        self.task.write_text(
            f"""# TASK-02-010: Seam fixture

Phase: PHASE-02
Plan ref: BUILD-02-010
Work root: n/a
Deliverable: n/a
Spec: SPEC-02-010.md
Evidence gate: n/a
Source guidance: spec
Upstream trace: {declaration}

## Goal

Fixture.

## Acceptance

- [ ] {TASK_ITEM}
{trace_section}""",
            encoding="utf-8",
        )

    def write_report(self, status="complete", ready="yes"):
        (self.root / "reports/REPORT-02-010.md").write_text(
            f"""Status: {status}

## Identity

- Work root: n/a

## Completion evidence

The seam fixture landed.

## Remaining risks

- none

## Ready for review

{ready}
""",
            encoding="utf-8",
        )

    def write_review(self, verdict="request-changes"):
        (self.root / "reviews/REVIEW-02-010.md").write_text(
            f"""# REVIEW-02-010

Target: TASK-02-010
Plan ref: BUILD-02-010
Reviewer: independent reviewer
Verdict: {verdict}

## Summary

Reviewed.
""",
            encoding="utf-8",
        )

    def run_cli(self, *argv: str):
        parser = build_parser()
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                args = parser.parse_args(list(argv))
                handler = getattr(args, "_handler", None)
                code = handler(args) if handler is not None else 2
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 2
        records = [
            json.loads(line)
            for line in stdout.getvalue().splitlines()
            if line.strip()
        ]
        return code, records, stderr.getvalue()


class AssignmentSeamTests(SeamFixture):
    def test_the_assignment_prompt_carries_the_complete_coder_projection(self):
        code, records, err = self.run_cli(
            "write-prompt", str(self.root), "--prompt-id", "PROMPT-02-010",
            "--task", str(self.task), "--content", "# Assignment prompt\n",
        )
        self.assertEqual(code, 0, err)
        prompt = (self.root / "prompts/PROMPT-02-010.md").read_text(encoding="utf-8")
        trace = trace_binding.bind(self.root, self.task).trace
        self.assertIn(trace_binding.CODER_SECTION_HEADING, prompt)
        self.assertIn(trace.coder_projection(), prompt)
        self.assertEqual(
            records[0]["details"]["upstream_trace"]["trace_identity"],
            trace.trace_identity(),
        )

    def test_the_coder_projection_leaks_no_governance_identity(self):
        self.run_cli(
            "write-prompt", str(self.root), "--prompt-id", "PROMPT-02-010",
            "--task", str(self.task), "--content", "# Assignment prompt\n",
        )
        prompt = (self.root / "prompts/PROMPT-02-010.md").read_text(encoding="utf-8")
        projection = prompt.split("```trace-projection\n")[1].split("```")[0]
        for token in (REQUIREMENTS, CONTEXT, "sha256:", "REQ-", "requirement"):
            self.assertNotIn(token, projection)

    def test_a_structurally_invalid_trace_never_reaches_a_coder(self):
        self.write_task(drift=True)
        code, records, err = self.run_cli(
            "write-prompt", str(self.root), "--prompt-id", "PROMPT-02-010",
            "--task", str(self.task), "--content", "# Assignment prompt\n",
        )
        self.assertEqual(code, 1)
        self.assertIn("criterion-digest-mismatch", err)
        self.assertFalse((self.root / "prompts/PROMPT-02-010.md").exists())

    def test_an_undeclared_task_gains_no_projection_and_no_bytes(self):
        self.write_task(declaration="n/a", section=False)
        code, records, err = self.run_cli(
            "write-prompt", str(self.root), "--prompt-id", "PROMPT-02-010",
            "--task", str(self.task), "--content", "# Assignment prompt\n",
        )
        self.assertEqual(code, 0, err)
        prompt = (self.root / "prompts/PROMPT-02-010.md").read_text(encoding="utf-8")
        self.assertNotIn(trace_binding.CODER_SECTION_HEADING, prompt)
        self.assertEqual(
            records[0]["details"]["upstream_trace"], {"declaration": "n/a"}
        )


class ReviewContextSeamTests(SeamFixture):
    def test_the_review_prompt_carries_the_reviewer_provenance_block(self):
        self.write_report()
        code, records, err = self.run_cli(
            "write-prompt", str(self.root), "--prompt-id", "PROMPT-02-010",
            "--review-kind", "task-closure", "--task", str(self.task),
            "--content", "# Review prompt\n",
        )
        self.assertEqual(code, 0, err)
        prompt = (self.root / "prompts/PROMPT-02-010.md").read_text(encoding="utf-8")
        trace = trace_binding.bind(self.root, self.task).trace
        self.assertIn(trace_binding.REVIEWER_SECTION_HEADING, prompt)
        self.assertIn(trace.reviewer_projection(), prompt)
        self.assertIn("Computed-by: pm", prompt)
        self.assertIn(REQUIREMENTS, prompt)

    def test_review_context_projects_the_block_and_the_rubric(self):
        code, records, err = self.run_cli(
            "review-context", str(self.root), "--review-kind", "task-closure",
            "--task", str(self.task),
        )
        self.assertEqual(code, 0, err)
        record = records[0]
        trace = trace_binding.bind(self.root, self.task).trace
        self.assertEqual(
            record["upstream_trace"]["reviewer_projection"]["body"],
            trace.reviewer_projection(),
        )
        self.assertEqual(
            record["contract_quality"]["placed_before"], "## Implementation evidence"
        )
        self.assertEqual(len(record["contract_quality"]["checks"]), 7)

    def test_review_context_reports_an_undeclared_task_without_inventing_one(self):
        self.write_task(declaration="n/a", section=False)
        code, records, err = self.run_cli(
            "review-context", str(self.root), "--review-kind", "task-closure",
            "--task", str(self.task),
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(records[0]["upstream_trace"]["declaration"], "n/a")
        self.assertNotIn("reviewer_projection", records[0]["upstream_trace"])


class ReadinessSeamTests(SeamFixture):
    def test_readiness_and_task_bundle_agree_on_the_new_check(self):
        code, readiness, _ = self.run_cli(
            "validate-task-readiness", str(self.task)
        )
        check = [
            c for c in readiness[0]["checks"] if c["name"] == "upstream-trace-valid"
        ]
        self.assertEqual(len(check), 1)
        self.assertTrue(check[0]["pass"])

        _, bundle, _ = self.run_cli("task-bundle", str(self.task))
        self.assertTrue(bundle[0]["ready"])
        self.assertEqual(bundle[0]["validator_blockers"], [])

    def test_a_drifted_criterion_blocks_readiness_with_its_contract_code(self):
        self.write_task(drift=True)
        code, readiness, err = self.run_cli(
            "validate-task-readiness", str(self.task)
        )
        self.assertEqual(code, 1)
        self.assertFalse(readiness[0]["ready"])
        failing = [c for c in readiness[0]["checks"] if not c["pass"]]
        self.assertEqual([c["name"] for c in failing], ["upstream-trace-valid"])
        self.assertIn("criterion-digest-mismatch", failing[0]["reason"])

    def test_an_undeclared_task_still_reads_ready(self):
        self.write_task(declaration="n/a", section=False)
        code, readiness, _ = self.run_cli(
            "validate-task-readiness", str(self.task)
        )
        check = [
            c for c in readiness[0]["checks"] if c["name"] == "upstream-trace-valid"
        ][0]
        self.assertTrue(check["pass"])

    def test_a_task_declaring_n_a_may_not_also_carry_a_record_block(self):
        self.write_task(declaration="n/a", section=True)
        code, readiness, err = self.run_cli(
            "validate-task-readiness", str(self.task)
        )
        self.assertEqual(code, 1)
        self.assertIn("trace-unparseable", err)


class EvidenceSeamTests(SeamFixture):
    def test_a_prompt_write_captures_its_exact_byte_count(self):
        code, records, err = self.run_cli(
            "write-prompt", str(self.root), "--prompt-id", "PROMPT-02-010",
            "--task", str(self.task), "--content", "# Assignment prompt\n",
        )
        self.assertEqual(code, 0, err)
        details = records[0]["details"]
        self.assertEqual(details["prompt_evidence"]["result"], pe.WRITTEN)
        ledger = pe.read_ledger(self.root)
        captured = [r for r in ledger.records if r.get("f") == "PCC"]
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["n"], details["bytes"])
        self.assertEqual(captured[0]["o"], 1)

    def test_successive_prompt_writes_carry_successive_ordinals(self):
        for _ in range(2):
            self.run_cli(
                "write-prompt", str(self.root), "--prompt-id", "PROMPT-02-010",
                "--task", str(self.task), "--content", "# Assignment prompt\n",
            )
        ordinals = [
            r["o"] for r in pe.read_ledger(self.root).records if r.get("f") == "PCC"
        ]
        self.assertEqual(ordinals, [1, 2])

    def test_a_plan_level_prompt_is_outside_the_unit_of_observation(self):
        outcome = pe.record_prompt_write(
            self.root, "PROMPT-PLAN-001", b"body", "2026-08-19"
        )
        self.assertEqual(outcome["result"], pe.REJECTED)
        self.assertFalse(pe.log_path(self.root).exists())

    def test_a_reopen_transition_is_labeled_at_the_move(self):
        task = self.root / "tasks/in-review/TASK-02-010.md"
        self.task.rename(task)
        self.write_review("request-changes")
        code, records, err = self.run_cli(
            "move-task", str(task), "in-progress",
        )
        self.assertEqual(code, 0, err)
        rows = pe.project_events(pe.read_ledger(self.root)).rows
        self.assertEqual(len(rows), 1)
        self.assertIn("RRG|pass 1|in-review>in-progress|-", rows[0])

    def test_an_ordinary_transition_labels_nothing(self):
        self.write_report()
        code, records, err = self.run_cli(
            "move-task", str(self.task), "in-review",
        )
        self.assertEqual(code, 0, err)
        self.assertNotIn("effectiveness_evidence", records[0]["details"])
        self.assertFalse(pe.log_path(self.root).exists())

    def test_a_blocked_report_records_one_clarification_idempotently(self):
        self.write_report(status="blocked", ready="no")
        report = self.root / "reports/REPORT-02-010.md"
        for _ in range(2):
            self.run_cli("report-action", str(report))
        rows = pe.project_events(pe.read_ledger(self.root)).rows
        self.assertEqual(
            [row.split("|")[3] for row in rows], ["CLR"]
        )
        self.assertTrue(rows[0].endswith("|REPORT-02-010"))

    def test_an_emission_failure_never_blocks_the_lifecycle(self):
        # A ledger the appender cannot write is the fail-closed case: the
        # observation is lost, the transition is not.
        log = pe.log_path(self.root)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.mkdir()
        code, records, err = self.run_cli(
            "write-prompt", str(self.root), "--prompt-id", "PROMPT-02-010",
            "--task", str(self.task), "--content", "# Assignment prompt\n",
        )
        self.assertEqual(code, 0, err)
        self.assertTrue((self.root / "prompts/PROMPT-02-010.md").exists())
        self.assertEqual(
            records[0]["details"]["prompt_evidence"]["result"], pe.REJECTED
        )


class RoutineSurfaceTests(SeamFixture):
    """The ledger is never an input to a routine surface."""

    ROUTINE = (
        ("next-action", lambda self: ("next-action", str(self.root))),
        ("task-bundle", lambda self: ("task-bundle", str(self.task))),
        (
            "handoff-packet",
            lambda self: ("handoff-packet", str(self.task), "--role", "coder"),
        ),
    )

    def test_no_routine_surface_reads_or_reports_the_ledger(self):
        pe.emit(
            self.root,
            pe.event(
                plan=pe.current_plan_id(self.root),
                unit="TASK-02-010",
                date="2026-08-19",
                family="PCC",
                ordinal=1,
                size=4812,
            ),
        )
        for name, argv in self.ROUTINE:
            with self.subTest(surface=name):
                _, records, _ = self.run_cli(*argv(self))
                blob = json.dumps(records)
                self.assertNotIn("prompt-evidence", blob)
                self.assertNotIn('"PCC"', blob)


class UnitClosureSeamTests(SeamFixture):
    """The `U` summary is written where the unit actually closes.

    § 12.2 emits the summary "at unit closure". The boundary that is, in a
    normal or unattended run, is the move into `done` — by review, by the
    no-review path, or by an administrative fast-forward — so that move
    derives it, and nothing about the move depends on the result.
    """

    def open_task(self):
        opened = self.root / "tasks/open/TASK-02-010.md"
        if self.task.exists():
            self.task.rename(opened)
        return opened

    def close_unit(self):
        return self.run_cli(
            "move-task", str(self.open_task()), "done",
            "--administrative", "--reason", "seam fixture closure",
        )

    def test_closing_a_unit_derives_its_summary_at_the_move(self):
        code, records, err = self.close_unit()
        self.assertEqual(code, 0, err)
        summary = records[0]["details"]["effectiveness_summary"]
        self.assertEqual(summary["result"], pe.WRITTEN)
        self.assertEqual(summary["unit"], "TASK-02-010")
        ledger = pe.read_ledger(self.root)
        summaries = [r for r in ledger.records if r.get("k") == "U"]
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["u"], "TASK-02-010")
        self.assertEqual(tuple(summaries[0]["f"]), pe.FAMILIES)

    def test_a_closure_summary_never_reads_pad_as_observed(self):
        # PAD's window opens at approval and closes at plan closeout; a unit
        # that was never approved has no window and reads `not-applicable`.
        self.close_unit()
        record = [r for r in pe.read_ledger(self.root).records if r["k"] == "U"][0]
        self.assertEqual(record["f"]["PAD"][1], "not-applicable")

    def test_the_summary_is_readable_as_a_bounded_answer(self):
        self.close_unit()
        answer = pe.project_units(pe.read_ledger(self.root))
        self.assertEqual(len(answer.rows), 1)
        self.assertTrue(answer.rows[0].startswith("U|TASK-02-010|"))

    def test_reclosing_a_reopened_unit_keeps_exactly_one_closure_summary(self):
        self.close_unit()
        done = self.root / "tasks/done/TASK-02-010.md"
        done.rename(self.root / "tasks/open/TASK-02-010.md")
        code, records, err = self.close_unit()
        self.assertEqual(code, 0, err)
        self.assertEqual(
            records[0]["details"]["effectiveness_summary"]["result"], pe.IDEMPOTENT
        )
        summaries = [r for r in pe.read_ledger(self.root).records if r["k"] == "U"]
        self.assertEqual(len(summaries), 1)

    def test_an_ordinary_move_derives_no_summary(self):
        self.write_report()
        code, records, err = self.run_cli("move-task", str(self.task), "in-review")
        self.assertEqual(code, 0, err)
        self.assertNotIn("effectiveness_summary", records[0]["details"])

    def test_a_summary_that_cannot_be_written_never_blocks_the_closure(self):
        log = pe.log_path(self.root)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.mkdir()
        code, records, err = self.close_unit()
        self.assertEqual(code, 0, err)
        self.assertTrue((self.root / "tasks/done/TASK-02-010.md").is_file())
        self.assertEqual(
            records[0]["details"]["effectiveness_summary"]["result"], pe.REJECTED
        )


class PlanCloseoutSeamTests(SeamFixture):
    """The ordered close runs where the plan actually closes.

    § 14.4 is a sequence, not a mode: superseding summaries, then the closing
    projection, then the mediated delete. `archive-plan` and `reset-plan` are
    the two commands the canonical closeout runbook routes through, so both
    run it, and running both runs it exactly once.
    """

    def approve_a_unit(self):
        plan = pe.current_plan_id(self.root)
        outcome = pe.emit(
            self.root,
            pe.event(
                plan=plan, unit="TASK-02-010", date="2026-08-19", family="RRR",
                ordinal=1, marker="approve", artifact="REVIEW-02-010",
            ),
        )
        self.assertEqual(outcome["result"], pe.WRITTEN)
        return plan

    def archive(self):
        return self.run_cli(
            "archive-plan", str(self.root), "--closed", "2026-08-19",
            "--summary", "Seam plan closed", "--content", "# Closeout\n",
        )

    def reset(self):
        return self.run_cli("reset-plan", str(self.root), "--carry-standards")

    def test_archiving_a_plan_runs_the_ordered_close(self):
        plan = self.approve_a_unit()
        code, records, err = self.archive()
        self.assertEqual(code, 0, err)
        closeout = records[0]["details"]["effectiveness_closeout"]
        self.assertEqual(closeout["plan"], plan)
        self.assertFalse(closeout["already_closed"])
        # Step 1 before step 2 before step 3: the superseding summary is in
        # the projection the sequence produced, and the log is gone after it.
        self.assertEqual(len(closeout["superseding_summaries"]), 1)
        summary = closeout["superseding_summaries"][0]
        self.assertEqual(summary["result"], pe.WRITTEN)
        self.assertEqual(summary["record"]["f"]["PAD"][1], "observed")
        self.assertEqual(closeout["closing_projection"]["rows"], [summary["row"]])
        self.assertTrue(closeout["log_deleted"])
        self.assertFalse(pe.log_path(self.root).exists())

    def test_the_deletion_is_mediated_so_the_absence_is_explainable(self):
        self.approve_a_unit()
        self.archive()
        journal = (self.root / ".cartopian/provenance.log").read_text(encoding="utf-8")
        self.assertIn(pe.LOG_RELPATH, journal)
        self.assertIn("mediated-delete", journal)

    def test_resetting_a_plan_closes_the_window_when_no_archive_was_taken(self):
        plan = self.approve_a_unit()
        code, records, err = self.reset()
        self.assertEqual(code, 0, err)
        closeout = records[0]["details"]["effectiveness_closeout"]
        self.assertEqual(closeout["plan"], plan)
        self.assertEqual(len(closeout["superseding_summaries"]), 1)
        self.assertTrue(closeout["log_deleted"])
        self.assertFalse(pe.log_path(self.root).exists())

    def test_archiving_then_resetting_closes_the_window_exactly_once(self):
        self.approve_a_unit()
        self.archive()
        code, records, err = self.reset()
        self.assertEqual(code, 0, err)
        closeout = records[0]["details"]["effectiveness_closeout"]
        self.assertTrue(closeout["already_closed"])
        self.assertEqual(closeout["superseding_summaries"], [])
        self.assertFalse(closeout["log_deleted"])

    def test_no_evidence_survives_the_plan_that_produced_it(self):
        self.approve_a_unit()
        self.reset()
        answer = pe.project_units(pe.read_ledger(self.root), units=["TASK-02-010"])
        self.assertEqual(answer.rows, [])
        self.assertEqual(answer.unavailable_units, ["TASK-02-010"])

    def test_a_close_that_cannot_run_never_blocks_the_reset(self):
        log = pe.log_path(self.root)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.mkdir()
        code, records, err = self.reset()
        self.assertEqual(code, 0, err)
        self.assertFalse((self.root / "specs/SPEC-02-010.md").exists())
        closeout = records[0]["details"]["effectiveness_closeout"]
        self.assertFalse(closeout["log_deleted"])

    def test_a_close_that_cannot_run_never_blocks_the_archive(self):
        log = pe.log_path(self.root)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.mkdir()
        code, records, err = self.archive()
        self.assertEqual(code, 0, err)
        self.assertTrue((self.root / "archive/PLAN-001").is_dir())
        self.assertFalse(records[0]["details"]["effectiveness_closeout"]["log_deleted"])
if __name__ == "__main__":  # pragma: no cover
    unittest.main()
