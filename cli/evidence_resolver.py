"""Shared request-evidence resolver over host-captured operator turns.

This module decides which captured operator words are evidence for a governed
unit, and in what state. It is the one place that answers that question for
every consumer (``cli/request_trace.py`` routes prompt generation, dispatch,
handoff, review intake, lifecycle guards, and audit through it).

Sources, in trust order:

- **Adapter receipts.** The host intake adapter (``cli/intake_adapter.py``)
  records the operator's prompts and the assistant messages they answer under
  the intake root. ``select_project`` binds a session to a project by receipt
  ordinal range in ``requests/bindings.json``. A binding counts only when the
  session record in the intake root carries the same binding; a binding that
  exists in the project alone has no receipt and yields no candidates.
- **The confirmation exchange.** When requirements or the plan lock, the
  writer binds the pair the operator answered (the compact intent summary
  and the confirming or correcting reply) into the binding. That pair is the
  project's original evidence. At most one non-revoked confirmation exists;
  a fresh one after revocation records ``supersedes``.
- **References.** A decision names captured turns with one structural
  marker, ``Operator request evidence for: <unit>: <capture-id>[, ...]``,
  and requirements or a task name them in their evidence sections. Text
  and provenance always come from the capture; a block quote under the
  marker is checked against the captured text whole and never substitutes
  for it.

Everything else is *unconfirmed*: legacy ``Operator request quote for:``
quotations, the historical DEC-007..DEC-009 attributions, JSON under
``requests/chat/`` (nothing in the repository writes it), a reference to a
turn that was never captured for this project, a partial quotation, a bare
assent whose proposal was not captured, or a revoked identity. Unconfirmed
items are reported for the lookup tool and the audit; they never satisfy an
evidence gate.

Refusals that block rather than degrade:

- ``inconsistent-pair`` — a late Stop disagreed with the paired proposal; the
  operator resolves it with a fresh scope statement.
- ``unpaired-assent`` — the confirmation reply is a low-information response
  ("yes", "proceed") whose proposal was not captured.
- ``ambiguous-request`` — more than one non-revoked confirmation governs the
  project.
- ``ambiguous-session`` — more than one capture session is bound and the
  writer was not told which one is confirming.

Capture identities: an operator turn is ``<handle>/turn-<ordinal>``; its
paired assistant proposal is ``<handle>/proposal-<ordinal>``; the adapter's
pair record is ``<handle>/pair-<reply ordinal>``. Ordinals are adapter
receipt ordinals and are the only ordering used. Across sessions bound to
one project, turns are ordered by binding order, then ordinal.

Assurance is procedural traceability: this module establishes where each
selected excerpt came from under an intact installation. It does not prove
human authorship against unrestricted same-user access to the intake root or
to ``requests/``.
"""
from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from cli import intake_adapter
from cli.request_trace import (
    REQUESTS_DIRNAME,
    HOST_CHAT_DIRNAME,
    CapturedContext,
    GovernedUnit,
    RequestEvidence,
    RequestRefusal,
    content_identity,
    is_low_information,
    read_contained_text,
)

BINDINGS_BASENAME = "bindings.json"
BINDINGS_SCHEMA = "cartopian-request-bindings-v1"
REVOCATIONS_BASENAME = "revocations.json"
REVOCATIONS_SCHEMA = "cartopian-request-revocations-v1"
QUARANTINE_DIRNAME = "quarantine"

SOURCE_KIND = "adapter-capture"
PROJECT_UNIT = GovernedUnit("project", "project")

HANDLE_RE = re.compile(r"^cs-[0-9a-f]{16}$")
CAPTURE_ID_RE = re.compile(r"^(cs-[0-9a-f]{16})/turn-(\d+)$")
CAPTURE_ID_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9/_-])(cs-[0-9a-f]{16}/turn-\d+)(?![A-Za-z0-9_-])")
SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
UNIT_RE = r"(project:project|planning:PLAN-\d{3}|task:TASK-\d{2}-\d{3})"

DECISION_EVIDENCE_MARKER = "Operator request evidence for:"
DECISION_EVIDENCE_MARKER_RE = re.compile(
    rf"^Operator request evidence for:\s*{UNIT_RE}:\s*(.+?)\s*$"
)
EVIDENCE_SECTION_HEADINGS = (
    "## Operator intent",
    "## Original request evidence",
    "## Request evidence",
)

EVIDENCE_KINDS = ("instruction", "confirmation", "correction")

