"""Coordinated install, update, repair, and verification workflow.

The planner is deliberately filesystem-first and side-effect free.  It derives
all destinations from the install root and the closed client registry below;
callers may select supported clients and dispositions, but cannot supply a
surface destination or executable.  Applying a plan uses recoverable
replacement for tool-owned files and bounded, preserving merges for explicitly
authorized client configuration.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import tomllib
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from cli.config_schema import identity_contract
from cli.install_state import (
    RECORD_SCHEMA_VERSION,
    SCHEMA_IDENTITY,
    SURFACE_KINDS,
    build_record,
    evaluate_record,
    stable_projection,
)

MCP_PROTOCOL_VERSION = "2024-11-05"
STATE_FILE = "install-update-state.json"

TOOL_SHIPPED: Tuple[Tuple[str, str], ...] = (
    ("protocol", "protocol"),
    ("templates", "templates"),
    ("skills", "skills"),
    ("wrappers", "wrappers"),
    ("cli", "cli"),
    ("mcp_server", "mcp_server"),
    ("bin/cartopian", "bin/cartopian"),
    ("bin/cartopian.cmd", "bin/cartopian.cmd"),
    ("bin/cartopian-mcp", "bin/cartopian-mcp"),
    ("bin/cartopian-mcp.cmd", "bin/cartopian-mcp.cmd"),
    ("install-cartopian.md", "install-cartopian.md"),
    ("scripts/install.py", "scripts/install.py"),
    ("CHANGELOG.md", "protocol/CHANGELOG.md"),
)
COPY_ALWAYS = frozenset(("CHANGELOG.md",))
OPERATOR_FILES = ("cartopian.toml", "projects.json")

CORE_TARGETS = (
    "protocol",
    "templates",
    "skills",
    "cli",
    "bin/cartopian",
    "bin/cartopian.cmd",
    "install-cartopian.md",
    "scripts/install.py",
    "CHANGELOG.md",
)
MCP_TARGETS = ("mcp_server", "bin/cartopian-mcp", "bin/cartopian-mcp.cmd")
WRAPPER_TARGETS = ("wrappers",)
VERIFICATION_TARGETS = ("protocol/INSTALL_VERIFICATION.md",)

SUPPORTED_CLIENTS = (
    "claude-code",
    "codex",
    "gemini",
    "devin",
    "windsurf",
    "claude-desktop",
    "cursor",
)

_CLIENTS: Dict[str, Dict[str, Any]] = {
    "claude-code": {
        "config": ".claude.json",
        "format": "json",
        "bridges": (
            (
                "templates/clients/claude-code/skills/use-cartopian/SKILL.md",
                ".claude/skills/use-cartopian/SKILL.md",
            ),
            (
                "templates/clients/claude-code/commands/use-cartopian.md",
                ".claude/commands/use-cartopian.md",
            ),
        ),
        "restart": "none",
    },
    "codex": {
        "config": ".codex/config.toml",
        "format": "toml",
        "bridges": (
            (
                "templates/clients/codex/skills/use-cartopian/SKILL.md",
                ".codex/skills/use-cartopian/SKILL.md",
            ),
        ),
        "restart": "restart-client",
    },
    "gemini": {
        "config": ".gemini/settings.json",
        "format": "json",
        "bridges": (
            (
                "templates/clients/gemini/use-cartopian.toml",
                ".gemini/commands/use-cartopian.toml",
            ),
        ),
        "restart": "restart-client",
    },
    "devin": {
        "config": ".config/devin/config.json",
        "config_windows": "devin/config.json",
        "format": "json",
        "bridges": (
            (
                "templates/clients/devin/skills/use-cartopian/SKILL.md",
                ".config/devin/skills/use-cartopian/SKILL.md",
            ),
        ),
        "restart": "restart-client",
    },
    "windsurf": {
        "config": ".codeium/windsurf/mcp_config.json",
        "config_windows": "Windsurf/mcp_config.json",
        "format": "json",
        "bridges": (
            (
                "templates/clients/windsurf/use-cartopian.md",
                ".codeium/windsurf/workflows/use-cartopian.md",
            ),
        ),
        "bridges_windows": (
            (
                "templates/clients/windsurf/use-cartopian.md",
                "Windsurf/workflows/use-cartopian.md",
            ),
        ),
        "restart": "restart-client",
    },
    "claude-desktop": {
        "config": "Library/Application Support/Claude/claude_desktop_config.json",
        "config_windows": "Claude/claude_desktop_config.json",
        "format": "json",
        "bridges": (),
        "restart": "restart-client",
    },
    "cursor": {
        "config": ".cursor/mcp.json",
        "format": "json",
        "bridges": (),
        "restart": "restart-client",
    },
}

_SURFACE_ROWS = {
    "core-files": CORE_TARGETS,
    "mcp-server-files": MCP_TARGETS,
    "wrappers": WRAPPER_TARGETS,
    "verification-content": VERIFICATION_TARGETS,
}
_OPTIONAL_SURFACES = (
    "bridges",
    "client-registrations",
    "client-configuration",
)
_SHARED_REGISTRATION_SURFACES = (
    "client-registrations",
    "client-configuration",
)
_CHOICE_MAP = {
    "accept": "authorized",
    "decline": "declined",
    "defer": "deferred",
}
_TRANSIENT_NAMES = frozenset((".DS_Store", "__pycache__"))
_MAX_PRIOR_STATE_BYTES = 2 * 1024 * 1024


class WorkflowRefusal(ValueError):
    """Fail-closed validation or apply error."""


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_roots(source_root: Path, install_root: Path) -> Tuple[Path, Path]:
    source = source_root.expanduser().resolve()
    install = install_root.expanduser().resolve()
    if not (source / "bin" / "cartopian").is_file():
        raise WorkflowRefusal("source identity is not a Cartopian source tree")
    missing = [
        source_rel
        for _target_rel, source_rel in TOOL_SHIPPED
        if not (source / source_rel).exists()
    ]
    if missing:
        raise WorkflowRefusal(
            "source identity is incomplete: " + ", ".join(sorted(missing))
        )
    if install == Path(install.anchor) or install == Path.home().resolve():
        raise WorkflowRefusal("install destination is an unsafe broad path")
    if install == source or _is_relative_to(install, source):
        raise WorkflowRefusal(
            "install destination cannot be the source tree or a child of it"
        )
    return source, install


def _validate_clients(clients: Iterable[str]) -> Tuple[str, ...]:
    requested = tuple(dict.fromkeys(str(value) for value in clients))
    unknown = sorted(set(requested) - set(SUPPORTED_CLIENTS))
    if unknown:
        raise WorkflowRefusal(
            "unsupported client identifier(s): " + ", ".join(unknown)
        )
    return tuple(client for client in SUPPORTED_CLIENTS if client in requested)


def _validate_decisions(decisions: Mapping[str, str]) -> Dict[str, str]:
    normalized = {str(key): str(value) for key, value in decisions.items()}
    unknown_surfaces = sorted(set(normalized) - set(_OPTIONAL_SURFACES))
    if unknown_surfaces:
        raise WorkflowRefusal(
            "decisions target unsupported or non-optional surfaces: "
            + ", ".join(unknown_surfaces)
        )
    unknown_values = sorted(set(normalized.values()) - set(_CHOICE_MAP))
    if unknown_values:
        raise WorkflowRefusal(
            "unsupported repair disposition(s): " + ", ".join(unknown_values)
        )
    shared = {
        normalized[surface]
        for surface in _SHARED_REGISTRATION_SURFACES
        if surface in normalized
    }
    if len(shared) > 1:
        raise WorkflowRefusal(
            "client registration and client configuration share one repair "
            "adapter and cannot receive contradictory dispositions"
        )
    if shared:
        disposition = next(iter(shared))
        for surface in _SHARED_REGISTRATION_SURFACES:
            normalized[surface] = disposition
    return normalized


def _iter_files(path: Path) -> Iterable[Tuple[str, bytes]]:
    if path.is_symlink():
        yield "@symlink", os.readlink(path).encode("utf-8")
        return
    if path.is_file():
        yield "@file", path.read_bytes()
        return
    if not path.is_dir():
        return
    for child in sorted(path.rglob("*"), key=lambda item: item.as_posix()):
        relative = child.relative_to(path)
        if any(part in _TRANSIENT_NAMES for part in relative.parts):
            continue
        if child.suffix in (".pyc", ".pyo"):
            continue
        if child.is_symlink():
            yield relative.as_posix() + "@symlink", os.readlink(child).encode(
                "utf-8"
            )
        elif child.is_file():
            yield relative.as_posix(), child.read_bytes()


def _digest_entries(entries: Iterable[Tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    found = False
    for name, payload in entries:
        found = True
        encoded = name.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return "sha256:" + digest.hexdigest() if found else "absent"


def _digest_path(path: Path) -> str:
    return _digest_entries(_iter_files(path))


def _source_for_target(source_root: Path, target_rel: str) -> Path:
    for target, source in TOOL_SHIPPED:
        if target == target_rel:
            return source_root / source
    # Verification content is a subpath of the protocol row.
    return source_root / target_rel


def _surface_digest(
    root: Path,
    target_rels: Sequence[str],
    *,
    source_root: Optional[Path] = None,
) -> str:
    entries: List[Tuple[str, bytes]] = []
    for target_rel in target_rels:
        path = (
            _source_for_target(source_root, target_rel)
            if source_root is not None
            else root / target_rel
        )
        for nested, payload in _iter_files(path):
            entries.append((f"{target_rel}/{nested}", payload))
    return _digest_entries(entries)


def _observed_surface_digest(
    source_root: Path,
    install_root: Path,
    target_rels: Sequence[str],
    *,
    mode: str,
) -> str:
    entries: List[Tuple[str, bytes]] = []
    for target_rel in target_rels:
        target = install_root / target_rel
        source = _source_for_target(source_root, target_rel)
        use_source = False
        if (
            mode == "symlink"
            and target_rel not in COPY_ALWAYS
            and target.is_symlink()
        ):
            try:
                use_source = Path(os.readlink(target)) == source
            except OSError:
                use_source = False
        observed = source if use_source else target
        for nested, payload in _iter_files(observed):
            entries.append((f"{target_rel}/{nested}", payload))
    return _digest_entries(entries)


def _materialization_identity(
    install_root: Path,
    target_rels: Sequence[str],
    *,
    mode: str,
    desired: bool,
) -> str:
    entries: List[Tuple[str, bytes]] = []
    for target_rel in target_rels:
        expected = "copy" if target_rel in COPY_ALWAYS else mode
        if desired:
            materialization = expected
        else:
            target = install_root / target_rel
            path = target
            symlinked = False
            while _is_relative_to(path, install_root):
                if path.is_symlink():
                    symlinked = True
                    break
                if path == install_root:
                    break
                path = path.parent
            if symlinked:
                materialization = "symlink"
            elif target.exists():
                materialization = "copy"
            else:
                materialization = "absent"
        entries.append((target_rel, materialization.encode("utf-8")))
    return _digest_entries(entries)


def _source_identity(source_root: Path) -> str:
    return _surface_digest(
        source_root,
        tuple(target for target, _source in TOOL_SHIPPED),
        source_root=source_root,
    )


def _expected_mcp_command(install_root: Path) -> str:
    name = "cartopian-mcp.cmd" if os.name == "nt" else "cartopian-mcp"
    return str(install_root / "bin" / name)


def _appdata_root(client_home: Path) -> Path:
    raw = os.environ.get("APPDATA")
    return Path(raw).expanduser().resolve() if raw else client_home / "AppData/Roaming"


def _client_config_path(client: str, client_home: Path) -> Path:
    descriptor = _CLIENTS[client]
    if os.name == "nt" and "config_windows" in descriptor:
        return _appdata_root(client_home) / descriptor["config_windows"]
    return client_home / descriptor["config"]


def _client_bridge_rows(
    client: str, client_home: Path
) -> Tuple[Tuple[str, Path], ...]:
    descriptor = _CLIENTS[client]
    if os.name == "nt" and "bridges_windows" in descriptor:
        return tuple(
            (source, _appdata_root(client_home) / destination)
            for source, destination in descriptor["bridges_windows"]
        )
    if os.name == "nt" and client == "devin":
        return tuple(
            (source, _appdata_root(client_home) / destination.removeprefix(".config/"))
            for source, destination in descriptor["bridges"]
        )
    return tuple(
        (source, client_home / destination)
        for source, destination in descriptor["bridges"]
    )


def _json_registration(path: Path, expected: str) -> Tuple[str, str]:
    if not path.exists():
        return "missing", "absent"
    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
    except (OSError, json.JSONDecodeError):
        return "malformed", _digest_path(path)
    if not isinstance(data, dict):
        return "malformed", _digest_path(path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or "cartopian" not in servers:
        return "missing", "absent"
    entry = servers.get("cartopian")
    command = entry.get("command") if isinstance(entry, dict) else None
    if command == expected:
        return "current", "expected-command"
    fingerprint = hashlib.sha256(
        json.dumps(
            entry, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
    ).hexdigest()
    return "dirty", f"configuration-fingerprint:{fingerprint}"


def _toml_registration(path: Path, expected: str) -> Tuple[str, str]:
    if not path.exists():
        return "missing", "absent"
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError):
        return "malformed", _digest_path(path)
    servers = data.get("mcp_servers")
    entry = servers.get("cartopian") if isinstance(servers, dict) else None
    if not isinstance(entry, dict):
        return "missing", "absent"
    if entry.get("command") == expected:
        return "current", "expected-command"
    fingerprint = hashlib.sha256(
        json.dumps(
            entry, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
    ).hexdigest()
    return "dirty", f"configuration-fingerprint:{fingerprint}"


def _registration_observations(
    clients: Sequence[str], client_home: Path, install_root: Path
) -> Dict[str, Dict[str, str]]:
    expected = _expected_mcp_command(install_root)
    observations: Dict[str, Dict[str, str]] = {}
    for client in clients:
        descriptor = _CLIENTS[client]
        path = _client_config_path(client, client_home)
        if descriptor["format"] == "toml":
            state, identity = _toml_registration(path, expected)
        else:
            state, identity = _json_registration(path, expected)
        observations[client] = {
            "state": state,
            "identity": identity,
            "path_class": f"{client}-user-configuration",
        }
    return observations


def _bridge_observations(
    clients: Sequence[str], source_root: Path, client_home: Path
) -> Dict[str, Dict[str, Any]]:
    observations: Dict[str, Dict[str, Any]] = {}
    for client in clients:
        rows = _client_bridge_rows(client, client_home)
        if not rows:
            continue
        desired_entries: List[Tuple[str, bytes]] = []
        observed_entries: List[Tuple[str, bytes]] = []
        missing = False
        for source_rel, destination in rows:
            desired_entries.extend(
                (source_rel + "/" + name, payload)
                for name, payload in _iter_files(source_root / source_rel)
            )
            if not destination.exists():
                missing = True
            observed_entries.extend(
                (source_rel + "/" + name, payload)
                for name, payload in _iter_files(destination)
            )
        desired = _digest_entries(desired_entries)
        observed = _digest_entries(observed_entries)
        observations[client] = {
            "state": (
                "missing" if missing else ("current" if desired == observed else "dirty")
            ),
            "desired": desired,
            "observed": observed,
            "path_class": f"{client}-user-bridge",
        }
    return observations


def _aggregate_optional(
    kind: str,
    observations: Mapping[str, Mapping[str, Any]],
    desired_identity: str,
) -> Dict[str, Any]:
    if not observations:
        return {
            "kind": kind,
            "locator": f"supported-clients:{kind}",
            "desired_identity": "not-applicable",
            "observed_identity": "not-applicable",
            "state": "not-applicable",
            "affected": False,
            "required": False,
        }
    states = [str(item["state"]) for item in observations.values()]
    affected = any(state != "current" for state in states)
    if "malformed" in states:
        state = "malformed"
    elif "dirty" in states:
        state = "dirty"
    elif "missing" in states:
        state = "missing"
    else:
        state = "current"
    observed = _digest_entries(
        (
            client,
            str(
                observations[client].get(
                    "observed",
                    observations[client].get("identity", "unknown"),
                )
            ).encode("utf-8"),
        )
        for client in sorted(observations)
    )
    return {
        "kind": kind,
        "locator": f"supported-clients:{kind}",
        "desired_identity": desired_identity,
        "observed_identity": desired_identity if not affected else observed,
        "state": state,
        "affected": affected,
        "required": False,
    }


def _release_version(source_root: Path) -> Optional[str]:
    for name in ("RELEASE_VERSION", "VERSION"):
        path = source_root / name
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return None


def _target_schema(source_root: Path) -> Optional[str]:
    try:
        content = (source_root / "protocol" / "CHANGELOG.md").read_text(
            encoding="utf-8"
        )
    except OSError:
        return None
    match = re.search(r"^### (v[0-9][^\s]*)\s", content, flags=re.MULTILINE)
    return match.group(1) if match else None


def _version_key(value: str) -> Tuple[int, ...]:
    match = re.fullmatch(r"v(\d+)(?:\.(\d+))?(?:\.(\d+))?", value)
    if not match:
        return ()
    return tuple(int(part or 0) for part in match.groups())


def _migration_offers(
    install_root: Path, source_root: Path
) -> Tuple[List[Dict[str, Any]], str]:
    target = _target_schema(source_root)
    registry = install_root / "projects.json"
    if target is None or not registry.exists():
        return [], "unknown" if target is None else "current"
    try:
        entries = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], "unknown"
    if not isinstance(entries, list):
        return [], "unknown"
    offers: List[Dict[str, Any]] = []
    aggregate = "current"
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("path"):
            continue
        identity = str(entry.get("id") or "unidentified-project")
        config = Path(str(entry["path"])) / "cartopian.toml"
        current: Optional[str] = None
        try:
            with config.open("rb") as stream:
                data = tomllib.load(stream)
            project = data.get("project")
            if isinstance(project, dict):
                raw = project.get("project_schema_version")
                current = str(raw) if raw is not None else None
        except (OSError, tomllib.TOMLDecodeError):
            aggregate = "malformed"
        if current == target:
            continue
        current_key = _version_key(current or "")
        target_key = _version_key(target)
        if not current_key or not target_key:
            applicability = "unknown"
            aggregate = "unknown"
        elif current_key > target_key:
            applicability = "unsupported-newer"
            aggregate = "unsupported"
        else:
            applicability = "applicable"
            if aggregate == "current":
                aggregate = "older"
        offers.append(
            {
                "project_identity": identity,
                "current_schema": current or "unset",
                "target_schema": target,
                "applicability": applicability,
                "choice_state": "offered",
                "result": "not-run",
                "supported_workflow": "migrate-project",
            }
        )
    offers.sort(key=lambda item: item["project_identity"])
    return offers, aggregate


def _version_records(
    source_root: Path,
    install_root: Path,
    source_identity: str,
    migration_state: str,
    *,
    release_ref: Optional[str] = None,
) -> List[Dict[str, Any]]:
    authorities = identity_contract()
    release = release_ref or _release_version(source_root)
    target_schema = _target_schema(source_root)
    if migration_state == "current":
        project_schema_value = target_schema
    elif migration_state == "older":
        project_schema_value = f"mixed-older-than:{target_schema or 'unknown'}"
    elif migration_state == "unsupported":
        project_schema_value = f"unsupported-newer-than:{target_schema or 'unknown'}"
    elif migration_state == "malformed":
        project_schema_value = "malformed"
    else:
        project_schema_value = None
    installed_exists = any((install_root / target).exists() for target in CORE_TARGETS)
    installed_identity = _surface_digest(install_root, CORE_TARGETS)
    return [
        {
            "kind": "release_version",
            "value": release,
            "state": "known" if release else "unknown",
            "authority": authorities["release_version"]["authority"],
            "verification": "verified" if release else "unknown",
        },
        {
            "kind": "installed_content",
            "value": installed_identity if installed_exists else None,
            "state": "verified" if installed_exists else "unknown",
            "authority": authorities["installed_content"]["authority"],
            "verification": "verified" if installed_exists else "unknown",
        },
        {
            "kind": "project_schema_version",
            "value": project_schema_value,
            "state": migration_state,
            "authority": authorities["project_schema_version"]["authority"],
            "verification": "verified" if migration_state != "unknown" else "unknown",
        },
        {
            "kind": "running_server",
            "value": None,
            "state": "unknown",
            "authority": authorities["running_server"]["authority"],
            "verification": "unknown",
        },
        {
            "kind": "mcp_protocol_version",
            "value": MCP_PROTOCOL_VERSION,
            "state": "supported",
            "authority": authorities["mcp_protocol_version"]["authority"],
            "verification": "verified",
        },
    ]


def _required_surface(
    kind: str, source_root: Path, install_root: Path, *, mode: str
) -> Dict[str, Any]:
    targets = _SURFACE_ROWS[kind]
    desired_content = _surface_digest(
        source_root, targets, source_root=source_root
    )
    observed_content = _observed_surface_digest(
        source_root, install_root, targets, mode=mode
    )
    desired_materialization = _materialization_identity(
        install_root, targets, mode=mode, desired=True
    )
    observed_materialization = _materialization_identity(
        install_root, targets, mode=mode, desired=False
    )
    desired = f"{desired_content};materialization={desired_materialization}"
    observed = (
        f"{observed_content};materialization={observed_materialization}"
    )
    materialization_mismatch = (
        desired_materialization != observed_materialization
    )
    affected = desired != observed
    return {
        "kind": kind,
        "locator": f"installed:{kind}",
        "desired_identity": desired,
        "observed_identity": observed,
        "state": "pending" if affected else "current",
        "affected": affected,
        "required": True,
        "materialization_mismatch": materialization_mismatch,
    }


def _choice(
    surface: str,
    decision: Optional[str],
    *,
    fresh_authorized: bool,
    carried_decline: bool,
    decision_context: Mapping[str, Any],
) -> Dict[str, Any]:
    if decision is not None:
        state = _CHOICE_MAP[decision]
        provenance = "bounded-caller-disposition"
    elif fresh_authorized:
        state = "authorized"
        provenance = "bounded-fresh-install-client-selection"
    elif carried_decline:
        state = "declined"
        provenance = "prior-run-matched-decline"
    else:
        state = "offered"
        provenance = "coordinated-workflow-detection"
    action = {
        "bridges": "repair",
        "client-registrations": "register",
        "client-configuration": "reconfigure",
    }[surface]
    return {
        "id": f"{surface}-{action}",
        "surface": surface,
        "offered_action": action,
        "state": state,
        "provenance": provenance,
        "decision_context": copy.deepcopy(dict(decision_context)),
    }


def _decision_context(
    *,
    surface: Mapping[str, Any],
    source_identity: str,
    clients: Sequence[str],
    mode: str,
) -> Dict[str, Any]:
    return {
        "context_schema": "coordinated-repair-v1",
        "surface": str(surface["kind"]),
        "desired_identity": str(surface["desired_identity"]),
        "observed_identity": str(surface["observed_identity"]),
        "clients": list(clients),
        "source": {
            "kind": "local-checkout",
            "value": source_identity,
            "authority": "maintainer-source-content",
        },
        "materialization_mode": mode,
    }


def _prior_declined_contexts(
    install_root: Path,
) -> Dict[str, Mapping[str, Any]]:
    path = install_root / STATE_FILE
    try:
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size > _MAX_PRIOR_STATE_BYTES
        ):
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if (
        not isinstance(raw, Mapping)
        or raw.get("schema_identity") != SCHEMA_IDENTITY
        or raw.get("record_schema_version") != RECORD_SCHEMA_VERSION
        or raw.get("state")
        not in ("complete", "repair-offered", "blocked", "failed")
    ):
        return {}
    evaluated = evaluate_record(raw)
    if any(
        item.get("severity") == "error"
        and item.get("code") not in ("apply-refused", "apply-failed")
        for item in evaluated.get("diagnostics", [])
        if isinstance(item, Mapping)
    ):
        return {}
    contexts: Dict[str, Mapping[str, Any]] = {}
    for item in evaluated.get("choices", []):
        if (
            isinstance(item, Mapping)
            and item.get("state") == "declined"
            and isinstance(item.get("decision_context"), Mapping)
        ):
            contexts[str(item.get("surface"))] = item["decision_context"]
    return contexts


def _plan_actions(
    surfaces: Sequence[Mapping[str, Any]],
    choices: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    choice_by_surface = {item["surface"]: item for item in choices}
    actions: List[Dict[str, Any]] = []
    for kind in SURFACE_KINDS:
        surface = next(item for item in surfaces if item["kind"] == kind)
        choice = choice_by_surface.get(kind)
        if kind == "project-schema-migration-offers":
            action = "offer-migration" if surface["affected"] else "none"
            authorization = "separate-project-approval"
            restart = "none"
        elif kind in _OPTIONAL_SURFACES and surface["affected"]:
            action = choice["offered_action"] if choice else "repair"
            authorization = choice["state"] if choice else "offered"
            restart = "client-specific"
        elif surface["affected"]:
            action = (
                "convert-materialization"
                if surface.get("materialization_mismatch")
                else "install"
            )
            authorization = "required"
            restart = (
                "reconnect-mcp"
                if kind == "mcp-server-files"
                else ("reopen-shell" if kind == "wrappers" else "none")
            )
        elif kind == "project-schema-migration-offers":
            action = "none"
            authorization = "separate-project-approval"
            restart = "none"
        else:
            action = "verify"
            authorization = "required" if surface["required"] else "not-required"
            restart = "none"
        actions.append(
            {
                "surface": kind,
                "action": action,
                "reason": surface["state"],
                "authorization": authorization,
                "expected_verification": "authoritative-content-identity",
                "restart_impact": restart,
            }
        )
    return actions


def plan_workflow(
    *,
    source_root: Path,
    install_root: Path,
    operation: str,
    mode: str = "copy",
    client_home: Optional[Path] = None,
    clients: Sequence[str] = (),
    decisions: Optional[Mapping[str, str]] = None,
    release_ref: Optional[str] = None,
) -> "OrderedDict[str, Any]":
    """Inventory all supported surfaces and return a deterministic plan.

    This function performs no writes.  ``client_home`` is an adapter context
    used by isolated tests and embedded installers; it is not exposed as a
    public destination-selection option.
    """
    if operation not in ("fresh-install", "update", "repair", "verification"):
        raise WorkflowRefusal(f"unsupported operation: {operation}")
    if mode not in ("copy", "symlink"):
        raise WorkflowRefusal(f"unsupported materialization mode: {mode}")
    source, install = _validate_roots(source_root, install_root)
    selected = _validate_clients(clients)
    dispositions = _validate_decisions(decisions or {})
    home = (client_home or Path.home()).expanduser().resolve()

    # When no client was explicitly selected, detect only clients with an
    # existing closed registration or bridge location.
    if not selected:
        detected = []
        for client in SUPPORTED_CLIENTS:
            descriptor = _CLIENTS[client]
            config_exists = _client_config_path(client, home).exists()
            bridge_exists = any(
                destination.exists()
                for _source, destination in _client_bridge_rows(client, home)
            )
            if config_exists or bridge_exists:
                detected.append(client)
        selected = tuple(detected)

    source_identity = _source_identity(source)
    prior_declines = (
        {}
        if operation == "fresh-install"
        else _prior_declined_contexts(install)
    )
    registration_facts = _registration_observations(selected, home, install)
    bridge_facts = _bridge_observations(selected, source, home)
    registration_desired = _digest_entries(
        (
            client,
            (client + ":cartopian-mcp").encode("utf-8"),
        )
        for client in selected
    )
    bridge_desired = _digest_entries(
        (client, str(bridge_facts[client]["desired"]).encode("utf-8"))
        for client in sorted(bridge_facts)
    )

    surfaces: List[Dict[str, Any]] = []
    for kind in ("core-files", "mcp-server-files", "wrappers"):
        surfaces.append(
            _required_surface(kind, source, install, mode=mode)
        )
    surfaces.append(
        _aggregate_optional("bridges", bridge_facts, bridge_desired)
    )
    registration_surface = _aggregate_optional(
        "client-registrations", registration_facts, registration_desired
    )
    surfaces.append(registration_surface)
    surfaces.append(
        {
            **copy.deepcopy(registration_surface),
            "kind": "client-configuration",
            "locator": "supported-clients:client-configuration",
        }
    )
    surfaces.append(
        _required_surface(
            "verification-content", source, install, mode=mode
        )
    )

    migrations, migration_state = _migration_offers(install, source)
    target_schema = _target_schema(source) or "unknown"
    surfaces.append(
        {
            "kind": "project-schema-migration-offers",
            "locator": "registered-projects:schema",
            "desired_identity": target_schema,
            "observed_identity": (
                target_schema if not migrations else f"{len(migrations)}-offer(s)"
            ),
            "state": "offered" if migrations else "not-applicable",
            "affected": bool(migrations),
            "required": False,
        }
    )

    choices: List[Dict[str, str]] = []
    for surface in surfaces:
        kind = surface["kind"]
        if kind not in _OPTIONAL_SURFACES or not surface["affected"]:
            continue
        context = _decision_context(
            surface=surface,
            source_identity=source_identity,
            clients=selected,
            mode=mode,
        )
        choice = _choice(
            kind,
            dispositions.get(kind),
            fresh_authorized=operation == "fresh-install" and bool(clients),
            carried_decline=(
                dispositions.get(kind) is None
                and prior_declines.get(kind) == context
            ),
            decision_context=context,
        )
        choices.append(choice)
        if choice["state"] in ("declined", "deferred"):
            surface["state"] = choice["state"]
        elif choice["state"] == "offered":
            surface["state"] = "offered"

    marker_payload = json.dumps(
        {
            "operation": operation,
            "source": source_identity,
            "surfaces": [
                (item["kind"], item["observed_identity"], item["state"])
                for item in surfaces
            ],
            "choices": [
                (item["surface"], item["state"]) for item in choices
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    run_marker = "run:" + hashlib.sha256(marker_payload).hexdigest()[:20]
    record = build_record(
        operation=operation,
        run_marker=run_marker,
        source={
            "kind": "local-checkout",
            "value": source_identity,
            "state": "known",
            "authority": "maintainer-source-content",
        },
        versions=_version_records(
            source,
            install,
            source_identity,
            migration_state,
            release_ref=release_ref,
        ),
        surfaces=surfaces,
        state="planned",
        choices=choices,
        migrations=migrations,
        internal={
            "source_root": str(source),
            "install_root": str(install),
            "client_home": str(home),
            "clients": list(selected),
            "mode": mode,
            "release_ref": release_ref,
            "affected_surface_plan": _plan_actions(surfaces, choices),
            "registration_observations": registration_facts,
            "bridge_observations": bridge_facts,
        },
    )
    return record


def _ignore_transients(_directory: str, names: List[str]) -> List[str]:
    return [
        name
        for name in names
        if name in _TRANSIENT_NAMES or name.endswith((".pyc", ".pyo"))
    ]


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _replace_tool_path(
    source: Path, target: Path, *, mode: str, force_copy: bool = False
) -> bool:
    effective_mode = "copy" if force_copy else mode
    desired = _digest_path(source)
    observed = _digest_path(target)
    if effective_mode == "copy" and desired == observed and not target.is_symlink():
        return False
    if effective_mode == "symlink" and target.is_symlink():
        try:
            if Path(os.readlink(target)) == source:
                return False
        except OSError:
            pass
    target.parent.mkdir(parents=True, exist_ok=True)
    stage_parent = Path(
        tempfile.mkdtemp(prefix=".cartopian-stage-", dir=str(target.parent))
    )
    staged = stage_parent / "payload"
    backup = target.parent / f".{target.name}.cartopian-backup"
    try:
        if effective_mode == "symlink":
            os.symlink(str(source), str(staged), target_is_directory=source.is_dir())
        elif source.is_dir():
            shutil.copytree(
                source, staged, symlinks=False, ignore=_ignore_transients
            )
        else:
            shutil.copy2(source, staged)
        if backup.exists() or backup.is_symlink():
            raise WorkflowRefusal(
                f"recovery boundary already exists for {target.name}; inspect it before retry"
            )
        had_target = target.exists() or target.is_symlink()
        if had_target:
            os.replace(target, backup)
        try:
            os.replace(staged, target)
        except BaseException:
            if had_target and backup.exists():
                os.replace(backup, target)
            raise
        if backup.exists() or backup.is_symlink():
            _remove_path(backup)
        return True
    finally:
        if stage_parent.exists():
            shutil.rmtree(stage_parent, ignore_errors=True)


def _seed_operator_files(source_root: Path, install_root: Path) -> None:
    config = install_root / "cartopian.toml"
    if not config.exists():
        _replace_tool_path(
            source_root / "templates" / "global.cartopian.toml",
            config,
            mode="copy",
        )
    registry = install_root / "projects.json"
    if not registry.exists():
        _atomic_write_text(registry, "[]\n")


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temp = Path(raw)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists() and not path.is_symlink():
            os.chmod(temp, stat.S_IMODE(path.stat().st_mode))
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _validate_operator_config_target(path: Path) -> None:
    if path.is_symlink():
        raise WorkflowRefusal(
            "client configuration is a symlink and was preserved"
        )
    if not path.exists():
        return
    if not path.is_file():
        raise WorkflowRefusal(
            "client configuration is not a regular file and was preserved"
        )
    if path.stat().st_nlink != 1:
        raise WorkflowRefusal(
            "client configuration has multiple hard links and was preserved"
        )


def _merge_json_registration(path: Path, command: str) -> None:
    _validate_operator_config_target(path)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkflowRefusal(
                f"client configuration is malformed and was preserved: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise WorkflowRefusal(
                "client configuration is not an object and was preserved"
            )
    else:
        data = {}
    servers = data.get("mcpServers")
    if servers is None:
        servers = {}
        data["mcpServers"] = servers
    if not isinstance(servers, dict):
        raise WorkflowRefusal(
            "client mcpServers value is not an object and was preserved"
        )
    servers["cartopian"] = {"command": command}
    _atomic_write_text(
        path,
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )


def _merge_toml_registration(path: Path, command: str) -> None:
    _validate_operator_config_target(path)
    content = ""
    if path.exists():
        try:
            content = path.read_text(encoding="utf-8")
            tomllib.loads(content)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise WorkflowRefusal(
                f"client configuration is malformed and was preserved: {exc}"
            ) from exc
    escaped = command.replace("\\", "\\\\").replace('"', '\\"')
    section_pattern = re.compile(
        r"(?ms)^\[mcp_servers\.cartopian\]\s*\n(?P<body>.*?)(?=^\[|\Z)"
    )
    match = section_pattern.search(content)
    if match is None:
        separator = "" if not content or content.endswith("\n\n") else "\n"
        updated = (
            content
            + separator
            + "[mcp_servers.cartopian]\n"
            + f'command = "{escaped}"\n'
        )
    else:
        body = match.group("body")
        if re.search(r"(?m)^command\s*=", body):
            new_body = re.sub(
                r'(?m)^command\s*=.*$',
                f'command = "{escaped}"',
                body,
                count=1,
            )
        else:
            new_body = f'command = "{escaped}"\n' + body
        updated = content[: match.start("body")] + new_body + content[match.end("body") :]
    tomllib.loads(updated)
    _atomic_write_text(path, updated)


def _apply_registrations(
    clients: Sequence[str], client_home: Path, install_root: Path
) -> None:
    command = _expected_mcp_command(install_root)
    for client in clients:
        descriptor = _CLIENTS[client]
        path = _client_config_path(client, client_home)
        try:
            if descriptor["format"] == "toml":
                _merge_toml_registration(path, command)
            else:
                _merge_json_registration(path, command)
        except WorkflowRefusal as exc:
            raise WorkflowRefusal(
                f"{client}: {exc}"
            ) from exc


def _apply_bridges(
    clients: Sequence[str], source_root: Path, client_home: Path
) -> None:
    for client in clients:
        for source_rel, destination in _client_bridge_rows(client, client_home):
            _replace_tool_path(
                source_root / source_rel,
                destination,
                mode="copy",
            )


def _write_state(record: Mapping[str, Any]) -> None:
    internal = record.get("internal")
    if not isinstance(internal, Mapping):
        return
    install_root = Path(str(internal["install_root"]))
    _atomic_write_text(
        install_root / STATE_FILE,
        json.dumps(
            stable_projection(record),
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
    )


def _blocked_apply_record(
    plan: Mapping[str, Any],
    *,
    refused_surface: str,
    attempted_action: str,
    recovery: str,
    recovery_artifact: str,
    failure_state: str = "blocked",
    mutation_status: str = "refused-preserved",
) -> "OrderedDict[str, Any]":
    observed = verify_workflow(plan)
    updated = copy.deepcopy(dict(observed))
    blocked_surfaces = {refused_surface}
    if refused_surface in _SHARED_REGISTRATION_SURFACES:
        blocked_surfaces.update(_SHARED_REGISTRATION_SURFACES)
    for surface in updated["surfaces"]:
        if surface["kind"] in blocked_surfaces:
            surface["state"] = failure_state
            surface["affected"] = True
    for checkpoint in updated["checkpoints"]:
        if checkpoint["surface"] not in blocked_surfaces:
            continue
        checkpoint["status"] = failure_state
        checkpoint["verification"] = "failed"
        checkpoint["retry_safety"] = "inspect-before-retry"
        checkpoint["evidence"]["verification"] = "failed"
        checkpoint["evidence"]["observed_state"] = failure_state
        if checkpoint["surface"] == refused_surface:
            checkpoint["attempted_action"] = attempted_action
            checkpoint["mutation_status"] = mutation_status
            checkpoint["recovery"] = recovery
            checkpoint["recovery_artifact"] = recovery_artifact
    updated["state"] = failure_state
    return evaluate_record(updated)


def apply_workflow(plan: Mapping[str, Any]) -> "OrderedDict[str, Any]":
    """Apply authorized plan work, then verify every surface."""
    internal = plan.get("internal")
    if not isinstance(internal, Mapping):
        raise WorkflowRefusal("workflow plan has no trusted adapter context")
    source_root, install_root = _validate_roots(
        Path(str(internal["source_root"])),
        Path(str(internal["install_root"])),
    )
    mode = str(internal.get("mode"))
    clients = _validate_clients(internal.get("clients", ()))
    client_home = Path(str(internal["client_home"])).resolve()
    choices = {
        item["surface"]: item
        for item in plan.get("choices", [])
        if isinstance(item, Mapping)
    }
    surfaces = {
        item["kind"]: item
        for item in plan.get("surfaces", [])
        if isinstance(item, Mapping)
    }

    refused_surface = "core-files"
    attempted_action = "install-tool-owned-content"
    recovery = (
        "inspect the refused tool-owned surface and its recoverable replacement "
        "boundary before retry"
    )
    recovery_artifact = "tool-owned-content-preserved-or-recoverable"
    try:
        install_root.mkdir(parents=True, exist_ok=True)
        for surface_kind in ("core-files", "mcp-server-files", "wrappers"):
            surface = surfaces[surface_kind]
            if not surface.get("affected"):
                continue
            refused_surface = surface_kind
            attempted_action = (
                "convert-materialization"
                if surface.get("materialization_mismatch")
                else "install-tool-owned-content"
            )
            recovery_artifact = f"installed:{surface_kind}:replacement-boundary"
            for target_rel in _SURFACE_ROWS[surface_kind]:
                source = _source_for_target(source_root, target_rel)
                _replace_tool_path(
                    source,
                    install_root / target_rel,
                    mode=mode,
                    force_copy=target_rel in COPY_ALWAYS,
                )
        refused_surface = "core-files"
        attempted_action = "seed-operator-files"
        recovery = (
            "inspect the operator-owned seed files; existing operator content "
            "was not overwritten"
        )
        recovery_artifact = "operator-files:preserved"
        _seed_operator_files(source_root, install_root)

        bridge_choice = choices.get("bridges", {})
        if bridge_choice.get("state") == "authorized":
            refused_surface = "bridges"
            attempted_action = "repair"
            recovery = (
                "inspect the derived client bridge replacement boundary before retry"
            )
            recovery_artifact = "supported-client-bridge:replacement-boundary"
            _apply_bridges(clients, source_root, client_home)

        registration_authorized = any(
            choices.get(surface, {}).get("state") == "authorized"
            for surface in _SHARED_REGISTRATION_SURFACES
        )
        if registration_authorized:
            refused_surface = "client-configuration"
            attempted_action = "reconfigure-registration"
            recovery = (
                "correct or replace the malformed or unsafe client configuration "
                "under operator authority, then retry the bounded repair"
            )
            recovery_artifact = "operator-client-configuration:preserved"
            _apply_registrations(clients, client_home, install_root)
    except (WorkflowRefusal, OSError) as exc:
        os_failure = isinstance(exc, OSError)
        failure_recovery = recovery
        mutation_status = "refused-preserved"
        failure_state = "blocked"
        if os_failure:
            failure_state = "failed"
            failure_recovery = (
                "restore operating-system access to the bounded "
                f"{refused_surface} destination, inspect its preserved or "
                "recoverable content, then retry"
            )
            mutation_status = (
                "os-error-preserved"
                if recovery_artifact.endswith(":preserved")
                else "os-error-recoverable"
            )
        try:
            blocked = _blocked_apply_record(
                plan,
                refused_surface=refused_surface,
                attempted_action=attempted_action,
                recovery=failure_recovery,
                recovery_artifact=recovery_artifact,
                failure_state=failure_state,
                mutation_status=mutation_status,
            )
            _write_state(blocked)
        except Exception:
            # Persistence is best-effort on an already-failing apply boundary.
            # The original refusal or OS error remains the truthful cause.
            pass
        raise

    result = verify_workflow(plan)
    _write_state(result)
    return result


def _verification_checkpoint(
    surface: Mapping[str, Any], *, failed: bool = False
) -> Dict[str, Any]:
    state = str(surface["state"])
    completed = state in ("current", "verified", "not-applicable")
    if failed:
        status = "failed"
        verification = "failed"
    elif completed:
        status = "completed"
        verification = "verified"
    else:
        status = "unverified"
        verification = "unverified"
    evidence = {
        "identity": str(surface["observed_identity"]),
        "kind": (
            "schema-observation"
            if surface["kind"] == "project-schema-migration-offers"
            else (
                "registration-observation"
                if surface["kind"]
                in ("bridges", "client-registrations", "client-configuration")
                else "file-digest"
            )
        ),
        "observed_identity": str(surface["observed_identity"]),
        "observed_state": state,
        "authority": "coordinated-surface-adapter",
        "verification": verification,
        "path_class": str(surface["locator"]),
    }
    return {
        "id": f"verify-{surface['kind']}",
        "phase": (
            "migration-offer"
            if surface["kind"] == "project-schema-migration-offers"
            else "verify"
        ),
        "surface": surface["kind"],
        "status": status,
        "evidence": evidence,
        "verification": verification,
        "retry_safety": (
            "idempotent" if completed and not failed else "inspect-before-retry"
        ),
    }


def verify_workflow(record: Mapping[str, Any]) -> "OrderedDict[str, Any]":
    """Re-inventory a planned/applied run and attach portable evidence."""
    internal = record.get("internal")
    if not isinstance(internal, Mapping):
        raise WorkflowRefusal("workflow record has no trusted adapter context")
    source_root, install_root = _validate_roots(
        Path(str(internal["source_root"])),
        Path(str(internal["install_root"])),
    )
    client_home = Path(str(internal["client_home"])).resolve()
    clients = _validate_clients(internal.get("clients", ()))
    mode = str(internal.get("mode"))
    choices = {
        item["surface"]: item
        for item in record.get("choices", [])
        if isinstance(item, Mapping)
    }

    verified_surfaces: List[Dict[str, Any]] = []
    failed_kinds: set[str] = set()
    for kind in ("core-files", "mcp-server-files", "wrappers"):
        surface = _required_surface(
            kind, source_root, install_root, mode=mode
        )
        if surface["affected"]:
            surface["state"] = "failed"
            failed_kinds.add(kind)
        else:
            surface["state"] = "verified"
        verified_surfaces.append(surface)

    bridge_facts = _bridge_observations(clients, source_root, client_home)
    registration_facts = _registration_observations(
        clients, client_home, install_root
    )
    registration_desired = _digest_entries(
        (client, (client + ":cartopian-mcp").encode("utf-8"))
        for client in clients
    )
    bridge_desired = _digest_entries(
        (client, str(bridge_facts[client]["desired"]).encode("utf-8"))
        for client in sorted(bridge_facts)
    )
    optional = {
        "bridges": _aggregate_optional("bridges", bridge_facts, bridge_desired),
        "client-registrations": _aggregate_optional(
            "client-registrations", registration_facts, registration_desired
        ),
    }
    optional["client-configuration"] = {
        **copy.deepcopy(optional["client-registrations"]),
        "kind": "client-configuration",
        "locator": "supported-clients:client-configuration",
    }
    for kind in _OPTIONAL_SURFACES:
        surface = optional[kind]
        choice = choices.get(kind)
        if choice and choice.get("state") in ("declined", "deferred"):
            surface["state"] = str(choice["state"])
        elif choice and choice.get("state") == "offered":
            surface["state"] = "offered"
        elif choice and choice.get("state") == "authorized":
            if surface["affected"]:
                surface["state"] = "failed"
                failed_kinds.add(kind)
            else:
                surface["state"] = "verified"
        verified_surfaces.append(surface)

    verification = _required_surface(
        "verification-content", source_root, install_root, mode=mode
    )
    if verification["affected"]:
        verification["state"] = "failed"
        failed_kinds.add("verification-content")
    else:
        verification["state"] = "verified"
    verified_surfaces.append(verification)

    migrations, migration_state = _migration_offers(install_root, source_root)
    target_schema = _target_schema(source_root) or "unknown"
    verified_surfaces.append(
        {
            "kind": "project-schema-migration-offers",
            "locator": "registered-projects:schema",
            "desired_identity": target_schema,
            "observed_identity": (
                target_schema if not migrations else f"{len(migrations)}-offer(s)"
            ),
            "state": "offered" if migrations else "not-applicable",
            "affected": bool(migrations),
            "required": False,
        }
    )
    verified_surfaces.sort(key=lambda item: SURFACE_KINDS.index(item["kind"]))
    checkpoints = [
        _verification_checkpoint(
            surface, failed=surface["kind"] in failed_kinds
        )
        for surface in verified_surfaces
    ]
    has_offer = any(
        item.get("state") == "offered" for item in record.get("choices", [])
    )
    state = "failed" if failed_kinds else ("repair-offered" if has_offer else "complete")
    updated = copy.deepcopy(dict(record))
    updated["state"] = state
    updated["surfaces"] = verified_surfaces
    updated["checkpoints"] = checkpoints
    updated["migrations"] = migrations
    updated["versions"] = _version_records(
        source_root,
        install_root,
        str(record["run"]["source"]["value"]),
        migration_state,
        release_ref=(
            str(internal["release_ref"])
            if internal.get("release_ref")
            else None
        ),
    )
    updated_internal = dict(internal)
    updated_internal["registration_observations"] = registration_facts
    updated_internal["bridge_observations"] = bridge_facts
    updated["internal"] = updated_internal
    return evaluate_record(updated)


def portable_verification_document() -> str:
    """Return release-driven verification guidance without governance IDs."""
    return (
        "# Installed-surface verification\n\n"
        "Compare the recorded desired and observed SHA-256 identities for each "
        "closed surface. A completed checkpoint requires a matching identity, "
        "a verified method, and an explicit portability class. Client "
        "registration evidence records only a closed path class and whether "
        "the fixed Cartopian MCP command matches; it never records credentials, "
        "caller-selected executables, or arbitrary destinations. A static "
        "cross-platform check is parity evidence, not native execution proof.\n"
    )
