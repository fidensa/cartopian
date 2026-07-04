"""`cartopian compose-state <project-path>` aggregator."""
import argparse
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from cli.commands.resolve_config import _CliError, _load_toml, _require_project_keys
from cli.emit import emit_record
from cli.main import EXIT_ENV, EXIT_OK, EXIT_USAGE, stderr_error, stderr_usage

_TASK_ID_RE = re.compile(r"^(TASK-\d{2}-\d{3})(?:-[^/]*)?\.md$")
_PHASE_ID_RE = re.compile(r"^(PHASE-\d{2}-[a-z0-9][a-z0-9-]*)")
_PLAN_DIRS = (
    "phases",
    "prompts",
    "reports",
    "reviews",
    "decisions",
    "specs",
)
_ACTIVE_STATUSES = ("in-progress", "in-review")
_TASK_STATUSES = ("open", "in-progress", "in-review", "done")


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    """Add arguments for compose-state."""
    subparser.add_argument(
        "project_path",
        help="Absolute path to the Cartopian project directory",
    )


def _load_project_config(project_path: Path) -> Dict[str, Any]:
    project_toml = project_path / "cartopian.toml"
    project_cfg = _load_toml(project_toml, "project config")
    if project_cfg is None:
        raise _CliError(EXIT_ENV, "error", f"project config not found: {project_toml}")
    _require_project_keys(project_cfg, project_toml)
    return project_cfg


def _iter_task_paths(project_path: Path, status: str) -> List[Path]:
    task_dir = project_path / "tasks" / status
    if not task_dir.is_dir():
        return []
    return [
        path for path in sorted(task_dir.iterdir(), key=lambda candidate: candidate.name)
        if path.is_file() and _TASK_ID_RE.match(path.name)
    ]


def _has_plan_artifacts(project_path: Path) -> bool:
    if (project_path / "IMPLEMENTATION_PLAN.md").is_file():
        return True
    for dirname in _PLAN_DIRS:
        directory = project_path / dirname
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.is_file() and path.suffix == ".md":
                return True
    for status in _TASK_STATUSES:
        if _iter_task_paths(project_path, status):
            return True
    return False


def _first_heading(content: str) -> str:
    """Return the first markdown H1 line text, or empty string."""
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _task_title(content: str, task_id: str) -> str:
    """Return a display title with any leading task id prefix removed."""
    heading = _first_heading(content)
    if heading.startswith(f"{task_id}:"):
        return heading[len(task_id) + 1 :].strip()
    return heading


def _task_phase_id(content: str) -> Optional[str]:
    """Return the declared Phase header from a task file, if present."""
    for line in content.splitlines():
        if line.startswith("## "):
            break
        stripped = line.strip()
        if stripped.startswith("Phase:"):
            value = stripped[len("Phase:") :].strip()
            return value or None
    return None


def _task_blocked_by(content: str) -> List[str]:
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


def _read_task_record(task_path: Path) -> Optional[Dict[str, Any]]:
    """Read one task file into a structured record for state composition."""
    match = _TASK_ID_RE.match(task_path.name)
    if match is None:
        return None
    try:
        content = task_path.read_text(encoding="utf-8")
    except OSError:
        return None
    return {
        "task_id": match.group(1),
        "title": _task_title(content, match.group(1)),
        "phase_id": _task_phase_id(content),
        "blocked_by": _task_blocked_by(content),
    }


def _phase_paths(project_path: Path) -> List[Path]:
    phases_dir = project_path / "phases"
    if not phases_dir.is_dir():
        return []
    return [
        path for path in sorted(phases_dir.iterdir(), key=lambda candidate: candidate.name)
        if path.is_file() and path.suffix == ".md" and _PHASE_ID_RE.match(path.stem)
    ]


