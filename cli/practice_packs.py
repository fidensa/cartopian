"""Deterministic validation, selection, and bounded practice-pack loading.

The machine vocabulary and the five-pack metadata live in
``protocol/risk-and-practice-contract.json``.  This module contains only the
shared executable projection used by the CLI and MCP surfaces.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


REGISTRY_PATH = (
    Path(__file__).resolve().parents[1]
    / "protocol"
    / "risk-and-practice-contract.json"
)
DEFAULT_PROTOCOL_ROOT = REGISTRY_PATH.parent
MAX_ENVELOPE_VALUE_BYTES = 512
MAX_ENVELOPE_VALUES = 32
MAX_BODY_BUDGET_BYTES = 64 * 1024

_IDENTITY_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BODY_REF_RE = re.compile(r"^packs/[a-z][a-z0-9]*(?:-[a-z0-9]+)*\.md$")
_BODY_IDENTITY_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_FACT_TO_ENVELOPE = {
    "primary_outcome": "primary_outcomes",
    "artifact_kind": "artifact_kinds",
    "incidental_term": "incidental_terms",
    "exclusion": "exclusions",
    "lifecycle_substrate": "lifecycle_substrate_activities",
}
_SOURCE_CLASSES = ("governing", "conditional", "structural-exemplar", "watchlist")
_SOURCE_STATUSES = ("current", "stale", "unknown")
_CONFLICT_DISPOSITIONS = ("none", "resolved", "unresolved")
# Source applicability conditions may additionally declare scope facts;
# pack matching never reads them.
_SOURCE_CONDITION_FACTS = (*_FACT_TO_ENVELOPE, "domain_scope")
_SOURCE_FACT_TO_ENVELOPE = {**_FACT_TO_ENVELOPE, "domain_scope": "domain_scopes"}
# Only identity and applicability cross into a handoff; never source text.
_SOURCE_IDENTITY_FIELDS = (
    "source_id",
    "class",
    "title",
    "context",
    "status",
    "governed_scope",
    "applicability_boundary",
    "precedence_scope",
)
_APPLICABLE_SOURCE_CLASSES = ("governing", "conditional")
_SOURCE_RECORD_FIELDS = (
    "source_id",
    "class",
    "title",
    "context",
    "verified_on",
    "status",
    "governed_scope",
    "applicability_boundary",
    "applies_when",
    "exclusions",
    "precedence_scope",
    "conflict_disposition",
    "refresh_trigger",
)


class PracticePackError(ValueError):
    """Stable fail-closed diagnostic for metadata, selection, or body input."""

    def __init__(self, code: str, detail: str, *, pack_id: str | None = None) -> None:
        self.code = code
        self.detail = detail
        self.pack_id = pack_id
        super().__init__(f"{code}: {detail}")


def _load_registry() -> dict[str, Any]:
    try:
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        packs = registry["packs"]
        metadata_ref = packs.get("metadata_catalog_ref", "fixtures.pack_candidates")
        if metadata_ref != "fixtures.pack_candidates":
            raise KeyError("unsupported metadata_catalog_ref")
        catalog = registry["fixtures"]["pack_candidates"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PracticePackError(
            "pack-contract-unavailable",
            "the authoritative practice-pack registry is unreadable",
        ) from exc
    if not isinstance(catalog, list):
        raise PracticePackError(
            "pack-contract-invalid", "the authoritative metadata catalog is not a list"
        )
    return registry


def load_pack_catalog() -> list[dict[str, Any]]:
    """Return a detached copy of the authoritative five-pack metadata."""
    registry = _load_registry()
    return json.loads(json.dumps(registry["fixtures"]["pack_candidates"]))


def _invalid(code: str, detail: str, *, pack_id: str | None = None) -> dict[str, Any]:
    try:
        registry = _load_registry()
        contract_id = registry["contract_id"]
        contract_version = registry["contract_version"]
        authored_bodies = len(registry["fixtures"]["pack_candidates"])
    except PracticePackError:
        contract_id = "risk-and-practice"
        contract_version = None
        authored_bodies = 0
    rejected = []
    if pack_id:
        rejected.append({"pack_id": pack_id, "reasons": [f"{code}:{detail}"]})
    return _with_context_receipt(
        {
            "contract_id": contract_id,
            "contract_version": contract_version,
            "outcome": "invalid",
            "pack_id": None,
            "ordered_match_reasons": [],
            "rejected_candidates": rejected,
            "bodies_loaded": 0,
            "loaded_body_bytes": 0,
            "body_identity": None,
            "body_budget_bytes": None,
            "applicable_sources": [],
            "context_receipt": None,
            "body": None,
            "error": {"code": code, "detail": detail},
        },
        authored_bodies=authored_bodies,
    )


def _base_result(registry: Mapping[str, Any], outcome: str) -> dict[str, Any]:
    return {
        "contract_id": registry["contract_id"],
        "contract_version": registry["contract_version"],
        "outcome": outcome,
        "pack_id": None,
        "ordered_match_reasons": [],
        "rejected_candidates": [],
        "bodies_loaded": 0,
        "loaded_body_bytes": 0,
        "body_identity": None,
        "body_budget_bytes": None,
        "applicable_sources": [],
        "context_receipt": None,
        "body": None,
        "error": None,
    }


def _routing_metadata_bytes(result: Mapping[str, Any]) -> int:
    """Measure the compact routing projection: this result without the body.

    The receipt is excluded so the measurement cannot depend on itself, and the
    serialization is canonical so any consumer can recompute the same number.
    """
    projection = {
        field: (None if field == "body" else value)
        for field, value in result.items()
        if field != "context_receipt"
    }
    canonical = json.dumps(
        projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return len(canonical.encode("utf-8"))


def _with_context_receipt(result: dict[str, Any], *, authored_bodies: int) -> dict[str, Any]:
    """Separate compact routing metadata from the one admitted body.

    Full source documents are never retrieved, so their active contribution is
    a measured zero rather than an assertion.
    """
    result["context_receipt"] = {
        "routing_metadata_bytes": _routing_metadata_bytes(result),
        "selected_body_bytes": result["loaded_body_bytes"],
        "body_budget_bytes": result["body_budget_bytes"],
        "unmatched_body_bytes": 0,
        "unloaded_pack_bodies": authored_bodies - result["bodies_loaded"],
        "source_document_bytes": 0,
    }
    return result


def _require_identity(value: object, field: str, pack_id: str | None) -> str:
    if not isinstance(value, str) or not _IDENTITY_RE.fullmatch(value):
        raise PracticePackError(
            "pack-metadata-invalid", f"{field} is not a stable identity", pack_id=pack_id
        )
    return value


def _condition_values(
    condition: Mapping[str, object], *, pack_id: str, index: int, negative: bool
) -> tuple[str, tuple[str, ...]]:
    fact = condition.get("fact")
    if not isinstance(fact, str) or fact not in _FACT_TO_ENVELOPE:
        raise PracticePackError(
            "pack-metadata-invalid",
            f"{pack_id} condition {index} has an undeclared fact",
            pack_id=pack_id,
        )
    has_value = "value" in condition
    has_any = "any_of" in condition
    if has_value == has_any:
        raise PracticePackError(
            "pack-metadata-invalid",
            f"{pack_id} condition {index} requires exactly one value form",
            pack_id=pack_id,
        )
    raw_values: object = [condition.get("value")] if has_value else condition.get("any_of")
    if not isinstance(raw_values, list) or not raw_values:
        raise PracticePackError(
            "pack-metadata-invalid",
            f"{pack_id} condition {index} has no values",
            pack_id=pack_id,
        )
    values: list[str] = []
    for value in raw_values:
        values.append(_require_identity(value, "condition value", pack_id))
    if len(values) != len(set(values)):
        raise PracticePackError(
            "pack-metadata-invalid",
            f"{pack_id} condition {index} repeats a value",
            pack_id=pack_id,
        )
    if not negative and fact == "lifecycle_substrate":
        raise PracticePackError(
            "pack-metadata-invalid",
            f"{pack_id} uses a negative-only fact as positive applicability",
            pack_id=pack_id,
        )
    return fact, tuple(values)


def _normalized_conditions(
    raw: object, *, pack_id: str, field: str, negative: bool
) -> tuple[dict[str, Any], ...]:
    if not isinstance(raw, list) or (not negative and not raw):
        raise PracticePackError(
            "pack-metadata-invalid",
            f"{pack_id}.{field} must be {'a list' if negative else 'a non-empty list'}",
            pack_id=pack_id,
        )
    normalized = []
    for index, condition in enumerate(raw):
        if not isinstance(condition, Mapping):
            raise PracticePackError(
                "pack-metadata-invalid",
                f"{pack_id}.{field}[{index}] is not an object",
                pack_id=pack_id,
            )
        fact, values = _condition_values(
            condition, pack_id=pack_id, index=index, negative=negative
        )
        normalized.append({"fact": fact, "values": values})
    identities = [(item["fact"], item["values"]) for item in normalized]
    if len(identities) != len(set(identities)):
        raise PracticePackError(
            "pack-metadata-invalid",
            f"{pack_id}.{field} repeats a condition",
            pack_id=pack_id,
        )
    return tuple(normalized)


def _source_error(detail: str) -> PracticePackError:
    return PracticePackError("pack-source-record-invalid", detail)


def _source_records(
    registry: Mapping[str, Any],
    records_override: object = None,
) -> dict[str, dict[str, Any]]:
    """Validate the complete closed source-record catalog before any use.

    ``records_override`` is a test-only fixture seam; the authoritative
    catalog lives in the registry's ``source_stack``.
    """
    if records_override is None:
        try:
            raw = registry["packs"]["source_stack"]["records"]
        except (KeyError, TypeError) as exc:
            raise _source_error(
                "the classified source-record catalog is missing"
            ) from exc
    else:
        raw = records_override
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise _source_error("the classified source-record catalog is not a list")
    records: dict[str, dict[str, Any]] = {}
    for record in raw:
        if not isinstance(record, Mapping):
            raise _source_error("a source record is not an object")
        source_id = record.get("source_id")
        if not isinstance(source_id, str) or not _IDENTITY_RE.fullmatch(source_id):
            raise _source_error("a source record has no stable source identity")
        if source_id in records:
            raise _source_error(f"{source_id} is declared more than once")
        _validate_source_record(source_id, record)
        records[source_id] = dict(record)
    return records


def _validate_source_record(source_id: str, record: Mapping[str, Any]) -> None:
    """Enforce the closed source-record schema; every violation fails closed."""
    missing = [field for field in _SOURCE_RECORD_FIELDS if field not in record]
    if missing:
        raise _source_error(f"{source_id} is missing {','.join(missing)}")
    unknown = sorted(set(record) - set(_SOURCE_RECORD_FIELDS))
    if unknown:
        raise _source_error(
            f"{source_id} declares fields outside the closed schema: {','.join(unknown)}"
        )
    source_class = record["class"]
    if source_class not in _SOURCE_CLASSES:
        raise _source_error(f"{source_id} has an undeclared source class")
    if record["status"] not in _SOURCE_STATUSES:
        raise _source_error(f"{source_id} has an undeclared status")
    for field in ("title", "context", "governed_scope", "refresh_trigger"):
        value = record[field]
        if not isinstance(value, str) or not value.strip():
            raise _source_error(f"{source_id}.{field} is not a non-empty string")
    verified_on = record["verified_on"]
    if not isinstance(verified_on, str) or not _DATE_RE.fullmatch(verified_on):
        raise _source_error(f"{source_id}.verified_on is not a YYYY-MM-DD date")
    try:
        date.fromisoformat(verified_on)
    except ValueError as exc:
        raise _source_error(
            f"{source_id}.verified_on is not a real calendar date"
        ) from exc
    exclusions = record["exclusions"]
    if (
        not isinstance(exclusions, list)
        or not exclusions
        or any(not isinstance(item, str) or not item.strip() for item in exclusions)
    ):
        raise _source_error(
            f"{source_id}.exclusions is not a non-empty list of non-empty strings"
        )
    precedence_scope = record["precedence_scope"]
    if not isinstance(precedence_scope, str) or not _IDENTITY_RE.fullmatch(
        precedence_scope
    ):
        raise _source_error(
            f"{source_id}.precedence_scope is not one stable scope identity"
        )
    if record["conflict_disposition"] not in _CONFLICT_DISPOSITIONS:
        raise _source_error(f"{source_id} has an undeclared conflict disposition")
    boundary = record["applicability_boundary"]
    conditions = record["applies_when"]
    if not isinstance(conditions, list):
        raise _source_error(f"{source_id}.applies_when is not a list")
    if source_class == "conditional":
        if not isinstance(boundary, str) or not boundary.strip():
            raise _source_error(
                f"{source_id} is conditional without an applicability boundary"
            )
        if not conditions:
            raise _source_error(
                f"{source_id} is conditional without applicability conditions"
            )
    else:
        if boundary is not None:
            raise _source_error(
                f"{source_id} declares an applicability boundary outside the "
                "conditional class"
            )
        if conditions:
            raise _source_error(
                f"{source_id} declares applicability conditions outside the "
                "conditional class"
            )
    for index, condition in enumerate(conditions):
        _validate_source_condition(source_id, index, condition)


def _validate_source_condition(source_id: str, index: int, condition: object) -> None:
    """One closed condition form: a declared fact with exactly one value shape."""
    if not isinstance(condition, Mapping):
        raise _source_error(f"{source_id} condition {index} is not an object")
    fact = condition.get("fact")
    if not isinstance(fact, str) or fact not in _SOURCE_CONDITION_FACTS:
        raise _source_error(f"{source_id} condition {index} has an undeclared fact")
    has_value = "value" in condition
    has_any = "any_of" in condition
    if has_value == has_any:
        raise _source_error(
            f"{source_id} condition {index} requires exactly one of value or any_of"
        )
    unknown = sorted(set(condition) - {"fact", "value", "any_of"})
    if unknown:
        raise _source_error(
            f"{source_id} condition {index} declares keys outside the closed "
            f"form: {','.join(unknown)}"
        )
    raw_values = [condition["value"]] if has_value else condition["any_of"]
    if not isinstance(raw_values, list) or not raw_values:
        raise _source_error(f"{source_id} condition {index} has no values")
    values: list[str] = []
    for value in raw_values:
        if not isinstance(value, str) or not _IDENTITY_RE.fullmatch(value):
            raise _source_error(
                f"{source_id} condition {index} has a value that is not a "
                "stable identity"
            )
        values.append(value)
    if len(values) != len(set(values)):
        raise _source_error(f"{source_id} condition {index} repeats a value")


def _validate_source_stack(
    pack_id: str,
    raw_sources: object,
    records: Mapping[str, Mapping[str, Any]],
) -> tuple[str, ...]:
    """Fail closed on missing authority, staleness, malformation, or conflict."""
    if not isinstance(raw_sources, list) or not raw_sources:
        raise PracticePackError(
            "pack-metadata-invalid",
            f"{pack_id}.sources must be a non-empty list",
            pack_id=pack_id,
        )
    source_ids: list[str] = []
    for source_id in raw_sources:
        if not isinstance(source_id, str) or source_id not in records:
            raise PracticePackError(
                "pack-source-record-invalid",
                f"{pack_id} references an unknown source {source_id!r}",
                pack_id=pack_id,
            )
        source_ids.append(source_id)
    if len(source_ids) != len(set(source_ids)):
        raise PracticePackError(
            "pack-metadata-invalid",
            f"{pack_id}.sources repeats a source",
            pack_id=pack_id,
        )
    referenced = [records[source_id] for source_id in source_ids]
    for record in referenced:
        if record["conflict_disposition"] == "unresolved":
            raise PracticePackError(
                "pack-source-conflict-unresolved",
                f"{record['source_id']} records an unresolved conflict",
                pack_id=pack_id,
            )
    scopes: dict[tuple[str, str], list[str]] = {}
    for record in referenced:
        scopes.setdefault(
            (record["class"], record["precedence_scope"]), []
        ).append(record["source_id"])
    for (source_class, scope), members in scopes.items():
        if len(members) < 2:
            continue
        unresolved = [
            source_id
            for source_id in members
            if records[source_id]["conflict_disposition"] != "resolved"
        ]
        if unresolved:
            raise PracticePackError(
                "pack-source-conflict-unresolved",
                f"{pack_id} has an unresolved equal-scope conflict in {scope}",
                pack_id=pack_id,
            )
    if not any(record["class"] == "governing" for record in referenced):
        raise PracticePackError(
            "pack-source-authority-missing",
            f"{pack_id} references no governing source; exemplars and watchlists never substitute",
            pack_id=pack_id,
        )
    for record in referenced:
        if record["class"] in ("governing", "conditional") and record["status"] != "current":
            raise PracticePackError(
                "pack-source-stale",
                f"{record['source_id']} is {record['status']} and cannot back {pack_id}",
                pack_id=pack_id,
            )
    return tuple(source_ids)


def validate_pack_catalog(
    metadata: Iterable[Mapping[str, object]],
    *,
    require_initial_catalog: bool = True,
    source_records: Sequence[Mapping[str, object]] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Validate metadata completely before selection can retrieve a body."""
    registry = _load_registry()
    packs_contract = registry["packs"]
    required_fields = {
        item["id"] for item in packs_contract["metadata_fields"] if item["required"]
    }
    allowed_families = {
        item["family"]
        for item in packs_contract["delivery_scope"]["required_initial_packs"]
    }
    precedence_ranks = {
        item["id"]: item["rank"] for item in packs_contract["precedence_classes"]
    }
    fact_classes = packs_contract["fact_precedence_classes"]
    forbidden = set(registry["independence"]["forbidden_pack_metadata_fields"])
    records = _source_records(registry, source_records)
    try:
        candidates = list(metadata)
    except TypeError as exc:
        raise PracticePackError(
            "pack-metadata-invalid", "metadata must be an iterable of records"
        ) from exc
    if not candidates:
        raise PracticePackError("pack-metadata-invalid", "metadata catalog is empty")

    normalized: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            raise PracticePackError(
                "pack-metadata-invalid", f"metadata record {index} is not an object"
            )
        raw_id = candidate.get("pack_id")
        pack_id = raw_id if isinstance(raw_id, str) else None
        missing = sorted(required_fields - set(candidate))
        if missing:
            raise PracticePackError(
                "pack-metadata-invalid",
                f"{pack_id or index} missing fields: {','.join(missing)}",
                pack_id=pack_id,
            )
        if set(candidate) & forbidden:
            raise PracticePackError(
                "pack-metadata-invalid",
                f"{pack_id or index} carries an independent risk or judgment field",
                pack_id=pack_id,
            )
        pack_id = _require_identity(raw_id, "pack_id", pack_id)
        family = _require_identity(candidate.get("family"), "family", pack_id)
        if family not in allowed_families:
            raise PracticePackError(
                "pack-metadata-invalid", f"{pack_id} has unapproved family {family}", pack_id=pack_id
            )
        revision = candidate.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise PracticePackError(
                "pack-metadata-invalid", f"{pack_id}.revision must be a positive integer", pack_id=pack_id
            )
        contract_version = candidate.get("contract_version")
        if contract_version != registry["contract_version"]:
            raise PracticePackError(
                "pack-metadata-incompatible",
                f"{pack_id} targets contract version {contract_version}",
                pack_id=pack_id,
            )
        applies = _normalized_conditions(
            candidate.get("applies_when"),
            pack_id=pack_id,
            field="applies_when",
            negative=False,
        )
        vetoes = _normalized_conditions(
            candidate.get("never_when"),
            pack_id=pack_id,
            field="never_when",
            negative=True,
        )
        declared_class = candidate.get("precedence_class")
        positive_classes = [fact_classes.get(item["fact"]) for item in applies]
        if None in positive_classes:
            raise PracticePackError(
                "pack-metadata-invalid",
                f"{pack_id} positive fact has no precedence class",
                pack_id=pack_id,
            )
        expected_class = min(positive_classes, key=precedence_ranks.__getitem__)
        if declared_class != expected_class:
            raise PracticePackError(
                "pack-metadata-invalid",
                f"{pack_id}.precedence_class must be {expected_class}",
                pack_id=pack_id,
            )
        tie_key = _require_identity(candidate.get("tie_key"), "tie_key", pack_id)
        body_ref = candidate.get("body_ref")
        if not isinstance(body_ref, str) or not _BODY_REF_RE.fullmatch(body_ref):
            raise PracticePackError(
                "pack-metadata-invalid", f"{pack_id}.body_ref is outside packs/*.md", pack_id=pack_id
            )
        if body_ref != f"packs/{pack_id}.md":
            raise PracticePackError(
                "pack-metadata-invalid",
                f"{pack_id}.body_ref must be packs/{pack_id}.md",
                pack_id=pack_id,
            )
        body_parts = PurePosixPath(body_ref).parts
        if ".." in body_parts or "." in body_parts or "\\" in body_ref:
            raise PracticePackError(
                "pack-metadata-invalid", f"{pack_id}.body_ref is not bounded", pack_id=pack_id
            )
        body_identity = candidate.get("body_content_identity")
        if not isinstance(body_identity, str) or not _BODY_IDENTITY_RE.fullmatch(
            body_identity
        ):
            raise PracticePackError(
                "pack-metadata-invalid",
                f"{pack_id}.body_content_identity is not a sha256 body identity",
                pack_id=pack_id,
            )
        budget = candidate.get("body_budget_bytes")
        if (
            not isinstance(budget, int)
            or isinstance(budget, bool)
            or budget < 1
            or budget > MAX_BODY_BUDGET_BYTES
        ):
            raise PracticePackError(
                "pack-metadata-invalid", f"{pack_id}.body_budget_bytes is invalid", pack_id=pack_id
            )
        sources = _validate_source_stack(pack_id, candidate.get("sources"), records)
        areas = candidate.get("content_areas")
        if not isinstance(areas, list) or not areas:
            raise PracticePackError(
                "pack-metadata-invalid", f"{pack_id}.content_areas is empty", pack_id=pack_id
            )
        normalized_areas = tuple(
            _require_identity(area, "content area", pack_id) for area in areas
        )
        if len(normalized_areas) != len(set(normalized_areas)):
            raise PracticePackError(
                "pack-metadata-invalid", f"{pack_id}.content_areas repeats an area", pack_id=pack_id
            )
        evidence_shape = candidate.get("evidence_shape")
        if not isinstance(evidence_shape, str) or not evidence_shape.strip():
            raise PracticePackError(
                "pack-metadata-invalid", f"{pack_id}.evidence_shape is empty", pack_id=pack_id
            )
        if len(evidence_shape.encode("utf-8")) > MAX_ENVELOPE_VALUE_BYTES:
            raise PracticePackError(
                "pack-metadata-invalid", f"{pack_id}.evidence_shape is oversized", pack_id=pack_id
            )
        normalized.append(
            {
                **dict(candidate),
                "pack_id": pack_id,
                "family": family,
                "revision": revision,
                "applies_when": applies,
                "never_when": vetoes,
                "precedence_class": declared_class,
                "precedence_rank": precedence_ranks[declared_class],
                "tie_key": tie_key,
                "body_ref": body_ref,
                "body_content_identity": body_identity,
                "body_budget_bytes": budget,
                "sources": sources,
                "content_areas": normalized_areas,
                "evidence_shape": evidence_shape.strip(),
            }
        )

    for field in ("pack_id", "family", "tie_key", "body_ref"):
        values = [item[field] for item in normalized]
        if len(values) != len(set(values)):
            raise PracticePackError(
                "pack-metadata-invalid", f"metadata catalog repeats {field}"
            )
    if require_initial_catalog:
        required = {
            (item["pack_id"], item["family"], tuple(item["content_areas"]))
            for item in packs_contract["delivery_scope"]["required_initial_packs"]
        }
        actual = {
            (item["pack_id"], item["family"], tuple(item["content_areas"]))
            for item in normalized
        }
        if actual != required:
            raise PracticePackError(
                "pack-metadata-invalid",
                "catalog must contain exactly the five approved packs and content areas",
            )
    return tuple(sorted(normalized, key=lambda item: item["tie_key"]))


