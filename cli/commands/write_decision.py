"""`cartopian write-decision <project-root> --dec-id DEC-NNN --title ...`.

Structured writer that records a decision **and** updates its index in one
invocation:

- writes ``decisions/DEC-NNN.md`` (the body, via ``--content`` /
  ``--content-file``), then
- updates ``decisions/INDEX.md`` — appending the matching table row, or
  replacing the existing row for the same ``DEC-NNN`` on re-issue, then
- reports the nearest live locked decisions (``cli/decision_neighbors.py``).

The neighbor rows are the one surface that tells the PM which existing
rulings sit next to the one it just recorded. They are advisory and never
change the exit code: a decision is a ruling, and detecting that two rulings
are *about* the same thing is not the same as knowing they disagree. The
rows cost a fixed three lines, are emitted only here, and add nothing to
session startup.

Both writes go through the mediated-write primitive (``decision``
dest_kind). The INDEX update is a read-modify-write of the full file rendered
back through the primitive — no raw edit, no second bypass surface. The DEC
file is written first; if it refuses, the index is left untouched.
"""
import argparse
import re
from pathlib import Path
from typing import List, Optional, Tuple

from cli import decision_neighbors, trace_binding
from cli.commands import _writers
from cli.mediated_write import GuardRefusal, mediated_write

_INDEX_HEADER = (
    "# Decisions Index\n\n"
    "| ID | Title | Date | Status | Supersedes |\n"
    "| --- | --- | --- | --- | --- |\n"
)


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    _writers.add_content_args(subparser)
    subparser.add_argument(
        "--dec-id",
        required=True,
        help="Decision id in DEC-NNN format (three-digit number)",
    )
    subparser.add_argument(
        "--title",
        required=True,
        help="Short decision title for the INDEX.md row",
    )
    subparser.add_argument(
        "--date",
        required=True,
        help="Decision date in YYYY-MM-DD form for the INDEX.md row",
    )
    subparser.add_argument(
        "--status",
        choices=trace_binding.DECISION_STATUSES,
        default=None,
        help=(
            "Decision status. Omit to take the body's own `Status:` header; "
            "supplying a value that contradicts that header is refused "
            "(default when neither declares one: locked)"
        ),
    )
    subparser.add_argument(
        "--supersedes",
        default=None,
        help=(
            "DEC-NNN this supersedes, or 'none'. Omit to take the body's own "
            "`Supersedes:` header; supplying a value that contradicts that "
            "header is refused (default when neither declares one: none)"
        ),
    )


#: Body header fields the INDEX row projects, with the value used when neither
#: the body nor the caller declares one.
_PROJECTED_FIELDS = (("Status", "status", "locked"), ("Supersedes", "supersedes", "none"))


_HEADER_LINE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 _/-]*:")


def _stamp_missing_header(content: str, field: str, value: str) -> str:
    """Insert a ``Field: value`` line into a decision's header block.

    Only ever called for a field the body does not declare, so this inserts and
    never replaces. The line goes after the last existing header line, which
    keeps the block contiguous and in template order (Date, Status,
    Supersedes); with no header block yet it goes under the H1, followed by the
    blank line the block needs to read as one.
    """
    lines = content.splitlines(keepends=True)
    block_end = next((i for i, text in enumerate(lines) if text.startswith("## ")), len(lines))
    insert_at, h1_at = None, None
    for i in range(block_end):
        stripped = lines[i].strip()
        if _HEADER_LINE_RE.match(stripped):
            insert_at = i + 1
        elif h1_at is None and lines[i].startswith("# "):
            h1_at = i
    if insert_at is not None:
        lines.insert(insert_at, f"{field}: {value}\n")
        return "".join(lines)
    at = (h1_at + 1) if h1_at is not None else 0
    block = [f"{field}: {value}\n"]
    if h1_at is not None:
        block.insert(0, "\n")
    if at < len(lines) and lines[at].strip():
        block.append("\n")
    lines[at:at] = block
    return "".join(lines)


def _equivalent(field: str, left: str, right: str) -> bool:
    """Compare two values for one projected field the way the field means them."""
    if field == "Supersedes":
        blank = {"none", "n/a", ""}
        left_n = "none" if left.strip().lower() in blank else left.strip()
        right_n = "none" if right.strip().lower() in blank else right.strip()
        return left_n == right_n
    return left.strip().lower() == right.strip().lower()


def _reconcile_header(
    content: str, field: str, supplied: Optional[str], fallback: str
) -> Tuple[str, str, Optional[str]]:
    """Agree the body header and the INDEX cell, or refuse.

    The decision file is the ruling; the INDEX row is a projection of it. When
    those two can disagree the projection stops being trustworthy, and the
    disagreement is silent — which is how a decision ends up indexed as locked
    while every reader treats it as unreadable. So: the body wins when it
    declares the field, the caller's value fills it in when the body is silent
    (and is stamped into the body so they cannot drift later), and an explicit
    contradiction is refused rather than resolved by precedence.

    Returns ``(content, resolved value, error)``.
    """
    declared = trace_binding.decision_header(content, field)
    if declared:
        if field == "Status" and declared.lower() not in trace_binding.DECISION_STATUSES:
            return content, declared, (
                f"decision-status-unrecognized: body declares {field}: {declared!r}; "
                f"a decision is {' or '.join(trace_binding.DECISION_STATUSES)} "
                "— an unrecognized status authorizes nothing"
            )
        if supplied is not None and not _equivalent(field, declared, supplied):
            return content, declared, (
                f"decision-header-conflict: body declares {field}: {declared!r} but "
                f"--{field.lower()} says {supplied!r}; the decision file is the ruling, "
                "so correct one of them rather than indexing a value the file denies"
            )
        return content, declared, None
    resolved = supplied if supplied is not None else fallback
    return _stamp_missing_header(content, field, resolved), resolved, None


