"""Regression contract for up-front request evidence."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from cli import migrations, request_trace
from cli.commands import capture_request, move_task, plan_audit
from cli.main import OPERATOR_ONLY_SUBCOMMANDS, SUBCOMMANDS, build_parser
from mcp_server import server


ORIGINAL = (
    "Do you understand that the human intent is provided at the point where I "
    "tell you what I want in the first place. It's not something I provide later "
    "in the process, it's the up-front ask, before you pervert it into some warped "
    "version of my words. That's what the reviewer should be comparing to your "
    "prompt and the implemented feature."
)


CONFIG = '''[project]
name = "Trace"
id = "trace"
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
'''


class RequestTraceContract(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "cartopian.toml").write_text(CONFIG, encoding="utf-8")
        for directory in ("tasks/in-review", "tasks/done", "prompts", "reviews", "reports"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        (self.root / "decisions").mkdir()
        self.task = self.root / "tasks/in-review/TASK-02-010.md"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def capture(
        self,
        text: str,
        *,
        correction: bool = False,
        unit: str = "project",
    ) -> None:
        source = self.root / ("correction.txt" if correction else "request.txt")
        source.write_text(text, encoding="utf-8")
        args = argparse.Namespace(
            project_root=str(self.root), request_id="REQUEST-001", unit=unit,
            content_file=str(source), correction_of="REQUEST-001" if correction else None,
            captured_at="2026-07-27T12:00:00Z",
        )
        fixture_env = {
            key: value
            for key, value in os.environ.items()
            if key not in capture_request.NON_OPERATOR_MARKERS
        }
        with mock.patch.dict(os.environ, fixture_env, clear=True):
            self.assertEqual(capture_request.handler(args), 0)

    def seed_task(self) -> None:
        self.task.write_text("# TASK-02-010: Trace\n\nPhase: PHASE-02\nPlan ref: BUILD-02-010\n", encoding="utf-8")

    def seed_plan_ancestry(self) -> None:
        (self.root / "phases").mkdir(exist_ok=True)
        (self.root / "IMPLEMENTATION_PLAN.md").write_text(
            "# Plan\n\n- `BUILD-02-010` — Trace work.\n", encoding="utf-8"
        )
        (self.root / "phases/PHASE-02.md").write_text(
            "# PHASE-02\n\n- `BUILD-02-010` — Trace work.\n", encoding="utf-8"
        )

    def run_cli(self, *argv: str) -> tuple[int, list[dict], str]:
        parser = build_parser()
        stdout = io.StringIO()
        stderr = io.StringIO()
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

    def write_decision(
        self,
        decision_id: str,
        quote: str,
        *,
        marked: bool = True,
        unit: str = "task:TASK-02-010",
    ) -> None:
        decisions = self.root / "decisions"
        decisions.mkdir(exist_ok=True)
        attribution = (
            f"Operator request quote for: {unit}"
            if marked
            else "The PM described this as operator context:"
        )
        (decisions / f"{decision_id}.md").write_text(
            f"# {decision_id}: Request source\n\n"
            "Date: 2026-07-27\nStatus: locked\nSupersedes: none\n\n"
            f"## Context\n\n{attribution}\n\n> {quote}\n",
            encoding="utf-8",
        )

    def write_chat_record(
        self,
        record_id: str,
        text: str,
        *,
        kind: str,
        sequence: int,
    ) -> None:
        base = self.root / "requests/chat"
        base.mkdir(parents=True, exist_ok=True)
        record = {
            "schema": "cartopian-host-chat-v1",
            "record_id": record_id,
            "role": "operator",
            "kind": kind,
            "sequence": sequence,
            "unit": {"kind": "task", "id": "TASK-02-010"},
            "text": text,
            "content_identity": "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "observed_at": f"2026-07-27T12:0{sequence}:00Z",
            "source": {
                "host": "test-host",
                "conversation_id": "conversation-1",
                "message_id": f"message-{sequence}",
            },
        }
        (base / f"{record_id}.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def test_exact_initiating_sentence_reaches_review_without_later_action(self) -> None:
        self.capture(ORIGINAL, unit="task:TASK-02-010")
        self.seed_task()
        context = request_trace.context_for_task(self.root, self.task)
        self.assertEqual(context.trace[0].text, ORIGINAL)
        self.assertIn(ORIGINAL, context.section)
        self.assertEqual(
            context.trace[0].identity,
            "sha256:" + hashlib.sha256(ORIGINAL.encode("utf-8")).hexdigest(),
        )
        self.assertIn("## Original operator request (verbatim)", context.section)
        self.assertIn("## PM-derived guidance and delivered outcome", context.section)

    def test_explicit_correction_is_ordered_and_unrelated_history_is_absent(self) -> None:
        self.capture(ORIGINAL, unit="task:TASK-02-010")
        correction = "Correction: preserve the exact opening sentence too."
        self.capture(correction, correction=True)
        self.seed_task()
        context = request_trace.context_for_task(self.root, self.task)
        self.assertEqual([item.text for item in context.trace], [ORIGINAL, correction])
        self.assertNotIn("unrelated assistant message", context.section)
        self.assertEqual(context.evidence_ids, ["REQUEST-001", "REQUEST-001-CORRECTION-001"])
        self.assertIn("### Explicit correction 1", context.section)
        self.assertIn("Review target: task:TASK-02-010", context.section)
        self.assertIn("Governed unit: task:TASK-02-010", context.section)

    def test_applicable_decision_quotes_resolve_without_native_capture(self) -> None:
        initiating = "Build the exact requested review behavior."
        correction = "Correction: keep alignment inside the existing review."
        self.write_decision("DEC-001", initiating)
        self.write_decision("DEC-002", correction)
        self.write_decision(
            "DEC-003", "Unrelated quoted history.", unit="project:project"
        )
        self.task.write_text(
            "# TASK-02-010: Trace\n\n"
            "Phase: PHASE-02\nPlan ref: BUILD-02-010\n\n"
            "## Operator intent\n\nDEC-001 and DEC-002 preserve the exact request.\n",
            encoding="utf-8",
        )

        context = request_trace.context_for_task(self.root, self.task)

        self.assertEqual([item.text for item in context.trace], [initiating, correction])
        self.assertEqual(
            context.evidence_ids,
            ["DEC-001-QUOTE-001", "DEC-002-QUOTE-001"],
        )
        self.assertEqual([item.sequence for item in context.trace], [1, 2])
        self.assertEqual(
            [item.source_identity for item in context.trace],
            ["DEC-001", "DEC-002"],
        )
        self.assertTrue(all(item.source_content_identity.startswith("sha256:") for item in context.trace))
        self.assertNotIn("Unrelated quoted history", context.section)
        self.assertIn("Source path: decisions/DEC-001.md", context.section)

    def test_structural_decision_quote_preserves_matching_boundary_marks(self) -> None:
        quote = '"Keep these literal boundary marks"'
        self.write_decision("DEC-013", quote)
        self.seed_task()

        context = request_trace.context_for_task(self.root, self.task)

        self.assertEqual(context.trace[0].text, quote)

    def test_structural_decision_quote_preserves_bare_blank_paragraph(self) -> None:
        path = self.root / "decisions/DEC-014.md"
        path.write_text(
            "# DEC-014: Request source\n\n"
            "Date: 2026-07-27\nStatus: locked\nSupersedes: none\n\n"
            "## Context\n\n"
            "Operator request quote for: task:TASK-02-010\n\n"
            "> Keep the first paragraph exactly.\n\n"
            "> Keep the later paragraph too.\n",
            encoding="utf-8",
        )
        self.seed_task()

        context = request_trace.context_for_task(self.root, self.task)

        self.assertEqual(
            context.trace[0].text,
            "Keep the first paragraph exactly.\n\nKeep the later paragraph too.",
        )

    def test_host_chat_records_supply_original_and_explicit_correction(self) -> None:
        self.write_chat_record("CHAT-ASK-001", ORIGINAL, kind="original", sequence=0)
        correction = "Correction: do not add another review stage."
        self.write_chat_record("CHAT-CORRECTION-001", correction, kind="correction", sequence=1)
        self.seed_task()

        context = request_trace.context_for_task(self.root, self.task)

        self.assertEqual([item.text for item in context.trace], [ORIGINAL, correction])
        self.assertEqual([item.source_kind for item in context.trace], ["host-chat", "host-chat"])
        self.assertEqual([item.sequence for item in context.trace], [1, 2])
        self.assertIn("test-host:conversation-1:message-0", context.section)

    def test_changed_decision_source_invalidates_bound_context(self) -> None:
        self.write_decision("DEC-005", "Preserve this exact request.")
        self.task.write_text(
            "# TASK-02-010: Trace\n\nPhase: PHASE-02\n\n"
            "## Operator intent\n\nExact operator quote source: DEC-005\n",
            encoding="utf-8",
        )
        context = request_trace.context_for_task(self.root, self.task)
        prompt_text = request_trace.upsert_request_sections("# Review prompt\n", context.section)
        self.write_decision("DEC-005", "Changed request text.")

        current = request_trace.context_for_task(
            self.root,
            self.task,
            prompt_text=prompt_text,
        )
        preflight = request_trace.preflight_prompt_binding(current, prompt_text)

        self.assertFalse(preflight["ok"])
        self.assertEqual(preflight["rule"], "stale-request-context")

    def test_ordinary_plan_quote_is_not_promoted_but_explicit_source_ref_is(self) -> None:
        quote = "Keep the plan review focused."
        self.write_decision("DEC-004", quote, marked=False)
        (self.root / "IMPLEMENTATION_PLAN.md").write_text(
            "# Plan\n\nThe operator may have said this:\n\n"
            f"> \"{quote}\"\n",
            encoding="utf-8",
        )
        with self.assertRaises(request_trace.RequestRefusal) as caught:
            request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(caught.exception.rule, "unit-request-not-captured")

        self.write_decision("DEC-004", quote, unit="project:project")
        (self.root / "IMPLEMENTATION_PLAN.md").write_text(
            "# Plan\n\n## Original request evidence\n\n"
            "Exact operator quote source: DEC-004\n",
            encoding="utf-8",
        )
        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(context.evidence_ids, ["DEC-004-QUOTE-001"])
        self.assertEqual(context.trace[0].text, quote)

    def test_ordinary_and_loosely_attributed_decision_quotes_are_not_evidence(self) -> None:
        self.write_decision("DEC-010", "This is only PM context.", marked=False)
        self.task.write_text(
            "# TASK-02-010: Trace\n\n## Operator intent\n\nDEC-010\n",
            encoding="utf-8",
        )

        with self.assertRaises(request_trace.RequestRefusal) as caught:
            request_trace.context_for_task(self.root, self.task)

        self.assertEqual(caught.exception.rule, "unit-request-not-captured")

    def test_three_legacy_attribution_sentences_remain_bounded_compatible(self) -> None:
        decisions = self.root / "decisions"
        decisions.mkdir(exist_ok=True)
        legacy = {
            "DEC-021": (
                "The operator clarified the governing intent in these exact words:",
                "Preserve the original request.",
            ),
            "DEC-022": (
                "The first independent review of the corrective request-trace "
                "implementation proposed restoring or adding an up-front safeguard as "
                "part of resolving request alignment. The operator rejected that framing "
                "and clarified the intended process in these exact words:",
                "Keep alignment in the existing review.",
            ),
            "DEC-023": (
                "The second independent review treated a native host callback as "
                "the only acceptable way to preserve operator request evidence. "
                "The operator corrected that assumption:",
                "Decisions and chat are valid sources.",
            ),
        }
        for decision_id, (attribution, quote) in legacy.items():
            (decisions / f"{decision_id}-legacy.md").write_text(
                f"# {decision_id}\n\nDate: 2026-07-27\n\n## Context\n\n"
                f"{attribution}\n\n> \"{quote}\"\n",
                encoding="utf-8",
            )
        self.task.write_text(
            "# TASK-02-010: Trace\n\n## Operator intent\n\n"
            "DEC-021, DEC-022, and DEC-023 preserve the exact request.\n",
            encoding="utf-8",
        )

        context = request_trace.context_for_task(self.root, self.task)

        self.assertEqual(
            [item.text for item in context.trace],
            [
                "Preserve the original request.",
                "Keep alignment in the existing review.",
                "Decisions and chat are valid sources.",
            ],
        )

    def test_malformed_decision_quote_marker_fails_closed(self) -> None:
        self.write_decision("DEC-011", "Malformed.")
        path = self.root / "decisions/DEC-011.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "Operator request quote for: task:TASK-02-010",
                "Operator request quote for: whichever task applies",
            ),
            encoding="utf-8",
        )
        self.seed_task()

        with self.assertRaises(request_trace.RequestRefusal) as caught:
            request_trace.context_for_task(self.root, self.task)

        self.assertEqual(caught.exception.rule, "malformed-decision-quote-marker")

    def test_decision_quote_with_ambiguous_unit_binding_fails_closed(self) -> None:
        decisions = self.root / "decisions"
        decisions.mkdir(exist_ok=True)
        (decisions / "DEC-012.md").write_text(
            "# DEC-012\n\n"
            "Operator request quote for: project:project\n\n"
            "> \"Same text.\"\n\n"
            "Operator request quote for: task:TASK-02-010\n\n"
            "> \"Same text.\"\n",
            encoding="utf-8",
        )
        self.seed_task()

        with self.assertRaises(request_trace.RequestRefusal) as caught:
            request_trace.context_for_task(self.root, self.task)

        self.assertEqual(caught.exception.rule, "ambiguous-decision-quote-marker")

    def test_decision_first_authoring_resolves_and_allows_requirements(self) -> None:
        body = (
            "# DEC-001: Preserve request\n\n"
            "Date: 2026-07-27\nStatus: locked\nSupersedes: none\n\n"
            "Operator request quote for: project:project\n\n"
            "> Build from this exact request.\n"
        )
        code, _, error = self.run_cli(
            "write-decision", str(self.root), "--dec-id", "DEC-001",
            "--title", "Preserve request",
            "--date", "2026-07-27", "--content", body,
        )
        self.assertEqual(code, 0, msg=error)

        code, _, error = self.run_cli(
            "write-requirements", str(self.root), "--content", "# Requirements\n"
        )

        self.assertEqual(code, 0, msg=error)
        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(context.trace[0].text, "Build from this exact request.")

    def test_optional_capture_remains_reachable_after_decision(self) -> None:
        code, _, error = self.run_cli(
            "write-decision", str(self.root), "--dec-id", "DEC-001",
            "--title", "Context", "--date", "2026-07-27",
            "--content", "# DEC-001\n\nOrdinary decision prose.\n",
        )
        self.assertEqual(code, 0, msg=error)

        self.capture("Capture remains optional.")

        self.assertTrue((self.root / "requests/REQUEST-001.json").is_file())

    def test_capture_first_authoring_remains_recoverable(self) -> None:
        self.capture("Capture first, then author.")
        code, _, error = self.run_cli(
            "write-decision", str(self.root), "--dec-id", "DEC-001",
            "--title", "Choice", "--date", "2026-07-27",
            "--content", "# DEC-001\n",
        )
        self.assertEqual(code, 0, msg=error)
        code, _, error = self.run_cli(
            "write-requirements", str(self.root), "--content", "# Requirements\n"
        )
        self.assertEqual(code, 0, msg=error)

    def test_no_evidence_still_refuses_requirements(self) -> None:
        code, _, error = self.run_cli(
            "write-requirements", str(self.root), "--content", "# Requirements\n"
        )

        self.assertEqual(code, 1)
        self.assertIn("request-not-captured", error)
        self.assertFalse((self.root / "REQUIREMENTS.md").exists())

    def test_management_projection_uses_only_real_canonical_and_applicable_artifacts(self) -> None:
        self.capture(ORIGINAL, unit="task:TASK-02-010")
        self.seed_task()
        (self.root / "phases").mkdir(exist_ok=True)
        (self.root / "specs").mkdir(exist_ok=True)
        (self.root / "phases/PHASE-02.md").write_text("# Phase\n", encoding="utf-8")
        (self.root / "specs/SPEC-02-010.md").write_text("# Spec\n", encoding="utf-8")
        (self.root / "reviews/REVIEW-02-010.md").write_text("# Review\n", encoding="utf-8")

        context = request_trace.context_for_task(self.root, self.task)

        self.assertIn("specs/SPEC-02-010.md", context.management_artifacts)
        self.assertIn("phases/PHASE-02.md", context.management_artifacts)
        self.assertIn("reviews/REVIEW-02-010.md", context.management_artifacts)
        self.assertNotIn("REQUIREMENTS.md", context.management_artifacts)
        self.assertTrue(
            all((self.root / path).is_file() for path in context.management_artifacts)
        )

    def test_drift_blocks_approval_and_reviewer_owns_comparison(self) -> None:
        self.capture(ORIGINAL, unit="task:TASK-02-010")
        self.seed_task()
        prompt = self.root / "prompts/PROMPT-02-010.md"
        report = self.root / "reports/REPORT-02-010.md"
        contradiction = "Require the operator to confirm intent after delivery."
        prompt.write_text(f"# PM prompt\n\n{contradiction}\n", encoding="utf-8")
        report.write_text(
            f"# Delivered outcome\n\nImplemented: {contradiction}\n",
            encoding="utf-8",
        )
        context = request_trace.context_for_task(self.root, self.task)
        prompt.write_text(
            request_trace.upsert_request_sections(
                prompt.read_text(encoding="utf-8"), context.section
            ),
            encoding="utf-8",
        )
        review = (
            "# REVIEW-02-010\n\nReviewer: reviewer\nVerdict: approve\n"
            "Request alignment: drifted\nRequest evidence: REQUEST-001\n"
        )
        result = request_trace.parse_alignment(
            review, expected_evidence=context.evidence_ids, legacy=context.legacy
        )
        self.assertTrue(result["blocking"])
        self.assertEqual(result["detail"], "review records drift from the initiating request")
        review_path = self.root / "reviews/REVIEW-02-010.md"
        review_path.write_text(review, encoding="utf-8")
        self.assertIn(
            "review records drift",
            move_task._alignment_error(
                self.root, review_path, review, "02-010"
            ) or "",
        )
        self.assertIn("prompts/PROMPT-02-010.md", context.management_artifacts)
        self.assertIn("reports/REPORT-02-010.md", context.management_artifacts)
        self.assertIn(contradiction, prompt.read_text(encoding="utf-8"))
        self.assertIn(contradiction, report.read_text(encoding="utf-8"))
        self.assertIn("configured reviewer", context.section)
        self.assertNotIn("operator performs the review", context.section.lower())

    def test_v09_derivative_refuses_before_capture(self) -> None:
        with self.assertRaises(request_trace.RequestRefusal) as caught:
            request_trace.require_request_before_derivative(self.root, "requirements")
        self.assertEqual(caught.exception.rule, "request-not-captured")

    def test_task_context_refuses_project_request_without_plan_ancestry(self) -> None:
        self.capture("Found the project.")
        self.seed_task()
        with self.assertRaises(request_trace.RequestRefusal) as caught:
            request_trace.context_for_task(self.root, self.task)
        self.assertEqual(caught.exception.rule, "unit-request-not-captured")

    def test_planned_task_inherits_project_request_for_assignment_and_closure(self) -> None:
        self.capture(ORIGINAL)
        self.seed_task()
        self.seed_plan_ancestry()

        assignment = request_trace.context_for_task_assignment(self.root, self.task)
        closure = request_trace.context_for_task(self.root, self.task)

        expected_unit = request_trace.GovernedUnit("project", "project")
        self.assertEqual(assignment.trace[0].unit, expected_unit)
        self.assertEqual(closure.trace[0].unit, expected_unit)
        self.assertEqual(assignment.evidence_ids, ["REQUEST-001"])
        self.assertEqual(closure.evidence_ids, ["REQUEST-001"])

    def test_planned_prompt_matches_assignment_and_handoff_identity(self) -> None:
        self.capture(ORIGINAL)
        self.task = self.root / "tasks/in-progress/TASK-02-010.md"
        self.task.parent.mkdir(parents=True)
        self.seed_task()
        self.seed_plan_ancestry()
        assignment = request_trace.context_for_task_assignment(self.root, self.task)

        code, write_records, error = self.run_cli(
            "write-prompt", str(self.root), "--prompt-id", "PROMPT-02-010",
            "--task", str(self.task), "--content", "# Assignment prompt\n",
        )
        self.assertEqual(code, 0, msg=error)
        self.assertEqual(
            write_records[0]["details"]["request_context_identity"],
            assignment.context_identity,
        )
        self.assertEqual(
            write_records[0]["details"]["request_evidence"],
            assignment.evidence_ids,
        )

        code, packet_records, error = self.run_cli(
            "handoff-packet", str(self.task), "--role", "reviewer",
        )
        self.assertEqual(code, 0, msg=error)
        packet_trace = packet_records[0]["request_trace"]
        self.assertEqual(packet_trace["context_identity"], assignment.context_identity)
        self.assertEqual(
            [
                record["record_id"]
                for record in packet_trace["request_trace"]["records"]
            ],
            assignment.evidence_ids,
        )

    def test_ad_hoc_task_write_prompt_refuses_project_request(self) -> None:
        self.capture(ORIGINAL)
        self.task = self.root / "tasks/in-progress/TASK-02-010.md"
        self.task.parent.mkdir(parents=True)
        self.seed_task()

        code, records, error = self.run_cli(
            "write-prompt", str(self.root), "--prompt-id", "PROMPT-02-010",
            "--task", str(self.task), "--content", "# Assignment prompt\n",
        )

        self.assertEqual(code, 1)
        self.assertEqual(records, [])
        self.assertIn("unit-request-not-captured", error)

    def test_task_bound_request_still_authors_prompt_without_plan_ancestry(self) -> None:
        self.capture(ORIGINAL, unit="task:TASK-02-010")
        self.task = self.root / "tasks/in-progress/TASK-02-010.md"
        self.task.parent.mkdir(parents=True)
        self.seed_task()

        code, records, error = self.run_cli(
            "write-prompt", str(self.root), "--prompt-id", "PROMPT-02-010",
            "--task", str(self.task), "--content", "# Assignment prompt\n",
        )

        self.assertEqual(code, 0, msg=error)
        self.assertEqual(records[0]["details"]["request_evidence"], ["REQUEST-001"])

    def test_planned_task_inherits_project_corrections_in_order(self) -> None:
        self.capture(ORIGINAL)
        correction = "Correction: keep the task within the approved plan."
        self.capture(correction, correction=True)
        self.seed_task()
        self.seed_plan_ancestry()

        context = request_trace.context_for_task_assignment(self.root, self.task)

        self.assertEqual([item.text for item in context.trace], [ORIGINAL, correction])

    def test_task_bound_evidence_precedes_inherited_project_evidence(self) -> None:
        self.capture(ORIGINAL)
        task_specific = "Add this explicitly authorized task-scoped behavior."
        self.write_decision("DEC-030", task_specific)
        self.seed_task()
        self.seed_plan_ancestry()

        context = request_trace.context_for_task_assignment(self.root, self.task)

        self.assertEqual([item.text for item in context.trace], [task_specific])
        self.assertEqual(
            context.trace[0].unit,
            request_trace.GovernedUnit("task", "TASK-02-010"),
        )

    def test_project_request_does_not_cross_a_mismatched_phase_chain(self) -> None:
        self.capture(ORIGINAL)
        self.seed_task()
        self.seed_plan_ancestry()
        self.task.write_text(
            self.task.read_text(encoding="utf-8").replace(
                "Phase: PHASE-02", "Phase: PHASE-03"
            ),
            encoding="utf-8",
        )

        with self.assertRaises(request_trace.RequestRefusal) as caught:
            request_trace.context_for_task_assignment(self.root, self.task)

        self.assertEqual(caught.exception.rule, "unit-request-not-captured")

    def test_task_capture_is_reachable_after_planning_artifacts_exist(self) -> None:
        self.seed_task()
        spec = self.root / "specs/SPEC-02-010.md"
        spec.parent.mkdir(parents=True)
        spec.write_text("# Planned spec\n", encoding="utf-8")
        self.capture(ORIGINAL, unit="task:TASK-02-010")
        context = request_trace.context_for_task(self.root, self.task)
        self.assertEqual(context.trace[0].unit, request_trace.GovernedUnit("task", "TASK-02-010"))

    def test_dispatched_and_mcp_capture_attempts_fail_closed(self) -> None:
        source = self.root / "request.txt"
        source.write_text(ORIGINAL, encoding="utf-8")
        args = argparse.Namespace(
            project_root=str(self.root), request_id="REQUEST-001", unit="project",
            content_file=str(source), correction_of=None,
            captured_at="2026-07-27T12:00:00Z",
        )
        for marker in capture_request.NON_OPERATOR_MARKERS:
            with self.subTest(marker=marker):
                with mock.patch.dict(os.environ, {marker: "test"}, clear=False):
                    self.assertEqual(capture_request.handler(args), 1)
                self.assertFalse((self.root / "requests/REQUEST-001.json").exists())

    def test_duplicate_correction_content_is_rejected(self) -> None:
        self.capture(ORIGINAL, unit="task:TASK-02-010")
        correction = "Correction: keep the existing review pass."
        self.capture(correction, correction=True)
        source = self.root / "correction.txt"
        args = argparse.Namespace(
            project_root=str(self.root), request_id="REQUEST-001", unit="project",
            content_file=str(source), correction_of="REQUEST-001",
            captured_at="2026-07-27T12:01:00Z",
        )
        fixture_env = {
            key: value
            for key, value in os.environ.items()
            if key not in capture_request.NON_OPERATOR_MARKERS
        }
        with mock.patch.dict(os.environ, fixture_env, clear=True):
            self.assertEqual(capture_request.handler(args), 1)
        self.assertFalse(
            (self.root / "requests/REQUEST-001-CORRECTION-002.json").exists()
        )

    def test_request_store_rejects_a_symlinked_record(self) -> None:
        self.capture(ORIGINAL)
        record = self.root / "requests/REQUEST-001.json"
        target = self.root / "record-target.json"
        target.write_bytes(record.read_bytes())
        record.unlink()
        os.symlink(target, record)
        with self.assertRaises(request_trace.RequestRefusal) as caught:
            request_trace.load_records(self.root)
        self.assertEqual(caught.exception.rule, "unsafe-file")

    def test_historical_review_is_byte_identical_and_not_retroactively_blocked(self) -> None:
        old = self.root / "tasks/done/TASK-01-001.md"
        old.write_text("# TASK-01-001: Old\n", encoding="utf-8")
        review = self.root / "reviews/REVIEW-01-001.md"
        review.write_text("# REVIEW-01-001\n\nVerdict: approve\n", encoding="utf-8")
        before = review.read_bytes()
        blockers, warnings = plan_audit._check_request_trace(self.root, "v0.9.0", True)
        self.assertEqual(blockers, [])
        self.assertEqual(warnings, [])
        self.assertEqual(review.read_bytes(), before)
        plan = migrations.plan_entry(self.root, "v0.9.0")
        self.assertEqual(plan.writes, ())
        self.assertEqual(plan.deletes, ())
        self.assertEqual(review.read_bytes(), before)

    def test_historical_in_review_unit_remains_closable_after_migration(self) -> None:
        self.seed_task()
        prompt = self.root / "prompts/PROMPT-02-010.md"
        prompt.write_text("# Historical review prompt\n", encoding="utf-8")
        review = self.root / "reviews/REVIEW-02-010.md"
        review.write_text("# REVIEW-02-010\n\nVerdict: approve\n", encoding="utf-8")

        self.assertIsNone(
            move_task._alignment_error(
                self.root, review, review.read_text(encoding="utf-8"), "02-010"
            )
        )
        blockers, warnings = plan_audit._check_request_trace(
            self.root, "v0.9.0", True
        )
        self.assertEqual(blockers, [])
        self.assertEqual(warnings, [])

        # Once an initiating request exists, deleting the generated comparison
        # context cannot masquerade as the legacy exemption.
        self.task.unlink()
        prompt.unlink()
        review.unlink()
        self.capture(ORIGINAL, unit="task:TASK-02-010")
        self.seed_task()
        prompt.write_text("# Captured-work review prompt\n", encoding="utf-8")
        review.write_text("# REVIEW-02-010\n\nVerdict: approve\n", encoding="utf-8")
        error = move_task._alignment_error(
            self.root, review, review.read_text(encoding="utf-8"), "02-010"
        )
        self.assertIn("captured work omits", error or "")

    def test_migrated_active_review_prompt_is_regenerated_then_audit_recovers(self) -> None:
        self.root.joinpath("cartopian.toml").write_text(
            CONFIG.replace('project_schema_version = "v0.10.0"',
                           'project_schema_version = "v0.8.0"'),
            encoding="utf-8",
        )
        self.capture(ORIGINAL, unit="task:TASK-02-010")
        self.seed_task()
        prompt = self.root / "prompts/PROMPT-02-010.md"
        prompt.write_text("# Active review prompt from v0.8\n", encoding="utf-8")
        self.root.joinpath("cartopian.toml").write_text(CONFIG, encoding="utf-8")

        blockers, _ = plan_audit._check_request_trace(self.root, "v0.9.0", True)
        self.assertEqual([item["kind"] for item in blockers], ["stale-request-context"])

        (self.root / "reports/REPORT-02-010.md").write_text(
            "# REPORT-02-010\n\n"
            "Status: complete\n\n"
            "## Identity\n\n- Work root: n/a\n\n"
            "## Completion evidence\n\nHistorical task completed.\n\n"
            "## Remaining risks\n\nNone.\n\n"
            "## Ready to close\n\nyes\n",
            encoding="utf-8",
        )
        code, _, error = self.run_cli(
            "write-prompt", str(self.root), "--prompt-id", "PROMPT-02-010",
            "--review-kind", "task-closure", "--task", str(self.task),
            "--content", "# Regenerated existing review prompt\n",
        )
        self.assertEqual(code, 0, msg=error)
        blockers, warnings = plan_audit._check_request_trace(
            self.root, "v0.9.0", True
        )
        self.assertEqual(blockers, [])
        self.assertEqual(warnings, [])
        self.assertIn(
            "## Original operator request (verbatim)",
            prompt.read_text(encoding="utf-8"),
        )

    def test_request_evidence_runbooks_do_not_require_native_capture(self) -> None:
        skills = Path(__file__).parents[1] / "skills"
        for filename in (
            "run-task.md",
            "plan-project.md",
            "adopt-plan.md",
            "adopt-requirements.md",
        ):
            with self.subTest(filename=filename):
                normalized = " ".join(
                    (skills / filename).read_text(encoding="utf-8").split()
                )
                for source in (
                    "structurally marked decision quotations",
                    "supported host chat records",
                    "optional immutable request records",
                ):
                    self.assertIn(source, normalized)
                self.assertIn(
                    "A native host adapter is optional when another supported "
                    "source resolves",
                    normalized,
                )
                self.assertIn("ordinary PM prose is excluded", normalized)
                self.assertIn(
                    "fail closed only when no applicable exact source of any "
                    "supported kind resolves",
                    normalized,
                )
                for forbidden in (
                    "capture bridge",
                    "required task-unit record",
                    "the host records its raw UTF-8 payload",
                    "operator-message boundary records",
                    "native host adapter is required",
                    "native callback is required",
                ):
                    self.assertNotIn(forbidden, normalized)

    def test_removed_surface_is_absent_from_cli_mcp_and_templates(self) -> None:
        retired = "attest-" + "intent"
        self.assertNotIn(retired, SUBCOMMANDS)
        self.assertNotIn(retired, OPERATOR_ONLY_SUBCOMMANDS)
        self.assertNotIn(retired.replace("-", "_"), {item["name"] for item in server.list_tools()})
        retired_template = "INTENT_" + "ATTESTATION.md"
        self.assertFalse((Path(__file__).parents[1] / "templates" / retired_template).exists())

    def test_v08_changelog_history_is_byte_identical_and_v09_is_forward(self) -> None:
        changelog = (
            Path(__file__).parents[1] / "protocol" / "CHANGELOG.md"
        ).read_text(encoding="utf-8")
        self.assertLess(changelog.index("### v0.9.0"), changelog.index("### v0.8.0"))
        v08 = "### v0.8.0" + changelog.split("### v0.8.0", 1)[1].split(
            "### v0.7.0", 1
        )[0]
        self.assertEqual(
            hashlib.sha256(v08.encode("utf-8")).hexdigest(),
            "9572b8da2ea418ca08b2a7feec8d43a70b032c83e16ebca7da3428a01383f662",
        )


if __name__ == "__main__":
    unittest.main()
