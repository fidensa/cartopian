"""Tests for `cartopian generate-config`."""
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"
CHANGELOG = REPO_ROOT / "protocol" / "CHANGELOG.md"


def _current_protocol_version() -> str:
    """Read the topmost `### vX.Y.Z` entry header beneath `## Entries`."""
    text = CHANGELOG.read_text(encoding="utf-8")
    head, _, body = text.partition("\n## Entries\n")
    m = re.search(r"^###\s+(v\d+\.\d+\.\d+)\b", body, flags=re.MULTILINE)
    assert m is not None, f"could not find a version entry under ## Entries in {CHANGELOG}"
    return m.group(1)


def _run(*cli_args, home=None):
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


class TestGenerateConfigHelp(unittest.TestCase):
    def test_help_lists_subcommand(self):
        proc = subprocess.run(
            [sys.executable, str(ENTRYPOINT), "generate-config", "--help"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            env={"PATH": os.environ.get("PATH", "")},
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("project_path", proc.stdout)
        self.assertIn("--name", proc.stdout)
        self.assertIn("--id", proc.stdout)
        self.assertIn("--role", proc.stdout)
        self.assertIn("--handoff", proc.stdout)
        self.assertIn("--work-root", proc.stdout)
        self.assertIn("--git-versioning", proc.stdout)
        self.assertIn("--git-key", proc.stdout)
        # No `--kind` flag
        self.assertNotIn("--kind", proc.stdout)


class TestGenerateConfigHappyPath(unittest.TestCase):
    def test_minimal_round_trip_to_toml(self):
        version = _current_protocol_version()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(
                str(proj),
                "--name", "Cartopian Manager",
                "--id", "cartopian-manager",
                "--role", "pm=Plans...",
                "--role", "coder=Writes",
                "--handoff", "coder=claude-vscode",
                home=tmp_path,
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout!r} stderr={proc.stderr!r}")
            self.assertEqual(proc.stderr, "")
            line = proc.stdout.strip()
            record = json.loads(line)
            self.assertEqual(record["action"], "generate-config")
            details = record["details"]
            self.assertEqual(details["project_path"], str(proj))
            self.assertEqual(details["config_path"], str(proj / "cartopian.toml"))
            self.assertEqual(details["protocol_version"], version)
            # File round-trips through tomllib
            with (proj / "cartopian.toml").open("rb") as fh:
                data = tomllib.load(fh)
            self.assertEqual(data["project"]["name"], "Cartopian Manager")
            self.assertEqual(data["project"]["id"], "cartopian-manager")
            self.assertEqual(data["project"]["protocol_version"], version)
            self.assertEqual(data["roles"], {"pm": "Plans...", "coder": "Writes"})
            self.assertEqual(data["handoffs"], {"coder": {"agent": "claude-vscode"}})
            # No protocol defaults written
            self.assertNotIn("automation", data)
            self.assertNotIn("defaults", data)
            self.assertNotIn("git", data)
            self.assertNotIn("work_roots", data.get("project", {}))

    def test_full_flag_set_round_trip(self):
        version = _current_protocol_version()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(
                str(proj),
                "--name", "Demo",
                "--id", "demo",
                "--role", "pm=Plans things",
                "--role", "coder=Writes code",
                "--handoff", "coder=cartopian-claude",
                "--handoff-model", "coder=claude-opus-4-8",
                "--handoff-auto-start", "coder=false",
                "--handoff-timeout", "coder=30m",
                "--automation-confirmation", "until-blocked",
                "--automation-max-handoffs", "5",
                "--work-root", "build",
                "--work-root", "docs",
                "--git-versioning", "true",
                "--git-key", "pm_owns_product_branches=true",
                "--git-key", "default_branch_pattern=task/{task_id}-{slug}",
                "--git-key", "max_retries=3",
                home=tmp_path,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            with (proj / "cartopian.toml").open("rb") as fh:
                data = tomllib.load(fh)
            self.assertEqual(data["project"]["name"], "Demo")
            self.assertEqual(data["project"]["id"], "demo")
            self.assertEqual(data["project"]["protocol_version"], version)
            self.assertEqual(data["project"]["work_roots"], ["build", "docs"])
            self.assertEqual(data["roles"], {"pm": "Plans things", "coder": "Writes code"})
            self.assertNotIn("pm", data["handoffs"])
            self.assertEqual(
                data["handoffs"]["coder"],
                {
                    "agent": "cartopian-claude",
                    "model": "claude-opus-4-8",
                    "auto_start": False,
                    "timeout": "30m",
                },
            )
            self.assertEqual(
                data["automation"],
                {"confirmation": "until-blocked", "max_handoffs_per_run": 5},
            )
            self.assertEqual(data["defaults"], {"git_versioning": True})
            # primitive type fidelity in [git]
            self.assertIs(data["git"]["pm_owns_product_branches"], True)
            self.assertEqual(data["git"]["default_branch_pattern"], "task/{task_id}-{slug}")
            self.assertEqual(data["git"]["max_retries"], 3)
            self.assertIsInstance(data["git"]["max_retries"], int)

    def test_omitted_optional_flags_emit_no_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(
                str(proj),
                "--name", "X",
                "--id", "x",
                home=tmp_path,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            with (proj / "cartopian.toml").open("rb") as fh:
                data = tomllib.load(fh)
            self.assertEqual(set(data.keys()), {"project"})
            self.assertEqual(set(data["project"].keys()), {"name", "id", "protocol_version"})

    def test_confirmation_record_key_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(
                str(proj), "--name", "X", "--id", "x", home=tmp_path,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            line = proc.stdout.strip()
            self.assertEqual(line.count("\n"), 0)
            # Top-level keys in locked order: action, details
            top_keys = [m for m in re.findall(r'"([^"]+)":', line) if m in ("action", "details")]
            self.assertEqual(top_keys, ["action", "details"])
            # Details keys in locked order: project_path, config_path, protocol_version
            details_segment = line.split('"details":', 1)[1]
            detail_keys = [
                m for m in re.findall(r'"([^"]+)":', details_segment)
                if m in ("project_path", "config_path", "protocol_version")
            ]
            self.assertEqual(detail_keys, ["project_path", "config_path", "protocol_version"])


class TestGenerateConfigGuards(unittest.TestCase):
    def test_existing_target_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            existing = proj / "cartopian.toml"
            existing.write_text("preexisting = true\n", encoding="utf-8")
            original_bytes = existing.read_bytes()
            proc = _run(
                str(proj), "--name", "X", "--id", "x", home=tmp_path,
            )
            self.assertEqual(proc.returncode, 1, msg=proc.stderr)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[guard]"), msg=proc.stderr)
            self.assertEqual(proc.stderr.count("\n"), 1)
            self.assertEqual(existing.read_bytes(), original_bytes)

    def test_orphan_handoff_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(
                str(proj),
                "--name", "X", "--id", "x",
                "--handoff", "foo=bar",
                home=tmp_path,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stdout, "")
            self.assertIn("[usage] orphan-handoff: foo", proc.stderr)
            self.assertIn("--role first", proc.stderr)
            self.assertFalse((proj / "cartopian.toml").exists())

    def test_pm_handoff_rejected(self):
        # The PM is the interactive session orchestrator, never launched as a
        # handoff; a [handoffs.pm] block is forbidden and must fail closed so it
        # can never be written. Regression for the "PM dispatch is manual"
        # confusion caused by a stray [handoffs.pm] block.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(
                str(proj),
                "--name", "X", "--id", "x",
                "--role", "pm=Plans",
                "--handoff", "pm=cartopian-claude",
                home=tmp_path,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stdout, "")
            self.assertIn("handoffs-pm-forbidden", proc.stderr)
            self.assertFalse((proj / "cartopian.toml").exists())

    def test_pm_handoff_timeout_rejected(self):
        # The guard must fire on every --handoff* flavour, not just --handoff.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(
                str(proj),
                "--name", "X", "--id", "x",
                "--role", "pm=Plans",
                "--handoff-timeout", "pm=60m",
                home=tmp_path,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertIn("handoffs-pm-forbidden", proc.stderr)
            self.assertFalse((proj / "cartopian.toml").exists())

    def test_orphan_handoff_auto_start_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(
                str(proj),
                "--name", "X", "--id", "x",
                "--handoff-auto-start", "foo=true",
                home=tmp_path,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertIn("orphan-handoff: foo", proc.stderr)
            self.assertFalse((proj / "cartopian.toml").exists())

    def test_git_key_without_git_versioning_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(
                str(proj),
                "--name", "X", "--id", "x",
                "--git-key", "pm_owns_product_branches=true",
                home=tmp_path,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)
            self.assertFalse((proj / "cartopian.toml").exists())

    def test_git_key_with_git_versioning_false_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(
                str(proj),
                "--name", "X", "--id", "x",
                "--git-versioning", "false",
                "--git-key", "pm_owns_product_branches=true",
                home=tmp_path,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)
            self.assertFalse((proj / "cartopian.toml").exists())


class TestGenerateConfigUsage(unittest.TestCase):
    def test_relative_project_path_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proc = _run("relative/proj", "--name", "X", "--id", "x", home=tmp_path)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)
            self.assertIn("absolute path", proc.stderr)

    def test_missing_required_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(str(proj), "--id", "x", home=tmp_path)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)
            self.assertFalse((proj / "cartopian.toml").exists())

    def test_missing_required_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(str(proj), "--name", "X", home=tmp_path)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)
            self.assertFalse((proj / "cartopian.toml").exists())

    def test_bad_id_uppercase(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(str(proj), "--name", "X", "--id", "BadID", home=tmp_path)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stdout, "")
            self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)
            self.assertFalse((proj / "cartopian.toml").exists())

    def test_bad_id_leading_hyphen(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(str(proj), "--name", "X", "--id", "-bad", home=tmp_path)
            self.assertEqual(proc.returncode, 2)
            self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)
            self.assertFalse((proj / "cartopian.toml").exists())

    def test_bad_role_grammar(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(
                str(proj), "--name", "X", "--id", "x",
                "--role", "no-equals-sign",
                home=tmp_path,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)

    def test_bad_automation_confirmation_enum(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(
                str(proj), "--name", "X", "--id", "x",
                "--automation-confirmation", "sometimes",
                home=tmp_path,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)

    def test_bad_max_handoffs_not_positive(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(
                str(proj), "--name", "X", "--id", "x",
                "--automation-max-handoffs", "0",
                home=tmp_path,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)

    def test_bad_work_root_grammar(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(
                str(proj), "--name", "X", "--id", "x",
                "--work-root", "has/slash",
                home=tmp_path,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertTrue(proc.stderr.startswith("[usage]"), msg=proc.stderr)


class TestGenerateConfigRepeatedSingleValuedFlags(unittest.TestCase):
    """Repeated non-repeatable flags must exit 2 with `[usage]`."""

    def _assert_rejected(self, proc, proj, flag):
        self.assertEqual(proc.returncode, 2, msg=f"stdout={proc.stdout!r} stderr={proc.stderr!r}")
        self.assertEqual(proc.stdout, "")
        self.assertTrue(
            proc.stderr.startswith(f"[usage] {flag}:"),
            msg=f"expected '[usage] {flag}:' prefix, got: {proc.stderr!r}",
        )
        self.assertFalse((proj / "cartopian.toml").exists())

    def test_repeated_name_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(
                str(proj),
                "--name", "X", "--name", "Y",
                "--id", "x",
                home=tmp_path,
            )
            self._assert_rejected(proc, proj, "--name")

    def test_repeated_id_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(
                str(proj),
                "--name", "X",
                "--id", "a", "--id", "b",
                home=tmp_path,
            )
            self._assert_rejected(proc, proj, "--id")

    def test_repeated_automation_confirmation_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(
                str(proj),
                "--name", "X", "--id", "x",
                "--automation-confirmation", "each-handoff",
                "--automation-confirmation", "until-blocked",
                home=tmp_path,
            )
            self._assert_rejected(proc, proj, "--automation-confirmation")

    def test_repeated_automation_max_handoffs_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(
                str(proj),
                "--name", "X", "--id", "x",
                "--automation-max-handoffs", "1",
                "--automation-max-handoffs", "2",
                home=tmp_path,
            )
            self._assert_rejected(proc, proj, "--automation-max-handoffs")

    def test_repeated_git_versioning_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(
                str(proj),
                "--name", "X", "--id", "x",
                "--git-versioning", "true",
                "--git-versioning", "false",
                home=tmp_path,
            )
            self._assert_rejected(proc, proj, "--git-versioning")

    def test_repeatable_role_flag_still_accepts_repeats(self):
        """Negative control: --role is repeatable and must continue to accept repeats."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(
                str(proj),
                "--name", "X", "--id", "x",
                "--role", "pm=A",
                "--role", "coder=B",
                home=tmp_path,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            with (proj / "cartopian.toml").open("rb") as fh:
                data = tomllib.load(fh)
            self.assertEqual(data["roles"], {"pm": "A", "coder": "B"})


class TestGenerateConfigGitKeyPrimitives(unittest.TestCase):
    def test_bool_int_string_fidelity(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            proj.mkdir()
            proc = _run(
                str(proj),
                "--name", "X", "--id", "x",
                "--git-versioning", "true",
                "--git-key", "flag=true",
                "--git-key", "n=42",
                "--git-key", 'quoted="hello world"',
                "--git-key", "bare=plainstring",
                home=tmp_path,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            with (proj / "cartopian.toml").open("rb") as fh:
                data = tomllib.load(fh)
            self.assertIs(data["git"]["flag"], True)
            self.assertEqual(data["git"]["n"], 42)
            self.assertIsInstance(data["git"]["n"], int)
            self.assertEqual(data["git"]["quoted"], "hello world")
            self.assertEqual(data["git"]["bare"], "plainstring")


if __name__ == "__main__":
    unittest.main()