def _sanitize_cell(value: str) -> str:
    """Keep table-row text on one cell: collapse newlines, escape pipes."""
    return value.replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()


def _render_index(rows: List[str]) -> str:
    body = "".join(f"{row}\n" for row in rows)
    return _INDEX_HEADER + body


def _existing_rows(index_path: Path) -> List[str]:
    """Return existing data rows (table body) from INDEX.md, if any.

    Tolerant of a missing/empty/freshly-seeded INDEX.md: returns whatever data
    rows are present, dropping the heading and the two header/separator rows.
    A data row is any line beginning with ``|`` that is not the column-header
    or separator line.
    """
    try:
        text = index_path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        lowered = stripped.lower()
        if lowered.startswith("| id ") or set(stripped) <= set("|- "):
            continue  # column header or separator
        rows.append(stripped)
    return rows


def handler(args: argparse.Namespace) -> int:
    dec_id = args.dec_id
    if not _writers.DEC_ID_RE.match(dec_id):
        _writers.stderr("usage", f"--dec-id must match DEC-NNN grammar; got: {dec_id!r}")
        return _writers.EXIT_USAGE
    if not _writers.DATE_RE.match(args.date):
        _writers.stderr("usage", f"--date must be YYYY-MM-DD; got: {args.date!r}")
        return _writers.EXIT_USAGE

    root, err = _writers.validated_root(args.project_root)
    if err is not None:
        _writers.stderr("usage", err)
        return _writers.EXIT_USAGE

    content, cerr = _writers.resolve_content(args)
    if cerr is not None:
        _writers.stderr("usage", cerr)
        return _writers.EXIT_USAGE

    # Reconcile before a byte lands: the body and its INDEX row are written in
    # the same breath, so they have no excuse to disagree afterwards.
    # --content-file always yields bytes; a decision body is markdown, so decode
    # once here and let both the reconciliation and the write see the same text.
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            _writers.stderr("usage", f"decision body must be UTF-8 text: {exc}")
            return _writers.EXIT_USAGE
    resolved: dict = {}
    for field, attr, fallback in _PROJECTED_FIELDS:
        content, value, err = _reconcile_header(content, field, getattr(args, attr), fallback)
        if err is not None:
            _writers.stderr("guard", err)
            return _writers.EXIT_FAIL
        resolved[attr] = value

    dec_filename = f"{dec_id}.md"
    matches = _writers.identifier_files(root / "decisions", dec_id)
    if len(matches) > 1:
        _writers.stderr(
            "guard",
            f"decision-id-collision: {dec_id} resolves to multiple files: "
            + ", ".join(str(path) for path in matches),
        )
        return _writers.EXIT_FAIL
    if matches and matches[0].name != dec_filename:
        _writers.stderr(
            "guard",
            f"artifact-name-migration-required: {matches[0]} must be migrated to {dec_filename}",
        )
        return _writers.EXIT_FAIL

    # 1. Write the DEC body first. If it refuses, the index stays untouched.
    try:
        dec_result = mediated_write(root, "decision", dec_filename, content)
    except GuardRefusal as refusal:
        _writers.stderr("guard", f"{refusal.rule}: {refusal.detail}")
        return _writers.EXIT_FAIL

    # 2. Read-modify-write INDEX.md through the same primitive. Replace an
    #    existing row for this DEC id (re-issue), else append.
    index_path = Path(root) / "decisions" / "INDEX.md"
    title = _sanitize_cell(args.title)
    supersedes = _sanitize_cell(resolved["supersedes"])
    new_row = (
        f"| [{dec_id}]({dec_filename}) | {title} | {args.date} | "
        f"{resolved['status']} | {supersedes} |"
    )

    rows = _existing_rows(index_path)
    row_prefix = f"| [{dec_id}]("
    replaced = False
    for i, row in enumerate(rows):
        if row.startswith(row_prefix):
            rows[i] = new_row
            replaced = True
            break
    if not replaced:
        rows.append(new_row)

    try:
        index_result = mediated_write(root, "decision", "INDEX.md", _render_index(rows))
    except GuardRefusal as refusal:
        _writers.stderr(
            "guard",
            f"{refusal.rule}: {refusal.detail} (DEC body written: {dec_result['path']})",
        )
        return _writers.EXIT_FAIL

    # Advisory, and deliberately last: a failure to rank neighbors must never
    # cost a caller the decision it already wrote to disk.
    try:
        neighbors = decision_neighbors.neighbors(root, content, exclude_id=dec_id)
    except Exception:  # pragma: no cover - advisory surface, never fatal
        neighbors = []

    _writers.emit_record({
        "action": "write-decision",
        "details": {
            "dest_kind": "decision",
            "dec_id": dec_id,
            "neighbors": neighbors,
            "decision_path": dec_result["path"],
            "decision_bytes": dec_result["bytes"],
            "index_path": index_result["path"],
            "index_row_replaced": replaced,
            "index_rows": len(rows),
        },
    })
    return _writers.EXIT_OK
