"""Bind one governed task to its validated upstream trace.

:mod:`cli.acceptance_trace` is the mechanism — normalization, serialization,
projections, bounds, determinations — and knows nothing about the filesystem.
This module is the seam that resolves a task on disk into the inputs that
mechanism needs:

* the governing specification's ``## Examples / acceptance`` enumeration,
* the task's own ``## Acceptance`` checklist,
* the PM-authored record set in the task's ``## Upstream trace`` section,
* the authoritative source identities from resolved source guidance, already
  deidentified by :func:`cli.source_guidance.assignee_projection`, and
* the operator excerpts from the immutable request trace, as
  ``REQ-<order> sha256:<content identity>``.

**Declaration, and the migration boundary it draws.** A task declares
``Upstream trace: required`` or ``Upstream trace: n/a`` in its header block,
exactly as it already declares ``Source guidance:``. A task that declares
neither is legacy: it is read, it is not enforced, and it is not silently
treated as either state. There is no permanent dual contract — the header is
the one authoritative switch, and a project migrates one task at a time.

The trace is not a persisted manifest. It lives in the task, which readiness,
prompt assembly, and review context already open, so the routine path gains no
additional file read.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from cli import acceptance_trace, request_trace, source_guidance

#: Header the task uses to declare whether the trace contract governs it.
DECLARATION_HEADER = "Upstream trace"
REQUIRED = "required"
NOT_APPLICABLE = "n/a"
NOT_DECLARED = "not-declared"
DECLARATIONS = (REQUIRED, NOT_APPLICABLE)

_HEADER_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 _/-]*?):\s*(.*)$")


def declaration(task_text: str) -> str:
    """Return ``required``, ``n/a``, or ``not-declared`` for one task."""
    for line in task_text.splitlines():
        if line.startswith("## "):
            break
        stripped = line.strip()
        match = _HEADER_RE.match(stripped)
        if not match:
            continue
        if match.group(1).strip() == DECLARATION_HEADER:
            value = match.group(2).strip().lower()
            return value if value in DECLARATIONS else NOT_DECLARED
    return NOT_DECLARED


def _header(task_text: str, name: str) -> Optional[str]:
    for line in task_text.splitlines():
        if line.startswith("## "):
            break
        match = _HEADER_RE.match(line.strip())
        if match and match.group(1).strip() == name:
            return match.group(2).strip()
    return None


def governing_spec_path(project_root: Path, task_path: Path, task_text: str) -> Optional[Path]:
    """Resolve the task's ``Spec:`` header to a contained specification path."""
    raw = (_header(task_text, "Spec") or "").strip()
    if raw.lower() in {"", "none", "n/a"}:
        return None
    candidate = Path(raw)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        relative = (
            candidate
            if candidate.parts and candidate.parts[0] == "specs"
            else Path("specs") / candidate
        )
        resolved = (project_root / relative).resolve()
    try:
        resolved.relative_to((project_root / "specs").resolve())
    except ValueError:
        raise acceptance_trace.TraceRefusal(
            "trace-unparseable",
            f"the task's Spec: header escapes the project's specs directory: {raw}",
        )
    return resolved


def source_identities(task_path: Path, task_text: str) -> List[str]:
    """Authoritative source identities, in the deidentified assignee form.

    These are the identities an ``S|`` record keys on and an edge copies
    verbatim, so both sides of the coverage check read the same string.
    """
    record = source_guidance.resolve_task_guidance(task_path, content=task_text)
    outcome = record.get("outcome")
    if outcome == "invalid":
        # Silently returning an empty source list would make every `S|` record
        # disappear, and with them every `source-uncovered` finding. An
        # unresolvable guidance record fails closed instead.
        raise acceptance_trace.TraceRefusal(
            "trace-incomplete",
            "source guidance is unresolvable ("
            + ", ".join(record.get("blocker_codes", []) or ["unknown"])
            + "), so authoritative sources cannot be enumerated",
        )
    if outcome != "valid":
        return []
    projection = source_guidance.assignee_projection(record)
    out: List[str] = []
    for source in projection.get("authoritative_sources", []):
        identity = source.get("identity", "").strip()
        if identity and identity not in out:
            out.append(identity)
    return out


def source_width_problems(task_path: Path, task_text: str) -> List[str]:
    """Source identities too wide for any trace edge to copy verbatim.

    An edge's source field is capped at ``CAP_SOURCE_IDENTITY`` bytes and must
    equal the projected identity exactly, so a wider identity can never be
    traced — only scoped by an ``A|`` record. Measured on the assignee
    projection, because a PM-scoped identity projects to a fixed-width alias.
    Empty for a task that does not declare ``Upstream trace: required`` or
    whose guidance does not resolve (readiness reports that separately).
    """
    if declaration(task_text) != REQUIRED:
        return []
    try:
        sources = source_identities(task_path, task_text)
    except (
        acceptance_trace.TraceRefusal,
        source_guidance.SourceGuidanceParseError,
        OSError,
        UnicodeDecodeError,
    ):
        return []
    cap = acceptance_trace.CAP_SOURCE_IDENTITY
    problems: List[str] = []
    for identity in sources:
        width = len(identity.encode("utf-8"))
        if width > cap:
            problems.append(
                f"Source guidance Identity is {width} B, over the {cap} B "
                f"upstream-trace source cap, so no edge can name it: {identity}"
            )
    return problems


