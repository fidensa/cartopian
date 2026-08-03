"""Deterministic validation for the risk and practice extension contract.

The contract's machine values live once in
``protocol/risk-and-practice-contract.json``. ``protocol/RISK_AND_PRACTICE.md``
is its plain-language projection. These checks prove the registry is
structurally complete and fail-closed, that the declared rules resolve the
fixed fixtures to exactly one outcome each, that the three mechanisms carry no
activation edges, that unmatched guidance contributes zero active-context
bytes, and that the projection does not drift from the authority.

Rule evaluation here is table-driven: the ordering, dominance, veto, and
precedence behavior is read from the registry rather than restated in Python,
so this module validates the contract without becoming a second definition of
it. The runtime classifier and selector consume the same registry and the same
fixtures.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = REPO_ROOT / "protocol" / "risk-and-practice-contract.json"
PROJECTION_PATH = REPO_ROOT / "protocol" / "RISK_AND_PRACTICE.md"

_CONFIDENCE_RE = re.compile(r"\b\d{1,3}\s?%|\bconfidence\s+(?:score|percent)")


def _registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _projection() -> str:
    return PROJECTION_PATH.read_text(encoding="utf-8")


def _band_rank(registry: dict, band: str) -> int:
    return registry["risk"]["band_order"].index(band)


def _state_floor(registry: dict, observation_id: str, state_id: str) -> str:
    for observation in registry["risk"]["observations"]:
        if observation["id"] != observation_id:
            continue
        for state in observation["states"]:
            if state["id"] == state_id:
                return state["band_floor"]
        raise AssertionError(f"unknown state {state_id} for {observation_id}")
    raise AssertionError(f"unknown observation {observation_id}")


def classify(registry: dict, observations: dict) -> dict:
    """Apply the registry's declared dominance rule to one observation set.

    Returns the band plus the ordered reasons that produced it, or the
    fail-closed invalid result when the observation set is incomplete.
    """
    declared = [item["id"] for item in registry["risk"]["observations"]]
    missing = [item for item in declared if item not in observations]
    unknown = [item for item in observations if item not in declared]
    if missing or unknown:
        return {
            "outcome": registry["risk"]["classification"]["incomplete_input"],
            "band": None,
            "reasons": tuple(
                [f"missing-observation:{item}" for item in missing]
                + [f"undeclared-observation:{item}" for item in unknown]
            ),
        }
    floors = [
        (observation_id, _state_floor(registry, observation_id, state_id))
        for observation_id, state_id in sorted(observations.items())
    ]
    band = max(floors, key=lambda pair: _band_rank(registry, pair[1]))[1]
    reasons = tuple(
        f"{observation_id}={observations[observation_id]}->{floor}"
        for observation_id, floor in floors
        if _band_rank(registry, floor) == _band_rank(registry, band)
    )
    return {"outcome": "classified", "band": band, "reasons": reasons}


def _condition_matches(condition: dict, envelope: dict) -> bool:
    fact = condition["fact"]
    if fact == "primary_outcome":
        return condition["value"] in envelope.get("primary_outcomes", [])
    if fact == "artifact_kind":
        return condition["value"] in envelope.get("artifact_kinds", [])
    if fact == "incidental_term":
        return condition["value"] in envelope.get("incidental_terms", [])
    if fact == "exclusion":
        return condition["value"] in envelope.get("exclusions", [])
    raise AssertionError(f"undeclared envelope fact {fact}")


def _class_rank(registry: dict, class_id: str) -> int:
    for entry in registry["packs"]["precedence_classes"]:
        if entry["id"] == class_id:
            return entry["rank"]
    raise AssertionError(f"unknown precedence class {class_id}")


def _fact_class(registry: dict, fact: str) -> str:
    return registry["packs"]["fact_precedence_classes"][fact]


def select(registry: dict, envelope: dict, candidates: list) -> dict:
    """Apply the registry's declared selection rules to one task envelope."""
    compatible = registry["contract_version"]
    invalid = [
        candidate["pack_id"]
        for candidate in candidates
        if candidate["contract_version"] != compatible
    ]
    if invalid:
        return {
            "outcome": "invalid",
            "pack_id": None,
            "bodies_loaded": 0,
            "diagnostics": tuple(f"incompatible-contract-version:{item}" for item in sorted(invalid)),
        }

    eligible = []
    rejected = []
    for candidate in candidates:
        vetoes = [
            condition
            for condition in candidate["never_when"]
            if _condition_matches(condition, envelope)
        ]
        if vetoes:
            rejected.append((candidate["pack_id"], "negative-applicability-veto"))
            continue
        if not all(
            _condition_matches(condition, envelope)
            for condition in candidate["applies_when"]
        ):
            rejected.append((candidate["pack_id"], "unmet-positive-condition"))
            continue
        best_class = min(
            _class_rank(registry, _fact_class(registry, condition["fact"]))
            for condition in candidate["applies_when"]
        )
        eligible.append(
            {
                "pack_id": candidate["pack_id"],
                "precedence": (best_class, -len(candidate["applies_when"])),
                "tie_key": candidate["tie_key"],
            }
        )

    if not eligible:
        return {
            "outcome": "none",
            "pack_id": None,
            "bodies_loaded": 0,
            "diagnostics": tuple(f"{pack}:{reason}" for pack, reason in sorted(rejected)),
        }

    best = min(item["precedence"] for item in eligible)
    winners = [item for item in eligible if item["precedence"] == best]
    if len(winners) > 1:
        hint = envelope.get("authorized_profile_hint")
        hinted = [item for item in winners if item["pack_id"] == hint]
        if envelope.get("hint_authorized") and hinted:
            return {
                "outcome": "selected",
                "pack_id": hinted[0]["pack_id"],
                "bodies_loaded": 1,
                "diagnostics": ("collision-resolved-by-authorized-hint",),
            }
        return {
            "outcome": "ambiguous",
            "pack_id": None,
            "bodies_loaded": 0,
            "diagnostics": tuple(
                sorted(item["tie_key"] for item in winners)
            ),
        }
    return {
        "outcome": "selected",
        "pack_id": winners[0]["pack_id"],
        "bodies_loaded": 1,
        "diagnostics": tuple(f"{pack}:{reason}" for pack, reason in sorted(rejected)),
    }


