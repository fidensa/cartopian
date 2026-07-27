"""Deterministic migration to Cartopian's preferred configuration contract.

The planner accepts only Cartopian's three owned configuration locations:
the operator-global file, a governed project's committed file, and that
project's machine-local file.  It converts supported authored vocabulary to
the authoritative schema in :mod:`cli.config_schema`, validates semantic
equivalence with the normal resolver, and emits a closed sequence of writes.

Execution accepts only a plan produced by this module.  Every individual file
write is atomic and content-pinned.  Multi-file progress is recorded beneath
the governed project, and the project schema marker is always the last write.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import tomllib
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from cli.atomic_write import (
    DIR_FD_SUPPORTED,
    GuardRefusal,
    _atomic_write_via_dir_fd,
    _atomic_write_via_path,
    _reverify_chain,
    _snapshot_chain,
    make_tmp_name,
)
from cli.capabilities import is_known_grant_name
from cli.commands._registry import MalformedRegistry, read_registry
from cli.config_schema import (
    ConfigDiagnostic,
    resolve_configuration,
)
from cli.protocol_gate import read_shipped_project_schema_version


CURRENT_SCHEMA_IDENTITY = "cartopian-authoritative-config-v2"
CHECKPOINT_IDENTITY = "cartopian-config-migration-checkpoint-v1"
CHECKPOINT_RELATIVE_PATH = ".cartopian/config-migration.json"

SUPPORTED_OLDER_MARKERS = (
    "v0.1.0",
    "v0.2.0",
    "v0.3.0",
    "v0.4.0",
    "v0.5.0",
    "v0.6.0",
    "v0.7.0",
)
ACTIVITY_ORDER = ("task_run", "task_review", "planning_review")
PRESERVED_FACTS = (
    "role-descriptions",
    "review-modes-and-roles",
    "launch-targets-and-options",
    "automation-values",
    "work-root-declarations-and-mappings",
    "capability-activation",
    "effective-grant-sets",
    "scope-precedence-and-attribution",
)

_ROOT_KEYS = {
    "global": frozenset(("defaults", "automation", "roles", "reviews", "git", "handoffs")),
    "project": frozenset(
        ("project", "defaults", "automation", "roles", "reviews", "git", "handoffs")
    ),
    "machine-local": frozenset(("work_roots",)),
}
_ROLE_EXECUTION_KEYS = ("target", "model", "effort", "timeout")
_ROLE_KEYS = frozenset(
    ("description", "grants", *_ROLE_EXECUTION_KEYS, "launch", "auto_launch")
)
_HANDOFF_KEYS = frozenset(
    (
        "agent",
        "target",
        "model",
        "effort",
        "timeout",
        "auto_start",
        "auto_start_tasks",
        "auto_start_reviews",
        "planning_reviews",
    )
)


@dataclass(frozen=True)
class ConfigurationMigrationEntry:
    identity: str
    from_identities: Tuple[str, ...]
    to_identity: str
    supported_forms: Tuple[str, ...]
    transforms: Tuple[str, ...]
    validation_gates: Tuple[str, ...]
    recovery: str

    def as_record(self) -> Dict[str, Any]:
        return {
            "identity": self.identity,
            "from_identities": list(self.from_identities),
            "to_identity": self.to_identity,
            "supported_forms": list(self.supported_forms),
            "transforms": list(self.transforms),
            "validation_gates": list(self.validation_gates),
            "recovery": self.recovery,
        }


CONFIGURATION_MIGRATION_ENTRIES = (
    ConfigurationMigrationEntry(
        identity="config-v0.1-v0.4-to-v0.5",
        from_identities=("unset", "v0.1.0", "v0.2.0", "v0.3.0", "v0.4.0"),
        to_identity="v0.5.0",
        supported_forms=("legacy", "transitional"),
        transforms=(
            "protocol-version-marker",
            "role-string-form",
            "legacy-auto-start",
            "implicit-pre-v0.5-review",
        ),
        validation_gates=(
            "closed-source-vocabulary",
            "deterministic-review-intent",
            "effective-semantic-equivalence",
        ),
        recovery="resolve every pending operator choice, then rerun migrate-config",
    ),
    ConfigurationMigrationEntry(
        identity="config-v0.5-to-v0.6",
        from_identities=("v0.5.0",),
        to_identity="v0.6.0",
        supported_forms=("transitional", "partial"),
        transforms=(
            "handoff-launch-to-role-launch",
            "split-permissions-to-work-type-permissions",
            "marker-last-advancement",
        ),
        validation_gates=(
            "closed-grant-vocabulary",
            "no-authority-widening",
            "scope-ownership",
            "canonical-resolution",
        ),
        recovery="repair the named source fact without changing its intended scope, then rerun",
    ),
    ConfigurationMigrationEntry(
        identity="config-v0.6-to-v0.7",
        from_identities=("v0.6.0",),
        to_identity="v0.7.0",
        supported_forms=("superseded-role-launch", "partial"),
        transforms=(
            "flatten-role-launch-fields",
            "remove-supported-residual-vocabulary",
            "remove-legacy-comment-tombstones",
            "marker-last-advancement",
        ),
        validation_gates=(
            "explicit-old-new-agreement",
            "effective-semantic-equivalence",
            "canonical-output-has-one-role-table",
        ),
        recovery="resolve conflicting old and preferred definitions, then rerun",
    ),
    ConfigurationMigrationEntry(
        identity="config-v0.7-to-v0.8",
        from_identities=("v0.7.0",),
        to_identity="v0.8.0",
        supported_forms=("preferred", "superseded-role-launch", "partial"),
        transforms=(
            "flatten-role-launch-fields",
            "remove-supported-residual-vocabulary",
            "remove-legacy-comment-tombstones",
            "marker-last-advancement",
        ),
        validation_gates=(
            "effective-semantic-equivalence",
            "no-fabricated-operator-attestation",
            "no-promoted-legacy-decision",
            "no-historical-review-rewrite",
        ),
        recovery=(
            "resolve the reported configuration diagnostic, then rerun; operator-"
            "intent attestations are never created by migration"
        ),
    ),
    ConfigurationMigrationEntry(
        identity="config-v0.8-partial-repair",
        from_identities=("v0.8.0",),
        to_identity="v0.8.0",
        supported_forms=("superseded-role-launch", "partial"),
        transforms=(
            "flatten-role-launch-fields",
            "remove-supported-residual-vocabulary",
            "remove-legacy-comment-tombstones",
        ),
        validation_gates=(
            "explicit-old-new-agreement",
            "effective-semantic-equivalence",
            "canonical-output-has-one-role-table",
        ),
        recovery="resolve conflicting old and preferred definitions, then rerun",
    ),
)

class MigrationDiagnostic(ValueError):
    def __init__(
        self,
        code: str,
        field_name: str,
        scope: str,
        message: str,
        recovery: str,
        *,
        pending: bool = False,
        affected_projects: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        self.code = code
        self.field_name = field_name
        self.scope = scope
        self.message = message
        self.recovery = recovery
        self.pending = pending
        self.affected_projects = tuple(
            {
                "id": str(project["id"]),
                "changed_activities": list(project["changed_activities"]),
            }
            for project in affected_projects
        )
        super().__init__(f"{code}: {scope}:{field_name}: {message}")

    def as_record(self) -> Dict[str, Any]:
        record: Dict[str, Any] = {
            "code": self.code,
            "field": self.field_name,
            "scope": self.scope,
            "classification": (
                "pending-operator-decision" if self.pending else "migration-refusal"
            ),
            "message": self.message,
            "recovery": self.recovery,
        }
        if self.affected_projects:
            record["affected_projects"] = list(self.affected_projects)
        return record


class MigrationRefused(GuardRefusal):
    """Execution was attempted for a non-executable or stale plan."""


class MigrationInterrupted(RuntimeError):
    """Testable interruption boundary after a completed, evidenced step."""


@dataclass(frozen=True)
class MigrationStep:
    step_id: str
    kind: str
    scope: str
    relative_target: str
    path: Path
    base: Path
    before: bytes
    after: bytes
    validation: Tuple[str, ...]

    def as_record(self) -> Dict[str, Any]:
        return {
            "id": self.step_id,
            "kind": self.kind,
            "scope": self.scope,
            "target": self.relative_target,
            "before_identity": _identity(self.before),
            "after_identity": _identity(self.after),
            "validation": list(self.validation),
            "marker_last": self.kind == "update-marker",
        }


@dataclass
class ConfigurationMigrationPlan:
    status: str
    compatibility_state: str
    current_schema_version: str
    detected_schema_version: Optional[str]
    entries: Tuple[ConfigurationMigrationEntry, ...] = ()
    source_facts: Tuple[Dict[str, str], ...] = ()
    target_facts: Tuple[Dict[str, str], ...] = ()
    conflicts: Tuple[Dict[str, Any], ...] = ()
    pending_choices: Tuple[Dict[str, Any], ...] = ()
    steps: Tuple[MigrationStep, ...] = ()
    validation_gates: Tuple[str, ...] = ()
    marker_update: Optional[Dict[str, Any]] = None
    equivalence: Dict[str, Any] = field(
        default_factory=lambda: {"status": "not-run", "differences": []}
    )
    attribution_changes: List[Dict[str, str]] = field(default_factory=list)
    checkpoint_state: str = "absent"
    diagnostics: Tuple[Dict[str, Any], ...] = ()
    source_effective: Optional[Dict[str, Any]] = field(default=None, repr=False)
    target_effective: Optional[Dict[str, Any]] = field(default=None, repr=False)

    def as_record(self) -> Dict[str, Any]:
        authority_status = (
            "failed"
            if any(
                item.get("code") == "migration-authority-divergence"
                for item in self.diagnostics
            )
            else (
                "not-run"
                if self.current_schema_version == "unknown"
                else "passed"
            )
        )
        return {
            "status": self.status,
            "compatibility_state": self.compatibility_state,
            "schema_identity": CURRENT_SCHEMA_IDENTITY,
            "detected_schema_version": self.detected_schema_version,
            "current_schema_version": self.current_schema_version,
            "entries": [entry.as_record() for entry in self.entries],
            "source_scopes": ["global", "project", "machine-local"],
            "source_facts": list(self.source_facts),
            "target_facts": list(self.target_facts),
            "preserved_effective_behavior": list(PRESERVED_FACTS),
            "equivalence": self.equivalence,
            "attribution_changes": list(self.attribution_changes),
            "conflicts": list(self.conflicts),
            "pending_choices": list(self.pending_choices),
            "steps": [step.as_record() for step in self.steps],
            "validation_gates": list(self.validation_gates),
            "marker_update": self.marker_update,
            "checkpoint_state": self.checkpoint_state,
            "checkpoint_lifecycle": {
                "retained_while": "in-progress",
                "removed_after": "canonical post-validation",
                "stale_behavior": "replaced only by a newly executable plan",
            },
            "migration_authority": {
                "configuration": (
                    "cli.config_migration.CONFIGURATION_MIGRATION_ENTRIES"
                ),
                "filesystem": "cli.migrations.ENTRY_VERSIONS",
                "historical_contract": "protocol/CHANGELOG.md",
                "cross_validation": authority_status,
            },
            "diagnostics": list(self.diagnostics),
            "guidance": {
                "deprecation": (
                    "Supported legacy and transitional forms are migration-only; "
                    "new configuration must use the preferred contract."
                ),
                "recovery": (
                    "No marker is advanced on refusal. Repair the named scoped fact "
                    "or resolve the pending choice, then rerun the same command."
                ),
            },
        }


@dataclass
class _ScopeState:
    scope: str
    path: Path
    before: bytes
    raw: Dict[str, Any]
    canonical: Dict[str, Any]
    changed: bool = False
    facts: List[Dict[str, str]] = field(default_factory=list)
    permission_sources: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)


def _identity(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _version_tuple(value: str) -> Tuple[int, int, int]:
    parts = value[1:].split(".")
    if (
        len(parts) != 3
        or not all(part.isdigit() for part in parts)
        or not value.startswith("v")
    ):
        raise ValueError(value)
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def _diagnose(
    code: str,
    field_name: str,
    scope: str,
    message: str,
    recovery: str,
    *,
    pending: bool = False,
    affected_projects: Sequence[Mapping[str, Any]] = (),
) -> None:
    raise MigrationDiagnostic(
        code,
        field_name,
        scope,
        message,
        recovery,
        pending=pending,
        affected_projects=affected_projects,
    )


def _read_scope(path: Path, scope: str, *, required: bool) -> Tuple[bytes, Dict[str, Any]]:
    raw_path = os.fspath(path)
    if not os.path.lexists(raw_path):
        if required:
            _diagnose(
                "missing-project-config",
                "cartopian.toml",
                scope,
                "the governed project configuration is missing",
                "restore the project cartopian.toml before migration",
            )
        return b"", {}
    if os.path.islink(raw_path):
        _diagnose(
            "unsafe-config-path",
            path.name,
            scope,
            "configuration path is a symlink",
            "replace it with an owned regular file",
        )
    try:
        st = os.lstat(raw_path)
    except OSError:
        _diagnose(
            "unreadable-config",
            path.name,
            scope,
            "configuration metadata cannot be read",
            "repair file ownership or permissions",
        )
    if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
        _diagnose(
            "unsafe-config-path",
            path.name,
            scope,
            "configuration must be a single-link regular file",
            "replace it with an owned regular file",
        )
    try:
        data = path.read_bytes()
        parsed = tomllib.loads(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        _diagnose(
            "malformed-config",
            path.name,
            scope,
            "configuration is unreadable or not valid UTF-8 TOML",
            "repair TOML syntax without changing intended behavior",
        )
    if not isinstance(parsed, dict):
        _diagnose(
            "malformed-config",
            "<root>",
            scope,
            "configuration root must be a table",
            "repair the authored configuration",
        )
    return data, parsed


def _check_root_keys(config: Mapping[str, Any], scope: str) -> None:
    unknown = sorted(set(config) - _ROOT_KEYS[scope])
    if unknown:
        _diagnose(
            "unknown-source-field",
            unknown[0],
            scope,
            "field is outside the supported authored migration inventory",
            "remove the unknown field or migrate it through an approved entry",
        )


def _as_bool(value: Any, field_name: str, scope: str) -> bool:
    if not isinstance(value, bool):
        _diagnose(
            "malformed-source-value",
            field_name,
            scope,
            "legacy launch permission must be a boolean",
            "set an explicit true or false value",
        )
    return value


def _conflict(
    field_name: str,
    scope: str,
    first: str,
    second: str,
) -> None:
    _diagnose(
        "conflicting-definition",
        field_name,
        scope,
        f"{first} and {second} define different effective intent",
        "choose one intended value and make the old and preferred definitions agree",
        pending=True,
    )


def _normalize_roles_and_handoffs(
    config: Dict[str, Any], scope: str
) -> Tuple[Dict[str, Any], bool, List[Dict[str, str]], List[Tuple[str, Dict[str, Any]]]]:
    target = copy.deepcopy(config)
    changed = False
    facts: List[Dict[str, str]] = []
    permission_sources: List[Tuple[str, Dict[str, Any]]] = []
    roles_raw = target.get("roles", {})
    if not isinstance(roles_raw, dict):
        _diagnose(
            "malformed-source-value",
            "roles",
            scope,
            "roles must be a table",
            "repair the roles table",
        )
    roles: Dict[str, Any] = OrderedDict()
    for role_name, role_value in roles_raw.items():
        role_field = f"roles.{role_name}"
        if isinstance(role_value, str):
            if not role_value.strip():
                _diagnose(
                    "malformed-source-value",
                    role_field,
                    scope,
                    "legacy role description is empty",
                    "write a non-empty role description",
                )
            roles[role_name] = OrderedDict((("description", role_value),))
            changed = True
            facts.append(
                {"scope": scope, "field": role_field, "form": "legacy-role-string"}
            )
            continue
        if not isinstance(role_value, dict):
            _diagnose(
                "malformed-source-value",
                role_field,
                scope,
                "role must be a description string or table",
                "repair the role declaration",
            )
        unknown = sorted(set(role_value) - _ROLE_KEYS)
        if unknown:
            _diagnose(
                "unknown-source-field",
                f"{role_field}.{unknown[0]}",
                scope,
                "role field is outside the supported migration inventory",
                "remove the unknown role field",
            )
        role = copy.deepcopy(role_value)
        nested_launch = role.pop("launch", None)
        if nested_launch is not None:
            if not isinstance(nested_launch, dict):
                _diagnose(
                    "malformed-source-value",
                    f"{role_field}.launch",
                    scope,
                    "superseded launch definition must be a table",
                    "repair the launch definition",
                )
            unknown_launch = sorted(set(nested_launch) - set(_ROLE_EXECUTION_KEYS))
            if unknown_launch:
                _diagnose(
                    "unknown-source-field",
                    f"{role_field}.launch.{unknown_launch[0]}",
                    scope,
                    "launch field is outside the supported migration inventory",
                    "remove the unknown launch field",
                )
            for key, value in nested_launch.items():
                if key in role and role[key] != value:
                    _conflict(
                        f"{role_field}.{key}",
                        scope,
                        f"{role_field}.launch.{key}",
                        f"{role_field}.{key}",
                    )
                role[key] = value
                facts.append(
                    {
                        "scope": scope,
                        "field": f"{role_field}.launch.{key}",
                        "form": "superseded-role-launch",
                    }
                )
            changed = True
        grants = role.get("grants")
        if grants is not None:
            if not isinstance(grants, list):
                _diagnose(
                    "malformed-source-value",
                    f"{role_field}.grants",
                    scope,
                    "grants must be a list",
                    "repair the grant declaration",
                )
            for grant in grants:
                if not is_known_grant_name(grant):
                    _diagnose(
                        "unknown-grant",
                        f"{role_field}.grants",
                        scope,
                        "grant list contains a value outside the closed vocabulary",
                        "replace the unknown grant with an approved capability or preset",
                    )
        roles[role_name] = role

    handoffs_present = "handoffs" in target
    handoffs = target.pop("handoffs", {})
    if handoffs_present:
        changed = True
    if not isinstance(handoffs, dict):
        _diagnose(
            "malformed-source-value",
            "handoffs",
            scope,
            "handoffs must be a table",
            "repair the handoffs table",
        )
    for role_name, handoff_value in handoffs.items():
        field_name = f"handoffs.{role_name}"
        if role_name == "pm":
            _diagnose(
                "authority-widening",
                field_name,
                scope,
                "the interactive PM role cannot be a migration launch target",
                "remove the PM handoff declaration",
            )
        if not isinstance(handoff_value, dict):
            _diagnose(
                "malformed-source-value",
                field_name,
                scope,
                "handoff must be a table",
                "repair the handoff declaration",
            )
        unknown = sorted(set(handoff_value) - _HANDOFF_KEYS)
        if unknown:
            _diagnose(
                "unknown-source-field",
                f"{field_name}.{unknown[0]}",
                scope,
                "handoff field is outside the supported migration inventory",
                "remove the unknown handoff field",
            )
        role = roles.setdefault(role_name, OrderedDict())
        old_target = handoff_value.get("agent")
        handoff_target = handoff_value.get("target")
        if old_target is not None and handoff_target is not None and old_target != handoff_target:
            _conflict(f"{field_name}.agent", scope, "agent", "target")
        mapped_target = old_target if old_target is not None else handoff_target
        mapped_values = {
            "target": mapped_target,
            "model": handoff_value.get("model"),
            "effort": handoff_value.get("effort"),
            "timeout": handoff_value.get("timeout"),
        }
        for key, value in mapped_values.items():
            if value is None:
                continue
            current = role.get(key)
            if current is not None and current != value:
                _conflict(
                    f"roles.{role_name}.{key}",
                    scope,
                    field_name,
                    f"roles.{role_name}",
                )
            role[key] = value
            facts.append(
                {
                    "scope": scope,
                    "field": f"{field_name}.{('agent' if key == 'target' else key)}",
                    "form": "legacy-handoff-launch",
                }
            )
        permission_values = {
            key: handoff_value[key]
            for key in (
                "auto_start",
                "auto_start_tasks",
                "auto_start_reviews",
                "planning_reviews",
            )
            if key in handoff_value
        }
        if permission_values:
            permission_sources.append((role_name, permission_values))
            for key in permission_values:
                facts.append(
                    {
                        "scope": scope,
                        "field": f"{field_name}.{key}",
                        "form": "legacy-launch-permission",
                    }
                )
    if roles:
        target["roles"] = roles
    elif "roles" in target:
        target.pop("roles")
        changed = True
    return target, changed, facts, permission_sources


def _effective_role_names(
    global_cfg: Mapping[str, Any], project_cfg: Mapping[str, Any]
) -> List[str]:
    names = list(global_cfg.get("roles", {}))
    names.extend(
        name for name in project_cfg.get("roles", {}) if name not in names
    )
    for name in ("pm", "operator"):
        if name not in names:
            names.append(name)
    return names


def _effective_review(
    global_cfg: Mapping[str, Any],
    project_cfg: Mapping[str, Any],
    mode_key: str,
    role_key: str,
) -> Tuple[str, Optional[str]]:
    global_reviews = global_cfg.get("reviews", {})
    project_reviews = project_cfg.get("reviews", {})
    mode = project_reviews.get(mode_key, global_reviews.get(mode_key, "off"))
    role = project_reviews.get(role_key, global_reviews.get(role_key))
    return mode, role if mode == "required" else None


def _add_implicit_pre_v050_review(
    global_cfg: Dict[str, Any],
    project_cfg: Dict[str, Any],
    marker: Optional[str],
) -> List[Dict[str, str]]:
    if marker is not None and _version_tuple(marker) >= (0, 5, 0):
        return []
    if "reviewer" not in _effective_role_names(global_cfg, project_cfg):
        return []
    global_reviews = global_cfg.get("reviews", {})
    project_reviews = project_cfg.setdefault("reviews", OrderedDict())
    changes: List[Dict[str, str]] = []
    for mode_key, role_key in (
        ("planning", "planning_role"),
        ("task_closure", "task_role"),
    ):
        if mode_key not in project_reviews and mode_key not in global_reviews:
            project_reviews[mode_key] = "required"
            changes.append(
                {
                    "field": f"reviews.{mode_key}",
                    "before": "legacy-pre-v0.5",
                    "after": "project",
                }
            )
        mode, _ = _effective_review(global_cfg, project_cfg, mode_key, role_key)
        if (
            mode == "required"
            and role_key not in project_reviews
            and role_key not in global_reviews
        ):
            project_reviews[role_key] = "reviewer"
            changes.append(
                {
                    "field": f"reviews.{role_key}",
                    "before": "legacy-pre-v0.5",
                    "after": "project",
                }
            )
    if not project_reviews:
        project_cfg.pop("reviews", None)
    return changes


def _effective_launch_target(
    role_name: str,
    global_cfg: Mapping[str, Any],
    project_cfg: Mapping[str, Any],
) -> Optional[str]:
    global_role = global_cfg.get("roles", {}).get(role_name, {})
    project_role = project_cfg.get("roles", {}).get(role_name, {})
    return project_role.get("target", global_role.get("target"))


def _permission_flags(
    values: Mapping[str, Any], role_name: str, scope: str
) -> Tuple[bool, bool]:
    legacy_auto = (
        _as_bool(
            values["auto_start"],
            f"handoffs.{role_name}.auto_start",
            scope,
        )
        if "auto_start" in values
        else None
    )
    split_tasks = (
        _as_bool(
            values["auto_start_tasks"],
            f"handoffs.{role_name}.auto_start_tasks",
            scope,
        )
        if "auto_start_tasks" in values
        else None
    )
    if (
        legacy_auto is not None
        and split_tasks is not None
        and legacy_auto != split_tasks
    ):
        _conflict(
            f"roles.{role_name}.auto_launch",
            scope,
            "auto_start",
            "auto_start_tasks",
        )
    task_permission = (
        split_tasks if split_tasks is not None else bool(legacy_auto)
    )

    planning_legacy = (
        _as_bool(
            values["planning_reviews"],
            f"handoffs.{role_name}.planning_reviews",
            scope,
        )
        if "planning_reviews" in values
        else None
    )
    split_reviews = (
        _as_bool(
            values["auto_start_reviews"],
            f"handoffs.{role_name}.auto_start_reviews",
            scope,
        )
        if "auto_start_reviews" in values
        else None
    )
    if (
        planning_legacy is True
        and legacy_auto is None
        and split_reviews is None
    ):
        _diagnose(
            "ambiguous-planning-review-permission",
            f"handoffs.{role_name}.planning_reviews",
            scope,
            (
                "planning_reviews = true is authored with split task launch "
                "but without either the combined or split review permission"
            ),
            (
                "choose whether planning reviews launch automatically and author "
                "auto_start_reviews explicitly"
            ),
            pending=True,
        )
    derived_reviews = (
        legacy_auto and planning_legacy
        if legacy_auto is not None and planning_legacy is not None
        else False
    )
    if (
        split_reviews is not None
        and legacy_auto is not None
        and planning_legacy is not None
        and bool(derived_reviews) != split_reviews
    ):
        _conflict(
            f"roles.{role_name}.auto_launch",
            scope,
            "auto_start plus planning_reviews",
            "auto_start_reviews",
        )
    review_permission = (
        split_reviews if split_reviews is not None else bool(derived_reviews)
    )
    return task_permission, review_permission


def _assigned_activities(
    role_name: str,
    global_cfg: Dict[str, Any],
    project_cfg: Dict[str, Any],
) -> Tuple[str, ...]:
    assigned: List[str] = []
    if _effective_launch_target(role_name, global_cfg, project_cfg) is not None:
        assigned.append("task_run")
    task_mode, task_role = _effective_review(
        global_cfg, project_cfg, "task_closure", "task_role"
    )
    if task_mode == "required" and task_role == role_name:
        assigned.append("task_review")
    planning_mode, planning_role = _effective_review(
        global_cfg, project_cfg, "planning", "planning_role"
    )
    if planning_mode == "required" and planning_role == role_name:
        assigned.append("planning_review")
    return tuple(assigned)


def _target_permission_activities(
    values: Mapping[str, Any],
    role_name: str,
    scope: str,
    global_cfg: Dict[str, Any],
    project_cfg: Dict[str, Any],
) -> Tuple[str, ...]:
    """Target-only seam kept independent from compatibility interpretation."""
    task_permission, review_permission = _permission_flags(
        values, role_name, scope
    )
    assigned = _assigned_activities(role_name, global_cfg, project_cfg)
    return tuple(
        activity
        for activity in ACTIVITY_ORDER
        if (
            activity in assigned
            and (
                (activity in ("task_run", "task_review") and task_permission)
                or (activity == "planning_review" and review_permission)
            )
        )
    )


def _safe_global_permission_activities(
    values: Mapping[str, Any],
    role_name: str,
    global_cfg: Dict[str, Any],
) -> Tuple[str, ...]:
    task_permission, _review_permission = _permission_flags(
        values, role_name, "global"
    )
    global_role = global_cfg.get("roles", {}).get(role_name, {})
    if task_permission and global_role.get("target"):
        return ("task_run",)
    return ()


def _set_target_auto_launch(
    state: _ScopeState,
    role_name: str,
    expected: Sequence[str],
    source_label: str,
) -> None:
    role = state.canonical.setdefault("roles", OrderedDict()).setdefault(
        role_name, OrderedDict()
    )
    if "auto_launch" in role:
        current = role["auto_launch"]
        if not isinstance(current, list) or tuple(current) != tuple(expected):
            _conflict(
                f"roles.{role_name}.auto_launch",
                state.scope,
                source_label,
                "roles preferred permission",
            )
    elif not expected:
        return
    else:
        role["auto_launch"] = list(expected)
    state.changed = True


def _map_target_permissions(
    global_state: _ScopeState,
    project_state: _ScopeState,
    global_cfg: Dict[str, Any],
    project_cfg: Dict[str, Any],
) -> None:
    project_mappings: Dict[str, Tuple[str, ...]] = {}
    for role_name, values in global_state.permission_sources:
        effective = _target_permission_activities(
            values, role_name, "global", global_cfg, project_cfg
        )
        safe_global = _safe_global_permission_activities(
            values, role_name, global_cfg
        )
        _set_target_auto_launch(
            global_state,
            role_name,
            safe_global,
            f"handoffs.{role_name} global legacy permission",
        )
        if effective != safe_global:
            project_mappings[role_name] = effective

    for role_name, values in project_state.permission_sources:
        effective = _target_permission_activities(
            values, role_name, "project", global_cfg, project_cfg
        )
        if role_name in project_mappings and project_mappings[role_name] != effective:
            _conflict(
                f"roles.{role_name}.auto_launch",
                "project",
                "global legacy permission materialization",
                "project legacy permission",
            )
        project_mappings[role_name] = effective

    for role_name, expected in project_mappings.items():
        _set_target_auto_launch(
            project_state,
            role_name,
            expected,
            f"handoffs.{role_name} scoped legacy permission",
        )


def _marker(
    project_cfg: Dict[str, Any], current_version: str
) -> Tuple[Optional[str], bool, List[Dict[str, str]]]:
    project = project_cfg.get("project")
    if not isinstance(project, dict):
        _diagnose(
            "missing-project-table",
            "project",
            "project",
            "project configuration requires a [project] table",
            "restore the governed project identity table",
        )
    old = project.get("protocol_version")
    new = project.get("project_schema_version")
    if old is not None and new is not None and old != new:
        _conflict(
            "project.project_schema_version",
            "project",
            "project.protocol_version",
            "project.project_schema_version",
        )
    detected = new if new is not None else old
    if detected is not None:
        if not isinstance(detected, str):
            _diagnose(
                "malformed-marker",
                "project.project_schema_version",
                "project",
                "schema marker must be a vX.Y.Z string",
                "repair the marker before migration",
            )
        try:
            detected_tuple = _version_tuple(detected)
            current_tuple = _version_tuple(current_version)
        except ValueError:
            _diagnose(
                "malformed-marker",
                "project.project_schema_version",
                "project",
                "schema marker must be a vX.Y.Z string",
                "repair the marker before migration",
            )
        if detected_tuple > current_tuple:
            _diagnose(
                "newer-marker",
                "project.project_schema_version",
                "project",
                "project marker is newer than this installed migration registry",
                "upgrade Cartopian; do not infer a downgrade",
            )
        if detected != current_version and detected not in SUPPORTED_OLDER_MARKERS:
            _diagnose(
                "unsupported-marker",
                "project.project_schema_version",
                "project",
                "no shipped migration entry accepts this older marker",
                "install a Cartopian release that ships the missing entry",
            )
    changed = False
    facts: List[Dict[str, str]] = []
    if old is not None:
        project.pop("protocol_version", None)
        project["project_schema_version"] = detected
        changed = True
        facts.append(
            {
                "scope": "project",
                "field": "project.protocol_version",
                "form": "legacy-marker",
            }
        )
    if detected is None:
        project["project_schema_version"] = "v0.1.0"
        changed = True
        facts.append(
            {
                "scope": "project",
                "field": "project.project_schema_version",
                "form": "missing-supported-marker",
            }
        )
    return detected, changed, facts


def _compatibility_normalize_scope(
    config: Mapping[str, Any],
) -> Tuple[Dict[str, Any], List[Tuple[str, Dict[str, Any]]]]:
    """Interpret authored legacy vocabulary without using target records."""
    result = copy.deepcopy(dict(config))
    roles_raw = result.get("roles", {})
    roles: Dict[str, Any] = OrderedDict()
    for role_name, role_value in roles_raw.items():
        if isinstance(role_value, str):
            roles[role_name] = OrderedDict((("description", role_value),))
        else:
            role = copy.deepcopy(role_value)
            nested_launch = role.pop("launch", {})
            if isinstance(nested_launch, dict):
                for key, value in nested_launch.items():
                    role.setdefault(key, value)
            roles[role_name] = role
    permissions: List[Tuple[str, Dict[str, Any]]] = []
    handoffs = result.pop("handoffs", {})
    for role_name, handoff_value in handoffs.items():
        role = roles.setdefault(role_name, OrderedDict())
        target = handoff_value.get("agent", handoff_value.get("target"))
        for key, value in (
            ("target", target),
            ("model", handoff_value.get("model")),
            ("effort", handoff_value.get("effort")),
            ("timeout", handoff_value.get("timeout")),
        ):
            if value is not None:
                role[key] = value
        values = {
            key: handoff_value[key]
            for key in (
                "auto_start",
                "auto_start_tasks",
                "auto_start_reviews",
                "planning_reviews",
            )
            if key in handoff_value
        }
        if values:
            permissions.append((role_name, values))
    if roles:
        result["roles"] = roles
    elif "roles" in result:
        result.pop("roles")
    return result, permissions


def _compatibility_permission_activities(
    values: Mapping[str, Any],
    role_name: str,
    scope: str,
    global_cfg: Dict[str, Any],
    project_cfg: Dict[str, Any],
) -> Tuple[str, ...]:
    task_permission, review_permission = _compatibility_permission_flags(
        values, role_name, scope
    )
    assigned = _assigned_activities(role_name, global_cfg, project_cfg)
    return tuple(
        activity
        for activity in ACTIVITY_ORDER
        if (
            activity in assigned
            and (
                (activity in ("task_run", "task_review") and task_permission)
                or (activity == "planning_review" and review_permission)
            )
        )
    )


def _compatibility_permission_flags(
    values: Mapping[str, Any], role_name: str, scope: str
) -> Tuple[bool, bool]:
    """Interpret source permission vocabulary independently of target mapping."""
    legacy_auto = (
        _as_bool(
            values["auto_start"],
            f"handoffs.{role_name}.auto_start",
            scope,
        )
        if "auto_start" in values
        else None
    )
    split_tasks = (
        _as_bool(
            values["auto_start_tasks"],
            f"handoffs.{role_name}.auto_start_tasks",
            scope,
        )
        if "auto_start_tasks" in values
        else None
    )
    if (
        legacy_auto is not None
        and split_tasks is not None
        and legacy_auto != split_tasks
    ):
        _conflict(
            f"roles.{role_name}.auto_launch",
            scope,
            "auto_start",
            "auto_start_tasks",
        )
    task_permission = (
        split_tasks if split_tasks is not None else bool(legacy_auto)
    )

    planning_legacy = (
        _as_bool(
            values["planning_reviews"],
            f"handoffs.{role_name}.planning_reviews",
            scope,
        )
        if "planning_reviews" in values
        else None
    )
    split_reviews = (
        _as_bool(
            values["auto_start_reviews"],
            f"handoffs.{role_name}.auto_start_reviews",
            scope,
        )
        if "auto_start_reviews" in values
        else None
    )
    if (
        planning_legacy is True
        and legacy_auto is None
        and split_reviews is None
    ):
        _diagnose(
            "ambiguous-planning-review-permission",
            f"handoffs.{role_name}.planning_reviews",
            scope,
            (
                "planning_reviews = true is authored with split task launch "
                "but without either the combined or split review permission"
            ),
            (
                "choose whether planning reviews launch automatically and author "
                "auto_start_reviews explicitly"
            ),
            pending=True,
        )
    derived_reviews = (
        legacy_auto and planning_legacy
        if legacy_auto is not None and planning_legacy is not None
        else False
    )
    if (
        split_reviews is not None
        and legacy_auto is not None
        and planning_legacy is not None
        and bool(derived_reviews) != split_reviews
    ):
        _conflict(
            f"roles.{role_name}.auto_launch",
            scope,
            "auto_start plus planning_reviews",
            "auto_start_reviews",
        )
    review_permission = (
        split_reviews if split_reviews is not None else bool(derived_reviews)
    )
    return task_permission, review_permission


def _compatibility_safe_global_permission_activities(
    values: Mapping[str, Any],
    role_name: str,
    global_cfg: Dict[str, Any],
) -> Tuple[str, ...]:
    task_permission, _review_permission = _compatibility_permission_flags(
        values, role_name, "global"
    )
    global_role = global_cfg.get("roles", {}).get(role_name, {})
    if task_permission and global_role.get("target"):
        return ("task_run",)
    return ()


def _compatibility_set_auto_launch(
    config: Dict[str, Any], role_name: str, activities: Sequence[str]
) -> None:
    if not activities:
        return
    role = config.setdefault("roles", OrderedDict()).setdefault(
        role_name, OrderedDict()
    )
    if "auto_launch" not in role:
        role["auto_launch"] = list(activities)


def _resolve_compatibility_configuration(
    global_raw: Mapping[str, Any],
    project_raw: Mapping[str, Any],
    local_raw: Mapping[str, Any],
    detected_marker: Optional[str],
) -> Dict[str, Any]:
    """Resolve true source semantics through the versioned compatibility model."""
    global_cfg, global_permissions = _compatibility_normalize_scope(
        global_raw
    )
    project_cfg, project_permissions = _compatibility_normalize_scope(
        project_raw
    )
    project_table = project_cfg["project"]
    project_table.pop("protocol_version", None)
    project_table["project_schema_version"] = detected_marker or "v0.1.0"
    _add_implicit_pre_v050_review(
        global_cfg, project_cfg, detected_marker
    )

    project_materializations: Dict[str, Tuple[str, ...]] = {}
    for role_name, values in global_permissions:
        effective = _compatibility_permission_activities(
            values, role_name, "global", global_cfg, project_cfg
        )
        safe_global = _compatibility_safe_global_permission_activities(
            values, role_name, global_cfg
        )
        _compatibility_set_auto_launch(
            global_cfg, role_name, safe_global
        )
        if effective != safe_global:
            project_materializations[role_name] = effective
    for role_name, values in project_permissions:
        project_materializations[role_name] = (
            _compatibility_permission_activities(
                values, role_name, "project", global_cfg, project_cfg
            )
        )
    for role_name, activities in project_materializations.items():
        _compatibility_set_auto_launch(
            project_cfg, role_name, activities
        )
    return resolve_configuration(global_cfg, project_cfg, local_raw)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"unsupported TOML value: {type(value).__name__}")


_TABLE_LINE_RE = re.compile(
    r"^\s*\[([A-Za-z0-9_.-]+)\](?:\s*#.*)?\s*$"
)
_KEY_LINE_RE = re.compile(
    r"^(\s*)([A-Za-z0-9_-]+)(\s*=\s*)(.*?)(\r?\n)?$"
)
_LEGACY_TOMBSTONE_RE = re.compile(
    rb"(?m)^[ \t]*# migrated legacy:.*(?:\r?\n|$)"
)


def _inline_comment(value_text: str) -> str:
    quote: Optional[str] = None
    escaped = False
    for index, char in enumerate(value_text):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
        elif char == "#":
            return value_text[index:]
    return ""


class _TomlEditor:
    """Bounded editor for the allowlisted legacy-to-preferred transforms."""

    def __init__(self, data: bytes) -> None:
        text = data.decode("utf-8")
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.lines = text.splitlines(keepends=True)

    def _headers(self) -> List[Tuple[str, int]]:
        result: List[Tuple[str, int]] = []
        for index, line in enumerate(self.lines):
            match = _TABLE_LINE_RE.match(line.rstrip("\r\n"))
            if match:
                result.append((match.group(1), index))
        return result

    def _bounds(self, table: str) -> Optional[Tuple[int, int, int]]:
        headers = self._headers()
        for position, (name, header_index) in enumerate(headers):
            if name == table:
                end = (
                    headers[position + 1][1]
                    if position + 1 < len(headers)
                    else len(self.lines)
                )
                return header_index, header_index + 1, end
        return None

    def _key_index(self, table: str, key: str) -> Optional[int]:
        bounds = self._bounds(table)
        if bounds is None:
            return None
        _header, start, end = bounds
        for index in range(start, end):
            match = _KEY_LINE_RE.match(self.lines[index])
            if match and match.group(2) == key:
                return index
        return None

    def _key_insertion_index(self, table: str) -> int:
        bounds = self._bounds(table)
        assert bounds is not None
        _header, start, end = bounds
        if end == len(self.lines):
            return end
        return self._before_leading_comments(end, start)

    def _before_leading_comments(self, insertion: int, start: int) -> int:
        while insertion > start:
            previous = self.lines[insertion - 1]
            if previous.strip() and not previous.lstrip().startswith("#"):
                break
            insertion -= 1
        return insertion

    def _normalize_blank_seam(self, insertion: int) -> None:
        """Collapse only whitespace joined by a structural removal."""
        left = min(insertion, len(self.lines))
        while left > 0 and not self.lines[left - 1].strip():
            left -= 1
        right = min(insertion, len(self.lines))
        while right < len(self.lines) and not self.lines[right].strip():
            right += 1
        replacement = (
            [self.newline]
            if left > 0 and right < len(self.lines)
            else []
        )
        self.lines[left:right] = replacement

    def convert_role_strings(self, roles: Mapping[str, Any]) -> None:
        replacements: List[Tuple[int, str, str]] = []
        for role_name, role_value in roles.items():
            if not isinstance(role_value, str):
                continue
            index = self._key_index("roles", role_name)
            if index is None:
                continue
            match = _KEY_LINE_RE.match(self.lines[index])
            if match is None:
                continue
            line_ending = match.group(5) or self.newline
            header = f"{match.group(1)}[roles.{role_name}]{line_ending}"
            description = (
                f"{match.group(1)}description{match.group(3)}"
                f"{match.group(4)}{line_ending}"
            )
            replacements.append((index, header, description))
        for index, header, description in reversed(replacements):
            self.lines[index : index + 1] = [header, description]

    def remove_empty_table_header(self, table: str) -> None:
        bounds = self._bounds(table)
        if bounds is None:
            return
        header, start, end = bounds
        if any(_KEY_LINE_RE.match(self.lines[index]) for index in range(start, end)):
            return
        line = self.lines[header]
        line_ending = (
            "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        )
        content = line[: -len(line_ending)] if line_ending else line
        suffix = content[content.find("]") + 1 :].strip()
        if suffix.startswith("#"):
            indentation = content[: len(content) - len(content.lstrip())]
            self.lines[header] = f"{indentation}{suffix}{line_ending}"
        else:
            del self.lines[header]
            self._normalize_blank_seam(header)

    def ensure_table(self, table: str) -> None:
        if self._bounds(table) is not None:
            return
        insertion = len(self.lines)
        insertion_floor = 0
        parent = table.rpartition(".")[0]
        if parent:
            parent_bounds = self._bounds(parent)
            if parent_bounds is not None:
                insertion = parent_bounds[2]
                insertion_floor = parent_bounds[1]
            else:
                prefix = table + "."
                for name, index in self._headers():
                    if name.startswith(prefix):
                        insertion = index
                        break
            if insertion < len(self.lines):
                insertion = self._before_leading_comments(
                    insertion, insertion_floor
                )
        block: List[str] = []
        if insertion > 0 and self.lines[insertion - 1].strip():
            block.append(self.newline)
        block.append(f"[{table}]{self.newline}")
        self.lines[insertion:insertion] = block

    def key_comment(self, table: str, key: str) -> str:
        index = self._key_index(table, key)
        if index is None:
            return ""
        match = _KEY_LINE_RE.match(self.lines[index])
        return _inline_comment(match.group(4)) if match is not None else ""

    def add_key(
        self, table: str, key: str, value: Any, *, comment: str = ""
    ) -> None:
        self.ensure_table(table)
        if self._key_index(table, key) is not None:
            return
        insertion = self._key_insertion_index(table)
        suffix = f" {comment}" if comment else ""
        self.lines[insertion:insertion] = [
            f"{key} = {_toml_value(value)}{suffix}{self.newline}"
        ]

    def set_key(
        self,
        table: str,
        key: str,
        value: Any,
        *,
        rename_from: Optional[str] = None,
    ) -> None:
        index = self._key_index(table, key)
        if index is None and rename_from is not None:
            index = self._key_index(table, rename_from)
        if index is None:
            self.add_key(table, key, value)
            return
        match = _KEY_LINE_RE.match(self.lines[index])
        if match is None:
            return
        comment = _inline_comment(match.group(4))
        suffix = f" {comment}" if comment else ""
        line_ending = match.group(5) or self.newline
        self.lines[index] = (
            f"{match.group(1)}{key}{match.group(3)}"
            f"{_toml_value(value)}{suffix}{line_ending}"
        )

    def remove_table(self, table: str) -> None:
        bounds = self._bounds(table)
        if bounds is None:
            return
        header, start, end = bounds
        # Comments immediately preceding a retired header are attached to that
        # table, including an inline header comment, and retire with it.
        removal_start = header
        while (
            removal_start > 0
            and self.lines[removal_start - 1].lstrip().startswith("#")
        ):
            removal_start -= 1
        # Keep a trailing standalone comment/blank group that clearly leads
        # into the following surviving table.
        removal_end = self._before_leading_comments(end, start)
        del self.lines[removal_start:removal_end]
        self._normalize_blank_seam(removal_start)

    def remove_legacy_tables(self) -> None:
        names = [
            name
            for name, _index in self._headers()
            if name == "handoffs"
            or name.startswith("handoffs.")
            or (
                name.startswith("roles.")
                and name.endswith(".launch")
                and len(name.split(".")) == 3
            )
        ]
        for name in reversed(names):
            self.remove_table(name)

    def remove_legacy_tombstones(self) -> None:
        self.lines = [
            line
            for line in self.lines
            if not line.lstrip().startswith("# migrated legacy:")
        ]

    def bytes(self) -> bytes:
        return "".join(self.lines).encode("utf-8")


def _render_preserving(
    before: bytes,
    raw: Mapping[str, Any],
    target: Mapping[str, Any],
    scope: str,
) -> bytes:
    editor = _TomlEditor(before)
    raw_roles = raw.get("roles", {})
    target_roles = target.get("roles", {})
    if isinstance(raw_roles, dict):
        editor.convert_role_strings(raw_roles)
        # A parent [roles] header is never needed once legacy string entries
        # have become role subtables. Removing only the empty parent leaves
        # every surviving [roles.<name>] block intact.
        editor.remove_empty_table_header("roles")

    if scope == "project":
        target_project = target["project"]
        editor.set_key(
            "project",
            "project_schema_version",
            target_project["project_schema_version"],
            rename_from="protocol_version",
        )

    for role_name, target_role in target_roles.items():
        raw_role = raw_roles.get(role_name, {})
        raw_role_table = raw_role if isinstance(raw_role, dict) else {}
        if (
            "auto_launch" in target_role
            and "auto_launch" not in raw_role_table
        ):
            editor.add_key(
                f"roles.{role_name}",
                "auto_launch",
                target_role["auto_launch"],
                comment=next(
                    (
                        editor.key_comment(f"handoffs.{role_name}", key)
                        for key in (
                            "auto_start_tasks",
                            "auto_start",
                            "auto_start_reviews",
                            "planning_reviews",
                        )
                        if editor.key_comment(f"handoffs.{role_name}", key)
                    ),
                    "",
                ),
            )
        raw_launch = raw_role_table.get("launch", {})
        raw_launch_table = raw_launch if isinstance(raw_launch, dict) else {}
        for key in _ROLE_EXECUTION_KEYS:
            if key not in target_role or key in raw_role_table:
                continue
            source_table = f"roles.{role_name}.launch"
            source_key = key
            if key not in raw_launch_table:
                source_table = f"handoffs.{role_name}"
                source_key = (
                    "agent"
                    if key == "target"
                    and editor._key_index(source_table, "agent") is not None
                    else key
                )
            editor.add_key(
                f"roles.{role_name}",
                key,
                target_role[key],
                comment=editor.key_comment(source_table, source_key),
            )

    target_reviews = target.get("reviews", {})
    raw_reviews = raw.get("reviews", {})
    if not isinstance(raw_reviews, dict):
        raw_reviews = {}
    for key, value in target_reviews.items():
        if key not in raw_reviews:
            editor.add_key("reviews", key, value)

    editor.remove_legacy_tables()
    editor.remove_legacy_tombstones()

    result = editor.bytes()
    try:
        parsed = tomllib.loads(result.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        _diagnose(
            "comment-preserving-transform-unavailable",
            "<root>",
            scope,
            "bounded migration edits did not produce valid TOML",
            "simplify the legacy layout without removing operator comments",
        )
    if parsed != target:
        _diagnose(
            "comment-preserving-transform-unavailable",
            "<root>",
            scope,
            "bounded migration edits cannot represent this supported source layout",
            "simplify the legacy layout without removing operator comments",
        )
    return result


def _semantic_view(record: Mapping[str, Any]) -> Dict[str, Any]:
    view = copy.deepcopy(dict(record))
    view.pop("record_schema_version", None)
    view.pop("schema_identity", None)
    view.pop("project_schema_version", None)
    return view


def _changed_launch_activities(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> Tuple[str, ...]:
    changed: set[str] = set()
    before_roles = before.get("roles", {})
    after_roles = after.get("roles", {})
    for role_name in set(before_roles) | set(after_roles):
        before_role = before_roles.get(role_name, {})
        after_role = after_roles.get(role_name, {})
        before_launch = tuple(before_role.get("auto_launch", ()))
        after_launch = tuple(after_role.get("auto_launch", ()))
        for activity in set(before_launch) ^ set(after_launch):
            changed.add(activity)
        before_source = before_role.get("attribution", {}).get("auto_launch")
        after_source = after_role.get("attribution", {}).get("auto_launch")
        if before_source != after_source:
            changed.update(before_launch)
            changed.update(after_launch)
    return tuple(activity for activity in ACTIVITY_ORDER if activity in changed)


def _raise_registered_project_diagnostic(
    project_id: str, diagnostic: Mapping[str, Any]
) -> None:
    _diagnose(
        str(diagnostic.get("code", "registered-project-unmigratable")),
        str(diagnostic.get("field", "cartopian.toml")),
        f"registered-project:{project_id}",
        (
            f"registered project {project_id!r} cannot be evaluated: "
            f"{diagnostic.get('message', 'configuration is not migratable')}"
        ),
        str(
            diagnostic.get(
                "recovery",
                "repair the named registered project before global migration",
            )
        ),
        pending=(
            diagnostic.get("classification")
            == "pending-operator-decision"
        ),
    )


def _global_write_impacts(
    project_root: Path,
    home: Path,
    global_after: bytes,
    registered_projects: Sequence[Mapping[str, Any]],
) -> Tuple[Dict[str, Any], ...]:
    """Compare every other project's source record with post-global-write state."""
    prospective_global = tomllib.loads(global_after.decode("utf-8"))
    current_real = os.path.realpath(os.fspath(project_root))
    impacts: List[Dict[str, Any]] = []
    for entry in registered_projects:
        project_id = str(entry["id"])
        registered_path = Path(str(entry["path"]))
        try:
            registered_real = os.path.realpath(
                os.fspath(registered_path.resolve(strict=True))
            )
        except OSError:
            _diagnose(
                "registry-coverage-incomplete",
                project_id,
                f"registered-project:{project_id}",
                f"registered project {project_id!r} cannot be resolved",
                "repair or remove the named unresolved registry entry",
            )
        if registered_real == current_real:
            continue

        other_plan = plan_configuration_migration(
            registered_path,
            home_root=home,
            _validate_global_inventory=False,
        )
        if other_plan.status in ("refused", "pending"):
            diagnostic = (
                other_plan.diagnostics[0]
                if other_plan.diagnostics
                else {
                    "code": "registered-project-unmigratable",
                    "field": "cartopian.toml",
                    "message": "configuration is not migratable",
                    "recovery": (
                        "repair the named registered project before global migration"
                    ),
                }
            )
            _raise_registered_project_diagnostic(project_id, diagnostic)
        if other_plan.source_effective is None:
            _diagnose(
                "global-impact-unvalidated",
                project_id,
                f"registered-project:{project_id}",
                f"registered project {project_id!r} has no source semantic record",
                "repair the named project before global migration",
            )

        other_project_path = registered_path / "cartopian.toml"
        other_local_path = registered_path / "cartopian.local.toml"
        _project_bytes, other_project_raw = _read_scope(
            other_project_path, "project", required=True
        )
        _local_bytes, other_local_raw = _read_scope(
            other_local_path, "machine-local", required=False
        )
        try:
            prospective_effective = _resolve_compatibility_configuration(
                prospective_global,
                other_project_raw,
                other_local_raw,
                other_plan.detected_schema_version,
            )
        except ConfigDiagnostic as exc:
            _raise_registered_project_diagnostic(
                project_id, exc.as_record()
            )
        if _semantic_view(other_plan.source_effective) == _semantic_view(
            prospective_effective
        ):
            continue
        changed_activities = _changed_launch_activities(
            other_plan.source_effective, prospective_effective
        )
        impacts.append(
            {
                "id": project_id,
                "changed_activities": list(changed_activities),
            }
        )
    return tuple(sorted(impacts, key=lambda item: item["id"]))


