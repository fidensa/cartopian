"""Acceptance-to-source traceability and the two closure determinations.

This module implements the accepted traceability and upstream-verification
contract. It owns four things and nothing else:

1. **Material-criterion derivation.** The material set is the union of two
   enumerations — the governing specification's ``## Examples / acceptance``
   list and the governing task's ``## Acceptance`` checklist — reduced by the
   bounded origin-merge rule. Nothing else is material: constraints, prose,
   non-acceptance examples, and unrelated history stay outside it.

2. **Deterministic serialization.** Trace records (typed edges and
   exemptions), ``X|`` conflict dispositions, and ``O|`` origin records
   serialize into one totally ordered body whose SHA-256 is the *trace
   identity*. Two producers with the same record set emit byte-identical
   bodies, which is what lets a reviewer confirm the record set they read is
   the record set the PM derived.

3. **Role-specific projections.** The coder projection carries the complete
   immediate contract (every material criterion) and no governance identity.
   The reviewer projection carries the full typed record set, coverage
   results, waivers, dispositions, origins, and the determination block.

4. **Bounds and fail-closed enforcement.** Structural errors fail at
   readiness; the D1/D2 determinations fail at closure. The code set is
   closed (:data:`READINESS_CODES` + :data:`CLOSURE_CODES`); no rule here
   introduces a new one.

The conformance anchor is the five-criterion reference shape: 143 coder bytes
+ 2,438 reviewer bytes = 2,581 routine bytes, with the trace body hashing to
``sha256:f5ae02d6…2a3d3763`` and the reviewer body to
``sha256:cdda0a36…7eff0f35``. :func:`conformance_anchor` rebuilds that shape
from this module's own serializer on every readiness evaluation, so a
serialization drift is caught as ``trace-unparseable`` rather than silently
re-baselining the accepted budget.

Standard library only. No network, no third-party runtime dependency, and no
persisted manifest artifact: the record set lives in the governing task's
``## Upstream trace`` section, which readiness, prompt assembly, and review
context already read.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Closed vocabularies (§§ 3.1, 3.4, 4.4, 5.4).
# ---------------------------------------------------------------------------
SOURCE_TYPES: Tuple[str, ...] = (
    "requirement",
    "standard",
    "plan-item",
    "decision",
    "spec",
    "operator-request",
)

EXEMPTION_REASONS: Tuple[str, ...] = (
    "derived-mechanical",
    "template-fixed",
    "restates-parent",
)

WAIVER_CLASSES: Tuple[str, ...] = (
    "procedural-authorization",
    "background-scope",
)

DISPOSITION_KINDS: Tuple[str, ...] = ("precedence", "narrowing", "amendment")

PRECEDENCE_CLASS: Dict[str, str] = {
    "requirement": "behavior",
    "standard": "behavior",
    "plan-item": "boundary",
    "decision": "boundary",
    "spec": "contract",
    "operator-request": "intent",
}

# ---------------------------------------------------------------------------
# Error codes (§ 9.1). Membership is closed: no rule in this module may add
# one, and every new behavior routes through a code the operator accepted.
# ---------------------------------------------------------------------------
READINESS_CODES: Tuple[str, ...] = (
    "trace-missing",
    "trace-unparseable",
    "trace-incomplete",
    "criterion-digest-mismatch",
    "exemption-conflict",
    "unknown-source-type",
    "spec-self-reference",
    "unknown-waiver-class",
    "bound-exceeded",
    "trace-identity-mismatch",
)

CLOSURE_CODES: Tuple[str, ...] = (
    "trace-identity-mismatch",
    "unresolved-source-conflict",
    "source-uncovered",
    "request-uncovered",
    "waiver-rejected",
    "acceptance-item-unmet",
    "upstream-intent-uncovered",
    "exemption-unjustified",
)

#: Reason codes a ``D1`` line may carry (§ 7.1).
D1_REASONS: Tuple[str, ...] = ("acceptance-item-unmet",)

#: Reason codes a criterion-scoped ``D2`` line may carry (§ 7.2).
D2_REASONS: Tuple[str, ...] = (
    "upstream-intent-uncovered",
    "exemption-unjustified",
    "unresolved-source-conflict",
)

#: Reason codes a task-scoped ``D2`` result may carry (§ 7.3).
D2_TASK_REASONS: Tuple[str, ...] = (
    "source-uncovered",
    "request-uncovered",
    "waiver-rejected",
)

# ---------------------------------------------------------------------------
# Closed field-width table (§ 8.2). A field wider than its cap is
# ``trace-unparseable`` and names the offending record and field. These caps
# are a validity check, not the budget — § 8.3 is the budget.
# ---------------------------------------------------------------------------
CAP_ORDINAL = 3
CAP_DIGEST12 = 12
CAP_TYPE_EDGE = 16
CAP_TYPE_EXEMPTION = 23
CAP_SOURCE_IDENTITY = 97
CAP_APPLICABLE_CONTEXT = 69
CAP_OCCURRENCE = 3
CAP_WAIVER_CLASS = 24
CAP_DISPOSITION_KIND = 10
CAP_WAIVER_RECORD = 183
CAP_DISPOSITION_RECORD = 114

#: Ordinals are ``C<nn>`` and a task carries at most 99 material criteria.
MAX_CRITERIA = 99

ORDINAL_RE = re.compile(r"^C\d{2}$")
DIGEST12_RE = re.compile(r"^[0-9a-f]{12}$")
OCCURRENCE_RE = re.compile(r"^[1-9][0-9]{0,2}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: A ``spec`` edge names a clause by content identity, never by path (§ 3.3).
SPEC_CLAUSE_RE = re.compile(r"^spec-clause sha256:([0-9a-f]{64})$")

#: An ``operator-request`` edge names an excerpt by alias + content identity.
#: The alias is ``REQ-<evidence order>``; the accepted evidence fixes the
#: order at three digits, which is what makes the identity exactly 79 B and
#: the ``R|`` ceilings in § 8.2 reproduce.
REQUEST_IDENTITY_RE = re.compile(r"^(REQ-\d{3}) sha256:([0-9a-f]{64})$")
#: Width of a well-formed ``operator-request`` source identity (§ 3.2).
REQUEST_IDENTITY_WIDTH = 79

# ---------------------------------------------------------------------------
# The accepted routine budget (§§ 8.3, 8.4).
# ---------------------------------------------------------------------------
#: ``(n, e, s, r, w, x, o)`` — the reference shape the budget was measured on.
REFERENCE_SHAPE: Tuple[int, int, int, int, int, int, int] = (5, 7, 4, 2, 1, 0, 0)
#: The accepted routine measurement at that shape, to the byte.
REFERENCE_CODER_BYTES = 143
REFERENCE_REVIEWER_BYTES = 2438
REFERENCE_ROUTINE_BYTES = 2581
REFERENCE_TRACE_IDENTITY = (
    "sha256:f5ae02d6a2520e56151ca5f687eb404c1beacad39d13516df8a8eac52a3d3763"
)
REFERENCE_REVIEWER_IDENTITY = (
    "sha256:cdda0a3673ca21faccacaae286958e69c0eb38f4f14d52055ebd467d7eff0f35"
)
REFERENCE_CRITERION_BODY_IDENTITY = (
    "sha256:5ea3b9b8c5a6c0816f84557cfbe0d1d63662956478355c7bff39a92689f7c511"
)

#: Section heading the PM-authored record set lives under, in the task.
TRACE_SECTION_HEADING = "## Upstream trace"
#: Section heading the reviewer's closure determinations live under.
DETERMINATION_SECTION_HEADING = "## Closure determinations"
#: Acceptance-list headings the material set is derived from (§ 2.1).
SPEC_ACCEPTANCE_HEADING = "## Examples / acceptance"
TASK_ACCEPTANCE_HEADING = "## Acceptance"


class TraceRefusal(Exception):
    """A fail-closed traceability refusal carrying its contract error code."""

    def __init__(self, code: str, detail: str, *, identity: str = "") -> None:
        self.code = code
        self.detail = detail
        self.identity = identity
        super().__init__(f"{code}: {detail}")


# ---------------------------------------------------------------------------
# Identity (§ 2.2).
# ---------------------------------------------------------------------------
def normalize(text: str) -> str:
    """Normalize criterion or clause text for hashing.

    NFC; leading and trailing whitespace stripped; internal whitespace runs
    collapsed to a single U+0020. Case and punctuation are preserved, and the
    hashed input never carries a trailing newline.
    """
    return " ".join(unicodedata.normalize("NFC", text).split())


def digest12(text: str) -> str:
    """Durable criterion / origin identity: first 12 hex of the SHA-256."""
    return full_identity(text)[:12]


def full_identity(text: str) -> str:
    """SHA-256 hex of the normalized text, with no trailing newline."""
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def body_identity(body: str) -> str:
    """``sha256:<64 hex>`` over a serialized body exactly as measured."""
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def measure(body: str) -> int:
    """Exact UTF-8 byte width of a serialized body."""
    return len(body.encode("utf-8"))


def est_tokens(byte_count: int) -> int:
    """Labeled, non-authoritative estimator (§ 8.6). Never enforced."""
    return -(-byte_count // 4)


# ---------------------------------------------------------------------------
# Record model.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Criterion:
    """One material acceptance criterion."""

    ordinal: str
    digest: str
    text: str
    #: ``spec`` when the governing text is a specification acceptance item,
    #: ``task`` when it is a task acceptance item. Constraint 1 of § 2.1.2
    #: keys the merge direction on this.
    origin_list: str


@dataclass(frozen=True)
class Record:
    """One trace record: a typed edge or an exemption (§§ 4.1, 4.5)."""

    ordinal: str
    digest: str
    type: str
    source_identity: str
    applicable_context: str
    occurrence: str

    @property
    def is_exemption(self) -> bool:
        return self.type.startswith("none:")

    @property
    def reason(self) -> str:
        return self.type[len("none:") :] if self.is_exemption else ""

    @property
    def precedence_class(self) -> str:
        return "" if self.is_exemption else PRECEDENCE_CLASS[self.type]

    def line(self) -> str:
        return "|".join(
            (
                self.ordinal,
                self.digest,
                self.type,
                self.source_identity,
                self.applicable_context,
                self.occurrence,
            )
        )

    def sort_key(self) -> Tuple[bytes, bytes, bytes, bytes]:
        return (
            self.ordinal.encode("utf-8"),
            self.type.encode("utf-8"),
            self.source_identity.encode("utf-8"),
            self.occurrence.encode("utf-8"),
        )


@dataclass(frozen=True)
class Disposition:
    """An ``X|`` same-class conflict disposition (§ 4.4)."""

    ordinal: str
    kind: str
    rule: str

    def line(self) -> str:
        return f"X|{self.ordinal}|{self.kind}|{self.rule}"

    def sort_key(self) -> bytes:
        return self.ordinal.encode("utf-8")


@dataclass(frozen=True)
class Origin:
    """An ``O|`` merged-away acceptance origin record (§ 4.6)."""

    ordinal: str
    digest: str

    def line(self) -> str:
        return f"O|{self.ordinal}|{self.digest}"

    def sort_key(self) -> Tuple[bytes, bytes]:
        return (self.ordinal.encode("utf-8"), self.digest.encode("utf-8"))


@dataclass(frozen=True)
class Waiver:
    """A ``W|`` operator-authorized coverage waiver (§ 5.4)."""

    identity: str
    waiver_class: str
    scope: str

    def line(self) -> str:
        return f"W|{self.identity}|{self.waiver_class}|{self.scope}"

    def sort_key(self) -> bytes:
        return self.identity.encode("utf-8")


@dataclass
class RecordSet:
    """The PM-authored record set, before it is bound to a material list."""

    records: List[Record] = field(default_factory=list)
    dispositions: List[Disposition] = field(default_factory=list)
    origins: List[Origin] = field(default_factory=list)
    waivers: List[Waiver] = field(default_factory=list)
    declared_identity: Optional[str] = None


# ---------------------------------------------------------------------------
# Acceptance-list extraction (§ 2.1).
# ---------------------------------------------------------------------------
_H2_RE = re.compile(r"^##\s+(.*?)\s*$")
_CHECKBOX_RE = re.compile(r"^-\s+\[[ xX]\]\s+(.*\S)\s*$")
_BULLET_RE = re.compile(r"^-\s+(?!\[[ xX]\])(.*\S)\s*$")


def _section_lines(text: str, heading: str) -> Optional[List[str]]:
    """Return the lines of one ``## `` section, or ``None`` when absent."""
    want = heading[3:].strip().casefold()
    collecting = False
    out: List[str] = []
    for line in text.splitlines():
        match = _H2_RE.match(line)
        if match:
            if collecting:
                break
            collecting = match.group(1).strip().casefold() == want
            continue
        if collecting:
            out.append(line)
    return out if collecting else None


