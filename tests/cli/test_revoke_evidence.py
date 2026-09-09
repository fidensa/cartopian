"""Tests for `cartopian revoke-evidence`, the request-store audit, and the
request-store provenance inventory (plan section 5 item 7).

Fixtures drive the real adapter, binding, and writers as in
``tests/cli/test_evidence_resolver.py``; the command, the audit check, and
the inventory are exercised on the resulting project.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
from unittest import mock

from cli import evidence_resolver, provenance, request_trace
from cli.commands import plan_audit, revoke_evidence
from cli.main import OPERATOR_ONLY_SUBCOMMANDS
from mcp_server import server
from tests.cli.test_evidence_resolver import ResolverCase


class RevokeCase(ResolverCase):
    def revoke_cmd(self, *ids: str, review_context: list[str] = (), reason: str = "test") -> tuple[int, list[dict], str]:
        out, err = io.StringIO(), io.StringIO()
        args = argparse.Namespace(
            project_root=str(self.root), evidence=list(ids),
            review_context=list(review_context), reason=reason,
        )
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = revoke_evidence.handler(args)
        records = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
        return code, records, err.getvalue()

    def revocations(self) -> list[dict]:
        return evidence_resolver.read_revocations(self.root)

    def write_chat(self, record_id: str, text: str) -> None:
        base = self.root / "requests" / "chat"
        base.mkdir(parents=True, exist_ok=True)
        body = {
            "schema": "cartopian-host-chat-v1", "record_id": record_id, "role": "operator",
            "kind": "original", "sequence": 1, "unit": {"kind": "project", "id": "project"},
            "text": text, "content_identity": "sha256:" + hashlib.sha256(text.encode()).hexdigest(),
            "observed_at": "2026-09-08T00:00:00Z",
            "source": {"host": "made-up", "conversation_id": "c1", "message_id": "m1"},
        }
        (base / f"{record_id}.json").write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")

    def confirmed_project(self):
        session = self.session()
        self.bind(session)
        reply = self.planning_exchange(session)
        self.lock_requirements()
        context = request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.approve_checkpoint("PLAN-001", "BUILD-01-001", [reply], context.context_identity)
        return session, reply, context


class RevokeCommandTests(RevokeCase):
    def test_revocation_is_durable_first_and_blocks_regenerated_prompts(self) -> None:
        session, reply, context = self.confirmed_project()
        code, records, err = self.revoke_cmd(reply, reason="operator withdrew the confirmation")
        self.assertEqual(code, 0, err)
        record = records[0]
        self.assertEqual(record["outcome"], "recorded")
        self.assertEqual(record["evidence"], [reply])
        # The bound review's context identity was collected, never a filename.
        self.assertEqual(record["review_contexts"], [context.context_identity])
        self.assertEqual(record["no_project_record"], [reply])
        self.assertEqual(record["quarantined"], [])
        entry = self.revocations()[0]
        self.assertEqual(entry["revocation_id"], "REVOKE-001")
        self.assertEqual(entry["reason"], "operator withdrew the confirmation")

        with self.assertRaises(request_trace.RequestRefusal) as caught:
            request_trace.context_for_checkpoint(self.root, "PLAN-001")
        self.assertEqual(caught.exception.rule, "unit-request-not-captured")
        task = self.seed_planned_task()
        with self.assertRaises(request_trace.RequestRefusal) as caught:
            request_trace.context_for_task_assignment(self.root, task)
        self.assertEqual(caught.exception.rule, "revoked-evidence")

        # Supersession: a fresh scope statement binds with `supersedes`.
        fresh = session.exchange("Fresh scope: sync notes to Markdown, no cloud storage, no mobile app.", "Recorded.")
        self.assertEqual(self.lock_requirements()[0], 0)
        self.assertEqual(self.bindings()[0]["confirmation"]["supersedes"], reply)
        self.assertEqual(request_trace.context_for_checkpoint(self.root, "PLAN-001").evidence_ids, [fresh])

    def test_chat_records_are_quarantined_byte_for_byte_and_idempotently(self) -> None:
        self.write_chat("CHAT-BSIDES-001", "I want it to sync to S3.")
        original = (self.root / "requests/chat/CHAT-BSIDES-001.json").read_bytes()
        code, records, err = self.revoke_cmd("CHAT-BSIDES-001")
        self.assertEqual(code, 0, err)
        self.assertEqual(records[0]["quarantined"], ["requests/quarantine/chat/CHAT-BSIDES-001.json"])
        moved = self.root / "requests/quarantine/chat/CHAT-BSIDES-001.json"
        self.assertEqual(moved.read_bytes(), original)
        self.assertFalse((self.root / "requests/chat/CHAT-BSIDES-001.json").exists())

        code, records, err = self.revoke_cmd("CHAT-BSIDES-001")
        self.assertEqual(code, 0, err)
        self.assertEqual(records[0]["outcome"], "retried")
        self.assertEqual(records[0]["already_quarantined"], ["requests/quarantine/chat/CHAT-BSIDES-001.json"])
        self.assertEqual(len(self.revocations()), 1)

    def test_crash_between_record_and_move_is_retry_safe(self) -> None:
        self.write_chat("CHAT-BSIDES-002", "Add a mobile app too.")
        # Step 1 landed, step 2 did not.
        evidence_resolver.write_revocations(self.root, [{
            "revocation_id": "REVOKE-001", "recorded_at": "2026-09-08T00:00:00+00:00",
            "evidence": ["CHAT-BSIDES-002"], "review_contexts": [], "reason": "crashed",
        }])
        code, records, err = self.revoke_cmd("CHAT-BSIDES-002")
        self.assertEqual(code, 0, err)
        self.assertEqual(records[0]["outcome"], "retried")
        self.assertEqual(records[0]["revocation_id"], "REVOKE-001")
        self.assertEqual(records[0]["quarantined"], ["requests/quarantine/chat/CHAT-BSIDES-002.json"])
        self.assertEqual(len(self.revocations()), 1)
        self.assertEqual(self.revocations()[0]["reason"], "crashed")

    def test_conflicting_quarantine_copy_is_refused_after_the_ledger_is_written(self) -> None:
        self.write_chat("CHAT-BSIDES-003", "one")
        dest = self.root / "requests/quarantine/chat/CHAT-BSIDES-003.json"
        dest.parent.mkdir(parents=True)
        dest.write_text("different bytes", encoding="utf-8")
        code, records, err = self.revoke_cmd("CHAT-BSIDES-003")
        self.assertEqual(code, 1)
        self.assertIn("quarantine-conflict", err)
        self.assertEqual(len(self.revocations()), 1)
        self.assertTrue((self.root / "requests/chat/CHAT-BSIDES-003.json").exists())

    def test_operator_only_boundary(self) -> None:
        self.assertIn("revoke-evidence", OPERATOR_ONLY_SUBCOMMANDS)
        server._TOOL_CACHE = None
        self.assertNotIn("revoke_evidence", {t["name"] for t in server.list_tools()})
        for marker in ("CARTOPIAN_ROLE", "CARTOPIAN_MCP_TOOL_CALL"):
            with mock.patch.dict(os.environ, {marker: "x"}):
                code, records, err = self.revoke_cmd("CHAT-BSIDES-001")
            self.assertEqual(code, 1)
            self.assertIn("non-operator-revocation", err)
            self.assertEqual(records, [])

    def test_malformed_identity_is_a_usage_error(self) -> None:
        code, _, err = self.revoke_cmd("DEC-001")
        self.assertEqual(code, 2)
        self.assertIn("--evidence", err)


class RequestStoreAuditTests(RevokeCase):
    def test_clean_project_has_no_store_findings(self) -> None:
        self.confirmed_project()
        blockers, warnings = plan_audit._check_request_store(self.root, "notes")
        self.assertEqual(blockers, [])
        self.assertEqual(warnings, [])

    def test_unreceipted_binding_and_foreign_binding_block(self) -> None:
        self.confirmed_project()
        bindings = self.bindings()
        forged = dict(bindings[0])
        forged.update({"binding_id": "cs-0123456789abcdef/binding-1", "handle": "cs-0123456789abcdef", "session_id": "sess-x", "confirmation": None})
        foreign = dict(bindings[0])
        foreign.update({"binding_id": bindings[0]["handle"] + "/binding-99", "project_id": "other", "project_path": "/elsewhere"})
        evidence_resolver.write_bindings(self.root, [*bindings, forged, foreign])
        blockers, _ = plan_audit._check_request_store(self.root, "notes")
        kinds = sorted((b["kind"], b.get("binding_id")) for b in blockers)
        self.assertIn(("request-binding-unreceipted", "cs-0123456789abcdef/binding-1"), kinds)
        self.assertIn(("request-binding-unreceipted", foreign["binding_id"]), kinds)
        self.assertIn(("request-binding-project-mismatch", foreign["binding_id"]), kinds)

    def test_unconfirmed_references_block_and_legacy_records_warn(self) -> None:
        session, reply, _ = self.confirmed_project()
        self.write_decision("DEC-001", f"Operator request evidence for: project:project: {session.handle}/turn-77")
        self.write_decision("DEC-002", "Operator request quote for: project:project", "quoted words")
        self.write_chat("CHAT-BSIDES-001", "hand-written")
        blockers, warnings = plan_audit._check_request_store(self.root, "notes")
        self.assertEqual([(b["kind"], b["reference"], b["failure_class"]) for b in blockers],
                         [("unconfirmed-evidence-reference", f"{session.handle}/turn-77", "not-captured")])
        self.assertEqual(sorted((w["kind"], w["reference"]) for w in warnings),
                         [("unconfirmed-evidence-record", "CHAT-BSIDES-001"), ("unconfirmed-evidence-record", "DEC-002-QUOTE-001")])

    def test_review_bound_to_hand_written_or_revoked_evidence_blocks(self) -> None:
        session, reply, context = self.confirmed_project()
        self.write_chat("CHAT-BSIDES-001", "hand-written")
        self.approve_checkpoint("PLAN-002", "BUILD-01-002", ["CHAT-BSIDES-001"])
        blockers, _ = plan_audit._check_request_store(self.root, "notes")
        self.assertEqual([(b["kind"], b["reference"]) for b in blockers], [("unconfirmed-evidence-bound", "CHAT-BSIDES-001")])

        self.revoke_cmd(reply)
        blockers, _ = plan_audit._check_request_store(self.root, "notes")
        kinds = [(b["kind"], b["reference"]) for b in blockers]
        self.assertIn(("revoked-evidence-in-use", reply), kinds)
        self.assertIn(("revoked-evidence-in-use", context.context_identity), kinds)

    def test_inconsistent_pair_and_referenced_bare_assent_block(self) -> None:
        session = self.session()
        self.bind(session)
        session.exchange("Sync notes to Markdown.", "Who is it for?")
        session.say("Solo writers.", turn_id="summary")
        session.stop("Summary A", turn_id="summary")
        reply = session.say("yes")
        self.lock_requirements()
        session.stop("Summary B", turn_id="summary")  # late, differing
        blockers, _ = plan_audit._check_request_store(self.root, "notes")
        self.assertEqual(blockers[0]["kind"], "request-store-integrity")
        self.assertEqual(blockers[0]["failure_class"], "inconsistent-pair")

    def test_referenced_unpaired_assent_blocks(self) -> None:
        session, reply, _ = self.confirmed_project()
        session.say("Anything else?")  # no stop follows
        bare = session.say("yes")
        self.write_decision("DEC-001", f"Operator request evidence for: project:project: {bare}")
        blockers, _ = plan_audit._check_request_store(self.root, "notes")
        self.assertEqual([(b["kind"], b["failure_class"]) for b in blockers], [("unconfirmed-evidence-reference", "unpaired-assent")])

    def test_task_reference_cross_unit_blocks(self) -> None:
        session, reply, _ = self.confirmed_project()
        turn = session.exchange("Skip drafts.", "Noted.")
        self.write_decision("DEC-001", f"Operator request evidence for: project:project: {turn}")
        task = self.seed_planned_task()
        task.write_text(task.read_text() + f"\n## Request evidence\n\n{turn}\n", encoding="utf-8")
        blockers, _ = plan_audit._check_request_store(self.root, "notes")
        self.assertEqual({b["failure_class"] for b in blockers}, {"cross-unit"})

    def test_plan_audit_record_inventories_the_request_store(self) -> None:
        session, reply, _ = self.confirmed_project()
        self.write_chat("CHAT-BSIDES-001", "hand-written")
        self.revoke_cmd("CHAT-BSIDES-001")
        inventory = provenance.request_store_inventory(self.root)
        statuses = {e["relpath"]: e["status"] for e in inventory["entries"]}
        self.assertEqual(statuses, {
            "requests/bindings.json": "bindings",
            "requests/quarantine/chat/CHAT-BSIDES-001.json": "quarantined",
            "requests/revocations.json": "revocations",
        })
        self.assertTrue(all(e["hash"].startswith("sha256:") for e in inventory["entries"]))
        self.assertIsNone(provenance.request_store_inventory(self.root / "phases"))

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            plan_audit.handler(argparse.Namespace(project_path=str(self.root)))
        record = json.loads(out.getvalue().splitlines()[0])
        self.assertEqual(record["provenance"]["request_store"]["counts"], {"bindings": 1, "quarantined": 1, "revocations": 1})
