"""Tests for the config-schema migration gate on ``[project].protocol_version``.

The gate compares a project config's declared ``[project].protocol_version``
against the shipped protocol version (the topmost ``### vX.Y.Z`` entry under
``## Entries`` in ``protocol/CHANGELOG.md``) and classifies:

- current                → pass, no gate noise;
- older-but-migratable   → surface a migration action naming detected and
                           shipped versions;
- unknown / newer        → fail closed with a named residual, never mutating
                           ``cartopian.toml``.

Trigger surfaces under test: ``next-action`` and ``plan-audit`` (PM
session-startup orientation) and ``scripts/install.py`` registered-project
reconciliation (upgrade/install).
"""
import argparse
import contextlib
import importlib.util
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli import protocol_gate  # red stage: module must exist
from cli.commands import next_action, plan_audit
from cli.main import EXIT_FAIL, EXIT_OK

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install.py"


def _load_install_module():
    spec = importlib.util.spec_from_file_location("cartopian_install_gate", INSTALL_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


install_mod = _load_install_module()


# Fixture CHANGELOG whose shipped (topmost) protocol version is v0.4.0, with
# a prior v0.3.0 entry — so a config carrying v0.3.0 is stale-but-migratable.
_FIXTURE_CHANGELOG = """# Cartopian Protocol Changelog

Fixture changelog for gate tests.

## Entries

### v0.4.0 — Fixture: newer shipped protocol

- **Protocol version:** `v0.4.0`

#### Applies-when precondition

Applies when the project's `cartopian.toml` `[project] protocol_version` is
unset, missing, or lexically less than `v0.4.0`.

### v0.3.0 — Fixture: prior protocol

- **Protocol version:** `v0.3.0`
"""


def _project_toml(version_line: str) -> str:
    return (
        "[project]\n"
        'id = "gate-proj"\n'
        'name = "Gate Project"\n'
        f"{version_line}"
    )


def _make_project(tmp: Path, *, version_line: str = 'protocol_version = "v0.3.0"\n') -> Path:
    project = tmp / "project"
    for sub in ("tasks/open", "tasks/in-progress", "tasks/in-review", "tasks/done",
                "phases", "prompts", "reports", "reviews"):
        (project / sub).mkdir(parents=True, exist_ok=True)
    (project / "cartopian.toml").write_text(_project_toml(version_line), encoding="utf-8")
    return project


@contextlib.contextmanager
def _fixture_changelog():
    """Patch the gate's default CHANGELOG to the v0.4.0-shipped fixture."""
    with tempfile.TemporaryDirectory(prefix="cartopian-gate-changelog-") as tmp:
        changelog = Path(tmp) / "CHANGELOG.md"
        changelog.write_text(_FIXTURE_CHANGELOG, encoding="utf-8")
        with mock.patch.object(protocol_gate, "DEFAULT_CHANGELOG_PATH", changelog):
            yield changelog


@contextlib.contextmanager
def _isolated_home():
    """Point ``Path.home()`` at a fresh temp dir (role-resolution isolation)."""
    with tempfile.TemporaryDirectory(prefix="cartopian-home-") as tmp:
        with mock.patch.object(Path, "home", return_value=Path(tmp)):
            yield Path(tmp)


def _invoke(module, project_path: str):
    """Invoke ``module.handler`` in-process; return (records, exit_code, stderr)."""
    args = argparse.Namespace(project_path=project_path)
    captured = []

    def _capture(record, *, out=None):
        captured.append(record)

    stderr = io.StringIO()
    original = module.emit_record
    module.emit_record = _capture
    try:
        with contextlib.redirect_stderr(stderr):
            rc = module.handler(args)
    finally:
        module.emit_record = original
    return captured, rc, stderr.getvalue()


class TestClassification(unittest.TestCase):
    def test_read_shipped_version_is_topmost_entry(self):
        with _fixture_changelog() as changelog:
            self.assertEqual(
                protocol_gate.read_shipped_protocol_version(changelog), "v0.4.0"
            )

    def test_current_passes(self):
        verdict = protocol_gate.classify_protocol_version("v0.4.0", "v0.4.0")
        self.assertEqual(verdict["status"], protocol_gate.GATE_CURRENT)

    def test_older_is_migratable_and_names_both_versions(self):
        verdict = protocol_gate.classify_protocol_version("v0.3.0", "v0.4.0")
        self.assertEqual(verdict["status"], protocol_gate.GATE_MIGRATE)
        self.assertEqual(verdict["detected_version"], "v0.3.0")
        self.assertEqual(verdict["shipped_version"], "v0.4.0")
        self.assertIn("v0.3.0", verdict["detail"])
        self.assertIn("v0.4.0", verdict["detail"])
        self.assertIn("CHANGELOG", verdict["detail"])

    def test_unset_marker_is_migratable(self):
        # CHANGELOG applies-when: "unset, missing, or lexically less".
        for declared in (None, "", "   "):
            verdict = protocol_gate.classify_protocol_version(declared, "v0.4.0")
            self.assertEqual(verdict["status"], protocol_gate.GATE_MIGRATE, declared)

    def test_newer_and_malformed_fail_closed(self):
        for declared in ("v9.9.9", "v0.5.0", "garbage", "0.3.0"):
            verdict = protocol_gate.classify_protocol_version(declared, "v0.4.0")
            self.assertEqual(verdict["status"], protocol_gate.GATE_BLOCKED, declared)
            self.assertIn(protocol_gate.RESIDUAL_NAME, verdict["detail"])
            self.assertIn("v0.4.0", verdict["detail"])


class TestNextActionGate(unittest.TestCase):
    """Session-startup orientation surface: ``cartopian next-action``."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cartopian-gate-na-")
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)

    def test_stale_version_surfaces_migration_blocker(self):
        project = _make_project(self.tmp_path)
        with _fixture_changelog(), _isolated_home():
            records, rc, _stderr = _invoke(next_action, str(project))
        self.assertEqual(rc, EXIT_OK)
        self.assertEqual(len(records), 1)
        gate_blockers = [b for b in records[0]["blockers"] if "v0.3.0" in b and "v0.4.0" in b]
        self.assertEqual(len(gate_blockers), 1, records[0]["blockers"])
        self.assertIn("migration", gate_blockers[0].lower())

    def test_missing_marker_surfaces_migration_not_missing_key(self):
        # A config with no [project].protocol_version at all must reach the
        # gate and classify as unset/older-but-migratable — matching installer
        # reconciliation — not be rejected by the required-keys check.
        project = _make_project(self.tmp_path, version_line="")
        with _fixture_changelog(), _isolated_home():
            records, rc, stderr = _invoke(next_action, str(project))
        self.assertNotIn("missing required key", stderr)
        self.assertEqual(rc, EXIT_OK)
        self.assertEqual(len(records), 1)
        gate_blockers = [
            b for b in records[0]["blockers"] if "unset" in b and "v0.4.0" in b
        ]
        self.assertEqual(len(gate_blockers), 1, records[0]["blockers"])
        self.assertIn("migration", gate_blockers[0].lower())

    def test_current_version_passes_with_no_gate_noise(self):
        project = _make_project(
            self.tmp_path, version_line='protocol_version = "v0.4.0"\n'
        )
        with _fixture_changelog(), _isolated_home():
            records, rc, stderr = _invoke(next_action, str(project))
        self.assertEqual(rc, EXIT_OK)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["blockers"], [])
        self.assertNotIn("protocol_version", stderr)
        self.assertNotIn("migration", stderr.lower())

    def test_unknown_version_fails_closed_without_mutating_config(self):
        project = _make_project(
            self.tmp_path, version_line='protocol_version = "v9.9.9"\n'
        )
        toml_path = project / "cartopian.toml"
        before = toml_path.read_bytes()
        with _fixture_changelog(), _isolated_home():
            records, rc, stderr = _invoke(next_action, str(project))
        self.assertEqual(rc, EXIT_FAIL)
        self.assertEqual(records, [])
        self.assertIn("[guard]", stderr)
        self.assertIn(protocol_gate.RESIDUAL_NAME, stderr)
        self.assertIn("v9.9.9", stderr)
        self.assertIn("v0.4.0", stderr)
        self.assertEqual(toml_path.read_bytes(), before)


class TestPlanAuditGate(unittest.TestCase):
    """Session-startup orientation surface: ``cartopian plan-audit``."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cartopian-gate-pa-")
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)

    def _gate_entries(self, entries):
        return [e for e in entries if str(e.get("kind", "")).startswith("protocol-version")]

    def test_stale_version_surfaces_migration_warning(self):
        project = _make_project(self.tmp_path)
        with _fixture_changelog(), _isolated_home():
            records, rc, _stderr = _invoke(plan_audit, str(project))
        self.assertEqual(rc, EXIT_OK)
        self.assertEqual(len(records), 1)
        gate_warnings = self._gate_entries(records[0]["warnings"])
        self.assertEqual(len(gate_warnings), 1, records[0]["warnings"])
        warning = gate_warnings[0]
        self.assertEqual(warning["kind"], "protocol-version-migration")
        self.assertEqual(warning["detected_version"], "v0.3.0")
        self.assertEqual(warning["shipped_version"], "v0.4.0")
        self.assertIn("v0.3.0", warning["detail"])
        self.assertIn("v0.4.0", warning["detail"])
        self.assertFalse(records[0]["clean"])

    def test_missing_marker_surfaces_migration_not_missing_key(self):
        # A config with no [project].protocol_version at all must reach the
        # gate and classify as unset/older-but-migratable — matching installer
        # reconciliation — not be rejected by the required-keys check.
        project = _make_project(self.tmp_path, version_line="")
        with _fixture_changelog(), _isolated_home():
            records, rc, stderr = _invoke(plan_audit, str(project))
        self.assertNotIn("missing required key", stderr)
        self.assertEqual(rc, EXIT_OK)
        self.assertEqual(len(records), 1)
        gate_warnings = self._gate_entries(records[0]["warnings"])
        self.assertEqual(len(gate_warnings), 1, records[0]["warnings"])
        warning = gate_warnings[0]
        self.assertEqual(warning["kind"], "protocol-version-migration")
        self.assertEqual(warning["detected_version"], "unset")
        self.assertEqual(warning["shipped_version"], "v0.4.0")
        self.assertIn("unset", warning["detail"])
        self.assertIn("v0.4.0", warning["detail"])

    def test_current_version_passes_with_no_gate_noise(self):
        project = _make_project(
            self.tmp_path, version_line='protocol_version = "v0.4.0"\n'
        )
        with _fixture_changelog(), _isolated_home():
            records, rc, stderr = _invoke(plan_audit, str(project))
        self.assertEqual(rc, EXIT_OK)
        self.assertEqual(len(records), 1)
        self.assertEqual(self._gate_entries(records[0]["warnings"]), [])
        self.assertEqual(self._gate_entries(records[0]["blockers"]), [])
        self.assertNotIn("migration", stderr.lower())

    def test_unknown_version_fails_closed_without_mutating_config(self):
        project = _make_project(
            self.tmp_path, version_line='protocol_version = "v9.9.9"\n'
        )
        toml_path = project / "cartopian.toml"
        before = toml_path.read_bytes()
        with _fixture_changelog(), _isolated_home():
            records, rc, stderr = _invoke(plan_audit, str(project))
        self.assertEqual(rc, EXIT_FAIL)
        self.assertEqual(len(records), 1)
        gate_blockers = self._gate_entries(records[0]["blockers"])
        self.assertEqual(len(gate_blockers), 1, records[0]["blockers"])
        blocker = gate_blockers[0]
        self.assertEqual(blocker["kind"], "protocol-version-unverifiable")
        self.assertEqual(blocker["detected_version"], "v9.9.9")
        self.assertEqual(blocker["shipped_version"], "v0.4.0")
        self.assertIn(protocol_gate.RESIDUAL_NAME, stderr)
        self.assertEqual(toml_path.read_bytes(), before)


