"""PM toolchain pinning / insulation.

The gap: the Cartopian CLI/MCP the PM runs on could resolve to the **editable
work-root source tree** — the same tree coders mutate during handoffs — so a
coder edit to ``cli/`` or ``mcp_server/`` landed directly on the PM's own
toolchain path, and "which code did the PM actually run?" (stale in-memory vs
on-disk new vs installed) was ambiguous.

The contract pinned here:

* **Launch-time audit (fail closed).** Every ``cartopian-*-pm`` containment
  wrapper resolves the toolchain its MCP config binds the PM to, prints its
  identity (command, root, ``VERSION``), and REFUSES to launch when that root
  is a git work tree (an editable checkout), unless the operator loudly opts
  in with ``CARTOPIAN_PM_TOOLCHAIN_DEV=1``. Helper:
  ``wrappers/bin/_cartopian-toolchain.sh :: cartopian_pm_toolchain_audit``.
* **Entrypoint insulation.** The installed ``bin/cartopian`` /
  ``bin/cartopian-mcp`` shims pin ``sys.path`` to their OWN install root, so
  coder edits to a work-root ``cli/`` tree never change the installed
  toolchain's behavior — demonstrated by mutating a work-root ``cli`` package
  mid-test and observing the installed entrypoint's output unchanged, even with
  the work root as cwd.

Stdlib-only; no live agent CLI is launched (a fake ``claude`` records argv).
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = REPO_ROOT / "wrappers" / "bin"
TOOLCHAIN_HELPER = BIN_DIR / "_cartopian-toolchain.sh"
CLI_SHIM = REPO_ROOT / "bin" / "cartopian"

PM_WRAPPERS = ["cartopian-claude-pm", "cartopian-codex-pm", "cartopian-gemini-pm"]

bash = shutil.which("bash")
pytestmark = pytest.mark.skipif(bash is None, reason="bash not available")


# --- fixtures ---------------------------------------------------------------


def _make_toolchain_root(root: Path, *, editable: bool, version: str | None = "v9.9.9") -> Path:
    """A fake toolchain root: <root>/bin/cartopian-mcp (+VERSION, +.git/)."""
    (root / "bin").mkdir(parents=True, exist_ok=True)
    cmd = root / "bin" / "cartopian-mcp"
    cmd.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    cmd.chmod(cmd.stat().st_mode | stat.S_IEXEC)
    if version is not None:
        (root / "VERSION").write_text(version + "\n", encoding="utf-8")
    if editable:
        (root / ".git").mkdir(exist_ok=True)
    return cmd


def _mcp_config(path: Path, command: str) -> Path:
    path.write_text(json.dumps({"mcpServers": {"cartopian": {"command": command}}}),
                    encoding="utf-8")
    return path


def _run_audit(mcp_config: Path, *, env_extra: dict | None = None):
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/tmp")}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [bash, "-c",
         f'source "{TOOLCHAIN_HELPER}"; cartopian_pm_toolchain_audit test-pm "{mcp_config}"'],
        capture_output=True, text=True, env=env, timeout=30,
    )


# --- the audit helper: fail-closed contract ---------------------------------


class TestToolchainAuditHelper:
    def test_pinned_install_root_passes_with_identity_banner(self, tmp_path):
        cmd = _make_toolchain_root(tmp_path / "inst", editable=False)
        cfg = _mcp_config(tmp_path / "mcp.json", str(cmd))
        proc = _run_audit(cfg)
        assert proc.returncode == 0, proc.stderr
        assert "PM toolchain = " in proc.stderr
        assert "version=v9.9.9" in proc.stderr, proc.stderr

    def test_editable_checkout_fails_closed(self, tmp_path):
        cmd = _make_toolchain_root(tmp_path / "dev", editable=True)
        cfg = _mcp_config(tmp_path / "mcp.json", str(cmd))
        proc = _run_audit(cfg)
        assert proc.returncode == 1, proc.stderr
        assert "EDITABLE checkout" in proc.stderr
        assert "pinned/installed toolchain" in proc.stderr

    def test_editable_checkout_dev_optin_proceeds_loudly(self, tmp_path):
        cmd = _make_toolchain_root(tmp_path / "dev", editable=True, version=None)
        cfg = _mcp_config(tmp_path / "mcp.json", str(cmd))
        proc = _run_audit(cfg, env_extra={"CARTOPIAN_PM_TOOLCHAIN_DEV": "1"})
        assert proc.returncode == 0, proc.stderr
        assert "WARNING" in proc.stderr
        assert "development ONLY" in proc.stderr
        assert "version=unknown" in proc.stderr

    def test_unresolvable_command_fails_closed(self, tmp_path):
        cfg = _mcp_config(tmp_path / "mcp.json", "")
        proc = _run_audit(cfg)
        assert proc.returncode == 1
        assert "cannot resolve" in proc.stderr

    def test_non_executable_command_fails_closed(self, tmp_path):
        missing = tmp_path / "nowhere" / "bin" / "cartopian-mcp"
        cfg = _mcp_config(tmp_path / "mcp.json", str(missing))
        proc = _run_audit(cfg)
        assert proc.returncode == 1
        assert "not executable" in proc.stderr


# --- every PM containment wrapper is wired through the audit ----------------


class TestPmWrappersWired:
    @pytest.mark.parametrize("wrapper", PM_WRAPPERS)
    def test_wrapper_sources_helper_and_calls_audit(self, wrapper):
        text = (BIN_DIR / wrapper).read_text(encoding="utf-8")
        assert "_cartopian-toolchain.sh" in text, f"{wrapper}: helper not sourced"
        assert "cartopian_pm_toolchain_audit" in text, f"{wrapper}: audit not called"

    def test_helper_exists_and_fails_closed_on_git_root(self):
        text = TOOLCHAIN_HELPER.read_text(encoding="utf-8")
        assert "/.git" in text.replace('"${root}/.git"', "/.git"), (
            "audit must detect a git work tree as the editable-checkout signal"
        )
        assert "CARTOPIAN_PM_TOOLCHAIN_DEV" in text
        assert "exit 1" in text


# --- wrapper-level behavior (cartopian-claude-pm end to end) ----------------


def _stage_claude_pm(tmp_path: Path, toolchain_cmd: str):
    """Copy the real cartopian-claude-pm + helper into an isolated layout whose
    etc/mcp-cartopian-only.json names `toolchain_cmd`, with a fake `claude`."""
    bin_dir = tmp_path / "wrap" / "bin"
    etc_dir = tmp_path / "wrap" / "etc"
    bin_dir.mkdir(parents=True)
    etc_dir.mkdir(parents=True)
    shutil.copy2(BIN_DIR / "cartopian-claude-pm", bin_dir / "cartopian-claude-pm")
    shutil.copy2(TOOLCHAIN_HELPER, bin_dir / "_cartopian-toolchain.sh")
    _mcp_config(etc_dir / "mcp-cartopian-only.json", toolchain_cmd)
    (etc_dir / "sandbox-pm-depth.json").write_text("{}", encoding="utf-8")

    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    argv_out = tmp_path / "claude-argv.txt"
    fake = fakebin / "claude"
    fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{argv_out}"\nexit 0\n',
                    encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return bin_dir / "cartopian-claude-pm", fakebin, argv_out


def _run_claude_pm(wrapper: Path, fakebin: Path):
    py_dir = Path(sys.executable).parent
    env = {
        "PATH": os.pathsep.join([str(fakebin), str(py_dir), "/usr/bin", "/bin",
                                 "/usr/sbin", "/sbin"]),
        "HOME": os.environ.get("HOME", "/tmp"),
    }
    return subprocess.run([bash, str(wrapper)], capture_output=True, text=True,
                          env=env, timeout=60)


class TestClaudePmLaunchAudit:
    def test_pinned_toolchain_launches_with_identity_banner(self, tmp_path):
        cmd = _make_toolchain_root(tmp_path / "inst", editable=False)
        wrapper, fakebin, argv_out = _stage_claude_pm(tmp_path, str(cmd))
        proc = _run_claude_pm(wrapper, fakebin)
        assert proc.returncode == 0, proc.stderr
        assert "PM toolchain = " in proc.stderr, proc.stderr
        assert "version=v9.9.9" in proc.stderr, proc.stderr
        assert argv_out.exists(), "claude was never launched"
        argv = argv_out.read_text(encoding="utf-8").splitlines()
        assert "--strict-mcp-config" in argv  # floor intact

    def test_editable_toolchain_refused_before_launch(self, tmp_path):
        cmd = _make_toolchain_root(tmp_path / "dev", editable=True)
        wrapper, fakebin, argv_out = _stage_claude_pm(tmp_path, str(cmd))
        proc = _run_claude_pm(wrapper, fakebin)
        assert proc.returncode == 1, proc.stderr
        assert "EDITABLE checkout" in proc.stderr
        assert not argv_out.exists(), (
            "claude must never launch on an editable-toolchain refusal"
        )


# --- entrypoint insulation evidence scenario ---------------------------------


_INSTALL_CLI = textwrap.dedent(
    """
    def main(argv):
        print("TOOLCHAIN=INSTALL")
        return 0
    """
)

_WORKROOT_CLI = textwrap.dedent(
    """
    def main(argv):
        print("TOOLCHAIN=WORKROOT")
        return 0
    """
)


def _stage_entrypoint(tmp_path: Path):
    """A fake pinned install root using the REAL bin/cartopian shim, plus a
    coder-editable work root carrying a different cli package."""
    inst = tmp_path / "inst"
    (inst / "bin").mkdir(parents=True)
    shutil.copy2(CLI_SHIM, inst / "bin" / "cartopian")
    (inst / "cli").mkdir()
    (inst / "cli" / "__init__.py").write_text("", encoding="utf-8")
    (inst / "cli" / "main.py").write_text(_INSTALL_CLI, encoding="utf-8")

    work = tmp_path / "work"
    (work / "cli").mkdir(parents=True)
    (work / "cli" / "__init__.py").write_text("", encoding="utf-8")
    (work / "cli" / "main.py").write_text(_WORKROOT_CLI, encoding="utf-8")
    return inst, work


def _run_entrypoint(inst: Path, cwd: Path):
    return subprocess.run(
        [sys.executable, str(inst / "bin" / "cartopian"), "noop"],
        capture_output=True, text=True, cwd=str(cwd), timeout=30,
        env={"HOME": os.environ.get("HOME", "/tmp"),
             "PATH": os.environ.get("PATH", "")},
    )


class TestEntrypointInsulation:
    def test_installed_shim_resolves_its_own_tree_even_from_work_root_cwd(self, tmp_path):
        inst, work = _stage_entrypoint(tmp_path)
        proc = _run_entrypoint(inst, cwd=work)
        assert proc.returncode == 0, proc.stderr
        assert "TOOLCHAIN=INSTALL" in proc.stdout
        assert "WORKROOT" not in proc.stdout

    def test_coder_edit_to_work_root_cli_does_not_change_installed_behavior(self, tmp_path):
        """Entrypoint insulation evidence gate: mutate the work-root cli/
        mid-'session' (between two invocations) and observe the installed
        toolchain's behavior unchanged."""
        inst, work = _stage_entrypoint(tmp_path)
        before = _run_entrypoint(inst, cwd=work)
        # The coder handoff lands an edit on the work-root cli/ tree.
        (work / "cli" / "main.py").write_text(
            _WORKROOT_CLI.replace("WORKROOT", "WORKROOT-EDITED"), encoding="utf-8"
        )
        after = _run_entrypoint(inst, cwd=work)
        assert before.stdout == after.stdout == "TOOLCHAIN=INSTALL\n", (
            f"installed toolchain behavior drifted: before={before.stdout!r} "
            f"after={after.stdout!r}"
        )

    def test_real_shim_inserts_its_own_root_first(self):
        """The shipped shim pins sys.path to its OWN root (bin/..), which is
        what makes the insulation hold; pin the mechanism, not just the
        observed behavior."""
        text = CLI_SHIM.read_text(encoding="utf-8")
        assert "sys.path.insert(0, repo_root)" in text
        assert 'os.path.dirname(os.path.abspath(__file__))' in text


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
