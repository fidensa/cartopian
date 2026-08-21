"""Bounded, deterministic prompt-effectiveness evidence.

This module implements the accepted prompt-effectiveness evidence contract:
one append-only ledger, three record kinds, six signal families, five
observation states, three budgets, and three bounded query projections.

What it can and cannot establish is narrow, and the narrowness is the point.
The ledger can show that a family of failure recurs and how often, compare a
unit against the distribution of prior units, show which check or reason code
keeps failing, and — decisively — distinguish "observed none" from "never
measured". It cannot show that a prompt *caused* an outcome. Correlation
between a prompt property and an outcome is a hypothesis, never a finding, and
nothing here computes a score, an index, a grade, or a ranking.

Four rules govern every write:

* **Zero routine bytes.** The ledger is never an input to startup, status,
  ``next-action``, ``task-bundle``, prompt writing, dispatch, handoff, wait,
  review context, or either projection of the traceability contract. It is
  invisible until someone asks for it.
* **Fail closed, never fail blocking.** An emission failure is recorded as
  ``omitted`` and must not block a lifecycle transition. Measurement that can
  stop work is a hazard nobody asked to buy.
* **No free text.** Every field is an integer, a closed-vocabulary token, a
  date, or an identifier from an existing numbering contract. A boundary with
  only prose to emit emits nothing and the family reads ``omitted``.
* **Absence is reported, never inferred.** ``observed 0``, ``unavailable``,
  ``omitted``, ``not-applicable``, and ``not-yet-observable`` are five
  distinct values and never collapse into one another.

Retention is plan-bounded: every record carries its plan window ``p``, only
the current plan's ``p`` may be written, and at plan closeout the log is
deleted as a mediated delete — after the superseding summaries and the
closing projection are produced, never before.

Standard library only. The ledger is derived evidence, never authority: a lost
or corrupt log loses history and never invalidates a task, review, verdict, or
determination.
"""
from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from cli import provenance

#: The one artifact this contract adds, beside the accepted journal.
LOG_BASENAME = "prompt-evidence.log"
LOG_RELPATH = f"{provenance.PROVENANCE_DIRNAME}/{LOG_BASENAME}"

#: Schema version carried by every record (§ 12.7).
SCHEMA_VERSION = 1

#: The six signal families, closed. A seventh is an amendment.
FAMILIES: Tuple[str, ...] = ("CLR", "OMR", "RRR", "RRG", "PCC", "PAD")

#: The five observation states, closed.
STATES: Tuple[str, ...] = (
    "observed",
    "unavailable",
    "omitted",
    "not-applicable",
    "not-yet-observable",
)

#: Families that carry a denominator in ``q`` (§ 12.4).
DENOMINATOR_FAMILIES: Tuple[str, ...] = ("OMR", "RRR", "PCC")

#: Families that emit ``E`` records. ``OMR``'s events *are* its ``D`` records.
EVENT_FAMILIES: Tuple[str, ...] = ("CLR", "RRR", "RRG", "PCC", "PAD")

#: ``D`` record address kinds.
ADDRESS_KINDS: Tuple[str, ...] = ("det", "cq", "fnd")

#: Determination ids consumed from the traceability contract.
DETERMINATION_IDS: Tuple[str, ...] = ("D1", "D2")

#: Verdicts and transitions the ``x`` field accepts.
VERDICTS: Tuple[str, ...] = ("approve", "request-changes", "reject")
TRANSITIONS: Tuple[str, ...] = ("in-review>in-progress", "in-review>open")

#: Key order per record kind. Two conforming implementations emit
#: byte-identical lines for identical observations because this order, and the
#: absence of optional whitespace, are both normative.
KEY_ORDER: Dict[str, Tuple[str, ...]] = {
    "U": ("v", "k", "p", "u", "d", "f", "q"),
    "E": ("v", "k", "p", "u", "d", "f", "o", "n", "x", "a"),
    "D": ("v", "k", "p", "u", "d", "f", "t", "n", "c", "r", "g", "h", "s", "a"),
}

#: Closed field-width table in exact UTF-8 bytes (§ 12.5). An over-width value
#: is a rejected emission, never a truncation: a truncated identifier is a
#: wrong identifier.
WIDTH_CAPS: Dict[str, int] = {
    "v": 2,
    "k": 1,
    "p": 12,
    "u": 16,
    "d": 10,
    "f": 3,
    "state": 18,
    "o": 3,
    "n": 12,
    "x": 24,
    "a": 24,
    "t": 3,
    "c": 3,
    "g": 4,
    "h": 27,
    "r": 26,
    "s": 7,
    "q": 12,
}

#: The three selected budgets (DEC-067 Decision 5).
ROUTINE_CONTEXT_BUDGET_BYTES = 0
RECORD_CAP = 64
RESERVED_SUMMARY_SLOTS = 2
ORDINARY_ALLOWANCE = RECORD_CAP - RESERVED_SUMMARY_SLOTS
QUERY_CAPS: Dict[str, int] = {"units": 50, "determinations": 200, "events": 200}

#: Worst-case answer bounds, derived from the § 15.0 widths (§ 15.6).
ANSWER_BOUNDS: Dict[str, int] = {
    "units": 50 * 291 + 50 * 52 + 70,
    "determinations": 200 * 71 + 200 * 52 + 70,
    "events": 200 * 68 + 200 * 52 + 70,
}

#: Per-kind maximum record widths, LF-inclusive (§ 12.5).
MAX_RECORD_BYTES: Dict[str, int] = {
    "U": 397,
    "E-CLR": 117,
    "E-PAD": 117,
    "E-RRR": 156,
    "E-RRG": 125,
    "E-PCC": 111,
    "D-det": 179,
    "D-cq": 186,
    "D-fnd": 152,
}

#: Hard per-unit ceiling: two maximum-width summaries plus the ordinary
#: allowance at the widest conformant ``E``/``D`` record.
UNIT_CEILING_BYTES = 2 * 397 + ORDINARY_ALLOWANCE * 186