def _source_facts(states: Sequence[_ScopeState]) -> Tuple[Dict[str, str], ...]:
    facts: List[Dict[str, str]] = []
    for state in states:
        facts.extend(state.facts)
        if not state.facts:
            facts.append(
                {"scope": state.scope, "field": "<root>", "form": "preferred"}
            )
    return tuple(facts)


def _target_facts(states: Sequence[_ScopeState]) -> Tuple[Dict[str, str], ...]:
    facts: List[Dict[str, str]] = []
    for state in states:
        facts.append(
            {
                "scope": state.scope,
                "field": "<root>",
                "form": "preferred" if state.raw or state.scope == "project" else "absent",
            }
        )
    return tuple(facts)


def _entry_chain(
    detected: Optional[str], current: str, has_residual: bool
) -> Tuple[ConfigurationMigrationEntry, ...]:
    entries: List[ConfigurationMigrationEntry] = []
    if detected is None or detected in ("v0.1.0", "v0.2.0", "v0.3.0", "v0.4.0"):
        entries.append(CONFIGURATION_MIGRATION_ENTRIES[0])
        if _version_tuple(current) >= (0, 6, 0):
            entries.append(CONFIGURATION_MIGRATION_ENTRIES[1])
        if _version_tuple(current) >= (0, 7, 0):
            entries.append(CONFIGURATION_MIGRATION_ENTRIES[2])
        if _version_tuple(current) >= (0, 8, 0):
            entries.append(CONFIGURATION_MIGRATION_ENTRIES[3])
    elif detected == "v0.5.0":
        entries.append(CONFIGURATION_MIGRATION_ENTRIES[1])
        if _version_tuple(current) >= (0, 7, 0):
            entries.append(CONFIGURATION_MIGRATION_ENTRIES[2])
        if _version_tuple(current) >= (0, 8, 0):
            entries.append(CONFIGURATION_MIGRATION_ENTRIES[3])
    elif detected == "v0.6.0":
        entries.append(CONFIGURATION_MIGRATION_ENTRIES[2])
        if _version_tuple(current) >= (0, 8, 0):
            entries.append(CONFIGURATION_MIGRATION_ENTRIES[3])
    elif detected == "v0.7.0":
        # v0.7 -> v0.8 introduces no configuration key. It advances the marker
        # after the resolver confirms effective behavior is unchanged — the
        # operator-intent contract lives in project artifacts, not in config,
        # and migration never fabricates an attestation or promotes an
        # unattested legacy decision.
        if _version_tuple(current) >= (0, 8, 0):
            entries.append(CONFIGURATION_MIGRATION_ENTRIES[3])
    elif detected == current and has_residual:
        entries.append(CONFIGURATION_MIGRATION_ENTRIES[4])
    return tuple(entries)


