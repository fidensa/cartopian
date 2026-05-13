"""Tests for FR-008 / TASK-01-017 cartopian-manager self-registration.

Exercises the register → discover round-trip and the FR-003 duplicate-id
rejection using the real `projects/cartopian-manager/` cartopian.toml as
the registration target. HOME is redirected to a tempdir so the operator's
real `~/.cartopian/projects.json` is never touched.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"
CARTOPIAN_MANAGER = REPO_ROOT / "projects" / "cartopian-manager"


def _run(*cli_args, home):
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
    }
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), *cli_args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


# `projects/` is gitignored (its own repo); skip self-registration tests on
# clones where the cartopian-manager fixture is not checked out.
_FIXTURE_AVAILABLE = (CARTOPIAN_MANAGER / "cartopian.toml").is_file()


@unittest.skipUnless(
    _FIXTURE_AVAILABLE,
    f"cartopian-manager fixture not present at {CARTOPIAN_MANAGER}",
)
class TestSelfRegisterCartopianManager(unittest.TestCase):

    def test_register_then_discover_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            register = _run(
                "register-project", str(CARTOPIAN_MANAGER), home=home,
            )
            self.assertEqual(register.returncode, 0, msg=register.stderr)
            self.assertEqual(register.stderr, "")
            rec = json.loads(register.stdout.strip())
            self.assertEqual(rec["action"], "register-project")
            self.assertEqual(rec["details"]["id"], "cartopian-manager")
            self.assertEqual(
                rec["details"]["path"], str(CARTOPIAN_MANAGER.resolve()),
            )
            self.assertEqual(rec["details"]["label"], "Cartopian Manager")

            persisted = json.loads(
                (home / ".cartopian" / "projects.json").read_text(
                    encoding="utf-8",
                )
            )
            self.assertEqual(len(persisted), 1)
            entry = persisted[0]
            self.assertEqual(entry["id"], "cartopian-manager")
            self.assertEqual(entry["path"], str(CARTOPIAN_MANAGER.resolve()))
            self.assertEqual(entry["label"], "Cartopian Manager")
            self.assertIsNotNone(entry["label"])

            discover = _run("discover-projects", home=home)
            self.assertEqual(discover.returncode, 0, msg=discover.stderr)
            lines = discover.stdout.splitlines()
            self.assertEqual(
                len(lines), 1, msg=f"expected single NDJSON record, got: {lines!r}",
            )
            discovered = json.loads(lines[0])
            self.assertEqual(discovered["id"], "cartopian-manager")
            self.assertEqual(
                discovered["path"], str(CARTOPIAN_MANAGER.resolve()),
            )
            self.assertEqual(discovered["label"], "Cartopian Manager")

    def test_duplicate_registration_is_rejected(self):
        # FR-003 duplicate-id rule: re-registering the same project must be
        # rejected. The path-collision guard fires first (same path AND id),
        # so it is the diagnostic the operator sees.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            r1 = _run("register-project", str(CARTOPIAN_MANAGER), home=home)
            self.assertEqual(r1.returncode, 0, msg=r1.stderr)
            r2 = _run("register-project", str(CARTOPIAN_MANAGER), home=home)
            self.assertEqual(r2.returncode, 1)
            self.assertEqual(r2.stdout, "")
            self.assertTrue(r2.stderr.startswith("[guard]"), msg=r2.stderr)
            self.assertIn("already registered", r2.stderr)
            persisted = json.loads(
                (home / ".cartopian" / "projects.json").read_text(
                    encoding="utf-8",
                )
            )
            self.assertEqual(len(persisted), 1)


if __name__ == "__main__":
    unittest.main()