UNIT_ID_RE = re.compile(r"^TASK-\d{2}-\d{3}$")
PLAN_ID_RE = re.compile(r"^PLAN-\d{3}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CRITERION_ORDINAL_RE = re.compile(r"^(C\d{2}|-)$")
GAP_ORDINAL_RE = re.compile(r"^C\d{1,2}$")
FINDING_ORDINAL_RE = re.compile(r"^F\d{1,2}$")
ARTIFACT_RE = re.compile(
    r"^(REVIEW-\d{2}-\d{3}|REPORT-\d{2}-\d{3}|DEC-\d{3}|BL-\d{3})$"
)

#: Emission outcomes returned by :func:`emit`.
WRITTEN = "written"
REJECTED = "rejected"
SUPPRESSED = "suppressed"
#: A boundary that ran again over an observation the unit already carries.
#: Lifecycle transitions are replayed — a task is closed, reopened, and closed
#: again — so the seams must be safe to re-enter without a second record.
IDEMPOTENT = "idempotent"


# ---------------------------------------------------------------------------
# Consumed vocabularies. Both are imported from their owning contracts and are
# never extended here: a value outside either set is a rejected emission, not a
# new code.
# ---------------------------------------------------------------------------
def _reason_codes() -> Tuple[str, ...]:
    from cli import acceptance_trace

    return acceptance_trace.D1_REASONS + acceptance_trace.D2_REASONS + (
        acceptance_trace.D2_TASK_REASONS
    )


def _check_names() -> Tuple[str, ...]:
    from cli import contract_review

    return contract_review.CHECK_CODES


def _severities() -> Tuple[str, ...]:
    from cli import contract_review

    return contract_review.SEVERITIES


# ---------------------------------------------------------------------------
# Plan window and storage.
# ---------------------------------------------------------------------------
def log_path(project_root: os.PathLike | str) -> Path:
    return Path(project_root) / provenance.PROVENANCE_DIRNAME / LOG_BASENAME


def current_plan_id(project_root: os.PathLike | str) -> str:
    """The plan window in flight: one past the highest closed archive.

    ``archive-plan`` allocates ``PLAN-NNN`` at closeout, so the plan currently
    running is always the next number. Deriving it rather than storing it
    keeps the window identity in one authoritative place.
    """
    archive_root = Path(project_root) / "archive"
    highest = 0
    if archive_root.is_dir():
        for entry in archive_root.iterdir():
            match = re.fullmatch(r"PLAN-(\d{3})", entry.name)
            if match and entry.is_dir():
                highest = max(highest, int(match.group(1)))
    return f"PLAN-{highest + 1:03d}"


def serialize(record: Dict[str, Any]) -> str:
    """Render one record as its normative line, LF-terminated.

    Keys are emitted in the § 12.3 order for the record's kind and nothing
    else is emitted; there is no optional whitespace anywhere in the line.
    """
    kind = record.get("k")
    order = KEY_ORDER.get(kind)
    if order is None:
        raise ValueError(f"unknown record kind: {kind!r}")
    ordered = {key: record[key] for key in order if key in record}
    extra = set(record) - set(order)
    if extra:
        raise ValueError(f"record carries keys outside its kind: {sorted(extra)}")
    return json.dumps(ordered, ensure_ascii=False, separators=(",", ":")) + "\n"


class ReadError(Exception):
    """A detected inconsistency in the ledger, reported rather than guessed."""

    def __init__(self, rule: str, detail: str) -> None:
        self.rule = rule
        self.detail = detail
        super().__init__(f"{rule}: {detail}")


@dataclass
class Ledger:
    """The current plan window's records, plus every detected inconsistency."""

    plan_id: str
    records: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[Dict[str, str]] = field(default_factory=list)
    #: Units a reader must report as ``unavailable`` — foreign plan window,
    #: unknown schema version, or an unreadable record naming them.
    unavailable_units: List[str] = field(default_factory=list)

    def for_unit(self, unit: str) -> List[Dict[str, Any]]:
        return [rec for rec in self.records if rec.get("u") == unit]

    def units(self) -> List[str]:
        seen: List[str] = []
        for rec in self.records:
            unit = rec.get("u")
            if isinstance(unit, str) and unit not in seen:
                seen.append(unit)
        return seen


def _contained_read(path: Path) -> Tuple[Optional[str], Optional[Dict[str, str]]]:
    """Read the ledger without following a link out of ``.cartopian``.

    The ledger is the only artifact this contract adds and it lives at one
    fixed path. A reader that follows a symlink there would report another
    file's contents as this project's evidence, so the leaf is opened
    ``O_NOFOLLOW``, its identity is re-checked on the open descriptor, and a
    non-regular or multiply-linked target is refused by name rather than read.
    """
    directory = path.parent
    try:
        if os.path.islink(directory):
            return None, {
                "rule": "ledger-uncontained",
                "detail": f"{directory} is a symlink, not the project's evidence directory",
            }
        if not os.path.isdir(directory):
            return None, None
        if os.path.islink(path):
            return None, {
                "rule": "ledger-uncontained",
                "detail": f"{path} is a symlink; the ledger is never followed out of place",
            }
        try:
            leaf = os.lstat(path)
        except FileNotFoundError:
            return None, None
        if not stat.S_ISREG(leaf.st_mode):
            return None, {
                "rule": "ledger-uncontained",
                "detail": f"{path} is not a regular file",
            }
        if leaf.st_nlink > 1:
            return None, {
                "rule": "ledger-uncontained",
                "detail": f"{path} carries {leaf.st_nlink} links",
            }
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        fd = os.open(path, flags)
        try:
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino) != (leaf.st_dev, leaf.st_ino):
                return None, {
                    "rule": "ledger-uncontained",
                    "detail": f"{path} changed identity between stat and open",
                }
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink > 1:
                return None, {
                    "rule": "ledger-uncontained",
                    "detail": f"{path} changed type between stat and open",
                }
            chunks: List[bytes] = []
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(fd)
    except OSError as exc:
        return None, {"rule": "ledger-unreadable", "detail": f"{path}: {exc}"}
    try:
        return b"".join(chunks).decode("utf-8"), None
    except UnicodeDecodeError as exc:
        return None, {"rule": "ledger-unreadable", "detail": f"{path}: {exc}"}


