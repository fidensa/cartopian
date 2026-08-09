"""Pure installed-versus-running MCP restart-state projection.

The evaluator keeps disk content, connected-process content, affected-surface
evidence, client identity, and fresh-process proof as separate authorities. It
does not restart a client, kill a process, control a GUI, or treat an operator
instruction as proof that the instruction was followed.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from collections import OrderedDict
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

RUNNING_SERVER_ENV = "CARTOPIAN_MCP_RUNNING_SERVER"

RESTART_STATUSES: Tuple[str, ...] = (
    "no_restart_needed",
    "restart_required",
    "restart_instructed",
    "verification_pending",
    "current",
    "blocked",
    "unverified",
)

RESTART_REASON_CODES: Tuple[str, ...] = (
    "mcp_surface_unaffected",
    "affected_surface_unknown",
    "installed_content_unknown",
    "installed_content_unverified",
    "running_content_unknown",
    "running_content_unverified",
    "running_content_stale",
    "fresh_process_required",
    "fresh_process_unknown",
    "fresh_process_content_unknown",
    "fresh_process_content_stale",
    "fresh_process_current",
    "client_unsupported",
)

_EXPECTED_PROOF = (
    "Reconnect to Cartopian and confirm a new server process plus verified "
    "loaded content matching installed MCP content."
)
_STATIC_RISK = (
    "Instruction wording has static parity coverage; process and loaded-content "
    "proof must come from the reconnected client."
)


def _instruction(action: str) -> "OrderedDict[str, str]":
    return OrderedDict(
        (
            ("class", "restart-client"),
            ("action", action),
            ("expected_proof", _EXPECTED_PROOF),
            ("evidence", "static-only"),
            ("residual_risk", _STATIC_RISK),
        )
    )


CLIENT_RESTART_INSTRUCTIONS: "OrderedDict[str, OrderedDict[str, str]]" = (
    OrderedDict(
        (
            ("claude-code", _instruction("Restart Claude Code.")),
            ("codex", _instruction("Restart Codex.")),
            ("gemini", _instruction("Restart Gemini CLI.")),
            ("devin", _instruction("Restart Devin.")),
            ("windsurf", _instruction("Restart Windsurf.")),
            ("claude-desktop", _instruction("Restart Claude Desktop.")),
            ("cursor", _instruction("Restart Cursor.")),
            ("opencode", _instruction("Restart opencode.")),
        )
    )
)

_CLIENT_ALIASES: Tuple[Tuple[str, str], ...] = (
    ("codex-mcp-client", "codex"),
    ("claude-code", "claude-code"),
    ("claude desktop", "claude-desktop"),
    ("claude-desktop", "claude-desktop"),
    ("gemini-cli", "gemini"),
    ("gemini", "gemini"),
    ("windsurf", "windsurf"),
    ("cursor", "cursor"),
    ("devin", "devin"),
    ("opencode", "opencode"),
    ("codex", "codex"),
)


def _platform_name(value: Optional[str]) -> str:
    raw = (value or sys.platform).lower()
    if raw.startswith(("win", "cygwin", "msys")):
        return "windows"
    if raw.startswith("darwin"):
        return "macos"
    if raw.startswith("linux"):
        return "linux"
    return "unknown"


def normalize_client_context(
    name: Optional[str],
    *,
    title: Optional[str] = None,
    platform: Optional[str] = None,
    source: str = "mcp-handshake",
) -> "OrderedDict[str, Any]":
    """Resolve a closed supported client identity without guessing."""
    raw_name = (name or "").strip()
    raw_title = (title or "").strip()
    haystack = f"{raw_name} {raw_title}".lower()
    client_id: Optional[str] = None
    for alias, candidate in _CLIENT_ALIASES:
        if alias in haystack:
            client_id = candidate
            break
    return OrderedDict(
        (
            ("id", client_id or "unsupported"),
            ("state", "supported" if client_id else "unsupported"),
            ("reported_name", raw_name or None),
            ("reported_title", raw_title or None),
            ("platform", _platform_name(platform)),
            ("source", source),
        )
    )


def client_context_from_environment(
    selected_clients: Sequence[str] = (),
) -> "OrderedDict[str, Any]":
    """Resolve the current interaction, falling back to one selected client."""
    from cli import host_capability

    name = os.environ.get(host_capability.CLIENT_ENV)
    title = os.environ.get(host_capability.CLIENT_TITLE_ENV)
    if name or title or host_capability.under_mcp_host():
        return normalize_client_context(name, title=title)
    selected = tuple(dict.fromkeys(str(item) for item in selected_clients))
    if len(selected) == 1:
        return normalize_client_context(
            selected[0], source="explicit-client-selection"
        )
    return normalize_client_context(None, source="unavailable")


def running_server_from_environment() -> "OrderedDict[str, Any]":
    """Read the process-scoped fact exported by the connected MCP server."""
    raw = os.environ.get(RUNNING_SERVER_ENV)
    if raw:
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            value = None
        if isinstance(value, Mapping):
            return _running_fact(value)
    return _running_fact({})


def _installed_fact(value: Mapping[str, Any]) -> "OrderedDict[str, Any]":
    return OrderedDict(
        (
            ("identity", value.get("identity")),
            ("state", value.get("state", "unknown")),
            ("verification", value.get("verification", "unknown")),
            ("completeness", value.get("completeness", "unknown")),
            (
                "authority",
                value.get(
                    "authority", "installed-or-materialized-content"
                ),
            ),
        )
    )


def _running_fact(value: Mapping[str, Any]) -> "OrderedDict[str, Any]":
    loaded = value.get("loaded_content")
    nested = loaded if isinstance(loaded, Mapping) else {}
    loaded_identity = value.get("loaded_identity")
    if loaded_identity is None:
        loaded_identity = nested.get("mcp_identity", nested.get("identity"))
    verification = value.get("verification")
    if verification is None:
        verification = nested.get(
            "mcp_verification", nested.get("verification", "unknown")
        )
    completeness = value.get("completeness")
    if completeness is None:
        completeness = nested.get("mcp_completeness", "unknown")
    return OrderedDict(
        (
            ("process_id", value.get("process_id")),
            ("instance_id", value.get("instance_id")),
            ("loaded_identity", loaded_identity),
            ("state", value.get("state", "unknown")),
            ("verification", verification or "unknown"),
            ("completeness", completeness or "unknown"),
            (
                "authority",
                value.get("authority", "connected-mcp-process"),
            ),
        )
    )


def _prior_fact(
    value: Optional[Mapping[str, Any]],
) -> "OrderedDict[str, Any]":
    item = value if isinstance(value, Mapping) else {}
    return OrderedDict(
        (
            ("process_id", item.get("process_id")),
            ("instance_id", item.get("instance_id")),
        )
    )


def _affected_fact(
    values: Sequence[Mapping[str, Any]] | Mapping[str, Any],
) -> "OrderedDict[str, Any]":
    if isinstance(values, Mapping) and "mcp_affecting_change" in values:
        affected = values.get("mcp_affecting_change")
        verification = values.get("verification", "unknown")
        evidence = copy.deepcopy(dict(values))
    else:
        rows = [
            item
            for item in values
            if isinstance(item, Mapping)
            and item.get("kind") == "mcp-server-files"
        ]
        if len(rows) == 1:
            affected = rows[0].get("affected")
            verification = rows[0].get("verification", "unknown")
            evidence = copy.deepcopy(dict(rows[0]))
        else:
            affected = None
            verification = "unknown"
            evidence = {
                "kind": "mcp-server-files",
                "fact_count": len(rows),
            }
    proven = isinstance(affected, bool) and verification == "verified"
    return OrderedDict(
        (
            ("mcp_affecting_change", affected if isinstance(affected, bool) else None),
            ("verification", verification),
            ("proven", proven),
            ("evidence", evidence),
        )
    )


def _new_process(
    prior: Mapping[str, Any], running: Mapping[str, Any]
) -> Optional[bool]:
    prior_instance = prior.get("instance_id")
    running_instance = running.get("instance_id")
    if prior_instance is not None and running_instance is not None:
        return prior_instance != running_instance
    prior_pid = prior.get("process_id")
    running_pid = running.get("process_id")
    if prior_pid is not None and running_pid is not None:
        return prior_pid != running_pid
    return None


def _fresh_proof(
    prior: Mapping[str, Any],
    running: Mapping[str, Any],
    *,
    identities_match: Optional[bool],
) -> "OrderedDict[str, Any]":
    new_process = _new_process(prior, running)
    verified = (
        new_process is True
        and identities_match is True
        and running.get("verification") == "verified"
    )
    return OrderedDict(
        (
            ("previous_process_id", prior.get("process_id")),
            ("previous_instance_id", prior.get("instance_id")),
            ("observed_process_id", running.get("process_id")),
            ("observed_instance_id", running.get("instance_id")),
            ("new_process", new_process),
            ("loaded_content_matches", identities_match),
            ("verification", "verified" if verified else "unverified"),
        )
    )


def _legacy_state(status: str) -> str:
    return {
        "no_restart_needed": "not-required",
        "restart_required": "required",
        "restart_instructed": "pending",
        "verification_pending": "pending",
        "current": "verified",
        "blocked": "blocked",
        "unverified": "required",
    }[status]


def evaluate_restart(
    *,
    installed: Mapping[str, Any],
    running: Mapping[str, Any],
    affected_surfaces: Sequence[Mapping[str, Any]] | Mapping[str, Any],
    client: Mapping[str, Any],
    prior_process: Optional[Mapping[str, Any]] = None,
) -> "OrderedDict[str, Any]":
    """Project deterministic restart state from separate authoritative facts."""
    disk = _installed_fact(installed)
    process = _running_fact(running)
    affected = _affected_fact(affected_surfaces)
    prior = _prior_fact(prior_process)
    installed_identity = disk.get("identity")
    running_identity = process.get("loaded_identity")
    identities_match: Optional[bool] = None
    if installed_identity is not None and running_identity is not None:
        identities_match = installed_identity == running_identity
    fresh = _fresh_proof(prior, process, identities_match=identities_match)

    status: str
    reason: str
    if (
        affected["proven"]
        and affected["mcp_affecting_change"] is False
    ):
        status = "no_restart_needed"
        reason = "mcp_surface_unaffected"
    elif not affected["proven"]:
        status = "restart_required"
        reason = "affected_surface_unknown"
    elif disk["completeness"] == "incomplete":
        status = "restart_required"
        reason = "installed_content_unverified"
    elif installed_identity is None or disk["state"] == "unknown":
        status = "restart_required"
        reason = "installed_content_unknown"
    elif disk["verification"] != "verified":
        status = "restart_required"
        reason = "installed_content_unverified"
    elif running_identity is None:
        status = (
            "verification_pending"
            if fresh["new_process"] is True
            else "restart_required"
        )
        reason = (
            "fresh_process_content_unknown"
            if fresh["new_process"] is True
            else "running_content_unknown"
        )
    elif process["state"] == "stale-runtime":
        status = "restart_required"
        reason = (
            "fresh_process_content_stale"
            if fresh["new_process"] is True
            else "running_content_stale"
        )
    elif process["completeness"] == "incomplete":
        status = "restart_required"
        reason = "running_content_unverified"
    elif process["verification"] != "verified":
        status = "restart_required"
        reason = "running_content_unverified"
    elif process["state"] == "unknown":
        status = (
            "verification_pending"
            if fresh["new_process"] is True
            else "restart_required"
        )
        reason = (
            "fresh_process_content_unknown"
            if fresh["new_process"] is True
            else "running_content_unknown"
        )
    elif identities_match is False:
        status = "restart_required"
        reason = (
            "fresh_process_content_stale"
            if fresh["new_process"] is True
            else "running_content_stale"
        )
    elif fresh["new_process"] is True:
        status = "current"
        reason = "fresh_process_current"
    elif fresh["new_process"] is False:
        status = "verification_pending"
        reason = "fresh_process_required"
    else:
        status = "verification_pending"
        reason = "fresh_process_unknown"

    needs_instruction = status in (
        "restart_required",
        "restart_instructed",
        "verification_pending",
        "unverified",
    )
    client_id = str(client.get("id", "unsupported"))
    instruction = CLIENT_RESTART_INSTRUCTIONS.get(client_id)
    if needs_instruction and instruction is None:
        status = "blocked"
        reason = "client_unsupported"
    selected_instruction = (
        copy.deepcopy(instruction) if needs_instruction and instruction else None
    )
    activation_allowed = status == "current" and fresh["verification"] == "verified"

    return OrderedDict(
        (
            ("status", status),
            ("reason_code", reason),
            ("legacy_state", _legacy_state(status)),
            ("installed", disk),
            ("running", process),
            ("affected_surface", affected),
            ("client", copy.deepcopy(dict(client))),
            ("instruction", selected_instruction),
            ("fresh_proof", fresh),
            ("activation_claim_allowed", activation_allowed),
            ("activation_claim", "active" if activation_allowed else "none"),
        )
    )


def restart_record(projection: Mapping[str, Any]) -> "OrderedDict[str, Any]":
    """Adapt the projection to the install/update contract's restart record."""
    installed = projection.get("installed", {})
    running = projection.get("running", {})
    fresh = projection.get("fresh_proof", {})
    instruction = projection.get("instruction")
    instruction_class = (
        instruction.get("class")
        if isinstance(instruction, Mapping)
        else "none"
    )
    return OrderedDict(
        (
            ("client", projection.get("client", {}).get("id", "unsupported")),
            ("installed_identity", installed.get("identity") or "unknown"),
            (
                "installed_verification",
                installed.get("verification", "unknown"),
            ),
            (
                "installed_completeness",
                installed.get("completeness", "unknown"),
            ),
            ("running_identity", running.get("loaded_identity") or "unknown"),
            (
                "running_verification",
                running.get("verification", "unknown"),
            ),
            (
                "running_completeness",
                running.get("completeness", "unknown"),
            ),
            ("state", projection.get("legacy_state")),
            ("instruction_class", instruction_class),
            ("proof_state", fresh.get("verification", "unverified")),
            ("status", projection.get("status")),
            ("reason_code", projection.get("reason_code")),
            ("process_id", running.get("process_id")),
            ("instance_id", running.get("instance_id")),
            ("previous_process_id", fresh.get("previous_process_id")),
            ("previous_instance_id", fresh.get("previous_instance_id")),
            ("instruction", copy.deepcopy(instruction)),
            (
                "expected_post_restart_proof",
                instruction.get("expected_proof")
                if isinstance(instruction, Mapping)
                else None,
            ),
            (
                "activation_claim_allowed",
                bool(projection.get("activation_claim_allowed")),
            ),
            ("fresh_proof", copy.deepcopy(dict(fresh))),
        )
    )
