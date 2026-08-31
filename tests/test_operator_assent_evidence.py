"""A low-information assent is authority only with its complete antecedent.

"yes", "continue", "proceed" — these state no intent of their own. The contract
proven here is that such a response becomes operator evidence only when it is
retained and evaluated together with the complete immediately preceding
question or proposal and that proposal's exact scope; that it authorizes
nothing absent from that proposal; and that a detached, missing, or ambiguous
antecedent fails closed and asks for a new, self-contained instruction.

The binding is integrity-bound as one value. Its identity covers the proposal
text, the exact scope, the provenance of *both* messages, and their two
positions together, so none of them can be rewritten after capture; and the
exact scope must be quoted from the proposal, because a scope in words the
proposal never used is exactly the absent detail an assent may not authorize.

"Immediately preceding" is a claim about a pair, so both sides must prove it:
the same host and conversation, two distinct message identities, and positions
exactly one apart. A proposal that only describes itself cannot show it
preceded *this* response rather than some other conversation's, so a binding
that retains no response provenance — or one whose two sides disagree — fails
closed.

Both gates are exercised. Host intake refuses to *store* a detached assent, and
trace resolution refuses to *use* one — the second matters because request
records, host chat turns, and decision quotes are three separate channels and
only one of them goes through the capture command.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli import request_trace
from cli.commands import capture_request

CONFIG = '''[project]
name = "Assent"
id = "assent"
project_schema_version = "v0.12.0"

[reviews]
planning = "required"
planning_role = "reviewer"
task_closure = "required"
task_role = "reviewer"

[roles.pm]
description = "PM"
grants = ["pm-solo"]
'''

PROPOSAL = (
    "I can add the retry only to the release-check call, with a fixed three "
    "attempts and no change to the timeout. Do you want that?"
)
# The exact scope is quoted from the proposal, never summarized in words the
# operator was never shown.
SCOPE = (
    "the retry only to the release-check call, with a fixed three attempts "
    "and no change to the timeout"
)
BROADER_SCOPE = "all files and all timeout behavior"

HOST = "claude-code"
CONVERSATION = "conversation-7"
PROPOSAL_MESSAGE = "message-41"
RESPONSE_MESSAGE = "message-42"


def _identity(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _antecedent(
    *,
    text: str = PROPOSAL,
    scope: str = SCOPE,
    host: str = HOST,
    conversation: str = CONVERSATION,
    message: str = PROPOSAL_MESSAGE,
    order: int = 41,
    response_host: str = HOST,
    response_conversation: str = CONVERSATION,
    response_message: str = RESPONSE_MESSAGE,
    response_order: int = 42,
) -> dict:
    """A well-formed stored binding; each test perturbs exactly one field."""
    source = request_trace.AntecedentSource(host, conversation, message)
    response = request_trace.AntecedentSource(
        response_host, response_conversation, response_message
    )
    return {
        "content_identity": request_trace.antecedent_identity(
            text=text,
            scope=scope,
            source=source,
            order=order,
            response=response,
            response_order=response_order,
        ),
        "order": order,
        "response": response.as_record(),
        "response_order": response_order,
        "scope": scope,
        "source": source.as_record(),
        "text": text,
    }


class AssentEvidenceContract(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "cartopian.toml").write_text(CONFIG, encoding="utf-8")
        for directory in ("tasks/in-review", "decisions", "prompts", "reviews", "reports"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        self.task = self.root / "tasks/in-review/TASK-02-010.md"
        self.task.write_text(
            "# TASK-02-010: Retry\n\nPhase: PHASE-02\nPlan ref: BUILD-02-010\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # -- host intake -----------------------------------------------------

    def capture(
        self,
        text,
        *,
        antecedent=None,
        scope=None,
        request_id="REQUEST-001",
        host=HOST,
        conversation=CONVERSATION,
        message=PROPOSAL_MESSAGE,
        order=41,
        response_host=HOST,
        response_conversation=CONVERSATION,
        response_message=RESPONSE_MESSAGE,
        response_order=42,
        provenance=True,
    ):
        source = self.root / f"{request_id}-message.txt"
        source.write_text(text, encoding="utf-8")
        antecedent_path = None
        if antecedent is not None:
            antecedent_path = self.root / f"{request_id}-antecedent.txt"
            antecedent_path.write_text(antecedent, encoding="utf-8")
        args = argparse.Namespace(
            project_root=str(self.root),
            request_id=request_id,
            unit="task:TASK-02-010",
            content_file=str(source),
            antecedent_file=None if antecedent_path is None else str(antecedent_path),
            antecedent_scope=scope,
            antecedent_host=host if provenance else None,
            antecedent_conversation=conversation if provenance else None,
            antecedent_message=message if provenance else None,
            antecedent_order=order if provenance else None,
            response_host=response_host if provenance else None,
            response_conversation=response_conversation if provenance else None,
            response_message=response_message if provenance else None,
            response_order=response_order if provenance else None,
            correction_of=None,
            captured_at="2026-07-27T12:00:00Z",
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in capture_request.NON_OPERATOR_MARKERS
        }
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.dict(os.environ, env, clear=True),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = capture_request.handler(args)
        return code, err.getvalue()

    def test_detached_assent_is_not_captured_as_evidence(self) -> None:
        for word in ("yes", "continue", "proceed", "Yes.", "  ok  "):
            with self.subTest(word=word):
                code, err = self.capture(word, provenance=False)
                self.assertEqual(code, 1)
                self.assertIn("detached-assent", err)
                self.assertIn("self-contained operator instruction", err)
                self.assertFalse((self.root / "requests").exists())

    def test_assent_bound_to_its_proposal_is_captured(self) -> None:
        code, err = self.capture("yes", antecedent=PROPOSAL, scope=SCOPE)
        self.assertEqual(code, 0, err)
        stored = json.loads(
            (self.root / "requests/REQUEST-001.json").read_text(encoding="utf-8")
        )
        self.assertEqual(stored["text"], "yes")
        self.assertEqual(stored["antecedent"], _antecedent())
        self.assertEqual(
            stored["antecedent"]["response"],
            {
                "host": HOST,
                "conversation_id": CONVERSATION,
                "message_id": RESPONSE_MESSAGE,
            },
        )
        # The identity is over the whole binding, not the proposal text alone.
        self.assertNotEqual(
            stored["antecedent"]["content_identity"], _identity(PROPOSAL)
        )

    def test_content_bearing_request_needs_no_antecedent(self) -> None:
        code, err = self.capture(
            "Add the retry to the release check only.", provenance=False
        )
        self.assertEqual(code, 0, err)

    def test_content_free_antecedent_is_ambiguous(self) -> None:
        code, err = self.capture("yes", antecedent="ok", scope="ok")
        self.assertEqual(code, 1)
        self.assertIn("ambiguous-antecedent", err)

    def test_intake_refuses_a_scope_the_proposal_does_not_state(self) -> None:
        code, err = self.capture("yes", antecedent=PROPOSAL, scope=BROADER_SCOPE)
        self.assertEqual(code, 1)
        self.assertIn("scope-exceeds-antecedent", err)
        self.assertIn("verbatim span", err)
        self.assertFalse((self.root / "requests").exists())

    def test_intake_refuses_a_non_adjacent_antecedent(self) -> None:
        code, err = self.capture(
            "yes", antecedent=PROPOSAL, scope=SCOPE, order=41, response_order=45
        )
        self.assertEqual(code, 1)
        self.assertIn("non-adjacent-antecedent", err)
        self.assertFalse((self.root / "requests").exists())

    def test_antecedent_flags_are_given_together(self) -> None:
        code, err = self.capture("yes", antecedent=PROPOSAL, provenance=False)
        self.assertEqual(code, 2)
        self.assertIn("given together", err)
        self.assertIn("--antecedent-scope", err)
        self.assertIn("--antecedent-host", err)
        self.assertIn("--response-host", err)
        self.assertIn("--response-conversation", err)
        self.assertIn("--response-message", err)
        self.assertIn("--response-order", err)

    def test_intake_refuses_an_empty_response_provenance_flag(self) -> None:
        code, err = self.capture(
            "yes", antecedent=PROPOSAL, scope=SCOPE, response_message="   "
        )
        self.assertEqual(code, 2)
        self.assertIn("--response-message must be non-empty", err)

    def test_intake_refuses_a_cross_conversation_response(self) -> None:
        """The proposal is quoted faithfully but from somewhere else."""
        code, err = self.capture(
            "yes",
            antecedent=PROPOSAL,
            scope=SCOPE,
            response_conversation="conversation-9",
        )
        self.assertEqual(code, 1)
        self.assertIn("non-adjacent-antecedent", err)
        self.assertFalse((self.root / "requests").exists())

    def test_intake_refuses_a_cross_host_response(self) -> None:
        code, err = self.capture(
            "yes", antecedent=PROPOSAL, scope=SCOPE, response_host="other-host"
        )
        self.assertEqual(code, 1)
        self.assertIn("non-adjacent-antecedent", err)
        self.assertFalse((self.root / "requests").exists())

    def test_intake_refuses_a_self_referential_response(self) -> None:
        """A message does not immediately precede itself."""
        code, err = self.capture(
            "yes", antecedent=PROPOSAL, scope=SCOPE, response_message=PROPOSAL_MESSAGE
        )
        self.assertEqual(code, 1)
        self.assertIn("non-adjacent-antecedent", err)
        self.assertFalse((self.root / "requests").exists())

    def test_antecedent_without_provenance_is_refused_at_intake(self) -> None:
        code, err = self.capture(
            "yes", antecedent=PROPOSAL, scope=SCOPE, host="   "
        )
        self.assertEqual(code, 2)
        self.assertIn("--antecedent-host must be non-empty", err)

    # -- trace resolution ------------------------------------------------

    def write_record(self, text, *, antecedent=None):
        base = self.root / "requests"
        base.mkdir(exist_ok=True)
        record = {
            "captured_at": "2026-07-27T12:00:00Z",
            "content_identity": _identity(text),
            "kind": "original",
            "record_id": "REQUEST-001",
            "request_id": "REQUEST-001",
            "schema": "cartopian-original-request-v1",
            "sequence": 0,
            "text": text,
            "unit": {"kind": "task", "id": "TASK-02-010"},
        }
        if antecedent is not None:
            record["antecedent"] = antecedent
        (base / "REQUEST-001.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def write_chat_turn(self, text, *, antecedent=None, message=RESPONSE_MESSAGE):
        base = self.root / "requests" / "chat"
        base.mkdir(parents=True, exist_ok=True)
        record = {
            "content_identity": _identity(text),
            "kind": "original",
            "observed_at": "2026-07-27T12:00:00Z",
            "record_id": "CHAT-001",
            "role": "operator",
            "schema": "cartopian-host-chat-v1",
            "sequence": 0,
            "source": {
                "host": HOST,
                "conversation_id": CONVERSATION,
                "message_id": message,
            },
            "text": text,
            "unit": {"kind": "task", "id": "TASK-02-010"},
        }
        if antecedent is not None:
            record["antecedent"] = antecedent
        (base / "CHAT-001.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def resolve(self):
        return request_trace.context_for_task(self.root, self.task)

    def assertRefuses(self, rule):
        with self.assertRaises(request_trace.RequestRefusal) as caught:
            self.resolve()
        self.assertEqual(caught.exception.rule, rule)
        return caught.exception

    def test_stored_detached_assent_is_refused_at_use(self) -> None:
        """A record the capture gate never saw still cannot become authority."""
        self.write_record("yes")
        refusal = self.assertRefuses("detached-assent")
        self.assertIn("self-contained operator instruction", refusal.recovery)

    def test_antecedent_without_scope_is_refused(self) -> None:
        self.write_record("yes", antecedent=_antecedent(scope="   "))
        self.assertRefuses("ambiguous-antecedent")

    def test_tampered_antecedent_text_is_refused(self) -> None:
        antecedent = _antecedent()
        antecedent["text"] = PROPOSAL + " Also drop the timeout entirely."
        self.write_record("yes", antecedent=antecedent)
        self.assertRefuses("changed-antecedent")

    def test_broadened_scope_under_an_unchanged_identity_is_refused(self) -> None:
        """The direct probe the previous binding failed: rewriting only the
        stored scope left the identity intact and the broader scope was
        rendered as authority."""
        antecedent = _antecedent()
        antecedent["scope"] = BROADER_SCOPE
        self.write_record("yes", antecedent=antecedent)
        self.assertRefuses("changed-antecedent")

    def test_scope_exceeding_the_proposal_is_refused_even_when_rebound(self) -> None:
        """Integrity alone is not the guard: a consistently re-signed binding
        whose scope states what the proposal never did still fails closed."""
        self.write_record("yes", antecedent=_antecedent(scope=BROADER_SCOPE))
        refusal = self.assertRefuses("scope-exceeds-antecedent")
        self.assertIn("verbatim span", refusal.recovery)

    def test_reordered_scope_terms_do_not_pass_as_a_quotation(self) -> None:
        self.write_record(
            "yes", antecedent=_antecedent(scope="the timeout and the retry")
        )
        self.assertRefuses("scope-exceeds-antecedent")

    def test_missing_source_provenance_is_refused(self) -> None:
        antecedent = _antecedent()
        del antecedent["source"]
        self.write_record("yes", antecedent=antecedent)
        self.assertRefuses("missing-antecedent-provenance")

    def test_incomplete_source_provenance_is_refused(self) -> None:
        antecedent = _antecedent()
        antecedent["source"]["message_id"] = "   "
        self.write_record("yes", antecedent=antecedent)
        self.assertRefuses("missing-antecedent-provenance")

    def test_missing_order_provenance_is_refused(self) -> None:
        for field in ("order", "response_order"):
            with self.subTest(field=field):
                antecedent = _antecedent()
                del antecedent[field]
                self.write_record("yes", antecedent=antecedent)
                self.assertRefuses("missing-antecedent-provenance")

    def test_non_integer_order_provenance_is_refused(self) -> None:
        antecedent = _antecedent()
        antecedent["order"] = "41"
        self.write_record("yes", antecedent=antecedent)
        self.assertRefuses("missing-antecedent-provenance")

    def test_missing_response_provenance_is_refused(self) -> None:
        """One side proves nothing: with nothing recorded about where the
        response came from, no stored proposal can be shown to have preceded
        it."""
        antecedent = _antecedent()
        del antecedent["response"]
        self.write_record("yes", antecedent=antecedent)
        refusal = self.assertRefuses("missing-antecedent-provenance")
        self.assertIn("response", refusal.detail)

    def test_incomplete_response_provenance_is_refused(self) -> None:
        antecedent = _antecedent()
        antecedent["response"]["conversation_id"] = "   "
        self.write_record("yes", antecedent=antecedent)
        self.assertRefuses("missing-antecedent-provenance")

    def test_cross_host_response_provenance_is_refused(self) -> None:
        self.write_record("yes", antecedent=_antecedent(response_host="other-host"))
        self.assertRefuses("non-adjacent-antecedent")

    def test_cross_conversation_response_provenance_is_refused(self) -> None:
        self.write_record(
            "yes", antecedent=_antecedent(response_conversation="conversation-9")
        )
        self.assertRefuses("non-adjacent-antecedent")

    def test_self_referential_message_identity_is_refused(self) -> None:
        """Proposal and response cannot be the same message."""
        self.write_record(
            "yes", antecedent=_antecedent(response_message=PROPOSAL_MESSAGE)
        )
        self.assertRefuses("non-adjacent-antecedent")

    def test_changed_response_provenance_under_an_unchanged_identity_is_refused(
        self,
    ) -> None:
        """Rewriting only the response side after capture must not pass: the
        identity covers both sides, so an unchanged hash over changed
        provenance is a tampered binding, not a valid one."""
        antecedent = _antecedent()
        antecedent["response"] = {
            "host": HOST,
            "conversation_id": CONVERSATION,
            "message_id": "message-99",
        }
        self.write_record("yes", antecedent=antecedent)
        self.assertRefuses("changed-antecedent")

    def test_non_adjacent_positions_are_refused(self) -> None:
        """A proposal three messages back is not the immediately preceding
        one, however faithfully it is quoted."""
        self.write_record(
            "yes", antecedent=_antecedent(order=41, response_order=44)
        )
        self.assertRefuses("non-adjacent-antecedent")

    def test_chat_antecedent_from_another_conversation_is_refused(self) -> None:
        self.write_chat_turn(
            "yes", antecedent=_antecedent(conversation="conversation-9")
        )
        with self.assertRaises(request_trace.RequestRefusal) as caught:
            request_trace.load_host_chat_records(self.root)
        self.assertEqual(caught.exception.rule, "non-adjacent-antecedent")

    def test_chat_antecedent_naming_its_own_message_is_refused(self) -> None:
        self.write_chat_turn(
            "yes", antecedent=_antecedent(message=RESPONSE_MESSAGE)
        )
        with self.assertRaises(request_trace.RequestRefusal) as caught:
            request_trace.load_host_chat_records(self.root)
        self.assertEqual(caught.exception.rule, "non-adjacent-antecedent")

    def test_chat_binding_claiming_another_response_message_is_refused(self) -> None:
        """The channel records where the response came from; a binding that
        names a different message contradicts it and fails closed."""
        self.write_chat_turn(
            "yes", antecedent=_antecedent(response_message="message-77")
        )
        with self.assertRaises(request_trace.RequestRefusal) as caught:
            request_trace.load_host_chat_records(self.root)
        self.assertEqual(caught.exception.rule, "non-adjacent-antecedent")

    def test_chat_antecedent_in_the_same_conversation_loads(self) -> None:
        self.write_chat_turn("yes", antecedent=_antecedent())
        records = request_trace.load_host_chat_records(self.root)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].antecedent.scope, SCOPE)
        self.assertEqual(records[0].antecedent.order, 41)

    def test_decision_quoting_a_bare_assent_is_detached(self) -> None:
        """A quoted excerpt carries no antecedent, so it cannot be bound."""
        (self.root / "decisions/DEC-001.md").write_text(
            "# DEC-001: Retry\n\nDate: 2026-07-27\nStatus: locked\n"
            "Supersedes: none\n\n## Context\n\n"
            "Operator request quote for: task:TASK-02-010\n\n> yes\n",
            encoding="utf-8",
        )
        self.task.write_text(
            self.task.read_text(encoding="utf-8") + "\nSee DEC-001.\n",
            encoding="utf-8",
        )
        self.assertRefuses("detached-assent")

    # -- rendering -------------------------------------------------------

    def test_bound_assent_renders_with_its_proposal_scope_and_provenance(self) -> None:
        self.write_record("yes", antecedent=_antecedent())
        context = self.resolve()
        section = context.section

        self.assertIn(PROPOSAL, section)
        self.assertIn(f"Antecedent scope: {SCOPE}", section)
        self.assertIn(
            f"Antecedent provenance: {HOST}:{CONVERSATION}:{PROPOSAL_MESSAGE} "
            "(message 41, answered at message 42)",
            section,
        )
        self.assertIn(
            f"Response provenance: {HOST}:{CONVERSATION}:{RESPONSE_MESSAGE} "
            "(message 42)",
            section,
        )
        self.assertIn(
            f"Antecedent content identity: {_antecedent()['content_identity']}",
            section,
        )
        self.assertIn("\nyes\n", section)
        # The bound is stated where the reader is, not only in the protocol.
        self.assertIn("authorizes nothing the proposal does not contain", section)
        # And the proposal precedes the assent, so it cannot be read alone.
        self.assertLess(section.index(PROPOSAL), section.rindex("\nyes\n"))

    def test_assignment_channel_renders_the_same_binding(self) -> None:
        """The coder channel no longer drops the assent: without its proposal
        the words carry no scope, so both travel together everywhere."""
        self.write_record("yes", antecedent=_antecedent())
        section = request_trace.context_for_task_assignment(self.root, self.task).section
        self.assertIn(PROPOSAL, section)
        self.assertIn(f"Antecedent scope: {SCOPE}", section)
        self.assertIn("low-information assent", section)

    def test_unreadable_assent_grammar_fails_closed(self) -> None:
        """The grammar decides which records need an antecedent at all, so an
        unreadable contract must not silently readmit a detached assent."""
        with mock.patch.object(Path, "read_text", side_effect=OSError("missing")):
            with self.assertRaises(request_trace.RequestRefusal) as caught:
                request_trace._low_information_responses()
        self.assertEqual(caught.exception.rule, "unreadable-assent-grammar")

    def test_empty_assent_grammar_fails_closed(self) -> None:
        with mock.patch.object(
            Path, "read_text", return_value='{"low_information_responses": []}'
        ):
            with self.assertRaises(request_trace.RequestRefusal) as caught:
                request_trace._low_information_responses()
        self.assertEqual(caught.exception.rule, "unreadable-assent-grammar")

    def test_ordinary_request_renders_unchanged(self) -> None:
        text = "Add the retry to the release check only."
        self.write_record(text)
        section = self.resolve().section
        self.assertIn(text, section)
        self.assertNotIn("Antecedent scope:", section)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
