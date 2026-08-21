"""Execution fixtures for bounded judgment-card activation and guidance loading.

These checks prove the target state the contract claims: one central bounded
failure-signal body, activation only at a justified lifecycle boundary with an
open non-enforceable failure, zero guidance bytes anywhere else, and no
activation edge from risk classification or practice-pack selection.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = REPO_ROOT / "protocol" / "risk-and-practice-contract.json"
PROJECTION_PATH = REPO_ROOT / "protocol" / "RISK_AND_PRACTICE.md"


def _registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _judgment() -> dict:
    return _registry()["judgment"]


def _measurement() -> dict:
    return _registry()["fixtures"]["judgment_context_measurement"]


def _envelope(case: dict) -> dict:
    return {
        "lifecycle_boundaries": case["lifecycle_boundaries"],
        "open_failure_conditions": case["open_failure_conditions"],
    }


def _activation_parity_disagreements(
    root: Path,
    *,
    cli_subcommands: set[str],
    mcp_tools: set[str],
) -> list[str]:
    """Report every surface that disagrees with the declared activation state.

    One checker, so the assertion that the surfaces agree and the assertion that
    a disagreement is caught exercise the same code. It reads the registry from
    ``root`` and the exposure from its arguments, so a fixture can stage either
    side without touching the repository.
    """
    registry = json.loads(
        (root / "protocol" / "risk-and-practice-contract.json").read_text(
            encoding="utf-8"
        )
    )
    declared = registry["judgment"]["activation_state"]
    state = declared["state"]
    disagreements: list[str] = []

    for surface in registry["authoritative_surfaces"]:
        if "judgment" not in surface["path"]:
            continue
        if surface["activation"] != state:
            disagreements.append(f"registry-surface:{surface['path']}")
        elif state == "active" and not (root / surface["path"]).exists():
            disagreements.append(f"missing-surface:{surface['path']}")

    for surface in declared["parity_surfaces"]:
        if surface["kind"] == "document":
            path = root / surface["path"]
            if not path.exists():
                disagreements.append(f"missing-document:{surface['path']}")
                continue
            text = path.read_text(encoding="utf-8")
            if state == "active":
                if surface["required_when_active"] not in text:
                    disagreements.append(f"{surface['id']}:not-stated")
                for phrase in declared["forbidden_when_active"]:
                    if phrase in text:
                        disagreements.append(f"{surface['id']}:contradicted")
                        break
        elif surface["kind"] == "cli-subcommand":
            if (surface["name"] in cli_subcommands) != (state == "active"):
                disagreements.append(f"{surface['id']}:not-exposed")
        elif surface["kind"] == "mcp-tool":
            if (surface["name"] in mcp_tools) != (state == "active"):
                disagreements.append(f"{surface['id']}:not-exposed")
        else:  # pragma: no cover - an undeclared kind is itself a disagreement
            disagreements.append(f"{surface['id']}:unknown-kind")

    return disagreements


def _staged_parity_root(tmp: Path) -> Path:
    """Copy every parity surface so a fixture can make one of them disagree."""
    root = tmp / "repo"
    registry = _registry()
    paths = [
        "protocol/risk-and-practice-contract.json",
        *(
            surface["path"]
            for surface in registry["judgment"]["activation_state"]["parity_surfaces"]
            if surface["kind"] == "document"
        ),
        *(
            surface["path"]
            for surface in registry["authoritative_surfaces"]
            if "judgment" in surface["path"]
        ),
    ]
    for relative in paths:
        source = REPO_ROOT / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)
    return root


def _staged_protocol_root(tmp: Path) -> Path:
    """Copy the authored guidance body so a fixture can corrupt its own copy."""
    root = tmp / "protocol"
    (root / "judgment").mkdir(parents=True)
    shutil.copy2(
        REPO_ROOT / "protocol" / "judgment" / "failure-signal.md",
        root / "judgment" / "failure-signal.md",
    )
    return root


class CentralGuidanceBodyTests(unittest.TestCase):
    """Exactly one bounded contract, shared by every card."""

    def test_one_body_is_authored_and_every_card_references_it(self) -> None:
        judgment = _judgment()
        declared = judgment["guidance_body"]

        self.assertEqual(declared["authored_body_count"], 1)
        authored = sorted(
            (REPO_ROOT / "protocol" / "judgment").glob("*.md")
        )
        self.assertEqual(
            [path.name for path in authored], ["failure-signal.md"]
        )
        for card in judgment["cards"]:
            with self.subTest(card=card["id"]):
                self.assertEqual(card["guidance"], declared["guidance_id"])
                self.assertEqual(
                    card["body_budget_bytes"], declared["body_budget_bytes"]
                )

    def test_the_authored_body_matches_its_declared_identity_and_budget(self) -> None:
        declared = _judgment()["guidance_body"]
        raw = (REPO_ROOT / "protocol" / declared["body_ref"]).read_bytes()

        self.assertEqual(
            f"sha256:{hashlib.sha256(raw).hexdigest()}",
            declared["body_content_identity"],
        )
        self.assertLessEqual(len(raw), declared["body_budget_bytes"])
        self.assertEqual(
            len(raw), _measurement()["authored_guidance_bytes"]
        )

    def test_the_body_projects_the_central_grammar_without_adding_one(self) -> None:
        judgment = _judgment()
        grammar = judgment["failure_signal_grammar"]
        body = (
            REPO_ROOT / "protocol" / judgment["guidance_body"]["body_ref"]
        ).read_text(encoding="utf-8")
        headings = re.findall(r"^##\s+(.+?)\s*$", body, re.MULTILINE)
        identities = [
            "-".join(item.casefold().replace("-", " ").split())
            for item in headings
        ]

        self.assertEqual(identities, [item["id"] for item in grammar])
        for element in grammar:
            with self.subTest(element=element["id"]):
                self.assertIn(element["prompt"], body)

    def test_no_card_carries_prose_of_its_own(self) -> None:
        judgment = _judgment()
        card_fields = {field for card in judgment["cards"] for field in card}

        self.assertNotIn("body_ref", card_fields)
        self.assertNotIn("body_content_identity", card_fields)
        self.assertNotIn("guidance_body", card_fields)


class ActivationTests(unittest.TestCase):
    """A card activates only at its own boundary, with its own failure open."""

    def test_boundary_and_open_failure_are_both_required(self) -> None:
        from cli.judgment_guidance import select_judgment_guidance

        for card in _judgment()["cards"]:
            with self.subTest(card=card["id"]):
                both = select_judgment_guidance(
                    {
                        "lifecycle_boundaries": [card["boundary_id"]],
                        "open_failure_conditions": [card["failure_id"]],
                    }
                )
                boundary_only = select_judgment_guidance(
                    {
                        "lifecycle_boundaries": [card["boundary_id"]],
                        "open_failure_conditions": [],
                    }
                )
                failure_only = select_judgment_guidance(
                    {
                        "lifecycle_boundaries": [],
                        "open_failure_conditions": [card["failure_id"]],
                    }
                )

                self.assertEqual(both["outcome"], "active")
                self.assertEqual(
                    [item["card_id"] for item in both["active_cards"]], [card["id"]]
                )
                self.assertEqual(boundary_only["outcome"], "none")
                self.assertEqual(boundary_only["loaded_guidance_bytes"], 0)
                self.assertEqual(failure_only["outcome"], "none")
                self.assertEqual(failure_only["loaded_guidance_bytes"], 0)

    def test_a_failure_open_outside_its_own_boundary_activates_nothing(self) -> None:
        from cli.judgment_guidance import select_judgment_guidance

        cards = _judgment()["cards"]
        for card in cards:
            others = [item for item in cards if item["id"] != card["id"]]
            with self.subTest(card=card["id"]):
                result = select_judgment_guidance(
                    {
                        "lifecycle_boundaries": [card["boundary_id"]],
                        "open_failure_conditions": [
                            item["failure_id"] for item in others
                        ],
                    }
                )
                self.assertEqual(result["outcome"], "none")
                self.assertEqual(result["guidance_bodies_loaded"], 0)

    def test_activation_is_order_independent_and_deduplicated(self) -> None:
        from cli.judgment_guidance import select_judgment_guidance

        cards = _judgment()["cards"]
        forward = select_judgment_guidance(
            {
                "lifecycle_boundaries": [item["boundary_id"] for item in cards],
                "open_failure_conditions": [item["failure_id"] for item in cards],
            }
        )
        reversed_and_repeated = select_judgment_guidance(
            {
                "lifecycle_boundaries": [
                    item["boundary_id"] for item in reversed(cards)
                ]
                * 2,
                "open_failure_conditions": [
                    item["failure_id"] for item in reversed(cards)
                ]
                * 2,
            }
        )

        self.assertEqual(forward, reversed_and_repeated)
        self.assertEqual(
            [item["card_id"] for item in forward["active_cards"]],
            [card["id"] for card in cards],
        )

    def test_an_empty_or_legacy_envelope_is_a_valid_none(self) -> None:
        from cli.judgment_guidance import select_judgment_guidance

        for envelope in ({}, {"lifecycle_boundaries": [], "open_failure_conditions": []}):
            with self.subTest(envelope=envelope):
                result = select_judgment_guidance(envelope)
                self.assertEqual(result["outcome"], "none")
                self.assertIsNone(result["error"])
                self.assertIsNone(result["body"])
                self.assertEqual(result["loaded_guidance_bytes"], 0)
                self.assertEqual(len(result["inactive_cards"]), 4)


class IndependenceTests(unittest.TestCase):
    """No implicit activation edge from risk classification or pack selection."""

    def test_a_risk_or_pack_fact_is_a_fail_closed_input_error(self) -> None:
        from cli.judgment_guidance import select_judgment_guidance

        for forbidden in _judgment()["forbidden_activation_inputs"]:
            with self.subTest(fact=forbidden):
                result = select_judgment_guidance(
                    {
                        "lifecycle_boundaries": ["delivery-and-closeout"],
                        "open_failure_conditions": ["artifact-mistaken-for-outcome"],
                        forbidden: "critical",
                    }
                )
                self.assertEqual(result["outcome"], "invalid")
                self.assertEqual(
                    result["error"]["code"], "judgment-envelope-forbidden-input"
                )
                self.assertEqual(result["loaded_guidance_bytes"], 0)
                self.assertIsNone(result["body"])

    def test_every_risk_and_pack_identifier_is_refused_by_name(self) -> None:
        registry = _registry()
        forbidden = set(registry["judgment"]["forbidden_activation_inputs"])
        shared = {"contract_id", "contract_version"}
        risk_fields = set(registry["risk"]["classification"]["result_fields"]) - shared
        pack_facts = {item["id"] for item in registry["packs"]["envelope_facts"]}

        self.assertTrue(risk_fields.issubset(forbidden))
        self.assertTrue(pack_facts.issubset(forbidden))
        self.assertIn("pack_id", forbidden)
        self.assertFalse(
            forbidden.intersection(
                {"lifecycle_boundaries", "open_failure_conditions"}
            )
        )

    def test_an_undeclared_fact_is_rejected_rather_than_ignored(self) -> None:
        from cli.judgment_guidance import select_judgment_guidance

        result = select_judgment_guidance(
            {
                "lifecycle_boundaries": ["delivery-and-closeout"],
                "open_failure_conditions": ["artifact-mistaken-for-outcome"],
                "project_history": ["TASK-04-001"],
            }
        )

        self.assertEqual(result["outcome"], "invalid")
        self.assertEqual(result["error"]["code"], "judgment-envelope-invalid")

    def test_activation_never_derives_or_reports_a_band_or_a_pack(self) -> None:
        from cli.judgment_guidance import select_judgment_guidance

        result = select_judgment_guidance(
            {
                "lifecycle_boundaries": ["evidence-and-review-gate"],
                "open_failure_conditions": ["evidence-self-certified-or-missing"],
            }
        )
        emitted = set(result)
        registry = _registry()

        self.assertEqual(emitted, set(registry["judgment"]["result_fields"]))
        for banned in ("band", "pack_id", "review_expectation", "operator_gate"):
            with self.subTest(field=banned):
                self.assertNotIn(banned, emitted)

    def test_pack_selection_and_risk_results_are_unchanged_by_activation(self) -> None:
        from cli.judgment_guidance import select_judgment_guidance
        from cli.practice_packs import select_practice_pack
        from cli.risk_contract import classify_risk

        envelope = {
            "primary_outcomes": ["software-behavior-change"],
            "artifact_kinds": ["executable-behavior"],
            "incidental_terms": [],
            "exclusions": [],
            "lifecycle_substrate_activities": [],
            "domain_scopes": [],
            "authorized_profile_hint": None,
        }
        observations = [
            {"observation": "consequence-reach", "state": "project-internal",
             "supporting_fact": "fixture:reach"},
            {"observation": "reversibility", "state": "direct-undo",
             "supporting_fact": "fixture:undo"},
            {"observation": "authority", "state": "covered",
             "supporting_fact": "fixture:authority"},
            {"observation": "ambiguity", "state": "confirmed",
             "supporting_fact": "fixture:inputs"},
            {"observation": "evidence-coverage", "state": "deterministic",
             "supporting_fact": "fixture:proof"},
        ]

        pack_before = select_practice_pack(envelope)
        risk_before = classify_risk(observations)
        card = select_judgment_guidance(
            {
                "lifecycle_boundaries": ["delivery-and-closeout"],
                "open_failure_conditions": ["artifact-mistaken-for-outcome"],
            }
        )
        pack_after = select_practice_pack(envelope)
        risk_after = classify_risk(observations)

        self.assertEqual(card["outcome"], "active")
        self.assertEqual(pack_before, pack_after)
        self.assertEqual(risk_before, risk_after)
        self.assertEqual(pack_after["pack_id"], "software-delivery")
        self.assertEqual(risk_after["band"], "bounded")


class MeasuredContextTests(unittest.TestCase):
    """Fixed-input measurements, recomputed rather than asserted."""

    def test_every_measurement_case_resolves_to_its_recorded_bytes(self) -> None:
        from cli.judgment_guidance import select_judgment_guidance

        measurement = _measurement()
        universe = measurement["authored_guidance_bytes"]
        for case in measurement["cases"]:
            with self.subTest(case=case["id"]):
                result = select_judgment_guidance(_envelope(case))
                self.assertEqual(result["outcome"], case["expected_outcome"])
                self.assertEqual(
                    [item["card_id"] for item in result["active_cards"]],
                    case["expected_cards"],
                )
                self.assertEqual(
                    result["loaded_guidance_bytes"], case["active_guidance_bytes"]
                )
                self.assertEqual(
                    case["active_guidance_bytes"] + case["excluded_guidance_bytes"],
                    universe,
                )

    def test_measurement_cases_reuse_the_shared_task_envelope_fixtures(self) -> None:
        registry = _registry()
        envelopes = {
            item["id"]: item for item in registry["fixtures"]["task_envelopes"]
        }
        referenced = 0
        for case in _measurement()["cases"]:
            envelope_id = case["task_envelope"]
            if envelope_id is None:
                continue
            referenced += 1
            with self.subTest(case=case["id"]):
                envelope = envelopes[envelope_id]
                self.assertEqual(
                    case["lifecycle_boundaries"], envelope["lifecycle_boundaries"]
                )
                self.assertEqual(
                    case["open_failure_conditions"],
                    envelope["open_failure_conditions"],
                )
                self.assertEqual(
                    case["expected_cards"], envelope["expected_judgment_cards"]
                )
        self.assertEqual(referenced, len(envelopes))

    def test_activating_every_card_loads_one_body_once(self) -> None:
        from cli.judgment_guidance import select_judgment_guidance

        measurement = _measurement()
        comparison = measurement["per_card_body_comparison"]
        cards = _judgment()["cards"]
        result = select_judgment_guidance(
            {
                "lifecycle_boundaries": [item["boundary_id"] for item in cards],
                "open_failure_conditions": [item["failure_id"] for item in cards],
            }
        )
        body_bytes = measurement["authored_guidance_bytes"]

        self.assertEqual(result["cards_active"], comparison["cards_at_peak"])
        self.assertEqual(result["guidance_bodies_loaded"], 1)
        self.assertEqual(result["loaded_guidance_bytes"], body_bytes)
        self.assertEqual(
            comparison["per_card_body_bytes_at_peak"],
            body_bytes * comparison["cards_at_peak"],
        )
        self.assertEqual(
            comparison["avoided_duplicate_bytes_at_peak"],
            comparison["per_card_body_bytes_at_peak"] - body_bytes,
        )
        self.assertEqual(
            measurement["peak_active_guidance_bytes"],
            result["loaded_guidance_bytes"],
        )
        self.assertEqual(measurement["per_card_authored_bytes"], 0)

    def test_the_receipt_recomputes_from_the_emitted_result(self) -> None:
        from cli.judgment_guidance import select_judgment_guidance

        for envelope, expected_bodies in (
            (
                {
                    "lifecycle_boundaries": ["migration-install-restart"],
                    "open_failure_conditions": [
                        "mixed-version-or-unproven-running-state"
                    ],
                },
                1,
            ),
            ({"lifecycle_boundaries": ["migration-install-restart"]}, 0),
        ):
            with self.subTest(bodies=expected_bodies):
                result = select_judgment_guidance(envelope)
                receipt = result["context_receipt"]
                projection = {
                    field: (None if field == "body" else value)
                    for field, value in result.items()
                    if field != "context_receipt"
                }
                canonical = json.dumps(
                    projection,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )

                self.assertEqual(
                    receipt["routing_metadata_bytes"],
                    len(canonical.encode("utf-8")),
                )
                self.assertEqual(receipt["central_bodies_loaded"], expected_bodies)
                self.assertEqual(receipt["duplicate_guidance_bytes"], 0)
                self.assertEqual(receipt["inactive_boundary_bytes"], 0)
                self.assertEqual(
                    set(receipt),
                    {
                        field["id"]
                        for field in _judgment()["context_receipt"]["fields"]
                    },
                )

    def test_the_recorded_prior_baseline_describes_the_before_state(self) -> None:
        prior = _measurement()["prior_baseline"]

        self.assertEqual(prior["authored_guidance_bodies"], 0)
        self.assertEqual(prior["authored_guidance_bytes"], 0)
        self.assertEqual(prior["runtime_activation_surfaces"], 0)
        self.assertEqual(
            prior["active_guidance_bytes_at_an_applicable_boundary"],
            prior["active_guidance_bytes_at_an_unrelated_boundary"],
        )


class BodyFailClosedTests(unittest.TestCase):
    """A body that is not the authored one loads nothing and leaks nothing."""

    def _select(self, root: Path) -> dict:
        from cli.judgment_guidance import select_judgment_guidance

        return select_judgment_guidance(
            {
                "lifecycle_boundaries": ["requirements-and-intent"],
                "open_failure_conditions": ["inferred-intent-not-confirmed"],
            },
            protocol_root=root,
        )

    def test_a_valid_staged_copy_still_loads(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = _staged_protocol_root(Path(raw))
            result = self._select(root)

        self.assertEqual(result["outcome"], "active")
        self.assertEqual(
            result["loaded_guidance_bytes"],
            _measurement()["authored_guidance_bytes"],
        )

    def test_missing_edited_oversized_and_non_utf8_bodies_fail_closed(self) -> None:
        declared = _judgment()["guidance_body"]
        cases = {
            "judgment-body-missing": lambda path: path.unlink(),
            "judgment-body-stale": lambda path: path.write_bytes(
                path.read_bytes().replace(b"Failure Signal", b"Failure  Signal")
            ),
            "judgment-body-oversized": lambda path: path.write_bytes(
                path.read_bytes() + b"x" * declared["body_budget_bytes"]
            ),
            "judgment-body-invalid-utf8": lambda path: path.write_bytes(b"\xff\xfe"),
            "judgment-body-invalid": lambda path: path.write_bytes(
                b"# Failure Signal\n\nNo header at all.\n"
            ),
        }
        for code, corrupt in cases.items():
            with self.subTest(code=code), tempfile.TemporaryDirectory() as raw:
                root = _staged_protocol_root(Path(raw))
                corrupt(root / declared["body_ref"])
                result = self._select(root)

                self.assertEqual(result["outcome"], "invalid")
                self.assertEqual(result["error"]["code"], code)
                self.assertEqual(result["loaded_guidance_bytes"], 0)
                self.assertEqual(result["guidance_bodies_loaded"], 0)
                self.assertIsNone(result["body"])
                self.assertNotIn("Name the claim", json.dumps(result))

    def test_a_body_outside_the_judgment_directory_fails_closed(self) -> None:
        declared = _judgment()["guidance_body"]
        with tempfile.TemporaryDirectory() as raw:
            root = _staged_protocol_root(Path(raw))
            escaped = root / "escaped.md"
            escaped.write_bytes(
                (REPO_ROOT / "protocol" / declared["body_ref"]).read_bytes()
            )
            target = root / declared["body_ref"]
            target.unlink()
            target.symlink_to(escaped)
            result = self._select(root)

        self.assertEqual(result["outcome"], "invalid")
        self.assertEqual(result["error"]["code"], "judgment-body-out-of-bounds")
        self.assertIsNone(result["body"])

    def test_every_declared_failure_code_is_reachable_or_contract_scoped(self) -> None:
        declared = set(_judgment()["failure_codes"])
        source = (REPO_ROOT / "cli" / "judgment_guidance.py").read_text(
            encoding="utf-8"
        )
        for code in declared:
            with self.subTest(code=code):
                self.assertIn(f'"{code}"', source)


class NoParallelGrammarTests(unittest.TestCase):
    """One owner for the grammar; no score and no repeated cross-model prompt."""

    def test_only_the_registry_and_its_one_body_own_the_element_prompts(self) -> None:
        judgment = _judgment()
        # The registry owns the prompts; its plain-language projection and the one
        # authored body restate them. Nothing else may.
        owners = {
            REGISTRY_PATH,
            PROJECTION_PATH,
            REPO_ROOT / "protocol" / judgment["guidance_body"]["body_ref"],
        }
        searched = [
            *(REPO_ROOT / "protocol").rglob("*.md"),
            *(REPO_ROOT / "templates").rglob("*.md"),
            *(REPO_ROOT / "skills").rglob("*.md"),
        ]
        for element in judgment["failure_signal_grammar"]:
            for path in searched:
                if path in owners:
                    continue
                with self.subTest(element=element["id"], path=path.name):
                    self.assertNotIn(
                        element["prompt"],
                        path.read_text(encoding="utf-8"),
                    )

    def test_pack_references_stay_single_sentence_pointers(self) -> None:
        recorded = _measurement()["pack_grammar_references"]
        pattern = re.compile(
            r"Escalate with the failure-signal grammar: the unverified claim, the "
            r"missing authority or evidence, the consequence of proceeding, and the "
            r"decision or proof required\."
        )
        sites = 0
        for path in sorted((REPO_ROOT / "protocol" / "packs").glob("*.md")):
            text = path.read_text(encoding="utf-8")
            found = pattern.findall(text)
            sites += len(found)
            with self.subTest(pack=path.stem):
                self.assertLessEqual(len(found), 1)
                for match in found:
                    self.assertEqual(
                        len(match.encode("utf-8")),
                        recorded["reference_bytes_each"],
                    )

        self.assertEqual(sites, recorded["reference_sites"])
        self.assertEqual(
            sites * recorded["reference_bytes_each"],
            recorded["authored_reference_bytes"],
        )
        self.assertEqual(recorded["parallel_grammar_tables"], 0)

    def test_no_numeric_score_or_repeated_cross_model_prompt_is_introduced(self) -> None:
        banned = re.compile(
            r"\b\d{1,3}\s?%|\bconfidence\s+(?:score|percent)|\bsecond model\b",
            re.IGNORECASE,
        )
        for path in (
            REPO_ROOT / "protocol" / _judgment()["guidance_body"]["body_ref"],
            REPO_ROOT / "cli" / "judgment_guidance.py",
            REPO_ROOT / "cli" / "commands" / "select_judgment_guidance.py",
        ):
            with self.subTest(path=path.name):
                self.assertIsNone(banned.search(path.read_text(encoding="utf-8")))


class ActivationStateParityTests(unittest.TestCase):
    """One activation state, stated identically by every surface that carries it.

    The mechanism was described as defined-but-inactive while the registry
    surface state, the workflow procedure, the templates, and the CLI/MCP
    surfaces already activated it. These checks make that class of divergence a
    failure instead of a reading.
    """

    def _exposure(self) -> tuple[set[str], set[str]]:
        from cli.main import SUBCOMMANDS
        from mcp_server import server

        server._TOOL_CACHE = None
        return set(SUBCOMMANDS), {tool["name"] for tool in server.list_tools()}

    def test_every_parity_surface_states_the_declared_activation_state(self) -> None:
        declared = _judgment()["activation_state"]
        cli_subcommands, mcp_tools = self._exposure()

        self.assertEqual(declared["state"], "active")
        self.assertEqual(
            _activation_parity_disagreements(
                REPO_ROOT, cli_subcommands=cli_subcommands, mcp_tools=mcp_tools
            ),
            [],
        )

    def test_the_declaration_covers_every_required_surface(self) -> None:
        declared = _judgment()["activation_state"]
        surfaces = {surface["id"]: surface for surface in declared["parity_surfaces"]}

        self.assertEqual(
            set(surfaces),
            {
                "invariant-protocol",
                "contract-projection",
                "task-envelope-template",
                "assignment-prompt-template",
                "workflow-procedure",
                "cli-subcommand",
                "mcp-tool",
            },
        )
        self.assertEqual(
            surfaces["invariant-protocol"]["path"], "protocol/CONVENTIONS.md"
        )
        self.assertTrue(declared["forbidden_when_active"])

    def test_a_restored_inactive_claim_is_reported_as_a_disagreement(self) -> None:
        declared = _judgment()["activation_state"]
        cli_subcommands, mcp_tools = self._exposure()
        for phrase in declared["forbidden_when_active"]:
            with self.subTest(phrase=phrase), tempfile.TemporaryDirectory() as raw:
                root = _staged_parity_root(Path(raw))
                conventions = root / "protocol" / "CONVENTIONS.md"
                conventions.write_text(
                    conventions.read_text(encoding="utf-8")
                    + f"\nJudgment cards remain {phrase} today.\n",
                    encoding="utf-8",
                )
                self.assertIn(
                    "invariant-protocol:contradicted",
                    _activation_parity_disagreements(
                        root,
                        cli_subcommands=cli_subcommands,
                        mcp_tools=mcp_tools,
                    ),
                )

    def test_a_silenced_document_surface_is_reported_as_a_disagreement(self) -> None:
        declared = _judgment()["activation_state"]
        cli_subcommands, mcp_tools = self._exposure()
        documents = [
            surface
            for surface in declared["parity_surfaces"]
            if surface["kind"] == "document"
        ]

        self.assertEqual(len(documents), 5)
        for surface in documents:
            with self.subTest(surface=surface["id"]), tempfile.TemporaryDirectory() as raw:
                root = _staged_parity_root(Path(raw))
                path = root / surface["path"]
                # Silencing the surface means removing every occurrence, not
                # the first one: a document may state its required phrase in a
                # heading and again in the prose that introduces it, and one
                # surviving mention still leaves the surface stated.
                path.write_text(
                    path.read_text(encoding="utf-8").replace(
                        surface["required_when_active"], ""
                    ),
                    encoding="utf-8",
                )
                self.assertIn(
                    f"{surface['id']}:not-stated",
                    _activation_parity_disagreements(
                        root,
                        cli_subcommands=cli_subcommands,
                        mcp_tools=mcp_tools,
                    ),
                )

    def test_a_pending_registry_surface_is_reported_as_a_disagreement(self) -> None:
        cli_subcommands, mcp_tools = self._exposure()
        with tempfile.TemporaryDirectory() as raw:
            root = _staged_parity_root(Path(raw))
            path = root / "protocol" / "risk-and-practice-contract.json"
            registry = json.loads(path.read_text(encoding="utf-8"))
            flipped = next(
                surface
                for surface in registry["authoritative_surfaces"]
                if "judgment" in surface["path"]
            )
            flipped["activation"] = "pending"
            path.write_text(json.dumps(registry, indent=2), encoding="utf-8")

            self.assertIn(
                f"registry-surface:{flipped['path']}",
                _activation_parity_disagreements(
                    root, cli_subcommands=cli_subcommands, mcp_tools=mcp_tools
                ),
            )

    def test_withdrawn_cli_or_mcp_exposure_is_reported_as_a_disagreement(self) -> None:
        cli_subcommands, mcp_tools = self._exposure()

        self.assertIn(
            "cli-subcommand:not-exposed",
            _activation_parity_disagreements(
                REPO_ROOT,
                cli_subcommands=cli_subcommands - {"select-judgment-guidance"},
                mcp_tools=mcp_tools,
            ),
        )
        self.assertIn(
            "mcp-tool:not-exposed",
            _activation_parity_disagreements(
                REPO_ROOT,
                cli_subcommands=cli_subcommands,
                mcp_tools=mcp_tools - {"select_judgment_guidance"},
            ),
        )


class StoppingBehaviorTests(unittest.TestCase):
    """The admitted body holds the boundary; it does not just name four fields."""

    def _active_body(self, card: dict) -> str:
        from cli.judgment_guidance import select_judgment_guidance

        result = select_judgment_guidance(
            {
                "lifecycle_boundaries": [card["boundary_id"]],
                "open_failure_conditions": [card["failure_id"]],
            }
        )
        self.assertEqual(result["outcome"], "active")
        self.assertEqual(
            [item["card_id"] for item in result["active_cards"]], [card["id"]]
        )
        return result["body"]

    def test_the_admitted_body_carries_every_declared_stop_requirement(self) -> None:
        judgment = _judgment()
        contract = judgment["guidance_body"]["stop_contract"]
        body = self._active_body(judgment["cards"][0])

        self.assertEqual(
            [item["id"] for item in contract["requirements"]],
            [
                "activation-is-a-hold",
                "fields-bind-to-the-active-failure",
                "resume-authority-or-proof-is-named",
                "proceeding-requires-a-satisfied-requirement",
            ],
        )
        for requirement in contract["requirements"]:
            with self.subTest(requirement=requirement["id"]):
                self.assertTrue(requirement["requirement"].strip())
                self.assertIn(requirement["body_marker"], body)

    def test_dropping_a_stop_requirement_is_caught_rather_than_tolerated(self) -> None:
        """The four headings survive the mutation; the stopping behavior does not."""
        judgment = _judgment()
        contract = judgment["guidance_body"]["stop_contract"]
        body = self._active_body(judgment["cards"][0])
        for requirement in contract["requirements"]:
            with self.subTest(requirement=requirement["id"]):
                stripped = body.replace(requirement["body_marker"], "", 1)
                self.assertNotEqual(stripped, body)
                unmet = [
                    item["id"]
                    for item in contract["requirements"]
                    if item["body_marker"] not in stripped
                ]
                self.assertEqual(unmet, [requirement["id"]])
                # Heading structure alone still validates, which is the proxy the
                # corrective pass rejects: it cannot stand in for this check.
                headings = [
                    "-".join(item.casefold().replace("-", " ").split())
                    for item in re.findall(r"^##\s+(.+?)\s*$", stripped, re.MULTILINE)
                ]
                self.assertEqual(
                    headings,
                    [item["id"] for item in judgment["failure_signal_grammar"]],
                )

    def test_each_active_failure_binds_its_own_claim_and_resume_requirement(self) -> None:
        judgment = _judgment()
        cards = judgment["cards"]
        claims = [card["claim_to_name"] for card in cards]
        resumes = [card["resume_requirement"] for card in cards]

        self.assertEqual(len(set(claims)), len(cards))
        self.assertEqual(len(set(resumes)), len(cards))
        for card in cards:
            with self.subTest(card=card["id"]):
                body = self._active_body(card)
                self.assertIn(card["failure_id"], body)
                self.assertIn(card["claim_to_name"], body)
                self.assertIn(card["resume_requirement"], body)
        self.assertTrue(
            judgment["guidance_body"]["stop_contract"][
                "per_failure_binding_rule"
            ].strip()
        )

    def test_the_projection_states_the_hold_and_every_resume_requirement(self) -> None:
        text = PROJECTION_PATH.read_text(encoding="utf-8")

        self.assertIn("**An active card is a hold, not a reminder.**", text)
        for card in _judgment()["cards"]:
            with self.subTest(card=card["id"]):
                self.assertIn(card["resume_requirement"], text)

    def test_the_strengthened_body_stays_inside_its_declared_ceiling(self) -> None:
        declared = _judgment()["guidance_body"]
        measurement = _measurement()
        raw = (REPO_ROOT / "protocol" / declared["body_ref"]).read_bytes()

        self.assertEqual(len(raw), measurement["authored_guidance_bytes"])
        self.assertLessEqual(len(raw), declared["body_budget_bytes"])
        self.assertEqual(
            declared["body_budget_bytes"], measurement["guidance_budget_bytes"]
        )
        # A raised ceiling still has to be a ceiling, and the saving from one
        # central body has to be recomputed from the measured size.
        self.assertGreater(declared["body_budget_bytes"], len(raw))
        comparison = measurement["per_card_body_comparison"]
        self.assertEqual(comparison["central_body_bytes_at_peak"], len(raw))
        self.assertEqual(
            comparison["per_card_body_bytes_at_peak"],
            len(raw) * comparison["cards_at_peak"],
        )


class JudgmentSurfaceParityTests(unittest.TestCase):
    """One shared implementation behind the command line and the tool surface."""

    def _cli_arguments(self) -> list[str]:
        return [
            "--lifecycle-boundary", "migration-install-restart",
            "--open-failure-condition", "mixed-version-or-unproven-running-state",
        ]

    def test_cli_and_mcp_return_the_same_structured_result(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "cli",
                "select-judgment-guidance",
                *self._cli_arguments(),
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        cli_record = json.loads(completed.stdout)

        from mcp_server import server

        server._TOOL_CACHE = None
        mcp_result = server.call_tool(
            "select_judgment_guidance",
            {
                "lifecycle_boundary": ["migration-install-restart"],
                "open_failure_condition": [
                    "mixed-version-or-unproven-running-state"
                ],
            },
        )

        self.assertFalse(mcp_result["isError"])
        self.assertEqual(mcp_result["structuredContent"]["records"], [cli_record])
        self.assertEqual(cli_record["outcome"], "active")
        self.assertEqual(cli_record["cards_active"], 1)

    def test_an_invalid_envelope_exits_non_zero_without_a_body(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "cli",
                "select-judgment-guidance",
                "--lifecycle-boundary",
                "Not An Identity",
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 1)
        record = json.loads(completed.stdout)
        self.assertEqual(record["outcome"], "invalid")
        self.assertIsNone(record["body"])
        self.assertIn("judgment-envelope-invalid", completed.stderr)


class JudgmentProjectionTests(unittest.TestCase):
    """The templates, runbook, and plain-language projection carry the contract."""

    def test_templates_and_runbook_project_the_judgment_envelope(self) -> None:
        task = (REPO_ROOT / "templates" / "TASK.md").read_text(encoding="utf-8")
        prompt = (REPO_ROOT / "templates" / "PROMPT.md").read_text(encoding="utf-8")
        run_task = (REPO_ROOT / "skills" / "run-task.md").read_text(encoding="utf-8")

        self.assertIn("## Judgment envelope", task)
        self.assertIn("## Judgment guidance", prompt)
        self.assertIn("select-judgment-guidance", run_task)
        for fact in ("lifecycle-boundaries", "open-failure-conditions"):
            with self.subTest(fact=fact):
                self.assertIn(fact, task)

        # Authoring the result in Stage 1 is not carrying it into the handoff.
        # The runbook's prompt-content list is the only place that makes the
        # activated guidance reach an assignee, so it names the judgment result
        # exactly where the prompt template places it: after the risk result and
        # before the pack result.
        carried = [
            run_task.index("The exact `## Risk result`"),
            run_task.index("The exact `## Judgment guidance` result"),
            run_task.index("The exact `## Practice-pack result`"),
        ]
        self.assertEqual(carried, sorted(carried))

    def test_the_projection_names_the_active_judgment_surfaces(self) -> None:
        text = PROJECTION_PATH.read_text(encoding="utf-8")
        judgment = _judgment()

        self.assertIn("select-judgment-guidance", text)
        self.assertIn(judgment["guidance_body"]["body_ref"], text)
        self.assertIn(str(_measurement()["authored_guidance_bytes"]), text)
        # The projection quotes the peak active cost of the pack pointers. That
        # figure is measured from the pack bodies above, so the prose may not
        # carry a number the measurement does not support.
        self.assertIn(
            f"{_measurement()['pack_grammar_references']['peak_active_reference_bytes']}-byte",
            text,
        )
        for outcome in judgment["outcomes"]:
            with self.subTest(outcome=outcome["id"]):
                self.assertIn(f"`{outcome['id']}`", text)
        for field in judgment["context_receipt"]["fields"]:
            with self.subTest(field=field["id"]):
                self.assertIn(f"`{field['id']}`", text)

    def test_every_declared_judgment_surface_exists(self) -> None:
        registry = _registry()
        judgment_surfaces = [
            surface
            for surface in registry["authoritative_surfaces"]
            if "judgment" in surface["path"]
        ]

        self.assertTrue(judgment_surfaces)
        for surface in judgment_surfaces:
            with self.subTest(path=surface["path"]):
                self.assertEqual(surface["activation"], "active")
                self.assertTrue((REPO_ROOT / surface["path"]).exists())


if __name__ == "__main__":
    unittest.main()
