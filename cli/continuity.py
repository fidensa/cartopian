"""The one authority for the optional cross-plan continuity artifact.

Continuity is the *optional* memory a completed plan may leave behind: one
project-root ``CONTINUITY.md`` holding the rulings that still govern, the cold
index of rulings that no longer do, and a compact per-plan ledger. It is
written only by the two preservation-bearing closeout outcomes
(``archive+index`` and ``ledger``); ``none`` and ``archive`` closeouts neither
read nor write any continuity content at all.

This module owns every mechanism the surrounding commands share, so that no
reader or writer re-implements one of them:

- the ``continuity-v1`` schema — parsing, serialization, counters, and the
  derived ``Retrieval`` value;
- the read-error contract: absence is a successful disabled state, presence
  with any defect is a fail-closed refusal;
- field grammar and the character / serialized-byte bounds;
- cross-plan decision identity (``PLAN-NNN/DEC-NNN``), the reference grammar
  shared by ``Supersedes:`` and ``Expires:``, and the removal multimap;
- the **commit-aware publication step**: the shared atomic-write mechanism
  commits a *first* publication with ``os.link`` and then unlinks its temporary
  name, so a named refusal can arrive *after* the commit. Every publication
  this contract performs is therefore classified from the artifact — against
  the bytes that were there before and the bytes being published — into
  ``not-published`` / ``published`` / ``unresolved``, never from "a refusal was
  raised";
- the five-shape plan-id reservation state machine, its residue collapse, its
  ``archive/INDEX.md`` row, and the create / adopt / release primitives;
- the window witness, computed from ``prompt_evidence.read_ledger`` alone.

Nothing here loosens a guard. Reservation files are written through the same
``cli.atomic_write`` mechanism ``cli.mediated_write`` and
``cli.commands.update_config`` call, with this module supplying the policy;
``archive/INDEX.md`` is written through ``archive-plan``'s own helpers, called
rather than changed.
"""
from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from cli import provenance
from cli.atomic_write import (
    DIR_FD_SUPPORTED,
    GuardRefusal,
    _atomic_write_via_dir_fd,
    _atomic_write_via_path,
    _snapshot_chain,
    make_tmp_name,
)
from cli.markdown_fences import FenceTracker

# ---------------------------------------------------------------------------
# Declared bounds. Every one is an engineering bound derived from the accepted
# contract's measurements, not an operator choice.
# ---------------------------------------------------------------------------
CONTINUITY_BASENAME = "CONTINUITY.md"
FORMAT_VALUE = "continuity-v1"

CONTINUITY_MAX_BYTES = 65536
CONTINUITY_PROJECTION_MAX_BYTES = 1536
LEDGER_SECTION_MAX_BYTES = 1024
PROJECTION_LIVE_ROW_CAP = 4

SCOPE_CHAR_MAX = 24
SCOPE_BYTE_MAX = 48
RULING_CHAR_MAX = 100
RULING_BYTE_MAX = 200
REFERENCE_HEADER_BYTE_MAX = 200
REFERENCE_LIST_MAX = 8

PLAN_COUNTER_MAX = 999

# ---------------------------------------------------------------------------
# The reservation: two fixed-content files and one fixed-summary index row.
# ---------------------------------------------------------------------------
NOT_ARCHIVED_BASENAME = "NOT-ARCHIVED.md"
LEDGER_FAILED_BASENAME = "LEDGER-FAILED.md"
RESERVATION_SUMMARY = "not archived - ledger preservation outcome"

PRESERVATION_INDEX = "archive+index"
PRESERVATION_LEDGER = "ledger"
PRESERVATIONS = (PRESERVATION_INDEX, PRESERVATION_LEDGER)

DISPOSITION_SUPERSEDED = "superseded"
DISPOSITION_EXPIRED = "expired"

# ---------------------------------------------------------------------------
# Grammars.
# ---------------------------------------------------------------------------
PLAN_ID_RE = re.compile(r"^PLAN-(\d{3})$")
DEC_ID_RE = re.compile(r"^DEC-\d{3}$")
QUALIFIED_ID_RE = re.compile(r"^PLAN-\d{3}/DEC-\d{3}$")
REFERENCE_RE = re.compile(r"^(?:PLAN-\d{3}/)?DEC-\d{3}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DECISION_FILE_RE = re.compile(r"^DEC-\d{3}\.md$")
_HEADER_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 _/-]*?):\s*(.*)$")
_SECTION_RE = re.compile(
    r"^### (PLAN-\d{3}) - closed (\d{4}-\d{2}-\d{2}) - (archive\+index|ledger)$"
)
_RESERVATION_ROW_RE = re.compile(
    r"^\| `(PLAN-\d{3})` \| (\d{4}-\d{2}-\d{2}) \| "
    + re.escape(RESERVATION_SUMMARY)
    + r" \|$"
)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")

_LIVE_TABLE_HEADER = (
    "| Decision | Scope | Ruling | Body |\n"
    "| --- | --- | --- | --- |\n"
)
_COLD_TABLE_HEADER = (
    "| Decision | Scope | Ruling | Disposition | Removed by | Body |\n"
    "| --- | --- | --- | --- | --- | --- |\n"
)


class ContinuityRefusal(Exception):
    """A continuity operation was refused fail-closed. ``rule`` names it."""

    def __init__(self, rule: str, detail: str) -> None:
        self.rule = rule
        self.detail = detail
        super().__init__(f"{rule}: {detail}")


# ---------------------------------------------------------------------------
# Field grammar (§ 5.4, § 5.5).
# ---------------------------------------------------------------------------
def serialized_bytes(value: str) -> int:
    """UTF-8 length of ``value`` after JSON string escaping, sans the quotes.

    Exactly the bytes the value contributes to the record ``cli.emit`` writes
    with ``json.dumps(..., ensure_ascii=False, separators=(",", ":"))``.
    Bounding the serialized form makes the worst case equal to the bound by
    construction, for every script and every escape.
    """
    return len(json.dumps(value, ensure_ascii=False)[1:-1].encode("utf-8"))


def cell_violation(value: str, field_name: str) -> Optional[str]:
    """Name the § 5.5 rule ``value`` breaks as a table cell, or ``None``.

    Refusal, not escaping: the value must be byte-identical in the Markdown
    cell and in the JSON projection, so a ``|`` is refused rather than escaped.
    """
    char_max, byte_max = (
        (SCOPE_CHAR_MAX, SCOPE_BYTE_MAX)
        if field_name == "Scope"
        else (RULING_CHAR_MAX, RULING_BYTE_MAX)
    )
    if value != value.strip(" "):
        return "must not have leading or trailing spaces"
    if not value:
        return "must be non-empty"
    if _CONTROL_RE.search(value):
        return "must contain no CR, LF, or C0/C1 control character"
    if "|" in value:
        return "must contain no `|`"
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return "must be valid UTF-8 with no unpaired surrogate"
    if len(value) > char_max:
        return f"exceeds the {char_max}-character bound ({len(value)} characters)"
    size = serialized_bytes(value)
    if size > byte_max:
        return f"exceeds the {byte_max}-serialized-byte bound ({size} bytes)"
    return None


def parse_reference_header(
    raw: str, header: str, *, allow_repeat: bool = False
) -> Tuple[List[str], Optional[str]]:
    """Parse a ``Supersedes:`` / ``Expires:`` value under the § 4.2 grammar.

    Returns ``(references, violation)``. ``none`` yields an empty list. A
    reference repeated inside one header value is refused at authoring time and
    therefore never reaches the removal multimap; ``allow_repeat`` is what the
    continuity write passes so that a hand-edited body collapses to one pair
    rather than being read as two removals.
    """
    value = raw.strip()
    if serialized_bytes(value) > REFERENCE_HEADER_BYTE_MAX:
        return [], (
            f"{header} exceeds the {REFERENCE_HEADER_BYTE_MAX}-serialized-byte bound"
        )
    if value == "none":
        return [], None
    if not value:
        return [], f"{header} must be `none` or a comma-separated reference list"
    parts = [part.strip() for part in value.split(",")]
    refs: List[str] = []
    for part in parts:
        if not REFERENCE_RE.match(part):
            return [], (
                f"{header} reference {part!r} is not DEC-NNN or PLAN-NNN/DEC-NNN"
            )
        if part in refs:
            if allow_repeat:
                continue
            return [], f"{header} repeats the reference {part}"
        refs.append(part)
    if len(refs) > REFERENCE_LIST_MAX:
        return [], (
            f"{header} carries {len(refs)} references; at most "
            f"{REFERENCE_LIST_MAX} are admitted"
        )
    return refs, None


