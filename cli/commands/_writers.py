"""Shared scaffolding for the structured artifact-writer commands.

Every PM-facing writer (``write-requirements``, ``write-plan``, ``write-task``,
…) is a thin typed front-end over the mediated-write primitive
(:mod:`cli.mediated_write`). This module factors out the surface they share:

- project-root validation (absolute, real directory),
- artifact-body acquisition (``--content`` / ``--content-file``),
- the single :func:`cli.mediated_write.mediated_write` call,
- the NDJSON success record,
- ``GuardRefusal`` → ``[guard] <rule>: <detail>`` stderr translation.

The PM never supplies a free-form destination: each command resolves its own
allowlisted ``(dest_kind, relative_target)`` from structured inputs (ids,
slugs) and the primitive enforces every write-safety rule. Re-issuing a writer
overwrites in place (the in-place revision semantics for ``request-changes``,
register G8).
"""
import argparse
import os
import re
import sys
from pathlib import Path
from typing import Optional, Tuple, Union

from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE
from cli.mediated_write import GuardRefusal, mediated_write

# Shared id / slug grammars (kept in sync with the existing lifecycle commands:
# move_task, delete_prompt, compose_state).
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
TASK_ID_RE = re.compile(r"^TASK-\d{2}-\d{3}$")
SPEC_ID_RE = re.compile(r"^SPEC-\d{2}-\d{3}$")
PHASE_ID_RE = re.compile(r"^PHASE-\d{2}-[a-z0-9][a-z0-9-]*$")
DEC_ID_RE = re.compile(r"^DEC-\d{3}$")
BL_ID_RE = re.compile(r"^BL-\d{3}$")
PROMPT_ID_RE = re.compile(
    r"^PROMPT-(?:\d{2}-\d{3}|PLAN-\d{3}(?:-[a-z0-9][a-z0-9-]*)?)$"
)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def stderr(prefix: str, msg: str) -> None:
    sys.stderr.write(f"[{prefix}] {msg}\n")


def add_content_args(subparser: argparse.ArgumentParser) -> None:
    """Bind the project-root positional and the artifact-body options.

    Every writer takes the project root (the allowlist anchor) plus exactly
    one body source. The destination subtree is implied by the verb, never a
    free-form path argument.
    """
    subparser.add_argument(
        "project_root",
        help="Absolute path to the Cartopian project root",
    )
    subparser.add_argument(
        "--content",
        default=None,
        help="Literal artifact body (UTF-8). Mutually exclusive with --content-file.",
    )
    subparser.add_argument(
        "--content-file",
        default=None,
        help="Path to a file whose bytes become the artifact body.",
    )


def resolve_content(args: argparse.Namespace) -> Tuple[Optional[Union[str, bytes]], Optional[str]]:
    """Return ``(content, error)`` from ``--content`` / ``--content-file``.

    Exactly one source must be given. ``error`` is a usage string when the
    inputs are missing, doubled, or unreadable; ``content`` is ``None`` then.
    """
    content = getattr(args, "content", None)
    content_file = getattr(args, "content_file", None)
    if content is not None and content_file is not None:
        return None, "pass exactly one of --content / --content-file"
    if content is not None:
        return content, None
    if content_file is not None:
        try:
            with open(content_file, "rb") as fh:
                return fh.read(), None
        except OSError as exc:
            return None, f"cannot read --content-file {content_file}: {exc.strerror or exc}"
    return None, "missing artifact body: pass --content or --content-file"


_STAMP_PLAN_RE = re.compile(r"^Plan refs?:")


def stamp_header_field(content: str, field: str, value: str) -> str:
    """Insert-or-replace a ``Field: value`` line in the artifact's top header
    block, rendering the line ourselves so it cannot be forged in body text.

    The header block is the run of lines before the first ``## `` section
    header (the same boundary ``validate_task_readiness._parse_headers`` uses).
    An existing ``field`` line in that block is replaced in place; otherwise the
    line is inserted immediately after the ``Plan ref(s):`` line when present,
    else directly under the H1 title.
    """
    line = f"{field}: {value}"
    lines = content.splitlines(keepends=True)
    block_end = len(lines)
    for i, text in enumerate(lines):
        if text.startswith("## "):
            block_end = i
            break

    field_re = re.compile(rf"^{re.escape(field)}:")
    for i in range(block_end):
        if field_re.match(lines[i].strip("\n")):
            newline = "\n" if lines[i].endswith("\n") else ""
            lines[i] = line + newline
            return "".join(lines)

    insert_at, h1_idx = None, None
    for i in range(block_end):
        if _STAMP_PLAN_RE.match(lines[i].strip("\n")):
            insert_at = i + 1
            break
        if h1_idx is None and lines[i].startswith("# "):
            h1_idx = i
    if insert_at is None:
        insert_at = (h1_idx + 1) if h1_idx is not None else 0
    lines.insert(insert_at, line + "\n")
    return "".join(lines)


