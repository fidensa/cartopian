"""Red/green execution fixtures for bounded optional practice packs."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = REPO_ROOT / "protocol" / "risk-and-practice-contract.json"


def _registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _catalog() -> list[dict]:
    registry = _registry()
    return copy.deepcopy(
        registry["packs"].get("catalog", registry["fixtures"]["pack_candidates"])
    )


def _source_stack_records() -> list[dict]:
    return copy.deepcopy(_registry()["packs"]["source_stack"]["records"])


def _envelope(outcome: str, **extra: object) -> dict[str, object]:
    envelope: dict[str, object] = {
        "primary_outcomes": [outcome],
        "artifact_kinds": [],
        "incidental_terms": [],
        "exclusions": [],
        "lifecycle_substrate_activities": [],
    }
    envelope.update(extra)
    return envelope


def _body(metadata: dict, *, revision: int | None = None,
          areas: list[str] | None = None) -> bytes:
    content_areas = metadata["content_areas"] if areas is None else areas
    headings = "\n".join(
        f"## {area.replace('-', ' ').title()}\n\nGuidance and evidence for {area}."
        for area in content_areas
    )
    return (
        "---\n"
        f"pack_id: {metadata['pack_id']}\n"
        f"revision: {metadata['revision'] if revision is None else revision}\n"
        f"content_areas: {','.join(content_areas)}\n"
        "---\n"
        f"# {metadata['pack_id']}\n\n"
        f"{headings}\n"
    ).encode("utf-8")


def _write_selected_body(root: Path, metadata: dict, body: bytes | None = None) -> Path:
    path = root / metadata["body_ref"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_body(metadata) if body is None else body)
    return path


class PracticePackRuntimeTests(unittest.TestCase):
    def test_all_five_packs_select_and_only_the_selected_body_loads(self) -> None:
        from cli.practice_packs import select_practice_pack

        registry = _registry()
        expected = {
            "software-behavior-change": "software-delivery",
            "supported-finding": "research-inquiry",
            "audience-facing-claim": "marketing-claim",
            "executed-service-action": "operations-change",
            "governing-interpretation": "policy-governance",
        }
        for primary_outcome, pack_id in expected.items():
            with self.subTest(pack=pack_id):
                result = select_practice_pack(_envelope(primary_outcome))
                self.assertEqual(result["outcome"], "selected")
                self.assertEqual(result["pack_id"], pack_id)
                self.assertEqual(result["bodies_loaded"], 1)
                self.assertEqual(
                    result["loaded_body_bytes"], len(result["body"].encode("utf-8"))
                )
                self.assertLessEqual(
                    result["loaded_body_bytes"],
                    next(
                        item["body_budget_bytes"]
                        for item in registry["packs"].get(
                            "catalog", registry["fixtures"]["pack_candidates"]
                        )
                        if item["pack_id"] == pack_id
                    ),
                )
                for area in next(
                    item["content_areas"]
                    for item in registry["packs"]["delivery_scope"][
                        "required_initial_packs"
                    ]
                    if item["pack_id"] == pack_id
                ):
                    self.assertIn(area.replace("-", " ").title(), result["body"])
                other_ids = set(expected.values()) - {pack_id}
                for other_id in other_ids:
                    self.assertNotIn(other_id, result["body"])

    def test_negative_veto_and_no_match_load_zero_bytes(self) -> None:
        from cli.practice_packs import select_practice_pack

        cases = (
            ("software-behavior-change", "software-guidance"),
            ("supported-finding", "research-guidance"),
            ("audience-facing-claim", "marketing-guidance"),
            ("executed-service-action", "operations-guidance"),
            ("governing-interpretation", "policy-guidance"),
        )
        for outcome, exclusion in cases:
            with self.subTest(outcome=outcome):
                result = select_practice_pack(
                    _envelope(outcome, exclusions=[exclusion])
                )
                self.assertEqual(result["outcome"], "none")
                self.assertEqual(result["bodies_loaded"], 0)
                self.assertEqual(result["loaded_body_bytes"], 0)
                self.assertIsNone(result["body"])

        none = select_practice_pack(_envelope("local-text-correction"))
        self.assertEqual(none["outcome"], "none")
        self.assertEqual(none["loaded_body_bytes"], 0)
        self.assertIsNone(none["body"])

    def test_equal_precedence_collision_fails_before_body_loading(self) -> None:
        import cli.practice_packs as packs

        with patch.object(packs, "_load_selected_body") as loader:
            result = packs.select_practice_pack(
                _envelope(
                    "supported-finding",
                    primary_outcomes=["supported-finding", "audience-facing-claim"],
                )
            )
        loader.assert_not_called()
        self.assertEqual(result["outcome"], "ambiguous")
        self.assertEqual(result["bodies_loaded"], 0)
        self.assertEqual(result["loaded_body_bytes"], 0)
        self.assertEqual(result["error"]["code"], "pack-selection-ambiguous")

    def test_authorized_hint_resolves_only_an_eligible_collision(self) -> None:
        from cli.practice_packs import select_practice_pack

        collision = _envelope(
            "supported-finding",
            primary_outcomes=["supported-finding", "audience-facing-claim"],
            authorized_profile_hint="research",
        )
        selected = select_practice_pack(collision)
        self.assertEqual(selected["pack_id"], "research-inquiry")

        vetoed = dict(collision)
        vetoed["exclusions"] = ["research-guidance"]
        result = select_practice_pack(vetoed)
        self.assertNotEqual(result["pack_id"], "research-inquiry")

    def test_invalid_or_incompatible_metadata_fails_before_retrieval(self) -> None:
        import cli.practice_packs as packs

        for field, value, code in (
            ("revision", 0, "pack-metadata-invalid"),
            ("contract_version", 999, "pack-metadata-incompatible"),
            ("body_ref", "../outside.md", "pack-metadata-invalid"),
        ):
            catalog = _catalog()
            catalog[0][field] = value
            with self.subTest(field=field), patch.object(
                packs, "_load_selected_body"
            ) as loader:
                result = packs.select_practice_pack(
                    _envelope("supported-finding"), metadata=catalog
                )
            loader.assert_not_called()
            self.assertEqual(result["outcome"], "invalid")
            self.assertEqual(result["loaded_body_bytes"], 0)
            self.assertEqual(result["error"]["code"], code)

    def test_metadata_validator_covers_every_declared_semantic(self) -> None:
        import cli.practice_packs as packs

        mutations = {
            "identity": lambda item: item.update(pack_id="Bad Identity"),
            "family": lambda item: item.update(family="finance"),
            "positive-applicability": lambda item: item.update(applies_when=[]),
            "negative-applicability": lambda item: item.update(
                never_when=[{"fact": "exclusion", "value": "x", "any_of": ["y"]}]
            ),
            "precedence": lambda item: item.update(precedence_class="artifact-kind"),
            "body-reference": lambda item: item.update(body_ref="packs/other.md"),
            "body-budget": lambda item: item.update(body_budget_bytes=0),
            "content-areas": lambda item: item.update(content_areas=[]),
            "evidence-shape": lambda item: item.update(evidence_shape=""),
        }
        for semantic, mutate in mutations.items():
            catalog = _catalog()
            mutate(catalog[0])
            with self.subTest(semantic=semantic), self.assertRaises(
                packs.PracticePackError
            ) as raised:
                packs.validate_pack_catalog(catalog)
            self.assertEqual(raised.exception.code, "pack-metadata-invalid")

    def test_primary_then_artifact_then_incidental_precedence(self) -> None:
        from cli.practice_packs import select_practice_pack

        catalog = _catalog()
        by_family = {item["family"]: item for item in catalog}
        by_family["research"]["applies_when"] = [
            {"fact": "incidental_term", "value": "shared-topic"}
        ]
        by_family["research"]["precedence_class"] = "incidental-term"
        by_family["software"]["applies_when"] = [
            {"fact": "artifact_kind", "value": "shared-artifact"}
        ]
        by_family["software"]["precedence_class"] = "artifact-kind"
        envelope = _envelope(
            "audience-facing-claim",
            artifact_kinds=["shared-artifact"],
            incidental_terms=["shared-topic"],
        )
        self.assertEqual(
            select_practice_pack(envelope, metadata=catalog)["pack_id"],
            "marketing-claim",
        )
        envelope["primary_outcomes"] = ["local-text-correction"]
        self.assertEqual(
            select_practice_pack(envelope, metadata=catalog)["pack_id"],
            "software-delivery",
        )

    def test_all_positive_conditions_are_required(self) -> None:
        from cli.practice_packs import select_practice_pack

        catalog = _catalog()
        research = next(item for item in catalog if item["family"] == "research")
        research["applies_when"].append(
            {"fact": "artifact_kind", "value": "comparative-finding"}
        )
        result = select_practice_pack(
            _envelope("supported-finding"), metadata=catalog
        )
        self.assertEqual(result["outcome"], "none")
        matched = select_practice_pack(
            _envelope(
                "supported-finding", artifact_kinds=["comparative-finding"]
            ),
            metadata=catalog,
        )
        self.assertEqual(matched["pack_id"], "research-inquiry")

    def test_selection_is_deterministic_and_ignores_incidental_subjects(self) -> None:
        from cli.practice_packs import select_practice_pack

        first = select_practice_pack(
            _envelope(
                "supported-finding",
                incidental_terms=["software-subject", "operations-subject"],
            )
        )
        second = select_practice_pack(
            {
                "incidental_terms": ["operations-subject", "software-subject"],
                "exclusions": [],
                "artifact_kinds": [],
                "primary_outcomes": ["supported-finding"],
                "lifecycle_substrate_activities": [],
                "risk_band": "critical",
                "open_failure_conditions": ["unrelated"],
            }
        )
        self.assertEqual(first, second)
        self.assertEqual(first["pack_id"], "research-inquiry")
        self.assertNotIn("risk_band", first)
        self.assertNotIn("judgment", first)


class PracticePackBodyFailureTests(unittest.TestCase):
    def _select_with_body(self, body: bytes | None, *, mutate=None) -> dict:
        from cli.practice_packs import select_practice_pack

        catalog = _catalog()
        research = next(item for item in catalog if item["family"] == "research")
        if mutate is not None:
            mutate(catalog, research)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            if body is not None:
                _write_selected_body(root, research, body)
            return select_practice_pack(
                _envelope("supported-finding"), metadata=catalog, protocol_root=root
            )

    def assert_closed(self, result: dict, code: str) -> None:
        self.assertEqual(result["outcome"], "invalid")
        self.assertEqual(result["bodies_loaded"], 0)
        self.assertEqual(result["loaded_body_bytes"], 0)
        self.assertIsNone(result["body"])
        self.assertEqual(result["error"]["code"], code)

    def test_missing_stale_oversized_and_invalid_utf8_bodies_fail_closed(self) -> None:
        research = next(item for item in _catalog() if item["family"] == "research")
        self.assert_closed(self._select_with_body(None), "pack-body-missing")
        self.assert_closed(
            self._select_with_body(_body(research, revision=research["revision"] + 1)),
            "pack-body-stale",
        )
        self.assert_closed(
            self._select_with_body(
                _body(research) + b"x" * research["body_budget_bytes"]
            ),
            "pack-body-oversized",
        )
        self.assert_closed(self._select_with_body(b"\xff\xfe"), "pack-body-invalid-utf8")

    def test_out_of_bounds_and_unreadable_bodies_fail_closed(self) -> None:
        from cli.practice_packs import select_practice_pack

        catalog = _catalog()
        research = next(item for item in catalog if item["family"] == "research")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            outside = root / "outside.md"
            outside.write_bytes(_body(research))
            link = root / research["body_ref"]
            link.parent.mkdir(parents=True)
            link.symlink_to(outside)
            result = select_practice_pack(
                _envelope("supported-finding"), metadata=catalog, protocol_root=root
            )
        self.assert_closed(result, "pack-body-out-of-bounds")

        import cli.practice_packs as packs

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _write_selected_body(root, research)
            with patch.object(
                packs, "_read_body_bytes", side_effect=PermissionError("fixture denial")
            ):
                result = select_practice_pack(
                    _envelope("supported-finding"),
                    metadata=catalog,
                    protocol_root=root,
                )
        self.assert_closed(result, "pack-body-unreadable")

    def test_declared_content_area_omission_fails_closed(self) -> None:
        research = next(item for item in _catalog() if item["family"] == "research")
        result = self._select_with_body(
            _body(research, areas=research["content_areas"][:-1])
        )
        self.assert_closed(result, "pack-body-content-areas-mismatch")


class PracticePackSurfaceParityTests(unittest.TestCase):
    def test_cli_and_mcp_return_the_same_selected_body(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "cli",
                "select-practice-pack",
                "--primary-outcome",
                "supported-finding",
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
            "select_practice_pack", {"primary_outcome": ["supported-finding"]}
        )
        self.assertFalse(mcp_result["isError"])
        self.assertEqual(mcp_result["structuredContent"]["records"], [cli_record])
        self.assertEqual(cli_record["pack_id"], "research-inquiry")
        self.assertGreater(cli_record["loaded_body_bytes"], 0)

    def test_cli_reports_ambiguity_as_a_fail_closed_guard(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "cli",
                "select-practice-pack",
                "--primary-outcome",
                "supported-finding",
                "--primary-outcome",
                "audience-facing-claim",
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("pack-selection-ambiguous", completed.stderr)
        record = json.loads(completed.stdout)
        self.assertEqual(record["outcome"], "ambiguous")
        self.assertEqual(record["loaded_body_bytes"], 0)
        self.assertIsNone(record["body"])


class SourceRecordContractTests(unittest.TestCase):
    """The bounded corrective contract: a closed, fail-closed source-record schema.

    The prior contract accepted malformed or incomplete source records: it did
    not validate identity stability or uniqueness, exclusions, precedence
    scope, or verification-date shape, it accepted a condition carrying both
    ``value`` and ``any_of``, and a list-valued precedence scope escaped as a
    raw TypeError. Every malformed case below must instead produce the
    canonical structured invalid result: zero bodies, zero loaded bytes, and
    no partial body.
    """

    def _select(self, records: object = None, catalog: list | None = None) -> dict:
        from cli.practice_packs import select_practice_pack

        return select_practice_pack(
            _envelope("supported-finding"),
            metadata=catalog,
            source_records=records,
        )

    def assert_invalid(
        self, result: dict, code: str = "pack-source-record-invalid"
    ) -> None:
        self.assertEqual(result["outcome"], "invalid")
        self.assertEqual(result["bodies_loaded"], 0)
        self.assertEqual(result["loaded_body_bytes"], 0)
        self.assertIsNone(result["body"])
        self.assertEqual(result["error"]["code"], code)

    def test_the_unmutated_catalog_still_routes_through_the_seam(self) -> None:
        result = self._select(records=_source_stack_records())
        self.assertEqual(result["outcome"], "selected")
        self.assertEqual(result["pack_id"], "research-inquiry")
        self.assertEqual(result["bodies_loaded"], 1)

    def test_the_authoritative_catalog_satisfies_the_closed_schema(self) -> None:
        import cli.practice_packs as packs

        catalog = packs.validate_pack_catalog(packs.load_pack_catalog())
        stack = _registry()["packs"]["source_stack"]
        self.assertEqual(
            [item["id"] for item in stack["classes"]],
            ["governing", "conditional", "structural-exemplar", "watchlist"],
        )
        records = {record["source_id"]: record for record in stack["records"]}
        for pack in catalog:
            with self.subTest(pack=pack["pack_id"]):
                referenced = [records[source_id] for source_id in pack["sources"]]
                self.assertTrue(
                    any(item["class"] == "governing" for item in referenced),
                    "every pack needs at least one governing source",
                )
                for record in referenced:
                    if record["class"] in {"governing", "conditional"}:
                        self.assertEqual(record["status"], "current")
                    if record["class"] == "conditional":
                        self.assertTrue(record["applicability_boundary"].strip())
                        self.assertTrue(record["applies_when"])

    def test_every_declared_record_field_is_required(self) -> None:
        declared = [
            item["id"]
            for item in _registry()["packs"]["source_stack"]["record_fields"]
        ]
        self.assertEqual(len(declared), 13)
        for field in declared:
            records = _source_stack_records()
            del records[0][field]
            with self.subTest(field=field):
                self.assert_invalid(self._select(records=records))

    def test_malformed_field_values_fail_closed_without_raw_exceptions(self) -> None:
        cases = {
            "unstable-source-id": ("source_id", "NIST SSDF (latest)"),
            "wrong-typed-source-id": ("source_id", 42),
            "undeclared-class": ("class", "advisory"),
            "undeclared-status": ("status", "fresh"),
            "empty-title": ("title", ""),
            "wrong-typed-context": ("context", 2022),
            "malformed-verification-date": ("verified_on", "verified 2026-08-07"),
            "impossible-verification-date": ("verified_on", "2026-13-40"),
            "null-exclusions": ("exclusions", None),
            "empty-exclusions": ("exclusions", []),
            "wrong-typed-exclusion-entry": ("exclusions", ["one exclusion", 3]),
            "list-valued-precedence-scope": (
                "precedence_scope",
                ["one-scope", "another-scope"],
            ),
            "malformed-precedence-scope": ("precedence_scope", "Not A Scope"),
            "undeclared-conflict-disposition": ("conflict_disposition", "tbd"),
            "wrong-typed-applies-when": ("applies_when", "always"),
            "empty-governed-scope": ("governed_scope", "  "),
            "empty-refresh-trigger": ("refresh_trigger", ""),
        }
        for name, (field, value) in cases.items():
            records = _source_stack_records()
            records[0][field] = value
            with self.subTest(case=name):
                self.assert_invalid(self._select(records=records))

    def test_empty_exclusions_fail_closed_before_body_retrieval(self) -> None:
        import cli.practice_packs as packs

        records = _source_stack_records()
        records[0]["exclusions"] = []
        with patch.object(packs, "_load_selected_body") as loader:
            result = self._select(records=records)
        loader.assert_not_called()
        self.assert_invalid(result)

    def test_the_record_schema_and_catalog_shape_are_closed(self) -> None:
        records = _source_stack_records()
        records.append(copy.deepcopy(records[0]))
        with self.subTest(case="duplicate-identity"):
            self.assert_invalid(self._select(records=records))

        records = _source_stack_records()
        records[0]["confidence"] = "high"
        with self.subTest(case="undeclared-record-field"):
            self.assert_invalid(self._select(records=records))

        with self.subTest(case="catalog-not-a-list"):
            self.assert_invalid(self._select(records={"source_id": "x"}))
        with self.subTest(case="record-not-an-object"):
            self.assert_invalid(self._select(records=["not-a-record"]))

    def test_applicability_conditions_are_a_closed_contract(self) -> None:
        def mutated(apply) -> list[dict]:
            records = _source_stack_records()
            record = next(
                item for item in records if item["class"] == "conditional"
            )
            apply(record)
            return records

        def set_condition(record: dict, condition: object) -> None:
            record["applies_when"][0] = condition

        cases = {
            "value-and-any-of": lambda record: set_condition(
                record,
                {
                    "fact": "domain_scope",
                    "value": "web-application",
                    "any_of": ["web-service"],
                },
            ),
            "neither-value-nor-any-of": lambda record: set_condition(
                record, {"fact": "domain_scope"}
            ),
            "undeclared-condition-key": lambda record: set_condition(
                record,
                {"fact": "domain_scope", "value": "web-application", "weight": 2},
            ),
            "undeclared-fact": lambda record: set_condition(
                record, {"fact": "vibes", "value": "good"}
            ),
            "non-object-condition": lambda record: set_condition(
                record, "domain_scope=web"
            ),
            "empty-any-of": lambda record: set_condition(
                record, {"fact": "domain_scope", "any_of": []}
            ),
            "non-identity-value": lambda record: set_condition(
                record, {"fact": "domain_scope", "value": "Web Application"}
            ),
            "repeated-any-of-value": lambda record: set_condition(
                record,
                {"fact": "domain_scope", "any_of": ["web-content", "web-content"]},
            ),
            "conditional-without-conditions": lambda record: record.update(
                applies_when=[]
            ),
            "conditional-without-boundary": lambda record: record.update(
                applicability_boundary=None
            ),
        }
        for name, apply in cases.items():
            with self.subTest(case=name):
                self.assert_invalid(self._select(records=mutated(apply)))

        records = _source_stack_records()
        records[0]["applies_when"] = [
            {"fact": "domain_scope", "value": "web-application"}
        ]
        with self.subTest(case="conditions-outside-conditional-class"):
            self.assert_invalid(self._select(records=records))

        records = _source_stack_records()
        records[0]["applicability_boundary"] = "everywhere"
        with self.subTest(case="boundary-outside-conditional-class"):
            self.assert_invalid(self._select(records=records))

    def test_missing_stale_or_conflicting_authority_fails_closed(self) -> None:
        records = _source_stack_records()
        by_id = {record["source_id"]: record for record in records}

        def research_catalog(mutate) -> list[dict]:
            catalog = _catalog()
            research = next(
                item for item in catalog if item["family"] == "research"
            )
            mutate(research)
            return catalog

        with self.subTest(case="no-governing-source"):
            catalog = research_catalog(
                lambda research: research.update(
                    sources=[
                        source_id
                        for source_id in research["sources"]
                        if by_id[source_id]["class"] != "governing"
                    ]
                )
            )
            self.assert_invalid(
                self._select(catalog=catalog), "pack-source-authority-missing"
            )

        with self.subTest(case="exemplar-never-substitutes"):
            catalog = research_catalog(
                lambda research: research.update(sources=["agent-skills-exemplar"])
            )
            self.assert_invalid(
                self._select(catalog=catalog), "pack-source-authority-missing"
            )

        for status in ("stale", "unknown"):
            with self.subTest(case=f"{status}-governing-source"):
                records = _source_stack_records()
                next(
                    item
                    for item in records
                    if item["source_id"] == "allea-code-2023"
                )["status"] = status
                self.assert_invalid(
                    self._select(records=records), "pack-source-stale"
                )

        with self.subTest(case="unresolved-conflict-disposition"):
            records = _source_stack_records()
            records[0]["conflict_disposition"] = "unresolved"
            self.assert_invalid(
                self._select(records=records), "pack-source-conflict-unresolved"
            )

        with self.subTest(case="unresolved-equal-scope-conflict"):
            records = _source_stack_records()
            by_id = {record["source_id"]: record for record in records}
            by_id["nist-csf-2-0"]["precedence_scope"] = by_id["nist-ssdf-1-1"][
                "precedence_scope"
            ]
            self.assert_invalid(
                self._select(records=records), "pack-source-conflict-unresolved"
            )

        with self.subTest(case="unknown-source-reference"):
            catalog = research_catalog(
                lambda research: research.update(
                    sources=[*research["sources"], "not-a-source"]
                )
            )
            self.assert_invalid(self._select(catalog=catalog))

        with self.subTest(case="repeated-source-reference"):
            catalog = research_catalog(
                lambda research: research.update(
                    sources=[*research["sources"], research["sources"][0]]
                )
            )
            self.assert_invalid(
                self._select(catalog=catalog), "pack-metadata-invalid"
            )

        with self.subTest(case="sources-missing-from-metadata"):
            catalog = research_catalog(lambda research: research.pop("sources"))
            self.assert_invalid(
                self._select(catalog=catalog), "pack-metadata-invalid"
            )

        with self.subTest(case="sources-not-a-list"):
            catalog = research_catalog(
                lambda research: research.update(sources="nist-ssdf-1-1")
            )
            self.assert_invalid(
                self._select(catalog=catalog), "pack-metadata-invalid"
            )


class SoftwareDeliveryMiniSkillTests(unittest.TestCase):
    """The software body is an operational mini-skill, not a topic checklist.

    These checks are structural and fail-closed only: they prove the declared
    operational-section contract, the approved domain coverage, the measured
    selected-body ceiling, source-identity alignment, and context exclusion.
    They deliberately do not score prose quality; whether the guidance is
    actionable, proportionate, and source-aligned stays with semantic review.
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
    DOMAIN_COVERAGE = [
        "requirements-and-constraints",
        "design-cohesion-and-coupling",
        "interfaces-and-error-models",
        "testing",
        "security-and-privacy",
        "accessibility",
        "performance-and-observability",
        "compatibility-and-migration",
        "delivery-and-rollback",
        "avoiding-speculative-abstraction",
    ]
    SELECTED_BODY_CEILING = 16 * 1024

    @staticmethod
    def _heading_identities(body: str, level: str) -> list[str]:
        import re

        pattern = re.compile(rf"^{level}\s+(.+?)\s*$", re.MULTILINE)
        return [
            "-".join(match.strip().casefold().replace("-", " ").split())
            for match in pattern.findall(body)
        ]

    def _selected(self) -> dict:
        from cli.practice_packs import select_practice_pack

        result = select_practice_pack(_envelope("software-behavior-change"))
        self.assertEqual(result["outcome"], "selected")
        self.assertEqual(result["pack_id"], "software-delivery")
        self.assertEqual(result["bodies_loaded"], 1)
        return result

    def test_metadata_declares_the_operational_section_contract(self) -> None:
        registry = _registry()
        candidate = next(
            item
            for item in registry["fixtures"]["pack_candidates"]
            if item["pack_id"] == "software-delivery"
        )
        scope_entry = next(
            entry
            for entry in registry["packs"]["delivery_scope"]["required_initial_packs"]
            if entry["pack_id"] == "software-delivery"
        )
        self.assertEqual(candidate["content_areas"], self.OPERATIONAL_SECTIONS)
        self.assertEqual(scope_entry["content_areas"], self.OPERATIONAL_SECTIONS)
        self.assertEqual(candidate["domain_coverage"], self.DOMAIN_COVERAGE)
        # The ceiling is a declared bound, not a quality target; there is no
        # minimum byte count.
        self.assertEqual(candidate["body_budget_bytes"], self.SELECTED_BODY_CEILING)

    def test_selected_body_carries_every_operational_section_in_order(self) -> None:
        result = self._selected()
        headings = self._heading_identities(
            result["body"].split("\n---\n", 1)[1], "##"
        )
        self.assertEqual(headings, self.OPERATIONAL_SECTIONS)
        self.assertEqual(
            result["loaded_body_bytes"], len(result["body"].encode("utf-8"))
        )
        self.assertLessEqual(result["loaded_body_bytes"], self.SELECTED_BODY_CEILING)

    def test_selected_body_covers_each_approved_domain_area(self) -> None:
        subsections = self._heading_identities(self._selected()["body"], "###")
        for area in self.DOMAIN_COVERAGE:
            with self.subTest(area=area):
                self.assertIn(area, subsections)

    def test_body_source_identities_stay_inside_their_declared_scopes(self) -> None:
        body = self._selected()["body"]
        for pin in ("SP 800-218", "5.0.0", "2.2", "40500:2025", "Cybersecurity Framework 2.0"):
            with self.subTest(pin=pin):
                self.assertIn(pin, body)
        sources_at = body.index("## Sources")
        for watchlist in ("SSDF 1.2", "WCAG 3"):
            with self.subTest(watchlist=watchlist):
                # Draft successors are tracked, never governing: they may be
                # named only inside the Sources section, as watchlist entries.
                self.assertGreater(body.index(watchlist), sources_at)

    def test_incidental_software_subject_alone_loads_zero_bytes(self) -> None:
        from cli.practice_packs import select_practice_pack

        result = select_practice_pack(
            _envelope("local-text-correction", incidental_terms=["software-subject"])
        )
        self.assertEqual(result["outcome"], "none")
        self.assertEqual(result["bodies_loaded"], 0)
        self.assertEqual(result["loaded_body_bytes"], 0)
        self.assertIsNone(result["body"])


