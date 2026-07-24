"""Closed Cartopian configuration, resolution, and identity contract.

This module is the executable authority for preferred-form configuration.
Migration-source names are inventoried here so migration tooling can recognize
them, but :func:`validate_authored_config` deliberately rejects them during
normal parsing.
"""
from __future__ import annotations

import os
import re
from collections import OrderedDict
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from cli.capabilities import is_known_grant_name, resolve_grants

SCOPES: Tuple[str, ...] = ("global", "project", "machine-local")
PRECEDENCE: Tuple[str, ...] = (
    "protocol-default",
    "global",
    "project",
    "machine-local",
)
ATTRIBUTION_VALUES: Tuple[str, ...] = PRECEDENCE + ("derived",)
AUTO_LAUNCH_ACTIVITIES: Tuple[str, ...] = (
    "task_run",
    "task_review",
    "planning_review",
)
MIGRATION_SOURCE_ALIASES: Tuple[str, ...] = (
    "project.protocol_version",
    "handoffs",
    "handoffs.*",
    "handoffs.*.auto_start",
    "handoffs.*.auto_start_tasks",
    "handoffs.*.auto_start_reviews",
)

ROLE_DEFAULTS: "OrderedDict[str, str]" = OrderedDict(
    (
        ("pm", "Manages the project lifecycle and orchestrates handoffs."),
        (
            "operator",
            "Human direction-setter who approves transitions and resolves judgment calls.",
        ),
    )
)
AUTOMATION_DEFAULTS: "OrderedDict[str, Any]" = OrderedDict(
    (
        ("initiation", "operator"),
        ("confirmation", "each-handoff"),
        ("max_handoffs_per_run", 1),
    )
)
REVIEW_DEFAULTS: "OrderedDict[str, str]" = OrderedDict(
    (("planning", "off"), ("task_closure", "off"))
)
GIT_VERSIONING_DEFAULT = False

IDENTITY_CONTRACT: "OrderedDict[str, Dict[str, Any]]" = OrderedDict(
    (
        (
            "release_version",
            {
                "authority": "maintainer-release-metadata",
                "states": ("known", "unknown"),
                "substitutes_for": (),
            },
        ),
        (
            "installed_content",
            {
                "authority": "installed-or-materialized-content",
                "required_facts": (
                    "revision",
                    "materialization",
                    "verification",
                ),
                "states": (
                    "verified",
                    "unknown",
                    "dirty",
                    "symlink-divergent",
                    "unverified",
                ),
                "substitutes_for": (),
            },
        ),
        (
            "project_schema_version",
            {
                "authority": "project-config-and-shipped-schema",
                "states": ("current", "older", "unsupported", "malformed"),
                "sole_project_migration_gate": True,
                "substitutes_for": (),
            },
        ),
        (
            "running_server",
            {
                "authority": "connected-mcp-process",
                "required_facts": ("process_id", "loaded_content"),
                "states": ("current", "stale-runtime", "unknown"),
                "substitutes_for": (),
            },
        ),
        (
            "mcp_protocol_version",
            {
                "authority": "mcp-wire-handshake",
                "states": ("supported", "unsupported"),
                "substitutes_for": (),
            },
        ),
    )
)


def identity_contract() -> "OrderedDict[str, Dict[str, Any]]":
    """Return the deterministic peer-identity vocabulary and authorities."""
    return OrderedDict((key, dict(value)) for key, value in IDENTITY_CONTRACT.items())


