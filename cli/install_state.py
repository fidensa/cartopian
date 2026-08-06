"""Authoritative install and update state contract.

This pure standard-library layer does not mutate installations, restart
clients, migrate projects, or persist runs. Adapters provide observations; the
contract canonicalizes them, validates authority boundaries, derives safe
outcomes, and exposes the stable machine projection shared by CLI and MCP.
"""
from __future__ import annotations

import copy
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from cli.config_schema import identity_contract
from cli.restart_state import RESTART_REASON_CODES, RESTART_STATUSES

SCHEMA_IDENTITY = "cartopian-install-update-state-v1"
RECORD_SCHEMA_VERSION = 1

OPERATION_KINDS: Tuple[str, ...] = (
    "fresh-install",
    "update",
    "repair",
    "resume",
    "verification",
)
SOURCE_KINDS: Tuple[str, ...] = (
    "release",
    "local-checkout",
    "installed-content",
    "portable-archive",
)
SOURCE_STATES: Tuple[str, ...] = (
    "known",
    "unknown",
    "malformed",
    "contradictory",
)
PEER_IDENTITY_KINDS: Tuple[str, ...] = tuple(identity_contract())
SURFACE_KINDS: Tuple[str, ...] = (
    "core-files",
    "mcp-server-files",
    "wrappers",
    "bridges",
    "client-registrations",
    "client-configuration",
    "verification-content",
    "project-schema-migration-offers",
)
LIFECYCLE_STATES: Tuple[str, ...] = (
    "preflight",
    "planned",
    "applying",
    "verifying",
    "repair-offered",
    "migration-offered",
    "restart-required",
    "restart-verified",
    "complete",
    "blocked",
    "failed",
)
SURFACE_STATES: Tuple[str, ...] = (
    "current",
    "verified",
    "stale",
    "pending",
    "offered",
    "declined",
    "deferred",
    "missing",
    "dirty",
    "unverified",
    "unknown",
    "malformed",
    "unsupported-newer",
    "contradictory",
    "blocked",
    "failed",
    "not-applicable",
)
CHECKPOINT_STATUSES: Tuple[str, ...] = (
    "pending",
    "in-progress",
    "completed",
    "unverified",
    "blocked",
    "failed",
)
CHECKPOINT_PHASES: Tuple[str, ...] = (
    "preflight",
    "plan",
    "apply",
    "repair",
    "verify",
    "restart",
    "migration-offer",
)
VERIFICATION_STATES: Tuple[str, ...] = (
    "unknown",
    "unverified",
    "verified",
    "failed",
)
RETRY_SAFETY_STATES: Tuple[str, ...] = (
    "idempotent",
    "inspect-before-retry",
    "refuse-replay",
)
OPERATOR_CHOICE_STATES: Tuple[str, ...] = (
    "not-offered",
    "offered",
    "authorized",
    "declined",
    "deferred",
)
OFFERED_ACTIONS: Tuple[str, ...] = (
    "repair",
    "replace",
    "register",
    "reconfigure",
    "migrate",
    "restart",
)
RESTART_STATES: Tuple[str, ...] = (
    "not-required",
    "required",
    "pending",
    "verified",
    "blocked",
)
RESTART_INSTRUCTION_CLASSES: Tuple[str, ...] = (
    "none",
    "restart-client",
    "reconnect-mcp",
    "reopen-shell",
)
MIGRATION_APPLICABILITY: Tuple[str, ...] = (
    "not-applicable",
    "applicable",
    "unknown",
    "unsupported-newer",
)
MIGRATION_RESULTS: Tuple[str, ...] = (
    "not-run",
    "completed",
    "failed",
    "blocked",
)
OUTCOME_STATUSES: Tuple[str, ...] = (
    "in-progress",
    "complete",
    "complete-qualified",
    "blocked",
    "failed",
)
OUTCOME_CLAIMS: Tuple[str, ...] = (
    "none",
    "fully-updated",
    "qualified-complete",
)
DIAGNOSTIC_SEVERITIES: Tuple[str, ...] = ("info", "warning", "error")

ALLOWED_TRANSITIONS: "OrderedDict[str, Tuple[str, ...]]" = OrderedDict(
    (
        ("preflight", ("planned", "blocked", "failed")),
        (
            "planned",
            (
                "applying",
                "verifying",
                "repair-offered",
                "migration-offered",
                "restart-required",
                "blocked",
                "failed",
            ),
        ),
        (
            "applying",
            (
                "verifying",
                "repair-offered",
                "migration-offered",
                "restart-required",
                "blocked",
                "failed",
            ),
        ),
        (
            "verifying",
            (
                "repair-offered",
                "migration-offered",
                "restart-required",
                "complete",
                "blocked",
                "failed",
            ),
        ),
        (
            "repair-offered",
            (
                "applying",
                "verifying",
                "migration-offered",
                "restart-required",
                "blocked",
                "failed",
            ),
        ),
        (
            "migration-offered",
            ("verifying", "restart-required", "blocked", "failed"),
        ),
        ("restart-required", ("restart-verified", "blocked", "failed")),
        ("restart-verified", ("verifying", "complete", "blocked", "failed")),
        ("complete", ()),
        ("blocked", ()),
        ("failed", ()),
    )
)

STABLE_FIELDS: Tuple[str, ...] = (
    "schema_identity",
    "record_schema_version",
    "run",
    "state",
    "versions",
    "surfaces",
    "checkpoints",
    "choices",
    "restarts",
    "migrations",
    "outcome",
    "diagnostics",
)
INTERNAL_FIELDS: Tuple[str, ...] = ("internal",)

PORTABLE_EVIDENCE_FIELDS: Tuple[str, ...] = (
    "identity",
    "kind",
    "digest",
    "observed_identity",
    "observed_state",
    "authority",
    "verification",
    "platform",
    "path_class",
    "marker",
)
PORTABLE_EVIDENCE_KINDS: Tuple[str, ...] = (
    "file-digest",
    "configuration-fingerprint",
    "registration-observation",
    "process-identity",
    "schema-observation",
)
_PRIVATE_EVIDENCE_FIELDS = frozenset(
    (
        "secret",
        "secrets",
        "token",
        "password",
        "credential",
        "prompt",
        "prompt_content",
        "conversation",
        "conversation_content",
    )
)
_GOVERNANCE_EVIDENCE_FIELDS = frozenset(
    (
        "task_id",
        "phase_id",
        "plan_ref",
        "spec_id",
        "decision_id",
        "project_id",
        "prompt_id",
        "report_id",
        "request_id",
        "review_id",
        "project_management_id",
    )
)
_EXECUTABLE_EVIDENCE_FIELDS = frozenset(("executable", "executable_path"))
_DESTINATION_EVIDENCE_FIELDS = frozenset(("destination", "destination_path"))

