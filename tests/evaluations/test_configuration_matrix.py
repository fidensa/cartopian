"""End-to-end tests for the deterministic configuration compatibility matrix."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from evaluations import configuration_matrix as matrix
from evaluations.configuration_matrix import render_machine, run_matrix

ROOT = Path(__file__).resolve().parents[2]


class ConfigurationCompatibilityMatrixTests(unittest.TestCase):
    def test_all_cases_match_and_cover_required_machine_fields(self) -> None:
        result = run_matrix()
        self.assertEqual(result["summary"]["mismatched"], 0)
        self.assertGreaterEqual(result["summary"]["total"], 15)
        for case in result["cases"]:
            with self.subTest(case=case["case"]):
                self.assertTrue(case["matched"])
                self.assertIn("probes", case)
                self.assertIn("invariants", case)
                self.assertIn("filesystem_identities", case)
                self.assertIn("diagnostics", case)
                self.assertIn("rerun_comparison", case)

    def test_repeated_in_process_results_are_byte_identical(self) -> None:
        self.assertEqual(
            render_machine(run_matrix()).encode(),
            render_machine(run_matrix()).encode(),
        )

    def test_repeated_cli_results_are_byte_identical(self) -> None:
        command = [sys.executable, "-m", "evaluations.configuration_matrix"]
        first = subprocess.run(command, cwd=ROOT, capture_output=True, check=False)
        second = subprocess.run(command, cwd=ROOT, capture_output=True, check=False)
        self.assertEqual(first.returncode, 0, first.stderr.decode())
        self.assertEqual(second.returncode, 0, second.stderr.decode())
        self.assertEqual(first.stdout, second.stdout)
        payload = json.loads(first.stdout)
        self.assertEqual(payload["summary"]["mismatched"], 0)
        self.assertNotIn(str(ROOT), first.stdout.decode())

    def test_canonical_suite_inventory_comes_from_surface_registry(self) -> None:
        registry = matrix.load_registry(ROOT / "config-surfaces.json")
        result = run_matrix()
        self.assertEqual(
            result["canonical_suites"],
            registry["canonical_test_suites"],
        )
        self.assertEqual(
            {suite["id"] for suite in result["canonical_suites"]},
            {"unittest-discovery", "pytest"},
        )

    def test_migration_probe_lists_and_recovery_classes_are_exact(self) -> None:
        cases = {item["case"]: item for item in run_matrix()["cases"]}
        executable = [
            "plan_configuration_migration",
            "execute_configuration_migration",
            "plan_configuration_migration",
            "execute_configuration_migration",
        ]
        planning_only = [
            "plan_configuration_migration",
            "plan_configuration_migration",
        ]
        for name in (
            "migration-legacy",
            "migration-transitional",
            "migration-canonical",
        ):
            self.assertEqual(cases[name]["probes"], executable)
        expected_recovery = {
            "migration-conflict-pending": (
                "pending",
                "pending-operator-decision",
            ),
            "migration-unknown-grant-refusal": (
                "refused",
                "migration-refusal",
            ),
            "migration-malformed-marker-refusal": (
                "refused",
                "migration-refusal",
            ),
            "migration-newer-marker-refusal": (
                "refused",
                "migration-refusal",
            ),
        }
        for name, (status, classification) in expected_recovery.items():
            with self.subTest(case=name):
                case = cases[name]
                self.assertEqual(case["observed"], status)
                self.assertEqual(
                    case["rerun_comparison"]["planner_status"],
                    status,
                )
                self.assertEqual(
                    case["rerun_comparison"]["diagnostic_classifications"],
                    (classification,),
                )
                self.assertEqual(case["probes"], planning_only)

    def test_simulated_and_delegated_evidence_is_explicit(self) -> None:
        cases = {item["case"]: item for item in run_matrix()["cases"]}
        version = cases["peer-version-identities"]
        self.assertEqual(
            version["limitations"][0]["status"],
            "simulated/static",
        )
        for name in (
            "clean_installed",
            "dirty_installed",
        ):
            observation = version["rerun_comparison"]["observations"][name]
            self.assertEqual(observation["execution"], "simulated/static")
            self.assertEqual(observation["limitation"], "patched-git-helper")
        launch = cases["launch-authority-separation"]
        self.assertEqual(
            launch["rerun_comparison"]["delegated_contract"],
            {
                "suite": (
                    "tests.test_initiation_intent_static."
                    "ConventionsIntentClassificationTest."
                    "test_stop_language_overrides_configuration"
                ),
                "status": "passed",
                "scope": "operator-language stop boundary",
            },
        )
        self.assertTrue(
            launch["rerun_comparison"]["dispatch"]["permitted"][
                "launch_boundary_reached"
            ]
        )
        self.assertFalse(
            launch["rerun_comparison"]["dispatch"]["permission_absent"][
                "launch_boundary_reached"
            ]
        )

    def test_complete_guard_fixture_tree_is_read_only(self) -> None:
        case = next(
            item
            for item in run_matrix()["cases"]
            if item["case"] == "missing-project-guard-parity"
        )
        identities = case["filesystem_identities"]
        self.assertEqual(
            identities["complete_fixture_tree_before"],
            identities["complete_fixture_tree_after"],
        )
        self.assertTrue(case["invariants"]["read_only"])

    def test_computed_invariants_turn_matrix_red_under_deliberate_mutation(
        self,
    ) -> None:
        registry = matrix.load_registry(ROOT / "config-surfaces.json")
        mutated_registry = json.loads(json.dumps(registry))
        mutated_registry["accepted_values"] = {
            "automation.initiation": ["invented"]
        }
        with mock.patch.object(
            matrix,
            "load_registry",
            return_value=mutated_registry,
        ):
            result = run_matrix()
        surface = next(
            item
            for item in result["cases"]
            if item["case"] == "cross-surface-registry-parity"
        )
        self.assertFalse(
            surface["invariants"]["registry_is_inventory_not_authority"]
        )
        self.assertFalse(surface["matched"])

        truthful = matrix._version_evidence_is_truthful

        def strip_simulation_label(observations, limitations):
            mutated = json.loads(json.dumps(observations))
            mutated["clean_installed"].pop("limitation")
            return truthful(mutated, limitations)

        with mock.patch.object(
            matrix,
            "_version_evidence_is_truthful",
            side_effect=strip_simulation_label,
        ):
            result = run_matrix()
        version = next(
            item
            for item in result["cases"]
            if item["case"] == "peer-version-identities"
        )
        self.assertFalse(
            version["invariants"]["static_or_unverified_not_silently_passed"]
        )
        self.assertFalse(version["matched"])

        real_run_cli = matrix._run_cli

        def mutate_fixture(home, *args):
            (home.parent / "deliberate-mutation.txt").write_text(
                "matrix must detect this write\n",
                encoding="utf-8",
            )
            return real_run_cli(home, *args)

        with mock.patch.object(
            matrix,
            "_run_cli",
            side_effect=mutate_fixture,
        ):
            result = run_matrix()
        guard = next(
            item
            for item in result["cases"]
            if item["case"] == "missing-project-guard-parity"
        )
        self.assertFalse(guard["invariants"]["read_only"])
        self.assertFalse(guard["matched"])


if __name__ == "__main__":
    unittest.main()
