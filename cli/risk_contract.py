"""Runtime projection of the authoritative risk contract.

The vocabulary and policy rows remain owned by
``protocol/risk-and-practice-contract.json``.  This module validates bounded
observable records, applies the registry's declared dominance rule, and builds
the deliberately small context used for a critical independent challenge.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


REGISTRY_PATH = (
    Path(__file__).resolve().parents[1]
    / "protocol"
    / "risk-and-practice-contract.json"
)
DEFAULT_MAX_REVIEW_CONTEXT_BYTES = 128 * 1024
MAX_SUPPORTING_FACT_BYTES = 1024


class RiskContractError(ValueError):
    """Stable fail-closed diagnostic for invalid risk or review input."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def load_risk_contract() -> dict[str, Any]:
    """Read the single machine authority and fail closed on malformed data."""
    try:
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        risk = registry["risk"]
        bands = risk["band_order"]
        observations = risk["observations"]
        governance = risk["governance"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RiskContractError(
            "risk-contract-unavailable", "the authoritative registry is unreadable"
        ) from exc
    if not isinstance(bands, list) or not bands:
        raise RiskContractError("risk-contract-invalid", "band_order is empty")
    if not isinstance(observations, list) or not observations:
        raise RiskContractError("risk-contract-invalid", "observations are empty")
    if not isinstance(governance, list):
        raise RiskContractError("risk-contract-invalid", "governance is not a list")
    return registry


def observation_choices() -> dict[str, tuple[str, ...]]:
    """Return observation/state choices in authoritative declaration order."""
    risk = load_risk_contract()["risk"]
    return {
        observation["id"]: tuple(state["id"] for state in observation["states"])
        for observation in risk["observations"]
    }


def _bounded_fact(value: object, observation_id: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RiskContractError(
            "missing-supporting-fact",
            f"{observation_id} requires a non-empty supporting fact identity",
        )
    normalized = value.strip()
    if len(normalized.encode("utf-8")) > MAX_SUPPORTING_FACT_BYTES:
        raise RiskContractError(
            "supporting-fact-too-large",
            f"{observation_id} exceeds {MAX_SUPPORTING_FACT_BYTES} bytes",
        )
    return normalized


def classify_risk(observations: Iterable[Mapping[str, object]]) -> dict[str, Any]:
    """Classify exactly one bounded record for each declared observation.

    The highest state floor wins.  Input ordering cannot change the result;
    reasons are emitted in the registry's declaration order.
    """
    registry = load_risk_contract()
    risk = registry["risk"]
    bands = risk["band_order"]
    declared = [item["id"] for item in risk["observations"]]
    state_rows = {
        item["id"]: {state["id"]: state for state in item["states"]}
        for item in risk["observations"]
    }

    provided: dict[str, Mapping[str, object]] = {}
    try:
        records = list(observations)
    except TypeError as exc:
        raise RiskContractError(
            "invalid-observations", "observations must be an iterable of records"
        ) from exc
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise RiskContractError(
                "invalid-observation-record", f"record {index} is not an object"
            )
        observation_id = record.get("observation")
        if not isinstance(observation_id, str):
            raise RiskContractError(
                "invalid-observation-record", f"record {index} has no observation id"
            )
        if observation_id not in declared:
            raise RiskContractError(
                "undeclared-observation", observation_id
            )
        if observation_id in provided:
            raise RiskContractError("duplicate-observation", observation_id)
        provided[observation_id] = record

    missing = [item for item in declared if item not in provided]
    if missing:
        raise RiskContractError(
            "invalid-observations",
            ",".join(f"missing-observation:{item}" for item in missing),
        )

    normalized: list[dict[str, Any]] = []
    for observation_id in declared:
        record = provided[observation_id]
        state_id = record.get("state")
        if not isinstance(state_id, str) or state_id not in state_rows[observation_id]:
            raise RiskContractError(
                "undeclared-observation-state",
                f"{observation_id}={state_id}",
            )
        state = state_rows[observation_id][state_id]
        normalized.append(
            {
                "observation": observation_id,
                "state": state_id,
                "supporting_fact": _bounded_fact(
                    record.get("supporting_fact"), observation_id
                ),
                "band_floor": state["band_floor"],
                "elevates_governance": bool(state["elevates_governance"]),
            }
        )

    band = max(
        (item["band_floor"] for item in normalized), key=bands.index
    )
    reasons = [
        item
        for item in normalized
        if bands.index(item["band_floor"]) == bands.index(band)
    ]
    rows = [item for item in risk["governance"] if item.get("band") == band]
    if len(rows) != 1:
        raise RiskContractError(
            "risk-contract-invalid", f"expected exactly one governance row for {band}"
        )
    governance = rows[0]
    return {
        "contract_id": registry["contract_id"],
        "contract_version": registry["contract_version"],
        "band": band,
        "ordered_reasons": reasons,
        "evidence_expectation": governance["evidence_expectation"],
        "review_expectation": governance["review_expectation"],
        "operator_gate": governance["operator_gate"],
        "contingency_expectation": governance["contingency_expectation"],
    }


def validate_risk_result(result: Mapping[str, object]) -> dict[str, Any]:
    """Validate a derived result against the current authoritative policy row."""
    registry = load_risk_contract()
    if result.get("contract_id") != registry["contract_id"]:
        raise RiskContractError("risk-result-incompatible", "contract_id differs")
    if result.get("contract_version") != registry["contract_version"]:
        raise RiskContractError("risk-result-incompatible", "contract_version differs")
    band = result.get("band")
    rows = [row for row in registry["risk"]["governance"] if row["band"] == band]
    if len(rows) != 1:
        raise RiskContractError("risk-result-invalid", f"unknown band: {band}")
    row = rows[0]
    for field in (
        "evidence_expectation",
        "review_expectation",
        "operator_gate",
        "contingency_expectation",
    ):
        if result.get(field) != row[field]:
            raise RiskContractError(
                "risk-result-invalid", f"{field} does not match band {band}"
            )
    reasons = result.get("ordered_reasons")
    if not isinstance(reasons, list) or not reasons:
        raise RiskContractError("risk-result-invalid", "ordered_reasons is empty")
    required = registry["risk"]["classification"]["result_fields"]
    return {field: result[field] for field in required}


def _contained_file(path: Path, allowed_roots: Sequence[Path], label: str) -> Path:
    if not path.is_absolute():
        raise RiskContractError("review-context-path-invalid", f"{label} must be absolute")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RiskContractError(
            "review-context-path-invalid", f"{label} does not resolve"
        ) from exc
    if not resolved.is_file():
        raise RiskContractError(
            "review-context-path-invalid", f"{label} is not a regular file"
        )
    contained = False
    for root in allowed_roots:
        try:
            resolved.relative_to(root.resolve(strict=True))
            contained = True
            break
        except (OSError, RuntimeError, ValueError):
            continue
    if not contained:
        raise RiskContractError(
            "review-context-path-outside-roots", f"{label} is outside allowed roots"
        )
    return resolved


def _read_context_entry(path: Path, label: str, max_bytes: int) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise RiskContractError(
            "review-context-unreadable", f"cannot stat {label}"
        ) from exc
    if size > max_bytes:
        raise RiskContractError(
            "review-context-too-large", f"{label} exceeds {max_bytes} bytes"
        )
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RiskContractError(
            "review-context-unreadable", f"cannot read {label} as UTF-8"
        ) from exc
    encoded = content.encode("utf-8")
    return {
        "path": str(path),
        "content_identity": "sha256:" + hashlib.sha256(encoded).hexdigest(),
        "content": content,
        "bytes": len(encoded),
    }


def build_adversarial_review_context(
    artifact_path: Path,
    governing_contract_path: Path,
    *,
    allowed_roots: Sequence[Path],
    risk_result: Mapping[str, object],
    max_context_bytes: int = DEFAULT_MAX_REVIEW_CONTEXT_BYTES,
) -> dict[str, Any]:
    """Build fresh critical-review context with exactly two payload entries."""
    if not isinstance(max_context_bytes, int) or max_context_bytes < 1:
        raise RiskContractError(
            "review-context-limit-invalid", "max_context_bytes must be positive"
        )
    validated = validate_risk_result(risk_result)
    if validated["band"] != "critical":
        raise RiskContractError(
            "critical-risk-required",
            "adversarial review context is reserved for the critical band",
        )
    if not allowed_roots:
        raise RiskContractError("review-context-path-invalid", "no allowed roots")
    resolved_roots = []
    for allowed_root in allowed_roots:
        candidate = Path(allowed_root)
        if not candidate.is_absolute():
            raise RiskContractError(
                "review-context-path-invalid", "allowed roots must be absolute"
            )
        try:
            resolved_root = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise RiskContractError(
                "review-context-path-invalid", "an allowed root does not resolve"
            ) from exc
        if not resolved_root.is_dir():
            raise RiskContractError(
                "review-context-path-invalid", "an allowed root is not a directory"
            )
        resolved_roots.append(resolved_root)
    artifact = _contained_file(Path(artifact_path), resolved_roots, "artifact")
    contract = _contained_file(
        Path(governing_contract_path), resolved_roots, "governing contract"
    )
    if artifact == contract:
        raise RiskContractError(
            "review-context-path-invalid", "artifact and governing contract must differ"
        )
    context = {
        "artifact": _read_context_entry(artifact, "artifact", max_context_bytes),
        "governing_contract": _read_context_entry(
            contract, "governing contract", max_context_bytes
        ),
    }
    context_bytes = sum(item["bytes"] for item in context.values())
    if context_bytes > max_context_bytes:
        raise RiskContractError(
            "review-context-too-large",
            f"combined payload is {context_bytes} bytes; limit is {max_context_bytes}",
        )
    identity_input = {
        "risk_result": validated,
        "context": {
            key: {
                "path": value["path"],
                "content_identity": value["content_identity"],
                "bytes": value["bytes"],
            }
            for key, value in context.items()
        },
    }
    canonical = json.dumps(
        identity_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return {
        "contract_id": validated["contract_id"],
        "contract_version": validated["contract_version"],
        "risk_band": "critical",
        "evidence_expectation": validated["evidence_expectation"],
        "review_expectation": validated["review_expectation"],
        "operator_gate": validated["operator_gate"],
        "contingency_expectation": validated["contingency_expectation"],
        "context_identity": "sha256:" + hashlib.sha256(canonical).hexdigest(),
        "context_bytes": context_bytes,
        "max_context_bytes": max_context_bytes,
        "context": context,
    }
