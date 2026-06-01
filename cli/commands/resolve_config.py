"""`cartopian resolve-config <project-path>` implementation (FR-011, SPEC-01-001)."""
import argparse
import sys
import tomllib
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from cli.commands._advisory_gate import evaluate_advisory_gate
from cli.commands._containment import (
    contained_pm_owned_git_block_message,
    pm_is_contained,
    resolve_pm_owns_product_branches,
)
from cli.emit import emit_record
from cli.main import EXIT_ENV, EXIT_FAIL, EXIT_OK, EXIT_USAGE

PROTOCOL_DEFAULT_ROLES: Dict[str, str] = {
    "pm": "Manages the project lifecycle and orchestrates handoffs.",
    "operator": "Human direction-setter who approves transitions and resolves judgment calls.",
}

PROTOCOL_DEFAULT_AUTOMATION: Dict[str, Any] = {
    "confirmation": "each-handoff",
    "max_handoffs_per_run": 1,
}

PROTOCOL_DEFAULT_GIT_VERSIONING: bool = False


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
        merged[role] = {
            "agent": block.get("agent"),
            "auto_start": block.get("auto_start"),
            "timeout": block.get("timeout"),
        }
    return merged


def _resolve_roles(
    global_cfg: Dict[str, Any], project_cfg: Dict[str, Any]
) -> Dict[str, str]:
    g_roles = global_cfg.get("roles", {}) or {}
    p_roles = project_cfg.get("roles", {}) or {}
    if not g_roles and not p_roles:
        return dict(PROTOCOL_DEFAULT_ROLES)
    merged = _merge_table(g_roles, p_roles)
    for key, default in PROTOCOL_DEFAULT_ROLES.items():
        if key not in merged:
            merged[key] = default
    return merged


def _resolve_automation(
    global_cfg: Dict[str, Any], project_cfg: Dict[str, Any]
) -> Dict[str, Any]:
    merged = dict(PROTOCOL_DEFAULT_AUTOMATION)
    g_auto = global_cfg.get("automation", {}) or {}
    p_auto = project_cfg.get("automation", {}) or {}
    merged.update(g_auto)
    merged.update(p_auto)
    return {
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
                    f"cartopian.local.toml must use absolute paths (DEC-003)"
                ),
            )
        resolved[name] = str(candidate)
    return resolved


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

        # FR-013 fail-closed guard (P01-BUILD-006): a contained PM cannot honor
        # git.pm_owns_product_branches=true (no shell for git/gh; mediated-git
        # deferred, RM-004). Refuse before any lifecycle data is resolved/emitted.
        guard_msg = contained_pm_owned_git_block_message(
            resolve_pm_owns_product_branches(global_cfg, project_cfg),
            pm_is_contained(),
        )
        if guard_msg is not None:
            raise _CliError(EXIT_FAIL, "guard", guard_msg)

        project_id, project_name, protocol_version = _require_project_keys(project_cfg, project_toml)

        # FR-008 advisory-tier gate (P02-BUILD-001): when the PM harness cannot
        # be constrained to Tier 1/2 (TASK-02-001 → tier-3) and no valid
        # operator acknowledgment exists for (harness, project), refuse launch /
        # lifecycle entry fail-closed before any config is emitted. With a valid
        # acknowledgment, proceed under a persistent per-session advisory banner
        # and do not re-prompt. Tier-1/2 (or no configured harness) is unaffected.
        advisory = evaluate_advisory_gate(project_path, project_id)
        if advisory.blocked:
            raise _CliError(EXIT_FAIL, "guard", advisory.detail)
        if advisory.advisory:
            _stderr("advisory", advisory.advisory)

        roles = _resolve_roles(global_cfg, project_cfg)
        handoffs = _resolve_handoffs(global_cfg, project_cfg)
        automation = _resolve_automation(global_cfg, project_cfg)
        work_roots = _resolve_work_roots(project_cfg, project_path)
        git_versioning, attribution = _resolve_git_versioning(global_cfg, project_cfg)
        git_block: Optional[Dict[str, Any]]
        if git_versioning:
            git_block = _resolve_git_block(global_cfg, project_cfg)
        else:
            git_block = None

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

        for role_name, description in roles.items():
            if role_name in PROTOCOL_DEFAULT_ROLES:
                continue
            if description == "":
                sys.stderr.write(
                    f"[validation] empty role description: {role_name}\n"
                )
    except _CliError as err:
        _stderr(err.prefix, err.message)
        return err.exit_code

    record = {
        "project_id": project_id,
        "project_name": project_name,
        "project_path": str(project_path),
        "protocol_version": protocol_version,
        "roles": roles,
        "handoffs": handoffs,
        "automation": automation,
        "work_roots": work_roots,
        "git_versioning": git_versioning,
        "git": git_block,
        "defaults_attribution": {"git_versioning": attribution},
    }
    emit_record(record)
    return EXIT_OK