def _normalize_envelope(envelope: Mapping[str, object]) -> dict[str, tuple[str, ...] | str | None]:
    if not isinstance(envelope, Mapping):
        raise PracticePackError("pack-envelope-invalid", "task envelope is not an object")
    normalized: dict[str, tuple[str, ...] | str | None] = {}
    for field in sorted(set(_SOURCE_FACT_TO_ENVELOPE.values())):
        raw = envelope.get(field, [])
        if not isinstance(raw, (list, tuple)) or len(raw) > MAX_ENVELOPE_VALUES:
            raise PracticePackError(
                "pack-envelope-invalid", f"{field} must be a bounded list"
            )
        values: list[str] = []
        for value in raw:
            if not isinstance(value, str) or not value.strip():
                raise PracticePackError(
                    "pack-envelope-invalid", f"{field} contains an invalid value"
                )
            clean = value.strip()
            if len(clean.encode("utf-8")) > MAX_ENVELOPE_VALUE_BYTES:
                raise PracticePackError(
                    "pack-envelope-invalid", f"{field} contains an oversized value"
                )
            values.append(clean)
        normalized[field] = tuple(sorted(set(values)))
    hint = envelope.get("authorized_profile_hint")
    if hint is not None:
        hint = _require_identity(hint, "authorized_profile_hint", None)
    normalized["authorized_profile_hint"] = hint
    return normalized


