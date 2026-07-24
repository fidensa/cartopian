"""Regression contract for startup and assignment-launch guidance.

The startup projections are owned by the compact skill metadata.  MCP prompt,
MCP resource, and installed bridge surfaces must state the same registry-first
outcome while giving each surface a directly executable action.

Assignment prose is checked against the already-tested dispatcher and wrapper
contract: project-root cwd, ordered resolved work roots, and honest per-wrapper
sandbox widening.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from mcp_server import server
from mcp_server.skill_metadata import BRIDGE_TARGETS


ROOT = Path(__file__).resolve().parents[1]
METADATA = ROOT / "skills" / "skill-metadata.json"
IMPOSSIBLE_THEN_FALLBACK = re.compile(
    r"\b(?:invoke|run|call)\b[^\n]{0,100}\bprompt\b"
    r"[^\n]{0,220}\b(?:cannot|can't|unable|fallback|instead|or\s+read)\b",
    re.IGNORECASE,
)


def _entry_record() -> dict:
    data = json.loads(METADATA.read_text(encoding="utf-8"))
    return next(
        record for record in data["skills"]
        if record["identity"] == "use_cartopian"
    )


class TestStartupGuidanceContract(unittest.TestCase):
    def test_mcp_prompt_and_resource_use_surface_appropriate_actions(self) -> None:
        startup = _entry_record()["startup"]
        prompt_text = server.get_prompt("use_cartopian")["messages"][0]["content"]["text"]
        resource_text = server.read_resource(
            "cartopian://skills/use_cartopian"
        )["contents"][0]["text"]

        self.assertEqual(
            prompt_text.count(f"**Startup outcome:** {startup['outcome']}"),
            1,
        )
        self.assertEqual(
            prompt_text.count(
                f"**Startup action:** {startup['mcp_prompt_action']}"
            ),
            1,
        )
        self.assertEqual(
            resource_text.count(f"**Startup outcome:** {startup['outcome']}"),
            1,
        )
        self.assertEqual(
            resource_text.count(
                f"**Startup action:** {startup['mcp_resource_action']}"
            ),
            1,
        )
        self.assertIsNone(IMPOSSIBLE_THEN_FALLBACK.search(prompt_text))
        self.assertIsNone(IMPOSSIBLE_THEN_FALLBACK.search(resource_text))

    def test_every_installed_bridge_projects_the_supported_resource_action(self) -> None:
        startup = _entry_record()["startup"]
        for bridge_id, target in BRIDGE_TARGETS.items():
            with self.subTest(bridge=bridge_id):
                text = (ROOT / target.path).read_text(encoding="utf-8")
                self.assertEqual(
                    text.count(f"**Startup outcome:** {startup['outcome']}"),
                    1,
                )
                self.assertEqual(
                    text.count(
                        f"**Startup action:** {startup['client_bridge_action']}"
                    ),
                    1,
                )
                self.assertIsNone(IMPOSSIBLE_THEN_FALLBACK.search(text))

    def test_initialize_directs_a_resource_read_without_impossible_fallback(self) -> None:
        startup = _entry_record()["startup"]
        text = server._server_instructions()
        self.assertIn(startup["outcome"], text)
        self.assertIn(startup["mcp_host_action"], text)
        self.assertIsNone(IMPOSSIBLE_THEN_FALLBACK.search(text))


class TestAssignmentLaunchGuidanceContract(unittest.TestCase):
    def test_assignment_template_matches_dispatch_and_wrapper_behavior(self) -> None:
        text = (ROOT / "templates" / "PROMPT.md").read_text(encoding="utf-8")
        lowered = text.lower()

        self.assertIn(
            "launched with cwd set to the **cartopian project root**",
            lowered,
        )
        self.assertNotIn("cwd set to the **primary work root**", lowered)
        self.assertIn("cartopian_work_roots", lowered)
        self.assertIn("declared work-root access does not grant", lowered)
        for wrapper in ("codex", "claude", "gemini", "devin"):
            self.assertIn(wrapper, lowered)
        self.assertIn("may be unwritable", lowered)

    def test_handoff_runbook_states_the_same_launch_boundary(self) -> None:
        text = (ROOT / "skills" / "run-handoff.md").read_text(encoding="utf-8")
        lowered = text.lower()

        self.assertIn(
            "assignee clis run with cwd set to the cartopian project root",
            lowered,
        )
        self.assertIn("cartopian_work_roots", lowered)
        self.assertIn("does not grant pm lifecycle authority", lowered)
        self.assertIn("codex", lowered)
        self.assertIn("claude", lowered)
        self.assertIn("gemini", lowered)
        self.assertIn("devin", lowered)
        self.assertIn("may be unwritable", lowered)


if __name__ == "__main__":
    unittest.main()