def _checkpoint_info(project_root: Path) -> Tuple[str, Optional[Dict[str, Any]]]:
    path = project_root / CHECKPOINT_RELATIVE_PATH
    if not os.path.lexists(path):
        return "absent", None
    if os.path.islink(path):
        return "stale", None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "stale", None
    if not isinstance(value, dict) or value.get("schema_identity") != CHECKPOINT_IDENTITY:
        return "stale", None
    status = value.get("status")
    if status == "complete":
        return "complete", value
    if status == "in-progress":
        return "in-progress", value
    return "stale", value


def _validate_migration_authorities(current_version: str) -> None:
    """Fail closed when shipped config/filesystem/changelog registries diverge."""
    from cli import migrations

    changelog_path = (
        Path(__file__).resolve().parents[1] / "protocol" / "CHANGELOG.md"
    )
    try:
        changelog = changelog_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        _diagnose(
            "migration-authority-divergence",
            "protocol/CHANGELOG.md",
            "installed-content",
            "the historical migration contract cannot be read",
            "repair or reinstall verified Cartopian content",
        )
    headings = tuple(
        re.findall(r"^### (v\d+\.\d+\.\d+)\b", changelog, re.MULTILINE)
    )
    declared_versions = set(headings)
    declared_versions.update(
        re.findall(
            r"^- \*\*Protocol version:\*\* `(v\d+\.\d+\.\d+)`",
            changelog,
            re.MULTILINE,
        )
    )
    if not headings or headings[0] != current_version:
        _diagnose(
            "migration-authority-divergence",
            "protocol/CHANGELOG.md",
            "installed-content",
            "the changelog head and shipped project schema marker disagree",
            "repair or reinstall one internally consistent Cartopian release",
        )
    known = set(declared_versions)
    known.add("v0.1.0")
    config_versions = {
        value
        for entry in CONFIGURATION_MIGRATION_ENTRIES
        for value in entry.from_identities + (entry.to_identity,)
        if value != "unset"
    }
    filesystem_versions = set(migrations.ENTRY_VERSIONS)
    if (
        not config_versions <= known
        or not filesystem_versions <= declared_versions
        or any(
            _version_tuple(value) > _version_tuple(current_version)
            for value in config_versions | filesystem_versions
        )
    ):
        _diagnose(
            "migration-authority-divergence",
            "<registry>",
            "installed-content",
            (
                "configuration, filesystem, and historical migration "
                "registries do not describe one shipped version set"
            ),
            "repair the shipped migration registries before migrating",
        )


