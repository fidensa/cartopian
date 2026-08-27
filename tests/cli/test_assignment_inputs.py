"""Regression contract for the typed assignment-input payload channel.

Proves the properties the channel exists for:

- Machine-created payloads round-trip exactly: UTF-8 bytes, trailing
  whitespace, and the final newline are preserved through render and
  structural extraction, whatever the content — fenced JSON, Cartopian
  identifiers, nested backtick/tilde fences, fence-length collisions.
- Contamination and deidentification validation inspect only the
  instruction channel: payload contents that would fail as authored
  instructions pass as payloads, and the identical content outside a
  machine-owned payload still fails.
- Hand-authored text cannot declare itself a trusted payload: authored
  bodies with payload declarations are refused, and mutated, missing,
  duplicated, or wrongly bound payloads fail verification and preflight.
- The fence tracker follows delimiter type and length, so nested shorter
  fences never close a generated outer fence.
"""
import unittest

from cli import assignment_inputs, prompt_composer
from cli.assignment_inputs import (
    CHANNEL_DEPENDENCY,
    CHANNEL_EXISTING,
    extract_payload_blocks,
    render_payload_block,
    strip_payload_blocks,
    verify_bound_payload,
)
from cli.markdown_fences import FenceTracker


def _roundtrip(text: str, channel: str = CHANNEL_EXISTING, logical: str = "project:resources/doc.md") -> str:
    block = render_payload_block(channel, logical, text)
    prompt = (
        "# Assignment\n\n"
        f"## {assignment_inputs.CHANNEL_SECTIONS[channel]}\n\n{block}\n"
    )
    (entry,) = extract_payload_blocks(prompt)
    assert entry["verified"], entry
    return entry["content"]


class TestFenceTracker(unittest.TestCase):
    def _delims(self, text: str):
        tracker = FenceTracker()
        return [tracker.feed(line) for line in text.splitlines()]

    def test_shorter_inner_fence_does_not_close_outer(self) -> None:
        text = "````text\n```json\n{}\n```\n````\nafter"
        self.assertEqual(
            self._delims(text), [True, False, False, False, True, False]
        )

    def test_tilde_fence_does_not_close_backtick_fence(self) -> None:
        text = "```text\n~~~\ncontent\n~~~\n```"
        self.assertEqual(
            self._delims(text), [True, False, False, False, True]
        )

    def test_longer_closer_closes(self) -> None:
        tracker = FenceTracker()
        tracker.feed("```")
        self.assertTrue(tracker.feed("`````"))
        self.assertFalse(tracker.in_fence)

    def test_closer_with_trailing_text_is_content(self) -> None:
        tracker = FenceTracker()
        tracker.feed("```")
        self.assertFalse(tracker.feed("``` not a closer"))
        self.assertTrue(tracker.in_fence)

    def test_backtick_info_string_with_backtick_is_not_an_opener(self) -> None:
        tracker = FenceTracker()
        self.assertFalse(tracker.feed("``` has a ` tick"))
        self.assertFalse(tracker.in_fence)

    def test_equal_length_same_char_closes(self) -> None:
        tracker = FenceTracker()
        tracker.feed("~~~~info")
        self.assertFalse(tracker.feed("~~~"))
        self.assertTrue(tracker.feed("~~~~"))
        self.assertFalse(tracker.in_fence)


class TestPayloadRoundTrip(unittest.TestCase):
    def test_fenced_json_and_identifiers_round_trip(self) -> None:
        text = (
            "# REQUIREMENTS\n\n"
            "Derived from TASK-01-002 and SPEC-01-002 (see DEC-001).\n\n"
            "```json\n{\"functional\": [\"FR-001\", \"FR-002\"]}\n```\n"
        )
        self.assertEqual(_roundtrip(text), text)

    def test_trailing_whitespace_and_no_final_newline(self) -> None:
        text = "line with trailing spaces   \n\tindented\nno final newline"
        self.assertEqual(_roundtrip(text), text)

    def test_final_newline_preserved(self) -> None:
        self.assertEqual(_roundtrip("one\ntwo\n"), "one\ntwo\n")

    def test_trailing_blank_lines_preserved(self) -> None:
        text = "content\n\n\n"
        self.assertEqual(_roundtrip(text), text)

    def test_crlf_content_round_trips(self) -> None:
        text = "first\r\nsecond\r\n"
        self.assertEqual(_roundtrip(text), text)

    def test_nested_and_colliding_fences_round_trip(self) -> None:
        text = (
            "````\nfour-backtick block\n````\n\n"
            "~~~~~\ntilde block\n~~~~~\n\n"
            "```python\ncode\n```\n"
        )
        self.assertEqual(_roundtrip(text), text)

    def test_non_ascii_utf8_round_trips(self) -> None:
        text = "café — ünïcode ✓\n带中文的内容\n"
        self.assertEqual(_roundtrip(text), text)

    def test_empty_payload_round_trips(self) -> None:
        self.assertEqual(_roundtrip(""), "")

    def test_dependency_channel_round_trips(self) -> None:
        text = "interface D2 { apply(a, b) }\n"
        self.assertEqual(
            _roundtrip(text, channel=CHANNEL_DEPENDENCY), text
        )


