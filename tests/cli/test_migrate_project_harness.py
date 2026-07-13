"""Executable harness for the PM-owned migration flow (`skills/migrate-project.md`).

The skill itself is agent-executed prose and is not callable by this suite, so
the test target is the *mechanized* operations it relies on: enumerating the
applicable `CHANGELOG.md` entries with the protocol gate, applying each entry's
config edits + marker bump through `cartopian update-config`, and — the
load-bearing rule — advancing the `[project].protocol_version` marker for an
entry **only** after that entry's steps (including any delegated non-config
step, simulated here as satisfied/unsatisfied) have completed.

The `_migrate` driver below is a faithful model of the skill's Step 2–3 loop.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"
CHANGELOG = REPO_ROOT / "protocol" / "CHANGELOG.md"

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "cartopian_protocol_gate", REPO_ROOT / "cli" / "protocol_gate.py"
)
protocol_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(protocol_gate)


def _changelog_versions_ascending():
    text = CHANGELOG.read_text(encoding="utf-8")
    _, _, body = text.partition("\n## Entries\n")
    vs = re.findall(r"^###\s+(v\d+\.\d+\.\d+)\b", body, flags=re.MULTILINE)
    assert vs, "no CHANGELOG entries found"
    return list(reversed(vs))  # oldest first


ASCENDING = _changelog_versions_ascending()
SHIPPED = ASCENDING[-1]

# Per CHANGELOG, v0.2.0 (file rename + header swap) and v0.3.0 (header swap +
# wrapper edits) carry non-config "delegated" steps; v0.4.0 is pure config. The
# driver takes the satisfied/unsatisfied state of those steps as a parameter, so
# the tests can exercise both the completed and the blocked path.


def _run_uc(proj, home, *args):
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "")}
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), "update-config", str(proj), *args],
        cwd=str(REPO_ROOT), capture_output=True, text=True, env=env,
    )


def _marker(cfg_path):
    return tomllib.loads(cfg_path.read_text()).get("project", {}).get("protocol_version")


def _applicable(start):
    """Entries that apply given the starting marker (None = unset → all)."""
    if start is None:
        return list(ASCENDING)
    return [v for v in ASCENDING if v > start]


def _migrate(proj, home, start, delegated_satisfied):
    """Model of the skill's Step 2–3 loop. Returns the version reached.

    For each applicable entry oldest-first: if the entry has a delegated step
    that is not satisfied, stop without bumping (leave the marker at the last
    fully-applied version). Otherwise perform the marker bump via update-config.
    """
    reached = start
    for v in _applicable(start):
        if not delegated_satisfied.get(v, True):
            return reached
        proc = _run_uc(proj, home, "--set", f"project.protocol_version={v}")
        assert proc.returncode == 0, proc.stderr
        reached = v
    return reached


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.proj = self.tmp / "proj"
        self.proj.mkdir()
        self.cfg = self.proj / "cartopian.toml"

    def tearDown(self):
        self._tmp.cleanup()

    def _write_marker(self, version):
        marker = "" if version is None else f'protocol_version = "{version}"\n'
        self.cfg.write_text(f'[project]\nname = "D"\nid = "d"\n{marker}', encoding="utf-8")

    def _classify(self):
        declared = _marker(self.cfg)
        return protocol_gate.classify_protocol_version(declared, SHIPPED)["status"]


class TestMarkerProgression(_Base):
    def test_from_unset_reaches_shipped(self):
        self._write_marker(None)
        reached = _migrate(self.proj, self.home, None, {})
        self.assertEqual(reached, SHIPPED)
        self.assertEqual(_marker(self.cfg), SHIPPED)
        self.assertEqual(self._classify(), protocol_gate.GATE_CURRENT)

    def test_from_each_historical_marker(self):
        for start in ASCENDING[:-1]:  # every version below shipped
            self._write_marker(start)
            reached = _migrate(self.proj, self.home, start, {})
            self.assertEqual(reached, SHIPPED, f"start={start}")
            self.assertEqual(self._classify(), protocol_gate.GATE_CURRENT, f"start={start}")

    def test_already_current_is_noop(self):
        self._write_marker(SHIPPED)
        before = self.cfg.read_bytes()
        reached = _migrate(self.proj, self.home, SHIPPED, {})
        self.assertEqual(reached, SHIPPED)
        self.assertEqual(self.cfg.read_bytes(), before)

    def test_unsatisfied_delegated_step_blocks_bump(self):
        # Start unset; the oldest applicable entry has an unsatisfied delegated
        # step, so the marker must not advance past the prior version (unset).
        self._write_marker(None)
        oldest = ASCENDING[0]
        reached = _migrate(self.proj, self.home, None, {oldest: False})
        self.assertIsNone(reached)
        self.assertIsNone(_marker(self.cfg))
        self.assertEqual(self._classify(), protocol_gate.GATE_MIGRATE)

    def test_partial_then_resume_is_idempotent(self):
        # Block at the second entry, then unblock and resume — reaches shipped,
        # and a further run is a no-op.
        if len(ASCENDING) < 2:
            self.skipTest("need >=2 changelog entries")
        self._write_marker(None)
        blocked = ASCENDING[1]
        reached = _migrate(self.proj, self.home, None, {blocked: False})
        self.assertEqual(reached, ASCENDING[0])
        # resume with everything satisfied
        reached2 = _migrate(self.proj, self.home, _marker(self.cfg), {})
        self.assertEqual(reached2, SHIPPED)
        before = self.cfg.read_bytes()
        _migrate(self.proj, self.home, _marker(self.cfg), {})
        self.assertEqual(self.cfg.read_bytes(), before)


class TestConfigEditDuringMigration(_Base):
    def test_v040_initiation_opt_in_is_a_real_config_edit(self):
        # Simulate the v0.4.0 entry's operator opt-in: a real config edit through
        # update-config, then the marker bump.
        self._write_marker("v0.3.0")
        proc = _run_uc(self.proj, self.home, "--set", "automation.initiation=auto")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        _run_uc(self.proj, self.home, "--set", "project.protocol_version=v0.4.0")
        cfg = tomllib.loads(self.cfg.read_text())
        self.assertEqual(cfg["automation"]["initiation"], "auto")
        self.assertEqual(cfg["project"]["protocol_version"], "v0.4.0")


if __name__ == "__main__":
    unittest.main()
