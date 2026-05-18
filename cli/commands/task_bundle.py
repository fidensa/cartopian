"""`cartopian task-bundle <task-path>` aggregator (FR-002)."""
import argparse
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from cli.commands.resolve_config import _CliError, _load_toml, _resolve_work_roots
from cli.commands.validate_task_readiness import (
    CHECK_ORDER,
    _check_acceptance,
    _check_blocked_by,
    _check_evidence_gate,
    _check_phase,
    _check_plan_ref,
    _check_work_root,
    _find_project_root,
    _parse_headers,
    _split_csv,
)
from cli.emit import emit_record
from cli.main import EXIT_ENV, EXIT_FAIL, EXIT_OK, EXIT_USAGE, stderr_error, stderr_usage

_TASK_ID_RE = re.compile(r"^(TASK-\d{2}-\d{3})(?:-[^/]*)?$")
_STATUS_DIRS = ("open", "in-progress", "in-review", "done")


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    """Add arguments for task-bundle."""
    subparser.add_argument(
        "task_path",
        help="Absolute path to the task file",
    )


def _first_heading(content: str) -> str:
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _extract_task_id(task_path: Path) -> Optional[str]:
    match = _TASK_ID_RE.match(task_path.stem)
    if match is None:
        return None
    return match.group(1)


def _resolve_spec_path(project_root: Path, headers: Dict[str, str]) -> Optional[str]:
    raw_spec = headers.get("Spec", "").strip()
    if not raw_spec or raw_spec.lower() in {"none", "n/a"}:
        return None
    candidate = Path(raw_spec)
    if not candidate.is_absolute():
        if candidate.parts and candidate.parts[0] == "specs":
            candidate = project_root / candidate
        else:
            candidate = project_root / "specs" / candidate
    return str(candidate.resolve())


def _read_task_title(task_path: Path, fallback: Optional[str]) -> Optional[str]:
    try:
        content = task_path.read_text(encoding="utf-8")
    except OSError:
        return fallback
    title = _first_heading(content)
    if title:
        return title
    return fallback


def _find_dependency(project_root: Path, task_id: str) -> Dict[str, Any]:
    fallback = {
        "task_id": task_id,
        "title": None,
        "path": None,
        "status": None,
    }
    for status in _STATUS_DIRS:
        status_dir = project_root / "tasks" / status
        if not status_dir.is_dir():
            continue
        matches = sorted(status_dir.glob(f"{task_id}-*.md"))
        direct = status_dir / f"{task_id}.md"
        if direct.is_file():
            matches = [direct, *matches]
        if not matches:
            continue
        match = matches[0].resolve()
        return {
            "task_id": task_id,
            "title": _read_task_title(match, task_id),
            "path": str(match),
            "status": status,
        }
    return fallback


def _collect_dependencies(project_root: Path, headers: Dict[str, str]) -> List[Dict[str, Any]]:
    raw = headers.get("Blocked by", "").strip()
    if not raw or raw.lower() in {"n/a", "none"}:
        return []
    return [_find_dependency(project_root, task_id) for task_id in _split_csv(raw)]


def _collect_work_roots(
    project_root: Path,
    project_cfg: Dict[str, Any],
    headers: Dict[str, str],
) -> List[Dict[str, Any]]:
    raw = headers.get("Work root", "").strip()
    if not raw or raw.lower() in {"n/a", "none"}:
        return []
    names = _split_csv(raw)
    try:
        resolved = _resolve_work_roots(project_cfg, project_root)
    except _CliError as err:
        if err.exit_code == EXIT_ENV:
            raise
        resolved = {}
    records: List[Dict[str, Any]] = []
    for name in names:
        absolute_path = resolved.get(name)
        path_obj = Path(absolute_path).resolve() if absolute_path is not None else None
        records.append(
            {
                "name": name,
                "absolute_path": str(path_obj) if path_obj is not None else None,
                "exists": path_obj.exists() if path_obj is not None else False,
            }
        )
    return records


def _build_validation_checks(
    project_root: Path,
    content: str,
    headers: Dict[str, str],
    presence: Dict[str, bool],
) -> List[Dict[str, Any]]:
    warnings: List[str] = []
    checks_by_name = {
        "phase-exists": _check_phase(project_root, headers),
        "plan-ref-exists": _check_plan_ref(project_root, headers),
        "blocked-by-complete": _check_blocked_by(project_root, headers),
        "evidence-gate-valid": _check_evidence_gate(headers, presence),
        "acceptance-present": _check_acceptance(content),
        "work-root-names-valid": _check_work_root(project_root, headers, presence, warnings),
    }
    return [checks_by_name[name] for name in CHECK_ORDER]


def _validator_blockers(checks: List[Dict[str, Any]]) -> List[str]:
    blockers: List[str] = []
    for check in checks:
        if check["pass"]:
            continue
        reason = check.get("reason")
        if reason:
            blockers.append(f"{check['name']}: {reason}")
    return blockers


def handler(args: argparse.Namespace) -> int:
    """Emit a bundled readiness record for the given task."""
    raw_path = args.task_path
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
        stderr_error(f"project root not found for task: {raw_path}")
        return EXIT_FAIL

    toml_path = project_root / "cartopian.toml"
    try:
        project_cfg = _load_toml(toml_path, "project config")
    except _CliError as err:
        stderr_error(err.message)
        return err.exit_code
    if project_cfg is None:
        stderr_error(f"project config not found: {toml_path}")
        return EXIT_ENV

    headers, presence = _parse_headers(content)
    checks = _build_validation_checks(project_root, content, headers, presence)
    ready = all(check["pass"] for check in checks)

    try:
        work_roots_resolved = _collect_work_roots(project_root, project_cfg, headers)
    except _CliError as err:
        stderr_error(err.message)
        return err.exit_code

    task_id = _extract_task_id(task_path)
    task_title = _first_heading(content) or task_path.stem
    if task_id is None:
        task_id = task_path.stem
    nn_nnn = task_id.removeprefix("TASK-")

    record = {
        "task_id": task_id,
        "task_title": task_title,
        "task_path": str(task_path),
        "task_status": task_path.parent.name,
        "spec_path": _resolve_spec_path(project_root, headers),
        "dependencies": _collect_dependencies(project_root, headers),
        "work_roots_resolved": work_roots_resolved,
        "ready": ready,
        "validator_blockers": _validator_blockers(checks),
        "expected_prompt_path": str((project_root / "prompts" / f"PROMPT-{nn_nnn}.md").resolve()),
        "expected_report_path": str((project_root / "reports" / f"REPORT-{nn_nnn}.md").resolve()),
    }
    emit_record(record)
    return EXIT_OK