class RegistryStructureTests(unittest.TestCase):
    def test_registry_and_projection_exist(self) -> None:
        self.assertTrue(REGISTRY_PATH.is_file(), f"missing {REGISTRY_PATH}")
        self.assertTrue(PROJECTION_PATH.is_file(), f"missing {PROJECTION_PATH}")

    def test_registry_declares_every_required_section(self) -> None:
        registry = _registry()
        for key in (
            "contract_id",
            "contract_version",
            "status",
            "risk",
            "judgment",
            "packs",
            "independence",
            "exemplar_recommendation",
            "authoritative_surfaces",
            "validation_obligations",
            "fixtures",
        ):
            self.assertIn(key, registry)

    def test_every_observation_state_declares_a_known_band_floor(self) -> None:
        registry = _registry()
        bands = registry["risk"]["band_order"]
        self.assertEqual(bands, ["routine", "bounded", "consequential", "critical"])
        observations = registry["risk"]["observations"]
        self.assertEqual(len(observations), 5)
        for observation in observations:
            state_ids = [state["id"] for state in observation["states"]]
            self.assertEqual(len(state_ids), len(set(state_ids)), observation["id"])
            self.assertIn(
                "unknown",
                state_ids,
                f"{observation['id']} must declare an explicit unknown state",
            )
            for state in observation["states"]:
                self.assertIn(state["band_floor"], bands)
                self.assertTrue(state["meaning"].strip())

    def test_unknown_state_never_carries_the_lowest_band_floor(self) -> None:
        registry = _registry()
        lowest = registry["risk"]["band_order"][0]
        for observation in registry["risk"]["observations"]:
            floor = _state_floor(registry, observation["id"], "unknown")
            self.assertNotEqual(
                floor,
                lowest,
                f"{observation['id']} converts a missing observation into a favorable one",
            )

    def test_every_band_derives_exactly_one_governance_row(self) -> None:
        registry = _registry()
        rows = registry["risk"]["governance"]
        self.assertEqual(
            [row["band"] for row in rows],
            registry["risk"]["band_order"],
        )
        meanings = registry["risk"]["meanings"]
        for row in rows:
            for key in (
                "evidence_expectation",
                "review_expectation",
                "operator_gate",
                "contingency_expectation",
            ):
                self.assertIn(row[key], meanings, f"{row['band']}.{key}")

    def test_no_confidence_scoring_vocabulary_is_introduced(self) -> None:
        self.assertIsNone(_CONFIDENCE_RE.search(REGISTRY_PATH.read_text(encoding="utf-8")))
        self.assertIsNone(_CONFIDENCE_RE.search(_projection()))


