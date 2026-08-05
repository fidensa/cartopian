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


if __name__ == "__main__":
    unittest.main()
