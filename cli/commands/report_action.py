"""`cartopian report-action <report-path>` aggregator."""
import argparse
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from cli.commands import parse_report
from cli.commands.plan_audit import _resolve_pm_owns_product_branches
from cli.commands.resolve_config import (
    _CliError,
    _load_toml,
    _require_project_keys,
    resolve_review_policy,
)
from cli.emit import emit_record
from cli.main import EXIT_ENV, EXIT_FAIL, EXIT_OK, EXIT_USAGE, stderr_error, stderr_usage

_TASK_ID_RE = re.compile(r"^TASK-(\d{2}-\d{3})(?:-[^/]*)?\.md$")
_TASK_STATUS_DIRS = ("open", "in-progress", "in-review", "done")
_TASK_READY_SECTION_RE = re.compile(
    r"^##\s+(?:Ready to close|Ready for review)\s*$(.*?)(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)
_IDENTITY_SECTION_RE = re.compile(
    r"^##\s+Identity\s*$(.*?)(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    """Add arguments for report-action."""
    subparser.add_argument(
        "report_path",
        help="Path to the report file to parse",
    )
    subparser.add_argument(
        "--variant",
        choices=list(parse_report.VARIANTS),
        default=None,
        help="Explicit variant; overrides filename/content inference",
    )


def _find_project_root(report_path: Path) -> Optional[Path]:
    if report_path.parent.name == "reports":
        return report_path.parent.parent
    for candidate in report_path.parents:
        if (candidate / "reports").is_dir():
            return candidate
    return None


def _load_project_config(project_root: Path) -> Dict[str, Any]:
    project_toml = project_root / "cartopian.toml"
    if not project_toml.is_file():
        raise _CliError(EXIT_ENV, "error", f"project config not found: {project_toml}")
    project_cfg = _load_toml(project_toml, "project config") or {}
    _require_project_keys(project_cfg, project_toml)
    return project_cfg


def _extract_heading_body(pattern: re.Pattern[str], content: str) -> Optional[str]:
    match = pattern.search(content)
    if not match:
        return None
    body = match.group(1).strip()
    return body if body else None


def _extract_identity_map(content: str) -> Dict[str, str]:
    body = _extract_heading_body(_IDENTITY_SECTION_RE, content)
    if body is None:
        return {}
    result: Dict[str, str] = {}
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        payload = stripped[2:]
        if ":" not in payload:
            continue
        key, value = payload.split(":", 1)
        result[key.strip()] = value.strip()
    return result


def _extract_ready_for_review(content: str) -> Optional[bool]:
    body = _extract_heading_body(_TASK_READY_SECTION_RE, content)
    if body is None:
        return None
    first_line = body.splitlines()[0].strip().lower()
    if first_line == "yes":
        return True
    if first_line == "no":
        return False
    return None


def _parse_report_state(
    report_path: Path,
    content: str,
    explicit_variant: Optional[str],
) -> Tuple[str, str, Optional[str], Optional[str]]:
    if explicit_variant:
        variant = explicit_variant
    else:
        variant, err = parse_report._infer_variant(report_path, content)
        if variant is None:
            raise _CliError(EXIT_USAGE, "usage", err or "cannot infer variant")

    if not parse_report._schema_ok(variant, content):
        return "failed-to-parse", variant, None, None

    raw_status = parse_report._extract_status(content)
    if raw_status is None or raw_status not in parse_report.STATUS_VERDICT:
        return "failed-to-parse", variant, None, None

    if variant in parse_report.REVIEW_VARIANTS:
        raw_verdict = parse_report._extract_review_verdict(content)
        if raw_status == "complete":
            if raw_verdict is None:
                return "failed-to-parse", variant, None, None
            return parse_report.REVIEW_VERDICT_OUTCOME[raw_verdict], variant, raw_status, raw_verdict
        return parse_report.STATUS_VERDICT[raw_status], variant, raw_status, raw_verdict

    return parse_report.STATUS_VERDICT[raw_status], variant, raw_status, None


def _report_suffix(report_path: Path, variant: str) -> Optional[str]:
    stem = report_path.stem
    if variant == "planning-review":
        prefix = "REPORT-PLAN-"
        if stem.startswith(prefix):
            return stem[len(prefix):]
        return None
    prefix = "REPORT-"
    if stem.startswith(prefix):
        return stem[len(prefix):]
    return None


def _find_expected_task_path(project_root: Path, task_id: str) -> Optional[Path]:
    for status in _TASK_STATUS_DIRS:
        status_dir = project_root / "tasks" / status
        if not status_dir.is_dir():
            continue
        direct = status_dir / f"{task_id}.md"
        if direct.is_file():
            return direct.resolve()
        for candidate in sorted(status_dir.glob(f"{task_id}-*.md")):
            if candidate.is_file():
                return candidate.resolve()
    return None


def _task_declares_work_roots(task_path: Optional[Path]) -> bool:
    if task_path is None or not task_path.is_file():
        return False
    try:
        content = task_path.read_text(encoding="utf-8")
    except OSError:
        return False
    match = re.search(r"^Work root:\s*(.+)$", content, re.MULTILINE)
    if not match:
        return False
    raw = match.group(1).strip().lower()
    return raw not in {"", "n/a", "none"}


def _expected_paths(
    project_root: Path,
    report_path: Path,
    variant: str,
    identity: Dict[str, str],
) -> Dict[str, Optional[Path]]:
    suffix = _report_suffix(report_path, variant)
    expected_prompt_path: Optional[Path] = None
    expected_review_path: Optional[Path] = None
    expected_task_path: Optional[Path] = None
    expected_review_id: Optional[str] = None

    if suffix is not None:
        if variant == "task":
            task_id = f"TASK-{suffix}"
            expected_prompt_path = (project_root / "prompts" / f"PROMPT-{suffix}.md").resolve()
            expected_review_path = (project_root / "reviews" / f"REVIEW-{suffix}.md").resolve()
            expected_task_path = _find_expected_task_path(project_root, task_id)
        elif variant == "review":
            expected_prompt_path = (project_root / "prompts" / f"PROMPT-{suffix}.md").resolve()
            expected_review_id = f"REVIEW-{suffix}"
            expected_review_path = (project_root / "reviews" / f"{expected_review_id}.md").resolve()
        else:
            expected_prompt_path = (project_root / "prompts" / f"PROMPT-PLAN-{suffix}.md").resolve()
            expected_review_id = f"REVIEW-PLAN-{suffix}"
            expected_review_path = (project_root / "reviews" / f"{expected_review_id}.md").resolve()

    return {
        "expected_prompt_path": expected_prompt_path,
        "expected_review_path": expected_review_path,
        "expected_task_path": expected_task_path,
        "expected_review_id": Path(expected_review_id) if expected_review_id is not None else None,
    }


def _normalize_path_value(value: str) -> Optional[str]:
    """Strip cosmetic markdown wrapping from an Identity path value.

    Report authors sometimes wrap path values in a markdown code span
    (`` `…` ``). The backticks are not part of the path; left in place they
    make the value parse as relative and produce a false path mismatch
    (AR-5). Strip surrounding backticks and whitespace before resolving;
    return None for an empty/blank value.
    """
    value = value.strip().strip("`").strip()
    return value or None


def _path_from_identity(identity: Dict[str, str], key: str) -> Optional[Path]:
    value = identity.get(key)
    if value is None:
        return None
    normalized = _normalize_path_value(value)
    if normalized is None:
        return None
    return Path(normalized).resolve()


def _task_path_mismatch(
    identity: Dict[str, str],
    expected_prompt_path: Optional[Path],
    expected_task_path: Optional[Path],
    expected_task_id: Optional[str],
) -> bool:
    """Whether a task report's filename fails to resolve to a real task.

    The coder (task) handoff is deidentified: the report carries no Identity
    ids/paths, and the report *filename* (`REPORT-NN-NNN.md`) is the source of
    truth for the task link. So the only hard requirement is that a task on disk
    matches that filename. Any Identity id/path a legacy report still declares is
    cross-checked when present (a stale or wrong value is a mismatch) but is
    never required.
    """
    if expected_task_path is None:
        return True  # no task on disk matches this report's filename
    declared_prompt_path = _path_from_identity(identity, "Prompt path")
    if declared_prompt_path is not None and declared_prompt_path != expected_prompt_path:
        return True
    declared_task_path = _path_from_identity(identity, "Task path")
    if declared_task_path is not None and declared_task_path != expected_task_path:
        return True
    declared_task_id = identity.get("Task ID")
    if declared_task_id is not None and declared_task_id != expected_task_id:
        return True
    return False


def _review_path_mismatch(
    identity: Dict[str, str],
    expected_prompt_path: Optional[Path],
    expected_review_path: Optional[Path],
    expected_review_id: Optional[str],
) -> bool:
    declared_prompt_path = _path_from_identity(identity, "Prompt path")
    declared_review_path = _path_from_identity(identity, "Review file path")
    declared_review_id = identity.get("Review ID")
    if declared_prompt_path != expected_prompt_path:
        return True
    if expected_review_path is None or declared_review_path != expected_review_path:
        return True
    if declared_review_id != expected_review_id:
        return True
    return not declared_review_path.exists()


def _target_task_status(
    variant: str,
    verdict: str,
    ready_for_review: Optional[bool],
    task_review_required: bool = True,
) -> Optional[str]:
    if verdict == "failed-to-parse":
        return None
    if variant == "task":
        if verdict == "accepted":
            if ready_for_review is True:
                return "in-review" if task_review_required else "done"
            if ready_for_review is False:
                return "in-progress"
            return None
        if verdict in {"blocked", "failed"}:
            return "in-progress"
        return None
    if verdict == "accepted":
        return "done"
    if verdict == "changes-requested":
        return "in-progress"
    if verdict == "rejected":
        return "open"
    if verdict in {"blocked", "failed"}:
        return "in-review"
    return None


def _prompt_to_overwrite(
    variant: str,
    verdict: str,
    ready_for_review: Optional[bool],
    prompt_path: Optional[Path],
    task_review_required: bool = True,
) -> Optional[str]:
    if prompt_path is None:
        return None
    if variant == "task":
        if (
            task_review_required
            and verdict == "accepted"
            and ready_for_review is True
        ):
            return str(prompt_path)
        return None
    if verdict in {"accepted", "changes-requested", "rejected"}:
        return str(prompt_path)
    return None


def _review_path_output(
    variant: str,
    verdict: str,
    ready_for_review: Optional[bool],
    expected_review_path: Optional[Path],
    declared_review_path: Optional[Path],
    task_review_required: bool = True,
) -> Optional[str]:
    if variant == "task":
        if (
            task_review_required
            and verdict == "accepted"
            and ready_for_review is True
            and expected_review_path is not None
        ):
            return str(expected_review_path)
        return None
    if declared_review_path is not None:
        return str(declared_review_path)
    if expected_review_path is not None:
        return str(expected_review_path)
    return None


def _recommended_action(
    variant: str,
    verdict: str,
    ready_for_review: Optional[bool],
    requires_pr_step: bool,
    task_review_required: bool = True,
) -> str:
    if verdict == "failed-to-parse":
        return "stop-for-inspection"
    if variant == "task":
        if verdict == "accepted":
            if ready_for_review is True:
                if not task_review_required:
                    if requires_pr_step:
                        return "prepare-pr-and-close-task"
                    return "close-task"
                if requires_pr_step:
                    return "prepare-pr-and-assign-review"
                return "assign-review"
            if ready_for_review is False:
                return "return-control-to-operator"
        if verdict in {"blocked", "failed"}:
            return "return-control-to-operator"
    else:
        if verdict == "accepted":
            if requires_pr_step:
                return "merge-pr-and-close-task"
            return "close-task"
        if verdict == "changes-requested":
            return "return-task-to-in-progress"
        if verdict == "rejected":
            return "return-task-to-open"
        if verdict in {"blocked", "failed"}:
            return "return-control-to-operator"
    return "return-control-to-operator"


def handler(args: argparse.Namespace) -> int:
    """Parse a handoff report and emit a single routing record."""
    raw_path = args.report_path
    if not Path(raw_path).is_absolute():
        stderr_usage(f"report_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE

    report_path = Path(raw_path)
    if not report_path.is_file():
        stderr_error(f"report not found: {raw_path}")
        return EXIT_FAIL

    report_path = report_path.resolve()
    try:
        content = report_path.read_text(encoding="utf-8")
    except OSError as exc:
        stderr_error(f"report unreadable: {raw_path} — {exc}")
        return EXIT_FAIL

    project_root = _find_project_root(report_path)
    if project_root is None:
        stderr_error(f"project config not found for report: {raw_path}")
        return EXIT_ENV

    try:
        _load_project_config(project_root)
        review_policy = resolve_review_policy(project_root)
    except _CliError as err:
        stderr_error(err.message)
        return err.exit_code

    try:
        verdict, variant, status_value, review_verdict = _parse_report_state(
            report_path,
            content,
            args.variant,
        )
    except _CliError as err:
        if err.exit_code == EXIT_USAGE:
            stderr_usage(err.message)
        else:
            stderr_error(err.message)
        return err.exit_code

    identity = _extract_identity_map(content)
    declared_task_path = _path_from_identity(identity, "Task path")
    declared_review_path = _path_from_identity(identity, "Review file path")
    ready_for_review = _extract_ready_for_review(content) if variant == "task" else None

    expected = _expected_paths(project_root, report_path, variant, identity)
    expected_prompt_path = expected["expected_prompt_path"]
    expected_review_path = expected["expected_review_path"]
    expected_task_path = expected["expected_task_path"]
    expected_review_id_obj = expected["expected_review_id"]
    expected_review_id = expected_review_id_obj.name if expected_review_id_obj is not None else None
    expected_task_id = f"TASK-{_report_suffix(report_path, variant)}" if variant == "task" else None

    if verdict == "failed-to-parse":
        path_mismatch = False
    elif variant == "task":
        path_mismatch = _task_path_mismatch(
            identity,
            expected_prompt_path,
            expected_task_path,
            expected_task_id,
        )
    else:
        path_mismatch = _review_path_mismatch(
            identity,
            expected_prompt_path,
            expected_review_path,
            expected_review_id,
        )

    task_path_for_pr = expected_task_path if expected_task_path is not None else declared_task_path
    requires_pr_step = False
    if verdict != "failed-to-parse" and task_path_for_pr is not None:
        if _resolve_pm_owns_product_branches(project_root) and _task_declares_work_roots(task_path_for_pr):
            if variant == "task":
                requires_pr_step = verdict == "accepted" and ready_for_review is True
            else:
                requires_pr_step = verdict == "accepted"

    task_review_required = review_policy["task_closure"]["mode"] == "required"
    target_task_status = _target_task_status(
        variant, verdict, ready_for_review, task_review_required
    )
    # Use the filename-derived prompt path: the deidentified task report no
    # longer declares a `Prompt path:`, and the expected path is authoritative.
    prompt_to_overwrite = _prompt_to_overwrite(
        variant,
        verdict,
        ready_for_review,
        expected_prompt_path,
        task_review_required,
    )
    review_path = _review_path_output(
        variant,
        verdict,
        ready_for_review,
        expected_review_path,
        declared_review_path,
        task_review_required,
    )
    record = {
        "verdict": verdict,
        "variant": variant,
        "report_path": str(report_path),
        "status": status_value,
        "review_verdict": review_verdict,
        "target_task_status": target_task_status,
        "requires_pr_step": requires_pr_step,
        "prompt_to_overwrite": prompt_to_overwrite,
        "review_path": review_path,
        "declared_report_task_path": str(declared_task_path) if declared_task_path is not None else None,
        "path_mismatch": path_mismatch,
        "report_id": report_path.stem,
        "task_path": str(expected_task_path) if expected_task_path is not None else None,
        "task_id": expected_task_id,
        "expected_prompt_path": str(expected_prompt_path) if expected_prompt_path is not None else None,
        "expected_review_path": str(expected_review_path) if expected_review_path is not None else None,
        "expected_task_path": str(expected_task_path) if expected_task_path is not None else None,
        "recommended_action": _recommended_action(
            variant,
            verdict,
            ready_for_review,
            requires_pr_step,
            task_review_required,
        ),
    }
    emit_record(record)
    return EXIT_OK