DIAGNOSTIC_CODES: Tuple[str, ...] = (
    "invalid-schema",
    "missing-field",
    "unknown-vocabulary",
    "source-identity-unresolved",
    "duplicate-version",
    "missing-version",
    "peer-identity-substitution",
    "identity-unresolved",
    "duplicate-surface",
    "missing-surface",
    "surface-detection-contradictory",
    "duplicate-checkpoint",
    "checkpoint-evidence-missing",
    "checkpoint-verification-missing",
    "checkpoint-replay-unsafe",
    "apply-refused",
    "apply-failed",
    "prior-checkpoint-evidence-missing",
    "duplicate-choice",
    "choice-not-authorized",
    "choice-contradictory",
    "duplicate-restart-client",
    "restart-fact-missing",
    "restart-proof-missing",
    "restart-identity-contradictory",
    "duplicate-migration-offer",
    "migration-not-authorized",
    "migration-identity-contradictory",
    "invalid-transition",
    "terminal-claim-unsafe",
    "decline-provenance-missing",
    "portable-evidence-private-field",
    "portable-evidence-governance-field",
    "portable-evidence-executable",
    "portable-evidence-destination",
    "portable-evidence-field-forbidden",
)

_SEVERITY_RANK = {value: index for index, value in enumerate(DIAGNOSTIC_SEVERITIES)}
_SURFACE_RANK = {value: index for index, value in enumerate(SURFACE_KINDS)}
_VERSION_RANK = {value: index for index, value in enumerate(PEER_IDENTITY_KINDS)}
_SAFE_SURFACE_STATES = frozenset(("current", "verified", "not-applicable"))
_PENDING_SURFACE_STATES = frozenset(
    (
        "stale",
        "pending",
        "offered",
        "missing",
        "dirty",
        "unverified",
        "unknown",
        "malformed",
        "unsupported-newer",
        "contradictory",
    )
)
_HARD_IDENTITY_STATES = frozenset(
    (
        "missing",
        "dirty",
        "malformed",
        "unsupported-newer",
        "contradictory",
        "unsupported",
    )
)
_SOFT_IDENTITY_STATES = frozenset(
    ("unknown", "unverified", "older", "stale-runtime")
)
# Every peer identity may also carry these facts, whatever its own states are.
_EXPLICIT_IDENTITY_STATES: Tuple[str, ...] = (
    "unknown",
    "missing",
    "malformed",
    "unsupported-newer",
    "contradictory",
)
_VERIFIED = "verified"


