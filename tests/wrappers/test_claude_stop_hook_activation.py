"""Integration coverage for dispatched Claude process-scoped hook activation.

No Claude API call is made. The real POSIX wrapper launches a fake ``claude``
executable that records exact argv; native-Windows construction is covered by
the shared JSON helper and PowerShell wrapper contract.
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

GATED_ROLES = (
    "[roles.coder]\n"
    'description = "Implements tasks."\n'
    'grants = ["coder-like"]\n'
)
UNGATED_ROLES = (
    "[roles.coder]\n"
    'description = "Implements tasks."\n'
)


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


def _project(tmp_path: Path, *, gated: bool = True) -> tuple[Path, Path, Path]:
    root = tmp_path / "project with spaces"
    (root / "prompts").mkdir(parents=True)
    (root / "reports").mkdir()
    root.joinpath("cartopian.toml").write_text(
        "[project]\n"
        'id = "demo"\n'
        'name = "Demo"\n'
        'project_schema_version = "v0.9.0"\n\n'
        + (GATED_ROLES if gated else UNGATED_ROLES),
        encoding="utf-8",
    )
    prompt = root / "prompts" / "PROMPT-01-401.md"
    prompt.write_text("perform the dispatched handoff\n", encoding="utf-8")
    report = root / "reports" / "REPORT-01-401.md"
    return root, prompt, report


def _run_result(
    wrapper: Path,
    prompt: Path,
    report: Path | None,
    fake_bin: Path,
    capture: Path,
    *,
    bare: bool = False,
    dispatched: bool = True,
) -> subprocess.CompletedProcess[str]:
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
    if dispatched:
        env.update(
            {
                "CARTOPIAN_ROLE": "coder",
                "CARTOPIAN_HANDOFF_ID": "controlled-test-handoff",
                "CARTOPIAN_PYTHON": sys.executable,
            }
        )
    if report is not None:
        env["CARTOPIAN_EXPECTED_REPORT_PATH"] = str(report)
    if bare:
        env["CARTOPIAN_CLAUDE_BARE"] = "true"
    return subprocess.run(
        [BASH, str(wrapper), str(prompt)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def _run(*args, **kwargs) -> list[str]:
    capture = args[4]
    result = _run_result(*args, **kwargs)
    assert result.returncode == 0, result.stderr
    assert capture.exists(), f"fake Claude was not launched: {result.stderr}"
    return capture.read_text(encoding="utf-8").splitlines()


def _settings_argument(argv: list[str]) -> dict:
    assert argv.count("--settings") == 1, argv
    return json.loads(argv[argv.index("--settings") + 1])


@pytest.mark.parametrize("mode", ["copy", "symlink"])
def test_installed_posix_dispatch_receives_capability_and_completion_hooks(tmp_path, mode):
    install_root = tmp_path / f"Cartopian {mode} install"
    _install_module().install(REPO_ROOT, install_root, mode=mode)
    project, prompt, report = _project(tmp_path, gated=True)
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
    hooks = _settings_argument(argv)["hooks"]
    assert set(hooks) == {"PreToolUse", "Stop"}
    capability_command = hooks["PreToolUse"][0]["hooks"][0]["command"]
    completion_command = hooks["Stop"][0]["hooks"][0]["command"]
    assert str(install_root / "cli" / "claude_hook.py") in capability_command
    assert str(install_root / "cli" / "claude_stop_hook.py") in completion_command
    assert sys.executable in capability_command
    assert "--setting-sources" not in argv
    assert settings_path.read_text(encoding="utf-8") == original
    assert local_path.read_text(encoding="utf-8") == local_original
    assert user_path.read_text(encoding="utf-8") == user_original


def test_ungated_dispatched_project_loads_only_completion_hook(tmp_path):
    _root, prompt, report = _project(tmp_path, gated=False)
    argv = _run(POSIX_WRAPPER, prompt, report, tmp_path / "bin", tmp_path / "argv")
    assert set(_settings_argument(argv)["hooks"]) == {"Stop"}


def test_gated_dispatch_without_expected_report_loads_only_capability_hook(tmp_path):
    _root, prompt, _report = _project(tmp_path, gated=True)
    argv = _run(POSIX_WRAPPER, prompt, None, tmp_path / "bin", tmp_path / "argv")
    assert set(_settings_argument(argv)["hooks"]) == {"PreToolUse"}


def test_no_dispatch_boundary_and_no_expected_report_adds_no_settings(tmp_path):
    _root, prompt, _report = _project(tmp_path)
    argv = _run(
        POSIX_WRAPPER,
        prompt,
        None,
        tmp_path / "bin",
        tmp_path / "argv",
        dispatched=False,
    )
    assert "--settings" not in argv


def test_bare_gated_handoff_keeps_both_explicit_hooks(tmp_path):
    _root, prompt, report = _project(tmp_path)
    argv = _run(
        POSIX_WRAPPER,
        prompt,
        report,
        tmp_path / "bin",
        tmp_path / "argv",
        bare=True,
    )
    assert "--bare" in argv
    assert set(_settings_argument(argv)["hooks"]) == {"PreToolUse", "Stop"}


def test_compatible_project_pretooluse_entry_is_reused_and_deduplicates(tmp_path):
    project, prompt, report = _project(tmp_path)
    expected = {
        "matcher": claude_launch_settings.CAPABILITY_MATCHER,
        "hooks": [{
            "type": "command",
            "command": f'"{sys.executable}" "{REPO_ROOT / "cli" / "claude_hook.py"}"',
        }],
    }
    settings_path = project / ".claude" / "settings.json"
    settings_path.parent.mkdir()
    project_settings = {"hooks": {"PreToolUse": [expected]}}
    original = json.dumps(project_settings, indent=2) + "\n"
    settings_path.write_text(original, encoding="utf-8")

    argv = _run(POSIX_WRAPPER, prompt, report, tmp_path / "bin", tmp_path / "argv")
    launch_entry = _settings_argument(argv)["hooks"]["PreToolUse"][0]
    assert launch_entry == expected
    assert [expected, launch_entry].count(expected) == 2
    assert len({json.dumps(entry, sort_keys=True) for entry in [expected, launch_entry]}) == 1
    assert settings_path.read_text(encoding="utf-8") == original


def test_stale_project_pretooluse_entry_refuses_instead_of_running_twice(tmp_path):
    project, prompt, report = _project(tmp_path)
    settings_path = project / ".claude" / "settings.json"
    settings_path.parent.mkdir()
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": claude_launch_settings.CAPABILITY_MATCHER,
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": '"/old python" "/old/cli/claude_hook.py"',
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    capture = tmp_path / "argv"
    result = _run_result(POSIX_WRAPPER, prompt, report, tmp_path / "bin", capture)
    assert result.returncode == 1
    assert not capture.exists()
    assert "incompatible legacy Cartopian PreToolUse" in result.stderr
    assert "--claude-hook" in result.stderr


def test_windows_settings_json_and_commands_quote_space_paths(tmp_path):
    install_root = tmp_path / "Windows Cartopian Install"
    interpreter = tmp_path / "Python Runtime" / "python.exe"
    project, _prompt, _report = _project(tmp_path)
    settings = claude_launch_settings.build_settings(
        install_root,
        windows=True,
        project_dir=project,
        include_capability=True,
        include_completion=True,
        interpreter=interpreter,
    )
    decoded = json.loads(json.dumps(settings, separators=(",", ":")))
    for event, script in (("PreToolUse", "claude_hook.py"), ("Stop", "claude_stop_hook.py")):
        command = decoded["hooks"][event][0]["hooks"][0]["command"]
        assert f'"{interpreter}"' in command
        assert f'"{install_root / "cli" / script}"' in command

    posix = claude_launch_settings.hook_command(
        install_root,
        "claude_hook.py",
        windows=False,
        interpreter=interpreter,
    )
    assert str(interpreter) in posix
    assert "'" in posix


def test_posix_generated_stop_command_runs_from_install_path_with_spaces(tmp_path):
    install_root = tmp_path / "Cartopian copy install with spaces"
    _install_module().install(REPO_ROOT, install_root, mode="copy")
    report = tmp_path / "report path with spaces" / "REPORT-01-401.md"
    report.parent.mkdir()
    settings = claude_launch_settings.build_settings(
        install_root, windows=False, include_completion=True
    )
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


def test_powershell_wrapper_constructs_native_windows_settings_argument():
    wrapper = PS1_WRAPPER.read_text(encoding="utf-8")
    helper = PS1_HELPER.read_text(encoding="utf-8")
    assert "if ($env:CARTOPIAN_ROLE -or $env:CARTOPIAN_EXPECTED_REPORT_PATH)" in wrapper
    assert "if ($env:CARTOPIAN_ROLE) { $SettingsHelperArgs += '--capability' }" in wrapper
    assert "if ($env:CARTOPIAN_EXPECTED_REPORT_PATH) { $SettingsHelperArgs += '--completion' }" in wrapper
    assert "if ($env:CARTOPIAN_PYTHON)" in wrapper
    assert "'--platform', 'windows'" in wrapper
    assert "$Args += @('--settings', $ClaudeLaunchSettingsJson)" in wrapper
    assert "$Args += '--setting-sources'" not in wrapper
    assert "if ($Bare)" in wrapper and "$Args += '--bare'" in wrapper
    assert "$startInfo.ArgumentList.Add([string]$argument)" in helper
    assert "ConvertTo-CartopianWindowsArgument" in helper


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