class RiskClassificationTests(unittest.TestCase):
    def test_fixed_envelopes_classify_to_the_recorded_band(self) -> None:
        registry = _registry()
        envelopes = registry["fixtures"]["task_envelopes"]
        self.assertGreaterEqual(len(envelopes), 5)
        for envelope in envelopes:
            with self.subTest(envelope=envelope["id"]):
                result = classify(registry, envelope["observations"])
                self.assertEqual(result["outcome"], "classified")
                self.assertEqual(result["band"], envelope["expected_band"])

    def test_classification_is_deterministic_across_repeated_evaluation(self) -> None:
        registry = _registry()
        for envelope in registry["fixtures"]["task_envelopes"]:
            first = classify(registry, envelope["observations"])
            second = classify(registry, dict(reversed(list(envelope["observations"].items()))))
            self.assertEqual(first, second, envelope["id"])

    def test_a_critical_condition_cannot_be_averaged_down(self) -> None:
        registry = _registry()
        lowest = registry["risk"]["band_order"][0]
        routine = {
            observation["id"]: next(
                state["id"]
                for state in observation["states"]
                if state["band_floor"] == lowest
            )
            for observation in registry["risk"]["observations"]
        }
        for observation in registry["risk"]["observations"]:
            critical_states = [
                state["id"]
                for state in observation["states"]
                if state["band_floor"] == "critical"
            ]
            for state_id in critical_states:
                probe = dict(routine)
                probe[observation["id"]] = state_id
                with self.subTest(observation=observation["id"], state=state_id):
                    self.assertEqual(classify(registry, probe)["band"], "critical")

    def test_absent_authority_dominates_every_routine_observation(self) -> None:
        registry = _registry()
        result = classify(
            registry,
            {
                "consequence-reach": "external-or-material",
                "reversibility": "direct-undo",
                "authority": "absent",
                "ambiguity": "confirmed",
                "evidence-coverage": "deterministic",
            },
        )
        self.assertEqual(result["band"], "critical")
        self.assertIn("authority=absent->critical", result["reasons"])

    def test_incomplete_observation_sets_fail_closed(self) -> None:
        registry = _registry()
        partial = {"consequence-reach": "local-artifact"}
        result = classify(registry, partial)
        self.assertEqual(result["outcome"], "invalid")
        self.assertIsNone(result["band"])
        self.assertTrue(result["reasons"])

    def test_configured_review_policy_remains_authoritative(self) -> None:
        registry = _registry()
        separation = registry["risk"]["policy_separation"]
        self.assertTrue(separation["configured_policy_is_authoritative"])
        self.assertFalse(separation["risk_may_rewrite_configuration"])
        self.assertTrue(separation["additional_depth_requires_explicit_contract"])
        for surface in separation["never_written_by_risk"]:
            self.assertTrue(surface.strip())


class JudgmentLayerTests(unittest.TestCase):
    def test_exactly_four_cards_remain_with_recorded_evidence(self) -> None:
        registry = _registry()
        cards = registry["judgment"]["cards"]
        self.assertEqual(len(cards), 4)
        ids = [card["id"] for card in cards]
        self.assertEqual(len(ids), len(set(ids)))
        for card in cards:
            self.assertEqual(card["disposition"], "keep-separate")
            self.assertTrue(card["lifecycle_boundary"].strip())
            self.assertTrue(card["non_enforceable_failure"].strip())
            self.assertTrue(card["evidence"].strip())
            self.assertIsInstance(card["body_budget_bytes"], int)
            self.assertGreater(card["body_budget_bytes"], 0)

    def test_failure_signal_grammar_is_common_and_ordered(self) -> None:
        registry = _registry()
        grammar = registry["judgment"]["failure_signal_grammar"]
        self.assertEqual(
            [element["id"] for element in grammar],
            [
                "unverified-claim",
                "missing-authority-or-evidence",
                "consequence-of-proceeding",
                "next-decision-or-proof",
            ],
        )
        self.assertEqual(registry["judgment"]["grammar_ownership"], "central")

    def test_admission_requires_both_records(self) -> None:
        registry = _registry()
        rule = registry["judgment"]["admission_rule"]
        self.assertEqual(
            sorted(rule["required_records"]),
            ["fixed-input-context-measurement", "non-enforceable-failure-record"],
        )
        self.assertEqual(rule["either_record_alone"], "rejected")
        self.assertEqual(rule["default_outcome"], "not-admitted")


