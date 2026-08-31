"""Record an operator message at an optional host intake boundary.

This handler is host-facing ingress, not a PM transcription API.  The CLI can
enforce the managed-role and MCP markers below; it cannot authenticate the
human authorship of bytes supplied by an otherwise unmarked local process.
Supported integrations therefore invoke it from their native user-message
callback with the raw payload, before the model receives the governed unit.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from cli.atomic_write import (
    DIR_FD_SUPPORTED,
    GuardRefusal,
    _atomic_write_via_dir_fd,
    _atomic_write_via_path,
    _snapshot_chain,
    make_tmp_name,
)
from cli import report_identity
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE, stderr_guard, stderr_usage
from cli.request_trace import (
    ASSENT_RECOVERY,
    CORRECTION_ID_RE,
    REQUEST_ID_RE,
    REQUESTS_DIRNAME,
    Antecedent,
    AntecedentSource,
    GovernedUnit,
    RequestRefusal,
    antecedent_identity,
    check_antecedent_completeness,
    check_message_adjacency,
    content_identity,
    is_low_information,
    load_records,
)


# These markers prove the caller is not the host/operator intake boundary.
# Dispatch exports CARTOPIAN_ROLE into every managed role session; the MCP
# server sets CARTOPIAN_MCP_TOOL_CALL around every in-process tool invocation.
NON_OPERATOR_MARKERS = ("CARTOPIAN_ROLE", "CARTOPIAN_MCP_TOOL_CALL")


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("project_root", help="Absolute Cartopian project root")
    parser.add_argument("--request-id", required=True, help="REQUEST-NNN")
    parser.add_argument("--unit", required=True, help="project, planning:PLAN-NNN, or task:TASK-NN-NNN")
    parser.add_argument(
        "--content-file",
        required=True,
        help="Host-boundary file containing only the raw operator message",
    )
    parser.add_argument(
        "--antecedent-file",
        default=None,
        help=(
            "Host-boundary file containing the complete immediately preceding "
            "question or proposal this message answers. Required when the "
            "message is a low-information response such as \"yes\"."
        ),
    )
    parser.add_argument(
        "--antecedent-scope",
        default=None,
        help=(
            "The exact scope the antecedent proposal covers, quoted verbatim "
            "from that proposal. Required with --antecedent-file."
        ),
    )
    parser.add_argument(
        "--antecedent-host",
        default=None,
        help="Host that supplied the antecedent message. Required with --antecedent-file.",
    )
    parser.add_argument(
        "--antecedent-conversation",
        default=None,
        help="Host conversation the antecedent message belongs to. Required with --antecedent-file.",
    )
    parser.add_argument(
        "--antecedent-message",
        default=None,
        help="Host message id of the antecedent proposal. Required with --antecedent-file.",
    )
    parser.add_argument(
        "--antecedent-order",
        default=None,
        type=int,
        help="Position of the antecedent message in that conversation. Required with --antecedent-file.",
    )
    parser.add_argument(
        "--response-host",
        default=None,
        help=(
            "Host that supplied the captured response; must equal "
            "--antecedent-host. Required with --antecedent-file."
        ),
    )
    parser.add_argument(
        "--response-conversation",
        default=None,
        help=(
            "Host conversation the captured response belongs to; must equal "
            "--antecedent-conversation. Required with --antecedent-file."
        ),
    )
    parser.add_argument(
        "--response-message",
        default=None,
        help=(
            "Host message id of the captured response; must differ from "
            "--antecedent-message. Required with --antecedent-file."
        ),
    )
    parser.add_argument(
        "--response-order",
        default=None,
        type=int,
        help=(
            "Position of the captured response in that conversation; must be "
            "exactly one after --antecedent-order. Required with --antecedent-file."
        ),
    )
    parser.add_argument("--correction-of", default=None, help="Original REQUEST-NNN for an explicit follow-up correction")
    parser.add_argument("--captured-at", default=None, help="UTC ISO-8601 timestamp; defaults to the intake time")


def _unit(raw: str) -> GovernedUnit:
    if raw == "project":
        return GovernedUnit("project", "project")
    kind, sep, identifier = raw.partition(":")
    if not sep or kind not in ("planning", "task"):
        raise ValueError("--unit must be project, planning:PLAN-NNN, or task:TASK-NN-NNN")
    grammar = r"PLAN-\d{3}" if kind == "planning" else r"TASK-\d{2}-\d{3}"
    if re.fullmatch(grammar, identifier) is None:
        raise ValueError(f"invalid {kind} unit id: {identifier!r}")
    return GovernedUnit(kind, identifier)


def _write_new(root: Path, filename: str, data: bytes) -> Path:
    base = root / REQUESTS_DIRNAME
    if not base.exists():
        os.mkdir(base, 0o755)
    if base.is_symlink() or not base.is_dir():
        raise GuardRefusal("unsafe-request-store", "requests/ is not a real directory")
    destination = base / filename
    if os.path.lexists(destination):
        raise GuardRefusal("immutable-request", f"request record already exists: {filename}")
    snapshot = _snapshot_chain(str(base.resolve()), str(root.resolve()))
    tmp = make_tmp_name(filename)
    writer = _atomic_write_via_dir_fd if DIR_FD_SUPPORTED else _atomic_write_via_path
    writer(str(base.resolve()), snapshot, filename, tmp, data, 0o600, expect_absent=True)
    return destination


def _late_derivatives(root: Path, unit: GovernedUnit) -> list[str]:
    if unit.kind == "project":
        candidates = [root / "REQUIREMENTS.md", root / "IMPLEMENTATION_PLAN.md"]
        for directory in ("phases", "tasks", "specs", "prompts"):
            base = root / directory
            if base.is_dir():
                candidates.extend(
                    path
                    for path in base.rglob("*")
                    if path.is_file()
                )
    elif unit.kind == "task":
        suffix = unit.identifier.removeprefix("TASK-")
        # Tasks and specs are commonly authored in bulk during project
        # planning, before the operator message that actually initiates a task
        # run.  The task-unit boundary is the assignment/review prompt, not the
        # pre-existing planning artifact.
        candidates = [
            root / "prompts" / f"PROMPT-{suffix}.md",
            report_identity.completion_report_path(root, suffix),
            report_identity.review_report_path(root, suffix),
            root / "reviews" / f"REVIEW-{suffix}.md",
        ]
    else:
        candidates = [root / "prompts" / f"PROMPT-{unit.identifier}.md"]
    return sorted({path.relative_to(root).as_posix() for path in candidates if path.is_file()})


def handler(args: argparse.Namespace) -> int:
    for marker in NON_OPERATOR_MARKERS:
        if os.environ.get(marker):
            stderr_guard(
                "non-operator-request-capture: "
                f"{marker} is set; dispatched roles and managed-agent tool "
                "calls cannot author the operator request channel"
            )
            return EXIT_FAIL
    root = Path(args.project_root)
    if not root.is_absolute() or not (root / "cartopian.toml").is_file():
        stderr_usage("project_root must be an absolute Cartopian project root")
        return EXIT_USAGE
    try:
        unit = _unit(args.unit)
        raw = Path(args.content_file).read_bytes()
        text = raw.decode("utf-8")
    except (ValueError, OSError, UnicodeDecodeError) as exc:
        stderr_usage(str(exc))
        return EXIT_USAGE
    # No byte ceiling: the operator message is the authority being captured.
    # Its integrity is bound by SHA-256 content identity, never by size.
    #
    # A low-information response ("yes", "continue", "proceed") states no
    # intent of its own, so it is captured only bound to the complete
    # immediately preceding question or proposal and that proposal's exact
    # scope. Refusing here keeps a detached assent out of the record store
    # entirely; the trace resolver refuses the same shape again for records
    # and channels this command did not write.
    antecedent = None
    antecedent_file = getattr(args, "antecedent_file", None)
    supplied = {
        "--antecedent-file": antecedent_file,
        "--antecedent-scope": getattr(args, "antecedent_scope", None),
        "--antecedent-host": getattr(args, "antecedent_host", None),
        "--antecedent-conversation": getattr(args, "antecedent_conversation", None),
        "--antecedent-message": getattr(args, "antecedent_message", None),
        "--antecedent-order": getattr(args, "antecedent_order", None),
        "--response-host": getattr(args, "response_host", None),
        "--response-conversation": getattr(args, "response_conversation", None),
        "--response-message": getattr(args, "response_message", None),
        "--response-order": getattr(args, "response_order", None),
    }
    if any(value is not None for value in supplied.values()):
        # Provenance is not an optional extra, and one side of it is not
        # enough: unless both messages are placed, nothing in the record shows
        # that the retained proposal was the message immediately before this
        # one rather than a faithful quotation from somewhere else.
        missing = [flag for flag, value in supplied.items() if value is None]
        if missing:
            stderr_usage(
                "the antecedent flags are given together; missing: "
                + ", ".join(sorted(missing))
            )
            return EXIT_USAGE
        try:
            antecedent_raw = Path(antecedent_file).read_bytes()
            antecedent_text = antecedent_raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            stderr_usage(str(exc))
            return EXIT_USAGE
        if not supplied["--antecedent-scope"].strip():
            stderr_usage("--antecedent-scope must be non-empty")
            return EXIT_USAGE
        for flag in (
            "--antecedent-host",
            "--antecedent-conversation",
            "--antecedent-message",
            "--response-host",
            "--response-conversation",
            "--response-message",
        ):
            if not supplied[flag].strip():
                stderr_usage(f"{flag} must be non-empty")
                return EXIT_USAGE
        source = AntecedentSource(
            supplied["--antecedent-host"],
            supplied["--antecedent-conversation"],
            supplied["--antecedent-message"],
        )
        response = AntecedentSource(
            supplied["--response-host"],
            supplied["--response-conversation"],
            supplied["--response-message"],
        )
        order = supplied["--antecedent-order"]
        response_order = supplied["--response-order"]
        binding = Antecedent(
            antecedent_text,
            supplied["--antecedent-scope"],
            source,
            order,
            response,
            response_order,
            antecedent_identity(
                text=antecedent_text,
                scope=supplied["--antecedent-scope"],
                source=source,
                order=order,
                response=response,
                response_order=response_order,
            ),
        )
        try:
            if order < 0 or response_order < 0:
                raise RequestRefusal(
                    "non-adjacent-antecedent",
                    "--antecedent-order and --response-order are message "
                    "positions and cannot be negative",
                    ASSENT_RECOVERY,
                )
            check_message_adjacency(
                source, order, response, response_order, what="the captured response"
            )
            check_antecedent_completeness("--antecedent-file", binding)
        except RequestRefusal as refusal:
            stderr_guard(
                f"{refusal.rule}: {refusal.detail}. recovery: {refusal.recovery}"
            )
            return EXIT_FAIL
        antecedent = binding.as_record()
    if antecedent is None and is_low_information(text):
        stderr_guard(
            "detached-assent: the captured message is a low-information "
            "response and is not standalone evidence of intent. recovery: "
            f"{ASSENT_RECOVERY}"
        )
        return EXIT_FAIL
    try:
        records = load_records(root)
    except RequestRefusal as refusal:
        stderr_guard(f"{refusal.rule}: {refusal.detail}")
        return EXIT_FAIL
    originals = [record for record in records if record.kind == "original"]
    correction_of = args.correction_of
    if correction_of:
        if not REQUEST_ID_RE.fullmatch(correction_of):
            stderr_usage("--correction-of must match REQUEST-NNN")
            return EXIT_USAGE
        original = next((record for record in originals if record.request_id == correction_of), None)
        if original is None:
            stderr_guard(f"unknown-request: {correction_of} has no initiating record")
            return EXIT_FAIL
        identity = content_identity(raw)
        if any(
            record.request_id == correction_of and record.identity == identity
            for record in records
        ):
            stderr_guard(
                "duplicate-request-content: identical content already exists "
                f"for originating request {correction_of}"
            )
            return EXIT_FAIL
        sequence = 1 + max((r.sequence for r in records if r.request_id == correction_of), default=0)
        record_id = f"{correction_of}-CORRECTION-{sequence:03d}"
        request_id = correction_of
        if args.request_id != correction_of:
            stderr_usage("--request-id must equal --correction-of for a correction")
            return EXIT_USAGE
        unit = original.unit
        kind = "correction"
    else:
        if not REQUEST_ID_RE.fullmatch(args.request_id):
            stderr_usage("--request-id must match REQUEST-NNN")
            return EXIT_USAGE
        if any(record.kind == "original" and record.unit == unit for record in records):
            stderr_guard(f"request-already-captured: {unit.kind}:{unit.identifier} already has an initiating request")
            return EXIT_FAIL
        late = _late_derivatives(root, unit)
        if late:
            stderr_guard(
                "late-request-capture: PM-derived artifacts already exist for "
                f"{unit.kind}:{unit.identifier}; first found: {late[0]}"
            )
            return EXIT_FAIL
        record_id = request_id = args.request_id
        sequence = 0
        kind = "original"
    captured_at = args.captured_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    record = {
        "captured_at": captured_at,
        "content_identity": content_identity(raw),
        "kind": kind,
        "record_id": record_id,
        "request_id": request_id,
        "schema": "cartopian-original-request-v1",
        "sequence": sequence,
        "text": text,
        "unit": unit.as_record(),
    }
    if antecedent is not None:
        record["antecedent"] = antecedent
    data = (json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    try:
        path = _write_new(root.resolve(), record_id + ".json", data)
    except (GuardRefusal, RequestRefusal) as refusal:
        stderr_guard(f"{refusal.rule}: {refusal.detail}")
        return EXIT_FAIL
    emit_record({"action": "capture-request", "details": {"record_id": record_id, "request_id": request_id, "kind": kind, "path": str(path), "content_identity": record["content_identity"], "unit": unit.as_record()}})
    return EXIT_OK
