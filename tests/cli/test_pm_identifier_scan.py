"""The PM-identifier scan over product-code files.

The always-on hygiene fail-safe — product code must not carry Cartopian planning
identifiers. Pure regex, no model round-trip; these tests pin both the positive
detections and the absence of false positives on ordinary code.
"""
import tempfile
import unittest
from pathlib import Path

from cli.provenance import scan_pm_identifiers


class TestScanPmIdentifiers(unittest.TestCase):
    def _write(self, dir_path: Path, name: str, text: str) -> Path:
        p = dir_path / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_flags_each_identifier_family(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            dirty = self._write(
                d,
                "leaky.py",
                "\n".join(
                    [
                        "def f():",
                        "    # FR-002 data-scoped guard",
                        "    x = 1  # see DEC-005 and TASK-01-002",
                        "    # BL-013 / OQ-009 / REVIEW-03-007",
                        "    # P01-05 and P04-BUILD-005",
                        "    # PROMPT-PLAN-004 and REVIEW-PLAN-001 word-segment ids",
                        "    return x",
                    ]
                ),
            )
            hits = scan_pm_identifiers([dirty])
            found = {h["match"] for h in hits}
            for expected in {
                "FR-002",
                "DEC-005",
                "TASK-01-002",
                "BL-013",
                "OQ-009",
                "REVIEW-03-007",
                "P01-05",
                "P04-BUILD-005",
                "PROMPT-PLAN-004",
                "REVIEW-PLAN-001",
            }:
                self.assertIn(expected, found, msg=f"missed {expected}: {found}")
            # Every hit carries a usable location.
            for h in hits:
                self.assertTrue(h["path"].endswith("leaky.py"))
                self.assertIsInstance(h["line"], int)

    def test_no_false_positive_on_ordinary_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            clean = self._write(
                d,
                "clean.py",
                "\n".join(
                    [
                        "TIMEOUT = 60  # seconds",
                        "result = total - 1",
                        "name = 'REPORT'  # a bare word, not an id",
                        "path = 'a-1/b-2'",
                        "version = 'v0.4.0'",
                    ]
                ),
            )
            self.assertEqual(scan_pm_identifiers([clean]), [])

    def test_skips_unreadable_and_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            missing = d / "nope.py"
            binary = self._write(d, "blob.bin", "")
            binary.write_bytes(b"\xff\xfe\x00\x01DEC-005")
            # Neither raises; the missing path is skipped and the binary decode
            # error is swallowed (a binary file is not product source to lint).
            self.assertEqual(scan_pm_identifiers([missing, binary]), [])
