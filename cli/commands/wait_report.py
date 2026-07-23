"""`cartopian wait-report <report-path> [--role <role>] [--max-block <duration>]`.

Filesystem-polls until a handoff report file exists and reaches an
``accepted`` outcome under the ``report-action`` aggregator's verdict
semantics (``accepted`` / ``blocked`` / ``failed`` / ``failed-to-parse``), or
until the deadline elapses.

The wait is terminal by default: called without ``--max-block``, it blocks
until the report lands (or guard-fails), bounded by the resolved handoff
timeout as the absolute ceiling — ``[handoffs.<role>] timeout`` when
``--role`` is given and resolvable from the report's project, the protocol
default (``60m``) otherwise. ``--max-block`` is an explicit opt-in bounding
one observation slice for hosts that cannot sustain a blocking call for the
full handoff timeout.

Outcomes:

- Report present and ``accepted`` → exit 0, emit one NDJSON ``accepted`` record.
- Report present but not ``accepted`` → exit 1 with a ``[guard]`` stderr line.
- The resolved timeout ceiling elapses first → exit 1, emit one NDJSON
  ``timeout`` record (terminal; the handoff blew its absolute limit).
- An explicit ``--max-block`` slice elapses before the ceiling → exit 0, emit
  one NDJSON ``still_running`` record (nonterminal; reachable only when
  ``--max-block`` was supplied).

Read-only: never writes to the project tree. Standard library only. Validity
is judged via the ``report-action`` aggregator, not the deprecated public
``parse-report`` surface.
"""
import argparse
import re
import time
from pathlib import Path
from typing import Optional

from cli.commands import handoff_packet, report_action
from cli.commands.resolve_config import _CliError
from cli.commands.wait_handoff import (
    DEFAULT_TIMEOUT_SECONDS,
    _resolve_timeout_seconds,
)
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE, stderr_guard, stderr_usage

# Duration grammar: a positive integer followed by a unit suffix.
_DURATION_RE = re.compile(r"^(\d+)([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

DEFAULT_POLL_SECONDS = 5.0


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    """Add arguments for wait-report."""
    subparser.add_argument(
        "report_path",
        help="Absolute path to the report file to wait for",
    )
    subparser.add_argument(
        "--role",
        default=None,
        help=(
            "Optional role whose configured [handoffs.<role>] timeout bounds "
            "the wait (protocol default 60m when omitted or unresolvable)"
        ),
    )
    subparser.add_argument(
        "--max-block",
        dest="max_block",
        default=None,
        help=(
            "Optional observation-slice budget, e.g. 30s, 1m, 5h. Default: "
            "block until a terminal outcome, bounded by the resolved handoff "
            "timeout"
        ),
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


def _report_verdict(report_path: Path) -> Optional[str]:
    """Return the report-action verdict for the report, or None if not present.

    A present-but-unreadable or variant-unresolvable report is reported as
    ``failed-to-parse`` — present, but not ``accepted``.
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


def _resolve_ceiling_seconds(report_path: Path, role: Optional[str]) -> int:
    """Resolve the absolute timeout ceiling for this wait in whole seconds.

    With a ``--role`` and a discoverable project root, the ceiling is the
    resolved ``[handoffs.<role>] timeout``; otherwise the protocol default.
    wait-report is an observer, so resolution gaps degrade to the default
    rather than failing.
    """
    if role:
        project_root = handoff_packet._find_project_root(report_path)
        if project_root is not None:
            return _resolve_timeout_seconds(project_root, role)
    return DEFAULT_TIMEOUT_SECONDS


def handler(args: argparse.Namespace) -> int:
    """Block until the report is accepted, fails the guard, or the deadline hits."""
    raw_path = args.report_path
    if not Path(raw_path).is_absolute():
        stderr_usage(f"report_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE

    max_block_seconds: Optional[int] = None
    if args.max_block is not None:
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

    report_path = Path(raw_path)

    # The resolved handoff timeout is the absolute ceiling. Without
    # --max-block the wait is terminal: it blocks to that ceiling and the
    # deadline classifies as `timeout`. An explicit --max-block bounds one
    # nonterminal observation slice unless the ceiling is the limiting factor.
    timeout_seconds = _resolve_ceiling_seconds(report_path, args.role)
    if max_block_seconds is None:
        effective_seconds = timeout_seconds
        deadline_still_running = False
    else:
        effective_seconds = min(max_block_seconds, timeout_seconds)
        deadline_still_running = max_block_seconds < timeout_seconds

    deadline = time.monotonic() + effective_seconds

    while True:
        verdict = _report_verdict(report_path)
        if verdict is not None:
            resolved = str(report_path.resolve())
            if verdict == "accepted":
                emit_record(
                    {
                        "report_path": resolved,
                        "status": "accepted",
                        "verdict": verdict,
                        "accepted": True,
                        "still_running": False,
                    }
                )
                return EXIT_OK
            stderr_guard(
                f"report present but not accepted (verdict={verdict}): {resolved}"
            )
            return EXIT_FAIL

        now = time.monotonic()
        if now >= deadline:
            record = {
                "report_path": str(report_path.resolve()),
                "status": "still_running" if deadline_still_running else "timeout",
                "verdict": None,
                "accepted": False,
                "still_running": deadline_still_running,
                "max_block_seconds": max_block_seconds,
                "timeout_seconds": timeout_seconds,
                "effective_block_seconds": effective_seconds,
            }
            emit_record(record)
            return EXIT_OK if deadline_still_running else EXIT_FAIL

        time.sleep(min(poll_interval, deadline - now))
