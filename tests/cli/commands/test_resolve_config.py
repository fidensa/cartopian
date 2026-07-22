"""Tests for `cartopian resolve-config`."""
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
                'auto_start_tasks = true\n'
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
            "capabilities",
            "handoffs",
            "reviews",
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
            {
                "agent": "claude",
                "model": None,
                "effort": None,
                "auto_start_tasks": True,
                "auto_start_reviews": None,
                "timeout": "60m",
            },
        )
        self.assertEqual(
            record["automation"],
            {
                "initiation": "operator",
                "confirmation": "until-blocked",
                "max_handoffs_per_run": 3,
            },
        )
        self.assertEqual(record["reviews"]["planning"]["mode"], "off")
        self.assertEqual(record["reviews"]["task_closure"]["mode"], "off")
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


class TestReviewPolicyResolution(unittest.TestCase):
    _PROJECT = (
        '[project]\n'
        'id = "demo"\n'
        'name = "Demo"\n'
        'protocol_version = "v0.6.0"\n'
    )

    def _record(self, result):
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return json.loads(result.stdout.splitlines()[0])

    def test_arbitrary_role_name_can_own_both_review_loops(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                self._PROJECT
                + '\n[roles]\nquality-checker = "Checks work independently."\n'
                + '\n[reviews]\n'
                + 'planning = "required"\nplanning_role = "quality-checker"\n'
                + 'task_closure = "required"\ntask_role = "quality-checker"\n',
            )
            result = _run(sb.project, home=sb.home)
        reviews = self._record(result)["reviews"]
        self.assertEqual(reviews["planning"]["role"], "quality-checker")
        self.assertEqual(reviews["task_closure"]["role"], "quality-checker")

    def test_legacy_project_with_reviewer_preserves_both_review_loops(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                '[project]\nid = "demo"\nname = "Demo"\n'
                'protocol_version = "v0.4.0"\n'
                '\n[roles]\nreviewer = "Checks work."\n',
            )
            result = _run(sb.project, home=sb.home)
        reviews = self._record(result)["reviews"]
        self.assertEqual(reviews["planning"]["mode"], "required")
        self.assertEqual(reviews["planning"]["role"], "reviewer")
        self.assertEqual(
            reviews["task_closure"]["attribution"]["mode"],
            "legacy-pre-v0.5",
        )

    def test_current_project_never_infers_review_from_reviewer_role(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                self._PROJECT + '\n[roles]\nreviewer = "Checks work."\n',
            )
            result = _run(sb.project, home=sb.home)
        reviews = self._record(result)["reviews"]
        self.assertEqual(reviews["planning"]["mode"], "off")
        self.assertEqual(reviews["task_closure"]["mode"], "off")

    def test_project_can_disable_globally_required_reviews(self):
        with _Sandbox() as sb:
            _write(
                sb.home / ".cartopian" / "cartopian.toml",
                '[roles]\nreviewer = "Checks work."\n'
                '\n[reviews]\n'
                'planning = "required"\nplanning_role = "reviewer"\n'
                'task_closure = "required"\ntask_role = "reviewer"\n',
            )
            _write(
                sb.project / "cartopian.toml",
                self._PROJECT
                + '\n[reviews]\nplanning = "off"\ntask_closure = "off"\n',
            )
            result = _run(sb.project, home=sb.home)
        record = self._record(result)
        self.assertIn("reviewer", record["roles"])
        self.assertEqual(record["reviews"]["planning"]["mode"], "off")
        self.assertIsNone(record["reviews"]["planning"]["role"])
        self.assertEqual(record["reviews"]["task_closure"]["mode"], "off")

    def test_required_review_needs_declared_assigned_role(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                self._PROJECT
                + '\n[reviews]\n'
                + 'planning = "off"\n'
                + 'task_closure = "required"\ntask_role = "reviewer"\n',
            )
            result = _run(sb.project, home=sb.home)
        self.assertEqual(result.returncode, 1)
        self.assertIn("names undeclared role 'reviewer'", result.stderr)

    def test_malformed_reviews_shape_fails_closed(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                'reviews = "off"\n\n' + self._PROJECT,
            )
            result = _run(sb.project, home=sb.home)
        self.assertEqual(result.returncode, 1)
        self.assertIn("[reviews] must be a table", result.stderr)


