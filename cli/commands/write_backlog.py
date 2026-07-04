"""`cartopian write-backlog <project-root> --bl-id BL-NNN --title ...`.

Structured writer giving PM/reviewer follow-up notes a **durable,
CLI-supported home** — the project-root ``BACKLOG.md`` — so they are never
parked in ``STATE.md`` (which stays canonical composed state only, under its
5KB ceiling). One entry per ``BL-NNN`` id:

- a section heading ``## BL-NNN — <title>``, followed by
- the entry body (``--content`` / ``--content-file``).

Ids are **writer-allocated, never caller-supplied**. Omitting ``--bl-id`` mints
the next id from the ``Highest id issued:`` preamble field (mark + 1) and bumps
that field; deleted ids are never reissued. Supplying ``--bl-id`` is legal only
to revise a *live* entry in place. The whole file is rendered back through the
mediated-write primitive (``backlog`` dest_kind → the allowlisted root file
``BACKLOG.md``) — no raw edit, no second bypass surface. An existing
``BACKLOG.md`` keeps its preamble (everything before the first entry heading); a
missing one is seeded with a minimal header.
"""
import argparse
import re
from pathlib import Path
from typing import List, Optional, Tuple

from cli.commands import _writers
from cli.mediated_write import GuardRefusal, mediated_write

_DEFAULT_PREAMBLE = (
    "# Backlog\n\n"
    "Durable follow-up work that should not live in `STATE.md`. Use this file "
    "for actionable tech debt, process debt, and protocol-hardening items that "
    "are not yet promoted into a task or roadmap entry.\n"
)

_ENTRY_HEADING_RE = re.compile(r"^## (BL-\d{3})\b")
# Fenced-code-block delimiters (``` or ~~~, optionally indented up to three
# spaces per CommonMark). Heading detection must ignore fenced lines so an
# entry body that QUOTES a `## BL-NNN` heading inside a code fence is never
# misread as a section boundary (which would shear the entry on round-trip).
_FENCE_RE = re.compile(r"^ {0,3}(```|~~~)")

# The high-water mark: a visible preamble field, owned exclusively by the
# mediated writers, that records the highest id ever issued. New-entry ids are
# allocated from this field (mark + 1), never caller-supplied, so a deleted
# entry can never be reissued — monotonicity by construction. It travels with
# the file (no machine-local counter, no sidecar split-brain state) and rides
# through the byte-for-byte preamble preservation both writers already provide.
_MARK_RE = re.compile(r"^Highest id issued:[ \t]*(BL-\d{3})[ \t]*$", re.MULTILINE)


def _id_num(bl_id: str) -> int:
    """``"BL-019"`` -> ``19``. Assumes BL-NNN grammar already validated."""
    return int(bl_id[3:])


def _format_id(num: int) -> str:
    return f"BL-{num:03d}"


def _live_ids(sections: List[Tuple[str, str]]) -> List[int]:
    return [_id_num(sid) for sid, _ in sections]


def live_entry_ids(root: Path) -> List[int]:
    """Numeric ids of the entries currently live in ``<root>/BACKLOG.md``
    (empty when the file is absent). The promotion writers use this to verify a
    ``--source`` id names something that actually exists before stamping it."""
    try:
        existing = (root / "BACKLOG.md").read_text(encoding="utf-8")
    except OSError:
        return []
    _preamble, sections = _split_sections(existing)
    return _live_ids(sections)


def _read_mark(preamble: str) -> Optional[int]:
    """Return the recorded high-water number, or ``None`` for a legacy file
    that predates the field (the one permitted self-heal path)."""
    m = _MARK_RE.search(preamble)
    return _id_num(m.group(1)) if m else None


def _write_mark(preamble: str, num: int) -> str:
    """Set the ``Highest id issued:`` field to ``num``, preserving the rest of
    the preamble byte-for-byte. Replaces the line in place when present, else
    inserts it as its own paragraph directly under the first H1."""
    line = f"Highest id issued: {_format_id(num)}"
    if _MARK_RE.search(preamble):
        return _MARK_RE.sub(line, preamble, count=1)
    lines = preamble.splitlines(keepends=True)
    insert_at = 0
    for i, text in enumerate(lines):
        if text.startswith("# "):
            insert_at = i + 1
            break
    lines.insert(insert_at, f"\n{line}\n")
    return "".join(lines)


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    _writers.add_content_args(subparser)
    subparser.add_argument(
        "--bl-id",
        default=None,
        help=(
            "Backlog entry id (BL-NNN grammar). Omit to allocate a fresh id; "
            "supply one only to revise an existing (live) entry in place."
        ),
    )
    subparser.add_argument(
        "--title",
        required=True,
        help="Short entry title for the `## BL-NNN — <title>` heading",
    )


def _split_sections(text: str) -> Tuple[str, List[Tuple[str, str]]]:
    """Return ``(preamble, [(bl_id, section_text), ...])``.

    A section runs from its ``## BL-NNN`` heading line up to the next entry
    heading (or EOF). The preamble is everything before the first entry
    heading and is preserved verbatim, so hand-authored context paragraphs
    survive.

    Heading detection is **fence-aware**: lines inside a fenced code block
    (``` / ~~~ delimiters) never count as section boundaries, so an entry body
    that quotes another entry's heading inside a fence round-trips intact. An
    unclosed fence degrades conservatively (everything after it stays in the
    current section — nothing is sheared). The one inherent ambiguity left is
    a *bare, unfenced* body line that exactly matches the ``## BL-NNN``
    heading grammar at column 0; quote such lines in a fence (or indent them)
    to keep them inside the entry.
    """
    lines = text.splitlines(keepends=True)
    boundaries: List[Tuple[int, str]] = []  # (line index, bl_id)
    in_fence = False
    for i, line in enumerate(lines):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _ENTRY_HEADING_RE.match(line)
        if m:
            boundaries.append((i, m.group(1)))
    if not boundaries:
        return text, []
    preamble = "".join(lines[: boundaries[0][0]])
    sections: List[Tuple[str, str]] = []
    for j, (start, bl_id) in enumerate(boundaries):
        end = boundaries[j + 1][0] if j + 1 < len(boundaries) else len(lines)
        sections.append((bl_id, "".join(lines[start:end])))
    return preamble, sections