def _phase_info(phase_path: Path, project_path: Path) -> Optional[Dict[str, str]]:
    """Return phase id/title/path info for a phase markdown file."""
    match = _PHASE_ID_RE.match(phase_path.stem)
    if match is None:
        return None
    try:
        content = phase_path.read_text(encoding="utf-8")
    except OSError:
        return None
    heading = _first_heading(content)
    title = heading
    prefix = f"{match.group(1)}:"
    if heading.startswith(prefix):
        title = heading[len(prefix) :].strip()
    elif heading.startswith(f"{match.group(1)} -"):
        title = heading[len(match.group(1)) + 1 :].strip(" -")
    return {
        "phase_id": match.group(1),
        "title": title or match.group(1),
        "path": str(phase_path.relative_to(project_path).as_posix()),
    }


def _phase_order(project_path: Path) -> Dict[str, int]:
    """Return phase id -> deterministic sort index."""
    order: Dict[str, int] = {}
    for index, phase_path in enumerate(_phase_paths(project_path)):
        info = _phase_info(phase_path, project_path)
        if info is not None:
            order[info["phase_id"]] = index
    return order


def _current_phase(project_path: Path) -> Optional[str]:
    """Return the current phase summary derived from active/open work."""
    phase_by_id: Dict[str, Dict[str, str]] = {}
    order = _phase_order(project_path)
    for phase_path in _phase_paths(project_path):
        info = _phase_info(phase_path, project_path)
        if info is not None:
            phase_by_id[info["phase_id"]] = info

    candidate_ids: List[str] = []
    for status in ("in-progress", "in-review", "open"):
        for task_path in _iter_task_paths(project_path, status):
            task = _read_task_record(task_path)
            if task is None:
                continue
            phase_id = task["phase_id"]
            if isinstance(phase_id, str) and phase_id in phase_by_id and phase_id not in candidate_ids:
                candidate_ids.append(phase_id)

    if not candidate_ids:
        return None

    current_id = sorted(candidate_ids, key=lambda phase_id: order.get(phase_id, len(order)))[0]
    info = phase_by_id[current_id]
    return f"{info['phase_id']}: {info['title']} (`{info['path']}`)"


def _active_work(project_path: Path) -> Optional[str]:
    """Return active work bullet lines from in-progress and in-review work.

    Every task in an active status directory is surfaced, ordered by phase,
    then by status (in-progress before in-review), then filename. The
    filesystem is the source of truth, so concurrent active tasks must all
    appear here just as ``list-tasks`` reports them.
    """
    order = _phase_order(project_path)
    rows: List[tuple[int, int, str, str]] = []
    for status_rank, status in enumerate(_ACTIVE_STATUSES):
        for task_path in _iter_task_paths(project_path, status):
            task = _read_task_record(task_path)
            if task is None:
                continue
            phase_id = task["phase_id"] if isinstance(task["phase_id"], str) else ""
            rel_path = task_path.relative_to(project_path).as_posix()
            line = f"- {task['task_id']}: {task['title']} (`{rel_path}`)"
            rows.append((order.get(phase_id, len(order)), status_rank, task_path.name, line))
    if not rows:
        return None
    rows.sort(key=lambda item: (item[0], item[1], item[2]))
    return "\n".join(item[3] for item in rows)


def _dependency_state(project_path: Path, blocked_by: List[str]) -> str:
    """Return a bracketed dependency state label for an open task."""
    if not blocked_by:
        return "ready"
    done_ids = set()
    for task_path in _iter_task_paths(project_path, "done"):
        task = _read_task_record(task_path)
        if task is not None:
            done_ids.add(task["task_id"])
    pending = [task_id for task_id in blocked_by if task_id not in done_ids]
    if pending:
        return f"blocked by: {', '.join(pending)}"
    return "ready"