def excerpt_identities(project_root: Path, task_path: Path) -> List[str]:
    """Operator excerpts as ``REQ-<evidence order> sha256:<content identity>``.

    Request coverage matches against excerpt *content* identity, never against
    a containing artifact: an edge naming the artifact could not be matched to
    the excerpt at all, which is what would make silent non-coverage of
    confirmed operator intent invisible.
    """
    context = request_trace.context_for_task_assignment(project_root, task_path)
    out: List[str] = []
    for record in context.evidence:
        identity = f"REQ-{record.sequence:03d} {record.identity}"
        if identity not in out:
            out.append(identity)
    return out


def enumerate_inputs(
    project_root: Path,
    task_path: Path,
    *,
    task_text: Optional[str] = None,
    tolerate_request_refusal: bool = False,
) -> Dict[str, Any]:
    """The mechanical inputs a PM maps before any record exists.

    Readiness needs a valid block; authoring needs the ordinals, digests, and
    identities that block must name. This projection lists them once — the
    material criteria in contract order (before any merge), the deidentified
    source identities, and each operator excerpt's alias, identity, and a
    bounded text preview — so the PM maps criteria to authority without
    hashing anything by hand. Read-only and on demand: it is never part of a
    routine assignment or review body.

    With ``tolerate_request_refusal``, a request-evidence refusal (such as an
    unbound governing decision, which an ``A|`` record may be the fix for)
    leaves ``excerpts`` empty and is reported under ``excerpts_refusal``
    instead of hiding the criteria and source identities.
    """
    project_root = Path(project_root)
    task_path = Path(task_path)
    text = task_text if task_text is not None else task_path.read_text(encoding="utf-8")
    spec_path = governing_spec_path(project_root, task_path, text)
    spec_items: List[str] = []
    if spec_path is not None:
        spec_items = acceptance_trace.spec_acceptance_items(
            spec_path.read_text(encoding="utf-8")
        )
    task_items = acceptance_trace.task_acceptance_items(text)
    criteria = acceptance_trace._material_after_merges(  # noqa: SLF001
        [acceptance_trace.normalize(t) for t in spec_items],
        [acceptance_trace.normalize(t) for t in task_items],
        (),
    )
    excerpts = []
    excerpts_refusal = None
    try:
        evidence = request_trace.context_for_task_assignment(project_root, task_path).evidence
    except request_trace.RequestRefusal as refusal:
        if not tolerate_request_refusal:
            raise
        evidence = []
        excerpts_refusal = refusal.as_record()
    for record in evidence:
        preview = acceptance_trace.normalize(record.text)
        if len(preview) > 160:
            preview = preview[:157] + "..."
        excerpts.append(
            {
                "alias": f"REQ-{record.sequence:03d}",
                "identity": f"REQ-{record.sequence:03d} {record.identity}",
                "unit": record.unit.as_record(),
                "preview": preview,
            }
        )
    return {
        "declaration": declaration(text),
        "spec_path": str(spec_path) if spec_path else None,
        "criteria": [
            {
                "ordinal": c.ordinal,
                "digest12": c.digest,
                "origin_list": c.origin_list,
                "text": c.text,
            }
            for c in criteria
        ],
        "sources": source_identities(task_path, text),
        "source_width_problems": source_width_problems(task_path, text),
        "mapping_fields": acceptance_trace.mapping_fields(),
        "excerpts": excerpts,
        "excerpts_refusal": excerpts_refusal,
    }


#: Structural marker a decision uses to record an authorized plan-level
#: disposition for an operator excerpt no task claims.
OUT_OF_PLAN_MARKER = "Out-of-plan request:"


#: The statuses a decision may declare. ``locked`` is a ruling in force;
#: ``open`` is a proposal. Anything else — including an absent header — is
#: unreadable, and readers say so rather than guessing.
DECISION_STATUSES: Tuple[str, ...] = ("locked", "open")


def decision_header(text: str, name: str) -> str:
    """One ``Name: value`` line from a decision's top header block.

    The block is the run of lines before the first ``## `` section, matching
    the boundary every other header reader in the CLI uses. Returns ``""`` when
    the field is absent, which callers must distinguish from a declared empty
    value if the difference matters to them.
    """
    for line in text.splitlines():
        if line.startswith("## "):
            break
        stripped = line.strip()
        if stripped.startswith(f"{name}:"):
            return stripped[len(name) + 1 :].strip()
    return ""


# A superseding decision that changes nothing in a ruling but its reach names
# the decision it restates instead of copying it: `Restates: DEC-NNN
# sha256:<64 hex>`. Decisions are immutable, so a pointer pinned to the target's
# exact bytes is as fixed as a copy and cannot be mistranscribed. Every reader
# sees the restating decision's *effective* text: its own file, then the
# restated decision's effective text under a `## Restated from` heading. The
# heading keeps header reads (Status, Supersedes) on the restating file's own
# block, and everything below it -- the ruling, its markers, and its
# `Operator request evidence for:` bindings -- counts as the restating
# decision's own.
RESTATES_HEADER = "Restates"
RESTATED_SECTION_PREFIX = "## Restated from "
_RESTATES_RE = re.compile(r"(DEC-\d{3})(?:\s+(sha256:[0-9a-f]{64}))?")


