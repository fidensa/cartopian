"""The one authoritative domain-neutral delivery-gate validator.

The vocabulary, row grammar, closed value sets, and failure recoveries are
owned by ``protocol/delivery-contract.json``.  This module projects that
contract onto the plan surface and returns ordered findings plus the three
independent outcome states — artifact, outcome, follow-up — that every
consumer shares: the ``validate-delivery`` command and its MCP tool,
``close-audit``, ``compose-state``, ``next-action``, and review context.

The module is a pure read.  It performs no delivery, holds no external-action
capability, and writes nothing; a pending or declined authority state is
reported honestly rather than converted into a verified outcome.
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_CONTRACT_PATH = (
    Path(__file__).resolve().parents[1] / "protocol" / "delivery-contract.json"
)

_H2_RE = re.compile(r"^##\s+(.+?)\s*$")
_ANGLE_PLACEHOLDER_RE = re.compile(r"^<.*>$", re.DOTALL)
_DIGIT_RE = re.compile(r"\d")
_WHITESPACE_RE = re.compile(r"\s+")

PLAN_SURFACE = "IMPLEMENTATION_PLAN.md"


class DeliveryContractError(ValueError):
    """Fail-closed diagnostic for an unusable delivery-contract registry."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@lru_cache(maxsize=1)
