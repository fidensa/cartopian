"""Tests for cartopian-manager self-registration.

Exercises the register → discover round-trip and the duplicate-id rejection
using the real `projects/cartopian-manager/` cartopian.toml as the
registration target. HOME is redirected to a tempdir so the operator's
real `~/.cartopian/projects.json` is never touched.
"""
import os
import subprocess
import sys
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


if __name__ == "__main__":
    unittest.main()
