"""Peer Cartopian version identities with explicit authority and state."""
from __future__ import annotations

import hashlib
import os
import subprocess
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cli.config_schema import identity_contract

MCP_CONTENT_PATHS: Tuple[str, ...] = (
    "mcp_server",
    "bin/cartopian-mcp",
    "bin/cartopian-mcp.cmd",
)
_TRANSIENT_NAMES = frozenset((".DS_Store", "__pycache__"))


def _read_marker(path: Path) -> Optional[str]:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _git(root: Path, *args: str) -> Optional[str]:
    if not (root / ".git").exists():
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = proc.stdout.strip()
    return value or None


def _content_entries(path: Path) -> Tuple[List[Tuple[str, bytes]], bool]:
    entries: List[Tuple[str, bytes]] = []
    if path.is_symlink():
        try:
            path = path.resolve(strict=True)
        except (OSError, RuntimeError):
            return entries, False
    if path.is_file():
        try:
            entries.append(("@file", path.read_bytes()))
        except OSError:
            return entries, False
        return entries, True
    if not path.is_dir():
        return entries, False
    try:
        children = sorted(path.rglob("*"), key=lambda item: item.as_posix())
    except OSError:
        return entries, False
    for child in children:
        relative = child.relative_to(path)
        if any(part in _TRANSIENT_NAMES for part in relative.parts):
            continue
        if child.suffix in (".pyc", ".pyo"):
            continue
        try:
            if not child.is_file():
                continue
            payload = child.read_bytes()
        except OSError:
            return entries, False
        entries.append((relative.as_posix(), payload))
    return entries, True


def mcp_content_identity(root: Path) -> Dict[str, Any]:
    """Observe the MCP digest without strengthening provenance verification."""
    logical_root = Path(os.path.abspath(root))
    digest = hashlib.sha256()
    found = False
    complete = True
    for relative in MCP_CONTENT_PATHS:
        path = logical_root / relative
        entries, path_complete = _content_entries(path)
        if not path_complete or not entries:
            complete = False
        for nested, payload in entries:
            found = True
            name = f"{relative}/{nested}".encode("utf-8")
            digest.update(len(name).to_bytes(8, "big"))
            digest.update(name)
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
    return {
        "identity": (
            "sha256:" + digest.hexdigest()
            if found and complete
            else None
        ),
        "state": "unverified" if complete else "incomplete",
        "verification": "unverified",
        "completeness": "complete" if complete else "incomplete",
        "authority": identity_contract()["installed_content"]["authority"],
        "paths": list(MCP_CONTENT_PATHS),
    }


def release_version(root: Path) -> Dict[str, Any]:
    """Inspect maintainer-authored release metadata only."""
    value = _read_marker(root / "RELEASE_VERSION")
    return {
        "value": value,
        "state": "known" if value is not None else "unknown",
        "authority": identity_contract()["release_version"]["authority"],
        "verification": "verified" if value is not None else "unknown",
        "attribution": "release-metadata" if value is not None else "unavailable",
    }


def installed_content(root: Path) -> Dict[str, Any]:
    """Identify exact loaded/materialized content without claiming a release."""
    logical_root = Path(os.path.abspath(root))
    loaded_root = logical_root.resolve()
    revision = _git(loaded_root, "rev-parse", "HEAD")
    status = _git(loaded_root, "status", "--porcelain")
    recorded_ref = _read_marker(logical_root / "VERSION")
    if logical_root.is_symlink():
        materialization = "symlink"
    elif (loaded_root / ".git").exists():
        materialization = "source-checkout"
    else:
        materialization = "copy"

    if logical_root.is_symlink() and logical_root != loaded_root:
        verification = "symlink-divergent"
    elif revision is not None and status:
        verification = "dirty"
    elif revision is not None:
        verification = "verified"
    else:
        verification = "unverified"
    mcp = mcp_content_identity(logical_root)
    mcp_complete = mcp["completeness"] == "complete"
    return {
        "revision": revision,
        "recorded_ref": recorded_ref,
        "materialization": materialization,
        "verification": verification,
        "state": verification,
        "authority": identity_contract()["installed_content"]["authority"],
        "loaded_root": str(loaded_root),
        "attribution": "runtime-inspection",
        "mcp_identity": mcp["identity"],
        "mcp_state": verification if mcp_complete else "incomplete",
        "mcp_verification": verification,
        "mcp_completeness": mcp["completeness"],
    }


def running_server(
    content: Dict[str, Any],
    *,
    process_id: Optional[int] = None,
    instance_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Identify the connected process and the content it loaded."""
    verification = content.get("mcp_verification", "unknown")
    completeness = content.get("mcp_completeness", "unknown")
    if verification == "symlink-divergent":
        state = "stale-runtime"
    elif completeness != "complete":
        state = "unknown"
    elif verification == "verified":
        state = "current"
    else:
        state = "unknown"
    return {
        "process_id": process_id if process_id is not None else os.getpid(),
        "instance_id": instance_id,
        "loaded_content": {
            "revision": content["revision"],
            "loaded_root": content["loaded_root"],
            "verification": content["verification"],
            "mcp_identity": content.get("mcp_identity"),
            "mcp_verification": verification,
            "mcp_completeness": completeness,
        },
        "state": state,
        "authority": identity_contract()["running_server"]["authority"],
        "attribution": "connected-process",
    }


def version_identities(
    root: Path,
    *,
    project_schema: Optional[Dict[str, Any]] = None,
    mcp_protocol_version: Optional[str] = None,
    include_running_server: bool = False,
    running_server_fact: Optional[Dict[str, Any]] = None,
) -> "OrderedDict[str, Dict[str, Any]]":
    """Return deterministic structured peer identities for this context."""
    content = installed_content(root)
    installed_record = dict(content)
    if not include_running_server and running_server_fact is None:
        for field in (
            "mcp_identity",
            "mcp_state",
            "mcp_verification",
            "mcp_completeness",
        ):
            installed_record.pop(field, None)
    schema_record = project_schema or {
        "value": None,
        "target": None,
        "state": "unknown",
        "authority": identity_contract()["project_schema_version"]["authority"],
        "verification": "unknown",
        "attribution": "unavailable",
    }
    records: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    records["release_version"] = release_version(root)
    records["installed_content"] = installed_record
    records["project_schema_version"] = schema_record
    if include_running_server:
        records["running_server"] = (
            dict(running_server_fact)
            if running_server_fact is not None
            else running_server(content)
        )
    else:
        records["running_server"] = {
            "process_id": None,
            "loaded_content": None,
            "state": "unknown",
            "authority": identity_contract()["running_server"]["authority"],
            "attribution": "not-connected-context",
        }
    records["mcp_protocol_version"] = {
        "value": mcp_protocol_version,
        "state": "supported" if mcp_protocol_version is not None else "unknown",
        "authority": identity_contract()["mcp_protocol_version"]["authority"],
        "verification": (
            "verified" if mcp_protocol_version is not None else "unknown"
        ),
        "attribution": (
            "wire-handshake" if mcp_protocol_version is not None else "unavailable"
        ),
    }
    return records
