"""`cartopian resolve-config <project-path>` implementation."""
import argparse
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from cli.capabilities import GrantResolution, resolve_grants, role_description
from cli.emit import emit_record
from cli.main import EXIT_ENV, EXIT_FAIL, EXIT_OK, EXIT_USAGE

PROTOCOL_DEFAULT_ROLES: Dict[str, str] = {
    "pm": "Manages the project lifecycle and orchestrates handoffs.",
    "operator": "Human direction-setter who approves transitions and resolves judgment calls.",
}

PROTOCOL_DEFAULT_AUTOMATION: Dict[str, Any] = {
    "initiation": "operator",
    "confirmation": "each-handoff",
    "max_handoffs_per_run": 1,
}

_AUTOMATION_INITIATION_VALUES = ("operator", "auto")

PROTOCOL_DEFAULT_GIT_VERSIONING: bool = False

PROTOCOL_DEFAULT_REVIEWS: Dict[str, str] = {
    "planning": "off",
    "task_closure": "off",
}

_REVIEW_MODES = ("required", "off")
_EXPLICIT_REVIEW_POLICY_VERSION = (0, 5, 0)


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "project_path",
        help="Absolute path to the project root",
    )


def _stderr(prefix: str, msg: str) -> None:
    sys.stderr.write(f"[{prefix}] {msg}\n")


def _load_toml(path: Path, label: str) -> Optional[Dict[str, Any]]:
    """Load a TOML file. Returns dict, or None if missing.

    Raises a (prefix, msg, exit_code) tuple-encoded RuntimeError on read/parse failure.
    """
    if not path.exists():
        return None
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise _CliError(
            EXIT_ENV,
            "error",
            f"{label} unreadable: {path} — {exc}",
        ) from exc


class _CliError(Exception):
    def __init__(self, exit_code: int, prefix: str, message: str) -> None:
        self.exit_code = exit_code
        self.prefix = prefix
        self.message = message
        super().__init__(message)


def _require_project_table(project_cfg: Dict[str, Any], project_toml: Path) -> Dict[str, Any]:
    if "project" not in project_cfg:
        looks_like_workspace = any(key in project_cfg for key in ("defaults", "roles", "handoffs", "workspace"))
        if looks_like_workspace:
            hint = (
                f"{project_toml} is a Cartopian workspace config, not a project config. "
                "Run `cartopian discover-projects` (or call the `discover_projects` MCP tool) "
                "to list registered projects, then pass a project id or absolute path to this command."
            )
        else:
            hint = (
                f"not a Cartopian project: {project_toml} has no [project] table. "
                "Run `cartopian discover-projects` to see registered projects, "
                "or run `cartopian scaffold-project` / the `init project` skill to create one."
            )
        raise _CliError(EXIT_FAIL, "guard", hint)
    project_table = project_cfg["project"]
    if not isinstance(project_table, dict):
        raise _CliError(
            EXIT_FAIL,
            "error",
            f"project config malformed: [project] must be a table in {project_toml}",
        )
    return project_table


def _load_project_config(project_path: Path) -> Dict[str, Any]:
    project_toml = project_path / "cartopian.toml"
    if not project_toml.exists():
        raise _CliError(EXIT_FAIL, "error", f"project config not found: {project_toml}")
    project_cfg = _load_toml(project_toml, "project config") or {}
    _require_project_table(project_cfg, project_toml)
    return project_cfg


