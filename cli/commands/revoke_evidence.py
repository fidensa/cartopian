"""`cartopian revoke-evidence <project-root> --evidence <id> ...` — operator-only.

Revokes request evidence the operator no longer stands behind: a captured
turn (`cs-<handle>/turn-<n>`), a `capture-request` record (`REQUEST-NNN` or
`REQUEST-NNN-CORRECTION-NNN`), or a hand-written host chat file
(`CHAT-...`). Three ordered steps (plan section 4.13):

1. Record a revocation entry in ``requests/revocations.json`` naming the
   evidence identities and every review-context identity that bound them
   (collected from ``reviews/`` and ``prompts/``; never filenames). This is
   durable first: from this point the shared resolver refuses every review
   bound to a revoked identity, whether or not its prompt still exists.
2. Move the affected project-side records byte-for-byte to
   ``requests/quarantine/`` (``requests/REQUEST-*.json`` and
   ``requests/chat/*.json``; a captured turn lives in the intake root and has
   no project file to move). The move is a rename, so bytes are preserved;
   it is idempotent and retry-safe after a crash between the two steps: an
   entry that already names exactly these identities is reused rather than
   duplicated, and a record already in quarantine is left there.
3. Nothing is re-certified. A fresh operator scope statement captured through
   the adapter supersedes the revoked original at the next lock
   (``supersedes: <revoked id>``); quarantined files are auditable, never
   resolved.

Operator-only like ``capture-request``: absent from the managed-agent MCP
registry and refused whenever ``CARTOPIAN_ROLE`` or
``CARTOPIAN_MCP_TOOL_CALL`` is set. The PM never runs it.
"""
from __future__ import annotations

import argparse
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from cli import evidence_resolver, request_trace
from cli.commands.capture_request import NON_OPERATOR_MARKERS
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE, stderr_guard, stderr_usage

EVIDENCE_ID_RES = (
    evidence_resolver.CAPTURE_ID_RE,
    request_trace.REQUEST_ID_RE,
    request_trace.CORRECTION_ID_RE,
    request_trace.CHAT_RECORD_ID_RE,
)
_EVIDENCE_LINE_RE = re.compile(r"^Request evidence:\s*(.+?)\s*$", re.MULTILINE)
_CONTEXT_LINE_RE = re.compile(r"^Request-context identity:\s*(sha256:[0-9a-f]{64})\s*$", re.MULTILINE)


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("project_root", help="Absolute Cartopian project root")
    subparser.add_argument(
        "--evidence",
        action="append",
        required=True,
        metavar="ID",
        help="Evidence identity to revoke (repeatable): cs-<handle>/turn-<n>, REQUEST-NNN[-CORRECTION-NNN], or CHAT-...",
    )
    subparser.add_argument(
        "--review-context",
        action="append",
        default=[],
        metavar="SHA256",
        help="Additional review-context identity that bound the evidence (repeatable); bound reviews and prompts are collected automatically",
    )
    subparser.add_argument("--reason", default="", help="Operator's reason, recorded verbatim")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _valid_id(value: str) -> bool:
    return any(pattern.fullmatch(value) for pattern in EVIDENCE_ID_RES)


def bound_review_contexts(project_root: Path, ids: Set[str]) -> List[str]:
    """Review-context identities of every review or prompt naming any id."""
    found: Set[str] = set()
    for dirname in ("reviews", "prompts"):
        base = project_root / dirname
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.md")):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            named: Set[str] = set()
            for match in _EVIDENCE_LINE_RE.finditer(text):
                named.update(item.strip() for item in match.group(1).split(","))
            if not (named & ids):
                continue
            for match in _CONTEXT_LINE_RE.finditer(text):
                found.add(match.group(1))
    return sorted(found)


def _project_record_paths(project_root: Path, evidence_id: str) -> List[Tuple[Path, Path]]:
    """``(source, quarantine destination)`` pairs for one identity."""
    requests = project_root / request_trace.REQUESTS_DIRNAME
    quarantine = requests / evidence_resolver.QUARANTINE_DIRNAME
    if evidence_resolver.CAPTURE_ID_RE.fullmatch(evidence_id):
        return []
    if request_trace.CHAT_RECORD_ID_RE.fullmatch(evidence_id):
        name = f"{evidence_id}.json"
        return [(requests / request_trace.HOST_CHAT_DIRNAME / name, quarantine / request_trace.HOST_CHAT_DIRNAME / name)]
    name = f"{evidence_id}.json"
    return [(requests / name, quarantine / name)]


