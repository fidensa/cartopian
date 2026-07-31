"""Observe and persist truthful MCP restart state for the current process."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from collections import OrderedDict
from typing import Any, Dict, Mapping, Optional, Tuple

from cli.emit import emit_record
from cli.install_state import evaluate_record, stable_projection
from cli.install_workflow import STATE_FILE, _atomic_write_text
from cli.main import EXIT_FAIL, EXIT_OK, stderr_error
from cli.restart_state import (
    client_context_from_environment,
    evaluate_restart,
    restart_record,
    running_server_from_environment,
)
from cli.version_identities import (
    INSTALL_STATE_PRESENT,
    content_bound_restart_candidate,
    install_record_evidence,
    installed_content,
    mcp_content_identity,
    restart_evidence_withheld,
)

_MAX_STATE_BYTES = 2 * 1024 * 1024


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.description = (
        "Compare installed MCP content with the connected server process, "
        "persist deterministic restart state, and require fresh-process "
        "matching-content proof before activation."
    )
    subparser.add_argument("install_root", type=Path)
    subparser.add_argument(
        "--mcp-affecting-change",
        action="store_true",
        help=(
            "record that the just-completed update affected the MCP server "
            "surface; this records proof requirements but performs no restart"
        ),
    )


def _read_state(path: Path) -> Dict[str, Any]:
    try:
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size > _MAX_STATE_BYTES
        ):
            raise ValueError("state is absent, symlinked, or oversized")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(
            "install-update-state.json is unavailable or unsafe"
        ) from exc
    if not isinstance(raw, dict):
        raise ValueError("install-update-state.json is not an object")
    return raw


def _record_authority(record: Any) -> Tuple[bool, Optional[str]]:
    """Report whether persisted evidence in ``record`` may strengthen at all.

    File and JSON shape say nothing about whether a record was written by a
    supported adapter or ever proved the content it describes. Pending restart
    rows and persisted surface proofs are siblings of the installed-content
    row, so they are readable only through the same authority the installed
    identity itself is read through.
    """
    evidence = install_record_evidence(record)
    return (
        evidence["status"] == INSTALL_STATE_PRESENT,
        evidence["mcp_identity"],
    )


def _pending_restart(
    record: Any, client_id: str, observed_mcp_identity: Optional[str]
) -> "OrderedDict[str, Any]":
    """Select the pending restart row this observed content may be read with.

    The row is withheld — not merely discounted later — unless the same
    record's MCP identity names the content being verified, so no process,
    instance, or freshness fact can be attributed from a record that attests
    other content.
    """
    return content_bound_restart_candidate(
        install_record_evidence(record),
        observed_mcp_identity=observed_mcp_identity,
        client_id=client_id,
    )


def _persisted_installed_proof(
    record: Mapping[str, Any], identity: Any
) -> bool:
    surfaces = record.get("surfaces")
    if not isinstance(surfaces, list) or identity is None:
        return False
    matches = [
        item
        for item in surfaces
        if isinstance(item, Mapping)
        and item.get("kind") == "mcp-server-files"
        and item.get("observed_content_identity") == identity
        and item.get("state") in ("current", "verified")
        and item.get("verification") == "verified"
        and item.get("completeness") == "complete"
    ]
    return len(matches) == 1


def _update_version_facts(
    record: Dict[str, Any],
    *,
    installed: Mapping[str, Any],
    running: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> None:
    versions = record.get("versions")
    if not isinstance(versions, list):
        return
    for item in versions:
        if not isinstance(item, dict):
            continue
        if item.get("kind") == "installed_content":
            item["mcp_identity"] = installed.get("identity")
            item["mcp_verification"] = installed.get(
                "verification", "unknown"
            )
            item["mcp_completeness"] = installed.get(
                "completeness", "unknown"
            )
        elif item.get("kind") == "running_server":
            item["value"] = running.get("loaded_identity")
            item["process_id"] = running.get("process_id")
            item["instance_id"] = running.get("instance_id")
            item["loaded_content"] = copy.deepcopy(
                running.get("loaded_content")
            )
            item["verification"] = running.get("verification", "unknown")
            if projection.get("status") == "current":
                item["state"] = "current"
            elif projection.get("reason_code") in (
                "running_content_stale",
                "fresh_process_content_stale",
            ):
                item["state"] = "stale-runtime"
            else:
                item["state"] = "unknown"


def handler(args: argparse.Namespace) -> int:
    root = args.install_root.expanduser().resolve()
    if root == Path(root.anchor) or root == Path.home().resolve():
        stderr_error("install root is an unsafe broad path")
        return EXIT_FAIL
    state_path = root / STATE_FILE
    try:
        raw = _read_state(state_path)
    except ValueError as exc:
        stderr_error(str(exc))
        return EXIT_FAIL

    running = running_server_from_environment()
    client = client_context_from_environment()
    record_usable, recorded_mcp = _record_authority(raw)
    installed_observation = mcp_content_identity(root)
    observed_identity = installed_observation.get("identity")
    restart_evidence = _pending_restart(
        raw, str(client["id"]), observed_identity
    )
    pending = restart_evidence["row"]
    # An uninterpretable record — or one whose restart evidence this content
    # cannot claim — is not evidence that nothing changed: whatever wrote it
    # may have touched the MCP surface, so the surface stays affected. The
    # shared authority now reports both refusals as withheld evidence; this
    # command keeps its own record check as well, so the classification can
    # only ever add to what is already treated as affected here.
    affected = bool(
        args.mcp_affecting_change
        or pending is not None
        or not record_usable
        or restart_evidence_withheld(restart_evidence)
    )
    prior = (
        {
            "process_id": pending.get("process_id"),
            "instance_id": pending.get("instance_id"),
        }
        if pending is not None
        else (
            {
                "process_id": running.get("process_id"),
                "instance_id": running.get("instance_id"),
            }
            if args.mcp_affecting_change
            else None
        )
    )
    installed_provenance = installed_content(root)
    installed_verification = installed_provenance.get(
        "mcp_verification", "unknown"
    )
    installed_state = installed_provenance.get("mcp_state", "unknown")
    if (
        record_usable
        # A positive record that names its own MCP subset identity must name
        # the observed content; otherwise it attests other content and cannot
        # strengthen this verdict.
        and (recorded_mcp is None or recorded_mcp == observed_identity)
        and installed_observation.get("completeness") == "complete"
        and (
            (
                pending is not None
                and pending.get("installed_identity") == observed_identity
                and pending.get("installed_verification") == "verified"
            )
            or _persisted_installed_proof(raw, observed_identity)
        )
    ):
        installed_verification = "verified"
        installed_state = "verified"
    installed = {
        "identity": installed_observation.get("identity"),
        "state": installed_state,
        "verification": installed_verification,
        "completeness": installed_observation.get(
            "completeness", "unknown"
        ),
        "authority": installed_observation.get("authority"),
    }
    projection = evaluate_restart(
        installed=installed,
        running=running,
        affected_surfaces={
            "mcp_affecting_change": affected,
            "verification": "verified",
            "source": "explicit-update-or-persisted-restart",
        },
        client=client,
        prior_process=prior,
    )
    updated = copy.deepcopy(raw)
    updated["restarts"] = [restart_record(projection)]
    _update_version_facts(
        updated,
        installed=installed,
        running=running,
        projection=projection,
    )
    if projection["status"] in (
        "restart_required",
        "restart_instructed",
        "verification_pending",
        "unverified",
    ):
        updated["state"] = "restart-required"
    elif projection["status"] == "blocked":
        updated["state"] = "blocked"
    elif projection["status"] == "current":
        updated["state"] = "complete"
    evaluated = evaluate_record(updated)
    _atomic_write_text(
        state_path,
        json.dumps(
            stable_projection(evaluated),
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
    )
    emit_record(
        {
            "restart_state": projection,
            "workflow": stable_projection(evaluated),
        }
    )
    return (
        EXIT_FAIL
        if evaluated["outcome"]["status"] in ("blocked", "failed")
        else EXIT_OK
    )