def read_ledger(
    project_root: os.PathLike | str, *, plan_id: Optional[str] = None
) -> Ledger:
    """Read one plan window's records, naming every inconsistency it finds.

    A torn final line is discarded on read; a record whose ``p`` is not the
    window's plan is a detected inconsistency; a record carrying an unknown
    ``v``, an unknown ``k``, or a shape this schema cannot interpret names the
    error and reports the affected units as ``unavailable``. None of them is
    ever silently dropped, coerced, or admitted as a record — an admitted
    record is one a projection can render without guessing.

    ``plan_id`` reads a stated window instead of the derived current one. The
    plan-closing sequence needs it: ``archive-plan`` allocates the closing
    plan's number before the sequence runs, so the derived window has already
    advanced past the records being closed.
    """
    plan_id = plan_id or current_plan_id(project_root)
    ledger = Ledger(plan_id=plan_id)
    path = log_path(project_root)
    raw, failure = _contained_read(path)
    if failure is not None:
        ledger.errors.append(failure)
        return ledger
    if raw is None:
        return ledger
    lines = raw.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    elif lines:
        # A final line with no LF is a torn write; discard it on read.
        torn = lines.pop()
        ledger.errors.append(
            {
                "rule": "torn-final-record",
                "detail": f"discarded {len(torn.encode('utf-8'))} B unterminated tail",
            }
        )
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            ledger.errors.append(
                {"rule": "record-unparseable", "detail": f"line {index}"}
            )
            continue
        if not isinstance(record, dict):
            ledger.errors.append(
                {"rule": "record-unparseable", "detail": f"line {index} is not an object"}
            )
            continue
        unit = record.get("u")

        def uninterpretable(rule: str, detail: str) -> None:
            ledger.errors.append({"rule": rule, "detail": detail})
            if isinstance(unit, str) and unit not in ledger.unavailable_units:
                ledger.unavailable_units.append(unit)

        if record.get("v") != SCHEMA_VERSION:
            uninterpretable(
                "unknown-schema-version",
                f"line {index} carries v={record.get('v')!r}",
            )
            continue
        if record.get("p") != plan_id:
            uninterpretable(
                "foreign-plan-window",
                f"line {index} carries p={record.get('p')!r}, "
                f"window is {plan_id}",
            )
            continue
        if record.get("k") not in KEY_ORDER:
            uninterpretable(
                "unknown-record-kind",
                f"line {index} carries k={record.get('k')!r}",
            )
            continue
        problem = validate(record)
        if problem is not None:
            # A record that passes `v`, `p`, and `k` can still be a shape no
            # projection can render. Admitting it would move the failure from
            # this named error to a KeyError inside a bounded query, which is
            # the opposite of reporting the unit as unavailable.
            uninterpretable("record-malformed", f"line {index}: {problem}")
            continue
        ledger.records.append(record)
    return ledger