class TestHandoffLaunchResolution(unittest.TestCase):
    def test_legacy_launch_keys_map_to_explicit_fields(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                '[project]\nid = "demo"\nname = "Demo"\n'
                'protocol_version = "v0.4.0"\n'
                '\n[roles]\nreviewer = "Checks work."\n'
                '\n[handoffs.reviewer]\nagent = "claude"\n'
                'auto_start = true\nplanning_reviews = true\n',
            )
            result = _run(sb.project, home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        handoff = json.loads(result.stdout)["handoffs"]["reviewer"]
        self.assertTrue(handoff["auto_start_tasks"])
        self.assertTrue(handoff["auto_start_reviews"])
        self.assertNotIn("auto_start", handoff)
        self.assertNotIn("planning_reviews", handoff)


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

    def test_defaults_only_cartopian_toml_is_not_a_project(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                '[defaults]\n'
                'git_versioning = false\n'
                '\n'
                '[roles]\n'
                'pm = "Plans the work."\n',
            )
            result = _run(sb.project, home=sb.home)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr.rstrip("\n"),
            f"[guard] {sb.project.resolve() / 'cartopian.toml'} is a Cartopian workspace config, "
            "not a project config. "
            "Run `cartopian discover-projects` (or call the `discover_projects` MCP tool) "
            "to list registered projects, then pass a project id or absolute path to this command.",
        )


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


class TestAutomationInitiationResolution(unittest.TestCase):
    _BARE_PROJECT = (
        '[project]\n'
        'id = "demo"\n'
        'name = "Demo"\n'
        'protocol_version = "v0.2.0"\n'
    )

    def _record(self, result):
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return json.loads(result.stdout.splitlines()[0])

    def test_protocol_default_is_operator(self):
        with _Sandbox() as sb:
            _write(sb.project / "cartopian.toml", self._BARE_PROJECT)
            result = _run(sb.project, home=sb.home)
        record = self._record(result)
        self.assertEqual(record["automation"]["initiation"], "operator")

    def test_global_supplies_initiation(self):
        with _Sandbox() as sb:
            _write(
                sb.home / ".cartopian" / "cartopian.toml",
                '[automation]\ninitiation = "auto"\n',
            )
            _write(sb.project / "cartopian.toml", self._BARE_PROJECT)
            result = _run(sb.project, home=sb.home)
        record = self._record(result)
        self.assertEqual(record["automation"]["initiation"], "auto")

    def test_project_overrides_global_initiation(self):
        with _Sandbox() as sb:
            _write(
                sb.home / ".cartopian" / "cartopian.toml",
                '[automation]\ninitiation = "auto"\n',
            )
            _write(
                sb.project / "cartopian.toml",
                self._BARE_PROJECT + '\n[automation]\ninitiation = "operator"\n',
            )
            result = _run(sb.project, home=sb.home)
        record = self._record(result)
        self.assertEqual(record["automation"]["initiation"], "operator")

    def test_project_opts_into_auto_over_global_silence(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                self._BARE_PROJECT + '\n[automation]\ninitiation = "auto"\n',
            )
            result = _run(sb.project, home=sb.home)
        record = self._record(result)
        self.assertEqual(record["automation"]["initiation"], "auto")

    def test_initiation_merges_independently_of_other_automation_keys(self):
        # Global sets pace; project sets initiation — both must survive.
        with _Sandbox() as sb:
            _write(
                sb.home / ".cartopian" / "cartopian.toml",
                '[automation]\nconfirmation = "until-blocked"\nmax_handoffs_per_run = 4\n',
            )
            _write(
                sb.project / "cartopian.toml",
                self._BARE_PROJECT + '\n[automation]\ninitiation = "auto"\n',
            )
            result = _run(sb.project, home=sb.home)
        record = self._record(result)
        self.assertEqual(
            record["automation"],
            {
                "initiation": "auto",
                "confirmation": "until-blocked",
                "max_handoffs_per_run": 4,
            },
        )

    def test_unknown_initiation_value_fails_safe_to_operator(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                self._BARE_PROJECT + '\n[automation]\ninitiation = "always"\n',
            )
            result = _run(sb.project, home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("[validation]", result.stderr)
        self.assertIn("initiation", result.stderr)
        record = json.loads(result.stdout.splitlines()[0])
        self.assertEqual(record["automation"]["initiation"], "operator")


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
            "cartopian.local.toml must use absolute paths",
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
