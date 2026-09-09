"""Tests for the intent packet and the lookup tool (plan section 5 item 5).

Fixtures drive the real intake adapter, binding, and writers exactly as
``tests/cli/test_evidence_resolver.py`` does; what is asserted is the
reviewer channel (``ReviewContext.section``) and ``lookup-evidence`` output,
including its ``--recent`` rows.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json

from cli import evidence_resolver, request_trace
from cli.commands import lookup_evidence
from cli.intake_adapter import PRESELECTION_MAX_EVENTS
from tests.cli.test_evidence_resolver import PROJECT, SUMMARY, ResolverCase

LONG_TURN = (
    "Please make sure the exporter preserves every heading level, keeps "
    "inline code spans intact, never rewrites relative links, and writes one "
    "file per note with the note title as the filename."
)


class PacketCase(ResolverCase):
    @staticmethod
    def run_records(handler, **kwargs) -> tuple[int, list[dict], str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = handler(argparse.Namespace(**kwargs))
        records = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
        return code, records, err.getvalue()

    def lookup(self, unit: str = "planning:PLAN-001", recent: bool = False):
        return self.run_records(lookup_evidence.handler, project_root=str(self.root), unit=unit, recent=recent)


class IntentPacketTests(PacketCase):
    def test_packet_orders_instructions_exchange_corrections_then_trailer(self) -> None:
        session = self.session()
        self.bind(session)
        instruction = session.exchange(LONG_TURN, "Understood. Who is it for?")
        session.exchange("Solo writers like me; no cloud storage.", SUMMARY)
        reply = session.say("yes")
        self.lock_requirements()
        correction = session.exchange("Also skip drafts older than a year.", "Noted.")
        self.write_decision(
            "DEC-001",
            f"Operator request evidence for: project:project: {instruction}, {correction}",
        )

        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(context.evidence_ids, [instruction, reply, correction])
        self.assertEqual([r.kind for r in context.trace], ["instruction", "confirmation", "correction"])
        section = context.section

        # Original words first, in order; identities in the trailer; summary
        # channel last.
        order = [
            "### Operator instructions",
            LONG_TURN,
            "### Confirmation exchange",
            SUMMARY,
            "Operator reply:",
            "\nyes\n",
            request_trace.ASSENT_BOUND_SENTENCE,
            "### Operator corrections",
            "Also skip drafts older than a year.",
            f"Request evidence: {instruction}, {reply}, {correction}",
            f"Request-context identity: {context.context_identity}",
            "Omitted candidates: 1 across 1 session.",
            "## PM-derived guidance and delivered outcome",
        ]
        positions = [section.index(item) for item in order]
        self.assertEqual(positions, sorted(positions), section)
        # No per-excerpt metadata blocks for captured turns.
        for marker in ("Source path:", "Evidence order:", "Content identity:", "Source identity:", "Governed unit:"):
            self.assertNotIn(marker, section)
        self.assertEqual(section.count("Request-context identity:"), 1)
        self.assertEqual(context.omitted, 1)
        self.assertEqual(context.measures["candidates"], 4)
        self.assertEqual(context.as_record()["candidates"]["omitted"], 1)

    def test_packet_size_does_not_grow_with_unrelated_chatter(self) -> None:
        session = self.session()
        self.bind(session)
        self.planning_exchange(session)
        self.lock_requirements()
        quiet = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        for index in range(30):
            session.exchange(f"Unrelated question {index}?", f"Answer {index}." * 20)
        noisy = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(
            request_trace.bound_section_text(noisy.section),
            request_trace.bound_section_text(quiet.section),
        )
        self.assertNotIn("Unrelated question", noisy.section)
        self.assertIn("Omitted candidates: 33 across 1 session.", noisy.section)

    def test_eviction_is_reported_in_the_packet_and_the_lookup_remedy(self) -> None:
        session = self.session()
        session.exchange("Sync notes to Markdown; never touch the originals.", "Understood.")
        for index in range(PRESELECTION_MAX_EVENTS):
            session.exchange(f"filler {index}", f"reply {index}")
        self.bind(session)
        # Before any confirmation, the lookup names eviction as the recovery.
        code, records, err = self.lookup("project:project")
        self.assertEqual(code, 1)
        self.assertIn("unit-request-not-captured", err)
        self.assertEqual(records[0]["state"], "missing")
        self.assertIn("evicted", records[0]["missing"]["remedy"])
        self.assertIn("restate", records[0]["missing"]["remedy"])
        self.assertEqual(len(records[0]["candidates"]["evictions"]), 1)

        session.say("Build a CLI that syncs notes to Markdown; no cloud storage.")
        self.lock_requirements()
        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertIn("Evicted before selection:", context.section)
        self.assertIn("restated by the operator", context.section)
        self.assertEqual(context.as_record()["candidates"]["evictions"], 1)

    def test_legacy_record_evidence_keeps_its_block_and_gets_the_trailer(self) -> None:
        # No capture session at all: the request-record path renders as
        # before, and the trailer still carries the identity line only.
        self.write_config("v0.8.0")
        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertTrue(context.legacy)
        self.assertIn("Request state: unavailable-for-legacy", context.section)
        self.assertIn(f"Request-context identity: {context.context_identity}", context.section)
        self.assertNotIn("Omitted candidates:", context.section)
        self.assertNotIn("Request evidence:", context.section)


class RecentTurnsTests(PacketCase):
    def test_recent_lists_the_last_five_turns_with_previews_and_selection(self) -> None:
        session = self.session()
        self.bind(session)
        reply = self.planning_exchange(session)
        self.lock_requirements()
        long_turn = LONG_TURN * 3
        long_id = session.exchange(long_turn, "Noted.")
        for index in range(3):
            session.exchange(f"chatter {index}", f"answer {index}")

        code, records, err = self.lookup(recent=True)
        self.assertEqual(code, 0, err)
        recent = records[0]["recent"]
        self.assertEqual(len(recent), evidence_resolver.RECENT_TURNS)
        self.assertEqual([r["ordinal"] for r in recent], sorted(r["ordinal"] for r in recent))
        by_id = {r["capture_id"]: r for r in recent}
        self.assertEqual(by_id[reply]["selected"], "confirmation")
        long_row = by_id[long_id]
        self.assertTrue(long_row["truncated"])
        self.assertEqual(len(long_row["preview"]), evidence_resolver.CANDIDATE_PREVIEW_CHARS)
        self.assertIsNone(long_row["selected"])
        self.assertEqual(recent[-1]["preview"], "chatter 2")
        dumped = json.dumps(records[0])
        self.assertNotIn(long_turn, dumped)
        self.assertNotIn(SUMMARY, dumped)
        # Without the flag nothing is listed.
        code, records, _ = self.lookup()
        self.assertNotIn("recent", records[0])

    def test_recent_is_available_when_the_unit_has_no_usable_evidence(self) -> None:
        session = self.session()
        self.bind(session)
        session.say("Sync notes to Markdown.")
        session.say("yes")  # unpaired: no Stop was recorded
        code, records, err = self.lookup(recent=True)
        self.assertEqual(code, 1)
        self.assertEqual(records[0]["state"], "missing")
        self.assertEqual([r["pair_state"] for r in records[0]["recent"]], ["unpaired", "unpaired"])
        self.assertEqual([r["selected"] for r in records[0]["recent"]], [None, None])

    def test_recent_spans_bound_sessions_in_binding_order(self) -> None:
        a = self.session(session_id="sess-a")
        self.bind(a)
        self.planning_exchange(a)
        self.lock_requirements()
        a.end()
        b = self.session(session_id="sess-b")
        self.bind(b)
        b.exchange("Second session note.", "Noted.")
        code, records, err = self.lookup(recent=True)
        self.assertEqual(code, 0, err)
        self.assertEqual([r["session"] for r in records[0]["recent"]], [a.handle] * 4 + [b.handle])


class LookupToolTests(PacketCase):
    def test_lookup_lists_applicable_identities_one_line_each_without_text(self) -> None:
        session = self.session()
        self.bind(session)
        reply = self.planning_exchange(session)
        self.lock_requirements()
        correction = session.exchange("Also skip drafts older than a year.", "Noted.")
        self.write_decision("DEC-001", f"Operator request evidence for: project:project: {correction}")
        self.write_decision("DEC-002", "Operator request quote for: project:project", "Some quoted words.")

        code, records, err = self.lookup("planning:PLAN-001")
        self.assertEqual(code, 0, err)
        record = records[0]
        self.assertEqual(record["state"], "resolved")
        self.assertEqual([e["id"] for e in record["evidence"]], [reply, correction])
        self.assertEqual(record["evidence"][0]["context"], f"{session.handle}/proposal-7")
        self.assertEqual(record["lines"], [
            f"{reply} | confirmation | project:project | session {session.handle} ordinal 8 answering {session.handle}/proposal-7",
            # No Stop was recorded between the reply and the correction, so
            # the correction is unpaired and carries no proposal.
            f"{correction} | correction | project:project | session {session.handle} ordinal 9",
        ])
        self.assertEqual(record["candidates"]["omitted"], 3)
        self.assertEqual([(u["reference"], u["reason"]) for u in record["unconfirmed"]], [("DEC-002-QUOTE-001", "legacy-quotation")])
        self.assertIsNone(record["missing"])
        self.assertEqual(record["context_identity"], request_trace.context_for_checkpoint(self.root, "PLAN-001").context_identity)
        dumped = json.dumps(record)
        for text in (SUMMARY, "skip drafts", "Some quoted words", "Solo writers"):
            self.assertNotIn(text, dumped)

        code, records, err = self.lookup("project:project")
        self.assertEqual(code, 0, err)
        self.assertEqual([e["id"] for e in records[0]["evidence"]], [reply, correction])
        self.assertIsNone(records[0]["context_identity"])

    def test_lookup_returns_one_missing_item_with_the_operator_remedy(self) -> None:
        session = self.session()
        self.bind(session)
        session.say("Sync notes to Markdown.")
        code, records, err = self.lookup("planning:PLAN-001")
        self.assertEqual(code, 1)
        self.assertIn("unit-request-not-captured", err)
        record = records[0]
        self.assertEqual(record["state"], "missing")
        self.assertEqual(record["evidence"], [])
        self.assertEqual(record["missing"]["rule"], "unit-request-not-captured")
        self.assertIn("Never create, copy, or edit records", record["missing"]["remedy"])
        self.assertEqual(record["candidates"]["candidates"], 1)
        self.assertNotIn("Sync notes", json.dumps(record))

    def test_lookup_for_a_task_uses_the_task_trace(self) -> None:
        session = self.session()
        self.bind(session)
        reply = self.planning_exchange(session)
        self.lock_requirements()
        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.approve_checkpoint("PLAN-001", "BUILD-01-001", [reply], context.context_identity)
        self.seed_planned_task()
        code, records, err = self.lookup("task:TASK-01-001")
        self.assertEqual(code, 0, err)
        self.assertEqual([e["id"] for e in records[0]["evidence"]], [reply])
        code, records, err = self.lookup("task:TASK-01-002")
        self.assertEqual(code, 1)
        self.assertIn("task-not-found", err)
        self.assertEqual(records[0]["missing"]["rule"], "task-not-found")

    def test_lookup_rejects_a_malformed_unit(self) -> None:
        code, _, err = self.lookup("decision:DEC-001")
        self.assertEqual(code, 2)
        self.assertIn("--unit", err)
