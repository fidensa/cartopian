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
from unittest import mock

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


from tests._install_fixture import install_copy_fixture


def _fake_claude(
    fake_bin: Path, capture: Path, *, version: str = "2.1.278 (Claude Code)"
) -> None:
    fake_bin.mkdir(parents=True, exist_ok=True)
    executable = fake_bin / "claude"
    executable.write_text(
        "#!/bin/sh\n"
        f"if [ \"${{1:-}}\" = \"--version\" ]; then printf '%s\\n' '{version}'; exit 0; fi\n"
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
    claude_version: str = "2.1.278 (Claude Code)",
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    _fake_claude(fake_bin, capture, version=claude_version)
    path_parts = [str(fake_bin), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    timeout = shutil.which("timeout") or shutil.which("gtimeout")
    if timeout:
        path_parts.insert(1, str(Path(timeout).parent))
    home = prompt.parents[2] / "home with spaces"
    host_tmp = home / ".cartopian" / "claude-host-tmp"
    host_tmp.mkdir(parents=True, mode=0o700, exist_ok=True)
    host_tmp.chmod(0o700)
    env = {
        "PATH": os.pathsep.join(path_parts),
        "HOME": str(home),
        "CARTOPIAN_TIMEOUT": "30s",
        "CARTOPIAN_LAUNCH_CWD": str(prompt.parent.parent),
        "CARTOPIAN_CLAUDE_EXECUTABLE": str((fake_bin / "claude").resolve()),
        claude_launch_settings.CLAUDE_HOST_TMPDIR_ENV: str(host_tmp),
        "TMPDIR": str(host_tmp),
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
    if extra_env:
        env.update(extra_env)
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


def test_posix_wrapper_privileged_startup_ignores_bash_env(tmp_path):
    startup = tmp_path / "bash-env.sh"
    marker = tmp_path / "startup-ran"
    startup.write_text('printf injected > "$MARKER"\n', encoding="utf-8")
    env = {
        "PATH": "/usr/bin:/bin",
        "BASH_ENV": str(startup),
        "MARKER": str(marker),
    }

    result = subprocess.run(
        [str(REPO_ROOT / "wrappers" / "bin" / "cartopian-claude")],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert result.returncode != 0
    assert not marker.exists()


def test_activated_stop_state_refuses_symlink_relocation(tmp_path):
    project, _prompt, report = _project(tmp_path, gated=True)
    home = tmp_path / "home"
    config_root = home / ".cartopian"
    config_root.mkdir(parents=True)
    outside = tmp_path / "agent-writable-state"
    outside.mkdir()
    try:
        config_root.joinpath("stop-state").symlink_to(
            outside, target_is_directory=True
        )
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks unavailable")

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="Stop state must be a direct directory",
    ):
        claude_launch_settings.build_settings(
            REPO_ROOT,
            windows=False,
            project_dir=project,
            include_capability=True,
            include_completion=True,
            environ={
                "CARTOPIAN_ROLE": "coder",
                "CARTOPIAN_EXPECTED_REPORT_PATH": str(report),
                "HOME": str(home),
            },
        )


def test_claude_host_temp_refuses_symlink_relocation(tmp_path):
    home = tmp_path / "home"
    config_root = home / ".cartopian"
    config_root.mkdir(parents=True)
    outside = tmp_path / "agent-writable-temp"
    outside.mkdir()
    host_tmp = config_root / "claude-host-tmp"
    try:
        host_tmp.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks unavailable")
    env = {
        "HOME": str(home),
        claude_launch_settings.CLAUDE_HOST_TMPDIR_ENV: str(host_tmp),
        "TMPDIR": str(host_tmp),
    }

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="host temp must be a direct directory",
    ):
        claude_launch_settings.prepare_claude_host_tmpdir(
            env,
            windows=False,
            require_binding=True,
        )


def test_installed_posix_dispatch_receives_capability_and_completion_hooks(tmp_path):
    install_root = tmp_path / "Cartopian copy install"
    install_copy_fixture(REPO_ROOT, install_root)
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
        tmp_path / "copy argv.txt",
    )
    launch_settings = _settings_argument(argv)
    hooks = launch_settings["hooks"]
    assert set(hooks) == {"PreToolUse", "Stop"}
    capability_handler = hooks["PreToolUse"][0]["hooks"][0]
    completion_handler = hooks["Stop"][0]["hooks"][0]
    assert capability_handler["command"] == sys.executable
    assert completion_handler["command"] == sys.executable
    capability_argv = capability_handler["args"]
    completion_argv = completion_handler["args"]
    assert capability_argv[:2] == ["-I", "-S"]
    assert completion_argv[:2] == ["-I", "-S"]
    assert capability_argv[2] == str(install_root / "cli" / "claude_hook.py")
    assert completion_argv[2] == str(install_root / "cli" / "claude_stop_hook.py")
    assert "--role=coder" in capability_argv
    assert capability_argv[capability_argv.index("--project-root") + 1] == str(
        project.resolve()
    )
    assert capability_argv.count("--work-root") == 0
    assert capability_argv.count("--settings-path") == 1
    assert capability_argv[capability_argv.index("--settings-path") + 1] == str(
        (tmp_path / "home with spaces" / ".claude.json").resolve()
    )
    assert completion_argv[completion_argv.index("--expected-report") + 1] == str(
        report
    )
    assert completion_argv[completion_argv.index("--max-blocks") + 1] == "3"
    assert completion_argv[completion_argv.index("--state-dir") + 1] == str(
        (tmp_path / "home with spaces" / ".cartopian" / "stop-state").resolve()
    )
    assert launch_settings["disableAllHooks"] is False
    assert launch_settings["env"]["TMPDIR"] == str(
        tmp_path / "home with spaces" / ".cartopian" / "claude-host-tmp"
    )
    assert set(claude_launch_settings._UNCONTAINED_SESSION_TOOLS) <= set(
        launch_settings["permissions"]["deny"]
    )
    sandbox = launch_settings["sandbox"]
    assert sandbox["enabled"] is True
    assert sandbox["failIfUnavailable"] is True
    assert sandbox["allowUnsandboxedCommands"] is False
    assert sandbox["allowAppleEvents"] is False
    assert sandbox["excludedCommands"] == []
    assert sandbox["ignoreViolations"] == {}
    assert sandbox["network"]["allowMachLookup"] == []
    assert sandbox["filesystem"]["disabled"] is False
    deny_write = sandbox["filesystem"]["denyWrite"]
    assert deny_write[0] == str(project.resolve())
    assert str(install_root.resolve()) in deny_write
    assert str((tmp_path / "home with spaces" / ".cartopian").resolve()) in deny_write
    assert str((tmp_path / "home with spaces" / ".claude.json").resolve()) in deny_write
    assert str(user_path.resolve()) not in deny_write
    assert str(settings_path.resolve()) not in deny_write
    assert str(local_path.resolve()) not in deny_write
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in argv
    unavailable = argv[argv.index("--disallowedTools") + 1].split(",")
    assert set(claude_launch_settings._UNCONTAINED_SESSION_TOOLS) <= set(unavailable)
    assert settings_path.read_text(encoding="utf-8") == original
    assert local_path.read_text(encoding="utf-8") == local_original
    assert user_path.read_text(encoding="utf-8") == user_original


def test_ungated_dispatched_project_loads_only_completion_hook(tmp_path):
    _root, prompt, report = _project(tmp_path, gated=False)
    argv = _run(POSIX_WRAPPER, prompt, report, tmp_path / "bin", tmp_path / "argv")
    settings = _settings_argument(argv)
    assert set(settings["hooks"]) == {"Stop"}
    assert "sandbox" not in settings
    assert "--setting-sources" not in argv
    assert "--strict-mcp-config" not in argv
    assert "--disallowedTools" not in argv


def test_gated_dispatch_without_expected_report_loads_only_capability_hook(tmp_path):
    root, prompt, _report = _project(tmp_path, gated=True)
    argv = _run(POSIX_WRAPPER, prompt, None, tmp_path / "bin", tmp_path / "argv")
    settings = _settings_argument(argv)
    assert set(settings["hooks"]) == {"PreToolUse"}
    assert str(root.resolve()) in settings["sandbox"]["filesystem"]["denyWrite"]


def test_activated_wrapper_refuses_missing_host_temp_binding_before_claude(tmp_path):
    _root, prompt, report = _project(tmp_path, gated=True)
    capture = tmp_path / "argv"

    result = _run_result(
        POSIX_WRAPPER,
        prompt,
        report,
        tmp_path / "bin",
        capture,
        extra_env={
            claude_launch_settings.CLAUDE_HOST_TMPDIR_ENV: "",
            "TMPDIR": "/tmp",
        },
    )

    assert result.returncode != 0
    assert "dispatch-bound protected host temp" in result.stderr
    assert not capture.exists()


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


@pytest.mark.parametrize(
    ("gated", "version", "minimum"),
    (
        (False, "2.1.138 (Claude Code)", "2.1.139"),
        (True, "2.1.277 (Claude Code)", "2.1.278"),
    ),
)
def test_wrapper_refuses_unsupported_claude_version_before_launch(
    tmp_path, gated, version, minimum
):
    _root, prompt, report = _project(tmp_path, gated=gated)
    capture = tmp_path / "argv"
    result = _run_result(
        POSIX_WRAPPER,
        prompt,
        report,
        tmp_path / "bin",
        capture,
        claude_version=version,
    )

    assert result.returncode == 1
    assert not capture.exists()
    assert f"upgrade to {minimum}+" in result.stderr


def test_claude_version_parser_and_feature_floors():
    assert claude_launch_settings.parse_claude_version(
        "2.1.278 (Claude Code)"
    ) == (2, 1, 278)
    assert claude_launch_settings.parse_claude_version("not a version") is None
    assert claude_launch_settings.require_claude_version(
        "2.1.139", activated=False
    ) == (2, 1, 139)
    assert claude_launch_settings.require_claude_version(
        "2.1.278", activated=True
    ) == (2, 1, 278)
    with pytest.raises(claude_launch_settings.SettingsError, match="2.1.139"):
        claude_launch_settings.require_claude_version(
            "2.1.138", activated=False
        )
    with pytest.raises(claude_launch_settings.SettingsError, match="2.1.278"):
        claude_launch_settings.require_claude_version(
            "2.1.277", activated=True
        )


def test_native_windows_version_probe_routes_npm_cmd_shim_through_comspec():
    shim = r"C:\Program Files\Claude & Tools\claude.cmd"
    comspec = r"C:\Windows\System32\cmd.exe"
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="2.1.278 (Claude Code)\n", stderr=""
    )

    with (
        mock.patch(
            "cli.claude_launch_settings.shutil.which", return_value=shim
        ),
        mock.patch(
            "cli.claude_launch_settings.subprocess.run",
            return_value=completed,
        ) as run,
    ):
        version, raw = claude_launch_settings.probe_claude_version(
            windows=True,
            environ={"COMSPEC": comspec, "PATH": r"C:\Program Files\Claude & Tools"},
        )

    assert version == (2, 1, 278)
    assert raw == "2.1.278 (Claude Code)"
    command = run.call_args.args[0]
    assert command == [
        comspec,
        "/d",
        "/v:off",
        "/s",
        "/c",
        '""%CARTOPIAN_CLAUDE_VERSION_EXECUTABLE%" --version"',
    ]
    assert "call" not in command[-1].lower()
    assert run.call_args.kwargs["env"][
        "CARTOPIAN_CLAUDE_VERSION_EXECUTABLE"
    ] == shim