def spec_acceptance_items(spec_text: str) -> List[str]:
    """The governing specification's ``## Examples / acceptance`` enumeration.

    Only top-level ``- `` bullets are enumerated items. Nested bullets are
    elaboration of their parent item, not separate acceptance items, and
    prose is not material at all.
    """
    lines = _section_lines(spec_text, SPEC_ACCEPTANCE_HEADING)
    if lines is None:
        return []
    return [m.group(1) for m in (_BULLET_RE.match(ln) for ln in lines) if m]


def task_acceptance_items(task_text: str) -> List[str]:
    """The governing task's ``## Acceptance`` checklist enumeration."""
    lines = _section_lines(task_text, TASK_ACCEPTANCE_HEADING)
    if lines is None:
        return []
    return [m.group(1) for m in (_CHECKBOX_RE.match(ln) for ln in lines) if m]


# ---------------------------------------------------------------------------
# Record-set parsing (§§ 4.1, 4.5, 4.6, 5.4).
# ---------------------------------------------------------------------------
_FENCE_RE = re.compile(r"^```")
_TRACE_IDENTITY_LINE_RE = re.compile(r"^Trace-identity:\s*(sha256:[0-9a-f]{64})\s*$")


def extract_record_block(task_text: str) -> Optional[List[str]]:
    """Return the raw record lines of the task's ``## Upstream trace`` section.

    The records live in one fenced block so ordinary prose in the section can
    never be mistaken for a record. ``None`` means the section is absent.
    """
    lines = _section_lines(task_text, TRACE_SECTION_HEADING)
    if lines is None:
        return None
    out: List[str] = []
    inside = False
    for line in lines:
        if _FENCE_RE.match(line.strip()):
            inside = not inside
            continue
        if inside and line.strip():
            out.append(line.rstrip("\n"))
    return out


