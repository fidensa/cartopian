"""Operator-intent attestations: artifact model, resolver, and review context.

Planning and task-closure reviewers historically saw exactly one evidence
channel: project-management-authored guidance (the task, the spec, the prompt).
When that guidance drifts away from what the operator actually approved, every
management artifact can agree with itself and the review still approves the
drifted outcome.  This module is the second, independent channel.

The independence is structural, not editorial:

- An **attestation** (``intent/ATTEST-NNN-slug.md``) binds one eligible
  in-project source to its exact SHA-256 content identity, the operator's
  confirmation, a closed applicability scope set, requiredness, and optional
  complete named-section selectors.
- No ``dest_kind`` in :mod:`cli.mediated_write` maps to ``intent/``, so the
  mediated writer — the sole write path every structured PM command uses —
  cannot author, revise, or weaken an attestation.  ``cartopian attest-intent``
  is the only writer, and it refuses to run inside a dispatched handoff or an
  MCP tool invocation (see :mod:`cli.commands.attest_intent`).
- Eligibility is established *only* by a current attestation.  PM authorship
  and ``Status: locked`` alone are insufficient, so the PM cannot promote its
  own artifact into operator intent.

Resolution is one implementation consumed by every downstream surface (task
readiness, prompt generation, dispatch preflight, review parsing, lifecycle
guards, plan audit, CLI/MCP projection).  It scans **all** current attestations
for scope matches — declared references are supplemental and additive and can
never suppress that scan — resolves supersession to a uniquely attested current
successor, de-duplicates deterministically while preserving provenance, and
emits one normalized record.

Bounds are refusals, never truncation: a whole source is included at up to
8 KiB, one source contributes at most 8 KiB of complete selected sections, and
the whole review is capped at 24 KiB.  Overflow refuses and tells the operator
to narrow the attestation.

Standard library only; deterministic and offline.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------

#: Project-relative home of operator-intent attestations.  Deliberately absent
#: from :data:`cli.mediated_write.DEST_KINDS`.
INTENT_DIRNAME = "intent"

#: Project-relative home of future mediated operator-intent records.
INTENT_RECORDS_DIRNAME = "records"

ATTESTATION_ID_RE = re.compile(r"^ATTEST-\d{3}$")
ATTESTATION_FILE_RE = re.compile(r"^(ATTEST-\d{3})(?:-[a-z0-9][a-z0-9-]*)?$")
DEC_ID_RE = re.compile(r"^DEC-\d{3}$")
DEC_FILE_RE = re.compile(r"^(DEC-\d{3})(?:-[a-z0-9][a-z0-9-]*)?$")
OIR_FILE_RE = re.compile(r"^(OIR-\d{3})(?:-[a-z0-9][a-z0-9-]*)?$")
OIR_ID_RE = re.compile(r"^OIR-\d{3}$")
TASK_ID_RE = re.compile(r"^TASK-\d{2}-\d{3}$")
PHASE_ID_RE = re.compile(r"^PHASE-\d{2}-[a-z0-9][a-z0-9-]*$")
PLAN_REF_RE = re.compile(r"^P\d{2}-[A-Z][A-Z0-9]*-\d{3}$")
CHECKPOINT_ID_RE = re.compile(r"^PLAN-\d{3}(?:-[a-z0-9][a-z0-9-]*)?$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

#: Closed set of eligible source kinds.  Order is the deterministic
#: source-kind ordering key (see :func:`_ordering_key`).
SOURCE_KINDS: Tuple[str, ...] = (
    "requirements-intent",
    "decision",
    "operator-intent-record",
)

#: Closed union of applicability scope kinds, most specific first.  The
#: position in this tuple is the specificity ordering key.
SCOPE_KINDS: Tuple[str, ...] = (
    "task",
    "plan-ref",
    "phase",
    "review-kind",
    "project",
)

#: Closed set of review kinds an attestation scope or a review target may name.
REVIEW_KINDS: Tuple[str, ...] = ("planning", "task-closure")

ATTESTATION_STATUSES: Tuple[str, ...] = ("current", "superseded")

#: The one operator-confirmation marker an attestation may carry.
CONFIRMED_BY = "operator"

#: The section of ``REQUIREMENTS.md`` that carries operator-confirmed intent.
REQUIREMENTS_INTENT_SECTION = "Confirmed intent"
REQUIREMENTS_INTENT_ID = "REQUIREMENTS.md#Confirmed-intent"

# ---------------------------------------------------------------------------
# Bounds (NFRs).  Refusal, never truncation.
# ---------------------------------------------------------------------------

#: A whole eligible source is included when it is at most this many bytes.
WHOLE_SOURCE_MAX_BYTES = 8 * 1024

#: One source contributes at most this many bytes of selected content.
PER_SOURCE_MAX_BYTES = 8 * 1024

#: Total operator-intent excerpt content per review.
TOTAL_MAX_BYTES = 24 * 1024

#: Generated prompt section heading.  Bound to the review-context identity.
INTENT_SECTION_HEADING = "## Operator intent"

#: Emitted when a complete applicability scan and supplemental-reference
#: resolution find nothing.
NONE_RECORDED = "none recorded"

#: Closed alignment vocabulary recorded by review artifacts.
ALIGNMENT_VALUES: Tuple[str, ...] = ("aligned", "drifted", "not assessable")

#: The one non-blocking ``not assessable`` reason.
ALIGNMENT_NONE_RECORDED_REASON = NONE_RECORDED

#: Task / planning-checkpoint field carrying supplemental closed references.
INTENT_REFS_FIELD = "Intent refs"

#: Review-artifact field carrying the alignment result.
ALIGNMENT_FIELD = "Operator-intent alignment"
ALIGNMENT_EVIDENCE_FIELD = "Operator-intent evidence"

#: The project schema version at which the alignment contract becomes
#: enforceable.  Projects below it keep their historical reviews readable and
#: unrewritten; the compatibility window closes at v0.9.0 (see
#: ``protocol/CHANGELOG.md`` § v0.8.0).
ALIGNMENT_ENFORCED_FROM = (0, 8, 0)
ALIGNMENT_COMPATIBILITY_WINDOW_ENDS = "v0.9.0"


class IntentRefusal(Exception):
    """A fail-closed operator-intent refusal.

    ``rule`` names the violated invariant (a stable, testable identifier);
    ``detail`` explains it; ``recovery`` names the operator-actionable repair.
    Never carries source content.
    """

    def __init__(self, rule: str, detail: str, recovery: str = "") -> None:
        self.rule = rule
        self.detail = detail
        self.recovery = recovery
        super().__init__(f"{rule}: {detail}")

    def as_record(self) -> Dict[str, str]:
        return {
            "rule": self.rule,
            "detail": self.detail,
            "recovery": self.recovery,
        }


# ---------------------------------------------------------------------------
# Small shared primitives
# ---------------------------------------------------------------------------


def content_identity(data: bytes) -> str:
    """Lowercase-hex SHA-256 over exact bytes, prefixed ``sha256:``."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def find_project_root(anchor: Path) -> Optional[Path]:
    """Walk up from ``anchor`` to the enclosing Cartopian project root."""
    candidates = [anchor] if anchor.is_dir() else [anchor.parent]
    candidates += list(anchor.parents)
    for candidate in candidates:
        if (candidate / "cartopian.toml").is_file():
            return candidate
    return None


def _parse_header_block(text: str) -> Dict[str, str]:
    """Parse the ``Field: value`` block above the first ``## `` section.

    Mirrors the boundary every other Cartopian artifact reader uses.  The
    first occurrence of a field wins; later duplicates are ignored so a body
    line can never shadow an authored header.
    """
    headers: Dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("## "):
            break
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = stripped.partition(":")
        if not sep:
            continue
        key = key.strip()
        if not key or key not in _ALLOWED_HEADER_KEYS:
            continue
        headers.setdefault(key, value.strip())
    return headers


_ALLOWED_HEADER_KEYS = frozenset(
    {
        "Attestation ID",
        "Status",
        "Confirmed by",
        "Confirmed at",
        "Source kind",
        "Source path",
        "Source hash",
        "Required",
        "Scopes",
        "Sections",
        "Supersedes",
        # Fields read from *source* artifacts, not attestations.
        "Date",
        "Verdict",
        INTENT_REFS_FIELD,
        ALIGNMENT_FIELD,
        ALIGNMENT_EVIDENCE_FIELD,
        "Phase",
        "Plan ref",
        "Target",
    }
)


def read_header_field(text: str, field_name: str) -> Optional[str]:
    """Return one header-block field value from an arbitrary artifact."""
    return _parse_header_block(text).get(field_name)