def parse_headers(content: str) -> Dict[str, str]:
    """Parse the ``Field: value`` header block of an authored artifact body.

    The block is the run of lines before the first ``## `` section header —
    the boundary the rest of the surface already uses — with fenced code blocks
    and HTML comments skipped, so neither a quoted operator line nor a
    commented-out template hint can be read as a header. The first occurrence
    of each key wins.
    """
    headers: Dict[str, str] = {}
    tracker = FenceTracker()
    commented = False
    for line in content.splitlines():
        is_delimiter = tracker.feed(line)
        if is_delimiter or tracker.in_fence:
            continue
        if commented:
            commented = "-->" not in line
            continue
        if "<!--" in line:
            commented = "-->" not in line.split("<!--", 1)[1]
            line = line.split("<!--", 1)[0]
        if line.startswith("## "):
            break
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _HEADER_RE.match(stripped)
        if not match:
            continue
        key = match.group(1).strip()
        if key not in headers:
            headers[key] = match.group(2).strip()
    return headers


def validate_decision_headers(content: str, decisions_dir: Path) -> Optional[str]:
    """Validate the optional continuity headers in an authored decision body.

    Fail-closed at authoring time (§ 5.3): any ``Scope:``, ``Ruling:``,
    ``Supersedes:``, or ``Expires:`` header present must satisfy § 5.5 and
    § 4.2, or the write is refused before a byte lands. Returns the violation
    message, or ``None`` when every present header is admissible.
    """
    headers = parse_headers(content)
    for field_name in ("Scope", "Ruling"):
        if field_name not in headers:
            continue
        violation = cell_violation(headers[field_name], field_name)
        if violation is not None:
            return f"{field_name}: {violation}"
    for header in ("Supersedes", "Expires"):
        if header not in headers:
            continue
        refs, violation = parse_reference_header(headers[header], header)
        if violation is not None:
            return violation
        for ref in refs:
            if "/" in ref:
                continue
            # A bare reference names the plan window now closing, so it must
            # name a file that exists in the current `decisions/`.
            if not (decisions_dir / f"{ref}.md").is_file():
                return (
                    f"{header} bare reference {ref} names no file in "
                    f"{decisions_dir}/{ref}.md"
                )
    return None


# ---------------------------------------------------------------------------
# The continuity-v1 schema (§ 5).
# ---------------------------------------------------------------------------
@dataclass
class LiveRow:
    """One governing ruling. ``body`` is a locator or the literal ``none``."""

    id: str
    scope: str
    ruling: str
    body: str

    @property
    def plan(self) -> str:
        return self.id.split("/", 1)[0]

    @property
    def dec(self) -> str:
        return self.id.split("/", 1)[1]

    @property
    def has_locator(self) -> bool:
        return self.body != "none"


@dataclass
class ColdRow:
    """One ruling that no longer governs, with its own retrieval truth."""

    id: str
    scope: str
    ruling: str
    disposition: str
    removed_by: str
    body: str

    @property
    def has_locator(self) -> bool:
        return self.body != "none"


@dataclass
class LedgerSection:
    """One closed plan's six-group entry, or its prune tombstone."""

    plan: str
    closed: str
    preservation: str
    body_lines: List[str]

    @property
    def heading(self) -> str:
        return f"### {self.plan} - closed {self.closed} - {self.preservation}"

    def unit_bytes(self) -> int:
        """The section's cost as a per-plan increment: with its separator."""
        return len(self.render(separated=True).encode("utf-8"))

    def render(self, *, separated: bool) -> str:
        body = "".join(f"{line}\n" for line in self.body_lines)
        return f"{self.heading}\n\n{body}" + ("\n" if separated else "")

    @property
    def tombstoned(self) -> bool:
        return len(self.body_lines) == 1 and self.body_lines[0].startswith("- Pruned:")


@dataclass
class Continuity:
    """The whole artifact, in the fixed § 5.1 section order."""

    live: List[LiveRow] = field(default_factory=list)
    cold: List[ColdRow] = field(default_factory=list)
    sections: List[LedgerSection] = field(default_factory=list)
    pruned: int = 0

    # -- derived counters ---------------------------------------------------
    @property
    def plans_recorded(self) -> int:
        return len(self.sections)

    @property
    def highest_plan(self) -> int:
        return max((plan_number(s.plan) for s in self.sections), default=0)

    @property
    def superseded(self) -> int:
        return sum(1 for row in self.cold if row.disposition == DISPOSITION_SUPERSEDED)

    @property
    def expired(self) -> int:
        return sum(1 for row in self.cold if row.disposition == DISPOSITION_EXPIRED)

    @property
    def retrieval(self) -> str:
        return derived_retrieval(self.live, self.cold)

    def section(self, plan: str) -> Optional[LedgerSection]:
        for entry in self.sections:
            if entry.plan == plan:
                return entry
        return None

    def records(self, plan: str) -> bool:
        return self.section(plan) is not None


def plan_number(plan_id: str) -> int:
    match = PLAN_ID_RE.match(plan_id)
    if match is None:
        raise ValueError(f"not a plan id: {plan_id!r}")
    return int(match.group(1))


def plan_name(number: int) -> str:
    return f"PLAN-{number:03d}"


def locator_for(qualified_id: str) -> str:
    """The derivable archived-body locator for a qualified decision id."""
    plan, dec = qualified_id.split("/", 1)
    return f"archive/{plan}/decisions/{dec}.md"


def derived_retrieval(live: Sequence[LiveRow], cold: Sequence[ColdRow]) -> str:
    """The § 5.6 summary over the rows actually present — never a mode."""
    rows = list(live) + list(cold)
    if not rows:
        return "none"
    with_body = sum(1 for row in rows if row.has_locator)
    if with_body == len(rows):
        return "body"
    if with_body == 0:
        return "ruling"
    return "mixed"


def _cells(line: str, count: int, section: str) -> List[str]:
    if not (line.startswith("| ") and line.endswith(" |")):
        raise ContinuityRefusal("continuity-parse-failed", section)
    cells = [cell.strip() for cell in line[2:-2].split(" | ")]
    if len(cells) != count:
        raise ContinuityRefusal("continuity-parse-failed", section)
    return cells


def serialize_continuity(model: Continuity) -> str:
    """Render the artifact deterministically, in the load-bearing § 5.1 order."""
    highest = model.highest_plan
    out = [
        "# Continuity\n",
        "\n",
        f"Format: {FORMAT_VALUE}\n",
        f"Plans recorded: {model.plans_recorded}\n",
        "Highest plan recorded: "
        + (plan_name(highest) if highest else "none")
        + "\n",
        f"Live decisions: {len(model.live)}\n",
        f"Cold decisions: {len(model.cold)}\n",
        "\n",
        "## Live decisions\n",
        "\n",
        _LIVE_TABLE_HEADER,
    ]
    for row in model.live:
        out.append(f"| {row.id} | {row.scope} | {row.ruling} | {row.body} |\n")
    out.append("\n")
    out.append("## Cold decisions\n")
    out.append("\n")
    out.append(
        f"- Superseded: {model.superseded}; Expired: {model.expired}; "
        f"Pruned: {model.pruned}; Retrieval: {model.retrieval}\n"
    )
    out.append("\n")
    out.append("## Cold index\n")
    out.append("\n")
    out.append(_COLD_TABLE_HEADER)
    for row in model.cold:
        out.append(
            f"| {row.id} | {row.scope} | {row.ruling} | {row.disposition} | "
            f"{row.removed_by} | {row.body} |\n"
        )
    out.append("\n")
    out.append("## Plan ledger\n")
    if model.sections:
        out.append("\n")
    for index, section in enumerate(model.sections):
        out.append(section.render(separated=index < len(model.sections) - 1))
    return "".join(out)