def _check_width(value: str, cap: int, *, field_name: str, line: str) -> None:
    width = len(value.encode("utf-8"))
    if width > cap:
        raise TraceRefusal(
            "trace-unparseable",
            f"field {field_name!r} is {width} B, over its {cap} B cap: {line}",
            identity=line,
        )


def _decode_field(value: str) -> str:
    """Decode the normative ``%7C`` escape for a literal pipe (§ 4.1)."""
    return value.replace("%7C", "|")


def _parse_typed_record(line: str) -> Record:
    fields = line.split("|")
    if len(fields) != 6:
        raise TraceRefusal(
            "trace-unparseable",
            f"trace record must carry exactly six fields, got {len(fields)}: {line}",
            identity=line,
        )
    if any(part == "" for part in fields):
        raise TraceRefusal(
            "trace-unparseable", f"trace record has an empty field: {line}", identity=line
        )
    ordinal, digest, kind, source, context, occurrence = fields
    _check_width(ordinal, CAP_ORDINAL, field_name="ordinal", line=line)
    _check_width(digest, CAP_DIGEST12, field_name="digest12", line=line)
    _check_width(source, CAP_SOURCE_IDENTITY, field_name="source-identity", line=line)
    _check_width(
        context, CAP_APPLICABLE_CONTEXT, field_name="applicable-context", line=line
    )
    _check_width(occurrence, CAP_OCCURRENCE, field_name="occurrence", line=line)
    if not ORDINAL_RE.match(ordinal):
        raise TraceRefusal(
            "trace-unparseable", f"ordinal must match C<nn>: {line}", identity=line
        )
    if not DIGEST12_RE.match(digest):
        raise TraceRefusal(
            "trace-unparseable",
            f"digest12 must be 12 lowercase hex characters: {line}",
            identity=line,
        )
    if kind.startswith("none:"):
        _check_width(kind, CAP_TYPE_EXEMPTION, field_name="type", line=line)
        if kind[len("none:") :] not in EXEMPTION_REASONS:
            raise TraceRefusal(
                "unknown-source-type",
                f"exemption reason outside the closed set: {kind!r}",
                identity=line,
            )
        if source != "-" or context != "-":
            raise TraceRefusal(
                "trace-unparseable",
                "exemption fields 4 and 5 must be exactly '-': " + line,
                identity=line,
            )
        if occurrence != "1":
            raise TraceRefusal(
                "trace-unparseable",
                "exemption field 6 must be exactly '1': " + line,
                identity=line,
            )
    else:
        _check_width(kind, CAP_TYPE_EDGE, field_name="type", line=line)
        if kind not in SOURCE_TYPES:
            raise TraceRefusal(
                "unknown-source-type",
                f"source type outside the closed vocabulary: {kind!r}",
                identity=line,
            )
        if not OCCURRENCE_RE.match(occurrence):
            raise TraceRefusal(
                "trace-unparseable",
                f"occurrence must be a positive integer ≤ 999: {line}",
                identity=line,
            )
        if kind == "spec" and not SPEC_CLAUSE_RE.match(source):
            raise TraceRefusal(
                "trace-unparseable",
                "a spec edge names a clause as 'spec-clause sha256:<64 hex>': " + line,
                identity=line,
            )
        if kind == "operator-request" and not REQUEST_IDENTITY_RE.match(source):
            raise TraceRefusal(
                "trace-unparseable",
                "an operator-request edge names '<alias> sha256:<64 hex>': " + line,
                identity=line,
            )
    return Record(
        ordinal=ordinal,
        digest=digest,
        type=kind,
        source_identity=_decode_field(source),
        applicable_context=_decode_field(context),
        occurrence=occurrence,
    )


def _parse_disposition(line: str) -> Disposition:
    if len(line.encode("utf-8")) + 1 > CAP_DISPOSITION_RECORD:
        raise TraceRefusal(
            "trace-unparseable",
            f"X| record exceeds its {CAP_DISPOSITION_RECORD} B cap: {line}",
            identity=line,
        )
    fields = line.split("|", 3)
    if len(fields) != 4 or any(part == "" for part in fields[1:]):
        raise TraceRefusal(
            "trace-unparseable",
            "X| record must be X|<ordinal>|<kind>|<rule>: " + line,
            identity=line,
        )
    _, ordinal, kind, rule = fields
    _check_width(ordinal, CAP_ORDINAL, field_name="ordinal", line=line)
    _check_width(kind, CAP_DISPOSITION_KIND, field_name="disposition kind", line=line)
    if not ORDINAL_RE.match(ordinal):
        raise TraceRefusal(
            "trace-unparseable", f"X| ordinal must match C<nn>: {line}", identity=line
        )
    if kind == "unresolved" or kind not in DISPOSITION_KINDS:
        raise TraceRefusal(
            "unresolved-source-conflict"
            if kind == "unresolved"
            else "trace-unparseable",
            f"disposition kind must be one of {DISPOSITION_KINDS}: {kind!r}",
            identity=line,
        )
    return Disposition(ordinal=ordinal, kind=kind, rule=_decode_field(rule))


def _parse_origin(line: str) -> Origin:
    fields = line.split("|")
    if len(fields) != 3 or any(part == "" for part in fields):
        raise TraceRefusal(
            "trace-unparseable",
            "O| record must be O|<ordinal>|<origin-digest12>: " + line,
            identity=line,
        )
    _, ordinal, digest = fields
    _check_width(ordinal, CAP_ORDINAL, field_name="ordinal", line=line)
    _check_width(digest, CAP_DIGEST12, field_name="origin digest12", line=line)
    if not ORDINAL_RE.match(ordinal):
        raise TraceRefusal(
            "trace-unparseable", f"O| ordinal must match C<nn>: {line}", identity=line
        )
    if not DIGEST12_RE.match(digest):
        raise TraceRefusal(
            "trace-unparseable",
            f"O| origin digest must be 12 lowercase hex characters: {line}",
            identity=line,
        )
    return Origin(ordinal=ordinal, digest=digest)


def _parse_waiver(line: str) -> Waiver:
    if len(line.encode("utf-8")) + 1 > CAP_WAIVER_RECORD:
        raise TraceRefusal(
            "trace-unparseable",
            f"W| record exceeds its {CAP_WAIVER_RECORD} B cap: {line}",
            identity=line,
        )
    fields = line.split("|", 3)
    if len(fields) != 4 or any(part == "" for part in fields[1:]):
        raise TraceRefusal(
            "trace-unparseable",
            "W| record must be W|<identity>|<class>|<scope>: " + line,
            identity=line,
        )
    _, identity, waiver_class, scope = fields
    _check_width(waiver_class, CAP_WAIVER_CLASS, field_name="waiver class", line=line)
    if waiver_class not in WAIVER_CLASSES:
        raise TraceRefusal(
            "unknown-waiver-class",
            f"waiver class outside the closed set: {waiver_class!r}",
            identity=line,
        )
    return Waiver(
        identity=_decode_field(identity),
        waiver_class=waiver_class,
        scope=_decode_field(scope),
    )


