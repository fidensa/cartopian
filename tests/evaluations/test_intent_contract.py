"""Focused deterministic coverage for the compact planning-intent contract."""

from __future__ import annotations

import unittest
from pathlib import Path

from evaluations.intent import IntentContractEvaluator
from evaluations.runner import EvaluationCase, ExpectedOutcome, InputSpec

ROOT = Path(__file__).resolve().parents[2]


class IntentContractEvaluationTests(unittest.TestCase):
    def _case(self, value: object) -> EvaluationCase:
        return EvaluationCase(
            identifier="focused-intent",
            category="intent-contract",
            input=InputSpec(value=value, uses_value=True),
            expected_outcome=ExpectedOutcome("pass"),
            measurement_boundary=None,
            rationale="Focused compact-intent behavior.",
            source_path="evaluations/cases/focused-intent.json",
        )

    def _fixture(self, name: str) -> object:
        import json

        return json.loads(
            (ROOT / "evaluations" / "fixtures" / name).read_text(encoding="utf-8")
        )

    def test_required_scenarios_match(self) -> None:
        evaluator = IntentContractEvaluator()
        fixtures = (
            "intent-complete.json",
            "intent-partial.json",
            "intent-conflicting.json",
            "intent-excluded-scope.json",
            "intent-premature-execution.json",
        )

        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                case = self._case(self._fixture(fixture))
                self.assertEqual(evaluator.validate(case, ROOT), ())
                self.assertEqual(evaluator.evaluate(case, ROOT).outcome, "pass")

    def test_all_three_request_classes_remain_distinct(self) -> None:
        evaluator = IntentContractEvaluator()
        base = self._fixture("intent-complete.json")
        assert isinstance(base, dict)
        expected = base["expected"]
        request = base["request"]
        assert isinstance(expected, dict)
        assert isinstance(request, dict)

        scenarios = (
            ("informational", "status", "auto", False),
            ("scoped", "generate_tasks", "operator", False),
            ("execution", "execute", "operator", True),
        )
        for request_class, operation, initiation, starts_execution in scenarios:
            with self.subTest(request_class=request_class):
                request["class"] = request_class
                request["operation"] = operation
                request["initiation"] = initiation
                expected["request_class"] = request_class
                expected["starts_execution"] = starts_execution
                expected["generated_phases"] = (
                    ["PHASE-01-entry"]
                    if request_class == "scoped"
                    else []
                )
                case = self._case(base)
                self.assertEqual(evaluator.validate(case, ROOT), ())
                self.assertEqual(evaluator.evaluate(case, ROOT).outcome, "pass")

    def test_confidence_scoring_and_cross_model_confirmation_fail_closed(self) -> None:
        evaluator = IntentContractEvaluator()
        for prohibited in ("confidence_percent", "cross_model_confirmation"):
            with self.subTest(prohibited=prohibited):
                fixture = self._fixture("intent-complete.json")
                assert isinstance(fixture, dict)
                fixture[prohibited] = 90
                diagnostics = evaluator.validate(self._case(fixture), ROOT)
                self.assertEqual(
                    diagnostics[0].diagnostic_class,
                    "fixture_unknown_field",
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
