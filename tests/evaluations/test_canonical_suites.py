"""Direct coverage for the canonical-suite runner and output parsers."""
from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from cli.config_surface_parity import canonical_suite_manifest_diagnostics
from evaluations import canonical_suites


class CanonicalSuiteParserTests(unittest.TestCase):
    def test_unittest_counts_include_failures_errors_skips_and_zero_fallback(
        self,
    ) -> None:
        output = "Ran 12 tests in 0.1s\nFAILED (failures=1, errors=2, skipped=3)\n"
        self.assertEqual(
            canonical_suites._unittest_counts(output),
            {
                "collected": 12,
                "passed": 6,
                "failed": 3,
                "skipped": 3,
            },
        )
        self.assertEqual(
            canonical_suites._unittest_counts("FAILED\n"),
            {
                "collected": 0,
                "passed": 0,
                "failed": 0,
                "skipped": 0,
            },
        )

    def test_unittest_collection_observes_required_module_paths(self) -> None:
        output = (
            "test_one "
            "(tests.test_config_surface_parity.TestSurfaceRegistry.test_one) ... ok\n"
            "test_two "
            "(tests.test_config_surface_parity.TestSurfaceRegistry.test_two) ... ok\n"
            "test_other "
            "(tests.evaluations.test_configuration_matrix.Matrix.test_other) ... ok\n"
        )
        self.assertEqual(
            canonical_suites._unittest_collection(
                output,
                (
                    "tests/test_config_surface_parity.py",
                    "tests/evaluations/test_configuration_matrix.py",
                ),
            ),
            {
                "tests/test_config_surface_parity.py": 2,
                "tests/evaluations/test_configuration_matrix.py": 1,
            },
        )

    def test_pytest_collection_uses_summary_and_node_id_fallback(self) -> None:
        nodes = (
            "tests/a.py::test_one\n"
            "tests/a.py::test_two\n"
            "tests/b.py::test_three\n"
        )
        self.assertEqual(
            canonical_suites._pytest_collection(nodes + "\n3 tests collected\n"),
            (3, {"tests/a.py": 2, "tests/b.py": 1}),
        )
        self.assertEqual(
            canonical_suites._pytest_collection(nodes),
            (3, {"tests/a.py": 2, "tests/b.py": 1}),
        )
        self.assertEqual(canonical_suites._pytest_collection("no tests ran\n"), (0, {}))

    def test_pytest_execution_counts_and_zero_summary_fail_closed(self) -> None:
        self.assertEqual(
            canonical_suites._pytest_execution_counts(
                "8 passed, 2 failed, 1 error, 3 skipped\n",
                14,
            ),
            {
                "collected": 14,
                "passed": 8,
                "failed": 3,
                "skipped": 3,
            },
        )
        self.assertEqual(
            canonical_suites._pytest_execution_counts("interrupted\n", 14),
            {
                "collected": 14,
                "passed": 0,
                "failed": 14,
                "skipped": 0,
            },
        )


class CanonicalSuiteRunnerTests(unittest.TestCase):
    def test_evaluate_unittest_suite_tracks_required_file_collection(self) -> None:
        suite = {
            "id": "unittest",
            "runner": "unittest",
            "collection_command": ["{python}", "-m", "unittest", "-v"],
            "execution_command": ["{python}", "-m", "unittest", "-v"],
            "collection_executes": True,
            "minimum_collected": 2,
            "required_tests": [{"path": "tests/probe.py", "count": 2}],
        }
        output = (
            "test_one (tests.probe.Probe.test_one) ... ok\n"
            "test_two (tests.probe.Probe.test_two) ... ok\n"
            "Ran 2 tests in 0.01s\nOK\n"
        )
        completed = subprocess.CompletedProcess([], 0, "", output)
        with patch.object(canonical_suites, "_run", return_value=completed):
            result = canonical_suites.evaluate_suite(suite)
        self.assertTrue(result["green"])
        self.assertEqual(result["required_tests_collected"], {"tests/probe.py": 2})
        self.assertEqual(result["counts"]["passed"], 2)
        no_summary = subprocess.CompletedProcess([], 1, "", "FAILED\n")
        with patch.object(canonical_suites, "_run", return_value=no_summary):
            result = canonical_suites.evaluate_suite(suite)
        self.assertFalse(result["green"])
        self.assertEqual(result["counts"]["collected"], 0)
        self.assertIn("collection-exit-1", result["issues"])

    def test_evaluate_pytest_suite_reports_execution_failure(self) -> None:
        suite = {
            "id": "pytest",
            "runner": "pytest",
            "collection_command": ["{python}", "-m", "pytest", "--collect-only", "-q"],
            "execution_command": ["{python}", "-m", "pytest", "-q"],
            "collection_executes": False,
            "minimum_collected": 2,
            "required_tests": [{"path": "tests/probe.py", "count": 2}],
        }
        collection = subprocess.CompletedProcess(
            [],
            0,
            "tests/probe.py::test_one\ntests/probe.py::test_two\n2 tests collected\n",
            "",
        )
        execution = subprocess.CompletedProcess([], 1, "", "1 passed, 1 failed\n")
        with patch.object(
            canonical_suites,
            "_run",
            side_effect=(collection, execution),
        ):
            result = canonical_suites.evaluate_suite(suite)
        self.assertFalse(result["green"])
        self.assertEqual(result["counts"]["failed"], 1)
        self.assertIn("execution-exit-1", result["issues"])

    def test_manifest_rejects_vacuous_or_unobservable_declarations(self) -> None:
        registry = {
            "canonical_test_suites": [
                {
                    "id": "unittest",
                    "runner": "unittest",
                    "collection_command": ["{python}", "-m", "unittest"],
                    "execution_command": ["{python}", "-m", "unittest"],
                    "collection_executes": True,
                    "minimum_collected": 1,
                    "required_tests": [],
                }
            ]
        }
        diagnostics = canonical_suite_manifest_diagnostics(
            canonical_suites.ROOT,
            registry,
        )
        details = {diagnostic.detail for diagnostic in diagnostics}
        self.assertIn(
            "minimum_collected must be a nontrivial integer of at least 2",
            details,
        )
        self.assertIn("required_tests must be a non-empty list", details)
        registry["canonical_test_suites"][0]["minimum_collected"] = 2
        registry["canonical_test_suites"][0]["required_tests"] = [
            {
                "path": "tests/evaluations/test_canonical_suites.py",
                "count": 8,
            }
        ]
        diagnostics = canonical_suite_manifest_diagnostics(
            canonical_suites.ROOT,
            registry,
        )
        self.assertIn(
            "unittest required_tests need verbose execution output",
            {diagnostic.detail for diagnostic in diagnostics},
        )
        registry["canonical_test_suites"][0]["collection_command"].append("-v")
        registry["canonical_test_suites"][0]["execution_command"].append("-v")
        self.assertEqual(
            canonical_suite_manifest_diagnostics(
                canonical_suites.ROOT,
                registry,
            ),
            (),
        )

    def test_run_returns_manifest_failure_without_executing_a_suite(self) -> None:
        diagnostic = canonical_suites.canonical_suite_manifest_diagnostics(
            canonical_suites.ROOT,
            {"canonical_test_suites": []},
        )
        with (
            patch.object(canonical_suites, "load_registry", return_value={}),
            patch.object(
                canonical_suites,
                "canonical_suite_manifest_diagnostics",
                return_value=diagnostic,
            ),
            patch.object(canonical_suites, "evaluate_suite") as evaluate,
        ):
            result = canonical_suites.run()
        self.assertFalse(result["green"])
        self.assertTrue(result["manifest_diagnostics"])
        evaluate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