def parse_record_set(lines: Sequence[str]) -> RecordSet:
    """Parse authored record lines into a :class:`RecordSet`.

    Every structural rule that can be decided from one line alone is decided
    here and fails closed. Cross-record rules (completeness, conflicts, merge
    constraints, digests) are decided in :func:`build`, which has the material
    list they are checked against.
    """
    out = RecordSet()
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        identity_line = _TRACE_IDENTITY_LINE_RE.match(line)
        if identity_line:
            if out.declared_identity is not None:
                raise TraceRefusal(
                    "trace-unparseable",
                    "the record block declares more than one Trace-identity",
                    identity=line,
                )
            out.declared_identity = identity_line.group(1)
            continue
        if line.startswith("X|"):
            out.dispositions.append(_parse_disposition(line))
        elif line.startswith("O|"):
            out.origins.append(_parse_origin(line))
        elif line.startswith("W|"):
            out.waivers.append(_parse_waiver(line))
        else:
            out.records.append(_parse_typed_record(line))
    return out


def _dedupe(items: Sequence, key) -> List:
    """Collapse byte-identical records, preserving first-seen order (§ 4.3)."""
    seen = set()
    out = []
    for item in items:
        marker = key(item)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(item)
    return out


def _require_sorted(items: Sequence, key, *, label: str) -> None:
    keys = [key(item) for item in items]
    if keys != sorted(keys):
        raise TraceRefusal(
            "trace-unparseable",
            f"{label} records are not in the contract's total sort order",
        )


# ---------------------------------------------------------------------------
# The bound function (§ 8.3).
# ---------------------------------------------------------------------------
def b_coder(n: int) -> int:
    """Exact width of the coder projection at ``n`` material criteria."""
    return 23 + 24 * n


def b_routine(n: int, e: int, s: int, r: int, w: int, x: int, o: int) -> int:
    """The routine-context ceiling, anchored at the accepted 2,581 B shape.

    Every term is signed: a shape below the reference in a variable is allowed
    correspondingly less, which is why a one-criterion task carrying full
    inherited provenance can fail ``bound-exceeded`` (§ 10.1 rung 3).
    """
    return 260 + 96 * n + 146 * e + 112 * s + 94 * r + 183 * w + 114 * x + 19 * o


# ---------------------------------------------------------------------------
# The bound trace.
# ---------------------------------------------------------------------------
@dataclass
class Finding:
    """One fail-closed observation, carrying its contract code."""

    code: str
    boundary: str
    detail: str
    identity: str = ""

    def as_dict(self) -> Dict[str, str]:
        return {
            "code": self.code,
            "boundary": self.boundary,
            "detail": self.detail,
            "identity": self.identity,
        }


@dataclass
class Coverage:
    """Derived coverage records (§ 5). Not part of the hashed body."""

    criterion_complete: bool
    source_records: List[str]
    source_complete: bool
    request_records: List[str]
    request_complete: bool
    uncovered_sources: List[str]
    uncovered_requests: List[str]


