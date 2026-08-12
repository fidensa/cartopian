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

# Vocabulary that would represent an operator-accepted decision as still open.
# No string value under an accepted-decision subtree may contain any of these.
_OPEN_STATE_WORDS = (
    "pending",
    "reserved",
    "contested",
    "contestable",
    "reopen",
    "confirm or strike",
    "maintainer addition",
    "awaiting",
)

# The same prohibition on the plain-language projection, phrased so that the
# projection's legitimate uses -- pending *projections*, authority "reserved to
# someone who has not granted it" -- are not caught.
_PROJECTION_OPEN_STATE_PHRASES = (
    "contestable",
    "contested",
    "reopen",
    "confirm or strike",
    "maintainer addition",
    "still reserved",
    "awaiting",
)


def _string_values(node: object, path: str = "") -> list:
    """Yield every ``(path, string)`` pair inside one registry subtree."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.extend(_string_values(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_string_values(value, f"{path}[{index}]"))
    elif isinstance(node, str):
        found.append((path, node))
    return found


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
        (observation_id, _state_floor(registry, observation_id, observations[observation_id]))
        for observation_id in declared
    ]
    band = max(floors, key=lambda pair: _band_rank(registry, pair[1]))[1]
    reasons = tuple(
        f"{observation_id}={observations[observation_id]}->{floor}"
        for observation_id, floor in floors
        if _band_rank(registry, floor) == _band_rank(registry, band)
    )
    return {"outcome": "classified", "band": band, "reasons": reasons}


_CONDITION_FACTS = {
    "primary_outcome": "primary_outcomes",
    "artifact_kind": "artifact_kinds",
    "incidental_term": "incidental_terms",
    "exclusion": "exclusions",
    "lifecycle_substrate": "lifecycle_substrate_activities",
}


def _condition_matches(condition: dict, envelope: dict) -> bool:
    """Match one declared condition against one declared envelope fact.

    A condition declares either an exact ``value`` or a closed ``any_of`` set.
    Both forms read only the declared envelope facts; neither infers anything
    from prose, filenames, work-root contents, or runtime activity.
    """
    fact = condition["fact"]
    if fact not in _CONDITION_FACTS:
        raise AssertionError(f"undeclared envelope fact {fact}")
    declared = envelope.get(_CONDITION_FACTS[fact], [])
    if "any_of" in condition:
        return any(value in declared for value in condition["any_of"])
    return condition["value"] in declared


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


def activate_cards(registry: dict, envelope: dict) -> tuple:
    """Apply the registry's declared judgment-activation rule to one envelope.

    A card is eligible only when the envelope declares that the work crosses
    the card's lifecycle boundary *and* that the card's named non-enforceable
    failure is still open. Neither the band nor the pack outcome is an input.
    """
    rule = registry["judgment"]["activation_rule"]
    boundaries = set(envelope.get(rule["boundary_fact"], []))
    failures = set(envelope.get(rule["failure_fact"], []))
    return tuple(
        card["id"]
        for card in registry["judgment"]["cards"]
        if card["boundary_id"] in boundaries and card["failure_id"] in failures
    )


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
            "accepted_decisions",
            "mechanism_validation_exemplars",
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

    def test_observation_records_declare_their_supporting_fact_and_elevation(self) -> None:
        registry = _registry()
        fields = {
            field["id"]: field
            for field in registry["risk"]["observation_record_fields"]
        }
        for required in ("observation", "state", "supporting_fact", "elevates_governance"):
            self.assertIn(required, fields)
            self.assertTrue(fields[required]["meaning"].strip())
        lowest = registry["risk"]["band_order"][0]
        for observation in registry["risk"]["observations"]:
            for state in observation["states"]:
                with self.subTest(observation=observation["id"], state=state["id"]):
                    self.assertEqual(
                        state["elevates_governance"],
                        state["band_floor"] != lowest,
                    )

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

    def test_fixed_envelopes_produce_the_recorded_ordered_reasons(self) -> None:
        registry = _registry()
        for envelope in registry["fixtures"]["task_envelopes"]:
            with self.subTest(envelope=envelope["id"]):
                result = classify(registry, envelope["observations"])
                self.assertEqual(
                    list(result["reasons"]),
                    envelope["expected_ordered_reasons"],
                )

    def test_ordered_reasons_follow_declared_observation_order(self) -> None:
        registry = _registry()
        declared = [item["id"] for item in registry["risk"]["observations"]]
        for envelope in registry["fixtures"]["task_envelopes"]:
            reasons = classify(registry, envelope["observations"])["reasons"]
            positions = [declared.index(reason.split("=", 1)[0]) for reason in reasons]
            with self.subTest(envelope=envelope["id"]):
                self.assertEqual(
                    positions,
                    sorted(positions),
                    "reasons must follow the declared observation order",
                )

    def test_ordered_reasons_are_stable_under_input_reordering(self) -> None:
        registry = _registry()
        for envelope in registry["fixtures"]["task_envelopes"]:
            observations = envelope["observations"]
            shuffled = dict(reversed(list(observations.items())))
            with self.subTest(envelope=envelope["id"]):
                self.assertEqual(
                    classify(registry, observations)["reasons"],
                    classify(registry, shuffled)["reasons"],
                )

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

    def test_every_card_declares_a_machine_boundary_and_failure_condition(self) -> None:
        registry = _registry()
        cards = registry["judgment"]["cards"]
        boundaries = [card["boundary_id"] for card in cards]
        failures = [card["failure_id"] for card in cards]
        self.assertEqual(len(set(boundaries)), len(cards))
        self.assertEqual(len(set(failures)), len(cards))
        for card in cards:
            with self.subTest(card=card["id"]):
                self.assertTrue(card["boundary_id"].strip())
                self.assertTrue(card["failure_id"].strip())
                self.assertEqual(card["guidance"], "failure_signal_grammar")

    def test_activation_rule_reads_only_declared_boundary_facts(self) -> None:
        registry = _registry()
        rule = registry["judgment"]["activation_rule"]
        self.assertEqual(rule["combinator"], "boundary-and-open-failure")
        self.assertEqual(rule["default_outcome"], "no-card")
        for fact in (rule["boundary_fact"], rule["failure_fact"]):
            self.assertTrue(fact.strip())
        forbidden = set(registry["independence"]["forbidden_pack_metadata_fields"])
        self.assertFalse({rule["boundary_fact"], rule["failure_fact"]} & forbidden)

    def test_fixed_envelopes_activate_the_recorded_judgment_cards(self) -> None:
        registry = _registry()
        for envelope in registry["fixtures"]["task_envelopes"]:
            with self.subTest(envelope=envelope["id"]):
                self.assertEqual(
                    list(activate_cards(registry, envelope)),
                    envelope["expected_judgment_cards"],
                )

    def test_crossing_a_boundary_alone_activates_no_card(self) -> None:
        registry = _registry()
        rule = registry["judgment"]["activation_rule"]
        crossed_without_open_failure = [
            envelope
            for envelope in registry["fixtures"]["task_envelopes"]
            if envelope[rule["boundary_fact"]] and not envelope["expected_judgment_cards"]
        ]
        self.assertTrue(
            crossed_without_open_failure,
            "no fixture proves a crossed boundary alone does not activate a card",
        )
        for envelope in crossed_without_open_failure:
            with self.subTest(envelope=envelope["id"]):
                self.assertEqual(activate_cards(registry, envelope), ())

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

    def test_software_shape_rationale_keys_on_the_declared_contract(self) -> None:
        # The one-shape rationale must distinguish software delivery by its
        # declared positive condition and evidence shape, not by claiming its
        # body lacks ownership, stop, handoff, or contingency guidance.
        registry = _registry()
        candidate = next(
            item
            for item in registry["fixtures"]["pack_candidates"]
            if item["pack_id"] == "software-delivery"
        )
        self.assertEqual(candidate["demonstrates_shapes"], ["software"])
        reason = candidate["demonstrates_shapes_reason"]
        for condition in candidate["applies_when"]:
            self.assertIn(condition["value"], reason)
        for term in ("artifact behavior", "target-state", "recovery"):
            self.assertIn(term, reason.lower())
        self.assertNotIn("declares no", reason)


class OperationsPrimaryOutcomeTests(unittest.TestCase):
    """Ordinary Cartopian lifecycle mechanics are process substrate.

    Cartopian moves task files, dispatches handoffs, routes reviews, refreshes
    state, and cleans up after itself as a normal part of running. None of that
    may make operations a task's primary outcome.
    """

    def _operations(self, registry: dict) -> dict:
        return next(
            item
            for item in registry["fixtures"]["pack_candidates"]
            if item["pack_id"] == "operations-change"
        )

    def test_operations_requires_a_declared_qualifying_outcome(self) -> None:
        registry = _registry()
        declared = registry["packs"]["operations_primary_outcome"]
        qualifying = [item["id"] for item in declared["qualifying_outcomes"]]
        self.assertTrue(qualifying)
        positives = self._operations(registry)["applies_when"]
        self.assertEqual(len(positives), 1)
        self.assertEqual(positives[0]["fact"], "primary_outcome")
        self.assertEqual(positives[0]["any_of"], qualifying)

        candidates = registry["fixtures"]["pack_candidates"]
        for outcome in qualifying:
            with self.subTest(outcome=outcome):
                envelope = {"primary_outcomes": [outcome]}
                self.assertEqual(
                    select(registry, envelope, candidates)["pack_id"],
                    "operations-change",
                )
        # An envelope that declares no qualifying outcome selects nothing, no
        # matter what its other facts say.
        for fact in ("artifact_kinds", "incidental_terms"):
            with self.subTest(fact=fact):
                envelope = {fact: qualifying}
                self.assertEqual(select(registry, envelope, candidates)["outcome"], "none")

    def test_primary_outcome_is_never_inferred(self) -> None:
        registry = _registry()
        declaration = registry["packs"]["outcome_declaration"]
        sources = {item["id"] for item in declaration["non_inference_sources"]}
        self.assertEqual(
            sources,
            {
                "prose-verbs",
                "filenames",
                "work-root-contents",
                "conversation",
                "project-history",
                "cartopian-runtime-activity",
            },
        )
        # Selection reads only the declared facts: an envelope carrying nothing
        # but undeclared keys resolves to none rather than guessing.
        noise = {
            "description": "restart the service and dispatch the handoff",
            "files": ["run-handoff.md"],
            "history": ["executed-service-action"],
        }
        self.assertEqual(
            select(registry, noise, registry["fixtures"]["pack_candidates"])["outcome"],
            "none",
        )

    def test_each_lifecycle_substrate_activity_vetoes_operations_alone(self) -> None:
        registry = _registry()
        substrate = registry["packs"]["lifecycle_substrate"]
        activities = [item["id"] for item in substrate["activities"]]
        self.assertEqual(
            set(activities),
            {
                "task-directory-movement",
                "handoff-dispatch",
                "review-routing",
                "state-file-refresh",
                "pm-cleanup",
            },
        )
        candidates = registry["fixtures"]["pack_candidates"]
        qualifying = [
            item["id"]
            for item in registry["packs"]["operations_primary_outcome"]["qualifying_outcomes"]
        ]
        for activity in activities:
            for outcome in qualifying:
                with self.subTest(activity=activity, outcome=outcome):
                    envelope = {
                        "primary_outcomes": [outcome],
                        "lifecycle_substrate_activities": [activity],
                    }
                    result = select(registry, envelope, candidates)
                    self.assertEqual(result["outcome"], "none")
                    self.assertEqual(result["bodies_loaded"], 0)

    def test_substrate_is_a_negative_only_fact(self) -> None:
        registry = _registry()
        negative_only = set(registry["packs"]["negative_only_facts"])
        self.assertIn("lifecycle_substrate", negative_only)
        for candidate in registry["fixtures"]["pack_candidates"]:
            for condition in candidate["applies_when"]:
                with self.subTest(pack=candidate["pack_id"]):
                    self.assertNotIn(condition["fact"], negative_only)
        # A negative-only fact declares no precedence class, so it can never
        # rank a candidate.
        self.assertFalse(negative_only & set(registry["packs"]["fact_precedence_classes"]))

    def test_declared_negative_applicability_is_exhaustive_on_the_candidate(self) -> None:
        registry = _registry()
        declared = registry["packs"]["operations_primary_outcome"]["negative_applicability"]
        categories = {item["id"] for item in declared}
        self.assertEqual(
            categories,
            {
                "governance-mechanics-only",
                "implementing-software-functionality",
                "researching-operations",
                "documenting-without-executing",
                "operational-language-as-subject-only",
            },
        )
        vetoes = self._operations(registry)["never_when"]
        for entry in declared:
            with self.subTest(category=entry["id"]):
                self.assertIn(entry["condition"], vetoes)
                self.assertTrue(entry["vetoes_when"].strip())

        # Each declared category vetoes on its own against an otherwise
        # qualifying envelope.
        candidates = registry["fixtures"]["pack_candidates"]
        for entry in declared:
            condition = entry["condition"]
            values = condition.get("any_of", [condition.get("value")])
            for value in values:
                with self.subTest(category=entry["id"], value=value):
                    envelope = {
                        "primary_outcomes": ["executed-service-action"],
                        _CONDITION_FACTS[condition["fact"]]: [value],
                    }
                    if condition["fact"] == "primary_outcome":
                        envelope["primary_outcomes"] = ["executed-service-action", value]
                    self.assertNotEqual(
                        select(registry, envelope, candidates)["pack_id"],
                        "operations-change",
                    )

    def test_the_six_boundary_fixtures_resolve_deterministically(self) -> None:
        registry = _registry()
        fixtures = registry["fixtures"]["operations_boundary"]
        self.assertEqual(len(fixtures), 6)
        self.assertEqual(
            [item["id"] for item in fixtures],
            [
                "routine-cartopian-handoff-selects-no-operations",
                "task-status-movement-selects-no-operations",
                "implementing-handoff-functionality-selects-software",
                "researching-handoff-practices-selects-research",
                "executing-and-verifying-a-service-restart-selects-operations",
                "ambiguous-primary-outcomes-load-no-body",
            ],
        )
        candidates = registry["fixtures"]["pack_candidates"]
        for fixture in fixtures:
            with self.subTest(fixture=fixture["id"]):
                self.assertTrue(fixture["boundary"].strip())
                result = select(registry, fixture, candidates)
                self.assertEqual(result["outcome"], fixture["expected_selection"])
                self.assertEqual(result["pack_id"], fixture["expected_pack"])
                self.assertEqual(
                    result["bodies_loaded"], fixture["expected_bodies_loaded"]
                )
                # Repeating the resolution over reordered facts returns the
                # same outcome: the result is a function of the declared set.
                reordered = dict(fixture)
                for key in _CONDITION_FACTS.values():
                    reordered[key] = list(reversed(fixture.get(key, [])))
                self.assertEqual(
                    select(registry, reordered, candidates)["pack_id"],
                    fixture["expected_pack"],
                )

    def test_no_boundary_fixture_selects_operations_from_substrate(self) -> None:
        registry = _registry()
        for fixture in registry["fixtures"]["operations_boundary"]:
            if fixture.get("lifecycle_substrate_activities"):
                with self.subTest(fixture=fixture["id"]):
                    self.assertIsNone(fixture["expected_pack"])
                    self.assertEqual(fixture["expected_bodies_loaded"], 0)
                    self.assertEqual(fixture["expected_veto"], "governance-mechanics-only")

    def test_operations_bodies_and_runtime_selection_activate_after_the_gate(self) -> None:
        registry = _registry()
        gate = registry["packs"]["runtime_activation_gate"]
        conditions = {item["id"]: item for item in gate["conditions"]}
        self.assertEqual(
            set(conditions),
            {
                "operations-safeguards-validated",
                "operator-exemplar-acceptance",
                "equivalent-cli-and-mcp-validation",
                "task-review",
            },
        )
        # All explicit selection gates are now evidenced. Risk classification
        # remains an independent path and is not evidence for any of them.
        self.assertTrue(conditions["equivalent-cli-and-mcp-validation"]["met"])
        self.assertTrue(conditions["task-review"]["met"])
        for condition in gate["conditions"]:
            with self.subTest(condition=condition["id"]):
                if condition["met"]:
                    self.assertTrue(condition["evidence"])
                else:
                    self.assertIsNone(condition["evidence"])
        unmet = [item for item in gate["conditions"] if not item["met"]]
        self.assertEqual(gate["state"], "unmet" if unmet else "met")
        self.assertEqual(gate["inactive_until_met"], [])
        # Activation must not reduce or expand the approved delivery scope.
        scope = registry["packs"]["delivery_scope"]
        self.assertEqual(scope["required_initial_pack_count"], 5)
        self.assertIsNone(scope["phase_exit"]["blocked_by"])
        self.assertTrue((REPO_ROOT / "protocol" / "packs").is_dir())
        selector = next(
            surface
            for surface in registry["authoritative_surfaces"]
            if surface["path"] == "cli/practice_packs.py"
        )
        self.assertEqual(selector["activation"], "active")
        classifier = next(
            surface
            for surface in registry["authoritative_surfaces"]
            if surface["path"] == "cli/risk_contract.py"
        )
        self.assertEqual(classifier["activation"], "active")


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

    def test_card_activation_is_unchanged_by_band_or_pack_outcome(self) -> None:
        registry = _registry()
        candidates = registry["fixtures"]["pack_candidates"]
        critical_states = {
            observation["id"]: next(
                state["id"]
                for state in observation["states"]
                if state["band_floor"] == "critical"
            )
            for observation in registry["risk"]["observations"]
        }
        for envelope in registry["fixtures"]["task_envelopes"]:
            baseline = activate_cards(registry, envelope)
            escalated = dict(envelope)
            escalated["observations"] = critical_states
            without_candidates = dict(envelope)
            with self.subTest(envelope=envelope["id"]):
                self.assertEqual(classify(registry, critical_states)["band"], "critical")
                self.assertEqual(activate_cards(registry, escalated), baseline)
                select(registry, without_candidates, [])
                self.assertEqual(activate_cards(registry, without_candidates), baseline)

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


class RequiredInitialPackDeliveryTests(unittest.TestCase):
    """Phase 04 ships five optional packs; the exemplar pair only validates the mechanism.

    The approved families and their content areas are fixed by the operator's
    own words. They are restated here as a literal so the registry cannot drift
    from them silently: if the registry loses a family or a content area, these
    checks fail rather than re-deriving the expectation from the registry.

    All five entries were deliberately revised to the operational mini-skill
    contract: their content areas are now the operational mini-skill sections,
    and the former topic areas live on as reviewed domain coverage inside each
    body rather than as headings.
    """

    OPERATIONAL_SECTIONS = [
        "intended-outcome",
        "when-to-apply",
        "when-not-to-apply",
        "principles-and-heuristics",
        "working-process",
        "decision-gates",
        "failure-modes",
        "evidence-and-verification",
        "examples-and-counterexamples",
        "stop-and-escalation",
        "sources",
    ]

    APPROVED_FAMILIES = {
        "software": OPERATIONAL_SECTIONS,
        "research": OPERATIONAL_SECTIONS,
        "marketing": OPERATIONAL_SECTIONS,
        "operations": OPERATIONAL_SECTIONS,
        "policy": OPERATIONAL_SECTIONS,
    }

    def _scope(self, registry: dict) -> dict:
        return registry["packs"]["delivery_scope"]

    def test_exactly_five_initial_packs_are_required_with_their_content_areas(self) -> None:
        registry = _registry()
        scope = self._scope(registry)
        required = scope["required_initial_packs"]
        self.assertEqual(scope["required_initial_pack_count"], 5)
        self.assertEqual(len(required), 5)
        self.assertEqual(
            {entry["family"]: entry["content_areas"] for entry in required},
            self.APPROVED_FAMILIES,
        )
        pack_ids = [entry["pack_id"] for entry in required]
        self.assertEqual(len(set(pack_ids)), 5)
        for entry in required:
            with self.subTest(family=entry["family"]):
                self.assertTrue(entry["applicability_boundary"].strip())
                self.assertTrue(entry["not_applicable_when"].strip())

    def test_every_required_pack_has_validating_metadata_declaring_the_same_areas(self) -> None:
        registry = _registry()
        required = {entry["pack_id"]: entry for entry in self._scope(registry)["required_initial_packs"]}
        candidates = {item["pack_id"]: item for item in registry["fixtures"]["pack_candidates"]}
        self.assertEqual(sorted(required), sorted(candidates))
        fields = {field["id"] for field in registry["packs"]["metadata_fields"]}
        self.assertIn("family", fields)
        self.assertIn("content_areas", fields)
        for pack_id, entry in required.items():
            with self.subTest(pack=pack_id):
                candidate = candidates[pack_id]
                self.assertEqual(candidate["family"], entry["family"])
                self.assertEqual(candidate["profile_shape"], entry["family"])
                self.assertEqual(candidate["content_areas"], entry["content_areas"])
                self.assertEqual(candidate["contract_version"], registry["contract_version"])
                self.assertTrue(candidate["body_ref"].strip())
                self.assertGreater(candidate["body_budget_bytes"], 0)

    def test_required_packs_stay_optional_and_are_never_always_loaded(self) -> None:
        registry = _registry()
        scope = self._scope(registry)
        self.assertFalse(scope["mandatory"])
        self.assertFalse(scope["always_loaded"])
        out_of_scope = {item["id"] for item in scope["out_of_scope"]}
        self.assertEqual(
            out_of_scope,
            {
                "profiles-beyond-the-five-initial-families",
                "mandatory-packs",
                "always-loaded-catalogs",
            },
        )
        # "no pack is required for any task" must remain true alongside "five
        # packs ship": a no-match envelope is still a valid zero-body result.
        outcomes = {item["id"]: item for item in registry["packs"]["outcomes"]}
        self.assertEqual(outcomes["none"]["bodies_loaded"], 0)
        self.assertFalse(outcomes["none"]["is_error"])

    def test_every_required_pack_selects_positively_and_is_vetoed_negatively(self) -> None:
        registry = _registry()
        candidates = registry["fixtures"]["pack_candidates"]
        by_id = {item["pack_id"]: item for item in candidates}
        for entry in self._scope(registry)["required_initial_packs"]:
            pack_id = entry["pack_id"]
            candidate = by_id[pack_id]
            for condition in candidate["applies_when"]:
                values = condition.get("any_of", [condition.get("value")])
                for value in values:
                    with self.subTest(pack=pack_id, positive=value):
                        envelope = {_CONDITION_FACTS[condition["fact"]]: [value]}
                        result = select(registry, envelope, candidates)
                        self.assertEqual(result["pack_id"], pack_id)
                        self.assertEqual(result["bodies_loaded"], 1)
            for condition in candidate["never_when"]:
                values = condition.get("any_of", [condition.get("value")])
                for value in values:
                    with self.subTest(pack=pack_id, negative=value):
                        positive = candidate["applies_when"][0]
                        positive_value = positive.get(
                            "value", positive.get("any_of", [None])[0]
                        )
                        envelope = {
                            _CONDITION_FACTS[positive["fact"]]: [positive_value],
                        }
                        key = _CONDITION_FACTS[condition["fact"]]
                        envelope[key] = sorted(set(envelope.get(key, []) + [value]))
                        result = select(registry, envelope, candidates)
                        self.assertNotEqual(result["pack_id"], pack_id)

    def test_the_exemplar_comparison_does_not_bound_delivery_scope(self) -> None:
        registry = _registry()
        exemplars = registry["mechanism_validation_exemplars"]
        self.assertFalse(exemplars["bounds_delivery_scope"])
        self.assertTrue(exemplars["scope_limit"].strip())
        self.assertEqual(
            [pack["pack_id"] for pack in exemplars["exemplar_packs"]],
            ["research-inquiry", "operations-change"],
        )
        required_ids = {
            entry["pack_id"] for entry in self._scope(registry)["required_initial_packs"]
        }
        for entry in exemplars["candidate_sets"]:
            with self.subTest(candidate_set=entry["id"]):
                self.assertFalse(entry["bounds_delivery_scope"])
        five_pack = [
            entry
            for entry in exemplars["candidate_sets"]
            if set(entry["packs"]) == required_ids
        ]
        self.assertEqual(len(five_pack), 1)
        self.assertNotEqual(five_pack[0]["verdict"], "rejected")
        self.assertEqual(five_pack[0]["verdict"], "is-the-delivery-scope")
        self.assertTrue(five_pack[0]["is_delivery_scope"])

    def test_a_missing_required_pack_body_blocks_phase_exit(self) -> None:
        registry = _registry()
        phase_exit = self._scope(registry)["phase_exit"]
        self.assertTrue(phase_exit["missing_or_invalid_body_blocks_exit"])
        self.assertEqual(phase_exit["bodies_required"], 5)
        self.assertTrue(phase_exit["exemplar_pass_is_not_sufficient"].strip())
        self.assertTrue(phase_exit["rule"].strip())
        self.assertEqual(
            phase_exit["state"],
            "blocked" if phase_exit["bodies_authored"] < 5 else "clear",
        )

    def test_only_one_required_pack_body_can_enter_active_context(self) -> None:
        registry = _registry()
        required_ids = [
            entry["pack_id"] for entry in self._scope(registry)["required_initial_packs"]
        ]
        measurement = registry["fixtures"]["context_measurement"]
        specimens = {item["id"]: item for item in measurement["specimens"]}
        pack_specimens = {
            item["pack_id"]: item
            for item in measurement["specimens"]
            if item["kind"] == "pack"
        }
        self.assertEqual(sorted(pack_specimens), sorted(required_ids))
        selected = [
            case for case in measurement["cases"] if case["expected_selection"] == "selected"
        ]
        self.assertEqual(
            sorted(case["selected_pack"] for case in selected), sorted(required_ids)
        )
        for case in selected:
            with self.subTest(case=case["id"]):
                active = [
                    specimens[item]["pack_id"]
                    for item in case["active_specimens"]
                    if specimens[item]["kind"] == "pack"
                ]
                self.assertEqual(active, [case["selected_pack"]])
                others = [
                    pack for pack in required_ids if pack != case["selected_pack"]
                ]
                self.assertEqual(len(others), 4)
                # The four required packs that did not match contribute their
                # full byte weight to the excluded side and nothing to context.
                self.assertEqual(
                    case["excluded_bytes"],
                    sum(pack_specimens[pack]["exact_bytes"] for pack in others),
                )

    def test_adding_the_other_required_packs_does_not_raise_peak_active_context(self) -> None:
        registry = _registry()
        measurement = registry["fixtures"]["context_measurement"]
        admission = measurement["body_admission"]
        core = next(
            item for item in measurement["specimens"] if item["kind"] == "core"
        )
        budgets = [
            item["body_budget_bytes"] for item in registry["fixtures"]["pack_candidates"]
        ]
        self.assertEqual(admission["required_pack_count"], 5)
        # Budgets are declared per pack and need not be uniform; peak active
        # context is the core line plus the largest declared budget because at
        # most one body is ever admitted.
        self.assertEqual(admission["largest_declared_body_budget_bytes"], max(budgets))
        self.assertEqual(admission["total_authored_body_bytes"], sum(budgets))
        self.assertEqual(admission["peak_active_body_bytes"], max(budgets))
        self.assertEqual(
            admission["peak_active_bytes"], core["exact_bytes"] + max(budgets)
        )


class MechanismValidationExemplarTests(unittest.TestCase):
    """The exemplar comparison ranks mechanism coverage, not delivery scope."""

    def test_exemplar_set_is_bounded_and_covers_every_shape(self) -> None:
        registry = _registry()
        recommendation = registry["mechanism_validation_exemplars"]
        self.assertIn(len(recommendation["exemplar_packs"]), (1, 2))
        self.assertTrue(recommendation["rationale"].strip())
        self.assertTrue(recommendation["purpose"].strip())
        self.assertTrue(recommendation["exemplar_and_delivery_relationship"].strip())
        self.assertEqual(recommendation["delivery_scope_ref"], "packs.delivery_scope")
        covered = set()
        for pack in recommendation["exemplar_packs"]:
            covered.update(pack["demonstrates_shapes"])
        shape_ids = {shape["id"] for shape in registry["packs"]["profile_shapes"]}
        self.assertEqual(covered, shape_ids)

    def test_acceptance_state_is_explicit_and_not_self_claimed(self) -> None:
        registry = _registry()
        recommendation = registry["mechanism_validation_exemplars"]
        self.assertIn(
            recommendation["status"],
            ("pending-operator-acceptance", "operator-accepted"),
        )
        self.assertEqual(recommendation["acceptance_owner"], "operator")
        if recommendation["status"] == "pending-operator-acceptance":
            self.assertIsNone(recommendation["accepted_on"])
        else:
            # Acceptance is recorded against operator evidence, never claimed by
            # the contract itself, and never read as accepting a reduced scope.
            self.assertTrue(recommendation["accepted_on"])
            self.assertTrue(recommendation["acceptance_evidence"])
            self.assertTrue(recommendation["acceptance_meaning"].strip())

    def test_every_candidate_set_recomputes_from_the_declared_evidence(self) -> None:
        """Coverage flags and cost figures are derived, not asserted in prose."""
        registry = _registry()
        recommendation = registry["mechanism_validation_exemplars"]
        sets = recommendation["candidate_sets"]
        self.assertGreaterEqual(len(sets), 2)

        candidates = {item["pack_id"]: item for item in registry["fixtures"]["pack_candidates"]}
        measurement = registry["fixtures"]["context_measurement"]
        specimens = {
            item["pack_id"]: item
            for item in measurement["specimens"]
            if item["kind"] == "pack"
        }
        core = next(
            item for item in measurement["specimens"] if item["kind"] == "core"
        )
        recommended = next(item for item in sets if item["verdict"] == "recommended")

        for entry in sets:
            with self.subTest(candidate_set=entry["id"]):
                packs = entry["packs"]
                self.assertTrue(packs)
                self.assertEqual(len(packs), len(set(packs)))
                shapes = set()
                negative_facts = set()
                for pack in packs:
                    self.assertIn(pack, candidates)
                    shapes.update(candidates[pack]["demonstrates_shapes"])
                    negative_facts.update(
                        condition["fact"] for condition in candidates[pack]["never_when"]
                    )
                self.assertEqual(entry["shapes_demonstrated"], sorted(shapes))
                self.assertEqual(entry["all_five_shapes_demonstrated"], len(shapes) == 5)
                self.assertEqual(entry["within_two_pack_bound"], len(packs) <= 2)
                self.assertEqual(
                    entry["equal_precedence_collision_demonstrable"], len(packs) >= 2
                )
                self.assertEqual(
                    entry["single_body_admission_demonstrable"], len(packs) >= 2
                )
                self.assertEqual(
                    entry["veto_on_competing_primary_outcome"],
                    "primary_outcome" in negative_facts,
                )
                self.assertEqual(
                    entry["veto_on_incidental_subject"],
                    "incidental_term" in negative_facts,
                )
                self.assertEqual(
                    entry["lifecycle_substrate_safeguards_demonstrable"],
                    "lifecycle_substrate" in negative_facts,
                )

                exact = sum(specimens[pack]["exact_bytes"] for pack in packs)
                tokens = sum(specimens[pack]["estimated_tokens"] for pack in packs)
                self.assertEqual(entry["resident_metadata_bytes"], exact)
                self.assertEqual(entry["resident_metadata_estimated_tokens"], tokens)
                self.assertEqual(
                    entry["delta_bytes_vs_recommended"],
                    exact - recommended["resident_metadata_bytes"],
                )
                self.assertEqual(
                    entry["delta_estimated_tokens_vs_recommended"],
                    tokens - recommended["resident_metadata_estimated_tokens"],
                )
                set_budgets = [
                    candidates[pack]["body_budget_bytes"] for pack in packs
                ]
                self.assertEqual(
                    entry["authored_body_budget_bytes"], sum(set_budgets)
                )
                # At most one body is ever admitted, so peak active context is
                # the core line plus the set's largest declared budget; it does
                # not grow with the size of the set.
                self.assertEqual(
                    entry["peak_active_bytes"], core["exact_bytes"] + max(set_budgets)
                )
                self.assertIn(
                    entry["verdict"],
                    ("recommended", "rejected-as-exemplar-set", "is-the-delivery-scope"),
                )
                if entry["verdict"] != "recommended":
                    self.assertTrue(entry["verdict_reasons"])

    def test_exactly_one_candidate_set_is_recommended_and_it_is_the_exemplar_set(
        self,
    ) -> None:
        registry = _registry()
        recommendation = registry["mechanism_validation_exemplars"]
        sets = recommendation["candidate_sets"]
        chosen = [item for item in sets if item["verdict"] == "recommended"]
        self.assertEqual(len(chosen), 1)
        self.assertEqual(
            chosen[0]["packs"],
            [pack["pack_id"] for pack in recommendation["exemplar_packs"]],
        )
        self.assertTrue(chosen[0]["all_five_shapes_demonstrated"])
        self.assertTrue(chosen[0]["within_two_pack_bound"])
        self.assertTrue(chosen[0]["lifecycle_substrate_safeguards_demonstrable"])
        self.assertFalse(chosen[0]["verdict_reasons"])
        # The recommended set is the only one that reaches every shape inside
        # the one-or-two exemplar bound. That is the decisive argument, and it
        # is checked rather than asserted.
        equally_covering = [
            item
            for item in sets
            if item["all_five_shapes_demonstrated"] and item["within_two_pack_bound"]
        ]
        self.assertEqual(equally_covering, chosen)

    def test_cost_evidence_is_labelled_as_measurement_not_budget(self) -> None:
        registry = _registry()
        recommendation = registry["mechanism_validation_exemplars"]
        for field in ("cost_method", "cost_finding", "measurement_vs_production_budgets"):
            self.assertTrue(recommendation[field].strip(), field)
        self.assertIn("estimated token", recommendation["cost_method"])
        self.assertNotRegex(recommendation["measurement_vs_production_budgets"], _CONFIDENCE_RE)
        # The comparison survives as recorded evidence behind a closed decision.
        self.assertTrue(recommendation["representation_mapping"]["evidence_role"].strip())

    def test_current_cost_narrative_cannot_contradict_the_computed_figures(self) -> None:
        """Every byte figure the current finding quotes must be derivable from
        the declared budgets and core line, so a budget revision that leaves the
        prose behind fails here instead of shipping a stale narrative."""
        registry = _registry()
        recommendation = registry["mechanism_validation_exemplars"]
        finding = recommendation["cost_finding"]
        fixtures = registry["fixtures"]
        budgets = [item["body_budget_bytes"] for item in fixtures["pack_candidates"]]
        measurement = fixtures["context_measurement"]
        core = next(
            item for item in measurement["specimens"] if item["kind"] == "core"
        )
        resident = sum(
            item["exact_bytes"]
            for item in measurement["specimens"]
            if item["kind"] == "pack"
        )
        current_peak = core["exact_bytes"] + max(budgets)
        current_total = sum(budgets)

        admission = measurement["body_admission"]
        self.assertEqual(admission["peak_active_bytes"], current_peak)
        self.assertEqual(admission["total_authored_body_bytes"], current_total)

        # The current narrative must state the computed current figures.
        self.assertIn(f"{current_peak} bytes", finding)
        self.assertIn(f"{current_total} bytes", finding)
        self.assertIn(f"{resident} resident bytes", finding)
        # And it may not quote a byte figure the declared evidence cannot
        # produce: each quoted figure is a declared budget, the core line plus
        # a declared budget, or the total across all declared budgets.
        derivable = set(budgets)
        derivable.update(core["exact_bytes"] + budget for budget in set(budgets))
        derivable.add(current_total)
        quoted = {
            int(match) for match in re.findall(r"(\d{4,})(?:-byte| bytes)", finding)
        }
        self.assertLessEqual(quoted, derivable, finding)

    def test_historical_cost_figures_are_a_labelled_dated_snapshot(self) -> None:
        registry = _registry()
        recommendation = registry["mechanism_validation_exemplars"]
        snapshot = recommendation["cost_finding_at_acceptance"]
        self.assertIn(recommendation["accepted_on"], snapshot)
        self.assertIn("snapshot", snapshot.lower())
        self.assertIn("acceptance", snapshot.lower())
        # The snapshot must point back at the current authority so its figures
        # cannot be read as present-tense state.
        self.assertIn("cost_finding", snapshot)

    def test_projection_cost_narrative_matches_the_current_authority(self) -> None:
        registry = _registry()
        fixtures = registry["fixtures"]
        budgets = [item["body_budget_bytes"] for item in fixtures["pack_candidates"]]
        core = next(
            item
            for item in fixtures["context_measurement"]["specimens"]
            if item["kind"] == "core"
        )
        current_peak = core["exact_bytes"] + max(budgets)
        current_total = sum(budgets)
        text = _projection()
        self.assertIn(f"{current_peak:,} bytes", text)
        self.assertIn(f"{current_total:,} bytes", text)

    def test_no_pack_body_ships_before_the_activation_gate_is_met(self) -> None:
        registry = _registry()
        gate = registry["packs"]["runtime_activation_gate"]
        if gate["state"] == "met":
            self.skipTest("activation gate met; body shipping is authorized")
        packs_dir = REPO_ROOT / "protocol" / "packs"
        self.assertFalse(
            packs_dir.exists(),
            "practice-pack bodies must not ship before the runtime activation gate is met",
        )
        # Deferring bodies is not the same as not owing them.
        phase_exit = registry["packs"]["delivery_scope"]["phase_exit"]
        self.assertEqual(phase_exit["bodies_required"], 5)
        self.assertEqual(phase_exit["state"], "blocked")


class AcceptedDecisionStateTests(unittest.TestCase):
    """Operator-accepted decisions are locked on every delivered surface.

    DEC-038 accepted ``operational-language-as-subject-only`` as a governing
    negative applicability veto. DEC-037 accepted the representation mapping in
    which ``research-inquiry`` carries the research, marketing, and policy claim
    shapes and ``operations-change`` carries the operations and software change
    shapes. Neither is a proposal, a maintainer addition awaiting confirmation,
    or a live choice. These checks fail if the authority or its projection emits
    either decision as pending, reserved, contestable, or reopenable.
    """

    def _decision(self, registry: dict, decision_id: str) -> dict:
        for entry in registry["accepted_decisions"]["decisions"]:
            if entry["decision_id"] == decision_id:
                return entry
        raise AssertionError(f"no accepted-decision record for {decision_id}")

    def test_both_accepted_decisions_carry_operator_provenance(self) -> None:
        registry = _registry()
        block = registry["accepted_decisions"]
        self.assertTrue(block["statement"].strip())
        recorded = {entry["decision_id"] for entry in block["decisions"]}
        self.assertLessEqual({"DEC-037", "DEC-038"}, recorded)
        for decision_id in ("DEC-037", "DEC-038"):
            with self.subTest(decision=decision_id):
                decision = self._decision(registry, decision_id)
                # Acceptance is recorded against verbatim operator evidence and
                # is never claimed by the contract on its own authority.
                self.assertEqual(decision["status"], "operator-accepted")
                self.assertEqual(decision["state"], "locked")
                self.assertEqual(decision["acceptance_owner"], "operator")
                self.assertTrue(decision["accepted_on"])
                self.assertTrue(decision["acceptance_evidence"])
                self.assertTrue(decision["governs"])
                self.assertTrue(decision["acceptance_meaning"].strip())
                self.assertFalse(decision["reopenable"])
                self.assertFalse(decision["contested"])

    def test_the_subject_only_veto_is_accepted_and_still_governs(self) -> None:
        registry = _registry()
        declared = registry["packs"]["operations_primary_outcome"]
        acceptance = declared["acceptance"]
        self.assertEqual(acceptance["status"], "operator-accepted")
        self.assertEqual(acceptance["decision_ref"], "DEC-038")
        self.assertFalse(acceptance["reopenable"])
        self.assertEqual(
            acceptance["acceptance_evidence"],
            self._decision(registry, "DEC-038")["acceptance_evidence"],
        )
        self.assertIn(
            "operational-language-as-subject-only",
            acceptance["accepted_safeguards"],
        )

        veto = next(
            entry
            for entry in declared["negative_applicability"]
            if entry["id"] == "operational-language-as-subject-only"
        )
        self.assertEqual(veto["status"], "operator-accepted")
        self.assertEqual(veto["decision_ref"], "DEC-038")
        self.assertFalse(veto["reopenable"])

        # Accepted state does not replace behavior: the veto still stops an
        # otherwise qualifying operations envelope on its own.
        candidates = registry["fixtures"]["pack_candidates"]
        envelope = {
            "primary_outcomes": ["executed-service-action"],
            "incidental_terms": ["operations-subject"],
        }
        self.assertNotEqual(
            select(registry, envelope, candidates)["pack_id"],
            "operations-change",
        )

    def test_the_representation_mapping_is_a_closed_decision(self) -> None:
        registry = _registry()
        recommendation = registry["mechanism_validation_exemplars"]
        self.assertNotIn("contested_input", recommendation)
        mapping = recommendation["representation_mapping"]
        self.assertEqual(mapping["status"], "operator-accepted")
        self.assertEqual(mapping["decision_ref"], "DEC-037")
        self.assertFalse(mapping["reopenable"])
        self.assertFalse(mapping["contested"])
        self.assertEqual(
            mapping["acceptance_evidence"],
            self._decision(registry, "DEC-037")["acceptance_evidence"],
        )

        # The accepted mapping is the one the exemplar packs actually carry, and
        # it covers every declared profile shape.
        represents = {
            entry["pack_id"]: entry["represents_shapes"] for entry in mapping["mapping"]
        }
        for pack in recommendation["exemplar_packs"]:
            with self.subTest(pack=pack["pack_id"]):
                self.assertEqual(represents[pack["pack_id"]], pack["demonstrates_shapes"])
        shape_ids = {shape["id"] for shape in registry["packs"]["profile_shapes"]}
        covered = set()
        for shapes in represents.values():
            covered.update(shapes)
        self.assertEqual(covered, shape_ids)

        # The alternative-set comparison and its costs survive as the evidence
        # behind the closed decision, not as a live choice.
        self.assertTrue(mapping["evidence_role"].strip())
        self.assertGreaterEqual(len(recommendation["candidate_sets"]), 2)

    def test_no_accepted_decision_is_emitted_as_open(self) -> None:
        registry = _registry()
        governed = {
            "accepted_decisions": registry["accepted_decisions"],
            "packs.operations_primary_outcome": registry["packs"][
                "operations_primary_outcome"
            ],
            "mechanism_validation_exemplars": registry[
                "mechanism_validation_exemplars"
            ],
        }
        # The registry names the representations it forbids; the scan below
        # covers every one of them.
        prohibited = registry["accepted_decisions"]["prohibited_representations"]
        self.assertTrue(prohibited)
        for representation in prohibited:
            self.assertTrue(
                any(word in representation for word in _OPEN_STATE_WORDS),
                representation,
            )

        for name, subtree in governed.items():
            for path, value in _string_values(subtree, name):
                if ".prohibited_representations[" in path:
                    continue  # the declaration of what is forbidden, not a use
                for word in _OPEN_STATE_WORDS:
                    with self.subTest(path=path, word=word):
                        self.assertNotIn(word, value.lower())

        text = _projection().lower()
        for phrase in _PROJECTION_OPEN_STATE_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, text)

    def test_projection_records_both_accepted_decision_states(self) -> None:
        registry = _registry()
        text = _projection()
        for decision_id in ("DEC-037", "DEC-038"):
            decision = self._decision(registry, decision_id)
            with self.subTest(decision=decision_id):
                self.assertIn(decision_id, text)
                self.assertIn(decision["status"], text)
                for evidence in decision["acceptance_evidence"]:
                    self.assertIn(evidence, text)


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
        expected.extend(item["id"] for item in registry["risk"]["observation_record_fields"])
        for card in registry["judgment"]["cards"]:
            expected.extend((card["id"], card["boundary_id"], card["failure_id"]))
        expected.extend(item["id"] for item in registry["judgment"]["envelope_facts"])
        expected.extend(item["id"] for item in registry["judgment"]["failure_signal_grammar"])
        expected.extend(item["id"] for item in registry["packs"]["metadata_fields"])
        expected.extend(item["id"] for item in registry["packs"]["envelope_facts"])
        expected.extend(item["id"] for item in registry["packs"]["precedence_classes"])
        packs = registry["packs"]
        expected.extend(
            item["id"] for item in packs["operations_primary_outcome"]["qualifying_outcomes"]
        )
        expected.extend(
            item["id"] for item in packs["operations_primary_outcome"]["negative_applicability"]
        )
        expected.extend(item["id"] for item in packs["lifecycle_substrate"]["activities"])
        expected.extend(
            item["id"] for item in packs["outcome_declaration"]["non_inference_sources"]
        )
        expected.extend(item["id"] for item in packs["runtime_activation_gate"]["conditions"])
        expected.extend(
            item["id"] for item in registry["mechanism_validation_exemplars"]["candidate_sets"]
        )
        expected.extend(item["id"] for item in registry["packs"]["selection_rules"])
        expected.extend(item["id"] for item in registry["packs"]["outcomes"])
        expected.extend(item["id"] for item in registry["packs"]["profile_shapes"])
        expected.extend(
            pack["pack_id"]
            for pack in registry["mechanism_validation_exemplars"]["exemplar_packs"]
        )
        expected.extend(item["id"] for item in packs["delivery_scope"]["out_of_scope"])
        for entry in packs["delivery_scope"]["required_initial_packs"]:
            expected.append(entry["pack_id"])
            expected.append(entry["family"])
            expected.extend(entry["content_areas"])
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

    def test_projection_states_the_acceptance_state_truthfully(self) -> None:
        registry = _registry()
        text = _projection()
        self.assertIn(registry["mechanism_validation_exemplars"]["status"], text)

    def test_projection_states_the_five_pack_delivery_scope(self) -> None:
        registry = _registry()
        text = _projection()
        scope = registry["packs"]["delivery_scope"]
        self.assertIn(str(scope["required_initial_pack_count"]), text)
        # The projection must not repeat the corrected claim that shipping all
        # five packs is the catalog the operator rejected.
        self.assertNotIn("specialist catalog the operator explicitly rejected", text)
        self.assertNotIn("without growing a catalog", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