def _append_line(project_root: Path, line: str) -> bool:
    """Append one complete line with the journal's own hardened primitive."""
    root = Path(os.path.realpath(os.fspath(project_root)))
    log_dir = root / provenance.PROVENANCE_DIRNAME
    try:
        if os.path.lexists(log_dir):
            dir_st = os.lstat(log_dir)
            if not stat.S_ISDIR(dir_st.st_mode) or stat.S_ISLNK(dir_st.st_mode):
                return False
        else:
            try:
                os.mkdir(log_dir, 0o700)
            except FileExistsError:
                pass
            dir_st = os.lstat(log_dir)
            if not stat.S_ISDIR(dir_st.st_mode) or stat.S_ISLNK(dir_st.st_mode):
                return False
        payload = line.encode("utf-8")
        flags = (
            os.O_WRONLY
            | os.O_APPEND
            | os.O_CREAT
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        path = log_dir / LOG_BASENAME
        if os.path.lexists(path):
            log_st = os.lstat(path)
            if not stat.S_ISREG(log_st.st_mode) or log_st.st_nlink > 1:
                return False
        fd = os.open(path, flags, 0o600)
        try:
            # § 12.1: a record is written by ONE append of ONE complete line.
            # Resuming a short write would interleave this record with a
            # concurrent writer's, so a short write is a failed emission — the
            # family reads `omitted` and the torn tail is discarded on read.
            written = os.write(fd, payload)
            if written != len(payload):
                return False
            os.fsync(fd)
        finally:
            os.close(fd)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Record construction and validation (§§ 12.3, 12.5, 13.5).
# ---------------------------------------------------------------------------
def _width_ok(value: Any, cap_key: str) -> bool:
    return len(str(value).encode("utf-8")) <= WIDTH_CAPS[cap_key]


def _validate_common(record: Dict[str, Any]) -> Optional[str]:
    if record.get("v") != SCHEMA_VERSION:
        return f"schema version must be {SCHEMA_VERSION}"
    unit = record.get("u")
    if not isinstance(unit, str) or not UNIT_ID_RE.match(unit):
        return "unit id is not a TASK-NN-NNN"
    if not _width_ok(unit, "u"):
        return "unit id exceeds its width cap"
    plan = record.get("p")
    if not isinstance(plan, str) or not PLAN_ID_RE.match(plan):
        return "plan id is not a PLAN-NNN"
    if not _width_ok(plan, "p"):
        return "plan id exceeds its width cap"
    date = record.get("d")
    if not isinstance(date, str) or not DATE_RE.match(date):
        return "date must be YYYY-MM-DD with no time of day"
    return None


def validate(record: Dict[str, Any]) -> Optional[str]:
    """Return the rejection reason for ``record``, or ``None`` when valid.

    Every check below is a § 13.5 rejected-emission condition: a width
    overrun, a token outside its closed domain, a unit that is not a
    ``TASK-NN-NNN``, or a boundary with only free text to offer. A rejected
    emission is never retried with a modified value — modifying a value to
    make it fit is how a ledger starts lying.
    """
    kind = record.get("k")
    if kind not in KEY_ORDER:
        return f"unknown record kind {kind!r}"
    common = _validate_common(record)
    if common is not None:
        return common
    extra = set(record) - set(KEY_ORDER[kind])
    if extra:
        return f"record carries keys outside its kind: {sorted(extra)}"

    if kind == "U":
        families = record.get("f")
        if not isinstance(families, dict) or tuple(families) != FAMILIES:
            return "U record must carry all six families in the fixed order"
        for name, value in families.items():
            if (
                not isinstance(value, list)
                or len(value) != 2
                or not isinstance(value[0], int)
                or isinstance(value[0], bool)
                or value[0] < 0
            ):
                return f"family {name} must be [count, state] with a count ≥ 0"
            if value[1] not in STATES:
                return f"family {name} carries state {value[1]!r} outside the closed set"
            if not _width_ok(value[0], "n") or not _width_ok(value[1], "state"):
                return f"family {name} exceeds a width cap"
        denominators = record.get("q")
        if not isinstance(denominators, dict) or tuple(denominators) != DENOMINATOR_FAMILIES:
            return "U record must carry q for OMR, RRR, and PCC in that order"
        for name, value in denominators.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                return f"denominator {name} must be an integer ≥ 0"
            if not _width_ok(value, "q"):
                return f"denominator {name} exceeds its width cap"
        return None

    family = record.get("f")
    if family not in FAMILIES:
        return f"family {family!r} is outside the closed set"

    if kind == "E":
        if family == "OMR":
            return "OMR emits no E record; its events are its D records"
        ordinal = record.get("o")
        if family in ("RRR", "RRG", "PCC"):
            if not isinstance(ordinal, int) or isinstance(ordinal, bool):
                return f"{family} E record requires an integer event ordinal"
            if not 1 <= ordinal <= 999 or not _width_ok(ordinal, "o"):
                return "event ordinal must be 1..999"
        elif ordinal is not None:
            return f"{family} carries no event ordinal"
        size = record.get("n")
        if family == "PCC":
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                return "PCC E record requires the exact prompt byte count"
            if not _width_ok(size, "n"):
                return "prompt byte count exceeds its width cap"
        elif size is not None:
            return f"{family} carries no n field"
        marker = record.get("x")
        if family == "RRR":
            if marker not in VERDICTS:
                return f"RRR x must be one of {VERDICTS}"
        elif family == "RRG":
            if marker not in TRANSITIONS:
                return f"RRG x must be one of {TRANSITIONS}"
        elif marker is not None:
            return f"{family} carries no x field"
        if marker is not None and not _width_ok(marker, "x"):
            return "x exceeds its width cap"
        artifact = record.get("a")
        if family in ("CLR", "PAD", "RRR"):
            if not isinstance(artifact, str) or not ARTIFACT_RE.match(artifact):
                return f"{family} E record requires an artifact pointer by name"
            if not _width_ok(artifact, "a"):
                return "artifact pointer exceeds its width cap"
        elif artifact is not None:
            return f"{family} carries no artifact pointer"
        return None

    # kind == "D"
    if family not in ("OMR", "RRR"):
        return "D records belong to the OMR and RRR families only"
    kindtoken = record.get("t")
    if kindtoken not in ADDRESS_KINDS:
        return f"t must be one of {ADDRESS_KINDS}"
    artifact = record.get("a")
    if not isinstance(artifact, str) or not ARTIFACT_RE.match(artifact):
        return "D record requires an artifact pointer by name"
    if not _width_ok(artifact, "a"):
        return "artifact pointer exceeds its width cap"
    if kindtoken == "det":
        if record.get("n") not in DETERMINATION_IDS:
            return f"determination id must be one of {DETERMINATION_IDS}"
        ordinal = record.get("c")
        if not isinstance(ordinal, str) or not CRITERION_ORDINAL_RE.match(ordinal):
            return "c must be a material-criterion ordinal or '-'"
        reason = record.get("r")
        if reason not in _reason_codes():
            return f"reason code {reason!r} is outside the consumed set"
        if not _width_ok(reason, "r"):
            return "reason code exceeds its width cap"
        if any(key in record for key in ("g", "h", "s")):
            return "a det address carries no gap ordinal, check name, or severity"
        return None
    gap = record.get("g")
    if kindtoken == "cq":
        if not isinstance(gap, str) or not GAP_ORDINAL_RE.match(gap):
            return "cq address requires a contract-quality gap ordinal C<n>"
        if record.get("h") not in _check_names():
            return f"check name {record.get('h')!r} is outside the seven checks"
        if not _width_ok(record.get("h"), "h"):
            return "check name exceeds its width cap"
    else:
        if not isinstance(gap, str) or not FINDING_ORDINAL_RE.match(gap):
            return "fnd address requires an implementation finding ordinal F<n>"
        if "h" in record:
            return "a fnd address carries no check name"
    if not _width_ok(gap, "g"):
        return "gap ordinal exceeds its width cap"
    if record.get("s") not in _severities():
        return f"severity {record.get('s')!r} is outside the consumed vocabulary"
    if any(key in record for key in ("n", "c", "r")):
        return "a cq/fnd address carries no determination id, criterion, or reason"
    return None


def _record(kind: str, plan: str, unit: str, date: str, **rest: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "v": SCHEMA_VERSION,
        "k": kind,
        "p": plan,
        "u": unit,
        "d": date,
    }
    base.update({key: value for key, value in rest.items() if value is not None})
    return {key: base[key] for key in KEY_ORDER[kind] if key in base}


def event(
    *,
    plan: str,
    unit: str,
    date: str,
    family: str,
    ordinal: Optional[int] = None,
    size: Optional[int] = None,
    marker: Optional[str] = None,
    artifact: Optional[str] = None,
) -> Dict[str, Any]:
    """Build one ``E`` record: proof that a family's boundary actually ran."""
    return _record(
        "E", plan, unit, date, f=family, o=ordinal, n=size, x=marker, a=artifact
    )


def determination_address(
    *,
    plan: str,
    unit: str,
    date: str,
    family: str,
    kind: str,
    artifact: str,
    determination: Optional[str] = None,
    criterion: Optional[str] = None,
    reason: Optional[str] = None,
    gap: Optional[str] = None,
    check: Optional[str] = None,
    severity: Optional[str] = None,
) -> Dict[str, Any]:
    """Build one ``D`` record: an address into an accepted outcome, never a body."""
    return _record(
        "D",
        plan,
        unit,
        date,
        f=family,
        t=kind,
        n=determination,
        c=criterion,
        r=reason,
        g=gap,
        h=check,
        s=severity,
        a=artifact,
    )


def summary(
    *,
    plan: str,
    unit: str,
    date: str,
    families: Dict[str, Tuple[int, str]],
    denominators: Dict[str, int],
) -> Dict[str, Any]:
    """Build one ``U`` record: six counts, six states, three denominators."""
    return _record(
        "U",
        plan,
        unit,
        date,
        f={name: [families[name][0], families[name][1]] for name in FAMILIES},
        q={name: denominators[name] for name in DENOMINATOR_FAMILIES},
    )


# ---------------------------------------------------------------------------
# Cap accounting (§ 13.2) and emission (§ 13.3).
# ---------------------------------------------------------------------------
@dataclass
class Allowance:
    """A unit's cap position, recoverable from a bounded read of its records."""

    unit: str
    ordinary_used: int
    summaries_used: int

    @property
    def ordinary_remaining(self) -> int:
        return max(0, ORDINARY_ALLOWANCE - self.ordinary_used)

    @property
    def total_used(self) -> int:
        return self.ordinary_used + self.summaries_used

    @property
    def at_cap(self) -> bool:
        return self.ordinary_remaining == 0


def allowance(ledger: Ledger, unit: str) -> Allowance:
    """Read a unit's ordinary and reserved usage.

    The count is derived by reading the current plan's records for that unit —
    a bounded read of at most 64 lines — so it survives a restart with no
    external counter, and the partition is recoverable from the same read.
    """
    ordinary = 0
    summaries = 0
    for record in ledger.for_unit(unit):
        if record.get("k") == "U":
            summaries += 1
        else:
            ordinary += 1
    return Allowance(unit=unit, ordinary_used=ordinary, summaries_used=summaries)


def emit(
    project_root: os.PathLike | str,
    record: Dict[str, Any],
    *,
    ledger: Optional[Ledger] = None,
) -> Dict[str, Any]:
    """Append one record, or say precisely why it was not appended.

    Never raises and never blocks: the result is a structured outcome the
    caller reports and then ignores. A lifecycle transition proceeds whether
    the emission was written, rejected, or suppressed.

    ``rejected`` — the value violated a closed domain or a width cap.
    ``suppressed`` — the ordinary allowance is exhausted; the record is not
    written, no earlier record is rewritten, and the family will read
    ``omitted`` in the unit's summary.
    """
    root = Path(project_root)
    current = ledger if ledger is not None else read_ledger(root)
    unit = record.get("u")
    family = record.get("f")
    family_code = family if isinstance(family, str) else None

    if record.get("p") != current.plan_id:
        return {
            "result": REJECTED,
            "reason": "plan id does not match the current plan window",
            "family": family_code,
            "unit": unit,
        }
    problem = validate(record)
    if problem is not None:
        return {
            "result": REJECTED,
            "reason": problem,
            "family": family_code,
            "unit": unit,
        }

    used = allowance(current, str(unit))
    if record["k"] == "U":
        if used.summaries_used >= RESERVED_SUMMARY_SLOTS:
            return {
                "result": REJECTED,
                "reason": "a unit never holds a third U record",
                "family": None,
                "unit": unit,
            }
    elif used.ordinary_remaining <= 0:
        return {
            "result": SUPPRESSED,
            "reason": (
                f"the unit's {ORDINARY_ALLOWANCE}-record ordinary allowance is "
                "exhausted; the family reads omitted"
            ),
            "family": family_code,
            "unit": unit,
        }

    line = serialize(record)
    if not _append_line(root, line):
        return {
            "result": REJECTED,
            "reason": "the ledger could not be appended",
            "family": family_code,
            "unit": unit,
        }
    current.records.append(record)
    return {
        "result": WRITTEN,
        "reason": None,
        "family": family_code,
        "unit": unit,
        "bytes": len(line.encode("utf-8")),
    }


# ---------------------------------------------------------------------------
# Summary derivation (§ 12.4).
# ---------------------------------------------------------------------------
@dataclass
class Expectation:
    """What a boundary says it should have produced for one family.

    ``expected`` is the count the retained sources independently support —
    prompt writes for ``PCC``, recorded passes for ``RRR``, material criteria
    for ``OMR``. ``None`` means the boundary cannot say, which is what makes
    ``unavailable`` honest rather than a rounded zero.
    """

    available: bool = True
    expected: Optional[int] = None
    denominator: int = 0
    forced_state: Optional[str] = None


def derive_summary_values(
    records: Sequence[Dict[str, Any]],
    expectations: Dict[str, Expectation],
    *,
    post_approval_closed: bool,
) -> Tuple[Dict[str, Tuple[int, str]], Dict[str, int]]:
    """Derive the six ``[count, state]`` pairs and three denominators.

    ``PCC``'s count is summed *bytes*; every other count is events. That is
    the one asymmetry in the record and it is deliberate — a prompt's cost is
    its size, not the number of times it was written.
    """
    families: Dict[str, Tuple[int, str]] = {}
    for name in FAMILIES:
        expectation = expectations.get(name, Expectation(available=False))
        if name == "OMR":
            matching = [
                rec for rec in records if rec.get("k") == "D" and rec.get("f") == "OMR"
            ]
        elif name == "RRR":
            matching = [
                rec
                for rec in records
                if rec.get("k") == "E"
                and rec.get("f") == "RRR"
                and rec.get("x") != "approve"
            ]
        else:
            matching = [
                rec for rec in records if rec.get("k") == "E" and rec.get("f") == name
            ]
        if name == "PCC":
            count = sum(int(rec.get("n", 0)) for rec in matching)
            emitted = len(matching)
        else:
            count = len(matching)
            emitted = count if name != "RRR" else len(
                [rec for rec in records if rec.get("k") == "E" and rec.get("f") == "RRR"]
            )

        if expectation.forced_state is not None:
            state = expectation.forced_state
        elif not expectation.available:
            state = "unavailable"
        elif name == "PAD" and not post_approval_closed:
            state = "not-yet-observable"
        elif expectation.expected is not None and emitted < expectation.expected:
            state = "omitted"
        else:
            state = "observed"
        if state in ("unavailable", "not-applicable", "not-yet-observable"):
            # "We don't know", "the question doesn't arise", and "too early"
            # carry no count. Only `omitted` keeps its partial tally, and the
            # state — not the number — is what says the tally is incomplete.
            count = 0
        families[name] = (count, state)

    denominators = {
        name: int(expectations.get(name, Expectation()).denominator)
        for name in DENOMINATOR_FAMILIES
    }
    return families, denominators


def suppressed_families(
    records: Sequence[Dict[str, Any]], families: Dict[str, Tuple[int, str]]
) -> List[str]:
    """Families reading ``omitted`` on a unit that reached its record cap.

    § 13.4 keeps the record itself honest by refusing to distinguish a cap
    ``omitted`` from any other ``omitted``; the announcement obligation is on
    the query, and this is what it names.
    """
    ordinary = sum(1 for rec in records if rec.get("k") != "U")
    if ordinary < ORDINARY_ALLOWANCE:
        return []
    return [name for name in FAMILIES if families.get(name, (0, ""))[1] == "omitted"]


# ---------------------------------------------------------------------------
# Bounded query projections (§ 15).
# ---------------------------------------------------------------------------
def _current_summary(records: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A unit's current value is the last ``U`` record for that unit."""
    summaries = [rec for rec in records if rec.get("k") == "U"]
    return summaries[-1] if summaries else None


def unit_row(record: Dict[str, Any]) -> str:
    """Render one ``U`` row: always six families, always with their states."""
    families = record["f"]
    denominators = record.get("q", {})
    segments = []
    for name in FAMILIES:
        count, state = families[name][0], families[name][1]
        if name == "PCC":
            segment = f"{name} {count} B {state}"
        else:
            segment = f"{name} {count} {state}"
        if name in DENOMINATOR_FAMILIES:
            segment += f" of {denominators.get(name, 0)}"
        segments.append(segment)
    return "|".join(["U", record["u"], record["d"], *segments])


def determination_row(record: Dict[str, Any]) -> str:
    """Render one ``D`` row in the shape its ``t`` value defines."""
    kind = record["t"]
    if kind == "det":
        address = f"{record['n']} {record['c']}"
        value = record["r"]
    elif kind == "cq":
        address = f"CQ {record['g']}"
        value = f"{record['h']} {record['s']}"
    else:
        address = record["g"]
        value = record["s"]
    return "|".join(["D", record["u"], address, value, record["a"]])


def event_row(record: Dict[str, Any]) -> str:
    """Render one ``E`` row: seven fields, ``-`` where one does not apply."""
    family = record["f"]
    if family in ("RRR", "RRG"):
        ordinal = f"pass {record['o']}"
    elif family == "PCC":
        ordinal = f"write {record['o']}"
    else:
        ordinal = "-"
    if family == "RRR":
        value = record["x"]
    elif family == "RRG":
        value = record["x"]
    elif family == "PCC":
        value = f"{record['n']} B"
    else:
        value = "-"
    artifact = record.get("a") or "-"
    return "|".join(
        ["E", record["u"], record["d"], family, ordinal, value, artifact]
    )


def truncation_line(row_class: str, returned: int, matched: int, cap: int) -> str:
    return f"TRUNCATED|{row_class}|{returned} of {matched} matched|cap {cap}"


def capped_line(unit: str, records: int, families: Sequence[str]) -> str:
    return f"CAPPED|{unit}|{records} of {RECORD_CAP}|{' '.join(families)}"


@dataclass
class Answer:
    """One bounded query answer: rows, cap announcements, truncation."""

    row_class: str
    rows: List[str]
    capped: List[str]
    truncated: Optional[str]
    matched: int
    unavailable_units: List[str]

    def body(self) -> str:
        lines = [*self.rows, *self.capped]
        if self.truncated:
            lines.append(self.truncated)
        return "".join(line + "\n" for line in lines)

    def as_record(self) -> Dict[str, Any]:
        body = self.body()
        return {
            "row_class": self.row_class,
            "rows": list(self.rows),
            "capped": list(self.capped),
            "truncated": self.truncated,
            "matched": self.matched,
            "returned": len(self.rows),
            "cap": QUERY_CAPS[self.row_class],
            "unavailable_units": list(self.unavailable_units),
            "bytes": len(body.encode("utf-8")),
            "est_tokens": -(-len(body.encode("utf-8")) // 4),
            "bound_bytes": ANSWER_BOUNDS[self.row_class],
        }


def _cap_announcements(
    ledger: Ledger, units_in_order: Sequence[str]
) -> List[str]:
    out: List[str] = []
    for unit in units_in_order:
        records = ledger.for_unit(unit)
        current = _current_summary(records)
        if current is None:
            continue
        families = {
            name: (current["f"][name][0], current["f"][name][1]) for name in FAMILIES
        }
        suppressed = suppressed_families(records, families)
        if suppressed:
            out.append(capped_line(unit, len(records), suppressed))
    return out


def _answer(
    ledger: Ledger,
    row_class: str,
    rows: List[Tuple[str, str]],
) -> Answer:
    """Assemble rows into a bounded answer with its two announcement kinds.

    Truncation is by the caller's stated order, never a curated or "best"
    subset: a curated prefix would reintroduce exactly the survivorship bias
    this ledger exists to correct. Silence means completeness.
    """
    cap = QUERY_CAPS[row_class]
    matched = len(rows)
    kept = rows[:cap]
    truncated = (
        truncation_line(row_class, len(kept), matched, cap) if matched > cap else None
    )
    ordered_units: List[str] = []
    for unit, _ in kept:
        if unit not in ordered_units:
            ordered_units.append(unit)
    return Answer(
        row_class=row_class,
        rows=[row for _, row in kept],
        capped=_cap_announcements(ledger, ordered_units),
        truncated=truncated,
        matched=matched,
        unavailable_units=list(ledger.unavailable_units),
    )


def project_units(ledger: Ledger, *, units: Optional[Sequence[str]] = None) -> Answer:
    """The ``U`` projection: one row per unit, in the caller's stated order."""
    wanted = list(units) if units else ledger.units()
    rows: List[Tuple[str, str]] = []
    for unit in wanted:
        current = _current_summary(ledger.for_unit(unit))
        if current is None:
            if unit not in ledger.unavailable_units:
                ledger.unavailable_units.append(unit)
            continue
        rows.append((unit, unit_row(current)))
    return _answer(ledger, "units", rows)


def project_determinations(
    ledger: Ledger, *, units: Optional[Sequence[str]] = None
) -> Answer:
    """The ``D`` projection: one row per recorded reason address."""
    wanted = set(units) if units else None
    rows = [
        (rec["u"], determination_row(rec))
        for rec in ledger.records
        if rec.get("k") == "D" and (wanted is None or rec.get("u") in wanted)
    ]
    return _answer(ledger, "determinations", rows)


def project_events(ledger: Ledger, *, units: Optional[Sequence[str]] = None) -> Answer:
    """The ``E`` projection: the per-pass and per-write detail behind a count."""
    wanted = set(units) if units else None
    rows = [
        (rec["u"], event_row(rec))
        for rec in ledger.records
        if rec.get("k") == "E" and (wanted is None or rec.get("u") in wanted)
    ]
    return _answer(ledger, "events", rows)


PROJECTIONS = {
    "U": project_units,
    "D": project_determinations,
    "E": project_events,
}


# ---------------------------------------------------------------------------
# Boundary helpers.
#
# Both are best-effort and never raise: an evidence-emission failure is
# recorded as ``omitted`` and must not block a lifecycle transition.
# ``PCC`` in particular must be captured at prompt-write time or not at all —
# the prompt is deleted at approval and the journal stores a hash, not a
# length, so deferring this collection destroys the evidence in principle.
# ---------------------------------------------------------------------------
_PROMPT_ID_RE = re.compile(r"^PROMPT-(\d{2}-\d{3})$")


def record_prompt_write(
    project_root: os.PathLike | str, prompt_id: str, body: bytes, date: str
) -> Dict[str, Any]:
    """Capture one prompt write's exact byte count against its unit.

    Plan-level prompts (``PROMPT-PLAN-NNN``) are outside the unit of
    observation: they emit nothing and are ``not-applicable``.
    """
    try:
        match = _PROMPT_ID_RE.match(prompt_id)
        if not match:
            return {"result": REJECTED, "reason": "not a unit-scoped prompt"}
        unit = f"TASK-{match.group(1)}"
        ledger = read_ledger(project_root)
        ordinal = 1 + sum(
            1
            for rec in ledger.for_unit(unit)
            if rec.get("k") == "E" and rec.get("f") == "PCC"
        )
        if ordinal > 999:
            return {"result": REJECTED, "reason": "event ordinal exceeds 999"}
        record = event(
            plan=ledger.plan_id,
            unit=unit,
            date=date,
            family="PCC",
            ordinal=ordinal,
            size=len(body),
        )
        return emit(project_root, record, ledger=ledger)
    except Exception:  # pragma: no cover - measurement never breaks a write
        return {"result": REJECTED, "reason": "capture failed"}


def record_transition(
    project_root: os.PathLike | str,
    unit: str,
    from_status: str,
    to_status: str,
    date: str,
) -> Dict[str, Any]:
    """Capture one reopen transition out of review, with its ordinal."""
    try:
        transition = f"{from_status}>{to_status}"
        if transition not in TRANSITIONS or not UNIT_ID_RE.match(unit):
            return {"result": REJECTED, "reason": "not a recorded reopen transition"}
        ledger = read_ledger(project_root)
        ordinal = 1 + sum(
            1
            for rec in ledger.for_unit(unit)
            if rec.get("k") == "E" and rec.get("f") == "RRG"
        )
        if ordinal > 999:
            return {"result": REJECTED, "reason": "event ordinal exceeds 999"}
        record = event(
            plan=ledger.plan_id,
            unit=unit,
            date=date,
            family="RRG",
            ordinal=ordinal,
            marker=transition,
        )
        return emit(project_root, record, ledger=ledger)
    except Exception:  # pragma: no cover - measurement never blocks a move
        return {"result": REJECTED, "reason": "capture failed"}


# ---------------------------------------------------------------------------
# Unit closure and plan closeout (§§ 12.2, 14.4).
#
# These are the two boundaries the contract names, and both are wired into the
# lifecycle commands that own them rather than left to a manual invocation: a
# `U` record that is only written when someone remembers to ask for it is not
# a summary "at unit closure", and a log that is only deleted when someone
# remembers to ask is not plan-bounded retention.
#
# Both are best-effort in exactly the § 8 sense. Every failure path returns a
# structured outcome; none raises, and none may change a lifecycle command's
# exit code.
# ---------------------------------------------------------------------------
_SUFFIX_RE = re.compile(r"^TASK-(\d{2}-\d{3})$")


def read_provenance_journal(
    project_root: os.PathLike | str,
) -> Optional[List[Dict[str, Any]]]:
    """The accepted journal's rows, or ``None`` when it cannot be read.

    ``None`` is what makes ``unavailable`` honest: a boundary whose retained
    source is missing cannot claim it observed zero of anything.
    """
    path = Path(project_root) / provenance.PROVENANCE_DIRNAME / provenance.LOG_BASENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    rows: List[Dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and isinstance(record.get("relpath"), str):
            rows.append(record)
    return rows


def boundary_expectations(
    project_root: os.PathLike | str,
    unit: str,
    records: Sequence[Dict[str, Any]],
) -> Dict[str, Expectation]:
    """What each family's boundary independently says it should have produced.

    Every figure below is read off retained artifacts — the accepted journal,
    the review and report files, the task's own trace — never off the evidence
    ledger itself. That is what lets a suppressed or missing emission show up
    as ``omitted`` instead of being rounded into a true zero.
    """
    from cli import trace_binding

    root = Path(project_root)
    suffix_match = _SUFFIX_RE.match(unit)
    suffix = suffix_match.group(1) if suffix_match else ""
    journal = read_provenance_journal(root)
    expectations: Dict[str, Expectation] = {}

    if journal is None:
        for family in ("CLR", "RRR", "RRG", "PCC"):
            expectations[family] = Expectation(available=False)
    else:
        prompt_writes = sum(
            1
            for row in journal
            if row["relpath"] == f"prompts/PROMPT-{suffix}.md"
            and row.get("hash") != "deleted"
        )
        review_writes = sum(
            1
            for row in journal
            if row["relpath"] == f"reviews/REVIEW-{suffix}.md"
            and row.get("hash") != "deleted"
        )
        transitions = 0
        previous: Optional[str] = None
        for row in journal:
            parts = row["relpath"].split("/")
            if (
                row.get("action") != "move-task"
                or len(parts) < 3
                or not parts[2].startswith(unit)
            ):
                continue
            if previous == "in-review" and parts[1] in ("in-progress", "open"):
                transitions += 1
            previous = parts[1]
        touches_unit = any(
            parts[2].startswith(unit)
            for parts in (row["relpath"].split("/") for row in journal)
            if len(parts) >= 3
        )
        expectations["PCC"] = Expectation(
            available=prompt_writes > 0,
            expected=prompt_writes or None,
            denominator=prompt_writes,
        )
        expectations["RRR"] = Expectation(
            available=review_writes > 0,
            expected=review_writes or None,
            denominator=review_writes,
            forced_state=None if review_writes else "not-yet-observable",
        )
        expectations["RRG"] = Expectation(
            available=touches_unit, expected=transitions or None
        )
        report_path = root / "reports" / f"REPORT-{suffix}.md"
        expectations["CLR"] = Expectation(available=report_path.is_file())

    task_path = None
    for status in ("in-review", "in-progress", "done", "open"):
        directory = root / "tasks" / status
        if not directory.is_dir():
            continue
        matches = sorted(directory.glob(f"{unit}*.md"))
        if matches:
            task_path = matches[0]
            break
    criteria = 0
    omr_state: Optional[str] = "not-yet-observable"
    if task_path is not None:
        binding = trace_binding.bind(root, task_path)
        if binding.trace is not None:
            criteria = len(binding.trace.criteria)
            omr_state = None
    expectations["OMR"] = Expectation(
        available=omr_state is None,
        expected=None,
        denominator=criteria,
        forced_state=omr_state,
    )
    approved = any(
        record.get("k") == "E"
        and record.get("f") == "RRR"
        and record.get("x") == "approve"
        for record in records
    )
    expectations["PAD"] = Expectation(
        available=approved,
        expected=None,
        forced_state=None if approved else "not-applicable",
    )
    return expectations


def summarize_unit(
    project_root: os.PathLike | str,
    unit: str,
    date: str,
    *,
    post_approval_closed: bool = False,
    ledger: Optional[Ledger] = None,
    only_if_absent: bool = False,
) -> Dict[str, Any]:
    """Derive and append one unit's ``U`` record. Never raises.

    ``only_if_absent`` is the unit-closure seam's idempotency: a unit that is
    closed, reopened, and closed again crosses this boundary more than once,
    and the second crossing must not spend the slot § 13.2 reserves for the
    superseding summary.
    """
    try:
        root = Path(project_root)
        current = ledger if ledger is not None else read_ledger(root)
        records = current.for_unit(unit)
        if only_if_absent and any(rec.get("k") == "U" for rec in records):
            return {
                "result": IDEMPOTENT,
                "reason": "the unit already carries a closure summary",
                "family": None,
                "unit": unit,
            }
        expectations = boundary_expectations(root, unit, records)
        families, denominators = derive_summary_values(
            records, expectations, post_approval_closed=post_approval_closed
        )
        record = summary(
            plan=current.plan_id,
            unit=unit,
            date=date,
            families=families,
            denominators=denominators,
        )
        outcome = emit(root, record, ledger=current)
        return {
            "unit": unit,
            "record": record,
            "row": unit_row(record),
            "suppressed_families": suppressed_families(records, families),
            **outcome,
        }
    except Exception:  # pragma: no cover - measurement never blocks a closure
        return {
            "result": REJECTED,
            "reason": "the unit summary could not be derived",
            "family": None,
            "unit": unit,
        }


def close_plan_sequence(
    project_root: os.PathLike | str,
    *,
    date: str,
    plan_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the § 14.4 plan-closing sequence, in its normative order.

    1. Superseding ``U`` for every unit whose post-approval window is open —
       the only record that moves ``PAD`` off ``not-yet-observable``.
    2. The closing projection, from the log as it now stands.
    3. The mediated delete, last.

    A run that deletes before step 1 loses ``PAD`` for the whole plan; a run
    that deletes before step 2 loses everything the projection was for.

    Re-entrant by construction: a window whose log is already gone reports
    ``already_closed`` and writes nothing, so archiving and then resetting a
    plan closes the window exactly once.
    """
    root = Path(project_root)
    window = plan_id or current_plan_id(root)
    result: Dict[str, Any] = {
        "plan": window,
        "already_closed": False,
        "superseding_summaries": [],
        "closing_projection": None,
        "log_deleted": False,
        "retained": False,
    }
    try:
        if not os.path.lexists(log_path(root)):
            result["already_closed"] = True
            result["closing_projection"] = project_units(
                read_ledger(root, plan_id=window)
            ).as_record()
            return result
        ledger = read_ledger(root, plan_id=window)
        result["ledger_errors"] = ledger.errors
        for unit in ledger.units():
            records = ledger.for_unit(unit)
            approved = any(
                rec.get("k") == "E"
                and rec.get("f") == "RRR"
                and rec.get("x") == "approve"
                for rec in records
            )
            if not approved:
                continue
            if sum(1 for rec in records if rec.get("k") == "U") >= RESERVED_SUMMARY_SLOTS:
                continue
            result["superseding_summaries"].append(
                summarize_unit(
                    root, unit, date, post_approval_closed=True, ledger=ledger
                )
            )
        result["closing_projection"] = project_units(
            read_ledger(root, plan_id=window)
        ).as_record()
        result["log_deleted"] = delete_log(root)
    except Exception:  # pragma: no cover - closeout never fails on evidence
        result["error"] = "the plan-closing sequence did not complete"
    return result


# ---------------------------------------------------------------------------
# Retention (§ 14).
# ---------------------------------------------------------------------------
def delete_log(project_root: os.PathLike | str) -> bool:
    """Delete the ledger as a mediated delete, so the absence is explainable.

    This is the last step of the plan-closing sequence, never the first: a run
    that deletes before the superseding summaries lose ``PAD`` for the whole
    plan, and a run that deletes before the closing projection loses
    everything the projection was for.
    """
    root = Path(project_root)
    path = log_path(root)
    if not os.path.lexists(path):
        return False
    try:
        os.unlink(path)
    except OSError:
        return False
    provenance.record_delete(root, path, action="mediated-delete")
    return True