NOT_CAPTURED_REMEDY = (
    "Stop. Report the missing evidence to the operator and name the "
    "supported intake for this host: the operator's own words reach "
    "Cartopian only through the host intake hooks in the operator's "
    "interactive session (`scripts/install.py --intake-hooks`; on Codex the "
    "operator must also trust the hooks in `/hooks`, on Hermes enable the "
    "plugin with `hermes plugins enable cartopian-intake`), bound to the project "
    "with `select_project`. Never create, copy, or edit records under "
    "`requests/` or the intake directory by any means, including shell."
)
INCONSISTENT_PAIR_REMEDY = (
    "Stop and report to the operator: the assistant message this reply "
    "answered was recorded twice with different text, so which proposal the "
    "operator confirmed cannot be established. The operator resolves it with "
    "a fresh scope statement in their own session; nothing in the project "
    "may be edited to clear it."
)
UNPAIRED_ASSENT_REMEDY = (
    "Stop and report to the operator: the reply is a content-free assent and "
    "the proposal it answered was not captured (interrupted, empty, or "
    "evicted), so it authorizes nothing. Ask the operator for a "
    "self-contained statement of the intent in their own session."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Project-side records: bindings and revocations
# ---------------------------------------------------------------------------
def bindings_path(project_root: Path) -> Path:
    return Path(project_root) / REQUESTS_DIRNAME / BINDINGS_BASENAME


def read_bindings(project_root: Path) -> List[Dict[str, Any]]:
    path = bindings_path(project_root)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("bindings"), list):
        return []
    return [b for b in data["bindings"] if isinstance(b, dict)]


def write_bindings(project_root: Path, bindings: List[Dict[str, Any]]) -> None:
    path = bindings_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(path, {"schema": BINDINGS_SCHEMA, "bindings": bindings})


def has_capture_history(project_root: Path) -> bool:
    """True once the project has ever been bound to a capture session.

    Used to keep the legacy (pre-v0.9) readability path closed for any
    project that had capture: the schema-version threshold has no trust
    effect and cannot be lowered to bypass evidence resolution.
    """
    return bindings_path(project_root).is_file()


def revocations_path(project_root: Path) -> Path:
    return Path(project_root) / REQUESTS_DIRNAME / REVOCATIONS_BASENAME


def read_revocations(project_root: Path) -> List[Dict[str, Any]]:
    path = revocations_path(project_root)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("revocations"), list):
        return []
    return [r for r in data["revocations"] if isinstance(r, dict)]


def write_revocations(project_root: Path, entries: List[Dict[str, Any]]) -> None:
    """Persist the revocation ledger (operator-only ``revoke-evidence``)."""
    path = revocations_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(path, {"schema": REVOCATIONS_SCHEMA, "revocations": entries})


def revoked_evidence_ids(project_root: Path) -> Set[str]:
    ids: Set[str] = set()
    for entry in read_revocations(project_root):
        for item in entry.get("evidence") or []:
            if isinstance(item, str):
                ids.add(item)
    return ids


def revoked_context_identities(project_root: Path) -> Set[str]:
    ids: Set[str] = set()
    for entry in read_revocations(project_root):
        for item in entry.get("review_contexts") or []:
            if isinstance(item, str):
                ids.add(item)
    return ids


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CapturedProposal:
    capture_id: str
    ordinal: int
    turn_id: Optional[str]
    text: str
    identity: str


@dataclass(frozen=True)
class CapturedTurn:
    """One operator prompt captured by the adapter inside a project binding."""

    capture_id: str
    handle: str
    host: str
    session_id: str
    binding_id: str
    position: Tuple[int, int]
    ordinal: int
    turn_id: Optional[str]
    text: str
    identity: str
    received: str
    source_identity: str
    source_path: str
    source_content_identity: str
    pair_state: Optional[str]
    unpaired_reason: Optional[str]
    inconsistent: bool
    proposal: Optional[CapturedProposal]

    @property
    def low_information(self) -> bool:
        return is_low_information(self.text)

    @property
    def paired(self) -> bool:
        return self.pair_state == "paired" and self.proposal is not None

    def context(self) -> Optional[CapturedContext]:
        if self.proposal is None:
            return None
        return CapturedContext(
            capture_id=self.proposal.capture_id,
            identity=self.proposal.identity,
            text=self.proposal.text,
            turn_id=self.proposal.turn_id,
        )


@dataclass(frozen=True)
class Eviction:
    handle: str
    count: int
    bytes: int
    first_ordinal: int
    last_ordinal: int

    def as_record(self) -> Dict[str, Any]:
        return {
            "handle": self.handle,
            "count": self.count,
            "bytes": self.bytes,
            "first_ordinal": self.first_ordinal,
            "last_ordinal": self.last_ordinal,
        }


@dataclass
class Candidates:
    bindings: List[Dict[str, Any]] = field(default_factory=list)
    turns: List[CapturedTurn] = field(default_factory=list)
    evictions: List[Eviction] = field(default_factory=list)
    unreceipted_bindings: List[str] = field(default_factory=list)
    session_ended: Dict[str, bool] = field(default_factory=dict)
    _index: Dict[str, CapturedTurn] = field(default_factory=dict)

    @property
    def sessions(self) -> int:
        return len({(b["host"], b["session_id"]) for b in self.bindings})

    def by_id(self, capture_id: str) -> Optional[CapturedTurn]:
        return self._index.get(capture_id)

    def evicted_ordinal(self, handle: str, ordinal: int) -> bool:
        return any(
            e.handle == handle and e.first_ordinal <= ordinal <= e.last_ordinal
            for e in self.evictions
        )

    def summary(self) -> Dict[str, Any]:
        return {
            "candidates": len(self.turns),
            "sessions": self.sessions,
            "evictions": [e.as_record() for e in self.evictions],
            "unreceipted_bindings": list(self.unreceipted_bindings),
        }