def test_posix_version_probe_receives_explicit_sanitized_environment():
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="2.1.278 (Claude Code)\n", stderr=""
    )
    source = {
        "PATH": "/usr/bin:/bin",
        "SAFE": "kept",
        "NODE_OPTIONS": "--require=/tmp/pwn.js",
        "LD_PRELOAD": "/tmp/pwn.so",
        "BUN_OPTIONS": "--preload=/tmp/pwn.ts",
        "PYTHONPATH": "/tmp/pwn-python",
    }

    with mock.patch(
        "cli.claude_launch_settings.resolve_claude_executable",
        return_value="/usr/bin/claude",
    ), mock.patch(
        "cli.claude_launch_settings.subprocess.run",
        return_value=completed,
    ) as run:
        version, _raw = claude_launch_settings.probe_claude_version(
            windows=False,
            environ=source,
        )

    assert version == (2, 1, 278)
    assert run.call_args.kwargs["env"] == {
        "PATH": "/usr/bin:/bin",
        "SAFE": "kept",
    }


def test_bare_gated_handoff_refuses_before_claude(tmp_path):
    _root, prompt, report = _project(tmp_path)
    capture = tmp_path / "argv"
    result = _run_result(
        POSIX_WRAPPER,
        prompt,
        report,
        tmp_path / "bin",
        capture,
        bare=True,
    )
    assert result.returncode == 1
    assert not capture.exists()
    assert "CARTOPIAN_CLAUDE_BARE" in result.stderr


def test_bare_launch_without_cartopian_hooks_remains_available(tmp_path):
    _root, prompt, _report = _project(tmp_path)
    argv = _run(
        POSIX_WRAPPER,
        prompt,
        None,
        tmp_path / "bin",
        tmp_path / "argv",
        bare=True,
        dispatched=False,
    )
    assert "--bare" in argv


@pytest.mark.parametrize("unsafe_key", ("CLAUDE_CODE_SIMPLE", "CLAUDE_CODE_SAFE_MODE"))
def test_wrapper_refuses_inherited_hook_suppression_before_claude(
    tmp_path, unsafe_key
):
    _root, prompt, report = _project(tmp_path)
    capture = tmp_path / "argv"
    result = _run_result(
        POSIX_WRAPPER,
        prompt,
        report,
        tmp_path / "bin",
        capture,
        extra_env={unsafe_key: "1"},
    )
    assert result.returncode == 1
    assert not capture.exists()
    assert unsafe_key in result.stderr


def test_compatible_normal_project_entry_is_excluded_from_activated_launch(tmp_path):
    project, prompt, report = _project(tmp_path)
    fake_bin = tmp_path / "bin"
    _fake_claude(fake_bin, tmp_path / "unused-preflight-capture")
    path_parts = [str(fake_bin), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    timeout = shutil.which("timeout") or shutil.which("gtimeout")
    if timeout:
        path_parts.insert(1, str(Path(timeout).parent))
    expected = claude_launch_settings.build_settings(
        REPO_ROOT,
        windows=False,
        project_dir=project,
        include_capability=True,
        environ={
            "CARTOPIAN_ROLE": "coder",
            "CARTOPIAN_CLAUDE_EXECUTABLE": str(fake_bin / "claude"),
            "HOME": str(tmp_path / "home with spaces"),
            "PATH": os.pathsep.join(path_parts),
        },
    )["hooks"]["PreToolUse"][0]
    settings_path = project / ".claude" / "settings.json"
    settings_path.parent.mkdir()
    project_settings = {"hooks": {"PreToolUse": [expected]}}
    original = json.dumps(project_settings, indent=2) + "\n"
    settings_path.write_text(original, encoding="utf-8")

    argv = _run(POSIX_WRAPPER, prompt, report, fake_bin, tmp_path / "argv")
    launch_entry = _settings_argument(argv)["hooks"]["PreToolUse"][0]
    assert launch_entry == expected
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert settings_path.read_text(encoding="utf-8") == original


def test_stale_normal_project_pretooluse_entry_is_excluded(tmp_path):
    project, prompt, report = _project(tmp_path)
    settings_path = project / ".claude" / "settings.json"
    settings_path.parent.mkdir()
    original = (
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
            },
            indent=2,
        )
        + "\n"
    )
    settings_path.write_text(original, encoding="utf-8")
    capture = tmp_path / "argv"
    result = _run_result(POSIX_WRAPPER, prompt, report, tmp_path / "bin", capture)
    assert result.returncode == 0, result.stderr
    argv = capture.read_text(encoding="utf-8").splitlines()
    launch_handler = _settings_argument(argv)["hooks"]["PreToolUse"][0]["hooks"][0]
    assert launch_handler["command"] == sys.executable
    assert "/old/cli/claude_hook.py" not in json.dumps(launch_handler)
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert settings_path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("script", ("claude_hook.py", "claude_stop_hook.py"))
def test_windows_hook_handler_uses_shell_free_exec_form_for_metacharacter_paths(
    tmp_path, script
):
    install_root = tmp_path / "Windows $Cartopian & Install"
    interpreter = tmp_path / "Python $Runtime & Tools" / "python.exe"
    handler = claude_launch_settings.hook_handler(
        install_root,
        script,
        interpreter=interpreter,
        arguments=("--project-root", r"C:\Project $Tree & Files"),
    )
    decoded = json.loads(json.dumps(handler, separators=(",", ":")))

    assert decoded["command"] == str(interpreter)
    assert decoded["args"][:2] == ["-I", "-S"]
    assert decoded["args"][2] == str(install_root / "cli" / script)
    assert decoded["args"][3:] == [
        "--project-root",
        r"C:\Project $Tree & Files",
    ]
    assert decoded["type"] == "command"


