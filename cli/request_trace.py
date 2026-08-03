"""Deterministic request evidence for assignment, planning, and closure.

Exact operator excerpts may come from immutable request records, supported
host chat records, or explicitly unit-bound decision quotations.  Every
excerpt retains its source, governed unit, deterministic order, and SHA-256
content identity.  Ordinary PM-authored prose is never promoted into this
channel; it remains in the separate management/delivery projection.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from cli import report_identity


REQUESTS_DIRNAME = "requests"
HOST_CHAT_DIRNAME = "chat"
REQUEST_ID_RE = re.compile(r"^REQUEST-\d{3}$")
CORRECTION_ID_RE = re.compile(r"^(REQUEST-\d{3})-CORRECTION-(\d{3})$")
CHAT_RECORD_ID_RE = re.compile(r"^CHAT-[A-Z0-9][A-Z0-9-]{1,79}$")
PHASE_ID_RE = re.compile(r"^PHASE-\d{2}(?:-[a-z0-9][a-z0-9-]*)?$")
PLAN_REF_RE = re.compile(
    r"^(?:BUILD|DESIGN|RESEARCH|TEST|RELEASE|VERIFY|CORRECTIVE)-\d{2}-\d{3}$"
)
CHECKPOINT_ID_RE = re.compile(r"^PLAN-\d{3}(?:-[a-z0-9][a-z0-9-]*)?$")
REVIEW_KINDS = ("planning", "task-closure")
UNIT_KINDS = ("project", "planning", "task")
REQUEST_SECTION_HEADING = "## Original operator request (verbatim)"
MANAGEMENT_SECTION_HEADING = "## PM-derived guidance and delivered outcome"
# Split-report contract: the review prompt binds the *preserved* completion
# report by path and content identity; the reviewer reads the artifact
# directly and the prompt never reproduces the report body.
PRESERVED_COMPLETION_HEADING = "## Preserved coder completion evidence"
# Retired shared-slot contract: prompts generated before the split embedded a
# block-quoted snapshot of the coder report under this heading because the
# reviewer reused the coder's report slot. Such a binding is refused
# deterministically — regenerate the prompt under the split-report contract.
CAPTURED_COMPLETION_HEADING = "## Captured coder completion evidence"
CAPTURED_REPORT_MARKER = "Captured report:"
LEGACY_STATE = "unavailable-for-legacy"
ALIGNMENT_VALUES = ("aligned", "drifted", LEGACY_STATE)
ALIGNMENT_FIELD = "Request alignment"
ALIGNMENT_EVIDENCE_FIELD = "Request evidence"
REQUEST_CAPTURE_FROM = (0, 9, 0)
MAX_REQUEST_BYTES = 24 * 1024
MAX_COMPLETION_EVIDENCE_BYTES = 256 * 1024
DECISION_QUOTE_MARKER = "Operator request quote for:"
DECISION_QUOTE_MARKER_RE = re.compile(
    r"^Operator request quote for:\s*"
    r"(project:project|planning:PLAN-\d{3}(?:-[a-z0-9][a-z0-9-]*)?|"
    r"task:TASK-\d{2}-\d{3})$"
)
LEGACY_DECISION_ATTRIBUTIONS = (
    "The operator clarified the governing intent in these exact words:",
    (
        "The first independent review of the corrective request-trace "
        "implementation proposed restoring or adding an up-front safeguard as "
        "part of resolving request alignment. The operator rejected that framing "
        "and clarified the intended process in these exact words:"
    ),
    (
        "The second independent review treated a native host callback as the "
        "only acceptable way to preserve operator request evidence. The operator "
        "corrected that assumption:"
    ),
)


class RequestRefusal(Exception):
    def __init__(self, rule: str, detail: str, recovery: str = "") -> None:
        self.rule = rule
        self.detail = detail
        self.recovery = recovery
        super().__init__(f"{rule}: {detail}")

    def as_record(self) -> Dict[str, str]:
        return {"rule": self.rule, "detail": self.detail, "recovery": self.recovery}


def content_identity(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _version(value: Optional[str]) -> Tuple[int, int, int]:
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", (value or "").strip())
    return tuple(map(int, match.groups())) if match else (0, 0, 0)


def request_capture_enforced(project_schema_version: Optional[str]) -> bool:
    return _version(project_schema_version) >= REQUEST_CAPTURE_FROM


def project_schema_version(project_root: Path) -> Optional[str]:
    try:
        with (Path(project_root) / "cartopian.toml").open("rb") as handle:
            return (tomllib.load(handle).get("project", {}) or {}).get(
                "project_schema_version"
            )
    except (OSError, tomllib.TOMLDecodeError):
        return None


def find_project_root(anchor: Path) -> Optional[Path]:
    start = anchor if anchor.is_dir() else anchor.parent
    for candidate in (start, *start.parents):
        if (candidate / "cartopian.toml").is_file():
            return candidate
    return None


def _within(child: str, parent: str) -> bool:
    child = os.path.normcase(os.path.normpath(child))
    parent = os.path.normcase(os.path.normpath(parent))
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def read_contained_text(project_root: Path, path: Path, *, what: str) -> str:
    root = os.path.realpath(os.fspath(project_root))
    supplied = os.path.abspath(os.fspath(path))
    resolved = os.path.realpath(supplied)
    if not _within(resolved, root):
        raise RequestRefusal("outside-project", f"{what} escapes the project")
    if os.path.islink(supplied):
        raise RequestRefusal("unsafe-file", f"{what} is a symlink")
    try:
        info = os.lstat(supplied)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
            raise RequestRefusal("unsafe-file", f"{what} is not a single-link file")
        with Path(supplied).open("r", encoding="utf-8", newline="") as handle:
            return handle.read()
    except UnicodeDecodeError:
        raise RequestRefusal("invalid-utf8", f"{what} is not valid UTF-8")
    except OSError as exc:
        raise RequestRefusal("unreadable", f"cannot read {what}: {exc}")


@dataclass(frozen=True)
class GovernedUnit:
    kind: str
    identifier: str

    def as_record(self) -> Dict[str, str]:
        return {"kind": self.kind, "id": self.identifier}


@dataclass(frozen=True)
class RequestRecord:
    record_id: str
    request_id: str
    kind: str
    unit: GovernedUnit
    captured_at: str
    identity: str
    text: str
    sequence: int
    path: str

    def as_record(self, *, include_text: bool = True) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "record_id": self.record_id,
            "request_id": self.request_id,
            "kind": self.kind,
            "unit": self.unit.as_record(),
            "captured_at": self.captured_at,
            "content_identity": self.identity,
            "sequence": self.sequence,
            "path": self.path,
        }
        if include_text:
            result["text"] = self.text
        return result


@dataclass(frozen=True)
class RequestEvidence:
    """One exact excerpt selected for a governed review unit."""

    record_id: str
    kind: str
    unit: GovernedUnit
    identity: str
    text: str
    sequence: int
    source_sequence: int
    source_kind: str
    source_identity: str
    source_path: str
    source_content_identity: str
    observed_at: str = ""

    def as_record(self, *, include_text: bool = True) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "record_id": self.record_id,
            "kind": self.kind,
            "unit": self.unit.as_record(),
            "content_identity": self.identity,
            "sequence": self.sequence,
            "source": {
                "kind": self.source_kind,
                "identity": self.source_identity,
                "path": self.source_path,
                "content_identity": self.source_content_identity,
                "sequence": self.source_sequence,
            },
        }
        if self.observed_at:
            result["observed_at"] = self.observed_at
        if include_text:
            result["text"] = self.text
        return result


def _record_from_json(path: Path, project_root: Path) -> RequestRecord:
    text = read_contained_text(project_root, path, what="request record")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RequestRefusal("malformed-request", f"{path.name}: {exc.msg}")
    if data.get("schema") != "cartopian-original-request-v1":
        raise RequestRefusal("malformed-request", f"{path.name}: unknown schema")
    record_id = data.get("record_id")
    request_id = data.get("request_id")
    kind = data.get("kind")
    unit_data = data.get("unit")
    captured_at = data.get("captured_at")
    identity = data.get("content_identity")
    request_text = data.get("text")
    sequence = data.get("sequence")
    if not all(isinstance(value, str) for value in (
        record_id, request_id, kind, captured_at, identity, request_text
    )) or not isinstance(unit_data, dict) or not isinstance(sequence, int):
        raise RequestRefusal("malformed-request", f"{path.name}: missing typed fields")
    unit = GovernedUnit(str(unit_data.get("kind", "")), str(unit_data.get("id", "")))
    expected_name = record_id + ".json"
    if path.name != expected_name or not REQUEST_ID_RE.fullmatch(request_id):
        raise RequestRefusal("malformed-request", f"{path.name}: identity mismatch")
    if kind == "original":
        if record_id != request_id or sequence != 0:
            raise RequestRefusal("malformed-request", f"{path.name}: invalid original ordering")
    elif kind == "correction":
        match = CORRECTION_ID_RE.fullmatch(record_id)
        if match is None or match.group(1) != request_id or int(match.group(2)) != sequence:
            raise RequestRefusal("malformed-request", f"{path.name}: invalid correction link")
    else:
        raise RequestRefusal("malformed-request", f"{path.name}: invalid kind")
    raw = request_text.encode("utf-8")
    if len(raw) > MAX_REQUEST_BYTES or content_identity(raw) != identity:
        raise RequestRefusal("changed-request", f"{path.name}: text identity does not match")
    if unit.kind not in UNIT_KINDS or not unit.identifier:
        raise RequestRefusal("malformed-request", f"{path.name}: invalid governed unit")
    relpath = path.relative_to(project_root).as_posix()
    return RequestRecord(record_id, request_id, kind, unit, captured_at, identity,
                         request_text, sequence, relpath)


def load_records(project_root: Path) -> List[RequestRecord]:
    base = Path(project_root) / REQUESTS_DIRNAME
    if not base.exists():
        return []
    if base.is_symlink() or not base.is_dir():
        raise RequestRefusal("unsafe-request-store", "requests/ is not a real directory")
    records = [_record_from_json(path, Path(project_root)) for path in sorted(base.glob("*.json"))]
    ids = [record.record_id for record in records]
    if len(ids) != len(set(ids)):
        raise RequestRefusal("ambiguous-request", "duplicate request record identity")
    originals = {record.request_id for record in records if record.kind == "original"}
    for record in records:
        if record.kind == "correction" and record.request_id not in originals:
            raise RequestRefusal("orphan-correction", f"{record.record_id} has no original request")
    for request_id in sorted(originals):
        identities = [
            record.identity for record in records if record.request_id == request_id
        ]
        if len(identities) != len(set(identities)):
            raise RequestRefusal(
                "duplicate-request-content",
                f"{request_id} contains repeated content identity",
            )
    return records


def _record_evidence(project_root: Path, record: RequestRecord) -> RequestEvidence:
    source_text = read_contained_text(
        project_root,
        Path(project_root) / record.path,
        what="request record",
    )
    return RequestEvidence(
        record_id=record.record_id,
        kind=record.kind,
        unit=record.unit,
        identity=record.identity,
        text=record.text,
        sequence=record.sequence,
        source_sequence=record.sequence,
        source_kind="request-record",
        source_identity=record.record_id,
        source_path=record.path,
        source_content_identity=content_identity(source_text.encode("utf-8")),
        observed_at=record.captured_at,
    )


def _host_chat_record(path: Path, project_root: Path) -> RequestEvidence:
    source_text = read_contained_text(project_root, path, what="host chat record")
    try:
        data = json.loads(source_text)
    except json.JSONDecodeError as exc:
        raise RequestRefusal("malformed-chat-record", f"{path.name}: {exc.msg}")
    if data.get("schema") != "cartopian-host-chat-v1":
        raise RequestRefusal("malformed-chat-record", f"{path.name}: unknown schema")
    record_id = data.get("record_id")
    role = data.get("role")
    kind = data.get("kind")
    sequence = data.get("sequence")
    unit_data = data.get("unit")
    excerpt = data.get("text")
    identity = data.get("content_identity")
    source = data.get("source")
    observed_at = data.get("observed_at", "")
    if (
        not isinstance(record_id, str)
        or CHAT_RECORD_ID_RE.fullmatch(record_id) is None
        or path.name != record_id + ".json"
        or role != "operator"
        or kind not in ("original", "correction")
        or not isinstance(sequence, int)
        or sequence < 0
        or not isinstance(unit_data, dict)
        or not isinstance(excerpt, str)
        or not isinstance(identity, str)
        or not isinstance(source, dict)
        or not isinstance(observed_at, str)
    ):
        raise RequestRefusal("malformed-chat-record", f"{path.name}: missing typed fields")
    if (kind == "original") != (sequence == 0):
        raise RequestRefusal("malformed-chat-record", f"{path.name}: invalid ordering")
    unit = GovernedUnit(str(unit_data.get("kind", "")), str(unit_data.get("id", "")))
    if unit.kind not in UNIT_KINDS or not unit.identifier:
        raise RequestRefusal("malformed-chat-record", f"{path.name}: invalid governed unit")
    host = source.get("host")
    conversation = source.get("conversation_id")
    message = source.get("message_id")
    if not all(isinstance(value, str) and value.strip() for value in (host, conversation, message)):
        raise RequestRefusal("malformed-chat-record", f"{path.name}: invalid source provenance")
    raw = excerpt.encode("utf-8")
    if len(raw) > MAX_REQUEST_BYTES or content_identity(raw) != identity:
        raise RequestRefusal("changed-chat-record", f"{path.name}: text identity does not match")
    return RequestEvidence(
        record_id=record_id,
        kind=kind,
        unit=unit,
        identity=identity,
        text=excerpt,
        sequence=sequence,
        source_sequence=sequence,
        source_kind="host-chat",
        source_identity=f"{host}:{conversation}:{message}",
        source_path=path.relative_to(project_root).as_posix(),
        source_content_identity=content_identity(source_text.encode("utf-8")),
        observed_at=observed_at,
    )


def load_host_chat_records(project_root: Path) -> List[RequestEvidence]:
    base = Path(project_root) / REQUESTS_DIRNAME / HOST_CHAT_DIRNAME
    if not base.exists():
        return []
    if base.is_symlink() or not base.is_dir():
        raise RequestRefusal("unsafe-chat-store", "requests/chat/ is not a real directory")
    records = [
        _host_chat_record(path, Path(project_root))
        for path in sorted(base.glob("*.json"))
    ]
    ids = [record.record_id for record in records]
    sources = [record.source_identity for record in records]
    if len(ids) != len(set(ids)) or len(sources) != len(set(sources)):
        raise RequestRefusal("ambiguous-chat-record", "host chat identities are not unique")
    for unit in sorted({record.unit for record in records}, key=lambda item: (item.kind, item.identifier)):
        unit_records = [record for record in records if record.unit == unit]
        originals = [record for record in unit_records if record.kind == "original"]
        if len(originals) > 1:
            raise RequestRefusal(
                "ambiguous-chat-record",
                f"more than one host chat turn initiates {unit.kind}:{unit.identifier}",
            )
        if unit_records and not originals:
            raise RequestRefusal(
                "orphan-chat-correction",
                f"host chat corrections for {unit.kind}:{unit.identifier} have no initiating turn",
            )
        sequences = sorted(record.sequence for record in unit_records)
        if sequences != list(range(len(unit_records))):
            raise RequestRefusal(
                "chat-correction-gap",
                f"host chat turns for {unit.kind}:{unit.identifier} are not contiguous",
            )
        identities = [record.identity for record in unit_records]
        if len(identities) != len(set(identities)):
            raise RequestRefusal(
                "duplicate-request-content",
                f"host chat turns for {unit.kind}:{unit.identifier} repeat content",
            )
    return records


def _section(text: str, heading: str) -> str:
    lines = text.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == heading]
    if len(starts) != 1:
        return ""
    start = starts[0] + 1
    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    return "".join(lines[start:end])


def _explicit_decision_refs(text: str) -> List[str]:
    """Return only decision refs explicitly classified as operator evidence."""
    evidence_text = "\n".join(
        _section(text, heading)
        for heading in (
            "## Operator intent",
            "## Original request evidence",
            "## Request evidence",
        )
    )
    marker_lines = "\n".join(
        line
        for line in text.splitlines()
        if re.match(r"^(?:Exact )?Operator (?:quote|excerpt) source:\s*", line, re.IGNORECASE)
    )
    return sorted(set(re.findall(r"\bDEC-\d{3}\b", evidence_text + "\n" + marker_lines)))


def _decision_path(project_root: Path, decision_id: str) -> Path:
    matches = sorted((Path(project_root) / "decisions").glob(f"{decision_id}-*.md"))
    exact = Path(project_root) / "decisions" / f"{decision_id}.md"
    if exact.is_file():
        matches.insert(0, exact)
    unique = list(dict.fromkeys(matches))
    if len(unique) != 1:
        detail = "does not resolve" if not unique else "is ambiguous"
        raise RequestRefusal("unresolved-request-source", f"{decision_id} {detail}")
    return unique[0]


def _quote_after_marker(
    lines: Sequence[str], marker_index: int, decision_id: str
) -> Tuple[str, int]:
    index = marker_index + 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines) or not lines[index].startswith(">"):
        raise RequestRefusal(
            "malformed-decision-quote-marker",
            f"{decision_id} marker is not followed by one Markdown block quote",
        )
    quoted: List[str] = []
    while index < len(lines):
        if lines[index].startswith(">"):
            line = lines[index][1:]
            if line.startswith(" "):
                line = line[1:]
            quoted.append(line)
            index += 1
            continue
        if not lines[index].strip():
            next_quote = index
            while next_quote < len(lines) and not lines[next_quote].strip():
                next_quote += 1
            if next_quote < len(lines) and lines[next_quote].startswith(">"):
                quoted.extend("" for _ in range(next_quote - index))
                index = next_quote
                continue
        break
    excerpt = "\n".join(quoted)
    if not excerpt:
        raise RequestRefusal(
            "malformed-decision-quote-marker",
            f"{decision_id} marker has an empty quotation",
        )
    return excerpt, index


def _marker_unit(raw: str) -> GovernedUnit:
    kind, identifier = raw.split(":", 1)
    return GovernedUnit(kind, identifier)


def _structural_decision_quotes(
    decision_id: str, text: str
) -> List[Tuple[GovernedUnit, str]]:
    """Read only exact, unit-bearing quote markers from a decision."""
    lines = text.splitlines()
    results: List[Tuple[GovernedUnit, str]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.strip().lower().startswith(DECISION_QUOTE_MARKER.lower()):
            match = DECISION_QUOTE_MARKER_RE.fullmatch(line)
            if match is None:
                raise RequestRefusal(
                    "malformed-decision-quote-marker",
                    f"{decision_id} has an invalid operator-quote unit marker",
                )
            excerpt, index = _quote_after_marker(lines, index, decision_id)
            results.append((_marker_unit(match.group(1)), excerpt))
            continue
        index += 1
    bindings: Dict[str, GovernedUnit] = {}
    for unit, excerpt in results:
        identity = content_identity(excerpt.encode("utf-8"))
        prior = bindings.get(identity)
        if prior is not None and prior != unit:
            raise RequestRefusal(
                "ambiguous-decision-quote-marker",
                f"{decision_id} binds the same quotation to more than one governed unit",
            )
        bindings[identity] = unit
    return results


def _legacy_decision_quotes(decision_id: str, text: str) -> List[str]:
    """Read only the three exact historical attribution sentences."""
    lines = text.splitlines()
    results: List[str] = []
    for index, line in enumerate(lines):
        if line.strip() not in LEGACY_DECISION_ATTRIBUTIONS:
            continue
        try:
            excerpt, _ = _quote_after_marker(lines, index, decision_id)
        except RequestRefusal:
            continue
        # These historical forms used matching punctuation around the quoted
        # text. Their exact attribution sentence makes this compatibility
        # transform bounded; structurally marked quotations stay lossless.
        if (
            len(excerpt) >= 2
            and excerpt[0] == excerpt[-1]
            and excerpt[0] in ('"', "'")
        ):
            excerpt = excerpt[1:-1]
        results.append(excerpt)
    return results


def _unit_applies(
    source: GovernedUnit,
    target: GovernedUnit,
    *,
    allow_project_origin: bool,
) -> bool:
    return source == target or (
        allow_project_origin and source == GovernedUnit("project", "project")
    )


def _decision_evidence(
    project_root: Path,
    unit: GovernedUnit,
    source_texts: Sequence[str],
    *,
    allow_project_origin: bool = False,
) -> List[RequestEvidence]:
    evidence: List[RequestEvidence] = []
    decisions_dir = Path(project_root) / "decisions"

    def append(
        decision_id: str,
        path: Path,
        decision_text: str,
        quote_index: int,
        excerpt: str,
        source_unit: GovernedUnit,
    ) -> None:
        raw = excerpt.encode("utf-8")
        if len(raw) > MAX_REQUEST_BYTES:
            raise RequestRefusal(
                "request-evidence-too-large",
                f"{decision_id} operator excerpt exceeds {MAX_REQUEST_BYTES} bytes",
            )
        evidence.append(RequestEvidence(
            record_id=f"{decision_id}-QUOTE-{quote_index:03d}",
            kind="source-excerpt",
            unit=source_unit,
            identity=content_identity(raw),
            text=excerpt,
            sequence=quote_index,
            source_sequence=quote_index,
            source_kind="decision",
            source_identity=decision_id,
            source_path=path.relative_to(project_root).as_posix(),
            source_content_identity=content_identity(decision_text.encode("utf-8")),
            observed_at=_header(decision_text, "Date") or "",
        ))

    # New evidence is self-selecting: the structural marker names its governed
    # unit, so a greenfield decision can establish evidence before requirements
    # or a plan exists to reference that decision.
    grouped: Dict[str, List[Path]] = {}
    if decisions_dir.is_dir():
        for path in sorted(decisions_dir.glob("DEC-*.md")):
            match = re.fullmatch(r"(DEC-\d{3})(?:-[a-z0-9][a-z0-9-]*)?\.md", path.name)
            if match:
                grouped.setdefault(match.group(1), []).append(path)
    structurally_marked: set[str] = set()
    for decision_id, paths in sorted(grouped.items()):
        parsed: List[Tuple[Path, str, List[Tuple[GovernedUnit, str]]]] = []
        for path in paths:
            decision_text = read_contained_text(
                project_root, path, what="decision request source"
            )
            quotes = _structural_decision_quotes(decision_id, decision_text)
            if quotes:
                parsed.append((path, decision_text, quotes))
        if not parsed:
            continue
        if len(paths) != 1:
            raise RequestRefusal(
                "unresolved-request-source", f"{decision_id} is ambiguous"
            )
        path, decision_text, quotes = parsed[0]
        structurally_marked.add(decision_id)
        for quote_index, (source_unit, excerpt) in enumerate(quotes, start=1):
            if _unit_applies(
                source_unit, unit, allow_project_origin=allow_project_origin
            ):
                append(
                    decision_id, path, decision_text, quote_index, excerpt, source_unit
                )

    # The three historical attribution sentences remain readable only when an
    # applicable artifact explicitly selects the decision. No general prose
    # heuristic is retained for other decisions.
    for decision_id in sorted({ref for text in source_texts for ref in _explicit_decision_refs(text)}):
        if decision_id in structurally_marked:
            continue
        path = _decision_path(project_root, decision_id)
        decision_text = read_contained_text(project_root, path, what="decision request source")
        for quote_index, excerpt in enumerate(
            _legacy_decision_quotes(decision_id, decision_text), start=1
        ):
            append(decision_id, path, decision_text, quote_index, excerpt, unit)
    return evidence


def _target_unit(review_kind: str, task_path: Optional[Path], checkpoint_id: Optional[str]) -> GovernedUnit:
    if review_kind in ("task-assignment", "task-closure"):
        if task_path is None:
            raise RequestRefusal("missing-review-target", "task review has no task")
        match = re.match(r"^(TASK-\d{2}-\d{3})", task_path.stem)
        if match is None:
            raise RequestRefusal("malformed-review-target", f"invalid task name: {task_path.name}")
        return GovernedUnit("task", match.group(1))
    if checkpoint_id is None or not CHECKPOINT_ID_RE.fullmatch(checkpoint_id):
        raise RequestRefusal("malformed-review-target", "planning review has no valid checkpoint")
    return GovernedUnit("planning", checkpoint_id)


def _select_record_trace(
    records: Sequence[RequestRecord],
    unit: GovernedUnit,
    *,
    allow_project_origin: bool = False,
) -> List[RequestRecord]:
    originals = [r for r in records if r.kind == "original" and r.unit == unit]
    if not originals and allow_project_origin:
        originals = [
            r for r in records
            if r.kind == "original" and r.unit == GovernedUnit("project", "project")
        ]
    if len(originals) > 1:
        raise RequestRefusal(
            "ambiguous-request",
            f"more than one initiating request governs {unit.kind}:{unit.identifier}",
            "capture one request per governed unit and express changes as corrections",
        )
    if not originals:
        return []
    original = originals[0]
    corrections = sorted(
        (r for r in records if r.kind == "correction" and r.request_id == original.request_id),
        key=lambda item: item.sequence,
    )
    if [item.sequence for item in corrections] != list(range(1, len(corrections) + 1)):
        raise RequestRefusal("correction-gap", f"corrections for {original.request_id} are not contiguous")
    return [original, *corrections]


def _select_chat_trace(
    records: Sequence[RequestEvidence],
    unit: GovernedUnit,
    *,
    allow_project_origin: bool = False,
) -> List[RequestEvidence]:
    selected_unit = unit
    originals = [record for record in records if record.kind == "original" and record.unit == unit]
    if not originals and allow_project_origin:
        selected_unit = GovernedUnit("project", "project")
        originals = [
            record for record in records
            if record.kind == "original" and record.unit == selected_unit
        ]
    if not originals:
        return []
    return sorted(
        (record for record in records if record.unit == selected_unit),
        key=lambda record: (record.sequence, record.record_id),
    )


def _source_texts(
    project_root: Path,
    review_kind: str,
    task_path: Optional[Path],
    *,
    phase_id: Optional[str] = None,
    checkpoint_text: Optional[str] = None,
) -> List[str]:
    texts: List[str] = []

    def add(path: Path, what: str) -> None:
        if path.is_file():
            texts.append(read_contained_text(project_root, path, what=what))

    if review_kind in ("task-assignment", "task-closure") and task_path is not None:
        add(Path(task_path), "task request-evidence selector")
        return texts
    add(Path(project_root) / "REQUIREMENTS.md", "requirements request-evidence selector")
    add(Path(project_root) / "IMPLEMENTATION_PLAN.md", "plan request-evidence selector")
    if phase_id:
        add(Path(project_root) / "phases" / f"{phase_id}.md", "phase request-evidence selector")
    if checkpoint_text:
        authored = checkpoint_text.split(REQUEST_SECTION_HEADING, 1)[0]
        texts.append(authored)
    return texts


def _resolve_trace(
    project_root: Path,
    target: GovernedUnit,
    source_texts: Sequence[str],
    *,
    allow_project_origin: bool = False,
) -> List[RequestEvidence]:
    stored = [
        _record_evidence(project_root, record)
        for record in _select_record_trace(
            load_records(project_root),
            target,
            allow_project_origin=allow_project_origin,
        )
    ]
    chat = _select_chat_trace(
        load_host_chat_records(project_root),
        target,
        allow_project_origin=allow_project_origin,
    )
    decisions = _decision_evidence(
        project_root,
        target,
        source_texts,
        allow_project_origin=allow_project_origin,
    )
    ordered = [*stored, *chat, *decisions]
    identities: set[str] = set()
    unique: List[RequestEvidence] = []
    for evidence in ordered:
        if evidence.identity in identities:
            continue
        identities.add(evidence.identity)
        unique.append(evidence)
    return [replace(evidence, sequence=index) for index, evidence in enumerate(unique, start=1)]


def _phase_from_text(text: str) -> Optional[str]:
    match = re.search(r"^Phase:\s*(PHASE-\d{2}(?:-[a-z0-9][a-z0-9-]*)?)\s*$", text, re.MULTILINE)
    return match.group(1) if match else None


def _bound_management_artifacts(project_root: Path, prompt_text: str) -> Optional[List[str]]:
    """Reuse a generated prompt's artifact snapshot during binding checks.

    Review and report files can appear after prompt generation.  They do not
    retroactively change the inventory handed to that reviewer; a later
    review-pass prompt regeneration takes a fresh existing-file snapshot.
    """
    if REQUEST_SECTION_HEADING not in prompt_text:
        return None
    lines = prompt_text.splitlines()
    try:
        start = lines.index(MANAGEMENT_SECTION_HEADING) + 1
    except ValueError:
        raise RequestRefusal(
            "stale-request-context",
            "generated request context has no PM-derived artifact channel",
        )
    artifacts: List[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        if not line.startswith("- "):
            continue
        relative = line[2:].strip()
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise RequestRefusal(
                "stale-request-context",
                f"generated management artifact path is unsafe: {relative}",
            )
        absolute = Path(project_root) / candidate
        if (
            not absolute.is_file()
            or absolute.is_symlink()
            or not _within(os.path.realpath(absolute), os.path.realpath(project_root))
        ):
            raise RequestRefusal(
                "stale-request-context",
                f"generated management artifact is missing or unsafe: {relative}",
            )
        artifacts.append(candidate.as_posix())
    return artifacts


def _management_artifacts(
    project_root: Path,
    review_kind: str,
    task_path: Optional[Path],
    checkpoint_id: Optional[str],
    *,
    phase_id: Optional[str] = None,
    checkpoint_text: Optional[str] = None,
) -> List[str]:
    project_root = Path(os.path.realpath(os.fspath(project_root)))
    if checkpoint_text:
        bound = _bound_management_artifacts(project_root, checkpoint_text)
        if bound is not None:
            return bound
    artifacts: List[Tuple[str, Path]] = []

    def add(kind: str, path: Path) -> None:
        if path.is_file():
            if path.is_symlink():
                raise RequestRefusal("unsafe-file", f"{kind} artifact is a symlink")
            resolved = Path(os.path.realpath(os.fspath(path)))
            if not _within(os.fspath(resolved), os.fspath(project_root)):
                raise RequestRefusal("outside-project", f"{kind} artifact escapes the project")
            artifacts.append((kind, resolved))

    for kind, name in (
        ("requirements", "REQUIREMENTS.md"),
        ("plan", "IMPLEMENTATION_PLAN.md"),
        ("standards", "STANDARDS.md"),
    ):
        add(kind, project_root / name)
    if review_kind in ("task-assignment", "task-closure") and task_path is not None:
        task_path = Path(os.path.realpath(os.fspath(task_path)))
        add("task", task_path)
        task_text = read_contained_text(project_root, task_path, what="task artifact")
        resolved_phase = _phase_from_text(task_text)
        if resolved_phase:
            add("phase", project_root / "phases" / f"{resolved_phase}.md")
        suffix = task_path.stem.removeprefix("TASK-")[:6]
        for spec in sorted((project_root / "specs").glob(f"SPEC-{suffix}*.md")):
            add("spec", spec)
        if review_kind == "task-closure":
            add("prompt", project_root / "prompts" / f"PROMPT-{suffix}.md")
        # Only the preserved completion report is management input; the
        # task-review report slot (REPORT-NN-NNN-review.md) is this review's
        # output and must not fold into the bound context identity.
        add("report", report_identity.completion_report_path(project_root, suffix))
        add("review", project_root / "reviews" / f"REVIEW-{suffix}.md")
    elif checkpoint_id:
        resolved_phase = phase_id or _phase_from_text(checkpoint_text or "")
        if resolved_phase:
            add("phase", project_root / "phases" / f"{resolved_phase}.md")
        add("prompt", project_root / "prompts" / f"PROMPT-{checkpoint_id}.md")
        add("report", report_identity.planning_report_path(project_root, checkpoint_id))
        add("review", project_root / "reviews" / f"REVIEW-{checkpoint_id}.md")
    artifacts.sort(key=lambda item: (item[0], item[1].as_posix()))
    return [path.relative_to(project_root).as_posix() for _, path in artifacts]


@dataclass(frozen=True)
class CapturedCompletionEvidence:
    """Content-hashed reference to the preserved task-completion report.

    ``source_path``/``review_path`` are project-relative identities;
    ``completion_report_path``/``review_report_path`` are the absolute paths
    the review prompt binds so the reviewer reads completion evidence directly
    from the preserved artifact and publishes only to the independent
    review-report slot. ``content`` is retained for binding verification and
    is never rendered into a prompt.
    """

    source_path: str
    review_path: str
    completion_report_path: str
    review_report_path: str
    content_identity: str
    content_bytes: int
    status: Optional[str]
    ready_to_close: Optional[bool]
    content: str

    def as_record(self, *, include_content: bool = False) -> Dict[str, Any]:
        record: Dict[str, Any] = {
            "source_path": self.source_path,
            "review_path": self.review_path,
            "completion_report_path": self.completion_report_path,
            "review_report_path": self.review_report_path,
            "content_identity": self.content_identity,
            "content_bytes": self.content_bytes,
            "status": self.status,
            "ready_to_close": self.ready_to_close,
        }
        if include_content:
            record["content"] = self.content
        return record


def _task_nn_nnn(task_path: Path) -> str:
    match = re.match(r"^TASK-(\d{2}-\d{3})", task_path.stem)
    if match is None:
        raise RequestRefusal(
            "malformed-review-target",
            f"invalid task name: {task_path.name}",
        )
    return match.group(1)


def _task_report_path(project_root: Path, task_path: Path) -> Path:
    return report_identity.completion_report_path(
        project_root, _task_nn_nnn(task_path)
    )


def _task_review_report_path(project_root: Path, task_path: Path) -> Path:
    return report_identity.review_report_path(
        project_root, _task_nn_nnn(task_path)
    )


def _report_status(text: str) -> Optional[str]:
    match = re.search(r"^Status:\s*(complete|blocked|failed)\s*$", text, re.MULTILINE)
    return match.group(1) if match else None


def _report_ready(text: str) -> Optional[bool]:
    match = re.search(
        r"^##\s+(?:Ready to close|Ready for review)\s*$"
        r"(.*?)(?=^##\s+|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        return None
    lines = [line.strip().lower() for line in match.group(1).splitlines() if line.strip()]
    if not lines:
        return None
    if lines[0] == "yes":
        return True
    if lines[0] == "no":
        return False
    return None


def _capture_completion_file(
    project_root: Path,
    task_path: Path,
) -> CapturedCompletionEvidence:
    # Import lazily to avoid the parse_report -> request_trace module cycle
    # while still validating against the canonical publication schema.
    from cli.commands import parse_report

    report_path = _task_report_path(project_root, task_path)
    if not report_path.is_file():
        raise RequestRefusal(
            "missing-coder-completion-evidence",
            f"task-closure review requires the coder report before slot reuse: "
            f"{report_path.relative_to(project_root).as_posix()}",
            "capture the accepted coder report while it is still present, then prepare the review prompt",
        )
    content = read_contained_text(
        project_root,
        report_path,
        what="coder completion report",
    )
    raw = content.encode("utf-8")
    if len(raw) > MAX_COMPLETION_EVIDENCE_BYTES:
        raise RequestRefusal(
            "coder-completion-evidence-too-large",
            f"coder report exceeds {MAX_COMPLETION_EVIDENCE_BYTES} bytes",
        )
    status = _report_status(content)
    ready = _report_ready(content)
    inferred_variant, _variant_error = parse_report._infer_variant(
        report_path,
        content,
    )
    if (
        inferred_variant != "task"
        or not parse_report._schema_ok("task", content)
        or status != "complete"
        or ready is not True
    ):
        raise RequestRefusal(
            "malformed-coder-completion-evidence",
            "coder report is not an accepted task-completion publication",
            "repair or rerun the coder handoff before preparing task-closure review",
        )
    review_report_path = _task_review_report_path(project_root, task_path)
    return CapturedCompletionEvidence(
        source_path=report_path.relative_to(project_root).as_posix(),
        review_path=review_report_path.relative_to(project_root).as_posix(),
        completion_report_path=str(report_path.resolve()),
        review_report_path=str(review_report_path.resolve()),
        content_identity=content_identity(raw),
        content_bytes=len(raw),
        status=status,
        ready_to_close=ready,
        content=content,
    )


def _parse_bound_completion(
    project_root: Path,
    prompt_text: str,
) -> Optional[CapturedCompletionEvidence]:
    """Verify a prompt's preserved-completion binding against the live artifact.

    The prompt names the preserved completion report and the independent
    expected review-report path; the completion report must still exist,
    byte-identical to the bound content identity, because it remains the
    reviewer's direct evidence source throughout task review.
    """
    if CAPTURED_COMPLETION_HEADING in prompt_text:
        # Shared-slot prompts embedded the report body because the reviewer
        # reused the coder's report slot. That contract is retired.
        raise RequestRefusal(
            "stale-request-context",
            "review prompt binds the retired embedded completion-evidence "
            "contract (shared report slot)",
            "regenerate the review prompt under the split-report contract",
        )
    if PRESERVED_COMPLETION_HEADING not in prompt_text:
        return None
    lines = prompt_text.splitlines()
    starts = [
        index
        for index, line in enumerate(lines)
        if line == PRESERVED_COMPLETION_HEADING
    ]
    if len(starts) != 1:
        raise RequestRefusal(
            "stale-request-context",
            "generated context has an ambiguous preserved coder-evidence section",
        )
    start = starts[0] + 1
    try:
        end = lines.index(MANAGEMENT_SECTION_HEADING, start)
    except ValueError:
        raise RequestRefusal(
            "stale-request-context",
            "preserved coder evidence has no management-channel boundary",
        )
    fields: Dict[str, str] = {}
    for index in range(start, end):
        line = lines[index]
        if ":" in line:
            key, value = line.split(":", 1)
            fields.setdefault(key.strip(), value.strip())
    source = fields.get("Source path", "")
    candidate = Path(source)
    if (
        not source
        or candidate.is_absolute()
        or ".." in candidate.parts
        or candidate.parent.as_posix() != "reports"
        or not report_identity.TASK_COMPLETION_REPORT_RE.fullmatch(candidate.name)
    ):
        raise RequestRefusal(
            "stale-request-context",
            "preserved coder evidence source path is unsafe or malformed",
        )
    nn_nnn = report_identity.nn_nnn_for_report_name(candidate.name)
    completion_path = report_identity.completion_report_path(project_root, nn_nnn)
    review_path = report_identity.review_report_path(project_root, nn_nnn)
    if not _within(
        os.path.realpath(completion_path.parent), os.path.realpath(project_root)
    ):
        raise RequestRefusal(
            "stale-request-context",
            "preserved coder evidence source escapes the project",
        )
    declared_completion = fields.get("Completion report path", "")
    declared_review = fields.get("Expected review report path", "")
    if declared_completion != str(completion_path.resolve()) or (
        declared_review != str(review_path.resolve())
    ):
        raise RequestRefusal(
            "stale-request-context",
            "bound report paths do not match the task's protocol-derived "
            "report identities",
        )
    if not completion_path.is_file():
        raise RequestRefusal(
            "missing-coder-completion-evidence",
            "the preserved coder completion report is absent: "
            f"{candidate.as_posix()}",
            "restore the preserved completion report or rerun the coder "
            "handoff before task-closure review",
        )
    content = read_contained_text(
        project_root,
        completion_path,
        what="preserved coder completion report",
    )
    raw = content.encode("utf-8")
    identity = content_identity(raw)
    try:
        declared_bytes = int(fields.get("Content bytes", ""))
    except ValueError:
        declared_bytes = -1
    if (
        fields.get("Content identity") != identity
        or declared_bytes != len(raw)
        or len(raw) > MAX_COMPLETION_EVIDENCE_BYTES
    ):
        raise RequestRefusal(
            "stale-request-context",
            "the preserved completion report no longer matches the bound "
            "content identity; completion evidence must stay immutable "
            "throughout task review",
        )
    status = fields.get("Coder status")
    ready_raw = fields.get("Ready to close")
    ready = True if ready_raw == "yes" else False if ready_raw == "no" else None
    if status != _report_status(content) or ready != _report_ready(content):
        raise RequestRefusal(
            "stale-request-context",
            "preserved coder outcome fields do not match the bound report",
        )
    # Import lazily to avoid the parse_report -> request_trace module cycle.
    from cli.commands import parse_report

    inferred_variant, _variant_error = parse_report._infer_variant(
        completion_path, content
    )
    if (
        inferred_variant != "task"
        or not parse_report._schema_ok("task", content)
        or status != "complete"
        or ready is not True
    ):
        raise RequestRefusal(
            "malformed-coder-completion-evidence",
            "the preserved coder report is not an accepted task-completion "
            "publication",
            "repair or rerun the coder handoff before task-closure review",
        )
    return CapturedCompletionEvidence(
        source_path=candidate.as_posix(),
        review_path=review_path.relative_to(project_root).as_posix(),
        completion_report_path=str(completion_path.resolve()),
        review_report_path=str(review_path.resolve()),
        content_identity=identity,
        content_bytes=len(raw),
        status=status,
        ready_to_close=ready,
        content=content,
    )


def _captured_completion_evidence(
    project_root: Path,
    task_path: Path,
    prompt_text: Optional[str],
    *,
    required: bool,
) -> Optional[CapturedCompletionEvidence]:
    if prompt_text and REQUEST_SECTION_HEADING in prompt_text:
        bound = _parse_bound_completion(project_root, prompt_text)
        if bound is not None:
            expected = _task_report_path(project_root, task_path)
            if bound.source_path != expected.relative_to(project_root).as_posix():
                raise RequestRefusal(
                    "stale-request-context",
                    "bound completion evidence does not belong to the task "
                    "under review",
                )
            return bound
    if required:
        return _capture_completion_file(project_root, task_path)
    return None


def _fence(text: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def render_sections(
    trace: Sequence[RequestEvidence],
    management: Sequence[str],
    identity: str,
    *,
    target: GovernedUnit,
    review_kind: str,
    legacy: bool,
    captured_completion: Optional[CapturedCompletionEvidence] = None,
) -> str:
    lines = [
        REQUEST_SECTION_HEADING,
        "",
        f"Request-context identity: {identity}",
        f"Review target: {target.kind}:{target.identifier}",
    ]
    if legacy:
        lines += [f"Request state: {LEGACY_STATE}", "", "This unit predates request-evidence resolution. This state is explicit and non-blocking only for historical work."]
    else:
        lines += ["Request state: resolved", f"Request evidence: {', '.join(r.record_id for r in trace)}"]
        for record in trace:
            if record.kind == "original":
                label = "Initiating request"
            elif record.kind == "correction":
                label = f"Explicit correction {record.source_sequence}"
            else:
                label = f"Exact operator excerpt {record.sequence}"
            fence = _fence(record.text)
            lines += [
                "",
                f"### {label} — {record.record_id}",
                "",
                f"Governed unit: {record.unit.kind}:{record.unit.identifier}",
                f"Evidence order: {record.sequence}",
                f"Content identity: {record.identity}",
                f"Source: {record.source_kind}:{record.source_identity}",
                f"Source path: {record.source_path}",
                f"Source identity: {record.source_content_identity}",
            ]
            if record.observed_at:
                lines.append(f"Observed at: {record.observed_at}")
            lines += ["", fence + "text", record.text, fence]
    if captured_completion is not None:
        lines += [
            "",
            PRESERVED_COMPLETION_HEADING,
            "",
            f"Source path: {captured_completion.source_path}",
            f"Completion report path: {captured_completion.completion_report_path}",
            f"Expected review report path: {captured_completion.review_report_path}",
            f"Content identity: {captured_completion.content_identity}",
            f"Content bytes: {captured_completion.content_bytes}",
            f"Coder status: {captured_completion.status}",
            "Ready to close: "
            + ("yes" if captured_completion.ready_to_close else "no"),
            "",
            "Read the coder completion evidence directly from the preserved "
            "completion report path above; this prompt does not reproduce the "
            "report body. Publish the review result only to the expected "
            "review report path.",
        ]
    if review_kind == "task-assignment":
        comparison_instruction = (
            "Before changing any work root, compare the verbatim operator "
            "request above with these PM-derived artifacts and this prompt's "
            "authored instructions. Treat PM artifacts as guidance, not as "
            "independent authority. If they add implementation, destinations, "
            "features, conventions, or scope the operator did not request, "
            "stop and report the mismatch instead of implementing it. An "
            "invitation to propose an option authorizes a proposal, not its "
            "implementation."
        )
    else:
        comparison_instruction = (
            "The configured reviewer compares the verbatim request above with "
            "these PM-derived artifacts, the captured coder completion evidence, "
            "and the delivered outcome. PM-authored requirements are not "
            "independent operator authority; any unrequested implementation, "
            "destination, feature, convention, or scope is request drift. The "
            "operator is the request source, not the reviewer."
        )
    lines += ["", MANAGEMENT_SECTION_HEADING, "", comparison_instruction, ""]
    lines.extend(f"- {path}" for path in management)
    return "\n".join(lines).rstrip() + "\n"


@dataclass
class ReviewContext:
    review_kind: str
    target: GovernedUnit
    trace: List[RequestEvidence]
    management_artifacts: List[str]
    context_identity: str
    section: str
    legacy: bool
    captured_completion: Optional[CapturedCompletionEvidence] = None

    @property
    def evidence_ids(self) -> List[str]:
        return [record.record_id for record in self.trace]

    @property
    def evidence(self) -> List[RequestEvidence]:
        return self.trace

    @property
    def measures(self) -> Dict[str, int]:
        return {"request_bytes": sum(len(record.text.encode("utf-8")) for record in self.trace)}

    def as_record(self) -> Dict[str, Any]:
        return {
            "request_trace": {
                "state": LEGACY_STATE if self.legacy else "resolved",
                "records": [record.as_record() for record in self.trace],
            },
            "management_guidance": {"artifact_paths": self.management_artifacts},
            "captured_completion_evidence": (
                self.captured_completion.as_record()
                if self.captured_completion is not None
                else None
            ),
            "review_kind": self.review_kind,
            "target": self.target.as_record(),
            "context_identity": self.context_identity,
            "measures": {"request_bytes": sum(len(r.text.encode('utf-8')) for r in self.trace)},
        }


def _context(
    project_root: Path,
    review_kind: str,
    task_path: Optional[Path],
    checkpoint_id: Optional[str],
    *,
    phase_id: Optional[str] = None,
    plan_ref: Optional[str] = None,
    checkpoint_text: Optional[str] = None,
    allow_historical_legacy: bool = False,
    require_completion_evidence: bool = False,
) -> ReviewContext:
    del plan_ref
    target = _target_unit(review_kind, task_path, checkpoint_id)
    # A planning checkpoint is a review of the project-planning unit, so its
    # project-origin record is an explicit, deterministic source.  A task is a
    # distinct intake unit: silently substituting the project's founding ask
    # would present unrelated text as that task's initiating request.
    source_texts = _source_texts(
        project_root,
        review_kind,
        task_path,
        phase_id=phase_id,
        checkpoint_text=checkpoint_text,
    )
    trace = _resolve_trace(
        project_root,
        target,
        source_texts,
        allow_project_origin=review_kind == "planning",
    )
    if (
        not trace
        and request_capture_enforced(project_schema_version(project_root))
        and not allow_historical_legacy
    ):
        raise RequestRefusal(
            "unit-request-not-captured",
            f"no exact operator request evidence resolves for {target.kind}:{target.identifier}",
            "provide an applicable exact decision quotation, supported host chat record, or host intake record",
        )
    legacy = not trace
    captured_completion = (
        _captured_completion_evidence(
            project_root,
            task_path,
            checkpoint_text,
            required=require_completion_evidence,
        )
        if review_kind == "task-closure" and task_path is not None
        else None
    )
    management = _management_artifacts(
        project_root,
        review_kind,
        task_path,
        checkpoint_id,
        phase_id=phase_id,
        checkpoint_text=checkpoint_text,
    )
    if captured_completion is not None:
        management = [
            path for path in management
            if path != captured_completion.source_path
        ]
    payload = {
        "schema": "cartopian-review-context-v3",
        "review_kind": review_kind,
        "target": target.as_record(),
        "request_records": [
            {
                "id": record.record_id,
                "identity": record.identity,
                "sequence": record.sequence,
                "source_identity": record.source_identity,
                "source_content_identity": record.source_content_identity,
                "source_sequence": record.source_sequence,
                "unit": record.unit.as_record(),
            }
            for record in trace
        ],
        "legacy": legacy,
        "management_artifacts": management,
        "captured_completion_evidence": (
            captured_completion.as_record()
            if captured_completion is not None
            else None
        ),
    }
    identity = content_identity(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    section = render_sections(
        trace,
        management,
        identity,
        target=target,
        review_kind=review_kind,
        legacy=legacy,
        captured_completion=captured_completion,
    )
    return ReviewContext(
        review_kind,
        target,
        list(trace),
        management,
        identity,
        section,
        legacy,
        captured_completion,
    )


def context_for_task(
    project_root: Path,
    task_path: Path,
    *_ignored: object,
    prompt_text: Optional[str] = None,
    allow_historical_legacy: bool = False,
    require_completion_evidence: bool = False,
) -> ReviewContext:
    return _context(
        Path(project_root),
        "task-closure",
        Path(task_path),
        None,
        checkpoint_text=prompt_text,
        allow_historical_legacy=allow_historical_legacy,
        require_completion_evidence=require_completion_evidence,
    )


def context_for_task_assignment(
    project_root: Path,
    task_path: Path,
    *,
    prompt_text: Optional[str] = None,
    allow_historical_legacy: bool = False,
) -> ReviewContext:
    """Bind exact operator evidence to the coder handoff before execution."""
    return _context(
        Path(project_root),
        "task-assignment",
        Path(task_path),
        None,
        checkpoint_text=prompt_text,
        allow_historical_legacy=allow_historical_legacy,
        require_completion_evidence=False,
    )


def context_for_checkpoint(project_root: Path, checkpoint_id: str, *, phase_id: Optional[str] = None, plan_ref: Optional[str] = None, checkpoint_text: Optional[str] = None) -> ReviewContext:
    return _context(
        Path(project_root),
        "planning",
        None,
        checkpoint_id,
        phase_id=phase_id,
        plan_ref=plan_ref,
        checkpoint_text=checkpoint_text,
    )


def _section_bounds(lines: Sequence[str]) -> Optional[Tuple[int, int]]:
    starts = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == REQUEST_SECTION_HEADING]
    if len(starts) != 1:
        return None
    start = starts[0]
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if (
            lines[i].startswith("## ")
            and lines[i].rstrip("\r\n")
            not in (
                PRESERVED_COMPLETION_HEADING,
                CAPTURED_COMPLETION_HEADING,
                MANAGEMENT_SECTION_HEADING,
            )
        ):
            end = i
            break
    return start, end


def extract_request_sections(prompt_text: str) -> Optional[str]:
    lines = prompt_text.splitlines(keepends=True)
    bounds = _section_bounds(lines)
    return None if bounds is None else "".join(lines[bounds[0]:bounds[1]]).rstrip() + "\n"


def upsert_request_sections(prompt_text: str, section: str) -> str:
    lines = prompt_text.splitlines(keepends=True)
    bounds = _section_bounds(lines)
    if bounds is not None:
        del lines[bounds[0]:bounds[1]]
    body = "".join(lines).rstrip() + "\n\n" if lines else ""
    return body + section.rstrip() + "\n"


def preflight_prompt_binding(context: ReviewContext, prompt_text: str) -> Dict[str, Any]:
    actual = extract_request_sections(prompt_text)
    ok = actual == context.section
    return {
        "ok": ok,
        "rule": None if ok else "stale-request-context",
        "detail": "request context is current" if ok else "prompt omits or changes the generated request comparison context",
        "recovery": "regenerate the prompt from the current intake trace" if not ok else "",
        "context_identity": context.context_identity,
    }


def _header(text: str, name: str) -> Optional[str]:
    match = re.search(rf"^{re.escape(name)}:\s*(.*?)\s*$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def parse_alignment(text: str, *, expected_evidence: Optional[Sequence[str]] = None, legacy: bool = False) -> Dict[str, Any]:
    raw = _header(text, ALIGNMENT_FIELD)
    evidence_raw = _header(text, ALIGNMENT_EVIDENCE_FIELD)
    evidence = [] if not evidence_raw or evidence_raw == "none" else [item.strip() for item in evidence_raw.split(",") if item.strip()]
    value = raw.split(" — ", 1)[0] if raw else None
    present = value in ALIGNMENT_VALUES
    detail = "request alignment is missing or invalid"
    blocking = True
    if present and value == "drifted":
        detail = "review records drift from the initiating request"
    elif present and value == "aligned":
        mismatch = expected_evidence is not None and evidence != list(expected_evidence)
        blocking = mismatch or legacy
        detail = "request evidence does not match the bound trace" if mismatch else ("legacy context cannot claim aligned" if legacy else "aligned")
    elif present and value == LEGACY_STATE:
        blocking = not legacy
        detail = "historical request capture was unavailable" if legacy else "new captured work cannot claim legacy status"
    return {"value": value, "reason": None, "evidence": evidence, "present": present, "blocking": blocking, "detail": detail}


def review_contract_applies(prompt_text: str) -> bool:
    return REQUEST_SECTION_HEADING in prompt_text


def alignment_enforced(project_schema_version: Optional[str]) -> bool:
    """Compatibility name for callers; the corrected contract begins at v0.9."""
    return request_capture_enforced(project_schema_version)


def require_request_before_derivative(project_root: Path, dest_kind: str, relative_target: str = "") -> None:
    if dest_kind not in {"requirements", "plan", "phase", "task", "spec", "prompt"}:
        return
    if not request_capture_enforced(project_schema_version(project_root)):
        return
    task_path: Optional[Path] = None
    checkpoint_id: Optional[str] = None
    if dest_kind in {"task", "spec"}:
        match = re.search(r"(?:TASK|SPEC)-(\d{2}-\d{3})", relative_target)
        unit = GovernedUnit("task", f"TASK-{match.group(1)}") if match else GovernedUnit("project", "project")
        if match:
            task_id = f"TASK-{match.group(1)}"
            candidates = sorted(
                path
                for path in (Path(project_root) / "tasks").glob(f"*/{task_id}*.md")
                if path.is_file() and not path.is_symlink()
            )
            task_path = candidates[0] if len(candidates) == 1 else None
    elif dest_kind == "prompt" and relative_target.startswith("PROMPT-PLAN-"):
        checkpoint_id = Path(relative_target).stem.removeprefix("PROMPT-")
        unit = GovernedUnit("planning", checkpoint_id)
    elif dest_kind == "prompt":
        match = re.search(r"PROMPT-(\d{2}-\d{3})", relative_target)
        unit = GovernedUnit("task", f"TASK-{match.group(1)}") if match else GovernedUnit("project", "project")
        if match:
            task_id = f"TASK-{match.group(1)}"
            candidates = sorted(
                path
                for path in (Path(project_root) / "tasks").glob(f"*/{task_id}*.md")
                if path.is_file() and not path.is_symlink()
            )
            task_path = candidates[0] if len(candidates) == 1 else None
    else:
        unit = GovernedUnit("project", "project")
    # Task/spec authoring during project planning is governed by the project
    # intake.  A task-assignment/review prompt is different: it begins the task
    # unit and therefore requires that task's own host-captured message.
    allow_project_origin = unit.kind == "planning" or dest_kind in {"task", "spec"}
    review_kind = "task-closure" if unit.kind == "task" else "planning"
    source_texts = _source_texts(
        project_root,
        review_kind,
        task_path,
    )
    if not _resolve_trace(
        project_root,
        unit,
        source_texts,
        allow_project_origin=allow_project_origin,
    ):
        raise RequestRefusal(
            "request-not-captured",
            "new PM-derived work cannot be authored before exact operator request evidence resolves",
            "provide an applicable exact decision quotation, supported host chat record, or host intake record",
        )
