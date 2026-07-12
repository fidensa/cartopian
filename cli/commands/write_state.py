"""`cartopian write-state <project-root> [--note ...]`.

Structured writer for ``STATE.md``. When plan artifacts exist, the canonical
body (Current phase / Active work / Open work / What to do next) is composed
in-process from filesystem facts — the PM never authors or round-trips it.
The only PM-authored input is the bounded ``## Situation`` section, supplied
as discrete ``--note`` lines for facts that are true, non-derivable from the
filesystem/config/protocol, and change what the next session does.

Notes have a one-delivery TTL: every write starts from zero notes, and a
``--note`` byte-identical to one already in ``STATE.md`` is refused — the fact
must be promoted (``write-backlog``, ``write-decision``), dropped, or
consciously restated. ``plan-audit`` blocks while notes are present, so a
delivered note cannot be skimmed past.

The no-plan project (post-closeout, pre-plan) has nothing to compose from, so
``--content`` / ``--content-file`` remain legal there — and only there. The
5KB ceiling from the close/run-task skills applies to every final body.
"""
import argparse
from pathlib import Path
from typing import List, Optional, Tuple

from cli.commands import _writers
from cli.commands.compose_state import (
    _has_plan_artifacts,
    _load_project_config,
    compose_record,
)
from cli.commands.resolve_config import _CliError, _require_project_keys

# STATE.md ceiling — "under 5KB" per skills/close-plan.md and skills/run-task.md.
STATE_MAX_BYTES = 5 * 1024

# Situation-section bounds: a note is a short fact, not a note-taking surface.
NOTE_MAX_COUNT = 5
NOTE_MAX_CHARS = 200
SITUATION_MAX_BYTES = 1024
SITUATION_HEADING = "## Situation"


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    _writers.add_content_args(subparser)
    subparser.add_argument(
        "--note",
        action="append",
        default=None,
        dest="notes",
        metavar="NOTE",
        help=(
            "Situation note for the next session (repeatable, max "
            f"{NOTE_MAX_COUNT}, each a single line of at most {NOTE_MAX_CHARS} "
            "chars). Only for facts about current project state that are not "
            "derivable from the filesystem, config, or protocol AND change "
            "what the next session does. One-delivery TTL: every write starts "
            "from zero notes; a note byte-identical to one already in "
            "STATE.md is refused — promote it (write-backlog, write-decision), "
            "drop it, or restate it."
        ),
    )


def existing_notes(project_root: Path) -> List[str]:
    """Return the bullet texts under ``## Situation`` in the current STATE.md."""
    state_path = project_root / "STATE.md"
    if not state_path.is_file():
        return []
    try:
        text = state_path.read_text(encoding="utf-8")
    except OSError:
        return []
    notes: List[str] = []
    in_section = False
    for line in text.splitlines():
        if line.startswith("## ") or line.startswith("# "):
            in_section = line.strip() == SITUATION_HEADING
        elif in_section and line.startswith("- "):
            notes.append(line[2:].strip())
    return notes


def _validate_notes(
    notes: List[str], project_root: Path
) -> Optional[Tuple[str, str]]:
    """Return ``(prefix, message)`` on the first violated note bound, else None."""
    if len(notes) > NOTE_MAX_COUNT:
        return (
            "guard",
            f"too-many-notes: {len(notes)} notes given; ceiling is "
            f"{NOTE_MAX_COUNT} — STATE.md is a handoff surface, not a notebook",
        )
    for note in notes:
        if not note.strip():
            return ("usage", "empty --note; a note must state a fact")
        if "\n" in note or "\r" in note:
            return (
                "usage",
                f"multi-line --note; a note is a single line: {note[:60]!r}...",
            )
        if len(note) > NOTE_MAX_CHARS:
            return (
                "guard",
                f"note-too-long: {len(note)} chars; ceiling is {NOTE_MAX_CHARS} "
                f"— if it needs more room it belongs in BACKLOG.md or a decision",
            )
    delivered = set(existing_notes(project_root))
    for note in notes:
        if note.strip() in delivered:
            return (
                "guard",
                "note-carry-forward: this note was already delivered to this "
                f"session: {note.strip()[:80]!r}. Promote it (write-backlog, "
                "write-decision) or drop it; verbatim re-pass is refused",
            )
    section = _render_situation(notes)
    if len(section.encode("utf-8")) > SITUATION_MAX_BYTES:
        return (
            "guard",
            f"situation-too-large: rendered Situation section is "
            f"{len(section.encode('utf-8'))} bytes; ceiling is "
            f"{SITUATION_MAX_BYTES} bytes",
        )
    return None


def _render_situation(notes: List[str]) -> str:
    bullets = "\n".join(f"- {note.strip()}" for note in notes)
    return f"{SITUATION_HEADING}\n\n{bullets}"


def handler(args: argparse.Namespace) -> int:
    root, err = _writers.validated_root(args.project_root)
    if err is not None:
        _writers.stderr("usage", err)
        return _writers.EXIT_USAGE

    notes = args.notes or []
    has_plan = _has_plan_artifacts(root)
    content_given = args.content is not None or args.content_file is not None

    if has_plan:
        if content_given:
            _writers.stderr(
                "guard",
                "state-body-is-composed: this project has plan artifacts, so "
                "the STATE.md body is composed from the filesystem — do not "
                "pass --content/--content-file; pass --note for "
                "session-critical non-derivable facts",
            )
            return _writers.EXIT_FAIL
        nerr = _validate_notes(notes, root)
        if nerr is not None:
            _writers.stderr(*nerr)
            return _writers.EXIT_FAIL if nerr[0] == "guard" else _writers.EXIT_USAGE
        try:
            project_cfg = _load_project_config(root)
            project_name = _require_project_keys(
                project_cfg, root / "cartopian.toml"
            )[1]
        except _CliError as cli_err:
            _writers.stderr("error", cli_err.message)
            return cli_err.exit_code
        record = compose_record(root, project_name)
        content = record["rendered_body"]
        if notes:
            content = f"{content}\n\n{_render_situation(notes)}"
        content += "\n" if not content.endswith("\n") else ""
    else:
        if notes:
            _writers.stderr(
                "guard",
                "notes-require-plan: --note rides the composed body; this "
                "project has no plan artifacts, so author the full no-plan "
                "body via --content/--content-file instead",
            )
            return _writers.EXIT_FAIL
        content, cerr = _writers.resolve_content(args)
        if cerr is not None:
            _writers.stderr("usage", cerr)
            return _writers.EXIT_USAGE

    data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
    if len(data) > STATE_MAX_BYTES:
        _writers.stderr(
            "guard",
            f"state-too-large: STATE.md body is {len(data)} bytes; "
            f"ceiling is {STATE_MAX_BYTES} bytes (5KB)",
        )
        return _writers.EXIT_FAIL

    return _writers.perform_write(
        args,
        action="write-state",
        dest_kind="state",
        relative_target="STATE.md",
        content=content,
        extra_details={
            "ceiling_bytes": STATE_MAX_BYTES,
            "mode": "composed" if has_plan else "no-plan",
            "notes": len(notes),
        },
    )
