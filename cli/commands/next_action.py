"""`cartopian next-action <project-path>` aggregator.

Emits a single flat NDJSON record with all orientation data a PM needs to
start or resume a session: active task, next open task, phase, PM role,
dispatch kind, blockers, and any STATE.md vs. filesystem disagreement.
"""
import argparse
import re
import sys
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional

from cli.capabilities import role_description
from cli.commands.resolve_config import _CliError, _require_project_keys
from cli.emit import emit_record
from cli.main import EXIT_ENV, EXIT_FAIL, EXIT_OK, EXIT_USAGE, stderr_error, stderr_guard, stderr_usage

_TASK_FILENAME_RE = re.compile(r"^(TASK-\d{2}-\d{3})(?:-[^/]*)?\.md$")
_PHASE_STEM_RE = re.compile(r"^PHASE-\d{2}-[a-z0-9][a-z0-9-]*$")
_STATE_TASK_STATUS_RE = re.compile(
    r"\b(TASK-\d{2}-\d{3})\b[^`\n]*`([^`\n]+)`"
)

_ACTIVE_STATUSES = ("in-progress", "in-review")
_ALL_STATUSES = ("open", "in-progress", "in-review", "done")
_DEFAULT_PM_ROLE = "Manages the project lifecycle and orchestrates handoffs."


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    """Add arguments for next-action."""
    subparser.add_argument(
        "project_path",
        help="Absolute path to the Cartopian project directory",
    )


