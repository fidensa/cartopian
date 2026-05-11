"""`cartopian move-task <task-path> <to-status>` (FR-004 #4, SPEC-01-001)."""
import argparse
import os
import sys
from pathlib import Path
from typing import Dict, Tuple

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


def _stderr(prefix: str, msg: str) -> None:
    sys.stderr.write(f"[{prefix}] {msg}\n")


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