class RestatementError(ValueError):
    """A ``Restates:`` pointer that cannot be resolved to pinned text."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def decision_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def restated_decision(text: str) -> Optional[Tuple[str, Optional[str]]]:
    """``(DEC-NNN, digest or None)`` from a ``Restates:`` header, else None."""
    value = decision_header(text, RESTATES_HEADER)
    if not value or value.lower() in ("none", "n/a"):
        return None
    match = _RESTATES_RE.fullmatch(value)
    if match is None:
        raise RestatementError(
            "decision-restatement-malformed",
            f"`{RESTATES_HEADER}: {value}` must be `{RESTATES_HEADER}: DEC-NNN sha256:<64 hex>`",
        )
    return match.group(1), match.group(2)


def raw_decision_files(project_root: Path) -> Dict[str, bytes]:
    """``{stem: bytes}`` for every readable ``decisions/DEC-*.md`` file."""
    directory = Path(project_root) / "decisions"
    if not directory.is_dir():
        return {}
    raw: Dict[str, bytes] = {}
    for path in sorted(directory.glob("DEC-*.md")):
        try:
            raw[path.stem] = path.read_bytes()
        except OSError:
            continue
    return raw


def resolve_decision_text(stem: str, raw: Dict[str, bytes]) -> str:
    """A decision's effective text, following pinned ``Restates:`` pointers.

    Fails closed: an unpinned, mismatched, missing, circular, or
    non-superseding pointer raises :class:`RestatementError`, because a
    ruling whose text cannot be established authorizes nothing.
    """
    seen: List[str] = []
    parts: List[str] = []
    current = stem
    while True:
        try:
            text = raw[current].decode("utf-8")
        except KeyError:
            if not seen:
                raise RestatementError(
                    "decision-not-found", f"no readable decisions/{current}.md"
                ) from None
            raise RestatementError(
                "decision-restatement-target-missing",
                f"{seen[-1]} restates {current}, which has no decisions/{current}.md",
            ) from None
        except UnicodeDecodeError:
            raise RestatementError(
                "decision-restatement-unreadable", f"decisions/{current}.md is not UTF-8 text"
            ) from None
        seen.append(current)
        target = restated_decision(text)
        if target is None:
            parts.append(text)
            break
        target_id, digest = target
        if digest is None:
            raise RestatementError(
                "decision-restatement-unpinned",
                f"{current} restates {target_id} without its content digest; "
                "re-issue it through `cartopian write-decision`, which pins it",
            )
        if target_id in seen:
            raise RestatementError(
                "decision-restatement-cycle",
                f"{current} restates {target_id}, which already restates {current}",
            )
        if target_id not in re.findall(r"DEC-\d{3}", decision_header(text, "Supersedes")):
            raise RestatementError(
                "decision-restatement-not-superseded",
                f"{current} restates {target_id} but does not supersede it; a "
                f"restatement retires its target, so add {target_id} to `Supersedes:`",
            )
        if target_id in raw and decision_digest(raw[target_id]) != digest:
            raise RestatementError(
                "decision-restatement-digest-mismatch",
                f"{current} pins {target_id} at {digest}, but decisions/{target_id}.md "
                "has changed since; a decision is immutable, so restore it",
            )
        parts.append(text.rstrip("\n") + f"\n\n{RESTATED_SECTION_PREFIX}{target_id} ({digest})\n\n")
        current = target_id
    return "".join(parts)


def pin_restatement(
    project_root: Path, decision_id: str, content: str
) -> Tuple[str, Optional[Dict[str, str]], Optional[RestatementError]]:
    """Pin and check a body's ``Restates:`` pointer before it is written.

    ``Restates: DEC-NNN`` without a digest is pinned to the target's current
    bytes; a supplied digest must match them. The whole chain must then
    resolve with this body in place. Returns ``(content, restatement, error)``.
    """
    try:
        target = restated_decision(content)
    except RestatementError as exc:
        return content, None, exc
    if target is None:
        return content, None, None
    target_id, digest = target
    if target_id == decision_id:
        return content, None, RestatementError(
            "decision-restatement-cycle", f"{decision_id} cannot restate itself"
        )
    raw = raw_decision_files(project_root)
    if target_id not in raw:
        return content, None, RestatementError(
            "decision-restatement-target-missing",
            f"{decision_id} restates {target_id}, which has no decisions/{target_id}.md",
        )
    actual = decision_digest(raw[target_id])
    if digest is None:
        lines = content.splitlines(keepends=True)
        for index, line in enumerate(lines):
            if line.startswith("## "):
                break
            if line.strip().startswith(f"{RESTATES_HEADER}:"):
                ending = "\n" if line.endswith("\n") else ""
                lines[index] = f"{RESTATES_HEADER}: {target_id} {actual}{ending}"
                break
        content = "".join(lines)
    raw[decision_id] = content.encode("utf-8")
    try:
        resolve_decision_text(decision_id, raw)
    except RestatementError as exc:
        return content, None, exc
    return content, {"decision": target_id, "digest": actual}, None


def effective_decision_text(project_root: Path, decision_id: str) -> str:
    """One decision's effective text; raises :class:`RestatementError`."""
    return resolve_decision_text(decision_id, raw_decision_files(project_root))


def restatement_problems(project_root: Path) -> List[Dict[str, str]]:
    """Every decision whose ``Restates:`` pointer cannot be resolved."""
    raw = raw_decision_files(project_root)
    problems: List[Dict[str, str]] = []
    for stem in sorted(raw):
        try:
            resolve_decision_text(stem, raw)
        except RestatementError as exc:
            problems.append({"decision": f"decisions/{stem}.md", "code": exc.code, "detail": exc.detail})
    return problems


def decision_bodies(project_root: Path) -> Dict[str, str]:
    """``{DEC-NNN: effective body}`` for every readable decision file.

    A restating decision whose pointer cannot be resolved keeps only its own
    text here; the authorization readers in ``evidence_resolver`` refuse it.
    """
    raw = raw_decision_files(project_root)
    texts: Dict[str, str] = {}
    for stem, data in raw.items():
        try:
            texts[stem] = resolve_decision_text(stem, raw)
        except RestatementError:
            try:
                texts[stem] = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
    return texts