@dataclass
class Trace:
    """A material list bound to a validated record set.

    Construction never partially succeeds: :func:`build` raises
    :class:`TraceRefusal` on any structural (readiness) code, so every
    ``Trace`` instance is a body that may be projected. Closure-scoped
    findings are reported by :meth:`closure_findings`, not raised.
    """

    criteria: List[Criterion]
    records: List[Record]
    dispositions: List[Disposition]
    origins: List[Origin]
    waivers: List[Waiver]
    sources: List[str]
    excerpts: List[str]
    coverage: Coverage

    # -- serialization ----------------------------------------------------
    def trace_body(self) -> str:
        """The hashed body: trace records, then ``X|``, then ``O|`` (§ 4.2)."""
        lines = [rec.line() for rec in self.records]
        lines += [d.line() for d in self.dispositions]
        lines += [o.line() for o in self.origins]
        return "".join(line + "\n" for line in lines)

    def trace_identity(self) -> str:
        return body_identity(self.trace_body())

    def criterion_body(self) -> str:
        """The body named by ``Computed-from``: one normalized text per line."""
        return "".join(normalize(c.text) + "\n" for c in self.criteria)

    def criterion_body_identity(self) -> str:
        return body_identity(self.criterion_body())

    def coder_projection(self) -> str:
        """Complete, deidentified, and carrying no governance identity (§ 6.1)."""
        identity16 = self.trace_identity()[len("sha256:") :][:16]
        exempt = {
            rec.ordinal for rec in self.records if rec.is_exemption
        }
        lines = [f"trace {identity16}"]
        for crit in self.criteria:
            token = "exempt" if crit.ordinal in exempt else "traced"
            lines.append(f"{crit.ordinal} {crit.digest} {token}")
        return "".join(line + "\n" for line in lines)

    def determination_template(self) -> List[str]:
        out: List[str] = []
        for crit in self.criteria:
            out.append(f"D1 {crit.ordinal}: <pass|fail> reason:<code|->")
            out.append(f"D2 {crit.ordinal}: <pass|fail> reason:<code|->")
        return out

    def reviewer_projection(
        self, determinations: Optional[Sequence[str]] = None
    ) -> str:
        """The authoritative PM-computed provenance block (§ 6.2)."""
        cov = self.coverage
        lines = [
            "Computed-by: pm",
            f"Computed-from: spec-body {self.criterion_body_identity()}",
            f"Trace-identity: {self.trace_identity()}",
            f"Edges: {len(self.records)}",
        ]
        lines += [rec.line() for rec in self.records]
        lines.append(
            "Criterion-coverage: "
            + ("complete" if cov.criterion_complete else "incomplete")
        )
        lines.append(
            "Source-coverage: " + ("complete" if cov.source_complete else "incomplete")
        )
        lines += cov.source_records
        lines.append(
            "Request-coverage: "
            + ("complete" if cov.request_complete else "incomplete")
        )
        lines += cov.request_records
        lines.append(f"Waivers: {len(self.waivers)}")
        lines += [wv.line() for wv in self.waivers]
        lines.append(
            "Conflicts: none"
            if not self.dispositions
            else f"Conflicts: {len(self.dispositions)}"
        )
        lines += [d.line() for d in self.dispositions]
        lines += [o.line() for o in self.origins]
        lines += list(determinations) if determinations else self.determination_template()
        return "".join(line + "\n" for line in lines)

    def completion_evidence(self, determinations: Sequence[str]) -> str:
        """The closure record: trace identity plus the determination lines."""
        lines = [f"Trace-identity: {self.trace_identity()}", *determinations]
        return "".join(line + "\n" for line in lines)

    # -- shape and bounds -------------------------------------------------
    def shape(self) -> Dict[str, int]:
        # ``s`` and ``r`` count emitted ``S|`` / ``R|`` records, not enumerated
        # identities: a waived source or excerpt is carried by its ``W|``
        # record instead and is paid for at the 183 B waiver rate (§ 8.3).
        return {
            "n": len(self.criteria),
            "e": len(self.records),
            "s": len(self.coverage.source_records),
            "r": len(self.coverage.request_records),
            "w": len(self.waivers),
            "x": len(self.dispositions),
            "o": len(self.origins),
        }

    def bounds(self) -> Dict[str, int]:
        shape = self.shape()
        coder = measure(self.coder_projection())
        reviewer = measure(self.reviewer_projection())
        allowed = b_routine(**shape)
        return {
            **shape,
            "coder_bytes": coder,
            "reviewer_bytes": reviewer,
            "routine_bytes": coder + reviewer,
            "b_coder": b_coder(shape["n"]),
            "b_routine": allowed,
            "head": allowed - (coder + reviewer),
            "est_tokens_coder": est_tokens(coder),
            "est_tokens_reviewer": est_tokens(reviewer),
        }

    # -- closure ----------------------------------------------------------
    def conflicts(self) -> List[Tuple[str, str, List[str]]]:
        """Same-class distinct-identity groups requiring a disposition (§ 4.4).

        Returns ``(ordinal, precedence-class, identities)`` triples. Repeated
        occurrence never trips this: the check keys on *distinct* source
        identities, so repetition, cross-class authority, and same-class
        contradiction stay three different facts.
        """
        by_criterion: Dict[str, Dict[str, List[str]]] = {}
        for rec in self.records:
            if rec.is_exemption:
                continue
            klass = rec.precedence_class
            bucket = by_criterion.setdefault(rec.ordinal, {}).setdefault(klass, [])
            if rec.source_identity not in bucket:
                bucket.append(rec.source_identity)
        out: List[Tuple[str, str, List[str]]] = []
        for ordinal in sorted(by_criterion):
            for klass in sorted(by_criterion[ordinal]):
                identities = by_criterion[ordinal][klass]
                if len(identities) > 1:
                    out.append((ordinal, klass, sorted(identities)))
        return out

    def closure_findings(self) -> List[Finding]:
        """Task-scoped closure findings derivable without a reviewer verdict.

        These are the mechanical half of D2: unresolved same-class conflict and
        source / request non-coverage. The semantic half — whether a reached
        source actually governs its criterion — is the reviewer's and is
        recorded on the determination lines.
        """
        findings: List[Finding] = []
        disposed = {d.ordinal for d in self.dispositions}
        for ordinal, klass, identities in self.conflicts():
            if ordinal not in disposed:
                findings.append(
                    Finding(
                        "unresolved-source-conflict",
                        "closure",
                        f"{ordinal} carries {len(identities)} distinct "
                        f"{klass}-class source identities and no X| disposition",
                        ordinal,
                    )
                )
        for identity in self.coverage.uncovered_sources:
            findings.append(
                Finding(
                    "source-uncovered",
                    "closure",
                    "authoritative source claimed by no criterion and not waived",
                    identity,
                )
            )
        for identity in self.coverage.uncovered_requests:
            findings.append(
                Finding(
                    "request-uncovered",
                    "closure",
                    "operator excerpt claimed by no operator-request edge and not waived",
                    identity,
                )
            )
        return findings

    def as_record(self) -> Dict[str, object]:
        cov = self.coverage
        return {
            "trace_identity": self.trace_identity(),
            "criterion_body_identity": self.criterion_body_identity(),
            "criteria": [
                {
                    "ordinal": c.ordinal,
                    "digest12": c.digest,
                    "origin_list": c.origin_list,
                }
                for c in self.criteria
            ],
            "edges": len(self.records),
            "exemptions": sum(1 for rec in self.records if rec.is_exemption),
            "criterion_coverage": "complete" if cov.criterion_complete else "incomplete",
            "source_coverage": "complete" if cov.source_complete else "incomplete",
            "request_coverage": "complete" if cov.request_complete else "incomplete",
            "uncovered_sources": list(cov.uncovered_sources),
            "uncovered_requests": list(cov.uncovered_requests),
            "bounds": self.bounds(),
            "closure_findings": [f.as_dict() for f in self.closure_findings()],
        }


# ---------------------------------------------------------------------------
# Derivation and structural validation.
# ---------------------------------------------------------------------------
def _ordinal(index: int) -> str:
    return f"C{index:02d}"


def _claimed_map(records: Sequence[Record], identity: str) -> List[str]:
    ordinals = sorted(
        {
            rec.ordinal
            for rec in records
            if not rec.is_exemption and rec.source_identity == identity
        }
    )
    return ordinals


def _coverage(
    records: Sequence[Record],
    sources: Sequence[str],
    excerpts: Sequence[str],
    waived: Iterable[str],
    *,
    criterion_complete: bool,
) -> Coverage:
    waived_set = set(waived)
    source_lines: List[str] = []
    uncovered_sources: List[str] = []
    for identity in sorted(
        (s for s in sources if s not in waived_set), key=lambda v: v.encode("utf-8")
    ):
        claimed = _claimed_map(records, identity)
        if claimed:
            source_lines.append(f"S|{identity}|claimed:{','.join(claimed)}")
        else:
            source_lines.append(f"S|{identity}|uncovered")
            uncovered_sources.append(identity)
    request_lines: List[str] = []
    uncovered_requests: List[str] = []
    for identity in sorted(
        (x for x in excerpts if x not in waived_set), key=lambda v: v.encode("utf-8")
    ):
        claimed = _claimed_map(records, identity)
        if claimed:
            request_lines.append(f"R|{identity}|claimed:{','.join(claimed)}")
        else:
            request_lines.append(f"R|{identity}|uncovered")
            uncovered_requests.append(identity)
    return Coverage(
        criterion_complete=criterion_complete,
        source_records=source_lines,
        source_complete=not uncovered_sources,
        request_records=request_lines,
        request_complete=not uncovered_requests,
        uncovered_sources=uncovered_sources,
        uncovered_requests=uncovered_requests,
    )


