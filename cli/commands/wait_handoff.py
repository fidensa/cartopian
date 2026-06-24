"""`cartopian wait-handoff <task-path> --role <role> --max-block <duration>`.

Read-only observer that monitors one handoff. It resolves the expected report
path from the task file (the same task-derived ``reports/REPORT-NN-NNN.md``
logic used by ``handoff-packet``) and filesystem-polls two signals:

- the expected report file (the authoritative completion signal), classified
  with ``report-action`` verdict semantics; and
- the optional wrapper status file at ``<report-path>.status`` (an early-exit
  optimization for crash detection — see ``wrappers/README.md``).

Terminal status flags emitted on stdout (one NDJSON record):

- ``done``: a report is present and parses successfully (report-action verdict
  ``accepted``/``blocked``/``failed``). The PM reads the report verdict to
  decide lifecycle action.
- ``failed-to-parse``: a report is present but invalid.
- ``failed``: the wrapper status file reports the assignee process exited
  non-zero and no valid report appeared.
- ``timeout``: the configured handoff timeout (the maximum absolute limit) was
  reached before any terminal signal.
- ``still-running``: the ``--max-block`` polling budget elapsed before the
  configured timeout; the assignee may still be working.

The effective block budget is ``min(--max-block, configured timeout)``; the
configured timeout from ``[handoffs.<role>] timeout`` (protocol default ``60m``)
is honored as the maximum absolute ceiling. Read-only: never writes to the
project tree, never moves tasks, never launches processes. Standard library
only (see STANDARDS.md § Wait Command Standards).
"""
import argparse
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

from cli.commands import handoff_packet, report_action
from cli.commands.resolve_config import _CliError, _load_toml, _resolve_handoffs
from cli.emit import emit_record
from cli.main import (
    EXIT_ENV,
    EXIT_FAIL,
    EXIT_OK,
    EXIT_USAGE,
    stderr_error,
    stderr_usage,
)

