"""Registered deterministic category evaluators."""

from __future__ import annotations

import json
from pathlib import Path

from evaluations.runner import (
    AmbiguousJsonError,
    Diagnostic,
    EvaluationCase,
    EvaluationRegistry,
    ObservedResult,
    load_json_document,
)


class StructuralTextMatchEvaluator:
    """Minimal representative evaluator for the category extension seam."""

    _FIELDS = frozenset({"actual", "expected"})

    def _load_fixture(self, case: EvaluationCase, repository_root: Path) -> object:
        assert case.input.fixture is not None
        path = repository_root.joinpath(*case.input.fixture.split("/"))
        return load_json_document(path.read_text(encoding="utf-8"))

    def validate(
        self, case: EvaluationCase, repository_root: Path
    ) -> tuple[Diagnostic, ...]:
        if case.input.fixture is None:
            return (
                Diagnostic(
                    "unsupported_input",
                    "The structural evaluator requires a fixture reference.",
                ),
            )
        if case.measurement_boundary is not None:
            return (
                Diagnostic(
                    "unsupported_measurement_boundary",
                    "The structural evaluator does not use a measurement boundary.",
                ),
            )
        try:
            fixture = self._load_fixture(case, repository_root)
        except UnicodeDecodeError:
            return (
                Diagnostic("malformed_fixture", "Fixture is not valid UTF-8."),
            )
        except json.JSONDecodeError as exc:
            return (
                Diagnostic(
                    "malformed_fixture",
                    f"Fixture contains invalid JSON at line {exc.lineno}, "
                    f"column {exc.colno}.",
                ),
            )
        except AmbiguousJsonError as exc:
            return (
                Diagnostic(
                    "malformed_fixture",
                    f"Fixture contains ambiguous JSON: {exc}.",
                ),
            )
        if not isinstance(fixture, dict):
            return (
                Diagnostic("malformed_fixture", "Fixture must be a JSON object."),
            )
        unknown = sorted(set(fixture) - self._FIELDS)
        if unknown:
            return (
                Diagnostic(
                    "fixture_unknown_field",
                    f"Fixture field {unknown[0]!r} is not allowed.",
                ),
            )
        missing = sorted(self._FIELDS - set(fixture))
        if missing:
            return (
                Diagnostic(
                    "fixture_missing_field",
                    f"Fixture field {missing[0]!r} is required.",
                ),
            )
        if not isinstance(fixture["actual"], str) or not isinstance(
            fixture["expected"], str
        ):
            return (
                Diagnostic(
                    "fixture_invalid_type",
                    "Fixture fields 'actual' and 'expected' must be strings.",
                ),
            )
        return ()

    def evaluate(
        self, case: EvaluationCase, repository_root: Path
    ) -> ObservedResult:
        fixture = self._load_fixture(case, repository_root)
        assert isinstance(fixture, dict)
        if fixture["actual"] == fixture["expected"]:
            return ObservedResult("pass")
        return ObservedResult(
            "fail",
            (
                Diagnostic(
                    "text_mismatch",
                    "Fixture actual text does not match expected text.",
                ),
            ),
        )


def default_registry() -> EvaluationRegistry:
    """Return a fresh registry so later categories can extend it explicitly."""

    return {"structural": StructuralTextMatchEvaluator()}