def parse_continuity(text: str) -> Continuity:
    """Parse a ``continuity-v1`` artifact, refusing every malformed shape.

    ``continuity-format-unrecognized`` when the format header is missing or
    names a format this reader does not implement; ``continuity-parse-failed``
    naming the section otherwise. A future ``continuity-v2`` is introduced by
    a migration entry with an explicit window, never by a reader that silently
    accepts both.
    """
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    else:
        raise ContinuityRefusal("continuity-parse-failed", "header block")
    cursor = 0

    def take(expected: str, section: str) -> None:
        nonlocal cursor
        if cursor >= len(lines) or lines[cursor] != expected:
            raise ContinuityRefusal("continuity-parse-failed", section)
        cursor += 1

    if not lines or lines[0] != "# Continuity":
        raise ContinuityRefusal("continuity-parse-failed", "header block")
    cursor = 1
    take("", "header block")

    header: Dict[str, str] = {}
    while cursor < len(lines) and lines[cursor] != "":
        match = _HEADER_RE.match(lines[cursor])
        if match is None:
            raise ContinuityRefusal("continuity-parse-failed", "header block")
        header[match.group(1).strip()] = match.group(2).strip()
        cursor += 1
    if header.get("Format") != FORMAT_VALUE:
        raise ContinuityRefusal(
            "continuity-format-unrecognized",
            f"Format is {header.get('Format', '<absent>')!r}, not {FORMAT_VALUE!r}",
        )
    for required in ("Plans recorded", "Highest plan recorded", "Live decisions", "Cold decisions"):
        if required not in header:
            raise ContinuityRefusal("continuity-parse-failed", "header block")
    take("", "header block")

    model = Continuity()

    take("## Live decisions", "live decisions")
    take("", "live decisions")
    take(_LIVE_TABLE_HEADER.split("\n")[0], "live decisions")
    take(_LIVE_TABLE_HEADER.split("\n")[1], "live decisions")
    seen: set = set()
    while cursor < len(lines) and lines[cursor].startswith("|"):
        cells = _cells(lines[cursor], 4, "live decisions")
        if not QUALIFIED_ID_RE.match(cells[0]) or cells[0] in seen:
            raise ContinuityRefusal("continuity-parse-failed", "live decisions")
        seen.add(cells[0])
        model.live.append(LiveRow(cells[0], cells[1], cells[2], cells[3]))
        cursor += 1
    take("", "live decisions")

    take("## Cold decisions", "cold decisions")
    take("", "cold decisions")
    if cursor >= len(lines):
        raise ContinuityRefusal("continuity-parse-failed", "cold decisions")
    counters = re.fullmatch(
        r"- Superseded: (\d+); Expired: (\d+); Pruned: (\d+); "
        r"Retrieval: (body|ruling|mixed|none)",
        lines[cursor],
    )
    if counters is None:
        raise ContinuityRefusal("continuity-parse-failed", "cold decisions")
    model.pruned = int(counters.group(3))
    cursor += 1
    take("", "cold decisions")

    take("## Cold index", "cold index")
    take("", "cold index")
    take(_COLD_TABLE_HEADER.split("\n")[0], "cold index")
    take(_COLD_TABLE_HEADER.split("\n")[1], "cold index")
    seen = set()
    while cursor < len(lines) and lines[cursor].startswith("|"):
        cells = _cells(lines[cursor], 6, "cold index")
        if (
            not QUALIFIED_ID_RE.match(cells[0])
            or cells[0] in seen
            or cells[3] not in (DISPOSITION_SUPERSEDED, DISPOSITION_EXPIRED)
            or not QUALIFIED_ID_RE.match(cells[4])
        ):
            raise ContinuityRefusal("continuity-parse-failed", "cold index")
        seen.add(cells[0])
        model.cold.append(
            ColdRow(cells[0], cells[1], cells[2], cells[3], cells[4], cells[5])
        )
        cursor += 1
    take("", "cold index")

    take("## Plan ledger", "plan ledger")
    if cursor < len(lines):
        take("", "plan ledger")
    while cursor < len(lines):
        heading = _SECTION_RE.match(lines[cursor])
        if heading is None:
            raise ContinuityRefusal("continuity-parse-failed", "plan ledger")
        cursor += 1
        take("", "plan ledger")
        body: List[str] = []
        while cursor < len(lines) and not lines[cursor].startswith("### "):
            body.append(lines[cursor])
            cursor += 1
        while body and body[-1] == "":
            body.pop()
        if not body:
            raise ContinuityRefusal("continuity-parse-failed", "plan ledger")
        model.sections.append(
            LedgerSection(heading.group(1), heading.group(2), heading.group(3), body)
        )

    plans = [section.plan for section in model.sections]
    if len(set(plans)) != len(plans) or plans != sorted(plans):
        raise ContinuityRefusal("continuity-parse-failed", "plan ledger")
    if str(model.plans_recorded) != header["Plans recorded"]:
        raise ContinuityRefusal("continuity-parse-failed", "header block")
    recorded_highest = plan_name(model.highest_plan) if model.highest_plan else "none"
    if header["Highest plan recorded"] != recorded_highest:
        raise ContinuityRefusal("continuity-parse-failed", "header block")
    if header["Live decisions"] != str(len(model.live)):
        raise ContinuityRefusal("continuity-parse-failed", "header block")
    if header["Cold decisions"] != str(len(model.cold)):
        raise ContinuityRefusal("continuity-parse-failed", "header block")
    if int(counters.group(1)) != model.superseded or int(counters.group(2)) != model.expired:
        raise ContinuityRefusal("continuity-parse-failed", "cold decisions")
    if counters.group(4) != model.retrieval:
        raise ContinuityRefusal("continuity-parse-failed", "cold decisions")
    return model


# ---------------------------------------------------------------------------
# The read-error contract (§ 8) and the artifact's path guards (§ 13.3).
# ---------------------------------------------------------------------------
def artifact_path(project_root) -> Path:
    return Path(project_root) / CONTINUITY_BASENAME


@dataclass
class ArtifactRead:
    """What a read of the root artifact found. ``raw`` is ``None`` when absent."""

    raw: Optional[bytes] = None
    text: Optional[str] = None
    model: Optional[Continuity] = None

    @property
    def present(self) -> bool:
        return self.raw is not None

    @property
    def highest(self) -> int:
        return self.model.highest_plan if self.model is not None else 0


