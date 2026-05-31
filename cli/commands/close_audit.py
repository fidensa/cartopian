"""`cartopian close-audit <project-path>` aggregator (FR-005)."""
import argparse
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

from cli.commands.resolve_config import _CliError, _load_toml, _require_project_keys
from cli.emit import emit_record
from cli.main import EXIT_ENV, EXIT_OK, EXIT_USAGE, stderr_error, stderr_usage

_TASK_ID_RE = re.compile(r"^(TASK-\d{2}-\d{3})(?:-[^/]*)?\.md$")
_PROMPT_TASK_RE = re.compile(r"^PROMPT-(\d{2}-\d{3})(?:-[^/]*)?\.md$")
_REPORT_TASK_RE = re.compile(r"^REPORT-(\d{2}-\d{3})(?:-[^/]*)?\.md$")
_EXIT_CRITERIA_RE = re.compile(
    r"^##\s+Exit criteria\s*$(.*?)(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)
_CRITERION_ID_RE = re.compile(
    r"\b("
    r"TASK-\d{2}-\d{3}"
    r"|DEC(?:ISION)?-[A-Za-z0-9-]+"
    r"|SPEC-\d{2}-\d{3}(?:-[A-Za-z0-9-]+)?"
    r"|REVIEW(?:-PLAN)?-[A-Za-z0-9-]+"
    r"|REPORT(?:-PLAN)?-[A-Za-z0-9-]+"
    r")\b"
)
_PLAN_DIRS = (
    "phases",
    "prompts",
    "reports",
    "reviews",
    "decisions",
    "specs",
)
_ACTIVE_STATUSES = ("open", "in-progress", "in-review")


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    """Add arguments for close-audit."""
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


def _iter_matching_files(directory: Path, pattern: re.Pattern[str]) -> Iterable[Path]:
    if not directory.is_dir():
        return []
    return [
        path for path in sorted(directory.iterdir(), key=lambda candidate: candidate.name)
        if path.is_file() and pattern.match(path.name)
    ]


def _has_plan_artifacts(project_root: Path) -> bool:
    if (project_root / "IMPLEMENTATION_PLAN.md").is_file():
        return True
    for dirname in _PLAN_DIRS:
        directory = project_root / dirname
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.is_file() and path.suffix == ".md":
                return True
    for status in ("open", "in-progress", "in-review", "done"):
        if any(_iter_matching_files(project_root / "tasks" / status, _TASK_ID_RE)):
            return True
    return False


def _collect_active_tasks(project_root: Path) -> List[Dict[str, str]]:
    records: List[Dict[str, str]] = []
    for status in _ACTIVE_STATUSES:
        for task_path in _iter_matching_files(project_root / "tasks" / status, _TASK_ID_RE):
            match = _TASK_ID_RE.match(task_path.name)
            if match is None:
                continue
            records.append(
                {
                    "task_id": match.group(1),
                    "path": str(task_path.resolve()),
                    "status": status,
                }
            )
    return records


def _collect_stale_prompts(project_root: Path) -> List[Dict[str, str]]:
    prompted_task_ids = {
        task["task_id"]
        for task in _collect_active_tasks(project_root)
        if task["status"] in {"in-progress", "in-review"}
    }
    stale_prompts: List[Dict[str, str]] = []
    for prompt_path in _iter_matching_files(project_root / "prompts", _PROMPT_TASK_RE):
        match = _PROMPT_TASK_RE.match(prompt_path.name)
        if match is None:
            continue
        task_id = f"TASK-{match.group(1)}"
        if task_id not in prompted_task_ids:
            stale_prompts.append(
                {
                    "path": str(prompt_path.resolve()),
                    "task_id": task_id,
                }
            )
    return stale_prompts


def _task_in_done(project_root: Path, task_id: str) -> bool:
    done_dir = project_root / "tasks" / "done"
    for task_path in _iter_matching_files(done_dir, _TASK_ID_RE):
        match = _TASK_ID_RE.match(task_path.name)
        if match is not None and match.group(1) == task_id:
            return True
    return False


def _collect_unresolved_reports(project_root: Path) -> List[Dict[str, str]]:
    unresolved: List[Dict[str, str]] = []
    for report_path in _iter_matching_files(project_root / "reports", _REPORT_TASK_RE):
        match = _REPORT_TASK_RE.match(report_path.name)
        if match is None:
            continue
        suffix = match.group(1)
        task_id = f"TASK-{suffix}"
        prompt_path = project_root / "prompts" / f"PROMPT-{suffix}.md"
        if _task_in_done(project_root, task_id):
            continue
        if not prompt_path.exists():
            continue
        unresolved.append({"path": str(report_path.resolve())})
    return unresolved


def _extract_exit_criteria(content: str) -> List[str]:
    match = _EXIT_CRITERIA_RE.search(content)
    if match is None:
        return []
    items: List[str] = []
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            items.append(stripped[2:].strip())
    return items


def _criterion_token_exists(project_root: Path, token: str) -> bool:
    if token.startswith("TASK-"):
        return _task_in_done(project_root, token)
    if token.startswith("DECISION-") or token.startswith("DEC-"):
        return any(token in path.stem for path in (project_root / "decisions").glob("*.md"))
    if token.startswith("SPEC-"):
        return any(path.stem.startswith(token) for path in (project_root / "specs").glob("*.md"))
    if token.startswith("REVIEW-"):
        return any(path.stem == token for path in (project_root / "reviews").glob("*.md"))
    if token.startswith("REPORT-"):
        return any(path.stem == token for path in (project_root / "reports").glob("*.md"))
    return False


def _collect_unmet_exit_criteria(project_root: Path) -> List[str]:
    unmet: List[str] = []
    phases_dir = project_root / "phases"
    if not phases_dir.is_dir():
        return unmet
    for phase_path in sorted(phases_dir.glob("PHASE-*.md")):
        try:
            content = phase_path.read_text(encoding="utf-8")
        except OSError:
            unmet.append(f"{phase_path.stem}: exit criteria unreadable")
            continue
        for criterion in _extract_exit_criteria(content):
            tokens = _CRITERION_ID_RE.findall(criterion)
            if not tokens:
                continue
            missing = [token for token in tokens if not _criterion_token_exists(project_root, token)]
            if missing:
                missing_list = ", ".join(missing)
                unmet.append(f"{phase_path.stem}: unmet exit criterion `{criterion}` (missing: {missing_list})")
    return unmet


def _no_plan_record(project_id: str, project_path: Path) -> Dict[str, Any]:
    return {
        "project_id": project_id,
        "project_path": str(project_path.resolve()),
        "closable": None,
        "open_count": None,
        "in_progress_count": None,
        "in_review_count": None,
        "open_tasks": None,
        "stale_prompts": None,
        "unresolved_reports": None,
        "unmet_exit_criteria": None,
        "blocking_reasons": None,
    }


def handler(args: argparse.Namespace) -> int:
    """Emit a single closeout-audit decision packet for the given project."""
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
        project_id = _require_project_keys(
            project_cfg,
            project_path / "cartopian.toml",
        )[0]
    except _CliError as err:
        stderr_error(err.message)
        return err.exit_code

    if not _has_plan_artifacts(project_path):
        emit_record(_no_plan_record(project_id, project_path))
        return EXIT_OK

    open_tasks = _collect_active_tasks(project_path)
    open_count = sum(1 for task in open_tasks if task["status"] == "open")
    in_progress_count = sum(1 for task in open_tasks if task["status"] == "in-progress")
    in_review_count = sum(1 for task in open_tasks if task["status"] == "in-review")
    stale_prompts = _collect_stale_prompts(project_path)
    unresolved_reports = _collect_unresolved_reports(project_path)
    unmet_exit_criteria = _collect_unmet_exit_criteria(project_path)

    blocking_reasons: List[str] = []
    for task in open_tasks:
        blocking_reasons.append(
            f"active task blocks closeout: {task['task_id']} remains in {task['status']}"
        )
    for prompt in stale_prompts:
        blocking_reasons.append(
            f"stale prompt blocks closeout: {prompt['task_id']} -> {prompt['path']}"
        )
    for report in unresolved_reports:
        report_name = Path(report["path"]).name
        blocking_reasons.append(
            f"unresolved report blocks closeout: {report_name}"
        )
    for criterion in unmet_exit_criteria:
        blocking_reasons.append(f"phase exit criteria incomplete: {criterion}")

    record = {
        "project_id": project_id,
        "project_path": str(project_path.resolve()),
        "closable": not blocking_reasons,
        "open_count": open_count,
        "in_progress_count": in_progress_count,
        "in_review_count": in_review_count,
        "open_tasks": open_tasks,
        "stale_prompts": stale_prompts,
        "unresolved_reports": unresolved_reports,
        "unmet_exit_criteria": unmet_exit_criteria,
        "blocking_reasons": blocking_reasons,
    }
    emit_record(record)
    return EXIT_OK
