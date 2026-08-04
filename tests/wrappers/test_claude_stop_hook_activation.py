"""Integration coverage for dispatched Claude completion-hook activation.

No Claude API call is made. The real Cartopian wrappers launch a fake
``claude`` executable that records the exact argv it received.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from cli import claude_launch_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
POSIX_WRAPPER = REPO_ROOT / "wrappers" / "bin" / "cartopian-claude"
PS1_WRAPPER = REPO_ROOT / "wrappers" / "ps1" / "cartopian-claude.ps1"
PS1_HELPER = REPO_ROOT / "wrappers" / "ps1" / "CartopianStatus.ps1"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")


def _install_module():
    path = REPO_ROOT / "scripts" / "install.py"
    spec = importlib.util.spec_from_file_location("cartopian_install_activation", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _fake_claude(fake_bin: Path, capture: Path) -> None:
    fake_bin.mkdir(parents=True)
    executable = fake_bin / "claude"
    executable.write_text(
        "#!/bin/sh\n"
        f": > '{capture}'\n"
        f"for argument do printf '%s\\n' \"$argument\" >> '{capture}'; done\n"
        "exit 0\n",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)


def _project(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "project with spaces"
    (root / "prompts").mkdir(parents=True)
    (root / "reports").mkdir()
    prompt = root / "prompts" / "PROMPT-01-401.md"
    prompt.write_text("perform the dispatched handoff\n", encoding="utf-8")
    report = root / "reports" / "REPORT-01-401.md"
    return root, prompt, report


def _run(
    wrapper: Path,
    prompt: Path,
    report: Path | None,
    fake_bin: Path,
    capture: Path,
    *,
    bare: bool = False,
) -> list[str]:
    _fake_claude(fake_bin, capture)
    path_parts = [str(fake_bin), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    timeout = shutil.which("timeout") or shutil.which("gtimeout")
    if timeout:
        path_parts.insert(1, str(Path(timeout).parent))
    env = {
        "PATH": os.pathsep.join(path_parts),
        "HOME": str(prompt.parents[2] / "home with spaces"),
        "CARTOPIAN_TIMEOUT": "30s",
        "CARTOPIAN_LAUNCH_CWD": str(prompt.parent.parent),
    }
    if report is not None:
        env["CARTOPIAN_EXPECTED_REPORT_PATH"] = str(report)
    if bare:
        env["CARTOPIAN_CLAUDE_BARE"] = "true"
    result = subprocess.run(
        [BASH, str(wrapper), str(prompt)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert capture.exists(), f"fake Claude was not launched: {result.stderr}"
    return capture.read_text(encoding="utf-8").splitlines()


def _settings_argument(argv: list[str]) -> dict:
    assert argv.count("--settings") == 1, argv
    index = argv.index("--settings")
    return json.loads(argv[index + 1])


@pytest.mark.parametrize("mode", ["copy", "symlink"])
def test_installed_posix_wrapper_activates_process_scoped_stop_hook(tmp_path, mode):
    install_root = tmp_path / f"Cartopian {mode} install"
    _install_module().install(REPO_ROOT, install_root, mode=mode)
    project, prompt, report = _project(tmp_path)
    settings_path = project / ".claude" / "settings.json"
    settings_path.parent.mkdir()
    original = '{"theme":"dark","permissions":{"allow":["Read"]}}\n'
    settings_path.write_text(original, encoding="utf-8")
    local_path = project / ".claude" / "settings.local.json"
    local_original = '{"model":"local-choice"}\n'
    local_path.write_text(local_original, encoding="utf-8")
    user_path = tmp_path / "home with spaces" / ".claude" / "settings.json"
    user_path.parent.mkdir(parents=True)
    user_original = '{"verbose":true}\n'
    user_path.write_text(user_original, encoding="utf-8")

    argv = _run(
        install_root / "wrappers" / "bin" / "cartopian-claude",
        prompt,
        report,
        tmp_path / "fake bin",
        tmp_path / f"{mode} argv.txt",
    )
    settings = _settings_argument(argv)
    command = settings["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert "claude_stop_hook.py" in command
    assert str(install_root / "cli" / "claude_stop_hook.py") in command
    assert "PreToolUse" not in settings["hooks"]
    assert "--setting-sources" not in argv
    assert settings_path.read_text(encoding="utf-8") == original
    assert local_path.read_text(encoding="utf-8") == local_original
    assert user_path.read_text(encoding="utf-8") == user_original
    assert str(prompt.resolve()) in argv


def test_no_expected_report_means_no_per_launch_settings(tmp_path):
    _project_root, prompt, _report = _project(tmp_path)
    argv = _run(
        POSIX_WRAPPER,
        prompt,
        None,
        tmp_path / "fake bin",
        tmp_path / "no-report argv.txt",
    )
    assert "--settings" not in argv


def test_bare_handoff_keeps_explicit_completion_settings(tmp_path):
    _project_root, prompt, report = _project(tmp_path)
    argv = _run(
        POSIX_WRAPPER,
        prompt,
        report,
        tmp_path / "fake bin",
        tmp_path / "bare argv.txt",
        bare=True,
    )
    assert "--bare" in argv
    _settings_argument(argv)


def test_legacy_project_entry_is_reused_for_cross_scope_deduplication(tmp_path):
    project, prompt, report = _project(tmp_path)
    legacy_entry = {
        "hooks": [
            {
                "type": "command",
                "command": '"/Python With Spaces/python" "/old install/cli/claude_stop_hook.py"',
            }
        ]
    }
    settings_path = project / ".claude" / "settings.json"
    settings_path.parent.mkdir()
    project_settings = {
        "hooks": {
            "Stop": [legacy_entry, {"hooks": [{"type": "command", "command": "notify"}]}]
        }
    }
    original = json.dumps(project_settings, indent=2) + "\n"
    settings_path.write_text(original, encoding="utf-8")

    argv = _run(
        POSIX_WRAPPER,
        prompt,
        report,
        tmp_path / "fake bin",
        tmp_path / "legacy argv.txt",
    )
    launch_entry = _settings_argument(argv)["hooks"]["Stop"][0]
    assert launch_entry == legacy_entry
    combined = project_settings["hooks"]["Stop"] + [launch_entry]
    deduplicated = []
    for entry in combined:
        if entry not in deduplicated:
            deduplicated.append(entry)
    completion = [
        entry
        for entry in deduplicated
        if "claude_stop_hook.py" in json.dumps(entry)
    ]
    assert len(completion) == 1
    assert settings_path.read_text(encoding="utf-8") == original


def test_windows_settings_json_and_command_quoting_with_spaces(tmp_path):
    install_root = tmp_path / "Windows Cartopian Install"
    settings = claude_launch_settings.build_settings(install_root, windows=True)
    encoded = json.dumps(settings, separators=(",", ":"))
    decoded = json.loads(encoded)
    command = decoded["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert f'"{install_root / "cli" / "claude_stop_hook.py"}"' in command
    assert "claude_stop_hook.py" in command


def test_posix_generated_hook_command_runs_from_install_path_with_spaces(tmp_path):
    install_root = tmp_path / "Cartopian copy install with spaces"
    _install_module().install(REPO_ROOT, install_root, mode="copy")
    report = tmp_path / "report path with spaces" / "REPORT-01-401.md"
    report.parent.mkdir()
    settings = claude_launch_settings.build_settings(install_root, windows=False)
    command = settings["hooks"]["Stop"][0]["hooks"][0]["command"]
    payload = {
        "session_id": "quoted-command-session",
        "hook_event_name": "Stop",
        "stop_hook_active": False,
    }
    env = os.environ.copy()
    env["CARTOPIAN_EXPECTED_REPORT_PATH"] = str(report)
    result = subprocess.run(
        command,
        shell=True,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["decision"] == "block"


def test_powershell_wrapper_constructs_exact_settings_argument():
    wrapper = PS1_WRAPPER.read_text(encoding="utf-8")
    helper = PS1_HELPER.read_text(encoding="utf-8")
    assert "if ($env:CARTOPIAN_EXPECTED_REPORT_PATH)" in wrapper
    assert "'--platform', 'windows'" in wrapper
    assert "$Args += @('--settings', ($ClaudeLaunchSettings -join \"`n\"))" in wrapper
    assert "$Args += '--setting-sources'" not in wrapper
    assert "if ($Bare)" in wrapper and "$Args += '--bare'" in wrapper
    assert "$startInfo.ArgumentList.Add([string]$argument)" in helper
    assert "ConvertTo-CartopianWindowsArgument" in helper
    assert "Start-Process joins -ArgumentList values" in helper


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
