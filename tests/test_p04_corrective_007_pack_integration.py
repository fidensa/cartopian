"""Integration proof for the five source-backed packs.

The deterministic pack, risk, judgment, and source contracts already have
focused suites. This module proves the three integration surfaces those suites
deliberately leave open:

1. the durable semantic-review record, which is the only thing that can
   establish guidance quality — and which must stay bound to the exact bytes it
   reviewed rather than inheriting its dispositions across an edit;
2. CLI/MCP structured-result parity for every one of the five packs, plus the
   no-match and collision outcomes, not just a representative pack;
3. the standard-library-only runtime boundary for the Phase 04 modules.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import tomllib
import unittest
from hashlib import sha256
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = REPO_ROOT / "protocol" / "risk-and-practice-contract.json"
REVIEW_RECORD_PATH = REPO_ROOT / "tests" / "acceptance" / "practice-pack-semantic-review.md"

_PACK_HEADING = re.compile(r"^## ([a-z][a-z0-9]*(?:-[a-z0-9]+)*)$", re.MULTILINE)
_DIMENSION_HEADING = re.compile(r"^### (.+?)\s*$")
_IDENTITY_LINE = re.compile(r"^- Body content identity: `(sha256:[0-9a-f]{64})`$")
_QUOTE_LINE = re.compile(r"^- Quoted from the body: `(.+)`$")
_DISPOSITION_LINE = re.compile(r"^- Disposition: (.+?)\s*$")
_OBSERVATION_LINE = re.compile(r"^- Observation: (.+)$")


def _registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _catalog() -> list[dict]:
    return _registry()["fixtures"]["pack_candidates"]


def _authored_body(pack_id: str) -> str:
    return (REPO_ROOT / "protocol" / "packs" / f"{pack_id}.md").read_text(
        encoding="utf-8"
    )


def _first_positive_value(metadata: dict) -> str:
    """The declared fact that positively selects this pack, from the authority."""
    condition = metadata["applies_when"][0]
    return (
        condition["value"] if "value" in condition else condition["any_of"][0]
    )


def _parse_review_record() -> dict[str, dict]:
    """Parse the durable record into pack -> {identity, dimensions}.

    Parsing is strict on purpose: an entry that does not carry an observation,
    a verbatim excerpt, and a disposition is not a reviewer observation, and a
    lenient parser would let it count as one.
    """
    text = REVIEW_RECORD_PATH.read_text(encoding="utf-8")
    known = {item["pack_id"] for item in _catalog()}
    packs: dict[str, dict] = {}
    pack: str | None = None
    dimension: str | None = None
    for line in text.splitlines():
        heading = _PACK_HEADING.fullmatch(line)
        if heading:
            pack = heading.group(1) if heading.group(1) in known else None
            dimension = None
            if pack:
                packs[pack] = {"identity": None, "dimensions": {}}
            continue
        if pack is None:
            continue
        heading = _DIMENSION_HEADING.fullmatch(line)
        if heading:
            dimension = heading.group(1)
            packs[pack]["dimensions"][dimension] = {
                "observation": None,
                "quotes": [],
                "disposition": None,
            }
            continue
        if dimension is None:
            identity = _IDENTITY_LINE.fullmatch(line)
            if identity:
                packs[pack]["identity"] = identity.group(1)
            continue
        entry = packs[pack]["dimensions"][dimension]
        observation = _OBSERVATION_LINE.fullmatch(line)
        if observation:
            entry["observation"] = observation.group(1)
        quote = _QUOTE_LINE.fullmatch(line)
        if quote:
            entry["quotes"].append(quote.group(1))
        disposition = _DISPOSITION_LINE.fullmatch(line)
        if disposition:
            entry["disposition"] = disposition.group(1)
    return packs


class PackSemanticReviewEvidenceTests(unittest.TestCase):
    """Durable reviewer evidence, bound to the bytes it actually reviewed."""

    def setUp(self) -> None:
        self.assertTrue(
            REVIEW_RECORD_PATH.is_file(),
            "the durable semantic-review record does not exist; structural "
            "validation alone cannot establish pack guidance quality",
        )
        self.record = _parse_review_record()
        self.contract = _registry()["packs"]["semantic_review"]

    def test_the_record_covers_every_approved_pack_and_declared_dimension(self) -> None:
        declared = {item["id"] for item in self.contract["dimensions"]}
        self.assertEqual(
            set(self.record), {item["pack_id"] for item in _catalog()}
        )
        for pack_id, entry in self.record.items():
            with self.subTest(pack=pack_id):
                self.assertEqual(set(entry["dimensions"]), declared)

    def test_every_dimension_carries_an_observation_and_a_disposition(self) -> None:
        for pack_id, entry in self.record.items():
            for dimension, item in entry["dimensions"].items():
                with self.subTest(pack=pack_id, dimension=dimension):
                    self.assertIsNotNone(item["observation"])
                    # An observation short enough to be a heading restatement
                    # is not a direct observation of the body.
                    self.assertGreater(len(item["observation"]), 40)
                    self.assertIn(item["disposition"], ("adequate", "inadequate"))
                    self.assertTrue(item["quotes"])

    def test_every_recorded_excerpt_appears_verbatim_in_the_reviewed_body(self) -> None:
        """The record inspected contents, not headings: prove it byte for byte."""
        for pack_id, entry in self.record.items():
            body = _authored_body(pack_id)
            for dimension, item in entry["dimensions"].items():
                for quote in item["quotes"]:
                    with self.subTest(pack=pack_id, dimension=dimension):
                        self.assertIn(quote, body)

    def test_the_record_is_bound_to_the_exact_body_it_reviewed(self) -> None:
        """An edited body invalidates the review instead of inheriting it."""
        declared = {
            item["pack_id"]: item["body_content_identity"] for item in _catalog()
        }
        for pack_id, entry in self.record.items():
            with self.subTest(pack=pack_id):
                on_disk = "sha256:" + sha256(
                    (REPO_ROOT / "protocol" / "packs" / f"{pack_id}.md").read_bytes()
                ).hexdigest()
                self.assertEqual(entry["identity"], declared[pack_id])
                self.assertEqual(entry["identity"], on_disk)

    def test_the_record_carries_dispositions_and_never_a_numeric_score(self) -> None:
        text = REVIEW_RECORD_PATH.read_text(encoding="utf-8")
        for pattern in (
            r"^\s*-\s*(score|grade|rating|points|weight|percentage)\s*:",
            r"\b\d+\s*/\s*\d+\b",
            r"\b\d+(\.\d+)?\s*(%|points|pts)\b",
        ):
            with self.subTest(pattern=pattern):
                self.assertIsNone(
                    re.search(pattern, text, re.IGNORECASE | re.MULTILINE),
                    f"the record must not carry a numeric quality score: {pattern}",
                )
        dispositions = {
            item["disposition"]
            for entry in self.record.values()
            for item in entry["dimensions"].values()
        }
        self.assertTrue(dispositions <= {"adequate", "inadequate"})

    def test_the_record_names_what_cannot_satisfy_semantic_review(self) -> None:
        text = REVIEW_RECORD_PATH.read_text(encoding="utf-8")
        for item in self.contract["not_satisfied_by"]:
            with self.subTest(disqualifier=item["id"]):
                self.assertIn(item["id"], text)


class FivePackSurfaceParityTests(unittest.TestCase):
    """One authoritative result, projected identically by CLI and MCP."""

    def _cli(self, *args: str) -> tuple[int, dict]:
        completed = subprocess.run(
            [sys.executable, "-m", "cli", "select-practice-pack", *args],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        return completed.returncode, json.loads(completed.stdout)

    def _mcp(self, arguments: dict) -> tuple[int, dict]:
        """MCP projects the same record and the same exit status, including
        when the outcome is a fail-closed guard."""
        from mcp_server import server

        server._TOOL_CACHE = None
        result = server.call_tool("select_practice_pack", arguments)
        structured = result["structuredContent"]
        self.assertEqual(result["isError"], structured["exit_code"] != 0)
        records = structured["records"]
        self.assertEqual(len(records), 1)
        return structured["exit_code"], records[0]

    def test_every_pack_returns_one_identical_record_across_cli_and_mcp(self) -> None:
        for metadata in _catalog():
            pack_id = metadata["pack_id"]
            outcome = _first_positive_value(metadata)
            with self.subTest(pack=pack_id):
                status, cli_record = self._cli("--primary-outcome", outcome)
                self.assertEqual(status, 0, cli_record)
                self.assertEqual(
                    self._mcp({"primary_outcome": [outcome]}), (status, cli_record)
                )
                self.assertEqual(cli_record["outcome"], "selected")
                self.assertEqual(cli_record["pack_id"], pack_id)
                self.assertEqual(cli_record["bodies_loaded"], 1)
                self.assertEqual(
                    cli_record["body_identity"], metadata["body_content_identity"]
                )
                receipt = cli_record["context_receipt"]
                self.assertEqual(receipt["unmatched_body_bytes"], 0)
                self.assertEqual(receipt["unloaded_pack_bodies"], 4)
                self.assertEqual(receipt["source_document_bytes"], 0)

    def test_every_selected_body_stays_inside_its_declared_ceiling(self) -> None:
        """The measured ceiling is a bound, never a target: no minimum is asserted."""
        for metadata in _catalog():
            outcome = _first_positive_value(metadata)
            with self.subTest(pack=metadata["pack_id"]):
                _, record = self._cli("--primary-outcome", outcome)
                self.assertEqual(record["body_budget_bytes"], 16384)
                self.assertLessEqual(
                    record["loaded_body_bytes"], record["body_budget_bytes"]
                )
                self.assertEqual(
                    record["loaded_body_bytes"],
                    len(_authored_body(metadata["pack_id"]).encode("utf-8")),
                )
                self.assertLess(
                    record["context_receipt"]["routing_metadata_bytes"],
                    record["body_budget_bytes"],
                )

    def test_no_match_and_collision_stay_identical_across_surfaces(self) -> None:
        status, none_record = self._cli()
        self.assertEqual(status, 0)
        self.assertEqual(self._mcp({}), (status, none_record))
        self.assertEqual(none_record["outcome"], "none")
        self.assertIsNone(none_record["body"])
        self.assertEqual(none_record["context_receipt"]["unloaded_pack_bodies"], 5)

        status, collision = self._cli(
            "--primary-outcome",
            "supported-finding",
            "--primary-outcome",
            "audience-facing-claim",
        )
        self.assertEqual(status, 1)
        self.assertEqual(
            self._mcp(
                {"primary_outcome": ["supported-finding", "audience-facing-claim"]}
            ),
            (status, collision),
        )
        self.assertEqual(collision["outcome"], "ambiguous")
        self.assertEqual(collision["loaded_body_bytes"], 0)
        self.assertIsNone(collision["body"])


class Phase04RuntimeBoundaryTests(unittest.TestCase):
    """No third-party runtime dependency enters through the Phase 04 modules."""

    RUNTIME_MODULES = (
        Path("cli") / "practice_packs.py",
        Path("cli") / "risk_contract.py",
        Path("cli") / "judgment_guidance.py",
        Path("cli") / "source_guidance.py",
        Path("cli") / "commands" / "select_practice_pack.py",
        Path("cli") / "commands" / "select_judgment_guidance.py",
        Path("cli") / "commands" / "classify_risk.py",
        Path("cli") / "commands" / "adversarial_review_context.py",
    )

    def test_phase_04_runtime_imports_only_the_standard_library(self) -> None:
        allowed = set(sys.stdlib_module_names) | {"cli"}
        for relative in self.RUNTIME_MODULES:
            with self.subTest(module=str(relative)):
                path = REPO_ROOT / relative
                self.assertTrue(path.is_file(), str(relative))
                tree = ast.parse(path.read_text(encoding="utf-8"))
                roots: set[str] = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        roots.update(
                            alias.name.split(".", 1)[0] for alias in node.names
                        )
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        roots.add(node.module.split(".", 1)[0])
                self.assertEqual(roots - allowed, set())

    def test_the_packaged_runtime_declares_no_dependency(self) -> None:
        metadata = tomllib.loads(
            (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(metadata["project"]["dependencies"], [])
        self.assertEqual(metadata["project"]["requires-python"], ">=3.11")


if __name__ == "__main__":
    unittest.main()