class TestInstallReconciliationGate(unittest.TestCase):
    """Upgrade/install surface: registered-project reconciliation."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cartopian-gate-install-")
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        self.install_root = tmp / ".cartopian"
        self.install_root.mkdir(parents=True)
        self.project = _make_project(tmp)
        (self.install_root / "projects.json").write_text(
            json.dumps([{"id": "gate-proj", "path": str(self.project), "label": None}]) + "\n",
            encoding="utf-8",
        )
        # Minimal source tree: the gate module plus the fixture CHANGELOG.
        self.source_root = tmp / "source"
        (self.source_root / "cli").mkdir(parents=True)
        shutil.copy2(
            REPO_ROOT / "cli" / "protocol_gate.py",
            self.source_root / "cli" / "protocol_gate.py",
        )
        (self.source_root / "protocol").mkdir(parents=True)
        (self.source_root / "protocol" / "CHANGELOG.md").write_text(
            _FIXTURE_CHANGELOG, encoding="utf-8"
        )

    def _set_project_version(self, version_line: str) -> None:
        (self.project / "cartopian.toml").write_text(
            _project_toml(version_line), encoding="utf-8"
        )

    def _reconcile(self):
        actions = []
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            residuals = install_mod.reconcile_registered_projects(
                self.install_root, self.source_root, actions
            )
        return residuals, actions, stderr.getvalue()

    def test_stale_version_surfaces_migration_naming_both_versions(self):
        residuals, _actions, stderr = self._reconcile()
        self.assertEqual(residuals, [])
        self.assertIn("[migration]", stderr)
        self.assertIn("v0.3.0", stderr)
        self.assertIn("v0.4.0", stderr)

    def test_current_version_is_silent(self):
        self._set_project_version('protocol_version = "v0.4.0"\n')
        residuals, _actions, stderr = self._reconcile()
        self.assertEqual(residuals, [])
        self.assertNotIn("[migration]", stderr)
        self.assertNotIn("[residual]", stderr)

    def test_unknown_version_fails_closed_without_mutating_config(self):
        self._set_project_version('protocol_version = "v9.9.9"\n')
        toml_path = self.project / "cartopian.toml"
        before = toml_path.read_bytes()
        residuals, _actions, stderr = self._reconcile()
        self.assertEqual(len(residuals), 1)
        self.assertIn("[residual]", stderr)
        self.assertIn(protocol_gate.RESIDUAL_NAME, stderr)
        self.assertIn("v9.9.9", stderr)
        self.assertIn("v0.4.0", stderr)
        self.assertEqual(toml_path.read_bytes(), before)

    def test_source_without_gate_module_skips_gracefully(self):
        (self.source_root / "cli" / "protocol_gate.py").unlink()
        residuals, actions, _stderr = self._reconcile()
        self.assertEqual(residuals, [])
        self.assertTrue(any("protocol-version" in a for a in actions), actions)

    def test_missing_registry_is_a_noop(self):
        (self.install_root / "projects.json").unlink()
        residuals, _actions, stderr = self._reconcile()
        self.assertEqual(residuals, [])
        self.assertEqual(stderr, "")


class TestInstallMainGate(unittest.TestCase):
    """End-to-end: ``scripts/install.py`` main() against the real repo
    CHANGELOG (shipped version = its topmost entry)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cartopian-gate-main-")
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        self.install_root = tmp / ".cartopian"
        self.install_root.mkdir(parents=True)
        self.project = _make_project(tmp)
        (self.install_root / "projects.json").write_text(
            json.dumps([{"id": "gate-proj", "path": str(self.project), "label": None}]) + "\n",
            encoding="utf-8",
        )
        self.shipped = protocol_gate.read_shipped_protocol_version(
            REPO_ROOT / "protocol" / "CHANGELOG.md"
        )

    def _run_main(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = install_mod.main(
                ["--source", str(REPO_ROOT), "--prefix", str(self.install_root), "--quiet"]
            )
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_stale_registered_project_is_surfaced_on_upgrade(self):
        (self.project / "cartopian.toml").write_text(
            _project_toml('protocol_version = "v0.2.0"\n'), encoding="utf-8"
        )
        rc, _stdout, stderr = self._run_main()
        self.assertEqual(rc, install_mod.EXIT_OK)
        self.assertIn("[migration]", stderr)
        self.assertIn("v0.2.0", stderr)
        self.assertIn(self.shipped, stderr)

    def test_unknown_registered_project_fails_install_closed(self):
        (self.project / "cartopian.toml").write_text(
            _project_toml('protocol_version = "v9.9.9"\n'), encoding="utf-8"
        )
        toml_path = self.project / "cartopian.toml"
        before = toml_path.read_bytes()
        rc, _stdout, stderr = self._run_main()
        self.assertEqual(rc, install_mod.EXIT_FAIL)
        self.assertIn("[residual]", stderr)
        self.assertIn("v9.9.9", stderr)
        self.assertEqual(toml_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
