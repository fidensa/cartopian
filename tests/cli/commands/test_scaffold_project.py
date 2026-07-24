"""Tests for `cartopian scaffold-project`."""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"
CHANGELOG = REPO_ROOT / "protocol" / "CHANGELOG.md"
REQUIREMENTS = (
    REPO_ROOT
    / "projects"
    / "cartopian-manager"
    / "REQUIREMENTS.md"
)

REQUIRED_DIRS = (
    "phases",
    "tasks/open",
    "tasks/in-progress",
    "tasks/in-review",
    "tasks/done",
    "prompts",
    "reports",
    "specs",
    "decisions",
    "reviews",
    "resources",
)
REQUIRED_FILES = (
    "STATE.md",
    "STANDARDS.md",
    "decisions/INDEX.md",
)
GITIGNORE_LINE = "cartopian.local.toml"


def _current_protocol_version() -> str:
    text = CHANGELOG.read_text(encoding="utf-8")
    _, _, body = text.partition("\n## Entries\n")
    m = re.search(r"^###\s+(v\d+\.\d+\.\d+)\b", body, flags=re.MULTILINE)
    assert m is not None, "no protocol version entry found"
    return m.group(1)


def _run(*cli_args, home=None):
    env = {
        "HOME": str(home) if home is not None else os.environ.get("HOME", ""),
        "PATH": os.environ.get("PATH", ""),
    }
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), "scaffold-project", *cli_args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _run_generate_config(*cli_args, home=None):
    env = {
        "HOME": str(home) if home is not None else os.environ.get("HOME", ""),
        "PATH": os.environ.get("PATH", ""),
    }
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), "generate-config", *cli_args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _hash_tree(root: Path) -> dict:
    """Map relative path → (mtime_ns, sha256) for every file under root."""
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(root))
            data = path.read_bytes()
            out[rel] = (path.stat().st_mtime_ns, hashlib.sha256(data).hexdigest())
    return out


def _make_well_formed(root: Path) -> None:
    """Create a complete, well-formed scaffold at root for no-op tests."""
    for rel in REQUIRED_DIRS:
        (root / rel).mkdir(parents=True, exist_ok=True)
    for rel in REQUIRED_FILES:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {rel}\n", encoding="utf-8")
    (root / ".gitignore").write_text(GITIGNORE_LINE + "\n", encoding="utf-8")


