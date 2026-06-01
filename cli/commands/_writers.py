"""Shared scaffolding for the FR-005 structured artifact-writer commands (SPEC-01-003).

Every PM-facing writer (``write-requirements``, ``write-plan``, ``write-task``,
…) is a thin typed front-end over the SPEC-01-002 mediated-write primitive
(:mod:`cli.mediated_write`). This module factors out the surface they share:

- project-root validation (absolute, real directory),
- artifact-body acquisition (``--content`` / ``--content-file``),
- the single :func:`cli.mediated_write.mediated_write` call,
- the FR-014 NDJSON success record,
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

# Shared id / slug grammars (kept in sync with the existing FR-004 commands:
# move_task, delete_prompt, compose_state).
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
TASK_ID_RE = re.compile(r"^TASK-\d{2}-\d{3}$")
SPEC_ID_RE = re.compile(r"^SPEC-\d{2}-\d{3}$")
PHASE_ID_RE = re.compile(r"^PHASE-\d{2}-[a-z0-9][a-z0-9-]*$")
DEC_ID_RE = re.compile(r"^DEC-\d{3}$")
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
    """Validate, write through the primitive, and emit the FR-014 record.

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