def read_artifact(project_root, *, parse: bool = True) -> ArtifactRead:
    """Read the root artifact under § 8, or report its absence.

    Absence is a successful disabled state. Presence with any defect — a
    symlink, a non-regular file, a hardlink, invalid UTF-8, an unrecognized
    format, or a malformed section — is a fail-closed refusal, never a silent
    downgrade to the disabled record.
    """
    path = artifact_path(project_root)
    if not os.path.lexists(path):
        return ArtifactRead()
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode):
        raise ContinuityRefusal(
            "continuity-unsafe-artifact", f"artifact is a symlink: {path}"
        )
    if not stat.S_ISREG(info.st_mode):
        raise ContinuityRefusal(
            "continuity-unsafe-artifact", f"artifact is not a regular file: {path}"
        )
    if info.st_nlink > 1:
        raise ContinuityRefusal(
            "continuity-unsafe-artifact",
            f"artifact is a hardlink (st_nlink={info.st_nlink}): {path}",
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ContinuityRefusal(
            "continuity-unsafe-artifact", f"artifact could not be read: {path}: {exc}"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContinuityRefusal(
            "continuity-encoding-invalid", f"artifact is not UTF-8: {path}: {exc}"
        )
    if not parse:
        return ArtifactRead(raw=raw, text=text)
    return ArtifactRead(raw=raw, text=text, model=parse_continuity(text))


# ---------------------------------------------------------------------------
# Mediated deletion and publication residue (§ 4.3, § 11.1).
# ---------------------------------------------------------------------------
def mediated_unlink(project_root, path: Path, *, directory: bool = False) -> None:
    """Remove one path and record the tombstone, as ``delete_log`` already does."""
    if directory:
        os.rmdir(path)
    else:
        os.unlink(path)
    provenance.record_delete(project_root, path, action="mediated-delete")


def residue_prefix(basename: str) -> str:
    """The fixed temp-name prefix ``cli.atomic_write.make_tmp_name`` produces."""
    return f"{basename}.cartmp."


@dataclass
class Collapse:
    """The outcome of one residue collapse over a published basename.

    ``removed`` names each entry with its kind — ``residue`` when it shared the
    committed file's ``(dev, ino)``, ``orphan`` when it did not. ``surviving``
    names the hardlinked siblings the collapse could not remove; those, and
    only those, leave the file unreadable.
    """

    removed: List[str] = field(default_factory=list)
    surviving: List[str] = field(default_factory=list)
    hardlinked: bool = False


def collapse_residue(project_root, path: Path) -> Collapse:
    """Remove every publication residue sibling of ``path``.

    A residue entry is one whose name carries the primitive's own fixed temp
    shape, ``<basename>.cartmp.*``, in the same directory. Inode identity makes
    the removal safe: in a directory this module shares with other writers,
    nothing but this publication's own temporary name can match a committed
    file's inode, and an entry that matches the name shape without matching the
    inode is the uncommitted temporary a killed process left behind.
    """
    result = Collapse()
    parent = path.parent
    prefix = residue_prefix(path.name)
    try:
        entries = sorted(os.listdir(parent))
    except OSError:
        return result
    try:
        committed = os.stat(path, follow_symlinks=False)
        identity: Optional[Tuple[int, int]] = (committed.st_dev, committed.st_ino)
    except OSError:
        identity = None
    for name in entries:
        if not name.startswith(prefix):
            continue
        sibling = parent / name
        try:
            info = os.lstat(sibling)
        except OSError:
            continue
        is_residue = identity is not None and (info.st_dev, info.st_ino) == identity
        try:
            mediated_unlink(project_root, sibling)
        except OSError:
            if is_residue:
                result.surviving.append(name)
            continue
        result.removed.append(f"{name} ({'residue' if is_residue else 'orphan'})")
        result.hardlinked = result.hardlinked or is_residue
    if result.surviving:
        return result
    try:
        if os.stat(path, follow_symlinks=False).st_nlink > 1:
            result.surviving.append(f"{path.name} remains at st_nlink > 1")
    except OSError:
        pass
    return result


NOT_PUBLISHED = "not-published"
PUBLISHED = "published"
UNRESOLVED = "unresolved"


@dataclass
class Publication:
    """The § 11.1 commit-aware classification of one publication."""

    outcome: str
    refusal: Optional[Exception] = None
    collapse: Collapse = field(default_factory=Collapse)

    @property
    def residue_collapsed(self) -> bool:
        return self.collapse.hardlinked

    @property
    def surviving(self) -> List[str]:
        return self.collapse.surviving

    @property
    def clean(self) -> bool:
        return self.outcome == PUBLISHED and not self.collapse.surviving


def publish(
    project_root,
    path: Path,
    prior: Optional[bytes],
    intended: bytes,
    writer: Callable[[], None],
) -> Publication:
    """Perform one publication and classify it **from the artifact**.

    The shared primitive commits a *first* publication with ``os.link`` and
    then unlinks its temporary name, so a named refusal can arrive after the
    commit. Nothing downstream is keyed on "a refusal was raised": the outcome
    is decided by what is on disk, against the two byte strings the caller
    already holds.
    """
    refusal: Optional[Exception] = None
    try:
        writer()
    except (GuardRefusal, ContinuityRefusal, OSError) as exc:
        refusal = exc

    try:
        info = os.lstat(path)
    except FileNotFoundError:
        outcome = NOT_PUBLISHED if prior is None else UNRESOLVED
        return Publication(outcome, refusal)
    except OSError:
        return Publication(UNRESOLVED, refusal)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return Publication(UNRESOLVED, refusal)
    try:
        landed = path.read_bytes()
    except OSError:
        return Publication(UNRESOLVED, refusal)
    if landed == intended:
        return Publication(PUBLISHED, refusal, collapse_residue(project_root, path))
    if prior is not None and landed == prior:
        return Publication(NOT_PUBLISHED, refusal)
    return Publication(UNRESOLVED, refusal)


def _atomic_publish_file(
    real_root: str, directory: Path, name: str, data: bytes, *, mode: int = 0o644
) -> None:
    """Write ``name`` into ``directory`` through the shared atomic mechanism.

    ``expect_absent`` is the right precondition for both reservation files:
    each is written only where it is absent, and ``os.link`` is the call that
    refuses ``toctou`` on a destination that appeared rather than silently
    overwriting it. The parent is pinned by its own ``O_NOFOLLOW |
    O_DIRECTORY`` fd and the whole chain back to the project root is
    re-verified by ``(dev, ino)`` immediately before the bind.
    """
    from cli import mediated_write as _mediated

    canonical_parent = os.path.realpath(str(directory))
    if not os.path.isdir(canonical_parent):
        raise GuardRefusal(
            "missing-parent", f"destination parent directory does not exist: {directory}"
        )
    snapshot = _snapshot_chain(canonical_parent, real_root)
    tmp_name = make_tmp_name(name)
    safe_mode = (mode & 0o777) & ~0o111
    if DIR_FD_SUPPORTED and not _mediated._force_path_based:
        _atomic_write_via_dir_fd(
            canonical_parent, snapshot, name, tmp_name, data, safe_mode,
            expect_absent=True,
        )
    else:
        _atomic_write_via_path(
            canonical_parent, snapshot, name, tmp_name, data, safe_mode,
            expect_absent=True,
        )


# ---------------------------------------------------------------------------
# The plan-id reservation (§ 4.3, § 6.5).
#
# A `ledger` closeout creates `archive/PLAN-NNN/` holding `NOT-ARCHIVED.md`, no
# `CLOSEOUT.md`, and — only after an attempt proven not to have committed — a
# `LEDGER-FAILED.md` marker. It holds no plan content: it exists to occupy the
# plan id so no later closeout can reuse it, and to carry on disk, without
# reference to `CONTINUITY.md`, whether the attempt that created it is known to
# have recorded nothing.
# ---------------------------------------------------------------------------
SHAPE_MARKED = "marked"
SHAPE_MARKER_ONLY = "marker-only"
SHAPE_UNMARKED = "unmarked"
SHAPE_INCOMPLETE = "incomplete"
SHAPE_EMPTY = "empty"
SHAPE_ABSENT = "absent"
SHAPE_ARCHIVE = "archive"
SHAPE_MALFORMED = "malformed"

#: The four shapes that prove nothing was committed, and are therefore the only
#: ones § 11.6 may release.
RELEASABLE_SHAPES = (SHAPE_MARKED, SHAPE_MARKER_ONLY, SHAPE_EMPTY, SHAPE_INCOMPLETE)


def not_archived_body(plan_id: str) -> str:
    return (
        f"# {plan_id} - not archived\n"
        "\n"
        "This plan window was closed with the `ledger` preservation outcome. No plan\n"
        "content was archived. The surviving record is the project-root `CONTINUITY.md`.\n"
        f"This directory exists only to reserve the plan id `{plan_id}`.\n"
    )


def ledger_failed_body(plan_id: str) -> str:
    return (
        f"# {plan_id} - ledger attempt failed\n"
        "\n"
        "The last `write-continuity --mode ledger` attempt for this plan window ended in\n"
        f"a refusal before any continuity content was written, so `{plan_id}` is recorded\n"
        "in no `CONTINUITY.md`. Retry that command to finalize the plan, or run\n"
        f"`release-reservation --plan {plan_id}` to give the reservation back.\n"
    )


def reservation_row(plan_id: str, closed: str) -> str:
    return f"| `{plan_id}` | {closed} | {RESERVATION_SUMMARY} |"


def archive_root(project_root) -> Path:
    return Path(project_root) / "archive"


def index_path(project_root) -> Path:
    return archive_root(project_root) / "INDEX.md"


def existing_archive_numbers(project_root) -> List[int]:
    """Every ``PLAN-NNN`` entry under ``archive/``, by the allocator's own rule.

    Mirrors ``archive_plan._existing_archives``: an entry is admitted on its
    name and on being a non-symlink directory, and its contents are never
    inspected — which is why a reservation occupies its id in every shape.
    """
    root = archive_root(project_root)
    found: List[int] = []
    if not root.is_dir() or root.is_symlink():
        return found
    for entry in root.iterdir():
        match = PLAN_ID_RE.match(entry.name)
        if match and entry.is_dir() and not entry.is_symlink():
            found.append(int(match.group(1)))
    return sorted(found)


def highest_archive_number(project_root) -> int:
    return max(existing_archive_numbers(project_root), default=0)


def has_reservation_row(project_root, plan_id: str) -> bool:
    """Whether ``archive/INDEX.md`` carries this plan's **reservation** row.

    The row's summary is fixed text, so a reservation's row is
    self-identifying: a real archive's row is never a reservation row. This is
    the durable discriminator that separates an ``incomplete`` reservation from
    an ``unmarked`` one.
    """
    path = index_path(project_root)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    for line in text.splitlines():
        match = _RESERVATION_ROW_RE.match(line.strip())
        if match is not None and match.group(1) == plan_id:
            return True
    return False


@dataclass
class Reservation:
    """What ``archive/PLAN-NNN`` is, after its publication residue is collapsed."""

    plan: str
    path: Path
    shape: str
    entries: List[str] = field(default_factory=list)
    row_present: bool = False
    residue_collapsed: List[str] = field(default_factory=list)
    detail: str = ""

    @property
    def is_reservation(self) -> bool:
        return self.shape in (
            SHAPE_MARKED, SHAPE_MARKER_ONLY, SHAPE_UNMARKED, SHAPE_INCOMPLETE, SHAPE_EMPTY,
        )

    @property
    def releasable(self) -> bool:
        return self.shape in RELEASABLE_SHAPES


def classify_reservation(project_root, plan_id: str, *, collapse: bool = True) -> Reservation:
    """Name ``archive/PLAN-NNN``'s shape over the two axes § 4.3 reads.

    The entry names left after the residue collapse, and whether
    ``archive/INDEX.md`` carries this plan's reservation row. Nothing else is
    read; in particular no continuity content is opened on any path.
    """
    path = archive_root(project_root) / plan_id
    row = has_reservation_row(project_root, plan_id)
    if not os.path.lexists(path):
        return Reservation(plan_id, path, SHAPE_ABSENT, row_present=row)
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return Reservation(
            plan_id, path, SHAPE_MALFORMED, row_present=row,
            detail=f"{path} is not a plain directory",
        )
    collapsed: List[str] = []
    if collapse:
        for basename in (NOT_ARCHIVED_BASENAME, LEDGER_FAILED_BASENAME):
            outcome = collapse_residue(project_root, path / basename)
            collapsed.extend(outcome.removed)
            if outcome.surviving:
                raise ContinuityRefusal(
                    "continuity-reservation-cleanup-failed",
                    "publication residue could not be collapsed: "
                    + ", ".join(str(path / name) for name in outcome.surviving),
                )
    try:
        entries = sorted(os.listdir(path))
    except OSError as exc:
        return Reservation(
            plan_id, path, SHAPE_MALFORMED, row_present=row,
            residue_collapsed=collapsed, detail=f"{path} could not be read: {exc}",
        )
    state = Reservation(
        plan_id, path, SHAPE_MALFORMED, entries=entries, row_present=row,
        residue_collapsed=collapsed,
    )
    if "CLOSEOUT.md" in entries:
        state.shape = SHAPE_ARCHIVE
        return state
    foreign = [
        name
        for name in entries
        if name not in (NOT_ARCHIVED_BASENAME, LEDGER_FAILED_BASENAME)
    ]
    if foreign:
        state.shape = SHAPE_MALFORMED
        state.detail = f"{path} holds entries outside the reservation: " + ", ".join(foreign)
        return state
    sentinel = NOT_ARCHIVED_BASENAME in entries
    marker = LEDGER_FAILED_BASENAME in entries
    if sentinel and marker:
        state.shape = SHAPE_MARKED
    elif marker:
        state.shape = SHAPE_MARKER_ONLY
    elif sentinel:
        state.shape = SHAPE_UNMARKED if row else SHAPE_INCOMPLETE
    else:
        state.shape = SHAPE_EMPTY
    return state


def marker_plan_id(path: Path) -> Optional[str]:
    """The plan id a ``LEDGER-FAILED.md`` names, or ``None`` if unreadable."""
    try:
        first = path.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, UnicodeDecodeError, IndexError):
        return None
    match = re.fullmatch(r"# (PLAN-\d{3}) - ledger attempt failed", first)
    return match.group(1) if match is not None else None


# ---------------------------------------------------------------------------
# The window witness (§ 4.3).
#
# `W(X)` holds for a plan id `X` when `prompt_evidence.read_ledger(root,
# plan_id=X)` returns at least one admitted record and reports no
# `foreign-plan-window` error. At most one `W(X)` can hold. It is the durable
# fact that separates a closeout that is *retrying* from one that was
# *abandoned* and from a *later plan* closing over an older reservation.
# ---------------------------------------------------------------------------
_PROBE_WINDOW = "PLAN-000"
_FOREIGN_P_RE = re.compile(r"carries p='(PLAN-\d{3})'")


def _foreign(ledger) -> bool:
    return any(error.get("rule") == "foreign-plan-window" for error in ledger.errors)


def witness_holds(project_root, plan_id: str) -> bool:
    """Whether ``W(plan_id)`` holds."""
    from cli import prompt_evidence

    ledger = prompt_evidence.read_ledger(project_root, plan_id=plan_id)
    return bool(ledger.records) and not _foreign(ledger)


def witness_contradicts(project_root, plan_id: str) -> bool:
    """Whether the log names a window other than ``plan_id``, or mixes windows.

    A silent witness contradicts nothing, so a project that records no prompt
    evidence is unaffected.
    """
    from cli import prompt_evidence

    return _foreign(prompt_evidence.read_ledger(project_root, plan_id=plan_id))


def window_witness(project_root) -> Optional[str]:
    """The plan id the witness names, or ``None`` when it is silent.

    Candidates come from ``read_ledger``'s own foreign-window reports over a
    probe window; each candidate is then decided by a real ``read_ledger``
    call, so the witness is computed from that function alone and this module
    parses no log line itself.
    """
    from cli import prompt_evidence

    probe = prompt_evidence.read_ledger(project_root, plan_id=_PROBE_WINDOW)
    candidates = {_PROBE_WINDOW} if probe.records else set()
    for error in probe.errors:
        if error.get("rule") != "foreign-plan-window":
            continue
        match = _FOREIGN_P_RE.search(error.get("detail", ""))
        if match is not None:
            candidates.add(match.group(1))
    holding = [plan for plan in sorted(candidates) if witness_holds(project_root, plan)]
    return holding[0] if len(holding) == 1 else None


# ---------------------------------------------------------------------------
# `archive/INDEX.md` — ensured and dropped through archive-plan's own helpers.
# ---------------------------------------------------------------------------
def _index_publication(project_root, prior: Optional[bytes], intended: str) -> None:
    """Publish ``archive/INDEX.md`` and refuse with § 6.5's three codes.

    ``_atomic_text`` commits with ``os.replace`` on a create and on a rewrite
    alike, so this publication has no post-commit step and can leave no
    residue — a property worth naming rather than assuming, because
    ``_existing_archives`` refuses ``archive-index: hardlinked file is not
    allowed`` at ``st_nlink > 1``.
    """
    from cli.commands.archive_plan import _atomic_text

    path = index_path(project_root)
    data = intended.encode("utf-8")
    result = publish(
        project_root, path, prior, data, lambda: _atomic_text(path, intended)
    )
    if result.outcome == NOT_PUBLISHED:
        raise ContinuityRefusal(
            "continuity-reservation-write-failed",
            f"archive/INDEX.md was not published: {path}",
        )
    if result.outcome == UNRESOLVED:
        raise ContinuityRefusal(
            "continuity-reservation-write-unresolved",
            f"archive/INDEX.md holds neither the prior nor the intended bytes: {path}",
        )
    if result.surviving:
        raise ContinuityRefusal(
            "continuity-reservation-cleanup-failed",
            "publication residue survived beside archive/INDEX.md: "
            + ", ".join(result.surviving),
        )


def ensure_reservation_row(project_root, plan_id: str, closed: str) -> bool:
    """Append this plan's reservation row unless one already carries the text.

    Ensuring rather than appending is what makes creation, adoption, and
    release idempotent over the one file none of them owns exclusively.
    """
    if has_reservation_row(project_root, plan_id):
        return False
    from cli.commands.archive_plan import _index_body

    path = index_path(project_root)
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    prior = path.read_bytes() if path.is_file() else None
    intended = _index_body(path, plan_id, closed, RESERVATION_SUMMARY)
    _index_publication(project_root, prior, intended)
    return True


def drop_reservation_row(project_root, plan_id: str) -> Tuple[bool, List[str]]:
    """Remove this plan's reservation row, matched on its fixed summary.

    Any other row for the same id is left in place and reported: a real
    archive's row is never a reservation row, so an archive that has since
    taken the id keeps its own entry.
    """
    path = index_path(project_root)
    if not path.is_file():
        return False, []
    prior = path.read_bytes()
    kept: List[str] = []
    keep_lines: List[str] = []
    dropped = False
    for line in prior.decode("utf-8").split("\n"):
        stripped = line.strip()
        match = _RESERVATION_ROW_RE.match(stripped)
        if match is not None and match.group(1) == plan_id:
            dropped = True
            continue
        if stripped.startswith(f"| `{plan_id}` |"):
            kept.append(stripped)
        keep_lines.append(line)
    if not dropped:
        return False, kept
    _index_publication(project_root, prior, "\n".join(keep_lines))
    return True, kept


# ---------------------------------------------------------------------------
# Reservation creation, adoption, and removal (§ 6.5, § 11.6).
# ---------------------------------------------------------------------------
REPAIR_RESIDUE = "publication residue collapsed"
REPAIR_SENTINEL = NOT_ARCHIVED_BASENAME
REPAIR_ROW = "archive/INDEX.md row"
REPAIR_MARKER = "LEDGER-FAILED.md cleared"


def _publish_reservation_file(
    project_root, directory: Path, name: str, body: str
) -> None:
    """Publish one reservation file, classified by the same § 11.1 step."""
    real_root = os.path.realpath(os.fspath(project_root))
    path = directory / name
    data = body.encode("utf-8")
    result = publish(
        project_root,
        path,
        None,
        data,
        lambda: _atomic_publish_file(real_root, directory, name, data),
    )
    if result.outcome == NOT_PUBLISHED:
        raise ContinuityRefusal(
            "continuity-reservation-write-failed", f"{name} was not published: {path}"
        )
    if result.outcome == UNRESOLVED:
        raise ContinuityRefusal(
            "continuity-reservation-write-unresolved",
            f"{name} holds neither absence nor the intended bytes: {path}",
        )
    if result.surviving:
        raise ContinuityRefusal(
            "continuity-reservation-cleanup-failed",
            f"publication residue survived beside {name}: " + ", ".join(result.surviving),
        )


def create_reservation(project_root, plan_id: str, closed: str) -> Path:
    """Create a whole reservation, in the order every prefix of which is named.

    ``mkdir`` leaves **empty**; the sentinel without its index row leaves
    **incomplete**; the row completes it to **unmarked**. The row is ensured
    strictly after the sentinel and strictly before this pass can publish
    anything, which is what makes a sentinel *without* a row a durable proof
    that nothing was committed.
    """
    root = archive_root(project_root)
    if os.path.lexists(root) and (root.is_symlink() or not root.is_dir()):
        raise ContinuityRefusal(
            "continuity-reservation-malformed", f"archive/ is not a plain directory: {root}"
        )
    path = root / plan_id
    if os.path.lexists(path):
        raise ContinuityRefusal(
            "continuity-reservation-collision", f"reservation path already exists: {path}"
        )
    root.mkdir(mode=0o755, parents=True, exist_ok=True)
    try:
        path.mkdir(mode=0o755)
    except OSError as exc:
        raise ContinuityRefusal(
            "continuity-reservation-write-failed",
            f"reservation directory could not be created: {path}: {exc}",
        )
    _publish_reservation_file(project_root, path, NOT_ARCHIVED_BASENAME, not_archived_body(plan_id))
    ensure_reservation_row(project_root, plan_id, closed)
    return path


def adopt_reservation(
    project_root, state: Reservation, closed: str
) -> List[str]:
    """Complete whatever a prefix of an earlier creation or release left partial.

    The marker is cleared **last**: until the reservation is whole again it is
    the only durable statement that nothing was committed, and a process killed
    part-way through the adoption must not be able to erase it while leaving
    the reservation partial.
    """
    repaired: List[str] = []
    if state.residue_collapsed:
        repaired.append(REPAIR_RESIDUE)
    path = state.path
    if not (path / NOT_ARCHIVED_BASENAME).is_file():
        _publish_reservation_file(
            project_root, path, NOT_ARCHIVED_BASENAME, not_archived_body(state.plan)
        )
        repaired.append(REPAIR_SENTINEL)
    if ensure_reservation_row(project_root, state.plan, closed):
        repaired.append(REPAIR_ROW)
    marker = path / LEDGER_FAILED_BASENAME
    if marker.is_file():
        mediated_unlink(project_root, marker)
        repaired.append(REPAIR_MARKER)
    return repaired


def write_failure_marker(project_root, plan_id: str) -> str:
    """Write ``LEDGER-FAILED.md`` as the last act of a proven non-commit.

    Reported, never retried, and the one publication whose failure is not
    itself a refusal: the pass writing it is already exiting non-zero, so what
    its outcome changes is what the next command finds.
    """
    path = archive_root(project_root) / plan_id / LEDGER_FAILED_BASENAME
    body = ledger_failed_body(plan_id)
    data = body.encode("utf-8")
    real_root = os.path.realpath(os.fspath(project_root))
    result = publish(
        project_root,
        path,
        None,
        data,
        lambda: _atomic_publish_file(real_root, path.parent, LEDGER_FAILED_BASENAME, data),
    )
    if result.outcome == PUBLISHED:
        return "written"
    if result.outcome == NOT_PUBLISHED:
        return "write-failed"
    return "unresolved"


# ---------------------------------------------------------------------------
# Decision files: the single authoritative source for Scope, Ruling, Status,
# Supersedes, and Expires (§ 5.3).
# ---------------------------------------------------------------------------
@dataclass
class DecisionFile:
    """One locked decision, qualified into the plan window now closing."""

    dec: str
    qualified: str
    path: Path
    status: str
    scope: Optional[str] = None
    ruling: Optional[str] = None
    supersedes: List[str] = field(default_factory=list)
    expires: List[str] = field(default_factory=list)

    @property
    def locked(self) -> bool:
        return self.status == "locked"

    @property
    def candidate(self) -> bool:
        return self.locked and self.scope is not None and self.ruling is not None

    @property
    def removes(self) -> bool:
        return bool(self.supersedes or self.expires)


def read_decisions(project_root, plan_id: str) -> List[DecisionFile]:
    """Enumerate ``decisions/*.md``, qualified into ``plan_id``.

    Every locked file is a **removal source**; the subset that also carries a
    valid ``Scope:`` and ``Ruling:`` is a live-row candidate. Reading removal
    headers from every locked decision — not only from candidates — is what
    keeps a pure-expiry decision from silently dropping its expiry.
    """
    directory = Path(project_root) / "decisions"
    decisions: List[DecisionFile] = []
    if not directory.is_dir():
        return decisions
    for entry in sorted(directory.iterdir()):
        if not entry.is_file() or entry.suffix != ".md" or entry.name == "INDEX.md":
            continue
        if not DECISION_FILE_RE.match(entry.name):
            raise ContinuityRefusal(
                "continuity-decision-name-noncanonical",
                f"decision basename is not DEC-NNN.md: {entry}",
            )
        try:
            content = entry.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ContinuityRefusal(
                "continuity-field-unsafe", f"{entry} body must be valid UTF-8: {exc}"
            )
        headers = parse_headers(content)
        record = DecisionFile(
            dec=entry.stem,
            qualified=f"{plan_id}/{entry.stem}",
            path=entry,
            status=headers.get("Status", ""),
        )
        for field_name in ("Scope", "Ruling"):
            if field_name not in headers:
                continue
            violation = cell_violation(headers[field_name], field_name)
            if violation is not None:
                raise ContinuityRefusal(
                    "continuity-field-unsafe",
                    f"{entry} {field_name} {violation}",
                )
            setattr(record, field_name.lower(), headers[field_name])
        for header, attribute in (("Supersedes", "supersedes"), ("Expires", "expires")):
            if header not in headers:
                continue
            refs, violation = parse_reference_header(
                headers[header], header, allow_repeat=True
            )
            if violation is not None:
                rule = (
                    "continuity-reference-list-overlong"
                    if "references" in violation
                    else "continuity-reference-malformed"
                )
                raise ContinuityRefusal(rule, f"{entry} {violation}")
            setattr(record, attribute, refs)
        decisions.append(record)
    return decisions


# ---------------------------------------------------------------------------
# Reference resolution and the removal multimap (§ 4.2, § 10.5).
# ---------------------------------------------------------------------------
def resolve_reference(
    ref: str, universe: Sequence[str], plan_id: str, source: str
) -> str:
    """Resolve one ``Supersedes:``/``Expires:`` reference against the live set.

    A qualified reference names a decision recorded in a closed plan window and
    is the only form that can reach a live row from a prior plan. A bare
    reference names the plan window now closing: a bare form that matches more
    than one row is ambiguous, and one that matches only a prior plan's row is
    unresolved — the qualified form is the remedy the refusal names.
    """
    if "/" in ref:
        matches = [row for row in universe if row == ref]
        if not matches:
            raise ContinuityRefusal(
                "continuity-reference-unresolved",
                f"{source} names {ref}, which resolves to no live row",
            )
        return matches[0]
    matches = [row for row in universe if row.split("/", 1)[1] == ref]
    if len(matches) > 1:
        raise ContinuityRefusal(
            "continuity-reference-ambiguous",
            f"{source} names the bare reference {ref}, which resolves to "
            + ", ".join(matches),
        )
    if not matches:
        raise ContinuityRefusal(
            "continuity-reference-unresolved",
            f"{source} names {ref}, which resolves to no live row",
        )
    if not matches[0].startswith(f"{plan_id}/"):
        raise ContinuityRefusal(
            "continuity-reference-unresolved",
            f"{source} names the bare reference {ref}; a bare reference names the "
            f"plan window now closing, so use {matches[0]} to reach a prior plan",
        )
    return matches[0]


def removal_multimap(
    decisions: Sequence[DecisionFile], universe: Sequence[str], plan_id: str
) -> Dict[str, List[Tuple[str, str]]]:
    """Target identity → the distinct ``(removing id, disposition)`` pairs.

    Every § 10.5 rule is decided on this multimap alone, so the outcome does not
    depend on file order, directory iteration order, or which header the writer
    happened to parse first.
    """
    multimap: Dict[str, List[Tuple[str, str]]] = {}
    for decision in decisions:
        if not decision.locked:
            continue
        for refs, disposition in (
            (decision.supersedes, DISPOSITION_SUPERSEDED),
            (decision.expires, DISPOSITION_EXPIRED),
        ):
            for ref in refs:
                target = resolve_reference(ref, universe, plan_id, str(decision.path))
                if target == decision.qualified:
                    raise ContinuityRefusal(
                        "continuity-reference-self",
                        f"{decision.path} names itself, {target}",
                    )
                pair = (decision.qualified, disposition)
                pairs = multimap.setdefault(target, [])
                if pair not in pairs:
                    pairs.append(pair)
    for target, pairs in multimap.items():
        dispositions = {disposition for _, disposition in pairs}
        if len(dispositions) > 1:
            raise ContinuityRefusal(
                "continuity-removal-target-conflicted",
                f"{target} is named by "
                + ", ".join(f"{who} ({how})" for who, how in pairs),
            )
        if len(pairs) > 1:
            raise ContinuityRefusal(
                "continuity-removal-target-duplicated",
                f"{target} is named by " + ", ".join(who for who, _ in pairs),
            )
    return multimap


# ---------------------------------------------------------------------------
# Plan-id resolution and pass selection (§ 4.3, § 6.6, § 11.2).
# ---------------------------------------------------------------------------
SOURCE_ARCHIVE_BOUND = "archive-bound"
SOURCE_ALLOCATED = "reservation-allocated"
SOURCE_ADOPTED = "reservation-adopted"
SOURCE_RECORDED = "recorded"

PASS_COMPUTE = "compute"
PASS_REPLACE = "ledger-replace"

_AMBIGUITY_REMEDIES = (
    "run `--plan {here}` to complete or correct the recorded plan, or "
    "`--plan {next}` to assert a new one"
)


@dataclass
class PlanBinding:
    """Which plan a pass records, how it was decided, and which pass it is."""

    plan: str
    source: str
    kind: str
    stale_reservation: Optional[str] = None
    witness: Optional[str] = None
    reservation: Optional[Reservation] = None


def _mismatch(requested: str, expected: str) -> ContinuityRefusal:
    return ContinuityRefusal(
        "continuity-plan-id-mismatch",
        f"--plan {requested} does not match the resolved plan {expected}",
    )


def _allocate(number: int) -> str:
    """Bind a newly allocated id, or refuse at the terminal counter.

    Called only where an allocation actually happens: 999 is terminal for every
    *allocating* path and for nothing else, so a ledger-replace of an already
    recorded plan and `prune-continuity` both still work there.
    """
    if number > PLAN_COUNTER_MAX:
        raise ContinuityRefusal(
            "continuity-plan-counter-exhausted",
            f"PLAN counter exhausted at {PLAN_COUNTER_MAX}; no further plan id can "
            "be allocated. The remedy is a new project: recycling a plan id would "
            "make every recorded identity and every locator ambiguous.",
        )
    return plan_name(number)


def select_recorded_pass(
    model: Optional[Continuity], requested: Optional[str]
) -> Optional[PlanBinding]:
    """Decide the pass from the recorded set alone, before any mode-specific rule.

    Stating pass selection against "is the resolved plan id already recorded?"
    rather than as an arithmetic relation to ``H`` is what lets legitimate
    ledger gaps coexist with idempotency.
    """
    if requested is None or model is None:
        return None
    highest = model.highest_plan
    if model.records(requested):
        if plan_number(requested) < highest:
            raise ContinuityRefusal(
                "continuity-plan-closed",
                f"{requested} closed below the highest recorded plan "
                f"{plan_name(highest)}; a closed plan's contribution is not rewritten",
            )
        return PlanBinding(requested, SOURCE_RECORDED, PASS_REPLACE)
    if plan_number(requested) <= highest:
        raise ContinuityRefusal(
            "continuity-plan-id-mismatch",
            f"--plan {requested} is not recorded and is not above the highest "
            f"recorded plan {plan_name(highest)}; a plan the operator chose not to "
            "preserve is not retro-recorded",
        )
    return None


def bind_archive(project_root, model: Optional[Continuity], requested: str) -> PlanBinding:
    """Validate an ``index``-mode ``--plan`` against the archive just created.

    Nothing is allocated. Conditions 1 and 2 make it structurally impossible to
    compose a row naming a path that never materialized; condition 3 is what
    makes "the archive just created" checkable, because ``archive-plan`` always
    allocates the highest id.
    """
    numbers = existing_archive_numbers(project_root)
    highest_entry = plan_name(max(numbers)) if numbers else "<none>"
    path = archive_root(project_root) / requested
    highest_recorded = model.highest_plan if model is not None else 0

    def refuse(reason: str) -> ContinuityRefusal:
        return ContinuityRefusal(
            "continuity-archive-unbound",
            f"--plan {requested} {reason}; highest archive entry is {highest_entry}",
        )

    if not path.is_dir() or path.is_symlink():
        raise refuse("names no real archive directory")
    if not (path / "CLOSEOUT.md").is_file():
        raise refuse("names a reservation, not an archive: no CLOSEOUT.md")
    if not numbers or plan_number(requested) != max(numbers):
        raise refuse("is not the highest-numbered archive entry")
    if plan_number(requested) <= highest_recorded:
        raise refuse(f"is not above the highest recorded plan {plan_name(highest_recorded)}")
    return PlanBinding(requested, SOURCE_ARCHIVE_BOUND, PASS_COMPUTE)


def resolve_ledger_plan(
    project_root, model: Optional[Continuity], requested: Optional[str]
) -> PlanBinding:
    """Resolve a ``ledger`` id from ``archive/``, ``H``, and the window witness.

    Reads nothing else — no ``decisions/`` directory, no configuration, and no
    other part of ``CONTINUITY.md``. The table is total: every combination of
    ``M``, ``H``, the shape of ``archive/PLAN-<M>``, and the witness selects
    exactly one outcome, and every outcome either binds an id or refuses with a
    named code.
    """
    recorded = select_recorded_pass(model, requested)
    if recorded is not None:
        recorded.witness = window_witness(project_root)
        return recorded

    highest_archive = highest_archive_number(project_root)
    highest_recorded = model.highest_plan if model is not None else 0
    witness = window_witness(project_root)
    following_number = max(highest_archive, highest_recorded) + 1
    # The next allocatable id, or None past the terminal counter. Nothing can
    # *equal* an unrepresentable id, so every comparison below is False there
    # and only a real allocation raises: 999 is terminal for every allocating
    # path and for nothing else.
    following = (
        plan_name(following_number) if following_number <= PLAN_COUNTER_MAX else None
    )

    def allocate() -> str:
        return _allocate(following_number)

    def ambiguous(rule: str, here: str) -> ContinuityRefusal:
        # Both remedies are stated verbatim so the disambiguation never asks
        # the operator to re-derive the allocator. Past the terminal counter
        # only the first remedy exists, and the refusal says so.
        if following is None:
            remedies = (
                f"run `--plan {here}` to complete or correct the recorded plan; "
                f"no new id can be asserted, because the PLAN counter is "
                f"exhausted at {PLAN_COUNTER_MAX}"
            )
        else:
            remedies = _AMBIGUITY_REMEDIES.format(here=here, next=following)
        return ContinuityRefusal(
            rule,
            f"{here} cannot be told apart from a new closeout without --plan: "
            + remedies,
        )

    if highest_archive == 0 and highest_recorded == 0:
        if requested is not None and requested != following:
            raise _mismatch(requested, following or f"PLAN-{following_number}")
        return PlanBinding(allocate(), SOURCE_ALLOCATED, PASS_COMPUTE, witness=witness)

    if highest_archive > highest_recorded:
        here = plan_name(highest_archive)
        state = classify_reservation(project_root, here)
        if state.is_reservation:
            if requested is None:
                if witness == here:
                    return PlanBinding(
                        here, SOURCE_ADOPTED, PASS_COMPUTE,
                        witness=witness, reservation=state,
                    )
                if witness is not None and witness == following:
                    return PlanBinding(
                        allocate(), SOURCE_ALLOCATED, PASS_COMPUTE,
                        stale_reservation=here, witness=witness,
                    )
                raise ambiguous("continuity-ledger-reservation-ambiguous", here)
            if requested == here:
                return PlanBinding(
                    here, SOURCE_ADOPTED, PASS_COMPUTE,
                    witness=witness, reservation=state,
                )
            if requested is not None and requested == following:
                return PlanBinding(
                    allocate(), SOURCE_ALLOCATED, PASS_COMPUTE,
                    stale_reservation=here, witness=witness,
                )
            raise _mismatch(requested, f"{here} or {following}")
        if state.shape == SHAPE_ARCHIVE:
            if requested is None:
                if witness is not None and witness == following:
                    return PlanBinding(
                        allocate(), SOURCE_ALLOCATED, PASS_COMPUTE, witness=witness
                    )
                raise ambiguous("continuity-ledger-plan-ambiguous", here)
            if requested == here:
                raise ContinuityRefusal(
                    "continuity-mode-archive-present",
                    f"an archive exists for {here}, so its outcome is archive+index, "
                    "not ledger",
                )
            if requested is not None and requested == following:
                return PlanBinding(
                    allocate(), SOURCE_ALLOCATED, PASS_COMPUTE, witness=witness
                )
            raise _mismatch(requested, following or f"PLAN-{following_number}")
        raise ContinuityRefusal(
            "continuity-reservation-malformed",
            state.detail or f"{state.path} is neither a reservation nor an archive",
        )

    if highest_archive == highest_recorded:
        here = plan_name(highest_recorded)
        if requested is None:
            state = classify_reservation(project_root, here)
            if witness == here and state.is_reservation:
                return PlanBinding(
                    here, SOURCE_RECORDED, PASS_REPLACE,
                    witness=witness, reservation=state,
                )
            if witness is not None and witness == following:
                return PlanBinding(
                    allocate(), SOURCE_ALLOCATED, PASS_COMPUTE, witness=witness
                )
            raise ambiguous("continuity-ledger-plan-ambiguous", here)
        if requested is not None and requested == following:
            return PlanBinding(
                allocate(), SOURCE_ALLOCATED, PASS_COMPUTE, witness=witness
            )
        raise _mismatch(requested, following or f"PLAN-{following_number}")

    # M < H — a recorded plan's directory was removed out of band. Allocating
    # `max(M, H) + 1` rather than `M + 1` is what stops a removed archive from
    # re-minting an id `CONTINUITY.md` already records.
    if requested is not None and requested != following:
        raise _mismatch(requested, following or f"PLAN-{following_number}")
    return PlanBinding(allocate(), SOURCE_ALLOCATED, PASS_COMPUTE, witness=witness)


# ---------------------------------------------------------------------------
# Ledger-section bodies and the file ceiling (§ 5.4, § 12.2, § 12.3).
# ---------------------------------------------------------------------------
TOMBSTONE_TEMPLATE = (
    "- Pruned: ledger entry removed {pruned} to recover file capacity; this "
    "plan's indexed rows and any archive are unchanged"
)


def ledger_body_lines(content: str) -> Tuple[List[str], Optional[str]]:
    """Normalize one PM-authored six-group body into section lines.

    Returns ``(lines, violation)``. The body is authored where judgment lives;
    what this enforces is only that it can be carried in the file's section
    grammar without ambiguity.
    """
    if "\r" in content:
        return [], "ledger section body must not contain CR"
    lines = content.split("\n")
    while lines and lines[-1].strip() == "":
        lines.pop()
    while lines and lines[0].strip() == "":
        lines.pop(0)
    if not lines:
        return [], "ledger section body must not be empty"
    for line in lines:
        if line.startswith("#"):
            return [], (
                "ledger section body must not contain a Markdown heading line: "
                f"{line!r}"
            )
    return lines, None


def tombstone_section(section: LedgerSection, pruned: str) -> LedgerSection:
    return LedgerSection(
        section.plan,
        section.closed,
        section.preservation,
        [TOMBSTONE_TEMPLATE.format(pruned=pruned)],
    )


def prunable_bytes(project_root, model: Continuity) -> Tuple[int, List[str]]:
    """How much a full prune could still recover, and what it would touch.

    Live rows are never prunable, and neither is a cold row whose ``Body`` is
    ``none`` — precisely every cold row a ``ledger`` closeout writes, and the
    only surviving copy of its ruling. That asymmetry is why a fully pruned
    ``ledger`` file holds fewer plans than a fully pruned ``archive+index`` one.
    """
    highest = plan_name(model.highest_plan) if model.highest_plan else None
    recoverable = 0
    targets: List[str] = []
    for section in model.sections:
        if section.tombstoned or section.plan == highest:
            continue
        saving = section.unit_bytes() - tombstone_section(section, "2026-01-01").unit_bytes()
        if saving > 0:
            recoverable += saving
            targets.append(f"--plan {section.plan}")
    for row in model.cold:
        if not row.has_locator:
            continue
        if not (Path(project_root) / row.body).is_file():
            continue
        recoverable += len(
            (
                f"| {row.id} | {row.scope} | {row.ruling} | {row.disposition} | "
                f"{row.removed_by} | {row.body} |\n"
            ).encode("utf-8")
        )
        targets.append(f"--cold {row.id}")
    return recoverable, targets


def enforce_ceiling(project_root, model: Continuity, text: str) -> int:
    """Refuse a write that would cross the declared file ceiling.

    ``continuity-ceiling-exceeded`` names the supported mediated recovery.
    Where every prunable byte is already reclaimed there is no further mediated
    recovery, and the distinct ``continuity-capacity-structural`` says so with
    the residue arithmetic behind it.
    """
    size = len(text.encode("utf-8"))
    if size <= CONTINUITY_MAX_BYTES:
        return size
    recoverable, targets = prunable_bytes(project_root, model)
    if recoverable <= 0:
        raise ContinuityRefusal(
            "continuity-capacity-structural",
            f"the write is {size} bytes against the {CONTINUITY_MAX_BYTES}-byte "
            f"ceiling and no prunable byte remains: {len(model.live)} live rows and "
            f"{sum(1 for row in model.cold if not row.has_locator)} unarchived cold "
            "rows are non-prunable, and the newest ledger section is kept. There is "
            "no further mediated recovery; start a new project, or accept that no "
            "further plan can be recorded with preservation.",
        )
    raise ContinuityRefusal(
        "continuity-ceiling-exceeded",
        f"the write is {size} bytes against the {CONTINUITY_MAX_BYTES}-byte "
        f"ceiling; `cartopian prune-continuity` can still recover {recoverable} "
        "bytes from " + ", ".join(targets),
    )


def close_evidence_window(project_root, plan_id: str, closed: str) -> Dict[str, object]:
    """Close the reserved window under § 11.5's witness rule, after the write.

    ``close_plan_sequence`` deletes the log regardless of how many records it
    admitted, so a call naming the wrong window destroys a live window's
    evidence. Deferring costs nothing: the window it declines to close will be
    closed by its own closeout, and the deferral is reported rather than silent.
    """
    from cli import prompt_evidence

    if not os.path.lexists(prompt_evidence.log_path(project_root)):
        return prompt_evidence.close_plan_sequence(
            project_root, date=closed, plan_id=plan_id
        )
    if witness_holds(project_root, plan_id):
        return prompt_evidence.close_plan_sequence(
            project_root, date=closed, plan_id=plan_id
        )
    return {
        "plan": plan_id,
        "deferred": "foreign-window",
        "witness": window_witness(project_root),
    }