class ContractRefusal(ValueError):
    """A requested transition was refused without mutating the record."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _diagnostic(
    code: str,
    severity: str,
    field: str,
    detail: str,
    recovery: str,
) -> Dict[str, str]:
    if code not in DIAGNOSTIC_CODES:
        raise ValueError(f"unknown diagnostic code: {code}")
    return {
        "code": code,
        "severity": severity,
        "field": field,
        "detail": detail,
        "recovery": recovery,
    }


def _diagnostic_sort_key(item: Mapping[str, Any]) -> Tuple[int, str, str, str]:
    return (
        _SEVERITY_RANK.get(str(item.get("severity")), len(_SEVERITY_RANK)),
        str(item.get("code", "")),
        str(item.get("field", "")),
        str(item.get("detail", "")),
    )


def supported_record_schema_version(value: Any) -> bool:
    """Report whether ``value`` is *this* contract's record schema version.

    Type identity is part of the answer. ``True`` and ``1.0`` compare equal to
    ``1`` in Python, but neither is the integer the schema declares, so a
    record carrying one was not written by a supported adapter and must not be
    read as though it were.
    """
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value == RECORD_SCHEMA_VERSION
    )


def identity_state_vocabulary(kind: str) -> Tuple[str, ...]:
    """Return the closed state vocabulary for one peer identity kind."""
    metadata = identity_contract().get(kind)
    if metadata is None:
        return ()
    values = list(metadata.get("states", ()))
    for explicit in _EXPLICIT_IDENTITY_STATES:
        if explicit not in values:
            values.append(explicit)
    return tuple(values)


def positive_identity_fact(kind: str, state: Any, verification: Any) -> bool:
    """Report whether a recorded peer-identity fact positively holds.

    Both values must come from the closed vocabularies for ``kind``, and
    neither may leave the identity unresolved: a hard state contradicts the
    claim, a soft state or any verification other than ``verified`` leaves it
    unproven, and a boolean or other non-vocabulary value is not a fact this
    contract can read at all. A consumer reading a persisted record for
    verification may strengthen its verdict only from a fact this accepts.
    """
    if state not in identity_state_vocabulary(kind):
        return False
    if state in _HARD_IDENTITY_STATES or state in _SOFT_IDENTITY_STATES:
        return False
    if verification not in VERIFICATION_STATES:
        return False
    return verification == _VERIFIED


def _ordered_vocabulary() -> "OrderedDict[str, List[str]]":
    version_states = OrderedDict(
        (kind, list(identity_state_vocabulary(kind)))
        for kind in identity_contract()
    )
    return OrderedDict(
        (
            ("operation_kinds", list(OPERATION_KINDS)),
            ("source_kinds", list(SOURCE_KINDS)),
            ("source_states", list(SOURCE_STATES)),
            ("peer_identity_kinds", list(PEER_IDENTITY_KINDS)),
            ("version_states", version_states),
            ("surface_kinds", list(SURFACE_KINDS)),
            ("lifecycle_states", list(LIFECYCLE_STATES)),
            ("surface_states", list(SURFACE_STATES)),
            ("checkpoint_statuses", list(CHECKPOINT_STATUSES)),
            ("checkpoint_phases", list(CHECKPOINT_PHASES)),
            ("verification_states", list(VERIFICATION_STATES)),
            ("retry_safety_states", list(RETRY_SAFETY_STATES)),
            ("operator_choice_states", list(OPERATOR_CHOICE_STATES)),
            ("offered_actions", list(OFFERED_ACTIONS)),
            ("restart_states", list(RESTART_STATES)),
            ("restart_statuses", list(RESTART_STATUSES)),
            ("restart_reason_codes", list(RESTART_REASON_CODES)),
            ("restart_instruction_classes", list(RESTART_INSTRUCTION_CLASSES)),
            ("migration_applicability", list(MIGRATION_APPLICABILITY)),
            ("migration_results", list(MIGRATION_RESULTS)),
            ("outcome_statuses", list(OUTCOME_STATUSES)),
            ("outcome_claims", list(OUTCOME_CLAIMS)),
            ("diagnostic_severities", list(DIAGNOSTIC_SEVERITIES)),
            ("diagnostic_codes", list(DIAGNOSTIC_CODES)),
        )
    )


def contract_projection() -> "OrderedDict[str, Any]":
    """Return deterministic machine-readable contract metadata."""
    return OrderedDict(
        (
            ("schema_identity", SCHEMA_IDENTITY),
            ("record_schema_version", RECORD_SCHEMA_VERSION),
            (
                "field_boundaries",
                OrderedDict(
                    (
                        ("stable", list(STABLE_FIELDS)),
                        ("internal", list(INTERNAL_FIELDS)),
                    )
                ),
            ),
            ("vocabularies", _ordered_vocabulary()),
            (
                "transitions",
                OrderedDict(
                    (state, list(targets))
                    for state, targets in ALLOWED_TRANSITIONS.items()
                ),
            ),
            (
                "portable_evidence",
                OrderedDict(
                    (
                        ("allowed_fields", list(PORTABLE_EVIDENCE_FIELDS)),
                        ("kinds", list(PORTABLE_EVIDENCE_KINDS)),
                        (
                            "excluded_classes",
                            [
                                "secrets-and-credentials",
                                "private-prompt-or-conversation-content",
                                "project-management-identifiers",
                                "caller-selected-executables",
                                "caller-selected-destinations",
                            ],
                        ),
                    )
                ),
            ),
        )
    )


def _canonical_items(
    items: Optional[Sequence[Mapping[str, Any]]],
    *,
    field: str,
) -> List[Dict[str, Any]]:
    copied = [copy.deepcopy(dict(item)) for item in (items or ())]
    if field == "versions":
        copied.sort(
            key=lambda item: (
                _VERSION_RANK.get(str(item.get("kind")), len(_VERSION_RANK)),
                str(item.get("kind", "")),
            )
        )
    elif field == "surfaces":
        copied.sort(
            key=lambda item: (
                _SURFACE_RANK.get(str(item.get("kind")), len(_SURFACE_RANK)),
                str(item.get("kind", "")),
                str(item.get("locator", "")),
            )
        )
    elif field == "checkpoints":
        copied.sort(key=lambda item: str(item.get("id", "")))
    elif field == "choices":
        copied.sort(
            key=lambda item: (
                _SURFACE_RANK.get(
                    str(item.get("surface")), len(_SURFACE_RANK)
                ),
                str(item.get("offered_action", "")),
                str(item.get("id", "")),
            )
        )
    elif field == "restarts":
        copied.sort(key=lambda item: str(item.get("client", "")))
    elif field == "migrations":
        copied.sort(key=lambda item: str(item.get("project_identity", "")))
    return copied


def build_record(
    *,
    operation: str,
    run_marker: str,
    source: Mapping[str, Any],
    versions: Sequence[Mapping[str, Any]],
    surfaces: Sequence[Mapping[str, Any]],
    state: str = "preflight",
    checkpoints: Optional[Sequence[Mapping[str, Any]]] = None,
    choices: Optional[Sequence[Mapping[str, Any]]] = None,
    restarts: Optional[Sequence[Mapping[str, Any]]] = None,
    migrations: Optional[Sequence[Mapping[str, Any]]] = None,
    internal: Optional[Mapping[str, Any]] = None,
) -> "OrderedDict[str, Any]":
    """Build, canonicalize, validate, and classify one state record."""
    record: "OrderedDict[str, Any]" = OrderedDict(
        (
            ("schema_identity", SCHEMA_IDENTITY),
            ("record_schema_version", RECORD_SCHEMA_VERSION),
            (
                "run",
                {
                    "operation": operation,
                    "marker": run_marker,
                    "source": copy.deepcopy(dict(source)),
                },
            ),
            ("state", state),
            ("versions", _canonical_items(versions, field="versions")),
            ("surfaces", _canonical_items(surfaces, field="surfaces")),
            (
                "checkpoints",
                _canonical_items(checkpoints, field="checkpoints"),
            ),
            ("choices", _canonical_items(choices, field="choices")),
            ("restarts", _canonical_items(restarts, field="restarts")),
            ("migrations", _canonical_items(migrations, field="migrations")),
            ("outcome", {}),
            ("diagnostics", []),
        )
    )
    if internal is not None:
        record["internal"] = copy.deepcopy(dict(internal))
    return evaluate_record(record)


def stable_projection(record: Mapping[str, Any]) -> "OrderedDict[str, Any]":
    """Return only versioned, portable external fields in contract order."""
    projected = OrderedDict(
        (field, copy.deepcopy(record.get(field))) for field in STABLE_FIELDS
    )
    checkpoints = projected.get("checkpoints")
    if isinstance(checkpoints, list):
        for item in checkpoints:
            if not isinstance(item, dict):
                continue
            evidence = item.get("evidence")
            if isinstance(evidence, Mapping):
                item["evidence"] = OrderedDict(
                    (field, copy.deepcopy(evidence[field]))
                    for field in PORTABLE_EVIDENCE_FIELDS
                    if field in evidence
                    and isinstance(
                        evidence[field], (str, int, float, bool)
                    )
                )
    return projected


def validate_portable_evidence(
    evidence: Mapping[str, Any],
    *,
    field: str = "evidence",
) -> List[Dict[str, str]]:
    """Validate compact portable evidence using a closed field allowlist."""
    diagnostics: List[Dict[str, str]] = []
    for key in sorted(str(item) for item in evidence):
        path = f"{field}.{key}"
        if key in _DESTINATION_EVIDENCE_FIELDS:
            diagnostics.append(
                _diagnostic(
                    "portable-evidence-destination",
                    "error",
                    path,
                    "portable evidence cannot select or capture a filesystem destination",
                    "replace it with a closed path_class when location class matters",
                )
            )
        elif key in _EXECUTABLE_EVIDENCE_FIELDS:
            diagnostics.append(
                _diagnostic(
                    "portable-evidence-executable",
                    "error",
                    path,
                    "portable evidence cannot select or capture an executable",
                    "record adapter identity outside portable evidence",
                )
            )
        elif key in _GOVERNANCE_EVIDENCE_FIELDS:
            diagnostics.append(
                _diagnostic(
                    "portable-evidence-governance-field",
                    "error",
                    path,
                    "portable evidence cannot contain project-management identifiers",
                    "remove governance identifiers from portable evidence",
                )
            )
        elif key in _PRIVATE_EVIDENCE_FIELDS:
            diagnostics.append(
                _diagnostic(
                    "portable-evidence-private-field",
                    "error",
                    path,
                    "portable evidence cannot contain secrets or private interaction content",
                    "retain only verification facts",
                )
            )
        elif key not in PORTABLE_EVIDENCE_FIELDS:
            diagnostics.append(
                _diagnostic(
                    "portable-evidence-field-forbidden",
                    "error",
                    path,
                    "portable evidence uses a field outside the closed schema",
                    "use only fields exposed by the contract projection",
                )
            )
    kind = evidence.get("kind")
    if kind is not None and kind not in PORTABLE_EVIDENCE_KINDS:
        diagnostics.append(
            _diagnostic(
                "portable-evidence-field-forbidden",
                "error",
                f"{field}.kind",
                f"portable evidence kind is outside the closed vocabulary: {kind!r}",
                "use one evidence kind exposed by the contract projection",
            )
        )
    for key in PORTABLE_EVIDENCE_FIELDS:
        value = evidence.get(key)
        if value is not None and not isinstance(value, (str, int, float, bool)):
            diagnostics.append(
                _diagnostic(
                    "portable-evidence-field-forbidden",
                    "error",
                    f"{field}.{key}",
                    "portable evidence values must be scalar",
                    "replace nested or executable data with a compact observed identity",
                )
            )
    diagnostics.sort(key=_diagnostic_sort_key)
    return diagnostics


def _require_fields(
    item: Mapping[str, Any],
    fields: Iterable[str],
    path: str,
    diagnostics: List[Dict[str, str]],
) -> None:
    for name in fields:
        if name not in item:
            diagnostics.append(
                _diagnostic(
                    "missing-field",
                    "error",
                    f"{path}.{name}",
                    f"required field {name!r} is missing",
                    "supply the observed fact explicitly before continuing",
                )
            )


def _validate_schema(
    record: Mapping[str, Any], diagnostics: List[Dict[str, str]]
) -> None:
    if record.get("schema_identity") != SCHEMA_IDENTITY:
        diagnostics.append(
            _diagnostic(
                "invalid-schema",
                "error",
                "schema_identity",
                "state record schema identity is missing or unsupported",
                "use the installed contract without coercing a newer schema",
            )
        )
    if not supported_record_schema_version(record.get("record_schema_version")):
        diagnostics.append(
            _diagnostic(
                "invalid-schema",
                "error",
                "record_schema_version",
                "state record schema version is missing or unsupported",
                "migrate through a supported adapter before continuing",
            )
        )


def _validate_run(
    record: Mapping[str, Any], diagnostics: List[Dict[str, str]]
) -> None:
    run = record.get("run")
    if not isinstance(run, Mapping):
        diagnostics.append(
            _diagnostic(
                "missing-field",
                "error",
                "run",
                "run identity is missing",
                "supply one operation, marker, and intended source",
            )
        )
        return
    _require_fields(run, ("operation", "marker", "source"), "run", diagnostics)
    operation = run.get("operation")
    if operation not in OPERATION_KINDS:
        diagnostics.append(
            _diagnostic(
                "unknown-vocabulary",
                "error",
                "run.operation",
                f"unsupported operation kind: {operation!r}",
                "select one operation kind from the contract",
            )
        )
    if not isinstance(run.get("marker"), str) or not run.get("marker"):
        diagnostics.append(
            _diagnostic(
                "missing-field",
                "error",
                "run.marker",
                "deterministic run marker is missing",
                "supply a non-empty run marker",
            )
        )
    source = run.get("source")
    if not isinstance(source, Mapping):
        diagnostics.append(
            _diagnostic(
                "source-identity-unresolved",
                "error",
                "run.source",
                "intended source identity is missing",
                "attribute the intended source before planning work",
            )
        )
        return
    _require_fields(
        source, ("kind", "value", "state", "authority"), "run.source", diagnostics
    )
    if source.get("kind") not in SOURCE_KINDS:
        diagnostics.append(
            _diagnostic(
                "unknown-vocabulary",
                "error",
                "run.source.kind",
                f"unsupported source kind: {source.get('kind')!r}",
                "select one source kind from the contract projection",
            )
        )
    if source.get("state") not in SOURCE_STATES:
        diagnostics.append(
            _diagnostic(
                "unknown-vocabulary",
                "error",
                "run.source.state",
                f"unsupported source state: {source.get('state')!r}",
                "preserve source ambiguity using a closed source state",
            )
        )
    if (
        source.get("state") != "known"
        or not source.get("value")
        or not source.get("authority")
    ):
        diagnostics.append(
            _diagnostic(
                "source-identity-unresolved",
                "error",
                "run.source",
                "intended source is unknown, malformed, or contradictory",
                "resolve one attributable source identity before continuing",
            )
        )


def _validate_versions(
    record: Mapping[str, Any], diagnostics: List[Dict[str, str]]
) -> None:
    versions = record.get("versions")
    if not isinstance(versions, list):
        diagnostics.append(
            _diagnostic(
                "missing-field",
                "error",
                "versions",
                "peer version facts are missing",
                "account for every peer identity explicitly",
            )
        )
        return
    counts: Dict[str, int] = {}
    contract = identity_contract()
    for index, item in enumerate(versions):
        path = f"versions[{index}]"
        if not isinstance(item, Mapping):
            diagnostics.append(
                _diagnostic(
                    "missing-field",
                    "error",
                    path,
                    "version fact is not an object",
                    "emit one structured fact per peer identity",
                )
            )
            continue
        _require_fields(
            item,
            ("kind", "value", "state", "authority", "verification"),
            path,
            diagnostics,
        )
        kind = str(item.get("kind", ""))
        counts[kind] = counts.get(kind, 0) + 1
        if kind not in PEER_IDENTITY_KINDS:
            diagnostics.append(
                _diagnostic(
                    "unknown-vocabulary",
                    "error",
                    f"{path}.kind",
                    f"unknown peer identity kind: {kind!r}",
                    "use the closed peer identity vocabulary",
                )
            )
            continue
        expected_authority = contract[kind]["authority"]
        derived_from = item.get("derived_from")
        if item.get("authority") != expected_authority or (
            derived_from is not None and derived_from != kind
        ):
            diagnostics.append(
                _diagnostic(
                    "peer-identity-substitution",
                    "error",
                    path,
                    f"{kind} is attributed to another identity's authority",
                    "observe it from its own authoritative surface",
                )
            )
        state = item.get("state")
        if state not in identity_state_vocabulary(kind):
            diagnostics.append(
                _diagnostic(
                    "unknown-vocabulary",
                    "error",
                    f"{path}.state",
                    f"unsupported state {state!r} for {kind}",
                    "preserve the fact using a closed identity state",
                )
            )
        elif state in _HARD_IDENTITY_STATES:
            diagnostics.append(
                _diagnostic(
                    "identity-unresolved",
                    "error",
                    path,
                    f"{kind} is explicitly {state}",
                    "repair or reconcile this identity before completion",
                )
            )
        elif state in _SOFT_IDENTITY_STATES:
            diagnostics.append(
                _diagnostic(
                    "identity-unresolved",
                    "warning",
                    path,
                    f"{kind} remains {state}",
                    "retain a qualified claim until this identity is verified",
                )
            )
        if state not in ("unknown", "missing") and item.get("value") in (
            None,
            "",
        ):
            diagnostics.append(
                _diagnostic(
                    "missing-field",
                    "error",
                    f"{path}.value",
                    f"{kind} state {state!r} has no identity value",
                    "record the observed identity value or mark the fact unknown",
                )
            )
        verification = item.get("verification")
        if verification not in VERIFICATION_STATES:
            diagnostics.append(
                _diagnostic(
                    "unknown-vocabulary",
                    "error",
                    f"{path}.verification",
                    f"unknown verification state: {item.get('verification')!r}",
                    "use the closed verification vocabulary",
                )
            )
        elif verification == "failed":
            diagnostics.append(
                _diagnostic(
                    "identity-unresolved",
                    "error",
                    f"{path}.verification",
                    f"{kind} verification failed",
                    "repair or reconcile this identity before completion",
                )
            )
        elif verification in ("unknown", "unverified"):
            diagnostics.append(
                _diagnostic(
                    "identity-unresolved",
                    "warning",
                    f"{path}.verification",
                    f"{kind} proof remains {verification}",
                    "retain a qualified claim until this identity is verified",
                )
            )
    for kind in PEER_IDENTITY_KINDS:
        if counts.get(kind, 0) == 0:
            diagnostics.append(
                _diagnostic(
                    "missing-version",
                    "error",
                    "versions",
                    f"peer identity is not accounted for: {kind}",
                    "emit it explicitly, using unknown when necessary",
                )
            )
        elif counts[kind] > 1:
            diagnostics.append(
                _diagnostic(
                    "duplicate-version",
                    "error",
                    "versions",
                    f"peer identity appears more than once: {kind}",
                    "reconcile to one authoritative identity record",
                )
            )


def _validate_surfaces(
    record: Mapping[str, Any], diagnostics: List[Dict[str, str]]
) -> None:
    surfaces = record.get("surfaces")
    if not isinstance(surfaces, list):
        diagnostics.append(
            _diagnostic(
                "missing-field",
                "error",
                "surfaces",
                "surface accounting is missing",
                "account for every in-scope surface explicitly",
            )
        )
        return
    counts: Dict[str, int] = {}
    for index, item in enumerate(surfaces):
        path = f"surfaces[{index}]"
        if not isinstance(item, Mapping):
            diagnostics.append(
                _diagnostic(
                    "missing-field",
                    "error",
                    path,
                    "surface fact is not an object",
                    "emit one structured fact per surface",
                )
            )
            continue
        _require_fields(
            item,
            (
                "kind",
                "locator",
                "desired_identity",
                "observed_identity",
                "state",
                "affected",
                "required",
            ),
            path,
            diagnostics,
        )
        kind = str(item.get("kind", ""))
        counts[kind] = counts.get(kind, 0) + 1
        if kind not in SURFACE_KINDS:
            diagnostics.append(
                _diagnostic(
                    "unknown-vocabulary",
                    "error",
                    f"{path}.kind",
                    f"unknown surface kind: {kind!r}",
                    "use the closed surface vocabulary",
                )
            )
        if item.get("state") not in SURFACE_STATES:
            diagnostics.append(
                _diagnostic(
                    "unknown-vocabulary",
                    "error",
                    f"{path}.state",
                    f"unknown surface state: {item.get('state')!r}",
                    "use the closed surface-state vocabulary",
                )
            )
        if not isinstance(item.get("affected"), bool):
            diagnostics.append(
                _diagnostic(
                    "missing-field",
                    "error",
                    f"{path}.affected",
                    "affected detection is not an explicit boolean",
                    "detect and record whether this surface is affected",
                )
            )
        if not isinstance(item.get("required"), bool):
            diagnostics.append(
                _diagnostic(
                    "missing-field",
                    "error",
                    f"{path}.required",
                    "surface policy is not an explicit boolean",
                    "record whether this surface is required",
                )
            )
        observed_differs = (
            item.get("desired_identity") != item.get("observed_identity")
        )
        if (
            item.get("affected") is False
            and observed_differs
            and item.get("state") not in ("not-applicable", "declined")
        ) or (
            item.get("affected") is True
            and item.get("state") == "not-applicable"
        ):
            diagnostics.append(
                _diagnostic(
                    "surface-detection-contradictory",
                    "error",
                    path,
                    "affected flag contradicts desired/observed facts",
                    "re-run detection and preserve one consistent result",
                )
            )
    for kind in SURFACE_KINDS:
        if counts.get(kind, 0) == 0:
            diagnostics.append(
                _diagnostic(
                    "missing-surface",
                    "error",
                    "surfaces",
                    f"in-scope surface is not accounted for: {kind}",
                    "emit it explicitly using an unresolved state when needed",
                )
            )
        elif counts[kind] > 1:
            diagnostics.append(
                _diagnostic(
                    "duplicate-surface",
                    "error",
                    "surfaces",
                    f"in-scope surface appears more than once: {kind}",
                    "reconcile detection to one surface record",
                )
            )


def _validate_checkpoints(
    record: Mapping[str, Any], diagnostics: List[Dict[str, str]]
) -> None:
    checkpoints = record.get("checkpoints")
    if not isinstance(checkpoints, list):
        diagnostics.append(
            _diagnostic(
                "missing-field",
                "error",
                "checkpoints",
                "checkpoint accounting is missing",
                "supply an empty list when no checkpoint exists",
            )
        )
        return
    ids: Dict[str, int] = {}
    choices = (
        record.get("choices") if isinstance(record.get("choices"), list) else []
    )
    for index, item in enumerate(checkpoints):
        path = f"checkpoints[{index}]"
        if not isinstance(item, Mapping):
            continue
        _require_fields(
            item,
            (
                "id",
                "phase",
                "surface",
                "status",
                "evidence",
                "verification",
                "retry_safety",
            ),
            path,
            diagnostics,
        )
        checkpoint_id = str(item.get("id", ""))
        ids[checkpoint_id] = ids.get(checkpoint_id, 0) + 1
        vocab_checks = (
            ("phase", CHECKPOINT_PHASES),
            ("surface", SURFACE_KINDS),
            ("status", CHECKPOINT_STATUSES),
            ("verification", VERIFICATION_STATES),
            ("retry_safety", RETRY_SAFETY_STATES),
        )
        for name, vocabulary in vocab_checks:
            if item.get(name) not in vocabulary:
                diagnostics.append(
                    _diagnostic(
                        "unknown-vocabulary",
                        "error",
                        f"{path}.{name}",
                        f"unknown checkpoint {name}: {item.get(name)!r}",
                        f"use the closed checkpoint {name} vocabulary",
                    )
                )
        evidence = item.get("evidence")
        if isinstance(evidence, Mapping):
            diagnostics.extend(
                validate_portable_evidence(
                    evidence, field=f"{path}.evidence"
                )
            )
        if item.get("status") == "completed":
            if not isinstance(evidence, Mapping) or not evidence:
                diagnostics.append(
                    _diagnostic(
                        "checkpoint-evidence-missing",
                        "error",
                        f"{path}.evidence",
                        "completed checkpoint has no portable evidence",
                        "inspect and persist evidence before marking completion",
                    )
                )
            else:
                if not all(
                    evidence.get(name)
                    for name in ("identity", "kind", "verification")
                ):
                    diagnostics.append(
                        _diagnostic(
                            "checkpoint-evidence-missing",
                            "error",
                            f"{path}.evidence",
                            "portable checkpoint evidence lacks identity, kind, or verification",
                            "persist the closed evidence identity and verification facts",
                        )
                    )
                elif evidence.get("verification") != "verified":
                    diagnostics.append(
                        _diagnostic(
                            "checkpoint-verification-missing",
                            "error",
                            f"{path}.evidence.verification",
                            "portable checkpoint evidence is not verified",
                            "verify the evidence before marking checkpoint completion",
                        )
                    )
            if item.get("verification") != "verified":
                diagnostics.append(
                    _diagnostic(
                        "checkpoint-verification-missing",
                        "error",
                        f"{path}.verification",
                        "completed checkpoint is not verified",
                        "downgrade it to unverified or verify the evidence",
                    )
                )
            if item.get("phase") == "repair":
                authorized = any(
                    choice.get("surface") == item.get("surface")
                    and choice.get("offered_action") == "repair"
                    and choice.get("state") == "authorized"
                    for choice in choices
                    if isinstance(choice, Mapping)
                )
                if not authorized:
                    diagnostics.append(
                        _diagnostic(
                            "choice-not-authorized",
                            "error",
                            path,
                            "repair completed without operator authorization",
                            "obtain explicit authorization before repair",
                        )
                    )
        if item.get("status") == "unverified" and item.get("retry_safety") not in (
            "inspect-before-retry",
            "refuse-replay",
        ):
            # `refuse-replay` is strictly stronger than inspection, so it
            # satisfies the same invariant: no unverified work is replayed
            # blindly.
            diagnostics.append(
                _diagnostic(
                    "checkpoint-replay-unsafe",
                    "error",
                    path,
                    "unverified checkpoint must be inspected before replay",
                    "classify it inspect-before-retry or refuse-replay and inspect the target",
                )
            )
        if (
            item.get("status") in ("blocked", "failed")
            and item.get("attempted_action")
        ):
            mutation_status = str(item.get("mutation_status", ""))
            os_failure = mutation_status.startswith("os-error-")
            diagnostic_code = "apply-failed" if os_failure else "apply-refused"
            diagnostic_verb = "failed" if os_failure else "refused"
            recovery = str(item.get("recovery", "")).strip()
            recovery_artifact = str(
                item.get("recovery_artifact", "")
            ).strip()
            if not recovery or not recovery_artifact:
                diagnostics.append(
                    _diagnostic(
                        "missing-field",
                        "error",
                        path,
                        "apply outcome lacks recovery guidance or a recovery artifact",
                        "record the attempted action, retry safety, recovery guidance, and preserved or backup artifact",
                    )
                )
            diagnostics.append(
                _diagnostic(
                    diagnostic_code,
                    "error",
                    path,
                    (
                        f"{item.get('surface')} {diagnostic_verb} "
                        f"{item.get('attempted_action')}"
                    ),
                    recovery
                    or "inspect the affected surface and preserved content before retry",
                )
            )
    for checkpoint_id, count in ids.items():
        if not checkpoint_id or count > 1:
            diagnostics.append(
                _diagnostic(
                    "duplicate-checkpoint",
                    "error",
                    "checkpoints",
                    f"checkpoint identity is empty or duplicated: {checkpoint_id!r}",
                    "use one stable identity per checkpoint",
                )
            )
    run = record.get("run")
    if isinstance(run, Mapping) and run.get("operation") == "resume":
        has_prior = any(
            item.get("status") == "completed"
            and item.get("verification") == "verified"
            and isinstance(item.get("evidence"), Mapping)
            and bool(item.get("evidence"))
            for item in checkpoints
            if isinstance(item, Mapping)
        )
        if not has_prior:
            diagnostics.append(
                _diagnostic(
                    "prior-checkpoint-evidence-missing",
                    "error",
                    "checkpoints",
                    "resume has no evidence-backed prior checkpoint",
                    "inspect the interrupted run before selecting resume work",
                )
            )


def _validate_choices(
    record: Mapping[str, Any], diagnostics: List[Dict[str, str]]
) -> None:
    choices = record.get("choices")
    if not isinstance(choices, list):
        diagnostics.append(
            _diagnostic(
                "missing-field",
                "error",
                "choices",
                "operator choice accounting is missing",
                "supply an empty list when no action was offered",
            )
        )
        return
    ids: Dict[str, int] = {}
    surfaces = {
        item.get("kind"): item
        for item in record.get("surfaces", [])
        if isinstance(item, Mapping)
    }
    for index, item in enumerate(choices):
        path = f"choices[{index}]"
        if not isinstance(item, Mapping):
            continue
        _require_fields(
            item,
            ("id", "surface", "offered_action", "state", "provenance"),
            path,
            diagnostics,
        )
        choice_id = str(item.get("id", ""))
        ids[choice_id] = ids.get(choice_id, 0) + 1
        vocab_checks = (
            ("surface", SURFACE_KINDS),
            ("offered_action", OFFERED_ACTIONS),
            ("state", OPERATOR_CHOICE_STATES),
        )
        for name, vocabulary in vocab_checks:
            if item.get(name) not in vocabulary:
                diagnostics.append(
                    _diagnostic(
                        "unknown-vocabulary",
                        "error",
                        f"{path}.{name}",
                        f"unknown choice {name}: {item.get(name)!r}",
                        f"use the closed choice {name} vocabulary",
                    )
                )
        if not item.get("provenance"):
            diagnostics.append(
                _diagnostic(
                    "missing-field",
                    "error",
                    f"{path}.provenance",
                    "operator choice provenance is missing",
                    "record where the choice was observed",
                )
            )
        surface = surfaces.get(item.get("surface"))
        if (
            isinstance(surface, Mapping)
            and surface.get("affected") is False
            and item.get("state")
            in ("offered", "declined", "deferred")
        ):
            diagnostics.append(
                _diagnostic(
                    "choice-contradictory",
                    "error",
                    path,
                    "choice exists for a surface marked unaffected",
                    "reconcile affected-surface detection",
                )
            )
    for surface_kind, surface in surfaces.items():
        if surface.get("state") not in ("declined", "deferred"):
            continue
        disposition = surface.get("state")
        provenance_backed = any(
            isinstance(choice, Mapping)
            and choice.get("surface") == surface_kind
            and choice.get("state") == disposition
            and bool(choice.get("provenance"))
            for choice in choices
        )
        if not provenance_backed:
            diagnostics.append(
                _diagnostic(
                    "decline-provenance-missing",
                    "error",
                    f"surfaces.{surface_kind}",
                    f"{disposition} surface has no provenance-backed operator choice",
                    "record the observed disposition and its provenance before claiming it",
                )
            )
    for choice_id, count in ids.items():
        if not choice_id or count > 1:
            diagnostics.append(
                _diagnostic(
                    "duplicate-choice",
                    "error",
                    "choices",
                    f"choice identity is empty or duplicated: {choice_id!r}",
                    "use one stable identity per offered action",
                )
            )


def _validate_restarts(
    record: Mapping[str, Any], diagnostics: List[Dict[str, str]]
) -> None:
    restarts = record.get("restarts")
    if not isinstance(restarts, list):
        diagnostics.append(
            _diagnostic(
                "missing-field",
                "error",
                "restarts",
                "restart accounting is missing",
                "supply an empty list when no client is affected",
            )
        )
        return
    clients: Dict[str, int] = {}
    for index, item in enumerate(restarts):
        path = f"restarts[{index}]"
        if not isinstance(item, Mapping):
            continue
        _require_fields(
            item,
            (
                "client",
                "installed_identity",
                "running_identity",
                "state",
                "instruction_class",
                "proof_state",
            ),
            path,
            diagnostics,
        )
        client = str(item.get("client", ""))
        clients[client] = clients.get(client, 0) + 1
        vocab_checks = (
            ("state", RESTART_STATES),
            ("instruction_class", RESTART_INSTRUCTION_CLASSES),
            ("proof_state", VERIFICATION_STATES),
        )
        for name, vocabulary in vocab_checks:
            if item.get(name) not in vocabulary:
                diagnostics.append(
                    _diagnostic(
                        "unknown-vocabulary",
                        "error",
                        f"{path}.{name}",
                        f"unknown restart {name}: {item.get(name)!r}",
                        f"use the closed restart {name} vocabulary",
                    )
                )
        identities_match = (
            item.get("installed_identity") == item.get("running_identity")
        )
        status = item.get("status")
        reason_code = item.get("reason_code")
        if status is not None and status not in RESTART_STATUSES:
            diagnostics.append(
                _diagnostic(
                    "unknown-vocabulary",
                    "error",
                    f"{path}.status",
                    f"unknown restart status: {status!r}",
                    "use the closed restart status vocabulary",
                )
            )
        if reason_code is not None and reason_code not in RESTART_REASON_CODES:
            diagnostics.append(
                _diagnostic(
                    "unknown-vocabulary",
                    "error",
                    f"{path}.reason_code",
                    f"unknown restart reason code: {reason_code!r}",
                    "use the closed restart reason vocabulary",
                )
            )
        if status in (
            "restart_required",
            "restart_instructed",
            "verification_pending",
            "unverified",
        ):
            instruction = item.get("instruction")
            if not isinstance(instruction, Mapping) or not instruction.get(
                "action"
            ) or not instruction.get("expected_proof"):
                diagnostics.append(
                    _diagnostic(
                        "restart-fact-missing",
                        "error",
                        path,
                        "restart-needed state lacks one direct instruction and proof condition",
                        "select the supported current client instruction",
                    )
                )
        if item.get("activation_claim_allowed") is True and (
            status != "current"
            or item.get("proof_state") != "verified"
            or not identities_match
        ):
            diagnostics.append(
                _diagnostic(
                    "restart-proof-missing",
                    "error",
                    path,
                    "activation is claimed without verified fresh matching process proof",
                    "withhold activation until a new matching process is observed",
                )
            )
        if item.get("state") in ("required", "pending") and item.get(
            "instruction_class"
        ) == "none":
            diagnostics.append(
                _diagnostic(
                    "restart-fact-missing",
                    "error",
                    path,
                    "pending restart has no direct client instruction",
                    "select one direct instruction class for this client",
                )
            )
        if (
            item.get("state") == "not-required"
            and item.get("instruction_class") != "none"
        ):
            diagnostics.append(
                _diagnostic(
                    "restart-identity-contradictory",
                    "error",
                    path,
                    "non-required restart carries an action instruction",
                    "use instruction class none or mark the restart required",
                )
            )
        if item.get("state") == "verified" and (
            item.get("proof_state") != "verified" or not identities_match
        ):
            diagnostics.append(
                _diagnostic(
                    "restart-proof-missing",
                    "error",
                    path,
                    "restart is verified without matching fresh-process proof",
                    "observe the new process identity after the client action",
                )
            )
        if (
            not identities_match
            and item.get("state") in (
            "not-required",
            "verified",
            )
            and reason_code != "mcp_surface_unaffected"
        ):
            diagnostics.append(
                _diagnostic(
                    "restart-identity-contradictory",
                    "error",
                    path,
                    "running and installed identities differ but restart is not pending",
                    "mark restart required or verify a matching process",
                )
            )
    for client, count in clients.items():
        if not client or count > 1:
            diagnostics.append(
                _diagnostic(
                    "duplicate-restart-client",
                    "error",
                    "restarts",
                    f"restart client is empty or duplicated: {client!r}",
                    "emit exactly one restart fact per client",
                )
            )
    state = record.get("state")
    running_stale = any(
        isinstance(item, Mapping)
        and item.get("kind") == "running_server"
        and item.get("state") == "stale-runtime"
        for item in record.get("versions", [])
    )
    restart_pending = any(
        isinstance(item, Mapping)
        and item.get("state") in ("required", "pending")
        for item in restarts
    )
    if running_stale and not restart_pending:
        diagnostics.append(
            _diagnostic(
                "restart-fact-missing",
                "error",
                "restarts",
                "running server is stale but no restart is pending",
                "record the affected client and one direct restart action",
            )
        )
    if state == "restart-required" and not any(
        isinstance(item, Mapping)
        and item.get("state") in ("required", "pending")
        for item in restarts
    ):
        diagnostics.append(
            _diagnostic(
                "restart-fact-missing",
                "error",
                "restarts",
                "restart-required state has no pending client restart",
                "record the affected client and one direct action",
            )
        )
    if state == "restart-verified" and (
        not restarts
        or any(
            isinstance(item, Mapping) and item.get("state") != "verified"
            for item in restarts
        )
    ):
        diagnostics.append(
            _diagnostic(
                "restart-proof-missing",
                "error",
                "restarts",
                "restart-verified state retains an unverified client",
                "verify every affected client or block",
            )
        )


def _validate_migrations(
    record: Mapping[str, Any], diagnostics: List[Dict[str, str]]
) -> None:
    migrations = record.get("migrations")
    if not isinstance(migrations, list):
        diagnostics.append(
            _diagnostic(
                "missing-field",
                "error",
                "migrations",
                "migration-offer accounting is missing",
                "supply an empty list when no project is applicable",
            )
        )
        return
    identities: Dict[str, int] = {}
    for index, item in enumerate(migrations):
        path = f"migrations[{index}]"
        if not isinstance(item, Mapping):
            continue
        _require_fields(
            item,
            (
                "project_identity",
                "current_schema",
                "target_schema",
                "applicability",
                "choice_state",
                "result",
            ),
            path,
            diagnostics,
        )
        project_identity = str(item.get("project_identity", ""))
        identities[project_identity] = identities.get(project_identity, 0) + 1
        vocab_checks = (
            ("applicability", MIGRATION_APPLICABILITY),
            ("choice_state", OPERATOR_CHOICE_STATES),
            ("result", MIGRATION_RESULTS),
        )
        for name, vocabulary in vocab_checks:
            if item.get(name) not in vocabulary:
                diagnostics.append(
                    _diagnostic(
                        "unknown-vocabulary",
                        "error",
                        f"{path}.{name}",
                        f"unknown migration {name}: {item.get(name)!r}",
                        f"use the closed migration {name} vocabulary",
                    )
                )
        if (
            item.get("result") == "completed"
            and item.get("choice_state") != "authorized"
        ):
            diagnostics.append(
                _diagnostic(
                    "migration-not-authorized",
                    "error",
                    path,
                    "migration completion is claimed without authorization",
                    "run migration only after operator authorization",
                )
            )
        if (
            item.get("applicability") == "not-applicable"
            and item.get("result") == "completed"
        ):
            diagnostics.append(
                _diagnostic(
                    "migration-identity-contradictory",
                    "error",
                    path,
                    "non-applicable migration is marked completed",
                    "reconcile schema applicability and evidence",
                )
            )
    for project_identity, count in identities.items():
        if not project_identity or count > 1:
            diagnostics.append(
                _diagnostic(
                    "duplicate-migration-offer",
                    "error",
                    "migrations",
                    f"migration project is empty or duplicated: {project_identity!r}",
                    "emit exactly one offer per governed project",
                )
            )


def _terminal_diagnostics(
    record: Mapping[str, Any], diagnostics: List[Dict[str, str]]
) -> None:
    if record.get("state") != "complete":
        return
    unsafe: List[str] = []
    for item in record.get("surfaces", []):
        if not isinstance(item, Mapping):
            continue
        state = item.get("state")
        if state in _SAFE_SURFACE_STATES or state in ("declined", "deferred"):
            continue
        if (
            item.get("kind") == "project-schema-migration-offers"
            and state == "offered"
        ):
            continue
        unsafe.append(f"surface {item.get('kind')} is {state}")
    for item in record.get("choices", []):
        if isinstance(item, Mapping) and item.get("state") == "offered":
            unsafe.append(
                f"operator choice {item.get('id')} is offered but unresolved"
            )
        if isinstance(item, Mapping) and item.get("state") == "authorized":
            surface = next(
                (
                    surface
                    for surface in record.get("surfaces", [])
                    if isinstance(surface, Mapping)
                    and surface.get("kind") == item.get("surface")
                ),
                {},
            )
            if surface.get("state") not in _SAFE_SURFACE_STATES:
                unsafe.append(
                    f"authorized action {item.get('id')} is not verified"
                )
    for item in record.get("restarts", []):
        if isinstance(item, Mapping) and item.get("state") not in (
            "not-required",
            "verified",
        ):
            unsafe.append(
                f"client {item.get('client')} restart is {item.get('state')}"
            )
    if unsafe:
        diagnostics.append(
            _diagnostic(
                "terminal-claim-unsafe",
                "error",
                "outcome",
                "; ".join(sorted(unsafe)),
                "complete, verify, explicitly decline, or block unresolved work",
            )
        )


def _derive_outcome(
    record: Mapping[str, Any], diagnostics: Sequence[Mapping[str, Any]]
) -> Dict[str, Any]:
    surfaces = [
        item for item in record.get("surfaces", []) if isinstance(item, Mapping)
    ]
    completed = [
        str(item.get("kind"))
        for item in surfaces
        if item.get("state") in _SAFE_SURFACE_STATES
    ]
    declined = [
        str(item.get("kind"))
        for item in surfaces
        if item.get("state") == "declined"
    ]
    deferred = [
        str(item.get("kind"))
        for item in surfaces
        if item.get("state") == "deferred"
    ]
    blocked = [
        str(item.get("kind"))
        for item in surfaces
        if item.get("state") in ("blocked", "failed")
    ]
    pending = [
        str(item.get("kind"))
        for item in surfaces
        if item.get("state") in _PENDING_SURFACE_STATES
    ]
    restart_required = any(
        isinstance(item, Mapping)
        and item.get("state") in ("required", "pending")
        for item in record.get("restarts", [])
    ) or any(
        isinstance(item, Mapping)
        and item.get("kind") == "running_server"
        and item.get("state") == "stale-runtime"
        for item in record.get("versions", [])
    )
    errors = [item for item in diagnostics if item.get("severity") == "error"]
    warnings = [
        item for item in diagnostics if item.get("severity") == "warning"
    ]
    lifecycle = record.get("state")
    qualified = bool(
        declined
        or deferred
        or pending
        or blocked
        or warnings
        or any(
            isinstance(item, Mapping)
            and item.get("applicability") == "applicable"
            and item.get("result") != "completed"
            for item in record.get("migrations", [])
        )
        or any(
            item.get("kind") == "project-schema-migration-offers"
            and item.get("state") == "offered"
            for item in surfaces
        )
    )
    if lifecycle == "failed":
        status = "failed"
    elif lifecycle == "blocked" or errors:
        status = "blocked"
    elif lifecycle == "complete":
        status = "complete-qualified" if qualified else "complete"
    else:
        status = "in-progress"
    fully_updated = status == "complete"
    claim = {
        "complete": "fully-updated",
        "complete-qualified": "qualified-complete",
    }.get(status, "none")
    recovery: List[str] = []
    for item in diagnostics:
        value = str(item.get("recovery", ""))
        if value and value not in recovery:
            recovery.append(value)
    return OrderedDict(
        (
            ("status", status),
            ("claim", claim),
            ("fully_updated", fully_updated),
            ("completed_surfaces", completed),
            ("pending_surfaces", pending),
            ("blocked_surfaces", blocked),
            ("declined_surfaces", declined),
            ("deferred_surfaces", deferred),
            ("restart_required", restart_required),
            ("recovery_guidance", recovery),
        )
    )


def evaluate_record(record: Mapping[str, Any]) -> "OrderedDict[str, Any]":
    """Canonicalize an existing record and recompute diagnostics/outcome."""
    canonical: "OrderedDict[str, Any]" = OrderedDict()
    for field in STABLE_FIELDS:
        if field in (
            "versions",
            "surfaces",
            "checkpoints",
            "choices",
            "restarts",
            "migrations",
        ):
            canonical[field] = _canonical_items(record.get(field), field=field)
        elif field in ("outcome", "diagnostics"):
            canonical[field] = {} if field == "outcome" else []
        else:
            canonical[field] = copy.deepcopy(record.get(field))
    if "internal" in record:
        canonical["internal"] = copy.deepcopy(record["internal"])

    diagnostics: List[Dict[str, str]] = []
    _validate_schema(canonical, diagnostics)
    _validate_run(canonical, diagnostics)
    if canonical.get("state") not in LIFECYCLE_STATES:
        diagnostics.append(
            _diagnostic(
                "unknown-vocabulary",
                "error",
                "state",
                f"unknown lifecycle state: {canonical.get('state')!r}",
                "use the closed lifecycle-state vocabulary",
            )
        )
    _validate_versions(canonical, diagnostics)
    _validate_surfaces(canonical, diagnostics)
    _validate_checkpoints(canonical, diagnostics)
    _validate_choices(canonical, diagnostics)
    _validate_restarts(canonical, diagnostics)
    _validate_migrations(canonical, diagnostics)
    _terminal_diagnostics(canonical, diagnostics)
    diagnostics.sort(key=_diagnostic_sort_key)
    canonical["diagnostics"] = diagnostics
    canonical["outcome"] = _derive_outcome(canonical, diagnostics)
    return canonical


def transition(
    record: Mapping[str, Any], target_state: str
) -> "OrderedDict[str, Any]":
    """Return a transitioned copy, refusing edges outside the grammar."""
    current = record.get("state")
    if current not in ALLOWED_TRANSITIONS:
        raise ContractRefusal(
            "invalid-transition", f"unknown current state: {current!r}"
        )
    if target_state not in ALLOWED_TRANSITIONS[current]:
        raise ContractRefusal(
            "invalid-transition",
            f"{current!r} cannot transition to {target_state!r}",
        )
    updated = copy.deepcopy(dict(record))
    updated["state"] = target_state
    return evaluate_record(updated)


def resume_work(record: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Return only incomplete or unverified checkpoints in stable order."""
    pending = [
        copy.deepcopy(dict(item))
        for item in record.get("checkpoints", [])
        if isinstance(item, Mapping)
        and not (
            item.get("status") == "completed"
            and item.get("verification") == "verified"
            and isinstance(item.get("evidence"), Mapping)
            and bool(item.get("evidence"))
        )
    ]
    pending.sort(key=lambda item: str(item.get("id", "")))
    return pending
