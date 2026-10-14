"""Unit tests for cli.deidentify (coder-handoff deidentification)."""
import unittest

from cli import deidentify
from cli.deidentify import IDENTIFIER_RE


class TestIdentifierRegex(unittest.TestCase):
    def test_matches_each_family(self):
        for token in (
            "TASK-01-002", "SPEC-12-300", "PHASE-01",
            "PROMPT-01-002", "PROMPT-PLAN-003",
            "REVIEW-01-002", "REVIEW-PLAN-003", "REPORT-01-002",
            "REPORT-01-002-review",
            "DEC-001", "BL-007", "RM-002", "FR-003", "NF-010",
            "BUILD-01-003", "P01-BUILD-003",
        ):
            self.assertEqual(IDENTIFIER_RE.findall(token), [token], token)

    def test_substring_and_boundary_safety(self):
        # Longer tokens / word-joined forms must NOT match.
        for text in ("TASK-01-0024", "xFR-001", "FR-001x", "DECODE-001", "aDEC-001"):
            self.assertEqual(IDENTIFIER_RE.findall(text), [], text)

    def test_retired_descriptive_identifiers_do_not_partially_match(self):
        for text in ("PHASE-01-build", "PROMPT-PLAN-003-slug"):
            self.assertEqual(IDENTIFIER_RE.findall(text), [], text)


class TestScrubPossessive(unittest.TestCase):
    def test_possessive_becomes_noun_phrase_not_dangling_suffix(self):
        self.assertEqual(
            deidentify.scrub_field("Origin: TASK-01-002's deliverable"),
            "Origin: a related task's deliverable",
        )

    def test_possessive_noun_follows_identifier_family(self):
        for text, expected in (
            ("SPEC-01-001’s interface", "a related spec's interface"),
            ("DEC-004's ruling", "a related decision's ruling"),
            ("FR-003's wording", "a related requirement's wording"),
            ("BUILD-01-003's scope", "a related plan item's scope"),
            ("P01-BUILD-003's scope", "a related plan item's scope"),
        ):
            with self.subTest(text=text):
                self.assertEqual(deidentify.scrub_field(text), expected)

    def test_possessive_inside_phrase_ref_keeps_its_noun(self):
        self.assertEqual(
            deidentify.scrub_field("Match per SPEC-01-001's interface."),
            "Match per a related spec's interface.",
        )

    def test_prose_without_identifiers_unchanged(self):
        self.assertEqual(deidentify.scrub_field("no ids here's fine"), "no ids here's fine")


class TestDeidentifySpec(unittest.TestCase):
    def test_open_question_placeholders_accept_list_markers(self):
        for empty in ("none", "none.", "n/a", "- none", "  - None.  ", "* none", "+ n/a"):
            with self.subTest(empty=empty):
                _, receipt = deidentify.assignment_spec_projection(
                    "# Spec\n\n## Open questions\n\n" + empty + "\n",
                )
                self.assertEqual(receipt["open_question_lines"], [])

    def test_placeholder_does_not_mask_real_open_question(self):
        for question in ("- Which error type?", "- none of the sources agree; which governs?"):
            with self.subTest(question=question):
                _, receipt = deidentify.assignment_spec_projection(
                    "# Spec\n\n## Open questions\n\n- none\n" + question + "\n",
                )
                self.assertEqual(receipt["open_question_lines"], [question])

    def _clean(self, text):
        body, _ = deidentify.deidentify_spec(text)
        return body

    def test_title_id_stripped(self):
        self.assertEqual(self._clean("# SPEC-01-002: Widgets\n").strip(), "# Widgets")

    def test_title_only_id_becomes_generic(self):
        self.assertEqual(self._clean("# SPEC-01-002\n").strip(), "# Specification")

    def test_plan_refs_line_removed(self):
        out = self._clean("# T\n\nPlan refs: BUILD-01-003\n\nbody\n")
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
