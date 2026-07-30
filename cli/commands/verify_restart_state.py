"""Observe and persist truthful MCP restart state for the current process."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

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
from cli.version_identities import installed_content, mcp_content_identity

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


def _pending_restart(
    record: Mapping[str, Any], client_id: str
) -> Optional[Dict[str, Any]]:
    rows = record.get("restarts")
    if not isinstance(rows, list):
        return None
    candidates = [
        dict(item)
        for item in rows
        if isinstance(item, Mapping)
        and item.get("state") in ("required", "pending")
        and (
            item.get("client") == client_id
            or client_id == "unsupported"
            or len(rows) == 1
        )
    ]
    return candidates[0] if len(candidates) == 1 else None


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
    pending = _pending_restart(raw, str(client["id"]))
    affected = bool(args.mcp_affecting_change or pending is not None)
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
    installed_observation = mcp_content_identity(root)
    installed_provenance = installed_content(root)
    installed_verification = installed_provenance.get(
        "mcp_verification", "unknown"
    )
    installed_state = installed_provenance.get("mcp_state", "unknown")
    if (
        (
            pending is not None
            and pending.get("installed_identity")
            == installed_observation.get("identity")
            and pending.get("installed_verification") == "verified"
        )
        or _persisted_installed_proof(
            raw, installed_observation.get("identity")
        )
    ) and installed_observation.get("completeness") == "complete":
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