def build(
    *,
    spec_acceptance: Sequence[str],
    task_acceptance: Sequence[str],
    record_set: RecordSet,
    sources: Sequence[str],
    excerpts: Sequence[str],
    enforce_bounds: bool = True,
) -> Trace:
    """Bind a record set to the union material set and validate it closed.

    Raises :class:`TraceRefusal` carrying a § 9.1 readiness code on the first
    structural violation, in the contract's own detection order. The material
    set is the union of the two acceptance enumerations reduced by the merge
    rule; ordinals are assigned by position in that reduced list, so two
    producers reading the same task and specification derive the same body.
    """
    spec_items = [normalize(t) for t in spec_acceptance]
    task_items = [normalize(t) for t in task_acceptance]
    if not spec_items and not task_items:
        raise TraceRefusal(
            "trace-incomplete",
            "the governing contract enumerates no acceptance item to trace",
        )

    records = _dedupe(record_set.records, lambda rec: rec.line())
    dispositions = _dedupe(record_set.dispositions, lambda d: d.line())
    origins = _dedupe(record_set.origins, lambda o: o.line())
    waivers = _dedupe(record_set.waivers, lambda w: w.line())

    _require_sorted(records, Record.sort_key, label="trace")
    _require_sorted(dispositions, Disposition.sort_key, label="X|")
    _require_sorted(origins, Origin.sort_key, label="O|")
    _require_sorted(waivers, Waiver.sort_key, label="W|")

    if not records:
        raise TraceRefusal(
            "trace-missing",
            "no trace record for a task whose governing contract has material criteria",
        )

    # --- merge rule (§ 2.1.2), applied before ordinals are assigned -------
    spec_digests = {digest12(t): t for t in spec_items}
    task_digests: Dict[str, str] = {}
    for text in task_items:
        task_digests.setdefault(digest12(text), text)

    merged_away: Dict[str, Origin] = {}
    for origin in origins:
        if origin.digest not in task_digests:
            raise TraceRefusal(
                "criterion-digest-mismatch",
                "O| origin digest matches no enumerated task acceptance item",
                identity=origin.line(),
            )
        if origin.digest in merged_away:
            raise TraceRefusal(
                "trace-unparseable",
                "an origin appears in more than one O| record",
                identity=origin.line(),
            )
        merged_away[origin.digest] = origin

    material: List[Criterion] = []
    for text in spec_items:
        material.append(
            Criterion(ordinal="", digest=digest12(text), text=text, origin_list="spec")
        )
    for text in task_items:
        if digest12(text) in merged_away:
            continue
        material.append(
            Criterion(ordinal="", digest=digest12(text), text=text, origin_list="task")
        )
    # A criterion restated identically in both lists carries one ordinal; the
    # positional rule keeps the first occurrence, which is the specification's.
    deduped: List[Criterion] = []
    seen_digests = set()
    for crit in material:
        if crit.digest in seen_digests:
            continue
        seen_digests.add(crit.digest)
        deduped.append(crit)
    if len(deduped) > MAX_CRITERIA:
        raise TraceRefusal(
            "trace-unparseable",
            f"a task carries at most {MAX_CRITERIA} material criteria; got {len(deduped)}",
        )
    criteria = [
        Criterion(
            ordinal=_ordinal(index),
            digest=crit.digest,
            text=crit.text,
            origin_list=crit.origin_list,
        )
        for index, crit in enumerate(deduped, start=1)
    ]
    by_ordinal = {c.ordinal: c for c in criteria}
    by_digest = {c.digest: c for c in criteria}

    # --- per-record binding ---------------------------------------------
    for rec in records:
        crit = by_ordinal.get(rec.ordinal)
        if crit is None:
            raise TraceRefusal(
                "trace-unparseable",
                f"record names ordinal {rec.ordinal}, which is no material criterion",
                identity=rec.line(),
            )
        if rec.digest != crit.digest:
            raise TraceRefusal(
                "criterion-digest-mismatch",
                f"{rec.ordinal} records digest {rec.digest} but its text hashes to "
                f"{crit.digest}",
                identity=rec.line(),
            )
        if rec.type == "spec":
            clause = SPEC_CLAUSE_RE.match(rec.source_identity).group(1)
            if clause == full_identity(crit.text):
                raise TraceRefusal(
                    "spec-self-reference",
                    f"{rec.ordinal} traces to a spec clause identical to its own text",
                    identity=rec.line(),
                )

    # --- criterion-side completeness and exemption conflict (§ 4.3) ------
    typed_by_ordinal: Dict[str, List[Record]] = {}
    exempt_by_ordinal: Dict[str, List[Record]] = {}
    for rec in records:
        bucket = exempt_by_ordinal if rec.is_exemption else typed_by_ordinal
        bucket.setdefault(rec.ordinal, []).append(rec)
    for crit in criteria:
        typed = typed_by_ordinal.get(crit.ordinal, [])
        exempt = exempt_by_ordinal.get(crit.ordinal, [])
        if not typed and not exempt:
            raise TraceRefusal(
                "trace-incomplete",
                f"{crit.ordinal} carries no typed edge and no exemption",
                identity=crit.digest,
            )
        if typed and exempt:
            raise TraceRefusal(
                "exemption-conflict",
                f"{crit.ordinal} carries both a typed edge and an exemption",
                identity=crit.digest,
            )
        if len({rec.type for rec in exempt}) > 1:
            raise TraceRefusal(
                "exemption-conflict",
                f"{crit.ordinal} carries two exemptions with different reasons",
                identity=crit.digest,
            )

    # --- origin record constraints (§ 4.6) -------------------------------
    for origin in origins:
        covering = by_ordinal.get(origin.ordinal)
        if covering is None:
            raise TraceRefusal(
                "trace-unparseable",
                f"O| names ordinal {origin.ordinal}, which is no material criterion",
                identity=origin.line(),
            )
        if covering.origin_list != "spec":
            raise TraceRefusal(
                "trace-unparseable",
                "a merge may only name a criterion whose governing text is a "
                "specification acceptance item",
                identity=origin.line(),
            )
        if origin.digest in by_digest:
            raise TraceRefusal(
                "trace-unparseable",
                "a merged origin may not also carry its own ordinal",
                identity=origin.line(),
            )
        if exempt_by_ordinal.get(origin.ordinal):
            raise TraceRefusal(
                "exemption-conflict",
                "an origin may not be merged onto a criterion that asserts no "
                "upstream authority governs it",
                identity=origin.line(),
            )

    # --- origin-side completeness (§ 5.1) --------------------------------
    for text in spec_items + task_items:
        item_digest = digest12(text)
        if item_digest in by_digest or item_digest in merged_away:
            continue
        raise TraceRefusal(
            "trace-incomplete",
            "an enumerated acceptance item is neither a material criterion nor "
            "the subject of an O| record",
            identity=item_digest,
        )

    # --- disposition binding ---------------------------------------------
    for disp in dispositions:
        if disp.ordinal not in by_ordinal:
            raise TraceRefusal(
                "trace-unparseable",
                f"X| names ordinal {disp.ordinal}, which is no material criterion",
                identity=disp.line(),
            )

    waived = {wv.identity for wv in waivers}
    coverage = _coverage(
        records, sources, excerpts, waived, criterion_complete=True
    )
    trace = Trace(
        criteria=criteria,
        records=records,
        dispositions=dispositions,
        origins=origins,
        waivers=waivers,
        sources=list(sources),
        excerpts=list(excerpts),
        coverage=coverage,
    )

    declared = record_set.declared_identity
    if declared is not None and declared != trace.trace_identity():
        raise TraceRefusal(
            "trace-identity-mismatch",
            f"declared {declared} but the serialized body hashes to "
            f"{trace.trace_identity()}",
            identity=declared,
        )

    if enforce_bounds:
        measured = trace.bounds()
        if measured["coder_bytes"] != measured["b_coder"]:
            raise TraceRefusal(
                "bound-exceeded",
                f"coder projection measured {measured['coder_bytes']} B against an "
                f"exact bound of {measured['b_coder']} B",
            )
        if measured["routine_bytes"] > measured["b_routine"]:
            raise TraceRefusal(
                "bound-exceeded",
                f"routine context measured {measured['routine_bytes']} B against "
                f"B_routine {measured['b_routine']} B — walk the § 10.1 ladder; "
                "never truncate",
            )
    return trace


