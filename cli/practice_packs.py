"""Deterministic validation, selection, and bounded practice-pack loading.

The machine vocabulary and the five-pack metadata live in
``protocol/risk-and-practice-contract.json``.  This module contains only the
shared executable projection used by the CLI and MCP surfaces.
"""
from __future__ import annotations

import json
import re
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
_BODY_REF_RE = re.compile(r"^packs/[a-z][a-z0-9]*(?:-[a-z0-9]+)*\.md$")
_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_FACT_TO_ENVELOPE = {
    "primary_outcome": "primary_outcomes",
    "artifact_kind": "artifact_kinds",
    "incidental_term": "incidental_terms",
    "exclusion": "exclusions",
    "lifecycle_substrate": "lifecycle_substrate_activities",
}


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
    except PracticePackError:
        contract_id = "risk-and-practice"
        contract_version = None
    rejected = []
    if pack_id:
        rejected.append({"pack_id": pack_id, "reasons": [f"{code}:{detail}"]})
    return {
        "contract_id": contract_id,
        "contract_version": contract_version,
        "outcome": "invalid",
        "pack_id": None,
        "ordered_match_reasons": [],
        "rejected_candidates": rejected,
        "bodies_loaded": 0,
        "loaded_body_bytes": 0,
        "body": None,
        "error": {"code": code, "detail": detail},
    }


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
        "body": None,
        "error": None,
    }


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


def validate_pack_catalog(
    metadata: Iterable[Mapping[str, object]], *, require_initial_catalog: bool = True
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
                "body_budget_bytes": budget,
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
    for field in _FACT_TO_ENVELOPE.values():
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


def _load_selected_body(metadata: Mapping[str, Any], protocol_root: Path) -> tuple[str, int]:
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
    return body, len(raw)


def select_practice_pack(
    envelope: Mapping[str, object],
    *,
    metadata: Iterable[Mapping[str, object]] | None = None,
    protocol_root: Path | str = DEFAULT_PROTOCOL_ROOT,
) -> dict[str, Any]:
    """Resolve exactly one eligible pack or none, then load only that body."""
    try:
        registry = _load_registry()
        candidates = validate_pack_catalog(
            load_pack_catalog() if metadata is None else metadata
        )
        facts = _normalize_envelope(envelope)
    except PracticePackError as exc:
        return _invalid(exc.code, exc.detail, pack_id=exc.pack_id)

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
        return result

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
        return result

    selected = survivors[0]
    result["ordered_match_reasons"] = selected["reasons"]
    try:
        body, byte_count = _load_selected_body(selected["metadata"], Path(protocol_root))
    except PracticePackError as exc:
        invalid = _invalid(exc.code, exc.detail, pack_id=exc.pack_id)
        invalid["ordered_match_reasons"] = selected["reasons"]
        invalid["rejected_candidates"] = rejected + invalid["rejected_candidates"]
        return invalid
    result.update(
        {
            "outcome": "selected",
            "pack_id": selected["metadata"]["pack_id"],
            "bodies_loaded": 1,
            "loaded_body_bytes": byte_count,
            "body": body,
        }
    )
    return result
