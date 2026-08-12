"""Red/green execution fixtures for bounded optional practice packs."""
from __future__ import annotations

import copy
import hashlib
import json
import re
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
    """Write a fixture body and declare its identity, as authoring would.

    Identity is declared rather than left stale so each fixture keeps failing
    for the reason it names; the identity-mismatch case declares its own.
    """
    path = root / metadata["body_ref"]
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _body(metadata) if body is None else body
    path.write_bytes(raw)
    metadata["body_content_identity"] = f"sha256:{hashlib.sha256(raw).hexdigest()}"
    return path


# One distinctive sentence fragment per authored body. A failure case that
# leaked any partial content would carry one of these; a zero-byte outcome
# carries none of them.
_BODY_FINGERPRINTS = {
    "software-delivery": "Cargo-cult abstraction",
    "research-inquiry": "cherry-pick",
    "marketing-claim": "puffery",
    "operations-change": "blast radius",
    "policy-governance": "sunset",
}


def _authored_body_bytes(pack_id: str) -> bytes:
    return (REPO_ROOT / "protocol" / "packs" / f"{pack_id}.md").read_bytes()


def _assert_no_body_leaked(case: unittest.TestCase, result: dict) -> None:
    """No fail-closed outcome may carry any fragment of any authored body."""
    case.assertIsNone(result["body"])
    case.assertEqual(result["loaded_body_bytes"], 0)
    case.assertEqual(result["bodies_loaded"], 0)
    case.assertEqual(result["body_identity"], None)
    case.assertEqual(result["applicable_sources"], [])
    case.assertEqual(result["context_receipt"]["selected_body_bytes"], 0)
    case.assertEqual(result["context_receipt"]["unmatched_body_bytes"], 0)
    serialized = json.dumps(result)
    for pack_id, fingerprint in _BODY_FINGERPRINTS.items():
        case.assertNotIn(fingerprint, serialized, pack_id)


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
        self.assertEqual(result["error"]["code"], code)
        _assert_no_body_leaked(self, result)

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
        self.assertEqual(result["error"]["code"], code)
        _assert_no_body_leaked(self, result)

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