def intake_store(root: Optional[Path] = None) -> intake_adapter.SessionStore:
    return intake_adapter.SessionStore(root or intake_adapter.default_intake_root())


def _binding_ok(binding: Dict[str, Any], project_root: Path) -> bool:
    handle = binding.get("handle")
    host = binding.get("host")
    session_id = binding.get("session_id")
    binding_id = binding.get("binding_id")
    from_ordinal = binding.get("from_ordinal")
    to_ordinal = binding.get("to_ordinal")
    if not (isinstance(handle, str) and HANDLE_RE.fullmatch(handle)):
        return False
    if host not in intake_adapter.HOSTS:
        return False
    if not (isinstance(session_id, str) and SAFE_SEGMENT_RE.fullmatch(session_id)):
        return False
    if not isinstance(binding_id, str) or not binding_id.startswith(handle + "/binding-"):
        return False
    if isinstance(from_ordinal, bool) or not isinstance(from_ordinal, int) or from_ordinal < 1:
        return False
    if to_ordinal is not None and (isinstance(to_ordinal, bool) or not isinstance(to_ordinal, int)):
        return False
    project_path = binding.get("project_path")
    if not isinstance(project_path, str):
        return False
    return os.path.realpath(project_path) == os.path.realpath(str(project_root))


def _receipted(binding: Dict[str, Any], record: Optional[Dict[str, Any]]) -> bool:
    """The session record must carry the same binding, or it has no receipt."""
    if record is None or record.get("handle") != binding["handle"]:
        return False
    for own in record.get("bindings") or []:
        if not isinstance(own, dict):
            continue
        if (
            own.get("binding_id") == binding["binding_id"]
            and own.get("from_ordinal") == binding["from_ordinal"]
            and own.get("to_ordinal") == binding.get("to_ordinal")
            and isinstance(own.get("project_path"), str)
            and os.path.realpath(own["project_path"]) == os.path.realpath(binding["project_path"])
        ):
            return True
    return False


def _binding_sort_key(binding: Dict[str, Any]) -> str:
    # ``bound_at`` has second precision, so two sessions bound within one
    # second tie; the stable sort then keeps ``bindings.json`` append order,
    # which is the order the bindings were actually made. A random handle
    # must never decide which session's turns come first.
    return str(binding.get("bound_at") or "")


def load_candidates(
    project_root: Path, store: Optional[intake_adapter.SessionStore] = None
) -> Candidates:
    """Every captured operator turn inside a receipted binding of this project."""
    project_root = Path(project_root)
    result = Candidates()
    raw = read_bindings(project_root)
    if not raw:
        return result
    store = store or intake_store()
    verified: List[Dict[str, Any]] = []
    for binding in sorted(raw, key=_binding_sort_key):
        if not _binding_ok(binding, project_root):
            result.unreceipted_bindings.append(str(binding.get("binding_id") or "<malformed>"))
            continue
        record = store.load_session(binding["host"], binding["session_id"])
        if not _receipted(binding, record):
            result.unreceipted_bindings.append(binding["binding_id"])
            continue
        verified.append(binding)
        result.session_ended[binding["binding_id"]] = bool(record.get("ended"))
    result.bindings = verified

    for index, binding in enumerate(verified):
        host, session_id, handle = binding["host"], binding["session_id"], binding["handle"]
        lo = int(binding["from_ordinal"])
        hi = binding.get("to_ordinal")
        events = store.read_events(host, session_id)
        by_ordinal = {int(e.get("ordinal", 0)): e for e in events if e.get("event") != "evicted"}
        for marker in (e for e in events if e.get("event") == "evicted"):
            first = int(marker.get("first_ordinal", 0))
            last = int(marker.get("last_ordinal", 0))
            if last >= lo and (hi is None or first <= hi):
                result.evictions.append(Eviction(
                    handle=handle,
                    count=int(marker.get("count", 0)),
                    bytes=int(marker.get("bytes", 0)),
                    first_ordinal=first,
                    last_ordinal=last,
                ))
        pairs = {
            p["pair_id"]: p for p in store.pair_states(host, session_id)
        }
        rel = f"sessions/{host}/{session_id}/events.jsonl"
        for ordinal in sorted(by_ordinal):
            event = by_ordinal[ordinal]
            if event.get("event") != "UserPromptSubmit":
                continue
            if ordinal < lo or (hi is not None and ordinal > hi):
                continue
            text = event.get("text") if isinstance(event.get("text"), str) else ""
            pair = pairs.get(f"{handle}/pair-{ordinal}")
            proposal: Optional[CapturedProposal] = None
            pair_state = None
            unpaired_reason = None
            inconsistent = False
            if pair is not None:
                pair_state = pair.get("state")
                unpaired_reason = pair.get("unpaired_reason")
                inconsistent = bool(pair.get("inconsistent"))
                if pair_state == "paired":
                    stop = by_ordinal.get(int(pair.get("proposal_ordinal") or 0))
                    if stop is not None and isinstance(stop.get("text"), str):
                        proposal = CapturedProposal(
                            capture_id=f"{handle}/proposal-{stop['ordinal']}",
                            ordinal=int(stop["ordinal"]),
                            turn_id=stop.get("turn_id"),
                            text=stop["text"],
                            identity=content_identity(stop["text"].encode("utf-8")),
                        )
                    else:
                        # The paired proposal is no longer readable: the pair
                        # cannot be presented, and a bare assent has nothing
                        # to bound it.
                        pair_state = "unpaired"
                        unpaired_reason = "evicted"
            turn = CapturedTurn(
                capture_id=f"{handle}/turn-{ordinal}",
                handle=handle,
                host=host,
                session_id=session_id,
                binding_id=binding["binding_id"],
                position=(index, ordinal),
                ordinal=ordinal,
                turn_id=event.get("turn_id") if isinstance(event.get("turn_id"), str) else None,
                text=text,
                identity=content_identity(text.encode("utf-8")),
                received=str(event.get("received") or ""),
                source_identity=f"{host}:{session_id}:{event.get('turn_id') or ordinal}",
                source_path=f"{rel}#{ordinal}",
                source_content_identity=content_identity(
                    json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                ),
                pair_state=pair_state,
                unpaired_reason=unpaired_reason,
                inconsistent=inconsistent,
                proposal=proposal,
            )
            result.turns.append(turn)
            result._index[turn.capture_id] = turn
    return result