def superseded_ids(texts: Dict[str, str]) -> Set[str]:
    """Decision ids any other decision retires through ``Supersedes:``."""
    retired: Set[str] = set()
    for text in texts.values():
        retired.update(re.findall(r"DEC-\d{3}", decision_header(text, "Supersedes")))
    return retired


def is_current_locked_decision(decision_id: str, text: str, retired: Set[str]) -> bool:
    """Whether a decision can authorize a disposition or select evidence."""
    return decision_id not in retired and decision_header(text, "Status").lower() == "locked"


# Who a ruling speaks for. `operator` is a ruling the operator made, and it
# carries the captured turns that prove it; `pm` is the PM's own call. The
# split exists because every other reader treats a locked decision as a
# ruling in force whoever wrote it, which let a PM-authored decision drop an
# operator request from the plan or quietly overturn an operator ruling.
AUTHORITY_HEADER = "Authority"
DECISION_AUTHORITIES: Tuple[str, ...] = ("operator", "pm")
OPERATOR_AUTHORITY = "operator"
PM_AUTHORITY = "pm"
#: A decision that adds to or narrows an earlier one without retiring it.
AMENDS_HEADER = "Amends"
#: An operator decision that adopts earlier PM decisions as operator rulings.
RATIFIES_HEADER = "Ratifies"
#: The one-line summary of what was ruled, projected into the index.
RULING_HEADER = "Ruling"
RULING_CAP = 240
#: An operator decision that ends the plan before its work is done.
CLOSES_PLAN_HEADER = "Closes plan"
CLOSES_PLAN_EARLY = "early"
#: An operator decision naming the rulings that outlive the plan at reset.
CARRIES_HEADER = "Carries forward"
#: Evidence units that name one plan's tasks or checkpoints. A later plan
#: reuses those identifiers for unrelated work, so a ruling bound to them is
#: plan-scoped and cannot carry forward.
PLAN_SCOPED_UNITS: Tuple[str, ...] = ("task", "planning")


def decision_ids_in_header(text: str, name: str) -> List[str]:
    """The ``DEC-NNN`` ids one header names, in order and without repeats."""
    seen: List[str] = []
    for found in re.findall(r"DEC-\d{3}", decision_header(text, name)):
        if found not in seen:
            seen.append(found)
    return seen


def has_evidence_marker(text: str) -> bool:
    """Whether a decision's text names any captured operator turn."""
    from cli.evidence_resolver import DECISION_EVIDENCE_MARKER  # local: import cycle

    marker = DECISION_EVIDENCE_MARKER.lower()
    return any(line.strip().lower().startswith(marker) for line in text.splitlines())


def declared_authority(text: str) -> str:
    """The ``Authority:`` header, lower-cased; ``""`` when absent."""
    return decision_header(text, AUTHORITY_HEADER).lower()


def base_authority(text: str) -> str:
    """The authority one decision's own effective text supports.

    ``operator`` only when the text names captured operator turns and does not
    declare otherwise. A decision recorded before the header existed is read by
    the same rule, so an evidence-bearing legacy ruling keeps its standing and
    an unevidenced one is the PM's. A declared ``operator`` with no evidence,
    or an unrecognized value, is read as ``pm``: a claim to speak for the
    operator is only as good as the turns it names.
    """
    declared = declared_authority(text)
    if declared not in ("", OPERATOR_AUTHORITY):
        return PM_AUTHORITY
    return OPERATOR_AUTHORITY if has_evidence_marker(text) else PM_AUTHORITY


def ratifications(texts: Dict[str, str]) -> Dict[str, List[str]]:
    """``{pm decision: [ratifying operator decisions]}`` from current rulings.

    Only a current, locked decision whose own text carries operator authority
    ratifies; ratification does not chain, so a ratified PM decision cannot in
    turn ratify another.

    Carrying a decision forward is adopting it, so ``Carries forward:`` targets
    count as ratified by the decision that carries them.
    """
    retired = superseded_ids(texts)
    out: Dict[str, List[str]] = {}
    for stem in sorted(texts):
        text = texts[stem]
        if not is_current_locked_decision(stem, text, retired):
            continue
        if base_authority(text) != OPERATOR_AUTHORITY:
            continue
        targets = decision_ids_in_header(text, RATIFIES_HEADER)
        targets += [t for t in decision_ids_in_header(text, CARRIES_HEADER) if t not in targets]
        for target in targets:
            if target != stem:
                out.setdefault(target, []).append(stem)
    return out


def amendments(texts: Dict[str, str]) -> Dict[str, List[str]]:
    """``{amended decision: [current locked decisions amending it]}``."""
    retired = superseded_ids(texts)
    out: Dict[str, List[str]] = {}
    for stem in sorted(texts):
        text = texts[stem]
        if not is_current_locked_decision(stem, text, retired):
            continue
        for target in decision_ids_in_header(text, AMENDS_HEADER):
            if target != stem:
                out.setdefault(target, []).append(stem)
    return out


def superseders(texts: Dict[str, str]) -> Dict[str, List[str]]:
    """``{retired decision: [decisions naming it under Supersedes:]}``."""
    out: Dict[str, List[str]] = {}
    for stem in sorted(texts):
        for target in decision_ids_in_header(texts[stem], "Supersedes"):
            if target != stem:
                out.setdefault(target, []).append(stem)
    return out


