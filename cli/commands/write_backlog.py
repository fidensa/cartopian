"""`cartopian write-backlog <project-root> --bl-id BL-NNN --title ...` (BL-002, FR-005-family).

Structured writer giving PM/reviewer follow-up notes a **durable,
CLI-supported home** — the project-root ``BACKLOG.md`` — so they are never
parked in ``STATE.md`` (which stays canonical composed state only, under its
5KB ceiling). One entry per ``BL-NNN`` id:

- a section heading ``## BL-NNN — <title>``, followed by
- the entry body (``--content`` / ``--content-file``).

Re-issuing the same ``--bl-id`` replaces that entry's section in place;
a new id appends. The whole file is rendered back through the SPEC-01-002
mediated-write primitive (``backlog`` dest_kind → the allowlisted root file
``BACKLOG.md``) — no raw edit, no second bypass surface. An existing
``BACKLOG.md`` keeps its preamble (everything before the first entry
heading); a missing one is seeded with a minimal header.
"""
import argparse
import re
from pathlib import Path
from typing import List, Tuple

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


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    _writers.add_content_args(subparser)
    subparser.add_argument(
        "--bl-id",
        required=True,
        help="Backlog entry id, e.g. BL-001 (grammar BL-NNN)",
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
    bl_id = args.bl_id
    if not _writers.BL_ID_RE.match(bl_id):
        _writers.stderr("usage", f"--bl-id must match BL-NNN grammar; got: {bl_id!r}")
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

    new_section = _render_entry(bl_id, title, content)
    replaced = False
    for i, (sid, _text) in enumerate(sections):
        if sid == bl_id:
            sections[i] = (bl_id, new_section)
            replaced = True
            break
    if not replaced:
        sections.append((bl_id, new_section))

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
            "path": result["path"],
            "bytes": result["bytes"],
            "entry_replaced": replaced,
            "entries": len(sections),
        },
    })
    return _writers.EXIT_OK
