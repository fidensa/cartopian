"""Deterministic default paths for project-resource deliverables.

The protocol already owns task and spec naming. A supporting document — the
research findings, the design note, the evaluation — has one durable home,
``resources/``, and the PM asking the operator to name that file turned a
routine kickoff into a question the operator could not see. The default is
therefore a protocol decision: ``project:resources/<kind>/<title-slug>.md``,
derived from the plan-ref kind and the task's own title. It carries no task,
plan, spec, or requirement identifier (the ``Deliverable:`` field is
deidentified by rule), and an explicit path in the task is always the
override.

A work-root deliverable (``root:<path>``) stays operator-chosen: it changes
the product's structure, which is exactly the kind of destination choice that
belongs to the operator.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Callable, Iterable, Optional, Tuple

from cli.deidentify import IDENTIFIER_RE

#: Plan-item kinds whose work product is a durable document.
DOCUMENT_WORK_KINDS: Tuple[str, ...] = ("DESIGN", "RESEARCH")
#: Header values that ask for the protocol default.
DEFAULT_SENTINELS = frozenset({"", "default"})
MAX_SLUG_LENGTH = 60

_TITLE_ID_PREFIX_RE = re.compile(r"^TASK-[A-Za-z0-9]+-[A-Za-z0-9]+\s*:\s*")
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


def title_slug(title: str) -> str:
    """Lower-case, identifier-free, ASCII, hyphenated slug of a task title."""
    text = _TITLE_ID_PREFIX_RE.sub("", title.strip())
    text = IDENTIFIER_RE.sub(" ", text)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = _NON_SLUG_RE.sub("-", text.lower()).strip("-")
    if len(slug) > MAX_SLUG_LENGTH:
        slug = slug[:MAX_SLUG_LENGTH].rstrip("-")
    return slug or "deliverable"


def default_project_deliverable(
    kind: str,
    title: str,
    *,
    taken: Iterable[str] = (),
    exists: Optional[Callable[[str], bool]] = None,
) -> str:
    """``project:resources/<kind>/<slug>.md`` for a document-work task.

    Two tasks with the same title, a truncated or non-ASCII title, or a
    resource carried forward from an earlier plan would otherwise share one
    path and let later work overwrite earlier evidence. ``taken`` is every
    deliverable value other tasks already declare and ``exists`` tests the
    project-relative path on disk; the first free ordinal suffix (``-2``,
    ``-3``, …) is appended deterministically. An ordinal is not a governance
    identifier, so the value stays deidentified.
    """
    base = f"resources/{kind.lower()}/{title_slug(title)}"
    taken_set = {value.strip() for value in taken}

    def free(relpath: str) -> bool:
        if f"project:{relpath}" in taken_set or relpath in taken_set:
            return False
        return not (exists is not None and exists(relpath))

    candidate = f"{base}.md"
    ordinal = 2
    while not free(candidate):
        candidate = f"{base}-{ordinal}.md"
        ordinal += 1
    return f"project:{candidate}"


def _first_heading(content: str) -> str:
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _plan_ref_kind(content: str) -> str:
    for line in content.splitlines():
        if line.startswith("## "):
            break
        stripped = line.strip()
        if stripped.startswith("Plan ref:"):
            return stripped[len("Plan ref:") :].strip().partition("-")[0]
    return ""


def resolve_default(
    content: str,
    *,
    taken: Iterable[str] = (),
    exists: Optional[Callable[[str], bool]] = None,
) -> Optional[str]:
    """The default deliverable this task body would take, or ``None``.

    ``None`` when the task is not document work, or when it already names a
    deliverable (including an explicit ``n/a``, which readiness rejects for
    document work rather than silently replacing).
    """
    kind = _plan_ref_kind(content)
    header_present = False
    value = ""
    for line in content.splitlines():
        if line.startswith("## "):
            break
        stripped = line.strip()
        if stripped.startswith("Deliverable:"):
            header_present = True
            value = stripped[len("Deliverable:") :].strip()
            break
    if header_present and value.lower() not in DEFAULT_SENTINELS:
        return None
    if kind not in DOCUMENT_WORK_KINDS and value.lower() != "default":
        return None
    if not kind:
        return None
    return default_project_deliverable(
        kind, _first_heading(content), taken=taken, exists=exists
    )


def stamp_default(
    content: str,
    *,
    taken: Iterable[str] = (),
    exists: Optional[Callable[[str], bool]] = None,
) -> Tuple[str, Optional[str]]:
    """Return ``(content, stamped_value)``; stamps only when a default applies.

    The header line is replaced in place when present, otherwise inserted
    after the last header line before the first ``## `` section.
    """
    default = resolve_default(content, taken=taken, exists=exists)
    if default is None:
        return content, None
    lines = content.splitlines(keepends=True)
    end_of_headers = len(lines)
    last_header = -1
    for index, line in enumerate(lines):
        if line.startswith("## "):
            end_of_headers = index
            break
        stripped = line.strip()
        if stripped.startswith("Deliverable:"):
            newline = "\n" if line.endswith("\n") else ""
            lines[index] = f"Deliverable: {default}{newline}"
            return "".join(lines), default
        if re.match(r"^[A-Za-z][A-Za-z0-9 _/-]*?:\s", line) or re.match(
            r"^[A-Za-z][A-Za-z0-9 _/-]*?:$", stripped
        ):
            last_header = index
    insert_at = last_header + 1 if last_header >= 0 else end_of_headers
    lines.insert(insert_at, f"Deliverable: {default}\n")
    return "".join(lines), default
