"""Tests for `cartopian register-project`."""
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
        [sys.executable, str(ENTRYPOINT), "register-project", *cli_args],
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


def _seed_project(root: Path, project_id: str = "demo", name: str = "Demo Project") -> Path:
    proj = root / project_id
    proj.mkdir(parents=True, exist_ok=True)
    toml = proj / "cartopian.toml"
    parts = ["[project]"]
    if project_id is not None:
        parts.append(f'id = "{project_id}"')
    if name is not None:
        parts.append(f'name = "{name}"')
    parts.append('protocol_version = "v0.2.0"')
    toml.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return proj


def _seed_project_raw(root: Path, dirname: str, toml_text: str) -> Path:
    proj = root / dirname
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "cartopian.toml").write_text(toml_text, encoding="utf-8")
    return proj


def _no_files_outside_home(home: Path, tmp_root: Path) -> bool:
    # All file writes must be inside `home` (which lives inside `tmp_root`).
    home_resolved = home.resolve()
    for entry in tmp_root.rglob("projects.json*"):
        if home_resolved not in entry.resolve().parents and entry.resolve() != home_resolved:
            # the registry file must reside under ~/.cartopian/
            if not str(entry.resolve()).startswith(str(home_resolved) + os.sep):
                return False
    return True


class TestRegisterProjectHelp(unittest.TestCase):
    def test_help_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, str(ENTRYPOINT), "register-project", "--help"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                env={"HOME": tmp, "PATH": os.environ.get("PATH", "")},
            )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("project_path", proc.stdout)
        self.assertIn("--label", proc.stdout)


class TestRegisterProjectHappyPath(unittest.TestCase):
    def test_explicit_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            proj = _seed_project(workspace, project_id="demo", name="Demo Project")
            proc = _run(str(proj), "--label", "My Demo", home=home)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(proc.stderr, "")
            line = proc.stdout.strip()
            self.assertEqual(line.count("\n"), 0)
            rec = json.loads(line)
            self.assertEqual(rec["action"], "register-project")
            self.assertEqual(rec["details"]["id"], "demo")
            self.assertEqual(rec["details"]["path"], str(proj.resolve()))
            self.assertEqual(rec["details"]["label"], "My Demo")
            persisted = _read_registry(home)
            self.assertEqual(len(persisted), 1)
            self.assertEqual(persisted[0]["id"], "demo")
            self.assertEqual(persisted[0]["path"], str(proj.resolve()))
            self.assertEqual(persisted[0]["label"], "My Demo")

    def test_label_defaults_to_project_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            proj = _seed_project(workspace, project_id="demo", name="Default Name")
            proc = _run(str(proj), home=home)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(proc.stderr, "")
            rec = json.loads(proc.stdout.strip())
            self.assertEqual(rec["details"]["label"], "Default Name")
            persisted = _read_registry(home)
            self.assertEqual(persisted[0]["label"], "Default Name")

    def test_multiple_registrations_preserve_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            a = _seed_project(workspace / "a", project_id="alpha", name="Alpha")
            b = _seed_project(workspace / "b", project_id="beta", name="Beta")
            r1 = _run(str(a), home=home)
            r2 = _run(str(b), home=home)
            self.assertEqual(r1.returncode, 0, msg=r1.stderr)
            self.assertEqual(r2.returncode, 0, msg=r2.stderr)
            persisted = _read_registry(home)
            self.assertEqual([e["id"] for e in persisted], ["alpha", "beta"])


class TestRegisterProjectUsage(unittest.TestCase):
    def test_relative_path_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            proc = _run("relative/path", home=home)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)
        self.assertIn("absolute path", proc.stderr)