# The public metadata object intentionally uses primitive immutable values so it
# is easy for CLI/MCP parity tests and later surface generation to inspect.
CONFIG_SCHEMA: Dict[str, Any] = {
    "schema_identity": "cartopian-authoritative-config-v1",
    "scopes": SCOPES,
    "precedence": PRECEDENCE,
    "attribution_values": ATTRIBUTION_VALUES,
    "migration_source_aliases": MIGRATION_SOURCE_ALIASES,
    "auto_launch": {
        "type": "closed-list",
        "values": AUTO_LAUNCH_ACTIVITIES,
        "default": (),
        "merge": "replace",
    },
    "preferred_output": (
        "schema_identity",
        "project_id",
        "project_name",
        "project_schema_version",
        "roles",
        "capabilities",
        "reviews",
        "automation",
        "work_roots",
        "work_roots_attribution",
        "git_versioning",
        "git",
        "defaults_attribution",
    ),
    "role_output": (
        "description",
        "effective_grants",
        "assigned_work_types",
        "launch",
        "auto_launch",
        "attribution",
    ),
    "fields": OrderedDict(
        (
            (
                "project.id",
                {
                    "scopes": ("project",),
                    "type": "kebab-case-string",
                    "required": True,
                },
            ),
            (
                "project.name",
                {
                    "scopes": ("project",),
                    "type": "non-empty-string",
                    "required": True,
                },
            ),
            (
                "project.project_schema_version",
                {
                    "scopes": ("project",),
                    "type": "vX.Y.Z",
                    "required": True,
                    "authority": "project-migration-gate",
                },
            ),
            (
                "project.work_roots",
                {
                    "scopes": ("project",),
                    "type": "unique-name-list",
                    "default": (),
                    "merge": "project-only",
                },
            ),
            (
                "roles.*.description",
                {
                    "scopes": ("global", "project"),
                    "type": "non-empty-string",
                    "merge": "field-override",
                },
            ),
            (
                "roles.*.grants",
                {
                    "scopes": ("global", "project"),
                    "type": "closed-grant-list",
                    "merge": "replace",
                },
            ),
            (
                "roles.*.launch.target",
                {
                    "scopes": ("global", "project"),
                    "type": "non-empty-string",
                    "merge": "field-override",
                },
            ),
            (
                "roles.*.launch.model",
                {
                    "scopes": ("global", "project"),
                    "type": "non-empty-string",
                    "merge": "field-override",
                },
            ),
            (
                "roles.*.launch.effort",
                {
                    "scopes": ("global", "project"),
                    "type": "non-empty-string",
                    "merge": "field-override",
                },
            ),
            (
                "roles.*.launch.timeout",
                {
                    "scopes": ("global", "project"),
                    "type": "positive-duration",
                    "merge": "field-override",
                },
            ),
            (
                "roles.*.auto_launch",
                {
                    "scopes": ("global", "project"),
                    "type": "closed-unique-list",
                    "values": AUTO_LAUNCH_ACTIVITIES,
                    "default": (),
                    "merge": "replace",
                },
            ),
            (
                "reviews.planning",
                {
                    "scopes": ("global", "project"),
                    "type": "enum",
                    "values": ("required", "off"),
                    "default": "off",
                    "merge": "field-override",
                },
            ),
            (
                "reviews.planning_role",
                {
                    "scopes": ("global", "project"),
                    "type": "role-reference",
                    "merge": "field-override",
                },
            ),
            (
                "reviews.task_closure",
                {
                    "scopes": ("global", "project"),
                    "type": "enum",
                    "values": ("required", "off"),
                    "default": "off",
                    "merge": "field-override",
                },
            ),
            (
                "reviews.task_role",
                {
                    "scopes": ("global", "project"),
                    "type": "role-reference",
                    "merge": "field-override",
                },
            ),
            (
                "automation.initiation",
                {
                    "scopes": ("global", "project"),
                    "type": "enum",
                    "values": ("operator", "auto"),
                    "default": "operator",
                    "merge": "field-override",
                },
            ),
            (
                "automation.confirmation",
                {
                    "scopes": ("global", "project"),
                    "type": "enum",
                    "values": ("each-handoff", "until-blocked"),
                    "default": "each-handoff",
                    "merge": "field-override",
                },
            ),
            (
                "automation.max_handoffs_per_run",
                {
                    "scopes": ("global", "project"),
                    "type": "positive-integer",
                    "default": 1,
                    "merge": "field-override",
                },
            ),
            (
                "defaults.git_versioning",
                {
                    "scopes": ("global", "project"),
                    "type": "boolean",
                    "default": False,
                    "merge": "field-override",
                },
            ),
            (
                "git.pm_owns_product_branches",
                {
                    "scopes": ("global", "project"),
                    "type": "boolean",
                    "default": False,
                    "merge": "field-override",
                },
            ),
            (
                "git.default_branch_pattern",
                {
                    "scopes": ("global", "project"),
                    "type": "non-empty-string",
                    "default": "task/{task_id}-{slug}",
                    "merge": "field-override",
                },
            ),
            (
                "git.default_merge_strategy",
                {
                    "scopes": ("global", "project"),
                    "type": "enum",
                    "values": ("merge", "squash", "rebase"),
                    "default": "merge",
                    "merge": "field-override",
                },
            ),
            (
                "work_roots.*",
                {
                    "scopes": ("machine-local",),
                    "type": "absolute-path",
                    "merge": "declared-name-map",
                },
            ),
        )
    ),
    "identities": IDENTITY_CONTRACT,
}

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_VERSION_RE = re.compile(r"^v\d+\.\d+\.\d+$")
_DURATION_RE = re.compile(r"^([1-9]\d*)([smh])$")
_ROOT_KEYS = {
    "global": frozenset(("defaults", "automation", "roles", "reviews", "git")),
    "project": frozenset(
        ("project", "defaults", "automation", "roles", "reviews", "git")
    ),
    "machine-local": frozenset(("work_roots",)),
}
_ROLE_KEYS = frozenset(("description", "grants", "launch", "auto_launch"))
_LAUNCH_KEYS = frozenset(("target", "model", "effort", "timeout"))
_REVIEW_KEYS = frozenset(
    ("planning", "planning_role", "task_closure", "task_role")
)
_AUTOMATION_KEYS = frozenset(
    ("initiation", "confirmation", "max_handoffs_per_run")
)
_DEFAULT_KEYS = frozenset(("git_versioning",))
_GIT_KEYS = frozenset(
    ("pm_owns_product_branches", "default_branch_pattern", "default_merge_strategy")
)
_PROJECT_KEYS = frozenset(("id", "name", "project_schema_version", "work_roots"))