def test_activated_native_windows_settings_refuse_unattested_host(tmp_path):
    project, _prompt, _report = _project(tmp_path)

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="activated Claude containment is refused on native Windows",
    ):
        claude_launch_settings.build_settings(
            REPO_ROOT,
            windows=True,
            project_dir=project,
            include_capability=True,
            include_completion=True,
            interpreter=tmp_path / "Python Runtime" / "python.exe",
        )


def test_posix_sandbox_grants_only_authorized_work_roots(tmp_path):
    project, _prompt, _report = _project(tmp_path, gated=True)
    work_root = tmp_path / "product tree"
    foreign_project = tmp_path / "foreign project"
    home = tmp_path / "home"
    work_root.mkdir()
    foreign_project.mkdir()
    foreign_project.joinpath("cartopian.toml").write_text(
        "[project]\n"
        'id = "foreign"\n'
        'name = "Foreign"\n'
        'project_schema_version = "v0.13.0"\n\n'
        + UNGATED_ROLES,
        encoding="utf-8",
    )
    registry = home / ".cartopian" / "projects.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps(
            [
                {"id": "demo", "path": str(project)},
                {"id": "foreign", "path": str(foreign_project)},
            ]
        ),
        encoding="utf-8",
    )
    project.joinpath("cartopian.toml").write_text(
        "[project]\n"
        'id = "demo"\n'
        'name = "Demo"\n'
        'project_schema_version = "v0.13.0"\n'
        'work_roots = ["product"]\n\n'
        "[roles.coder]\n"
        'description = "Implements tasks."\n'
        'grants = ["coder-like"]\n\n'
        "[roles.reviewer]\n"
        'description = "Reviews tasks."\n'
        'grants = ["reviewer-like"]\n',
        encoding="utf-8",
    )
    project.joinpath("cartopian.local.toml").write_text(
        f'[work_roots]\nproduct = "{work_root}"\n', encoding="utf-8"
    )

    coder = claude_launch_settings.build_settings(
        REPO_ROOT,
        windows=False,
        project_dir=project,
        include_capability=True,
        environ={"CARTOPIAN_ROLE": "coder", "HOME": str(home)},
    )["sandbox"]["filesystem"]
    assert str(project.resolve()) in coder["denyWrite"]
    assert str(foreign_project.resolve()) in coder["denyWrite"]
    assert str(work_root.resolve()) not in coder["denyWrite"]
    assert coder["allowWrite"] == [str(work_root.resolve())]

    reviewer = claude_launch_settings.build_settings(
        REPO_ROOT,
        windows=False,
        project_dir=project,
        include_capability=True,
        environ={"CARTOPIAN_ROLE": "reviewer", "HOME": str(home)},
    )["sandbox"]["filesystem"]
    assert str(project.resolve()) in reviewer["denyWrite"]
    assert str(foreign_project.resolve()) in reviewer["denyWrite"]
    assert str(work_root.resolve()) in reviewer["denyWrite"]
    assert "allowWrite" not in reviewer


def test_posix_settings_reject_cross_boundary_work_root_hardlink(tmp_path):
    project, _prompt, _report = _project(tmp_path, gated=True)
    work_root = tmp_path / "product"
    outside = tmp_path / "outside"
    work_root.mkdir()
    outside.mkdir()
    source = outside / "protected.txt"
    source.write_text("protected\n", encoding="utf-8")
    try:
        os.link(source, work_root / "alias.txt")
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    project.joinpath("cartopian.toml").write_text(
        "[project]\n"
        'id = "demo"\n'
        'name = "Demo"\n'
        'project_schema_version = "v0.13.0"\n'
        'work_roots = ["product"]\n\n'
        + GATED_ROLES,
        encoding="utf-8",
    )
    project.joinpath("cartopian.local.toml").write_text(
        f'[work_roots]\nproduct = "{work_root}"\n', encoding="utf-8"
    )

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="hard-link names outside the authorized root set",
    ):
        claude_launch_settings.build_settings(
            REPO_ROOT,
            windows=False,
            project_dir=project,
            include_capability=True,
            environ={"CARTOPIAN_ROLE": "coder"},
        )


def test_settings_refuse_posix_work_root_path_list_separator(tmp_path):
    project, _prompt, _report = _project(tmp_path, gated=True)
    work_root = tmp_path / "product:alias"
    work_root.mkdir()
    project.joinpath("cartopian.toml").write_text(
        "[project]\n"
        'id = "demo"\n'
        'name = "Demo"\n'
        'project_schema_version = "v0.13.0"\n'
        'work_roots = ["product"]\n\n'
        + GATED_ROLES,
        encoding="utf-8",
    )
    project.joinpath("cartopian.local.toml").write_text(
        f'[work_roots]\nproduct = "{work_root}"\n', encoding="utf-8"
    )

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="path-list separator ':'",
    ):
        claude_launch_settings.build_settings(
            REPO_ROOT,
            windows=False,
            project_dir=project,
            include_capability=True,
            environ={"CARTOPIAN_ROLE": "coder"},
        )


def test_windows_work_root_transport_refuses_semicolon_but_allows_drive_colon():
    claude_launch_settings.refuse_work_root_transport_paths(
        [r"C:\product"], windows=True
    )
    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="path-list separator ';'",
    ):
        claude_launch_settings.refuse_work_root_transport_paths(
            [r"C:\product;alias"], windows=True
        )


