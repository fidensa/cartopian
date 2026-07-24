"""Focused tests for authoritative compact skill metadata."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from mcp_server import server
from mcp_server.skill_metadata import (
    BRIDGE_TARGETS,
    discovery_description,
    generate_surfaces,
    load_metadata,
    validate_repository,
)


ROOT = Path(__file__).resolve().parents[2]


class TestRepositorySkillMetadata(unittest.TestCase):
    def test_repository_metadata_and_derived_surfaces_are_valid(self):
        self.assertEqual(validate_repository(ROOT), [])

    def test_mcp_prompt_and_resource_descriptions_share_authority(self):
        records = load_metadata(ROOT)
        expected = {
            record["identity"]: discovery_description(record)
            for record in records
        }
        prompts = {item["name"]: item["description"] for item in server.list_prompts()}
        resources = {
            item["uri"].removeprefix("cartopian://skills/"): item["description"]
            for item in server.list_resources()
            if item["uri"].startswith("cartopian://skills/")
        }
        self.assertEqual(prompts, expected)
        self.assertEqual(resources, expected)

    def test_metadata_covers_every_shipped_runbook(self):
        records = load_metadata(ROOT)
        declared = {record["runbook"] for record in records}
        shipped = {
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "skills").glob("*.md")
            if path.name.lower() != "readme.md"
        }
        shipped.add("install-cartopian.md")
        self.assertEqual(declared, shipped)

    def test_bridge_drift_fixture_fails_deterministically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT / "skills", root / "skills")
            shutil.copytree(ROOT / "templates", root / "templates")
            shutil.copy2(ROOT / "install-cartopian.md", root / "install-cartopian.md")
            target = root / BRIDGE_TARGETS["codex_skill"].path
            text = target.read_text(encoding="utf-8")
            target.write_text(
                text.replace(
                    "description: Enter Cartopian PM mode. Use when",
                    "description: Stale independently maintained prose. Use when",
                    1,
                ),
                encoding="utf-8",
            )

            first = validate_repository(root)
            second = validate_repository(root)

        self.assertEqual(first, second)
        self.assertEqual(first, sorted(first))
        self.assertTrue(
            any(
                "bridge[codex_skill].description: derived surface drift" in item
                for item in first
            ),
            first,
        )

    def test_duplicate_unknown_missing_and_invalid_reference_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "skills").mkdir()
            (root / "skills" / "one.md").write_text("# One\n", encoding="utf-8")
            data = {
                "schema_version": 1,
                "skills": [
                    {
                        "identity": "one",
                        "description": "Do one thing.",
                        "applicability": "Use for one thing.",
                        "runbook": "skills/missing.md",
                        "surfaces": {
                            "mcp_prompt": True,
                            "mcp_resource": True,
                            "client_bridges": ["unknown_bridge"],
                        },
                        "lifecycle": "shipped",
                        "unknown": True,
                    },
                    {
                        "identity": "one",
                        "description": "",
                        "applicability": "",
                        "runbook": "skills/one.md",
                        "surfaces": {
                            "mcp_prompt": True,
                            "mcp_resource": True,
                            "client_bridges": [],
                        },
                        "lifecycle": "shipped",
                    },
                ],
            }
            (root / "skills" / "skill-metadata.json").write_text(
                json.dumps(data),
                encoding="utf-8",
            )

            diagnostics = validate_repository(root)

        self.assertEqual(diagnostics, sorted(diagnostics))
        joined = "\n".join(diagnostics)
        self.assertIn("duplicate identity 'one'", joined)
        self.assertIn("unknown field 'unknown'", joined)
        self.assertIn("required non-empty string", joined)
        self.assertIn("runbook target does not exist: skills/missing.md", joined)
        self.assertIn("unsupported client bridge 'unknown_bridge'", joined)

    def test_generation_is_byte_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT / "skills", root / "skills")
            shutil.copytree(ROOT / "templates", root / "templates")
            shutil.copy2(ROOT / "install-cartopian.md", root / "install-cartopian.md")

            generate_surfaces(root)
            first = {
                bridge_id: (root / target.path).read_bytes()
                for bridge_id, target in BRIDGE_TARGETS.items()
            }
            generate_surfaces(root)
            second = {
                bridge_id: (root / target.path).read_bytes()
                for bridge_id, target in BRIDGE_TARGETS.items()
            }

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
