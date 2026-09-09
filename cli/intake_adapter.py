"""Host intake adapter — the hook-invoked recorder for operator request evidence.

One stdlib script, invoked by a host's user-level hooks (Codex CLI/desktop
via ``~/.codex/hooks.json``; Claude Code via ``~/.claude/settings.json``), that
records the operator's own words and the assistant proposal they answer at the
only point where a host — not the PM — supplies them. Every record here is an
*adapter receipt*: the resolver (``cli/request_trace.py`` and its successors)
treats adapter receipts as the only source of confirmed operator evidence.
Anything a PM writes by hand under ``requests/`` has no receipt and stays
unconfirmed.

Events recorded (both hosts unless noted):

- ``SessionStart``      — creates the session record and its opaque handle.
- ``UserPromptSubmit``  — the operator's prompt text (``prompt``); the first
                          one in a session prints the routing line below.
- ``Stop``              — the assistant's final message for the turn
                          (``last_assistant_message``).
- ``SessionEnd``        — ends the session; an unbound session's buffer is
                          discarded here.
- ``Interrupt``         — Codex only; marks the turn's proposal as withdrawn.
- ``SubagentStart`` / ``SubagentStop`` and every other event are ignored.

Pairing (plan section 4.5). Every ``UserPromptSubmit`` freezes one immutable
pair record in ``pairs.jsonl``: the reply is paired with the latest ``Stop``
since the previous submission when that Stop carries text and its turn has
no ``Interrupt``; repeated Stops for the turn collapse to the last one;
otherwise the reply is ``unpaired`` with a reason. A Stop that arrives after
the submission for a turn already consumed is appended as a ``late`` record
that never rewrites the pair; when its text differs from the paired
proposal the pair is marked inconsistent. Subagent events never supply a
proposal.

Routing line (stdout, which both hosts add to the model's context on
``UserPromptSubmit``)::

    cartopian-session: <handle> (Cartopian capture active; preselection buffer bounded to 200 events / 2 MiB)

The handle is an opaque routing reference the PM hands to ``select_project``;
it is not a credential. The server resolves it here (``resolve_handle``) and
takes host and session identity from the session record, never from tool
arguments.

Storage (same-user writable by design; see the plan's assurance statement)::

    <intake root>/sessions/<host>/<session_id>/session.json
    <intake root>/sessions/<host>/<session_id>/events.jsonl
    <intake root>/sessions/<host>/<session_id>/pairs.jsonl
    <intake root>/handles/<handle>.json

The intake root defaults to ``~/.cartopian/intake``; ``CARTOPIAN_INTAKE_ROOT``
overrides it (tests, alternate install roots).

Guarantees this script keeps:

- It never fails the host: every error is one stderr line and exit 0.
- It never records inside a dispatched session: ``CARTOPIAN_ROLE`` set means
  exit 0 with no output and no file touched.
- Receipt ordinals are adapter-assigned, monotonic per session, and never
  reused; eviction replaces dropped events with one ``evicted`` marker that
  names the ordinal range, count, and bytes discarded.
- ``transcript_path`` is never read or stored.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROLE_ENV = "CARTOPIAN_ROLE"
INTAKE_ROOT_ENV = "CARTOPIAN_INTAKE_ROOT"

HOSTS = ("claude", "codex", "hermes", "opencode", "antigravity")

# Preselection buffer bounds (section 4.2). Both are enforced per session
# while the session is bound to no project; a bound session keeps everything.
PRESELECTION_MAX_EVENTS = 200
PRESELECTION_MAX_BYTES = 2 * 1024 * 1024

# Sessions that never bound a project and received no SessionEnd are swept
# after this age.
UNBOUND_SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60

ROUTING_LINE_PREFIX = "cartopian-session:"

# Host payload field that carries the opaque turn correlation key.
# Opaque per-turn correlation keys. Hermes exposes none; pairing then relies
# on receipt order alone, which its single-threaded sessions guarantee.
# opencode's assistant message carries the user message id as ``parentID``,
# which the plugin shim forwards as ``message_id``.
_TURN_KEYS = {
    "claude": "prompt_id",
    "codex": "turn_id",
    "hermes": None,
    "opencode": "message_id",
    # Antigravity: the transcript step index of the operator's input, derived
    # by the adapter for both the prompt and its Stop.
    "antigravity": "turn_id",
}
# Hosts whose hook protocol requires a JSON object on stdout for every call.
_ALWAYS_JSON_HOSTS = frozenset({"antigravity"})
# The Hermes and opencode shims (``wrappers/intake/``) send canonical
# payloads; Antigravity is normalized below from its own hook vocabulary.

RECORDED_EVENTS = frozenset(
    {"SessionStart", "UserPromptSubmit", "Stop", "SessionEnd", "Interrupt"}
)


def default_intake_root() -> Path:
    override = os.environ.get(INTAKE_ROOT_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cartopian" / "intake"


def routing_line(handle: str) -> str:
    mib = PRESELECTION_MAX_BYTES // (1024 * 1024)
    return (
        f"{ROUTING_LINE_PREFIX} {handle} (Cartopian capture active; "
        f"preselection buffer bounded to {PRESELECTION_MAX_EVENTS} events / "
        f"{mib} MiB)"
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _new_handle() -> str:
    return "cs-" + secrets.token_hex(8)


# ---------------------------------------------------------------------------
# Session store
# ---------------------------------------------------------------------------
class SessionStore:
    """Filesystem layout under one intake root."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def session_dir(self, host: str, session_id: str) -> Path:
        return self.root / "sessions" / host / session_id

    def handle_path(self, handle: str) -> Path:
        return self.root / "handles" / f"{handle}.json"

    # -- session record ----------------------------------------------------
    def load_session(self, host: str, session_id: str) -> Optional[Dict[str, Any]]:
        path = self.session_dir(host, session_id) / "session.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def save_session(self, record: Dict[str, Any]) -> None:
        sdir = self.session_dir(record["host"], record["session_id"])
        sdir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(sdir / "session.json", record)

    def create_session(
        self, host: str, session_id: str, *, created_by: str, cwd: Optional[str]
    ) -> Dict[str, Any]:
        handle = _new_handle()
        record = {
            "schema": "cartopian-intake-session-v1",
            "handle": handle,
            "host": host,
            "session_id": session_id,
            "created": _now(),
            "created_by": created_by,
            "cwd": cwd,
            "announce_pending": True,
            "bindings": [],
            "ended": None,
        }
        self.save_session(record)
        hpath = self.handle_path(handle)
        hpath.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(
            hpath, {"handle": handle, "host": host, "session_id": session_id}
        )
        return record

    def ensure_session(
        self, host: str, session_id: str, *, event: str, cwd: Optional[str]
    ) -> Dict[str, Any]:
        record = self.load_session(host, session_id)
        if record is None:
            record = self.create_session(
                host, session_id, created_by=event, cwd=cwd
            )
        return record

    def discard_session(self, record: Dict[str, Any]) -> None:
        sdir = self.session_dir(record["host"], record["session_id"])
        handle = record.get("handle")
        if isinstance(handle, str):
            try:
                self.handle_path(handle).unlink()
            except FileNotFoundError:
                pass
        shutil.rmtree(sdir, ignore_errors=True)

    # -- events ------------------------------------------------------------
    def events_path(self, host: str, session_id: str) -> Path:
        return self.session_dir(host, session_id) / "events.jsonl"

    def read_events(self, host: str, session_id: str) -> List[Dict[str, Any]]:
        path = self.events_path(host, session_id)
        if not path.is_file():
            return []
        events: List[Dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    events.append(item)
        return events

    def append_event(
        self, record: Dict[str, Any], event: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Assign the next receipt ordinal, append, and enforce the buffer bound."""
        host, session_id = record["host"], record["session_id"]
        path = self.events_path(host, session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.read_events(host, session_id)
        last = max((int(e.get("ordinal", 0)) for e in existing), default=0)
        event = dict(event)
        event["ordinal"] = last + 1
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        if not record.get("bindings"):
            self._enforce_preselection_bound(path, existing + [event])
        return event

    def _enforce_preselection_bound(
        self, path: Path, events: List[Dict[str, Any]]
    ) -> None:
        """Evict oldest events until the unbound buffer fits both bounds.

        Marker events already present are kept and folded into one leading
        marker so the log always carries a single, cumulative eviction notice.
        """
        def size(e: Dict[str, Any]) -> int:
            return len(json.dumps(e, ensure_ascii=False).encode("utf-8")) + 1

        kept = [e for e in events if e.get("event") != "evicted"]
        markers = [e for e in events if e.get("event") == "evicted"]
        total_bytes = sum(size(e) for e in kept)
        dropped: List[Dict[str, Any]] = []
        while kept and (
            len(kept) > PRESELECTION_MAX_EVENTS or total_bytes > PRESELECTION_MAX_BYTES
        ):
            victim = kept.pop(0)
            total_bytes -= size(victim)
            dropped.append(victim)
        if not dropped:
            return
        prior_count = sum(int(m.get("count", 0)) for m in markers)
        prior_bytes = sum(int(m.get("bytes", 0)) for m in markers)
        prior_first = min(
            (int(m.get("first_ordinal", 0)) for m in markers), default=None
        )
        marker = {
            "event": "evicted",
            "received": _now(),
            "count": prior_count + len(dropped),
            "bytes": prior_bytes + sum(size(e) for e in dropped),
            "first_ordinal": prior_first if prior_first is not None else dropped[0]["ordinal"],
            "last_ordinal": dropped[-1]["ordinal"],
            # The marker carries the ordinal just before the first kept event
            # so ordinal monotonicity stays readable without the dropped lines.
            "ordinal": dropped[-1]["ordinal"],
        }
        lines = [json.dumps(marker, ensure_ascii=False)]
        lines.extend(json.dumps(e, ensure_ascii=False) for e in kept)
        _atomic_write_text(path, "\n".join(lines) + "\n")

    # -- pairs -------------------------------------------------------------
    def pairs_path(self, host: str, session_id: str) -> Path:
        return self.session_dir(host, session_id) / "pairs.jsonl"

    def read_pairs(self, host: str, session_id: str) -> List[Dict[str, Any]]:
        path = self.pairs_path(host, session_id)
        if not path.is_file():
            return []
        items: List[Dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    items.append(item)
        return items

    def append_pair_record(self, host: str, session_id: str, record: Dict[str, Any]) -> None:
        path = self.pairs_path(host, session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def pair_states(self, host: str, session_id: str) -> List[Dict[str, Any]]:
        """Effective pair state: each frozen pair plus its late records."""
        pairs: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        for item in self.read_pairs(host, session_id):
            if item.get("kind") == "pair":
                view = dict(item)
                view["late"] = []
                view["inconsistent"] = False
                pairs[item["pair_id"]] = view
                order.append(item["pair_id"])
            elif item.get("kind") == "late" and item.get("pair_id") in pairs:
                view = pairs[item["pair_id"]]
                view["late"].append(item)
                if item.get("inconsistent"):
                    view["inconsistent"] = True
        return [pairs[pid] for pid in order]

    # -- handles -----------------------------------------------------------
    def resolve_handle(self, handle: str) -> Optional[Dict[str, Any]]:
        """Return the session record a handle routes to, or None."""
        if not isinstance(handle, str) or not handle.startswith("cs-"):
            return None
        hpath = self.handle_path(handle)
        if not hpath.is_file():
            return None
        try:
            index = json.loads(hpath.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(index, dict):
            return None
        host, session_id = index.get("host"), index.get("session_id")
        if not isinstance(host, str) or not isinstance(session_id, str):
            return None
        record = self.load_session(host, session_id)
        if record is None or record.get("handle") != handle:
            return None
        return record

    # -- housekeeping ------------------------------------------------------
    def sweep_stale_unbound(self, now: Optional[float] = None) -> int:
        """Discard unbound sessions with no end event older than the max age."""
        now = time.time() if now is None else now
        sessions_root = self.root / "sessions"
        if not sessions_root.is_dir():
            return 0
        swept = 0
        for host_dir in sessions_root.iterdir():
            if not host_dir.is_dir():
                continue
            for sdir in host_dir.iterdir():
                record = self.load_session(host_dir.name, sdir.name)
                if record is None or record.get("bindings"):
                    continue
                try:
                    newest = max(
                        p.stat().st_mtime for p in sdir.iterdir() if p.is_file()
                    )
                except (ValueError, OSError):
                    newest = 0.0
                if now - newest >= UNBOUND_SESSION_MAX_AGE_SECONDS:
                    self.discard_session(record)
                    swept += 1
        return swept


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}-{secrets.token_hex(4)}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Event handling
# ---------------------------------------------------------------------------
def _text_field(payload: Dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def _turn_id(host: str, payload: Dict[str, Any]) -> Optional[str]:
    key = _TURN_KEYS.get(host)
    value = payload.get(key) if key else None
    return value if isinstance(value, str) and value else None


def normalize_payload(host: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Translate a host's own hook vocabulary into the canonical events.

    Canonical: ``SessionStart``, ``UserPromptSubmit`` (``prompt``), ``Stop``
    (``last_assistant_message``), ``Interrupt``, ``SessionEnd``. Every host
    but Antigravity already speaks it.
    """
    if host == "antigravity":
        return _normalize_antigravity(payload)
    return payload


def format_routing_output(host: str, line: Optional[str]) -> str:
    """The stdout shape that puts the routing line into the model's context.

    ``line`` may be ``None`` for hosts that need a JSON object on every call.
    """
    if host == "hermes":
        return json.dumps({"context": line})
    if host == "antigravity":
        if not line:
            return "{}"
        return json.dumps({"injectSteps": [{"ephemeralMessage": line}]})
    return line or ""


# ---------------------------------------------------------------------------
# Antigravity: hook payloads carry no text; the operator's input and the
# model's reply are read from the transcript the payload names, at the
# host's own hook moment (the prompt is present at the turn's first
# PreInvocation, the final reply at Stop).
# ---------------------------------------------------------------------------
_AGY_REQUEST_OPEN = "<USER_REQUEST>"
_AGY_REQUEST_CLOSE = "</USER_REQUEST>"


def _agy_read_transcript(path: Any) -> List[Dict[str, Any]]:
    if not isinstance(path, str) or not path or not os.path.isfile(path):
        return []
    entries: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    entries.append(item)
    except OSError:
        return []
    return entries


def _agy_request_text(content: str) -> str:
    """The operator's words inside the ``<USER_REQUEST>`` wrapper, else all."""
    start = content.find(_AGY_REQUEST_OPEN)
    if start < 0:
        return content.strip()
    start += len(_AGY_REQUEST_OPEN)
    end = content.find(_AGY_REQUEST_CLOSE, start)
    return (content[start:end] if end >= 0 else content[start:]).strip()


def _agy_last_turn(entries: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """``(last USER_INPUT, last PLANNER_RESPONSE after it)``."""
    user = None
    reply = None
    for item in entries:
        if item.get("type") == "USER_INPUT" and item.get("source") == "USER_EXPLICIT":
            user, reply = item, None
        elif user is not None and item.get("type") == "PLANNER_RESPONSE" and isinstance(item.get("content"), str):
            reply = item
    return user, reply


def _normalize_antigravity(payload: Dict[str, Any]) -> Dict[str, Any]:
    event = payload.get("hook_event_name")
    session_id = payload.get("conversationId")
    workspaces = payload.get("workspacePaths")
    cwd = workspaces[0] if isinstance(workspaces, list) and workspaces and isinstance(workspaces[0], str) else None
    out: Dict[str, Any] = {"hook_event_name": event, "session_id": session_id, "cwd": cwd}
    if event == "SessionStart":
        out["source"] = "startup"
        return out
    if event == "PreInvocation":
        if payload.get("invocationNum") not in (0, None):
            return out  # a later model call within the same turn: not a prompt
        user, _ = _agy_last_turn(_agy_read_transcript(payload.get("transcriptPath")))
        text = _agy_request_text(user.get("content") or "") if user else ""
        if not user or not text:
            return out  # nothing to record; the event stays unrecorded
        out["hook_event_name"] = "UserPromptSubmit"
        out["prompt"] = text
        out["turn_id"] = f"step-{user.get('step_index')}"
        return out
    if event == "Stop":
        user, reply = _agy_last_turn(_agy_read_transcript(payload.get("transcriptPath")))
        out["last_assistant_message"] = (reply.get("content") or "") if reply else ""
        if user is not None:
            out["turn_id"] = f"step-{user.get('step_index')}"
        return out
    return out


# ---------------------------------------------------------------------------
# Pairing state machine (section 4.5)
# ---------------------------------------------------------------------------
def _pair_id(record: Dict[str, Any], reply_ordinal: int) -> str:
    return f"{record['handle']}/pair-{reply_ordinal}"


def resolve_pair(
    record: Dict[str, Any], prior: List[Dict[str, Any]], reply: Dict[str, Any]
) -> Dict[str, Any]:
    """Freeze the pair for ``reply`` from the events received before it.

    ``prior`` is every event with a smaller ordinal (markers included). The
    result is the immutable pair record; nothing after this call changes it.
    """
    prev_submissions = [e for e in prior if e.get("event") == "UserPromptSubmit"]
    floor = prev_submissions[-1]["ordinal"] if prev_submissions else 0
    window = [e for e in prior if int(e.get("ordinal", 0)) > floor]
    stops = [e for e in window if e.get("event") == "Stop"]
    interrupts = [e for e in window if e.get("event") == "Interrupt"]
    evicted = any(e.get("event") == "evicted" for e in window)

    base = {
        "kind": "pair",
        "pair_id": _pair_id(record, reply["ordinal"]),
        "received": reply["received"],
        "reply_ordinal": reply["ordinal"],
        "reply_turn_id": reply.get("turn_id"),
        "proposal_ordinal": None,
        "proposal_turn_id": None,
        "collapsed_stop_ordinals": [],
        "state": "unpaired",
        "unpaired_reason": None,
    }
    if not stops:
        base["unpaired_reason"] = "evicted" if evicted else "no-stop"
        return base
    proposal = stops[-1]
    turn = proposal.get("turn_id")
    base["proposal_turn_id"] = turn
    base["collapsed_stop_ordinals"] = [
        e["ordinal"] for e in stops[:-1] if turn is None or e.get("turn_id") in (turn, None)
    ]
    interrupted = any(
        turn is None or i.get("turn_id") is None or i.get("turn_id") == turn
        for i in interrupts
    )
    if interrupted:
        base["unpaired_reason"] = "interrupted"
        return base
    if not (proposal.get("text") or "").strip():
        base["unpaired_reason"] = "empty-stop"
        return base
    base["proposal_ordinal"] = proposal["ordinal"]
    base["state"] = "paired"
    return base


def classify_late_stop(
    record: Dict[str, Any],
    prior: List[Dict[str, Any]],
    pairs: List[Dict[str, Any]],
    stop: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Return a ``late`` record when ``stop`` belongs to an already-consumed turn.

    A turn is consumed once a later ``UserPromptSubmit`` has frozen the pair
    that answers it. A Stop with no turn identity, or for the open turn, is
    ordinary and returns ``None``.
    """
    turn = stop.get("turn_id")
    if turn is None:
        return None
    starter = next(
        (e for e in prior if e.get("event") == "UserPromptSubmit" and e.get("turn_id") == turn),
        None,
    )
    if starter is None:
        return None
    consumer = next(
        (
            e for e in prior
            if e.get("event") == "UserPromptSubmit" and int(e.get("ordinal", 0)) > starter["ordinal"]
        ),
        None,
    )
    if consumer is None:
        return None  # the turn is still open: this is its normal Stop
    pair_id = _pair_id(record, consumer["ordinal"])
    pair = next((p for p in pairs if p.get("kind") == "pair" and p.get("pair_id") == pair_id), None)
    inconsistent = False
    if pair is not None and pair.get("state") == "paired":
        paired = next(
            (e for e in prior if int(e.get("ordinal", -1)) == pair.get("proposal_ordinal")), None
        )
        paired_text = (paired or {}).get("text")
        if paired_text is None:
            inconsistent = True  # paired proposal no longer readable: cannot confirm agreement
        else:
            inconsistent = paired_text.strip() != (stop.get("text") or "").strip()
    return {
        "kind": "late",
        "pair_id": pair_id,
        "received": stop["received"],
        "event_ordinal": stop["ordinal"],
        "turn_id": turn,
        "inconsistent": inconsistent,
    }


def handle_event(
    store: SessionStore, host: str, payload: Dict[str, Any]
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Process one hook payload.

    Returns ``(stdout_line, recorded_event)``. Either may be ``None``: ignored
    events record nothing, and only the first prompt after a (re)binding
    prints the routing line.
    """
    payload = normalize_payload(host, payload)
    event = payload.get("hook_event_name")
    if event not in RECORDED_EVENTS:
        return None, None
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        sys.stderr.write("cartopian intake: payload carries no session_id; not recorded\n")
        return None, None
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None

    if event == "SessionStart":
        store.sweep_stale_unbound()
    record = store.ensure_session(host, session_id, event=event, cwd=cwd)

    item: Dict[str, Any] = {
        "event": event,
        "received": _now(),
        "turn_id": _turn_id(host, payload),
    }
    if event == "SessionStart":
        item["source"] = _text_field(payload, "source") or None
    elif event == "UserPromptSubmit":
        item["text"] = _text_field(payload, "prompt")
    elif event == "Stop":
        item["text"] = _text_field(payload, "last_assistant_message")
    elif event == "SessionEnd":
        item["reason"] = _text_field(payload, "reason") or None
    prior = store.read_events(host, session_id)
    recorded = store.append_event(record, item)

    if event == "UserPromptSubmit":
        store.append_pair_record(host, session_id, resolve_pair(record, prior, recorded))
    elif event == "Stop":
        late = classify_late_stop(
            record, prior, store.read_pairs(host, session_id), recorded
        )
        if late is not None:
            store.append_pair_record(host, session_id, late)

    stdout_line: Optional[str] = None
    if event == "UserPromptSubmit" and record.get("announce_pending", True):
        stdout_line = routing_line(record["handle"])
        record["announce_pending"] = False
        store.save_session(record)
    elif event == "SessionEnd":
        if record.get("bindings"):
            record["ended"] = recorded["received"]
            store.save_session(record)
        else:
            store.discard_session(record)
    return stdout_line, recorded


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="intake_adapter",
        description="Cartopian host intake adapter (invoked by host hooks).",
    )
    p.add_argument("--host", choices=HOSTS, help="host whose hook is invoking the adapter")
    p.add_argument(
        "--event",
        default=None,
        help="hook event name, for hosts whose payload does not carry it (Antigravity)",
    )
    p.add_argument(
        "--lookup",
        metavar="HANDLE",
        help="print the session record a handle routes to (diagnostic; no capture)",
    )
    p.add_argument(
        "--root",
        type=Path,
        default=None,
        help=f"intake root (default: ${INTAKE_ROOT_ENV} or ~/.cartopian/intake)",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    store = SessionStore(args.root or default_intake_root())

    if args.lookup:
        record = store.resolve_handle(args.lookup)
        if record is None:
            sys.stderr.write(f"session-unbound: no session routes from handle {args.lookup}\n")
            return 1
        events = store.read_events(record["host"], record["session_id"])
        summary = dict(record)
        summary["event_count"] = len([e for e in events if e.get("event") != "evicted"])
        summary["evicted"] = next(
            (e for e in events if e.get("event") == "evicted"), None
        )
        pairs = store.pair_states(record["host"], record["session_id"])
        summary["pairs"] = {
            "paired": sum(1 for p in pairs if p["state"] == "paired"),
            "unpaired": sum(1 for p in pairs if p["state"] == "unpaired"),
            "inconsistent": sum(1 for p in pairs if p["inconsistent"]),
        }
        sys.stdout.write(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
        return 0

    if not args.host:
        sys.stderr.write("cartopian intake: --host is required in hook mode\n")
        return 0
    always_json = args.host in _ALWAYS_JSON_HOSTS

    def finish(line: Optional[str]) -> int:
        if line or always_json:
            sys.stdout.write(format_routing_output(args.host, line) + "\n")
        return 0

    if os.environ.get(ROLE_ENV):
        return finish(None)  # dispatched session: never capture, never announce

    try:
        raw = sys.stdin.buffer.read()
        payload = json.loads(raw.decode("utf-8")) if raw.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError("hook payload is not a JSON object")
    except Exception as exc:
        sys.stderr.write(f"cartopian intake: unreadable hook payload ({exc}); not recorded\n")
        return finish(None)
    if args.event:
        payload["hook_event_name"] = args.event

    try:
        line, _ = handle_event(store, args.host, payload)
    except Exception as exc:  # never fail the host
        sys.stderr.write(f"cartopian intake: capture failed ({exc}); host not interrupted\n")
        return finish(None)
    return finish(line)


if __name__ == "__main__":
    sys.exit(main())
