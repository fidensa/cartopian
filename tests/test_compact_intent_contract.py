"""Static guardrails for the operator-facing compact intent workflow."""

from __future__ import annotations

import unittest
from pathlib import Path

from mcp_server.skill_metadata import load_metadata

ROOT = Path(__file__).resolve().parents[1]


class CompactIntentRunbookTests(unittest.TestCase):
    def test_authoritative_contract_names_fields_states_and_lock_gate(self) -> None:
        text = (ROOT / "protocol" / "CONVENTIONS.md").read_text(encoding="utf-8")

        self.assertIn("## Planning Intent Contract", text)
        for field in (
            "outcome",
            "beneficiary",
            "why now",
            "success signal",
            "binding constraint",
            "explicit exclusions",
        ):
            self.assertIn(field, text)
        for state in ("`present`", "`missing`", "`conflicting`"):
            self.assertIn(state, text)
        self.assertIn("working assumption", text)
        self.assertIn("operator confirmation", text)
        self.assertIn("must not lock", text)
        self.assertIn("numerical confidence", text)
        self.assertIn("cross-model confirmation", text)

    def test_all_planning_entries_apply_the_contract(self) -> None:
        for relative in (
            "skills/plan-project.md",
            "skills/adopt-requirements.md",
            "skills/adopt-plan.md",
        ):
            with self.subTest(relative=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("Planning Intent Contract", text)
                self.assertIn("confirmed", text)

    def test_task_generation_remains_current_phase_only_and_bounded(self) -> None:
        for relative in ("skills/plan-project.md", "skills/adopt-plan.md"):
            with self.subTest(relative=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("current active phase", text)
                self.assertIn("future-phase", text)
                self.assertIn("does not authorize running it", text)

    def test_use_cartopian_remains_the_only_client_entry(self) -> None:
        records = load_metadata(ROOT)
        entries = [
            record["identity"]
            for record in records
            if record["surfaces"]["client_bridges"]
        ]

        self.assertEqual(entries, ["use_cartopian"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