class ResearchInquiryMiniSkillTests(unittest.TestCase):
    """The research body is an operational mini-skill, not a topic checklist.

    These checks are structural and fail-closed only: they prove the declared
    operational-section contract, the approved domain coverage, the measured
    selected-body ceiling, source-identity alignment, and applicability
    boundaries. They deliberately do not score prose quality; whether the
    guidance is actionable, proportionate, and source-aligned stays with
    semantic review.
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
    DOMAIN_COVERAGE = [
        "question-framing-and-scope",
        "source-strategy-and-hierarchy",
        "primary-versus-secondary-evidence",
        "independence-and-triangulation",
        "freshness-and-version-context",
        "method-fit",
        "claim-evidence-mapping",
        "conflicts-and-contradictions",
        "uncertainty",
        "synthesis",
        "citation-and-fact-checking",
        "search-and-stopping-rules",
    ]
    SELECTED_BODY_CEILING = 16 * 1024

    _heading_identities = staticmethod(
        SoftwareDeliveryMiniSkillTests._heading_identities
    )

    def _selected(self, **extra: object) -> dict:
        from cli.practice_packs import select_practice_pack

        result = select_practice_pack(_envelope("supported-finding", **extra))
        self.assertEqual(result["outcome"], "selected")
        self.assertEqual(result["pack_id"], "research-inquiry")
        self.assertEqual(result["bodies_loaded"], 1)
        return result

    def test_metadata_declares_the_operational_section_contract(self) -> None:
        registry = _registry()
        candidate = next(
            item
            for item in registry["fixtures"]["pack_candidates"]
            if item["pack_id"] == "research-inquiry"
        )
        scope_entry = next(
            entry
            for entry in registry["packs"]["delivery_scope"]["required_initial_packs"]
            if entry["pack_id"] == "research-inquiry"
        )
        self.assertEqual(candidate["content_areas"], self.OPERATIONAL_SECTIONS)
        self.assertEqual(scope_entry["content_areas"], self.OPERATIONAL_SECTIONS)
        self.assertEqual(candidate["domain_coverage"], self.DOMAIN_COVERAGE)
        # The ceiling is a declared bound, not a quality target; there is no
        # minimum byte count.
        self.assertEqual(candidate["body_budget_bytes"], self.SELECTED_BODY_CEILING)

    def test_selected_body_carries_every_operational_section_in_order(self) -> None:
        result = self._selected()
        headings = self._heading_identities(
            result["body"].split("\n---\n", 1)[1], "##"
        )
        self.assertEqual(headings, self.OPERATIONAL_SECTIONS)
        self.assertEqual(
            result["loaded_body_bytes"], len(result["body"].encode("utf-8"))
        )
        self.assertLessEqual(result["loaded_body_bytes"], self.SELECTED_BODY_CEILING)

    def test_selected_body_covers_each_approved_domain_area(self) -> None:
        subsections = self._heading_identities(self._selected()["body"], "###")
        for area in self.DOMAIN_COVERAGE:
            with self.subTest(area=area):
                self.assertIn(area, subsections)

    def test_body_source_identities_stay_inside_their_declared_scopes(self) -> None:
        body = self._selected()["body"]
        for pin in ("ALLEA", "2023", "PRISMA 2020", "Cochrane", "6.5.1"):
            with self.subTest(pin=pin):
                self.assertIn(pin, body)
        # The structural exemplar informs mini-skill anatomy only: it may be
        # named only inside the Sources section, never as domain guidance.
        sources_at = body.index("## Sources")
        self.assertGreater(body.index("agent-skills"), sources_at)

    def test_software_as_research_subject_selects_research_guidance(self) -> None:
        # A supported finding about software is research: the incidental
        # software subject vetoes the software pack and selects this one.
        result = self._selected(incidental_terms=["software-subject"])
        rejected = {
            item["pack_id"]: item["reasons"] for item in result["rejected_candidates"]
        }
        self.assertIn(
            "incidental_term:software-subject:matched",
            rejected["software-delivery"],
        )

    def test_pure_lookup_without_a_declared_finding_loads_zero_bytes(self) -> None:
        from cli.practice_packs import select_practice_pack

        result = select_practice_pack(_envelope("local-text-correction"))
        self.assertEqual(result["outcome"], "none")
        self.assertEqual(result["bodies_loaded"], 0)
        self.assertEqual(result["loaded_body_bytes"], 0)
        self.assertIsNone(result["body"])


class MarketingClaimMiniSkillTests(unittest.TestCase):
    """The marketing body is an operational mini-skill, not a topic checklist.

    These checks are structural and fail-closed only: they prove the declared
    operational-section contract, the approved domain coverage, the measured
    selected-body ceiling, source-identity alignment, and applicability
    boundaries. They deliberately do not score prose quality; whether the
    guidance is actionable, proportionate, and source-aligned stays with
    semantic review.
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
    DOMAIN_COVERAGE = [
        "intended-audience-and-behavior",
        "brand-authority",
        "claim-inventory",
        "substantiation",
        "disclosures",
        "jurisdictional-legal-review",
        "channel-choice",
        "launch-checks",
        "measurement-design",
        "interpretation-limits",
        "follow-up",
    ]
    SELECTED_BODY_CEILING = 16 * 1024

    _heading_identities = staticmethod(
        SoftwareDeliveryMiniSkillTests._heading_identities
    )

    def _selected(self, **extra: object) -> dict:
        from cli.practice_packs import select_practice_pack

        result = select_practice_pack(_envelope("audience-facing-claim", **extra))
        self.assertEqual(result["outcome"], "selected")
        self.assertEqual(result["pack_id"], "marketing-claim")
        self.assertEqual(result["bodies_loaded"], 1)
        return result

    def test_metadata_declares_the_operational_section_contract(self) -> None:
        registry = _registry()
        candidate = next(
            item
            for item in registry["fixtures"]["pack_candidates"]
            if item["pack_id"] == "marketing-claim"
        )
        scope_entry = next(
            entry
            for entry in registry["packs"]["delivery_scope"]["required_initial_packs"]
            if entry["pack_id"] == "marketing-claim"
        )
        self.assertEqual(candidate["content_areas"], self.OPERATIONAL_SECTIONS)
        self.assertEqual(scope_entry["content_areas"], self.OPERATIONAL_SECTIONS)
        self.assertEqual(candidate["domain_coverage"], self.DOMAIN_COVERAGE)
        # The ceiling is a declared bound, not a quality target; there is no
        # minimum byte count.
        self.assertEqual(candidate["body_budget_bytes"], self.SELECTED_BODY_CEILING)

    def test_selected_body_carries_every_operational_section_in_order(self) -> None:
        result = self._selected()
        headings = self._heading_identities(
            result["body"].split("\n---\n", 1)[1], "##"
        )
        self.assertEqual(headings, self.OPERATIONAL_SECTIONS)
        self.assertEqual(
            result["loaded_body_bytes"], len(result["body"].encode("utf-8"))
        )
        self.assertLessEqual(result["loaded_body_bytes"], self.SELECTED_BODY_CEILING)

    def test_selected_body_covers_each_approved_domain_area(self) -> None:
        subsections = self._heading_identities(self._selected()["body"], "###")
        for area in self.DOMAIN_COVERAGE:
            with self.subTest(area=area):
                self.assertIn(area, subsections)

    def test_body_source_identities_stay_inside_their_declared_scopes(self) -> None:
        body = self._selected()["body"]
        for pin in (
            "ICC Advertising and Marketing Communications Code",
            "11th edition",
            "September 2024",
            "Endorsement Guides",
            "Consumer Reviews and Testimonials Rule",
            "2024-10-21",
        ):
            with self.subTest(pin=pin):
                self.assertIn(pin, body)
        # The FTC authority set is conditional on United States jurisdiction:
        # every mention of it must live alongside its declared scope, never as
        # universal marketing law.
        for index, line in enumerate(body.splitlines()):
            if "FTC" in line or "Federal Trade Commission" in line:
                with self.subTest(line=index + 1):
                    self.assertRegex(line, r"United States|U\.S\.")
        # The structural exemplar informs mini-skill anatomy only: it may be
        # named only inside the Sources section, never as domain guidance.
        sources_at = body.index("## Sources")
        self.assertGreater(body.index("agent-skills"), sources_at)

    def test_incidental_promotional_language_alone_loads_zero_bytes(self) -> None:
        # Internal documentation, policy, research, or software work that only
        # mentions marketing never declares the audience-facing-claim outcome,
        # so it selects no marketing body and contributes zero bytes.
        from cli.practice_packs import select_practice_pack

        result = select_practice_pack(
            _envelope("local-text-correction", incidental_terms=["marketing-subject"])
        )
        self.assertEqual(result["outcome"], "none")
        self.assertEqual(result["bodies_loaded"], 0)
        self.assertEqual(result["loaded_body_bytes"], 0)
        self.assertIsNone(result["body"])


if __name__ == "__main__":
    unittest.main()