def test_work_root_transport_refuses_line_or_control_delimiters():
    for unsafe in ("/tmp/allowed\n/suffix", "/tmp/allowed\r/suffix", "/tmp/a\tb"):
        with pytest.raises(
            claude_launch_settings.SettingsError,
            match="control character",
        ):
            claude_launch_settings.refuse_work_root_transport_paths(
                [unsafe], windows=False
            )


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_linked_project_git_metadata_is_protected_but_product_git_is_writable(
    tmp_path,
):
    main = tmp_path / "governance-main"
    project = tmp_path / "governance-linked"
    product = tmp_path / "product-repository"
    main.mkdir()
    product.mkdir()
    subprocess.run(["git", "init", "-q", str(main)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(main),
            "-c",
            "user.name=Cartopian Test",
            "-c",
            "user.email=cartopian@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(main), "worktree", "add", "-qb", "linked", str(project)],
        check=True,
    )
    subprocess.run(["git", "init", "-q", str(product)], check=True)
    project.joinpath("cartopian.toml").write_text(
        "[project]\n"
        'id = "linked-demo"\n'
        'name = "Linked Demo"\n'
        'project_schema_version = "v0.13.0"\n'
        'work_roots = ["product"]\n\n'
        + GATED_ROLES,
        encoding="utf-8",
    )
    project.joinpath("cartopian.local.toml").write_text(
        f'[work_roots]\nproduct = "{product}"\n', encoding="utf-8"
    )

    settings = claude_launch_settings.build_settings(
        REPO_ROOT,
        windows=False,
        project_dir=project,
        include_capability=True,
        environ={
            "CARTOPIAN_ROLE": "coder",
            "HOME": str(tmp_path / "home"),
        },
    )
    git_roots = set(claude_launch_settings.project_git_protected_roots(project))
    filesystem = settings["sandbox"]["filesystem"]
    protected_args = settings["hooks"]["PreToolUse"][0]["hooks"][0]["args"]
    captured_index = protected_args.index("--work-root")
    captured_info = product.lstat()
    assert protected_args[captured_index + 1 : captured_index + 5] == [
        "product",
        str(product.resolve()),
        str(captured_info.st_dev),
        str(captured_info.st_ino),
    ]
    hook_protected = {
        protected_args[index + 1]
        for index, argument in enumerate(protected_args)
        if argument == "--protected-root"
    }

    assert git_roots == {
        os.path.abspath(project / ".git"),
        os.path.realpath(main / ".git"),
        os.path.realpath(main / ".git" / "worktrees" / project.name),
    }
    assert git_roots <= set(filesystem["denyWrite"])
    assert git_roots <= hook_protected
    assert filesystem["allowWrite"] == [str(product.resolve())]
    assert os.path.realpath(product / ".git") not in filesystem["denyWrite"]


def test_governed_git_metadata_resolution_fails_closed(tmp_path):
    project = tmp_path / "project"
    project.joinpath(".git").mkdir(parents=True)
    failed = subprocess.CompletedProcess(
        args=["git", "rev-parse"],
        returncode=128,
        stdout="",
        stderr="metadata unavailable",
    )

    with (
        mock.patch(
            "cli.claude_launch_settings.subprocess.run", return_value=failed
        ),
        pytest.raises(
            claude_launch_settings.SettingsError,
            match="cannot resolve governed-project Git metadata",
        ),
    ):
        claude_launch_settings.project_git_protected_roots(project)


def test_git_metadata_probe_receives_explicit_sanitized_environment(tmp_path):
    completed = subprocess.CompletedProcess(
        args=["git", "rev-parse"],
        returncode=0,
        stdout=str(tmp_path) + "\n",
        stderr="",
    )
    source = {
        "PATH": "/usr/bin:/bin",
        "SAFE": "kept",
        "GIT_CONFIG_GLOBAL": "/tmp/attacker.gitconfig",
        "NODE_OPTIONS": "--require=/tmp/pwn.js",
        "LD_PRELOAD": "/tmp/pwn.so",
        "BUN_OPTIONS": "--preload=/tmp/pwn.ts",
    }

    with mock.patch(
        "cli.claude_launch_settings.subprocess.run",
        return_value=completed,
    ) as run:
        resolved = claude_launch_settings._git_absolute_path(
            tmp_path,
            "--show-toplevel",
            required=True,
            environ=source,
        )

    assert resolved == str(tmp_path.resolve())
    assert run.call_args.kwargs["env"] == {
        "PATH": "/usr/bin:/bin",
        "SAFE": "kept",
    }


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_linked_worktree_control_character_metadata_path_fails_closed(tmp_path):
    main = tmp_path / "main\nrepo"
    linked = tmp_path / "linked-checkout"
    main.mkdir()
    subprocess.run(["git", "init", "-q", str(main)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(main),
            "-c",
            "user.name=Cartopian Test",
            "-c",
            "user.email=cartopian@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(main), "worktree", "add", "-qb", "linked", str(linked)],
        check=True,
    )

    for operation in (
        lambda: claude_launch_settings.project_git_protected_roots(linked),
        lambda: claude_launch_settings._normal_settings_paths(linked, {}),
    ):
        with pytest.raises(
            claude_launch_settings.SettingsError,
            match="(control character|invalid metadata path)",
        ):
            operation()


def test_posix_sandbox_fails_closed_for_unknown_dispatched_role(tmp_path):
    project, _prompt, _report = _project(tmp_path, gated=True)
    work_root = tmp_path / "product"
    work_root.mkdir()
    project.joinpath("cartopian.toml").write_text(
        "[project]\n"
        'id = "demo"\n'
        'name = "Demo"\n'
        'project_schema_version = "v0.13.0"\n'
        'work_roots = ["product"]\n\n'
        + GATED_ROLES,
        encoding="utf-8",
    )
    project.joinpath("cartopian.local.toml").write_text(
        f'[work_roots]\nproduct = "{work_root}"\n', encoding="utf-8"
    )
    filesystem = claude_launch_settings.build_settings(
        REPO_ROOT,
        windows=False,
        project_dir=project,
        include_capability=True,
        environ={"CARTOPIAN_ROLE": "not-a-role"},
    )["sandbox"]["filesystem"]
    assert str(project.resolve()) in filesystem["denyWrite"]
    assert str(work_root.resolve()) in filesystem["denyWrite"]


def test_activated_wsl_launch_refuses_unattested_interop_boundary(tmp_path):
    project, _prompt, _report = _project(tmp_path, gated=True)

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="refuses WSL2.*seccomp",
    ):
        claude_launch_settings.build_settings(
            REPO_ROOT,
            windows=False,
            project_dir=project,
            include_capability=True,
            environ={
                "CARTOPIAN_ROLE": "coder",
                "WSL_DISTRO_NAME": "Ubuntu",
            },
        )


def test_posix_sandbox_refuses_authorized_work_root_inside_project(tmp_path):
    project, _prompt, _report = _project(tmp_path, gated=True)
    work_root = project / "nested product"
    work_root.mkdir()
    project.joinpath("cartopian.toml").write_text(
        "[project]\n"
        'id = "demo"\n'
        'name = "Demo"\n'
        'project_schema_version = "v0.13.0"\n'
        'work_roots = ["product"]\n\n'
        + GATED_ROLES,
        encoding="utf-8",
    )
    project.joinpath("cartopian.local.toml").write_text(
        f'[work_roots]\nproduct = "{work_root}"\n', encoding="utf-8"
    )

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="cannot grant write:worktree.*protected enforcement/runtime root",
    ):
        claude_launch_settings.build_settings(
            REPO_ROOT,
            windows=False,
            project_dir=project,
            include_capability=True,
            environ={"CARTOPIAN_ROLE": "coder"},
        )


@pytest.mark.parametrize("unsafe_path", ("project", "work-root"))
def test_posix_sandbox_refuses_literal_paths_with_glob_syntax(
    tmp_path, unsafe_path
):
    project_parent = tmp_path / ("parent[1]" if unsafe_path == "project" else "parent")
    project, _prompt, _report = _project(project_parent, gated=True)
    work_root = tmp_path / ("product?tree" if unsafe_path == "work-root" else "product")
    work_root.mkdir()
    project.joinpath("cartopian.toml").write_text(
        "[project]\n"
        'id = "demo"\n'
        'name = "Demo"\n'
        'project_schema_version = "v0.13.0"\n'
        'work_roots = ["product"]\n\n'
        + GATED_ROLES,
        encoding="utf-8",
    )
    project.joinpath("cartopian.local.toml").write_text(
        f'[work_roots]\nproduct = "{work_root}"\n', encoding="utf-8"
    )

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="cannot safely encode literal policy paths.*glob metacharacters",
    ):
        claude_launch_settings.build_settings(
            REPO_ROOT,
            windows=False,
            project_dir=project,
            include_capability=True,
            environ={"CARTOPIAN_ROLE": "coder", "HOME": str(tmp_path / "home")},
        )


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS filesystem aliases")
@pytest.mark.parametrize(
    ("actual_name", "alias_name"),
    (
        ("GovernanceProject", "governanceproject"),
        (
            "Caf\N{LATIN SMALL LETTER E WITH ACUTE}Project",
            "Cafe\N{COMBINING ACUTE ACCENT}Project",
        ),
    ),
)
def test_posix_sandbox_refuses_nested_work_root_through_filesystem_alias(
    tmp_path, actual_name, alias_name
):
    project = tmp_path / actual_name
    work_root = project / "product"
    work_root.mkdir(parents=True)
    alias = tmp_path / alias_name
    try:
        same_project = os.path.samefile(project, alias)
    except OSError:
        same_project = False
    if not same_project:
        pytest.skip("test volume does not provide this alias behavior")
    project.joinpath("cartopian.toml").write_text(
        "[project]\n"
        'id = "demo"\n'
        'name = "Demo"\n'
        'project_schema_version = "v0.13.0"\n'
        'work_roots = ["product"]\n\n'
        + GATED_ROLES,
        encoding="utf-8",
    )
    project.joinpath("cartopian.local.toml").write_text(
        f'[work_roots]\nproduct = "{alias / "product"}"\n',
        encoding="utf-8",
    )

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="cannot grant write:worktree.*protected enforcement/runtime root",
    ):
        claude_launch_settings.build_settings(
            REPO_ROOT,
            windows=False,
            project_dir=project,
            include_capability=True,
            environ={"CARTOPIAN_ROLE": "coder", "HOME": str(tmp_path / "home")},
        )


@pytest.mark.parametrize("scope", ("user", "project"))
def test_activated_settings_isolation_ignores_normal_excluded_commands(
    tmp_path, scope
):
    project, _prompt, _report = _project(tmp_path, gated=True)
    config_home = tmp_path / "claude config"
    config_home.mkdir()
    settings_dir = config_home if scope == "user" else project / ".claude"
    settings_dir.mkdir(exist_ok=True)
    settings_dir.joinpath("settings.json").write_text(
        json.dumps({"sandbox": {"excludedCommands": ["python *"]}}),
        encoding="utf-8",
    )

    settings = claude_launch_settings.build_settings(
        REPO_ROOT,
        windows=False,
        project_dir=project,
        include_capability=True,
        environ={
            "CARTOPIAN_ROLE": "coder",
            "CLAUDE_CONFIG_DIR": str(config_home),
        },
    )

    assert settings["sandbox"]["enabled"] is True
    assert settings["sandbox"]["excludedCommands"] == []