class TestVerification(unittest.TestCase):
    LOGICAL = "project:resources/doc.md"

    def _prompt(self, block: str) -> str:
        return f"# A\n\n## Existing deliverable input\n\n{block}\n"

    def test_bound_payload_verifies(self) -> None:
        text = "exact content\n"
        prompt = self._prompt(
            render_payload_block(CHANNEL_EXISTING, self.LOGICAL, text)
        )
        result = verify_bound_payload(
            prompt, CHANNEL_EXISTING, self.LOGICAL, text.encode("utf-8")
        )
        self.assertEqual(result["state"], "bound")

    def test_missing_payload_fails(self) -> None:
        result = verify_bound_payload(
            "# A\n\nno payload\n", CHANNEL_EXISTING, self.LOGICAL, b"x\n"
        )
        self.assertEqual(result["state"], "missing")

    def test_stale_payload_fails_against_current_resource(self) -> None:
        prompt = self._prompt(
            render_payload_block(CHANNEL_EXISTING, self.LOGICAL, "old\n")
        )
        result = verify_bound_payload(
            prompt, CHANNEL_EXISTING, self.LOGICAL, b"new\n"
        )
        self.assertEqual(result["state"], "mismatch")

    def test_duplicate_payload_fails(self) -> None:
        block = render_payload_block(CHANNEL_EXISTING, self.LOGICAL, "x\n")
        result = verify_bound_payload(
            self._prompt(f"{block}\n\n{block}"),
            CHANNEL_EXISTING,
            self.LOGICAL,
            b"x\n",
        )
        self.assertEqual(result["state"], "duplicate")

    def test_hand_mutated_block_content_fails_self_verification(self) -> None:
        block = render_payload_block(CHANNEL_EXISTING, self.LOGICAL, "x\n")
        tampered = block.replace("x\n", "y\n")
        (entry,) = extract_payload_blocks(self._prompt(tampered))
        self.assertFalse(entry["verified"])
        self.assertEqual(entry["error"], "binding-mismatch")
        result = verify_bound_payload(
            self._prompt(tampered), CHANNEL_EXISTING, self.LOGICAL, b"y\n"
        )
        self.assertEqual(result["state"], "mismatch")

    def test_wrong_channel_is_missing(self) -> None:
        prompt = self._prompt(
            render_payload_block(CHANNEL_EXISTING, self.LOGICAL, "x\n")
        )
        result = verify_bound_payload(
            prompt, CHANNEL_DEPENDENCY, self.LOGICAL, b"x\n"
        )
        self.assertEqual(result["state"], "missing")

    def test_unterminated_block_is_an_error(self) -> None:
        block = render_payload_block(CHANNEL_EXISTING, self.LOGICAL, "x\n")
        opener_and_body = "\n".join(block.splitlines()[:-1])
        (entry,) = extract_payload_blocks(self._prompt(opener_and_body))
        self.assertEqual(entry["error"], "unterminated")

    def test_malformed_declaration_is_an_error(self) -> None:
        prompt = self._prompt(
            "````cartopian-input channel=bogus logical=x bytes=2 "
            "sha256=" + "0" * 64 + "\nx\n````"
        )
        (entry,) = extract_payload_blocks(prompt)
        self.assertEqual(entry["error"], "malformed-declaration")

    def test_audit_flags_unexpected_and_stale_payloads(self) -> None:
        text = "current\n"
        good = render_payload_block(CHANNEL_EXISTING, self.LOGICAL, text)
        expected = [
            assignment_inputs.payload_binding(
                CHANNEL_EXISTING, self.LOGICAL, text
            )
        ]
        ok = assignment_inputs.audit_payloads(self._prompt(good), expected)
        self.assertTrue(ok["ok"], ok)

        smuggled = render_payload_block(
            CHANNEL_EXISTING, "project:resources/other.md", "smuggled\n"
        )
        bad = assignment_inputs.audit_payloads(
            self._prompt(f"{good}\n\n{smuggled}"), expected
        )
        self.assertFalse(bad["ok"])
        self.assertIn("not a machine-resolved assignment input", bad["problems"][0])

        stale = render_payload_block(CHANNEL_EXISTING, self.LOGICAL, "stale\n")
        drift = assignment_inputs.audit_payloads(self._prompt(stale), expected)
        self.assertFalse(drift["ok"])

        dupes = assignment_inputs.audit_payloads(
            self._prompt(f"{good}\n\n{good}"), expected
        )
        self.assertFalse(dupes["ok"])