def candidate_summary(project_root: Path) -> Dict[str, Any]:
    return load_candidates(project_root).summary()


# ---------------------------------------------------------------------------
# Recent turns (lookup-evidence --recent)
# ---------------------------------------------------------------------------
RECENT_TURNS = 5
CANDIDATE_PREVIEW_CHARS = 120


def candidate_preview(text: str) -> Tuple[str, bool]:
    """A fixed-length, whitespace-collapsed preview and whether it was cut."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= CANDIDATE_PREVIEW_CHARS:
        return collapsed, False
    return collapsed[:CANDIDATE_PREVIEW_CHARS], True


def recent_turns(candidates: Candidates, selected: Dict[str, str]) -> List[Dict[str, Any]]:
    """The last few captured operator turns, oldest first, as identity rows.

    The one place a PM learns the identity of a turn the resolver did not
    select (an operator correction stated after lock) so a decision can
    reference it. Each row is the capture identity, session handle, receipt
    ordinal, pairing state, a fixed-length preview, and the kind it was
    selected as (or null). Never the whole text; never more than
    :data:`RECENT_TURNS` rows.
    """
    turns = sorted(candidates.turns, key=lambda t: t.position)[-RECENT_TURNS:]
    rows: List[Dict[str, Any]] = []
    for turn in turns:
        preview, truncated = candidate_preview(turn.text)
        rows.append({
            "capture_id": turn.capture_id,
            "session": turn.handle,
            "ordinal": turn.ordinal,
            "pair_state": turn.pair_state or "unpaired",
            "preview": preview,
            "truncated": truncated,
            "selected": selected.get(turn.capture_id),
        })
    return rows


# ---------------------------------------------------------------------------
# Confirmation exchange
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Confirmation:
    binding_id: str
    pair_id: str
    reply: CapturedTurn
    supersedes: Optional[str]
    bound_at: str

    def as_record(self) -> Dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "pair_id": self.pair_id,
            "reply": self.reply.capture_id,
            "reply_turn_id": self.reply.turn_id,
            "proposal": self.reply.proposal.capture_id if self.reply.proposal else None,
            "proposal_turn_id": self.reply.proposal.turn_id if self.reply.proposal else None,
            "supersedes": self.supersedes,
            "bound_at": self.bound_at,
        }


def confirmation_record(turn: CapturedTurn, *, supersedes: Optional[str], when: str) -> Dict[str, Any]:
    """The shape persisted in the binding's ``confirmation`` field."""
    return {
        "pair_id": f"{turn.handle}/pair-{turn.ordinal}",
        "reply": turn.capture_id,
        "reply_turn_id": turn.turn_id,
        "proposal": turn.proposal.capture_id if turn.proposal else None,
        "proposal_turn_id": turn.proposal.turn_id if turn.proposal else None,
        "supersedes": supersedes,
        "bound_at": when,
    }


def _check_pair_usable(turn: CapturedTurn, *, what: str) -> None:
    if turn.inconsistent:
        raise RequestRefusal(
            "inconsistent-pair",
            f"{what} {turn.capture_id} answers a proposal recorded with differing text",
            INCONSISTENT_PAIR_REMEDY,
        )
    if turn.low_information and not turn.paired:
        raise RequestRefusal(
            "unpaired-assent",
            f"{what} {turn.capture_id} is a low-information response with no "
            f"captured proposal ({turn.unpaired_reason or 'unpaired'})",
            UNPAIRED_ASSENT_REMEDY,
        )
    if turn.paired and turn.low_information and (
        not turn.proposal.text.strip() or is_low_information(turn.proposal.text)
    ):
        raise RequestRefusal(
            "ambiguous-antecedent",
            f"{what} {turn.capture_id} answers a proposal that states nothing",
            UNPAIRED_ASSENT_REMEDY,
        )


