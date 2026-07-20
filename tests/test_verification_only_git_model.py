"""Verification-only prompts must carry the no-product-git operating model."""
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestVerificationOnlyPromptContract(unittest.TestCase):
    def test_assignment_and_review_prompt_rules_are_explicit(self) -> None:
        text = (ROOT / "skills/run-task.md").read_text(encoding="utf-8")
        for phrase in (
            "verification-only",
            "Cartopian git versioning is off",
            "product-repository branches are not PM-owned",
            "prior completed tasks' deliverables",
            "not evidence that this verification task modified files",
            "must not issue `request-changes` merely because `git status`",
        ):
            self.assertIn(phrase, text)

    def test_prompt_and_review_templates_preserve_rule(self) -> None:
        prompt = (ROOT / "templates/PROMPT.md").read_text(encoding="utf-8")
        review = (ROOT / "templates/REVIEW.md").read_text(encoding="utf-8")
        self.assertIn("verification-only", prompt)
        self.assertIn("pre-existing uncommitted deliverables", prompt)
        self.assertIn("verification-only", review)
        self.assertIn("Do not treat `git status` alone", review)


if __name__ == "__main__":
    unittest.main()