class OperationsChangeMiniSkillTests(unittest.TestCase):
    """The operations body is an operational mini-skill, not a topic checklist.

    These checks are structural and fail-closed only: they prove the declared
    operational-section contract, the approved domain coverage, the measured
    selected-body ceiling, source-identity alignment inside declared scopes,
    and the lifecycle-substrate and incidental-subject vetoes. They
    deliberately do not score prose quality; whether the safe-change and
    incident guidance is actionable, proportionate, and source-aligned stays
    with semantic review.
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
        "authority-and-change-window",
        "ownership",
        "preconditions-and-current-state-capture",
        "rehearsal",
        "blast-radius",
        "communication-and-handoff",
        "monitoring-and-stop-conditions",
        "rollback-and-recovery",
        "incident-coordination",
        "immediate-verification",
        "closure-and-follow-up",
    ]
    SELECTED_BODY_CEILING = 16 * 1024

    _heading_identities = staticmethod(
        SoftwareDeliveryMiniSkillTests._heading_identities
    )

    def _selected(self, **extra: object) -> dict:
        from cli.practice_packs import select_practice_pack

        result = select_practice_pack(_envelope("executed-service-action", **extra))
        self.assertEqual(result["outcome"], "selected")
        self.assertEqual(result["pack_id"], "operations-change")
        self.assertEqual(result["bodies_loaded"], 1)
        return result

    def test_metadata_declares_the_operational_section_contract(self) -> None:
        registry = _registry()
        candidate = next(
            item
            for item in registry["fixtures"]["pack_candidates"]
            if item["pack_id"] == "operations-change"
        )
        scope_entry = next(
            entry
            for entry in registry["packs"]["delivery_scope"]["required_initial_packs"]
            if entry["pack_id"] == "operations-change"
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

    def test_body_carries_an_executable_safe_change_and_incident_process(self) -> None:
        # The revision-1 body was four topic headings with no executable
        # process: no numbered safe-change sequence, no gates to answer before
        # touching the system, and no incident path. Those are the structural
        # markers of the process this pack is required to carry.
        body = self._selected()["body"]
        process = body.split("## Working Process", 1)[1].split("\n## ", 1)[0]
        steps = [
            line for line in process.splitlines() if re.match(r"^\d+\. ", line.strip())
        ]
        self.assertGreaterEqual(len(steps), 8)
        gates = body.split("## Decision Gates", 1)[1].split("\n## ", 1)[0]
        self.assertGreaterEqual(
            len([line for line in gates.splitlines() if line.startswith("- ")]), 6
        )
        failures = body.split("## Failure Modes", 1)[1].split("\n## ", 1)[0]
        for failure in (
            "unclear command",
            "hidden ownership",
            "unrehearsed rollback",
            "destructive scope",
            "monitoring without thresholds",
            "silent partial failure",
            "premature success",
            "handoff without state",
        ):
            with self.subTest(failure=failure):
                self.assertIn(failure, failures.casefold())

    def test_body_source_identities_stay_inside_their_declared_scopes(self) -> None:
        body = self._selected()["body"]
        for pin in (
            "Cybersecurity Framework 2.0",
            "2024-02-26",
            "SP 800-61 Rev. 3",
            "2025-04-03",
            "SP 800-34 Rev. 1",
            "Site Reliability Engineering Workbook",
        ):
            with self.subTest(pin=pin):
                self.assertIn(pin, body)
        # NIST SP 800-34 Rev. 1 is conditional on federal information-system
        # contingency planning: it may never be stated as a universal modern
        # operations baseline.
        for index, line in enumerate(body.splitlines()):
            if "800-34" in line:
                with self.subTest(line=index + 1):
                    self.assertRegex(line, r"federal")
            # NIST SP 800-61 Rev. 3 is conditional on cybersecurity incident
            # response, not every operational incident.
            if "800-61" in line:
                with self.subTest(line=index + 1):
                    self.assertRegex(line, r"cybersecurity|security incident")
        # The structural exemplar informs mini-skill anatomy only: it may be
        # named only inside the Sources section, never as domain guidance.
        sources_at = body.index("## Sources")
        self.assertGreater(body.index("agent-skills"), sources_at)

    def test_lifecycle_substrate_activity_loads_zero_bytes(self) -> None:
        # Cartopian moving its own work through its own lifecycle is process
        # substrate, not an operational outcome: the veto fires even though the
        # qualifying primary outcome matched.
        from cli.practice_packs import select_practice_pack

        result = select_practice_pack(
            _envelope(
                "executed-service-action",
                lifecycle_substrate_activities=["task-directory-movement"],
            )
        )
        self.assertEqual(result["outcome"], "none")
        self.assertEqual(result["bodies_loaded"], 0)
        self.assertEqual(result["loaded_body_bytes"], 0)
        self.assertIsNone(result["body"])

    def test_incidental_operational_vocabulary_alone_loads_zero_bytes(self) -> None:
        # Implementation-only work that merely mentions deployment, rollback,
        # or monitoring never declares an executed operational outcome, so it
        # selects no operations body and contributes zero bytes.
        from cli.practice_packs import select_practice_pack

        result = select_practice_pack(
            _envelope("software-behavior-change", incidental_terms=["operations-subject"])
        )
        self.assertNotEqual(result["pack_id"], "operations-change")
        rejected = {
            item["pack_id"]: item["reasons"] for item in result["rejected_candidates"]
        }
        self.assertIn(
            "incidental_term:operations-subject:matched",
            rejected["operations-change"],
        )
        self.assertNotIn("operations-change", result["body"] or "")


class PolicyGovernanceMiniSkillTests(unittest.TestCase):
    """The policy body is an operational mini-skill, not a topic checklist.

    These checks are structural and fail-closed only: they prove the declared
    operational-section contract, the approved domain coverage, the measured
    selected-body ceiling, source-identity alignment inside declared scopes,
    and the incidental-subject veto. They deliberately do not score prose
    quality; whether the authority, participation, publication, and
    implementation guidance is actionable, proportionate, and source-aligned
    stays with semantic review.
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
        "governing-authority-and-jurisdiction",
        "problem-and-objective",
        "stakeholder-participation",
        "evidence-and-impact",
        "alternatives",
        "conflicts-and-distributional-effects",
        "compliance",
        "decision-ownership",
        "publication",
        "effective-dates",
        "implementation-ownership",
        "monitoring",
        "review-and-revision",
    ]
    SELECTED_BODY_CEILING = 16 * 1024

    _heading_identities = staticmethod(
        SoftwareDeliveryMiniSkillTests._heading_identities
    )

    def _selected(self, **extra: object) -> dict:
        from cli.practice_packs import select_practice_pack

        result = select_practice_pack(_envelope("governing-interpretation", **extra))
        self.assertEqual(result["outcome"], "selected")
        self.assertEqual(result["pack_id"], "policy-governance")
        self.assertEqual(result["bodies_loaded"], 1)
        return result

    def test_metadata_declares_the_operational_section_contract(self) -> None:
        registry = _registry()
        candidate = next(
            item
            for item in registry["fixtures"]["pack_candidates"]
            if item["pack_id"] == "policy-governance"
        )
        scope_entry = next(
            entry
            for entry in registry["packs"]["delivery_scope"]["required_initial_packs"]
            if entry["pack_id"] == "policy-governance"
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

    def test_body_carries_an_executable_policy_development_process(self) -> None:
        # The revision-1 body was four topic headings — stakeholder review,
        # compliance, publication, effective-date checks — with no policy
        # development process at all: no numbered sequence from authority to
        # review, no gates to answer before binding anyone, no named failure
        # modes, and no source. Those are the structural markers of the process
        # this pack is required to carry.
        body = self._selected()["body"]
        process = body.split("## Working Process", 1)[1].split("\n## ", 1)[0]
        steps = [
            line for line in process.splitlines() if re.match(r"^\d+\. ", line.strip())
        ]
        self.assertGreaterEqual(len(steps), 8)
        gates = body.split("## Decision Gates", 1)[1].split("\n## ", 1)[0]
        self.assertGreaterEqual(
            len([line for line in gates.splitlines() if line.startswith("- ")]), 6
        )
        failures = body.split("## Failure Modes", 1)[1].split("\n## ", 1)[0]
        for failure in (
            "missing authority",
            "stakeholder theater",
            "unsupported impact claim",
            "unresolved conflict",
            "ambiguous effective date",
            "inaccessible publication",
            "policy without implementation ownership",
            "no review trigger",
        ):
            with self.subTest(failure=failure):
                self.assertIn(failure, failures.casefold())

    def test_body_source_identities_stay_inside_their_declared_scopes(self) -> None:
        body = self._selected()["body"]
        for pin in (
            "OECD/LEGAL/0390",
            "2012-03-22",
            "Regulatory Impact Assessment",
            "OECD/LEGAL/0464",
            "2021-10-06",
            "OECD/LEGAL/0475",
            "2022-06-10",
            "Regulatory Policy Outlook 2025",
            "2025-04-09",
        ):
            with self.subTest(pin=pin):
                self.assertIn(pin, body)
        # The supplementary instruments are conditional: each may be named only
        # alongside its declared scope, never as universal policy authority.
        for index, line in enumerate(body.splitlines()):
            if "OECD/LEGAL/0464" in line:
                with self.subTest(line=index + 1):
                    self.assertRegex(line, r"innovation|experimentation")
            if "OECD/LEGAL/0475" in line:
                with self.subTest(line=index + 1):
                    self.assertRegex(line, r"transboundary|international")
            if "Regulatory Policy Outlook 2025" in line:
                with self.subTest(line=index + 1):
                    self.assertRegex(line, r"supporting implementation evidence")
            if "Regulatory Impact Assessment" in line:
                with self.subTest(line=index + 1):
                    self.assertRegex(line, r"impact[- ]assessment|applicability")
        # Governing law stays primary over the generic instrument stack.
        self.assertIn("outrank this pack", body)
        # The structural exemplar informs mini-skill anatomy only: it may be
        # named only inside the Sources section, never as domain guidance.
        sources_at = body.index("## Sources")
        self.assertGreater(body.index("agent-skills"), sources_at)

    def test_incidental_policy_source_alone_loads_zero_bytes(self) -> None:
        # Research, marketing, or software work that merely cites a policy
        # never declares a governing interpretation, so it selects no policy
        # body and contributes zero bytes.
        from cli.practice_packs import select_practice_pack

        result = select_practice_pack(
            _envelope("supported-finding", incidental_terms=["policy-source"])
        )
        self.assertNotEqual(result["pack_id"], "policy-governance")
        rejected = {
            item["pack_id"]: item["reasons"] for item in result["rejected_candidates"]
        }
        self.assertIn("incidental_term:policy-source:matched", rejected["policy-governance"])
        self.assertNotIn("policy-governance", result["body"] or "")


class SelectedBodyIdentityTests(unittest.TestCase):
    """The one admitted body is the authored body, proven by measured identity."""

    def test_every_declared_identity_matches_its_authored_body(self) -> None:
        for candidate in _catalog():
            with self.subTest(pack=candidate["pack_id"]):
                measured = hashlib.sha256(
                    _authored_body_bytes(candidate["pack_id"])
                ).hexdigest()
                self.assertEqual(
                    candidate["body_content_identity"], f"sha256:{measured}"
                )

    def test_selection_returns_the_measured_identity_and_declared_ceiling(self) -> None:
        from cli.practice_packs import select_practice_pack

        expected = {
            "software-behavior-change": "software-delivery",
            "supported-finding": "research-inquiry",
            "audience-facing-claim": "marketing-claim",
            "executed-service-action": "operations-change",
            "governing-interpretation": "policy-governance",
        }
        declared = {item["pack_id"]: item for item in _catalog()}
        for primary_outcome, pack_id in expected.items():
            with self.subTest(pack=pack_id):
                result = select_practice_pack(_envelope(primary_outcome))
                raw = _authored_body_bytes(pack_id)
                self.assertEqual(
                    result["body_identity"],
                    f"sha256:{hashlib.sha256(raw).hexdigest()}",
                )
                self.assertEqual(
                    result["body_identity"], declared[pack_id]["body_content_identity"]
                )
                self.assertEqual(result["loaded_body_bytes"], len(raw))
                self.assertEqual(
                    result["body_budget_bytes"], declared[pack_id]["body_budget_bytes"]
                )
                self.assertLessEqual(
                    result["loaded_body_bytes"], result["body_budget_bytes"]
                )

    def test_a_body_edited_out_from_under_its_metadata_is_stale(self) -> None:
        from cli.practice_packs import select_practice_pack

        catalog = _catalog()
        research = next(item for item in catalog if item["family"] == "research")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            # Structurally valid and inside budget, but not the authored body.
            _write_selected_body(root, research)
            research["body_content_identity"] = "sha256:" + "0" * 64
            result = select_practice_pack(
                _envelope("supported-finding"), metadata=catalog, protocol_root=root
            )
        self.assertEqual(result["outcome"], "invalid")
        self.assertEqual(result["error"]["code"], "pack-body-stale")
        _assert_no_body_leaked(self, result)

    def test_a_missing_or_malformed_declared_identity_fails_before_retrieval(self) -> None:
        from cli.practice_packs import select_practice_pack

        for mutation in (None, "", "sha256:not-hex", "cabb3190", "SHA256:" + "a" * 64):
            with self.subTest(identity=mutation):
                catalog = _catalog()
                research = next(item for item in catalog if item["family"] == "research")
                if mutation is None:
                    del research["body_content_identity"]
                else:
                    research["body_content_identity"] = mutation
                with tempfile.TemporaryDirectory() as raw:
                    root = Path(raw)
                    result = select_practice_pack(
                        _envelope("supported-finding"),
                        metadata=catalog,
                        protocol_root=root,
                    )
                self.assertEqual(result["outcome"], "invalid")
                self.assertEqual(result["error"]["code"], "pack-metadata-invalid")
                _assert_no_body_leaked(self, result)


class ApplicableSourceProjectionTests(unittest.TestCase):
    """Only the authority that governs or conditionally applies is projected."""

    def _sources(self, result: dict) -> list[str]:
        return [item["source_id"] for item in result["applicable_sources"]]

    def test_governing_sources_always_apply_and_other_classes_never_do(self) -> None:
        from cli.practice_packs import select_practice_pack

        records = {item["source_id"]: item for item in _source_stack_records()}
        declared = {item["pack_id"]: item for item in _catalog()}
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
                projected = self._sources(result)
                stack = declared[pack_id]["sources"]
                governing = [
                    source_id
                    for source_id in stack
                    if records[source_id]["class"] == "governing"
                ]
                self.assertTrue(governing)
                for source_id in governing:
                    self.assertIn(source_id, projected)
                for source_id in stack:
                    if records[source_id]["class"] in (
                        "structural-exemplar",
                        "watchlist",
                    ):
                        self.assertNotIn(source_id, projected)
                for item in result["applicable_sources"]:
                    self.assertIn(item["class"], ("governing", "conditional"))
                    self.assertEqual(
                        sorted(item),
                        sorted(
                            (
                                "source_id",
                                "class",
                                "title",
                                "context",
                                "status",
                                "governed_scope",
                                "applicability_boundary",
                                "precedence_scope",
                            )
                        ),
                    )
                    self.assertEqual(item["status"], "current")

    def test_a_conditional_source_applies_only_inside_its_declared_scope(self) -> None:
        from cli.practice_packs import select_practice_pack

        cases = (
            ("software-behavior-change", "web-application", ("owasp-asvs-5-0-0", "w3c-wcag-2-2", "iso-iec-40500-2025")),
            ("supported-finding", "systematic-review", ("prisma-2020",)),
            ("supported-finding", "healthcare-intervention-review", ("cochrane-handbook-6-5-1",)),
            ("audience-facing-claim", "united-states", ("ftc-advertising-authority",)),
            ("executed-service-action", "federal-information-system", ("nist-sp-800-34r1",)),
            ("governing-interpretation", "regulatory-impact-assessment", ("oecd-ria-2020",)),
        )
        for primary_outcome, scope, conditional_ids in cases:
            with self.subTest(scope=scope):
                undeclared = select_practice_pack(_envelope(primary_outcome))
                declared = select_practice_pack(
                    _envelope(primary_outcome, domain_scopes=[scope])
                )
                for source_id in conditional_ids:
                    self.assertNotIn(source_id, self._sources(undeclared))
                    self.assertIn(source_id, self._sources(declared))

    def test_a_declared_scope_never_changes_the_selection(self) -> None:
        from cli.practice_packs import select_practice_pack

        without = select_practice_pack(_envelope("software-behavior-change"))
        with_scope = select_practice_pack(
            _envelope(
                "software-behavior-change",
                domain_scopes=["web-application", "united-states"],
            )
        )
        for field in (
            "outcome",
            "pack_id",
            "ordered_match_reasons",
            "rejected_candidates",
            "bodies_loaded",
            "loaded_body_bytes",
            "body_identity",
            "body",
        ):
            self.assertEqual(without[field], with_scope[field], field)
        self.assertNotEqual(
            self._sources(without), self._sources(with_scope)
        )
        # A scope belonging to another family's conditional source grants no
        # authority here: applicability is read from the selected pack's stack.
        self.assertNotIn("ftc-advertising-authority", self._sources(with_scope))

    def test_no_source_document_text_is_projected(self) -> None:
        from cli.practice_packs import select_practice_pack

        result = select_practice_pack(
            _envelope("software-behavior-change", domain_scopes=["web-application"])
        )
        self.assertEqual(result["context_receipt"]["source_document_bytes"], 0)
        for item in result["applicable_sources"]:
            self.assertNotIn("applies_when", item)
            self.assertNotIn("refresh_trigger", item)
            self.assertLess(len(json.dumps(item).encode("utf-8")), 512)


class ContextReceiptTests(unittest.TestCase):
    """Compact routing metadata is measured separately from the one body."""

    def _recompute_routing_bytes(self, result: dict) -> int:
        projection = {
            field: (None if field == "body" else value)
            for field, value in result.items()
            if field != "context_receipt"
        }
        return len(
            json.dumps(
                projection,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )

    def test_the_receipt_separates_routing_metadata_from_the_selected_body(self) -> None:
        from cli.practice_packs import select_practice_pack

        for primary_outcome in (
            "software-behavior-change",
            "supported-finding",
            "audience-facing-claim",
            "executed-service-action",
            "governing-interpretation",
        ):
            with self.subTest(outcome=primary_outcome):
                result = select_practice_pack(_envelope(primary_outcome))
                receipt = result["context_receipt"]
                self.assertEqual(
                    receipt["routing_metadata_bytes"],
                    self._recompute_routing_bytes(result),
                )
                self.assertEqual(
                    receipt["selected_body_bytes"], result["loaded_body_bytes"]
                )
                self.assertEqual(
                    receipt["body_budget_bytes"], result["body_budget_bytes"]
                )
                self.assertEqual(receipt["unmatched_body_bytes"], 0)
                self.assertEqual(receipt["unloaded_pack_bodies"], 4)
                self.assertEqual(receipt["source_document_bytes"], 0)
                # Routing metadata stays compact: it is a small fraction of the
                # one admitted body, not a second body-sized cost.
                self.assertLess(
                    receipt["routing_metadata_bytes"], receipt["selected_body_bytes"]
                )

    def test_unmatched_bodies_and_full_sources_contribute_zero_bytes(self) -> None:
        from cli.practice_packs import select_practice_pack

        result = select_practice_pack(_envelope("software-behavior-change"))
        selected = _authored_body_bytes("software-delivery")
        self.assertEqual(result["body"].encode("utf-8"), selected)
        serialized = json.dumps(result).encode("utf-8")
        for pack_id, fingerprint in _BODY_FINGERPRINTS.items():
            if pack_id == "software-delivery":
                continue
            with self.subTest(pack=pack_id):
                self.assertNotIn(fingerprint.encode("utf-8"), serialized)
                self.assertNotIn(
                    _authored_body_bytes(pack_id)[:400], serialized
                )
        total_authored = sum(
            len(_authored_body_bytes(item["pack_id"])) for item in _catalog()
        )
        # The four unmatched bodies exist as maintenance surface and none of
        # them reaches the result.
        self.assertGreater(total_authored - len(selected), 0)
        self.assertEqual(result["context_receipt"]["unmatched_body_bytes"], 0)

    def test_every_fail_closed_outcome_returns_a_zero_body_receipt(self) -> None:
        from cli.practice_packs import select_practice_pack

        none_result = select_practice_pack(_envelope("routine-local-text-correction"))
        self.assertEqual(none_result["outcome"], "none")
        _assert_no_body_leaked(self, none_result)
        self.assertEqual(none_result["context_receipt"]["unloaded_pack_bodies"], 5)

        ambiguous = select_practice_pack(
            {
                "primary_outcomes": ["supported-finding", "audience-facing-claim"],
                "artifact_kinds": [],
                "incidental_terms": [],
                "exclusions": [],
                "lifecycle_substrate_activities": [],
            }
        )
        self.assertEqual(ambiguous["outcome"], "ambiguous")
        _assert_no_body_leaked(self, ambiguous)

        vetoed = select_practice_pack(
            _envelope("software-behavior-change", exclusions=["software-guidance"])
        )
        self.assertEqual(vetoed["outcome"], "none")
        _assert_no_body_leaked(self, vetoed)

        catalog = _catalog()
        next(item for item in catalog if item["family"] == "research")[
            "contract_version"
        ] = 99
        incompatible = select_practice_pack(
            _envelope("supported-finding"), metadata=catalog
        )
        self.assertEqual(incompatible["error"]["code"], "pack-metadata-incompatible")
        _assert_no_body_leaked(self, incompatible)


class IntegratedProjectionParityTests(unittest.TestCase):
    """CLI and MCP return one identical integrated projection."""

    def _cli(self, *args: str) -> dict:
        completed = subprocess.run(
            [sys.executable, "-m", "cli", "select-practice-pack", *args],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        return json.loads(completed.stdout)

    def test_both_surfaces_return_identity_sources_ceiling_and_receipt(self) -> None:
        from mcp_server import server

        cli_record = self._cli(
            "--primary-outcome",
            "software-behavior-change",
            "--domain-scope",
            "web-application",
        )
        server._TOOL_CACHE = None
        mcp_result = server.call_tool(
            "select_practice_pack",
            {
                "primary_outcome": ["software-behavior-change"],
                "domain_scope": ["web-application"],
            },
        )
        self.assertFalse(mcp_result["isError"])
        self.assertEqual(mcp_result["structuredContent"]["records"], [cli_record])
        self.assertEqual(cli_record["pack_id"], "software-delivery")
        self.assertTrue(cli_record["body_identity"].startswith("sha256:"))
        self.assertEqual(cli_record["body_budget_bytes"], 16384)
        self.assertIn(
            "owasp-asvs-5-0-0",
            [item["source_id"] for item in cli_record["applicable_sources"]],
        )
        self.assertEqual(cli_record["context_receipt"]["unmatched_body_bytes"], 0)

    def test_both_surfaces_expose_the_same_collision_state(self) -> None:
        from mcp_server import server

        cli_record = self._cli(
            "--primary-outcome",
            "supported-finding",
            "--primary-outcome",
            "audience-facing-claim",
        )
        server._TOOL_CACHE = None
        mcp_result = server.call_tool(
            "select_practice_pack",
            {"primary_outcome": ["supported-finding", "audience-facing-claim"]},
        )
        self.assertEqual(
            mcp_result["structuredContent"]["records"][0]["error"], cli_record["error"]
        )
        self.assertEqual(cli_record["outcome"], "ambiguous")
        _assert_no_body_leaked(self, {k: v for k, v in cli_record.items() if k != "action"})

    def test_the_result_carries_no_numeric_quality_score(self) -> None:
        from cli.practice_packs import select_practice_pack

        result = select_practice_pack(_envelope("software-behavior-change"))
        # The body is authored guidance; the automated surface is everything
        # else the result computes about it.
        computed = json.dumps(
            {key: value for key, value in result.items() if key != "body"}
        ).casefold()
        for forbidden in ("score", "grade", "rating", "rank", "percent"):
            self.assertNotIn(forbidden, computed)
        # The only numbers the result reports are measured byte counts and
        # counts of bodies, never a judgment of the guidance.
        self.assertEqual(
            sorted(
                key
                for key, value in result["context_receipt"].items()
                if isinstance(value, int)
            ),
            [
                "body_budget_bytes",
                "routing_metadata_bytes",
                "selected_body_bytes",
                "source_document_bytes",
                "unloaded_pack_bodies",
                "unmatched_body_bytes",
            ],
        )


if __name__ == "__main__":
    unittest.main()
