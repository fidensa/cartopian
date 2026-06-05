"""`cartopian delete-backlog <project-root> --bl-id BL-NNN` (BL-002, FR-005-family).

Mediated removal of a single backlog entry — the counterpart to
``write-backlog``. Trimming the project-root ``BACKLOG.md`` no longer requires
an operator hand-edit (which would bypass the mediated-write discipline the
backlog was built for): this command removes exactly the ``## BL-NNN — <title>``
section named by ``--bl-id`` and re-renders the whole file back through the
SPEC-01-002 mediated-write primitive (``backlog`` dest_kind → the allowlisted
root file ``BACKLOG.md``).

Removal is section-exact. The file preamble and every surviving entry round-trip
byte-for-byte: this reuses ``write-backlog``'s fence-aware section parser (a
``## BL-NNN`` heading quoted inside a code fence is body, never a boundary) and
its single assembler, so no second parser and no whole-document rewrite touch
author-controlled body text. Removing an id that is not present, or a missing
``BACKLOG.md``, fails cleanly with a ``[guard]`` line and a non-zero exit.
"""
import argparse
from pathlib import Path

from cli.commands import _writers, write_backlog
from cli.mediated_write import GuardRefusal, mediated_write


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "project_root",
        help="Absolute path to the Cartopian project root",
    )
    subparser.add_argument(
        "--bl-id",
        required=True,
        help="Backlog entry id to remove, e.g. BL-001 (grammar BL-NNN)",
    )


def handler(args: argparse.Namespace) -> int:
    bl_id = args.bl_id
    if not _writers.BL_ID_RE.match(bl_id):
        _writers.stderr("usage", f"--bl-id must match BL-NNN grammar; got: {bl_id!r}")
        return _writers.EXIT_USAGE

    root, err = _writers.validated_root(args.project_root)
    if err is not None:
        _writers.stderr("usage", err)
        return _writers.EXIT_USAGE

    backlog_path = Path(root) / "BACKLOG.md"
    try:
        existing = backlog_path.read_text(encoding="utf-8")
    except OSError:
        _writers.stderr("guard", f"no BACKLOG.md to trim at project root: {backlog_path}")
        return _writers.EXIT_FAIL

    preamble, sections = write_backlog._split_sections(existing)
    remaining = [(sid, text) for sid, text in sections if sid != bl_id]
    if len(remaining) == len(sections):
        _writers.stderr("guard", f"no backlog entry {bl_id} in {backlog_path}")
        return _writers.EXIT_FAIL

    rendered = write_backlog._assemble(preamble, remaining)

    try:
        result = mediated_write(root, "backlog", "BACKLOG.md", rendered)
    except GuardRefusal as refusal:
        _writers.stderr("guard", f"{refusal.rule}: {refusal.detail}")
        return _writers.EXIT_FAIL

    _writers.emit_record({
        "action": "delete-backlog",
        "details": {
            "dest_kind": "backlog",
            "bl_id": bl_id,
            "path": result["path"],
            "bytes": result["bytes"],
            "entries": len(remaining),
        },
    })
    return _writers.EXIT_OK