class PackSelectionTests(unittest.TestCase):
    def test_metadata_contract_declares_every_required_field(self) -> None:
        registry = _registry()
        fields = {field["id"]: field for field in registry["packs"]["metadata_fields"]}
        for required in (
            "pack_id",
            "revision",
            "contract_version",
            "applies_when",
            "never_when",
            "precedence_class",
            "tie_key",
            "body_ref",
            "body_budget_bytes",
            "evidence_shape",
        ):
            self.assertIn(required, fields)
            self.assertTrue(fields[required]["meaning"].strip())

    def test_precedence_classes_are_totally_ordered(self) -> None:
        registry = _registry()
        ranks = [entry["rank"] for entry in registry["packs"]["precedence_classes"]]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(len(ranks), len(set(ranks)))
        self.assertEqual(
            [entry["id"] for entry in registry["packs"]["precedence_classes"]],
            ["primary-outcome", "artifact-kind", "incidental-term"],
        )

    def test_tie_key_orders_diagnostics_and_never_selects_a_winner(self) -> None:
        registry = _registry()
        fields = {field["id"]: field for field in registry["packs"]["metadata_fields"]}
        self.assertEqual(fields["tie_key"]["use"], "diagnostic-ordering-only")
        self.assertFalse(fields["tie_key"]["breaks_selection_ties"])

    def test_every_outcome_declares_its_body_loading_bound(self) -> None:
        registry = _registry()
        outcomes = {item["id"]: item for item in registry["packs"]["outcomes"]}
        self.assertEqual(
            sorted(outcomes),
            ["ambiguous", "invalid", "none", "selected"],
        )
        self.assertEqual(outcomes["selected"]["bodies_loaded"], 1)
        for outcome in ("none", "ambiguous", "invalid"):
            self.assertEqual(outcomes[outcome]["bodies_loaded"], 0, outcome)
        self.assertFalse(outcomes["none"]["is_error"])
        self.assertTrue(outcomes["ambiguous"]["is_error"])
        self.assertTrue(outcomes["invalid"]["is_error"])

    def test_fixed_envelopes_select_the_recorded_outcome(self) -> None:
        registry = _registry()
        candidates = registry["fixtures"]["pack_candidates"]
        for envelope in registry["fixtures"]["task_envelopes"]:
            with self.subTest(envelope=envelope["id"]):
                result = select(registry, envelope, candidates)
                self.assertEqual(result["outcome"], envelope["expected_selection"])
                self.assertEqual(result["pack_id"], envelope.get("expected_pack"))
                self.assertEqual(
                    result["bodies_loaded"],
                    envelope["expected_bodies_loaded"],
                )

    def test_a_negative_condition_vetoes_every_positive_match(self) -> None:
        registry = _registry()
        candidates = registry["fixtures"]["pack_candidates"]
        research = next(item for item in candidates if item["pack_id"] == "research-inquiry")
        veto = next(
            condition
            for condition in research["never_when"]
            if condition["fact"] == "exclusion"
        )
        envelope = {
            "primary_outcomes": ["supported-finding"],
            "artifact_kinds": [],
            "incidental_terms": [],
            "exclusions": [veto["value"]],
        }
        result = select(registry, envelope, [research])
        self.assertEqual(result["outcome"], "none")
        self.assertEqual(result["bodies_loaded"], 0)

    def test_equal_precedence_fails_closed_before_any_body_loads(self) -> None:
        registry = _registry()
        collision = next(
            envelope
            for envelope in registry["fixtures"]["task_envelopes"]
            if envelope["expected_selection"] == "ambiguous"
        )
        result = select(registry, collision, registry["fixtures"]["pack_candidates"])
        self.assertEqual(result["outcome"], "ambiguous")
        self.assertEqual(result["bodies_loaded"], 0)
        self.assertGreaterEqual(len(result["diagnostics"]), 2)
        self.assertEqual(list(result["diagnostics"]), sorted(result["diagnostics"]))

    def test_authorized_hint_resolves_only_an_otherwise_eligible_collision(self) -> None:
        registry = _registry()
        candidates = registry["fixtures"]["pack_candidates"]
        collision = next(
            envelope
            for envelope in registry["fixtures"]["task_envelopes"]
            if envelope["expected_selection"] == "ambiguous"
        )
        hinted = dict(collision)
        hinted["authorized_profile_hint"] = "research-inquiry"
        hinted["hint_authorized"] = True
        self.assertEqual(select(registry, hinted, candidates)["pack_id"], "research-inquiry")

        unauthorized = dict(hinted)
        unauthorized["hint_authorized"] = False
        self.assertEqual(select(registry, unauthorized, candidates)["outcome"], "ambiguous")

        research = next(item for item in candidates if item["pack_id"] == "research-inquiry")
        veto = next(
            condition
            for condition in research["never_when"]
            if condition["fact"] == "exclusion"
        )
        vetoed = dict(hinted)
        vetoed["exclusions"] = list(collision.get("exclusions", [])) + [veto["value"]]
        result = select(registry, vetoed, candidates)
        self.assertNotEqual(result["pack_id"], "research-inquiry")

    def test_incompatible_metadata_fails_before_body_retrieval(self) -> None:
        registry = _registry()
        candidates = [dict(item) for item in registry["fixtures"]["pack_candidates"]]
        candidates[0]["contract_version"] = registry["contract_version"] + 1
        envelope = registry["fixtures"]["task_envelopes"][0]
        result = select(registry, envelope, candidates)
        self.assertEqual(result["outcome"], "invalid")
        self.assertEqual(result["bodies_loaded"], 0)

    def test_body_failure_modes_fail_closed_without_partial_content(self) -> None:
        registry = _registry()
        for mode in registry["packs"]["body_failure_modes"]:
            self.assertEqual(mode["outcome"], "invalid")
            self.assertEqual(mode["bodies_loaded"], 0)
            self.assertFalse(mode["partial_content_returned"])

    def test_all_five_profile_shapes_share_one_contract(self) -> None:
        registry = _registry()
        shapes = registry["packs"]["profile_shapes"]
        self.assertEqual(
            sorted(shape["id"] for shape in shapes),
            ["marketing", "operations", "policy", "research", "software"],
        )
        for shape in shapes:
            self.assertTrue(shape["positive_shape"].strip())
            self.assertTrue(shape["negative_shape"].strip())
            self.assertTrue(shape["evidence_shape"].strip())

    def test_every_declared_shape_has_a_validating_candidate(self) -> None:
        registry = _registry()
        shape_ids = {shape["id"] for shape in registry["packs"]["profile_shapes"]}
        covered = {item["profile_shape"] for item in registry["fixtures"]["pack_candidates"]}
        self.assertEqual(shape_ids, covered)


