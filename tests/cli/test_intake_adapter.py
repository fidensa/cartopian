"""Tests for the host intake adapter (``cli/intake_adapter.py``).

The adapter is the only producer of adapter receipts. These tests drive it
exactly as a host does — JSON payload on stdin, ``--host`` on argv — and
inspect the intake root it writes.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from cli import intake_adapter
from cli.intake_adapter import (
    PRESELECTION_MAX_BYTES,
    PRESELECTION_MAX_EVENTS,
    ROUTING_LINE_PREFIX,
    SessionStore,
    handle_event,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTER = REPO_ROOT / "cli" / "intake_adapter.py"


def _payload(event: str, host: str = "claude", session_id: str = "sess-1", **extra) -> dict:
    body = {
        "session_id": session_id,
        "hook_event_name": event,
        "cwd": "/tmp/work",
        "transcript_path": "/never/read.jsonl",
    }
    body.update(extra)
    return body


class AdapterCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "intake"
        self.store = SessionStore(self.root)

    def run_hook(self, host: str, payload: dict, env_extra: dict | None = None):
        env = {k: v for k, v in os.environ.items() if k != intake_adapter.ROLE_ENV}
        env[intake_adapter.INTAKE_ROOT_ENV] = str(self.root)
        env.update(env_extra or {})
        return subprocess.run(
            [sys.executable, str(ADAPTER), "--host", host],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
        )


class SubprocessContractTests(AdapterCase):
    def test_session_start_creates_record_and_handle(self) -> None:
        result = self.run_hook("claude", _payload("SessionStart", source="startup"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        record = self.store.load_session("claude", "sess-1")
        self.assertIsNotNone(record)
        self.assertTrue(record["handle"].startswith("cs-"))
        self.assertEqual(self.store.resolve_handle(record["handle"])["session_id"], "sess-1")

    def test_first_prompt_prints_routing_line_once(self) -> None:
        self.run_hook("claude", _payload("SessionStart"))
        first = self.run_hook(
            "claude", _payload("UserPromptSubmit", prompt="use cartopian", prompt_id="p-1")
        )
        record = self.store.load_session("claude", "sess-1")
        self.assertEqual(first.stdout.strip(), intake_adapter.routing_line(record["handle"]))
        self.assertTrue(first.stdout.startswith(ROUTING_LINE_PREFIX))
        second = self.run_hook(
            "claude", _payload("UserPromptSubmit", prompt="continue", prompt_id="p-2")
        )
        self.assertEqual(second.stdout, "")
        events = self.store.read_events("claude", "sess-1")
        self.assertEqual([e["ordinal"] for e in events], [1, 2, 3])
        self.assertEqual(events[1]["text"], "use cartopian")
        self.assertEqual(events[1]["turn_id"], "p-1")
        self.assertNotIn("transcript_path", json.dumps(events))

    def test_codex_turn_id_and_interrupt(self) -> None:
        self.run_hook("codex", _payload("SessionStart", host="codex"))
        self.run_hook("codex", _payload("UserPromptSubmit", prompt="plan it", turn_id="t-1"))
        self.run_hook("codex", _payload("Stop", last_assistant_message="Proposal A", turn_id="t-1"))
        self.run_hook("codex", _payload("Interrupt", turn_id="t-2"))
        events = self.store.read_events("codex", "sess-1")
        self.assertEqual([e["event"] for e in events], ["SessionStart", "UserPromptSubmit", "Stop", "Interrupt"])
        self.assertEqual(events[2]["text"], "Proposal A")
        self.assertEqual(events[3]["turn_id"], "t-2")

    def test_dispatched_session_records_nothing(self) -> None:
        result = self.run_hook(
            "claude",
            _payload("UserPromptSubmit", prompt="secret"),
            env_extra={intake_adapter.ROLE_ENV: "coder"},
        )
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertFalse(self.root.exists())

    def test_ignored_events_and_bad_payloads_never_fail_the_host(self) -> None:
        for payload in (_payload("SubagentStop"), _payload("PreToolUse"), {"nope": 1}):
            result = self.run_hook("claude", payload)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
        env = dict(os.environ, **{intake_adapter.INTAKE_ROOT_ENV: str(self.root)})
        garbage = subprocess.run(
            [sys.executable, str(ADAPTER), "--host", "claude"],
            input="not json", capture_output=True, text=True, env=env,
        )
        self.assertEqual(garbage.returncode, 0)
        self.assertIn("unreadable hook payload", garbage.stderr)
        self.assertFalse((self.root / "sessions").exists())

    def test_two_concurrent_sessions_get_distinct_handles(self) -> None:
        for sid in ("thread-a", "thread-b"):
            self.run_hook("codex", _payload("SessionStart", host="codex", session_id=sid))
        a = self.run_hook("codex", _payload("UserPromptSubmit", session_id="thread-a", prompt="A says", turn_id="ta"))
        b = self.run_hook("codex", _payload("UserPromptSubmit", session_id="thread-b", prompt="B says", turn_id="tb"))
        handle_a = a.stdout.split()[1]
        handle_b = b.stdout.split()[1]
        self.assertNotEqual(handle_a, handle_b)
        self.assertEqual(self.store.resolve_handle(handle_a)["session_id"], "thread-a")
        self.assertEqual(self.store.resolve_handle(handle_b)["session_id"], "thread-b")
        self.assertEqual(
            [e["text"] for e in self.store.read_events("codex", "thread-a") if e["event"] == "UserPromptSubmit"],
            ["A says"],
        )

    def test_lookup_reports_session_and_refuses_unknown_handle(self) -> None:
        self.run_hook("claude", _payload("SessionStart"))
        record = self.store.load_session("claude", "sess-1")
        env = dict(os.environ, **{intake_adapter.INTAKE_ROOT_ENV: str(self.root)})
        ok = subprocess.run(
            [sys.executable, str(ADAPTER), "--lookup", record["handle"]],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout)["session_id"], "sess-1")
        bad = subprocess.run(
            [sys.executable, str(ADAPTER), "--lookup", "cs-0000000000000000"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(bad.returncode, 1)
        self.assertIn("session-unbound", bad.stderr)


class InProcessTests(AdapterCase):
    def test_unbound_session_end_discards_buffer(self) -> None:
        handle_event(self.store, "claude", _payload("SessionStart"))
        handle_event(self.store, "claude", _payload("UserPromptSubmit", prompt="hi"))
        handle = self.store.load_session("claude", "sess-1")["handle"]
        handle_event(self.store, "claude", _payload("SessionEnd", reason="other"))
        self.assertIsNone(self.store.load_session("claude", "sess-1"))
        self.assertIsNone(self.store.resolve_handle(handle))

    def test_bound_session_end_is_kept(self) -> None:
        handle_event(self.store, "claude", _payload("SessionStart"))
        record = self.store.load_session("claude", "sess-1")
        record["bindings"] = [{"project_id": "p", "from_ordinal": 1}]
        self.store.save_session(record)
        handle_event(self.store, "claude", _payload("SessionEnd", reason="logout"))
        kept = self.store.load_session("claude", "sess-1")
        self.assertIsNotNone(kept)
        self.assertIsNotNone(kept["ended"])

    def test_session_created_lazily_when_hooks_join_mid_session(self) -> None:
        line, _ = handle_event(self.store, "claude", _payload("UserPromptSubmit", prompt="late start"))
        record = self.store.load_session("claude", "sess-1")
        self.assertEqual(record["created_by"], "UserPromptSubmit")
        self.assertEqual(line, intake_adapter.routing_line(record["handle"]))

    def test_event_count_bound_evicts_oldest_with_visible_marker(self) -> None:
        handle_event(self.store, "claude", _payload("SessionStart"))
        for i in range(PRESELECTION_MAX_EVENTS + 5):
            handle_event(self.store, "claude", _payload("UserPromptSubmit", prompt=f"m{i}"))
        events = self.store.read_events("claude", "sess-1")
        marker = events[0]
        self.assertEqual(marker["event"], "evicted")
        self.assertEqual(marker["count"], 6)  # SessionStart + m0..m4
        self.assertEqual(marker["first_ordinal"], 1)
        self.assertEqual(marker["last_ordinal"], 6)
        kept = events[1:]
        self.assertEqual(len(kept), PRESELECTION_MAX_EVENTS)
        self.assertEqual(kept[0]["ordinal"], 7)
        self.assertEqual(kept[0]["text"], "m5")
        self.assertEqual(kept[-1]["ordinal"], PRESELECTION_MAX_EVENTS + 6)

    def test_byte_bound_evicts_and_accumulates_marker(self) -> None:
        handle_event(self.store, "claude", _payload("SessionStart"))
        big = "x" * (PRESELECTION_MAX_BYTES // 3)
        for i in range(4):
            handle_event(self.store, "claude", _payload("UserPromptSubmit", prompt=big + str(i)))
        events = self.store.read_events("claude", "sess-1")
        self.assertEqual(events[0]["event"], "evicted")
        self.assertGreaterEqual(events[0]["count"], 2)
        total = sum(len(json.dumps(e).encode()) for e in events[1:])
        self.assertLessEqual(total, PRESELECTION_MAX_BYTES)
        self.assertEqual(sum(1 for e in events if e["event"] == "evicted"), 1)

    def test_bound_session_is_never_evicted(self) -> None:
        handle_event(self.store, "claude", _payload("SessionStart"))
        record = self.store.load_session("claude", "sess-1")
        record["bindings"] = [{"project_id": "p", "from_ordinal": 1}]
        self.store.save_session(record)
        for i in range(PRESELECTION_MAX_EVENTS + 5):
            handle_event(self.store, "claude", _payload("UserPromptSubmit", prompt=f"m{i}"))
        events = self.store.read_events("claude", "sess-1")
        self.assertEqual(len(events), PRESELECTION_MAX_EVENTS + 6)
        self.assertFalse(any(e["event"] == "evicted" for e in events))

    def test_stale_unbound_sessions_are_swept_at_session_start(self) -> None:
        handle_event(self.store, "codex", _payload("SessionStart", host="codex", session_id="old"))
        sdir = self.store.session_dir("codex", "old")
        stale = time.time() - intake_adapter.UNBOUND_SESSION_MAX_AGE_SECONDS - 60
        for p in sdir.iterdir():
            os.utime(p, (stale, stale))
        handle_event(self.store, "codex", _payload("SessionStart", host="codex", session_id="new"))
        self.assertIsNone(self.store.load_session("codex", "old"))
        self.assertIsNotNone(self.store.load_session("codex", "new"))

    def test_handle_resolution_ignores_forged_index(self) -> None:
        handle_event(self.store, "claude", _payload("SessionStart"))
        forged = self.store.handle_path("cs-ffffffffffffffff")
        forged.write_text(json.dumps({"handle": "cs-ffffffffffffffff", "host": "claude", "session_id": "sess-1"}))
        self.assertIsNone(self.store.resolve_handle("cs-ffffffffffffffff"))


class PairingTests(AdapterCase):
    """Section 4.5: freeze at UserPromptSubmit, late events never rewrite."""

    def _start(self, host: str = "claude") -> None:
        handle_event(self.store, host, _payload("SessionStart", host=host))

    def _submit(self, text: str, turn: str, host: str = "claude") -> None:
        key = "prompt_id" if host == "claude" else "turn_id"
        handle_event(self.store, host, _payload("UserPromptSubmit", host=host, prompt=text, **{key: turn}))

    def _stop(self, text: str, turn: str, host: str = "claude") -> None:
        key = "prompt_id" if host == "claude" else "turn_id"
        handle_event(self.store, host, _payload("Stop", host=host, last_assistant_message=text, **{key: turn}))

    def _pairs(self, host: str = "claude"):
        return self.store.pair_states(host, "sess-1")

    def test_planning_exchange_pairs_proposal_with_confirming_reply(self) -> None:
        self._start()
        self._submit("use cartopian", "t1")
        self._stop("Six-fact intent summary. Confirm?", "t1")
        self._submit("yes, except no cloud storage", "t2")
        pairs = self._pairs()
        self.assertEqual([p["state"] for p in pairs], ["unpaired", "paired"])
        self.assertEqual(pairs[0]["unpaired_reason"], "no-stop")
        confirm = pairs[1]
        self.assertEqual(confirm["proposal_turn_id"], "t1")
        self.assertEqual(confirm["reply_turn_id"], "t2")
        events = self.store.read_events("claude", "sess-1")
        self.assertEqual(events[confirm["proposal_ordinal"] - 1]["text"], "Six-fact intent summary. Confirm?")
        self.assertEqual(events[confirm["reply_ordinal"] - 1]["text"], "yes, except no cloud storage")
        self.assertFalse(confirm["inconsistent"])
        self.assertEqual(confirm["pair_id"], f"{self.store.load_session('claude', 'sess-1')['handle']}/pair-{confirm['reply_ordinal']}")

    def test_repeated_stop_before_submission_collapses_to_last(self) -> None:
        self._start()
        self._submit("go", "t1")
        self._stop("draft", "t1")
        self._stop("final proposal", "t1")
        self._submit("yes", "t2")
        pair = self._pairs()[-1]
        self.assertEqual(pair["state"], "paired")
        events = self.store.read_events("claude", "sess-1")
        self.assertEqual(events[pair["proposal_ordinal"] - 1]["text"], "final proposal")
        self.assertEqual(len(pair["collapsed_stop_ordinals"]), 1)

    def test_interrupt_then_yes_is_unpaired(self) -> None:
        self._start("codex")
        self._submit("go", "t1", host="codex")
        self._stop("proposal", "t1", host="codex")
        handle_event(self.store, "codex", _payload("Interrupt", host="codex", turn_id="t1"))
        self._submit("yes", "t2", host="codex")
        pair = self._pairs("codex")[-1]
        self.assertEqual((pair["state"], pair["unpaired_reason"]), ("unpaired", "interrupted"))

    def test_empty_stop_is_unpaired(self) -> None:
        self._start()
        self._submit("go", "t1")
        self._stop("", "t1")
        self._submit("yes", "t2")
        pair = self._pairs()[-1]
        self.assertEqual((pair["state"], pair["unpaired_reason"]), ("unpaired", "empty-stop"))

    def test_late_stop_with_same_text_is_recorded_but_consistent(self) -> None:
        self._start()
        self._submit("go", "t1")
        self._stop("proposal", "t1")
        self._submit("yes", "t2")
        self._stop("proposal", "t1")  # stop guard re-fired after the reply
        pair = self._pairs()[-1]
        self.assertEqual(pair["state"], "paired")
        self.assertEqual(len(pair["late"]), 1)
        self.assertFalse(pair["inconsistent"])
        raw = self.store.read_pairs("claude", "sess-1")
        self.assertEqual([r["kind"] for r in raw], ["pair", "pair", "late"])

    def test_late_stop_with_different_text_marks_pair_inconsistent(self) -> None:
        self._start()
        self._submit("go", "t1")
        self._stop("proposal A", "t1")
        self._submit("yes", "t2")
        self._stop("proposal B", "t1")
        pair = self._pairs()[-1]
        self.assertEqual(pair["state"], "paired")
        self.assertTrue(pair["inconsistent"])
        # The frozen pair still points at the original proposal.
        events = self.store.read_events("claude", "sess-1")
        self.assertEqual(events[pair["proposal_ordinal"] - 1]["text"], "proposal A")
        self.assertEqual(events[-1]["text"], "proposal B")  # original events preserved

    def test_stop_for_open_turn_is_not_late(self) -> None:
        self._start()
        self._submit("go", "t1")
        self._stop("proposal", "t1")
        self._submit("yes", "t2")
        self._stop("done", "t2")  # normal end of the turn started by t2
        self.assertEqual(self._pairs()[-1]["late"], [])
        self.assertEqual([r["kind"] for r in self.store.read_pairs("claude", "sess-1")], ["pair", "pair"])

    def test_late_stop_after_unpaired_reply_does_not_pair_it(self) -> None:
        self._start()
        self._submit("go", "t1")
        self._submit("yes", "t2")  # no Stop in between: unpaired
        self._stop("proposal", "t1")  # arrives late
        pair = self._pairs()[-1]
        self.assertEqual(pair["state"], "unpaired")
        self.assertEqual(len(pair["late"]), 1)
        self.assertFalse(pair["inconsistent"])

    def test_subagent_stop_never_supplies_a_proposal(self) -> None:
        self._start()
        self._submit("go", "t1")
        handle_event(self.store, "claude", _payload("SubagentStop", prompt_id="t1", last_assistant_message="subagent text"))
        self._submit("yes", "t2")
        pair = self._pairs()[-1]
        self.assertEqual((pair["state"], pair["unpaired_reason"]), ("unpaired", "no-stop"))

    def test_claude_harness_turns_are_not_operator_turns(self) -> None:
        notice = (
            "<task-notification>\n<task-id>k1</task-id>\n<status>completed</status>\n"
            "<result>{\"verdict\": \"approved\"}</result>\n</task-notification>"
        )
        hand_back = '<agent-message from="a1">\n  report text\n</agent-message>\n'
        self._start()
        self._submit("run the review", "t1")
        self._stop("Review is running in the background.", "t1")
        self._submit(notice, "t2")
        self._stop("The reviewer approved. Close the plan?", "t2")
        self._submit(hand_back, "t3")
        self._submit("yes", "t4")
        events = self.store.read_events("claude", "sess-1")
        prompts = [e["text"] for e in events if e["event"] == "UserPromptSubmit"]
        self.assertEqual(prompts, ["run the review", "yes"])
        pairs = self._pairs()
        self.assertEqual(len(pairs), 2)
        self.assertEqual(pairs[-1]["state"], "paired")
        self.assertEqual(pairs[-1]["proposal_turn_id"], "t2")
        self.assertEqual(
            events[pairs[-1]["proposal_ordinal"] - 1]["text"], "The reviewer approved. Close the plan?"
        )

    def test_operator_words_around_an_envelope_are_still_captured(self) -> None:
        quoted = "<task-notification>\n<status>failed</status>\n</task-notification>\nwhy did this fail?"
        self.assertFalse(intake_adapter.is_host_injected_prompt("claude", quoted))
        self.assertFalse(intake_adapter.is_host_injected_prompt("codex", "<task-notification></task-notification>"))
        self._start()
        self._submit(quoted, "t1")
        prompts = [e for e in self.store.read_events("claude", "sess-1") if e["event"] == "UserPromptSubmit"]
        self.assertEqual([e["text"] for e in prompts], [quoted])

    def test_evicted_proposal_is_reported_as_evicted(self) -> None:
        self._start()
        self._submit("go", "t1")
        self._stop("proposal", "t1")
        for i in range(PRESELECTION_MAX_EVENTS + 2):
            self._stop(f"noise {i}", "tx")
        self._submit("yes", "t2")
        pair = self._pairs()[-1]
        # The latest Stop survived eviction, so the reply pairs with the noise,
        # never with reconstructed text.
        self.assertEqual(pair["state"], "paired")
        self.assertEqual(self.store.read_events("claude", "sess-1")[0]["event"], "evicted")

    def test_lookup_reports_pair_counts(self) -> None:
        self._start()
        self._submit("go", "t1")
        self._stop("proposal", "t1")
        self._submit("yes", "t2")
        record = self.store.load_session("claude", "sess-1")
        env = dict(os.environ, **{intake_adapter.INTAKE_ROOT_ENV: str(self.root)})
        out = subprocess.run(
            [sys.executable, str(ADAPTER), "--lookup", record["handle"]],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(json.loads(out.stdout)["pairs"], {"paired": 1, "unpaired": 1, "inconsistent": 0})


if __name__ == "__main__":
    unittest.main()


class OtherHostTests(AdapterCase):
    """Hermes, opencode, and Antigravity feed the same adapter (plan 4.1)."""

    def test_hermes_plugin_payloads_get_context_json(self) -> None:
        self.run_hook("hermes", _payload("SessionStart", host="hermes", session_id="h-1", source="startup"))
        first = self.run_hook("hermes", _payload("UserPromptSubmit", session_id="h-1", prompt="Sync notes."))
        self.assertTrue(json.loads(first.stdout)["context"].startswith("cartopian-session: cs-"))
        self.run_hook("hermes", _payload("Stop", session_id="h-1", last_assistant_message="Summary."))
        self.run_hook("hermes", _payload("UserPromptSubmit", session_id="h-1", prompt="yes"))
        pairs = self.store.pair_states("hermes", "h-1")
        self.assertEqual(pairs[1]["state"], "paired")
        self.run_hook("hermes", _payload("SessionEnd", session_id="h-1", reason="completed"))
        self.assertIsNone(self.store.load_session("hermes", "h-1"))  # unbound: discarded at end

    def test_opencode_message_ids_are_turn_ids(self) -> None:
        self.run_hook("opencode", _payload("SessionStart", host="opencode", session_id="o-1", source="startup"))
        first = self.run_hook("opencode", _payload("UserPromptSubmit", session_id="o-1", prompt="Sync notes.", message_id="m1"))
        self.assertTrue(first.stdout.startswith("cartopian-session: cs-"))
        self.run_hook("opencode", _payload("Stop", session_id="o-1", last_assistant_message="Summary A", message_id="m1"))
        self.run_hook("opencode", _payload("UserPromptSubmit", session_id="o-1", prompt="yes", message_id="m2"))
        # A repeated idle for the consumed turn with the same text is late but consistent.
        self.run_hook("opencode", _payload("Stop", session_id="o-1", last_assistant_message="Summary A", message_id="m1"))
        events = self.store.read_events("opencode", "o-1")
        self.assertEqual([e.get("turn_id") for e in events][1:4], ["m1", "m1", "m2"])
        pairs = self.store.pair_states("opencode", "o-1")
        self.assertEqual(pairs[1]["state"], "paired")
        self.assertFalse(pairs[1]["inconsistent"])
        self.assertEqual(len(pairs[1]["late"]), 1)

    def test_normalize_payload_leaves_canonical_hosts_alone(self) -> None:
        raw = {"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": "x"}
        self.assertIs(intake_adapter.normalize_payload("claude", raw), raw)
        self.assertEqual(intake_adapter.format_routing_output("opencode", "line"), "line")
        self.assertEqual(intake_adapter.format_routing_output("antigravity", None), "{}")

    def test_antigravity_reads_the_transcript_at_its_hook_moments(self) -> None:
        transcript = Path(self.tmp.name) / "transcript_full.jsonl"
        wrapped = "<USER_REQUEST>\nSync notes to Markdown.\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\nThe current local time is: now.\n</ADDITIONAL_METADATA>"
        transcript.write_text(json.dumps({"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": wrapped}) + "\n")
        base = {"conversationId": "conv-1", "transcriptPath": str(transcript), "workspacePaths": ["/tmp/work"], "modelName": "m"}

        def hook(event, **extra):
            env = {k: v for k, v in os.environ.items() if k != intake_adapter.ROLE_ENV}
            env[intake_adapter.INTAKE_ROOT_ENV] = str(self.root)
            return subprocess.run(
                [sys.executable, str(ADAPTER), "--host", "antigravity", "--event", event],
                input=json.dumps({**base, **extra}), capture_output=True, text=True, env=env,
            )

        self.assertEqual(hook("SessionStart").stdout.strip(), "{}")
        first = json.loads(hook("PreInvocation", invocationNum=0, initialNumSteps=1).stdout)
        self.assertTrue(first["injectSteps"][0]["ephemeralMessage"].startswith("cartopian-session: cs-"))
        # A later model call in the same turn is not a prompt and records nothing.
        self.assertEqual(hook("PreInvocation", invocationNum=1, initialNumSteps=3).stdout.strip(), "{}")
        with transcript.open("a") as fh:
            fh.write(json.dumps({"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "Here is the summary."}) + "\n")
        self.assertEqual(hook("Stop", executionNum=0, terminationReason="NO_TOOL_CALL").stdout.strip(), "{}")
        with transcript.open("a") as fh:
            fh.write(json.dumps({"step_index": 2, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "<USER_REQUEST>\nyes\n</USER_REQUEST>"}) + "\n")
        self.assertEqual(hook("PreInvocation", invocationNum=0, initialNumSteps=3).stdout.strip(), "{}")  # announced once
        events = self.store.read_events("antigravity", "conv-1")
        self.assertEqual([(e["event"], e.get("turn_id")) for e in events],
                         [("SessionStart", None), ("UserPromptSubmit", "step-0"), ("Stop", "step-0"), ("UserPromptSubmit", "step-2")])
        self.assertEqual(events[1]["text"], "Sync notes to Markdown.")
        self.assertEqual(events[2]["text"], "Here is the summary.")
        self.assertEqual(events[1]["cwd"] if "cwd" in events[1] else "/tmp/work", "/tmp/work")
        pairs = self.store.pair_states("antigravity", "conv-1")
        self.assertEqual(pairs[1]["state"], "paired")
        self.assertEqual(pairs[1]["proposal_turn_id"], "step-0")
        # A dispatched session still answers the host with valid JSON.
        env = dict(os.environ, **{intake_adapter.ROLE_ENV: "coder", intake_adapter.INTAKE_ROOT_ENV: str(self.root)})
        out = subprocess.run([sys.executable, str(ADAPTER), "--host", "antigravity", "--event", "PreInvocation"],
                             input=json.dumps(base), capture_output=True, text=True, env=env)
        self.assertEqual(out.stdout.strip(), "{}")

