"""Tests for the shared request-evidence resolver (plan section 5 item 4).

Every fixture drives the real host intake adapter (``handle_event``), the
real ``select-project`` binding, and the real requirements/plan writers, with
an isolated HOME (project registry) and intake root. Evidence is then
resolved through the same entry points the consumers use
(``request_trace.context_for_*``) so what is asserted is what a reviewer
prompt would carry.
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

from cli import evidence_resolver, intake_adapter, request_trace
from cli.commands import select_project, write_plan, write_requirements
from cli.intake_adapter import SessionStore, handle_event
from cli.request_trace import GovernedUnit, RequestRefusal

PROJECT = GovernedUnit("project", "project")

SUMMARY = (
    "Here is the intent as I have it. Outcome: a CLI that syncs notes to "
    "Markdown. Beneficiary: solo writers. Why now: the old tool is retired "
    "next month. Success signal: a full export runs unattended. Binding "
    "constraint: no cloud storage. Exclusions: no mobile app. Correct "
    "anything that is wrong."
)


class Session:
    """One host session driven through the adapter exactly as a hook would."""

    def __init__(self, store: SessionStore, host: str, session_id: str) -> None:
        self.store = store
        self.host = host
        self.session_id = session_id
        self.key = "prompt_id" if host == "claude" else "turn_id"
        self.turn = 0
        self.lines: list[str] = []
        self._event("SessionStart")

    def _event(self, event: str, **extra) -> dict | None:
        payload = {"session_id": self.session_id, "hook_event_name": event, "cwd": "/tmp/work"}
        payload.update(extra)
        line, recorded = handle_event(self.store, self.host, payload)
        if line:
            self.lines.append(line)
        return recorded

    @property
    def record(self) -> dict:
        return self.store.load_session(self.host, self.session_id)

    @property
    def handle(self) -> str:
        return self.record["handle"]

    def say(self, text: str, *, turn_id: str | None = None) -> str:
        """Operator prompt; returns its capture identity."""
        self.turn += 1
        turn_id = turn_id or f"t{self.turn}"
        self.current_turn = turn_id
        recorded = self._event("UserPromptSubmit", prompt=text, **{self.key: turn_id})
        return f"{self.handle}/turn-{recorded['ordinal']}"

    def stop(self, text: str, *, turn_id: str | None = None) -> int:
        recorded = self._event("Stop", last_assistant_message=text, **{self.key: turn_id or self.current_turn})
        return recorded["ordinal"]

    def interrupt(self) -> None:
        self._event("Interrupt", **{self.key: self.current_turn})

    def exchange(self, operator: str, assistant: str) -> str:
        cid = self.say(operator)
        self.stop(assistant)
        return cid

    def end(self) -> None:
        self._event("SessionEnd", reason="other")


class ResolverCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.home = base / "home"
        (self.home / ".cartopian").mkdir(parents=True)
        self.intake = base / "intake"
        self.store = SessionStore(self.intake)
        self.root = base / "notes"
        for sub in ("tasks/open", "tasks/in-review", "phases", "decisions", "reviews", "prompts", "reports"):
            (self.root / sub).mkdir(parents=True)
        self.write_config("v0.13.0")
        (self.home / ".cartopian" / "projects.json").write_text(
            json.dumps([{"id": "notes", "path": str(self.root), "label": "notes"}])
        )
        env = {
            k: v for k, v in os.environ.items()
            if k not in (intake_adapter.ROLE_ENV, "CARTOPIAN_MCP_TOOL_CALL")
        }
        env["HOME"] = str(self.home)
        env[intake_adapter.INTAKE_ROOT_ENV] = str(self.intake)
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    # -- fixture helpers ---------------------------------------------------
    def write_config(self, version: str) -> None:
        (self.root / "cartopian.toml").write_text(
            "[project]\nname = \"notes\"\nid = \"notes\"\n"
            f"project_schema_version = \"{version}\"\n",
            encoding="utf-8",
        )

    def session(self, host: str = "claude", session_id: str = "sess-1") -> Session:
        return Session(self.store, host, session_id)

    def bind(self, session: Session, *, unbind: bool = False) -> tuple[int, str]:
        args = argparse.Namespace(project_root=str(self.root), handle=session.handle, unbind=unbind)
        return self._run(select_project.handler, args)

    def lock_requirements(self, handle: str | None = None, content: str = "# Requirements\n") -> tuple[int, str]:
        args = argparse.Namespace(project_root=str(self.root), content=content, content_file=None, handle=handle)
        return self._run(write_requirements.handler, args)

    def lock_plan(self, handle: str | None = None, content: str = "# Plan\n") -> tuple[int, str]:
        args = argparse.Namespace(project_root=str(self.root), content=content, content_file=None, handle=handle)
        return self._run(write_plan.handler, args)

    @staticmethod
    def _run(handler, args) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = handler(args)
        return code, err.getvalue()

    def planning_exchange(self, session: Session, reply: str = "yes") -> str:
        session.exchange("I want a CLI that syncs my notes to Markdown.", "Who is it for?")
        session.exchange("Solo writers like me; no cloud storage.", "Why now?")
        session.exchange("The old tool is retired next month.", SUMMARY)
        return session.say(reply)

    def write_decision(self, dec_id: str, marker: str, quote: str | None = None) -> None:
        body = (
            f"# {dec_id}: Scope\n\nDate: 2026-09-08\nStatus: locked\nSupersedes: none\n\n"
            f"## Context\n\n{marker}\n"
        )
        if quote is not None:
            body += "\n> " + quote.replace("\n", "\n> ") + "\n"
        (self.root / "decisions" / f"{dec_id}.md").write_text(body, encoding="utf-8")

    def seed_planned_task(self, task_id: str = "TASK-01-001", plan_ref: str = "BUILD-01-001") -> Path:
        (self.root / "IMPLEMENTATION_PLAN.md").write_text(f"# Plan\n\n- `{plan_ref}` — Sync.\n", encoding="utf-8")
        (self.root / "phases/PHASE-01.md").write_text(f"# PHASE-01\n\n- `{plan_ref}` — Sync.\n", encoding="utf-8")
        task = self.root / "tasks/open" / f"{task_id}.md"
        task.write_text(f"# {task_id}: Sync\n\nPhase: PHASE-01\nPlan ref: {plan_ref}\n", encoding="utf-8")
        return task

    def approve_checkpoint(self, checkpoint: str, plan_ref: str, evidence: list[str], context_identity: str = "") -> None:
        lines = [
            f"# REVIEW-{checkpoint}", "", f"Target: planning:{checkpoint}", f"Plan ref: {plan_ref}",
            "Verdict: approve", "Request alignment: aligned", f"Request evidence: {', '.join(evidence)}",
        ]
        if context_identity:
            lines.append(f"Request-context identity: {context_identity}")
        (self.root / "reviews" / f"REVIEW-{checkpoint}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def revoke(self, evidence: list[str], review_contexts: list[str] = ()) -> None:
        (self.root / "requests" / "revocations.json").write_text(json.dumps({
            "schema": evidence_resolver.REVOCATIONS_SCHEMA,
            "revocations": [{
                "revocation_id": "REVOKE-001", "recorded_at": "2026-09-08T00:00:00+00:00",
                "evidence": evidence, "review_contexts": list(review_contexts), "reason": "test",
            }],
        }), encoding="utf-8")

    def bindings(self) -> list[dict]:
        return evidence_resolver.read_bindings(self.root)


class PlanningExchangeTests(ResolverCase):
    def test_initial_planning_exchange_yields_a_usable_planning_approval(self) -> None:
        session = self.session()
        self.assertEqual(self.bind(session)[0], 0)
        reply = self.planning_exchange(session, "yes")

        code, err = self.lock_requirements()
        self.assertEqual(code, 0, err)
        confirmation = self.bindings()[0]["confirmation"]
        self.assertEqual(confirmation["reply"], reply)
        self.assertEqual(confirmation["pair_id"], f"{session.handle}/pair-8")
        self.assertEqual(confirmation["reply_turn_id"], "t4")
        self.assertEqual(confirmation["proposal_turn_id"], "t3")
        self.assertIsNone(confirmation["supersedes"])
        # The session record carries the same confirmation.
        self.assertEqual(session.record["bindings"][0]["confirmation"], confirmation)

        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertFalse(context.legacy)
        self.assertEqual(context.evidence_ids, [reply])
        record = context.trace[0]
        self.assertEqual(record.kind, "confirmation")
        self.assertEqual(record.unit, PROJECT)
        self.assertEqual(record.text, "yes")
        self.assertEqual(record.source_kind, "adapter-capture")
        self.assertEqual(record.context.text, SUMMARY)
        self.assertEqual(record.context.capture_id, f"{session.handle}/proposal-7")
        self.assertIn("Request state: resolved", context.section)
        self.assertIn(SUMMARY, context.section)
        self.assertIn("context, not evidence", context.section)
        self.assertIn(f"Omitted candidates: 3 across 1 session.", context.section)
        self.assertEqual(context.omitted, 3)

        # Plan lock reuses the confirmation; nothing is asked or bound again.
        code, err = self.lock_plan()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.bindings()[0]["confirmation"], confirmation)

    def test_correcting_reply_retains_proposal_and_exception(self) -> None:
        session = self.session()
        self.bind(session)
        reply = self.planning_exchange(session, "Yes, except the export must also cover attachments.")
        self.assertEqual(self.lock_requirements()[0], 0)

        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(context.evidence_ids, [reply])
        self.assertEqual(context.trace[0].text, "Yes, except the export must also cover attachments.")
        self.assertEqual(context.trace[0].context.text, SUMMARY)
        self.assertIn("also cover attachments", context.section)
        self.assertIn("no cloud storage", context.section)

    def test_lock_binds_content_bearing_unpaired_statement(self) -> None:
        # A self-contained scope statement needs no proposal to bound it.
        session = self.session()
        self.bind(session)
        cid = session.say("Build a CLI that syncs notes to Markdown; no cloud storage.")
        self.assertEqual(self.lock_requirements()[0], 0)
        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(context.evidence_ids, [cid])
        self.assertIsNone(context.trace[0].context)

    def test_no_bindings_falls_back_to_the_legacy_record_gate(self) -> None:
        code, err = self.lock_requirements()
        self.assertEqual(code, 1)
        self.assertIn("request-not-captured", err)
        self.assertIn("Never create, copy, or edit records", err)

    def test_unrelated_exchanges_are_omitted_and_counted(self) -> None:
        session = self.session()
        self.bind(session)
        session.exchange("What time is it in Lisbon?", "About noon.")
        session.exchange("Could we use S3 instead?", "You said no cloud storage; shall I drop it?")
        session.exchange("Drop it.", "Dropped.")
        reply = self.planning_exchange(session)
        self.lock_requirements()

        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(context.evidence_ids, [reply])
        self.assertNotIn("Lisbon", context.section)
        self.assertNotIn("S3", context.section)
        resolution = evidence_resolver.resolve(self.root, PROJECT)
        self.assertEqual(resolution.candidates_total, 7)
        self.assertEqual(resolution.omitted, 6)
        self.assertEqual(resolution.sessions, 1)

    def test_stop_refired_by_the_stop_guard_collapses_to_one_proposal(self) -> None:
        session = self.session()
        self.bind(session)
        session.exchange("Sync notes to Markdown.", "Who is it for?")
        session.say("Solo writers.")
        session.stop(SUMMARY)
        session.stop(SUMMARY)  # the stop guard re-fires for the same turn
        reply = session.say("yes")
        self.lock_requirements()
        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(context.evidence_ids, [reply])
        self.assertEqual(context.trace[0].context.text, SUMMARY)
        self.assertEqual(context.section.count(SUMMARY), 1)


class ReferenceTests(ResolverCase):
    def _confirmed(self) -> tuple[Session, str]:
        session = self.session()
        self.bind(session)
        reply = self.planning_exchange(session)
        self.assertEqual(self.lock_requirements()[0], 0)
        return session, reply

    def test_decision_reference_adds_a_correction_after_the_confirmation(self) -> None:
        session, reply = self._confirmed()
        correction = session.exchange("Also skip drafts older than a year.", "Noted; drafts older than a year are skipped.")
        self.write_decision("DEC-001", f"Operator request evidence for: project:project: {correction}")

        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(context.evidence_ids, [reply, correction])
        self.assertEqual([r.kind for r in context.trace], ["confirmation", "correction"])
        self.assertEqual(context.trace[1].text, "Also skip drafts older than a year.")
        self.assertIn("Operator correction", context.section)

    def test_reference_before_the_confirmation_is_an_instruction(self) -> None:
        session = self.session()
        self.bind(session)
        first = session.exchange("Sync notes to Markdown; never touch the originals.", "Understood.")
        reply = self.planning_exchange(session)
        self.lock_requirements()
        (self.root / "REQUIREMENTS.md").write_text(
            f"# Requirements\n\n## Request evidence\n\n- {first}\n", encoding="utf-8"
        )
        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(context.evidence_ids, [first, reply])
        self.assertEqual([r.kind for r in context.trace], ["instruction", "confirmation"])

    def test_whole_quote_under_the_marker_is_accepted_and_partial_is_not(self) -> None:
        session, reply = self._confirmed()
        correction = session.exchange("Also skip drafts older than a year.", "Noted.")
        self.write_decision("DEC-001", f"Operator request evidence for: project:project: {correction}",
                            quote="Also skip drafts   older than a year.")
        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(context.evidence_ids, [reply, correction])

        self.write_decision("DEC-001", f"Operator request evidence for: project:project: {correction}",
                            quote="skip drafts older than a year")
        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(context.evidence_ids, [reply])
        resolution = evidence_resolver.resolve(self.root, PROJECT)
        self.assertEqual([(u.reference, u.reason) for u in resolution.unconfirmed], [(correction, "partial-quotation")])

    def test_reference_to_an_uncaptured_turn_is_unconfirmed(self) -> None:
        _session, reply = self._confirmed()
        other = self.session(session_id="elsewhere")
        stray = other.exchange("Add a mobile app.", "Sure.")
        self.write_decision("DEC-001", f"Operator request evidence for: project:project: {stray}")
        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(context.evidence_ids, [reply])
        resolution = evidence_resolver.resolve(self.root, PROJECT)
        self.assertEqual([(u.reference, u.reason) for u in resolution.unconfirmed], [(stray, "not-captured")])

    def test_same_turn_under_two_units_is_cross_unit_and_unconfirmed(self) -> None:
        session, reply = self._confirmed()
        turn = session.exchange("Skip drafts.", "Noted.")
        self.write_decision("DEC-001", f"Operator request evidence for: project:project: {turn}")
        self.write_decision("DEC-002", f"Operator request evidence for: task:TASK-01-001: {turn}")
        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(context.evidence_ids, [reply])
        reasons = {u.reason for u in evidence_resolver.resolve(self.root, PROJECT).unconfirmed}
        self.assertEqual(reasons, {"cross-unit"})

    def test_referenced_bare_assent_without_proposal_is_unconfirmed(self) -> None:
        session, reply = self._confirmed()
        session.say("Shall I skip drafts?", turn_id="q")
        session.interrupt()
        assent = session.say("yes")
        self.write_decision("DEC-001", f"Operator request evidence for: project:project: {assent}")
        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(context.evidence_ids, [reply])
        resolution = evidence_resolver.resolve(self.root, PROJECT)
        self.assertEqual([(u.reference, u.reason) for u in resolution.unconfirmed], [(assent, "unpaired-assent")])

    def test_malformed_evidence_marker_fails_closed(self) -> None:
        self._confirmed()
        self.write_decision("DEC-001", "Operator request evidence for: whichever unit: cs-0000000000000000/turn-1")
        with self.assertRaises(RequestRefusal) as caught:
            request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(caught.exception.rule, "malformed-decision-evidence-marker")

    def test_identical_words_after_an_intervening_correction_are_kept(self) -> None:
        session, reply = self._confirmed()
        # Every reply here answers the same assistant message ("Noted."), so
        # only the words and the position relative to a correction differ.
        a = session.exchange("Skip drafts.", "Noted.")
        b = session.exchange("Actually keep drafts, but flag them.", "Noted.")
        c = session.exchange("Skip drafts.", "Noted.")
        self.write_decision("DEC-001", f"Operator request evidence for: project:project: {a}, {b}, {c}")
        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(context.evidence_ids, [reply, a, b, c])
        # Same words, same proposal, no correction in between: one presentation.
        d = session.exchange("Skip drafts.", "Noted.")
        self.write_decision("DEC-001", f"Operator request evidence for: project:project: {a}, {b}, {c}, {d}")
        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(context.evidence_ids, [reply, a, b, c])

    def test_task_bound_reference_governs_the_task_alone(self) -> None:
        session, reply = self._confirmed()
        task = self.seed_planned_task()
        task_turn = session.exchange("For the sync task, also emit a manifest.", "Manifest added to the task.")
        self.write_decision("DEC-001", f"Operator request evidence for: task:TASK-01-001: {task_turn}")
        context = request_trace.context_for_task_assignment(self.root, task)
        self.assertEqual(context.evidence_ids, [task_turn])
        self.assertEqual(context.trace[0].unit, GovernedUnit("task", "TASK-01-001"))
        self.assertEqual(context.trace[0].kind, "instruction")


class ContinuityTests(ResolverCase):
    def test_resumed_session_and_second_task_proceed_on_the_existing_binding(self) -> None:
        session = self.session()
        self.bind(session)
        reply = self.planning_exchange(session)
        self.lock_requirements()
        session.end()  # bound: the session survives SessionEnd

        checkpoint = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.approve_checkpoint("PLAN-001", "BUILD-01-001 through BUILD-01-002", checkpoint.evidence_ids, checkpoint.context_identity)
        first = self.seed_planned_task("TASK-01-001", "BUILD-01-001")
        second = self.root / "tasks/open/TASK-01-002.md"
        (self.root / "IMPLEMENTATION_PLAN.md").write_text("# Plan\n\n- `BUILD-01-001`\n- `BUILD-01-002`\n", encoding="utf-8")
        (self.root / "phases/PHASE-01.md").write_text("# PHASE-01\n\n- `BUILD-01-001`\n- `BUILD-01-002`\n", encoding="utf-8")
        second.write_text("# TASK-01-002: Manifest\n\nPhase: PHASE-01\nPlan ref: BUILD-01-002\n", encoding="utf-8")

        # A fresh session resuming the project binds independently and asks nothing.
        resumed = self.session(session_id="sess-2")
        self.bind(resumed)
        resumed.exchange("Resume where we left off.", "Resuming.")
        for task in (first, second):
            context = request_trace.context_for_task_assignment(self.root, task)
            self.assertEqual(context.evidence_ids, [reply])
            self.assertEqual(context.trace[0].context.text, SUMMARY)
        self.assertEqual(len(self.bindings()), 2)
        self.assertIsNone(self.bindings()[1]["confirmation"])

    def test_planning_across_two_sessions_unions_candidates(self) -> None:
        first = self.session(session_id="sess-1")
        self.bind(first)
        first.exchange("Sync notes to Markdown.", "Who is it for?")
        first.end()
        second = self.session(session_id="sess-2")
        self.bind(second)
        second.exchange("Solo writers; no cloud storage.", "Why now?")
        second.exchange("The old tool is retired next month.", SUMMARY)
        reply = second.say("yes")
        code, err = self.lock_requirements()
        self.assertEqual(code, 0, err)
        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(context.evidence_ids, [reply])
        self.assertEqual(evidence_resolver.resolve(self.root, PROJECT).sessions, 2)

    def test_two_active_sessions_need_the_handle_named_at_lock(self) -> None:
        a = self.session(session_id="sess-a")
        b = self.session(session_id="sess-b")
        self.bind(a)
        self.bind(b)
        self.planning_exchange(a)
        b.exchange("Unrelated chatter.", "Sure.")
        code, err = self.lock_requirements()
        self.assertEqual(code, 1)
        self.assertIn("ambiguous-session", err)
        code, err = self.lock_requirements(handle=a.handle)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.bindings()[0]["confirmation"]["reply"], f"{a.handle}/turn-8")

    def test_noisy_fixture_keeps_packet_size_invariant_to_candidate_count(self) -> None:
        session = self.session()
        self.bind(session)
        reply = self.planning_exchange(session)
        self.lock_requirements()
        quiet = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        for index in range(60):
            session.exchange(f"Unrelated question number {index} about formatting.", f"Answer {index}.")
        noisy = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(noisy.evidence_ids, [reply])
        # The packet carries the same words and identities; only the
        # omitted-candidates telemetry line changes, and it is not bound.
        self.assertEqual(
            request_trace.bound_section_text(noisy.section),
            request_trace.bound_section_text(quiet.section),
        )
        self.assertIn("Omitted candidates: 3 across 1 session.", quiet.section)
        self.assertIn("Omitted candidates: 63 across 1 session.", noisy.section)
        self.assertEqual(noisy.context_identity, quiet.context_identity)
        # A prompt written before the chatter still binds afterwards.
        prompt = request_trace.upsert_request_sections("# Planning review\n", quiet.section)
        self.assertTrue(request_trace.preflight_prompt_binding(noisy, prompt)["ok"])


class RefusalTests(ResolverCase):
    def test_bare_assent_after_no_stop_is_unpaired(self) -> None:
        session = self.session()
        self.bind(session)
        session.say("Sync notes to Markdown.")  # no Stop was recorded
        session.say("yes")
        code, err = self.lock_requirements()
        self.assertEqual(code, 1)
        self.assertIn("unpaired-assent", err)
        self.assertIsNone(self.bindings()[0]["confirmation"])
        with self.assertRaises(RequestRefusal) as caught:
            request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(caught.exception.rule, "unit-request-not-captured")

    def test_codex_interrupt_followed_by_yes_is_unpaired(self) -> None:
        session = self.session(host="codex", session_id="thread-1")
        self.bind(session)
        session.exchange("Sync notes to Markdown.", "Who is it for?")
        session.say("Solo writers.")
        session.stop(SUMMARY)
        session.interrupt()
        session.say("yes")
        code, err = self.lock_requirements()
        self.assertEqual(code, 1)
        self.assertIn("unpaired-assent", err)
        self.assertIn("interrupted", err)

    def test_late_stop_with_differing_text_makes_the_pair_inconsistent_and_blocks(self) -> None:
        session = self.session()
        self.bind(session)
        session.exchange("Sync notes to Markdown.", "Who is it for?")
        session.say("Solo writers.", turn_id="summary")
        session.stop(SUMMARY, turn_id="summary")
        reply = session.say("yes")
        self.assertEqual(self.lock_requirements()[0], 0)
        self.assertEqual(request_trace.context_for_checkpoint(self.root, "PLAN-001").evidence_ids, [reply])

        session.stop(SUMMARY + " Also, cloud storage is fine.", turn_id="summary")
        with self.assertRaises(RequestRefusal) as caught:
            request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(caught.exception.rule, "inconsistent-pair")
        self.assertIn("fresh scope statement", caught.exception.recovery)
        # Re-locking does not clear it either.
        code, err = self.lock_plan()
        self.assertEqual(code, 1)
        self.assertIn("inconsistent-pair", err)

    def test_pm_supplied_session_identity_in_bindings_is_unreceipted(self) -> None:
        genuine = self.session(session_id="sess-1")
        self.bind(genuine)
        self.planning_exchange(genuine)
        self.lock_requirements()
        other = self.session(session_id="sess-2")
        other.exchange("Ship a mobile app too.", "Mobile app added.")
        other.say("yes")
        # The PM edits bindings.json to point the binding at the other session.
        bindings = self.bindings()
        forged = dict(bindings[0])
        forged.update({"binding_id": f"{other.handle}/binding-1", "handle": other.handle, "session_id": "sess-2", "confirmation": None})
        evidence_resolver.write_bindings(self.root, [forged])

        with self.assertRaises(RequestRefusal) as caught:
            request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(caught.exception.rule, "unit-request-not-captured")
        resolution = evidence_resolver.resolve(self.root, PROJECT)
        self.assertEqual(resolution.unreceipted_bindings, [f"{other.handle}/binding-1"])
        self.assertEqual(resolution.candidates_total, 0)

    def test_hand_written_confirmation_is_unreceipted(self) -> None:
        session = self.session()
        self.bind(session)
        reply = self.planning_exchange(session)
        bindings = self.bindings()
        bindings[0]["confirmation"] = {
            "pair_id": f"{session.handle}/pair-8", "reply": reply, "reply_turn_id": "t4",
            "proposal": f"{session.handle}/proposal-7", "proposal_turn_id": "forged",
            "supersedes": None, "bound_at": "2026-09-08T00:00:00+00:00",
        }
        evidence_resolver.write_bindings(self.root, bindings)
        with self.assertRaises(RequestRefusal) as caught:
            request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(caught.exception.rule, "confirmation-unreceipted")

    def test_evicted_buffer_yields_a_specific_recovery_request(self) -> None:
        session = self.session()
        early = session.exchange("Sync notes to Markdown; never touch the originals.", "Understood.")
        for index in range(intake_adapter.PRESELECTION_MAX_EVENTS):
            session.exchange(f"filler {index}", f"reply {index}")
        self.bind(session)
        reply = session.say("Build a CLI that syncs notes to Markdown; no cloud storage.")
        self.lock_requirements()
        self.write_decision("DEC-001", f"Operator request evidence for: project:project: {early}")

        resolution = evidence_resolver.resolve(self.root, PROJECT)
        self.assertEqual([e.record_id for e in resolution.evidence], [reply])
        self.assertEqual(len(resolution.evictions), 1)
        self.assertEqual([(u.reference, u.reason) for u in resolution.unconfirmed], [(early, "evicted")])
        self.assertIn("ask the operator to restate", resolution.unconfirmed[0].detail)

    def test_lowered_schema_version_does_not_reopen_the_legacy_path(self) -> None:
        session = self.session()
        self.bind(session)
        session.say("Sync notes.")
        session.say("yes")
        self.write_config("v0.8.0")
        with self.assertRaises(RequestRefusal) as caught:
            request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(caught.exception.rule, "unit-request-not-captured")
        with self.assertRaises(RequestRefusal):
            request_trace.require_request_before_derivative(self.root, "task", "tasks/open/TASK-01-001.md")

    def test_tampered_intake_event_makes_the_bound_context_stale(self) -> None:
        session = self.session()
        self.bind(session)
        self.planning_exchange(session, "Yes, and no cloud storage.")
        self.lock_requirements()
        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        prompt = request_trace.upsert_request_sections("# Planning review\n", context.section)
        path = self.store.events_path("claude", "sess-1")
        path.write_text(path.read_text(encoding="utf-8").replace("no cloud storage.", "cloud storage is fine."), encoding="utf-8")
        current = request_trace.context_for_checkpoint(self.root, "PLAN-001", checkpoint_text=prompt)
        self.assertFalse(request_trace.preflight_prompt_binding(current, prompt)["ok"])


class RevocationTests(ResolverCase):
    def test_revoked_identity_blocks_regenerated_prompts_and_supersession_recovers(self) -> None:
        session = self.session()
        self.bind(session)
        reply = self.planning_exchange(session)
        self.lock_requirements()
        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.approve_checkpoint("PLAN-001", "BUILD-01-001", [reply], context.context_identity)
        task = self.seed_planned_task()
        self.assertEqual(request_trace.context_for_task_assignment(self.root, task).evidence_ids, [reply])

        self.revoke([reply], [context.context_identity])
        with self.assertRaises(RequestRefusal) as caught:
            request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(caught.exception.rule, "unit-request-not-captured")
        with self.assertRaises(RequestRefusal) as caught:
            request_trace.context_for_task_assignment(self.root, task)
        self.assertEqual(caught.exception.rule, "revoked-evidence")
        (self.root / "reviews/REVIEW-PLAN-001.md").unlink()
        with self.assertRaises(RequestRefusal):
            request_trace.context_for_task_assignment(self.root, task)

        # A fresh scope statement supersedes the revoked confirmation.
        fresh = session.exchange(
            "Fresh scope: sync notes to Markdown for solo writers, no cloud storage, no mobile app.",
            "Recorded.",
        )
        code, err = self.lock_requirements()
        self.assertEqual(code, 0, err)
        confirmation = self.bindings()[0]["confirmation"]
        self.assertEqual(confirmation["reply"], fresh)
        self.assertEqual(confirmation["supersedes"], reply)
        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(context.evidence_ids, [fresh])
        self.assertEqual(context.trace[0].kind, "confirmation")
        # A review approved on the fresh context is inherited again.
        self.approve_checkpoint("PLAN-001", "BUILD-01-001", [fresh], context.context_identity)
        self.assertEqual(request_trace.context_for_task_assignment(self.root, task).evidence_ids, [fresh])

    def test_revoked_reference_is_unconfirmed_and_revoked_record_original_is_not_counted(self) -> None:
        session = self.session()
        self.bind(session)
        reply = self.planning_exchange(session)
        self.lock_requirements()
        turn = session.exchange("Skip drafts.", "Noted.")
        self.write_decision("DEC-001", f"Operator request evidence for: project:project: {turn}")
        self.revoke([turn])
        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(context.evidence_ids, [reply])
        self.assertEqual([(u.reference, u.reason) for u in evidence_resolver.resolve(self.root, PROJECT).unconfirmed], [(turn, "revoked")])


if __name__ == "__main__":
    unittest.main()
