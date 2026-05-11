"""Tests for `cartopian resolve-config` (SPEC-01-001, FR-011)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"


def _run(project_path, *, home):
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
    }
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), "resolve-config", str(project_path)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class _Sandbox:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.project = self.root / "proj"
        self.home.mkdir()
        self.project.mkdir()

    def cleanup(self):
        self._tmp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.cleanup()


class TestHappyPath(unittest.TestCase):
    def test_emits_full_record(self):
        with _Sandbox() as sb:
            _write(
                sb.home / ".cartopian" / "cartopian.toml",
                '[automation]\nconfirmation = "until-blocked"\nmax_handoffs_per_run = 3\n',
            )
            _write(
                sb.project / "cartopian.toml",
                '[project]\n'
                'id = "demo"\n'
                'name = "Demo Project"\n'
                'protocol_version = "v0.2.0"\n'
                '\n'
                '[roles]\n'
                'pm = "Plans the work."\n'
                'operator = "Approves things."\n'
                'coder = "Writes code."\n'
                '\n'
                '[handoffs.coder]\n'
                'agent = "claude"\n'
                'auto_start = true\n'
                'timeout = "60m"\n',
            )
            result = _run(sb.project, home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stderr, "")
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])

        expected_keys = [
            "project_id",
            "project_name",
            "project_path",
            "protocol_version",
            "roles",
            "handoffs",
            "automation",
            "work_roots",
            "git_versioning",
            "git",
            "defaults_attribution",
        ]
        self.assertEqual(list(record.keys()), expected_keys)
        self.assertEqual(record["project_id"], "demo")
        self.assertEqual(record["project_name"], "Demo Project")
        self.assertEqual(record["project_path"], str(sb.project.resolve()))
        self.assertEqual(record["protocol_version"], "v0.2.0")
        self.assertEqual(record["roles"]["pm"], "Plans the work.")
        self.assertEqual(record["roles"]["coder"], "Writes code.")
        self.assertEqual(
            record["handoffs"]["coder"],
            {"agent": "claude", "auto_start": True, "timeout": "60m"},
        )
        self.assertEqual(
            record["automation"],
            {"confirmation": "until-blocked", "max_handoffs_per_run": 3},
        )
        self.assertEqual(record["work_roots"], {})
        self.assertFalse(record["git_versioning"])
        self.assertIsNone(record["git"])
        self.assertEqual(
            record["defaults_attribution"],
            {"git_versioning": "protocol-default: false"},
        )


class TestOrphanHandoffGuard(unittest.TestCase):
    def test_handoff_without_role_fails(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                '[project]\n'
                'id = "demo"\n'
                'name = "Demo"\n'
                'protocol_version = "v0.2.0"\n'
                '\n'
                '[handoffs.coder]\n'
                'agent = "claude"\n',
            )
            result = _run(sb.project, home=sb.home)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn(
            "[config] orphan-handoff: coder — declare in [roles] or remove the [handoffs.coder] block",
            result.stderr,
        )


class TestUnmappedWorkRootGuard(unittest.TestCase):
    def test_declared_work_root_without_local_mapping_fails(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                '[project]\n'
                'id = "demo"\n'
                'name = "Demo"\n'
                'protocol_version = "v0.2.0"\n'
                'work_roots = ["site"]\n'
                '\n'
                '[roles]\n'
                'pm = "."\n'
                'operator = "."\n',
            )
            result = _run(sb.project, home=sb.home)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("[work-root] unmapped: site", result.stderr)


class TestMissingProjectConfig(unittest.TestCase):
    def test_missing_cartopian_toml_fails(self):
        with _Sandbox() as sb:
            result = _run(sb.project, home=sb.home)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("[error] project config not found:", result.stderr)
        self.assertIn("cartopian.toml", result.stderr)


class TestGitVersioningAttribution(unittest.TestCase):
    _BARE_PROJECT = (
        '[project]\n'
        'id = "demo"\n'
        'name = "Demo"\n'
        'protocol_version = "v0.2.0"\n'
    )

    def test_both_silent_protocol_default(self):
        with _Sandbox() as sb:
            _write(sb.project / "cartopian.toml", self._BARE_PROJECT)
            result = _run(sb.project, home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = json.loads(result.stdout.splitlines()[0])
        self.assertEqual(
            record["defaults_attribution"]["git_versioning"],
            "protocol-default: false",
        )
        self.assertIsNone(record["git"])
        self.assertFalse(record["git_versioning"])

    def test_global_supplies_git_versioning(self):
        with _Sandbox() as sb:
            _write(
                sb.home / ".cartopian" / "cartopian.toml",
                '[defaults]\ngit_versioning = true\n\n'
                '[git]\npm_owns_product_branches = true\n'
                'default_branch_pattern = "main"\n',
            )
            _write(sb.project / "cartopian.toml", self._BARE_PROJECT)
            result = _run(sb.project, home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = json.loads(result.stdout.splitlines()[0])
        self.assertEqual(record["defaults_attribution"]["git_versioning"], "global")
        self.assertTrue(record["git_versioning"])
        self.assertEqual(
            record["git"],
            {"pm_owns_product_branches": True, "default_branch_pattern": "main"},
        )

    def test_project_supplies_git_versioning(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                self._BARE_PROJECT + '\n[defaults]\ngit_versioning = true\n',
            )
            result = _run(sb.project, home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = json.loads(result.stdout.splitlines()[0])
        self.assertEqual(record["defaults_attribution"]["git_versioning"], "project")
        self.assertTrue(record["git_versioning"])
        self.assertEqual(record["git"], {})


class TestRelativeProjectPathRejected(unittest.TestCase):
    def test_relative_project_path_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            env = {
                "HOME": str(home),
                "PATH": os.environ.get("PATH", ""),
            }
            result = subprocess.run(
                [sys.executable, str(ENTRYPOINT), "resolve-config", "projects/cartopian-manager"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr.rstrip("\n"),
            "[usage] project_path must be an absolute path; got: projects/cartopian-manager",
        )


class TestRelativeWorkRootMappingRejected(unittest.TestCase):
    def test_relative_work_root_mapping_rejected(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                '[project]\n'
                'id = "wr-rel"\n'
                'name = "WR Rel"\n'
                'protocol_version = "v0.2.0"\n'
                'work_roots = ["site"]\n'
                '\n'
                '[roles]\n'
                'pm = "."\n'
                'operator = "."\n',
            )
            _write(
                sb.project / "cartopian.local.toml",
                '[work_roots]\nsite = "relative/path"\n',
            )
            result = _run(sb.project, home=sb.home)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr.rstrip("\n"),
            '[work-root] non-absolute path: site = "relative/path" — '
            "cartopian.local.toml must use absolute paths (DEC-003)",
        )


class TestAbsoluteWorkRootMappingAccepted(unittest.TestCase):
    def test_absolute_work_root_mapping_accepted(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                '[project]\n'
                'id = "wr-abs"\n'
                'name = "WR Abs"\n'
                'protocol_version = "v0.2.0"\n'
                'work_roots = ["site"]\n'
                '\n'
                '[roles]\n'
                'pm = "."\n'
                'operator = "."\n',
            )
            _write(
                sb.project / "cartopian.local.toml",
                '[work_roots]\nsite = "/tmp/site-dir"\n',
            )
            result = _run(sb.project, home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record["work_roots"], {"site": "/tmp/site-dir"})


class TestEmptyDescriptionWarning(unittest.TestCase):
    def test_custom_role_empty_description_warns_but_emits(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                '[project]\n'
                'id = "demo"\n'
                'name = "Demo"\n'
                'protocol_version = "v0.2.0"\n'
                '\n'
                '[roles]\n'
                'designer = ""\n',
            )
            result = _run(sb.project, home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("[validation] empty role description: designer", result.stderr)
        record = json.loads(result.stdout.splitlines()[0])
        self.assertEqual(record["roles"]["designer"], "")
        self.assertIn("pm", record["roles"])
        self.assertIn("operator", record["roles"])


if __name__ == "__main__":
    unittest.main()
