"""`cartopian write-decision <project-root> --dec-id DEC-NNN --title ...`.

Structured writer that records a decision **and** regenerates its index in one
invocation:

- checks the body before a byte lands: a recorded ``locked`` or superseded
  decision is immutable, a new id may not be one an existing decision already
  names, a ``Ruling:`` line is required, ``Authority:`` is reconciled and its
  limits enforced, ``Amends:`` / ``Ratifies:`` / ``Carries forward:`` targets
  and ``Closes plan:`` are checked, and an operator decision's captured turns
  must confirm; then
- writes ``decisions/DEC-NNN.md`` (the body, via ``--content`` /
  ``--content-file``), then
- regenerates ``decisions/INDEX.md`` from every decision file: live rulings
  first, with their ruling, authority, and amendments, then the superseded
  history; then
- reports the nearest live locked decisions (``cli/decision_neighbors.py``).

The neighbor rows are the one surface that tells the PM which existing
rulings sit next to the one it just recorded. They are advisory and never
change the exit code: a decision is a ruling, and detecting that two rulings
are *about* the same thing is not the same as knowing they disagree. The
rows cost a fixed three lines, are emitted only here, and add nothing to
session startup.

Both writes go through the mediated-write primitive (``decision``
dest_kind). The INDEX update renders the full file back through the
primitive — no raw edit, no second bypass surface. The DEC file is written
first; if it refuses, the index is left untouched.
"""
import argparse
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from cli import decision_neighbors, evidence_resolver, trace_binding
from cli.commands import _writers
from cli.mediated_write import GuardRefusal, mediated_write
from cli.request_trace import RequestRefusal

_INDEX_HEADER = (
    "# Decisions Index\n\n"
    "Generated from the decision files by `cartopian write-decision`; "
    "edits here are overwritten.\n"
)
_LIVE_HEADER = (
    "\n## Live\n\n"
    "| ID | Title | Ruling | Authority | Status | Date | Amended by |\n"
    "| --- | --- | --- | --- | --- | --- | --- |\n"
)
_HISTORY_HEADER = (
    "\n## Superseded\n\n"
    "| ID | Title | Superseded by |\n"
    "| --- | --- | --- |\n"
)
#: The column layout of an index written before the generated layout, read
#: only to carry earlier rows' titles and dates forward.
_LEGACY_COLUMNS = ["id", "title", "date", "status", "supersedes"]
_CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")
_ROW_ID_RE = re.compile(r"\[(DEC-\d{3}[^\]]*)\]\(")


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


def _is_header_line(stripped: str) -> bool:
    if not _HEADER_LINE_RE.match(stripped):
        return False
    lowered = stripped.lower()
    return not (
        lowered.startswith(evidence_resolver.DECISION_EVIDENCE_MARKER.lower())
        or stripped.startswith(trace_binding.OUT_OF_PLAN_MARKER)
    )


def _stamp_missing_header(content: str, field: str, value: str) -> str:
    """Insert a ``Field: value`` line into a decision's header block.

    Only ever called for a field the body does not declare, so this inserts and
    never replaces. The line goes after the first contiguous run of header
    lines, which keeps the block contiguous and in template order (Date,
    Status, Supersedes, ...); with no header block yet it goes under the H1,
    followed by the blank line the block needs to read as one. Evidence and
    disposition markers are not header lines: inserting after one would part a
    marker from the block quote that belongs to it.
    """
    lines = content.splitlines(keepends=True)
    block_end = next((i for i, text in enumerate(lines) if text.startswith("## ")), len(lines))
    insert_at, h1_at = None, None
    for i in range(block_end):
        if _is_header_line(lines[i].strip()):
            insert_at = i + 1
            while insert_at < block_end and _is_header_line(lines[insert_at].strip()):
                insert_at += 1
            break
        if h1_at is None and lines[i].startswith("# "):
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