def _refusal_plan(
    current_version: str,
    diagnostic: MigrationDiagnostic,
    checkpoint_state: str,
    *,
    detected_schema_version: Optional[str] = None,
    compatibility_state: str = "unsupported",
) -> ConfigurationMigrationPlan:
    record = diagnostic.as_record()
    kwargs: Dict[str, Any] = {
        "status": "pending" if diagnostic.pending else "refused",
        "compatibility_state": compatibility_state,
        "current_schema_version": current_version,
        "detected_schema_version": detected_schema_version,
        "checkpoint_state": checkpoint_state,
        "diagnostics": (record,),
    }
    if diagnostic.pending:
        kwargs["pending_choices"] = (record,)
        kwargs["conflicts"] = (record,)
    else:
        kwargs["conflicts"] = (
            (record,) if diagnostic.code == "conflicting-definition" else ()
        )
    return ConfigurationMigrationPlan(**kwargs)


def plan_configuration_migration(
    project_root: Path,
    *,
    home_root: Optional[Path] = None,
    _validate_global_inventory: bool = True,
) -> ConfigurationMigrationPlan:
    """Read and validate all three scopes, returning a deterministic plan."""
    project_root = Path(project_root)
    home = Path.home() if home_root is None else Path(home_root)
    checkpoint_state, _checkpoint = _checkpoint_info(project_root)
    observed_marker: Optional[str] = None
    try:
        current_version = read_shipped_project_schema_version()
    except (OSError, RuntimeError, ValueError):
        diagnostic = MigrationDiagnostic(
            "shipped-marker-unavailable",
            "protocol/CHANGELOG.md",
            "installed-content",
            "current governed-project schema identity cannot be read",
            "repair or reinstall the verified Cartopian content",
        )
        return _refusal_plan("unknown", diagnostic, checkpoint_state)
    try:
        _validate_migration_authorities(current_version)
        project_path = project_root / "cartopian.toml"
        project_before, project_raw = _read_scope(
            project_path, "project", required=True
        )
        raw_project_table = project_raw.get("project")
        if isinstance(raw_project_table, dict):
            raw_marker = raw_project_table.get(
                "project_schema_version",
                raw_project_table.get("protocol_version"),
            )
            if isinstance(raw_marker, str):
                observed_marker = raw_marker
        # The protocol gate is intentionally first and uses only the governed
        # project's schema identity. A malformed/newer marker must not be
        # masked by unrelated legacy or global facts.
        marker_checked_project = copy.deepcopy(project_raw)
        detected, marker_changed, marker_facts = _marker(
            marker_checked_project, current_version
        )

        global_path = home / ".cartopian" / "cartopian.toml"
        local_path = project_root / "cartopian.local.toml"
        global_before, global_raw = _read_scope(
            global_path, "global", required=False
        )
        local_before, local_raw = _read_scope(
            local_path, "machine-local", required=False
        )
        _check_root_keys(global_raw, "global")
        _check_root_keys(project_raw, "project")
        _check_root_keys(local_raw, "machine-local")

        global_canonical, global_changed, global_facts, global_permissions = (
            _normalize_roles_and_handoffs(global_raw, "global")
        )
        project_canonical, project_changed, project_facts, project_permissions = (
            _normalize_roles_and_handoffs(marker_checked_project, "project")
        )
        if _LEGACY_TOMBSTONE_RE.search(global_before):
            global_changed = True
            global_facts.append(
                {
                    "scope": "global",
                    "field": "<comments>",
                    "form": "migration-generated-legacy-tombstone",
                }
            )
        if _LEGACY_TOMBSTONE_RE.search(project_before):
            project_changed = True
            project_facts.append(
                {
                    "scope": "project",
                    "field": "<comments>",
                    "form": "migration-generated-legacy-tombstone",
                }
            )
        local_canonical = copy.deepcopy(local_raw)

        project_changed = project_changed or marker_changed
        project_facts.extend(marker_facts)

        global_state = _ScopeState(
            "global",
            global_path,
            global_before,
            global_raw,
            global_canonical,
            global_changed,
            global_facts,
            global_permissions,
        )
        project_state = _ScopeState(
            "project",
            project_path,
            project_before,
            project_raw,
            project_canonical,
            project_changed,
            project_facts,
            project_permissions,
        )
        local_state = _ScopeState(
            "machine-local",
            local_path,
            local_before,
            local_raw,
            local_canonical,
        )
        states = (global_state, project_state, local_state)

        attribution_changes = _add_implicit_pre_v050_review(
            global_canonical, project_canonical, detected
        )
        if attribution_changes:
            project_state.changed = True
            project_state.facts.extend(
                {
                    "scope": "project",
                    "field": change["field"],
                    "form": "legacy-implicit-review",
                }
                for change in attribution_changes
            )

        _map_target_permissions(
            global_state,
            project_state,
            global_canonical,
            project_canonical,
        )

        # The source is interpreted independently from the authored legacy
        # bytes. The target is the prospective preferred record. Keeping these
        # paths separate makes this a real semantic gate rather than a
        # comparison of two copies of the post-transform target.
        target_project = copy.deepcopy(project_canonical)
        target_project["project"]["project_schema_version"] = current_version
        try:
            source_effective = _resolve_compatibility_configuration(
                global_raw, project_raw, local_raw, detected
            )
            target_effective = resolve_configuration(
                global_canonical, target_project, local_canonical
            )
        except ConfigDiagnostic as exc:
            code = "unknown-grant" if "unknown-value" in exc.code and ".grants" in exc.field else exc.code
            _diagnose(
                code,
                exc.field,
                exc.scope,
                exc.message,
                exc.recovery,
            )
        if _semantic_view(source_effective) != _semantic_view(target_effective):
            _diagnose(
                "semantic-drift",
                "<resolved>",
                "resolved",
                "prospective preferred configuration changes effective behavior",
                "resolve the differing source facts before migration",
            )

        has_residual = any(state.changed for state in states)
        entries = _entry_chain(detected, current_version, has_residual)
        steps: List[MigrationStep] = []
        global_step: Optional[MigrationStep] = None
        global_after: Optional[bytes] = None
        if global_state.changed:
            global_after = _render_preserving(
                global_before, global_raw, global_canonical, "global"
            )
            if _validate_global_inventory:
                registry_file = home / ".cartopian" / "projects.json"
                try:
                    registered_projects = read_registry(registry_file)
                except MalformedRegistry:
                    _diagnose(
                        "global-impact-unvalidated",
                        "projects.json",
                        "global",
                        "registered-project inventory is malformed",
                        "repair the registry before changing global configuration",
                    )
                impacts = _global_write_impacts(
                    project_root,
                    home,
                    global_after,
                    registered_projects,
                )
                if impacts:
                    summary = ", ".join(
                        (
                            f"{impact['id']} "
                            f"({', '.join(impact['changed_activities'])})"
                        )
                        for impact in impacts
                    )
                    _diagnose(
                        "cross-project-semantic-change",
                        "roles.*.auto_launch",
                        "global",
                        (
                            "the global rewrite would change resolved launch "
                            f"semantics for registered projects: {summary}"
                        ),
                        (
                            "choose an approved cross-project materialization "
                            "plan or preserve the global legacy source"
                        ),
                        pending=True,
                        affected_projects=impacts,
                    )
            global_step = MigrationStep(
                "write-global",
                "write-global",
                "global",
                "cartopian.toml",
                global_path,
                home / ".cartopian",
                global_before,
                global_after,
                ("parse-target", "validate-global-scope", "resolve-equivalence"),
            )

        source_marker = detected or "v0.1.0"
        interim_project = copy.deepcopy(project_canonical)
        interim_project["project"]["project_schema_version"] = source_marker
        final_project = copy.deepcopy(project_canonical)
        final_project["project"]["project_schema_version"] = current_version
        interim_bytes = _render_preserving(
            project_before, project_raw, interim_project, "project"
        )
        interim_raw = tomllib.loads(interim_bytes.decode("utf-8"))
        final_bytes = _render_preserving(
            interim_bytes, interim_raw, final_project, "project"
        )
        structural_change = project_state.changed
        if structural_change:
            steps.append(
                MigrationStep(
                    "write-project",
                    "write-project",
                    "project",
                    "cartopian.toml",
                    project_path,
                    project_root,
                    project_before,
                    interim_bytes,
                    (
                        "parse-target",
                        "validate-project-scope",
                        "resolve-equivalence",
                        "retain-source-marker",
                    ),
                )
            )
        # Materialize project-owned compatibility facts before retiring a
        # global legacy source they depend on. This makes every evidenced
        # interruption boundary independently replannable without narrowing
        # effective permissions. The governed-project marker still advances
        # only after both configuration writes.
        if global_step is not None:
            steps.append(global_step)
        if source_marker != current_version:
            marker_before = interim_bytes if structural_change else project_before
            steps.append(
                MigrationStep(
                    "update-marker",
                    "update-marker",
                    "project",
                    "cartopian.toml",
                    project_path,
                    project_root,
                    marker_before,
                    final_bytes,
                    ("canonical-resolution", "all-prior-steps-evidenced"),
                )
            )
        elif structural_change and steps:
            # No advancement is required for a current-marker partial form;
            # its one project structure write is already final.
            project_step = steps[-1]
            if project_step.kind == "write-project":
                steps[-1] = MigrationStep(
                    project_step.step_id,
                    project_step.kind,
                    project_step.scope,
                    project_step.relative_target,
                    project_step.path,
                    project_step.base,
                    project_step.before,
                    final_bytes,
                    project_step.validation,
                )

        if not steps:
            status = "noop"
            compatibility = "canonical"
        else:
            status = "planned"
            if checkpoint_state == "in-progress":
                compatibility = "partial"
            elif detected == current_version:
                compatibility = "partial"
            elif detected in (None, "v0.1.0", "v0.2.0", "v0.3.0", "v0.4.0"):
                compatibility = "legacy"
            else:
                compatibility = "transitional"

        marker_record = {
            "field": "project.project_schema_version",
            "from": detected or "unset",
            "to": current_version,
            "order": "last",
            "status": (
                "planned"
                if source_marker != current_version
                else "already-current"
            ),
        }
        gates: List[str] = []
        for entry in entries:
            for gate in entry.validation_gates:
                if gate not in gates:
                    gates.append(gate)
        return ConfigurationMigrationPlan(
            status=status,
            compatibility_state=compatibility,
            current_schema_version=current_version,
            detected_schema_version=detected,
            entries=entries,
            source_facts=_source_facts(states),
            target_facts=_target_facts(states),
            steps=tuple(steps),
            validation_gates=tuple(gates),
            marker_update=marker_record,
            equivalence={
                "status": "passed",
                "differences": (
                    ["project-schema-marker"]
                    + (
                        ["legacy-review-attribution"]
                        if attribution_changes
                        else []
                    )
                ),
            },
            attribution_changes=attribution_changes,
            checkpoint_state=checkpoint_state,
            source_effective=source_effective,
            target_effective=target_effective,
        )
    except MigrationDiagnostic as diagnostic:
        compatibility = "unsupported"
        if diagnostic.code == "conflicting-definition":
            compatibility = "partial"
        elif observed_marker in ("v0.5.0",):
            compatibility = "transitional"
        elif observed_marker in SUPPORTED_OLDER_MARKERS or observed_marker is None:
            compatibility = "legacy"
        return _refusal_plan(
            current_version,
            diagnostic,
            checkpoint_state,
            detected_schema_version=observed_marker,
            compatibility_state=compatibility,
        )

