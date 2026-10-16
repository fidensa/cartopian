"""Backlog revisit triggers: when a deferred entry is put back in front of the PM.

``BACKLOG.md`` survives plan closeout and is, by protocol, input to the next
planning cycle — but a file nothing reads is not an input. Each entry
therefore opens with a ``Revisit:`` line (stamped by ``write-backlog
--revisit``) naming the lifecycle fork at which the entry falls due:

- ``planning`` — the next planning cycle (the default; also what an entry
  without the line means, for files written before the field existed);
- ``PHASE-NN`` — when that phase's tasks are generated, and while it runs;
- ``TASK-NN-NNN`` — before that task is authored or dispatched.

:func:`evaluate` reads the backlog and the live plan surface and sorts every
entry into ``due`` (the fork is now), ``pending`` (not yet), ``missed`` (the
fork passed without the entry being promoted, re-deferred, or discarded) or
``unreadable`` (a ``Revisit:`` value outside the grammar — only a hand-edit
produces one). ``next-action`` carries ``live`` and ``due`` so the forks are
seen at startup; ``plan-audit`` warns on ``missed`` and ``unreadable`` as the
detection floor. Nothing here authorizes a promotion: a due entry is still
promoted through ``write-task``/``write-spec``/``write-phase --source`` and
removed through ``delete-backlog``, exactly as before.
"""
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

REVISIT_PLANNING = "planning"
REVISIT_FIELD = "Revisit"

_PHASE_RE = re.compile(r"^PHASE-\d{2}$")
_TASK_RE = re.compile(r"^TASK-(\d{2})-\d{3}$")
REVISIT_LINE_RE = re.compile(r"^Revisit:[ \t]*(.*?)[ \t]*$")
_HEADING_RE = re.compile(r"^## (BL-\d{3})(?:[ \t]+[—–-]+[ \t]*(.*?))?[ \t]*$")
_TASK_FILENAME_RE = re.compile(r"^(TASK-\d{2}-\d{3})\.md$")
_TASK_STATUSES = ("open", "in-progress", "in-review", "done")
_LIVE_STATUSES = ("open", "in-progress", "in-review")

GRAMMAR = "planning | PHASE-NN | TASK-NN-NNN"


def validate_revisit(value: str) -> Optional[str]:
    """Return ``None`` when ``value`` is a legal trigger, else the reason."""
    if value == REVISIT_PLANNING or _PHASE_RE.match(value) or _TASK_RE.match(value):
        return None
    return f"Revisit must be one of {GRAMMAR}; got: {value!r}"


def split_revisit_line(body: str) -> tuple[Optional[str], str]:
    """Return ``(value, rest)``: the ``Revisit:`` value when the body's first
    non-blank line declares one (``None`` otherwise) and the body without it."""
    lines = body.lstrip("\n").splitlines(keepends=True)
    if lines:
        m = REVISIT_LINE_RE.match(lines[0].rstrip("\n"))
        if m:
            return m.group(1), "".join(lines[1:]).lstrip("\n")
    return None, body


def render_body(revisit: str, body: str) -> str:
    """Render the entry body with the writer-owned ``Revisit:`` line first."""
    _existing, rest = split_revisit_line(body)
    rest = rest.strip("\n")
    return f"{REVISIT_FIELD}: {revisit}\n\n{rest}" if rest else f"{REVISIT_FIELD}: {revisit}"


def parse_section(bl_id: str, section: str) -> Dict[str, Any]:
    """Read one ``## BL-NNN — <title>`` section: its title, its body below the
    heading (``Revisit:`` line included, exactly as written), and its trigger."""
    lines = section.splitlines()
    title = ""
    heading = _HEADING_RE.match(lines[0]) if lines else None
    if heading and heading.group(2):
        title = heading.group(2).strip()
    body = "\n".join(lines[1:]).strip("\n")
    raw, _rest = split_revisit_line(body)
    record = {"id": bl_id, "title": title, "body": body}
    if raw is None:
        record.update({"revisit": REVISIT_PLANNING, "declared": False, "error": None})
    else:
        record.update({"revisit": raw, "declared": True, "error": validate_revisit(raw)})
    return record