def _matches(condition: Mapping[str, object], envelope: Mapping[str, object]) -> bool:
    field = _FACT_TO_ENVELOPE[str(condition["fact"])]
    declared = set(envelope[field])
    return bool(declared.intersection(condition["values"]))


def _reason(condition: Mapping[str, object], matched: bool) -> str:
    values = "|".join(condition["values"])
    return f"{condition['fact']}:{values}:{'matched' if matched else 'not-matched'}"


def _parse_body_header(body: str, pack_id: str) -> tuple[dict[str, str], str]:
    if not body.startswith("---\n"):
        raise PracticePackError(
            "pack-body-invalid", f"{pack_id} has no metadata header", pack_id=pack_id
        )
    end = body.find("\n---\n", 4)
    if end < 0:
        raise PracticePackError(
            "pack-body-invalid", f"{pack_id} has an unterminated metadata header", pack_id=pack_id
        )
    fields: dict[str, str] = {}
    for line in body[4:end].splitlines():
        if ":" not in line:
            raise PracticePackError(
                "pack-body-invalid", f"{pack_id} has malformed metadata", pack_id=pack_id
            )
        key, value = (part.strip() for part in line.split(":", 1))
        if key in fields or not key or not value:
            raise PracticePackError(
                "pack-body-invalid", f"{pack_id} has malformed metadata", pack_id=pack_id
            )
        fields[key] = value
    if set(fields) != {"pack_id", "revision", "content_areas"}:
        raise PracticePackError(
            "pack-body-invalid", f"{pack_id} metadata fields differ", pack_id=pack_id
        )
    return fields, body[end + 5 :]


