"""`cartopian wait-report <report-path> [--role <role>] [--max-block <duration>]`.

Uses the same canonical observer as ``wait-handoff`` until the report reaches
a complete report-action outcome, the wrapper exits leaving a permanently
malformed or absent publication, or the deadline elapses.

The wait is terminal by default: called without ``--max-block``, it blocks
until the report lands (or guard-fails), bounded by the resolved handoff
timeout as the absolute ceiling — ``roles.<role>.timeout`` when
``--role`` is given and resolves from the report's project, the protocol
default (``60m``) when no project is discoverable or no role is given.
Invalid discovered configuration fails closed. ``--max-block`` is an explicit opt-in bounding
one observation slice for hosts that cannot sustain a blocking call for the
full handoff timeout.

Outcomes:

- Complete report (accepted, blocked, failed, changes-requested, or rejected)
  → exit 0 and emit its terminal classification. Observation succeeded; the
  caller routes the report verdict. A matching automated ``state=running``
  status with ``retained_log_ready=false`` briefly defers this result; missing
  status is the manual/report-only path, and ``state=exited`` fails that
  diagnostic-publication barrier open.
- Incomplete report while the wrapper can still publish → remain nonterminal.
- Wrapper exit with malformed bytes or no report → deterministic exit 1
  classification matching ``wait-handoff``.
- The resolved timeout ceiling elapses first → exit 1, emit one NDJSON
  ``timeout`` record (terminal; the handoff blew its absolute limit).
- An explicit ``--max-block`` slice elapses before the ceiling → exit 0, emit
  one NDJSON ``still_running`` record (nonterminal; reachable only when
  ``--max-block`` was supplied).

Read-only: never writes to the project tree or reads the retained launch-log
body. Standard library only. Validity is judged via the ``report-action``
aggregator, not the deprecated public ``parse-report`` surface.
"""
import argparse
import sys
import time
from pathlib import Path
from typing import Optional

from cli import handoff_observer, host_capability, report_identity
from cli.commands import handoff_packet
from cli.commands.resolve_config import _CliError
from cli.commands.wait_handoff import (
    DEFAULT_TIMEOUT_SECONDS,
    _resolve_timeout_seconds,
)
from cli.emit import emit_progress, emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE, stderr_guard, stderr_usage