@pytest.mark.parametrize(
    "settings_fragment",
    (
        {"sandbox": {"filesystem": {"allowWrite": ["/tmp/open"]}}},
        {"permissions": {"additionalDirectories": ["/tmp/open"]}},
        {"permissions": {"allow": ["Edit(/tmp/open/**)"]}},
        {"permissions": {"allow": ["Write(/tmp/open/**)"]}},
        {"sandbox": {"network": {"allowUnixSockets": ["/var/run/docker.sock"]}}},
        {"sandbox": {"network": {"allowAllUnixSockets": True}}},
        {"sandbox": {"network": {"allowMachLookup": ["com.apple.*"]}}},
        {"sandbox": {"enableWeakerNestedSandbox": True}},
    ),
)
def test_activated_settings_isolation_ignores_normal_write_wideners(
    tmp_path, settings_fragment
):
    project, _prompt, _report = _project(tmp_path, gated=True)
    config_home = tmp_path / "claude-config"
    config_home.mkdir()
    config_home.joinpath("settings.json").write_text(
        json.dumps(settings_fragment), encoding="utf-8"
    )

    settings = claude_launch_settings.build_settings(
        REPO_ROOT,
        windows=False,
        project_dir=project,
        include_capability=True,
        environ={
            "CARTOPIAN_ROLE": "coder",
            "CLAUDE_CONFIG_DIR": str(config_home),
        },
    )

    assert settings["sandbox"]["enabled"] is True
    assert settings["sandbox"]["filesystem"].get("allowWrite", []) == []


@pytest.mark.parametrize(
    "settings_fragment",
    (
        {"sandbox": {"ripgrep": {"command": "/tmp/host-rg"}}},
        {"env": {"PATH": "/tmp/agent-writable-bin:/usr/bin"}},
        {"env": {"USE_BUILTIN_RIPGREP": "false"}},
        {"env": {"RIPGREP_CONFIG_PATH": "/tmp/rg.conf"}},
    ),
)
def test_activated_settings_isolation_ignores_user_host_tool_overrides(
    tmp_path, settings_fragment
):
    project, _prompt, _report = _project(tmp_path, gated=True)
    config_home = tmp_path / "claude-config"
    config_home.mkdir()
    config_home.joinpath("settings.json").write_text(
        json.dumps(settings_fragment), encoding="utf-8"
    )

    environ = {"CLAUDE_CONFIG_DIR": str(config_home), "PATH": "/usr/bin"}
    claude_launch_settings._refuse_external_host_tool_overrides(
        project, environ, platform_name="linux"
    )
    effective, sources = claude_launch_settings._effective_host_tool_environment(
        project, environ
    )
    assert effective["PATH"] == "/usr/bin"
    assert sources["PATH"] == "inherited environment"


@pytest.mark.parametrize("false_value", ("0", " false ", "NO", "off"))
def test_posix_sandbox_refuses_inherited_external_ripgrep(tmp_path, false_value):
    project, _prompt, _report = _project(tmp_path, gated=True)

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="USE_BUILTIN_RIPGREP=false",
    ):
        claude_launch_settings.build_settings(
            REPO_ROOT,
            windows=False,
            project_dir=project,
            include_capability=True,
            environ={
                "CARTOPIAN_ROLE": "coder",
                "USE_BUILTIN_RIPGREP": false_value,
            },
        )


def test_isolated_user_environment_cannot_mask_unsafe_legacy_values(tmp_path):
    project, _prompt, _report = _project(tmp_path, gated=True)
    config_home = tmp_path / "claude-config"
    config_home.mkdir()
    inherited_path = "/usr/bin:/bin"
    config_home.joinpath(".claude.json").write_text(
        "\ufeff"
        + json.dumps(
            {
                "env": {
                    "PATH": "/tmp/legacy-bin",
                    "USE_BUILTIN_RIPGREP": "off",
                    "RIPGREP_CONFIG_PATH": "/tmp/legacy-rg.conf",
                }
            }
        ),
        encoding="utf-8",
    )
    config_home.joinpath("settings.json").write_text(
        json.dumps(
            {
                "env": {
                    "PATH": inherited_path,
                    "USE_BUILTIN_RIPGREP": "true",
                    "RIPGREP_CONFIG_PATH": "",
                }
            }
        ),
        encoding="utf-8",
    )
    environ = {
        "CLAUDE_CONFIG_DIR": str(config_home),
        "PATH": inherited_path,
        "USE_BUILTIN_RIPGREP": "false",
        "RIPGREP_CONFIG_PATH": "/tmp/inherited-rg.conf",
    }

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="refuses host-tool overrides",
    ):
        claude_launch_settings._refuse_external_host_tool_overrides(
            project, environ, platform_name="linux"
        )
    effective, sources = claude_launch_settings._effective_host_tool_environment(
        project, environ
    )
    assert effective["PATH"] == "/tmp/legacy-bin"
    assert effective["USE_BUILTIN_RIPGREP"] == "off"
    assert effective["RIPGREP_CONFIG_PATH"] == "/tmp/legacy-rg.conf"
    assert sources["PATH"] == str(config_home / ".claude.json")


def test_macos_isolated_user_path_is_not_part_of_host_resolution(tmp_path):
    project, _prompt, _report = _project(tmp_path, gated=True)
    config_home = tmp_path / "claude-config"
    config_home.mkdir()
    user_bin = tmp_path / "user bin"
    user_bin.mkdir()
    rg = user_bin / "rg"
    rg.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    rg.chmod(rg.stat().st_mode | stat.S_IXUSR)
    config_home.joinpath("settings.json").write_text(
        json.dumps({"env": {"PATH": str(user_bin)}}),
        encoding="utf-8",
    )
    environ = {"CLAUDE_CONFIG_DIR": str(config_home), "PATH": "/usr/bin"}

    claude_launch_settings._refuse_external_host_tool_overrides(
        project, environ, platform_name="darwin"
    )
    effective, _sources = claude_launch_settings._effective_host_tool_environment(
        project, environ
    )
    roots = claude_launch_settings.sandbox_host_executable_roots(
        effective, platform_name="darwin"
    )

    assert str(user_bin.resolve()) not in roots
    assert str(rg.resolve()) not in roots


@pytest.mark.parametrize("invalid_override", (None, {}, [], ["false"]))
def test_invalid_legacy_env_values_do_not_clear_inherited_unsafe_value(
    tmp_path, invalid_override
):
    project, _prompt, _report = _project(tmp_path, gated=True)
    config_home = tmp_path / "claude-config"
    config_home.mkdir()
    config_home.joinpath(".claude.json").write_text(
        json.dumps({"env": {"USE_BUILTIN_RIPGREP": invalid_override}}),
        encoding="utf-8",
    )
    environ = {
        "CLAUDE_CONFIG_DIR": str(config_home),
        "USE_BUILTIN_RIPGREP": "false",
    }

    effective, _sources = claude_launch_settings._effective_host_tool_environment(
        project, environ
    )
    assert effective["USE_BUILTIN_RIPGREP"] == "false"