def confirmations(
    project_root: Path, candidates: Candidates, revoked: Set[str]
) -> Tuple[Optional[Confirmation], List[Confirmation]]:
    """``(active, revoked_confirmations)`` for the project.

    ``active`` is the one non-revoked confirmation, validated against the
    adapter's pair record. More than one is ``ambiguous-request``; a bound
    confirmation the adapter does not corroborate is
    ``confirmation-unreceipted``.
    """
    active: List[Confirmation] = []
    superseded: List[Confirmation] = []
    for binding in candidates.bindings:
        raw = binding.get("confirmation")
        if not isinstance(raw, dict):
            continue
        reply_id = raw.get("reply")
        turn = candidates.by_id(reply_id) if isinstance(reply_id, str) else None
        pair_id = raw.get("pair_id")
        if (
            turn is None
            or pair_id != f"{turn.handle}/pair-{turn.ordinal}"
            or raw.get("reply_turn_id") != turn.turn_id
            or (turn.proposal.turn_id if turn.proposal else None) != raw.get("proposal_turn_id")
            or (turn.proposal.capture_id if turn.proposal else None) != raw.get("proposal")
        ):
            raise RequestRefusal(
                "confirmation-unreceipted",
                f"{binding['binding_id']} binds a confirmation exchange the "
                "adapter's receipts do not corroborate",
                NOT_CAPTURED_REMEDY,
            )
        item = Confirmation(
            binding_id=binding["binding_id"],
            pair_id=pair_id,
            reply=turn,
            supersedes=raw.get("supersedes") if isinstance(raw.get("supersedes"), str) else None,
            bound_at=str(raw.get("bound_at") or ""),
        )
        if turn.capture_id in revoked:
            superseded.append(item)
        else:
            active.append(item)
    if len(active) > 1:
        raise RequestRefusal(
            "ambiguous-request",
            "more than one confirmation exchange governs project:project: "
            + ", ".join(c.reply.capture_id for c in active),
            "revoke the exchange that no longer governs (operator-only) and "
            "lock again after a fresh scope statement",
        )
    if active:
        _check_pair_usable(active[0].reply, what="confirmation reply")
    return (active[0] if active else None), superseded


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Reference:
    capture_id: str
    unit: GovernedUnit
    source: str
    quote: Optional[str] = None


@dataclass(frozen=True)
class Unconfirmed:
    reference: str
    source: str
    reason: str
    detail: str
    unit: Optional[GovernedUnit] = None

    def as_record(self) -> Dict[str, Any]:
        return {
            "reference": self.reference,
            "source": self.source,
            "reason": self.reason,
            "detail": self.detail,
            "unit": self.unit.as_record() if self.unit else None,
        }


def _section(text: str, heading: str) -> str:
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == heading]
    if len(starts) != 1:
        return ""
    start = starts[0] + 1
    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    return "".join(lines[start:end])


def text_references(text: str, unit: GovernedUnit, source: str) -> List[Reference]:
    """Capture identities named in an artifact's evidence sections."""
    body = "\n".join(_section(text, heading) for heading in EVIDENCE_SECTION_HEADINGS)
    seen: List[str] = []
    for match in CAPTURE_ID_TOKEN_RE.finditer(body):
        if match.group(1) not in seen:
            seen.append(match.group(1))
    return [Reference(cid, unit, source) for cid in seen]


def _quote_after(lines: Sequence[str], index: int) -> Tuple[Optional[str], int]:
    """A Markdown block quote directly under ``index``, whole, or ``None``."""
    i = index + 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or not lines[i].startswith(">"):
        return None, index + 1
    quoted: List[str] = []
    while i < len(lines):
        if lines[i].startswith(">"):
            line = lines[i][1:]
            if line.startswith(" "):
                line = line[1:]
            quoted.append(line)
            i += 1
            continue
        if not lines[i].strip():
            j = i
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and lines[j].startswith(">"):
                quoted.extend("" for _ in range(j - i))
                i = j
                continue
        break
    return "\n".join(quoted), i


