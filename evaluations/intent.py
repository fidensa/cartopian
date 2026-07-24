"""Model-free regression probe for the compact planning-intent contract."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evaluations.runner import (
    AmbiguousJsonError,
    Diagnostic,
    EvaluationCase,
    JsonValue,
    ObservedResult,
    load_json_document,
)

INTENT_FIELDS = (
    "outcome",
    "beneficiary",
    "why_now",
    "success_signal",
    "binding_constraint",
    "exclusions",
)
RESOLUTION_STATES = frozenset({"present", "missing", "conflicting"})
REQUEST_CLASSES = frozenset({"informational", "scoped", "execution"})
INITIATION_MODES = frozenset({"operator", "auto"})
OPERATIONS_BY_CLASS = {
    "informational": frozenset({"status"}),
    "scoped": frozenset({"plan", "generate_tasks"}),
    "execution": frozenset({"execute"}),
}
SOURCE_TYPES = frozenset({"operator", "approved-artifact"})

_FIXTURE_FIELDS = frozenset(
    {
        "intent",
        "working_assumptions",
        "operator_confirmation",
        "request",
        "expected",
    }
)
_SOURCE_FIELDS = frozenset({"value", "meaning", "source"})
_REQUEST_FIELDS = frozenset(
    {
        "class",
        "operation",
        "initiation",
        "active_phase",
        "requested_phases",
        "requested_scope",
    }
)
_EXPECTED_FIELDS = frozenset(
    {
        "record",
        "questions",
        "confirmed",
        "can_lock_requirements",
        "can_lock_plan",
        "request_class",
        "generated_phases",
        "expanded_future_phases",
        "starts_execution",
        "asks_for_confidence",
    }
)
_RECORD_FIELDS = frozenset({"state", "value"})
_QUESTION_FIELDS = frozenset(
    {"field", "state", "working_assumption", "prompt"}
)
_MISSING_QUESTIONS = {
    "outcome": "What observable change should this project produce?",
    "beneficiary": "Who is the primary beneficiary?",
    "why_now": "Why does this need to happen now?",
    "success_signal": "What observable evidence will show the project succeeded?",
    "binding_constraint": "What single non-negotiable boundary must the plan respect?",
    "exclusions": "What is explicitly out of scope?",
}
_CONFLICT_QUESTIONS = {
    "outcome": "Which observable project outcome should govern?",
    "beneficiary": "Which beneficiary should take priority?",
    "why_now": "Which timing rationale should govern?",
    "success_signal": "Which observable success signal should govern?",
    "binding_constraint": "Which non-negotiable constraint should govern?",
    "exclusions": (
        "Which exclusion should govern, and how should it relate to the "
        "requested scope?"
    ),
}


@dataclass(frozen=True)
class IntentSource:
    value: str
    meaning: str
    source: str


@dataclass(frozen=True)
class RequestSpec:
    request_class: str
    operation: str
    initiation: str
    active_phase: str | None
    requested_phases: tuple[str, ...]
    requested_scope: tuple[str, ...]


@dataclass(frozen=True)
class IntentFixture:
    intent: dict[str, tuple[IntentSource, ...]]
    working_assumptions: dict[str, str]
    operator_confirmation: bool
    request: RequestSpec
    expected: dict[str, JsonValue]


def _diagnostic(diagnostic_class: str, message: str) -> tuple[Diagnostic, ...]:
    return (Diagnostic(diagnostic_class, message),)


def _unknown_field(
    value: dict[str, Any],
    allowed: frozenset[str],
    label: str,
) -> tuple[Diagnostic, ...]:
    unknown = sorted(set(value) - allowed)
    if not unknown:
        return ()
    return _diagnostic(
        "fixture_unknown_field",
        f"{label} field {unknown[0]!r} is not allowed.",
    )


def _missing_field(
    value: dict[str, Any],
    required: frozenset[str] | set[str],
    label: str,
) -> tuple[Diagnostic, ...]:
    missing = sorted(set(required) - set(value))
    if not missing:
        return ()
    return _diagnostic(
        "fixture_missing_field",
        f"{label} field {missing[0]!r} is required.",
    )


def _string_list(
    value: Any,
    label: str,
) -> tuple[tuple[str, ...] | None, tuple[Diagnostic, ...]]:
    if (
        not isinstance(value, list)
        or not all(isinstance(item, str) and item.strip() for item in value)
    ):
        return None, _diagnostic(
            "fixture_invalid_type",
            f"{label} must be an array of non-empty strings.",
        )
    if len(set(value)) != len(value):
        return None, _diagnostic(
            "fixture_duplicate_value",
            f"{label} must not contain duplicates.",
        )
    return tuple(value), ()


def _parse_sources(
    raw: Any,
    field: str,
) -> tuple[tuple[IntentSource, ...] | None, tuple[Diagnostic, ...]]:
    if not isinstance(raw, list):
        return None, _diagnostic(
            "fixture_invalid_type",
            f"Intent field {field!r} must be an array.",
        )
    sources: list[IntentSource] = []
    for index, item in enumerate(raw):
        label = f"Intent field {field!r} source {index}"
        if not isinstance(item, dict):
            return None, _diagnostic(
                "fixture_invalid_type",
                f"{label} must be an object.",
            )
        errors = _unknown_field(item, _SOURCE_FIELDS, label)
        if errors:
            return None, errors
        errors = _missing_field(item, _SOURCE_FIELDS, label)
        if errors:
            return None, errors
        if not isinstance(item["value"], str) or not item["value"].strip():
            return None, _diagnostic(
                "fixture_invalid_type",
                f"{label} value must be a non-empty string.",
            )
        if not isinstance(item["meaning"], str) or not item["meaning"].strip():
            return None, _diagnostic(
                "fixture_invalid_type",
                f"{label} meaning must be a non-empty string.",
            )
        if item["source"] not in SOURCE_TYPES:
            return None, _diagnostic(
                "unsupported_intent_source",
                f"{label} source must be 'operator' or 'approved-artifact'.",
            )
        sources.append(
            IntentSource(
                value=item["value"],
                meaning=item["meaning"],
                source=item["source"],
            )
        )
    return tuple(sources), ()


def _parse_request(
    raw: Any,
) -> tuple[RequestSpec | None, tuple[Diagnostic, ...]]:
    if not isinstance(raw, dict):
        return None, _diagnostic(
            "fixture_invalid_type",
            "Request must be an object.",
        )
    errors = _unknown_field(raw, _REQUEST_FIELDS, "Request")
    if errors:
        return None, errors
    errors = _missing_field(raw, _REQUEST_FIELDS, "Request")
    if errors:
        return None, errors

    request_class = raw["class"]
    operation = raw["operation"]
    initiation = raw["initiation"]
    active_phase = raw["active_phase"]
    if request_class not in REQUEST_CLASSES:
        return None, _diagnostic(
            "unsupported_request_class",
            "Request class must be informational, scoped, or execution.",
        )
    if operation not in OPERATIONS_BY_CLASS[request_class]:
        return None, _diagnostic(
            "request_operation_mismatch",
            f"Operation {operation!r} is not valid for request class {request_class!r}.",
        )
    if initiation not in INITIATION_MODES:
        return None, _diagnostic(
            "unsupported_initiation",
            "Request initiation must be 'operator' or 'auto'.",
        )
    if active_phase is not None and (
        not isinstance(active_phase, str) or not active_phase.strip()
    ):
        return None, _diagnostic(
            "fixture_invalid_type",
            "Request active_phase must be null or a non-empty string.",
        )
    requested_phases, errors = _string_list(
        raw["requested_phases"],
        "Request requested_phases",
    )
    if errors:
        return None, errors
    requested_scope, errors = _string_list(
        raw["requested_scope"],
        "Request requested_scope",
    )
    if errors:
        return None, errors
    assert requested_phases is not None
    assert requested_scope is not None
    if operation == "generate_tasks" and active_phase is None:
        return None, _diagnostic(
            "missing_active_phase",
            "Task generation requires one active phase.",
        )
    return (
        RequestSpec(
            request_class=request_class,
            operation=operation,
            initiation=initiation,
            active_phase=active_phase,
            requested_phases=requested_phases,
            requested_scope=requested_scope,
        ),
        (),
    )


def _resolution(
    field: str,
    sources: tuple[IntentSource, ...],
    request: RequestSpec,
) -> tuple[str, str | None]:
    if not sources:
        return "missing", None
    meanings = {source.meaning for source in sources}
    scope_conflict = field == "exclusions" and bool(
        meanings & set(request.requested_scope)
    )
    if len(meanings) > 1 or scope_conflict:
        return "conflicting", None
    preferred = next(
        (source for source in sources if source.source == "operator"),
        sources[0],
    )
    return "present", preferred.value


def _validate_expected(raw: Any) -> tuple[Diagnostic, ...]:
    if not isinstance(raw, dict):
        return _diagnostic(
            "fixture_invalid_type",
            "Expected intent result must be an object.",
        )
    errors = _unknown_field(raw, _EXPECTED_FIELDS, "Expected intent result")
    if errors:
        return errors
    errors = _missing_field(raw, _EXPECTED_FIELDS, "Expected intent result")
    if errors:
        return errors
    record = raw["record"]
    if not isinstance(record, dict):
        return _diagnostic(
            "fixture_invalid_type",
            "Expected record must be an object.",
        )
    errors = _unknown_field(record, frozenset(INTENT_FIELDS), "Expected record")
    if errors:
        return errors
    errors = _missing_field(record, set(INTENT_FIELDS), "Expected record")
    if errors:
        return errors
    for field in INTENT_FIELDS:
        item = record[field]
        if not isinstance(item, dict):
            return _diagnostic(
                "fixture_invalid_type",
                f"Expected record field {field!r} must be an object.",
            )
        errors = _unknown_field(item, _RECORD_FIELDS, f"Expected record {field!r}")
        if errors:
            return errors
        errors = _missing_field(item, _RECORD_FIELDS, f"Expected record {field!r}")
        if errors:
            return errors
        if item["state"] not in RESOLUTION_STATES:
            return _diagnostic(
                "unsupported_resolution_state",
                f"Expected record field {field!r} has an invalid state.",
            )
        if item["value"] is not None and not isinstance(item["value"], str):
            return _diagnostic(
                "fixture_invalid_type",
                f"Expected record field {field!r} value must be a string or null.",
            )
    questions = raw["questions"]
    if not isinstance(questions, list):
        return _diagnostic(
            "fixture_invalid_type",
            "Expected questions must be an array.",
        )
    for index, question in enumerate(questions):
        if not isinstance(question, dict):
            return _diagnostic(
                "fixture_invalid_type",
                f"Expected question {index} must be an object.",
            )
        errors = _unknown_field(
            question,
            _QUESTION_FIELDS,
            f"Expected question {index}",
        )
        if errors:
            return errors
        errors = _missing_field(
            question,
            _QUESTION_FIELDS,
            f"Expected question {index}",
        )
        if errors:
            return errors
    for field in (
        "confirmed",
        "can_lock_requirements",
        "can_lock_plan",
        "starts_execution",
        "asks_for_confidence",
    ):
        if type(raw[field]) is not bool:
            return _diagnostic(
                "fixture_invalid_type",
                f"Expected field {field!r} must be a boolean.",
            )
    if raw["request_class"] not in REQUEST_CLASSES:
        return _diagnostic(
            "unsupported_request_class",
            "Expected request_class is invalid.",
        )
    for field in ("generated_phases", "expanded_future_phases"):
        _, errors = _string_list(raw[field], f"Expected {field}")
        if errors:
            return errors
    return ()


def _parse_fixture(
    raw: JsonValue,
) -> tuple[IntentFixture | None, tuple[Diagnostic, ...]]:
    if not isinstance(raw, dict):
        return None, _diagnostic(
            "malformed_fixture",
            "Intent fixture must be a JSON object.",
        )
    errors = _unknown_field(raw, _FIXTURE_FIELDS, "Intent fixture")
    if errors:
        return None, errors
    errors = _missing_field(raw, _FIXTURE_FIELDS, "Intent fixture")
    if errors:
        return None, errors

    request, errors = _parse_request(raw["request"])
    if errors:
        return None, errors
    assert request is not None

    intent_raw = raw["intent"]
    if not isinstance(intent_raw, dict):
        return None, _diagnostic(
            "fixture_invalid_type",
            "Intent must be an object.",
        )
    errors = _unknown_field(intent_raw, frozenset(INTENT_FIELDS), "Intent")
    if errors:
        return None, errors
    errors = _missing_field(intent_raw, set(INTENT_FIELDS), "Intent")
    if errors:
        return None, errors
    intent: dict[str, tuple[IntentSource, ...]] = {}
    for field in INTENT_FIELDS:
        sources, errors = _parse_sources(intent_raw[field], field)
        if errors:
            return None, errors
        assert sources is not None
        intent[field] = sources

    assumptions_raw = raw["working_assumptions"]
    if not isinstance(assumptions_raw, dict):
        return None, _diagnostic(
            "fixture_invalid_type",
            "Working assumptions must be an object.",
        )
    errors = _unknown_field(
        assumptions_raw,
        frozenset(INTENT_FIELDS),
        "Working assumptions",
    )
    if errors:
        return None, errors
    for field, value in assumptions_raw.items():
        if not isinstance(value, str) or not value.strip():
            return None, _diagnostic(
                "fixture_invalid_type",
                f"Working assumption {field!r} must be a non-empty string.",
            )

    unresolved = {
        field
        for field in INTENT_FIELDS
        if _resolution(field, intent[field], request)[0] != "present"
    }
    missing_assumptions = sorted(unresolved - set(assumptions_raw))
    if missing_assumptions:
        return None, _diagnostic(
            "missing_working_assumption",
            f"Unresolved field {missing_assumptions[0]!r} requires a working assumption.",
        )
    extra_assumptions = sorted(set(assumptions_raw) - unresolved)
    if extra_assumptions:
        return None, _diagnostic(
            "unresolved_assumption_mismatch",
            f"Resolved field {extra_assumptions[0]!r} must not carry a working assumption.",
        )

    if type(raw["operator_confirmation"]) is not bool:
        return None, _diagnostic(
            "fixture_invalid_type",
            "operator_confirmation must be a boolean.",
        )
    errors = _validate_expected(raw["expected"])
    if errors:
        return None, errors
    return (
        IntentFixture(
            intent=intent,
            working_assumptions=dict(assumptions_raw),
            operator_confirmation=raw["operator_confirmation"],
            request=request,
            expected=dict(raw["expected"]),
        ),
        (),
    )


def _evaluate_fixture(fixture: IntentFixture) -> dict[str, JsonValue]:
    record: dict[str, JsonValue] = {}
    questions: list[JsonValue] = []
    unresolved = False
    for field in INTENT_FIELDS:
        state, value = _resolution(
            field,
            fixture.intent[field],
            fixture.request,
        )
        record[field] = {"state": state, "value": value}
        if state == "present":
            continue
        unresolved = True
        questions.append(
            {
                "field": field,
                "state": state,
                "working_assumption": fixture.working_assumptions[field],
                "prompt": (
                    _MISSING_QUESTIONS[field]
                    if state == "missing"
                    else _CONFLICT_QUESTIONS[field]
                ),
            }
        )

    confirmed = fixture.operator_confirmation and not unresolved
    request = fixture.request
    generated_phases: list[JsonValue] = []
    if (
        confirmed
        and request.request_class == "scoped"
        and request.operation == "generate_tasks"
        and request.active_phase is not None
    ):
        generated_phases.append(request.active_phase)

    starts_execution = request.request_class == "execution"
    if request.request_class == "scoped":
        starts_execution = (
            confirmed
            and request.initiation == "auto"
            and request.operation in {"plan", "generate_tasks"}
        )
    if request.request_class == "informational":
        starts_execution = False

    return {
        "record": record,
        "questions": questions,
        "confirmed": confirmed,
        "can_lock_requirements": confirmed,
        "can_lock_plan": confirmed,
        "request_class": request.request_class,
        "generated_phases": generated_phases,
        "expanded_future_phases": [],
        "starts_execution": starts_execution,
        "asks_for_confidence": False,
    }


def _fixture_value(case: EvaluationCase, repository_root: Path) -> JsonValue:
    if case.input.uses_value:
        return case.input.value
    assert case.input.fixture is not None
    path = repository_root.joinpath(*case.input.fixture.split("/"))
    return load_json_document(path.read_text(encoding="utf-8"))


def _fixture_error(exc: Exception) -> tuple[Diagnostic, ...]:
    if isinstance(exc, UnicodeDecodeError):
        return _diagnostic("malformed_fixture", "Fixture is not valid UTF-8.")
    if isinstance(exc, json.JSONDecodeError):
        return _diagnostic(
            "malformed_fixture",
            f"Fixture contains invalid JSON at line {exc.lineno}, column {exc.colno}.",
        )
    if isinstance(exc, AmbiguousJsonError):
        return _diagnostic(
            "malformed_fixture",
            f"Fixture contains ambiguous JSON: {exc}.",
        )
    return _diagnostic("malformed_fixture", "Fixture could not be read.")


class IntentContractEvaluator:
    """Evaluate structured intent scenarios without interpreting natural language."""

    def validate(
        self,
        case: EvaluationCase,
        repository_root: Path,
    ) -> tuple[Diagnostic, ...]:
        if case.measurement_boundary is not None:
            return _diagnostic(
                "unsupported_measurement_boundary",
                "Intent-contract cases do not use a measurement boundary.",
            )
        try:
            raw = _fixture_value(case, repository_root)
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            AmbiguousJsonError,
        ) as exc:
            return _fixture_error(exc)
        _, errors = _parse_fixture(raw)
        return errors

    def evaluate(
        self,
        case: EvaluationCase,
        repository_root: Path,
    ) -> ObservedResult:
        raw = _fixture_value(case, repository_root)
        fixture, errors = _parse_fixture(raw)
        if errors:
            return ObservedResult("fail", errors)
        assert fixture is not None
        observed = _evaluate_fixture(fixture)
        if observed == fixture.expected:
            return ObservedResult("pass")
        return ObservedResult(
            "fail",
            _diagnostic(
                "intent_expectation_mismatch",
                "Observed compact-intent result does not match the fixture expectation.",
            ),
        )
