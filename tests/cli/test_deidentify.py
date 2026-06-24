"""Unit tests for cli.deidentify (coder-handoff deidentification)."""
import unittest

from cli import deidentify
from cli.deidentify import IDENTIFIER_RE


class TestIdentifierRegex(unittest.TestCase):
    def test_matches_each_family(self):
        for token in (
            "TASK-01-002", "SPEC-12-300", "PHASE-01-build-x",
            "PROMPT-01-002", "PROMPT-PLAN-003", "PROMPT-PLAN-003-slug",
            "REVIEW-01-002", "REVIEW-PLAN-003", "REPORT-01-002",
            "DEC-001", "BL-007", "RM-002", "FR-003", "NF-010",
            "P01-BUILD-003",
        ):
            self.assertEqual(IDENTIFIER_RE.findall(token), [token], token)

    def test_substring_and_boundary_safety(self):
        # Longer tokens / word-joined forms must NOT match.
        for text in ("TASK-01-0024", "xFR-001", "FR-001x", "DECODE-001", "aDEC-001"):
            self.assertEqual(IDENTIFIER_RE.findall(text), [], text)


class TestDeidentifySpec(unittest.TestCase):
    def _clean(self, text):
        body, _ = deidentify.deidentify_spec(text)
        return body

    def test_title_id_stripped(self):
        self.assertEqual(self._clean("# SPEC-01-002: Widgets\n").strip(), "# Widgets")

    def test_title_only_id_becomes_generic(self):
        self.assertEqual(self._clean("# SPEC-01-002\n").strip(), "# Specification")

    def test_plan_refs_line_removed(self):
        out = self._clean("# T\n\nPlan refs: P01-BUILD-003\n\nbody\n")
        self.assertNotIn("Plan refs", out)
        self.assertIn("body", out)

    def test_references_section_removed_until_next_heading(self):
        out = self._clean(
            "# T\n\n## References\n\n- SPEC-01-001\n- DEC-001\n\n## Goal\n\nship it\n"
        )
        self.assertNotIn("## References", out)
        self.assertNotIn("SPEC-01-001", out)
        self.assertIn("## Goal", out)
        self.assertIn("ship it", out)

    def test_inline_paren_and_phrase_refs_removed(self):
        out = self._clean("# T\n\nDo X (see SPEC-01-001) per FR-003.\n")
        self.assertNotIn("SPEC-01-001", out)
        self.assertNotIn("FR-003", out)
        self.assertIn("Do X", out)

    def test_bullet_label_stripped_keeps_marker_and_prose(self):
        out = self._clean("# T\n\n- FR-003: The widget renders.\n")
        self.assertIn("- The widget renders.", out)
        self.assertNotIn("FR-003", out)

    def test_code_fence_tokens_scrubbed_body_preserved(self):
        out = self._clean(
            "# T\n\n```python\ndef f():  # FR-003\n    return 1\n```\n"
        )
        self.assertNotIn("FR-003", out)
        self.assertIn("def f():", out)
        self.assertIn("    return 1", out)  # indentation preserved

    def test_redactions_list_complete_and_unique(self):
        _, red = deidentify.deidentify_spec(
            "# SPEC-01-002: T\n\nFR-003 and FR-003 and DEC-001\n"
        )
        self.assertEqual(red, ["DEC-001", "FR-003", "SPEC-01-002"])

    def test_clean_spec_unchanged_in_substance(self):
        src = "# Title\n\n## Goal\n\nDeliver the thing.\n"
        out, red = deidentify.deidentify_spec(src)
        self.assertEqual(red, [])
        self.assertIn("Deliver the thing.", out)
        self.assertIn("# Title", out)


if __name__ == "__main__":
    unittest.main()