# ---------------------------------------------------------------------------
# The conformance anchor (§ 8.4).
# ---------------------------------------------------------------------------
_ANCHOR_CRITERIA: Tuple[str, ...] = (
    "The clean fixture passes under baseline and candidate options.",
    "An omitted or contradictory upstream source produces a distinct fail-closed "
    "result under a qualifying candidate.",
    "Request drift reaches the reviewer through independently attributable "
    "evidence and does not enter raw coder context.",
    "Immediate-contract compliance can pass while upstream-intent adequacy fails.",
    "Repeating an otherwise identical source occurrence remains observable.",
)

_ANCHOR_RECORDS: Tuple[str, ...] = (
    "C01|50ee216fd65a|operator-request|REQ-003 sha256:bd4708b405fcd06ff278f20ea1d648593731de0acef939836bfe41efc2927c83|evidence order 3, observed 2026-08-14|1",
    "C01|50ee216fd65a|standard|Cartopian protocol/CONVENTIONS.md and protocol/RISK_AND_PRACTICE.md|installed Cartopian v1.6.40|1",
    "C02|31600474ea01|requirement|Cartopian REQUIREMENTS.md and STANDARDS.md|active Product Refinement contract as of 2026-08-14|1",
    "C03|315b7c625696|operator-request|REQ-002 sha256:00691c510e84362c4ce4c235eb1656c73f92e803c7865e410aa250154704fd61|evidence order 2, observed 2026-08-14|1",
    "C04|d9cd2134d111|decision|project-management-source sha256:223aec40379a1c8cfe7aabe536ea86c2828aecd68c61052a09b12ae898612ce4|locked 2026-08-14|1",
    "C04|d9cd2134d111|spec|spec-clause sha256:a9a822e60b9a1d42ed2d69239bb783d5dc60ab96ecc6671dbbcc69b37faeb91a|locked 2026-08-14|1",
    "C05|ed1d221431c3|plan-item|project-management-source sha256:6132460bc1f13d2f5b84bcc4a7e37b56f9087d4b782ac7844ad90510368f241d|operator-approved three-track Phase 05 decomposition as of 2026-08-14|1",
    "W|REQ-001 sha256:3587c427c1d9aeb2341afdb0ea11e2f67d1b3a2a38d13b4522e852507901a346|procedural-authorization|grants documentation access for this assignment; states no product behavior",
)

_ANCHOR_SOURCES: Tuple[str, ...] = (
    "Cartopian REQUIREMENTS.md and STANDARDS.md",
    "Cartopian protocol/CONVENTIONS.md and protocol/RISK_AND_PRACTICE.md",
    "project-management-source sha256:223aec40379a1c8cfe7aabe536ea86c2828aecd68c61052a09b12ae898612ce4",
    "project-management-source sha256:6132460bc1f13d2f5b84bcc4a7e37b56f9087d4b782ac7844ad90510368f241d",
)

_ANCHOR_EXCERPTS: Tuple[str, ...] = (
    "REQ-001 sha256:3587c427c1d9aeb2341afdb0ea11e2f67d1b3a2a38d13b4522e852507901a346",
    "REQ-002 sha256:00691c510e84362c4ce4c235eb1656c73f92e803c7865e410aa250154704fd61",
    "REQ-003 sha256:bd4708b405fcd06ff278f20ea1d648593731de0acef939836bfe41efc2927c83",
)


def reference_trace() -> Trace:
    """Rebuild the five-criterion budget fixture from this serializer."""
    return build(
        spec_acceptance=list(_ANCHOR_CRITERIA),
        task_acceptance=[],
        record_set=parse_record_set(_ANCHOR_RECORDS),
        sources=list(_ANCHOR_SOURCES),
        excerpts=list(_ANCHOR_EXCERPTS),
    )


def conformance_anchor() -> Dict[str, object]:
    """Measure the reference shape and report whether it still anchors.

    An implementation whose reference-shape bodies do not reproduce 143 /
    2,438 / 2,581 B and the two accepted identities has changed the
    serialization, not merely the content — which § 9.1 routes to
    ``trace-unparseable`` at readiness.
    """
    trace = reference_trace()
    coder = trace.coder_projection()
    reviewer = trace.reviewer_projection()
    measured = {
        "shape": tuple(trace.shape().values()),
        "coder_bytes": measure(coder),
        "reviewer_bytes": measure(reviewer),
        "routine_bytes": measure(coder) + measure(reviewer),
        "trace_identity": trace.trace_identity(),
        "reviewer_identity": body_identity(reviewer),
        "criterion_body_identity": trace.criterion_body_identity(),
    }
    measured["conforms"] = (
        measured["shape"] == REFERENCE_SHAPE
        and measured["coder_bytes"] == REFERENCE_CODER_BYTES
        and measured["reviewer_bytes"] == REFERENCE_REVIEWER_BYTES
        and measured["routine_bytes"] == REFERENCE_ROUTINE_BYTES
        and measured["trace_identity"] == REFERENCE_TRACE_IDENTITY
        and measured["reviewer_identity"] == REFERENCE_REVIEWER_IDENTITY
        and measured["criterion_body_identity"] == REFERENCE_CRITERION_BODY_IDENTITY
    )
    return measured


def assert_conformance_anchor() -> None:
    """Fail closed when the serializer has drifted off the accepted budget."""
    anchor = conformance_anchor()
    if not anchor["conforms"]:
        raise TraceRefusal(
            "trace-unparseable",
            "the reference-shape conformance anchor does not reproduce: measured "
            f"{anchor['coder_bytes']} coder + {anchor['reviewer_bytes']} reviewer "
            f"bytes against the accepted {REFERENCE_CODER_BYTES} + "
            f"{REFERENCE_REVIEWER_BYTES}",
            identity=str(anchor["trace_identity"]),
        )


# ---------------------------------------------------------------------------
# The on-demand diagnostic body (§ 13.3).
# ---------------------------------------------------------------------------
def diagnostic_body(
    trace: Trace,
    finding: Finding,
    *,
    scopes: Optional[Dict[str, str]] = None,
    statuses: Optional[Dict[str, str]] = None,
    rationale: str = "",
) -> str:
    """Render the failure-only body carrying every non-routine field.

    Source ``Status:``, source ``Scope:``, precedence derivation, edge
    rationale, and merge rationale live here and nowhere else: they were
    removed from every always-loaded body by the marginal-value rule, so they
    are loaded only when a failure needs them.
    """
    scopes = scopes or {}
    statuses = statuses or {}
    lines = [
        f"Error: {finding.code}",
        f"Detected-at: {finding.boundary}",
        f"Enforcement-point: {finding.boundary}",
        f"Trace-identity: {trace.trace_identity()}",
    ]
    ordinal = finding.identity if ORDINAL_RE.match(finding.identity or "") else ""
    if ordinal:
        crit = next((c for c in trace.criteria if c.ordinal == ordinal), None)
        if crit is not None:
            lines.append(f"Criterion: {crit.ordinal} {crit.digest}")
        involved = [rec for rec in trace.records if rec.ordinal == ordinal]
        lines += [rec.line() for rec in involved]
        identities = sorted({rec.source_identity for rec in involved if not rec.is_exemption})
        lines.append(f"Distinct-source-identities: {len(identities)}")
        classes = sorted({rec.precedence_class for rec in involved if not rec.is_exemption})
        lines.append(f"Precedence-class: {', '.join(classes) if classes else '-'}")
        for index, identity in enumerate(identities, start=1):
            lines.append(f"Source-{index}-status: {statuses.get(identity, 'unknown')}")
            lines.append(f"Source-{index}-scope: {scopes.get(identity, 'unknown')}")
    else:
        lines.append(f"Identity: {finding.identity or '-'}")
    lines.append(f"Detail: {finding.detail}")
    if rationale:
        lines.append(f"Edge-rationale: {rationale}")
    lines.append(
        "Determination-blocked: "
        + ("D2" if finding.code in CLOSURE_CODES else "readiness")
    )
    lines.append("Recovery-owner: pm")
    lines.append(
        "Recovery-action: re-derive the trace from current sources and reissue "
        "the assignment at readiness"
    )
    return "".join(line + "\n" for line in lines)