def decision_references(project_root: Path) -> Tuple[List[Reference], List[Unconfirmed]]:
    """Structural capture references from every decision, plus legacy quotes.

    Legacy ``Operator request quote for:`` markers stay readable (their shape
    is still validated so a malformed marker fails closed) but are reported
    as unconfirmed: a quotation carries no receipt.
    """
    from cli import request_trace  # local: request_trace imports this module lazily

    decisions_dir = Path(project_root) / "decisions"
    refs: List[Reference] = []
    unconfirmed: List[Unconfirmed] = []
    if not decisions_dir.is_dir():
        return refs, unconfirmed
    grouped: Dict[str, List[Path]] = {}
    for path in sorted(decisions_dir.glob("DEC-*.md")):
        match = re.fullmatch(r"(DEC-\d{3})(?:-.*)?\.md", path.name)
        if match:
            grouped.setdefault(match.group(1), []).append(path)
    for decision_id, paths in sorted(grouped.items()):
        for path in paths:
            text = read_contained_text(project_root, path, what="decision request source")
            lines = text.splitlines()
            i = 0
            while i < len(lines):
                line = lines[i]
                if line.strip().lower().startswith(DECISION_EVIDENCE_MARKER.lower()):
                    match = DECISION_EVIDENCE_MARKER_RE.fullmatch(line.strip())
                    if match is None:
                        raise RequestRefusal(
                            "malformed-decision-evidence-marker",
                            f"{decision_id} has an invalid operator-evidence marker; "
                            f"expected `{DECISION_EVIDENCE_MARKER} <unit>: <capture-id>[, ...]`",
                        )
                    unit = GovernedUnit(*match.group(1).split(":", 1))
                    ids = [item.strip() for item in match.group(2).split(",")]
                    quote, i = _quote_after(lines, i)
                    for cid in ids:
                        if not CAPTURE_ID_RE.fullmatch(cid):
                            raise RequestRefusal(
                                "malformed-decision-evidence-marker",
                                f"{decision_id} names `{cid}`, which is not a capture identity",
                            )
                        refs.append(Reference(cid, unit, decision_id, quote))
                    continue
                i += 1
            # Legacy quotations: shape-checked, never confirmed.
            for quote_index, (unit, excerpt) in enumerate(
                request_trace._structural_decision_quotes(decision_id, text), start=1  # noqa: SLF001
            ):
                unconfirmed.append(Unconfirmed(
                    reference=f"{decision_id}-QUOTE-{quote_index:03d}",
                    source=decision_id,
                    reason="legacy-quotation",
                    detail=(
                        "a block quote is not a capture; reference the captured "
                        f"turn with `{DECISION_EVIDENCE_MARKER} {unit.kind}:{unit.identifier}: <capture-id>`"
                    ),
                    unit=unit,
                ))
            for quote_index, _excerpt in enumerate(
                request_trace._legacy_decision_quotes(decision_id, text), start=1  # noqa: SLF001
            ):
                unconfirmed.append(Unconfirmed(
                    reference=f"{decision_id}-LEGACY-{quote_index:03d}",
                    source=decision_id,
                    reason="legacy-quotation",
                    detail="historical attribution wording is readable but carries no receipt",
                ))
    return refs, unconfirmed


def chat_records_unconfirmed(project_root: Path) -> List[Unconfirmed]:
    """Everything under ``requests/chat/`` is unconfirmed: nothing writes it."""
    base = Path(project_root) / REQUESTS_DIRNAME / HOST_CHAT_DIRNAME
    if not base.is_dir():
        return []
    return [
        Unconfirmed(
            reference=path.stem,
            source=path.relative_to(project_root).as_posix(),
            reason="no-adapter-receipt",
            detail="host chat records are not produced by any supported intake",
        )
        for path in sorted(base.glob("*.json"))
    ]


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _confirm_references(
    refs: Sequence[Reference], candidates: Candidates, revoked: Set[str]
) -> Tuple[List[Tuple[Reference, CapturedTurn]], List[Unconfirmed]]:
    confirmed: List[Tuple[Reference, CapturedTurn]] = []
    unconfirmed: List[Unconfirmed] = []
    units_by_id: Dict[str, Set[GovernedUnit]] = {}
    for ref in refs:
        units_by_id.setdefault(ref.capture_id, set()).add(ref.unit)

    def reject(ref: Reference, reason: str, detail: str) -> None:
        unconfirmed.append(Unconfirmed(ref.capture_id, ref.source, reason, detail, ref.unit))

    for ref in refs:
        if len(units_by_id[ref.capture_id]) > 1:
            reject(ref, "cross-unit", "the same captured turn is referenced under more than one governed unit")
            continue
        turn = candidates.by_id(ref.capture_id)
        if turn is None:
            match = CAPTURE_ID_RE.fullmatch(ref.capture_id)
            if match and candidates.evicted_ordinal(match.group(1), int(match.group(2))):
                reject(ref, "evicted", "the turn was evicted from the preselection buffer before the project was selected; ask the operator to restate it")
            else:
                reject(ref, "not-captured", "no receipted binding of this project contains the turn")
            continue
        if ref.capture_id in revoked:
            reject(ref, "revoked", "the identity was revoked; a fresh operator statement supersedes it")
            continue
        if turn.inconsistent:
            raise RequestRefusal(
                "inconsistent-pair",
                f"{ref.source} references {turn.capture_id}, which answers a proposal recorded with differing text",
                INCONSISTENT_PAIR_REMEDY,
            )
        if turn.low_information and not turn.paired:
            reject(ref, "unpaired-assent", f"a low-information response whose proposal was not captured ({turn.unpaired_reason or 'unpaired'})")
            continue
        if ref.quote is not None and _normalized(ref.quote) != _normalized(turn.text):
            reject(ref, "partial-quotation", "the block quote under the marker does not equal the captured text whole")
            continue
        confirmed.append((ref, turn))
    return confirmed, unconfirmed


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
@dataclass
class Resolution:
    target: GovernedUnit
    evidence: List[RequestEvidence]
    unconfirmed: List[Unconfirmed]
    confirmation: Optional[Confirmation]
    candidates_total: int
    sessions: int
    evictions: List[Eviction]
    unreceipted_bindings: List[str]

    @property
    def omitted(self) -> int:
        return self.candidates_total - len(self.evidence)

    def as_record(self) -> Dict[str, Any]:
        return {
            "target": self.target.as_record(),
            "evidence": [e.record_id for e in self.evidence],
            "confirmation": self.confirmation.as_record() if self.confirmation else None,
            "unconfirmed": [u.as_record() for u in self.unconfirmed],
            "candidates": self.candidates_total,
            "omitted": self.omitted,
            "sessions": self.sessions,
            "evictions": [e.as_record() for e in self.evictions],
            "unreceipted_bindings": list(self.unreceipted_bindings),
        }