def test_numeric_false_and_whitespace_ripgrep_config_are_refused(tmp_path):
    project, _prompt, _report = _project(tmp_path, gated=True)
    config_home = tmp_path / "claude-config"
    config_home.mkdir()
    config_home.joinpath(".claude.json").write_text(
        json.dumps(
            {
                "env": {
                    "USE_BUILTIN_RIPGREP": 0.0,
                    "RIPGREP_CONFIG_PATH": "   ",
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="USE_BUILTIN_RIPGREP=false.*RIPGREP_CONFIG_PATH",
    ):
        claude_launch_settings._refuse_external_host_tool_overrides(
            project,
            {"CLAUDE_CONFIG_DIR": str(config_home)},
            platform_name="darwin",
        )


def test_overflowed_legacy_number_is_modeled_as_javascript_infinity(tmp_path):
    project, _prompt, _report = _project(tmp_path, gated=True)
    config_home = tmp_path / "claude-config"
    config_home.mkdir()
    config_home.joinpath(".claude.json").write_text(
        '{"env":{"PATH":1e400,"RIPGREP_CONFIG_PATH":-1e400}}\n',
        encoding="utf-8",
    )
    environ = {"CLAUDE_CONFIG_DIR": str(config_home), "PATH": "/usr/bin"}

    effective, _sources = claude_launch_settings._effective_host_tool_environment(
        project, environ
    )

    assert effective["PATH"] == "Infinity"
    assert effective["RIPGREP_CONFIG_PATH"] == "-Infinity"
    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="refuses host-tool overrides",
    ):
        claude_launch_settings._refuse_external_host_tool_overrides(
            project, environ, platform_name="linux"
        )


def test_project_local_host_tool_overrides_are_frozen_not_refused(tmp_path):
    project, _prompt, _report = _project(tmp_path, gated=True)
    project_settings = project / ".claude" / "settings.json"
    project_settings.parent.mkdir()
    project_settings.write_text(
        json.dumps(
            {
                "sandbox": {"ripgrep": {"command": "/tmp/project-rg"}},
                "env": {
                    "PATH": "/tmp/project-bin",
                    "USE_BUILTIN_RIPGREP": "false",
                },
            }
        ),
        encoding="utf-8",
    )

    settings = claude_launch_settings.build_settings(
        REPO_ROOT,
        windows=False,
        project_dir=project,
        include_capability=True,
        environ={"CARTOPIAN_ROLE": "coder"},
    )

    assert settings["sandbox"]["enabled"] is True


def test_linux_host_sandbox_executables_are_protected(tmp_path):
    fake_bin = tmp_path / "host bin"
    fake_bin.mkdir()
    for name in ("bwrap", "socat", "rg"):
        executable = fake_bin / name
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    roots = claude_launch_settings.sandbox_host_executable_roots(
        {"PATH": str(fake_bin)}, platform_name="linux"
    )

    assert str(fake_bin.resolve()) in roots
    assert str((fake_bin / "bwrap").resolve()) in roots
    assert str((fake_bin / "socat").resolve()) in roots
    assert str((fake_bin / "rg").resolve()) in roots


def test_missing_linux_host_dependency_is_left_to_fail_if_unavailable(tmp_path):
    roots = claude_launch_settings.sandbox_host_executable_roots(
        {"PATH": str(tmp_path)}, platform_name="linux"
    )
    assert roots == (str(tmp_path.resolve()),)


@pytest.mark.parametrize("unsafe_path", ("bin:/usr/bin", ":/usr/bin"))
def test_posix_host_dependency_path_refuses_relative_components(unsafe_path):
    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="empty or relative PATH search component",
    ):
        claude_launch_settings.sandbox_host_executable_roots(
            {"PATH": unsafe_path}, platform_name="darwin"
        )


@pytest.mark.parametrize(
    "unsafe_key",
    (
        "PYTHONHOME",
        "PYTHONPATH",
        "LD_PRELOAD",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "OPENSSL_CONF",
        "OPENSSL_MODULES",
        "__PYVENV_LAUNCHER__",
        "GCONV_PATH",
        "GLIBC_TUNABLES",
        "CLAUDE_CONFIG_DIR",
        "CLAUDE_CODE_PROCESS_WRAPPER",
        "CLAUDE_CODE_SAFE_MODE",
        "CLAUDE_CODE_SIMPLE",
        "CLAUDE_CODE_STOP_HOOK_BLOCK_CAP",
        "CLAUDE_CODE_TMPDIR",
        "CLAUDE_TMPDIR",
        "NODE_OPTIONS",
        "NODE_PATH",
        "BUN_OPTIONS",
        "BUN_TMPDIR",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
    ),
)
def test_completion_only_hook_refuses_unsafe_settings_environment(tmp_path, unsafe_key):
    project, _prompt, _report = _project(tmp_path, gated=False)
    config_home = tmp_path / "claude-config"
    config_home.mkdir()
    config_home.joinpath("settings.json").write_text(
        json.dumps({"env": {unsafe_key: "adversarial-value"}}),
        encoding="utf-8",
    )

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="refuse Claude config env values",
    ):
        claude_launch_settings.build_settings(
            REPO_ROOT,
            windows=False,
            project_dir=project,
            include_completion=True,
            environ={"CLAUDE_CONFIG_DIR": str(config_home)},
        )


@pytest.mark.parametrize(
    ("unsafe_key", "unsafe_value"),
    (
        ("CARTOPIAN_CLAUDE_BARE", "true"),
        ("CLAUDE_CODE_SIMPLE", "1"),
        ("CLAUDE_CODE_SAFE_MODE", "1"),
        ("CLAUDE_CODE_PROCESS_WRAPPER", "1"),
        ("CLAUDE_CODE_STOP_HOOK_BLOCK_CAP", "1"),
        ("CLAUDE_CODE_TMPDIR", "/tmp/adversarial-claude"),
        ("CLAUDE_TMPDIR", "/tmp/adversarial-child-temp"),
    ),
)
def test_hook_refuses_inherited_suppression_environment(
    tmp_path, unsafe_key, unsafe_value
):
    project, _prompt, _report = _project(tmp_path, gated=False)

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="refuse inherited launch modes",
    ):
        claude_launch_settings.build_settings(
            REPO_ROOT,
            windows=False,
            project_dir=project,
            include_completion=True,
            environ={unsafe_key: unsafe_value},
        )


@pytest.mark.parametrize("temp_key", ("TMPDIR", "TMP", "TEMP"))
def test_inherited_system_temp_environment_does_not_disable_hooks(
    tmp_path, temp_key
):
    project, _prompt, _report = _project(tmp_path, gated=False)

    settings = claude_launch_settings.build_settings(
        REPO_ROOT,
        windows=False,
        project_dir=project,
        include_completion=True,
        environ={temp_key: str(tmp_path / "system-temp")},
    )

    assert "Stop" in settings["hooks"]


def test_completion_hook_allows_benign_settings_environment(tmp_path):
    project, _prompt, _report = _project(tmp_path, gated=False)
    config_home = tmp_path / "claude-config"
    config_home.mkdir()
    config_home.joinpath("settings.json").write_text(
        json.dumps({"env": {"EDITOR": "vim", "DISABLE_AUTOUPDATER": "1"}}),
        encoding="utf-8",
    )

    settings = claude_launch_settings.build_settings(
        REPO_ROOT,
        windows=False,
        project_dir=project,
        include_completion=True,
        environ={"CLAUDE_CONFIG_DIR": str(config_home)},
    )
    assert "Stop" in settings["hooks"]


def test_completion_hook_refuses_process_wrapper_setting(tmp_path):
    project, _prompt, _report = _project(tmp_path, gated=False)
    config_home = tmp_path / "claude-config"
    config_home.mkdir()
    config_home.joinpath("settings.json").write_text(
        json.dumps({"processWrapper": "/tmp/interpose"}), encoding="utf-8"
    )

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="processWrapper",
    ):
        claude_launch_settings.build_settings(
            REPO_ROOT,
            windows=False,
            project_dir=project,
            include_completion=True,
            environ={"CLAUDE_CONFIG_DIR": str(config_home)},
        )


def test_non_object_settings_do_not_crash_hook_preflight(tmp_path):
    project, _prompt, _report = _project(tmp_path, gated=False)
    config_home = tmp_path / "claude-config"
    config_home.mkdir()
    config_home.joinpath("settings.json").write_text("[]\n", encoding="utf-8")

    settings = claude_launch_settings.build_settings(
        REPO_ROOT,
        windows=False,
        project_dir=project,
        include_completion=True,
        environ={"CLAUDE_CONFIG_DIR": str(config_home)},
    )

    assert "Stop" in settings["hooks"]


