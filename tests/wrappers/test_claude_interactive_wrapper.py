"""cartopian-claude interactive mode: the operator-attended contained launch."""
from __future__ import annotations

import os
import pty
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cli import claude_launch_settings
from tests.wrappers.test_claude_stop_hook_activation import (
    BASH,
    _fake_claude,
    _project,
    _settings_argument,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "wrappers" / "bin" / "cartopian-claude"

pytestmark = pytest.mark.skipif(
    BASH is None or os.name == "nt", reason="POSIX bash wrapper only"
)


def _env(tmp_path: Path, prompt: Path, report: Path, fake_bin: Path) -> dict[str, str]:
    path_parts = [str(fake_bin), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    timeout = shutil.which("timeout") or shutil.which("gtimeout")
    if timeout:
        path_parts.insert(1, str(Path(timeout).parent))
    home = tmp_path / "home"
    host_tmp = home / ".cartopian" / "claude-host-tmp"
    host_tmp.mkdir(parents=True, mode=0o700, exist_ok=True)
    host_tmp.chmod(0o700)
    return {
        "PATH": os.pathsep.join(path_parts),
        "HOME": str(home),
        "CARTOPIAN_TIMEOUT": "30s",
        "CARTOPIAN_LAUNCH_CWD": str(prompt.parent.parent),
        "CARTOPIAN_CLAUDE_EXECUTABLE": str((fake_bin / "claude").resolve()),
        claude_launch_settings.CLAUDE_HOST_TMPDIR_ENV: str(host_tmp),
        "TMPDIR": str(host_tmp),
        "CARTOPIAN_ROLE": "coder",
        "CARTOPIAN_HANDOFF_ID": "interactive-test-handoff",
        "CARTOPIAN_PYTHON": sys.executable,
        "CARTOPIAN_EXPECTED_REPORT_PATH": str(report),
        "CARTOPIAN_CLAUDE_INTERACTIVE": "true",
    }


def _run_on_terminal(env: dict[str, str], prompt: Path) -> subprocess.CompletedProcess:
    primary, secondary = pty.openpty()
    try:
        return subprocess.run(
            [BASH, str(WRAPPER), str(prompt)],
            stdin=secondary,
            stdout=secondary,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            timeout=60,
        )
    finally:
        os.close(secondary)
        os.close(primary)


def test_interactive_mode_keeps_containment_and_drops_headless_flags(tmp_path):
    project, prompt, report = _project(tmp_path, gated=True)
    fake_bin = tmp_path / "fake-bin"
    capture = tmp_path / "argv.txt"
    _fake_claude(fake_bin, capture)

    result = _run_on_terminal(_env(tmp_path, prompt, report, fake_bin), prompt)

    assert result.returncode == 0, result.stderr
    argv = capture.read_text(encoding="utf-8").splitlines()
    assert "-p" not in argv
    assert "--dangerously-skip-permissions" not in argv
    assert "--output-format" not in argv
    # Containment is identical to the unattended launch.
    assert "--strict-mcp-config" in argv
    assert argv[argv.index("--setting-sources") + 1] == ""
    settings = _settings_argument(argv)
    assert set(settings["hooks"]) == {"PreToolUse", "Stop"}
    assert settings["sandbox"]["allowUnsandboxedCommands"] is False
    assert settings["sandbox"]["failIfUnavailable"] is True
    assert str(prompt) in argv
    assert "running claude interactively" in result.stderr
    assert "timeout=operator-attended" in result.stderr
    # The wrapper still publishes the exit status the PM's waits observe.
    assert Path(str(report) + ".status").exists()


def test_interactive_mode_requires_a_terminal(tmp_path):
    project, prompt, report = _project(tmp_path, gated=True)
    fake_bin = tmp_path / "fake-bin"
    capture = tmp_path / "argv.txt"
    _fake_claude(fake_bin, capture)

    result = subprocess.run(
        [BASH, str(WRAPPER), str(prompt)],
        capture_output=True,
        text=True,
        env=_env(tmp_path, prompt, report, fake_bin),
        timeout=60,
    )

    assert result.returncode == 1
    assert "requires an operator terminal" in result.stderr
    assert not capture.exists()


def test_interactive_mode_requires_a_dispatch_bound_launch(tmp_path):
    project, prompt, report = _project(tmp_path, gated=True)
    fake_bin = tmp_path / "fake-bin"
    capture = tmp_path / "argv.txt"
    _fake_claude(fake_bin, capture)
    env = _env(tmp_path, prompt, report, fake_bin)
    for key in ("CARTOPIAN_ROLE", "CARTOPIAN_EXPECTED_REPORT_PATH"):
        env.pop(key)

    result = _run_on_terminal(env, prompt)

    assert result.returncode == 1
    assert "only through cartopian dispatch --interactive" in result.stderr
    assert not capture.exists()