def add_source_arg(subparser: argparse.ArgumentParser) -> None:
    """Bind the ``--source BL-NNN`` promotion flag for artifact writers whose
    output can be a backlog-promotion target (task, spec, phase)."""
    subparser.add_argument(
        "--source",
        default=None,
        help=(
            "Backlog entry (BL-NNN) this artifact is promoted from. The writer "
            "verifies the entry is live and stamps `Source: BL-NNN` into the "
            "header itself — a hand-typed body line does not count."
        ),
    )


def apply_source_stamp(
    args: argparse.Namespace,
    root: Path,
    content: Union[str, bytes],
) -> Tuple[Union[str, bytes], Optional[str], Optional[Tuple[str, str]]]:
    """Honour ``--source`` when present: validate the id, verify it names a live
    backlog entry, and stamp ``Source: BL-NNN`` into the header block.

    Returns ``(content, source_id, error)`` where ``error`` is ``None`` or a
    ``(prefix, message)`` pair ready for :func:`stderr` (``usage`` for grammar,
    ``guard`` for a non-live referent). ``content`` is unchanged when no
    ``--source`` was given.
    """
    bl_id = getattr(args, "source", None)
    if bl_id is None:
        return content, None, None
    if not BL_ID_RE.match(bl_id):
        return content, None, ("usage", f"--source must match BL-NNN grammar; got: {bl_id!r}")
    # Lazy import: write_backlog imports this module, so importing it here (not
    # at module load) keeps the dependency one-directional.
    from cli.commands import write_backlog

    if int(bl_id[3:]) not in set(write_backlog.live_entry_ids(root)):
        return content, None, (
            "guard",
            f"source-entry-not-live: --source {bl_id} names no live backlog "
            "entry in BACKLOG.md; only a live entry can be promoted",
        )
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8")
        except UnicodeDecodeError:
            return content, None, ("usage", "artifact body must be valid UTF-8 to stamp --source")
    return stamp_header_field(content, "Source", bl_id), bl_id, None


def validated_root(raw: str) -> Tuple[Optional[Path], Optional[str]]:
    """Return ``(project_root, error)``; root must be an absolute directory."""
    if not isinstance(raw, str) or not Path(raw).is_absolute():
        return None, f"project_root must be an absolute path; got: {raw!r}"
    root = Path(os.path.normpath(raw))
    if not root.is_dir():
        return None, f"project_root is not a directory: {raw}"
    return root, None


def perform_write(
    args: argparse.Namespace,
    *,
    action: str,
    dest_kind: str,
    relative_target: str,
    content: Optional[Union[str, bytes]] = None,
    extra_details: Optional[dict] = None,
) -> int:
    """Validate, write through the primitive, and emit the NDJSON success record.

    ``content`` may be supplied by the caller (e.g. an index re-render); when
    ``None`` it is taken from ``--content`` / ``--content-file``. Returns the
    process exit code; refusals surface a ``[guard]`` line and write nothing.
    """
    root, err = validated_root(args.project_root)
    if err is not None:
        stderr("usage", err)
        return EXIT_USAGE

    if content is None:
        content, cerr = resolve_content(args)
        if cerr is not None:
            stderr("usage", cerr)
            return EXIT_USAGE

    try:
        result = mediated_write(root, dest_kind, relative_target, content)
    except GuardRefusal as refusal:
        stderr("guard", f"{refusal.rule}: {refusal.detail}")
        return EXIT_FAIL

    details = {
        "dest_kind": dest_kind,
        "relative_target": relative_target,
        "path": result["path"],
        "bytes": result["bytes"],
    }
    if extra_details:
        details.update(extra_details)
    emit_record({"action": action, "details": details})
    return EXIT_OK
