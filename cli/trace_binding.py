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

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
    "recorded as \"same as above\"."
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
