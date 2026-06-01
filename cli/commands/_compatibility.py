"""Advisory-acknowledgment ledger schema + parser (FR-008 persistence, TASK-02-002).

The FR-008 advisory gate (:mod:`cli.commands._advisory_gate`) blocks a Tier-3 PM
harness from launching unless the operator has recorded an explicit
acknowledgment of the unconstrained risk for that ``(harness, project)`` pair.
This module owns the *persisted record*: a markdown-first ledger written to the
project-root file ``COMPATIBILITY.md`` (SPEC-02-002 OQ-A) through the FR-003
mediated writer, with one fixed-schema entry per ``(harness, project_id)``.

It is the source of truth the launch gate reads and that the Phase 04 FR-009
compatibility matrix later consolidates. The fields are exactly the SPEC-02-002
Interface table:

    harness, project_id, tier, missing_assets, acknowledged_by,
    acknowledged_on, rationale, revoked

A record whose ``harness``/``project_id`` does not match the current launch, or
that is ``revoked``, is not a valid acknowledgment — the gate then re-blocks
fail-closed.

Import-cycle-free and stdlib-only (NF-001): this module knows only how to
parse/render/match the ledger. Harness canonicalization is done by callers (the
acknowledgment command stores the canonical harness key; the gate matches on the
canonical key from detection), so entries are compared by exact string equality.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import List, Optional, Union

# The dedicated, mutable, revocable project-root ledger (SPEC-02-002 OQ-A).
LEDGER_FILENAME = "COMPATIBILITY.md"

# The mediated-writer dest_kind that addresses LEDGER_FILENAME (see
# cli.mediated_write.ROOT_FILES — the single allowlist extension this task adds).
LEDGER_DEST_KIND = "compatibility"

# Marker line so the ledger is self-identifying and future readers/writers can
# detect the schema version.
LEDGER_VERSION_MARKER = "<!-- cartopian-compatibility-ledger: v1 -->"

# The ordered, fixed schema (SPEC-02-002 Interface table).
FIELD_ORDER = (
    "harness",
    "project_id",
    "tier",
    "missing_assets",
    "acknowledged_by",
    "acknowledged_on",
    "rationale",
    "revoked",
)

_ENTRY_HEADER_RE = re.compile(r"^##\s+ack:", re.IGNORECASE)
_FIELD_RE = re.compile(r"^-\s*([A-Za-z_]+):\s*(.*)$")


@dataclass(frozen=True)
class AckRecord:
    """One operator acknowledgment of a Tier-3 (harness, project) pair."""

    harness: str
    project_id: str
    tier: str = "tier-3"
    missing_assets: str = ""
    acknowledged_by: str = ""
    acknowledged_on: str = ""
    rationale: str = ""
    revoked: bool = False

    def matches(self, harness: str, project_id: str) -> bool:
        return self.harness == harness and self.project_id == project_id


def _sanitize(value: str) -> str:
    """Collapse a field value to a single safe ledger line.

    Newlines/tabs would break the one-line-per-field grammar, so they are
    folded to spaces; surrounding whitespace is stripped.
    """
    return re.sub(r"\s+", " ", str(value).replace("\x00", "")).strip()


def make_record(
    *,
    harness: str,
    project_id: str,
    tier: str = "tier-3",
    missing_assets: str = "",
    acknowledged_by: str = "",
    acknowledged_on: str = "",
    rationale: str = "",
    revoked: bool = False,
) -> AckRecord:
    """Build a schema-clean record with every field sanitized to one line."""
    return AckRecord(
        harness=_sanitize(harness),
        project_id=_sanitize(project_id),
        tier=_sanitize(tier),
        missing_assets=_sanitize(missing_assets),
        acknowledged_by=_sanitize(acknowledged_by),
        acknowledged_on=_sanitize(acknowledged_on),
        rationale=_sanitize(rationale),
        revoked=bool(revoked),
    )


def parse_ledger(text: str) -> List[AckRecord]:
    """Parse a ``COMPATIBILITY.md`` body into the list of acknowledgment records.

    Tolerant line scanner: each entry begins at a ``## ack:`` heading and is a
    block of ``- key: value`` lines. Unknown keys are ignored; a block missing
    ``harness`` or ``project_id`` is skipped (it is not a valid record). Never
    raises on malformed input — the gate treats unreadable/empty as "no record".
    """
    records: List[AckRecord] = []
    fields: Optional[dict] = None

    def _flush() -> None:
        if not fields:
            return
        harness = fields.get("harness", "")
        project_id = fields.get("project_id", "")
        if not harness or not project_id:
            return
        revoked_raw = fields.get("revoked", "false").strip().lower()
        records.append(
            AckRecord(
                harness=harness,
                project_id=project_id,
                tier=fields.get("tier", ""),
                missing_assets=fields.get("missing_assets", ""),
                acknowledged_by=fields.get("acknowledged_by", ""),
                acknowledged_on=fields.get("acknowledged_on", ""),
                rationale=fields.get("rationale", ""),
                revoked=revoked_raw in ("true", "1", "yes", "on"),
            )
        )

    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if _ENTRY_HEADER_RE.match(line.strip()):
            _flush()
            fields = {}
            continue
        if fields is None:
            continue
        m = _FIELD_RE.match(line.strip())
        if m:
            key = m.group(1).strip().lower()
            if key in FIELD_ORDER:
                fields[key] = m.group(2).strip()
    _flush()
    return records


def render_ledger(records: List[AckRecord]) -> str:
    """Render the records back to the markdown ledger body (deterministic)."""
    lines: List[str] = [
        "# COMPATIBILITY.md",
        "",
        LEDGER_VERSION_MARKER,
        "",
        "FR-008 / SPEC-02-002 operator acknowledgments that a PM harness runs",
        "unconstrained at Tier-3 for a project. One entry per (harness,",
        "project_id). Written only by the operator-only acknowledgment command",
        "through the FR-003 mediated writer. A `revoked: true` entry, or no",
        "entry, re-blocks PM launch fail-closed. Do not hand-edit during a live",
        "PM session.",
        "",
    ]
    for rec in records:
        lines.append(f"## ack: {rec.harness} @ {rec.project_id}")
        lines.append("")
        lines.append(f"- harness: {rec.harness}")
        lines.append(f"- project_id: {rec.project_id}")
        lines.append(f"- tier: {rec.tier}")
        lines.append(f"- missing_assets: {rec.missing_assets}")
        lines.append(f"- acknowledged_by: {rec.acknowledged_by}")
        lines.append(f"- acknowledged_on: {rec.acknowledged_on}")
        lines.append(f"- rationale: {rec.rationale}")
        lines.append(f"- revoked: {'true' if rec.revoked else 'false'}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def find_record(
    records: List[AckRecord], harness: str, project_id: str
) -> Optional[AckRecord]:
    """Return the entry for ``(harness, project_id)`` regardless of revoked state."""
    for rec in records:
        if rec.matches(harness, project_id):
            return rec
    return None


def find_valid_record(
    records: List[AckRecord], harness: str, project_id: str
) -> Optional[AckRecord]:
    """Return a *valid* (matching, non-revoked) acknowledgment, else ``None``.

    This is the single predicate the launch gate consults: a missing,
    mismatched, or revoked record all yield ``None`` → re-block fail-closed.
    """
    rec = find_record(records, harness, project_id)
    if rec is None or rec.revoked:
        return None
    return rec


def upsert_record(records: List[AckRecord], record: AckRecord) -> List[AckRecord]:
    """Return a new list with ``record`` replacing any same-pair entry (else appended)."""
    out: List[AckRecord] = []
    replaced = False
    for rec in records:
        if rec.matches(record.harness, record.project_id):
            out.append(record)
            replaced = True
        else:
            out.append(rec)
    if not replaced:
        out.append(record)
    return out


def revoke_record(
    records: List[AckRecord], harness: str, project_id: str
) -> Optional[List[AckRecord]]:
    """Return a new list with the matching entry marked ``revoked=True``.

    Returns ``None`` when there is no entry to revoke (caller fails closed with a
    "nothing to revoke" guard rather than writing a phantom record).
    """
    out: List[AckRecord] = []
    found = False
    for rec in records:
        if rec.matches(harness, project_id):
            out.append(replace(rec, revoked=True))
            found = True
        else:
            out.append(rec)
    return out if found else None


def read_ledger_text(project_root: Union[str, Path]) -> str:
    """Read ``COMPATIBILITY.md`` at the project root; "" if absent/unreadable.

    Reading is unprivileged context (the operator command runs uncontained, and
    the gate only needs to *read* the record). The *write* path is the guarded
    one — it goes exclusively through the FR-003 mediated writer.
    """
    path = Path(project_root) / LEDGER_FILENAME
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def load_records(project_root: Union[str, Path]) -> List[AckRecord]:
    """Convenience: read + parse the project-root ledger into records."""
    return parse_ledger(read_ledger_text(project_root))