def _evidence(turn: CapturedTurn, unit: GovernedUnit, kind: str, index: int) -> RequestEvidence:
    return RequestEvidence(
        record_id=turn.capture_id,
        kind=kind,
        unit=unit,
        identity=turn.identity,
        text=turn.text,
        sequence=index,
        source_sequence=turn.ordinal,
        source_kind=SOURCE_KIND,
        source_identity=turn.source_identity,
        source_path=turn.source_path,
        source_content_identity=turn.source_content_identity,
        observed_at=turn.received,
        context=turn.context(),
    )


def presentation_key(
    evidence: RequestEvidence, latest_distinct: Optional[str]
) -> Tuple[str, Optional[str], Optional[str]]:
    """Two records are one presentation only when the words, the proposal they
    answered, and their position relative to the latest correction all match.

    ``latest_distinct`` is the content identity of the most recent preceding
    record that says something else: the correction the repetition follows.
    Identical words repeated with nothing corrected in between collapse;
    the same words said again after an intervening correction are kept.
    """
    return (
        evidence.identity,
        evidence.context.identity if evidence.context is not None else None,
        latest_distinct,
    )


def dedupe_by_presentation(items: Iterable[RequestEvidence]) -> List[RequestEvidence]:
    seen: Set[Tuple[str, Optional[str], Optional[str]]] = set()
    unique: List[RequestEvidence] = []
    prev_identity: Optional[str] = None
    prev_distinct: Optional[str] = None
    for item in items:
        distinct = prev_identity if prev_identity != item.identity else prev_distinct
        key = presentation_key(item, distinct)
        if key not in seen:
            seen.add(key)
            unique.append(item)
        prev_identity, prev_distinct = item.identity, distinct
    return unique


def resolve(
    project_root: Path,
    target: GovernedUnit,
    source_texts: Sequence[str] = (),
    *,
    allow_project_origin: bool = False,
    store: Optional[intake_adapter.SessionStore] = None,
) -> Resolution:
    """Applicable captured evidence for ``target`` in presentation order.

    Applicable = the confirmation exchange (for ``project:project``, and for
    any unit consuming project origin) plus every confirmed reference bound to
    the target unit (or to ``project:project`` when project origin applies).
    Referenced turns after the confirmation are corrections; everything is
    ordered by binding order then receipt ordinal, and deduplicated by
    presentation. Unreferenced turns are omitted and only counted.
    """
    project_root = Path(project_root)
    candidates = load_candidates(project_root, store)
    revoked = revoked_evidence_ids(project_root)
    confirmation, _superseded = confirmations(project_root, candidates, revoked)

    refs, unconfirmed = decision_references(project_root)
    text_unit = target if target.kind == "task" else PROJECT_UNIT
    for text in source_texts:
        refs.extend(text_references(text, text_unit, "artifact"))
    confirmed, rejected = _confirm_references(refs, candidates, revoked)
    unconfirmed.extend(rejected)
    unconfirmed.extend(chat_records_unconfirmed(project_root))

    def applies(unit: GovernedUnit) -> bool:
        return unit == target or (allow_project_origin and unit == PROJECT_UNIT)

    selected: Dict[str, Tuple[CapturedTurn, GovernedUnit]] = {}
    if confirmation is not None and applies(PROJECT_UNIT):
        selected[confirmation.reply.capture_id] = (confirmation.reply, PROJECT_UNIT)
    for ref, turn in confirmed:
        if applies(ref.unit) and turn.capture_id not in selected:
            selected[turn.capture_id] = (turn, ref.unit)

    ordered = sorted(selected.values(), key=lambda item: item[0].position)
    confirmation_selected = confirmation is not None and confirmation.reply.capture_id in selected
    evidence: List[RequestEvidence] = []
    for index, (turn, unit) in enumerate(ordered, start=1):
        if confirmation_selected and turn.capture_id == confirmation.reply.capture_id:
            kind = "confirmation"
        elif confirmation_selected and turn.position > confirmation.reply.position:
            kind = "correction"
        elif not confirmation_selected and index > 1:
            # No confirmation in this unit's trace: its first referenced
            # turn is the instruction, later ones correct it.
            kind = "correction"
        else:
            kind = "instruction"
        evidence.append(_evidence(turn, unit, kind, index))
    evidence = dedupe_by_presentation(evidence)
    return Resolution(
        target=target,
        evidence=evidence,
        unconfirmed=unconfirmed,
        confirmation=confirmation,
        candidates_total=len(candidates.turns),
        sessions=candidates.sessions,
        evictions=list(candidates.evictions),
        unreceipted_bindings=list(candidates.unreceipted_bindings),
    )


