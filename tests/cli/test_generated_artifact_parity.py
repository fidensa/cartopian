"""Cartopian accepts the artifacts its own writers generate.

Evidence gate (red-before-green), one class per reported defect:

1. RED: for an in-review task with a retained request-changes review, the
   task-closure ``write-prompt`` materialized the rework-review payload, and
   ``handoff-packet`` (which excludes rework input for closure review)
   refused it as ``input-payload-audit``. GREEN: ``rework_review.applies`` is
   the one predicate the composer, writer, preflight, and dispatch consult,
   and the closure writer runs the same payload audit preflight runs.
   Forged or stale payloads still refuse.
2. RED: a review report whose header omitted ``Request-context identity``
   while the retained review carried it passed ``validate-report``, was
   refused by ``report-action``, and ``correct-report`` refused
   ``no-defect-to-correct``; the skeleton and template never emitted the
   field. GREEN: one binding rule (``report_action.retained_review_binding``)
   backs routing and the named ``retained-review-bound`` check; generators
   emit the field; a verified missing value is a mechanical correction, and
   stale, forged, or verdict-mismatched bindings are not.
3. RED: ``source_guidance`` split rows at every ``;`` and silently dropped
   the tail of a Scope or Rule value. GREEN: fields start only at
   ``; <Label>:`` boundaries, and ambiguous rows are rejected by name.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import unittest
from pathlib import Path

from cli import assignment_inputs, request_trace, rework_review, source_guidance
from cli.commands import correct_report, handoff_packet, report_action, validate_report, write_prompt
from tests.cli.commands.test_report_action import _review_report
from tests.scaffold import project_scaffold
from tests.test_kickoff_readiness import _TOML_PLANNING_REVIEW, _capture, _isolated_home
from tests.test_review_bootstrap_parity import BootstrapFixture

RETAINED_REVIEW = (
    "# REVIEW-07-001\n"
    "Target: TASK-07-001\n"
    "Verdict: request-changes\n"
    "## Findings\n"
    "- F1. [major] Fix the bootstrap parity output.\n"
)


def _invoke(handler, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = handler(argparse.Namespace(**kwargs))
    records = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    return code, records, err.getvalue()


# ---------------------------------------------------------------------------
# 1. Closure prompt writer and handoff preflight agree
# ---------------------------------------------------------------------------


class ClosureWriterPreflightParityTests(BootstrapFixture):
    def setUp(self) -> None:
        super().setUp()
        self.review = self.root / "reviews/REVIEW-07-001.md"
        self.review.write_text(RETAINED_REVIEW, encoding="utf-8")
        self.move_to_review()
        self.prompt = self.root / "prompts/PROMPT-07-001.md"

    def write_closure_prompt(self, body: str = "# Review task completion\n"):
        return self.run_cli(
            "write-prompt", str(self.root), "--prompt-id", "PROMPT-07-001",
            "--review-kind", "task-closure", "--task", str(self.task),
            "--content", body,
        )

    def handoff(self):
        return _invoke(handoff_packet.handler, task_path=str(self.task), role="reviewer")

    def test_rework_input_applies_only_outside_review(self) -> None:
        self.assertFalse(rework_review.applies(self.task))
        self.assertTrue(rework_review.applies(self.root / "tasks/in-progress/TASK-07-001.md"))

    def test_generated_closure_prompt_passes_handoff_preflight(self) -> None:
        code, _records, err = self.write_closure_prompt()
        self.assertEqual(code, 0, err)
        text = self.prompt.read_text(encoding="utf-8")
        self.assertNotIn("## Review findings input", text)
        self.assertEqual(assignment_inputs.extract_payload_blocks(text), [])

        code, records, err = self.handoff()
        self.assertEqual(code, 0, err)
        self.assertTrue(records[0]["input_payload_audit"]["ok"])
        self.assertTrue(records[0]["request_trace"]["preflight"]["ok"])

    def test_closure_writer_refuses_an_authored_rework_payload(self) -> None:
        forged = (
            "# Review task completion\n\n"
            + assignment_inputs.render_payload_block(
                assignment_inputs.CHANNEL_REWORK,
                "reviews/REVIEW-07-001.md",
                RETAINED_REVIEW,
            )
            + "\n"
        )
        code, _records, err = self.write_closure_prompt(forged)
        self.assertEqual(code, 1)
        self.assertIn("unbound-input-payload", err)
        self.assertFalse(self.prompt.exists())

    def test_preflight_still_refuses_a_rework_payload_injected_after_writing(self) -> None:
        code, _records, err = self.write_closure_prompt()
        self.assertEqual(code, 0, err)
        injected = (
            self.prompt.read_text(encoding="utf-8").rstrip()
            + "\n\n## Review findings input\n\n"
            + assignment_inputs.render_payload_block(
                assignment_inputs.CHANNEL_REWORK,
                "reviews/REVIEW-07-001.md",
                self.review.read_text(encoding="utf-8"),
            )
            + "\n"
        )
        self.prompt.write_text(injected, encoding="utf-8")
        code, _records, err = self.handoff()
        self.assertEqual(code, 1)
        self.assertIn("input-payload-audit", err)
        self.assertIn("not a machine-resolved assignment input", err)

    def test_closure_writer_runs_the_preflight_payload_audit(self) -> None:
        # Any problem the preflight audit would name refuses at write time,
        # before a prompt lands.
        original = handoff_packet.audit_prompt_payloads
        seen = {}

        def spy(*args, **kwargs):
            seen.update(kwargs)
            return {"ok": False, "problems": ["forced stale payload"]}

        handoff_packet.audit_prompt_payloads = spy
        try:
            code, _records, err = self.write_closure_prompt()
        finally:
            handoff_packet.audit_prompt_payloads = original
        self.assertEqual(code, 1)
        self.assertIn("assignment-input-stale: forced stale payload", err)
        self.assertIs(seen.get("include_rework"), False)
        self.assertFalse(self.prompt.exists())


# ---------------------------------------------------------------------------
# 2. Report validation, routing, and correction agree
# ---------------------------------------------------------------------------


class ReviewReportContextParityTests(unittest.TestCase):
    checkpoint = "PLAN-BUILD-01-005"

    def setUp(self) -> None:
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(_isolated_home())
        scaffold = stack.enter_context(project_scaffold(cartopian_toml=_TOML_PLANNING_REVIEW))
        self.root = scaffold.project_root
        _capture(self.root, "Build the scoped feature.", record_id="REQUEST-001", sequence=0)
        scaffold.write("tasks/open/TASK-01-005.md", "Phase: PHASE-01\nPlan ref: BUILD-01-005\n")
        scaffold.write("specs/SPEC-01-005.md", "# Spec\n")
        code, _records, err = _invoke(
            write_prompt.handler, project_root=str(self.root),
            prompt_id="PROMPT-" + self.checkpoint,
            content="# Review\n\n## Your role\nReview this scope.\n",
            content_file=None, review_kind="planning", task=None,
            checkpoint=self.checkpoint, phase="PHASE-01", plan_ref="BUILD-01-005",
        )
        self.assertEqual(code, 0, err)
        self.prompt = self.root / f"prompts/PROMPT-{self.checkpoint}.md"
        self.context = request_trace.context_for_checkpoint(
            self.root, self.checkpoint, checkpoint_text=self.prompt.read_text()
        ).context_identity
        self.review = self.root / f"reviews/REVIEW-{self.checkpoint}.md"
        self.report = self.root / f"reports/REPORT-{self.checkpoint}.md"

    def write_pair(self, *, review_context=None, review_verdict="approve", report_context=None):
        self.review.write_text(
            f"Request-context identity: {review_context or self.context}\n"
            f"Verdict: {review_verdict}\n"
            "Request alignment: aligned\nRequest evidence: REQUEST-001\n"
        )
        text = _review_report(
            report_stem=self.report.stem, review_id=self.review.stem,
            prompt_path=self.prompt, task_path=None, review_path=self.review,
            status="complete", verdict="approve",
        )
        line = f"Request-context identity: {self.context}\n\n"
        self.assertIn(line, text)
        text = text.replace(line, f"Request-context identity: {report_context}\n\n" if report_context else "")
        self.report.write_text(text)
        return text

    def validate(self):
        code, records, err = _invoke(
            validate_report.handler, report_path=str(self.report),
            variant=None, expected_identity=None,
        )
        return code, {item["name"]: item for item in records[0]["checks"]}, err

    def route(self):
        return _invoke(
            report_action.handler, report_path=str(self.report), variant=None,
            expected_identity=None, expected_review_identity=None,
        )

    def correct(self, corrected: str):
        current = self.report.read_text()
        from cli import report_identity

        return _invoke(
            correct_report.handler, report_path=str(self.report),
            expected_identity=report_identity.content_identity(current),
            corrected_content=corrected, corrected_file=None, variant=None,
        )

    def test_complete_pair_validates_and_routes(self) -> None:
        self.write_pair(report_context=self.context)
        code, checks, err = self.validate()
        self.assertEqual(code, 0, err)
        self.assertTrue(checks["retained-review-bound"]["pass"])
        code, records, err = self.route()
        self.assertEqual(code, 0, err)
        self.assertEqual(records[0]["verdict"], "accepted")

    def test_missing_context_is_named_by_validation_and_mechanically_corrected(self) -> None:
        text = self.write_pair(report_context=None)

        code, checks, err = self.validate()
        self.assertEqual(code, 1)
        check = checks["retained-review-bound"]
        self.assertFalse(check["pass"])
        self.assertEqual(check["failure_class"], "mechanical")
        self.assertIn(self.context, check["reason"])

        code, _records, err = self.route()
        self.assertEqual(code, 1)
        self.assertIn("Request-context identity differs", err)

        # A fabricated identity is not the verified value and never lands.
        forged = text.replace(
            "Request alignment:", f"Request-context identity: sha256:{'0' * 64}\nRequest alignment:", 1
        )
        code, _records, err = self.correct(forged)
        self.assertEqual(code, 1)
        self.assertIn("correction-does-not-validate", err)
        self.assertEqual(self.report.read_text(), text)

        # Reviewer judgment stays out of scope for the correction.
        judged = text.replace(
            "Request alignment:", f"Request-context identity: {self.context}\nRequest alignment:", 1
        ).replace("none.\n", "F1. invented finding.\n")
        code, _records, err = self.correct(judged)
        self.assertEqual(code, 1)
        self.assertIn("correction-outside-defect-scope", err)

        corrected = text.replace(
            "Request alignment:", f"Request-context identity: {self.context}\nRequest alignment:", 1
        )
        code, records, err = self.correct(corrected)
        self.assertEqual(code, 0, err)
        self.assertEqual(records[0]["resolved_checks"], ["retained-review-bound"])
        code, records, err = self.route()
        self.assertEqual(code, 0, err)
        self.assertEqual(records[0]["verdict"], "accepted")

    def test_stale_retained_review_is_substantive_and_not_correctable(self) -> None:
        stale = "sha256:" + "1" * 64
        text = self.write_pair(review_context=stale, report_context=None)
        code, checks, _err = self.validate()
        self.assertEqual(code, 1)
        self.assertEqual(checks["retained-review-bound"]["failure_class"], "substantive")
        code, _records, err = self.route()
        self.assertEqual(code, 1)
        code, _records, err = self.correct(text.replace(
            "Request alignment:", f"Request-context identity: {stale}\nRequest alignment:", 1
        ))
        self.assertEqual(code, 1)
        self.assertIn("non-mechanical-findings-present", err)

    def test_verdict_mismatch_is_substantive(self) -> None:
        self.write_pair(review_verdict="reject", report_context=self.context)
        code, checks, _err = self.validate()
        self.assertEqual(code, 1)
        check = checks["retained-review-bound"]
        self.assertEqual(check["failure_class"], "substantive")
        self.assertIn("verdict differs", check["reason"])
        code, _records, err = self.route()
        self.assertEqual(code, 1)
        self.assertIn("verdict differs", err)

    def test_missing_retained_review_is_missing_input(self) -> None:
        self.write_pair(report_context=self.context)
        self.review.unlink()
        code, checks, _err = self.validate()
        self.assertEqual(code, 1)
        self.assertEqual(checks["retained-review-bound"]["failure_class"], "missing-input")
        code, _records, _err = self.route()
        self.assertEqual(code, 1)


class ReviewSkeletonCarriesContextTests(BootstrapFixture):
    def test_review_report_skeleton_matches_the_review_file_context(self) -> None:
        self.move_to_review()
        code, _records, err = self.run_cli(
            "write-prompt", str(self.root), "--prompt-id", "PROMPT-07-001",
            "--review-kind", "task-closure", "--task", str(self.task),
            "--content", "# Review task completion\n",
        )
        self.assertEqual(code, 0, err)
        code, records, err = self.run_cli("report-skeleton", str(self.task), "--variant", "review")
        self.assertEqual(code, 0, err)
        record = records[0]
        identity = record["machine_fields"]["request_context_identity"]
        self.assertTrue(identity.startswith("sha256:"))
        line = f"Request-context identity: {identity}"
        self.assertIn(line, record["skeleton"].splitlines())
        self.assertIn(line, record["review_file_skeleton"].splitlines())

    def test_report_template_review_variants_carry_the_field(self) -> None:
        template = (Path(__file__).resolve().parents[2] / "templates/REPORT.md").read_text()
        for heading in ("# REPORT-NN-NNN-review\n", "# REPORT-<checkpoint-id>\n"):
            block = template.split(heading, 1)[1].split("## Identity", 1)[0]
            self.assertIn("Request-context identity:", block)


# ---------------------------------------------------------------------------
# 3. Source guidance values keep literal semicolons
# ---------------------------------------------------------------------------


def _guidance(source_row: str, conflict_row: str, claims: str = "- none") -> str:
    return (
        "# TASK-01-001: Demo\n\nSource guidance: task\n\n"
        "## Source guidance\n\n"
        f"### Authoritative sources\n\n- {source_row}\n\n"
        f"### Conflict resolution\n\n- {conflict_row}\n\n"
        f"### Unverified claims\n\n{claims}\n"
    )


def _evaluate(text: str):
    return source_guidance.evaluate_record(
        text, heading="Source guidance", declaration="task", owner_kind="task"
    )


class SourceGuidanceSeparatorTests(unittest.TestCase):
    SCOPE = "runtime limits; containment constraints; governed by REQUIREMENTS.md §4"
    RULE = "requirements govern product boundaries; standards govern style; operator decides ties"

    def test_semicolons_inside_values_are_preserved(self) -> None:
        record = _evaluate(_guidance(
            "Identity: Product requirements; Applicable context: revision 2026-08-13; "
            f"Status: current; Scope: {self.SCOPE}",
            f"Status: resolved; Rule: {self.RULE}; Decision: apply the requirements",
        ))
        self.assertEqual(record["outcome"], "valid", record["blockers"])
        source = record["authoritative_sources"][0]
        self.assertEqual(source["identity"], "Product requirements")
        self.assertEqual(source["applicable_context"], "revision 2026-08-13")
        self.assertEqual(source["scope"], self.SCOPE)
        self.assertEqual(record["conflict_resolution"]["rule"], self.RULE)
        self.assertEqual(record["conflict_resolution"]["decision"], "apply the requirements")
        # The canonical rendering round-trips to the same record.
        reparsed = _evaluate(
            "# TASK-01-001: Demo\n\nSource guidance: task\n\n" + source_guidance.render_guidance(record)
        )
        self.assertEqual(reparsed["authoritative_sources"], record["authoritative_sources"])
        self.assertEqual(reparsed["conflict_resolution"], record["conflict_resolution"])

    def test_semicolons_in_claim_values_are_preserved(self) -> None:
        record = _evaluate(_guidance(
            "Identity: Spec; Applicable context: v2; Status: current; Scope: all",
            "Status: none; Rule: spec governs; Decision: n/a",
            "- Claim: latency holds; under load; Decisiveness: non-decisive; "
            "Missing: benchmark; Consequence: slower path; Next: run the benchmark; then record it",
        ))
        self.assertEqual(record["outcome"], "valid", record["blockers"])
        claim = record["unverified_claims"][0]
        self.assertEqual(claim["claim"], "latency holds; under load")
        self.assertEqual(claim["next"], "run the benchmark; then record it")

    def test_a_value_repeating_a_field_label_is_rejected_not_truncated(self) -> None:
        record = _evaluate(_guidance(
            "Identity: Spec; Applicable context: v2; Status: current; "
            "Scope: pricing; Status: draft only",
            "Status: none; Rule: spec governs; Decision: n/a",
        ))
        self.assertEqual(record["outcome"], "invalid")
        self.assertIn("ambiguous-source-field", record["blocker_codes"])
        detail = next(b["detail"] for b in record["blockers"] if b["code"] == "ambiguous-source-field")
        self.assertIn("authoritative source 1", detail)
        self.assertIn("'Status' appears more than once", detail)

    def test_conflict_rule_repeating_a_label_is_rejected(self) -> None:
        record = _evaluate(_guidance(
            "Identity: Spec; Applicable context: v2; Status: current; Scope: all",
            "Status: resolved; Rule: spec governs; Decision: see ADR; Rule: operator decides",
        ))
        self.assertEqual(record["outcome"], "invalid")
        self.assertIn("ambiguous-source-field", record["blocker_codes"])

    def test_text_before_the_first_label_is_rejected(self) -> None:
        record = _evaluate(_guidance(
            "Primary source; Identity: Spec; Applicable context: v2; Status: current; Scope: all",
            "Status: none; Rule: spec governs; Decision: n/a",
        ))
        self.assertEqual(record["outcome"], "invalid")
        self.assertIn("ambiguous-source-field", record["blocker_codes"])

    def test_ambiguity_blocker_is_never_a_mechanical_report_edit(self) -> None:
        self.assertEqual(validate_report.source_blocker_class("ambiguous-source-field"), "substantive")


if __name__ == "__main__":
    unittest.main()
