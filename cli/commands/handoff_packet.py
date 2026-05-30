"""`cartopian handoff-packet <task-path> --role <role>` aggregator (FR-003).

Folds the handoff-packet assembly chain (resolved roles, handoff block,
work-root absolute paths, expected report path, git policy) into a single
NDJSON call. Read-only; no file writes, moves, renames, or deletes.
"""
import argparse
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from cli.commands.resolve_config import (
    _CliError,
    _load_toml,
    _resolve_automation,
    _resolve_git_block,
    _resolve_git_versioning,
    _resolve_handoffs,
    _resolve_roles,
    _resolve_work_roots,
)
from cli.emit import emit_record
from cli.main import (
    EXIT_ENV,
    EXIT_FAIL,
    EXIT_OK,
    EXIT_USAGE,
    stderr_error,
    stderr_guard,
    stderr_usage,
)

_TASK_ID_RE = re.compile(r"^(TASK-\d{2}-\d{3})(?:-[^/]*)?$")


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    """Add arguments for handoff-packet."""
    subparser.add_argument(
        "task_path",
        help="Absolute path to the task file",
    )
    subparser.add_argument(
        "--role",
        required=True,
        help="Role identifier being dispatched (must have a [handoffs.<role>] block)",
    )


def _first_heading(content: str) -> str:
    """Return the text after `# ` from the first top-level heading."""
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _extract_task_id(task_path: Path) -> Optional[str]:
    """Extract `TASK-NN-NNN` from the task filename stem, or None."""
    match = _TASK_ID_RE.match(task_path.stem)
    if match is None:
        return None
    return match.group(1)


def _find_project_root(task_path: Path) -> Optional[Path]:
    """Walk up from the task file to find the project root.

    A project root has a ``cartopian.toml`` plus either a ``phases/``
    directory or an ``IMPLEMENTATION_PLAN.md`` file.
    """
    for candidate in [task_path.parent] + list(task_path.parents):
        if (candidate / "cartopian.toml").is_file() and (
            (candidate / "phases").is_dir()
            or (candidate / "IMPLEMENTATION_PLAN.md").is_file()
        ):
            return candidate
    # Fall back to any ancestor carrying a cartopian.toml so a missing
    # config can still surface as EXIT_ENV rather than EXIT_FAIL.
    for candidate in [task_path.parent] + list(task_path.parents):
        if (candidate / "cartopian.toml").is_file():
            return candidate
    return None


def _build_work_roots(
    project_root: Path,
    project_cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Return an ordered list of ``{name, absolute_path}`` for all declared work roots.

    Names with no per-machine mapping resolve to ``absolute_path = null``.
    Re-raises ``_CliError`` with ``EXIT_ENV`` (unreadable local config).
    """
    project_table = project_cfg.get("project", {}) or {}
    names = project_table.get("work_roots", []) or []
    try:
        resolved = _resolve_work_roots(project_cfg, project_root)
    except _CliError as err:
        if err.exit_code == EXIT_ENV:
            raise
        resolved = {}
    return [{"name": name, "absolute_path": resolved.get(name)} for name in names]


def _expected_report_path(project_root: Path, task_id: str) -> Path:
    """Return the protocol-derived expected report path for a task.

    The report path is task-derived (``reports/REPORT-NN-NNN.md``), not
    role-derived. Shared with ``wait-handoff`` so both commands resolve the
    expected report path identically.
    """
    nn_nnn = task_id.removeprefix("TASK-") if task_id.startswith("TASK-") else task_id
    return (project_root / "reports" / f"REPORT-{nn_nnn}.md").resolve()


def _build_git_policy(git_block: Dict[str, Any]) -> Dict[str, Any]:
    """Project the resolved git block down to the FR-003 git_policy shape."""
    return {
        "branch_strategy": git_block.get("branch_strategy"),
        "auto_commit": git_block.get("auto_commit"),
        "auto_push": git_block.get("auto_push"),
    }


def handler(args: argparse.Namespace) -> int:
    """Handle handoff-packet command.

    Reads the task file and its enclosing project config, resolves the
    handoff block for ``--role``, and emits a single NDJSON record with
    the fields PMs need to compose a handoff prompt.
    """
    raw_path: str = args.task_path
    role: str = args.role

    if not Path(raw_path).is_absolute():
        stderr_usage(f"task_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE

    task_path = Path(raw_path)
    if not task_path.is_file():
        stderr_error(f"task file not found: {raw_path}")
        return EXIT_FAIL

    task_path = task_path.resolve()
    try:
        content = task_path.read_text(encoding="utf-8")
    except OSError as exc:
        stderr_error(f"task file unreadable: {raw_path} — {exc}")
        return EXIT_FAIL

    project_root = _find_project_root(task_path)
    if project_root is None:
        stderr_error(f"project config not found for task: {raw_path}")
        return EXIT_ENV

    project_toml = project_root / "cartopian.toml"
    if not project_toml.is_file():
        stderr_error(f"project config not found: {project_toml}")
        return EXIT_ENV

    try:
        project_cfg = _load_toml(project_toml, "project config") or {}
    except _CliError as err:
        stderr_error(err.message)
        return err.exit_code

    global_toml = Path.home() / ".cartopian" / "cartopian.toml"
    try:
        global_cfg = _load_toml(global_toml, "global config") or {}
    except _CliError as err:
        stderr_error(err.message)
        return err.exit_code

    handoffs = _resolve_handoffs(global_cfg, project_cfg)
    roles = _resolve_roles(global_cfg, project_cfg)

    raw_handoffs_project = project_cfg.get("handoffs", {}) or {}
    raw_handoffs_global = global_cfg.get("handoffs", {}) or {}
    role_block_present = role in raw_handoffs_project or role in raw_handoffs_global
    if not role_block_present:
        stderr_guard(
            f"no [handoffs.{role}] block configured — declare it in the project "
            f"or global cartopian.toml, or dispatch this role manually"
        )
        return EXIT_FAIL

    role_handoff = handoffs.get(role, {}) or {}
    automation = _resolve_automation(global_cfg, project_cfg)
    git_versioning, _attribution = _resolve_git_versioning(global_cfg, project_cfg)
    git_policy: Optional[Dict[str, Any]]
    if git_versioning:
        git_policy = _build_git_policy(_resolve_git_block(global_cfg, project_cfg))
    else:
        git_policy = None

    try:
        work_roots = _build_work_roots(project_root, project_cfg)
    except _CliError as err:
        stderr_error(err.message)
        return err.exit_code

    task_id = _extract_task_id(task_path) or task_path.stem
    task_title = _first_heading(content) or task_path.stem
    expected_report_path = _expected_report_path(project_root, task_id)

    record: Dict[str, Any] = {
        "task_id": task_id,
        "task_title": task_title,
        "task_path": str(task_path),
        "role": role,
        "role_description": roles.get(role),
        "handoff_target": role_handoff.get("agent"),
        "auto_start": role_handoff.get("auto_start"),
        "timeout": role_handoff.get("timeout"),
        "work_roots": work_roots,
        "expected_report_path": str(expected_report_path),
        "git_versioning": git_versioning,
        "git_policy": git_policy,
        "automation_policy": automation,
    }
    emit_record(record)
    return EXIT_OK
