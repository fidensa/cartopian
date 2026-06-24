"""`cartopian write-decision <project-root> --dec-id DEC-NNN --slug ... --title ...`.

Structured writer that records a decision **and** updates its index in one
invocation:

- writes ``decisions/DEC-NNN-slug.md`` (the body, via ``--content`` /
  ``--content-file``), then
- updates ``decisions/INDEX.md`` — appending the matching table row, or
  replacing the existing row for the same ``DEC-NNN`` on re-issue.

Both writes go through the mediated-write primitive (``decision``
dest_kind). The INDEX update is a read-modify-write of the full file rendered
back through the primitive — no raw edit, no second bypass surface. The DEC
file is written first; if it refuses, the index is left untouched.
"""
import argparse
from pathlib import Path
from typing import List

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
        "--slug",
        required=True,
        help="Kebab-case slug for the filename (DEC-NNN-<slug>.md)",
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
        choices=("locked", "open"),
        default="locked",
        help="Decision status for the INDEX.md row (default: locked)",
    )
    subparser.add_argument(
        "--supersedes",
        default="none",
        help="DEC-NNN this supersedes, or 'none' (default: none)",
    )


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
    slug = args.slug
    if not _writers.DEC_ID_RE.match(dec_id):
        _writers.stderr("usage", f"--dec-id must match DEC-NNN grammar; got: {dec_id!r}")
        return _writers.EXIT_USAGE
    if not _writers.SLUG_RE.match(slug):
        _writers.stderr(
            "usage", f"--slug must be kebab-case [a-z0-9][a-z0-9-]*; got: {slug!r}"
        )
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

    dec_filename = f"{dec_id}-{slug}.md"

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
    supersedes = _sanitize_cell(args.supersedes)
    new_row = (
        f"| [{dec_id}]({dec_filename}) | {title} | {args.date} | "
        f"{args.status} | {supersedes} |"
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

    _writers.emit_record({
        "action": "write-decision",
        "details": {
            "dest_kind": "decision",
            "dec_id": dec_id,
            "decision_path": dec_result["path"],
            "decision_bytes": dec_result["bytes"],
            "index_path": index_result["path"],
            "index_row_replaced": replaced,
            "index_rows": len(rows),
        },
    })
    return _writers.EXIT_OK
