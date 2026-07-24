"""Focused tests for deterministic structural and routing evaluations."""

from __future__ import annotations

import unittest
from pathlib import Path

from evaluations.routing import (
    ContextSizeEvaluator,
    RepositoryStructuralEvaluator,
    RoutingEvaluator,
    compact_routing_surface_bytes,
)
from evaluations.runner import (
    EvaluationCase,
    ExpectedOutcome,
    InputSpec,
    MeasurementBoundary,
)

ROOT = Path(__file__).resolve().parents[2]


class RoutingEvaluationTests(unittest.TestCase):
    def _case(
        self,
        value: object,
        *,
        category: str = "routing",
        boundary: MeasurementBoundary | None = None,
    ) -> EvaluationCase:
        return EvaluationCase(
            identifier="focused-case",
            category=category,
            input=InputSpec(value=value, uses_value=True),
            expected_outcome=ExpectedOutcome("pass"),
            measurement_boundary=boundary,
            rationale="Focused deterministic test.",
            source_path="evaluations/cases/focused-case.json",
        )

    def _routing_value(
        self,
        utterance: str,
        expectation: dict[str, object],
        *,
        candidates: list[str] | None = None,
    ) -> dict[str, object]:
        return {
            "utterance": utterance,
            "expectation": expectation,
            "candidates": candidates,
            "rationale": "Protects a focused routing behavior.",
        }

    def test_positive_negative_and_collision_outcomes(self) -> None:
        evaluator = RoutingEvaluator()
        values = (
            self._routing_value(
                "/use-cartopian and resume this project session.",
                {"type": "selection", "skill": "use_cartopian"},
            ),
            self._routing_value(
                "Install the email plugin and connect my mailbox.",
                {"type": "none"},
            ),
            self._routing_value(
                (
                    "We have external source material with requirements and need "
                    "a local requirements file; we also have an existing structured "
                    "plan to convert into project artifacts."
                ),
                {
                    "type": "collision",
                    "skills": ["adopt_plan", "adopt_requirements"],
                },
                candidates=["adopt_plan", "adopt_requirements"],
            ),
        )

        for value in values:
            with self.subTest(expectation=value["expectation"]):
                case = self._case(value)
                self.assertEqual(evaluator.validate(case, ROOT), ())
                self.assertEqual(evaluator.evaluate(case, ROOT).outcome, "pass")

    def test_routing_schema_failures_are_closed_and_stable(self) -> None:
        evaluator = RoutingEvaluator()
        values_and_classes = (
            (
                self._routing_value("", {"type": "none"}),
                "empty_utterance",
            ),
            (
                self._routing_value("Do something.", {"type": "maybe"}),
                "unsupported_expectation",
            ),
            (
                {
                    **self._routing_value("Do something.", {"type": "none"}),
                    "unknown": True,
                },
                "fixture_unknown_field",
            ),
            (
                self._routing_value(
                    "Use the missing workflow.",
                    {"type": "selection", "skill": "not_a_skill"},
                ),
                "unknown_skill",
            ),
            (
                self._routing_value(
                    "Adopt these artifacts.",
                    {
                        "type": "collision",
                        "skills": ["adopt_plan", "adopt_requirements"],
                    },
                    candidates=["adopt_requirements", "adopt_plan"],
                ),
                "unordered_candidates",
            ),
        )

        for value, diagnostic_class in values_and_classes:
            with self.subTest(diagnostic_class=diagnostic_class):
                case = self._case(value)
                first = evaluator.validate(case, ROOT)
                second = evaluator.validate(case, ROOT)
                self.assertEqual(first, second)
                self.assertEqual(first[0].diagnostic_class, diagnostic_class)

    def test_repository_structural_surface_is_valid(self) -> None:
        evaluator = RepositoryStructuralEvaluator()
        case = self._case(
            {"check": "skill-metadata-surfaces"},
            category="structural",
        )

        self.assertEqual(evaluator.validate(case, ROOT), ())
        self.assertEqual(evaluator.evaluate(case, ROOT).outcome, "pass")

    def test_context_measurement_passes_with_phase_00_baseline(self) -> None:
        evaluator = ContextSizeEvaluator()
        value = {
            "surface": "compact-skill-routing-metadata-v1",
            "baseline_label": "phase-00.use-cartopian-resource.canonicalized",
            "budget_relation": "not-greater-than",
        }
        case = self._case(
            value,
            category="context-size",
            boundary=MeasurementBoundary(max_input_bytes=6141),
        )

        self.assertEqual(evaluator.validate(case, ROOT), ())
        result = evaluator.evaluate(case, ROOT)
        self.assertEqual(result.outcome, "pass")
        self.assertEqual(
            result.diagnostics[0].diagnostic_class,
            "context_size_measurement",
        )
        self.assertIn(
            f"is {compact_routing_surface_bytes(ROOT)} exact UTF-8 bytes",
            result.diagnostics[0].message,
        )

    def test_unjustified_context_increase_fails(self) -> None:
        evaluator = ContextSizeEvaluator()
        value = {
            "surface": "compact-skill-routing-metadata-v1",
            "baseline_label": "test.too-small",
            "budget_relation": "not-greater-than",
        }
        case = self._case(
            value,
            category="context-size",
            boundary=MeasurementBoundary(max_input_bytes=1),
        )

        result = evaluator.evaluate(case, ROOT)

        self.assertEqual(result.outcome, "fail")
        self.assertEqual(
            result.diagnostics[0].diagnostic_class,
            "context_size_increase",
        )

    def test_context_allowance_requires_justification(self) -> None:
        evaluator = ContextSizeEvaluator()
        value = {
            "surface": "compact-skill-routing-metadata-v1",
            "baseline_label": "test.baseline",
            "budget_relation": "not-greater-than",
            "allowance": {"bytes": 10},
        }
        case = self._case(
            value,
            category="context-size",
            boundary=MeasurementBoundary(max_input_bytes=1),
        )

        diagnostics = evaluator.validate(case, ROOT)

        self.assertEqual(diagnostics[0].diagnostic_class, "incomplete_allowance")

    def test_context_baseline_label_is_required(self) -> None:
        evaluator = ContextSizeEvaluator()
        value = {
            "surface": "compact-skill-routing-metadata-v1",
            "baseline_label": "",
            "budget_relation": "not-greater-than",
        }
        case = self._case(
            value,
            category="context-size",
            boundary=MeasurementBoundary(max_input_bytes=6141),
        )

        diagnostics = evaluator.validate(case, ROOT)

        self.assertEqual(diagnostics[0].diagnostic_class, "missing_baseline_label")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