def entries(project_path: Path) -> List[Dict[str, Any]]:
    """Every live entry of ``<project>/BACKLOG.md`` with its parsed trigger.

    Empty when the file is absent or unreadable; the audit's own invariants
    report a damaged file, this reader only sorts what it can read.
    """
    # Lazy import: write_backlog imports this module for the grammar.
    from cli.commands import write_backlog

    try:
        text = (project_path / "BACKLOG.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    _preamble, sections = write_backlog._split_sections(text)
    return [parse_section(bl_id, section) for bl_id, section in sections]


def _task_statuses(project_path: Path) -> Dict[str, str]:
    statuses: Dict[str, str] = {}
    for status in _TASK_STATUSES:
        directory = project_path / "tasks" / status
        if not directory.is_dir():
            continue
        for entry in directory.iterdir():
            m = _TASK_FILENAME_RE.match(entry.name)
            if m and entry.is_file():
                statuses[m.group(1)] = status
    return statuses


def _phase_live(project_path: Path, stems: List[str]) -> Dict[str, bool]:
    """Phase stem -> whether any of its tasks is still open or active."""
    from cli.commands.next_action import _parse_phase_header

    live = {stem: False for stem in stems}
    for status in _LIVE_STATUSES:
        directory = project_path / "tasks" / status
        if not directory.is_dir():
            continue
        for entry in directory.iterdir():
            if not (entry.is_file() and _TASK_FILENAME_RE.match(entry.name)):
                continue
            try:
                phase = _parse_phase_header(entry.read_text(encoding="utf-8"))
            except OSError:
                continue
            if phase in live:
                live[phase] = True
    return live


def evaluate(project_path: Path) -> Dict[str, Any]:
    """Sort every live backlog entry by whether its trigger is now.

    Returns ``{"live", "due", "pending", "missed", "unreadable"}``; each list
    holds ``{"id", "title", "revisit", "reason"}`` records in file order.
    """
    from cli import planning_status
    from cli.commands.next_action import _next_unstarted_phase, _phase_task_presence

    project_path = Path(project_path)
    live_entries = entries(project_path)
    record: Dict[str, Any] = {
        "live": len(live_entries),
        "due": [],
        "pending": [],
        "missed": [],
        "unreadable": [],
    }
    if not live_entries:
        return record

    def put(bucket: str, entry: Dict[str, Any], reason: str) -> None:
        record[bucket].append({
            "id": entry["id"],
            "title": entry["title"],
            "revisit": entry["revisit"],
            "reason": reason,
        })

    plan_exists = (project_path / "IMPLEMENTATION_PLAN.md").is_file()
    stems = planning_status.phase_sequence(project_path)
    has_task = _phase_task_presence(project_path)
    next_unstarted = _next_unstarted_phase(stems, has_task)
    phase_live = _phase_live(project_path, stems)
    statuses = _task_statuses(project_path)
    position = {stem: index for index, stem in enumerate(stems)}
    behind = position.get(next_unstarted, len(stems)) if next_unstarted else len(stems)
    # Generation is the next step only once no earlier phase still carries
    # open or active work (the same test planning_status applies).
    generating = next_unstarted is not None and not any(phase_live.values())

    def phase_state(stem: str) -> tuple[str, str]:
        """Bucket and reason for a phase-shaped trigger."""
        if stem not in position:
            return "missed", f"{stem} is not a phase of the current plan"
        if stem == next_unstarted:
            if generating:
                return "due", f"{stem} tasks are about to be generated"
            return "pending", f"{stem} tasks are generated after the current phase completes"
        if phase_live.get(stem):
            return "due", f"{stem} is in progress"
        if has_task.get(stem):
            return "missed", f"{stem} is finished"
        if position[stem] < behind:
            return "missed", f"{stem} was passed without tasks"
        return "pending", f"{stem} tasks are not yet generated"

    for entry in live_entries:
        if entry["error"] is not None:
            put("unreadable", entry, entry["error"])
            continue
        trigger = entry["revisit"]
        if not plan_exists:
            put("due", entry, "no plan exists: triage at plan project Stage 1")
            continue
        if trigger == REVISIT_PLANNING:
            put("pending", entry, "next planning cycle")
            continue
        if _PHASE_RE.match(trigger):
            bucket, reason = phase_state(trigger)
            put(bucket, entry, reason)
            continue
        task_match = _TASK_RE.match(trigger)
        status = statuses.get(trigger)
        if status == "done":
            put("missed", entry, f"{trigger} is done")
        elif status is not None:
            put("due", entry, f"{trigger} is {status}")
        else:
            stem = f"PHASE-{task_match.group(1)}"
            bucket, reason = phase_state(stem)
            if bucket == "due":
                reason = (
                    f"{trigger} is not authored yet; {reason}" if stem == next_unstarted
                    else f"{trigger} is not authored and {reason}"
                )
            elif bucket == "pending":
                reason = f"{trigger} is not authored yet; {reason}"
            put(bucket, entry, reason)
    return record


def startup_record(project_path: Path) -> Dict[str, Any]:
    """The bounded ``backlog`` record ``next-action`` carries: how many entries
    are live and which are due at the current fork."""
    full = evaluate(project_path)
    return {"live": full["live"], "due": full["due"]}