def quarantine(project_root: Path, ids: Set[str]) -> Tuple[List[str], List[str], List[str], Optional[str]]:
    """Move project-side records to quarantine. Returns
    ``(moved, already_quarantined, no_project_record, conflict)``."""
    moved: List[str] = []
    already: List[str] = []
    none: List[str] = []
    for evidence_id in sorted(ids):
        pairs = _project_record_paths(project_root, evidence_id)
        if not pairs:
            none.append(evidence_id)
            continue
        for source, dest in pairs:
            rel_dest = dest.relative_to(project_root).as_posix()
            if source.is_symlink() or dest.is_symlink():
                return moved, already, none, f"{evidence_id}: a symlink stands where a record is expected"
            if dest.is_file() and source.is_file():
                if source.read_bytes() != dest.read_bytes():
                    return moved, already, none, (
                        f"{evidence_id}: {rel_dest} already exists with different bytes; "
                        "resolve by hand before retrying"
                    )
                source.unlink()
                already.append(rel_dest)
                continue
            if dest.is_file():
                already.append(rel_dest)
                continue
            if not source.is_file():
                none.append(evidence_id)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, dest)
            moved.append(rel_dest)
    return moved, already, none, None


def handler(args: argparse.Namespace) -> int:
    for marker in NON_OPERATOR_MARKERS:
        if os.environ.get(marker):
            stderr_guard(
                "non-operator-revocation: "
                f"{marker} is set; dispatched roles and managed-agent tool "
                "calls cannot revoke or quarantine operator evidence"
            )
            return EXIT_FAIL
    root = Path(args.project_root)
    if not root.is_absolute() or not (root / "cartopian.toml").is_file():
        stderr_usage("project_root must be an absolute Cartopian project root")
        return EXIT_USAGE
    root = root.resolve()
    ids = {item.strip() for item in args.evidence if item and item.strip()}
    bad = sorted(item for item in ids if not _valid_id(item))
    if not ids or bad:
        stderr_usage(
            "--evidence must name capture identities (cs-<handle>/turn-<n>), "
            "REQUEST-NNN[-CORRECTION-NNN], or CHAT-... records"
            + (f"; invalid: {', '.join(bad)}" if bad else "")
        )
        return EXIT_USAGE
    contexts = set(args.review_context or [])
    bad_contexts = sorted(c for c in contexts if not re.fullmatch(r"sha256:[0-9a-f]{64}", c))
    if bad_contexts:
        stderr_usage(f"--review-context must be sha256:<64 hex>; invalid: {', '.join(bad_contexts)}")
        return EXIT_USAGE
    contexts.update(bound_review_contexts(root, ids))

    # Step 1: the durable revocation entry. An entry naming exactly these
    # identities is reused (retry after a crash between the steps), with any
    # newly discovered review contexts folded in.
    entries = evidence_resolver.read_revocations(root)
    entry: Optional[Dict[str, Any]] = None
    for existing in entries:
        if sorted(existing.get("evidence") or []) == sorted(ids):
            entry = existing
            break
    outcome = "recorded"
    if entry is None:
        entry = {
            "revocation_id": f"REVOKE-{len(entries) + 1:03d}",
            "recorded_at": _now(),
            "evidence": sorted(ids),
            "review_contexts": sorted(contexts),
            "reason": args.reason or "",
        }
        entries.append(entry)
    else:
        outcome = "retried"
        merged = set(entry.get("review_contexts") or []) | contexts
        entry["review_contexts"] = sorted(merged)
    evidence_resolver.write_revocations(root, entries)

    # Step 2: quarantine, after the ledger is durable.
    moved, already, none, conflict = quarantine(root, ids)
    record = {
        "action": "revoke-evidence",
        "outcome": outcome,
        "revocation_id": entry["revocation_id"],
        "evidence": entry["evidence"],
        "review_contexts": entry["review_contexts"],
        "quarantined": moved,
        "already_quarantined": already,
        "no_project_record": none,
        "revocations_path": str(evidence_resolver.revocations_path(root)),
        "quarantine_path": str(root / request_trace.REQUESTS_DIRNAME / evidence_resolver.QUARANTINE_DIRNAME),
        "conflict": conflict,
    }
    emit_record(record)
    if conflict:
        stderr_guard(f"quarantine-conflict: {conflict}")
        return EXIT_FAIL
    return EXIT_OK