def test_legacy_global_config_unsafe_environment_is_refused_and_protected(tmp_path):
    project, _prompt, _report = _project(tmp_path, gated=True)
    config_home = tmp_path / "claude-config"
    config_home.mkdir()
    legacy = config_home / ".claude.json"
    legacy.write_text(
        json.dumps({"env": {"GCONV_PATH": str(tmp_path / "modules")}}),
        encoding="utf-8",
    )
    environ = {
        "CARTOPIAN_ROLE": "coder",
        "CLAUDE_CONFIG_DIR": str(config_home),
    }

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match=r"\.claude\.json.*GCONV_PATH",
    ):
        claude_launch_settings.build_settings(
            REPO_ROOT,
            windows=False,
            project_dir=project,
            include_completion=True,
            environ=environ,
        )
    with pytest.raises(
        claude_launch_settings.SettingsError,
        match=r"\.claude\.json.*GCONV_PATH",
    ):
        claude_launch_settings.build_settings(
            REPO_ROOT,
            windows=False,
            project_dir=project,
            include_capability=True,
            environ=environ,
        )

    legacy.write_text(
        json.dumps({"env": {"EDITOR": "vim"}}), encoding="utf-8"
    )
    settings = claude_launch_settings.build_settings(
        REPO_ROOT,
        windows=False,
        project_dir=project,
        include_capability=True,
        environ=environ,
    )
    handler_args = settings["hooks"]["PreToolUse"][0]["hooks"][0]["args"]
    protected = [
        handler_args[index + 1]
        for index, item in enumerate(handler_args)
        if item == "--settings-path"
    ]
    assert str(legacy.resolve()) in protected
    assert str(legacy.resolve()) in settings["sandbox"]["filesystem"]["denyWrite"]


def test_activated_legacy_global_home_cannot_relocate_sandbox_write_roots(tmp_path):
    project, _prompt, _report = _project(tmp_path, gated=True)
    config_home = tmp_path / "claude-config"
    config_home.mkdir()
    config_home.joinpath(".claude.json").write_text(
        "\ufeff"
        + json.dumps({"env": {"HOME": str(tmp_path / "hidden-home")}}),
        encoding="utf-8",
    )

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match=r"\.claude\.json.*HOME",
    ):
        claude_launch_settings.build_settings(
            REPO_ROOT,
            windows=False,
            project_dir=project,
            include_capability=True,
            environ={
                "CARTOPIAN_ROLE": "coder",
                "CLAUDE_CONFIG_DIR": str(config_home),
            },
        )


def test_activated_legacy_global_malformed_utf8_fails_closed(tmp_path):
    project, _prompt, _report = _project(tmp_path, gated=True)
    config_home = tmp_path / "claude-config"
    config_home.mkdir()
    config_home.joinpath(".claude.json").write_bytes(
        b'{"env":{"HOME":"\xff"}}\n'
    )

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="not valid UTF-8",
    ):
        claude_launch_settings.build_settings(
            REPO_ROOT,
            windows=False,
            project_dir=project,
            include_capability=True,
            environ={
                "CARTOPIAN_ROLE": "coder",
                "CLAUDE_CONFIG_DIR": str(config_home),
            },
        )


def test_capability_only_hook_ignores_unsafe_normal_settings_environment(tmp_path):
    project, _prompt, _report = _project(tmp_path, gated=True)
    config_home = tmp_path / "claude-config"
    config_home.mkdir()
    config_home.joinpath("settings.json").write_text(
        json.dumps({"env": {"GCONV_PATH": str(tmp_path / "modules")}}),
        encoding="utf-8",
    )

    settings = claude_launch_settings.build_settings(
        REPO_ROOT,
        windows=False,
        project_dir=project,
        include_capability=True,
        environ={
            "CARTOPIAN_ROLE": "coder",
            "CLAUDE_CONFIG_DIR": str(config_home),
        },
    )

    assert "PreToolUse" in settings["hooks"]


def test_process_settings_reenable_required_hooks_over_normal_disable(tmp_path):
    project, _prompt, _report = _project(tmp_path, gated=True)
    config_home = tmp_path / "claude-config"
    config_home.mkdir()
    config_home.joinpath("settings.json").write_text(
        json.dumps({"disableAllHooks": True}), encoding="utf-8"
    )
    settings = claude_launch_settings.build_settings(
        REPO_ROOT,
        windows=False,
        project_dir=project,
        include_capability=True,
        environ={
            "CARTOPIAN_ROLE": "coder",
            "CLAUDE_CONFIG_DIR": str(config_home),
        },
    )

    assert settings["disableAllHooks"] is False
    assert "PreToolUse" in settings["hooks"]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_normal_settings_paths_follow_linked_worktree_scope_rules(tmp_path):
    main = tmp_path / "main-checkout"
    linked = tmp_path / "linked-checkout"
    main.mkdir()
    subprocess.run(["git", "init", "-q", str(main)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(main),
            "-c",
            "user.name=Cartopian Test",
            "-c",
            "user.email=cartopian@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(main), "worktree", "add", "-qb", "linked", str(linked)],
        check=True,
    )
    user_config = tmp_path / "user-config"
    paths = claude_launch_settings._normal_settings_paths(
        linked, {"CLAUDE_CONFIG_DIR": str(user_config)}
    )

    assert paths == (
        (user_config / "settings.json").resolve(),
        (linked / ".claude" / "settings.json").resolve(),
        (linked / ".claude" / "settings.local.json").resolve(),
        (main / ".claude" / "settings.local.json").resolve(),
    )
    assert (user_config / "settings.local.json").resolve() not in paths

    nested_project = linked / "nested-cartopian-project"
    nested_project.mkdir()
    nested_paths = claude_launch_settings._normal_settings_paths(
        nested_project, {"CLAUDE_CONFIG_DIR": str(user_config)}
    )
    assert (nested_project / ".claude" / "settings.json").resolve() in nested_paths
    assert (nested_project / ".claude" / "settings.local.json").resolve() in nested_paths
    assert (linked / ".claude" / "settings.json").resolve() not in nested_paths
    assert (main / ".claude" / "settings.local.json").resolve() in nested_paths

    windows_paths = claude_launch_settings._normal_settings_paths(
        linked,
        {"CLAUDE_CONFIG_DIR": str(user_config)},
        windows=True,
    )
    assert (linked / ".claude" / "settings.local.json").resolve() in windows_paths
    assert (main / ".claude" / "settings.local.json").resolve() not in windows_paths

    home_repository_paths = claude_launch_settings._normal_settings_paths(
        linked, {"HOME": str(linked)}
    )
    assert (linked / ".claude" / "settings.local.json").resolve() in home_repository_paths
    assert (main / ".claude" / "settings.local.json").resolve() not in home_repository_paths

    getuid = getattr(os, "getuid", None)
    if getuid is not None:
        with mock.patch(
            "cli.claude_launch_settings.os.getuid", return_value=getuid() + 1
        ):
            foreign_owner_paths = claude_launch_settings._normal_settings_paths(
                linked,
                {
                    "CLAUDE_CONFIG_DIR": str(user_config),
                    "HOME": str(tmp_path / "home"),
                },
            )
        assert (
            linked / ".claude" / "settings.local.json"
        ).resolve() in foreign_owner_paths
        assert (
            main / ".claude" / "settings.local.json"
        ).resolve() not in foreign_owner_paths

    local_settings = main / ".claude" / "settings.local.json"
    local_settings.parent.mkdir()
    local_settings.write_text(
        json.dumps({"sandbox": {"excludedCommands": ["python *"]}}),
        encoding="utf-8",
    )
    claude_launch_settings._refuse_merged_sandbox_exclusions(
        linked, {"CLAUDE_CONFIG_DIR": str(user_config)}
    )

    local_settings.unlink()
    linked_local = linked / ".claude" / "settings.local.json"
    linked_local.parent.mkdir()
    linked_local.write_text(
        json.dumps({"sandbox": {"excludedCommands": ["python *"]}}),
        encoding="utf-8",
    )
    claude_launch_settings._refuse_merged_sandbox_exclusions(
        linked, {"CLAUDE_CONFIG_DIR": str(user_config)}
    )

    linked_local.unlink()
    user_config.mkdir()
    user_config.joinpath("settings.local.json").write_text(
        json.dumps({"sandbox": {"excludedCommands": ["python *"]}}),
        encoding="utf-8",
    )
    claude_launch_settings._refuse_merged_sandbox_exclusions(
        linked, {"CLAUDE_CONFIG_DIR": str(user_config)}
    )

    user_config.joinpath(".claude.json").write_text(
        json.dumps({"sandbox": {"excludedCommands": ["python *"]}}),
        encoding="utf-8",
    )
    claude_launch_settings._refuse_merged_sandbox_exclusions(
        linked, {"CLAUDE_CONFIG_DIR": str(user_config)}
    )


def test_windows_settings_paths_prefer_userprofile_over_home(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    userprofile = tmp_path / "windows-profile"
    home = tmp_path / "posix-home"

    paths = claude_launch_settings._normal_settings_paths(
        project,
        {"USERPROFILE": str(userprofile), "HOME": str(home)},
        windows=True,
    )

    assert paths[0] == (userprofile / ".claude" / "settings.json").resolve()
    assert (home / ".claude" / "settings.json").resolve() not in paths
    assert claude_launch_settings._legacy_global_config_path(
        project,
        {"USERPROFILE": str(userprofile), "HOME": str(home)},
        windows=True,
    ) == (userprofile / ".claude.json").resolve()


@pytest.mark.skipif(os.name == "nt", reason="POSIX passwd home semantics")
def test_posix_home_ignores_userprofile_when_home_is_unset(tmp_path):
    import pwd

    expected = Path(pwd.getpwuid(os.getuid()).pw_dir)

    assert claude_launch_settings._user_home(
        {"USERPROFILE": str(tmp_path / "foreign-profile")}, windows=False
    ) == expected


@pytest.mark.parametrize("home", ("", "relative/home", "~root", " home "))
def test_posix_home_requires_an_absolute_literal(home):
    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="POSIX HOME must be an absolute literal path",
    ):
        claude_launch_settings._user_home({"HOME": home}, windows=False)


@pytest.mark.parametrize("configured", ("relative/config", "~/.claude-alt"))
def test_relative_claude_config_dir_is_refused(tmp_path, configured):
    project, _prompt, _report = _project(tmp_path, gated=False)

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="CLAUDE_CONFIG_DIR must be an absolute path",
    ):
        claude_launch_settings.build_settings(
            REPO_ROOT,
            windows=False,
            project_dir=project,
            include_completion=True,
            environ={"CLAUDE_CONFIG_DIR": configured},
        )


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_submodule_local_settings_stay_at_submodule_root(tmp_path):
    source = tmp_path / "component-source"
    superproject = tmp_path / "superproject"
    source.mkdir()
    superproject.mkdir()
    for repository in (source, superproject):
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "-c",
                "user.name=Cartopian Test",
                "-c",
                "user.email=cartopian@example.invalid",
                "commit",
                "--allow-empty",
                "-qm",
                "fixture",
            ],
            check=True,
        )
    subprocess.run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "-C",
            str(superproject),
            "submodule",
            "add",
            "-q",
            str(source),
            "component",
        ],
        check=True,
    )
    component = superproject / "component"

    paths = claude_launch_settings._normal_settings_paths(
        component, {"CLAUDE_CONFIG_DIR": str(tmp_path / "user-config")}
    )

    assert (component / ".claude" / "settings.local.json").resolve() in paths
    assert (
        superproject / ".claude" / "settings.local.json"
    ).resolve() not in paths