def _heading_identity(value: str) -> str:
    return "-".join(value.strip().casefold().replace("-", " ").split())


def _read_body_bytes(path: Path) -> bytes:
    """Single body-read seam; callers never receive bytes until validation passes."""
    return path.read_bytes()


def _source_condition_matches(
    condition: Mapping[str, Any], envelope: Mapping[str, Any]
) -> bool:
    fact = str(condition["fact"])
    declared = set(envelope[_SOURCE_FACT_TO_ENVELOPE[fact]])
    values = [condition["value"]] if "value" in condition else condition["any_of"]
    return bool(declared.intersection(values))


def _applicable_sources(
    metadata: Mapping[str, Any],
    records: Mapping[str, Mapping[str, Any]],
    envelope: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Project only the authority that applies to this selection.

    Governing sources always apply inside their governed scope; a conditional
    source applies only when the envelope declares a matching fact. Structural
    exemplars and watchlists never carry applicable authority, so they are never
    projected. Nothing here can change which pack was selected.
    """
    applicable: list[dict[str, Any]] = []
    for source_id in metadata["sources"]:
        record = records[source_id]
        source_class = record["class"]
        if source_class not in _APPLICABLE_SOURCE_CLASSES:
            continue
        if source_class == "conditional" and not any(
            _source_condition_matches(condition, envelope)
            for condition in record["applies_when"]
        ):
            continue
        applicable.append({field: record[field] for field in _SOURCE_IDENTITY_FIELDS})
    return applicable


def _load_selected_body(
    metadata: Mapping[str, Any], protocol_root: Path
) -> tuple[str, int, str]:
    pack_id = metadata["pack_id"]
    root = Path(protocol_root)
    try:
        packs_root = (root / "packs").resolve(strict=True)
        candidate = (root / metadata["body_ref"]).resolve(strict=True)
    except FileNotFoundError as exc:
        raise PracticePackError(
            "pack-body-missing", f"{pack_id} body does not exist", pack_id=pack_id
        ) from exc
    except OSError as exc:
        raise PracticePackError(
            "pack-body-unreadable", f"{pack_id} body cannot be resolved", pack_id=pack_id
        ) from exc
    try:
        candidate.relative_to(packs_root)
    except ValueError as exc:
        raise PracticePackError(
            "pack-body-out-of-bounds", f"{pack_id} body resolves outside packs", pack_id=pack_id
        ) from exc
    if not candidate.is_file():
        raise PracticePackError(
            "pack-body-unreadable", f"{pack_id} body is not a regular file", pack_id=pack_id
        )
    try:
        declared_size = candidate.stat().st_size
    except OSError as exc:
        raise PracticePackError(
            "pack-body-unreadable", f"{pack_id} body cannot be inspected", pack_id=pack_id
        ) from exc
    if declared_size > metadata["body_budget_bytes"]:
        raise PracticePackError(
            "pack-body-oversized",
            f"{pack_id} body exceeds {metadata['body_budget_bytes']} bytes",
            pack_id=pack_id,
        )
    try:
        raw = _read_body_bytes(candidate)
    except OSError as exc:
        raise PracticePackError(
            "pack-body-unreadable", f"{pack_id} body cannot be read", pack_id=pack_id
        ) from exc
    if len(raw) > metadata["body_budget_bytes"]:
        raise PracticePackError(
            "pack-body-oversized",
            f"{pack_id} body exceeds {metadata['body_budget_bytes']} bytes",
            pack_id=pack_id,
        )
    try:
        body = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PracticePackError(
            "pack-body-invalid-utf8", f"{pack_id} body is not UTF-8", pack_id=pack_id
        ) from exc
    fields, guidance = _parse_body_header(body, pack_id)
    if fields["pack_id"] != pack_id:
        raise PracticePackError(
            "pack-body-stale", f"{pack_id} body identity differs", pack_id=pack_id
        )
    try:
        body_revision = int(fields["revision"])
    except ValueError as exc:
        raise PracticePackError(
            "pack-body-invalid", f"{pack_id} body revision is invalid", pack_id=pack_id
        ) from exc
    if body_revision != metadata["revision"]:
        raise PracticePackError(
            "pack-body-stale", f"{pack_id} body revision differs", pack_id=pack_id
        )
    header_areas = tuple(item.strip() for item in fields["content_areas"].split(","))
    headings = tuple(_heading_identity(item) for item in _HEADING_RE.findall(guidance))
    required = tuple(metadata["content_areas"])
    if header_areas != required or headings != required:
        raise PracticePackError(
            "pack-body-content-areas-mismatch",
            f"{pack_id} body does not cover exactly its approved content areas",
            pack_id=pack_id,
        )
    # Last, because a structurally broken body deserves its specific diagnostic:
    # this catches the body that is well-formed but no longer the authored one.
    identity = f"sha256:{hashlib.sha256(raw).hexdigest()}"
    if identity != metadata["body_content_identity"]:
        raise PracticePackError(
            "pack-body-stale",
            f"{pack_id} body content identity differs from its declared identity",
            pack_id=pack_id,
        )
    return body, len(raw), identity


def select_practice_pack(
    envelope: Mapping[str, object],
    *,
    metadata: Iterable[Mapping[str, object]] | None = None,
    protocol_root: Path | str = DEFAULT_PROTOCOL_ROOT,
    source_records: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, Any]:
    """Resolve exactly one eligible pack or none, then load only that body."""
    try:
        registry = _load_registry()
        candidates = validate_pack_catalog(
            load_pack_catalog() if metadata is None else metadata,
            source_records=source_records,
        )
        records = _source_records(registry, source_records)
        facts = _normalize_envelope(envelope)
    except PracticePackError as exc:
        return _invalid(exc.code, exc.detail, pack_id=exc.pack_id)
    authored_bodies = len(candidates)

    eligible: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for candidate in candidates:
        positives = [
            (condition, _matches(condition, facts))
            for condition in candidate["applies_when"]
        ]
        negatives = [
            (condition, _matches(condition, facts))
            for condition in candidate["never_when"]
        ]
        missing = [_reason(condition, matched) for condition, matched in positives if not matched]
        vetoes = [_reason(condition, matched) for condition, matched in negatives if matched]
        if missing or vetoes:
            rejected.append(
                {
                    "pack_id": candidate["pack_id"],
                    "reasons": [*missing, *vetoes],
                }
            )
            continue
        eligible.append(
            {
                "metadata": candidate,
                "reasons": [
                    {
                        "pack_id": candidate["pack_id"],
                        "condition": _reason(condition, matched),
                        "precedence_class": candidate["precedence_class"],
                    }
                    for condition, matched in positives
                ],
            }
        )

    result = _base_result(registry, "none")
    result["rejected_candidates"] = rejected
    if not eligible:
        return _with_context_receipt(result, authored_bodies=authored_bodies)

    best_rank = min(item["metadata"]["precedence_rank"] for item in eligible)
    survivors = [item for item in eligible if item["metadata"]["precedence_rank"] == best_rank]
    best_specificity = max(len(item["metadata"]["applies_when"]) for item in survivors)
    survivors = [
        item
        for item in survivors
        if len(item["metadata"]["applies_when"]) == best_specificity
    ]
    hint = facts["authorized_profile_hint"]
    if len(survivors) > 1 and hint:
        hinted = [
            item
            for item in survivors
            if hint in (item["metadata"]["pack_id"], item["metadata"]["family"])
        ]
        if len(hinted) == 1:
            survivors = hinted

    if len(survivors) != 1:
        result["outcome"] = "ambiguous"
        result["ordered_match_reasons"] = [
            reason
            for item in survivors
            for reason in item["reasons"]
        ]
        colliding = [item["metadata"]["pack_id"] for item in survivors]
        result["error"] = {
            "code": "pack-selection-ambiguous",
            "detail": "equal precedence: " + ",".join(colliding),
        }
        return _with_context_receipt(result, authored_bodies=authored_bodies)

    selected = survivors[0]
    result["ordered_match_reasons"] = selected["reasons"]
    try:
        body, byte_count, body_identity = _load_selected_body(
            selected["metadata"], Path(protocol_root)
        )
    except PracticePackError as exc:
        invalid = _invalid(exc.code, exc.detail, pack_id=exc.pack_id)
        invalid["ordered_match_reasons"] = selected["reasons"]
        invalid["rejected_candidates"] = rejected + invalid["rejected_candidates"]
        return _with_context_receipt(invalid, authored_bodies=authored_bodies)
    result.update(
        {
            "outcome": "selected",
            "pack_id": selected["metadata"]["pack_id"],
            "bodies_loaded": 1,
            "loaded_body_bytes": byte_count,
            "body_identity": body_identity,
            "body_budget_bytes": selected["metadata"]["body_budget_bytes"],
            "applicable_sources": _applicable_sources(
                selected["metadata"], records, facts
            ),
            "body": body,
        }
    )
    return _with_context_receipt(result, authored_bodies=authored_bodies)