# ---------------------------------------------------------------------------
# Closure determinations (§ 7).
# ---------------------------------------------------------------------------
_DETERMINATION_RE = re.compile(
    r"^(D1|D2)\s+(C\d{2}|task):\s+(pass|fail)\s+reason:(\S+)\s*$"
)


@dataclass
class Determination:
    determination: str
    scope: str
    verdict: str
    reason: str

    def line(self) -> str:
        return f"{self.determination} {self.scope}: {self.verdict} reason:{self.reason}"


@dataclass
class ClosureResult:
    determinations: List[Determination]
    declared_identity: Optional[str]
    blockers: List[Finding]

    @property
    def ok(self) -> bool:
        return not self.blockers

    def as_record(self) -> Dict[str, object]:
        return {
            "ok": self.ok,
            "declared_trace_identity": self.declared_identity,
            "determinations": [
                {
                    "determination": d.determination,
                    "scope": d.scope,
                    "verdict": d.verdict,
                    "reason": d.reason,
                }
                for d in self.determinations
            ],
            "blockers": [b.as_dict() for b in self.blockers],
        }


def parse_determinations(text: str) -> Tuple[List[Determination], Optional[str]]:
    """Read the ``## Closure determinations`` block of a review file."""
    lines = _section_lines(text, DETERMINATION_SECTION_HEADING)
    if lines is None:
        return [], None
    declared: Optional[str] = None
    out: List[Determination] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        identity = _TRACE_IDENTITY_LINE_RE.match(line)
        if identity:
            declared = identity.group(1)
            continue
        match = _DETERMINATION_RE.match(line)
        if match:
            out.append(
                Determination(
                    determination=match.group(1),
                    scope=match.group(2),
                    verdict=match.group(3),
                    reason=match.group(4),
                )
            )
    return out, declared


def evaluate_closure(
    trace: Trace, review_text: str, *, attributed_to: str = ""
) -> ClosureResult:
    """Evaluate D1 and D2 against a reviewer-authored determination block.

    D1 and D2 are independent by construction: D1 evaluates work against
    contract, D2 evaluates contract against upstream. Neither may be inferred
    from the other, so a missing line is a blocker rather than a default pass,
    and a criterion may not record one as "same as above".
    """
    determinations, declared = parse_determinations(review_text)
    blockers: List[Finding] = []

    if not determinations:
        blockers.append(
            Finding(
                "acceptance-item-unmet",
                "closure",
                "the review records no closure determinations; D1 and D2 are "
                "required per material criterion and neither defaults to pass",
            )
        )
        return ClosureResult(determinations, declared, blockers)

    if declared is None:
        blockers.append(
            Finding(
                "trace-identity-mismatch",
                "closure",
                "the determination block names no Trace-identity, so the verdicts "
                "are unattributed to a record set",
            )
        )
    elif declared != trace.trace_identity():
        blockers.append(
            Finding(
                "trace-identity-mismatch",
                "closure",
                f"determinations were recorded against {declared} but the current "
                f"body hashes to {trace.trace_identity()}",
                declared,
            )
        )

    if not attributed_to.strip():
        blockers.append(
            Finding(
                "acceptance-item-unmet",
                "closure",
                "the determination block carries no reviewer attribution; a "
                "self-certified determination is not an independent one",
            )
        )

    seen: Dict[Tuple[str, str], Determination] = {}
    for det in determinations:
        key = (det.determination, det.scope)
        prior = seen.get(key)
        if prior is not None:
            if prior.verdict != det.verdict or prior.reason != det.reason:
                blockers.append(
                    Finding(
                        "acceptance-item-unmet"
                        if det.determination == "D1"
                        else "upstream-intent-uncovered",
                        "closure",
                        f"contradictory {det.determination} records for {det.scope}",
                        det.scope,
                    )
                )
            continue
        seen[key] = det
        allowed = (
            D1_REASONS
            if det.determination == "D1"
            else (D2_TASK_REASONS if det.scope == "task" else D2_REASONS)
        )
        if det.verdict == "pass" and det.reason != "-":
            blockers.append(
                Finding(
                    "acceptance-item-unmet",
                    "closure",
                    f"{det.determination} {det.scope} passes but carries reason "
                    f"{det.reason!r}",
                    det.scope,
                )
            )
        if det.verdict == "fail":
            if det.reason not in allowed:
                blockers.append(
                    Finding(
                        "acceptance-item-unmet"
                        if det.determination == "D1"
                        else "upstream-intent-uncovered",
                        "closure",
                        f"{det.determination} {det.scope} fails with reason "
                        f"{det.reason!r}, outside the closed set {allowed}",
                        det.scope,
                    )
                )
            else:
                blockers.append(
                    Finding(det.reason, "closure", f"{det.determination} failed", det.scope)
                )

    if ("D1", "task") in seen:
        blockers.append(
            Finding(
                "acceptance-item-unmet",
                "closure",
                "D1 is recorded per material criterion and has no task scope",
                "task",
            )
        )
    for crit in trace.criteria:
        for name in ("D1", "D2"):
            if (name, crit.ordinal) not in seen:
                blockers.append(
                    Finding(
                        "acceptance-item-unmet"
                        if name == "D1"
                        else "upstream-intent-uncovered",
                        "closure",
                        f"{name} is missing for {crit.ordinal}; a missing "
                        "determination blocks approval and never defaults to pass",
                        crit.ordinal,
                    )
                )

    # Task-scoped D2 findings the body already proves must be recorded, or the
    # determination set contradicts its own evidence (§ 7.3).
    mechanical = trace.closure_findings()
    recorded_task_reasons = {
        det.reason
        for det in determinations
        if det.determination == "D2" and det.scope == "task" and det.verdict == "fail"
    }
    for finding in mechanical:
        if finding.code in D2_TASK_REASONS and finding.code not in recorded_task_reasons:
            blockers.append(
                Finding(
                    finding.code,
                    "closure",
                    f"{finding.detail}; the reviewer recorded no task-scoped D2 "
                    "failure for it",
                    finding.identity,
                )
            )
        elif finding.code == "unresolved-source-conflict":
            det = seen.get(("D2", finding.identity))
            if det is None or det.verdict != "fail":
                blockers.append(finding)
    return ClosureResult(determinations, declared, blockers)