def _merge_table(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Shallow merge: override wins, preserving base entries not in override."""
    result = dict(base)
    result.update(override)
    return result


def _resolve_handoffs(
    global_cfg: Dict[str, Any], project_cfg: Dict[str, Any]
) -> Dict[str, Dict[str, Any]]:
    g = global_cfg.get("handoffs", {}) or {}
    p = project_cfg.get("handoffs", {}) or {}
    merged: Dict[str, Dict[str, Any]] = {}
    for role in list(g.keys()) + [r for r in p.keys() if r not in g]:
        block: Dict[str, Any] = {}
        if role in g and isinstance(g[role], dict):
            block.update(g[role])
        if role in p and isinstance(p[role], dict):
            block.update(p[role])

        auto_start_tasks = block.get("auto_start_tasks")
        if auto_start_tasks is None and "auto_start" in block:
            # Pre-v0.5 compatibility: the former auto_start key controlled
            # task-scoped launches.
            auto_start_tasks = block.get("auto_start")

        auto_start_reviews = block.get("auto_start_reviews")
        if auto_start_reviews is None and (
            "auto_start" in block or "planning_reviews" in block
        ):
            # Pre-v0.5 planning launches required both legacy booleans. Collapse
            # them to the single explicit launch decision exposed now.
            auto_start_reviews = (
                block.get("auto_start") is True
                and block.get("planning_reviews") is True
            )
        merged[role] = {
            "agent": block.get("agent"),
            "model": block.get("model"),
            "effort": block.get("effort"),
            "auto_start_tasks": auto_start_tasks,
            "auto_start_reviews": auto_start_reviews,
            "timeout": block.get("timeout"),
        }
    return merged


def _resolve_roles(
    global_cfg: Dict[str, Any], project_cfg: Dict[str, Any]
) -> Dict[str, Any]:
    """Merge [roles]; values are legacy description strings or [roles.<name>] tables."""
    g_roles = global_cfg.get("roles", {}) or {}
    p_roles = project_cfg.get("roles", {}) or {}
    if not g_roles and not p_roles:
        return dict(PROTOCOL_DEFAULT_ROLES)
    merged = _merge_table(g_roles, p_roles)
    for key, default in PROTOCOL_DEFAULT_ROLES.items():
        if key not in merged:
            merged[key] = default
    return merged


def _review_table(cfg: Dict[str, Any], label: str) -> Dict[str, Any]:
    """Return one config's ``[reviews]`` table, validating its shape.

    Review behavior is policy, not a property of a specially named role.  A
    malformed policy must therefore fail closed instead of accidentally
    selecting the protocol default (review-off).
    """
    raw = cfg.get("reviews", {})
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise _CliError(
            EXIT_FAIL,
            "config",
            f"{label} malformed: [reviews] must be a table",
        )
    return raw


def _review_value(
    project_reviews: Dict[str, Any],
    global_reviews: Dict[str, Any],
    key: str,
    default: Any,
) -> Tuple[Any, str]:
    if key in project_reviews:
        return project_reviews[key], "project"
    if key in global_reviews:
        return global_reviews[key], "global"
    return default, "protocol-default"


def _resolve_reviews(
    global_cfg: Dict[str, Any],
    project_cfg: Dict[str, Any],
    roles: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Resolve review policy and arbitrary role assignments.

    ``planning`` and ``task_closure`` are independent ``required | off``
    policies.  ``planning_role`` and ``task_role`` name ordinary resolved
    roles; role names and descriptions carry no intrinsic review semantics.
    Project values override global values key-by-key, so a project can turn a
    globally configured review loop off without removing the inherited role.
    """
    global_reviews = _review_table(global_cfg, "global config")
    project_reviews = _review_table(project_cfg, "project config")
    resolved_roles = roles if roles is not None else _resolve_roles(global_cfg, project_cfg)

    # Compatibility is version-scoped, not role semantics. Before v0.5.0 the
    # protocol had two mandatory review loops and selected the conventional
    # `reviewer` role implicitly. Preserve that behavior until migration writes
    # an explicit policy. A v0.5.0+ project never acquires behavior from this
    # (or any other) role name.
    project_table = project_cfg.get("project", {})
    raw_version = (
        project_table.get("protocol_version")
        if isinstance(project_table, dict)
        else None
    )
    version_match = (
        re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", raw_version)
        if isinstance(raw_version, str)
        else None
    )
    legacy_protocol = raw_version is None or (
        version_match is not None
        and tuple(int(part) for part in version_match.groups())
        < _EXPLICIT_REVIEW_POLICY_VERSION
    )
    legacy_reviewer = legacy_protocol and "reviewer" in resolved_roles

    result: Dict[str, Dict[str, Any]] = {}
    fields = (
        ("planning", "planning_role"),
        ("task_closure", "task_role"),
    )
    for policy_key, role_key in fields:
        legacy_default = (
            legacy_reviewer
            and policy_key not in project_reviews
            and policy_key not in global_reviews
        )
        mode, mode_source = _review_value(
            project_reviews,
            global_reviews,
            policy_key,
            "required" if legacy_default else PROTOCOL_DEFAULT_REVIEWS[policy_key],
        )
        if legacy_default and mode_source == "protocol-default":
            mode_source = "legacy-pre-v0.5"
        if not isinstance(mode, str) or mode not in _REVIEW_MODES:
            raise _CliError(
                EXIT_FAIL,
                "config",
                f"[reviews].{policy_key} must be one of "
                f"{{{', '.join(_REVIEW_MODES)}}}; got: {mode!r}",
            )

        role, role_source = _review_value(
            project_reviews,
            global_reviews,
            role_key,
            "reviewer" if legacy_default else None,
        )
        if legacy_default and role_source == "protocol-default":
            role_source = "legacy-pre-v0.5"
        if role is not None and (not isinstance(role, str) or not role.strip()):
            raise _CliError(
                EXIT_FAIL,
                "config",
                f"[reviews].{role_key} must be a non-empty role name; got: {role!r}",
            )
        if isinstance(role, str):
            role = role.strip()

        if mode == "required":
            if role is None:
                raise _CliError(
                    EXIT_FAIL,
                    "config",
                    f"[reviews].{policy_key} = \"required\" requires "
                    f"[reviews].{role_key}",
                )
            if role not in resolved_roles:
                raise _CliError(
                    EXIT_FAIL,
                    "config",
                    f"[reviews].{role_key} names undeclared role {role!r}",
                )

        result[policy_key] = {
            "mode": mode,
            "role": role if mode == "required" else None,
            "attribution": {
                "mode": mode_source,
                "role": role_source if mode == "required" else None,
            },
        }
    return result


def resolve_review_policy(project_path: Path) -> Dict[str, Dict[str, Any]]:
    """Load and resolve one project's review policy for lifecycle commands."""
    project_cfg = _load_project_config(project_path)
    global_cfg = _load_toml(
        Path.home() / ".cartopian" / "cartopian.toml", "global config"
    ) or {}
    roles = _resolve_roles(global_cfg, project_cfg)
    return _resolve_reviews(global_cfg, project_cfg, roles)


def _resolve_automation(
    global_cfg: Dict[str, Any], project_cfg: Dict[str, Any]
) -> Dict[str, Any]:
    merged = dict(PROTOCOL_DEFAULT_AUTOMATION)
    g_auto = global_cfg.get("automation", {}) or {}
    p_auto = project_cfg.get("automation", {}) or {}
    merged.update(g_auto)
    merged.update(p_auto)
    initiation = merged.get("initiation", PROTOCOL_DEFAULT_AUTOMATION["initiation"])
    if initiation not in _AUTOMATION_INITIATION_VALUES:
        # Fail safe, not closed: an unknown value disables automatic initiation
        # rather than blocking the session on a typo.
        _stderr(
            "validation",
            f"unknown [automation].initiation value {initiation!r} — "
            'falling back to "operator" (execution waits for an operator directive)',
        )
        initiation = PROTOCOL_DEFAULT_AUTOMATION["initiation"]
    return {
        "initiation": initiation,
        "confirmation": merged.get("confirmation", PROTOCOL_DEFAULT_AUTOMATION["confirmation"]),
        "max_handoffs_per_run": merged.get(
            "max_handoffs_per_run", PROTOCOL_DEFAULT_AUTOMATION["max_handoffs_per_run"]
        ),
    }


def _resolve_git_versioning(
    global_cfg: Dict[str, Any], project_cfg: Dict[str, Any]
) -> Tuple[bool, str]:
    p_defaults = project_cfg.get("defaults", {}) or {}
    g_defaults = global_cfg.get("defaults", {}) or {}
    if "git_versioning" in p_defaults:
        return bool(p_defaults["git_versioning"]), "project"
    if "git_versioning" in g_defaults:
        return bool(g_defaults["git_versioning"]), "global"
    return PROTOCOL_DEFAULT_GIT_VERSIONING, "protocol-default: false"


def _resolve_git_block(
    global_cfg: Dict[str, Any], project_cfg: Dict[str, Any]
) -> Dict[str, Any]:
    g_git = global_cfg.get("git", {}) or {}
    p_git = project_cfg.get("git", {}) or {}
    return _merge_table(g_git, p_git)


def _resolve_work_roots(
    project_cfg: Dict[str, Any], project_path: Path
) -> Dict[str, str]:
    project_table = project_cfg.get("project", {}) or {}
    names = project_table.get("work_roots", []) or []
    if not names:
        return {}
    local_path = project_path / "cartopian.local.toml"
    local_cfg: Dict[str, Any] = {}
    if local_path.exists():
        try:
            with local_path.open("rb") as fh:
                local_cfg = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise _CliError(
                EXIT_ENV,
                "error",
                f"local config unreadable: {local_path} — {exc}",
            ) from exc
    local_work_roots = local_cfg.get("work_roots", {}) or {}
    resolved: Dict[str, str] = {}
    for name in names:
        if name not in local_work_roots:
            raise _CliError(
                EXIT_FAIL,
                "work-root",
                f"unmapped: {name} — add to {local_path}",
            )
        raw_value = str(local_work_roots[name])
        candidate = Path(raw_value)
        if not candidate.is_absolute():
            raise _CliError(
                EXIT_FAIL,
                "work-root",
                (
                    f'non-absolute path: {name} = "{raw_value}" — '
                    f"cartopian.local.toml must use absolute paths"
                ),
            )
        resolved[name] = str(candidate)
    return resolved


_DELIVERABLE_SKIP = {"", "n/a", "none"}


def _relpath_in_resources(relpath: str) -> bool:
    """True when a project-mode deliverable path lands under ``resources/``.

    The path must be relative, traversal-free, and name a file strictly inside
    ``resources/`` (CONVENTIONS § Project Resources). Windows separators are
    accepted and normalized before the check.
    """
    normalized = relpath.replace("\\", "/")
    if not normalized or normalized.startswith("/") or os.path.isabs(relpath):
        return False
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if ".." in parts or len(parts) < 2:
        return False
    return parts[0] == "resources"


def _lookup_work_root_path(
    project_cfg: Dict[str, Any], project_path: Path, name: str
) -> Optional[Path]:
    """Resolve one work-root ``name`` to its absolute path, or ``None``.

    Lenient and single-name by design — unlike :func:`_resolve_work_roots`
    (all-or-nothing), an unrelated unmapped or malformed root never poisons this
    lookup. Returns ``None`` when the name is not a declared work root, when
    ``cartopian.local.toml`` is absent/unreadable/omits it, or when the mapped
    value is not absolute. Callers that need existence verification treat
    ``None`` as "cannot verify on this machine".
    """
    project_table = project_cfg.get("project", {}) or {}
    if name not in (project_table.get("work_roots", []) or []):
        return None
    local_path = project_path / "cartopian.local.toml"
    if not local_path.exists():
        return None
    try:
        with local_path.open("rb") as fh:
            local_cfg = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    raw = (local_cfg.get("work_roots", {}) or {}).get(name)
    if raw is None:
        return None
    candidate = Path(str(raw))
    return candidate if candidate.is_absolute() else None


def _resolve_deliverable(
    project_cfg: Dict[str, Any], project_path: Path, raw_value: str
) -> Optional[Dict[str, Any]]:
    """Resolve a task's ``Deliverable:`` reference to an absolute path.

    The reference is name-only and deidentified (it mirrors ``Work root:`` and
    carries no ``NN-NNN``), in one of two forms:

    - ``<work-root-name>:<relative/path>`` (mode ``work-root``) — a work
      product intended to become part of the product, at an operator-chosen
      path. The coder writes it directly into that work root, exactly as it
      writes code. ``absolute_path`` is ``None`` when the name is unmapped
      on this machine; the work-root validator surfaces that separately, so the
      aggregator does not hard-fail on it.
    - ``project:resources/<relative/path>`` (mode ``project``) — a supporting
      artifact of the project itself, under the project's ``resources/``
      directory. Because the coder is not granted write access there, it
      returns the work product inline in its completion report and the PM
      persists it with ``cartopian write-resource`` before the report is
      cleared. ``in_resources`` reports whether the path actually lands under
      ``resources/`` (``None`` for work-root mode); ``validate-task-readiness``
      blocks a project-mode deliverable that escapes it, and ``plan-audit``
      warns on legacy ones.

    Returns ``None`` for an absent / ``n/a`` / ``none`` deliverable.
    """
    value = (raw_value or "").strip()
    if value.lower() in _DELIVERABLE_SKIP:
        return None
    root, sep, relpath = value.partition(":")
    root = root.strip()
    relpath = relpath.strip()
    if not sep or not relpath:
        # No ``<root>:`` prefix — treat the whole value as project-root-relative.
        root, relpath, mode = "project", value, "project"
    elif root == "project":
        mode = "project"
    else:
        mode = "work-root"
    if mode == "project":
        base: Optional[Path] = project_path
        in_resources: Optional[bool] = _relpath_in_resources(relpath)
    else:
        base = _lookup_work_root_path(project_cfg, project_path, root)
        in_resources = None
    absolute = (base / relpath).resolve() if base is not None else None
    return {
        "logical": value,
        "mode": mode,
        "root": root,
        "relpath": relpath,
        "in_resources": in_resources,
        "absolute_path": str(absolute) if absolute is not None else None,
        "exists": absolute.exists() if absolute is not None else False,
    }


def _require_project_keys(project_cfg: Dict[str, Any], project_toml: Path) -> Tuple[str, str, str]:
    project_table = _require_project_table(project_cfg, project_toml)
    for key in ("id", "name", "protocol_version"):
        if key not in project_table:
            raise _CliError(
                EXIT_FAIL,
                "error",
                f"project config missing required key: [project].{key}",
            )
    return (
        str(project_table["id"]),
        str(project_table["name"]),
        str(project_table["protocol_version"]),
    )


def _require_startup_project_keys(
    project_cfg: Dict[str, Any], project_toml: Path
) -> Tuple[str, str, Optional[str]]:
    """Required-keys check for the session-startup surfaces (next-action,
    plan-audit): ``[project].id`` and ``[project].name`` stay mandatory, but a
    missing ``protocol_version`` is returned as ``None`` so the protocol gate
    can classify it as unset/older-but-migratable — the CHANGELOG's "unset,
    missing" case, matching installer reconciliation — instead of rejecting
    the config before the gate runs. Commands that intentionally require the
    marker keep using :func:`_require_project_keys`.
    """
    project_table = _require_project_table(project_cfg, project_toml)
    for key in ("id", "name"):
        if key not in project_table:
            raise _CliError(
                EXIT_FAIL,
                "error",
                f"project config missing required key: [project].{key}",
            )
    declared = project_table.get("protocol_version")
    return (
        str(project_table["id"]),
        str(project_table["name"]),
        None if declared is None else str(declared),
    )


def validate_effective_config(
    roles: Dict[str, str],
    handoffs: Dict[str, Dict[str, Any]],
    capabilities: GrantResolution,
) -> list:
    """Validate the *resolved* effective config (project + global merged).

    This reasons about the merged result, not any single file — a project
    handoff may validly reference a globally-declared role, and a project
    override may reveal a valid global value. Raises :class:`_CliError` on a
    blocking violation (an orphan handoff whose role is declared nowhere in the
    effective ``[roles]``). Returns a list of advisory ``(prefix, message)``
    warnings the caller may surface. Shared by ``resolve-config`` and
    ``update-config`` so both reason identically.
    """
    for role in handoffs.keys():
        if role not in roles:
            raise _CliError(
                EXIT_FAIL,
                "config",
                (
                    f"orphan-handoff: {role} — declare in [roles] or "
                    f"remove the [handoffs.{role}] block"
                ),
            )
    warnings: list = []
    for role_name, description in roles.items():
        if role_name in PROTOCOL_DEFAULT_ROLES:
            continue
        if description == "":
            warnings.append(("validation", f"empty role description: {role_name}"))
    for role_name, entries in capabilities.invalid.items():
        warnings.append(
            (
                "validation",
                (
                    f"unknown capability grants for role {role_name!r} "
                    f"(role fails closed — holds no grants): {', '.join(entries)}"
                ),
            )
        )
    return warnings


def handler(args: argparse.Namespace) -> int:
    raw_path = args.project_path
    if not Path(raw_path).is_absolute():
        _stderr("usage", f"project_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE
    try:
        project_path = Path(raw_path).resolve(strict=True)
    except FileNotFoundError:
        _stderr("error", f"project path does not exist: {raw_path}")
        return EXIT_FAIL

    try:
        project_toml = project_path / "cartopian.toml"
        project_cfg = _load_project_config(project_path)
        global_toml = Path.home() / ".cartopian" / "cartopian.toml"
        global_cfg = _load_toml(global_toml, "global config") or {}

        project_id, project_name, protocol_version = _require_project_keys(project_cfg, project_toml)

        roles_raw = _resolve_roles(global_cfg, project_cfg)
        roles = {name: role_description(value) for name, value in roles_raw.items()}
        capabilities: GrantResolution = resolve_grants(roles_raw)
        reviews = _resolve_reviews(global_cfg, project_cfg, roles_raw)
        handoffs = _resolve_handoffs(global_cfg, project_cfg)
        automation = _resolve_automation(global_cfg, project_cfg)
        work_roots = _resolve_work_roots(project_cfg, project_path)
        git_versioning, attribution = _resolve_git_versioning(global_cfg, project_cfg)
        git_block: Optional[Dict[str, Any]]
        if git_versioning:
            git_block = _resolve_git_block(global_cfg, project_cfg)
        else:
            git_block = None

        for prefix, message in validate_effective_config(roles, handoffs, capabilities):
            _stderr(prefix, message)
    except _CliError as err:
        _stderr(err.prefix, err.message)
        return err.exit_code

    record = {
        "project_id": project_id,
        "project_name": project_name,
        "project_path": str(project_path),
        "protocol_version": protocol_version,
        "roles": roles,
        "capabilities": {
            "activated": capabilities.activated,
            "role_grants": {
                name: sorted(grants)
                for name, grants in capabilities.role_grants.items()
            },
        },
        "handoffs": handoffs,
        "reviews": reviews,
        "automation": automation,
        "work_roots": work_roots,
        "git_versioning": git_versioning,
        "git": git_block,
        "defaults_attribution": {"git_versioning": attribution},
    }
    emit_record(record)
    return EXIT_OK