def effective_authorities(texts: Dict[str, str]) -> Dict[str, str]:
    """``{DEC-NNN: operator | pm}`` with operator ratification applied."""
    ratified = ratifications(texts)
    return {
        stem: OPERATOR_AUTHORITY
        if base_authority(text) == OPERATOR_AUTHORITY or stem in ratified
        else PM_AUTHORITY
        for stem, text in texts.items()
    }


def carries_out_of_plan(text: str) -> bool:
    return any(line.strip().startswith(OUT_OF_PLAN_MARKER) for line in text.splitlines())


_LIST_MARKER_RE = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[`\"'])")
_BOLD_LEAD_RE = re.compile(r"^\*\*(.+?)\*\*\s*(.*)$")
#: Tighter than ``RULING_CAP``: a derived line stands in for one, and every
#: index row it lengthens is read by every session that opens the index.
DERIVED_RULING_CAP = 160


def ruling_line(text: str) -> str:
    """The ``Ruling:`` header, or for a decision without one, its first sentence.

    A decision recorded before the header existed still deserves a readable
    index row, and it cannot gain the header in place. The stand-in is the
    first sentence of ``## Decision``, skipping lead-ins that end in a colon,
    quotations, and evidence or disposition markers. It is a projection for
    readers, never a ruling: nothing authorizes from it.
    """
    declared = decision_header(text, RULING_HEADER)
    if declared:
        return declared
    section: Optional[List[str]] = None
    for line in text.splitlines():
        if line.startswith("## "):
            if section is not None:
                break
            if line[3:].strip().lower() == "decision":
                section = []
            continue
        if section is not None:
            section.append(line)
    collected: List[str] = []
    for line in section or []:
        stripped = line.strip()
        if not stripped:
            if collected:
                break
            continue
        if collected and _LIST_MARKER_RE.match(stripped):
            break
        if not collected and (
            stripped.endswith(":")
            or stripped.startswith((">", "#", "```", "|", OUT_OF_PLAN_MARKER))
            or stripped.lower().startswith("operator request evidence for:")
        ):
            continue
        collected.append(_LIST_MARKER_RE.sub("", stripped) if not collected else stripped)
    paragraph = " ".join(" ".join(collected).split())
    # A bold lead ("**Language.** Python 3.11 ...") names the topic; the ruling
    # is the sentence after it, so keep both.
    lead = _BOLD_LEAD_RE.match(paragraph)
    if lead and lead.group(2):
        rest = lead.group(2)
        joiner = " " if rest[0] in "\u2014-:;,(" else ": "
        paragraph = f"{lead.group(1).rstrip('.:')}{joiner}{rest}"
    paragraph = paragraph.replace("**", "").replace("__", "")
    sentence = _SENTENCE_END_RE.split(paragraph, maxsplit=1)[0]
    if len(sentence) > DERIVED_RULING_CAP:
        sentence = sentence[: DERIVED_RULING_CAP - 3].rsplit(" ", 1)[0] + "..."
    return sentence


def plan_scoped_reasons(decision_id: str, text: str) -> List[str]:
    """Why a decision's effective text binds only the plan it was made in.

    A ruling that closes the plan, disposes of the plan's operator requests,
    or binds evidence to the plan's tasks or planning checkpoints means nothing
    in the next plan, and could mean the wrong thing there, since the next plan
    reuses those identifiers for unrelated work.
    """
    from cli.evidence_resolver import decision_text_references  # local: import cycle

    reasons: List[str] = []
    if decision_header(text, CLOSES_PLAN_HEADER):
        reasons.append("it closes the plan")
    if carries_out_of_plan(text):
        reasons.append("it disposes of this plan's operator requests")
    try:
        refs = decision_text_references(decision_id, text)
    except request_trace.RequestRefusal:
        reasons.append("its evidence markers are malformed")
        refs = []
    units = sorted({
        f"{ref.unit.kind}:{ref.unit.identifier}" for ref in refs if ref.unit.kind in PLAN_SCOPED_UNITS
    })
    if units:
        reasons.append(f"it binds evidence to this plan's {', '.join(units)}")
    return reasons


def early_close_decision(project_root: Path) -> Optional[str]:
    """The current operator decision that closes the plan early, if one does.

    ``Closes plan: early`` is the operator ruling that the plan ends with its
    remaining work unfinished. Only a current, locked decision speaking for
    the operator counts; a PM decision may propose it as ``open``.
    """
    texts = decision_bodies(project_root)
    retired = superseded_ids(texts)
    authorities = effective_authorities(texts)
    for stem in sorted(texts):
        text = texts[stem]
        if not is_current_locked_decision(stem, text, retired):
            continue
        if authorities[stem] != OPERATOR_AUTHORITY:
            continue
        if decision_header(text, CLOSES_PLAN_HEADER).lower() == CLOSES_PLAN_EARLY:
            return f"decisions/{stem}.md"
    return None


def restatement_closure(raw: Dict[str, bytes], ids: Set[str]) -> Set[str]:
    """``ids`` plus every decision they restate, link by link."""
    closure = set(ids)
    pending = sorted(closure)
    while pending:
        current = pending.pop()
        try:
            restated = restated_decision(raw[current].decode("utf-8"))
        except (KeyError, UnicodeDecodeError, RestatementError):
            restated = None
        if restated is not None and restated[0] not in closure:
            closure.add(restated[0])
            pending.append(restated[0])
    return closure


