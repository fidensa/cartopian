"""`cartopian resolve-config <project-path>` implementation."""
import argparse
import os
import sys
import tomllib
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from cli.config_schema import (
    ConfigDiagnostic,
    resolve_configuration,
)
from cli.emit import emit_record
from cli.main import EXIT_ENV, EXIT_FAIL, EXIT_OK, EXIT_USAGE
from cli.protocol_gate import (
    GATE_BLOCKED,
    classify_project_schema_version,
    read_shipped_project_schema_version,
)
from cli.version_identities import version_identities

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


def resolve_project_configuration(
    project_path: Path,
    *,
    project_cfg_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Load all authored scopes and return the one canonical resolved record."""
    project_cfg = (
        project_cfg_override
        if project_cfg_override is not None
        else _load_project_config(project_path)
    )
    project_table = project_cfg.get("project", {})
    declared_schema = (
        project_table.get("project_schema_version")
        if isinstance(project_table, dict)
        else None
    )
    try:
        shipped_schema = read_shipped_project_schema_version()
    except (OSError, RuntimeError) as exc:
        raise _CliError(
            EXIT_ENV,
            "error",
            f"shipped project schema unreadable: {exc}",
        ) from exc
    gate = classify_project_schema_version(declared_schema, shipped_schema)
    if gate["status"] != "current":
        raise _CliError(EXIT_FAIL, "schema-gate", gate["detail"])
    global_cfg = _load_toml(
        Path.home() / ".cartopian" / "cartopian.toml", "global config"
    ) or {}
    local_cfg = _load_toml(
        project_path / "cartopian.local.toml", "local config"
    ) or {}
    try:
        record = resolve_configuration(global_cfg, project_cfg, local_cfg)
    except ConfigDiagnostic as exc:
        raise _CliError(EXIT_FAIL, "config", str(exc)) from exc
    record["project_path"] = str(project_path)
    return record


def resolve_review_policy(project_path: Path) -> Dict[str, Dict[str, Any]]:
    """Load and resolve one project's review policy for lifecycle commands."""
    return resolve_project_configuration(project_path)["reviews"]


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
    for key in ("id", "name", "project_schema_version"):
        if key not in project_table:
            raise _CliError(
                EXIT_FAIL,
                "error",
                f"project config missing required key: [project].{key}",
            )
    return (
        str(project_table["id"]),
        str(project_table["name"]),
        str(project_table["project_schema_version"]),
    )


def _require_startup_project_keys(
    project_cfg: Dict[str, Any], project_toml: Path
) -> Tuple[str, str, Optional[str]]:
    """Required-keys check for the session-startup surfaces (next-action,
    plan-audit): ``[project].id`` and ``[project].name`` stay mandatory, but a
    missing ``project_schema_version`` is returned as ``None`` so the schema gate
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
    declared = project_table.get("project_schema_version")
    return (
        str(project_table["id"]),
        str(project_table["name"]),
        None if declared is None else str(declared),
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
        local_cfg = _load_toml(
            project_path / "cartopian.local.toml", "local config"
        ) or {}
        record = resolve_configuration(global_cfg, project_cfg, local_cfg)
        shipped_schema = read_shipped_project_schema_version()
        schema_gate = classify_project_schema_version(
            record["project_schema_version"], shipped_schema
        )
        if schema_gate["status"] == GATE_BLOCKED:
            raise ConfigDiagnostic(
                "unsupported-schema-version",
                "project.project_schema_version",
                "project",
                schema_gate["detail"],
                "upgrade-cartopian-or-repair-schema-marker",
            )
        record["version_identities"] = version_identities(
            Path(__file__).resolve().parents[2],
            project_schema={
                "value": record["project_schema_version"],
                "target": shipped_schema,
                "state": (
                    "current"
                    if schema_gate["status"] == "current"
                    else "older"
                ),
                "authority": "project-config-and-shipped-schema",
                "verification": (
                    "verified"
                    if schema_gate["status"] == "current"
                    else "migration-required"
                ),
                "attribution": "project",
            },
        )
        record["project_path"] = str(project_path)
    except (OSError, RuntimeError) as err:
        _stderr("error", str(err))
        return EXIT_ENV
    except ConfigDiagnostic as err:
        _stderr("config", str(err))
        return EXIT_FAIL
    except _CliError as err:
        _stderr(err.prefix, err.message)
        return err.exit_code

    emit_record(record)
    return EXIT_OK
