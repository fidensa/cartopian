"""Deterministic judgment-card activation and bounded guidance loading.

The cards, their lifecycle boundaries, the shared failure-signal grammar, and
the one authored guidance body are owned by
``protocol/risk-and-practice-contract.json``.  This module is only the shared
executable projection the CLI and MCP surfaces call.

Activation reads two declared envelope facts and nothing else.  A risk band and
a pack outcome are not merely ignored here — supplying either is a fail-closed
input error, so the absence of an activation edge is enforced rather than
asserted.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping


REGISTRY_PATH = (
    Path(__file__).resolve().parents[1]
    / "protocol"
    / "risk-and-practice-contract.json"
)
DEFAULT_PROTOCOL_ROOT = REGISTRY_PATH.parent
MAX_ENVELOPE_VALUE_BYTES = 512
MAX_ENVELOPE_VALUES = 32
MAX_BODY_BUDGET_BYTES = 16 * 1024

ENVELOPE_FACTS = ("lifecycle_boundaries", "open_failure_conditions")

_IDENTITY_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_BODY_REF_RE = re.compile(r"^judgment/[a-z][a-z0-9]*(?:-[a-z0-9]+)*\.md$")
_BODY_IDENTITY_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


class JudgmentGuidanceError(ValueError):
    """Stable fail-closed diagnostic for envelope or guidance-body input."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _load_registry() -> dict[str, Any]:
    try:
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        judgment = registry["judgment"]
        cards = judgment["cards"]
        grammar = judgment["failure_signal_grammar"]
        body = judgment["guidance_body"]
        rule = judgment["activation_rule"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise JudgmentGuidanceError(
            "judgment-contract-unavailable",
            "the authoritative judgment registry is unreadable",
        ) from exc
    if not isinstance(cards, list) or not cards:
        raise JudgmentGuidanceError("judgment-contract-invalid", "cards are empty")
    if not isinstance(grammar, list) or not grammar:
        raise JudgmentGuidanceError(
            "judgment-contract-invalid", "the failure-signal grammar is empty"
        )
    if (rule.get("boundary_fact"), rule.get("failure_fact")) != ENVELOPE_FACTS:
        raise JudgmentGuidanceError(
            "judgment-contract-invalid", "activation facts differ from the projection"
        )
    _validate_guidance_body_metadata(body, cards, grammar)
    return registry


def _validate_guidance_body_metadata(
    body: Mapping[str, Any],
    cards: list[Mapping[str, Any]],
    grammar: list[Mapping[str, Any]],
) -> None:
    """Reject a guidance declaration that could not be one central contract."""
    if not isinstance(body, Mapping):
        raise JudgmentGuidanceError(
            "judgment-contract-invalid", "guidance_body is not an object"
        )
    if body.get("authored_body_count") != 1:
        raise JudgmentGuidanceError(
            "judgment-contract-invalid", "the grammar declares more than one body"
        )
    body_ref = body.get("body_ref")
    if not isinstance(body_ref, str) or not _BODY_REF_RE.fullmatch(body_ref):
        raise JudgmentGuidanceError(
            "judgment-contract-invalid", "guidance body_ref is not a bounded locator"
        )
    identity = body.get("body_content_identity")
    if not isinstance(identity, str) or not _BODY_IDENTITY_RE.fullmatch(identity):
        raise JudgmentGuidanceError(
            "judgment-contract-invalid", "guidance body_content_identity is malformed"
        )
    budget = body.get("body_budget_bytes")
    if not isinstance(budget, int) or isinstance(budget, bool):
        raise JudgmentGuidanceError(
            "judgment-contract-invalid", "guidance body_budget_bytes is not an integer"
        )
    if not 0 < budget <= MAX_BODY_BUDGET_BYTES:
        raise JudgmentGuidanceError(
            "judgment-contract-invalid", "guidance body_budget_bytes is out of range"
        )
    sections = body.get("sections")
    if list(sections or ()) != [item["id"] for item in grammar]:
        raise JudgmentGuidanceError(
            "judgment-contract-invalid",
            "guidance sections differ from the central grammar",
        )
    guidance_id = body.get("guidance_id")
    for card in cards:
        if card.get("guidance") != guidance_id:
            raise JudgmentGuidanceError(
                "judgment-contract-invalid",
                f"{card.get('id')} does not reference the central guidance",
            )
        if card.get("body_budget_bytes") != budget:
            raise JudgmentGuidanceError(
                "judgment-contract-invalid",
                f"{card.get('id')} declares a budget the central body does not share",
            )


def _normalize_envelope(
    envelope: Mapping[str, object], registry: Mapping[str, Any]
) -> dict[str, tuple[str, ...]]:
    """Accept the two declared facts, and only those two.

    An undeclared key is rejected rather than ignored so a caller cannot quietly
    hand the selector a risk band or a pack outcome and have it accepted.
    """
    if not isinstance(envelope, Mapping):
        raise JudgmentGuidanceError(
            "judgment-envelope-invalid", "the envelope is not an object"
        )
    forbidden = registry["judgment"]["forbidden_activation_inputs"]
    supplied = set(envelope)
    named = sorted(supplied.intersection(forbidden))
    if named:
        raise JudgmentGuidanceError(
            "judgment-envelope-forbidden-input",
            "activation reads no risk or pack fact: " + ",".join(named),
        )
    extra = sorted(supplied.difference(ENVELOPE_FACTS))
    if extra:
        raise JudgmentGuidanceError(
            "judgment-envelope-invalid",
            "undeclared envelope fact: " + ",".join(extra),
        )
    normalized: dict[str, tuple[str, ...]] = {}
    for fact in ENVELOPE_FACTS:
        raw = envelope.get(fact, ())
        if raw is None:
            raw = ()
        if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
            raise JudgmentGuidanceError(
                "judgment-envelope-invalid", f"{fact} must be a bounded list"
            )
        if len(raw) > MAX_ENVELOPE_VALUES:
            raise JudgmentGuidanceError(
                "judgment-envelope-invalid", f"{fact} declares too many values"
            )
        values: list[str] = []
        for value in raw:
            if not isinstance(value, str) or not value.strip():
                raise JudgmentGuidanceError(
                    "judgment-envelope-invalid", f"{fact} contains an invalid value"
                )
            clean = value.strip()
            if len(clean.encode("utf-8")) > MAX_ENVELOPE_VALUE_BYTES:
                raise JudgmentGuidanceError(
                    "judgment-envelope-invalid", f"{fact} contains an oversized value"
                )
            if not _IDENTITY_RE.fullmatch(clean):
                raise JudgmentGuidanceError(
                    "judgment-envelope-invalid",
                    f"{fact} contains a non-identity value: {clean}",
                )
            values.append(clean)
        normalized[fact] = tuple(sorted(set(values)))
    return normalized


def _heading_identity(value: str) -> str:
    return "-".join(value.strip().casefold().replace("-", " ").split())


def _parse_body_header(body: str) -> tuple[dict[str, str], str]:
    if not body.startswith("---\n"):
        raise JudgmentGuidanceError(
            "judgment-body-invalid", "the guidance body has no metadata header"
        )
    end = body.find("\n---\n", 4)
    if end < 0:
        raise JudgmentGuidanceError(
            "judgment-body-invalid", "the guidance body header is unterminated"
        )
    fields: dict[str, str] = {}
    for line in body[4:end].splitlines():
        if ":" not in line:
            raise JudgmentGuidanceError(
                "judgment-body-invalid", "the guidance body header is malformed"
            )
        key, value = (part.strip() for part in line.split(":", 1))
        if key in fields or not key or not value:
            raise JudgmentGuidanceError(
                "judgment-body-invalid", "the guidance body header is malformed"
            )
        fields[key] = value
    if set(fields) != {"contract_id", "revision", "elements"}:
        raise JudgmentGuidanceError(
            "judgment-body-invalid", "the guidance body header fields differ"
        )
    return fields, body[end + 5 :]


def _read_body_bytes(path: Path) -> bytes:
    """Single body-read seam; callers never receive bytes until validation passes."""
    return path.read_bytes()


def _load_guidance_body(
    declared: Mapping[str, Any],
    grammar: list[Mapping[str, Any]],
    protocol_root: Path,
) -> tuple[str, int, str]:
    root = Path(protocol_root)
    try:
        guidance_root = (root / "judgment").resolve(strict=True)
        candidate = (root / declared["body_ref"]).resolve(strict=True)
    except FileNotFoundError as exc:
        raise JudgmentGuidanceError(
            "judgment-body-missing", "the central guidance body does not exist"
        ) from exc
    except OSError as exc:
        raise JudgmentGuidanceError(
            "judgment-body-unreadable", "the central guidance body cannot be resolved"
        ) from exc
    try:
        candidate.relative_to(guidance_root)
    except ValueError as exc:
        raise JudgmentGuidanceError(
            "judgment-body-out-of-bounds",
            "the central guidance body resolves outside the judgment directory",
        ) from exc
    if not candidate.is_file():
        raise JudgmentGuidanceError(
            "judgment-body-unreadable",
            "the central guidance body is not a regular file",
        )
    budget = declared["body_budget_bytes"]
    try:
        declared_size = candidate.stat().st_size
    except OSError as exc:
        raise JudgmentGuidanceError(
            "judgment-body-unreadable", "the central guidance body cannot be inspected"
        ) from exc
    if declared_size > budget:
        raise JudgmentGuidanceError(
            "judgment-body-oversized",
            f"the central guidance body exceeds {budget} bytes",
        )
    try:
        raw = _read_body_bytes(candidate)
    except OSError as exc:
        raise JudgmentGuidanceError(
            "judgment-body-unreadable", "the central guidance body cannot be read"
        ) from exc
    if len(raw) > budget:
        raise JudgmentGuidanceError(
            "judgment-body-oversized",
            f"the central guidance body exceeds {budget} bytes",
        )
    try:
        body = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise JudgmentGuidanceError(
            "judgment-body-invalid-utf8", "the central guidance body is not UTF-8"
        ) from exc
    fields, guidance = _parse_body_header(body)
    if fields["contract_id"] != declared["contract_id"]:
        raise JudgmentGuidanceError(
            "judgment-body-stale", "the guidance body identity differs"
        )
    try:
        revision = int(fields["revision"])
    except ValueError as exc:
        raise JudgmentGuidanceError(
            "judgment-body-invalid", "the guidance body revision is invalid"
        ) from exc
    if revision != declared["revision"]:
        raise JudgmentGuidanceError(
            "judgment-body-stale", "the guidance body revision differs"
        )
    required = tuple(item["id"] for item in grammar)
    header_elements = tuple(item.strip() for item in fields["elements"].split(","))
    headings = tuple(_heading_identity(item) for item in _HEADING_RE.findall(guidance))
    if header_elements != required or headings != required:
        raise JudgmentGuidanceError(
            "judgment-body-invalid",
            "the guidance body does not name exactly the central grammar elements",
        )
    for element in grammar:
        if element["prompt"] not in guidance:
            raise JudgmentGuidanceError(
                "judgment-body-stale",
                f"the guidance body no longer carries the {element['id']} prompt",
            )
    # Last, because a structurally broken body deserves its specific diagnostic:
    # this catches the body that is well-formed but no longer the authored one.
    identity = f"sha256:{hashlib.sha256(raw).hexdigest()}"
    if identity != declared["body_content_identity"]:
        raise JudgmentGuidanceError(
            "judgment-body-stale",
            "the guidance body content identity differs from its declared identity",
        )
    return body, len(raw), identity


def _routing_metadata_bytes(result: Mapping[str, Any]) -> int:
    """Measure the compact activation projection: this result without the body.

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


def _with_context_receipt(result: dict[str, Any]) -> dict[str, Any]:
    """Separate the compact activation metadata from the one admitted body.

    The grammar has one authored body, so a duplicate copy is a measured zero
    rather than an assertion, and so is a workflow that activated no card.
    """
    result["context_receipt"] = {
        "routing_metadata_bytes": _routing_metadata_bytes(result),
        "loaded_guidance_bytes": result["loaded_guidance_bytes"],
        "guidance_budget_bytes": result["guidance_budget_bytes"],
        "central_bodies_loaded": result["guidance_bodies_loaded"],
        "duplicate_guidance_bytes": 0,
        "inactive_boundary_bytes": 0,
    }
    return result


def _base_result(contract_id: str, contract_version: object, outcome: str) -> dict[str, Any]:
    return {
        "contract_id": contract_id,
        "contract_version": contract_version,
        "outcome": outcome,
        "active_cards": [],
        "cards_active": 0,
        "ordered_activation_reasons": [],
        "inactive_cards": [],
        "guidance_bodies_loaded": 0,
        "loaded_guidance_bytes": 0,
        "guidance_identity": None,
        "guidance_budget_bytes": None,
        "context_receipt": None,
        "body": None,
        "error": None,
    }


def _invalid(
    code: str, detail: str, *, inactive_cards: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    try:
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        contract_id = registry["contract_id"]
        contract_version = registry["contract_version"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError):
        contract_id = "risk-and-practice"
        contract_version = None
    result = _base_result(contract_id, contract_version, "invalid")
    result["inactive_cards"] = inactive_cards or []
    result["error"] = {"code": code, "detail": detail}
    return _with_context_receipt(result)


def _activation_reason(card: Mapping[str, Any]) -> str:
    return f"{card['boundary_id']}+{card['failure_id']}:matched"


def select_judgment_guidance(
    envelope: Mapping[str, object],
    *,
    protocol_root: Path | str = DEFAULT_PROTOCOL_ROOT,
) -> dict[str, Any]:
    """Activate the cards the envelope declares, then load one body or none."""
    try:
        registry = _load_registry()
        facts = _normalize_envelope(envelope, registry)
    except JudgmentGuidanceError as exc:
        return _invalid(exc.code, exc.detail)

    judgment = registry["judgment"]
    cards = judgment["cards"]
    boundaries = set(facts["lifecycle_boundaries"])
    failures = set(facts["open_failure_conditions"])

    active: list[dict[str, Any]] = []
    inactive: list[dict[str, Any]] = []
    reasons: list[str] = []
    for card in cards:
        crossed = card["boundary_id"] in boundaries
        open_failure = card["failure_id"] in failures
        if crossed and open_failure:
            active.append(
                {
                    "card_id": card["id"],
                    "boundary_id": card["boundary_id"],
                    "failure_id": card["failure_id"],
                }
            )
            reasons.append(_activation_reason(card))
            continue
        # The card identity resolves its boundary and failure in the registry,
        # so a non-activation reason stays a short code rather than a restatement.
        missing = []
        if not crossed:
            missing.append("boundary-not-crossed")
        if not open_failure:
            missing.append("failure-not-open")
        inactive.append({"card_id": card["id"], "reason": "+".join(missing)})

    result = _base_result(
        registry["contract_id"], registry["contract_version"], "none"
    )
    result["inactive_cards"] = inactive
    if not active:
        return _with_context_receipt(result)

    declared = judgment["guidance_body"]
    try:
        body, byte_count, identity = _load_guidance_body(
            declared, judgment["failure_signal_grammar"], Path(protocol_root)
        )
    except JudgmentGuidanceError as exc:
        return _invalid(exc.code, exc.detail, inactive_cards=inactive)
    result.update(
        {
            "outcome": "active",
            "active_cards": active,
            "cards_active": len(active),
            "ordered_activation_reasons": reasons,
            "guidance_bodies_loaded": 1,
            "loaded_guidance_bytes": byte_count,
            "guidance_identity": identity,
            "guidance_budget_bytes": declared["body_budget_bytes"],
            "body": body,
        }
    )
    return _with_context_receipt(result)
