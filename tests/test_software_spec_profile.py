"""Static regression coverage for conditional software specification rules."""
from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CONVENTIONS = REPO_ROOT / "protocol" / "CONVENTIONS.md"
SPEC_TEMPLATE = REPO_ROOT / "templates" / "SPEC.md"
PLANNING_SKILLS = (
    REPO_ROOT / "skills" / "plan-project.md",
    REPO_ROOT / "skills" / "adopt-plan.md",
)

SRS_SECTIONS = (
    "Overview & Goals",
    "Functional Requirements",
    "Non-Functional Requirements",
    "User Stories & Use Cases",
)
TDS_SECTIONS = (
    "Architecture & Structure",
    "Data Models",
    "APIs & Integrations",
    "Edge Cases & Error Handling",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class SoftwareSpecProtocolTest(unittest.TestCase):
    def test_protocol_classifies_the_spec_outcome_not_the_project(self) -> None:
        text = _read(CONVENTIONS)
        self.assertIn("Every spec declares `Profile: software | general`", text)
        self.assertIn("outcome governed by that spec", text)
        self.assertIn("A software project may still use the general profile", text)
        self.assertIn("A generally non-software project still uses the software profile", text)

    def test_protocol_defines_srs_tds_and_prohibits_implementation_code(self) -> None:
        text = _read(CONVENTIONS)
        for heading in SRS_SECTIONS + TDS_SECTIONS:
            self.assertIn(f"**{heading}**", text)
        for guardrail in (
            "source or executable code",
            "pseudocode",
            "copy/paste-ready implementation snippets",
            "Contract notation is allowed",
        ):
            self.assertIn(guardrail, text)


class SoftwareSpecTemplateTest(unittest.TestCase):
    def test_template_has_explicit_profiles_and_all_software_sections(self) -> None:
        text = _read(SPEC_TEMPLATE)
        self.assertIn("Profile: <software | general>", text)
        self.assertIn("## SRS", text)
        self.assertIn("## TDS", text)
        for heading in SRS_SECTIONS + TDS_SECTIONS:
            self.assertIn(f"### {heading}", text)

    def test_review_checklist_rejects_copy_paste_implementation(self) -> None:
        text = _read(SPEC_TEMPLATE)
        self.assertIn("exactly one profile remains", text)
        self.assertIn("copy/paste-ready implementation", text)
        self.assertIn("field/type definitions", text)


class PlanningSkillSoftwareSpecTest(unittest.TestCase):
    def test_both_planners_apply_the_profile_and_code_boundary(self) -> None:
        for path in PLANNING_SKILLS:
            with self.subTest(skill=path.name):
                text = _read(path)
                self.assertIn("Profile", text)
                self.assertIn("classify the spec itself", text.lower())
                self.assertIn("copy/paste-ready implementation", text)
                self.assertIn("blocking finding", text)
                for heading in SRS_SECTIONS + TDS_SECTIONS:
                    self.assertIn(f"**{heading}**", text)


if __name__ == "__main__":
    unittest.main()