def _atomic_write(
    path: Path,
    base: Path,
    before: Optional[bytes],
    after: bytes,
    *,
    allow_create: bool = False,
) -> None:
    canonical_base = os.path.realpath(os.fspath(base))
    canonical_parent = os.path.realpath(os.fspath(path.parent))
    if (
        not os.path.isdir(canonical_base)
        or os.path.islink(os.fspath(base))
        or os.path.islink(os.fspath(path.parent))
    ):
        raise GuardRefusal("unsafe-parent", "migration parent cannot be verified")
    if canonical_parent != canonical_base and not canonical_parent.startswith(
        canonical_base + os.sep
    ):
        raise GuardRefusal("outside-allowlist", "migration target escapes its scope")
    raw_path = os.path.abspath(os.fspath(path))
    exists = os.path.lexists(raw_path)
    if os.path.islink(raw_path):
        raise GuardRefusal("symlink", "migration target is a symlink")
    expected_leaf = None
    safe_mode = 0o644
    if exists:
        st = os.lstat(raw_path)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
            raise GuardRefusal(
                "unsafe-target", "migration target must be a single-link regular file"
            )
        expected_leaf = (st.st_dev, st.st_ino)
        safe_mode = (st.st_mode & 0o777) & ~0o111
        if before is not None and path.read_bytes() != before:
            raise GuardRefusal(
                "unexpected-content", "migration target changed after planning"
            )
    elif not allow_create:
        raise GuardRefusal("missing-target", "migration target disappeared")
    snapshot = _snapshot_chain(canonical_parent, canonical_base)
    tmp_name = make_tmp_name(path.name)
    kwargs = {
        "expected_leaf": expected_leaf,
        "expect_absent": not exists,
        "expected_data": before if exists else None,
    }
    if DIR_FD_SUPPORTED:
        _atomic_write_via_dir_fd(
            canonical_parent,
            snapshot,
            path.name,
            tmp_name,
            after,
            safe_mode,
            **kwargs,
        )
    else:
        _atomic_write_via_path(
            canonical_parent,
            snapshot,
            path.name,
            tmp_name,
            after,
            safe_mode,
            **kwargs,
        )