def _load_toml(path: Path) -> Optional[Dict[str, Any]]:
    """Load a TOML file; return None if missing, raise on parse/read error."""
    if not path.exists():
        return None
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _merge_table(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    result.update(override)
    return result


def _resolve_pm_settings(project_path: Path, project_cfg: Dict[str, Any]) -> tuple[str, bool, str]:
    global_cfg = _load_toml(Path.home() / ".cartopian" / "cartopian.toml") or {}
    roles = _merge_table(global_cfg.get("roles", {}) or {}, project_cfg.get("roles", {}) or {})
    handoffs = _merge_table(
        global_cfg.get("handoffs", {}) or {},
        project_cfg.get("handoffs", {}) or {},
    )
    # Readiness gate is keyed on role-KEY presence, not description text: a
    # project may legitimately declare a `pm` role whose description equals the
    # default placeholder. `pm_role_declared` lets the resume gate distinguish
    # "absent → placeholder injected" from "declared with default-looking text".
    pm_role_declared = "pm" in roles
    pm_role = role_description(roles["pm"]) if pm_role_declared else _DEFAULT_PM_ROLE
    pm_dispatch_kind = "automated" if "pm" in handoffs else "manual"
    return pm_role, pm_role_declared, pm_dispatch_kind


def _first_heading(content: str) -> str:
    """Return the text after `# ` from the first top-level heading."""
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _parse_phase_header(content: str) -> Optional[str]:
    """Return the Phase header value from task file frontmatter, or None."""
    for line in content.splitlines():
        if line.startswith("## "):
            break
        stripped = line.strip()
        if stripped.startswith("Phase:"):
            value = stripped[len("Phase:"):].strip()
            return value if value else None
    return None


def _collect_phase_stems(phases_dir: Path) -> List[str]:
    """Return sorted list of phase file stems from the phases/ directory."""
    if not phases_dir.is_dir():
        return []
    stems = []
    for entry in phases_dir.iterdir():
        if entry.is_file() and entry.suffix == ".md":
            if _PHASE_STEM_RE.match(entry.stem):
                stems.append(entry.stem)
    return sorted(stems)


def _find_phase_id(project_path: Path) -> Optional[str]:
    """Determine the current phase ID from task headers or STATE.md.

    Returns the stem of the earliest phase file that has uncompleted tasks
    (open, in-progress, in-review), or the first phase stem mentioned in
    STATE.md that matches a known phase file, or None if no phase is active.
    """
    phase_stems = _collect_phase_stems(project_path / "phases")
    if not phase_stems:
        return None

    stem_set = set(phase_stems)
    tasks_dir = project_path / "tasks"

    # Collect phase IDs referenced by uncompleted tasks.
    referenced: Dict[str, str] = {}
    for status in ("open", "in-progress", "in-review"):
        status_dir = tasks_dir / status
        if not status_dir.is_dir():
            continue
        for entry in sorted(status_dir.iterdir(), key=lambda p: p.name):
            if not entry.is_file():
                continue
            if not _TASK_FILENAME_RE.match(entry.name):
                continue
            try:
                content = entry.read_text(encoding="utf-8")
            except OSError:
                continue
            phase = _parse_phase_header(content)
            if phase and phase in stem_set and phase not in referenced:
                referenced[phase] = status

    if referenced:
        # Return the earliest (lowest sort key) phase with uncompleted tasks.
        return min(referenced, key=lambda s: phase_stems.index(s))

    # Fall back: check STATE.md for a known phase stem mention.
    state_path = project_path / "STATE.md"
    if state_path.is_file():
        try:
            state_text = state_path.read_text(encoding="utf-8")
        except OSError:
            state_text = ""
        for stem in phase_stems:
            if stem in state_text:
                return stem

    return None


def _find_active_task(tasks_dir: Path) -> Optional[Dict[str, str]]:
    """Scan in-progress/ then in-review/ and return the first task found.

    Returns {id, title, path, status} or None.
    """
    for status in _ACTIVE_STATUSES:
        status_dir = tasks_dir / status
        if not status_dir.is_dir():
            continue
        for entry in sorted(status_dir.iterdir(), key=lambda p: p.name):
            if not entry.is_file():
                continue
            m = _TASK_FILENAME_RE.match(entry.name)
            if not m:
                continue
            try:
                content = entry.read_text(encoding="utf-8")
            except OSError:
                continue
            return {
                "id": m.group(1),
                "title": _first_heading(content),
                "path": str(entry),
                "status": status,
            }
    return None


def _parse_blocked_by(content: str) -> List[str]:
    """Return blocked-by task ids declared in the task header, if any."""
    for line in content.splitlines():
        if line.startswith("## "):
            break
        stripped = line.strip()
        if stripped.startswith("Blocked by:"):
            value = stripped[len("Blocked by:") :].strip()
            if not value or value.lower() in {"none", "n/a"}:
                return []
            return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _has_open_tasks(tasks_dir: Path) -> bool:
    """Return True when tasks/open/ contains at least one task file."""
    open_dir = tasks_dir / "open"
    if not open_dir.is_dir():
        return False
    return any(
        entry.is_file() and _TASK_FILENAME_RE.match(entry.name)
        for entry in open_dir.iterdir()
    )


def _collect_done_task_ids(tasks_dir: Path) -> set:
    """Return the task ids of every task file in tasks/done/."""
    done_dir = tasks_dir / "done"
    if not done_dir.is_dir():
        return set()
    done_ids = set()
    for entry in done_dir.iterdir():
        if not entry.is_file():
            continue
        m = _TASK_FILENAME_RE.match(entry.name)
        if m:
            done_ids.add(m.group(1))
    return done_ids


def _find_next_open_task(project_path: Path) -> Optional[Dict[str, str]]:
    """Return {id, title, path} for the next sequential ready task in tasks/open/.

    Linear execution order per protocol/CONVENTIONS.md § Task Execution Order:
    phase order first, then filename order within the phase, skipping tasks
    whose `Blocked by:` dependencies are not yet in tasks/done/.
    """
    tasks_dir = project_path / "tasks"
    open_dir = tasks_dir / "open"
    if not open_dir.is_dir():
        return None
    phase_order = {
        stem: index for index, stem in enumerate(_collect_phase_stems(project_path / "phases"))
    }
    done_ids = _collect_done_task_ids(tasks_dir)
    candidates = []
    for entry in sorted(open_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_file():
            continue
        m = _TASK_FILENAME_RE.match(entry.name)
        if not m:
            continue
        try:
            content = entry.read_text(encoding="utf-8")
        except OSError:
            continue
        if any(dep not in done_ids for dep in _parse_blocked_by(content)):
            continue
        phase_id = _parse_phase_header(content)
        known_phase = phase_id in phase_order
        candidates.append(
            (
                0 if known_phase else 1,
                phase_order.get(phase_id, len(phase_order)),
                entry.name,
                {
                    "id": m.group(1),
                    "title": _first_heading(content),
                    "path": str(entry),
                },
            )
        )
    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        return candidates[0][3]
    return None


def _find_open_questions_in_state(state_text: str) -> List[str]:
    """Return list items from any 'Open Questions' section in STATE.md.

    Scans for a section heading whose text contains 'open question'
    (case-insensitive) and collects bare '- ' list items under it until the
    next heading.  Items that are not list entries (e.g. "None.") are ignored.
    """
    items: List[str] = []
    in_oq_section = False
    for line in state_text.splitlines():
        if line.startswith("## ") or line.startswith("# "):
            heading = line.lstrip("#").strip().lower()
            in_oq_section = "open question" in heading
        elif in_oq_section and line.startswith("- "):
            items.append(line[2:].strip())
    return items


def _detect_blockers(
    project_path: Path,
    phase_id: Optional[str],
    tasks_dir: Path,
) -> List[str]:
    """Detect and return human-readable blocker strings.

    Checks for: (1) tasks present but no active phase; (2) unresolved open
    questions listed under an 'Open Questions' section in STATE.md.
    """
    blockers: List[str] = []

    if phase_id is None:
        has_tasks = False
        for status in ("open", "in-progress", "in-review"):
            status_dir = tasks_dir / status
            if status_dir.is_dir():
                for entry in status_dir.iterdir():
                    if entry.is_file() and _TASK_FILENAME_RE.match(entry.name):
                        has_tasks = True
                        break
            if has_tasks:
                break
        if has_tasks:
            blockers.append("no active phase detected but tasks are present")

    state_path = project_path / "STATE.md"
    if state_path.is_file():
        try:
            state_text = state_path.read_text(encoding="utf-8")
        except OSError:
            state_text = ""
        for oq in _find_open_questions_in_state(state_text):
            blockers.append(f"unresolved open question in STATE.md: {oq}")

    return blockers


def _detect_disagreement(project_path: Path) -> Optional[str]:
    """Detect STATE.md vs. filesystem task placement disagreements.

    Scans STATE.md for task IDs with backtick-quoted status claims, then
    compares each against the actual directory the task file lives in.
    Returns a human-readable description of the first disagreement found,
    or None if everything agrees (or STATE.md makes no status claims).
    """
    state_path = project_path / "STATE.md"
    if not state_path.is_file():
        return None
    try:
        state_text = state_path.read_text(encoding="utf-8")
    except OSError:
        return None

    # Extract task IDs with explicit status claims from STATE.md.
    claimed: Dict[str, str] = {}
    for m in _STATE_TASK_STATUS_RE.finditer(state_text):
        task_id = m.group(1)
        status = m.group(2)
        if status in _ALL_STATUSES and task_id not in claimed:
            claimed[task_id] = status

    if not claimed:
        return None

    # Build a map of task_id → actual status from the filesystem.
    tasks_dir = project_path / "tasks"
    actual: Dict[str, str] = {}
    for status in _ALL_STATUSES:
        status_dir = tasks_dir / status
        if not status_dir.is_dir():
            continue
        for entry in status_dir.iterdir():
            if not entry.is_file():
                continue
            m = _TASK_FILENAME_RE.match(entry.name)
            if m:
                actual[m.group(1)] = status

    disagreements: List[str] = []
    for task_id in sorted(claimed):
        claimed_status = claimed[task_id]
        actual_status = actual.get(task_id)
        if actual_status is None:
            disagreements.append(
                f"{task_id}: STATE.md claims '{claimed_status}' but task file not found on filesystem"
            )
        elif actual_status != claimed_status:
            disagreements.append(
                f"{task_id}: STATE.md claims '{claimed_status}' but filesystem shows '{actual_status}'"
            )

    return "; ".join(disagreements) if disagreements else None


def _phase_task_presence(project_path: Path) -> Dict[str, bool]:
    """Map each phase stem → whether any task file (any status) references it.

    A phase with no task files is *unstarted* — it exists in the plan but its
    tasks have not been generated yet. This is the distinction `phase_id` /
    `next_open_task` miss: both only see phases that already carry tasks.
    """
    phase_stems = _collect_phase_stems(project_path / "phases")
    stem_set = set(phase_stems)
    has_task = {stem: False for stem in phase_stems}
    tasks_dir = project_path / "tasks"
    for status in _ALL_STATUSES:
        status_dir = tasks_dir / status
        if not status_dir.is_dir():
            continue
        for entry in status_dir.iterdir():
            if not (entry.is_file() and _TASK_FILENAME_RE.match(entry.name)):
                continue
            try:
                content = entry.read_text(encoding="utf-8")
            except OSError:
                continue
            phase = _parse_phase_header(content)
            if phase in stem_set:
                has_task[phase] = True
    return has_task


def _next_unstarted_phase(phase_stems: List[str], has_task: Dict[str, bool]) -> Optional[str]:
    """The next phase to generate tasks for: the earliest phase *after the last
    phase that already has tasks* whose tasks have not been generated yet.

    Searching after the last task-bearing phase means earlier task-less phases
    (e.g. a completed rulings/contract phase) are treated as behind us, not
    "next". Returns None only when every phase from there on already has tasks —
    i.e. nothing is left to generate, so an empty open queue genuinely means the
    plan is finished rather than merely un-generated.
    """
    if not phase_stems:
        return None
    last_with_tasks = -1
    for index, stem in enumerate(phase_stems):
        if has_task.get(stem):
            last_with_tasks = index
    for index in range(last_with_tasks + 1, len(phase_stems)):
        if not has_task.get(phase_stems[index]):
            return phase_stems[index]
    return None


def handler(args: argparse.Namespace) -> int:
    """Handle next-action command.

    Reads the Cartopian project at the given path and emits a single NDJSON
    record with orientation fields.
    """
    raw_path: str = args.project_path
    if not Path(raw_path).is_absolute():
        stderr_usage(f"project_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE

    project_path = Path(raw_path).resolve()

    toml_path = project_path / "cartopian.toml"
    if not toml_path.exists():
        stderr_error(f"project config not found: {toml_path}")
        return EXIT_ENV

    try:
        cfg = _load_toml(toml_path) or {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        stderr_error(f"project config unreadable: {toml_path} — {exc}")
        return EXIT_ENV

    try:
        project_id = _require_project_keys(cfg, toml_path)[0]
    except _CliError as err:
        if err.prefix == "guard":
            stderr_guard(err.message)
        else:
            stderr_error(err.message)
        return err.exit_code

    try:
        pm_role, pm_role_declared, pm_dispatch_kind = _resolve_pm_settings(project_path, cfg)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        stderr_error(f"global config unreadable: {Path.home() / '.cartopian' / 'cartopian.toml'} — {exc}")
        return EXIT_ENV

    tasks_dir = project_path / "tasks"
    phase_id = _find_phase_id(project_path)
    active_task = _find_active_task(tasks_dir)
    next_open_task = _find_next_open_task(project_path)
    has_open_tasks = _has_open_tasks(tasks_dir)

    # Phase-aware completion truth (FR-012): an empty open queue does NOT imply
    # the plan is done if later phases exist whose tasks were never generated.
    phase_stems = _collect_phase_stems(project_path / "phases")
    has_task = _phase_task_presence(project_path)
    next_unstarted_phase = _next_unstarted_phase(phase_stems, has_task)
    # The plan is complete only when there is nothing active, nothing open, no
    # phase left to generate tasks for, AND at least one phase actually had
    # tasks (an all-empty plan is un-started, not complete).
    plan_complete = (
        active_task is None
        and next_open_task is None
        and not has_open_tasks
        and next_unstarted_phase is None
        and any(has_task.values())
    )

    blockers = _detect_blockers(project_path, phase_id, tasks_dir)
    # Open tasks that are all dependency-blocked are a deadlock, not progress:
    # next_open_task skips not-ready tasks, so without this the queue would
    # look empty while work still exists.
    if next_open_task is None and has_open_tasks and active_task is None:
        blockers.append(
            "open tasks exist but none are ready to start (unmet Blocked by: dependencies)"
        )

    record: Dict[str, Any] = {
        "project_id": project_id,
        "project_path": str(project_path),
        "phase_id": phase_id,
        "active_task": active_task,
        "next_open_task": next_open_task,
        "next_unstarted_phase": next_unstarted_phase,
        "plan_complete": plan_complete,
        "pm_role": pm_role,
        "pm_role_declared": pm_role_declared,
        "pm_dispatch_kind": pm_dispatch_kind,
        "blockers": blockers,
        "state_filesystem_disagreement": _detect_disagreement(project_path),
    }
    emit_record(record)
    return EXIT_OK
