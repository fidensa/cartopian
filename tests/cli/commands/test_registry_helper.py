"""Tests for `cli.commands._registry` helpers (kebab grammar + entry schema)."""
import json
import tempfile
import unittest
from pathlib import Path

from cli.commands._registry import (
    MalformedRegistry,
    is_kebab_case,
    read_registry,
)


class TestIsKebabCase(unittest.TestCase):
    def test_accept_cases(self):
        # F3 acceptance table.
        for v in ("bad", "bad-id", "bad-id-2", "b1-c2"):
            self.assertTrue(is_kebab_case(v), msg=f"should accept {v!r}")

    def test_reject_cases(self):
        # F3 rejection table.
        for v in (
            "Bad_ID",
            "bad_id",
            "BadID",
            "-bad",
            "bad-",
            "bad--id",
            "1bad",
            "",
            "bad id",
            "bad.id",
        ):
            self.assertFalse(is_kebab_case(v), msg=f"should reject {v!r}")

    def test_non_string_rejected(self):
        for v in (None, 42, True, [], {}):
            self.assertFalse(is_kebab_case(v), msg=f"should reject {v!r}")


def _write(tmp: Path, payload) -> Path:
    p = tmp / "projects.json"
    if isinstance(payload, str):
        p.write_text(payload, encoding="utf-8")
    else:
        p.write_text(json.dumps(payload), encoding="utf-8")
    return p


class TestReadRegistry(unittest.TestCase):
    def test_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "projects.json"
            self.assertEqual(read_registry(p), [])

    def test_empty_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(Path(tmp), "")
            self.assertEqual(read_registry(p), [])

    def test_corrupt_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(Path(tmp), "{not json")
            with self.assertRaises(MalformedRegistry):
                read_registry(p)

    def test_non_array_top_level_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(Path(tmp), {"id": "x"})
            with self.assertRaises(MalformedRegistry):
                read_registry(p)

    def test_entry_missing_id_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(Path(tmp), [{"path": "/abs/x"}])
            with self.assertRaises(MalformedRegistry):
                read_registry(p)

    def test_entry_missing_path_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(Path(tmp), [{"id": "x"}])
            with self.assertRaises(MalformedRegistry):
                read_registry(p)

    def test_entry_non_absolute_path_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(Path(tmp), [{"id": "x", "path": "rel/path"}])
            with self.assertRaises(MalformedRegistry):
                read_registry(p)

    def test_entry_non_kebab_id_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(Path(tmp), [{"id": "Bad_ID", "path": "/abs/x"}])
            with self.assertRaises(MalformedRegistry):
                read_registry(p)

    def test_entry_wrong_type_label_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(Path(tmp), [{"id": "x", "path": "/abs/x", "label": 7}])
            with self.assertRaises(MalformedRegistry):
                read_registry(p)

    def test_entry_extra_key_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(
                Path(tmp),
                [{"id": "x", "path": "/abs/x", "label": "X", "extra": 1}],
            )
            with self.assertRaises(MalformedRegistry):
                read_registry(p)

    def test_entry_label_null_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(Path(tmp), [{"id": "x", "path": "/abs/x", "label": None}])
            entries = read_registry(p)
            self.assertEqual(entries, [{"id": "x", "path": "/abs/x", "label": None}])

    def test_entry_label_absent_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(Path(tmp), [{"id": "x", "path": "/abs/x"}])
            entries = read_registry(p)
            self.assertEqual(entries, [{"id": "x", "path": "/abs/x"}])


if __name__ == "__main__":
    unittest.main()
