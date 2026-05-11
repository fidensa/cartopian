"""Tests for `cartopian unregister-project` (SPEC-01-001, FR-003, FR-014)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"


def _run(*cli_args, home):
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
    }
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), "unregister-project", *cli_args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _registry_path(home: Path) -> Path:
    return home / ".cartopian" / "projects.json"


def _read_registry(home: Path):
    p = _registry_path(home)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _write_registry(home: Path, data) -> Path:
    p = _registry_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        p.write_text(data, encoding="utf-8")
    else:
        p.write_text(json.dumps(data), encoding="utf-8")
    return p


class TestUnregisterProjectHelp(unittest.TestCase):
    def test_help_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, str(ENTRYPOINT), "unregister-project", "--help"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                env={"HOME": tmp, "PATH": os.environ.get("PATH", "")},
            )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("id_or_path", proc.stdout)


class TestUnregisterProjectHappyPath(unittest.TestCase):
    def test_unregister_by_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            entries = [
                {"id": "alpha", "path": "/abs/alpha", "label": "Alpha"},
                {"id": "beta", "path": "/abs/beta", "label": "Beta"},
            ]
            _write_registry(home, entries)
            proc = _run("alpha", home=home)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(proc.stderr, "")
            rec = json.loads(proc.stdout.strip())
            self.assertEqual(rec["action"], "unregister-project")
            self.assertEqual(rec["details"]["id"], "alpha")
            self.assertEqual(rec["details"]["path"], "/abs/alpha")
            persisted = _read_registry(home)
            self.assertEqual([e["id"] for e in persisted], ["beta"])

    def test_unregister_by_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            proj_a = workspace / "a"
            proj_a.mkdir()
            proj_b = workspace / "b"
            proj_b.mkdir()
            entries = [
                {"id": "alpha", "path": str(proj_a.resolve()), "label": "Alpha"},
                {"id": "beta", "path": str(proj_b.resolve()), "label": "Beta"},
            ]
            _write_registry(home, entries)
            proc = _run(str(proj_b), home=home)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(proc.stderr, "")
            rec = json.loads(proc.stdout.strip())
            self.assertEqual(rec["details"]["id"], "beta")
            self.assertEqual(rec["details"]["path"], str(proj_b.resolve()))
            persisted = _read_registry(home)
            self.assertEqual([e["id"] for e in persisted], ["alpha"])


class TestUnregisterProjectMissing(unittest.TestCase):
    def test_missing_entry_by_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            _write_registry(home, [{"id": "alpha", "path": "/abs/alpha", "label": "A"}])
            proc = _run("nope", home=home)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
        self.assertIn("no registry entry matches", proc.stderr)

    def test_missing_entry_by_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            _write_registry(home, [{"id": "alpha", "path": "/abs/alpha", "label": "A"}])
            proc = _run("/nonexistent/path", home=home)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)

    def test_missing_registry_file_is_missing_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            proc = _run("alpha", home=home)
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)


class TestUnregisterProjectUsage(unittest.TestCase):
    def test_path_shaped_relative_input_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            _write_registry(home, [{"id": "alpha", "path": "/abs/alpha", "label": "A"}])
            proc = _run("rel/path", home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)
        self.assertIn("absolute path", proc.stderr)

    def test_bare_id_without_separators_not_treated_as_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            _write_registry(home, [{"id": "bare-id-1", "path": "/abs/x", "label": "X"}])
            proc = _run("bare-id-1", home=home)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)


class TestUnregisterProjectMalformedRegistry(unittest.TestCase):
    def test_malformed_registry_exits_three(self):
        # F1: corrupt registry is FR-014 environment error (exit 3).
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            _write_registry(home, "{not json")
            proc = _run("alpha", home=home)
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.startswith("[error]"), msg=proc.stderr)

    def test_corrupt_entry_exits_three(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            _write_registry(home, [{"id": "x"}])
            proc = _run("alpha", home=home)
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.startswith("[error]"), msg=proc.stderr)


class TestUnregisterProjectAmbiguous(unittest.TestCase):
    def test_ambiguous_id_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            # Hand-write a registry with duplicate ids (cannot occur via
            # register-project, but the guard exists for manually-edited files).
            _write_registry(
                home,
                [
                    {"id": "dup", "path": "/abs/a", "label": "A"},
                    {"id": "dup", "path": "/abs/b", "label": "B"},
                ],
            )
            proc = _run("dup", home=home)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
        self.assertIn("ambiguous", proc.stderr)


if __name__ == "__main__":
    unittest.main()