def carried_decision_ids(project_root: Path) -> Set[str]:
    """Decisions an operator carry keeps: carriers, targets, restated text.

    After a reset that carried rulings forward these are the whole of
    ``decisions/``, and they are the project's standing rulings rather than
    evidence that a plan exists.
    """
    raw = raw_decision_files(project_root)
    texts = decision_bodies(project_root)
    retired = superseded_ids(texts)
    kept: Set[str] = set()
    for stem, text in texts.items():
        targets = decision_ids_in_header(text, CARRIES_HEADER)
        if not targets or not is_current_locked_decision(stem, text, retired):
            continue
        if base_authority(text) != OPERATOR_AUTHORITY:
            continue
        kept.add(stem)
        kept.update(targets)
    return restatement_closure(raw, kept)


def carry_problems(texts: Dict[str, str], carrier: str) -> List[str]:
    """Why ``carrier``'s ``Carries forward:`` list cannot outlive the plan.

    ``texts`` holds every decision with ``carrier`` in it. Each target must be
    a ruling in force and not plan-scoped, and a ruling travels with its
    amendments: carrying one without the other would change what it says.
    The carrier is kept too, as the record of the operator's choice, so it
    must not be plan-scoped either. An empty list means the carry holds.
    """
    targets = decision_ids_in_header(texts[carrier], CARRIES_HEADER)
    if not targets:
        return [f"`{CARRIES_HEADER}:` names no decision"]
    retired = superseded_ids(texts)
    amended_by = amendments(texts)
    kept = set(targets) | {carrier}
    problems: List[str] = []
    for target in [carrier] + targets:
        if target != carrier:
            if target not in texts:
                problems.append(f"{target} is not a recorded decision")
                continue
            if not is_current_locked_decision(target, texts[target], retired):
                problems.append(f"{target} is not a ruling in force (superseded, or not locked)")
                continue
            problems.extend(
                f"{target} has an amendment, {amender}, that is not carried; carry both or neither"
                for amender in amended_by.get(target, []) if amender not in kept
            )
        text = texts[target]
        problems.extend(f"{target} is plan-scoped: {reason}" for reason in plan_scoped_reasons(target, text))
        problems.extend(
            f"{target} amends {base}, which is not carried; carry both or neither"
            for base in decision_ids_in_header(text, AMENDS_HEADER) if base not in kept
        )
    return problems


def authority_findings(project_root: Path) -> Dict[str, List[Dict[str, Any]]]:
    """What ``plan-audit`` reports about decision authority.

    ``unreadable``: a current decision whose ``Authority:`` is outside the
    vocabulary, or claims ``operator`` without naming a captured turn — read as
    ``pm`` either way. ``unauthorized_dispositions``: a current, locked PM
    decision carrying ``Out-of-plan request:`` lines, which dispose of nothing.
    ``unratified``: current, locked decisions that are the PM's own call, for
    the operator to ratify or overturn.
    """
    texts = decision_bodies(project_root)
    retired = superseded_ids(texts)
    authorities = effective_authorities(texts)
    unreadable: List[Dict[str, Any]] = []
    unauthorized: List[Dict[str, Any]] = []
    unratified: List[str] = []
    for stem in sorted(texts):
        if stem in retired:
            continue
        text = texts[stem]
        declared = declared_authority(text)
        if declared and declared not in DECISION_AUTHORITIES:
            unreadable.append({"decision": f"decisions/{stem}.md", "declared": declared, "reason": "unrecognized"})
        elif declared == OPERATOR_AUTHORITY and not has_evidence_marker(text):
            unreadable.append({"decision": f"decisions/{stem}.md", "declared": declared, "reason": "unevidenced"})
        if not is_current_locked_decision(stem, text, retired):
            continue
        if authorities[stem] == PM_AUTHORITY:
            unratified.append(stem)
            if carries_out_of_plan(text):
                unauthorized.append({"decision": f"decisions/{stem}.md"})
    return {
        "unreadable": unreadable,
        "unauthorized_dispositions": unauthorized,
        "unratified": unratified,
    }


def unreadable_status_decisions(project_root: Path) -> List[Dict[str, str]]:
    """Decisions whose ``Status:`` is absent or outside the vocabulary.

    These are not a cosmetic defect. Authorization readers require an explicit
    ``locked``, so such a decision silently authorizes nothing however plainly
    its prose rules — a governing ruling that no mechanical reader can see.
    Detection belongs in ``plan-audit``; correcting the header is PM work.
    """
    findings: List[Dict[str, str]] = []
    texts = decision_bodies(project_root)
    retired = superseded_ids(texts)
    for stem in sorted(texts):
        if stem in retired:
            continue
        declared = decision_header(texts[stem], "Status")
        if declared.lower() in DECISION_STATUSES:
            continue
        findings.append({
            "decision": f"decisions/{stem}.md",
            "declared": declared,
            "reason": "absent" if not declared else "unrecognized",
        })
    return findings


def out_of_plan_dispositions(project_root: Path) -> Dict[str, str]:
    """``{content identity: decision relpath}`` for recorded out-of-plan requests.

    A decision line ``Out-of-plan request: sha256:<64 hex>`` records that the
    plan deliberately leaves an operator excerpt unclaimed. Decisions are the
    project's ruling record, so this is the one place a plan-level disposition
    can be authorized; task-level ``A|`` scoping never substitutes for it.

    Only a **current, locked** decision with **operator authority** authorizes.
    An ``open`` decision is a proposal, not a ruling, and a decision named in
    any other decision's ``Supersedes:`` line has been retired by a later
    ruling — whichever way that ruling went, the retired text no longer
    authorizes anything. Leaving an operator request out of the plan is the
    operator's call, so a PM decision disposes of nothing until the operator
    ratifies it.
    """
    out: Dict[str, str] = {}
    texts = decision_bodies(project_root)
    superseded = superseded_ids(texts)
    authorities = effective_authorities(texts)
    for stem in sorted(texts):
        text = texts[stem]
        if not is_current_locked_decision(stem, text, superseded):
            continue
        if authorities[stem] != OPERATOR_AUTHORITY:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith(OUT_OF_PLAN_MARKER):
                continue
            identity = stripped[len(OUT_OF_PLAN_MARKER) :].strip().split(" ", 1)[0]
            if re.fullmatch(r"sha256:[0-9a-f]{64}", identity):
                out.setdefault(identity, f"decisions/{stem}.md")
    return out


