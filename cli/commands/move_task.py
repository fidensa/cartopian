"""`cartopian move-task <task-path> <to-status>`."""
import argparse
import os
import re
import sys
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from cli.commands.resolve_config import _CliError, resolve_review_policy
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE
from cli.provenance import record_write as record_provenance

STATUSES = ("open", "in-progress", "in-review", "done")

# (from_status, to_status) -> disallowance reason. Pairs not present are allowed.
DISALLOWED: Dict[Tuple[str, str], str] = {
    ("open", "open"): "no-op (source=target)",
    ("in-progress", "in-progress"): "no-op",
    ("in-review", "in-review"): "no-op",
    ("done", "open"): "terminal",
    ("done", "in-progress"): "terminal",
    ("done", "in-review"): "terminal",
    ("done", "done"): "no-op terminal",
}

_REVIEW_REQUIRED_DISALLOWED: Dict[Tuple[str, str], str] = {
    ("in-progress", "open"): "use review verdict instead",
    ("in-progress", "done"): "task-closure review is required",
}

_REVIEW_OFF_DISALLOWED: Dict[Tuple[str, str], str] = {
    ("in-progress", "in-review"): "task-closure review is off",
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


def _guard_coder_report(project_root: Path, nn_nnn: str, _task_id: str) -> Optional[str]:
    # The coder (task) handoff is deidentified: the report records no task id.
    # The report *filename* `REPORT-NN-NNN.md` (matched to this task's NN-NNN) is
    # the task link, so existence + `Status: complete` is the whole guard.
    report = project_root / "reports" / f"REPORT-{nn_nnn}.md"
    if not report.is_file():
        return f"missing coder report: {report}"
    try:
        content = report.read_text(encoding="utf-8")
    except OSError:
        return f"report unreadable: {report}"
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
# `open -> in-progress` is deliberately unguarded: the PM moves the task first,
# then writes the prompt against the in-progress path; prompt existence is
# enforced fail-closed at the handoff boundary (`cartopian dispatch`).
_COMMON_GUARDS: Dict[Tuple[str, str], Callable[[Path, str, str], Optional[str]]] = {
    ("in-progress", "in-review"): _guard_coder_report,
}

_REVIEW_GUARDS: Dict[Tuple[str, str], Callable[[Path, str, str], Optional[str]]] = {
    ("in-review", "done"): _guard_review_verdict("approve"),
    ("in-review", "in-progress"): _guard_review_verdict("request-changes"),
    ("in-review", "open"): _guard_review_verdict("reject"),
}

_NO_REVIEW_GUARDS: Dict[Tuple[str, str], Callable[[Path, str, str], Optional[str]]] = {
    ("in-progress", "done"): _guard_coder_report,
}


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "task_path",
        help="Absolute path to the task file under tasks/<status>/",
    )
    subparser.add_argument(
        "--administrative",
        action="store_true",
        help="Allow the explicit administrative open -> done fast-forward",
    )
    subparser.add_argument(
        "--reason",
        default=None,
        help="Required reason for --administrative",
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

    administrative = bool(getattr(args, "administrative", False))
    administrative_reason = getattr(args, "reason", None)
    if administrative:
        if (from_status, to_status) != ("open", "done"):
            _stderr("usage", "--administrative is only valid for open -> done")
            return EXIT_USAGE
        if not isinstance(administrative_reason, str) or not administrative_reason.strip():
            _stderr("usage", "--administrative requires a non-empty --reason")
            return EXIT_USAGE
    elif administrative_reason is not None:
        _stderr("usage", "--reason requires --administrative")
        return EXIT_USAGE

    canonical_suffix = _extract_nn_nnn(task_path)
    if (
        canonical_suffix is not None
        and (from_status, to_status) == ("open", "done")
        and not administrative
    ):
        _stderr(
            "guard",
            "disallowed transition open -> done: use --administrative with --reason",
        )
        return EXIT_FAIL

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

    policy_pairs = (
        set(_REVIEW_REQUIRED_DISALLOWED)
        | set(_REVIEW_OFF_DISALLOWED)
        | set(_REVIEW_GUARDS)
        | set(_NO_REVIEW_GUARDS)
    )
    review_required: Optional[bool] = None
    project_root: Optional[Path] = None
    if canonical_suffix is not None and (from_status, to_status) in policy_pairs:
        project_root = _find_project_root(task_path)
        if project_root is None:
            _stderr("guard", f"project root not found for task: {raw_path}")
            return EXIT_FAIL
        try:
            review_required = (
                resolve_review_policy(project_root)["task_closure"]["mode"]
                == "required"
            )
        except _CliError as err:
            _stderr(err.prefix, err.message)
            return err.exit_code

        regime_disallowed = (
            _REVIEW_REQUIRED_DISALLOWED if review_required else _REVIEW_OFF_DISALLOWED
        )
        reason = regime_disallowed.get((from_status, to_status))
        if reason is not None:
            _stderr(
                "guard",
                f"disallowed transition {from_status} -> {to_status}: {reason}",
            )
            return EXIT_FAIL

    guard_fn = _COMMON_GUARDS.get((from_status, to_status))
    if guard_fn is None and review_required is True:
        guard_fn = _REVIEW_GUARDS.get((from_status, to_status))
    if guard_fn is None and review_required is False:
        guard_fn = _NO_REVIEW_GUARDS.get((from_status, to_status))
    if guard_fn is not None:
        nn_nnn = canonical_suffix
        if nn_nnn is not None:
            project_root = project_root or _find_project_root(task_path)
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

    # A task lifecycle transition is a mediated write (STANDARDS §
    # Project Artifact Standards). The relocation preserves content but lands a
    # new project-relative path, so carry provenance to that path — otherwise a
    # subsequent raw edit to the moved file would read as untracked (advisory)
    # rather than the drift (guard) it is. project root = tasks/<status>/file ->
    # parents[2]. Best-effort: a missed record degrades to advisory, not error.
    project_root = task_path.parents[2]
    try:
        record_provenance(project_root, dest, dest.read_bytes(), action="move-task")
    except OSError:
        pass

    record = {
        "action": "move-task",
        "details": {
            "task_path_before": str(task_path),
            "task_path_after": str(dest),
            "from_status": from_status,
            "to_status": to_status,
        },
    }
    if administrative:
        record["details"]["administrative"] = True
        record["details"]["reason"] = administrative_reason.strip()
    emit_record(record)
    return EXIT_OK