def _open_work(project_path: Path) -> Optional[str]:
    """Return open work bullet lines ordered by phase, then filename."""
    order = _phase_order(project_path)
    rows: List[tuple[int, str, str]] = []
    for task_path in _iter_task_paths(project_path, "open"):
        task = _read_task_record(task_path)
        if task is None:
            continue
        phase_id = task["phase_id"] if isinstance(task["phase_id"], str) else ""
        rel_path = task_path.relative_to(project_path).as_posix()
        dep_state = _dependency_state(project_path, task["blocked_by"])
        line = f"- {task['task_id']}: {task['title']} (`{rel_path}`) [{dep_state}]"
        rows.append((order.get(phase_id, len(order)), task_path.name, line))
    if not rows:
        return None
    rows.sort(key=lambda item: (item[0], item[1]))
    return "\n".join(item[2] for item in rows)


def _what_to_do_next(project_path: Path) -> Optional[str]:
    """Return a deterministic next-step sentence derived from task placement."""
    for status in _ACTIVE_STATUSES:
        for task_path in _iter_task_paths(project_path, status):
            task = _read_task_record(task_path)
            if task is None:
                continue
            rel_path = task_path.relative_to(project_path).as_posix()
            return f"Continue {task['task_id']} (`{rel_path}`)."

    order = _phase_order(project_path)
    candidates: List[tuple[int, str, str, str]] = []
    for task_path in _iter_task_paths(project_path, "open"):
        task = _read_task_record(task_path)
        if task is None:
            continue
        phase_id = task["phase_id"] if isinstance(task["phase_id"], str) else ""
        dep_state = _dependency_state(project_path, task["blocked_by"])
        if dep_state != "ready":
            continue
        rel_path = task_path.relative_to(project_path).as_posix()
        candidates.append((order.get(phase_id, len(order)), task_path.name, task["task_id"], rel_path))

    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1]))
        task_id = candidates[0][2]
        rel_path = candidates[0][3]
        return f"Start {task_id} (`{rel_path}`)."

    if _open_work(project_path) is not None:
        return "Resolve blocked open work before starting the next task."

    return "No active or open work remains; review closeout readiness."


def _render_body(
    project_name: str,
    current_phase: Optional[str],
    active_work: Optional[str],
    open_work: Optional[str],
    what_to_do_next: Optional[str],
) -> Optional[str]:
    """Render the canonical STATE.md body when plan data exists."""
    if None in (current_phase, active_work, open_work, what_to_do_next):
        return None
    return (
        f"# {project_name} - State\n\n"
        "## Current phase\n\n"
        f"{current_phase}\n\n"
        "## Active work\n\n"
        f"{active_work}\n\n"
        "## Open work\n\n"
        f"{open_work}\n\n"
        "## What to do next\n\n"
        f"{what_to_do_next}"
    )


def _no_plan_record() -> Dict[str, Any]:
    """Return the valid no-plan record shape."""
    return {
        "current_phase": None,
        "active_work": None,
        "open_work": None,
        "what_to_do_next": None,
        "rendered_body": None,
    }


def handler(args: argparse.Namespace) -> int:
    """Emit the canonical STATE.md sections derived from filesystem facts."""
    raw_path = args.project_path
    if not Path(raw_path).is_absolute():
        stderr_usage(f"project_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE

    try:
        project_path = Path(raw_path).resolve(strict=True)
    except FileNotFoundError:
        stderr_error(f"project path does not exist: {raw_path}")
        return 1

    try:
        project_cfg = _load_project_config(project_path)
        project_name = _require_project_keys(
            project_cfg,
            project_path / "cartopian.toml",
        )[1]
    except _CliError as err:
        stderr_error(err.message)
        return err.exit_code

    if not _has_plan_artifacts(project_path):
        emit_record(_no_plan_record())
        return EXIT_OK

    current_phase = _current_phase(project_path)
    active_work = _active_work(project_path) or "None"
    open_work = _open_work(project_path) or "None"
    what_to_do_next = _what_to_do_next(project_path) or "None"

    record = {
        "current_phase": current_phase or "None",
        "active_work": active_work,
        "open_work": open_work,
        "what_to_do_next": what_to_do_next,
        "rendered_body": _render_body(
            project_name,
            current_phase or "None",
            active_work,
            open_work,
            what_to_do_next,
        ),
    }
    emit_record(record)
    return EXIT_OK
