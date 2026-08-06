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
from typing import (
    AbstractSet,
    Any,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from cli.config_schema import identity_contract
from cli.install_state import (
    SCHEMA_IDENTITY,
    SURFACE_KINDS,
    build_record,
    evaluate_record,
    stable_projection,
    supported_record_schema_version,
)
from cli.restart_state import (
    client_context_from_environment,
    evaluate_restart,
    normalize_client_context,
    restart_record,
    running_server_from_environment,
)
from cli.resume_state import (
    ProgressRefusal,
    acquire_lease,
    advance_cleanup,
    advance_completion,
    assess_resume,
    begin_progress,
    carry_preserved_evidence,
    commit_checkpoint,
    new_owner_token,
    open_boundary,
    preserve_progress,
    quarantine_progress,
    read_progress,
    recoverable_write_text,
    record_failure,
    recovery_note as resume_recovery_note,
    release_lease,
)
from cli.version_identities import (
    content_bound_restart_candidate,
    install_state_evidence,
    is_release_tag,
    restart_evidence_withheld,
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
# The closed shipped surface set an install materializes, in the order both the
# installer and the runtime digest it (``cli.version_identities``
# ``INSTALLED_CONTENT_PATHS``; parity is asserted in
# ``tests/test_install_version_projection.py``). The recorded identity must
# cover the same surfaces the runtime reports as its installed revision, or a
# change outside the narrower subset would stay "verified".
INSTALLED_TARGETS: Tuple[str, ...] = tuple(
    target for target, _source in TOOL_SHIPPED
)
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
# The MCP-affected content surface. `cli` is included because the MCP server
# dispatches every tool call into the `cli` package in-process: a release that
# changes CLI behavior changes MCP behavior, and only fresh-process proof shows
# the running server serves the newly installed content.
MCP_TARGETS = (
    "mcp_server",
    "cli",
    "bin/cartopian-mcp",
    "bin/cartopian-mcp.cmd",
)
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
# Only a migration offer may be deferred here; running a migration stays with
# the separately authorized `migrate-project` workflow, so `accept` is refused
# rather than silently reinterpreted as authorization.
_MIGRATION_SURFACE = "project-schema-migration-offers"
_MIGRATION_DISPOSITIONS = ("defer",)

# Each surface adapter declares how safely its action may be repeated and how
# much of its result can be re-observed on resume.  Resume assessment consumes
# these; it never infers repeatability from the action name.
#
# Tool-owned content is replaced through a staged, digest-verified boundary, so
# repeating it converges.  Client registration and configuration merge into
# operator-owned files whose non-Cartopian siblings cannot be fully re-derived,
# so a partial merge must be inspected rather than replayed.  A project schema
# migration is externally visible and not idempotent, so resume never replays
# it; it can only be re-offered.
_SURFACE_RETRY_PROFILES: Dict[str, Tuple[str, str]] = {
    "core-files": ("idempotent", "observable"),
    "mcp-server-files": ("idempotent", "observable"),
    "wrappers": ("idempotent", "observable"),
    "bridges": ("idempotent", "observable"),
    "client-registrations": ("inspect-before-retry", "partially-observable"),
    "client-configuration": ("inspect-before-retry", "partially-observable"),
    "verification-content": ("idempotent", "observable"),
    _MIGRATION_SURFACE: ("refuse-replay", "unobservable"),
}
_RETRY_RANK = {
    "idempotent": 0,
    "inspect-before-retry": 1,
    "refuse-replay": 2,
}

# Resume classifications whose persisted record is intact and meaningful but
# belongs to a different source, run, or installation.  Each is the last useful
# recovery evidence for something this run is not, so it is preserved verbatim
# before a new envelope may take its place.  `corrupted` and `evidence-missing`
# are handled separately: those records are unusable in themselves and follow
# the quarantine rule instead.
_PRESERVE_BEFORE_REPLACEMENT = (
    "source-mismatch",
    "run-conflict",
    "orphaned",
)

# Resume classifications this workflow has a defined disposition for once the
# lease is held: reusable, preserved before replacement, or quarantined.  A
# classification that appears only *after* the plan was computed and is not one
# of these is a fact this run cannot reconcile, so it fails closed instead of
# guessing at a disposition.
_RECONCILABLE_POST_LEASE = frozenset(
    (
        "absent",
        "compatible",
        "stale",
        "source-mismatch",
        "run-conflict",
        "orphaned",
        "corrupted",
        "evidence-missing",
    )
)
_TRANSIENT_NAMES = frozenset((".DS_Store", "__pycache__"))
_MAX_PRIOR_STATE_BYTES = 2 * 1024 * 1024


class WorkflowRefusal(ValueError):
    """Fail-closed validation or apply error."""


def surface_retry_profile(kind: str) -> Dict[str, str]:
    """Return the adapter-declared retry and observation facts for a surface."""
    if kind not in _SURFACE_RETRY_PROFILES:
        raise WorkflowRefusal(f"unknown surface kind: {kind}")
    retry_safety, observation = _SURFACE_RETRY_PROFILES[kind]
    return {
        "surface": kind,
        "retry_safety": retry_safety,
        "observation": observation,
    }


def surface_retry_profiles() -> List[Dict[str, str]]:
    """Return every surface profile in closed contract order."""
    return [surface_retry_profile(kind) for kind in SURFACE_KINDS]


def _escalate_retry(*values: str) -> str:
    return max(values, key=lambda value: _RETRY_RANK.get(value, 1))


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
    unknown_surfaces = sorted(
        set(normalized) - set(_OPTIONAL_SURFACES) - {_MIGRATION_SURFACE}
    )
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
    migration = normalized.get(_MIGRATION_SURFACE)
    if migration is not None and migration not in _MIGRATION_DISPOSITIONS:
        raise WorkflowRefusal(
            "project schema migration is separately authorized through the "
            "migrate-project workflow; only "
            + "|".join(_MIGRATION_DISPOSITIONS)
            + " is accepted here"
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
        if child.is_file():
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


def _materialization_identity(
    install_root: Path,
    target_rels: Sequence[str],
    *,
    desired: bool,
) -> str:
    entries: List[Tuple[str, bytes]] = []
    for target_rel in target_rels:
        if desired:
            materialization = "copy"
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
                # Never desired: a linked surface (e.g. a legacy install) is
                # materialization drift and must not verify as current.
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
    running_fact: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    authorities = identity_contract()
    release = release_ref or _release_version(source_root)
    # Record a release claim only for a ref the reader will honor. A branch or
    # commit ref installs fine, but it is not a release; persisting it as
    # ``known``/``verified`` puts a claim in the state file that
    # ``version_identities.release_version`` refuses to read back.
    release_claim = release if is_release_tag(release) else None
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
    running = (
        running_server_from_environment()
        if running_fact is None
        else copy.deepcopy(dict(running_fact))
    )
    running_loaded = running.get("loaded_content")
    loaded = running_loaded if isinstance(running_loaded, Mapping) else {}
    running_value = running.get("loaded_identity")
    if running_value is None:
        running_value = loaded.get("mcp_identity", loaded.get("identity"))
    running_verification = running.get("verification")
    if running_verification is None:
        running_verification = loaded.get(
            "mcp_verification", loaded.get("verification", "unknown")
        )
    return [
        {
            "kind": "release_version",
            "value": release_claim,
            "state": "known" if release_claim else "unknown",
            "authority": authorities["release_version"]["authority"],
            "verification": "verified" if release_claim else "unknown",
        },
        {
            "kind": "installed_content",
            "value": installed_identity if installed_exists else None,
            "state": "verified" if installed_exists else "unknown",
            "authority": authorities["installed_content"]["authority"],
            "verification": "verified" if installed_exists else "unknown",
            # The whole shipped surface set, so a later runtime can detect
            # drift anywhere in the content it reports as installed, and the
            # narrower MCP subset the restart projection compares.
            "installed_identity": _surface_digest(
                install_root, INSTALLED_TARGETS
            ) if installed_exists else None,
            "mcp_identity": _surface_digest(
                install_root, MCP_TARGETS
            ) if installed_exists else None,
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
            "value": running_value,
            "state": running.get("state", "unknown"),
            "authority": authorities["running_server"]["authority"],
            "verification": running_verification or "unknown",
            "process_id": running.get("process_id"),
            "instance_id": running.get("instance_id"),
            "loaded_content": copy.deepcopy(running_loaded),
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
    kind: str, source_root: Path, install_root: Path
) -> Dict[str, Any]:
    targets = _SURFACE_ROWS[kind]
    try:
        desired_content = _surface_digest(
            source_root, targets, source_root=source_root
        )
        observed_content = _surface_digest(install_root, targets)
        content_completeness = "complete"
        verification = "verified"
    except OSError:
        desired_content = "unknown"
        observed_content = "unknown"
        content_completeness = "incomplete"
        verification = "unverified"
    desired_materialization = _materialization_identity(
        install_root, targets, desired=True
    )
    observed_materialization = _materialization_identity(
        install_root, targets, desired=False
    )
    desired = f"{desired_content};materialization={desired_materialization}"
    observed = (
        f"{observed_content};materialization={observed_materialization}"
    )
    materialization_mismatch = (
        desired_materialization != observed_materialization
    )
    affected = verification != "verified" or desired != observed
    return {
        "kind": kind,
        "locator": f"installed:{kind}",
        "desired_identity": desired,
        "observed_identity": observed,
        "desired_content_identity": desired_content,
        "observed_content_identity": observed_content,
        "state": "pending" if affected else "current",
        "affected": affected,
        "required": True,
        "materialization_mismatch": materialization_mismatch,
        "verification": verification,
        "completeness": content_completeness,
    }


def _prior_restart(
    install_root: Path, client_id: str
) -> "OrderedDict[str, Any]":
    """Read the pending restart fact the last coordinated run left behind.

    The restart row is a sibling of the installed-content row and carries no
    authority of its own, so it is read through the same record-compatibility
    and positive installed-content gate, and then through the same
    content-binding rule every other consumer applies: a record this runtime
    cannot interpret, or whose MCP identity does not name the MCP content
    installed here, supplies no prior process identity for fresh-process proof.
    The verdict is carried forward as well as the row, because a refused record
    or candidate is not the same evidence class as an absent one: only absence
    leaves the MCP surface free to be reported as unchanged.

    The observed identity is digested exactly as ``_version_records`` writes the
    recorded one, so the two sides of the comparison are the same authority.
    """
    evidence = install_state_evidence(install_root)
    return content_bound_restart_candidate(
        evidence,
        observed_mcp_identity=_surface_digest(install_root, MCP_TARGETS),
        client_id=client_id,
    )


def _restart_projection_for_result(
    *,
    mcp_surface: Mapping[str, Any],
    mcp_affecting_change: bool,
    running_fact: Mapping[str, Any],
    client_context: Mapping[str, Any],
    prior_process: Optional[Mapping[str, Any]],
) -> "OrderedDict[str, Any]":
    verified_install = (
        mcp_surface.get("state") in ("current", "verified")
        and mcp_surface.get("verification") == "verified"
    )
    return evaluate_restart(
        installed={
            "identity": mcp_surface.get("observed_content_identity"),
            "state": "verified" if verified_install else "unverified",
            "verification": "verified" if verified_install else "unverified",
            "completeness": mcp_surface.get("completeness", "unknown"),
            "authority": identity_contract()["installed_content"]["authority"],
        },
        running=running_fact,
        affected_surfaces={
            "mcp_affecting_change": mcp_affecting_change,
            "verification": mcp_surface.get("verification", "unknown"),
            "source": "affected-surface-plan",
        },
        client=client_context,
        prior_process=prior_process,
    )


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
        # Copy is the only materialization Cartopian installs; the literal
        # keeps previously persisted decision contexts comparable.
        "materialization_mode": "copy",
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
        or not supported_record_schema_version(raw.get("record_schema_version"))
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


def _persisted_source_identity(prior: Mapping[str, Any]) -> str:
    """Return the source identity a persisted record is bound to, if any."""
    envelope = prior.get("envelope")
    if not isinstance(envelope, Mapping):
        return ""
    run = envelope.get("run")
    if not isinstance(run, Mapping):
        return ""
    source = run.get("source")
    if not isinstance(source, Mapping):
        return ""
    return str(source.get("value", ""))


def _source_bound_envelope(
    prior: Mapping[str, Any], source_identity: str
) -> Optional[Mapping[str, Any]]:
    """Return the persisted envelope only when it is *this* source's record.

    Everything the planner carries forward from a prior run — declines,
    migration deferrals — is an answer to a question that a particular source
    asked.  Schema compatibility alone does not make that answer transferable,
    so the envelope is withheld entirely once the source identity differs.
    """
    if prior.get("classification") != "compatible":
        return None
    envelope = prior.get("envelope")
    if not isinstance(envelope, Mapping):
        return None
    if _persisted_source_identity(prior) != source_identity:
        return None
    return envelope


def _progress_declined_contexts(
    envelope: Optional[Mapping[str, Any]],
) -> Dict[str, Mapping[str, Any]]:
    """Declines carried by the persisted progress envelope, keyed by surface."""
    if not isinstance(envelope, Mapping):
        return {}
    projection = envelope.get("progress")
    if not isinstance(projection, Mapping):
        return {}
    if projection.get("schema_identity") != SCHEMA_IDENTITY or (
        not supported_record_schema_version(
            projection.get("record_schema_version")
        )
    ):
        return {}
    contexts: Dict[str, Mapping[str, Any]] = {}
    for item in projection.get("choices", []):
        if (
            isinstance(item, Mapping)
            and item.get("state") == "declined"
            and isinstance(item.get("decision_context"), Mapping)
        ):
            contexts[str(item.get("surface"))] = item["decision_context"]
    return contexts


def _progress_deferred_migrations(
    envelope: Optional[Mapping[str, Any]],
) -> Dict[str, Mapping[str, Any]]:
    """Migration deferrals carried by the persisted progress envelope."""
    if not isinstance(envelope, Mapping):
        return {}
    projection = envelope.get("progress")
    if not isinstance(projection, Mapping):
        return {}
    deferred: Dict[str, Mapping[str, Any]] = {}
    for item in projection.get("migrations", []):
        if isinstance(item, Mapping) and item.get("choice_state") == "deferred":
            deferred[str(item.get("project_identity"))] = item
    return deferred


def _progress_migration_context(
    envelope: Optional[Mapping[str, Any]],
) -> Optional[Mapping[str, Any]]:
    """The decision context the persisted migration deferral was made under."""
    if not isinstance(envelope, Mapping):
        return None
    projection = envelope.get("progress")
    if not isinstance(projection, Mapping):
        return None
    for item in projection.get("choices", []):
        if (
            isinstance(item, Mapping)
            and item.get("surface") == _MIGRATION_SURFACE
            and item.get("state") == "deferred"
            and isinstance(item.get("decision_context"), Mapping)
        ):
            return item["decision_context"]
    return None


def _apply_migration_deferrals(
    migrations: List[Dict[str, Any]],
    *,
    prior_deferrals: Mapping[str, Mapping[str, Any]],
    prior_context: Optional[Mapping[str, Any]],
    decision_context: Mapping[str, Any],
    explicit_defer: bool,
) -> None:
    """Carry a migration deferral only while its whole decision is unchanged.

    Two conditions must hold, and the first is what binds the answer to who
    asked.  A deferral is a decision about one offer made against one source; a
    changed source is a different authority proposing different content, so its
    offer must be answered afresh even when the schema numbers coincide.

    1. The recorded decision context must equal the context this run would
       produce.  That context names the validated source identity, the target
       schema, and the full offer set, so a changed source or a materially
       changed offer invalidates reuse outright.
    2. Each individual offer must still match on both schema identities, the
       applicability, and the named supported workflow — a per-project guard
       against a project changing underneath an otherwise-equal offer set.
    """
    reusable = prior_context is not None and _stable_context(
        prior_context
    ) == _stable_context(decision_context)
    for offer in migrations:
        if explicit_defer:
            offer["choice_state"] = "deferred"
            continue
        if not reusable:
            continue
        prior = prior_deferrals.get(str(offer.get("project_identity")))
        if prior is None:
            continue
        if all(
            str(prior.get(field)) == str(offer.get(field))
            for field in (
                "current_schema",
                "target_schema",
                "applicability",
                "supported_workflow",
            )
        ):
            offer["choice_state"] = "deferred"


def _stable_context(context: Any) -> str:
    """Return a comparison form that ignores key order but nothing else."""
    return json.dumps(
        context, sort_keys=True, separators=(",", ":"), default=str
    )


def _migration_decision_context(
    migrations: Sequence[Mapping[str, Any]],
    *,
    source_identity: str,
    target_schema: str,
) -> Dict[str, Any]:
    return {
        "context_schema": "coordinated-migration-offer-v1",
        "surface": _MIGRATION_SURFACE,
        "target_schema": target_schema,
        "offers": [
            [
                str(item.get("project_identity")),
                str(item.get("current_schema")),
                str(item.get("applicability")),
                str(item.get("supported_workflow")),
            ]
            for item in migrations
        ],
        "source": {
            "kind": "local-checkout",
            "value": source_identity,
            "authority": "maintainer-source-content",
        },
    }


def _migration_choice(
    migrations: Sequence[Mapping[str, Any]],
    *,
    source_identity: str,
    target_schema: str,
    provenance: str,
) -> Dict[str, Any]:
    """The provenance-backed deferral record for the migration-offer surface."""
    return {
        "id": f"{_MIGRATION_SURFACE}-migrate",
        "surface": _MIGRATION_SURFACE,
        "offered_action": "migrate",
        "state": "deferred",
        "provenance": provenance,
        "decision_context": _migration_decision_context(
            migrations,
            source_identity=source_identity,
            target_schema=target_schema,
        ),
    }


def _carry_verified_migration_deferrals(
    migrations: List[Dict[str, Any]], record: Mapping[str, Any]
) -> bool:
    """Re-apply the in-run migration deferral while its offer is unchanged."""
    prior = {
        str(item.get("project_identity")): item
        for item in record.get("migrations", [])
        if isinstance(item, Mapping) and item.get("choice_state") == "deferred"
    }
    if not prior:
        return False
    for offer in migrations:
        match = prior.get(str(offer.get("project_identity")))
        if match is None:
            continue
        if all(
            str(match.get(field)) == str(offer.get(field))
            for field in (
                "current_schema",
                "target_schema",
                "applicability",
                "supported_workflow",
            )
        ):
            offer["choice_state"] = "deferred"
    return bool(migrations) and all(
        item.get("choice_state") == "deferred" for item in migrations
    )


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
            action = (
                "offer-migration"
                if surface["affected"] and surface["state"] != "deferred"
                else "none"
            )
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
    client_home: Optional[Path] = None,
    clients: Sequence[str] = (),
    decisions: Optional[Mapping[str, str]] = None,
    release_ref: Optional[str] = None,
    running_server_fact: Optional[Mapping[str, Any]] = None,
    client_context: Optional[Mapping[str, Any]] = None,
    prior_process: Optional[Mapping[str, Any]] = None,
) -> "OrderedDict[str, Any]":
    """Inventory all supported surfaces and return a deterministic plan.

    This function performs no writes.  ``client_home`` is an adapter context
    used by isolated tests and embedded installers; it is not exposed as a
    public destination-selection option.
    """
    if operation not in ("fresh-install", "update", "repair", "verification"):
        raise WorkflowRefusal(f"unsupported operation: {operation}")
    source, install = _validate_roots(source_root, install_root)
    selected = _validate_clients(clients)
    dispositions = _validate_decisions(decisions or {})
    home = (client_home or Path.home()).expanduser().resolve()
    restart_observation_available = running_server_fact is not None
    running_observation = (
        copy.deepcopy(dict(running_server_fact))
        if running_server_fact is not None
        else {
            "process_id": None,
            "instance_id": None,
            "loaded_identity": None,
            "state": "unknown",
            "verification": "unknown",
            "authority": identity_contract()["running_server"]["authority"],
        }
    )

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
    # Reading persisted progress is the only prior-state input the planner
    # takes; it stays read-only here so planning remains side-effect free.
    prior_progress = read_progress(install)
    prior_envelope = _source_bound_envelope(prior_progress, source_identity)
    prior_declines = (
        {}
        if operation == "fresh-install"
        else {
            **_prior_declined_contexts(install),
            **_progress_declined_contexts(prior_envelope),
        }
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
        surfaces.append(_required_surface(kind, source, install))
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
        _required_surface("verification-content", source, install)
    )

    migrations, migration_state = _migration_offers(install, source)
    target_schema = _target_schema(source) or "unknown"
    migration_context = _migration_decision_context(
        migrations,
        source_identity=source_identity,
        target_schema=target_schema,
    )
    _apply_migration_deferrals(
        migrations,
        prior_deferrals=(
            {}
            if operation == "fresh-install"
            else _progress_deferred_migrations(prior_envelope)
        ),
        prior_context=(
            None
            if operation == "fresh-install"
            else _progress_migration_context(prior_envelope)
        ),
        decision_context=migration_context,
        explicit_defer=dispositions.get(_MIGRATION_SURFACE) == "defer",
    )
    migrations_deferred = bool(migrations) and all(
        item.get("choice_state") == "deferred" for item in migrations
    )
    surfaces.append(
        {
            "kind": "project-schema-migration-offers",
            "locator": "registered-projects:schema",
            "desired_identity": target_schema,
            "observed_identity": (
                target_schema if not migrations else f"{len(migrations)}-offer(s)"
            ),
            "state": (
                "deferred"
                if migrations_deferred
                else ("offered" if migrations else "not-applicable")
            ),
            "affected": bool(migrations),
            "required": False,
        }
    )
    current_client = (
        copy.deepcopy(dict(client_context))
        if client_context is not None
        else (
            normalize_client_context(
                selected[0], source="explicit-client-selection"
            )
            if len(selected) == 1
            else normalize_client_context(None, source="unavailable")
        )
    )
    restart_evidence = _prior_restart(install, str(current_client.get("id")))
    prior_restart = restart_evidence["row"]
    # Persisted restart evidence this content cannot claim is not evidence that
    # the MCP surface is unchanged: whatever wrote the row may have changed it,
    # so the surface stays restart-relevant while the row itself is withheld.
    # The shared authority decides which verdicts mean that — a record it
    # refused and a candidate it could not bind are both withheld evidence,
    # and only a genuinely absent record or candidate is benign here.
    withheld_restart_evidence = restart_evidence_withheld(restart_evidence)
    mcp_surface = next(
        item for item in surfaces if item["kind"] == "mcp-server-files"
    )
    mcp_affecting_change = bool(
        mcp_surface["affected"]
        or prior_restart is not None
        or withheld_restart_evidence
    )
    if prior_process is not None:
        restart_baseline = copy.deepcopy(dict(prior_process))
    elif prior_restart is not None:
        restart_baseline = {
            "process_id": prior_restart.get("process_id"),
            "instance_id": prior_restart.get("instance_id"),
        }
    elif mcp_surface["affected"] and restart_observation_available:
        restart_baseline = {
            "process_id": running_observation.get("process_id"),
            "instance_id": running_observation.get("instance_id"),
        }
    else:
        restart_baseline = None

    choices: List[Dict[str, str]] = []
    for surface in surfaces:
        kind = surface["kind"]
        if kind not in _OPTIONAL_SURFACES or not surface["affected"]:
            continue
        context = _decision_context(
            surface=surface,
            source_identity=source_identity,
            clients=selected,
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

    if migrations_deferred:
        choices.append(
            _migration_choice(
                migrations,
                source_identity=source_identity,
                target_schema=target_schema,
                provenance=(
                    "bounded-caller-disposition"
                    if dispositions.get(_MIGRATION_SURFACE) == "defer"
                    else "prior-run-matched-deferral"
                ),
            )
        )

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
            "restart_context": {
                "mcp_affecting_change": mcp_affecting_change,
                "running_process_id": running_observation.get("process_id"),
                "running_instance_id": running_observation.get("instance_id"),
                "running_loaded_identity": running_observation.get(
                    "loaded_identity"
                ),
                "client": current_client.get("id"),
                "prior_process": restart_baseline,
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    run_marker = "run:" + hashlib.sha256(marker_payload).hexdigest()[:20]
    plan_actions = _plan_actions(surfaces, choices)
    installed_identity = _surface_digest(install, CORE_TARGETS)
    resume_assessment = assess_resume(
        prior=prior_progress,
        current={
            "operation": operation,
            "marker": run_marker,
            "source_identity": source_identity,
            "installed_identity": (
                None if installed_identity == "absent" else installed_identity
            ),
            "surfaces": surfaces,
            "choices": choices,
            "migrations": migrations,
            "plan_actions": plan_actions,
            "restart": (
                {
                    "state": str(prior_restart.get("state")),
                    "instruction_class": str(
                        prior_restart.get("instruction_class", "none")
                    ),
                }
                if prior_restart is not None
                else {}
            ),
        },
        profiles=surface_retry_profiles(),
    )
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
            running_fact=running_observation,
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
            "release_ref": release_ref,
            "running_server_fact": running_observation,
            "restart_observation_available": restart_observation_available,
            "client_context": current_client,
            "mcp_affecting_change": mcp_affecting_change,
            "prior_process": restart_baseline,
            "restart_evidence": restart_evidence["status"],
            "affected_surface_plan": plan_actions,
            "registration_observations": registration_facts,
            "bridge_observations": bridge_facts,
            "surface_retry_profiles": surface_retry_profiles(),
            "progress_read": OrderedDict(
                (
                    ("classification", prior_progress["classification"]),
                    ("detail", prior_progress["detail"]),
                    ("lease_state", prior_progress["lease_state"]),
                )
            ),
            "resume_assessment": resume_assessment,
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


def _replace_tool_path(source: Path, target: Path) -> bool:
    desired = _digest_path(source)
    observed = _digest_path(target)
    if desired == observed and not target.is_symlink():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    stage_parent = Path(
        tempfile.mkdtemp(prefix=".cartopian-stage-", dir=str(target.parent))
    )
    staged = stage_parent / "payload"
    backup = target.parent / f".{target.name}.cartopian-backup"
    try:
        if source.is_dir():
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
            _replace_tool_path(source_root / source_rel, destination)


def _write_state(record: Mapping[str, Any]) -> None:
    internal = record.get("internal")
    if not isinstance(internal, Mapping):
        return
    install_root = Path(str(internal["install_root"]))
    recoverable_write_text(
        install_root / STATE_FILE,
        json.dumps(
            stable_projection(record),
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
    )


def _seeded_checkpoints(plan: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """One pending checkpoint per planned surface, before any mutation.

    Seeding up front is what lets a mutation boundary be persisted as
    ``in-progress``: a crash inside the window then reads back as uncertain
    instead of as absent work that a later run would silently redo.
    """
    rows: List[Dict[str, Any]] = []
    for surface in plan.get("surfaces", []):
        if not isinstance(surface, Mapping):
            continue
        kind = str(surface.get("kind"))
        profile = surface_retry_profile(kind)
        rows.append(
            {
                "id": f"verify-{kind}",
                "phase": (
                    "migration-offer" if kind == _MIGRATION_SURFACE else "verify"
                ),
                "surface": kind,
                "status": "pending",
                "evidence": {},
                "verification": "unknown",
                "retry_safety": profile["retry_safety"],
                "observation": profile["observation"],
            }
        )
    rows.sort(key=lambda item: str(item["id"]))
    return rows


def _seeded_projection(plan: Mapping[str, Any]) -> "OrderedDict[str, Any]":
    projection = stable_projection(plan)
    projection["checkpoints"] = _seeded_checkpoints(plan)
    return projection


def _uncertain_rows(assessment: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Return the assessed boundaries that may not be replayed uninspected."""
    return [
        dict(item)
        for item in assessment.get("uncertain", [])
        if isinstance(item, Mapping)
        and item.get("disposition")
        in ("inspect-before-retry", "refuse-replay")
    ]


def _uncertain_boundaries(plan: Mapping[str, Any]) -> List[Dict[str, Any]]:
    internal = plan.get("internal")
    assessment = (
        internal.get("resume_assessment")
        if isinstance(internal, Mapping)
        else None
    )
    if not isinstance(assessment, Mapping):
        return []
    return _uncertain_rows(assessment)


def _current_resume_facts(
    plan: Mapping[str, Any], *, install_root: Path
) -> Dict[str, Any]:
    """Rebuild the observation side of the resume assessment for apply.

    The surfaces, choices, migrations, and planned actions are this run's own
    authorized plan, so they are carried verbatim: apply may not widen what was
    authorized.  The installed-content identity is re-read from disk instead,
    because it is the authoritative observation another invocation can change
    between planning and lease acquisition, and the classification of a
    persisted record depends on whether the content it claims still exists.
    """
    internal = plan.get("internal")
    internal = internal if isinstance(internal, Mapping) else {}
    assessment = internal.get("resume_assessment")
    assessment = assessment if isinstance(assessment, Mapping) else {}
    restart = assessment.get("restart")
    run = plan.get("run")
    run = run if isinstance(run, Mapping) else {}
    source = run.get("source")
    source = source if isinstance(source, Mapping) else {}
    installed_identity = _surface_digest(install_root, CORE_TARGETS)
    return {
        "operation": str(run.get("operation")),
        "marker": str(run.get("marker")),
        "source_identity": str(source.get("value")),
        "installed_identity": (
            None if installed_identity == "absent" else installed_identity
        ),
        "surfaces": copy.deepcopy(list(plan.get("surfaces", []))),
        "choices": copy.deepcopy(list(plan.get("choices", []))),
        "migrations": copy.deepcopy(list(plan.get("migrations", []))),
        "plan_actions": copy.deepcopy(
            list(internal.get("affected_surface_plan", []))
        ),
        "restart": (
            copy.deepcopy(dict(restart))
            if isinstance(restart, Mapping)
            else {}
        ),
    }


def _prior_without_own_lease(
    held: Mapping[str, Any], *, owner: str
) -> "OrderedDict[str, Any]":
    """Drop this run's own lease from the freshly reread prior facts.

    A lease is evidence about some *other* invocation's claim on the root.
    This run acquired the lease it is holding, so leaving it in the prior facts
    would make every ordinary apply classify itself as recovering from its own
    orphan.
    """
    prior = OrderedDict(held)
    lease = prior.get("lease")
    if isinstance(lease, Mapping) and str(lease.get("owner", "")) == owner:
        prior["lease"] = None
        prior["lease_state"] = "absent"
    return prior


def _post_lease_assessment(
    plan: Mapping[str, Any],
    held: Mapping[str, Any],
    *,
    install_root: Path,
    owner: str,
    profiles: Sequence[Mapping[str, Any]],
) -> "OrderedDict[str, Any]":
    """Recompute the complete resume assessment under the acquired lease."""
    return assess_resume(
        prior=_prior_without_own_lease(held, owner=owner),
        current=_current_resume_facts(plan, install_root=install_root),
        profiles=profiles,
    )


def _foreign_open_boundary(held: Mapping[str, Any], *, run_marker: str) -> str:
    """Return the surface of another run's still-open mutation boundary."""
    envelope = held.get("envelope")
    if not isinstance(envelope, Mapping) or envelope.get("status") != "active":
        return ""
    boundary = envelope.get("boundary")
    if not isinstance(boundary, Mapping) or not boundary.get("surface"):
        return ""
    run = envelope.get("run")
    run = run if isinstance(run, Mapping) else {}
    if str(run.get("marker", "")) == run_marker:
        return ""
    return str(boundary.get("surface"))


def _gate_post_lease_resume(
    assessment: Mapping[str, Any],
    held: Mapping[str, Any],
    *,
    acknowledged: AbstractSet[str],
    run_marker: str,
    plan_compatibility: str,
) -> None:
    """Refuse a stale plan against the record it is actually about to replace.

    The plan-time gates ran against the progress record as it stood *before*
    the lease existed.  Another invocation can persist an open mutation
    boundary and crash in that window, so every gate is applied again here,
    against the reread record and the current observations, before anything is
    preserved, quarantined, or written over.
    """
    compatibility = str(assessment.get("compatibility", ""))
    detail = str(assessment.get("compatibility_detail", ""))
    if compatibility == "unsupported-newer":
        raise ProgressRefusal(
            "unsupported-newer",
            "persisted install progress was written by a newer schema than "
            "this tool supports; it is preserved untouched. Migrate progress "
            "with the newer tool before applying.",
        )
    if compatibility == "lease-conflict":
        raise ProgressRefusal(
            "lease-conflict",
            detail
            or "another invocation holds the progress lease for this root",
        )
    uncertain = _uncertain_rows(assessment)
    outstanding = {str(item.get("surface")) for item in uncertain}
    # An open boundary under a different run identity is uncertain even when
    # its projection carries no checkpoint row to describe it.
    foreign = _foreign_open_boundary(held, run_marker=run_marker)
    if foreign:
        outstanding.add(foreign)
    unacknowledged = sorted(outstanding - set(acknowledged))
    if unacknowledged:
        raise ProgressRefusal(
            "uncertain-boundary",
            "an interrupted run left uncertain work at "
            + ", ".join(unacknowledged)
            + "; it was discovered under the progress lease after this plan "
            "was computed, so the plan no longer describes the persisted "
            "record. That boundary must be inspected before retry rather "
            "than replaced or replayed",
        )
    refuse_replay = sorted(
        {
            str(item.get("surface"))
            for item in uncertain
            if item.get("disposition") == "refuse-replay"
        }
        & set(acknowledged)
    )
    if refuse_replay:
        raise ProgressRefusal(
            "refuse-replay",
            "prior work at "
            + ", ".join(refuse_replay)
            + " is not repeatable and cannot be replayed by resume; use its "
            "separately authorized workflow",
        )
    if (
        compatibility != plan_compatibility
        and compatibility not in _RECONCILABLE_POST_LEASE
    ):
        raise ProgressRefusal(
            "resume-unreconcilable",
            "persisted install progress changed between planning and lease "
            f"acquisition ({plan_compatibility or 'unknown'} -> "
            f"{compatibility or 'unknown'}) and the new facts cannot be "
            "reconciled with this plan"
            + (f": {detail}" if detail else "")
            + "; re-plan against the current record",
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


def apply_workflow(
    plan: Mapping[str, Any], *, inspected: Sequence[str] = ()
) -> "OrderedDict[str, Any]":
    """Apply authorized plan work, then verify every surface.

    Progress is persisted around every mutation boundary: intent before the
    mutation, verified evidence after it, and the schema, completion, and
    cleanup markers last.  ``inspected`` names the closed surfaces whose
    uncertain boundary the caller asserts it has inspected; without it, an
    interrupted prior run is refused rather than blindly replayed.
    """
    internal = plan.get("internal")
    if not isinstance(internal, Mapping):
        raise WorkflowRefusal("workflow plan has no trusted adapter context")
    source_root, install_root = _validate_roots(
        Path(str(internal["source_root"])),
        Path(str(internal["install_root"])),
    )
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

    acknowledged = set()
    for value in inspected:
        name = str(value)
        if name not in SURFACE_KINDS:
            raise WorkflowRefusal(f"unknown inspected surface: {name}")
        acknowledged.add(name)
    uncertain = _uncertain_boundaries(plan)
    outstanding = sorted(
        {str(item.get("surface")) for item in uncertain} - acknowledged
    )
    if outstanding:
        raise WorkflowRefusal(
            "an interrupted prior run left uncertain work at "
            + ", ".join(outstanding)
            + "; that boundary is uncertain and must be inspected before "
            "retry rather than replayed"
        )
    refuse_replay = sorted(
        {
            str(item.get("surface"))
            for item in uncertain
            if item.get("disposition") == "refuse-replay"
        }
        & acknowledged
    )
    if refuse_replay:
        raise WorkflowRefusal(
            "prior work at "
            + ", ".join(refuse_replay)
            + " is not repeatable and cannot be replayed by resume; use its "
            "separately authorized workflow"
        )

    progress_read = internal.get("progress_read")
    progress_read = progress_read if isinstance(progress_read, Mapping) else {}
    classification = str(progress_read.get("classification", "absent"))
    # The intrinsic read classification only describes whether the stored bytes
    # are usable.  Whether they may be *replaced* is a comparison against
    # current observations, which is what the resume assessment carries; apply
    # gates on the assessment so a changed source cannot walk past a record the
    # planner already judged incompatible.
    assessment = internal.get("resume_assessment")
    assessment = assessment if isinstance(assessment, Mapping) else {}
    compatibility = str(assessment.get("compatibility") or classification)
    compatibility_detail = str(
        assessment.get("compatibility_detail")
        or progress_read.get("detail", "")
    )
    if "unsupported-newer" in (classification, compatibility):
        raise WorkflowRefusal(
            "persisted install progress was written by a newer schema than "
            "this tool supports; it is preserved untouched. Migrate progress "
            "with the newer tool before applying."
        )
    profiles = internal.get("surface_retry_profiles")
    if not isinstance(profiles, list) or not profiles:
        profiles = surface_retry_profiles()

    owner = new_owner_token()
    run_marker = str(plan["run"]["marker"])
    try:
        install_root.mkdir(parents=True, exist_ok=True)
        # Claim the root before touching any persisted evidence, so two runs
        # cannot quarantine or supersede each other's record concurrently.
        lease = acquire_lease(
            install_root, run_marker=run_marker, owner=owner
        )
    except ProgressRefusal as exc:
        raise WorkflowRefusal(exc.detail) from exc
    try:
        # Re-read under the lease.  The gate must act on the record that is
        # actually about to be replaced, not on the plan-time snapshot: between
        # planning and applying, another run may have written a record bound to
        # a different source — or left an open mutation boundary and died.
        held = read_progress(install_root)
        held_envelope = held.get("envelope")
        held_recovery = (
            held_envelope.get("recovery")
            if isinstance(held_envelope, Mapping)
            else None
        )
        held_classification = str(held.get("classification") or classification)
        held_detail = str(
            held.get("detail") or progress_read.get("detail", "")
        )
        if held_classification == "unsupported-newer":
            raise ProgressRefusal(
                "unsupported-newer",
                "persisted install progress was written by a newer schema "
                "than this tool supports; it is preserved untouched. Migrate "
                "progress with the newer tool before applying.",
            )
        # The plan's assessment describes a record that may no longer be the
        # one on disk.  Recompute the *complete* assessment against the reread
        # record and the current observations, then run every gate against it,
        # so a plan computed against different facts cannot walk past work it
        # never saw.
        post_lease = _post_lease_assessment(
            plan,
            held,
            install_root=install_root,
            owner=owner,
            profiles=profiles,
        )
        _gate_post_lease_resume(
            post_lease,
            held,
            acknowledged=acknowledged,
            run_marker=run_marker,
            plan_compatibility=compatibility,
        )
        compatibility = str(post_lease.get("compatibility") or compatibility)
        compatibility_detail = str(
            post_lease.get("compatibility_detail") or compatibility_detail
        )
        held_source = _persisted_source_identity(held)
        if (
            held_source
            and held_source != str(plan["run"]["source"]["value"])
            and compatibility not in _PRESERVE_BEFORE_REPLACEMENT
        ):
            compatibility = "source-mismatch"
            compatibility_detail = (
                "persisted progress is bound to a different source identity "
                "and cannot drive new mutations"
            )
        progress_recovery = None
        if held_classification in ("corrupted", "evidence-missing"):
            progress_recovery = quarantine_progress(
                install_root,
                classification=held_classification,
                detail=held_detail,
            )
        elif compatibility in _PRESERVE_BEFORE_REPLACEMENT:
            # Intact, meaningful, and not this run's to consume.  It is retained
            # verbatim before any new envelope is written over it.
            progress_recovery = preserve_progress(
                install_root,
                classification=compatibility,
                detail=compatibility_detail,
            )
        elif lease.get("takeover"):
            progress_recovery = resume_recovery_note(
                "orphaned",
                "released a progress lease whose holder is no longer running",
            )
        # Evidence preserved by an earlier run keeps its provenance: it is
        # superseded only by terminal proof, and this run may be the one that
        # reaches it.
        progress_recovery = carry_preserved_evidence(
            install_root, progress_recovery, prior_recovery=held_recovery
        )
    except ProgressRefusal as exc:
        release_lease(install_root, owner)
        raise WorkflowRefusal(exc.detail) from exc

    try:
        return _apply_under_lease(
            plan,
            source_root=source_root,
            install_root=install_root,
            client_home=client_home,
            clients=clients,
            choices=choices,
            surfaces=surfaces,
            profiles=profiles,
            owner=owner,
            progress_recovery=progress_recovery,
        )
    finally:
        release_lease(install_root, owner)


def _apply_under_lease(
    plan: Mapping[str, Any],
    *,
    source_root: Path,
    install_root: Path,
    client_home: Path,
    clients: Sequence[str],
    choices: Mapping[str, Mapping[str, Any]],
    surfaces: Mapping[str, Mapping[str, Any]],
    profiles: Sequence[Mapping[str, Any]],
    owner: str,
    progress_recovery: Optional[Mapping[str, Any]],
) -> "OrderedDict[str, Any]":
    try:
        progress = begin_progress(
            install_root,
            record=plan,
            projection=_seeded_projection(plan),
            surface_profiles=profiles,
            owner=owner,
            recovery=progress_recovery,
        )
    except ProgressRefusal as exc:
        raise WorkflowRefusal(exc.detail) from exc

    refused_surface = "core-files"
    attempted_action = "install-tool-owned-content"
    recovery = (
        "inspect the refused tool-owned surface and its recoverable replacement "
        "boundary before retry"
    )
    recovery_artifact = "tool-owned-content-preserved-or-recoverable"
    try:
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
            progress = open_boundary(
                install_root,
                progress,
                surface=surface_kind,
                action=attempted_action,
                owner=owner,
            )
            for target_rel in _SURFACE_ROWS[surface_kind]:
                source = _source_for_target(source_root, target_rel)
                _replace_tool_path(source, install_root / target_rel)
        refused_surface = "core-files"
        attempted_action = "seed-operator-files"
        recovery = (
            "inspect the operator-owned seed files; existing operator content "
            "was not overwritten"
        )
        recovery_artifact = "operator-files:preserved"
        if any(
            not (install_root / name).exists() for name in OPERATOR_FILES
        ):
            progress = open_boundary(
                install_root,
                progress,
                surface="core-files",
                action=attempted_action,
                owner=owner,
            )
        _seed_operator_files(source_root, install_root)

        bridge_choice = choices.get("bridges", {})
        if bridge_choice.get("state") == "authorized":
            refused_surface = "bridges"
            attempted_action = "repair"
            recovery = (
                "inspect the derived client bridge replacement boundary before retry"
            )
            recovery_artifact = "supported-client-bridge:replacement-boundary"
            progress = open_boundary(
                install_root,
                progress,
                surface="bridges",
                action=attempted_action,
                phase="repair",
                owner=owner,
            )
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
            progress = open_boundary(
                install_root,
                progress,
                surface="client-configuration",
                action=attempted_action,
                phase="repair",
                owner=owner,
            )
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
            record_failure(
                install_root,
                progress,
                projection=stable_projection(blocked),
                owner=owner,
                detail=failure_recovery,
                mutation_status=mutation_status,
            )
        except Exception:
            # Persistence is best-effort on an already-failing apply boundary.
            # The original refusal or OS error remains the truthful cause.
            pass
        raise

    result = verify_workflow(plan)
    try:
        # Evidence first: every checkpoint reaches disk before any marker moves.
        for checkpoint in result["checkpoints"]:
            progress = commit_checkpoint(
                install_root, progress, checkpoint=checkpoint, owner=owner
            )
        progress = advance_completion(
            install_root,
            progress,
            projection=stable_projection(result),
            owner=owner,
        )
        # The visible mirror publishes between the completion and cleanup
        # markers, so a failure here leaves cleanup pending and the run
        # explicitly resumable rather than silently half-published.
        _write_state(result)
        terminal = progress.get("terminal")
        if (
            isinstance(terminal, Mapping)
            and terminal.get("completion") == "advanced"
        ):
            advance_cleanup(install_root, progress, owner=owner)
    except ProgressRefusal as exc:
        raise WorkflowRefusal(exc.detail) from exc
    except OSError as exc:
        raise WorkflowRefusal(
            "install progress could not be persisted "
            f"({exc.__class__.__name__}); no completion marker was advanced "
            "beyond the verified work and the run remains resumable"
        ) from exc
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
    profile = surface_retry_profile(str(surface["kind"]))
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
        # The adapter's declared retry class is the floor; an unfinished or
        # failed boundary can only make replay less safe, never more.
        "retry_safety": (
            profile["retry_safety"]
            if completed and not failed
            else _escalate_retry(
                profile["retry_safety"], "inspect-before-retry"
            )
        ),
        "observation": profile["observation"],
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
    choices = {
        item["surface"]: item
        for item in record.get("choices", [])
        if isinstance(item, Mapping)
    }

    verified_surfaces: List[Dict[str, Any]] = []
    failed_kinds: set[str] = set()
    for kind in ("core-files", "mcp-server-files", "wrappers"):
        surface = _required_surface(kind, source_root, install_root)
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
        "verification-content", source_root, install_root
    )
    if verification["affected"]:
        verification["state"] = "failed"
        failed_kinds.add("verification-content")
    else:
        verification["state"] = "verified"
    verified_surfaces.append(verification)

    migrations, migration_state = _migration_offers(install_root, source_root)
    migrations_deferred = _carry_verified_migration_deferrals(
        migrations, record
    )
    target_schema = _target_schema(source_root) or "unknown"
    verified_surfaces.append(
        {
            "kind": "project-schema-migration-offers",
            "locator": "registered-projects:schema",
            "desired_identity": target_schema,
            "observed_identity": (
                target_schema if not migrations else f"{len(migrations)}-offer(s)"
            ),
            "state": (
                "deferred"
                if migrations_deferred
                else ("offered" if migrations else "not-applicable")
            ),
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
    updated["surfaces"] = verified_surfaces
    updated["checkpoints"] = checkpoints
    updated["migrations"] = migrations
    running_fact = internal.get("running_server_fact")
    if not isinstance(running_fact, Mapping):
        running_fact = running_server_from_environment()
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
        running_fact=running_fact,
    )
    restart_projection = None
    restart_observation_available = bool(
        internal.get("restart_observation_available")
    )
    prior_process = internal.get("prior_process")
    if not isinstance(prior_process, Mapping):
        prior_process = None
    # Planning withheld a persisted restart row: either the record itself was
    # refused, or its candidate could not be bound to this MCP content. The
    # result must still report restart state rather than close as if no restart
    # evidence had ever been persisted.
    withheld_restart_evidence = restart_evidence_withheld(
        internal.get("restart_evidence")
    )
    if (
        restart_observation_available
        or prior_process is not None
        or withheld_restart_evidence
    ):
        mcp_surface = next(
            item
            for item in verified_surfaces
            if item["kind"] == "mcp-server-files"
        )
        current_client = internal.get("client_context")
        if not isinstance(current_client, Mapping):
            current_client = client_context_from_environment(clients)
        restart_projection = _restart_projection_for_result(
            mcp_surface=mcp_surface,
            mcp_affecting_change=bool(
                internal.get("mcp_affecting_change")
            ),
            running_fact=running_fact,
            client_context=current_client,
            prior_process=prior_process,
        )
        updated["restarts"] = [restart_record(restart_projection)]
        running_version = next(
            item
            for item in updated["versions"]
            if item["kind"] == "running_server"
        )
        if restart_projection["status"] == "current":
            running_version["state"] = "current"
        elif restart_projection["reason_code"] in (
            "running_content_stale",
            "fresh_process_content_stale",
        ):
            running_version["state"] = "stale-runtime"
        else:
            running_version["state"] = "unknown"
        if not failed_kinds:
            if restart_projection["status"] in (
                "restart_required",
                "restart_instructed",
                "verification_pending",
                "unverified",
            ):
                state = "restart-required"
            elif restart_projection["status"] == "blocked":
                state = "blocked"
    else:
        updated["restarts"] = []
    updated["state"] = state
    updated_internal = dict(internal)
    updated_internal["registration_observations"] = registration_facts
    updated_internal["bridge_observations"] = bridge_facts
    if restart_projection is not None:
        updated_internal["restart_projection"] = restart_projection
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
        "cross-platform check is parity evidence, not native execution proof.\n\n"
        "After an MCP-affecting update, installed and running identities remain "
        "separate. Restart state closes only when a new process or instance "
        "reports verified loaded MCP content matching the installed identity. "
        "Old or unknown content on a new process remains restart-required, and "
        "no activation claim is permitted before that proof.\n"
    )