# ---------------------------------------------------------------------------
# Lock-time confirmation binding (requirements / plan writers)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PendingConfirmation:
    binding_id: str
    reply: CapturedTurn
    supersedes: Optional[str]


@dataclass(frozen=True)
class LockEvidence:
    """What a requirements/plan writer holds before it writes.

    ``existing`` is the confirmation already bound to the project; ``pending``
    is the pair this lock will bind. Exactly one is set. ``None`` from
    :func:`prepare_confirmation` means the project has no receipted binding
    at all, so the legacy record gate applies instead.
    """

    existing: Optional[Confirmation] = None
    pending: Optional[PendingConfirmation] = None


def _choose_binding(candidates: Candidates, handle: Optional[str]) -> Dict[str, Any]:
    bindings = candidates.bindings
    if handle is not None:
        own = [b for b in bindings if b["handle"] == handle]
        if not own:
            raise RequestRefusal(
                "session-unbound",
                f"no receipted binding of this project routes from handle {handle}",
                "run `select_project` with the handle this session's routing line printed",
            )
        active = [b for b in own if b.get("to_ordinal") is None]
        return (active or own)[-1]
    active = [b for b in bindings if b.get("to_ordinal") is None]
    if len(active) == 1:
        return active[0]
    if not active:
        raise RequestRefusal(
            "session-unbound",
            "no capture session is currently bound to this project",
            "run `select_project` with this session's `cartopian-session:` handle, then lock again",
        )
    # Planning may span sessions. A session that has ended cannot be the one
    # confirming now; when exactly one bound session is still open, it is.
    live = [b for b in active if not candidates.session_ended.get(b["binding_id"])]
    if len(live) == 1:
        return live[0]
    raise RequestRefusal(
        "ambiguous-session",
        "more than one capture session is bound to this project: "
        + ", ".join(b["handle"] for b in (live or active)),
        "pass --handle naming the session in which the operator confirmed the intent summary",
    )


def prepare_confirmation(
    project_root: Path,
    handle: Optional[str] = None,
    *,
    store: Optional[intake_adapter.SessionStore] = None,
) -> Optional[LockEvidence]:
    """Resolve the exchange a requirements/plan lock binds, before writing.

    The confirmation is the operator's latest captured reply in the chosen
    binding: the compact intent summary is the proposal, the reply is the
    confirming or correcting words. Nothing is asked again. Returns ``None``
    when the project has no receipted binding (legacy record intake applies).
    """
    project_root = Path(project_root)
    candidates = load_candidates(project_root, store)
    if not candidates.bindings:
        return None
    revoked = revoked_evidence_ids(project_root)
    existing, superseded = confirmations(project_root, candidates, revoked)
    if existing is not None:
        return LockEvidence(existing=existing)
    binding = _choose_binding(candidates, handle)
    turns = [t for t in candidates.turns if t.binding_id == binding["binding_id"]]
    if not turns:
        raise RequestRefusal(
            "unit-request-not-captured",
            f"binding {binding['binding_id']} holds no captured operator turn to confirm against",
            NOT_CAPTURED_REMEDY,
        )
    reply = turns[-1]
    if reply.capture_id in revoked:
        raise RequestRefusal(
            "revoked-evidence",
            f"the latest captured reply {reply.capture_id} was revoked",
            "ask the operator for a fresh scope statement in their session, then lock again",
        )
    _check_pair_usable(reply, what="confirmation reply")
    supersedes = superseded[-1].reply.capture_id if superseded else None
    return LockEvidence(pending=PendingConfirmation(binding["binding_id"], reply, supersedes))


def bind_confirmation(
    project_root: Path,
    pending: PendingConfirmation,
    *,
    store: Optional[intake_adapter.SessionStore] = None,
) -> Dict[str, Any]:
    """Persist the confirmation into the project binding and the session record."""
    project_root = Path(project_root)
    store = store or intake_store()
    when = _now()
    record = confirmation_record(pending.reply, supersedes=pending.supersedes, when=when)
    bindings = read_bindings(project_root)
    hit = False
    for binding in bindings:
        if binding.get("binding_id") == pending.binding_id:
            binding["confirmation"] = record
            hit = True
    if not hit:
        raise RequestRefusal(
            "session-unbound",
            f"binding {pending.binding_id} disappeared before the confirmation could be bound",
            "run `select_project` again, then lock again",
        )
    write_bindings(project_root, bindings)
    session = store.load_session(pending.reply.host, pending.reply.session_id)
    if session is not None:
        for binding in session.get("bindings") or []:
            if isinstance(binding, dict) and binding.get("binding_id") == pending.binding_id:
                binding["confirmation"] = dict(record)
        store.save_session(session)
    return record
