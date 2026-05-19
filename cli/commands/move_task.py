"""`cartopian move-task <task-path> <to-status>` (FR-004 #4, SPEC-01-001)."""
import argparse
import os
import re
import sys
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE

STATUSES = ("open", "in-progress", "in-review", "done")

# (from_status, to_status) -> disallowance reason. Pairs not present are allowed.
DISALLOWED: Dict[Tuple[str, str], str] = {
    ("open", "open"): "no-op (source=target)",
    ("in-progress", "in-progress"): "no-op",
    ("in-progress", "open"): "use review verdict instead",
    ("in-review", "in-review"): "no-op",
    ("done", "open"): "terminal",
    ("done", "in-progress"): "terminal",
    ("done", "in-review"): "terminal",
    ("done", "done"): "no-op terminal",
}

_TASK_ID_RE = re.compile(r"^TASK-(\d{2}-\d{3})")
_STATUS_RE = re.compile(r"^Status:\s*(.+)$", re.MULTILINE)
_VERDICT_RE = re.compile(r"\bVerdict:\s*(approve|request-changes|reject)\b(?!\s*\|)")


def _stderr(prefix: str, msg: str) -> None:
    sys.stderr.write(f"[{prefix}] {msg}\n")


def _extract_nn_nnn(task_path: Path) -> Optional[str]:
    """Return NN-NNN from a TASK-NN-NNN[-slug].md stem, or None if non-canonical."""
    m = _TASK_ID_RE.match(task_path.stem)
    return m.group(1) if m else None


def _find_project_root(task_path: Path) -> Optional[Path]:
    for candidate in task_path.parents:
        if (candidate / "cartopian.toml").is_file() and (
            (candidate / "phases").is_dir()
            or (candidate / "IMPLEMENTATION_PLAN.md").is_file()
        ):
            return candidate
    return None


def _guard_prompt(project_root: Path, nn_nnn: str, _task_id: str) -> Optional[str]:
    prompt = project_root / "prompts" / f"PROMPT-{nn_nnn}.md"
    if not prompt.is_file():
        return f"missing prompt: {prompt}"
    return None


def _guard_coder_report(project_root: Path, nn_nnn: str, task_id: str) -> Optional[str]:
    report = project_root / "reports" / f"REPORT-{nn_nnn}.md"
    if not report.is_file():
        return f"missing coder report: {report}"
    try:
        content = report.read_text(encoding="utf-8")
    except OSError:
        return f"report unreadable: {report}"
    if f"Task ID: {task_id}" not in content:
        return f"report does not reference {task_id}: {report}"
    m = _STATUS_RE.search(content)
    if not m or m.group(1).strip() != "complete":
        return f"report Status is not 'complete': {report}"
    return None


def _guard_review_verdict(required: str) -> Callable[[Path, str, str], Optional[str]]:
    def _check(project_root: Path, nn_nnn: str, _task_id: str) -> Optional[str]:
        review = project_root / "reviews" / f"REVIEW-{nn_nnn}.md"
        if not review.is_file():
            return f"missing review artifact: {review}"
        try:
            content = review.read_text(encoding="utf-8")
        except OSError:
            return f"review artifact unreadable: {review}"
        m = _VERDICT_RE.search(content)
        if not m:
            return f"no Verdict: field in review artifact: {review}"
        verdict = m.group(1).strip()
        if verdict != required:
            return f"review verdict is '{verdict}', expected '{required}': {review}"
        return None
    return _check


# Transitions that require lifecycle artifact checks.
# Guard fn signature: (project_root, nn_nnn, task_id) -> error_str or None
_GUARDS: Dict[Tuple[str, str], Callable[[Path, str, str], Optional[str]]] = {
    ("open", "in-progress"): _guard_prompt,
    ("in-progress", "in-review"): _guard_coder_report,
    ("in-review", "done"): _guard_review_verdict("approve"),
    ("in-review", "in-progress"): _guard_review_verdict("request-changes"),
    ("in-review", "open"): _guard_review_verdict("reject"),
}


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "task_path",
        help="Absolute path to the task file under tasks/<status>/",
    )
    subparser.add_argument(
        "to_status",
        choices=list(STATUSES),
        help="Target status directory",
    )


def handler(args: argparse.Namespace) -> int:
    raw_path = args.task_path
    if not Path(raw_path).is_absolute():
        _stderr("usage", f"task_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE

    task_path = Path(raw_path)
    from_status = task_path.parent.name
    if from_status not in STATUSES or task_path.parent.parent.name != "tasks":
        _stderr(
            "usage",
            f"task_path parent must be tasks/<status> with status in "
            f"{{{', '.join(STATUSES)}}}; got: {raw_path}",
        )
        return EXIT_USAGE

    to_status = args.to_status

    reason = DISALLOWED.get((from_status, to_status))
    if reason is not None:
        _stderr(
            "guard",
            f"disallowed transition {from_status} -> {to_status}: {reason}",
        )
        return EXIT_FAIL

    if not task_path.is_file():
        _stderr("error", f"task file not found: {raw_path}")
        return EXIT_FAIL

    guard_fn = _GUARDS.get((from_status, to_status))
    if guard_fn is not None:
        nn_nnn = _extract_nn_nnn(task_path)
        if nn_nnn is not None:
            project_root = _find_project_root(task_path)
            if project_root is None:
                _stderr("guard", f"project root not found for task: {raw_path}")
                return EXIT_FAIL
            task_id = f"TASK-{nn_nnn}"
            error = guard_fn(project_root, nn_nnn, task_id)
            if error is not None:
                _stderr("guard", f"{from_status} -> {to_status}: {error}")
                return EXIT_FAIL

    dest = task_path.parent.parent / to_status / task_path.name
    if dest.exists():
        _stderr(
            "guard",
            f"destination already exists for {from_status} -> {to_status}: "
            f"{dest}",
        )
        return EXIT_FAIL

    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        os.rename(task_path, dest)
    except OSError as exc:
        _stderr("error", f"rename failed: {exc}")
        return EXIT_FAIL

    record = {
        "action": "move-task",
        "details": {
            "task_path_before": str(task_path),
            "task_path_after": str(dest),
            "from_status": from_status,
            "to_status": to_status,
        },
    }
    emit_record(record)
    return EXIT_OK