class ConfigDiagnostic(ValueError):
    """Stable fail-closed configuration diagnostic."""

    def __init__(
        self,
        code: str,
        field: str,
        scope: str,
        message: str,
        recovery: str,
    ) -> None:
        self.code = code
        self.field = field
        self.scope = scope
        self.classification = "invalid-configuration"
        self.recovery = recovery
        self.message = message
        super().__init__(
            f"{code}: {scope}:{field}: {message}; recovery={recovery}"
        )

    def as_record(self) -> Dict[str, str]:
        return {
            "code": self.code,
            "field": self.field,
            "scope": self.scope,
            "classification": self.classification,
            "recovery": self.recovery,
            "message": self.message,
        }


def _fail(
    code: str,
    field: str,
    scope: str,
    message: str,
    recovery: str = "repair-authored-config",
) -> None:
    raise ConfigDiagnostic(code, field, scope, message, recovery)


def _require_table(value: Any, field: str, scope: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        _fail("invalid-type", field, scope, "must be a table")
    return value


def _unknown_keys(
    table: Mapping[str, Any],
    allowed: Sequence[str],
    prefix: str,
    scope: str,
) -> None:
    for key in table:
        field = f"{prefix}.{key}" if prefix else key
        if key not in allowed:
            if (
                field == "project.protocol_version"
                or field == "handoffs"
                or field.startswith("handoffs.")
            ):
                _fail(
                    "migration-source-only",
                    field,
                    scope,
                    "legacy vocabulary is accepted only by migration tooling",
                    "run-approved-config-migration",
                )
            if not prefix and key == "work_roots" and scope != "machine-local":
                _fail(
                    "scope",
                    field,
                    scope,
                    "absolute work-root mappings belong only to machine-local config",
                    "move-mapping-to-cartopian.local.toml",
                )
            _fail(
                "unknown-key",
                field,
                scope,
                "key is outside the closed preferred schema",
            )


def _nonempty_string(value: Any, field: str, scope: str) -> None:
    if not isinstance(value, str) or not value.strip():
        _fail("invalid-type", field, scope, "must be a non-empty string")


def _validate_roles(raw: Any, scope: str) -> None:
    roles = _require_table(raw, "roles", scope)
    for name, value in roles.items():
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
            _fail(
                "invalid-role-name",
                f"roles.{name}",
                scope,
                "role name must match [A-Za-z0-9_-]+",
            )
        field = f"roles.{name}"
        role = _require_table(value, field, scope)
        _unknown_keys(role, _ROLE_KEYS, field, scope)
        if "description" in role:
            _nonempty_string(role["description"], f"{field}.description", scope)
        if "grants" in role:
            grants = role["grants"]
            if not isinstance(grants, list):
                _fail("invalid-type", f"{field}.grants", scope, "must be a list")
            seen = set()
            for grant in grants:
                if not is_known_grant_name(grant):
                    _fail(
                        "unknown-value",
                        f"{field}.grants",
                        scope,
                        f"unknown capability or preset {grant!r}",
                    )
                if grant in seen:
                    _fail(
                        "duplicate-value",
                        f"{field}.grants",
                        scope,
                        f"duplicate grant {grant!r}",
                    )
                seen.add(grant)
        if "auto_launch" in role:
            activities = role["auto_launch"]
            if not isinstance(activities, list):
                _fail(
                    "invalid-type",
                    f"{field}.auto_launch",
                    scope,
                    "must be a list",
                )
            seen = set()
            for activity in activities:
                if activity not in AUTO_LAUNCH_ACTIVITIES:
                    _fail(
                        "unknown-value",
                        f"{field}.auto_launch",
                        scope,
                        f"allowed values are {', '.join(AUTO_LAUNCH_ACTIVITIES)}; "
                        f"got {activity!r}",
                    )
                if activity in seen:
                    _fail(
                        "duplicate-value",
                        f"{field}.auto_launch",
                        scope,
                        f"duplicate activity {activity!r}",
                    )
                seen.add(activity)
        if "launch" in role:
            launch = _require_table(role["launch"], f"{field}.launch", scope)
            _unknown_keys(launch, _LAUNCH_KEYS, f"{field}.launch", scope)
            for key, launch_value in launch.items():
                _nonempty_string(launch_value, f"{field}.launch.{key}", scope)
            if "timeout" in launch and not _DURATION_RE.fullmatch(
                launch["timeout"]
            ):
                _fail(
                    "invalid-timeout",
                    f"{field}.launch.timeout",
                    scope,
                    "must be a positive duration with s, m, or h suffix",
                )


def _validate_reviews(raw: Any, scope: str) -> None:
    reviews = _require_table(raw, "reviews", scope)
    _unknown_keys(reviews, _REVIEW_KEYS, "reviews", scope)
    for key in ("planning", "task_closure"):
        if key in reviews and reviews[key] not in ("required", "off"):
            _fail(
                "unknown-value",
                f"reviews.{key}",
                scope,
                "must be one of required, off",
            )
    for key in ("planning_role", "task_role"):
        if key in reviews:
            _nonempty_string(reviews[key], f"reviews.{key}", scope)


def _validate_automation(raw: Any, scope: str) -> None:
    automation = _require_table(raw, "automation", scope)
    _unknown_keys(automation, _AUTOMATION_KEYS, "automation", scope)
    if "initiation" in automation and automation["initiation"] not in (
        "operator",
        "auto",
    ):
        _fail(
            "unknown-value",
            "automation.initiation",
            scope,
            "must be one of operator, auto",
        )
    if "confirmation" in automation and automation["confirmation"] not in (
        "each-handoff",
        "until-blocked",
    ):
        _fail(
            "unknown-value",
            "automation.confirmation",
            scope,
            "must be one of each-handoff, until-blocked",
        )
    if "max_handoffs_per_run" in automation:
        value = automation["max_handoffs_per_run"]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            _fail(
                "invalid-type",
                "automation.max_handoffs_per_run",
                scope,
                "must be a positive integer",
            )


def validate_authored_config(config: Mapping[str, Any], scope: str) -> None:
    """Validate one authored scope against the preferred closed contract."""
    if scope not in SCOPES:
        _fail("scope", "<root>", str(scope), f"unknown authored scope {scope!r}")
    if not isinstance(config, dict):
        _fail("invalid-type", "<root>", scope, "configuration must be a table")
    _unknown_keys(config, _ROOT_KEYS[scope], "", scope)

    if scope == "project":
        if "project" not in config:
            _fail(
                "missing-required",
                "project",
                scope,
                "project configuration requires a [project] table",
            )
        project = _require_table(config["project"], "project", scope)
        _unknown_keys(project, _PROJECT_KEYS, "project", scope)
        for key in ("id", "name", "project_schema_version"):
            if key not in project:
                _fail(
                    "missing-required",
                    f"project.{key}",
                    scope,
                    "required preferred-form field is absent",
                )
        _nonempty_string(project["id"], "project.id", scope)
        if not _ID_RE.fullmatch(project["id"]):
            _fail(
                "invalid-value",
                "project.id",
                scope,
                "must be kebab-case [a-z0-9][a-z0-9-]*",
            )
        _nonempty_string(project["name"], "project.name", scope)
        version = project["project_schema_version"]
        if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
            _fail(
                "malformed-schema-version",
                "project.project_schema_version",
                scope,
                "must be a vX.Y.Z string",
                "repair-schema-marker",
            )
        roots = project.get("work_roots", [])
        if not isinstance(roots, list):
            _fail(
                "invalid-type",
                "project.work_roots",
                scope,
                "must be a list of unique names",
            )
        seen = set()
        for name in roots:
            if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
                _fail(
                    "invalid-value",
                    "project.work_roots",
                    scope,
                    f"invalid work-root name {name!r}",
                )
            if name in seen:
                _fail(
                    "duplicate-value",
                    "project.work_roots",
                    scope,
                    f"duplicate work-root name {name!r}",
                )
            seen.add(name)

    if scope == "machine-local":
        mappings = _require_table(config.get("work_roots", {}), "work_roots", scope)
        for name, path in mappings.items():
            if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
                _fail(
                    "invalid-value",
                    f"work_roots.{name}",
                    scope,
                    "mapping name must match [A-Za-z0-9_-]+",
                )
            if not isinstance(path, str) or not os.path.isabs(path):
                _fail(
                    "absolute-path",
                    f"work_roots.{name}",
                    scope,
                    "machine-local work-root mapping must be an absolute path",
                )

    if "roles" in config:
        _validate_roles(config["roles"], scope)
    if "reviews" in config:
        _validate_reviews(config["reviews"], scope)
    if "automation" in config:
        _validate_automation(config["automation"], scope)
    if "defaults" in config:
        defaults = _require_table(config["defaults"], "defaults", scope)
        _unknown_keys(defaults, _DEFAULT_KEYS, "defaults", scope)
        if "git_versioning" in defaults and not isinstance(
            defaults["git_versioning"], bool
        ):
            _fail(
                "invalid-type",
                "defaults.git_versioning",
                scope,
                "must be a boolean",
            )
    if "git" in config:
        git = _require_table(config["git"], "git", scope)
        _unknown_keys(git, _GIT_KEYS, "git", scope)
        if "pm_owns_product_branches" in git and not isinstance(
            git["pm_owns_product_branches"], bool
        ):
            _fail(
                "invalid-type",
                "git.pm_owns_product_branches",
                scope,
                "must be a boolean",
            )
        for key in ("default_branch_pattern", "default_merge_strategy"):
            if key in git:
                _nonempty_string(git[key], f"git.{key}", scope)
        if (
            "default_merge_strategy" in git
            and git["default_merge_strategy"] not in ("merge", "squash", "rebase")
        ):
            _fail(
                "unknown-value",
                "git.default_merge_strategy",
                scope,
                "must be one of merge, squash, rebase",
            )


def _field_value(
    global_table: Mapping[str, Any],
    project_table: Mapping[str, Any],
    key: str,
    default: Any,
) -> Tuple[Any, str]:
    if key in project_table:
        return project_table[key], "project"
    if key in global_table:
        return global_table[key], "global"
    return default, "protocol-default"


def _merge_role(
    name: str,
    global_role: Optional[Mapping[str, Any]],
    project_role: Optional[Mapping[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    g = global_role or {}
    p = project_role or {}
    merged: Dict[str, Any] = {}
    attribution: Dict[str, Any] = {}
    default_description = ROLE_DEFAULTS.get(name)
    description, source = _field_value(g, p, "description", default_description or "")
    merged["description"] = description
    attribution["description"] = source

    if "grants" in p:
        merged["grants"] = list(p["grants"])
        attribution["grants"] = "project"
    elif "grants" in g:
        merged["grants"] = list(g["grants"])
        attribution["grants"] = "global"
    attribution.setdefault("grants", "protocol-default")

    auto_launch, source = _field_value(g, p, "auto_launch", [])
    merged["auto_launch"] = list(auto_launch)
    attribution["auto_launch"] = source

    g_launch = g.get("launch", {})
    p_launch = p.get("launch", {})
    launch: Dict[str, Any] = {}
    launch_sources: Dict[str, str] = {}
    for key in ("target", "model", "effort", "timeout"):
        value, launch_source = _field_value(g_launch, p_launch, key, None)
        launch[key] = value
        launch_sources[key] = launch_source
    merged["launch"] = launch
    attribution["launch"] = launch_sources
    return merged, attribution


def _resolve_reviews(
    global_cfg: Mapping[str, Any],
    project_cfg: Mapping[str, Any],
    role_names: Sequence[str],
) -> Dict[str, Dict[str, Any]]:
    g = global_cfg.get("reviews", {})
    p = project_cfg.get("reviews", {})
    result: Dict[str, Dict[str, Any]] = OrderedDict()
    for work_type, mode_key, role_key in (
        ("planning_review", "planning", "planning_role"),
        ("task_review", "task_closure", "task_role"),
    ):
        mode, mode_source = _field_value(g, p, mode_key, REVIEW_DEFAULTS[mode_key])
        role, role_source = _field_value(g, p, role_key, None)
        if mode == "required":
            if role is None:
                _fail(
                    "missing-reference",
                    f"reviews.{role_key}",
                    "resolved",
                    f"reviews.{mode_key} = 'required' requires an assigned role",
                )
            if role not in role_names:
                _fail(
                    "orphan-reference",
                    f"reviews.{role_key}",
                    "resolved",
                    f"names undeclared role {role!r}",
                )
        result[mode_key] = {
            "work_type": work_type,
            "mode": mode,
            "role": role if mode == "required" else None,
            "attribution": {
                "mode": mode_source,
                "role": role_source if mode == "required" else None,
            },
        }
    return result


def resolve_configuration(
    global_cfg: Mapping[str, Any],
    project_cfg: Mapping[str, Any],
    local_cfg: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate, merge, attribute, and emit the canonical resolved record."""
    validate_authored_config(global_cfg, "global")
    validate_authored_config(project_cfg, "project")
    local = local_cfg or {}
    validate_authored_config(local, "machine-local")

    project = project_cfg["project"]
    g_roles = global_cfg.get("roles", {})
    p_roles = project_cfg.get("roles", {})
    ordered_role_names = list(g_roles)
    ordered_role_names.extend(name for name in p_roles if name not in g_roles)
    for name in ROLE_DEFAULTS:
        if name not in ordered_role_names:
            ordered_role_names.append(name)

    merged_roles: Dict[str, Dict[str, Any]] = OrderedDict()
    role_attribution: Dict[str, Dict[str, Any]] = {}
    raw_for_capabilities: Dict[str, Dict[str, Any]] = {}
    for name in ordered_role_names:
        merged, attribution = _merge_role(
            name, g_roles.get(name), p_roles.get(name)
        )
        if not merged["description"]:
            _fail(
                "missing-field",
                f"roles.{name}.description",
                "resolved",
                "every resolved user-defined role requires a non-empty description",
            )
        if name == "pm" and merged["launch"]["target"] is not None:
            _fail(
                "pm-launch-forbidden",
                "roles.pm.launch.target",
                "resolved",
                "the interactive PM role cannot be a launch target",
            )
        if name == "pm" and merged["auto_launch"]:
            _fail(
                "pm-auto-launch-forbidden",
                "roles.pm.auto_launch",
                "resolved",
                "the interactive PM role cannot declare automatic-launch activity",
                "remove-pm-auto-launch",
            )
        merged_roles[name] = merged
        role_attribution[name] = attribution
        raw: Dict[str, Any] = {"description": merged["description"]}
        if "grants" in merged:
            raw["grants"] = merged["grants"]
        raw_for_capabilities[name] = raw

    capabilities = resolve_grants(raw_for_capabilities)
    if capabilities.invalid:
        role, values = next(iter(capabilities.invalid.items()))
        _fail(
            "unknown-value",
            f"roles.{role}.grants",
            "resolved",
            f"invalid grants: {', '.join(values)}",
        )

    reviews = _resolve_reviews(
        global_cfg, project_cfg, tuple(merged_roles)
    )

    for name, role in merged_roles.items():
        assigned = []
        assigned_sources: Dict[str, str] = OrderedDict()
        # Ordinary task assignment remains task-artifact/PM authority. A
        # configured non-PM launch target makes the role applicable to that
        # work type without selecting any task or assigning the role.
        if name != "pm" and role["launch"]["target"] is not None:
            assigned.append("task_run")
            assigned_sources["task_run"] = "derived"
        if reviews["task_closure"]["role"] == name:
            assigned.append("task_review")
            assigned_sources["task_review"] = reviews["task_closure"][
                "attribution"
            ]["role"]
        if reviews["planning"]["role"] == name:
            assigned.append("planning_review")
            assigned_sources["planning_review"] = reviews["planning"][
                "attribution"
            ]["role"]
        for permission in role["auto_launch"]:
            if permission not in assigned:
                _fail(
                    "inapplicable-permission",
                    f"roles.{name}.auto_launch",
                    "resolved",
                    f"{permission!r} is not assigned/applicable to role {name!r}",
                    "assign-work-type-or-remove-permission",
                )
        attribution = role_attribution[name]
        attribution["assigned_work_types"] = assigned_sources
        attribution["effective_grants"] = "derived"
        merged_roles[name] = OrderedDict(
            (
                ("description", role["description"]),
                (
                    "effective_grants",
                    sorted(capabilities.role_grants[name]),
                ),
                ("assigned_work_types", assigned),
                ("launch", role["launch"]),
                ("auto_launch", role["auto_launch"]),
                ("attribution", attribution),
            )
        )

    g_auto = global_cfg.get("automation", {})
    p_auto = project_cfg.get("automation", {})
    automation: Dict[str, Any] = OrderedDict()
    automation_sources: Dict[str, str] = OrderedDict()
    for key, default in AUTOMATION_DEFAULTS.items():
        value, source = _field_value(g_auto, p_auto, key, default)
        automation[key] = value
        automation_sources[key] = source
    automation["attribution"] = automation_sources

    g_defaults = global_cfg.get("defaults", {})
    p_defaults = project_cfg.get("defaults", {})
    git_versioning, git_source = _field_value(
        g_defaults, p_defaults, "git_versioning", GIT_VERSIONING_DEFAULT
    )
    g_git = global_cfg.get("git", {})
    p_git = project_cfg.get("git", {})
    git: Optional[Dict[str, Any]] = None
    git_attribution: Optional[Dict[str, str]] = None
    if git_versioning:
        git = OrderedDict()
        git_attribution = OrderedDict()
        for key, default in (
            ("pm_owns_product_branches", False),
            ("default_branch_pattern", "task/{task_id}-{slug}"),
            ("default_merge_strategy", "merge"),
        ):
            value, source = _field_value(g_git, p_git, key, default)
            git[key] = value
            git_attribution[key] = source

    declared_roots = project.get("work_roots", [])
    mappings = local.get("work_roots", {})
    for name in mappings:
        if name not in declared_roots:
            _fail(
                "orphan-reference",
                f"work_roots.{name}",
                "machine-local",
                "mapping has no project work-root declaration",
                "declare-name-or-remove-mapping",
            )
    resolved_roots: Dict[str, str] = OrderedDict()
    roots_attribution: Dict[str, Dict[str, str]] = OrderedDict()
    for name in declared_roots:
        if name not in mappings:
            _fail(
                "missing-reference",
                f"work_roots.{name}",
                "machine-local",
                "declared work root has no machine-local mapping",
                "add-machine-local-mapping",
            )
        resolved_roots[name] = mappings[name]
        roots_attribution[name] = {
            "declaration": "project",
            "mapping": "machine-local",
        }

    return OrderedDict(
        (
            ("schema_identity", CONFIG_SCHEMA["schema_identity"]),
            ("project_id", project["id"]),
            ("project_name", project["name"]),
            ("project_schema_version", project["project_schema_version"]),
            ("roles", merged_roles),
            (
                "capabilities",
                {
                    "activated": capabilities.activated,
                    "attribution": "derived",
                },
            ),
            ("reviews", reviews),
            ("automation", automation),
            ("work_roots", resolved_roots),
            ("work_roots_attribution", roots_attribution),
            ("git_versioning", git_versioning),
            ("git", git),
            (
                "defaults_attribution",
                {"git_versioning": git_source, "git": git_attribution},
            ),
        )
    )
