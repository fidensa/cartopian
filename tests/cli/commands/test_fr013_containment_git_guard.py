"""Fail-closed harness: contained PM + git.pm_owns_product_branches=true (FR-013, P01-BUILD-006).

A contained PM (DEC-001 capability floor — no shell, no git/gh) with
``git.pm_owns_product_branches = true`` is an UNSUPPORTED combination until
mediated-git lands (RM-004 deferred). Per REVIEW-PLAN-002 F1 the selected
behavior is **fail-closed block**: at config-resolution / lifecycle entry the
PM is refused with a structured ``[guard]`` line naming the unsupported
combination and a non-zero exit; no lifecycle record is emitted.

These tests pin that behavior at both lifecycle-entry surfaces a contained PM
reaches through the Cartopian MCP toolset:

* ``resolve-config`` — the config-resolution surface (use-cartopian Stage 1).
* ``next-action`` — the orientation aggregator (start-session Stage 2).

Containment is signalled deterministically by the ``CARTOPIAN_PM_CONTAINED``
environment variable, which the contained PM launch profile sets on the
Cartopian MCP server process (``wrappers/etc/mcp-cartopian-only.json``) where
these handlers run in-process.

Controls confirm NF-004: ``pm_owns_product_branches = false`` is unaffected,
and the same true setting under an UNCONTAINED PM proceeds unchanged (legacy
path). The guard fires only for the contained + true combination.
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
CONTAINMENT_ENV = "CARTOPIAN_PM_CONTAINED"


def _run(command, project_path, *, home, contained):
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
    }
    if contained:
        env[CONTAINMENT_ENV] = "1"
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), command, str(project_path)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


_PROJECT_HEADER = (
    "[project]\n"
    'id = "demo"\n'
    'name = "Demo Project"\n'
    'protocol_version = "v0.2.0"\n'
)


class _Sandbox:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.project = self.root / "proj"
        self.home.mkdir()
        self.project.mkdir()

    def write_project(self, *, pm_owns=None, git_versioning=True):
        body = _PROJECT_HEADER
        if git_versioning:
            body += "\n[defaults]\ngit_versioning = true\n"
        if pm_owns is not None:
            body += f"\n[git]\npm_owns_product_branches = {str(bool(pm_owns)).lower()}\n"
        _write(self.project / "cartopian.toml", body)

    def write_global(self, *, pm_owns=None):
        body = ""
        if pm_owns is not None:
            body += f"[git]\npm_owns_product_branches = {str(bool(pm_owns)).lower()}\n"
        _write(self.home / ".cartopian" / "cartopian.toml", body)

    def cleanup(self):
        self._tmp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.cleanup()


def _assert_blocked(testcase, result):
    testcase.assertNotEqual(result.returncode, 0, msg=f"expected non-zero exit, got 0\nstdout={result.stdout!r}")
    testcase.assertIn("[guard]", result.stderr, msg=f"stderr missing [guard] line: {result.stderr!r}")
    testcase.assertIn(
        "pm_owns_product_branches",
        result.stderr,
        msg=f"guard line does not name the unsupported setting: {result.stderr!r}",
    )
    # Fail closed: no lifecycle record may be emitted on stdout.
    testcase.assertEqual(
        result.stdout.strip(),
        "",
        msg=f"a lifecycle record was emitted despite the block: {result.stdout!r}",
    )


def _assert_proceeds(testcase, result):
    testcase.assertEqual(result.returncode, 0, msg=f"expected exit 0, got {result.returncode}\nstderr={result.stderr!r}")
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    testcase.assertEqual(len(lines), 1, msg=f"expected one NDJSON record, got: {result.stdout!r}")
    json.loads(lines[0])  # well-formed record


class TestContainedPmOwnedGitBlocked(unittest.TestCase):
    """Green: the unsupported combination is refused fail-closed at lifecycle entry."""

    def test_resolve_config_blocks_project_setting(self):
        with _Sandbox() as sb:
            sb.write_project(pm_owns=True)
            result = _run("resolve-config", sb.project, home=sb.home, contained=True)
        _assert_blocked(self, result)

    def test_next_action_blocks_project_setting(self):
        with _Sandbox() as sb:
            sb.write_project(pm_owns=True)
            result = _run("next-action", sb.project, home=sb.home, contained=True)
        _assert_blocked(self, result)

    def test_resolve_config_blocks_global_setting(self):
        """Effective resolution: pm_owns set only in global config still blocks."""
        with _Sandbox() as sb:
            sb.write_project(pm_owns=None)  # project silent
            sb.write_global(pm_owns=True)
            result = _run("resolve-config", sb.project, home=sb.home, contained=True)
        _assert_blocked(self, result)

    def test_project_false_overrides_global_true(self):
        """Project pm_owns=false wins over global true → not the unsupported combo."""
        with _Sandbox() as sb:
            sb.write_project(pm_owns=False)
            sb.write_global(pm_owns=True)
            result = _run("resolve-config", sb.project, home=sb.home, contained=True)
        _assert_proceeds(self, result)


class TestControlsUnchanged(unittest.TestCase):
    """NF-004: the guard is targeted; existing behavior is untouched otherwise."""

    def test_pm_owns_false_contained_proceeds(self):
        with _Sandbox() as sb:
            sb.write_project(pm_owns=False)
            result = _run("resolve-config", sb.project, home=sb.home, contained=True)
        _assert_proceeds(self, result)

    def test_pm_owns_default_contained_proceeds(self):
        """Protocol default (setting absent) is false → proceeds even when contained."""
        with _Sandbox() as sb:
            sb.write_project(pm_owns=None)
            result = _run("resolve-config", sb.project, home=sb.home, contained=True)
        _assert_proceeds(self, result)

    def test_pm_owns_true_uncontained_proceeds(self):
        """Legacy/uncontained PM with pm_owns=true is unchanged (no containment signal)."""
        with _Sandbox() as sb:
            sb.write_project(pm_owns=True)
            result = _run("resolve-config", sb.project, home=sb.home, contained=False)
        _assert_proceeds(self, result)

    def test_next_action_pm_owns_true_uncontained_proceeds(self):
        with _Sandbox() as sb:
            sb.write_project(pm_owns=True)
            result = _run("next-action", sb.project, home=sb.home, contained=False)
        _assert_proceeds(self, result)


class TestLaunchSurfaceSignal(unittest.TestCase):
    """The contained PM launch profile must actually emit the containment signal.

    Without this the guard would never fire in production: the handlers run
    in-process inside the Cartopian MCP server, so the signal must reach that
    process. Lock it so it cannot silently drift away.
    """

    def test_mcp_config_sets_containment_env(self):
        cfg = json.loads((REPO_ROOT / "wrappers" / "etc" / "mcp-cartopian-only.json").read_text())
        env = cfg["mcpServers"]["cartopian"].get("env", {})
        self.assertEqual(
            env.get(CONTAINMENT_ENV),
            "1",
            msg="Cartopian MCP server config must set CARTOPIAN_PM_CONTAINED=1 so the in-process handlers see containment",
        )

    def test_pm_wrapper_exports_containment_env(self):
        src = (REPO_ROOT / "wrappers" / "bin" / "cartopian-claude-pm").read_text()
        self.assertIn(
            f"export {CONTAINMENT_ENV}=1",
            src,
            msg="contained PM wrapper must export CARTOPIAN_PM_CONTAINED=1",
        )


if __name__ == "__main__":
    unittest.main()
