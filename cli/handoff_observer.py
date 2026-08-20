"""Canonical read-only observation of one report-authoritative handoff.

Both task-scoped and report-path-only waits consume this module.  A report
path appearing is not itself terminal: invalid/incomplete bytes remain
nonterminal while the wrapper can still publish a complete report.  Once the
wrapper has exited, malformed bytes and absence become deterministic terminal
failures.

Terminal observations bind final bytes.  While a matching automated launch is
still ``state=running``, even a *complete* report is nonterminal: the report
writer is alive and may still rewrite it (the supervisor's grace/reap window
is exactly such a period).  The supervisor publishes ``state=exited`` — and,
for retained-log launches, the atomic launch-log snapshot — only after the
child process is gone, so the content identity reported with a terminal
observation cannot change afterward.  Manual/report-only handoffs (no status
file) keep report-authoritative semantics unchanged.
"""
from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from cli import report_identity
from cli.commands import report_action
from cli.commands.resolve_config import _CliError


VALID_VARIANTS = ("task", "review", "planning-review")


@dataclass(frozen=True)
class ReportObservation:
    present: bool
    publication_state: str
    verdict: Optional[str]
    variant: Optional[str]
    content_identity: Optional[str]
    permanently_invalid: bool = False


@dataclass(frozen=True)
class WrapperObservation:
    state: Optional[str]
    exit_code: Optional[int]
    reason: Optional[str]
    launch_id: Optional[str]
    expected_variant: Optional[str]
    variant_matches: bool
    metadata: Dict[str, str]


@dataclass(frozen=True)
class HandoffObservation:
    terminal: bool
    classification: Optional[str]
    report: ReportObservation
    wrapper: WrapperObservation


def _read_status_fields(status_path: Path) -> Optional[Dict[str, str]]:
    if not status_path.is_file() or status_path.is_symlink():
        return None
    try:
        text = status_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    fields: Dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            fields[key] = value.strip()
    return fields


def observe_wrapper(
    status_path: Path,
    expected_variant: Optional[str],
) -> WrapperObservation:
    fields = _read_status_fields(status_path) or {}
    state = fields.get("state")
    if state not in {"running", "exited"}:
        state = None
    raw_code = fields.get("exit_code")
    try:
        exit_code = int(raw_code) if raw_code is not None else None
    except ValueError:
        exit_code = None
    status_variant = fields.get("expected_variant")
    if status_variant not in VALID_VARIANTS:
        status_variant = None
    variant_matches = not (
        expected_variant is not None
        and status_variant is not None
        and expected_variant != status_variant
    )
    return WrapperObservation(
        state=state,
        exit_code=exit_code,
        reason=fields.get("reason"),
        launch_id=fields.get("launch_id") or None,
        expected_variant=status_variant,
        variant_matches=variant_matches,
        metadata=fields,
    )


def observe_report(
    report_path: Path,
    expected_variant: Optional[str],
) -> ReportObservation:
    if report_path.is_symlink():
        return ReportObservation(
            present=True,
            publication_state="invalid",
            verdict=None,
            variant=expected_variant,
            content_identity=None,
            permanently_invalid=True,
        )
    if not report_path.is_file():
        return ReportObservation(False, "absent", None, None, None)
    try:
        content = report_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ReportObservation(True, "partial", None, expected_variant, None)
    identity = report_identity.content_identity(content)
    try:
        verdict, variant, _status, _review_verdict = (
            report_action._parse_report_state(
                report_path,
                content,
                expected_variant,
            )
        )
    except _CliError:
        return ReportObservation(
            True, "partial", "failed-to-parse", expected_variant, identity
        )
    if verdict == "failed-to-parse":
        return ReportObservation(True, "partial", verdict, variant, identity)
    return ReportObservation(
        True,
        "complete",
        verdict,
        variant,
        identity,
    )


def _retained_snapshot_published(report_path: Path) -> bool:
    """Whether this launch's bounded log snapshot is already observable.

    Dispatch removes the prior slot's launch log before it publishes the
    matching ``state=running`` marker.  The outer supervisor atomically
    publishes the current snapshot only after the child process has exited,
    immediately before it replaces the status with ``state=exited``.
    Observing a safe regular file at the deterministic companion path is
    therefore proof that the report writer is gone — sufficient terminal
    evidence when that final status replacement is lost or raced.  Only
    metadata is inspected; wait surfaces never open the launch-log body.
    """
    launch_log = Path(str(report_path) + ".launch.log")
    try:
        info = launch_log.lstat()
    except OSError:
        return False
    return (
        not launch_log.is_symlink()
        and stat.S_ISREG(info.st_mode)
        and info.st_nlink == 1
    )


def observe_once(
    report_path: Path,
    *,
    expected_variant: Optional[str] = None,
) -> HandoffObservation:
    status_path = Path(str(report_path) + ".status")
    wrapper = observe_wrapper(status_path, expected_variant)
    report = observe_report(report_path, expected_variant)

    if report.publication_state == "complete":
        # A live automated launch (a matching ``state=running`` status) means
        # the report writer may still be running — including the supervisor's
        # post-report grace window — so its bytes are not yet final. Defer
        # until the wrapper publishes ``state=exited``. The one fail-open
        # exception is supervisor loss after final publication: for
        # retained-log launches the atomic launch-log snapshot is published
        # only after the child is gone, so an observable snapshot is
        # equivalent terminal evidence when the exit-status replacement was
        # lost or raced.
        publication_pending = (
            wrapper.state == "running"
            and wrapper.variant_matches
            and not (
                wrapper.metadata.get("guarantee_scope") == "retained-launch-log"
                and _retained_snapshot_published(report_path)
            )
        )
        if publication_pending:
            return HandoffObservation(False, None, report, wrapper)
        return HandoffObservation(True, report.verdict, report, wrapper)
    if report.permanently_invalid:
        return HandoffObservation(True, "failed-to-parse", report, wrapper)

    # A stale status record from another variant cannot terminate this launch.
    if wrapper.state == "exited" and wrapper.variant_matches:
        if report.present:
            return HandoffObservation(True, "failed-to-parse", report, wrapper)
        classification = (
            "failed"
            if wrapper.exit_code not in (None, 0)
            else "exited-without-report"
        )
        return HandoffObservation(True, classification, report, wrapper)

    return HandoffObservation(False, None, report, wrapper)


def record_fields(observation: HandoffObservation) -> Dict[str, Any]:
    """Common machine fields emitted unchanged by both wait surfaces."""
    fields = {
        "terminal": observation.terminal,
        "classification": observation.classification,
        "publication_state": observation.report.publication_state,
        "report_verdict": observation.report.verdict,
        "report_variant": observation.report.variant,
        "report_content_identity": observation.report.content_identity,
        "wrapper_state": observation.wrapper.state,
        "exit_code": observation.wrapper.exit_code,
        "wrapper_reason": observation.wrapper.reason,
        "launch_id": observation.wrapper.launch_id,
        "status_expected_variant": observation.wrapper.expected_variant,
        "status_variant_matches": observation.wrapper.variant_matches,
    }
    return fields
