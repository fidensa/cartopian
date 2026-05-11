"""Tests for cli/emit.py NDJSON helper (DEC-008)."""
import io
import json
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from cli.emit import emit_record


class TestEmit(unittest.TestCase):
    def test_emit_single_record(self):
        buf = io.StringIO()
        emit_record({"a": 1, "b": "two"}, out=buf)
        self.assertEqual(buf.getvalue(), '{"a":1,"b":"two"}\n')

    def test_emit_multiple_records(self):
        buf = io.StringIO()
        emit_record({"a": 1}, out=buf)
        emit_record({"b": 2}, out=buf)
        emit_record({"c": 3}, out=buf)
        lines = buf.getvalue().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual([json.loads(line) for line in lines], [{"a": 1}, {"b": 2}, {"c": 3}])
        self.assertTrue(buf.getvalue().endswith("\n"))

    def test_emit_rejects_scalar(self):
        with self.assertRaises(TypeError):
            emit_record("hello", out=io.StringIO())

    def test_emit_rejects_top_level_array(self):
        with self.assertRaises(TypeError):
            emit_record([1, 2, 3], out=io.StringIO())

    def test_emit_unicode_safe(self):
        buf = io.StringIO()
        emit_record({"name": "café"}, out=buf)
        self.assertIn("café", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