def test_posix_generated_stop_command_runs_from_install_path_with_spaces(tmp_path):
    install_root = tmp_path / "Cartopian copy install with spaces"
    install_copy_fixture(REPO_ROOT, install_root)
    report = tmp_path / "report path with spaces" / "REPORT-01-401.md"
    report.parent.mkdir()
    settings = claude_launch_settings.build_settings(
        install_root,
        windows=False,
        include_completion=True,
        environ={"CARTOPIAN_EXPECTED_REPORT_PATH": str(report)},
    )
    handler = settings["hooks"]["Stop"][0]["hooks"][0]
    payload = {
        "session_id": "quoted-command-session",
        "hook_event_name": "Stop",
        "stop_hook_active": False,
    }
    env = os.environ.copy()
    env["CARTOPIAN_EXPECTED_REPORT_PATH"] = str(report)
    env["CARTOPIAN_STOP_GUARD_MAX_BLOCKS"] = "0"
    env["PYTHONHOME"] = "/definitely/not/a/python/home"
    shadow = tmp_path / "hostile-python-path"
    shadow.mkdir()
    shadow.joinpath("json.py").write_text(
        "raise SystemExit('PYTHONPATH shadow executed')\n", encoding="utf-8"
    )
    env["PYTHONPATH"] = str(shadow)
    result = subprocess.run(
        [handler["command"], *handler["args"]],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["decision"] == "block"


def test_bound_capability_hook_runs_under_poisoned_python_environment(tmp_path):
    install_root = tmp_path / "Cartopian copy install"
    install_copy_fixture(REPO_ROOT, install_root)
    project, _prompt, _report = _project(tmp_path, gated=True)
    home = tmp_path / "home"
    registry = home / ".cartopian" / "projects.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps([{"id": "demo", "path": str(project)}]),
        encoding="utf-8",
    )
    settings = claude_launch_settings.build_settings(
        install_root,
        windows=False,
        project_dir=project,
        include_capability=True,
        environ={"HOME": str(home), "CARTOPIAN_ROLE": "coder"},
    )
    handler = settings["hooks"]["PreToolUse"][0]["hooks"][0]
    shadow = tmp_path / "hostile-python-path"
    shadow.mkdir()
    shadow.joinpath("json.py").write_text(
        "raise SystemExit('PYTHONPATH shadow executed')\n", encoding="utf-8"
    )
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PYTHONHOME": "/definitely/not/a/python/home",
            "PYTHONPATH": str(shadow),
        }
    )
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(project / "STATE.md")},
        "cwd": str(project),
    }
    result = subprocess.run(
        [handler["command"], *handler["args"]],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    decision = json.loads(result.stdout)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_bound_capability_hook_accepts_leading_hyphen_role_name(tmp_path):
    install_root = tmp_path / "Cartopian copy install"
    install_copy_fixture(REPO_ROOT, install_root)
    project, _prompt, _report = _project(tmp_path, gated=True)
    project.joinpath("cartopian.toml").write_text(
        "[project]\n"
        'id = "demo"\n'
        'name = "Demo"\n'
        'project_schema_version = "v0.9.0"\n\n'
        '[roles."-coder"]\n'
        'description = "Implements tasks."\n'
        'grants = ["coder-like"]\n',
        encoding="utf-8",
    )
    home = tmp_path / "home"
    settings = claude_launch_settings.build_settings(
        install_root,
        windows=False,
        project_dir=project,
        include_capability=True,
        environ={"HOME": str(home), "CARTOPIAN_ROLE": "-coder"},
    )
    handler = settings["hooks"]["PreToolUse"][0]["hooks"][0]
    assert "--role=-coder" in handler["args"]
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(project / "STATE.md")},
        "cwd": str(project),
    }

    result = subprocess.run(
        [handler["command"], *handler["args"]],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["hookSpecificOutput"][
        "permissionDecision"
    ] == "deny"


def test_powershell_wrapper_constructs_native_windows_settings_argument():
    wrapper = PS1_WRAPPER.read_text(encoding="utf-8")
    helper = PS1_HELPER.read_text(encoding="utf-8")
    assert "if ($env:CARTOPIAN_ROLE -or $env:CARTOPIAN_EXPECTED_REPORT_PATH)" in wrapper
    assert "if ($env:CARTOPIAN_ROLE) { $SettingsHelperArgs += '--capability' }" in wrapper
    assert "if ($env:CARTOPIAN_EXPECTED_REPORT_PATH) { $SettingsHelperArgs += '--completion' }" in wrapper
    assert "$ClaudeExecutable = $env:CARTOPIAN_CLAUDE_EXECUTABLE" in wrapper
    assert "$PythonPath = $env:CARTOPIAN_PYTHON" in wrapper
    assert "hook-enabled launches require an absolute CARTOPIAN_CLAUDE_EXECUTABLE file" in wrapper
    assert "hook-enabled launches require an absolute CARTOPIAN_PYTHON file" in wrapper
    assert "$HookBound -and -not $StatusHelperPresent" in wrapper
    assert "hook-enabled launches require the installed status/supervisor helper" in wrapper
    assert ".cmd/.bat shims cannot preserve the exact settings argv boundary" in wrapper
    assert "'--platform', 'windows'" in wrapper
    assert "& $PythonPath -I -S @SettingsHelperArgs --preflight-only" in wrapper
    assert "$ClaudeVersionOutput = (& $ClaudeExecutable --version" in wrapper
    assert "$Args += @('--settings', $ClaudeLaunchSettingsJson)" in wrapper
    assert "$Args += '--strict-mcp-config'" in wrapper
    assert "'--disallowedTools'" in wrapper
    assert "EnterWorktree" in wrapper
    assert "$Args += @('--setting-sources', '')" in wrapper
    assert "if ($Bare)" in wrapper and "$Args += '--bare'" in wrapper
    assert "CARTOPIAN_CLAUDE_BARE=true suppresses required" in wrapper
    assert "$startInfo.ArgumentList.Add([string]$argument)" in helper
    assert "ConvertTo-CartopianWindowsArgument" in helper


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