# Duration grammar: a positive integer followed by a unit suffix.
_DURATION_RE = re.compile(r"^(\d+)([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

# Protocol default handoff timeout (CONVENTIONS.md § Handoffs).
DEFAULT_TIMEOUT = "60m"
DEFAULT_TIMEOUT_SECONDS = 3600
DEFAULT_POLL_SECONDS = 5.0

# Exit-code contract per status flag. ``done`` and ``still-running``
# are benign observations; the rest are logical failures the PM must handle.
_EXIT_FOR_STATUS = {
    "done": EXIT_OK,
    "still-running": EXIT_OK,
    "failed-to-parse": EXIT_FAIL,
    "failed": EXIT_FAIL,
    "timeout": EXIT_FAIL,
}


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    """Add arguments for wait-handoff."""
    subparser.add_argument(
        "task_path",
        help="Absolute path to the task file whose handoff to monitor",
    )
    subparser.add_argument(
        "--role",
        required=True,
        help="Role identifier being dispatched (resolves the configured timeout)",
    )
    subparser.add_argument(
        "--max-block",
        dest="max_block",
        required=True,
        help="Maximum time to block before giving up, e.g. 30s, 1m, 5h",
    )
    subparser.add_argument(
        "--poll-interval",
        dest="poll_interval",
        type=float,
        default=DEFAULT_POLL_SECONDS,
        help=f"Seconds between filesystem polls (default: {DEFAULT_POLL_SECONDS:g})",
    )


def _parse_duration(raw: str) -> Optional[int]:
    """Return the duration in whole seconds, or None if malformed.

    Accepts ``<positive-int><unit>`` where unit is one of s/m/h/d.
    """
    match = _DURATION_RE.match(raw.strip())
    if not match:
        return None
    value = int(match.group(1))
    if value <= 0:
        return None
    return value * _UNIT_SECONDS[match.group(2)]


def _resolve_timeout_seconds(project_root: Path, role: str) -> int:
    """Resolve the configured handoff timeout for ``role`` in whole seconds.

    Reads ``[handoffs.<role>] timeout`` along the project/global config chain,
    falling back to the protocol default (``60m``) when no block, no timeout
    field, or unreadable/malformed config is present. wait-handoff is an
    observer, so config gaps degrade to the default rather than failing.
    """
    project_toml = project_root / "cartopian.toml"
    global_toml = Path.home() / ".cartopian" / "cartopian.toml"
    try:
        project_cfg = _load_toml(project_toml, "project config") or {}
        global_cfg = _load_toml(global_toml, "global config") or {}
    except _CliError:
        return DEFAULT_TIMEOUT_SECONDS

    handoffs = _resolve_handoffs(global_cfg, project_cfg)
    role_block = handoffs.get(role, {}) or {}
    timeout_raw = role_block.get("timeout") or DEFAULT_TIMEOUT
    seconds = _parse_duration(str(timeout_raw))
    return seconds if seconds is not None else DEFAULT_TIMEOUT_SECONDS


def _report_verdict(report_path: Path) -> Optional[str]:
    """Return the report-action verdict for the report, or None if absent.

    A present-but-unreadable or variant-unresolvable report classifies as
    ``failed-to-parse``.
    """
    if not report_path.is_file():
        return None
    try:
        content = report_path.read_text(encoding="utf-8")
    except OSError:
        return "failed-to-parse"
    try:
        verdict = report_action._parse_report_state(
            report_path,
            content,
            None,
        )[0]
    except _CliError:
        return "failed-to-parse"
    return verdict


def _status_exit_code(status_path: Path) -> Optional[int]:
    """Return the non-zero exit code from a wrapper status file, else None.

    Returns the integer ``exit_code`` only when the wrapper reports
    ``state=exited`` with a non-zero code — the crash signal that lets
    wait-handoff exit early instead of blocking to the deadline. Absent,
    unreadable, still-running, clean-exit, or malformed status files yield None
    (the report remains the authoritative signal).
    """
    if not status_path.is_file():
        return None
    try:
        text = status_path.read_text(encoding="utf-8")
    except OSError:
        return None
    fields: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        fields[key.strip()] = value.strip()
    if fields.get("state") != "exited":
        return None
    raw = fields.get("exit_code")
    if raw is None:
        return None
    try:
        code = int(raw)
    except ValueError:
        return None
    return code if code != 0 else None


def handler(args: argparse.Namespace) -> int:
    """Block until a terminal handoff observation, then emit one NDJSON record."""
    raw_path = args.task_path
    if not Path(raw_path).is_absolute():
        stderr_usage(f"task_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE

    max_block_seconds = _parse_duration(args.max_block)
    if max_block_seconds is None:
        stderr_usage(
            f"invalid --max-block duration: {args.max_block!r}; "
            "expected a positive integer with unit s|m|h|d (e.g. 30s, 1m, 5h)"
        )
        return EXIT_USAGE

    poll_interval = args.poll_interval
    if poll_interval <= 0:
        stderr_usage(f"invalid --poll-interval: {poll_interval!r}; must be > 0")
        return EXIT_USAGE

    task_path = Path(raw_path)
    if not task_path.is_file():
        stderr_error(f"task file not found: {raw_path}")
        return EXIT_FAIL
    task_path = task_path.resolve()

    project_root = handoff_packet._find_project_root(task_path)
    if project_root is None:
        stderr_error(f"project config not found for task: {raw_path}")
        return EXIT_ENV

    task_id = handoff_packet._extract_task_id(task_path) or task_path.stem
    report_path = handoff_packet._expected_report_path(project_root, task_id)
    status_path = Path(str(report_path) + ".status")

    # The configured timeout is the absolute ceiling; --max-block is this
    # call's polling budget. Whichever is smaller bounds the loop.
    timeout_seconds = _resolve_timeout_seconds(project_root, args.role)
    effective_seconds = min(max_block_seconds, timeout_seconds)
    # When the configured ceiling is the limiting factor, hitting the deadline
    # means the agent blew its absolute limit (`timeout`); otherwise our own
    # polling budget elapsed while the agent is still permitted to run
    # (`still-running`).
    deadline_status = "timeout" if timeout_seconds <= max_block_seconds else "still-running"

    deadline = time.monotonic() + effective_seconds

    while True:
        verdict = _report_verdict(report_path)
        if verdict is not None and verdict != "failed-to-parse":
            return _emit(args.role, task_id, task_path, report_path,
                         "done", report_verdict=verdict,
                         max_block_seconds=max_block_seconds,
                         timeout_seconds=timeout_seconds,
                         effective_seconds=effective_seconds)

        crash_code = _status_exit_code(status_path)
        if crash_code is not None:
            return _emit(args.role, task_id, task_path, report_path,
                         "failed", report_verdict=verdict, exit_code=crash_code,
                         max_block_seconds=max_block_seconds,
                         timeout_seconds=timeout_seconds,
                         effective_seconds=effective_seconds)

        if verdict == "failed-to-parse":
            return _emit(args.role, task_id, task_path, report_path,
                         "failed-to-parse", report_verdict=verdict,
                         max_block_seconds=max_block_seconds,
                         timeout_seconds=timeout_seconds,
                         effective_seconds=effective_seconds)

        now = time.monotonic()
        if now >= deadline:
            return _emit(args.role, task_id, task_path, report_path,
                         deadline_status, report_verdict=verdict,
                         max_block_seconds=max_block_seconds,
                         timeout_seconds=timeout_seconds,
                         effective_seconds=effective_seconds)

        time.sleep(min(poll_interval, deadline - now))


def _emit(
    role: str,
    task_id: str,
    task_path: Path,
    report_path: Path,
    status: str,
    *,
    report_verdict: Optional[str] = None,
    exit_code: Optional[int] = None,
    max_block_seconds: int,
    timeout_seconds: int,
    effective_seconds: int,
) -> int:
    """Emit the single terminal NDJSON record and return the mapped exit code."""
    record: Dict[str, Any] = {
        "task_id": task_id,
        "task_path": str(task_path),
        "role": role,
        "report_path": str(report_path),
        "status": status,
        "report_verdict": report_verdict,
        "exit_code": exit_code,
        "max_block_seconds": max_block_seconds,
        "timeout_seconds": timeout_seconds,
        "effective_block_seconds": effective_seconds,
    }
    emit_record(record)
    return _EXIT_FOR_STATUS[status]
