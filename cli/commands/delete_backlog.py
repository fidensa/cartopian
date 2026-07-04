"""`cartopian delete-backlog <project-root> --bl-id BL-NNN`.

Mediated removal of a single backlog entry — the counterpart to
``write-backlog``. Trimming the project-root ``BACKLOG.md`` no longer requires
an operator hand-edit (which would bypass the mediated-write discipline the
backlog was built for): this command removes exactly the ``## BL-NNN — <title>``
section named by ``--bl-id`` and re-renders the whole file back through the
mediated-write primitive (``backlog`` dest_kind → the allowlisted root file
``BACKLOG.md``).

Removal is section-exact. The file preamble (including its ``Highest id issued:``
mark, which this command never touches — monotonicity by construction) and every
surviving entry round-trip byte-for-byte: this reuses ``write-backlog``'s
fence-aware section parser (a ``## BL-NNN`` heading quoted inside a code fence is
body, never a boundary) and its single assembler, so no second parser and no
whole-document rewrite touch author-controlled body text. Removing an id that is
not present, or a missing ``BACKLOG.md``, fails cleanly with a ``[guard]`` line
and a non-zero exit.

Deletion is **interlocked with promotion**: a live entry is removed only when a
governed durable artifact carries a matching ``Source: BL-NNN`` stamp (the
referent that keeps deletion from dangling), or when the operator declares the
entry abandoned with ``--discard`` (a loud, NDJSON-recorded override). Together
with ``write-task/-spec/-phase --source`` — which only stamps an entry that is
live — this makes stamp-then-delete the only ordering that executes; delete-first
(the dangling-id bug) is refused.
"""
import argparse
import re
from pathlib import Path
from typing import Optional

from cli.commands import _writers, write_backlog
from cli.mediated_write import GuardRefusal, mediated_write

# Governed durable surfaces scanned for a `Source: BL-NNN` promotion stamp. A
# stamp anywhere here is the durable artifact pointing back at the (about-to-be
# deleted) ephemeral entry — the referent that keeps deletion from dangling.
_STAMP_DIRS = (
    "tasks/open",
    "tasks/in-progress",
    "tasks/in-review",
    "tasks/done",
    "specs",
    "phases",
    "decisions",
)
_STAMP_FILES = ("IMPLEMENTATION_PLAN.md",)


def _source_stamp_re(bl_id: str) -> "re.Pattern[str]":
    return re.compile(rf"^Source:[ \t]*{re.escape(bl_id)}\b", re.MULTILINE)


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def find_source_stamp(root: Path, bl_id: str) -> Optional[Path]:
    """Return the first governed durable file carrying a ``Source: BL-NNN``
    stamp for ``bl_id``, or ``None`` if the promotion is unrecorded. Cheap,
    bounded stdlib scan over the fixed governed path set."""
    pat = _source_stamp_re(bl_id)
    for rel in _STAMP_FILES:
        path = root / rel
        if path.is_file() and pat.search(_safe_read(path)):
            return path
    for sub in _STAMP_DIRS:
        directory = root / sub
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            if pat.search(_safe_read(path)):
                return path
    return None


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "project_root",
        help="Absolute path to the Cartopian project root",
    )
    subparser.add_argument(
        "--bl-id",
        required=True,
        help="Backlog entry id to remove (grammar BL-NNN)",
    )
    subparser.add_argument(
        "--discard",
        action="store_true",
        help=(
            "Abandon the entry without a promotion stamp. Loud, recorded "
            "override of the interlock guard — for entries dropped, not promoted."
        ),
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

    # Interlock: a live entry may be removed only once a durable artifact
    # records where it went (a `Source: BL-NNN` stamp), or when the operator
    # explicitly declares it abandoned via --discard. Stamp-then-delete is the
    # only ordering that executes; delete-first (the dangling bug) is refused.
    stamp = find_source_stamp(root, bl_id)
    if stamp is None and not args.discard:
        _writers.stderr(
            "guard",
            f"undocumented-deletion: no `Source: {bl_id}` stamp in any governed "
            "artifact; stamp the promotion target via write-task/-spec/-phase "
            "--source, or pass --discard to abandon the entry",
        )
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
            "discarded": stamp is None and args.discard,
            "source_stamp": str(stamp) if stamp is not None else None,
            "path": result["path"],
            "bytes": result["bytes"],
            "entries": len(remaining),
        },
    })
    return _writers.EXIT_OK
