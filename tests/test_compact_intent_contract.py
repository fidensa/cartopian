"""Static guardrails for the operator-facing compact intent workflow."""

from __future__ import annotations

import unittest
from pathlib import Path

from mcp_server.skill_metadata import load_metadata

ROOT = Path(__file__).resolve().parents[1]
PLANNING_ENTRIES = (
    "skills/plan-project.md",
    "skills/adopt-requirements.md",
    "skills/adopt-plan.md",
)


def _contract_section() -> str:
    text = (ROOT / "protocol" / "CONVENTIONS.md").read_text(encoding="utf-8")
    start = text.index("## Planning Intent Contract")
    end = text.index("\n## ", start + 1)
    return text[start:end]


class CompactIntentRunbookTests(unittest.TestCase):
    def test_authoritative_contract_names_fields_states_and_lock_gate(self) -> None:
        text = _contract_section()

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
        self.assertIn("operator has confirmed", text)
        self.assertIn("must not lock", text)
        self.assertIn("numerical confidence", text)
        self.assertIn("cross-model", text)

    def test_contract_forbids_pm_supplied_facts(self) -> None:
        text = _contract_section()

        self.assertIn("never supplies an operator-owned fact", text)
        self.assertIn("project name", text)
        self.assertIn("closed only by an operator answer", text)
        self.assertIn("one question per turn", text)
        self.assertIn("never presents the six fields as a form", text)
        # The assume-and-confirm mechanism is gone: no field is ever filled
        # provisionally, so the contract must not reintroduce the term as a
        # permitted behavior.
        self.assertNotIn("provisional working assumption", text)
        self.assertNotIn("states one", text)

    def test_all_planning_entries_apply_the_contract(self) -> None:
        for relative in PLANNING_ENTRIES:
            with self.subTest(relative=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("Planning Intent Contract", text)
                self.assertIn("confirmed", text)
                self.assertIn("one question per turn", text.lower())
                self.assertIn("host", text.lower())
                self.assertNotIn("working assumption", text)
                self.assertNotIn("working-assumption", text)

    def test_plan_project_interview_never_opens_with_the_checklist(self) -> None:
        text = (ROOT / "skills" / "plan-project.md").read_text(encoding="utf-8")

        self.assertIn("### 1.2 Interview the operator", text)
        self.assertIn("Do not begin by listing\nwhat a plan requires", text)
        self.assertIn("Never fill a gap yourself", text)
        self.assertIn("Challenge vague statements", text)
        self.assertIn("Confirm the record once", text)

    def test_requirements_template_confirmed_intent_is_operator_owned(self) -> None:
        text = (ROOT / "templates" / "REQUIREMENTS.md").read_text(encoding="utf-8")

        self.assertIn("## Confirmed intent", text)
        self.assertIn("never a PM assumption", text)

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