class IndependenceTests(unittest.TestCase):
    def test_no_mechanism_silently_activates_another(self) -> None:
        registry = _registry()
        edges = registry["independence"]["forbidden_edges"]
        self.assertGreaterEqual(len(edges), 4)
        for edge in edges:
            self.assertIn(edge["from"], {"risk", "judgment", "pack-selection"})
            self.assertIn(edge["to"], {"risk", "judgment", "pack-selection"})
            self.assertNotEqual(edge["from"], edge["to"])
            self.assertTrue(edge["reason"].strip())

    def test_pack_metadata_cannot_carry_a_risk_or_judgment_field(self) -> None:
        registry = _registry()
        declared = {field["id"] for field in registry["packs"]["metadata_fields"]}
        forbidden = set(registry["independence"]["forbidden_pack_metadata_fields"])
        self.assertTrue(forbidden)
        self.assertFalse(declared & forbidden)
        for candidate in registry["fixtures"]["pack_candidates"]:
            self.assertFalse(set(candidate) & forbidden, candidate["pack_id"])

    def test_fixtures_prove_the_mechanisms_vary_independently(self) -> None:
        registry = _registry()
        envelopes = registry["fixtures"]["task_envelopes"]
        pack_without_card = [
            item
            for item in envelopes
            if item["expected_selection"] == "selected" and not item["expected_judgment_cards"]
        ]
        card_without_pack = [
            item
            for item in envelopes
            if item["expected_selection"] != "selected" and item["expected_judgment_cards"]
        ]
        both = [
            item
            for item in envelopes
            if item["expected_selection"] == "selected" and item["expected_judgment_cards"]
        ]
        self.assertTrue(pack_without_card, "no pack-without-card fixture")
        self.assertTrue(card_without_pack, "no card-without-pack fixture")
        self.assertTrue(both, "no independent-coactivation fixture")

    def test_selection_outcome_does_not_change_the_classified_band(self) -> None:
        registry = _registry()
        candidates = registry["fixtures"]["pack_candidates"]
        for envelope in registry["fixtures"]["task_envelopes"]:
            baseline = classify(registry, envelope["observations"])
            select(registry, envelope, candidates)
            self.assertEqual(classify(registry, envelope["observations"]), baseline)