class TestChannelAwareValidation(unittest.TestCase):
    """The same bytes pass as a machine payload and fail as instructions."""

    CONTAMINATED = (
        "# REQUIREMENTS for TASK-01-002\n\n"
        "Read protocol/CONVENTIONS.md for details, then run "
        "`cartopian move-task` when done.\n\n"
        "```json\n{\"ids\": [\"FR-001\", \"SPEC-01-002\"]}\n```\n"
    )
    LOGICAL = "project:resources/REQUIREMENTS.md"

    def _validate(self, body: str, **kwargs):
        contract = prompt_composer.load_contract()
        return prompt_composer.validate_prompt(
            body, contract, structural=False, **kwargs
        )

    def test_payload_contents_are_exempt(self) -> None:
        block = render_payload_block(
            CHANNEL_EXISTING, self.LOGICAL, self.CONTAMINATED
        )
        body = f"## Existing deliverable input\n\n{block}\n"
        manifest = [
            assignment_inputs.payload_binding(
                CHANNEL_EXISTING, self.LOGICAL, self.CONTAMINATED
            )
        ]
        findings = self._validate(body, input_payloads=manifest)
        self.assertEqual(findings, [])

    def test_same_content_outside_payload_fails(self) -> None:
        body = f"## Existing deliverable input\n\n{self.CONTAMINATED}\n"
        codes = {item["code"] for item in self._validate(body)}
        self.assertIn("raw-diagnostic-json", codes)
        self.assertIn("blanket-governance-read", codes)
        self.assertIn("pm-lifecycle-instruction", codes)
        self.assertIn("pm-identifier-present", codes)

    def test_identifiers_in_plain_fences_still_fail(self) -> None:
        # A neutral fence is not a payload: no exemption without the machine
        # binding.
        body = "## Notes\n\n```text\nImplements TASK-01-001.\n```\n"
        codes = {item["code"] for item in self._validate(body)}
        self.assertIn("pm-identifier-present", codes)

    def test_authored_body_cannot_declare_a_payload(self) -> None:
        block = render_payload_block(CHANNEL_EXISTING, self.LOGICAL, "x\n")
        findings = prompt_composer.validate_authored_body(
            f"# Prompt\n\n## Existing deliverable input\n\n{block}\n"
        )
        codes = [item["code"] for item in findings]
        self.assertIn("unbound-input-payload", codes)

    def test_authored_body_cannot_carry_machine_owned_sections(self) -> None:
        findings = prompt_composer.validate_authored_body(
            "# Prompt\n\n## Upstream contract input\n\nplain pasted text\n"
        )
        codes = [item["code"] for item in findings]
        self.assertIn("unbound-input-payload", codes)

    def test_unmanifested_payload_fails_composed_validation(self) -> None:
        block = render_payload_block(CHANNEL_EXISTING, self.LOGICAL, "x\n")
        body = f"## Existing deliverable input\n\n{block}\n"
        findings = self._validate(body, input_payloads=[])
        codes = [item["code"] for item in findings]
        self.assertIn("unbound-input-payload", codes)

    def test_missing_manifested_payload_fails(self) -> None:
        manifest = [
            assignment_inputs.payload_binding(
                CHANNEL_EXISTING, self.LOGICAL, "x\n"
            )
        ]
        findings = self._validate("## Notes\n\nbody\n", input_payloads=manifest)
        codes = [item["code"] for item in findings]
        self.assertIn("input-payload-mismatch", codes)

    def test_payload_outside_its_section_fails(self) -> None:
        block = render_payload_block(CHANNEL_EXISTING, self.LOGICAL, "x\n")
        body = f"## Notes\n\n{block}\n"
        manifest = [
            assignment_inputs.payload_binding(
                CHANNEL_EXISTING, self.LOGICAL, "x\n"
            )
        ]
        findings = self._validate(body, input_payloads=manifest)
        codes = [item["code"] for item in findings]
        self.assertIn("unbound-input-payload", codes)

    def test_strip_payload_blocks_removes_only_payloads(self) -> None:
        block = render_payload_block(CHANNEL_EXISTING, self.LOGICAL, "data\n")
        body = (
            "## Verification\n\nkeep this\n\n"
            f"## Existing deliverable input\n\n{block}\n\n"
            "## Completion report\n\n```text\nskeleton\n```\n"
        )
        instruction = strip_payload_blocks(body)
        self.assertIn("keep this", instruction)
        self.assertIn("skeleton", instruction)
        self.assertNotIn("data", instruction)
        self.assertNotIn(assignment_inputs.MARKER, instruction)


if __name__ == "__main__":
    unittest.main()