def _ensure_evidence_parent(project_root: Path) -> Path:
    parent = project_root / ".cartopian"
    if os.path.lexists(parent):
        st = os.lstat(parent)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise GuardRefusal(
                "unsafe-checkpoint", "migration evidence parent is not an owned directory"
            )
    else:
        os.mkdir(parent, 0o755)
    return parent


def _write_checkpoint(
    project_root: Path,
    plan: ConfigurationMigrationPlan,
    completed: Sequence[Dict[str, str]],
    status: str,
    *,
    evidence_parent_created: bool,
) -> None:
    parent = _ensure_evidence_parent(project_root)
    path = parent / "config-migration.json"
    payload = {
        "schema_identity": CHECKPOINT_IDENTITY,
        "migration_entries": [entry.identity for entry in plan.entries],
        "target_schema_version": plan.current_schema_version,
        "status": status,
        "evidence_parent_created": evidence_parent_created,
        "completed_steps": list(completed),
    }
    data = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")
    before = path.read_bytes() if path.exists() and not path.is_symlink() else None
    _atomic_write(
        path,
        project_root,
        before,
        data,
        allow_create=before is None,
    )


def _remove_checkpoint(project_root: Path) -> bool:
    path = project_root / CHECKPOINT_RELATIVE_PATH
    raw_path = os.fspath(path)
    if not os.path.lexists(raw_path):
        return False
    if os.path.islink(raw_path):
        raise GuardRefusal(
            "unsafe-checkpoint", "migration checkpoint is a symlink"
        )
    st = os.lstat(raw_path)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
        raise GuardRefusal(
            "unsafe-checkpoint",
            "migration checkpoint must be a single-link regular file",
        )
    root = os.path.realpath(os.fspath(project_root))
    parent = os.path.realpath(os.fspath(path.parent))
    if parent != root + os.sep + ".cartopian":
        raise GuardRefusal(
            "unsafe-checkpoint", "migration checkpoint parent changed"
        )
    snapshot = _snapshot_chain(parent, root)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    fd = os.open(raw_path, flags)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino):
            raise GuardRefusal(
                "unsafe-checkpoint", "migration checkpoint identity changed"
            )
    finally:
        os.close(fd)
    _reverify_chain(snapshot)
    os.unlink(raw_path)
    return True