def unclaimed_scoped_excerpts(project_root: Path) -> List[Dict[str, Any]]:
    """Operator excerpts every task scopes out and no task claims or waives.

    Read across every task that declares ``Upstream trace: required``. An
    excerpt is *claimed* by an ``operator-request`` edge, *released* by a
    ``W|`` waiver (operator authority), and *scoped* by an ``A|`` record.
    Only scoped-and-never-claimed excerpts are returned, each with the
    decision that dispositions it at the plan level when one exists.
    ``plan-audit`` warns on them; ``close-audit`` blocks on the undispositioned.
    """
    from cli.commands.validate_task_readiness import _parse_headers  # noqa: F401

    project_root = Path(project_root)
    claimed: set = set()
    scoped: Dict[str, List[str]] = {}
    seen: Dict[str, str] = {}
    for status in ("open", "in-progress", "in-review", "done"):
        status_dir = project_root / "tasks" / status
        if not status_dir.is_dir():
            continue
        for task_file in sorted(status_dir.iterdir()):
            match = re.fullmatch(r"(TASK-\d{2}-\d{3})\.md", task_file.name)
            if not task_file.is_file() or not match:
                continue
            try:
                text = task_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if declaration(text) != REQUIRED:
                continue
            binding = bind(project_root, task_file, task_text=text)
            if binding.trace is None:
                continue
            trace = binding.trace
            task_excerpts = {excerpt.split(" ", 1)[-1] for excerpt in trace.excerpts}
            for excerpt in trace.excerpts:
                seen.setdefault(excerpt.split(" ", 1)[-1], excerpt)
            for record in trace.records:
                if record.type == "operator-request":
                    claimed.add(record.source_identity.split(" ", 1)[-1])
            for waiver in trace.waivers:
                claimed.add(waiver.identity.split(" ", 1)[-1])
            for record in trace.applicability:
                # Only operator excerpts carry plan-level coverage; an A|
                # record for a source or a cited decision scopes nothing the
                # plan must still claim.
                if (
                    record.applicability_class == "outside-scope"
                    and record.identity.split(" ", 1)[-1] in task_excerpts
                ):
                    scoped.setdefault(record.identity.split(" ", 1)[-1], []).append(
                        match.group(1)
                    )
    dispositions = out_of_plan_dispositions(project_root)
    out: List[Dict[str, Any]] = []
    for digest in sorted(scoped):
        if digest in claimed:
            continue
        out.append(
            {
                "identity": seen.get(digest, digest),
                "content_identity": digest,
                "tasks": sorted(set(scoped[digest])),
                "disposition": dispositions.get(digest),
            }
        )
    return out


@dataclass
class Binding:
    """A task resolved against the trace contract."""

    declaration: str
    task_path: Path
    spec_path: Optional[Path]
    trace: Optional[acceptance_trace.Trace]
    refusal: Optional[acceptance_trace.TraceRefusal]

    @property
    def enforced(self) -> bool:
        return self.declaration == REQUIRED

    @property
    def ok(self) -> bool:
        return self.refusal is None

    def as_record(self) -> Dict[str, Any]:
        record: Dict[str, Any] = {
            "declaration": self.declaration,
            "task_path": str(self.task_path),
            "spec_path": str(self.spec_path) if self.spec_path else None,
            "ok": self.ok,
        }
        if self.refusal is not None:
            record["refusal"] = {
                "code": self.refusal.code,
                "detail": self.refusal.detail,
                "identity": self.refusal.identity,
            }
        if self.trace is not None:
            record["trace"] = self.trace.as_record()
        return record