class TestRegisterProjectGuards(unittest.TestCase):
    def test_missing_cartopian_toml(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            proj = tmp_path / "empty_proj"
            proj.mkdir()
            proc = _run(str(proj), home=home)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
        self.assertIn("not a Cartopian project", proc.stderr)

    def test_missing_project_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            proj = _seed_project_raw(
                tmp_path,
                "noid",
                '[project]\nname = "No Id"\nprotocol_version = "v0.2.0"\n',
            )
            proc = _run(str(proj), home=home)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
        self.assertIn("missing [project] id", proc.stderr)

    def test_malformed_project_id_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            proj = _seed_project_raw(
                tmp_path,
                "emptyid",
                '[project]\nid = ""\nname = "X"\nprotocol_version = "v0.2.0"\n',
            )
            proc = _run(str(proj), home=home)
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
        self.assertIn("malformed", proc.stderr)

    def test_malformed_project_id_whitespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            proj = _seed_project_raw(
                tmp_path,
                "wsid",
                '[project]\nid = "bad id"\nname = "X"\nprotocol_version = "v0.2.0"\n',
            )
            proc = _run(str(proj), home=home)
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
        self.assertIn("malformed", proc.stderr)

    def test_missing_project_name_with_no_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            proj = _seed_project_raw(
                tmp_path,
                "noname",
                '[project]\nid = "demo"\nprotocol_version = "v0.2.0"\n',
            )
            proc = _run(str(proj), home=home)
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
        self.assertIn("missing [project] name", proc.stderr)

    def test_missing_project_name_with_explicit_label_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            proj = _seed_project_raw(
                tmp_path,
                "noname",
                '[project]\nid = "demo"\nprotocol_version = "v0.2.0"\n',
            )
            proc = _run(str(proj), "--label", "Override", home=home)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        rec = json.loads(proc.stdout.strip())
        self.assertEqual(rec["details"]["label"], "Override")

    def test_non_kebab_project_id_rejected(self):
        # Kebab-case grammar must be enforced when reading
        # [project] id from cartopian.toml.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            proj = _seed_project_raw(
                tmp_path,
                "badid",
                '[project]\nid = "Bad_ID"\nname = "X"\nprotocol_version = "v0.2.0"\n',
            )
            proc = _run(str(proj), home=home)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
        self.assertIn("kebab-case", proc.stderr)

    def test_duplicate_id_with_different_paths(self):
        # Duplicate-id guard must still be reachable when paths differ.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            a = _seed_project(workspace / "a", project_id="dup", name="A")
            b = _seed_project(workspace / "b", project_id="dup", name="B")
            r1 = _run(str(a), home=home)
            self.assertEqual(r1.returncode, 0, msg=r1.stderr)
            r2 = _run(str(b), home=home)
            self.assertEqual(r2.returncode, 1)
            self.assertEqual(r2.stdout, "")
            self.assertTrue(r2.stderr.startswith("[guard]"), msg=r2.stderr)
            self.assertIn("duplicate registry id", r2.stderr)
            persisted = _read_registry(home)
            self.assertEqual(len(persisted), 1)

    def test_path_collision(self):
        # Re-registering the same path with a *different* id must report
        # path-collision diagnostic wins over the duplicate-id check.
        # Path-collision check runs first.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            proj = _seed_project(workspace, project_id="first", name="First")
            r1 = _run(str(proj), home=home)
            self.assertEqual(r1.returncode, 0, msg=r1.stderr)
            (proj / "cartopian.toml").write_text(
                '[project]\nid = "second"\nname = "Second"\nprotocol_version = "v0.2.0"\n',
                encoding="utf-8",
            )
            r2 = _run(str(proj), home=home)
            self.assertEqual(r2.returncode, 1)
            self.assertEqual(r2.stdout, "")
            self.assertTrue(r2.stderr.startswith("[guard]"), msg=r2.stderr)
            self.assertIn("path already registered", r2.stderr)
            persisted = _read_registry(home)
            self.assertEqual(len(persisted), 1)

    def test_same_path_retry_path_collision_wins(self):
        # F4: Same path AND same id — the path-collision guard fires before
        # the duplicate-id guard.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            proj = _seed_project(workspace, project_id="demo", name="Demo")
            r1 = _run(str(proj), home=home)
            self.assertEqual(r1.returncode, 0, msg=r1.stderr)
            r2 = _run(str(proj), home=home)
            self.assertEqual(r2.returncode, 1)
            self.assertEqual(r2.stdout, "")
            self.assertTrue(r2.stderr.startswith("[guard]"), msg=r2.stderr)
            self.assertIn("path already registered", r2.stderr)
            self.assertNotIn("duplicate registry id", r2.stderr)


class TestRegisterProjectMalformedRegistry(unittest.TestCase):
    def test_malformed_registry_exits_three(self):
        # Corrupt registry is an environment error (exit 3).
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            _write_registry(home, "{not json")
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            proj = _seed_project(workspace, project_id="demo", name="Demo")
            proc = _run(str(proj), home=home)
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.startswith("[error]"), msg=proc.stderr)
        self.assertIn("malformed", proc.stderr)

    def test_corrupt_entry_in_registry_exits_three(self):
        # A hand-edited entry missing `path` must be rejected as corrupt.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            _write_registry(home, [{"id": "x"}])
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            proj = _seed_project(workspace, project_id="demo", name="Demo")
            proc = _run(str(proj), home=home)
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.startswith("[error]"), msg=proc.stderr)


class TestRegisterProjectNoOutsideWrites(unittest.TestCase):
    def test_no_writes_outside_tmp(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            proj = _seed_project(workspace, project_id="demo", name="Demo")
            proc = _run(str(proj), home=home)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            # exactly one projects.json exists, under home
            self.assertTrue((home / ".cartopian" / "projects.json").is_file())
            self.assertTrue(_no_files_outside_home(home, tmp_path))


if __name__ == "__main__":
    unittest.main()