def load_delivery_contract() -> Dict[str, Any]:
    """Read the single machine authority and fail closed on malformed data."""
    try:
        registry = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
        record = registry["record"]
        rows = record["rows"]
        failures = registry["failures"]
        registry["result"]["result_fields"]
        registry["boundaries"]["review_context_max_bytes"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise DeliveryContractError(
            "delivery-contract-unavailable",
            "the authoritative delivery registry is unreadable",
        ) from exc
    if not isinstance(rows, list) or not rows:
        raise DeliveryContractError("delivery-contract-invalid", "rows are empty")
    if not isinstance(failures, list) or not failures:
        raise DeliveryContractError("delivery-contract-invalid", "failures are empty")
    return registry


def section_heading() -> str:
    return str(load_delivery_contract()["record"]["section_heading"])


def _recovery(code: str) -> str:
    for entry in load_delivery_contract()["failures"]:
        if entry["code"] == code:
            return str(entry["recovery"])
    raise DeliveryContractError("delivery-contract-invalid", f"undeclared code: {code}")


RECORD_FORM = "record-form"
RECORD_SEMANTICS = "record-semantics"


def failure_class(code: str) -> str:
    """The declared class of a finding code, from the single authority."""
    registry = load_delivery_contract()
    declared = registry.get("failure_classes")
    if not isinstance(declared, dict) or not declared:
        raise DeliveryContractError(
            "delivery-contract-invalid", "failure classes are undeclared"
        )
    for entry in registry["failures"]:
        if entry["code"] == code:
            value = entry.get("class")
            if value not in declared:
                raise DeliveryContractError(
                    "delivery-contract-invalid",
                    f"undeclared failure class for {code}: {value!r}",
                )
            return str(value)
    raise DeliveryContractError("delivery-contract-invalid", f"undeclared code: {code}")


# ---------------------------------------------------------------------------
# Record grammar
# ---------------------------------------------------------------------------


def _sections(content: str, heading: str) -> List[str]:
    """Return every H2 section body matching ``heading``, in document order."""
    bodies: List[str] = []
    current: Optional[List[str]] = None
    for line in content.splitlines():
        match = _H2_RE.match(line)
        if match is not None:
            if current is not None:
                bodies.append("\n".join(current).strip())
                current = None
            if match.group(1).strip().casefold() == heading.casefold():
                current = []
            continue
        if current is not None:
            current.append(line)
    if current is not None:
        bodies.append("\n".join(current).strip())
    return bodies


def _list_rows(body: str) -> List[str]:
    """Return Markdown list rows with indented continuation lines folded in.

    A wrapped bullet is one row: agents and operators routinely break a long
    semicolon-delimited row across physical lines.  Unindented prose and blank
    lines end the current row, so surrounding narrative is never consumed.
    """
    rows: List[str] = []
    current: Optional[str] = None
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            if current is not None:
                rows.append(current)
            current = stripped[2:].strip()
        elif current is not None and stripped and line[:1].isspace():
            current = f"{current} {stripped}"
        else:
            if current is not None:
                rows.append(current)
                current = None
    if current is not None:
        rows.append(current)
    return rows


def _split_parts(row: str) -> List[Tuple[str, str]]:
    """Split one row into ordered (label, value) parts.

    A part with no colon carries no label; it is returned with an empty label
    so the caller reports it as undeclared rather than dropping it silently.
    """
    parts: List[Tuple[str, str]] = []
    for chunk in row.split(";"):
        label, sep, value = chunk.partition(":")
        if not sep:
            parts.append(("", chunk.strip()))
            continue
        parts.append((label.strip(), value.strip()))
    return parts


def _normalize(value: str) -> str:
    """Casefold, collapse whitespace, and drop trailing sentence punctuation."""
    return _WHITESPACE_RE.sub(" ", value).strip().strip(".").strip().casefold()


def _is_placeholder(value: Optional[str]) -> bool:
    if value is None:
        return True
    stripped = value.strip()
    if not stripped:
        return True
    if _ANGLE_PLACEHOLDER_RE.match(stripped):
        return True
    terms = load_delivery_contract()["placeholders"]["terms"]
    return stripped.casefold() in {str(term).casefold() for term in terms}


def _declared_value(value: str, vocabulary: Sequence[Any]) -> Optional[str]:
    """Return the declared spelling of ``value``, or None when undeclared."""
    normalized = value.strip().casefold()
    for item in vocabulary:
        if str(item).casefold() == normalized:
            return str(item)
    return None


def _time_is_bounded(value: str, kind: str) -> bool:
    """True when a time value is bounded under the declared rule.

    A digit is what actually bounds a time — a date, a timestamp, or a counted
    interval — so it decides first. ``no later than 2026-09-13`` is bounded
    even though it contains the word ``later``; matching that word as a
    substring would reject an ordinary due date. Only a value carrying no
    digit is read for an unbounded term, which is what catches a cadence
    qualified into openness (``ongoing monthly``).

    ``observed`` never accepts a cadence: an observation happened at a moment.
    """
    spec = load_delivery_contract()["bounded_time"]
    normalized = _normalize(value)
    if _DIGIT_RE.search(normalized):
        return True
    words = normalized.split()
    for term in spec["unbounded_terms"]:
        parts = str(term).casefold().split()
        if any(
            words[index : index + len(parts)] == parts
            for index in range(len(words) - len(parts) + 1)
        ):
            return False
    if kind != "due":
        return False
    cadences = {str(item).casefold() for item in spec["cadences"]}
    return any(word in cadences for word in words)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class _Findings:
    """Accumulator that stamps each finding with its declared recovery."""

    def __init__(self) -> None:
        self.items: List[Dict[str, Optional[str]]] = []

    def add(
        self,
        code: str,
        detail: str,
        *,
        semantic: str = "contract",
        field: Optional[str] = None,
    ) -> None:
        self.items.append(
            {
                "code": code,
                "semantic": semantic,
                "field": field,
                "detail": detail,
                "recovery": _recovery(code),
            }
        )


def _parse_rows(
    body: str, findings: _Findings
) -> Tuple[Dict[str, Dict[str, str]], Dict[str, str]]:
    """Parse the section body into ``{row_id: {field_id: value}}``.

    Returns the parsed rows and the raw row values keyed by row id. Unknown
    rows, undeclared parts, and duplicate rows are reported rather than
    dropped: a stray semicolon inside a value must fail closed, not silently
    truncate the value it belongs to.
    """
    registry = load_delivery_contract()["record"]
    declared = {
        str(row["label"]).casefold(): row for row in registry["rows"]
    }
    applicability_label = str(registry["applicability_label"])
    declared_applicability = applicability_label.casefold()

    parsed: Dict[str, Dict[str, str]] = {}
    values: Dict[str, str] = {}
    seen: Dict[str, int] = {}

    for row in _list_rows(body):
        parts = _split_parts(row)
        if not parts:
            continue
        head_label, head_value = parts[0]
        key = head_label.casefold()
        if key == declared_applicability:
            row_id = "applicability"
            fields = [
                {"id": "justification", "label": "Justification", "required": False}
            ]
        elif key in declared:
            row_id = str(declared[key]["id"])
            fields = list(declared[key].get("fields", []))
        else:
            findings.add(
                "delivery-row-unknown",
                f"`{head_label or row}` is not a declared delivery semantic",
            )
            continue

        seen[row_id] = seen.get(row_id, 0) + 1
        if seen[row_id] > 1:
            findings.add(
                "delivery-row-duplicated",
                f"`{head_label}` appears {seen[row_id]} times",
                semantic=row_id,
            )
            continue

        labels = {str(item["label"]).casefold(): str(item["id"]) for item in fields}
        collected: Dict[str, str] = {}
        for label, value in parts[1:]:
            field_id = labels.get(label.casefold())
            if field_id is None:
                findings.add(
                    "delivery-field-unknown",
                    f"`{head_label}` carries an undeclared part `{label or value}`",
                    semantic=row_id,
                )
                continue
            if field_id in collected:
                findings.add(
                    "delivery-field-unknown",
                    f"`{head_label}` repeats the part `{label}`",
                    semantic=row_id,
                    field=field_id,
                )
                continue
            collected[field_id] = value
        parsed[row_id] = collected
        values[row_id] = head_value

    return parsed, values


def _check_field(
    row_id: str,
    row_label: str,
    spec: Dict[str, Any],
    collected: Dict[str, str],
    findings: _Findings,
) -> Optional[str]:
    """Validate one declared part of a row; return its value when usable."""
    field_id = str(spec["id"])
    label = str(spec["label"])
    value = collected.get(field_id)
    missing_code = str(spec.get("missing_code") or "")
    if value is None:
        if spec.get("required"):
            findings.add(
                missing_code or "delivery-field-missing",
                f"`{row_label}` does not name `{label}`",
                semantic=row_id,
                field=field_id,
            )
        return None
    if _is_placeholder(value):
        findings.add(
            missing_code or "delivery-field-placeholder",
            f"`{row_label}` records `{label}` as a placeholder",
            semantic=row_id,
            field=field_id,
        )
        return None
    vocabulary = spec.get("vocabulary")
    if vocabulary is not None:
        # Match case-insensitively but return the *declared* spelling. Every
        # downstream rule compares against a declared value, so returning the
        # author's casing would let `Availability: Unavailable` satisfy the
        # vocabulary and then silently miss the unavailable-owner rule.
        declared = _declared_value(value, vocabulary)
        if declared is None:
            findings.add(
                "delivery-value-not-declared",
                f"`{row_label}` records `{label}` as `{value}`; declared values are "
                + ", ".join(str(item) for item in vocabulary),
                semantic=row_id,
                field=field_id,
            )
            return None
        return declared
    time_kind = spec.get("time_kind")
    if time_kind is not None and not _time_is_bounded(value, str(time_kind)):
        findings.add(
            str(spec.get("unbounded_code") or "delivery-field-placeholder"),
            f"`{row_label}` records `{label}` as `{value}`, which names no bounded time",
            semantic=row_id,
            field=field_id,
        )
        return None
    return value.strip()


def _validate_not_applicable(
    parsed: Dict[str, Dict[str, str]], findings: _Findings
) -> None:
    justification = parsed.get("applicability", {}).get("justification")
    if _is_placeholder(justification):
        findings.add(
            "delivery-not-applicable-unjustified",
            "`Delivery: not-applicable` records no justification",
            semantic="applicability",
            field="justification",
        )
    obligations = sorted(key for key in parsed if key != "applicability")
    if obligations:
        findings.add(
            "delivery-not-applicable-contradicted",
            "`Delivery: not-applicable` still carries obligation rows: "
            + ", ".join(obligations),
            semantic="applicability",
        )


def _validate_required(
    parsed: Dict[str, Dict[str, str]],
    values: Dict[str, str],
    findings: _Findings,
) -> Dict[str, Any]:
    """Validate the declared rows in order and return the derived facts."""
    registry = load_delivery_contract()["record"]
    facts: Dict[str, Any] = {
        "owner": None,
        "target": None,
        "verification_result": None,
        "authority": None,
        "artifact": None,
        "follow_up": "unknown",
    }
    verification_target_ok = True

    for row in registry["rows"]:
        row_id = str(row["id"])
        row_label = str(row["label"])
        if row_id not in parsed:
            findings.add(
                "delivery-row-missing",
                f"the record does not name `{row_label}`",
                semantic=row_id,
            )
            continue
        collected = parsed[row_id]
        raw_value = values.get(row_id, "")

        not_applicable_value = row.get("not_applicable_value")
        is_not_applicable = (
            not_applicable_value is not None
            and _normalize(raw_value) == _normalize(str(not_applicable_value))
        )

        value: Optional[str] = None
        if is_not_applicable:
            value = str(not_applicable_value)
        elif _is_placeholder(raw_value):
            findings.add(
                "delivery-field-placeholder",
                f"`{row_label}` records {row['value_meaning']} as a placeholder",
                semantic=row_id,
            )
        else:
            vocabulary = row.get("value_vocabulary")
            if vocabulary is None:
                value = raw_value.strip()
            else:
                value = _declared_value(raw_value, vocabulary)
                if value is None:
                    findings.add(
                        "delivery-value-not-declared",
                        f"`{row_label}` records `{raw_value}`; declared values are "
                        + ", ".join(str(item) for item in vocabulary),
                        semantic=row_id,
                    )

        resolved: Dict[str, Optional[str]] = {}
        for spec in row.get("fields", []):
            field_id = str(spec["id"])
            if is_not_applicable:
                # A not-applicable row carries a justification, which the row's
                # own rule below validates — checking it twice would report one
                # empty justification as two separate defects. Any other part
                # contradicts the declaration.
                if field_id != "justification" and field_id in collected:
                    findings.add(
                        "delivery-field-unknown",
                        f"`{row_label}: {not_applicable_value}` also records `{spec['label']}`",
                        semantic=row_id,
                        field=field_id,
                    )
                continue
            resolved[field_id] = _check_field(
                row_id, row_label, spec, collected, findings
            )

        if row_id == "owner":
            facts["owner"] = value
            if resolved.get("availability") == "unavailable":
                findings.add(
                    "delivery-owner-unavailable",
                    f"the delivery owner `{value}` is recorded as unavailable",
                    semantic=row_id,
                    field="availability",
                )
        elif row_id == "target":
            facts["target"] = value

        # Cross-row parts: a part may be required to name the same fact as an
        # earlier row (target identity) or to name a different one (an observer
        # who is not the deliverer). Both compare against facts already read,
        # so the declaration order of the rows is what makes them decidable.
        for spec in row.get("fields", []):
            field_id = str(spec["id"])
            observed = resolved.get(field_id)
            if observed is None:
                continue
            match_row = spec.get("must_match_row")
            if match_row is not None:
                expected = facts.get(str(match_row))
                if expected is not None and _normalize(observed) != _normalize(expected):
                    findings.add(
                        str(spec.get("mismatch_code") or "delivery-evidence-target-mismatch"),
                        f"`{row_label}` names target `{observed}`; the record's target is `{expected}`",
                        semantic=row_id,
                        field=field_id,
                    )
                    if row_id == "immediate-verification":
                        verification_target_ok = False
            not_row = spec.get("not_row")
            if not_row is not None:
                excluded = facts.get(str(not_row))
                if excluded is not None and _normalize(observed) == _normalize(excluded):
                    findings.add(
                        str(spec.get("not_row_code") or "delivery-field-unknown"),
                        f"`{row_label}` names `{observed}`, who is the delivery owner",
                        semantic=row_id,
                        field=field_id,
                    )

        if row_id == "contingency":
            kind = resolved.get("kind")
            rollback = resolved.get("rollback")
            because = collected.get("because")
            if kind is not None and rollback is not None:
                if (kind == "rollback") != (rollback == "available"):
                    findings.add(
                        "delivery-contingency-contradictory",
                        f"`Kind: {kind}` disagrees with `Rollback: {rollback}`",
                        semantic=row_id,
                    )
                elif rollback == "inapplicable" and _is_placeholder(because):
                    findings.add(
                        "delivery-rollback-substitution-unjustified",
                        f"`Kind: {kind}` substitutes for rollback without a `Because` part",
                        semantic=row_id,
                        field="because",
                    )
                elif rollback == "available" and because is not None:
                    findings.add(
                        "delivery-contingency-contradictory",
                        "`Rollback: available` also records why rollback is inapplicable",
                        semantic=row_id,
                        field="because",
                    )
        elif row_id == "immediate-verification":
            result = resolved.get("result")
            facts["verification_result"] = result
            if result == "not-run":
                findings.add(
                    "delivery-verification-not-run",
                    "no immediate verification of the target was performed",
                    semantic=row_id,
                    field="result",
                )
            elif result == "fail":
                findings.add(
                    "delivery-verification-failed",
                    "the immediate verification of the target did not pass",
                    semantic=row_id,
                    field="result",
                )
        elif row_id == "follow-up":
            if is_not_applicable:
                if _is_placeholder(collected.get("justification")):
                    findings.add(
                        "delivery-not-applicable-unjustified",
                        "`Follow-up: not-applicable` records no justification",
                        semantic=row_id,
                        field="justification",
                    )
                    facts["follow_up"] = "required"
                else:
                    facts["follow_up"] = "not-applicable"
            elif (
                value is None
                or resolved.get("owner") is None
                or resolved.get("due") is None
                or resolved.get("status") is None
            ):
                facts["follow_up"] = "required"
            else:
                facts["follow_up"] = (
                    "satisfied" if resolved["status"] == "closed" else "scheduled"
                )
        elif row_id == "authority":
            facts["authority"] = value
            if value == "pending-operator-authorization":
                findings.add(
                    "delivery-authority-pending",
                    "the external action is not authorized yet",
                    semantic=row_id,
                )
            elif value == "declined":
                findings.add(
                    "delivery-authority-declined",
                    "the operator declined the external action",
                    semantic=row_id,
                )
            if value in ("pending-operator-authorization", "declined") and facts[
                "verification_result"
            ] == "pass":
                findings.add(
                    "delivery-authority-contradicted",
                    f"the record claims a passing target observation while authority is `{value}`",
                    semantic=row_id,
                )
        elif row_id == "artifact":
            facts["artifact"] = value
            if value == "incomplete":
                findings.add(
                    "delivery-artifact-incomplete",
                    "the work product itself is not finished",
                    semantic=row_id,
                )
                if facts["verification_result"] == "pass":
                    findings.add(
                        "delivery-artifact-contradicted",
                        "the record claims a passing target observation while the artifact is incomplete",
                        semantic=row_id,
                    )

    facts["verification_target_ok"] = verification_target_ok
    return facts


def _states(applicability: str, facts: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if applicability == "not-applicable":
        return {
            "artifact_state": "not-applicable",
            "outcome_state": "not-applicable",
            "follow_up_state": "not-applicable",
        }
    if applicability != "required" or facts is None:
        return {
            "artifact_state": "unknown",
            "outcome_state": "unknown",
            "follow_up_state": "unknown",
        }

    artifact = facts["artifact"] if facts["artifact"] in ("complete", "incomplete") else "unknown"

    authority = facts["authority"]
    if authority == "declined":
        outcome = "not-delivered"
    elif authority == "pending-operator-authorization":
        outcome = "pending-authority"
    elif authority != "operator-authorized":
        outcome = "unknown"
    elif (
        facts["verification_result"] == "pass"
        and facts["verification_target_ok"]
        and artifact == "complete"
    ):
        outcome = "verified"
    else:
        outcome = "unverified"

    return {
        "artifact_state": artifact,
        "outcome_state": outcome,
        "follow_up_state": facts["follow_up"],
    }


def _result(
    applicability: str,
    findings: _Findings,
    facts: Optional[Dict[str, Any]],
    contract_identity: Optional[str],
    *,
    states_known: bool = True,
) -> Dict[str, Any]:
    registry = load_delivery_contract()
    return {
        "contract_id": registry["contract_id"],
        "contract_version": registry["contract_version"],
        "applicability": applicability,
        **_states(applicability if states_known else "undeclared", facts),
        "contract_identity": contract_identity,
        "gate": "pass" if not findings.items else "blocked",
        "ordered_findings": findings.items,
        # Constant, not derived: this validator has no external-action path, so
        # no input can make it report that it delivered something.
        "delivery_performed": False,
    }


def validate_record(content: str) -> Dict[str, Any]:
    """Validate the delivery contract carried by ``content``.

    ``content`` is the full plan text.  The function is pure: the same text
    always yields the same findings, states, and contract identity.
    """
    findings = _Findings()
    bodies = _sections(content, section_heading())
    if not bodies:
        findings.add(
            "delivery-contract-undeclared",
            f"`{PLAN_SURFACE}` carries no `## {section_heading()}` section",
        )
        return _result("undeclared", findings, None, None)
    if len(bodies) > 1:
        findings.add(
            "delivery-contract-duplicated",
            f"`{PLAN_SURFACE}` carries {len(bodies)} `## {section_heading()}` sections",
        )
        return _result("undeclared", findings, None, None)

    body = bodies[0]
    contract_identity = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
    parsed, values = _parse_rows(body, findings)

    declared = [
        str(item["id"])
        for item in load_delivery_contract()["record"]["applicability"]
    ]
    raw_applicability = values.get("applicability")
    if raw_applicability is None or _is_placeholder(raw_applicability):
        findings.add(
            "delivery-applicability-missing",
            f"the section does not declare `{load_delivery_contract()['record']['applicability_label']}`",
            semantic="applicability",
        )
        return _result("undeclared", findings, None, contract_identity)
    applicability = _normalize(raw_applicability)
    if applicability not in declared:
        findings.add(
            "delivery-value-not-declared",
            f"`Delivery: {raw_applicability}` is not declared; declared values are "
            + ", ".join(declared),
            semantic="applicability",
        )
        return _result("undeclared", findings, None, contract_identity)

    if applicability == "not-applicable":
        _validate_not_applicable(parsed, findings)
        # The declaration is reported as made; only a not-applicable claim that
        # survives validation may collapse the three obligation states. An
        # unjustified or contradicted claim leaves them unknown rather than
        # discharging obligations it did not establish.
        return _result(
            "not-applicable",
            findings,
            None,
            contract_identity,
            states_known=not findings.items,
        )

    facts = _validate_required(parsed, values, findings)
    return _result("required", findings, facts, contract_identity)


def validate_plan(project_root: Path) -> Dict[str, Any]:
    """Validate the delivery contract carried by a project's plan surface.

    Reads exactly one contained path — ``<project_root>/IMPLEMENTATION_PLAN.md``
    — under the declared byte ceiling.  A missing plan is an undeclared
    contract, not a crash: a project with no plan has nothing to deliver yet.
    """
    root = Path(project_root)
    if not root.is_absolute():
        raise DeliveryContractError(
            "delivery-path-invalid", "project root must be an absolute path"
        )
    try:
        resolved_root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise DeliveryContractError(
            "delivery-path-invalid", "project root does not resolve"
        ) from exc

    plan_path = resolved_root / PLAN_SURFACE
    findings = _Findings()
    try:
        resolved_plan = plan_path.resolve(strict=True)
    except (OSError, RuntimeError):
        findings.add(
            "delivery-contract-undeclared",
            f"`{PLAN_SURFACE}` is not present in the project root",
        )
        return _result("undeclared", findings, None, None)
    try:
        resolved_plan.relative_to(resolved_root)
    except ValueError:
        raise DeliveryContractError(
            "delivery-path-outside-root",
            f"`{PLAN_SURFACE}` resolves outside the project root",
        )
    if not resolved_plan.is_file():
        findings.add(
            "delivery-contract-undeclared",
            f"`{PLAN_SURFACE}` is not a regular file",
        )
        return _result("undeclared", findings, None, None)

    ceiling = int(load_delivery_contract()["boundaries"]["max_surface_bytes"])
    try:
        if resolved_plan.stat().st_size > ceiling:
            findings.add(
                "delivery-record-unreadable",
                f"`{PLAN_SURFACE}` exceeds {ceiling} bytes",
            )
            return _result("undeclared", findings, None, None)
        content = resolved_plan.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        findings.add(
            "delivery-record-unreadable",
            f"`{PLAN_SURFACE}` cannot be read as UTF-8 text",
        )
        return _result("undeclared", findings, None, None)
    return validate_record(content)


# ---------------------------------------------------------------------------
# Shared consumer projections
# ---------------------------------------------------------------------------


def blocking_reasons(result: Dict[str, Any]) -> List[str]:
    """Return one closeout-blocking line per finding, in finding order."""
    return [
        f"delivery gate blocks closeout: {item['code']} — {item['detail']}"
        for item in result["ordered_findings"]
    ]


def record_form_findings(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Findings that say the plan has not been authored into the record form.

    These are the migration-relevant ones. A record-semantics finding — an
    unverified target, a declined external action, an unfinished artifact —
    means a migrated plan is telling the truth about where it is, and must not
    be mistaken for an unmigrated plan.
    """
    return [
        item
        for item in result["ordered_findings"]
        if failure_class(item["code"]) == RECORD_FORM
    ]


def record_is_declared(result: Dict[str, Any]) -> bool:
    """True when the plan carries a complete, coherent delivery record.

    Says nothing about whether the gate passes: a declared record may still be
    blocked on an obligation it has not met.
    """
    return (
        result["applicability"] != "undeclared"
        and not record_form_findings(result)
    )


def compact_status(result: Dict[str, Any]) -> Dict[str, Any]:
    """Bounded delivery status for state and startup surfaces.

    Status stays compact; the detail is on demand through
    ``cartopian validate-delivery``.  Only the first finding's code is carried
    so an unmet gate is visible without pulling the whole record into every
    session.
    """
    findings = result["ordered_findings"]
    return {
        "applicability": result["applicability"],
        "artifact_state": result["artifact_state"],
        "outcome_state": result["outcome_state"],
        "follow_up_state": result["follow_up_state"],
        "gate": result["gate"],
        "finding_count": len(findings),
        "first_finding": findings[0]["code"] if findings else None,
    }


def review_projection(result: Dict[str, Any], content: str) -> Dict[str, Any]:
    """Project the delivery contract and its evidence for review context.

    The reviewer receives the result and the delivery-contract section itself
    — never the rest of the plan — and only within the declared byte bound.
    """
    max_bytes = int(
        load_delivery_contract()["boundaries"]["review_context_max_bytes"]
    )
    bodies = _sections(content, section_heading())
    body = bodies[0] if len(bodies) == 1 else None
    encoded = body.encode("utf-8") if body is not None else b""
    within_bound = body is not None and len(encoded) <= max_bytes
    return {
        **result,
        "max_context_bytes": max_bytes,
        "context_bytes": len(encoded) if within_bound else 0,
        "section": body if within_bound else None,
        "section_omitted": None
        if within_bound
        else ("no-single-section" if body is None else "exceeds-max-context-bytes"),
    }


def review_projection_for_plan(project_root: Path) -> Dict[str, Any]:
    """Read-and-project helper for review context; containment as in ``validate_plan``."""
    result = validate_plan(project_root)
    plan_path = Path(project_root).resolve() / PLAN_SURFACE
    try:
        content = plan_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        content = ""
    return review_projection(result, content)


def declared_semantics() -> Sequence[str]:
    """Return the declared delivery semantics in contract order."""
    return tuple(
        str(row["id"]) for row in load_delivery_contract()["record"]["rows"]
    )
