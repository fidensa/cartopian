"""`cartopian wait-handoff <task-path> --role <role> [--max-block <duration>]`.

Read-only observer that monitors one handoff. It resolves the expected report
path from the task file through the authoritative report-identity model (the
same logic ``handoff-packet`` uses): ``reports/REPORT-NN-NNN.md`` for a task
run, ``reports/REPORT-NN-NNN-review.md`` for an in-review task's review
handoff. It filesystem-polls two signals:

- the expected report file (the authoritative completion signal), classified
  with ``report-action`` verdict semantics; and
- the optional wrapper status file at ``<report-path>.status`` (early-exit
  evidence plus the live automated retained-publication boundary — see
  ``wrappers/README.md``).

Terminal status flags emitted on stdout (one NDJSON record):

- ``done``: a report is present and parses successfully (report-action verdict
  ``accepted``/``blocked``/``failed``). The PM reads the report verdict to
  decide lifecycle action. A matching automated ``state=running`` status with
  ``retained_log_ready=false`` briefly defers this result; missing status is
  the manual/report-only path, and ``state=exited`` fails that diagnostic
  publication barrier open.
- ``failed-to-parse``: the wrapper has exited and the report publication is
  permanently invalid. A present but incomplete report remains nonterminal
  while the wrapper is still running.
- ``failed``: the wrapper exited non-zero and no report appeared.
- ``exited-without-report`` is carried in the common ``classification`` field
  when the wrapper exited cleanly without publishing a report (the legacy
  task-scoped ``status`` remains ``failed``).
- ``timeout``: the configured handoff timeout (the maximum absolute limit) was
  reached before any terminal signal.
- ``still-running``: the explicitly requested ``--max-block`` observation-slice
  budget elapsed before the configured timeout; the assignee may still be
  working. Reachable only when ``--max-block`` was supplied.

The wait is terminal by default: called without ``--max-block``, it blocks
until one of the terminal signals above, bounded by the resolved
``roles.<role>.timeout`` (protocol default ``60m``) as the absolute
ceiling — a single call, a single record, no nonterminal slices. That blocking
call is the entire completion mechanism; there is no wake or resume behind it.
``dispatch`` refuses to launch when the host's ``tools/call`` ceiling is
shorter than the role timeout (``cli/host_capability.py``), so by the time this
command runs the host has already been shown able to sustain it. The wait
rechecks this call's observation duration against its own progress-channel
availability so a token on an earlier call cannot stand in for progress on
this one. The resolved budget is echoed in the emitted record as
``host_wait_budget``.

``--max-block`` bounds a single nonterminal observation slice, for the one case
the gate cannot fix: a host ceiling that will not move. When supplied, the
effective block budget is ``min(--max-block, configured timeout)``.

Each poll emits an MCP progress notification through the host's progress
channel when one is installed — a no-op otherwise. This is liveness for hosts
that abort a call that goes silent, not user-facing output: it never touches
stdout, so the NDJSON contract is identical either way.

Read-only: never writes to the project tree, never moves tasks, never launches
processes, and never reads the retained launch-log body. Standard library only
(see STANDARDS.md § Wait Command Standards).
"""
import argparse
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from cli import handoff_observer, host_capability
from cli.commands import handoff_packet, report_action
from cli.commands.resolve_config import _CliError, resolve_project_configuration
from cli.emit import emit_progress, emit_record
from cli.main import (
    EXIT_ENV,
    EXIT_FAIL,
    EXIT_OK,
    EXIT_USAGE,
    stderr_error,
    stderr_guard,
    stderr_usage,
)

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
    # Reaches the model through the MCP `tools/list` surface. The docstring
    # above addresses a developer reading the source; this addresses a PM
    # choosing between one blocking call and a series of slices, because that
    # choice is where handoff observation actually goes wrong.
    subparser.description = (
        "Block until a dispatched assignee's report lands, then return one "
        "terminal NDJSON record. Call this WITHOUT `max_block` and let it "
        "block; it returns the moment the report lands. Its silence is "
        "expected and is not a lapse in commentary: no model turn is in "
        "progress while the call is pending, so an instruction to narrate "
        "ongoing work does not govern it. `max_block` is only for a host "
        "ceiling that cannot be raised, and `dispatch` already refused to "
        "launch if that were the case. A live automated retained-log marker "
        "may briefly coordinate publication; manual or exited-wrapper reports "
        "do not wait on it. See CONVENTIONS.md § Handoffs."
    )
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
        default=None,
        help=(
            "Optional observation-slice budget, e.g. 30s, 1m, 5h. Default: "
            "block until a terminal observation, bounded by the configured "
            "role launch timeout"
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


def _resolve_timeout_seconds(project_root: Path, role: str) -> int:
    """Resolve the configured handoff timeout for ``role`` in whole seconds.

    Reads ``roles.<role>.timeout`` from canonical resolution, falling
    back to the protocol default (``60m``) only when the valid role omits it.
    """
    resolved = resolve_project_configuration(project_root)
    role_record = resolved["roles"].get(role, {})
    timeout_raw = (
        role_record.get("launch", {}).get("timeout") or DEFAULT_TIMEOUT
    )
    seconds = _parse_duration(str(timeout_raw))
    return seconds if seconds is not None else DEFAULT_TIMEOUT_SECONDS


def _report_verdict(report_path: Path) -> Optional[str]:
    """Return the report-action verdict for the report, or None if absent.

    A present-but-unreadable or variant-unresolvable report classifies as
    ``failed-to-parse``.
    """
    observation = handoff_observer.observe_report(report_path, None)
    if not observation.present:
        return None
    return observation.verdict or "failed-to-parse"


def _read_status_fields(status_path: Path) -> Optional[Dict[str, str]]:
    """Parse a wrapper ``<report-path>.status`` file into a key=value dict.

    Returns None when the file is absent or unreadable (both valid: the report
    remains the authoritative signal). Malformed lines are skipped.
    """
    return handoff_observer._read_status_fields(status_path)


def _status_exit_code(status_path: Path) -> Optional[int]:
    """Return the non-zero exit code from a wrapper status file, else None.

    Returns the integer ``exit_code`` only when the wrapper reports
    ``state=exited`` with a non-zero code — the crash signal that lets
    wait-handoff exit early instead of blocking to the deadline. Absent,
    unreadable, still-running, clean-exit, or malformed status files yield None.
    A clean exit is handled separately by ``_status_reports_exit`` (a clean exit
    with no report is still terminal); the report otherwise remains the
    authoritative signal.
    """
    fields = _read_status_fields(status_path)
    if fields is None or fields.get("state") != "exited":
        return None
    raw = fields.get("exit_code")
    if raw is None:
        return None
    try:
        code = int(raw)
    except ValueError:
        return None
    return code if code != 0 else None


def _status_reports_exit(status_path: Path) -> Tuple[bool, Optional[int]]:
    """Report whether the wrapper says the assignee exited, and its code.

    Returns ``(exited, code)``: ``exited`` is True whenever the status file
    records ``state=exited`` (any exit code, including a clean ``0``); ``code``
    is the parsed exit code, or None when absent/unparseable. Unlike a non-zero
    exit code (a crash), a *clean* exit that produced no report is still
    terminal — the assignee process is gone and no report will appear — so
    wait-handoff must not block to the deadline waiting for one.
    """
    fields = _read_status_fields(status_path)
    if fields is None or fields.get("state") != "exited":
        return False, None
    raw = fields.get("exit_code")
    if raw is None:
        return True, None
    try:
        return True, int(raw)
    except ValueError:
        return True, None


def handler(args: argparse.Namespace) -> int:
    """Block until a terminal handoff observation, then emit one NDJSON record."""
    raw_path = args.task_path
    if not Path(raw_path).is_absolute():
        stderr_usage(f"task_path must be an absolute path; got: {raw_path}")
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

    task_path = Path(raw_path)
    if not task_path.is_file():
        stderr_error(f"task file not found: {raw_path}")
        return EXIT_FAIL
    task_path = task_path.resolve()

    project_root = handoff_packet._find_project_root(task_path)
    if project_root is None:
        stderr_error(f"project config not found for task: {raw_path}")
        return EXIT_ENV

    from cli import numbering_contract

    refusal = numbering_contract.guard_existing_task_trace(
        project_root, task_path
    )
    if refusal is not None:
        stderr_guard(f"numbering trace invalid ({refusal[0]}): {refusal[1]}")
        return EXIT_FAIL

    task_id = handoff_packet._extract_task_id(task_path) or task_path.stem
    # An in-review task's handoff is task review: observe the independent
    # review-report slot. The preserved completion report cannot satisfy a
    # review wait (and a review report cannot satisfy a completion wait).
    report_path = handoff_packet._expected_handoff_report_path(
        project_root, task_id, task_path
    )
    status_path = Path(str(report_path) + ".status")

    # The configured timeout is the absolute ceiling. Without --max-block the
    # wait is terminal: it blocks to that ceiling and can only end in a
    # terminal status. An explicit --max-block bounds one observation slice.
    try:
        timeout_seconds = _resolve_timeout_seconds(project_root, args.role)
    except _CliError as exc:
        stderr_error(exc.message)
        return exc.exit_code
    if max_block_seconds is None:
        effective_seconds = timeout_seconds
        deadline_status = "timeout"
    else:
        effective_seconds = min(max_block_seconds, timeout_seconds)
        # When the configured ceiling is the limiting factor, hitting the
        # deadline means the agent blew its absolute limit (`timeout`);
        # otherwise the requested slice elapsed while the agent is still
        # permitted to run (`still-running`).
        deadline_status = (
            "timeout" if timeout_seconds <= max_block_seconds else "still-running"
        )
    host_ok, _host_budget, host_refusal = host_capability.check_wait_budget(
        args.role,
        effective_seconds,
    )
    if not host_ok:
        stderr_guard(host_refusal)
        return EXIT_FAIL

    deadline = time.monotonic() + effective_seconds
    expected_variant = "review" if task_path.parent.name == "in-review" else "task"

    while True:
        observation = handoff_observer.observe_once(
            report_path,
            expected_variant=expected_variant,
        )
        if observation.terminal:
            if observation.report.publication_state == "complete":
                legacy_status = "done"
            elif observation.classification == "failed-to-parse":
                legacy_status = "failed-to-parse"
            else:
                legacy_status = "failed"
            return _emit(
                args.role,
                task_id,
                task_path,
                report_path,
                legacy_status,
                observation=observation,
                expected_variant=expected_variant,
                max_block_seconds=max_block_seconds,
                timeout_seconds=timeout_seconds,
                effective_seconds=effective_seconds,
            )

        now = time.monotonic()
        if now >= deadline:
            return _emit(args.role, task_id, task_path, report_path,
                         deadline_status, observation=observation,
                         expected_variant=expected_variant,
                         max_block_seconds=max_block_seconds,
                         timeout_seconds=timeout_seconds,
                         effective_seconds=effective_seconds)

        # Prove liveness to a host that aborts silent calls. This is not a
        # user-facing heartbeat and not a PM-chosen rhythm: it rides the
        # existing poll, goes to the host's progress channel rather than
        # stdout, and is a no-op when no host installed one.
        elapsed = effective_seconds - max(0.0, deadline - now)
        emit_progress(
            elapsed,
            float(effective_seconds),
            f"waiting on {report_path.name}",
        )

        time.sleep(min(poll_interval, deadline - now))


def _emit(
    role: str,
    task_id: str,
    task_path: Path,
    report_path: Path,
    status: str,
    *,
    observation: handoff_observer.HandoffObservation,
    expected_variant: str,
    max_block_seconds: Optional[int],
    timeout_seconds: int,
    effective_seconds: int,
) -> int:
    """Emit the single terminal NDJSON record and return the mapped exit code."""
    budget = host_capability.resolve_host_budget()
    common = handoff_observer.record_fields(observation)
    if status in {"timeout", "still-running"}:
        common["terminal"] = status == "timeout"
        common["classification"] = status
    record: Dict[str, Any] = {
        "task_id": task_id,
        "task_path": str(task_path),
        "role": role,
        "report_path": str(report_path),
        "status": status,
        "expected_report_variant": expected_variant,
        **common,
        "max_block_seconds": max_block_seconds,
        "timeout_seconds": timeout_seconds,
        "effective_block_seconds": effective_seconds,
        # What the host would have allowed this call, for the record. `null`
        # outside an MCP host, where no tools/call ceiling applies.
        "host_wait_budget": budget.record() if budget is not None else None,
    }
    emit_record(record)
    return _EXIT_FOR_STATUS[status]