def _render_entry(bl_id: str, title: str, body: str) -> str:
    body = body.strip("\n")
    return f"## {bl_id} — {title}\n\n{body}\n"


def _assemble(preamble: str, sections: List[Tuple[str, str]]) -> str:
    """Render ``(preamble, sections)`` back to file text.

    Assemble structurally: exactly one blank line between the preamble and the
    first entry, and between entries. Only the EDGES of each block are
    normalized (trailing newlines trimmed before the join); the interior of
    every section — author-controlled body text — is preserved byte-for-byte,
    so the mediated write is content-preserving. No whole-document regex or
    replace passes run over body content. Both ``write-backlog`` (revise) and
    ``delete-backlog`` (remove) round-trip through this single assembler, so an
    entry removal leaves every surviving entry byte-identical.
    """
    parts: List[str] = []
    if preamble.strip():
        parts.append(preamble.rstrip("\n"))
    parts.extend(section.rstrip("\n") for _sid, section in sections)
    return "\n\n".join(parts) + "\n"


def _sanitize_title(value: str) -> str:
    """Keep the heading on one line."""
    return " ".join(value.replace("\r", " ").replace("\n", " ").split()).strip()


def handler(args: argparse.Namespace) -> int:
    supplied_id = args.bl_id
    if supplied_id is not None and not _writers.BL_ID_RE.match(supplied_id):
        _writers.stderr(
            "usage", f"--bl-id must match BL-NNN grammar; got: {supplied_id!r}"
        )
        return _writers.EXIT_USAGE
    title = _sanitize_title(args.title)
    if not title:
        _writers.stderr("usage", "--title must be non-empty")
        return _writers.EXIT_USAGE

    root, err = _writers.validated_root(args.project_root)
    if err is not None:
        _writers.stderr("usage", err)
        return _writers.EXIT_USAGE

    content, cerr = _writers.resolve_content(args)
    if cerr is not None:
        _writers.stderr("usage", cerr)
        return _writers.EXIT_USAGE
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8")
        except UnicodeDecodeError:
            _writers.stderr("usage", "entry body must be valid UTF-8")
            return _writers.EXIT_USAGE

    backlog_path = Path(root) / "BACKLOG.md"
    try:
        existing = backlog_path.read_text(encoding="utf-8")
    except OSError:
        existing = ""

    if existing.strip():
        preamble, sections = _split_sections(existing)
    else:
        preamble, sections = _DEFAULT_PREAMBLE, []

    # Reconcile the high-water mark before allocating. `max_live` is the highest
    # id still present; the mark must never sit below it.
    live = _live_ids(sections)
    max_live = max(live) if live else 0
    mark = _read_mark(preamble)
    if mark is None:
        # Legacy file predating the field: the one permitted self-heal — adopt
        # the highest live id (or 0) and record it on this write.
        mark = max_live
    elif mark < max_live:
        # Only a raw hand-edit can drop the mark below a live id; refuse rather
        # than compound the corruption (move-task guard posture).
        _writers.stderr(
            "guard",
            "backlog-mark-regressed: Highest id issued is "
            f"{_format_id(mark)} but a live entry is {_format_id(max_live)}; "
            "BACKLOG.md was hand-edited",
        )
        return _writers.EXIT_FAIL

    if supplied_id is None:
        # New entry: the writer allocates. Never reuse — always mark + 1.
        bl_id = _format_id(mark + 1)
        mark += 1
        allocated = True
        sections.append((bl_id, _render_entry(bl_id, title, content)))
        replaced = False
    else:
        # Caller-supplied id is legal only to revise a live entry in place;
        # inventing new ids is exactly the ambient authority this design removes.
        bl_id = supplied_id
        if _id_num(bl_id) not in live:
            _writers.stderr(
                "guard",
                f"backlog-id-not-live: --bl-id {bl_id} names no live entry; "
                "omit --bl-id to allocate a fresh id, or name an existing entry "
                "to revise",
            )
            return _writers.EXIT_FAIL
        allocated = False
        new_section = _render_entry(bl_id, title, content)
        for i, (sid, _text) in enumerate(sections):
            if sid == bl_id:
                sections[i] = (bl_id, new_section)
                break
        replaced = True

    preamble = _write_mark(preamble, mark)
    rendered = _assemble(preamble, sections)

    try:
        result = mediated_write(root, "backlog", "BACKLOG.md", rendered)
    except GuardRefusal as refusal:
        _writers.stderr("guard", f"{refusal.rule}: {refusal.detail}")
        return _writers.EXIT_FAIL

    _writers.emit_record({
        "action": "write-backlog",
        "details": {
            "dest_kind": "backlog",
            "bl_id": bl_id,
            "allocated": allocated,
            "highest_id_issued": _format_id(mark),
            "path": result["path"],
            "bytes": result["bytes"],
            "entry_replaced": replaced,
            "entries": len(sections),
        },
    })
    return _writers.EXIT_OK