def _split_csv(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _split_semicolons(value: str) -> List[str]:
    return [item.strip() for item in value.split(";") if item.strip()]


def _within(child: str, parent: str) -> bool:
    child_n = os.path.normcase(os.path.normpath(child))
    parent_n = os.path.normcase(os.path.normpath(parent))
    if child_n == parent_n:
        return True
    if not parent_n.endswith(os.sep):
        parent_n += os.sep
    return child_n.startswith(parent_n)


def read_contained_bytes(project_root: Path, relpath: str, *, what: str) -> bytes:
    """Read one project-contained regular, single-link, non-symlinked file.

    Refuses traversal, absolute paths, symlink or hardlink escape, and any
    non-regular node.  Nothing outside the project directory can enter review
    context by any path.
    """
    if not relpath or os.path.isabs(relpath) or "\x00" in relpath:
        raise IntentRefusal(
            "outside-project-source",
            f"{what} path must be project-relative: {relpath!r}",
            "re-attest naming a path inside the project directory",
        )
    normalized = relpath.replace("\\", "/")
    if any(part in ("..", "") for part in normalized.split("/")[:-1] if part != "."):
        raise IntentRefusal(
            "outside-project-source",
            f"{what} path traverses outside the project: {relpath!r}",
            "re-attest naming a path inside the project directory",
        )
    real_root = os.path.realpath(os.fspath(project_root))
    candidate = os.path.join(real_root, *[p for p in normalized.split("/") if p != "."])
    if os.path.islink(candidate):
        raise IntentRefusal(
            "outside-project-source",
            f"{what} path is a symlink: {relpath}",
            "replace the symlink with the real in-project artifact",
        )
    parent = os.path.realpath(os.path.dirname(candidate))
    if not _within(parent, real_root):
        raise IntentRefusal(
            "outside-project-source",
            f"{what} path escapes the project directory: {relpath}",
            "re-attest naming a path inside the project directory",
        )
    try:
        leaf = os.lstat(candidate)
    except OSError:
        raise IntentRefusal(
            "missing-source",
            f"{what} does not exist: {relpath}",
            "restore the source artifact or re-attest the current one",
        )
    if not stat.S_ISREG(leaf.st_mode):
        raise IntentRefusal(
            "outside-project-source",
            f"{what} is not a regular file: {relpath}",
            "name a regular in-project file",
        )
    if leaf.st_nlink > 1:
        raise IntentRefusal(
            "outside-project-source",
            f"{what} is a hardlink (st_nlink={leaf.st_nlink}): {relpath}",
            "replace the hardlink with the real in-project artifact",
        )
    try:
        with open(candidate, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise IntentRefusal(
            "missing-source",
            f"{what} is unreadable: {relpath} — {exc.strerror or exc}",
            "restore read access to the source artifact",
        )


def read_contained_text(project_root: Path, path: Path, *, what: str) -> str:
    """Read one UTF-8 path only through the project-contained byte guard."""
    root = os.path.realpath(os.fspath(project_root))
    candidate = os.path.abspath(os.fspath(path))
    real_candidate = os.path.realpath(candidate)
    if not _within(real_candidate, root):
        raise IntentRefusal(
            "outside-project-source",
            f"{what} path escapes the project directory: {path}",
            f"name the canonical {what} inside the project directory",
        )
    relpath = os.path.relpath(real_candidate, root).replace(os.sep, "/")
    try:
        return read_contained_bytes(project_root, relpath, what=what).decode("utf-8")
    except UnicodeDecodeError:
        raise IntentRefusal(
            "malformed-artifact",
            f"{what} is not valid UTF-8: {relpath}",
            f"restore a UTF-8 {what}",
        )


# ---------------------------------------------------------------------------
# Complete named-section selection
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(r"^(#{2,6})\s+(.*?)\s*$")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")


def heading_positions(lines: Sequence[str]) -> List[Tuple[int, int, str]]:
    """Return ``(line index, heading level, heading text)`` for real headings.

    Fence-aware: a ``##`` line inside a fenced code block is content, not a
    heading. Both the source-section splitter and the prompt-section extractor
    depend on this — an attested decision that quotes markdown, and the
    generated prompt section that fences an entire source, would otherwise be
    cut in half at the first quoted heading.
    """
    found: List[Tuple[int, int, str]] = []
    fence: Optional[str] = None
    for index, raw in enumerate(lines):
        line = raw.rstrip("\r\n")
        opener = _FENCE_RE.match(line)
        if fence is None:
            if opener:
                fence = opener.group(1)
            else:
                match = _SECTION_RE.match(line)
                if match:
                    found.append((index, len(match.group(1)), match.group(2)))
            continue
        if (
            opener
            and opener.group(1)[0] == fence[0]
            and len(opener.group(1)) >= len(fence)
            and line[len(opener.group(1)):].strip() == ""
        ):
            fence = None
    return found


def named_sections(text: str) -> List[Tuple[str, str]]:
    """Return ``(heading-text, complete-section-text)`` for every ``##``+ heading.

    A section runs from its heading line through the line before the next
    heading at the same or a shallower level, so a selected section always
    carries its complete body and every nested subsection.
    """
    lines = text.splitlines(keepends=True)
    starts = heading_positions(lines)
    sections: List[Tuple[str, str]] = []
    for position, (index, level, heading) in enumerate(starts):
        end = len(lines)
        for next_index, next_level, _ in starts[position + 1:]:
            if next_level <= level:
                end = next_index
                break
        sections.append((heading, "".join(lines[index:end])))
    return sections


def select_sections(
    text: str, wanted: Sequence[str], *, source_label: str
) -> List[Tuple[str, str]]:
    """Return the complete text of each wanted section, in the wanted order.

    Refuses a missing heading, a duplicate heading (ambiguous selector), and
    any selector that is not an exact complete heading.  Never truncates.
    """
    available = named_sections(text)
    counts: Dict[str, int] = {}
    for heading, _ in available:
        counts[heading] = counts.get(heading, 0) + 1
    by_heading = {heading: body for heading, body in available}
    selected: List[Tuple[str, str]] = []
    for name in wanted:
        if counts.get(name, 0) == 0:
            raise IntentRefusal(
                "missing-section",
                f"{source_label} has no section titled {name!r}",
                "re-attest with an exact complete heading that exists in the source",
            )
        if counts[name] > 1:
            raise IntentRefusal(
                "duplicate-section",
                f"{source_label} has {counts[name]} sections titled {name!r}; "
                "the selector is ambiguous",
                "give the section a unique heading, then re-attest",
            )
        selected.append((name, by_heading[name]))
    return selected


# ---------------------------------------------------------------------------
# Attestation artifact
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Scope:
    kind: str
    value: Optional[str]

    def render(self) -> str:
        return self.kind if self.value is None else f"{self.kind}:{self.value}"

    @property
    def specificity(self) -> int:
        return SCOPE_KINDS.index(self.kind)


@dataclass(frozen=True)
class Attestation:
    attestation_id: str
    status: str
    confirmed_by: str
    confirmed_at: str
    source_kind: str
    source_relpath: str
    source_hash: str
    required: bool
    scopes: Tuple[Scope, ...]
    sections: Tuple[str, ...]
    supersedes: Optional[str]
    relpath: str
    attestation_hash: str = ""

    @property
    def source_identity(self) -> str:
        if self.source_kind == "requirements-intent":
            return REQUIREMENTS_INTENT_ID
        match = DEC_FILE_RE.match(Path(self.source_relpath).stem)
        if self.source_kind == "decision" and match:
            return match.group(1)
        match = OIR_FILE_RE.match(Path(self.source_relpath).stem)
        if self.source_kind == "operator-intent-record" and match:
            return match.group(1)
        return self.source_relpath

    def as_record(self) -> Dict[str, Any]:
        return {
            "attestation_id": self.attestation_id,
            "attestation_path": self.relpath,
            "attestation_hash": self.attestation_hash,
            "status": self.status,
            "confirmed_by": self.confirmed_by,
            "confirmed_at": self.confirmed_at,
            "source_kind": self.source_kind,
            "source_identity": self.source_identity,
            "source_path": self.source_relpath,
            "source_hash": self.source_hash,
            "required": self.required,
            "scopes": [scope.render() for scope in self.scopes],
            "sections": list(self.sections),
            "supersedes": self.supersedes,
        }


def parse_scope(raw: str) -> Scope:
    """Parse one ``kind`` / ``kind:value`` scope token from the closed union."""
    kind, sep, value = raw.partition(":")
    kind = kind.strip()
    value = value.strip()
    if kind not in SCOPE_KINDS:
        raise IntentRefusal(
            "malformed-scope",
            f"unknown applicability scope kind {kind!r}; the closed union is "
            + ", ".join(SCOPE_KINDS),
            "re-attest using a scope kind from the closed union",
        )
    if kind == "project":
        if sep:
            raise IntentRefusal(
                "malformed-scope",
                "the project scope carries no value",
                "declare the scope as bare `project`",
            )
        return Scope("project", None)
    if not sep or not value:
        raise IntentRefusal(
            "malformed-scope",
            f"scope {kind!r} requires a canonical value (kind:value)",
            "re-attest with a canonical value for this scope kind",
        )
    validators = {
        "task": (TASK_ID_RE, "TASK-NN-NNN"),
        "phase": (PHASE_ID_RE, "PHASE-NN-slug"),
        "plan-ref": (PLAN_REF_RE, "PNN-KIND-NNN"),
    }
    if kind == "review-kind":
        if value not in REVIEW_KINDS:
            raise IntentRefusal(
                "malformed-scope",
                f"review-kind scope value must be one of {', '.join(REVIEW_KINDS)}; "
                f"got {value!r}",
                "re-attest with a review kind from the closed union",
            )
        return Scope(kind, value)
    pattern, grammar = validators[kind]
    if not pattern.match(value):
        raise IntentRefusal(
            "malformed-scope",
            f"scope {kind!r} value {value!r} does not match {grammar}",
            "re-attest with a canonical value for this scope kind",
        )
    return Scope(kind, value)


def _parse_required(raw: Optional[str], attestation_id: str) -> bool:
    if raw is None or raw == "":
        raise IntentRefusal(
            "missing-requiredness",
            f"{attestation_id} declares no `Required:` value; requiredness is "
            "mandatory and settable only by the operator confirmation surface",
            "re-run `cartopian attest-intent` with --required true|false",
        )
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise IntentRefusal(
        "missing-requiredness",
        f"{attestation_id} declares Required: {raw!r}; the closed domain is "
        "true | false",
        "re-run `cartopian attest-intent` with --required true|false",
    )


def parse_attestation(
    text: str, relpath: str, *, raw_bytes: Optional[bytes] = None
) -> Attestation:
    """Parse one attestation artifact, refusing every malformed field."""
    headers = _parse_header_block(text)
    attestation_id = headers.get("Attestation ID", "")
    if not ATTESTATION_ID_RE.match(attestation_id):
        raise IntentRefusal(
            "malformed-attestation",
            f"{relpath} declares no valid `Attestation ID:` (ATTEST-NNN)",
            "re-create the attestation through `cartopian attest-intent`",
        )
    stem = Path(relpath).stem
    file_match = ATTESTATION_FILE_RE.match(stem)
    if not file_match or file_match.group(1) != attestation_id:
        raise IntentRefusal(
            "malformed-attestation",
            f"{relpath} filename does not match its Attestation ID "
            f"{attestation_id}",
            "re-create the attestation through `cartopian attest-intent`",
        )
    status = headers.get("Status", "")
    if status not in ATTESTATION_STATUSES:
        raise IntentRefusal(
            "malformed-attestation",
            f"{attestation_id} declares Status: {status!r}; the closed domain is "
            + " | ".join(ATTESTATION_STATUSES),
            "re-create the attestation through `cartopian attest-intent`",
        )
    confirmed_by = headers.get("Confirmed by", "")
    if confirmed_by != CONFIRMED_BY:
        raise IntentRefusal(
            "unattested-source",
            f"{attestation_id} is not operator-confirmed "
            f"(Confirmed by: {confirmed_by!r})",
            "only `cartopian attest-intent`, run by the operator, creates an "
            "attestation",
        )
    confirmed_at = headers.get("Confirmed at", "")
    if not DATE_RE.match(confirmed_at):
        raise IntentRefusal(
            "malformed-attestation",
            f"{attestation_id} declares no valid `Confirmed at:` (YYYY-MM-DD)",
            "re-create the attestation through `cartopian attest-intent`",
        )
    source_kind = headers.get("Source kind", "")
    if source_kind not in SOURCE_KINDS:
        raise IntentRefusal(
            "malformed-attestation",
            f"{attestation_id} declares Source kind: {source_kind!r}; the closed "
            "set is " + ", ".join(SOURCE_KINDS),
            "re-create the attestation naming an eligible source kind",
        )
    source_relpath = headers.get("Source path", "")
    if not source_relpath:
        raise IntentRefusal(
            "malformed-attestation",
            f"{attestation_id} declares no `Source path:`",
            "re-create the attestation naming its in-project source",
        )
    source_hash = headers.get("Source hash", "")
    if not SHA256_RE.match(source_hash):
        raise IntentRefusal(
            "malformed-attestation",
            f"{attestation_id} declares no valid `Source hash:` "
            "(sha256:<64 lowercase hex>)",
            "re-create the attestation through `cartopian attest-intent`",
        )
    required = _parse_required(headers.get("Required"), attestation_id)
    raw_scopes = headers.get("Scopes", "")
    if not raw_scopes.strip():
        raise IntentRefusal(
            "malformed-scope",
            f"{attestation_id} declares no `Scopes:`; applicability is a closed "
            "union and may not be empty",
            "re-attest declaring at least one applicability scope",
        )
    scopes = tuple(parse_scope(token) for token in _split_csv(raw_scopes))
    if not scopes:
        raise IntentRefusal(
            "malformed-scope",
            f"{attestation_id} declares no parseable applicability scope",
            "re-attest declaring at least one applicability scope",
        )
    raw_sections = headers.get("Sections", "").strip()
    if raw_sections in ("", "whole-source", "n/a", "none"):
        sections: Tuple[str, ...] = ()
    else:
        sections = tuple(_split_semicolons(raw_sections))
        if len(set(sections)) != len(sections):
            raise IntentRefusal(
                "unsupported-selector",
                f"{attestation_id} repeats a section selector",
                "re-attest with each complete section named once",
            )
    supersedes = headers.get("Supersedes", "none").strip()
    if supersedes in ("", "none", "n/a"):
        supersedes_value: Optional[str] = None
    elif ATTESTATION_ID_RE.match(supersedes):
        supersedes_value = supersedes
    else:
        raise IntentRefusal(
            "malformed-attestation",
            f"{attestation_id} declares Supersedes: {supersedes!r}; expected "
            "ATTEST-NNN or none",
            "re-create the attestation through `cartopian attest-intent`",
        )
    return Attestation(
        attestation_id=attestation_id,
        status=status,
        confirmed_by=confirmed_by,
        confirmed_at=confirmed_at,
        source_kind=source_kind,
        source_relpath=source_relpath.replace("\\", "/"),
        source_hash=source_hash,
        required=required,
        scopes=scopes,
        sections=sections,
        supersedes=supersedes_value,
        relpath=relpath.replace("\\", "/"),
        attestation_hash=content_identity(
            raw_bytes if raw_bytes is not None else text.encode("utf-8")
        ),
    )


def render_attestation(attestation: Attestation, title: str) -> str:
    """Render the canonical attestation artifact body.

    The confirmation surface renders this itself so no field can be forged by
    body text and so re-confirmation is byte-deterministic.
    """
    sections = (
        "; ".join(attestation.sections) if attestation.sections else "whole-source"
    )
    return (
        f"# {attestation.attestation_id}: {title}\n"
        "\n"
        f"Attestation ID: {attestation.attestation_id}\n"
        f"Status: {attestation.status}\n"
        f"Confirmed by: {attestation.confirmed_by}\n"
        f"Confirmed at: {attestation.confirmed_at}\n"
        f"Source kind: {attestation.source_kind}\n"
        f"Source path: {attestation.source_relpath}\n"
        f"Source hash: {attestation.source_hash}\n"
        f"Required: {'true' if attestation.required else 'false'}\n"
        f"Scopes: {', '.join(scope.render() for scope in attestation.scopes)}\n"
        f"Sections: {sections}\n"
        f"Supersedes: {attestation.supersedes or 'none'}\n"
        "\n"
        "## Operator confirmation\n"
        "\n"
        "This attestation was created by the operator through "
        "`cartopian attest-intent`. It binds the source above to its exact "
        "content identity, its closed applicability scope set, and its "
        "requiredness. No project-management, coder, or reviewer capability "
        "can create, change, or weaken it.\n"
    )


# ---------------------------------------------------------------------------
# Attestation store
# ---------------------------------------------------------------------------


def attestation_dir(project_root: Path) -> Path:
    return Path(project_root) / INTENT_DIRNAME


def load_attestations(project_root: Path) -> Tuple[List[Attestation], List[Dict[str, str]]]:
    """Load every attestation artifact under ``intent/``.

    Returns ``(attestations, invalid)``.  A malformed artifact never silently
    disappears: it is reported so callers can fail closed rather than treat a
    broken attestation as absent evidence.
    """
    base = attestation_dir(project_root)
    attestations: List[Attestation] = []
    invalid: List[Dict[str, str]] = []
    if not base.is_dir():
        return attestations, invalid
    for path in sorted(base.glob("*.md")):
        if not path.is_file() or path.is_symlink():
            invalid.append(
                {
                    "path": f"{INTENT_DIRNAME}/{path.name}",
                    "rule": "malformed-attestation",
                    "detail": "attestation is not a regular file",
                }
            )
            continue
        relpath = f"{INTENT_DIRNAME}/{path.name}"
        try:
            raw = read_contained_bytes(project_root, relpath, what="attestation")
            text = raw.decode("utf-8")
        except (IntentRefusal, UnicodeDecodeError) as exc:
            rule = exc.rule if isinstance(exc, IntentRefusal) else "malformed-attestation"
            detail = (
                exc.detail
                if isinstance(exc, IntentRefusal)
                else "attestation is not valid UTF-8"
            )
            invalid.append({"path": relpath, "rule": rule, "detail": detail})
            continue
        try:
            attestations.append(parse_attestation(text, relpath, raw_bytes=raw))
        except IntentRefusal as exc:
            invalid.append({"path": relpath, "rule": exc.rule, "detail": exc.detail})
    attestations.sort(key=lambda a: a.attestation_id)
    by_id: Dict[str, List[Attestation]] = {}
    for attestation in attestations:
        by_id.setdefault(attestation.attestation_id, []).append(attestation)
    for attestation_id, copies in sorted(by_id.items()):
        if len(copies) > 1:
            invalid.append(
                {
                    "path": ", ".join(item.relpath for item in copies),
                    "rule": "ambiguous-attestation",
                    "detail": (
                        f"{attestation_id} appears in {len(copies)} attestation "
                        "artifacts"
                    ),
                }
            )
    return attestations, invalid


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


def assert_source_eligible(
    project_root: Path, attestation: Attestation, source_text: str
) -> None:
    """Assert the attested source is an eligible in-project source kind.

    Eligibility is established only by a current operator attestation *of an
    eligible kind*; PM authorship or ``Status: locked`` alone is insufficient,
    and an open decision can never be current operator intent.
    """
    relpath = attestation.source_relpath
    parts = relpath.split("/")
    if attestation.source_kind == "requirements-intent":
        if relpath != "REQUIREMENTS.md":
            raise IntentRefusal(
                "ineligible-source",
                f"{attestation.attestation_id} attests source kind "
                "'requirements-intent' against "
                f"{relpath}; the eligible source is REQUIREMENTS.md",
                "re-attest the confirmed intent section of REQUIREMENTS.md",
            )
        if attestation.sections != (REQUIREMENTS_INTENT_SECTION,):
            raise IntentRefusal(
                "ineligible-source",
                f"{attestation.attestation_id} must select only the "
                f"'{REQUIREMENTS_INTENT_SECTION}' section of REQUIREMENTS.md; "
                "other requirements sections are management-authored guidance",
                "re-attest selecting exactly the confirmed intent section",
            )
        return
    if attestation.source_kind == "decision":
        if len(parts) != 2 or parts[0] != "decisions" or not DEC_FILE_RE.match(
            Path(relpath).stem
        ):
            raise IntentRefusal(
                "ineligible-source",
                f"{attestation.attestation_id} attests source kind 'decision' "
                f"against {relpath}; the eligible source is "
                "decisions/DEC-NNN-slug.md",
                "re-attest naming a decision artifact",
            )
        status = read_header_field(source_text, "Status")
        if status != "locked":
            raise IntentRefusal(
                "open-decision",
                f"{relpath} declares Status: {status!r}; an open decision cannot "
                "be attested as current operator intent",
                "lock the decision, then re-attest it",
            )
        if attestation.sections and "Decision" not in attestation.sections:
            raise IntentRefusal(
                "ineligible-source",
                f"{attestation.attestation_id} selects decision sections but omits "
                "the complete `Decision` section that records the choice",
                "re-attest the whole decision or include its complete Decision section",
            )
        return
    # operator-intent-record
    if (
        len(parts) != 3
        or parts[0] != INTENT_DIRNAME
        or parts[1] != INTENT_RECORDS_DIRNAME
        or not OIR_FILE_RE.match(Path(relpath).stem)
    ):
        raise IntentRefusal(
            "ineligible-source",
            f"{attestation.attestation_id} attests source kind "
            f"'operator-intent-record' against {relpath}; the eligible source is "
            f"{INTENT_DIRNAME}/{INTENT_RECORDS_DIRNAME}/OIR-NNN-slug.md",
            "re-attest naming a mediated operator-intent record",
        )


# ---------------------------------------------------------------------------
# Review target
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewTarget:
    review_kind: str
    task_id: Optional[str] = None
    phase_id: Optional[str] = None
    plan_ref: Optional[str] = None
    checkpoint_id: Optional[str] = None

    def as_record(self) -> Dict[str, Optional[str]]:
        return {
            "task_id": self.task_id,
            "phase_id": self.phase_id,
            "plan_ref": self.plan_ref,
            "checkpoint_id": self.checkpoint_id,
        }

    def matches(self, scope: Scope) -> bool:
        if scope.kind == "project":
            return True
        if scope.kind == "review-kind":
            return scope.value == self.review_kind
        if scope.kind == "task":
            return scope.value is not None and scope.value == self.task_id
        if scope.kind == "phase":
            return scope.value is not None and scope.value == self.phase_id
        if scope.kind == "plan-ref":
            return scope.value is not None and scope.value == self.plan_ref
        return False


def target_for_task(project_root: Path, task_path: Path) -> ReviewTarget:
    """Build the task-closure review target from a task file's own fields."""
    text = _contained_task_text(project_root, task_path)
    headers = _parse_header_block(text)
    stem_match = re.match(r"^(TASK-\d{2}-\d{3})", task_path.stem)
    task_id = stem_match.group(1) if stem_match else None
    phase = headers.get("Phase", "").strip() or None
    plan_ref = headers.get("Plan ref", "").strip() or None
    if phase is not None and not PHASE_ID_RE.match(phase):
        phase = None
    if plan_ref is not None and not PLAN_REF_RE.match(plan_ref):
        plan_ref = None
    return ReviewTarget(
        review_kind="task-closure",
        task_id=task_id,
        phase_id=phase,
        plan_ref=plan_ref,
    )


def _contained_task_text(project_root: Path, task_path: Path) -> str:
    real_root = os.path.realpath(os.fspath(project_root))
    real_task = os.path.realpath(os.fspath(task_path))
    if not _within(real_task, real_root):
        raise IntentRefusal(
            "outside-project-review-target",
            f"task review target escapes the project: {task_path}",
            "name the current task artifact inside the project",
        )
    relpath = os.path.relpath(real_task, real_root).replace(os.sep, "/")
    try:
        return read_contained_bytes(
            Path(real_root), relpath, what="task review target"
        ).decode("utf-8")
    except UnicodeDecodeError:
        raise IntentRefusal(
            "malformed-review-target",
            f"task review target is not valid UTF-8: {relpath}",
            "restore a UTF-8 task artifact",
        )


# ---------------------------------------------------------------------------
# Supplemental references
# ---------------------------------------------------------------------------


def parse_intent_refs(raw: Optional[str]) -> List[str]:
    """Parse the closed ``Intent refs:`` grammar into canonical tokens.

    Supplemental references are additive: they can add evidence the scan did
    not reach, but they can never suppress an automatically applicable
    attestation.  The grammar is closed — an attestation id or an eligible
    source identity.
    """
    if raw is None:
        return []
    value = raw.strip()
    if value in ("", "none", "n/a"):
        return []
    tokens = _split_csv(value)
    if not tokens:
        raise IntentRefusal(
            "malformed-reference",
            f"`{INTENT_REFS_FIELD}:` is present but names nothing; use `none`",
            f"write `{INTENT_REFS_FIELD}: none` when the artifact declares no "
            "supplemental reference",
        )
    for token in tokens:
        if not (
            ATTESTATION_ID_RE.match(token)
            or DEC_ID_RE.match(token)
            or OIR_ID_RE.match(token)
            or token == REQUIREMENTS_INTENT_ID
        ):
            raise IntentRefusal(
                "malformed-reference",
                f"`{INTENT_REFS_FIELD}:` token {token!r} is outside the closed "
                "grammar (ATTEST-NNN | DEC-NNN | OIR-NNN | "
                f"{REQUIREMENTS_INTENT_ID} | none)",
                "declare supplemental references using the closed grammar",
            )
    if len(set(tokens)) != len(tokens):
        raise IntentRefusal(
            "ambiguous-reference",
            f"`{INTENT_REFS_FIELD}:` repeats a reference",
            "name each supplemental reference once",
        )
    return tokens


# ---------------------------------------------------------------------------
# Supersession
# ---------------------------------------------------------------------------


def _decision_index(project_root: Path) -> Dict[str, str]:
    """Map ``DEC-NNN`` → project-relative path for every decision on disk."""
    index: Dict[str, str] = {}
    base = Path(project_root) / "decisions"
    if not base.is_dir():
        return index
    for path in sorted(base.glob("*.md")):
        match = DEC_FILE_RE.match(path.stem)
        if match:
            decision_id = match.group(1)
            relpath = f"decisions/{path.name}"
            if decision_id in index:
                raise IntentRefusal(
                    "ambiguous-reference",
                    f"{decision_id} has multiple decision artifacts "
                    f"({index[decision_id]}, {relpath})",
                    "retire the duplicate decision artifact so the identity is unique",
                )
            index[decision_id] = relpath
    return index


def _decision_supersedes(project_root: Path, relpath: str) -> Optional[str]:
    try:
        text = read_contained_bytes(project_root, relpath, what="decision").decode(
            "utf-8"
        )
    except (IntentRefusal, UnicodeDecodeError):
        return None
    value = (read_header_field(text, "Supersedes") or "").strip()
    return value if DEC_ID_RE.match(value) else None


def resolve_supersession(
    project_root: Path,
    dec_id: str,
    attested_by_source: Dict[str, List[Attestation]],
) -> Tuple[str, List[str]]:
    """Walk a decision's supersession chain forward to its current successor.

    Returns ``(current_dec_id, chain)`` where ``chain`` is the full bounded
    provenance chain from ``dec_id`` to the current successor.  A successor
    resolves only when it is unique *and* carries its own valid operator
    attestation.
    """
    index = _decision_index(project_root)
    if dec_id not in index:
        raise IntentRefusal(
            "broken-successor",
            f"{dec_id} names no decision artifact in this project",
            "restore the decision or re-attest the current one",
        )
    successors: Dict[str, List[str]] = {}
    for candidate_id, candidate_path in index.items():
        superseded = _decision_supersedes(project_root, candidate_path)
        if superseded:
            successors.setdefault(superseded, []).append(candidate_id)

    chain = [dec_id]
    seen = {dec_id}
    current = dec_id
    while True:
        nexts = sorted(successors.get(current, []))
        if not nexts:
            break
        if len(nexts) > 1:
            raise IntentRefusal(
                "ambiguous-successor",
                f"{current} is superseded by {len(nexts)} decisions "
                f"({', '.join(nexts)}); no unique current successor exists",
                "retire the duplicate successor so exactly one current decision "
                "remains",
            )
        successor = nexts[0]
        if successor in seen:
            raise IntentRefusal(
                "supersession-cycle",
                f"decision supersession cycles at {successor}",
                "break the supersession cycle in the decision artifacts",
            )
        seen.add(successor)
        chain.append(successor)
        current = successor
        if len(chain) > len(index) + 1:  # bounded: cannot exceed the artifact set
            raise IntentRefusal(
                "supersession-cycle",
                "decision supersession chain is unbounded",
                "break the supersession cycle in the decision artifacts",
            )
    if current != dec_id:
        successor_path = index[current]
        if not attested_by_source.get(successor_path):
            raise IntentRefusal(
                "unattested-successor",
                f"{dec_id} is superseded by {current}, which carries no current "
                "operator attestation of its own",
                f"attest {current} with `cartopian attest-intent`, or re-attest "
                "the intended current source",
            )
    return current, chain


def resolve_attestation_supersession(
    attestation_id: str,
    attestations: Sequence[Attestation],
) -> Tuple[Attestation, List[str]]:
    """Resolve a superseded attestation reference to one current successor.

    ``Attestation.supersedes`` is declared by the new attestation, so resolution
    walks that relation forward.  A reference to historical evidence is
    readiness-safe only when the chain is acyclic, unambiguous, and ends at a
    current attestation.
    """
    by_id = {item.attestation_id: item for item in attestations}
    if attestation_id not in by_id:
        raise IntentRefusal(
            "unresolved-reference",
            f"supplemental reference {attestation_id} names no attestation in "
            f"{INTENT_DIRNAME}/",
            "remove the reference or create the attestation with "
            "`cartopian attest-intent`",
        )
    successors: Dict[str, List[str]] = {}
    for item in attestations:
        if item.supersedes is not None:
            successors.setdefault(item.supersedes, []).append(item.attestation_id)

    chain = [attestation_id]
    seen = {attestation_id}
    current_id = attestation_id
    while by_id[current_id].status != "current":
        candidates = sorted(successors.get(current_id, ()))
        if not candidates:
            raise IntentRefusal(
                "superseded-reference",
                f"supplemental reference {attestation_id} is superseded and has "
                "no current successor attestation",
                "reference or create the current attestation instead",
            )
        if len(candidates) != 1:
            raise IntentRefusal(
                "ambiguous-successor",
                f"superseded attestation {current_id} has {len(candidates)} "
                f"successors ({', '.join(candidates)})",
                "leave exactly one successor in the attestation chain",
            )
        successor = candidates[0]
        if successor in seen:
            raise IntentRefusal(
                "supersession-cycle",
                f"attestation supersession cycles at {successor}",
                "break the attestation supersession cycle",
            )
        seen.add(successor)
        chain.append(successor)
        current_id = successor
        if len(chain) > len(attestations):
            raise IntentRefusal(
                "supersession-cycle",
                "attestation supersession chain is unbounded",
                "break the attestation supersession cycle",
            )
    return by_id[current_id], chain


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


@dataclass
class Evidence:
    attestation: Attestation
    matched_scopes: Tuple[Scope, ...]
    required: bool
    discovery: Tuple[str, ...]           # "applicability-scan" and/or "supplemental-reference"
    superseded_from: Optional[str]
    chain: Tuple[str, ...]
    selected: Tuple[Tuple[str, str], ...]  # (section-name-or-"whole source", text)
    full_source_bytes: int
    selected_bytes: int
    selected_identity: str
    duplicate_of: Tuple[str, ...] = ()

    @property
    def best_scope(self) -> Scope:
        return min(self.matched_scopes, key=lambda s: s.specificity)

    @property
    def best_specificity(self) -> int:
        if not self.matched_scopes:
            return len(SCOPE_KINDS)
        return self.best_scope.specificity

    def as_record(self) -> Dict[str, Any]:
        record = self.attestation.as_record()
        record.update(
            {
                "required": self.required,
                "matched_scopes": [scope.render() for scope in self.matched_scopes],
                "discovery": list(self.discovery),
                "current": self.attestation.status == "current",
                "resolved_from_superseded": self.superseded_from is not None,
                "superseded_from": self.superseded_from,
                "provenance_chain": list(self.chain),
                "selected_sections": [name for name, _ in self.selected],
                "selected_content": [
                    {"name": name, "content": content}
                    for name, content in self.selected
                ],
                "full_source_bytes": self.full_source_bytes,
                "selected_bytes": self.selected_bytes,
                "selected_identity": self.selected_identity,
                "duplicate_of": list(self.duplicate_of),
            }
        )
        return record


def _ordering_key(evidence: Evidence) -> Tuple[int, int, str]:
    """Stable ordering: applicability specificity, source kind, canonical id."""
    return (
        evidence.best_specificity,
        SOURCE_KINDS.index(evidence.attestation.source_kind),
        evidence.attestation.attestation_id,
    )


def _build_evidence(
    project_root: Path,
    attestation: Attestation,
    matched_scopes: Sequence[Scope],
    discovery: Sequence[str],
    superseded_from: Optional[str],
    chain: Sequence[str],
    required_override: Optional[bool] = None,
) -> Evidence:
    raw = read_contained_bytes(
        project_root, attestation.source_relpath, what="attested source"
    )
    actual = content_identity(raw)
    if actual != attestation.source_hash:
        raise IntentRefusal(
            "source-hash-mismatch",
            f"{attestation.source_relpath} no longer matches the content identity "
            f"{attestation.attestation_id} attested "
            f"({attestation.source_hash} → {actual})",
            "re-confirm the source with `cartopian attest-intent` after reviewing "
            "the change",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise IntentRefusal(
            "malformed-attestation",
            f"{attestation.source_relpath} is not valid UTF-8",
            "restore a UTF-8 source artifact",
        )
    assert_source_eligible(project_root, attestation, text)

    label = attestation.source_relpath
    if attestation.sections:
        selected = tuple(select_sections(text, attestation.sections, source_label=label))
    else:
        if len(raw) > WHOLE_SOURCE_MAX_BYTES:
            raise IntentRefusal(
                "oversize-source",
                f"{label} is {len(raw)} bytes, above the {WHOLE_SOURCE_MAX_BYTES}-byte "
                "whole-source bound, and the attestation selects no complete "
                "sections",
                "re-attest with operator-confirmed complete named-section "
                "selectors, or narrow the source",
            )
        selected = (("whole source", text),)
    selected_blob = "".join(body for _, body in selected).encode("utf-8")
    if len(selected_blob) > PER_SOURCE_MAX_BYTES:
        raise IntentRefusal(
            "oversize-selection",
            f"{label} contributes {len(selected_blob)} bytes, above the "
            f"{PER_SOURCE_MAX_BYTES}-byte per-source bound; content is never "
            "truncated to fit",
            "narrow or split the attestation so its selected sections fit intact",
        )
    return Evidence(
        attestation=attestation,
        matched_scopes=tuple(matched_scopes),
        required=(
            attestation.required
            if required_override is None
            else required_override
        ),
        discovery=tuple(discovery),
        superseded_from=superseded_from,
        chain=tuple(chain),
        selected=selected,
        full_source_bytes=len(raw),
        selected_bytes=len(selected_blob),
        selected_identity=content_identity(selected_blob),
    )


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------


@dataclass
class ReviewContext:
    review_kind: str
    target: ReviewTarget
    evidence: List[Evidence]
    none_recorded: bool
    attestations_scanned: int
    supplemental_references: List[str]
    management_artifacts: List[Dict[str, str]]
    context_identity: str
    measures: Dict[str, int]
    section: str

    def as_record(self) -> Dict[str, Any]:
        return {
            "review_kind": self.review_kind,
            "target": self.target.as_record(),
            "operator_intent": {
                "status": NONE_RECORDED if self.none_recorded else "resolved",
                "none_recorded": self.none_recorded,
                "evidence": [item.as_record() for item in self.evidence],
                "scan": {
                    "attestations_scanned": self.attestations_scanned,
                    "supplemental_references": list(self.supplemental_references),
                    "complete": True,
                },
            },
            "management_guidance": {"artifacts": list(self.management_artifacts)},
            "measures": dict(self.measures),
            "context_identity": self.context_identity,
            "operator_intent_section": self.section,
        }


def _canonical_identity_payload(
    review_kind: str,
    target: ReviewTarget,
    evidence: Sequence[Evidence],
) -> bytes:
    """Canonical bytes the context identity is taken over.

    The identity binds the review target and the operator-intent evidence — the
    facts the generated prompt section actually carries. It deliberately does
    **not** include the management-guidance artifact paths: the review prompt is
    itself one of those artifacts, so hashing them would make generating the
    prompt change the identity the prompt was just bound to, and every freshly
    generated review prompt would preflight as stale. The management channel
    stays in the emitted record; it is guidance for the reviewer, not part of
    the evidence binding.
    """
    payload = {
        "review_kind": review_kind,
        "target": target.as_record(),
        "evidence": [
            {
                "attestation_id": item.attestation.attestation_id,
                "attestation_path": item.attestation.relpath,
                "attestation_hash": item.attestation.attestation_hash,
                "attestation_status": item.attestation.status,
                "confirmed_by": item.attestation.confirmed_by,
                "confirmed_at": item.attestation.confirmed_at,
                "source_identity": item.attestation.source_identity,
                "source_path": item.attestation.source_relpath,
                "source_kind": item.attestation.source_kind,
                "source_hash": item.attestation.source_hash,
                "selected_identity": item.selected_identity,
                "selected_sections": [name for name, _ in item.selected],
                "required": item.required,
                "matched_scopes": sorted(
                    scope.render() for scope in item.matched_scopes
                ),
                "provenance_chain": list(item.chain),
                "superseded_from": item.superseded_from,
                "duplicate_of": list(item.duplicate_of),
            }
            for item in evidence
        ],
    }
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _fence_for(text: str) -> str:
    longest = 0
    for run in re.findall(r"`+", text):
        longest = max(longest, len(run))
    return "`" * max(3, longest + 1)


def render_intent_section(
    context_identity: str, evidence: Sequence[Evidence], none_recorded: bool
) -> str:
    """Render the generated ``## Operator intent`` prompt section.

    Deterministic and byte-stable: dispatch preflight re-renders it and compares
    exactly, so removing, reordering, or editing any part of it refuses the
    handoff.
    """
    lines: List[str] = [INTENT_SECTION_HEADING, ""]
    lines.append(f"Context identity: {context_identity}")
    lines.append("")
    if none_recorded:
        lines.append(NONE_RECORDED)
        lines.append("")
        lines.append(
            "A complete applicability scan and supplemental-reference resolution "
            "found no operator-confirmed intent for this review. Record "
            "`Operator-intent alignment: not assessable` with reason "
            f"`{ALIGNMENT_NONE_RECORDED_REASON}`; that result is explicitly "
            "non-blocking."
        )
        lines.append("")
        return "\n".join(lines)
    lines.append(
        "This evidence is derived from operator-confirmed attestations, not from "
        "management authorship. If the work, the specification, the task, or this "
        "prompt contradicts it, record "
        "`Operator-intent alignment: drifted` — drift blocks approval."
    )
    lines.append("")
    for index, item in enumerate(evidence, start=1):
        attestation = item.attestation
        lines.append(
            f"### Evidence {index} — {attestation.attestation_id} "
            f"({'required' if item.required else 'advisory'})"
        )
        lines.append("")
        lines.append(f"- Source: `{attestation.source_relpath}` ({attestation.source_kind})")
        lines.append(f"- Full-source identity: {attestation.source_hash}")
        lines.append(f"- Selected-content identity: {item.selected_identity}")
        lines.append(
            f"- Attestation: {attestation.attestation_id} "
            f"(`{attestation.relpath}`, {attestation.status}, confirmed by "
            f"{attestation.confirmed_by} on {attestation.confirmed_at})"
        )
        if item.matched_scopes:
            lines.append(
                "- Applicability match: "
                + ", ".join(sorted(scope.render() for scope in item.matched_scopes))
            )
        else:
            lines.append(
                "- Applicability match: supplemental reference "
                "(declared scopes: "
                + ", ".join(scope.render() for scope in attestation.scopes)
                + ")"
            )
        lines.append("- Discovery: " + ", ".join(item.discovery))
        if item.superseded_from is None:
            lines.append("- Supersession: current source")
        else:
            lines.append(
                "- Supersession: resolved from "
                f"{item.superseded_from} through the chain "
                + " -> ".join(item.chain)
            )
        if item.duplicate_of:
            lines.append(
                "- De-duplicated with: " + ", ".join(item.duplicate_of)
            )
        lines.append(
            "- Selected: "
            + ", ".join(name for name, _ in item.selected)
            + f" ({item.selected_bytes} bytes of {item.full_source_bytes})"
        )
        lines.append("")
        for name, body in item.selected:
            fence = _fence_for(body)
            lines.append(f"{fence}text")
            lines.append(body.rstrip("\n"))
            lines.append(fence)
            lines.append("")
    return "\n".join(lines)


def _management_artifacts(
    project_root: Path, target: ReviewTarget, task_path: Optional[Path]
) -> List[Dict[str, str]]:
    """The management-derived guidance channel: PM artifact paths only.

    Deliberately paths, not content — the two channels stay separate, and no
    conversation history, secret, or unrelated operator data is ever loaded.
    """
    artifacts: List[Dict[str, str]] = []

    real_root = os.path.realpath(os.fspath(project_root))

    def add(kind: str, candidate: Path) -> None:
        if candidate.is_file():
            artifacts.append(
                {
                    "kind": kind,
                    "path": os.path.relpath(
                        os.path.realpath(candidate), real_root
                    ).replace(os.sep, "/"),
                }
            )

    root = Path(project_root)
    if task_path is not None and task_path.is_file():
        # Path spelling must not perturb the context identity: the same review
        # resolved through an authored `/tmp/...` root and a filesystem-resolved
        # `/private/tmp/...` root is one review, so both sides canonicalize.
        add("task", Path(os.path.realpath(task_path)))
    if target.task_id:
        nn_nnn = target.task_id.removeprefix("TASK-")
        for spec in sorted((root / "specs").glob(f"SPEC-{nn_nnn}*.md")):
            add("spec", spec)
        add("prompt", root / "prompts" / f"PROMPT-{nn_nnn}.md")
        add("report", root / "reports" / f"REPORT-{nn_nnn}.md")
        add("review", root / "reviews" / f"REVIEW-{nn_nnn}.md")
    if target.checkpoint_id:
        add("prompt", root / "prompts" / f"PROMPT-{target.checkpoint_id}.md")
        add("report", root / "reports" / f"REPORT-{target.checkpoint_id}.md")
        add("review", root / "reviews" / f"REVIEW-{target.checkpoint_id}.md")
    if target.phase_id:
        add("phase", root / "phases" / f"{target.phase_id}.md")
    add("plan", root / "IMPLEMENTATION_PLAN.md")
    add("requirements", root / "REQUIREMENTS.md")
    artifacts.sort(key=lambda entry: (entry["kind"], entry["path"]))
    return artifacts


def resolve_review_context(
    project_root: Path,
    target: ReviewTarget,
    *,
    supplemental_refs: Sequence[str] = (),
    task_path: Optional[Path] = None,
) -> ReviewContext:
    """Resolve the complete two-channel review context, or refuse fail-closed.

    This is the single applicability implementation.  Every downstream surface
    consumes its normalized record; none re-derives applicability.
    """
    root = Path(os.path.realpath(os.fspath(project_root)))
    attestations, invalid = load_attestations(root)
    if invalid:
        first = invalid[0]
        raise IntentRefusal(
            first["rule"],
            f"{first['path']}: {first['detail']}",
            "repair or re-create the attestation through `cartopian attest-intent`",
        )
    current = [item for item in attestations if item.status == "current"]
    attested_by_source: Dict[str, List[Attestation]] = {}
    for item in current:
        attested_by_source.setdefault(item.source_relpath, []).append(item)

    decisions = _decision_index(root)

    def add_discovered(
        attestation: Attestation,
        *,
        matched: Sequence[Scope],
        discovery: str,
        required: bool,
        superseded_from: Optional[str] = None,
        chain: Sequence[str] = (),
    ) -> None:
        entry = discovered.setdefault(
            attestation.attestation_id,
            {
                "attestation": attestation,
                "matched": [],
                "discovery": set(),
                "required": False,
                "superseded_from": None,
                "chain": [],
            },
        )
        for scope in matched:
            if scope not in entry["matched"]:
                entry["matched"].append(scope)
        entry["discovery"].add(discovery)
        entry["required"] = bool(entry["required"] or required)
        if superseded_from is not None:
            if (
                entry["superseded_from"] is not None
                and entry["superseded_from"] != superseded_from
            ):
                raise IntentRefusal(
                    "ambiguous-successor",
                    f"{attestation.attestation_id} is reached through multiple "
                    "superseded sources",
                    "narrow or supersede the conflicting attestations",
                )
            entry["superseded_from"] = superseded_from
            entry["chain"] = list(chain)

    # --- Channel 1a: complete applicability scan -----------------------------
    # Every current attestation whose scope matches this target applies,
    # whether or not any artifact declared a reference to it.
    discovered: Dict[str, Dict[str, Any]] = {}
    for item in current:
        matched = tuple(scope for scope in item.scopes if target.matches(scope))
        if not matched:
            continue
        resolved = item
        required = item.required
        superseded_from: Optional[str] = None
        chain: Sequence[str] = ()
        if item.source_kind == "decision":
            match = DEC_FILE_RE.match(Path(item.source_relpath).stem)
            if match is None:
                raise IntentRefusal(
                    "ineligible-source",
                    f"{item.attestation_id} names no canonical decision identity",
                    "re-attest naming decisions/DEC-NNN-slug.md",
                )
            original_id = match.group(1)
            current_id, decision_chain = resolve_supersession(
                root, original_id, attested_by_source
            )
            if current_id != original_id:
                candidates = attested_by_source.get(decisions[current_id], [])
                if len(candidates) != 1:
                    raise IntentRefusal(
                        "ambiguous-successor",
                        f"{original_id} resolves to {current_id}, whose source "
                        f"carries {len(candidates)} current attestations",
                        "leave exactly one current attestation on the successor",
                    )
                resolved = candidates[0]
                required = required or resolved.required
                successor_matches = [
                    scope for scope in resolved.scopes if target.matches(scope)
                ]
                matched = tuple([*matched, *successor_matches])
                superseded_from = original_id
                chain = decision_chain
        add_discovered(
            resolved,
            matched=matched,
            discovery="applicability-scan",
            required=required,
            superseded_from=superseded_from,
            chain=chain,
        )

    # --- Channel 1b: supplemental references (additive only) -----------------
    for ref in supplemental_refs:
        if ATTESTATION_ID_RE.match(ref):
            attestation, attestation_chain = resolve_attestation_supersession(
                ref, attestations
            )
            add_discovered(
                attestation,
                matched=(),
                discovery="supplemental-reference",
                required=attestation.required,
                superseded_from=(ref if attestation.attestation_id != ref else None),
                chain=(
                    attestation_chain
                    if attestation.attestation_id != ref
                    else ()
                ),
            )
            continue

        if ref == REQUIREMENTS_INTENT_ID or OIR_ID_RE.match(ref):
            candidates = [
                item for item in current if item.source_identity == ref
            ]
            if not candidates:
                raise IntentRefusal(
                    "unattested-source",
                    f"supplemental reference {ref} carries no current operator "
                    "attestation",
                    "remove the reference or attest the eligible source through "
                    "`cartopian attest-intent`",
                )
            if len(candidates) > 1:
                raise IntentRefusal(
                    "ambiguous-reference",
                    f"supplemental reference {ref} carries {len(candidates)} "
                    "current attestations",
                    "supersede duplicate attestations so exactly one is current",
                )
            attestation = candidates[0]
            add_discovered(
                attestation,
                matched=(),
                discovery="supplemental-reference",
                required=attestation.required,
            )
            continue

        # DEC-NNN: an eligible source identity. Resolve supersession to the
        # unique current successor, which must carry its own attestation.
        if ref not in decisions:
            raise IntentRefusal(
                "unresolved-reference",
                f"supplemental reference {ref} names no decision artifact in this "
                "project",
                "remove the reference or restore the decision",
            )
        current_id, chain = resolve_supersession(root, ref, attested_by_source)
        source_path = decisions[current_id]
        candidates = attested_by_source.get(source_path, [])
        if not candidates:
            raise IntentRefusal(
                "unattested-source",
                f"supplemental reference {ref} resolves to {source_path}, which "
                "carries no current operator attestation; PM authorship or "
                "`Status: locked` alone does not make a source eligible",
                f"attest {current_id} with `cartopian attest-intent`",
            )
        if len(candidates) > 1:
            raise IntentRefusal(
                "ambiguous-reference",
                f"supplemental reference {ref} resolves to {source_path}, which "
                f"carries {len(candidates)} current attestations "
                f"({', '.join(sorted(c.attestation_id for c in candidates))})",
                "supersede the duplicate attestation so exactly one is current",
            )
        attestation = candidates[0]
        add_discovered(
            attestation,
            matched=(),
            discovery="supplemental-reference",
            required=attestation.required,
            superseded_from=(ref if current_id != ref else None),
            chain=(chain if current_id != ref else ()),
        )

    # --- Materialize evidence ------------------------------------------------
    evidence: List[Evidence] = []
    for entry in discovered.values():
        evidence.append(
            _build_evidence(
                root,
                entry["attestation"],
                entry["matched"],
                sorted(entry["discovery"]),
                entry["superseded_from"],
                entry["chain"],
                required_override=entry["required"],
            )
        )

    # --- Deterministic de-duplication, preserving provenance -----------------
    # Two attestations that carry the same selected content are one piece of
    # evidence. Requiredness is the union (a broader required attestation is
    # never shadowed by a narrower advisory one) and so are the matched scopes.
    evidence.sort(key=_ordering_key)
    deduped: List[Evidence] = []
    by_identity: Dict[str, Evidence] = {}
    for item in evidence:
        key = f"{item.attestation.source_relpath}|{item.selected_identity}"
        kept = by_identity.get(key)
        if kept is None:
            by_identity[key] = item
            deduped.append(item)
            continue
        kept.required = kept.required or item.required
        merged_scopes = list(kept.matched_scopes)
        for scope in item.matched_scopes:
            if scope not in merged_scopes:
                merged_scopes.append(scope)
        kept.matched_scopes = tuple(
            sorted(merged_scopes, key=lambda s: (s.specificity, s.render()))
        )
        kept.discovery = tuple(sorted(set(kept.discovery) | set(item.discovery)))
        kept.duplicate_of = tuple(
            sorted(set(kept.duplicate_of) | {item.attestation.attestation_id})
        )
        if kept.superseded_from is None and item.superseded_from is not None:
            kept.superseded_from = item.superseded_from
            kept.chain = item.chain
    deduped.sort(key=_ordering_key)

    by_source_bytes: Dict[str, int] = {}
    for item in deduped:
        source_path = item.attestation.source_relpath
        by_source_bytes[source_path] = (
            by_source_bytes.get(source_path, 0) + item.selected_bytes
        )
    oversized_sources = [
        (path, size)
        for path, size in sorted(by_source_bytes.items())
        if size > PER_SOURCE_MAX_BYTES
    ]
    if oversized_sources:
        path, size = oversized_sources[0]
        raise IntentRefusal(
            "oversize-selection",
            f"{path} contributes {size} bytes across current attestations, above "
            f"the {PER_SOURCE_MAX_BYTES}-byte per-source bound; content is never "
            "truncated to fit",
            "narrow, split, or supersede the attestations so the source's complete "
            "selected sections fit",
        )

    total_bytes = sum(item.selected_bytes for item in deduped)
    if total_bytes > TOTAL_MAX_BYTES:
        raise IntentRefusal(
            "context-overflow",
            f"applicable operator-intent evidence totals {total_bytes} bytes, above "
            f"the {TOTAL_MAX_BYTES}-byte per-review bound; content is never "
            "truncated to fit",
            "narrow or split an attestation, or reduce its applicability scope, "
            "then re-run the review handoff",
        )

    management_artifacts = _management_artifacts(root, target, task_path)
    identity = content_identity(
        _canonical_identity_payload(target.review_kind, target, deduped)
    )
    none_recorded = not deduped
    section = render_intent_section(identity, deduped, none_recorded)
    measures = {
        "evidence_count": len(deduped),
        "source_bytes": sum(item.full_source_bytes for item in deduped),
        "selected_bytes": total_bytes,
        "prompt_section_bytes": len(section.encode("utf-8")),
        "whole_source_max_bytes": WHOLE_SOURCE_MAX_BYTES,
        "per_source_max_bytes": PER_SOURCE_MAX_BYTES,
        "total_max_bytes": TOTAL_MAX_BYTES,
    }
    return ReviewContext(
        review_kind=target.review_kind,
        target=target,
        evidence=deduped,
        none_recorded=none_recorded,
        attestations_scanned=len(current),
        supplemental_references=list(supplemental_refs),
        management_artifacts=management_artifacts,
        context_identity=identity,
        measures=measures,
        section=section,
    )


# ---------------------------------------------------------------------------
# Prompt binding and preflight
# ---------------------------------------------------------------------------

def _intent_section_bounds(lines: Sequence[str]) -> Optional[Tuple[int, int]]:
    """Fence-aware ``(start, end)`` of the generated operator-intent section.

    Returns ``None`` when the section is absent or appears more than once — a
    duplicated section is not a single binding and must never be treated as one.
    """
    headings = heading_positions(lines)
    matches = [
        (position, index)
        for position, (index, level, text) in enumerate(headings)
        if level == 2 and f"## {text}" == INTENT_SECTION_HEADING
    ]
    if len(matches) != 1:
        return None
    position, start = matches[0]
    end = len(lines)
    for index, level, _ in headings[position + 1:]:
        if level <= 2:
            end = index
            break
    return start, end


def extract_intent_section(prompt_text: str) -> Optional[str]:
    """Return the prompt's ``## Operator intent`` section, or ``None``."""
    lines = prompt_text.splitlines()
    bounds = _intent_section_bounds(lines)
    if bounds is None:
        return None
    start, end = bounds
    return "\n".join(lines[start:end]).rstrip("\n") + "\n"


def upsert_intent_section(prompt_text: str, section: str) -> str:
    """Replace (or append) the generated ``## Operator intent`` section.

    The section is rendered by the tool, never authored, so an authored copy is
    always discarded in favour of the generated one.
    """
    body = section.rstrip("\n") + "\n"
    lines = prompt_text.splitlines(keepends=True)
    headings = heading_positions(lines)
    matches = [
        (position, index)
        for position, (index, level, text) in enumerate(headings)
        if level == 2 and f"## {text}" == INTENT_SECTION_HEADING
    ]
    if not matches:
        prefix = prompt_text if prompt_text.endswith("\n") else prompt_text + "\n"
        if prefix and not prefix.endswith("\n\n"):
            prefix += "\n"
        return prefix + body
    # Every authored duplicate is removed and replaced by one generated
    # section. Leaving a later duplicate in place would make the writer produce
    # a prompt that its own preflight correctly rejects as ambiguous.
    spans: List[Tuple[int, int]] = []
    for position, start in matches:
        end = len(lines)
        for index, level, _ in headings[position + 1:]:
            if level <= 2:
                end = index
                break
        spans.append((start, end))
    first_start = spans[0][0]
    tail_parts: List[str] = []
    cursor = first_start
    for start, end in spans:
        if cursor < start:
            tail_parts.extend(lines[cursor:start])
        cursor = max(cursor, end)
    tail_parts.extend(lines[cursor:])
    prefix = "".join(lines[:first_start])
    tail = "".join(tail_parts)
    return prefix + body + ("\n" + tail.lstrip("\n") if tail else "")


def preflight_prompt_binding(
    context: ReviewContext, prompt_text: str
) -> Dict[str, Any]:
    """Recompute the binding and classify any refusal reason.

    Returns ``{"ok": True, ...}`` or ``{"ok": False, "rule": ..., "detail": ...,
    "recovery": ...}``.  The comparison is exact: the generated section is
    byte-deterministic, so omitted evidence, altered content, a changed source,
    and a stale identity are all detected.
    """
    found = extract_intent_section(prompt_text)
    expected = context.section.rstrip("\n") + "\n"
    if found is None:
        return {
            "ok": False,
            "rule": "missing-operator-intent-section",
            "detail": (
                f"the review prompt carries no single `{INTENT_SECTION_HEADING}` "
                "section"
            ),
            "recovery": (
                "regenerate the prompt with `cartopian write-prompt "
                "--review-kind ...` so the section is tool-generated"
            ),
            "context_identity": context.context_identity,
        }
    if found == expected:
        return {"ok": True, "context_identity": context.context_identity}

    identity_line = f"Context identity: {context.context_identity}"
    if identity_line not in found:
        rule, detail = (
            "stale-prompt-binding",
            "the review prompt is bound to a different review-context identity "
            f"than the current one ({context.context_identity})",
        )
    else:
        present = set(re.findall(r"— (ATTEST-\d{3}) \(", found))
        expected_ids = {
            item.attestation.attestation_id for item in context.evidence
        }
        missing = sorted(expected_ids - present)
        if missing:
            rule, detail = (
                "omitted-applicable-evidence",
                "the review prompt omits applicable operator-intent evidence: "
                + ", ".join(missing),
            )
        else:
            rule, detail = (
                "altered-intent-content",
                "the review prompt's operator-intent content does not match the "
                "attested sources",
            )
    return {
        "ok": False,
        "rule": rule,
        "detail": detail,
        "recovery": (
            "regenerate the prompt with `cartopian write-prompt --review-kind ...` "
            "and dispatch again"
        ),
        "context_identity": context.context_identity,
    }


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------


def parse_alignment(
    review_text: str,
    *,
    required_evidence: Optional[bool] = None,
    expected_evidence: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Classify a review artifact's ``Operator-intent alignment:`` field.

    Returns ``{value, reason, present, blocking, detail}``.  Drift blocks.
    ``not assessable`` blocks when required evidence applies, is non-blocking
    when all applicable evidence is operator-marked advisory, and is also
    non-blocking for the exact ``none recorded`` result.  ``None`` fails closed
    for callers that have not resolved the current review context.
    """
    raw = read_header_field(review_text, ALIGNMENT_FIELD)
    raw_evidence = read_header_field(review_text, ALIGNMENT_EVIDENCE_FIELD)
    evidence: List[str] = []
    if raw_evidence is None:
        return {
            "value": None,
            "reason": None,
            "evidence": [],
            "present": False,
            "blocking": True,
            "detail": (
                f"review artifact records no `{ALIGNMENT_EVIDENCE_FIELD}:` field; "
                "approval requires the evidence considered"
            ),
        }
    if raw_evidence.strip().lower() == NONE_RECORDED:
        evidence = []
    else:
        evidence = _split_csv(raw_evidence)
        if not evidence or any(
            not ATTESTATION_ID_RE.match(item) for item in evidence
        ) or len(set(evidence)) != len(evidence):
            return {
                "value": None,
                "reason": None,
                "evidence": evidence,
                "present": True,
                "blocking": True,
                "detail": (
                    f"`{ALIGNMENT_EVIDENCE_FIELD}: {raw_evidence}` is outside "
                    "the closed grammar (ATTEST-NNN list | none recorded)"
                ),
            }
    if expected_evidence is not None and evidence != list(expected_evidence):
        expected = (
            ", ".join(expected_evidence)
            if expected_evidence
            else NONE_RECORDED
        )
        return {
            "value": None,
            "reason": None,
            "evidence": evidence,
            "present": True,
            "blocking": True,
            "detail": (
                f"`{ALIGNMENT_EVIDENCE_FIELD}:` does not match the current "
                f"review context; expected {expected}"
            ),
        }
    if raw is None:
        return {
            "value": None,
            "reason": None,
            "evidence": evidence,
            "present": False,
            "blocking": True,
            "detail": (
                f"review artifact records no `{ALIGNMENT_FIELD}:` field; approval "
                "requires an explicit alignment result"
            ),
        }
    value, _, reason = raw.partition("—")
    if not reason:
        value, _, reason = raw.partition(" - ")
    value = value.strip().lower()
    reason = reason.strip()
    if value not in ALIGNMENT_VALUES:
        return {
            "value": None,
            "reason": reason or None,
            "evidence": evidence,
            "present": True,
            "blocking": True,
            "detail": (
                f"`{ALIGNMENT_FIELD}: {raw}` is outside the closed domain "
                + " | ".join(ALIGNMENT_VALUES)
            ),
        }
    if value == "aligned":
        return {
            "value": value,
            "reason": reason or None,
            "evidence": evidence,
            "present": True,
            "blocking": False,
            "detail": "",
        }
    if value == "drifted":
        return {
            "value": value,
            "reason": reason or None,
            "evidence": evidence,
            "present": True,
            "blocking": True,
            "detail": (
                "operator-intent alignment is `drifted`; drift blocks approval "
                "regardless of management-artifact agreement"
            ),
        }
    if reason.lower() == ALIGNMENT_NONE_RECORDED_REASON:
        return {
            "value": value,
            "reason": reason,
            "evidence": evidence,
            "present": True,
            "blocking": False,
            "detail": "",
        }
    if required_evidence is False:
        return {
            "value": value,
            "reason": reason or None,
            "evidence": evidence,
            "present": True,
            "blocking": False,
            "detail": "",
        }
    return {
        "value": value,
        "reason": reason or None,
        "evidence": evidence,
        "present": True,
        "blocking": True,
        "detail": (
            "operator-intent alignment is `not assessable` for a reason other than "
            f"`{ALIGNMENT_NONE_RECORDED_REASON}`; required evidence that is not "
            "assessable blocks approval"
        ),
    }


def _dedupe(values: Sequence[str]) -> List[str]:
    seen: List[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def artifact_supplemental_refs(text: str) -> List[str]:
    """Parse the ``Intent refs:`` field of a task or planning-checkpoint artifact."""
    return parse_intent_refs(read_header_field(text, INTENT_REFS_FIELD))


def context_for_task(
    project_root: Path, task_path: Path, extra_refs: Sequence[str] = ()
) -> ReviewContext:
    """Resolve the task-closure review context for one task file.

    Supplemental references come from the task's own ``Intent refs:`` field;
    they are additive and can never suppress the applicability scan.
    """
    text = _contained_task_text(project_root, task_path)
    refs = _dedupe([*artifact_supplemental_refs(text), *extra_refs])
    return resolve_review_context(
        project_root,
        target_for_task(project_root, task_path),
        supplemental_refs=refs,
        task_path=task_path,
    )


def context_for_checkpoint(
    project_root: Path,
    checkpoint_id: str,
    *,
    phase_id: Optional[str] = None,
    plan_ref: Optional[str] = None,
    extra_refs: Sequence[str] = (),
    checkpoint_text: Optional[str] = None,
) -> ReviewContext:
    """Resolve the planning-checkpoint review context.

    A planning checkpoint has no task file, so its supplemental references come
    from the phase artifact it attaches to (when one is named) plus any the
    caller supplies.
    """
    refs: List[str] = []
    if checkpoint_text is not None:
        headers = _parse_header_block(checkpoint_text)
        authored_phase = (headers.get("Phase") or "").strip() or None
        authored_plan_ref = (headers.get("Plan ref") or "").strip() or None
        if authored_phase is not None and not PHASE_ID_RE.match(authored_phase):
            raise IntentRefusal(
                "malformed-review-target",
                f"planning checkpoint declares malformed Phase: {authored_phase!r}",
                "regenerate the checkpoint prompt with a canonical phase id",
            )
        if authored_plan_ref is not None and not PLAN_REF_RE.match(authored_plan_ref):
            raise IntentRefusal(
                "malformed-review-target",
                f"planning checkpoint declares malformed Plan ref: {authored_plan_ref!r}",
                "regenerate the checkpoint prompt with a canonical plan ref",
            )
        if phase_id is not None and authored_phase not in (None, phase_id):
            raise IntentRefusal(
                "ambiguous-review-target",
                f"planning checkpoint Phase: {authored_phase} disagrees with "
                f"the requested phase {phase_id}",
                "regenerate the prompt with one canonical review target",
            )
        if plan_ref is not None and authored_plan_ref not in (None, plan_ref):
            raise IntentRefusal(
                "ambiguous-review-target",
                f"planning checkpoint Plan ref: {authored_plan_ref} disagrees with "
                f"the requested plan ref {plan_ref}",
                "regenerate the prompt with one canonical review target",
            )
        phase_id = phase_id or authored_phase
        plan_ref = plan_ref or authored_plan_ref
        refs.extend(artifact_supplemental_refs(checkpoint_text))
    if phase_id:
        phase_path = Path(project_root) / "phases" / f"{phase_id}.md"
        if phase_path.is_file():
            try:
                phase_text = read_contained_bytes(
                    project_root,
                    f"phases/{phase_id}.md",
                    what="planning checkpoint artifact",
                ).decode("utf-8")
            except UnicodeDecodeError:
                raise IntentRefusal(
                    "malformed-review-target",
                    f"planning checkpoint phase is not valid UTF-8: {phase_id}",
                    "restore a UTF-8 phase artifact",
                )
            refs.extend(artifact_supplemental_refs(phase_text))
    refs.extend(extra_refs)
    return resolve_review_context(
        project_root,
        ReviewTarget(
            review_kind="planning",
            phase_id=phase_id,
            plan_ref=plan_ref,
            checkpoint_id=checkpoint_id,
        ),
        supplemental_refs=_dedupe(refs),
    )


def alignment_enforced(project_schema_version: Optional[str]) -> bool:
    """True when this project's schema is at or beyond the alignment contract.

    Historical reviews in pre-v0.8.0 projects stay readable and unrewritten; the
    guard applies to projects that have migrated. The compatibility window is
    documented and bounded (see :data:`ALIGNMENT_COMPATIBILITY_WINDOW_ENDS`).
    """
    if not project_schema_version:
        return False
    match = re.match(r"^v(\d+)\.(\d+)\.(\d+)$", project_schema_version.strip())
    if not match:
        return False
    return tuple(int(part) for part in match.groups()) >= ALIGNMENT_ENFORCED_FROM
