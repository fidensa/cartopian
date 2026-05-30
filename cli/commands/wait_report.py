"""`cartopian wait-report <report-path> --max-block <duration>` (FR-014, P01-BUILD-002).

Filesystem-polls until a handoff report file exists and reaches an
``accepted`` outcome under the ``report-action`` aggregator's verdict
semantics (``accepted`` / ``blocked`` / ``failed`` / ``failed-to-parse``), or
until the ``--max-block`` budget elapses.

Outcomes:

- Report present and ``accepted`` → exit 0, emit one NDJSON ``accepted`` record.
- Report present but not ``accepted`` → exit 1 with a ``[guard]`` stderr line.
- ``--max-block`` elapses before the report is present/valid → exit 0, emit one
  NDJSON ``still_running`` record.

Read-only: never writes to the project tree. Standard library only. Validity is
judged via the ``report-action`` aggregator, not the deprecated public
``parse-report`` surface.
"""
import argparse
import re
import time
from pathlib import Path
from typing import Optional

from cli.commands import report_action
from cli.commands.resolve_config import _CliError
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
        verdict, _variant, _status, _review_verdict = report_action._parse_report_state(
            report_path,
            content,
            None,
        )
    except _CliError:
        return "failed-to-parse"
    return verdict


def handler(args: argparse.Namespace) -> int:
    """Block until the report is accepted, fails the guard, or the budget elapses."""
    raw_path = args.report_path
    if not Path(raw_path).is_absolute():
        stderr_usage(f"report_path must be an absolute path; got: {raw_path}")
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

    report_path = Path(raw_path)
    deadline = time.monotonic() + max_block_seconds

    while True:
        verdict = _report_verdict(report_path)
        if verdict is not None:
            resolved = str(report_path.resolve())
            if verdict == "accepted":
                emit_record(
                    {
                        "report_path": resolved,
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
            emit_record(
                {
                    "report_path": str(report_path.resolve()),
                    "verdict": None,
                    "accepted": False,
                    "still_running": True,
                    "max_block_seconds": max_block_seconds,
                }
            )
            return EXIT_OK

        time.sleep(min(poll_interval, deadline - now))
