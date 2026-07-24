"""Discovery, validation, execution, selection, and rendering primitives."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, TypeAlias

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES_DIRECTORY = PurePosixPath("evaluations/cases")
DEFAULT_FIXTURE_DIRECTORIES = (PurePosixPath("evaluations/fixtures"),)

_CASE_FIELDS = frozenset(
    {
        "identifier",
        "category",
        "input",
        "expected_outcome",
        "measurement_boundary",
        "rationale",
    }
)
_REQUIRED_CASE_FIELDS = frozenset(
    {"identifier", "category", "input", "expected_outcome", "rationale"}
)
_INPUT_FIELDS = frozenset({"fixture", "value"})
_EXPECTED_FIELDS = frozenset({"outcome", "diagnostic_class"})
_BOUNDARY_FIELDS = frozenset({"max_input_bytes", "max_output_bytes"})
_LABEL = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_CATEGORY = re.compile(r"^[a-z][a-z0-9-]*$")

JsonValue: TypeAlias = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)


@dataclass(frozen=True)
class Diagnostic:
    diagnostic_class: str
    message: str


@dataclass(frozen=True)
class InputSpec:
    fixture: str | None = None
    value: JsonValue = None
    uses_value: bool = False


@dataclass(frozen=True)
class ExpectedOutcome:
    outcome: str
    diagnostic_class: str | None = None


@dataclass(frozen=True)
class MeasurementBoundary:
    max_input_bytes: int | None = None
    max_output_bytes: int | None = None


@dataclass(frozen=True)
class EvaluationCase:
    identifier: str
    category: str
    input: InputSpec
    expected_outcome: ExpectedOutcome
    measurement_boundary: MeasurementBoundary | None
    rationale: str
    source_path: str


@dataclass(frozen=True)
class ObservedResult:
    outcome: str
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class CaseResult:
    identifier: str
    category: str
    observed_outcome: str
    expected_outcome: str
    matched: bool
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True)
class AggregateResult:
    cases: tuple[CaseResult, ...]
    matched: int
    mismatched: int
    observed_pass: int
    observed_fail: int


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    location: str
    message: str


class ValidationError(Exception):
    def __init__(self, issues: Sequence[ValidationIssue]):
        ordered = tuple(
            sorted(issues, key=lambda issue: (issue.location, issue.code, issue.message))
        )
        super().__init__(f"{len(ordered)} evaluation validation error(s)")
        self.issues = ordered

    def render(self) -> str:
        lines = [
            f"validation error [{issue.code}] {issue.location}: {issue.message}"
            for issue in self.issues
        ]
        return "\n".join(lines) + "\n"


class SelectionError(Exception):
    def render(self) -> str:
        return f"selection error [no_cases_selected]: {self}\n"


class Evaluator(Protocol):
    def validate(
        self, case: EvaluationCase, repository_root: Path
    ) -> Sequence[Diagnostic]:
        """Return deterministic, case-specific validation diagnostics."""

    def evaluate(
        self, case: EvaluationCase, repository_root: Path
    ) -> ObservedResult:
        """Evaluate one already validated case."""


EvaluationRegistry: TypeAlias = Mapping[str, Evaluator]


class AmbiguousJsonError(ValueError):
    """Raised when JSON syntax is accepted loosely by the stdlib decoder."""


def _closed_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise AmbiguousJsonError(f"duplicate object field {key!r}")
        result[key] = value
    return result


def _reject_nonfinite_number(value: str) -> JsonValue:
    raise AmbiguousJsonError(f"non-finite number {value!r}")


def load_json_document(text: str) -> JsonValue:
    """Parse strict JSON, rejecting duplicate keys and non-finite numbers."""

    return json.loads(
        text,
        object_pairs_hook=_closed_object,
        parse_constant=_reject_nonfinite_number,
    )


def _relative_location(repository_root: Path, path: Path) -> str:
    try:
        return path.relative_to(repository_root).as_posix()
    except ValueError:
        return path.name


def _issue(
    issues: list[ValidationIssue],
    code: str,
    location: str,
    message: str,
) -> None:
    issues.append(ValidationIssue(code=code, location=location, message=message))


def _unknown_fields(
    data: Mapping[str, Any],
    allowed: frozenset[str],
    location: str,
    issues: list[ValidationIssue],
) -> None:
    for field in sorted(set(data) - allowed):
        _issue(
            issues,
            "unknown_field",
            f"{location}.{field}",
            f"field {field!r} is not allowed",
        )


def _parse_input(
    raw: Any,
    location: str,
    issues: list[ValidationIssue],
) -> InputSpec | None:
    if not isinstance(raw, dict):
        _issue(issues, "invalid_type", location, "input must be an object")
        return None
    _unknown_fields(raw, _INPUT_FIELDS, location, issues)
    present = sorted(set(raw) & _INPUT_FIELDS)
    if len(present) != 1:
        _issue(
            issues,
            "ambiguous_input",
            location,
            "input must contain exactly one of 'fixture' or 'value'",
        )
        return None
    if present[0] == "fixture":
        fixture = raw["fixture"]
        if not isinstance(fixture, str) or not fixture:
            _issue(
                issues,
                "invalid_type",
                f"{location}.fixture",
                "fixture must be a non-empty repository-relative string",
            )
            return None
        return InputSpec(fixture=fixture)
    return InputSpec(value=raw["value"], uses_value=True)


def _parse_expected(
    raw: Any,
    location: str,
    issues: list[ValidationIssue],
) -> ExpectedOutcome | None:
    if not isinstance(raw, dict):
        _issue(
            issues,
            "invalid_type",
            location,
            "expected_outcome must be an object",
        )
        return None
    _unknown_fields(raw, _EXPECTED_FIELDS, location, issues)
    outcome = raw.get("outcome")
    if outcome not in {"pass", "fail"}:
        _issue(
            issues,
            "invalid_expected_outcome",
            f"{location}.outcome",
            "outcome must be 'pass' or 'fail'",
        )
        return None
    diagnostic_class = raw.get("diagnostic_class")
    if diagnostic_class is not None:
        if not isinstance(diagnostic_class, str) or not _LABEL.fullmatch(
            diagnostic_class
        ):
            _issue(
                issues,
                "invalid_diagnostic_class",
                f"{location}.diagnostic_class",
                "diagnostic_class must use lowercase letters, digits, '.', '_', or '-'",
            )
            return None
        if outcome != "fail":
            _issue(
                issues,
                "invalid_diagnostic_class",
                f"{location}.diagnostic_class",
                "diagnostic_class is only valid for an expected failure",
            )
            return None
    return ExpectedOutcome(outcome=outcome, diagnostic_class=diagnostic_class)


def _parse_boundary(
    raw: Any,
    location: str,
    issues: list[ValidationIssue],
) -> MeasurementBoundary | None:
    if not isinstance(raw, dict):
        _issue(
            issues,
            "invalid_type",
            location,
            "measurement_boundary must be an object",
        )
        return None
    _unknown_fields(raw, _BOUNDARY_FIELDS, location, issues)
    if not set(raw) & _BOUNDARY_FIELDS:
        _issue(
            issues,
            "empty_measurement_boundary",
            location,
            "measurement_boundary must declare at least one threshold",
        )
        return None
    values: dict[str, int | None] = {
        "max_input_bytes": None,
        "max_output_bytes": None,
    }
    valid = True
    for field in sorted(set(raw) & _BOUNDARY_FIELDS):
        value = raw[field]
        if type(value) is not int or value < 0:
            _issue(
                issues,
                "invalid_threshold",
                f"{location}.{field}",
                f"{field} must be a non-negative integer",
            )
            valid = False
        else:
            values[field] = value
    if not valid:
        return None
    return MeasurementBoundary(**values)


def _parse_case(
    raw: Any,
    source_path: str,
    issues: list[ValidationIssue],
) -> EvaluationCase | None:
    if not isinstance(raw, dict):
        _issue(issues, "invalid_case", source_path, "case document must be an object")
        return None

    before = len(issues)
    _unknown_fields(raw, _CASE_FIELDS, source_path, issues)
    for field in sorted(_REQUIRED_CASE_FIELDS - set(raw)):
        _issue(
            issues,
            "missing_field",
            f"{source_path}.{field}",
            f"required field {field!r} is missing",
        )

    identifier = raw.get("identifier")
    if not isinstance(identifier, str) or not _LABEL.fullmatch(identifier):
        _issue(
            issues,
            "invalid_identifier",
            f"{source_path}.identifier",
            "identifier must use lowercase letters, digits, '.', '_', or '-'",
        )

    category = raw.get("category")
    if not isinstance(category, str) or not _CATEGORY.fullmatch(category):
        _issue(
            issues,
            "invalid_category",
            f"{source_path}.category",
            "category must use lowercase letters, digits, or '-'",
        )

    rationale = raw.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        _issue(
            issues,
            "invalid_rationale",
            f"{source_path}.rationale",
            "rationale must be a non-empty string",
        )

    input_spec = _parse_input(raw.get("input"), f"{source_path}.input", issues)
    expected = _parse_expected(
        raw.get("expected_outcome"),
        f"{source_path}.expected_outcome",
        issues,
    )
    boundary = None
    if "measurement_boundary" in raw:
        boundary = _parse_boundary(
            raw["measurement_boundary"],
            f"{source_path}.measurement_boundary",
            issues,
        )
    if len(issues) != before:
        return None
    assert isinstance(identifier, str)
    assert isinstance(category, str)
    assert isinstance(rationale, str)
    assert input_spec is not None
    assert expected is not None
    return EvaluationCase(
        identifier=identifier,
        category=category,
        input=input_spec,
        expected_outcome=expected,
        measurement_boundary=boundary,
        rationale=rationale,
        source_path=source_path,
    )


def _validate_fixture_reference(
    case: EvaluationCase,
    repository_root: Path,
    fixture_directories: Sequence[PurePosixPath],
) -> ValidationIssue | None:
    reference = case.input.fixture
    if reference is None:
        return None
    location = f"{case.source_path}.input.fixture"
    if "\\" in reference:
        return ValidationIssue(
            "invalid_fixture_path",
            location,
            "fixture path must use repository-relative POSIX separators",
        )
    fixture_path = PurePosixPath(reference)
    if fixture_path.is_absolute() or ".." in fixture_path.parts:
        return ValidationIssue(
            "fixture_escape",
            location,
            f"fixture {reference!r} is outside allowlisted fixture directories",
        )
    if not any(
        fixture_path == allowed or allowed in fixture_path.parents
        for allowed in fixture_directories
    ):
        return ValidationIssue(
            "fixture_not_allowlisted",
            location,
            f"fixture {reference!r} is outside allowlisted fixture directories",
        )

    root = repository_root.resolve()
    target = (root / Path(*fixture_path.parts)).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return ValidationIssue(
            "fixture_escape",
            location,
            f"fixture {reference!r} resolves outside the repository",
        )
    if not target.exists() or not target.is_file():
        return ValidationIssue(
            "missing_fixture",
            location,
            f"fixture {reference!r} does not name an existing file",
        )
    return None


def discover_cases(
    repository_root: Path,
    cases_directory: PurePosixPath | str,
    registry: EvaluationRegistry,
    *,
    fixture_directories: Sequence[PurePosixPath] = DEFAULT_FIXTURE_DIRECTORIES,
) -> tuple[EvaluationCase, ...]:
    """Discover and fully validate cases before any evaluator can execute."""

    root = repository_root.resolve()
    relative_cases = PurePosixPath(cases_directory)
    if relative_cases.is_absolute() or ".." in relative_cases.parts:
        raise ValidationError(
            [
                ValidationIssue(
                    "invalid_case_directory",
                    relative_cases.as_posix(),
                    "case directory must be repository-relative",
                )
            ]
        )
    cases_path = root / Path(*relative_cases.parts)
    issues: list[ValidationIssue] = []
    if not cases_path.is_dir():
        raise ValidationError(
            [
                ValidationIssue(
                    "missing_case_directory",
                    relative_cases.as_posix(),
                    "documented case directory does not exist",
                )
            ]
        )
    resolved_cases_path = cases_path.resolve()
    try:
        resolved_cases_path.relative_to(root)
    except ValueError:
        raise ValidationError(
            [
                ValidationIssue(
                    "case_directory_escape",
                    relative_cases.as_posix(),
                    "case directory resolves outside the repository",
                )
            ]
        )

    paths = sorted(cases_path.glob("*.json"), key=lambda path: path.name)
    if not paths:
        raise ValidationError(
            [
                ValidationIssue(
                    "empty_case_set",
                    relative_cases.as_posix(),
                    "case directory contains no JSON case documents",
                )
            ]
        )

    parsed: list[EvaluationCase] = []
    for path in paths:
        source_path = _relative_location(root, path)
        try:
            path.resolve().relative_to(resolved_cases_path)
        except ValueError:
            _issue(
                issues,
                "case_escape",
                source_path,
                "case document resolves outside the documented case directory",
            )
            continue
        try:
            raw = load_json_document(path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            _issue(
                issues,
                "malformed_case",
                source_path,
                "case document is not valid UTF-8",
            )
            continue
        except json.JSONDecodeError as exc:
            _issue(
                issues,
                "malformed_case",
                source_path,
                f"invalid JSON at line {exc.lineno}, column {exc.colno}",
            )
            continue
        except AmbiguousJsonError as exc:
            _issue(
                issues,
                "malformed_case",
                source_path,
                f"ambiguous JSON: {exc}",
            )
            continue
        case = _parse_case(raw, source_path, issues)
        if case is not None:
            parsed.append(case)

    by_identifier: dict[str, list[EvaluationCase]] = {}
    for case in parsed:
        by_identifier.setdefault(case.identifier, []).append(case)
    for identifier, duplicates in sorted(by_identifier.items()):
        if len(duplicates) > 1:
            for case in duplicates:
                _issue(
                    issues,
                    "duplicate_identifier",
                    f"{case.source_path}.identifier",
                    f"identifier {identifier!r} appears more than once",
                )

    for case in parsed:
        evaluator = registry.get(case.category)
        if evaluator is None:
            _issue(
                issues,
                "unsupported_category",
                f"{case.source_path}.category",
                f"category {case.category!r} has no registered evaluator",
            )
            continue
        fixture_issue = _validate_fixture_reference(
            case,
            root,
            fixture_directories,
        )
        if fixture_issue is not None:
            issues.append(fixture_issue)

    if issues:
        raise ValidationError(issues)

    for case in parsed:
        evaluator = registry[case.category]
        try:
            evaluator_issues = evaluator.validate(case, root)
        except Exception as exc:  # noqa: BLE001 - validation must fail closed
            _issue(
                issues,
                "evaluator_validation_exception",
                case.source_path,
                f"category validator raised {type(exc).__name__}",
            )
            continue
        for diagnostic in evaluator_issues:
            _issue(
                issues,
                diagnostic.diagnostic_class,
                case.source_path,
                diagnostic.message,
            )

    if issues:
        raise ValidationError(issues)
    return tuple(sorted(parsed, key=lambda case: case.identifier))


def select_cases(
    cases: Sequence[EvaluationCase],
    *,
    categories: Sequence[str] = (),
    identifiers: Sequence[str] = (),
) -> tuple[EvaluationCase, ...]:
    """Filter validated cases without disturbing their canonical order."""

    category_set = frozenset(categories)
    identifier_set = frozenset(identifiers)
    selected = tuple(
        case
        for case in cases
        if (not category_set or case.category in category_set)
        and (not identifier_set or case.identifier in identifier_set)
    )
    if not selected:
        filters: list[str] = []
        if category_set:
            filters.append(f"categories={','.join(sorted(category_set))}")
        if identifier_set:
            filters.append(f"cases={','.join(sorted(identifier_set))}")
        detail = " and ".join(filters) if filters else "the validated case set"
        raise SelectionError(f"{detail} matched no cases")
    return selected


def _result_matches(case: EvaluationCase, observed: ObservedResult) -> bool:
    if observed.outcome != case.expected_outcome.outcome:
        return False
    expected_diagnostic = case.expected_outcome.diagnostic_class
    if expected_diagnostic is None:
        return True
    return any(
        diagnostic.diagnostic_class == expected_diagnostic
        for diagnostic in observed.diagnostics
    )


def run_cases(
    cases: Sequence[EvaluationCase],
    registry: EvaluationRegistry,
    repository_root: Path,
) -> AggregateResult:
    """Execute validated cases in stable identifier order."""

    results: list[CaseResult] = []
    for case in sorted(cases, key=lambda item: item.identifier):
        evaluator = registry[case.category]
        try:
            observed = evaluator.evaluate(case, repository_root)
            if observed.outcome not in {"pass", "fail"}:
                raise ValueError("evaluator returned an invalid outcome")
            if not all(
                isinstance(diagnostic, Diagnostic)
                for diagnostic in observed.diagnostics
            ):
                raise TypeError("evaluator returned an invalid diagnostic")
            diagnostics = tuple(
                sorted(
                    observed.diagnostics,
                    key=lambda diagnostic: (
                        diagnostic.diagnostic_class,
                        diagnostic.message,
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001 - evaluator failures are results
            observed = ObservedResult(
                outcome="fail",
                diagnostics=(
                    Diagnostic(
                        "evaluator_exception",
                        f"Evaluator raised {type(exc).__name__}.",
                    ),
                ),
            )
            diagnostics = observed.diagnostics
        normalized = ObservedResult(observed.outcome, diagnostics)
        results.append(
            CaseResult(
                identifier=case.identifier,
                category=case.category,
                observed_outcome=normalized.outcome,
                expected_outcome=case.expected_outcome.outcome,
                matched=_result_matches(case, normalized),
                diagnostics=normalized.diagnostics,
            )
        )
    matched = sum(result.matched for result in results)
    observed_pass = sum(result.observed_outcome == "pass" for result in results)
    return AggregateResult(
        cases=tuple(results),
        matched=matched,
        mismatched=len(results) - matched,
        observed_pass=observed_pass,
        observed_fail=len(results) - observed_pass,
    )


def render_machine(aggregate: AggregateResult) -> str:
    """Render byte-stable, versioned JSON from normalized results."""

    record = {
        "cases": [
            {
                "category": result.category,
                "diagnostics": [
                    {
                        "class": diagnostic.diagnostic_class,
                        "message": diagnostic.message,
                    }
                    for diagnostic in result.diagnostics
                ],
                "expected_outcome": result.expected_outcome,
                "identifier": result.identifier,
                "matched": result.matched,
                "observed_outcome": result.observed_outcome,
            }
            for result in aggregate.cases
        ],
        "schema_version": 1,
        "summary": {
            "matched": aggregate.matched,
            "mismatched": aggregate.mismatched,
            "observed_fail": aggregate.observed_fail,
            "observed_pass": aggregate.observed_pass,
            "total": len(aggregate.cases),
        },
    }
    return json.dumps(
        record,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ) + "\n"


def render_human(aggregate: AggregateResult) -> str:
    """Render a concise stable summary from normalized results."""

    lines: list[str] = []
    for result in aggregate.cases:
        status = "MATCH" if result.matched else "MISMATCH"
        line = (
            f"{status} {result.identifier} [{result.category}] "
            f"observed={result.observed_outcome} expected={result.expected_outcome}"
        )
        if result.diagnostics:
            classes = ",".join(
                diagnostic.diagnostic_class for diagnostic in result.diagnostics
            )
            line += f" diagnostics={classes}"
        lines.append(line)
    lines.append(
        f"Summary: total={len(aggregate.cases)} matched={aggregate.matched} "
        f"mismatched={aggregate.mismatched} "
        f"observed_pass={aggregate.observed_pass} "
        f"observed_fail={aggregate.observed_fail}"
    )
    return "\n".join(lines) + "\n"