def _remove_empty_evidence_parent(
    project_root: Path, *, created_by_migration: bool
) -> bool:
    if not created_by_migration:
        return False
    parent = project_root / ".cartopian"
    raw_parent = os.fspath(parent)
    if not os.path.lexists(raw_parent):
        return False
    st = os.lstat(raw_parent)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise GuardRefusal(
            "unsafe-checkpoint",
            "migration-created evidence parent changed type",
        )
    expected = os.path.realpath(os.fspath(project_root)) + os.sep + ".cartopian"
    if os.path.realpath(raw_parent) != expected:
        raise GuardRefusal(
            "unsafe-checkpoint",
            "migration-created evidence parent changed containment",
        )
    try:
        os.rmdir(raw_parent)
    except OSError:
        return False
    return True


def execute_configuration_migration(
    project_root: Path,
    plan: ConfigurationMigrationPlan,
    *,
    home_root: Optional[Path] = None,
    interrupt_after_step: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply one closed plan, recording resumable evidence after every step."""
    project_root = Path(project_root)
    home = Path.home() if home_root is None else Path(home_root)
    expected_paths = {
        "global": home / ".cartopian" / "cartopian.toml",
        "project": project_root / "cartopian.toml",
    }
    if plan.status in ("refused", "pending"):
        diagnostic = plan.diagnostics[0] if plan.diagnostics else {}
        raise MigrationRefused(
            diagnostic.get("code", "non-executable-plan"),
            diagnostic.get("message", "migration plan is not executable"),
        )
    if plan.status == "noop":
        removed = False
        parent_removed = False
        if plan.checkpoint_state in ("complete", "in-progress"):
            _state, checkpoint = _checkpoint_info(project_root)
            evidence_parent_created = bool(
                checkpoint
                and checkpoint.get("evidence_parent_created") is True
            )
            removed = _remove_checkpoint(project_root)
            parent_removed = _remove_empty_evidence_parent(
                project_root,
                created_by_migration=evidence_parent_created,
            )
        return {
            "status": "noop",
            "compatibility_state": "canonical",
            "operations": [],
            "validation": {"status": "passed"},
            "marker": plan.current_schema_version,
            "checkpoint": {
                "lifecycle": "in-progress-only",
                "status": "removed" if removed else "absent",
                "parent_status": (
                    "removed" if parent_removed else "preserved-or-absent"
                ),
            },
        }
    if plan.status != "planned":
        raise MigrationRefused("non-executable-plan", "unknown migration plan status")
    if not plan.steps or plan.steps[-1].kind != "update-marker":
        if plan.detected_schema_version != plan.current_schema_version:
            raise MigrationRefused(
                "marker-order", "an advancing plan must end with update-marker"
            )

    operations: List[Dict[str, Any]] = []
    checkpoint_state, checkpoint = _checkpoint_info(project_root)
    evidence_parent_created = (
        bool(checkpoint.get("evidence_parent_created"))
        if checkpoint_state == "in-progress" and checkpoint is not None
        else not os.path.lexists(project_root / ".cartopian")
    )
    completed: List[Dict[str, str]] = []
    if checkpoint_state == "in-progress" and checkpoint is not None:
        current_identities = {}
        for scope, path in expected_paths.items():
            try:
                current_identities[scope] = _identity(path.read_bytes())
            except OSError:
                pass
        for item in checkpoint.get("completed_steps", []):
            if (
                isinstance(item, dict)
                and item.get("scope") in current_identities
                and item.get("output_identity")
                == current_identities[item["scope"]]
                and all(
                    isinstance(item.get(key), str)
                    for key in (
                        "id",
                        "scope",
                        "input_identity",
                        "output_identity",
                        "validation",
                    )
                )
            ):
                completed.append(
                    {
                        key: item[key]
                        for key in (
                            "id",
                            "scope",
                            "input_identity",
                            "output_identity",
                            "validation",
                        )
                    }
                )
    for step in plan.steps:
        if step.scope not in expected_paths or step.path != expected_paths[step.scope]:
            raise MigrationRefused(
                "outside-allowlist", "plan contains a non-owned configuration path"
            )
        try:
            current = step.path.read_bytes()
        except OSError as exc:
            raise MigrationRefused(
                "unreadable-target", "configuration target cannot be read"
            ) from exc
        if current == step.after:
            operation_status = "recognized-complete"
        elif current != step.before:
            raise MigrationRefused(
                "stale-plan", "configuration bytes changed after planning"
            )
        else:
            if step.kind == "update-marker":
                pre_marker = plan_configuration_migration(
                    project_root, home_root=home
                )
                if (
                    pre_marker.status != "planned"
                    or not pre_marker.steps
                    or pre_marker.steps[-1].kind != "update-marker"
                    or pre_marker.steps[-1].after != step.after
                ):
                    raise MigrationRefused(
                        "marker-validation",
                        "current scopes no longer validate for marker advancement",
                    )
            _atomic_write(step.path, step.base, step.before, step.after)
            operation_status = "applied"
        operation = {
            "id": step.step_id,
            "kind": step.kind,
            "scope": step.scope,
            "target": step.relative_target,
            "status": operation_status,
            "output_identity": _identity(step.after),
            "validation": "passed",
        }
        operations.append(operation)
        completed.append(
            {
                "id": step.step_id,
                "scope": step.scope,
                "input_identity": _identity(step.before),
                "output_identity": _identity(step.after),
                "validation": "passed",
            }
        )
        _write_checkpoint(
            project_root,
            plan,
            completed,
            "in-progress",
            evidence_parent_created=evidence_parent_created,
        )
        if interrupt_after_step == step.step_id:
            raise MigrationInterrupted(
                f"injected interruption after evidenced step {step.step_id}"
            )

    # Replan from disk as the final validation. A complete migration must be
    # canonical and have no remaining transformation.
    final_plan = plan_configuration_migration(project_root, home_root=home)
    if final_plan.status != "noop":
        raise MigrationRefused(
            "post-validation-failed",
            "completed writes did not resolve to one canonical no-op state",
        )
    _remove_checkpoint(project_root)
    parent_removed = _remove_empty_evidence_parent(
        project_root,
        created_by_migration=evidence_parent_created,
    )
    return {
        "status": "complete",
        "compatibility_state": "canonical",
        "operations": operations,
        "validation": {
            "status": "passed",
            "semantic_equivalence": plan.equivalence["status"],
            "rerun": "noop",
        },
        "marker": plan.current_schema_version,
        "checkpoint": {
            "lifecycle": "in-progress-only",
            "status": "removed-after-validation",
            "parent_status": (
                "removed-after-validation"
                if parent_removed
                else "preserved"
            ),
        },
    }
