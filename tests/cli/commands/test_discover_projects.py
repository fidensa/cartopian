"""Tests for `cartopian discover-projects` (SPEC-01-001, FR-003, FR-014)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"


def _run(*, home):
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
    }
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), "discover-projects"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _registry_path(home: Path) -> Path:
    return home / ".cartopian" / "projects.json"


def _write_registry(home: Path, data) -> Path:
    p = _registry_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        p.write_text(data, encoding="utf-8")
    else:
        p.write_text(json.dumps(data), encoding="utf-8")
    return p


class TestDiscoverProjectsHelp(unittest.TestCase):
    def test_help_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, str(ENTRYPOINT), "discover-projects", "--help"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                env={"HOME": tmp, "PATH": os.environ.get("PATH", "")},
            )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)


class TestDiscoverProjectsHappyPath(unittest.TestCase):
    def test_multiple_entries_emitted_in_insertion_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            entries = [
                {"id": "alpha", "path": "/abs/alpha", "label": "Alpha"},
                {"id": "beta", "path": "/abs/beta", "label": "Beta"},
                {"id": "gamma", "path": "/abs/gamma", "label": None},
            ]
            _write_registry(home, entries)
            proc = _run(home=home)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(proc.stderr, "")
        lines = proc.stdout.splitlines()
        self.assertEqual(len(lines), 3)
        recs = [json.loads(line) for line in lines]
        self.assertEqual([r["id"] for r in recs], ["alpha", "beta", "gamma"])
        self.assertEqual(recs[0]["path"], "/abs/alpha")
        self.assertEqual(recs[0]["label"], "Alpha")
        self.assertIsNone(recs[2]["label"])
        # ensure trailing newline on last record
        self.assertTrue(proc.stdout.endswith("\n"))


class TestDiscoverProjectsEmptyAndMissing(unittest.TestCase):
    def test_missing_registry_empty_stdout_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertFalse(_registry_path(home).exists())
            proc = _run(home=home)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")

    def test_empty_registry_file_empty_stdout_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            _write_registry(home, "")
            proc = _run(home=home)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")

    def test_empty_array_registry_empty_stdout_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            _write_registry(home, [])
            proc = _run(home=home)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")


class TestDiscoverProjectsMalformed(unittest.TestCase):
    def test_malformed_json_exits_three_with_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            _write_registry(home, "{not json")
            proc = _run(home=home)
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.startswith("[error]"), msg=proc.stderr)
        self.assertIn("malformed", proc.stderr)

    def test_non_array_top_level_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            _write_registry(home, {"id": "x"})
            proc = _run(home=home)
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.startswith("[error]"), msg=proc.stderr)


class TestDiscoverProjectsPerEntrySchema(unittest.TestCase):
    """Per-entry registry-schema validation (SPEC-01-001 + FR-003)."""

    def _assert_corrupt(self, proc):
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.startswith("[error]"), msg=proc.stderr)

    def test_entry_missing_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            _write_registry(home, [{"path": "/abs/x", "label": "X"}])
            proc = _run(home=home)
        self._assert_corrupt(proc)

    def test_entry_missing_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            _write_registry(home, [{"id": "x"}])
            proc = _run(home=home)
        self._assert_corrupt(proc)

    def test_entry_non_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            _write_registry(home, [{"id": "x", "path": "relative/path"}])
            proc = _run(home=home)
        self._assert_corrupt(proc)

    def test_entry_non_kebab_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            _write_registry(home, [{"id": "Bad_ID", "path": "/abs/x"}])
            proc = _run(home=home)
        self._assert_corrupt(proc)

    def test_entry_wrong_type_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            _write_registry(home, [{"id": "x", "path": "/abs/x", "label": 42}])
            proc = _run(home=home)
        self._assert_corrupt(proc)

    def test_entry_empty_label_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            _write_registry(home, [{"id": "x", "path": "/abs/x", "label": ""}])
            proc = _run(home=home)
        self._assert_corrupt(proc)

    def test_entry_unknown_extra_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            _write_registry(
                home,
                [{"id": "x", "path": "/abs/x", "label": "X", "extra": "no"}],
            )
            proc = _run(home=home)
        self._assert_corrupt(proc)

    def test_entry_non_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            _write_registry(home, ["not an object"])
            proc = _run(home=home)
        self._assert_corrupt(proc)

    def test_entry_label_null_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            _write_registry(home, [{"id": "x", "path": "/abs/x", "label": None}])
            proc = _run(home=home)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        rec = json.loads(proc.stdout.strip())
        self.assertIsNone(rec["label"])

    def test_entry_label_absent_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            _write_registry(home, [{"id": "x", "path": "/abs/x"}])
            proc = _run(home=home)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        rec = json.loads(proc.stdout.strip())
        self.assertIsNone(rec["label"])


if __name__ == "__main__":
    unittest.main()