def bind(
    project_root: Path,
    task_path: Path,
    *,
    task_text: Optional[str] = None,
    enforce_bounds: bool = True,
) -> Binding:
    """Resolve and validate one task's upstream trace.

    Never raises: a structural refusal is captured on the binding so the
    caller decides which boundary it blocks at. A task that does not declare
    the contract binds to ``declaration`` alone and carries no trace.
    """
    project_root = Path(project_root)
    task_path = Path(task_path)
    text = task_text if task_text is not None else task_path.read_text(encoding="utf-8")
    declared = declaration(text)
    if declared != REQUIRED:
        block = acceptance_trace.extract_record_block(text)
        if declared == NOT_APPLICABLE and block:
            return Binding(
                declaration=declared,
                task_path=task_path,
                spec_path=None,
                trace=None,
                refusal=acceptance_trace.TraceRefusal(
                    "trace-unparseable",
                    "the task declares `Upstream trace: n/a` but carries an "
                    "`## Upstream trace` record block",
                ),
            )
        return Binding(declared, task_path, None, None, None)

    spec_path: Optional[Path] = None
    try:
        acceptance_trace.assert_conformance_anchor()
        spec_path = governing_spec_path(project_root, task_path, text)
        spec_items: List[str] = []
        if spec_path is not None:
            try:
                spec_items = acceptance_trace.spec_acceptance_items(
                    spec_path.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeDecodeError) as exc:
                raise acceptance_trace.TraceRefusal(
                    "trace-unparseable",
                    f"the governing specification is unreadable: {exc}",
                )
        task_items = acceptance_trace.task_acceptance_items(text)
        block = acceptance_trace.extract_record_block(text)
        if block is None:
            raise acceptance_trace.TraceRefusal(
                "trace-missing",
                "the task declares `Upstream trace: required` and carries no "
                f"`{acceptance_trace.TRACE_SECTION_HEADING}` section",
            )
        record_set = acceptance_trace.parse_record_set(block)
        trace = acceptance_trace.build(
            spec_acceptance=spec_items,
            task_acceptance=task_items,
            record_set=record_set,
            sources=source_identities(task_path, text),
            excerpts=excerpt_identities(project_root, task_path),
            enforce_bounds=enforce_bounds,
        )
    except acceptance_trace.TraceRefusal as refusal:
        return Binding(declared, task_path, spec_path, None, refusal)
    except request_trace.RequestRefusal as refusal:
        return Binding(
            declared,
            task_path,
            spec_path,
            None,
            acceptance_trace.TraceRefusal(
                "trace-incomplete",
                f"request evidence could not be resolved ({refusal.rule}): "
                f"{refusal.detail}",
            ),
        )
    except (OSError, UnicodeDecodeError) as exc:
        return Binding(
            declared,
            task_path,
            spec_path,
            None,
            acceptance_trace.TraceRefusal(
                "trace-unparseable", f"trace inputs are unreadable: {exc}"
            ),
        )
    return Binding(declared, task_path, spec_path, trace, None)


# ---------------------------------------------------------------------------
# Prompt and review-context seams (§ 12).
# ---------------------------------------------------------------------------
CODER_SECTION_HEADING = "## Upstream trace projection"
REVIEWER_SECTION_HEADING = "## Upstream trace provenance"

_CODER_PREAMBLE = (
    "Every material acceptance criterion below is bound to upstream authority "
    "the PM verified. `traced` means at least one typed upstream source governs "
    "it; `exempt` means the PM recorded that none does. The digest is the "
    "criterion text's identity: if the text you were given does not hash to it, "
    "the contract drifted after this trace was derived — stop and report it."
)

_REVIEWER_PREAMBLE = (
    "PM-computed and independently attributable: the assignee cannot author "
    "this block. Recompute the derivation rather than accept it. `D1` asks "
    "whether the delivered work satisfies the task and specification; `D2` asks "
    "whether the task and specification adequately satisfy the upstream sources "
    "reached through the trace. They fail independently and neither may be "
    "recorded as \"same as above\". This block is input, not an output slot: "
    "intake reads verdicts only from the review file's "
    f"`{acceptance_trace.DETERMINATION_SECTION_HEADING}` section, so record "
    "them there, in the block the review-file skeleton generates."
)

_CLOSURE_PREAMBLE = (
    "Fill in every verdict below; do not move these lines under another "
    "heading. Intake reads determinations only from this section. A passing "
    "line carries `reason:-`; a failing D1 line carries "
    "`acceptance-item-unmet`; a failing D2 line carries "
    "`upstream-intent-uncovered`, `exemption-unjustified`, or "
    "`unresolved-source-conflict`. Record an identity no criterion claims on "
    "an added `D2 task: fail reason:<source-uncovered | request-uncovered | "
    "waiver-rejected>` line. Keep the `Trace-identity:` line unchanged."
)


def coder_section(trace: acceptance_trace.Trace) -> str:
    """The assignment-seam block: complete, bounded, carrying no governance id."""
    return (
        f"{CODER_SECTION_HEADING}\n\n{_CODER_PREAMBLE}\n\n```trace-projection\n"
        + trace.coder_projection()
        + "```\n"
    )


def reviewer_section(trace: acceptance_trace.Trace) -> str:
    """The review-context seam: the full typed record set and coverage results."""
    return (
        f"{REVIEWER_SECTION_HEADING}\n\n{_REVIEWER_PREAMBLE}\n\n```trace-provenance\n"
        + trace.reviewer_projection()
        + "```\n"
    )


def closure_section(trace: acceptance_trace.Trace) -> str:
    """The review file's determination output slot, pre-populated.

    Byte-identical to ``acceptance-trace --projection determinations`` inside
    the heading intake reads, so a reviewer who fills every placeholder has
    produced an evaluable determination block without transcribing anything.
    """
    return (
        f"{acceptance_trace.DETERMINATION_SECTION_HEADING}\n\n"
        f"{_CLOSURE_PREAMBLE}\n\n```\n"
        + trace.completion_evidence(trace.determination_template())
        + "```\n"
    )


def _section_bounds(lines: List[str], heading: str) -> Optional[Tuple[int, int]]:
    start: Optional[int] = None
    for index, line in enumerate(lines):
        if line.strip() == heading:
            start = index
            continue
        if start is not None and line.startswith("## "):
            return start, index
    return (start, len(lines)) if start is not None else None


def upsert_section(text: str, heading: str, section: str) -> str:
    """Insert or replace one ``## `` section, leaving every other line alone."""
    lines = text.splitlines(keepends=True)
    bounds = _section_bounds([line.rstrip("\n") for line in lines], heading)
    block = section if section.endswith("\n") else section + "\n"
    if bounds is None:
        prefix = text if text.endswith("\n") else text + "\n"
        return prefix + ("\n" if not prefix.endswith("\n\n") else "") + block
    start, end = bounds
    return "".join(lines[:start]) + block + "".join(lines[end:])
