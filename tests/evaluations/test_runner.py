from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

import evaluations.__main__ as evaluation_cli
from evaluations.categories import StructuralTextMatchEvaluator, default_registry
from evaluations.runner import (
    Diagnostic,
    EvaluationCase,
    ExpectedOutcome,
    InputSpec,
    ObservedResult,
    SelectionError,
    ValidationError,
    discover_cases,
    render_human,
    render_machine,
    run_cases,
    select_cases,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class _TemporaryEvaluationRepository:
    def __init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        (self.root / "evaluations" / "cases").mkdir(parents=True)
        (self.root / "evaluations" / "fixtures").mkdir(parents=True)

    def cleanup(self) -> None:
        self._temporary.cleanup()

    def write_json(self, relative_path: str, value: object) -> Path:
        path = self.root.joinpath(*relative_path.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    def write_text(self, relative_path: str, value: str) -> Path:
        path = self.root.joinpath(*relative_path.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        return path

    def fixture_case(
        self,
        filename: str,
        identifier: str,
        *,
        category: str = "structural",
        fixture: str = "evaluations/fixtures/sample.json",
        expected_outcome: str = "pass",
        diagnostic_class: str | None = None,
        extra: dict[str, object] | None = None,
    ) -> Path:
        expected: dict[str, str] = {"outcome": expected_outcome}
        if diagnostic_class is not None:
            expected["diagnostic_class"] = diagnostic_class
        case: dict[str, object] = {
            "identifier": identifier,
            "category": category,
            "input": {"fixture": fixture},
            "expected_outcome": expected,
            "rationale": "Temporary deterministic test case.",
        }
        if extra:
            case.update(extra)
        return self.write_json(f"evaluations/cases/{filename}", case)


class _AlwaysPassEvaluator:
    def __init__(self) -> None:
        self.validate_calls = 0
        self.evaluate_calls = 0

    def validate(
        self, case: EvaluationCase, repository_root: Path
    ) -> tuple[Diagnostic, ...]:
        self.validate_calls += 1
        return ()

    def evaluate(
        self, case: EvaluationCase, repository_root: Path
    ) -> ObservedResult:
        self.evaluate_calls += 1
        return ObservedResult("pass")


class _RaisingEvaluator(_AlwaysPassEvaluator):
    def evaluate(
        self, case: EvaluationCase, repository_root: Path
    ) -> ObservedResult:
        raise RuntimeError("private detail must not enter output")


class CanonicalEvaluationTests(unittest.TestCase):
    def test_canonical_pass_and_expected_failure_match(self) -> None:
        registry = default_registry()
        cases = discover_cases(
            REPOSITORY_ROOT,
            PurePosixPath("evaluations/cases"),
            registry,
        )

        aggregate = run_cases(cases, registry, REPOSITORY_ROOT)

        self.assertEqual(
            [case.identifier for case in aggregate.cases],
            [
                "context-routing-baseline",
                "routing-adoption-collision",
                "routing-code-negative",
                "routing-entry-positive",
                "routing-plan-positive",
                "routing-plugin-negative",
                "routing-task-positive",
                "routing-update-positive",
                "structural-skill-surfaces",
                "structural-text-match",
                "structural-text-mismatch",
            ],
        )
        self.assertEqual(aggregate.matched, 11)
        self.assertEqual(aggregate.mismatched, 0)
        self.assertEqual(aggregate.observed_pass, 10)
        self.assertEqual(aggregate.observed_fail, 1)
        self.assertTrue(all(case.matched for case in aggregate.cases))
        self.assertEqual(
            aggregate.cases[-1].diagnostics[0].diagnostic_class,
            "text_mismatch",
        )

    def test_machine_and_human_rendering_are_stable(self) -> None:
        registry = default_registry()
        cases = discover_cases(
            REPOSITORY_ROOT,
            "evaluations/cases",
            registry,
        )
        first = run_cases(cases, registry, REPOSITORY_ROOT)
        second = run_cases(cases, registry, REPOSITORY_ROOT)

        self.assertEqual(render_machine(first).encode(), render_machine(second).encode())
        self.assertEqual(render_human(first), render_human(second))
        self.assertNotIn(str(REPOSITORY_ROOT), render_machine(first))
        machine = json.loads(render_machine(first))
        self.assertEqual(machine["schema_version"], 1)
        self.assertEqual(machine["summary"]["mismatched"], 0)

    def test_cli_repeated_machine_runs_are_byte_identical(self) -> None:
        command = [sys.executable, "-m", "evaluations", "--format", "json"]
        first = subprocess.run(
            command,
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
        )
        second = subprocess.run(
            command,
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
        )

        self.assertEqual(first.returncode, 0, msg=first.stderr.decode())
        self.assertEqual(second.returncode, 0, msg=second.stderr.decode())
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(first.stderr, b"")
        self.assertEqual(second.stderr, b"")

    def test_cli_case_filter_selects_only_requested_case(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "evaluations",
                "--case",
                "structural-text-mismatch",
                "--format",
                "json",
            ],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(
            [case["identifier"] for case in payload["cases"]],
            ["structural-text-mismatch"],
        )

    def test_cli_category_and_case_filters_keep_canonical_order(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "evaluations",
                "--category",
                "routing",
                "--case",
                "routing-update-positive",
                "--case",
                "routing-entry-positive",
                "--case",
                "routing-code-negative",
                "--format",
                "json",
            ],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(
            [case["identifier"] for case in payload["cases"]],
            [
                "routing-code-negative",
                "routing-entry-positive",
                "routing-update-positive",
            ],
        )

    def test_cli_returns_one_and_renders_an_outcome_mismatch(self) -> None:
        repository = _TemporaryEvaluationRepository()
        self.addCleanup(repository.cleanup)
        repository.write_json(
            "evaluations/fixtures/mismatch.json",
            {"actual": "observed", "expected": "different"},
        )
        repository.fixture_case(
            "mismatch.json",
            "unexpected-failure",
            fixture="evaluations/fixtures/mismatch.json",
        )
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            mock.patch.object(evaluation_cli, "REPOSITORY_ROOT", repository.root),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            status = evaluation_cli.main(["--format", "json"])

        self.assertEqual(status, 1)
        self.assertEqual(stderr.getvalue(), "")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["summary"]["mismatched"], 1)
        self.assertFalse(payload["cases"][0]["matched"])


class DiscoveryAndValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = _TemporaryEvaluationRepository()
        self.addCleanup(self.repository.cleanup)
        self.repository.write_json(
            "evaluations/fixtures/sample.json",
            {"actual": "same", "expected": "same"},
        )

    def discover(self, registry=None):
        return discover_cases(
            self.repository.root,
            "evaluations/cases",
            registry or {"structural": StructuralTextMatchEvaluator()},
        )

    def test_discovery_sorts_by_identifier_not_filename(self) -> None:
        self.repository.fixture_case("10-z.json", "z-case")
        self.repository.fixture_case("20-a.json", "a-case")

        cases = self.discover()

        self.assertEqual([case.identifier for case in cases], ["a-case", "z-case"])

    def test_unknown_field_and_duplicate_identifier_are_distinct(self) -> None:
        self.repository.fixture_case("10-first.json", "duplicate")
        self.repository.fixture_case("20-second.json", "duplicate")
        self.repository.fixture_case(
            "30-unknown.json",
            "unknown",
            extra={"surprise": True},
        )

        with self.assertRaises(ValidationError) as captured:
            self.discover()

        codes = [issue.code for issue in captured.exception.issues]
        self.assertIn("duplicate_identifier", codes)
        self.assertIn("unknown_field", codes)
        rendered = captured.exception.render()
        self.assertNotIn(str(self.repository.root), rendered)

    def test_missing_fixture_fails_validation(self) -> None:
        self.repository.fixture_case(
            "missing.json",
            "missing",
            fixture="evaluations/fixtures/not-there.json",
        )

        with self.assertRaises(ValidationError) as captured:
            self.discover()

        self.assertEqual(
            [issue.code for issue in captured.exception.issues],
            ["missing_fixture"],
        )
        self.assertIn(
            "evaluations/fixtures/not-there.json",
            captured.exception.render(),
        )

    def test_fixture_parent_traversal_is_rejected(self) -> None:
        self.repository.fixture_case(
            "escape.json",
            "escape",
            fixture="evaluations/fixtures/../../outside.json",
        )

        with self.assertRaises(ValidationError) as captured:
            self.discover()

        self.assertEqual(
            [issue.code for issue in captured.exception.issues],
            ["fixture_escape"],
        )

    def test_fixture_symlink_escape_is_rejected(self) -> None:
        outside = self.repository.root.parent / "outside-evaluation-fixture.json"
        outside.write_text('{"actual":"x","expected":"x"}\n', encoding="utf-8")
        self.addCleanup(outside.unlink)
        link = self.repository.root / "evaluations" / "fixtures" / "linked.json"
        link.symlink_to(outside)
        self.repository.fixture_case(
            "symlink.json",
            "symlink",
            fixture="evaluations/fixtures/linked.json",
        )

        with self.assertRaises(ValidationError) as captured:
            self.discover()

        self.assertEqual(
            [issue.code for issue in captured.exception.issues],
            ["fixture_escape"],
        )

    def test_malformed_case_fails_closed(self) -> None:
        self.repository.write_text("evaluations/cases/10-malformed.json", "{")

        with self.assertRaises(ValidationError) as captured:
            self.discover()

        self.assertEqual(
            [issue.code for issue in captured.exception.issues],
            ["malformed_case"],
        )

    def test_malformed_fixture_fails_closed(self) -> None:
        self.repository.fixture_case(
            "bad-fixture.json",
            "bad-fixture",
            fixture="evaluations/fixtures/bad.json",
        )
        self.repository.write_text("evaluations/fixtures/bad.json", "{")

        with self.assertRaises(ValidationError) as captured:
            self.discover()

        self.assertEqual(
            [issue.code for issue in captured.exception.issues],
            ["malformed_fixture"],
        )

    def test_duplicate_json_fields_fail_closed(self) -> None:
        self.repository.write_text(
            "evaluations/cases/duplicate-field.json",
            '{"identifier":"first","identifier":"second"}',
        )

        with self.assertRaises(ValidationError) as captured:
            self.discover()

        self.assertEqual(
            [issue.code for issue in captured.exception.issues],
            ["malformed_case"],
        )

    def test_nonfinite_fixture_numbers_fail_closed(self) -> None:
        self.repository.fixture_case(
            "nonfinite-fixture.json",
            "nonfinite-fixture",
            fixture="evaluations/fixtures/nonfinite.json",
        )
        self.repository.write_text(
            "evaluations/fixtures/nonfinite.json",
            '{"actual":NaN,"expected":"value"}',
        )

        with self.assertRaises(ValidationError) as captured:
            self.discover()

        self.assertEqual(
            [issue.code for issue in captured.exception.issues],
            ["malformed_fixture"],
        )

    def test_case_symlink_escape_is_rejected(self) -> None:
        outside = self.repository.root.parent / "outside-evaluation-case.json"
        outside.write_text(
            json.dumps(
                {
                    "identifier": "outside",
                    "category": "structural",
                    "input": {"fixture": "evaluations/fixtures/sample.json"},
                    "expected_outcome": {"outcome": "pass"},
                    "rationale": "Must not be read through a symlink.",
                }
            ),
            encoding="utf-8",
        )
        self.addCleanup(outside.unlink)
        link = self.repository.root / "evaluations" / "cases" / "linked.json"
        link.symlink_to(outside)

        with self.assertRaises(ValidationError) as captured:
            self.discover()

        self.assertEqual(
            [issue.code for issue in captured.exception.issues],
            ["case_escape"],
        )

    def test_unsupported_category_fails_validation(self) -> None:
        self.repository.fixture_case(
            "unsupported.json",
            "unsupported",
            category="routing",
        )

        with self.assertRaises(ValidationError) as captured:
            self.discover()

        self.assertEqual(
            [issue.code for issue in captured.exception.issues],
            ["unsupported_category"],
        )

    def test_invalid_metadata_prevents_all_evaluation(self) -> None:
        evaluator = _AlwaysPassEvaluator()
        self.repository.fixture_case("10-valid.json", "valid")
        self.repository.fixture_case(
            "20-invalid.json",
            "invalid",
            extra={"unknown": "field"},
        )

        with self.assertRaises(ValidationError):
            self.discover({"structural": evaluator})

        self.assertEqual(evaluator.validate_calls, 0)
        self.assertEqual(evaluator.evaluate_calls, 0)

    def test_empty_case_set_fails_closed(self) -> None:
        with self.assertRaises(ValidationError) as captured:
            self.discover()

        self.assertEqual(
            [issue.code for issue in captured.exception.issues],
            ["empty_case_set"],
        )

    def test_future_domain_category_labels_use_same_registry_contract(self) -> None:
        labels = (
            "structural",
            "routing",
            "migration",
            "privacy",
            "context-output-budget",
        )
        registry = {label: _AlwaysPassEvaluator() for label in labels}
        for index, label in enumerate(reversed(labels)):
            self.repository.fixture_case(
                f"{index:02d}.json",
                f"{label}-case",
                category=label,
            )

        cases = self.discover(registry)
        aggregate = run_cases(cases, registry, self.repository.root)

        self.assertEqual(aggregate.mismatched, 0)
        self.assertEqual(
            [case.identifier for case in aggregate.cases],
            sorted(f"{label}-case" for label in labels),
        )


class SelectionExecutionAndRenderingTests(unittest.TestCase):
    def _case(
        self,
        identifier: str,
        category: str,
        *,
        outcome: str = "pass",
        diagnostic_class: str | None = None,
    ) -> EvaluationCase:
        return EvaluationCase(
            identifier=identifier,
            category=category,
            input=InputSpec(value=True, uses_value=True),
            expected_outcome=ExpectedOutcome(outcome, diagnostic_class),
            measurement_boundary=None,
            rationale="Test.",
            source_path=f"evaluations/cases/{identifier}.json",
        )

    def test_category_and_case_filters_preserve_canonical_order(self) -> None:
        cases = (
            self._case("a", "routing"),
            self._case("b", "structural"),
            self._case("c", "routing"),
        )

        by_category = select_cases(cases, categories=("routing",))
        by_case = select_cases(cases, identifiers=("c", "a"))

        self.assertEqual([case.identifier for case in by_category], ["a", "c"])
        self.assertEqual([case.identifier for case in by_case], ["a", "c"])

    def test_empty_filter_result_is_actionable(self) -> None:
        with self.assertRaises(SelectionError) as captured:
            select_cases((self._case("a", "structural"),), categories=("privacy",))

        self.assertEqual(
            captured.exception.render(),
            "selection error [no_cases_selected]: categories=privacy matched no cases\n",
        )

    def test_unexpected_failure_and_success_are_mismatches(self) -> None:
        failing = self._case("expected-pass", "raises")
        unexpected_success = self._case(
            "expected-fail",
            "passes",
            outcome="fail",
            diagnostic_class="expected_failure",
        )
        registry = {
            "passes": _AlwaysPassEvaluator(),
            "raises": _RaisingEvaluator(),
        }

        aggregate = run_cases(
            (unexpected_success, failing),
            registry,
            REPOSITORY_ROOT,
        )

        self.assertEqual(aggregate.matched, 0)
        self.assertEqual(aggregate.mismatched, 2)
        self.assertEqual(
            [case.identifier for case in aggregate.cases],
            ["expected-fail", "expected-pass"],
        )
        self.assertEqual(
            aggregate.cases[1].diagnostics[0].diagnostic_class,
            "evaluator_exception",
        )
        self.assertNotIn("private detail", render_machine(aggregate))

    def test_machine_rendering_sorts_diagnostics(self) -> None:
        class _UnsortedEvaluator(_AlwaysPassEvaluator):
            def evaluate(
                self, case: EvaluationCase, repository_root: Path
            ) -> ObservedResult:
                return ObservedResult(
                    "fail",
                    (
                        Diagnostic("z-last", "Second."),
                        Diagnostic("a-first", "First."),
                    ),
                )

        case = self._case("diagnostics", "unsorted", outcome="fail")
        aggregate = run_cases(
            (case,),
            {"unsorted": _UnsortedEvaluator()},
            REPOSITORY_ROOT,
        )
        payload = json.loads(render_machine(aggregate))

        self.assertEqual(
            [item["class"] for item in payload["cases"][0]["diagnostics"]],
            ["a-first", "z-last"],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