def prior_rows(index_path: Path) -> Dict[str, Dict[str, str]]:
    """``{DEC id: {title, date}}`` carried forward from the existing index.

    Title and date are descriptive and supplied by the caller at write time,
    so they are not recoverable from the decision file alone; every other cell
    is regenerated. Reads both the generated layout and the earlier
    single-table layout by each table's own column header, and tolerates a
    missing, empty, or freshly seeded index.
    """
    try:
        text = index_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    rows: Dict[str, Dict[str, str]] = {}
    columns: Optional[List[str]] = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if stripped:
                columns = None
            continue
        if set(stripped) <= set("|-: "):
            continue  # separator row
        cells = [cell.strip() for cell in _CELL_SPLIT_RE.split(stripped.strip("|"))]
        if cells and cells[0].lower() == "id":
            columns = [cell.lower() for cell in cells]
            continue
        match = _ROW_ID_RE.search(cells[0]) if cells else None
        if match is None:
            continue
        row = dict(zip(columns or _LEGACY_COLUMNS, cells))
        rows[match.group(1)] = {"title": row.get("title", ""), "date": row.get("date", "")}
    return rows


def _h1_title(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            return title.split(":", 1)[1].strip() if ":" in title else title
    return ""


def render_index(
    root: Path, prior: Dict[str, Dict[str, str]], dec_id: Optional[str] = None,
    title: str = "", date: str = "",
) -> Tuple[str, int]:
    """The whole index, regenerated from every decision file on disk.

    Live rulings come first, so a reader looking for what is in force reads
    only what is in force, and each row carries its one-line ruling, who it
    speaks for, and any later decision that amends it. Superseded decisions
    follow as a compact history that names what replaced each one, which is
    where every supersession is read. A decision without a ``Ruling:`` line
    shows the first sentence of its ruling instead. ``dec_id`` is the decision
    just written, whose title and date the caller supplies. Returns
    ``(text, row count)``.
    """
    texts = trace_binding.decision_bodies(root)
    retired_by = trace_binding.superseders(texts)
    amended_by = trace_binding.amendments(texts)
    ratified_by = trace_binding.ratifications(texts)
    live: List[str] = []
    history: List[str] = []
    for stem in sorted(texts):
        text = texts[stem]
        if stem == dec_id:
            row_title, row_date = title, date
        else:
            carried = prior.get(stem, {})
            row_title = carried.get("title") or _sanitize_cell(_h1_title(text))
            row_date = carried.get("date") or _sanitize_cell(trace_binding.decision_header(text, "Date"))
        link = f"[{stem}]({stem}.md)"
        if stem in retired_by:
            history.append(f"| {link} | {row_title} | {', '.join(retired_by[stem])} |")
            continue
        status = trace_binding.decision_header(text, "Status").lower()
        if status not in trace_binding.DECISION_STATUSES:
            status = "unreadable"
        authority = trace_binding.base_authority(text)
        if stem in ratified_by and authority == trace_binding.PM_AUTHORITY:
            authority = f"pm, ratified by {', '.join(ratified_by[stem])}"
        ruling = _sanitize_cell(trace_binding.ruling_line(text))
        amended = ", ".join(amended_by.get(stem, []))
        live.append(
            f"| {link} | {row_title} | {ruling} | {authority} | {status} | {row_date} | {amended} |"
        )
    body = _INDEX_HEADER + _LIVE_HEADER + "".join(f"{row}\n" for row in live)
    if history:
        body += _HISTORY_HEADER + "".join(f"{row}\n" for row in history)
    return body, len(live) + len(history)


def _refuse(code: str, detail: str) -> int:
    _writers.stderr("guard", f"{code}: {detail}")
    return _writers.EXIT_FAIL


def _immutability_error(dec_id: str, existing: Dict[str, str]) -> Optional[str]:
    """Why an already-recorded ``DEC-NNN`` may not be rewritten, if it may not.

    A ruling in force, or one a later decision retired, is history: changing
    it in place would change what every reader, pin, and earlier review relied
    on. Only a draft (``open``, or a status no reader can act on) may be
    re-issued, because nothing yet rules by it.
    """
    if dec_id not in existing:
        return None
    retired_by = trace_binding.superseders(existing).get(dec_id)
    if retired_by:
        return (
            f"{dec_id} is superseded by {', '.join(retired_by)}; a recorded decision is "
            "immutable, so record any change as a new decision"
        )
    if trace_binding.decision_header(existing[dec_id], "Status").lower() == "locked":
        return (
            f"{dec_id} is a locked ruling; a recorded decision is immutable, so write a new "
            f"decision that supersedes it (`Supersedes: {dec_id}`) or adds to it "
            f"(`Amends: {dec_id}`)"
        )
    return None


#: Headers that name other decisions by id.
_RELATION_HEADERS = (
    "Supersedes",
    trace_binding.AMENDS_HEADER,
    trace_binding.RATIFIES_HEADER,
    trace_binding.CARRIES_HEADER,
    trace_binding.RESTATES_HEADER,
)


def _naming_decisions(dec_id: str, existing: Dict[str, str]) -> List[str]:
    """Recorded decisions whose headers name ``dec_id``."""
    return [
        stem for stem in sorted(existing)
        if any(dec_id in trace_binding.decision_ids_in_header(existing[stem], header)
               for header in _RELATION_HEADERS)
    ]


def _relation_error(
    dec_id: str, content: str, effective: str, authority: str, status: str,
    prior: Dict[str, str],
) -> Optional[Tuple[str, str]]:
    """Check what this decision does to earlier ones against who it speaks for.

    ``prior`` is every other decision as it stands before this write. A PM
    decision may not retire, amend, or restate an operator ruling, may not
    ratify anything, and may not dispose of an operator request while locked;
    an open PM decision may still *propose* a disposition for the operator.
    The same holds for closing the plan early and for carrying rulings into
    the next plan.
    """
    retired = trace_binding.superseded_ids(prior)
    authorities = trace_binding.effective_authorities(prior)
    supersedes = trace_binding.decision_ids_in_header(content, "Supersedes")
    amends = trace_binding.decision_ids_in_header(content, trace_binding.AMENDS_HEADER)
    ratifies = trace_binding.decision_ids_in_header(content, trace_binding.RATIFIES_HEADER)
    for target in amends:
        if target == dec_id or target not in prior:
            return "decision-amends-invalid", f"{dec_id} amends {target}, which is not a recorded decision"
        if target in retired:
            return "decision-amends-invalid", (
                f"{target} is superseded; amend the decision that replaced it"
            )
        if target in supersedes:
            return "decision-amends-invalid", (
                f"{dec_id} both supersedes and amends {target}; a decision either retires "
                "a ruling or adds to it"
            )
    for target in ratifies:
        if target == dec_id or target not in prior:
            return "decision-ratifies-invalid", f"{dec_id} ratifies {target}, which is not a recorded decision"
        if target in retired:
            return "decision-ratifies-invalid", f"{target} is superseded; there is nothing left to ratify"
        if authorities[target] == trace_binding.OPERATOR_AUTHORITY:
            return "decision-ratifies-invalid", f"{target} already carries operator authority"
    closes = trace_binding.decision_header(content, trace_binding.CLOSES_PLAN_HEADER)
    carries = trace_binding.decision_header(content, trace_binding.CARRIES_HEADER)
    if closes and closes.lower() != trace_binding.CLOSES_PLAN_EARLY:
        return "decision-closes-plan-invalid", (
            f"`{trace_binding.CLOSES_PLAN_HEADER}: {closes}`; the one value is "
            f"`{trace_binding.CLOSES_PLAN_EARLY}`"
        )
    if closes and carries:
        return "decision-closes-plan-invalid", (
            f"{dec_id} both closes the plan and carries rulings forward; record them as two "
            "decisions, since the carrying decision is kept into the next plan and a kept "
            "early close would close that plan too"
        )
    if carries:
        problems = trace_binding.carry_problems({**prior, dec_id: effective}, dec_id)
        if problems:
            return "decision-carries-invalid", "; ".join(problems)
    if authority == trace_binding.OPERATOR_AUTHORITY:
        return None
    if status == "locked" and (closes or carries):
        return "decision-authority-insufficient", (
            f"{dec_id} is a PM decision; ending the plan early and choosing what outlives it "
            "are the operator's calls. Record it `Status: open` as a proposal, or cite the "
            "operator's captured turn with `Authority: operator`"
        )
    if ratifies:
        return "decision-authority-insufficient", (
            f"{dec_id} is a PM decision; only an operator decision, citing the operator's "
            "captured approval, ratifies earlier decisions"
        )
    for target in supersedes + amends:
        if authorities.get(target) == trace_binding.OPERATOR_AUTHORITY:
            return "decision-authority-insufficient", (
                f"{target} is an operator ruling; changing it needs the operator's captured "
                f"turn (`{evidence_resolver.DECISION_EVIDENCE_MARKER} <unit>: <capture-id>`) "
                "and `Authority: operator`"
            )
    if status == "locked" and trace_binding.carries_out_of_plan(effective):
        return "decision-authority-insufficient", (
            f"{dec_id} is a PM decision; leaving an operator request out of the plan is the "
            "operator's call. Record it `Status: open` as a proposal, or cite the operator's "
            "captured turn with `Authority: operator`"
        )
    return None


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

    dec_filename = f"{dec_id}.md"
    matches = _writers.identifier_files(root / "decisions", dec_id)
    if len(matches) > 1:
        return _refuse(
            "decision-id-collision",
            f"{dec_id} resolves to multiple files: " + ", ".join(str(path) for path in matches),
        )
    if matches and matches[0].name != dec_filename:
        return _refuse(
            "artifact-name-migration-required", f"{matches[0]} must be migrated to {dec_filename}"
        )

    existing = trace_binding.decision_bodies(root)
    immutable = _immutability_error(dec_id, existing)
    if immutable is not None:
        return _refuse("decision-immutable", immutable)
    prior = {stem: text for stem, text in existing.items() if stem != dec_id}
    if dec_id not in existing:
        naming = _naming_decisions(dec_id, existing)
        if naming:
            return _refuse(
                "decision-id-reserved",
                f"{dec_id} has no file, but {', '.join(naming)} already name it in a header "
                "(often a decision carried from an earlier plan); a new decision under that "
                "id would be read as the one they mean, so take the next id after the "
                "highest in use",
            )

    resolved: dict = {}
    for field, attr, fallback in _PROJECTED_FIELDS:
        content, value, err = _reconcile_header(content, field, getattr(args, attr), fallback)
        if err is not None:
            _writers.stderr("guard", err)
            return _writers.EXIT_FAIL
        resolved[attr] = value

    ruling = trace_binding.decision_header(content, trace_binding.RULING_HEADER)
    if not ruling:
        return _refuse(
            "decision-ruling-missing",
            f"{dec_id} needs a one-line `{trace_binding.RULING_HEADER}:` header stating what "
            "was decided; the index projects it so a reader need not open the file",
        )
    if len(ruling) > trace_binding.RULING_CAP:
        return _refuse(
            "decision-ruling-too-long",
            f"`{trace_binding.RULING_HEADER}:` is {len(ruling)} characters; keep it to "
            f"{trace_binding.RULING_CAP} and leave the detail to `## Decision`",
        )

    # A restatement points at the ruling it extends instead of copying it.
    # Pin the pointer to the target's bytes now, so no later reader has to
    # trust that the target is still the text this decision meant.
    content, restatement, rerr = trace_binding.pin_restatement(root, dec_id, content)
    if rerr is not None:
        return _refuse(rerr.code, rerr.detail)

    def effective_text(body: str) -> str:
        raw = trace_binding.raw_decision_files(root)
        raw[dec_id] = body.encode("utf-8")
        return trace_binding.resolve_decision_text(dec_id, raw)

    # Who the ruling speaks for. Absent a declaration, it is the operator's
    # exactly when it names the operator's captured turns, and the resolved
    # value is stamped so the file says so from now on.
    authority = trace_binding.declared_authority(content)
    if authority and authority not in trace_binding.DECISION_AUTHORITIES:
        return _refuse(
            "decision-authority-unrecognized",
            f"body declares {trace_binding.AUTHORITY_HEADER}: {authority!r}; a decision "
            f"speaks for {' or '.join(trace_binding.DECISION_AUTHORITIES)}",
        )
    if not authority:
        inferred = (
            trace_binding.OPERATOR_AUTHORITY
            if trace_binding.has_evidence_marker(effective_text(content))
            else trace_binding.PM_AUTHORITY
        )
        content = _stamp_missing_header(content, trace_binding.AUTHORITY_HEADER, inferred)
        authority = inferred
    effective = effective_text(content)

    relation = _relation_error(
        dec_id, content, effective, authority, resolved["status"].lower(), prior
    )
    if relation is not None:
        return _refuse(*relation)

    if authority == trace_binding.OPERATOR_AUTHORITY:
        if not trace_binding.has_evidence_marker(effective):
            return _refuse(
                "decision-authority-unevidenced",
                f"{dec_id} declares `Authority: operator` but names no captured operator turn; "
                f"add `{evidence_resolver.DECISION_EVIDENCE_MARKER} <unit>: <capture-id>` from "
                "`cartopian lookup-evidence`, or record it as `Authority: pm`",
            )
        try:
            unconfirmed = evidence_resolver.unconfirmed_decision_evidence(root, dec_id, effective)
        except RequestRefusal as refusal:
            return _refuse(refusal.rule, refusal.detail)
        if unconfirmed:
            return _refuse(
                "decision-evidence-unconfirmed",
                f"{dec_id} speaks for the operator, but these turns do not confirm: "
                + "; ".join(f"{item.reference} ({item.reason})" for item in unconfirmed),
            )

    # 1. Write the DEC body first. If it refuses, the index stays untouched.
    try:
        dec_result = mediated_write(root, "decision", dec_filename, content)
    except GuardRefusal as refusal:
        _writers.stderr("guard", f"{refusal.rule}: {refusal.detail}")
        return _writers.EXIT_FAIL

    # 2. Regenerate INDEX.md from the decision files through the same
    #    primitive, carrying earlier rows' titles and dates forward.
    index_path = Path(root) / "decisions" / "INDEX.md"
    carried_rows = prior_rows(index_path)
    replaced = dec_id in carried_rows
    index_text, row_count = render_index(
        root, carried_rows, dec_id, _sanitize_cell(args.title), args.date
    )

    try:
        index_result = mediated_write(root, "decision", "INDEX.md", index_text)
    except GuardRefusal as refusal:
        _writers.stderr(
            "guard",
            f"{refusal.rule}: {refusal.detail} (DEC body written: {dec_result['path']})",
        )
        return _writers.EXIT_FAIL

    # Advisory, and deliberately last: a failure to rank neighbors must never
    # cost a caller the decision it already wrote to disk.
    try:
        neighbors = decision_neighbors.neighbors(
            root, trace_binding.effective_decision_text(root, dec_id), exclude_id=dec_id
        )
    except Exception:  # pragma: no cover - advisory surface, never fatal
        neighbors = []

    _writers.emit_record({
        "action": "write-decision",
        "details": {
            "dest_kind": "decision",
            "dec_id": dec_id,
            "authority": authority,
            "neighbors": neighbors,
            "restates": restatement,
            "decision_path": dec_result["path"],
            "decision_bytes": dec_result["bytes"],
            "index_path": index_result["path"],
            "index_row_replaced": replaced,
            "index_rows": row_count,
        },
    })
    return _writers.EXIT_OK