DEFAULT_POLL_SECONDS = 5.0


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    """Add arguments for wait-report."""
    # The model-facing counterpart to the docstring above; see `wait_handoff`
    # for why the two audiences get different prose.
    subparser.description = (
        "Block until a report lands at a known path, then return one terminal "
        "NDJSON record. Use for a report with no task file, such as a planning "
        "checkpoint review; for a task-scoped handoff use `wait_handoff`. Call "
        "this WITHOUT `max_block` and let it block; it returns when report "
        "completion is observable. A live automated retained-log marker may "
        "briefly coordinate publication; manual or exited-wrapper reports do "
        "not wait on it. Its silence is expected and is not a lapse in "
        "commentary: no model turn is in progress while the call is pending, "
        "so an instruction to narrate ongoing work does not govern it. "
        "`max_block` is only for observing a manually launched handoff when "
        "the host ceiling cannot be raised; automatic `dispatch` refuses "
        "before launching that mismatch. See CONVENTIONS.md § Handoffs."
    )
    subparser.add_argument(
        "report_path",
        help="Absolute path to the report file to wait for",
    )
    subparser.add_argument(
        "--role",
        default=None,
        help=(
            "Optional role whose configured launch timeout bounds "
            "the wait (protocol default 60m when omitted or outside a project)"
        ),
    )
    subparser.add_argument(
        "--variant",
        choices=list(handoff_observer.VALID_VARIANTS),
        default=None,
        help=(
            "Expected report variant. When omitted, REPORT-NN-NNN-review.md "
            "filenames imply review, planning report filenames imply "
            "planning-review, and other unmarked slots default to task"
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

    Accepts ``<positive-int><unit>`` where unit is one of s/m/h/d — the same
    grammar ``roles.<role>.timeout`` uses, so both read from one parser.
    """
    return host_capability.parse_duration(raw)


def _report_verdict(report_path: Path) -> Optional[str]:
    """Return the report-action verdict for the report, or None if not present.

    A present-but-unreadable or variant-unresolvable report is reported as
    ``failed-to-parse`` — present, but not ``accepted``.
    """
    observation = handoff_observer.observe_report(report_path, None)
    if not observation.present:
        return None
    return observation.verdict or "failed-to-parse"


def _host_budget_record():
    """The host's tools/call ceiling for this record, or None outside a host."""
    budget = host_capability.resolve_host_budget()
    return budget.record() if budget is not None else None


def _resolve_ceiling_seconds(report_path: Path, role: Optional[str]) -> int:
    """Resolve the absolute timeout ceiling for this wait in whole seconds.

    With a ``--role`` and a discoverable project root, the ceiling is the
    resolved role launch timeout; otherwise the protocol default. A
    discoverable project's invalid configuration propagates to the command
    boundary so the observer fails closed.
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
    try:
        timeout_seconds = _resolve_ceiling_seconds(report_path, args.role)
    except _CliError as err:
        sys.stderr.write(f"[{err.prefix}] {err.message}\n")
        return err.exit_code
    if max_block_seconds is None:
        effective_seconds = timeout_seconds
        deadline_still_running = False
    else:
        effective_seconds = min(max_block_seconds, timeout_seconds)
        deadline_still_running = max_block_seconds < timeout_seconds
    host_ok, _host_budget, host_refusal = host_capability.check_wait_budget(
        args.role or "unspecified",
        effective_seconds,
    )
    if not host_ok:
        stderr_guard(host_refusal)
        return EXIT_FAIL

    deadline = time.monotonic() + effective_seconds
    explicit_variant = getattr(args, "variant", None)
    status_variant = handoff_observer.observe_wrapper(
        Path(str(report_path) + ".status"),
        None,
    ).expected_variant
    # Filename-implied default from the authoritative identity model:
    # REPORT-NN-NNN-review.md → review, REPORT-PLAN-* → planning-review, and
    # any other unmarked slot defaults to task — never to its own bytes.
    expected_variant = (
        explicit_variant
        or status_variant
        or report_identity.variant_for_report_name(report_path.name)
    )

    while True:
        observation = handoff_observer.observe_once(
            report_path,
            expected_variant=expected_variant,
        )
        if observation.terminal:
            classification = observation.classification
            common = handoff_observer.record_fields(observation)
            emit_record(
                {
                    "report_path": str(report_path.resolve()),
                    "status": classification,
                    "verdict": observation.report.verdict,
                    "accepted": classification == "accepted",
                    "still_running": False,
                    "expected_report_variant": expected_variant,
                    **common,
                    "max_block_seconds": max_block_seconds,
                    "timeout_seconds": timeout_seconds,
                    "effective_block_seconds": effective_seconds,
                    "host_wait_budget": _host_budget_record(),
                }
            )
            # A complete report is successful observation regardless of its
            # verdict; Stage 4 routes accepted/blocked/failed deterministically.
            if observation.report.publication_state == "complete":
                return EXIT_OK
            return EXIT_FAIL

        now = time.monotonic()
        if now >= deadline:
            record = {
                "report_path": str(report_path.resolve()),
                "status": "still_running" if deadline_still_running else "timeout",
                "verdict": None,
                "accepted": False,
                "still_running": deadline_still_running,
                "terminal": not deadline_still_running,
                "classification": (
                    "still-running" if deadline_still_running else "timeout"
                ),
                "publication_state": observation.report.publication_state,
                "report_verdict": observation.report.verdict,
                "report_variant": observation.report.variant,
                "report_content_identity": observation.report.content_identity,
                "expected_report_variant": expected_variant,
                "wrapper_state": observation.wrapper.state,
                "exit_code": observation.wrapper.exit_code,
                "wrapper_reason": observation.wrapper.reason,
                "launch_id": observation.wrapper.launch_id,
                "status_expected_variant": observation.wrapper.expected_variant,
                "status_variant_matches": observation.wrapper.variant_matches,
                "max_block_seconds": max_block_seconds,
                "timeout_seconds": timeout_seconds,
                "effective_block_seconds": effective_seconds,
                "host_wait_budget": _host_budget_record(),
            }
            emit_record(record)
            return EXIT_OK if deadline_still_running else EXIT_FAIL

        # Prove liveness to a host that aborts silent calls — the host's
        # progress channel, not stdout, and a no-op when none is installed.
        emit_progress(
            effective_seconds - max(0.0, deadline - now),
            float(effective_seconds),
            f"waiting on {report_path.name}",
        )

        time.sleep(min(poll_interval, deadline - now))