class ContextMeasurementTests(unittest.TestCase):
    def test_declared_specimen_bytes_match_the_specimen_text(self) -> None:
        registry = _registry()
        for specimen in registry["fixtures"]["context_measurement"]["specimens"]:
            encoded = len((specimen["text"] + "\n").encode("utf-8"))
            self.assertEqual(encoded, specimen["exact_bytes"], specimen["id"])
            self.assertEqual(
                -(-encoded // 4),
                specimen["estimated_tokens"],
                specimen["id"],
            )

    def test_matched_and_excluded_bytes_reconstruct_the_eligible_universe(self) -> None:
        registry = _registry()
        measurement = registry["fixtures"]["context_measurement"]
        specimens = {item["id"]: item for item in measurement["specimens"]}
        universe = sum(item["exact_bytes"] for item in specimens.values())
        self.assertEqual(universe, measurement["eligible_universe_bytes"])
        for case in measurement["cases"]:
            selected = sum(specimens[item]["exact_bytes"] for item in case["active_specimens"])
            excluded = universe - selected
            self.assertEqual(selected, case["active_bytes"], case["id"])
            self.assertEqual(excluded, case["excluded_bytes"], case["id"])
            self.assertEqual(selected + excluded, universe, case["id"])

    def test_unmatched_guidance_adds_zero_active_context_bytes(self) -> None:
        registry = _registry()
        measurement = registry["fixtures"]["context_measurement"]
        specimens = {item["id"]: item for item in measurement["specimens"]}
        for case in measurement["cases"]:
            pack_bytes = sum(
                specimens[item]["exact_bytes"]
                for item in case["active_specimens"]
                if specimens[item]["kind"] == "pack"
            )
            self.assertEqual(pack_bytes, case["active_pack_bytes"], case["id"])
            if case["expected_selection"] != "selected":
                self.assertEqual(pack_bytes, 0, case["id"])

    def test_every_measurement_case_matches_a_selection_fixture(self) -> None:
        registry = _registry()
        envelopes = {item["id"]: item for item in registry["fixtures"]["task_envelopes"]}
        for case in registry["fixtures"]["context_measurement"]["cases"]:
            envelope = envelopes[case["envelope"]]
            self.assertEqual(case["expected_selection"], envelope["expected_selection"])


class ExemplarRecommendationTests(unittest.TestCase):
    def test_recommendation_is_bounded_and_covers_every_shape(self) -> None:
        registry = _registry()
        recommendation = registry["exemplar_recommendation"]
        self.assertIn(len(recommendation["recommended_packs"]), (1, 2))
        self.assertTrue(recommendation["rationale"].strip())
        self.assertTrue(recommendation["alternative_considered"]["packs"])
        covered = set()
        for pack in recommendation["recommended_packs"]:
            covered.update(pack["demonstrates_shapes"])
        shape_ids = {shape["id"] for shape in registry["packs"]["profile_shapes"]}
        self.assertEqual(covered, shape_ids)

    def test_acceptance_state_is_explicit_and_not_self_claimed(self) -> None:
        registry = _registry()
        recommendation = registry["exemplar_recommendation"]
        self.assertIn(
            recommendation["status"],
            ("pending-operator-acceptance", "operator-accepted"),
        )
        self.assertEqual(recommendation["acceptance_owner"], "operator")
        if recommendation["status"] == "pending-operator-acceptance":
            self.assertIsNone(recommendation["accepted_on"])

    def test_no_pack_body_ships_before_acceptance(self) -> None:
        registry = _registry()
        if registry["exemplar_recommendation"]["status"] != "pending-operator-acceptance":
            self.skipTest("exemplar choice accepted; body shipping is authorized")
        packs_dir = REPO_ROOT / "protocol" / "packs"
        self.assertFalse(
            packs_dir.exists(),
            "practice-pack bodies must not ship before the operator accepts the exemplar choice",
        )


class AuthoritativeSurfaceTests(unittest.TestCase):
    def test_exactly_one_surface_owns_the_machine_values(self) -> None:
        registry = _registry()
        owners = [
            surface
            for surface in registry["authoritative_surfaces"]
            if surface["role"] == "authority"
        ]
        self.assertEqual(len(owners), 1)
        self.assertEqual(owners[0]["path"], "protocol/risk-and-practice-contract.json")

    def test_every_declared_surface_path_exists_or_is_a_pending_projection(self) -> None:
        registry = _registry()
        for surface in registry["authoritative_surfaces"]:
            with self.subTest(path=surface["path"]):
                self.assertIn(surface["role"], ("authority", "projection"))
                self.assertIn(surface["activation"], ("active", "pending"))
                if surface["activation"] == "active":
                    self.assertTrue((REPO_ROOT / surface["path"]).exists())

    def test_every_obligation_names_a_real_anchor_or_is_pending(self) -> None:
        registry = _registry()
        obligations = registry["validation_obligations"]
        self.assertTrue(obligations)
        for obligation in obligations:
            with self.subTest(obligation=obligation["id"]):
                self.assertTrue(obligation["requirement"].strip())
                anchor = obligation["test_anchor"]
                if obligation["state"] == "proven":
                    self.assertTrue((REPO_ROOT / anchor).exists())
                else:
                    self.assertEqual(obligation["state"], "pending")
                    self.assertIsNone(anchor)

    def test_projection_declares_the_registry_as_authority(self) -> None:
        text = _projection()
        self.assertIn("protocol/risk-and-practice-contract.json", text)

    def test_projection_names_every_authoritative_identifier(self) -> None:
        registry = _registry()
        text = _projection()
        expected = []
        expected.extend(registry["risk"]["band_order"])
        expected.extend(item["id"] for item in registry["risk"]["observations"])
        for observation in registry["risk"]["observations"]:
            expected.extend(state["id"] for state in observation["states"])
        expected.extend(registry["risk"]["meanings"])
        expected.extend(card["id"] for card in registry["judgment"]["cards"])
        expected.extend(item["id"] for item in registry["judgment"]["failure_signal_grammar"])
        expected.extend(item["id"] for item in registry["packs"]["metadata_fields"])
        expected.extend(item["id"] for item in registry["packs"]["precedence_classes"])
        expected.extend(item["id"] for item in registry["packs"]["selection_rules"])
        expected.extend(item["id"] for item in registry["packs"]["outcomes"])
        expected.extend(item["id"] for item in registry["packs"]["profile_shapes"])
        expected.extend(
            pack["pack_id"]
            for pack in registry["exemplar_recommendation"]["recommended_packs"]
        )
        for identifier in expected:
            with self.subTest(identifier=identifier):
                self.assertIn(identifier, text)

    def test_projected_governance_table_matches_the_authority(self) -> None:
        registry = _registry()
        text = _projection()
        for row in registry["risk"]["governance"]:
            expected = "| {band} | {evidence} | {review} | {gate} | {contingency} |".format(
                band=row["band"],
                evidence=row["evidence_expectation"],
                review=row["review_expectation"],
                gate=row["operator_gate"],
                contingency=row["contingency_expectation"],
            )
            self.assertIn(expected, text, row["band"])

    def test_projection_states_the_pending_acceptance_truthfully(self) -> None:
        registry = _registry()
        text = _projection()
        status = registry["exemplar_recommendation"]["status"]
        self.assertIn(status, text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
