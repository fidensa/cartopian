"""`cartopian lookup-evidence <project-root> --unit <unit>` — the lookup tool.

For one governed unit (``project:project``, ``planning:PLAN-NNN``, or
``task:TASK-NN-NNN``) this returns the applicable operator-evidence
identities, one line each, resolved through the same seam every review
prompt, dispatch preflight, and audit uses. When nothing resolves it returns
exactly one missing item with an operator-facing remedy.

What it never returns: captured text. Identities, kinds, units, sessions,
and ordinals are enough to reference a turn from a decision
(``Operator request evidence for: <unit>: <capture-id>``) or from an
evidence section; the words themselves reach a reviewer only through the
generated intent packet. ``--recent`` adds the last five captured operator
turns as identity rows with a fixed-length preview: the one way to find the
identity of a turn the resolver did not select, such as a correction the
operator stated after lock.

The missing item is a report to the operator, not a work item for the PM:
the remedy names the supported intake for this host and forbids writing
records under ``requests/`` or the intake directory by any means.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from cli import evidence_resolver, request_trace
from cli.commands.resolve_config import _CliError, resolve_project_configuration
from cli.config_schema import MACHINE_RECORD_SCHEMA_VERSION
from cli.emit import emit_record
from cli.main import EXIT_ENV, EXIT_FAIL, EXIT_OK, EXIT_USAGE, stderr_error, stderr_guard, stderr_usage
from cli.request_trace import GovernedUnit, RequestEvidence, RequestRefusal

UNIT_ARG_RE = re.compile(evidence_resolver.UNIT_RE)

EVICTED_RECOVERY = (
    "Captured operator turns were evicted from this session's preselection "
    "buffer before the project was selected ({count} events, {bytes} bytes). "
    "Ask the operator to restate anything material from them in their own "
    "session; evicted words are never reconstructed as evidence."
)


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.description = (
        "Applicable operator-evidence identities for one unit, or the one "
        "missing item and its operator-facing remedy; never captured text."
    )
    subparser.add_argument("project_root", help="Absolute path to the Cartopian project root")
    subparser.add_argument(
        "--unit",
        required=True,
        help="Governed unit: project:project | planning:PLAN-NNN | task:TASK-NN-NNN",
    )
    subparser.add_argument(
        "--recent",
        action="store_true",
        help="Also list the last five captured operator turns (identity, session, ordinal, 120-char preview, selected kind)",
    )


def _line(record: RequestEvidence) -> str:
    unit = f"{record.unit.kind}:{record.unit.identifier}"
    if record.source_kind == evidence_resolver.SOURCE_KIND:
        handle = record.record_id.split("/", 1)[0]
        where = f"session {handle} ordinal {record.source_sequence}"
        if record.context is not None:
            where += f" answering {record.context.capture_id}"
    else:
        where = f"{record.source_kind} {record.source_path}"
    return f"{record.record_id} | {record.kind} | {unit} | {where}"


def _evidence_entry(record: RequestEvidence) -> Dict[str, Any]:
    return {
        "id": record.record_id,
        "kind": record.kind,
        "unit": record.unit.as_record(),
        "source_kind": record.source_kind,
        "session": (
            record.record_id.split("/", 1)[0]
            if record.source_kind == evidence_resolver.SOURCE_KIND
            else None
        ),
        "ordinal": record.source_sequence,
        "context": record.context.capture_id if record.context is not None else None,
        "content_identity": record.identity,
    }


def _missing(refusal: RequestRefusal, candidates: Dict[str, Any]) -> Dict[str, Any]:
    remedy = refusal.recovery or request_trace.NOT_CAPTURED_RECOVERY
    evictions = candidates.get("evictions") or []
    if evictions and refusal.rule in ("unit-request-not-captured", "request-not-captured"):
        count = sum(int(e.get("count", 0)) for e in evictions)
        size = sum(int(e.get("bytes", 0)) for e in evictions)
        remedy = EVICTED_RECOVERY.format(count=count, bytes=size) + " " + remedy
    return {"rule": refusal.rule, "detail": refusal.detail, "remedy": remedy}


def handler(args: argparse.Namespace) -> int:
    root = Path(args.project_root)
    if not root.is_absolute():
        stderr_usage("project_root must be absolute")
        return EXIT_USAGE
    root = root.resolve()
    if not (root / "cartopian.toml").is_file():
        stderr_error(f"project config not found: {root / 'cartopian.toml'}")
        return EXIT_ENV
    if not UNIT_ARG_RE.fullmatch(args.unit or ""):
        stderr_usage("--unit must be project:project, planning:PLAN-NNN, or task:TASK-NN-NNN")
        return EXIT_USAGE
    unit = GovernedUnit(*args.unit.split(":", 1))
    try:
        resolved = resolve_project_configuration(root)
    except _CliError as exc:
        stderr_error(exc.message)
        return exc.exit_code
    base = {
        "record_schema_version": MACHINE_RECORD_SCHEMA_VERSION,
        "schema_identity": resolved["schema_identity"],
        "project_schema_version": resolved["project_schema_version"],
        "action": "lookup-evidence",
        "project_path": str(root),
        "unit": unit.as_record(),
    }
    loaded = evidence_resolver.load_candidates(root) if args.recent else None
    try:
        trace, summary, context_identity = request_trace.lookup_unit(root, unit)
    except RequestRefusal as refusal:
        candidates = evidence_resolver.candidate_summary(root)
        record = {
            **base,
            "state": "missing",
            "evidence": [],
            "lines": [],
            "unconfirmed": [],
            "candidates": candidates,
            "context_identity": None,
            "missing": _missing(refusal, candidates),
        }
        if loaded is not None:
            record["recent"] = evidence_resolver.recent_turns(loaded, {})
        emit_record(record)
        stderr_guard(f"{refusal.rule}: {refusal.detail}")
        return EXIT_FAIL
    legacy = not trace
    record = {
        **base,
        "state": request_trace.LEGACY_STATE if legacy else "resolved",
        "evidence": [_evidence_entry(item) for item in trace],
        "lines": [_line(item) for item in trace],
        "unconfirmed": list(summary.unconfirmed) if summary is not None else [],
        "evictions": list(summary.evictions) if summary is not None else [],
        "candidates": summary.as_record(trace) if summary is not None else None,
        "context_identity": context_identity,
        "missing": None,
    }
    if loaded is not None:
        selected = {
            item.record_id: item.kind
            for item in trace
            if item.source_kind == evidence_resolver.SOURCE_KIND
        }
        record["recent"] = evidence_resolver.recent_turns(loaded, selected)
    emit_record(record)
    return EXIT_OK