class TestScaffoldProjectHelp(unittest.TestCase):
    def test_help_lists_subcommand(self):
        proc = subprocess.run(
            [sys.executable, str(ENTRYPOINT), "scaffold-project", "--help"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            env={"PATH": os.environ.get("PATH", "")},
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("project_path", proc.stdout)


class TestScaffoldProjectHappyPath(unittest.TestCase):
    def test_missing_target_creates_full_scaffold(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            self.assertFalse(proj.exists())
            proc = _run(str(proj), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout!r} stderr={proc.stderr!r}")
            self.assertEqual(proc.stderr, "")
            self.assertTrue(proj.is_dir())
            for rel in REQUIRED_DIRS:
                self.assertTrue((proj / rel).is_dir(), msg=f"missing dir: {rel}")
            for rel in REQUIRED_FILES:
                self.assertTrue((proj / rel).is_file(), msg=f"missing file: {rel}")
            self.assertFalse((proj / "CONVENTIONS.md").exists())
            gi = proj / ".gitignore"
            self.assertTrue(gi.is_file())
            self.assertIn(GITIGNORE_LINE, gi.read_text(encoding="utf-8").splitlines())
            line = proc.stdout.strip()
            self.assertEqual(line.count("\n"), 0)
            record = json.loads(line)
            self.assertEqual(record["action"], "scaffold-project")
            self.assertEqual(record["details"]["project_path"], str(proj))
            self.assertEqual(record["details"]["outcome"], "scaffolded")

    def test_empty_target_creates_full_scaffold(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(str(proj), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            for rel in REQUIRED_DIRS:
                self.assertTrue((proj / rel).is_dir())
            for rel in REQUIRED_FILES:
                self.assertTrue((proj / rel).is_file())
            self.assertTrue((proj / ".gitignore").is_file())
            record = json.loads(proc.stdout.strip())
            self.assertEqual(record["details"]["outcome"], "scaffolded")

    def test_confirmation_record_key_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proc = _run(str(proj), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            line = proc.stdout.strip()
            top_keys = [m for m in re.findall(r'"([^"]+)":', line) if m in ("action", "details")]
            self.assertEqual(top_keys, ["action", "details"])
            details_segment = line.split('"details":', 1)[1]
            detail_keys = [
                m for m in re.findall(r'"([^"]+)":', details_segment)
                if m in ("project_path", "outcome")
            ]
            self.assertEqual(detail_keys, ["project_path", "outcome"])


class TestScaffoldProjectNoop(unittest.TestCase):
    def test_well_formed_target_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            _make_well_formed(proj)
            before = _hash_tree(proj)
            # Force a measurable mtime gap so an unintended write would be visible.
            time.sleep(0.05)
            proc = _run(str(proj), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout!r} stderr={proc.stderr!r}")
            self.assertEqual(proc.stderr, "")
            after = _hash_tree(proj)
            self.assertEqual(before, after, msg="files were touched during noop path")
            record = json.loads(proc.stdout.strip())
            self.assertEqual(record["action"], "scaffold-project")
            self.assertEqual(record["details"]["project_path"], str(proj))
            self.assertEqual(record["details"]["outcome"], "noop")

    def test_rerun_after_scaffold_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            first = _run(str(proj), home=tmp_path)
            self.assertEqual(first.returncode, 0, msg=first.stderr)
            self.assertEqual(json.loads(first.stdout.strip())["details"]["outcome"], "scaffolded")
            before = _hash_tree(proj)
            time.sleep(0.05)
            second = _run(str(proj), home=tmp_path)
            self.assertEqual(second.returncode, 0, msg=second.stderr)
            self.assertEqual(_hash_tree(proj), before)
            self.assertEqual(json.loads(second.stdout.strip())["details"]["outcome"], "noop")


class TestScaffoldProjectGuardedRefusal(unittest.TestCase):
    def test_foreign_file_at_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            foreign = proj / "README.md"
            foreign.write_text("hi", encoding="utf-8")
            proc = _run(str(proj), home=tmp_path)
            self.assertEqual(proc.returncode, 1, msg=proc.stderr)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
            self.assertIn(str(foreign), proc.stderr)
            self.assertIn("foreign file", proc.stderr)
            # Scaffold not created
            self.assertFalse((proj / "phases").exists())

    def test_foreign_directory_at_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            foreign = proj / "src"
            foreign.mkdir()
            proc = _run(str(proj), home=tmp_path)
            self.assertEqual(proc.returncode, 1, msg=proc.stderr)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
            self.assertIn("foreign directory", proc.stderr)
            self.assertIn(str(foreign), proc.stderr)
            self.assertFalse((proj / "phases").exists())

    def test_partial_scaffold_missing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            _make_well_formed(proj)
            # Remove one required directory
            removed = proj / "tasks" / "in-review"
            removed.rmdir()
            proc = _run(str(proj), home=tmp_path)
            self.assertEqual(proc.returncode, 1, msg=proc.stderr)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
            self.assertIn("partial scaffold", proc.stderr)
            self.assertIn("tasks/in-review", proc.stderr)
            # No directories created or modified
            self.assertFalse(removed.exists())

    def test_partial_scaffold_missing_seed_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            _make_well_formed(proj)
            (proj / "STANDARDS.md").unlink()
            proc = _run(str(proj), home=tmp_path)
            self.assertEqual(proc.returncode, 1, msg=proc.stderr)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
            self.assertIn("partial scaffold", proc.stderr)
            self.assertIn("STANDARDS.md", proc.stderr)
            self.assertFalse((proj / "STANDARDS.md").exists())

    def test_well_formed_with_drifted_gitignore_is_guarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            _make_well_formed(proj)
            # Replace .gitignore with one that lacks the load-bearing line.
            (proj / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
            before = _hash_tree(proj)
            proc = _run(str(proj), home=tmp_path)
            self.assertEqual(proc.returncode, 1, msg=proc.stderr)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
            self.assertIn(".gitignore", proc.stderr)
            self.assertEqual(_hash_tree(proj), before)


class TestScaffoldProjectGitignoreIdempotency(unittest.TestCase):
    def test_missing_gitignore_created_on_scaffolded(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            self.assertFalse((proj / ".gitignore").exists())
            proc = _run(str(proj), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            gi = proj / ".gitignore"
            self.assertTrue(gi.is_file())
            self.assertEqual(gi.read_text(encoding="utf-8"), GITIGNORE_LINE + "\n")

    def test_gitignore_with_line_left_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            _make_well_formed(proj)
            gi = proj / ".gitignore"
            # Use a richer existing .gitignore that already includes the line
            content = "# editor\n.DS_Store\n" + GITIGNORE_LINE + "\nnode_modules/\n"
            gi.write_text(content, encoding="utf-8")
            original_bytes = gi.read_bytes()
            proc = _run(str(proj), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(json.loads(proc.stdout.strip())["details"]["outcome"], "noop")
            self.assertEqual(gi.read_bytes(), original_bytes)

    def test_gitignore_without_line_is_appended_when_otherwise_only_gitignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            gi = proj / ".gitignore"
            existing = "# editor\n.DS_Store\nnode_modules/\n"
            gi.write_text(existing, encoding="utf-8")
            existing_bytes = gi.read_bytes()
            proc = _run(str(proj), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(json.loads(proc.stdout.strip())["details"]["outcome"], "scaffolded")
            after = gi.read_bytes()
            # Existing content preserved byte-for-byte at the head of the file.
            self.assertTrue(after.startswith(existing_bytes), msg=after)
            # Appended line present.
            tail = after[len(existing_bytes):]
            self.assertEqual(tail, (GITIGNORE_LINE + "\n").encode("utf-8"))

    def test_gitignore_without_trailing_newline_is_appended_correctly(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            gi = proj / ".gitignore"
            existing = "node_modules/"  # no trailing newline
            gi.write_text(existing, encoding="utf-8")
            proc = _run(str(proj), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            text = gi.read_text(encoding="utf-8")
            lines = text.splitlines()
            self.assertIn("node_modules/", lines)
            self.assertIn(GITIGNORE_LINE, lines)


class TestScaffoldProjectUsage(unittest.TestCase):
    def test_relative_path_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proc = _run("relative/proj", home=tmp_path)
            self.assertEqual(proc.returncode, 2, msg=proc.stderr)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)
            self.assertIn("absolute path", proc.stderr)

    def test_missing_parent_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target = tmp_path / "no-such-parent" / "proj"
            proc = _run(str(target), home=tmp_path)
            self.assertEqual(proc.returncode, 2, msg=proc.stderr)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)
            self.assertIn("parent does not exist", proc.stderr)
            self.assertFalse(target.exists())

    def test_target_is_existing_file_is_guarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target = tmp_path / "iam-a-file"
            target.write_text("hi", encoding="utf-8")
            proc = _run(str(target), home=tmp_path)
            self.assertEqual(proc.returncode, 1, msg=proc.stderr)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
            self.assertIn(str(target), proc.stderr)
            # File untouched
            self.assertEqual(target.read_text(encoding="utf-8"), "hi")

    def test_missing_positional_rejected(self):
        proc = subprocess.run(
            [sys.executable, str(ENTRYPOINT), "scaffold-project"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            env={"PATH": os.environ.get("PATH", "")},
        )
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)


class TestScaffoldProjectDec013WellFormedTomlVariants(unittest.TestCase):
    """cartopian.toml and cartopian.local.toml are tolerated at the
    project root by the no-op check (present optional; absence is not a
    partial-scaffold guard). Unknown top-level files remain drift."""

    def test_well_formed_with_cartopian_toml_present_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            _make_well_formed(proj)
            (proj / "cartopian.toml").write_text(
                "[project]\nname = \"demo\"\n", encoding="utf-8"
            )
            before = _hash_tree(proj)
            time.sleep(0.05)
            proc = _run(str(proj), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(proc.stderr, "")
            self.assertEqual(_hash_tree(proj), before)
            record = json.loads(proc.stdout.strip())
            self.assertEqual(record["details"]["outcome"], "noop")

    def test_well_formed_with_both_toml_files_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            _make_well_formed(proj)
            (proj / "cartopian.toml").write_text(
                "[project]\nname = \"demo\"\n", encoding="utf-8"
            )
            (proj / "cartopian.local.toml").write_text(
                "[machine]\nwork_root = \"/tmp/wr\"\n", encoding="utf-8"
            )
            before = _hash_tree(proj)
            time.sleep(0.05)
            proc = _run(str(proj), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(proc.stderr, "")
            self.assertEqual(_hash_tree(proj), before)
            record = json.loads(proc.stdout.strip())
            self.assertEqual(record["details"]["outcome"], "noop")

    def test_well_formed_with_only_cartopian_local_toml_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            _make_well_formed(proj)
            (proj / "cartopian.local.toml").write_text(
                "[machine]\nwork_root = \"/tmp/wr\"\n", encoding="utf-8"
            )
            before = _hash_tree(proj)
            time.sleep(0.05)
            proc = _run(str(proj), home=tmp_path)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(proc.stderr, "")
            self.assertEqual(_hash_tree(proj), before)
            record = json.loads(proc.stdout.strip())
            self.assertEqual(record["details"]["outcome"], "noop")

    def test_well_formed_with_unknown_top_level_file_is_guarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            _make_well_formed(proj)
            (proj / "cartopian.toml").write_text(
                "[project]\nname = \"demo\"\n", encoding="utf-8"
            )
            unknown = proj / "README.md"
            unknown.write_text("hi", encoding="utf-8")
            before = _hash_tree(proj)
            proc = _run(str(proj), home=tmp_path)
            self.assertEqual(proc.returncode, 1, msg=proc.stderr)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
            self.assertIn("foreign file", proc.stderr)
            self.assertIn(str(unknown), proc.stderr)
            self.assertEqual(_hash_tree(proj), before)

    def test_drifted_gitignore_with_cartopian_toml_present_is_guarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            _make_well_formed(proj)
            (proj / "cartopian.toml").write_text(
                "[project]\nname = \"demo\"\n", encoding="utf-8"
            )
            (proj / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
            before = _hash_tree(proj)
            proc = _run(str(proj), home=tmp_path)
            self.assertEqual(proc.returncode, 1, msg=proc.stderr)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
            self.assertIn(".gitignore", proc.stderr)
            self.assertEqual(_hash_tree(proj), before)


class TestScaffoldGenerateConfigIntegration(unittest.TestCase):
    """scaffold + generate-config in sequence yields a well-formed
    cartopian.toml whose [project] protocol_version matches the current
    protocol version read from protocol/CHANGELOG.md."""

    def test_scaffold_then_generate_config_round_trip(self):
        version = _current_protocol_version()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            scaffold = _run(str(proj), home=tmp_path)
            self.assertEqual(scaffold.returncode, 0, msg=scaffold.stderr)
            gen = _run_generate_config(
                str(proj), "--name", "Demo", "--id", "demo", home=tmp_path,
            )
            self.assertEqual(gen.returncode, 0, msg=gen.stderr)
            cfg_path = proj / "cartopian.toml"
            self.assertTrue(cfg_path.is_file())
            with cfg_path.open("rb") as fh:
                data = tomllib.load(fh)
            self.assertEqual(data["project"]["protocol_version"], version)
            self.assertEqual(data["project"]["name"], "Demo")
            self.assertEqual(data["project"]["id"], "demo")
            # cartopian.toml is tolerated at the project root by the no-op
            # check; re-running scaffold-project after generate-config therefore
            # yields a no-op success (exit 0, one NDJSON record, no files
            # touched).
            before = _hash_tree(proj)
            time.sleep(0.05)
            rerun = _run(str(proj), home=tmp_path)
            self.assertEqual(rerun.returncode, 0, msg=rerun.stderr)
            self.assertEqual(rerun.stderr, "")
            line = rerun.stdout.strip()
            self.assertEqual(line.count("\n"), 0)
            record = json.loads(line)
            self.assertEqual(record["action"], "scaffold-project")
            self.assertEqual(record["details"]["project_path"], str(proj))
            self.assertEqual(record["details"]["outcome"], "noop")
            self.assertEqual(_hash_tree(proj), before)



if __name__ == "__main__":
    unittest.main()
