"""Peer Cartopian version identities with explicit authority and state."""
from __future__ import annotations

import os
import subprocess
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Optional

from cli.config_schema import identity_contract


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
    return {
        "revision": revision,
        "recorded_ref": recorded_ref,
        "materialization": materialization,
        "verification": verification,
        "state": verification,
        "authority": identity_contract()["installed_content"]["authority"],
        "loaded_root": str(loaded_root),
        "attribution": "runtime-inspection",
    }


def running_server(
    content: Dict[str, Any],
    *,
    process_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Identify the connected process and the content it loaded."""
    verification = content["verification"]
    if verification == "verified":
        state = "current"
    elif verification == "symlink-divergent":
        state = "stale-runtime"
    else:
        state = "unknown"
    return {
        "process_id": process_id if process_id is not None else os.getpid(),
        "loaded_content": {
            "revision": content["revision"],
            "loaded_root": content["loaded_root"],
            "verification": verification,
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
) -> "OrderedDict[str, Dict[str, Any]]":
    """Return deterministic structured peer identities for this context."""
    content = installed_content(root)
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
    records["installed_content"] = content
    records["project_schema_version"] = schema_record
    records["running_server"] = (
        running_server(content)
        if include_running_server
        else {
            "process_id": None,
            "loaded_content": None,
            "state": "unknown",
            "authority": identity_contract()["running_server"]["authority"],
            "attribution": "not-connected-context",
        }
    )
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

