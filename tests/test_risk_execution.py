"""Execution fixtures for risk-scaled governance and critical review context."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]


def _observations(**overrides: str) -> list[dict[str, object]]:
    states = {
        "consequence-reach": "local-artifact",
        "reversibility": "direct-undo",
        "authority": "covered",
        "ambiguity": "confirmed",
        "evidence-coverage": "deterministic",
    }
    states.update(overrides)
    return [
        {
            "observation": observation,
            "state": state,
            "supporting_fact": f"fixture:{observation}:{state}",
        }
        for observation, state in states.items()
    ]


def _cli_arguments() -> list[str]:
    return [
        "--consequence-reach", "local-artifact",
        "--consequence-reach-fact", "fixture:local",
        "--reversibility", "direct-undo",
        "--reversibility-fact", "fixture:undo",
        "--authority", "covered",
        "--authority-fact", "fixture:authority",
        "--ambiguity", "confirmed",
        "--ambiguity-fact", "fixture:inputs",
        "--evidence-coverage", "deterministic",
        "--evidence-coverage-fact", "fixture:proof",
    ]


class RiskClassifierTests(unittest.TestCase):
    def test_critical_condition_dominates_four_routine_observations(self) -> None:
        from cli.risk_contract import classify_risk

        result = classify_risk(_observations(authority="absent"))

        self.assertEqual(result["band"], "critical")
        self.assertEqual(
            [reason["observation"] for reason in result["ordered_reasons"]],
            ["authority"],
        )
        self.assertEqual(result["operator_gate"], "explicit-operator-authorization")

    def test_missing_observation_fails_closed(self) -> None:
        from cli.risk_contract import RiskContractError, classify_risk

        with self.assertRaisesRegex(RiskContractError, "missing-observation:authority"):
            classify_risk(
                [
                    item
                    for item in _observations()
                    if item["observation"] != "authority"
                ]
            )

    def test_unknown_authority_and_evidence_are_never_favorable(self) -> None:
        from cli.risk_contract import classify_risk

        authority = classify_risk(_observations(authority="unknown"))
        evidence = classify_risk(_observations(**{"evidence-coverage": "unknown"}))

        self.assertEqual(authority["band"], "critical")
        self.assertEqual(evidence["band"], "consequential")

    def test_each_band_derives_its_complete_governance_row(self) -> None:
        from cli.risk_contract import classify_risk

        cases = {
            "routine": _observations(),
            "bounded": _observations(ambiguity="stated-assumption"),
            "consequential": _observations(reversibility="recovery-dependent"),
            "critical": _observations(**{"evidence-coverage": "unavailable"}),
        }
        expected = {
            "routine": (
                "task-local-proof", "none-by-default", "no-additional-gate",
                "known-correction-path",
            ),
            "bounded": (
                "artifact-and-recovery-proof", "policy-only",
                "boundary-crossing-approval", "named-recovery-action",
            ),
            "consequential": (
                "target-state-proof", "independent-when-qualitative",
                "material-commitment-approval", "recorded-trigger-and-owner",
            ),
            "critical": (
                "target-state-and-failclosed-proof", "independent-challenge",
                "explicit-operator-authorization",
                "evidenced-contingency-and-stop-condition",
            ),
        }

        for band, observations in cases.items():
            with self.subTest(band=band):
                result = classify_risk(observations)
                self.assertEqual(result["band"], band)
                self.assertEqual(
                    (
                        result["evidence_expectation"],
                        result["review_expectation"],
                        result["operator_gate"],
                        result["contingency_expectation"],
                    ),
                    expected[band],
                )

    def test_runtime_matches_every_authoritative_fixed_envelope(self) -> None:
        from cli.risk_contract import classify_risk, load_risk_contract

        registry = load_risk_contract()
        for envelope in registry["fixtures"]["task_envelopes"]:
            observations = [
                {
                    "observation": observation,
                    "state": state,
                    "supporting_fact": f"fixture:{envelope['id']}:{observation}",
                }
                for observation, state in reversed(
                    list(envelope["observations"].items())
                )
            ]
            with self.subTest(envelope=envelope["id"]):
                result = classify_risk(observations)
                self.assertEqual(result["band"], envelope["expected_band"])
                self.assertEqual(
                    [
                        f"{reason['observation']}={reason['state']}"
                        f"->{reason['band_floor']}"
                        for reason in result["ordered_reasons"]
                    ],
                    envelope["expected_ordered_reasons"],
                )

    def test_classifier_does_not_mutate_configured_policy_or_launch_authority(self) -> None:
        from cli.risk_contract import classify_risk

        configured = {
            "reviews": {"task_closure": "off", "task_role": "quality"},
            "roles": {"quality": {"grants": ["read"], "auto_launch": []}},
            "automation": {"initiation": "operator", "confirmation": "each-handoff"},
        }
        before = copy.deepcopy(configured)

        result = classify_risk(_observations(authority="absent"))

        self.assertEqual(configured, before)
        self.assertEqual(result["review_expectation"], "independent-challenge")
        self.assertNotIn("configured_policy", result)


class CriticalReviewContextTests(unittest.TestCase):
    def test_context_is_fresh_bounded_and_contains_only_contract_and_artifact(self) -> None:
        from cli.risk_contract import (
            build_adversarial_review_context,
            classify_risk,
        )

        risk_result = classify_risk(_observations(authority="absent"))
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifact = root / "artifact.txt"
            contract = root / "governing-contract.md"
            artifact.write_text("delivered artifact\n", encoding="utf-8")
            contract.write_text("# Contract\n\nChallenge decisive claims.\n", encoding="utf-8")

            first = build_adversarial_review_context(
                artifact, contract, allowed_roots=[root], risk_result=risk_result
            )
            second = build_adversarial_review_context(
                artifact, contract, allowed_roots=[root], risk_result=risk_result
            )

        self.assertEqual(first, second)
        self.assertEqual(set(first["context"]), {"artifact", "governing_contract"})
        self.assertNotIn("author_conclusion", json.dumps(first))
        self.assertLessEqual(first["context_bytes"], first["max_context_bytes"])
        self.assertEqual(first["risk_band"], "critical")

    def test_context_rejects_noncritical_results_and_oversized_inputs(self) -> None:
        from cli.risk_contract import (
            RiskContractError,
            build_adversarial_review_context,
            classify_risk,
        )

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifact = root / "artifact.txt"
            contract = root / "contract.md"
            artifact.write_text("artifact", encoding="utf-8")
            contract.write_text("contract", encoding="utf-8")
            with self.assertRaisesRegex(RiskContractError, "critical-risk-required"):
                build_adversarial_review_context(
                    artifact,
                    contract,
                    allowed_roots=[root],
                    risk_result=classify_risk(_observations()),
                )
            artifact.write_text("x" * 1025, encoding="utf-8")
            with self.assertRaisesRegex(RiskContractError, "review-context-too-large"):
                build_adversarial_review_context(
                    artifact,
                    contract,
                    allowed_roots=[root],
                    risk_result=classify_risk(_observations(authority="absent")),
                    max_context_bytes=1024,
                )

    def test_context_rejects_files_outside_the_allowed_roots(self) -> None:
        from cli.risk_contract import (
            RiskContractError,
            build_adversarial_review_context,
            classify_risk,
        )

        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            allowed = base / "allowed"
            outside = base / "outside"
            allowed.mkdir()
            outside.mkdir()
            artifact = outside / "artifact.txt"
            contract = allowed / "contract.md"
            artifact.write_text("artifact", encoding="utf-8")
            contract.write_text("contract", encoding="utf-8")
            with self.assertRaisesRegex(
                RiskContractError, "review-context-path-outside-roots"
            ):
                build_adversarial_review_context(
                    artifact,
                    contract,
                    allowed_roots=[allowed],
                    risk_result=classify_risk(_observations(authority="absent")),
                )


class RiskSurfaceParityTests(unittest.TestCase):
    def test_cli_and_mcp_return_the_same_structured_risk_result(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "cli", "classify-risk", *_cli_arguments()],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        cli_record = json.loads(completed.stdout)

        from mcp_server import server

        server._TOOL_CACHE = None
        arguments = {
            "consequence_reach": "local-artifact",
            "consequence_reach_fact": "fixture:local",
            "reversibility": "direct-undo",
            "reversibility_fact": "fixture:undo",
            "authority": "covered",
            "authority_fact": "fixture:authority",
            "ambiguity": "confirmed",
            "ambiguity_fact": "fixture:inputs",
            "evidence_coverage": "deterministic",
            "evidence_coverage_fact": "fixture:proof",
        }
        mcp_result = server.call_tool("classify_risk", arguments)

        self.assertFalse(mcp_result["isError"])
        self.assertEqual(mcp_result["structuredContent"]["records"], [cli_record])

    def test_cli_and_mcp_return_the_same_bounded_adversarial_context(self) -> None:
        from cli.risk_contract import classify_risk
        from mcp_server import server

        risk_result = classify_risk(_observations(authority="absent"))
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            home = Path(raw) / "home"
            root.mkdir()
            home.mkdir()
            (root / "cartopian.toml").write_text(
                "[project]\n"
                'id = "risk-fixture"\n'
                'name = "Risk fixture"\n'
                'project_schema_version = "v0.10.0"\n',
                encoding="utf-8",
            )
            artifact = root / "artifact.txt"
            contract = root / "contract.md"
            artifact.write_text("artifact\n", encoding="utf-8")
            contract.write_text("# Governing contract\n", encoding="utf-8")
            arguments = {
                "project_root": str(root),
                "artifact": str(artifact),
                "governing_contract": str(contract),
                "risk_result": json.dumps(risk_result, separators=(",", ":")),
            }
            env = dict(os.environ)
            env["HOME"] = str(home)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "cli",
                    "adversarial-review-context",
                    str(root),
                    "--artifact",
                    str(artifact),
                    "--governing-contract",
                    str(contract),
                    "--risk-result",
                    arguments["risk_result"],
                ],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            cli_record = json.loads(completed.stdout)

            server._TOOL_CACHE = None
            with patch.dict(os.environ, {"HOME": str(home)}):
                mcp_result = server.call_tool(
                    "adversarial_review_context", arguments
                )

        self.assertFalse(mcp_result["isError"])
        self.assertEqual(mcp_result["structuredContent"]["records"], [cli_record])
        self.assertEqual(
            set(cli_record["context"]), {"artifact", "governing_contract"}
        )

    def test_templates_and_skills_project_risk_without_rewriting_policy(self) -> None:
        task = (REPO_ROOT / "templates" / "TASK.md").read_text(encoding="utf-8")
        prompt = (REPO_ROOT / "templates" / "PROMPT.md").read_text(encoding="utf-8")
        report = (REPO_ROOT / "templates" / "REPORT.md").read_text(encoding="utf-8")
        run_task = (REPO_ROOT / "skills" / "run-task.md").read_text(encoding="utf-8")
        run_handoff = (REPO_ROOT / "skills" / "run-handoff.md").read_text(encoding="utf-8")

        self.assertIn("## Risk observations", task)
        self.assertIn("## Risk result", prompt)
        self.assertIn("## Risk-scaled evidence", report)
        self.assertIn("configured review policy remains authoritative", run_task)
        self.assertIn("adversarial-review-context", run_handoff)
        self.assertIn("without the author's conclusion", run_handoff)


if __name__ == "__main__":
    unittest.main()
