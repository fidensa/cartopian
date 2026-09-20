"""Focused launch-boundary checks shared by dispatch and its rehearsal."""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from unittest import mock

import pytest

from cli import claude_launch_settings, dispatch_rehearsal, launch_preflight

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _underlying_claude_on_path(tmp_path, monkeypatch):
    fake_bin = tmp_path / "underlying-agent-bin"
    fake_bin.mkdir()
    claude = fake_bin / "claude"
    claude.write_text("#!/bin/sh\necho '2.1.278 (Claude Code)'\n", encoding="utf-8")
    claude.chmod(claude.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv(
        "PATH", str(fake_bin) + os.pathsep + os.environ.get("PATH", "")
    )


def _claude_stub(tmp_path: Path) -> Path:
    del tmp_path
    return REPO_ROOT / "wrappers" / "bin" / "cartopian-claude"


def _windows_claude_stub(tmp_path: Path) -> Path:
    del tmp_path
    return REPO_ROOT / "wrappers" / "ps1" / "cartopian-claude.cmd"


def _role(agent: Path, grants: tuple[str, ...]) -> dict:
    return {
        "launch": {"agent": str(agent)},
        "effective_grants": list(grants),
        "auto_launch": ["task_run"],
    }


def _codes(findings: list[dict[str, str]]) -> set[str]:
    return {finding["code"] for finding in findings}


def test_activated_posix_claude_rejects_authorized_nested_root(tmp_path):
    project = tmp_path / "project"
    nested = project / "product"
    nested.mkdir(parents=True)
    agent = _claude_stub(tmp_path)
    role = _role(agent, ("write:worktree",))

    findings = launch_preflight.environment_checks(
        "coder",
        role,
        {"product": str(nested)},
        project_root=project,
        capabilities_activated=True,
    )

    assert "claude-nested-work-root" in _codes(findings)


def test_activated_posix_claude_rejects_work_root_nested_in_writable_work_root(
    tmp_path,
):
    project = tmp_path / "project"
    project.mkdir()
    product = tmp_path / "product"
    child = product / "child"
    child.mkdir(parents=True)

    findings = launch_preflight.environment_checks(
        "coder",
        _role(_claude_stub(tmp_path), ("write:worktree",)),
        {"product": str(product), "child": str(child)},
        project_root=project,
        capabilities_activated=True,
    )

    assert "claude-nested-work-root" in _codes(findings)


def test_activated_posix_claude_rejects_work_root_nested_in_implicit_write_root(
    tmp_path,
):
    project = tmp_path / "project"
    project.mkdir()
    home = tmp_path / "home"
    product = home / ".npm" / "_logs" / "product"
    product.mkdir(parents=True)
    env = {**os.environ, "HOME": str(home)}

    findings = launch_preflight.environment_checks(
        "coder",
        _role(_claude_stub(tmp_path), ("write:worktree",)),
        {"product": str(product)},
        project_root=project,
        capabilities_activated=True,
        environ=env,
    )

    assert "claude-nested-work-root" in _codes(findings)


def test_precontainment_path_rejects_implicit_shell_writable_directory(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    home = tmp_path / "home"
    implicit_bin = home / ".npm" / "_logs"
    implicit_bin.mkdir(parents=True)
    env = {
        "HOME": str(home),
        "PATH": str(implicit_bin),
    }

    unsafe = claude_launch_settings.unsafe_precontainment_path_components(
        env, project, ()
    )

    assert unsafe == (str(implicit_bin),)


def test_underlying_claude_inside_implicit_write_root_is_refused(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    home = tmp_path / "home"
    implicit_bin = home / ".claude" / "debug"
    implicit_bin.mkdir(parents=True)
    claude = implicit_bin / "claude"
    claude.write_text("#!/bin/sh\n", encoding="utf-8")
    claude.chmod(claude.stat().st_mode | stat.S_IXUSR)
    env = {
        "HOME": str(home),
        "PATH": str(implicit_bin),
        "CARTOPIAN_CLAUDE_EXECUTABLE": str(claude),
    }

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="implicit shell-writable root",
    ):
        claude_launch_settings.claude_executable_protected_roots(
            project, {}, env, windows=False
        )


def test_protected_project_inside_implicit_write_root_is_refused(tmp_path):
    home = tmp_path / "home"
    project = home / ".npm" / "_logs" / "project"
    project.mkdir(parents=True)
    env = {**os.environ, "HOME": str(home)}

    findings = launch_preflight.environment_checks(
        "coder",
        _role(_claude_stub(tmp_path), ("write:worktree",)),
        {},
        project_root=project,
        capabilities_activated=True,
        environ=env,
    )

    assert "claude-implicit-write-overlap" in _codes(findings)


def test_activated_posix_claude_rejects_external_project_symlink(tmp_path):
    project = tmp_path / "project"
    backing = tmp_path / "external"
    project.mkdir()
    backing.mkdir()
    project.joinpath("specs").symlink_to(backing, target_is_directory=True)

    findings = launch_preflight.environment_checks(
        "coder",
        _role(_claude_stub(tmp_path), ("write:worktree",)),
        {},
        project_root=project,
        capabilities_activated=True,
    )

    assert "claude-project-path-alias" in _codes(findings)


def test_activated_posix_claude_rejects_multiply_linked_project_file(tmp_path):
    project = tmp_path / "project"
    work_root = tmp_path / "product"
    project.mkdir()
    work_root.mkdir()
    source = project / "STATE.md"
    source.write_text("state\n", encoding="utf-8")
    try:
        os.link(source, work_root / "ordinary.txt")
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    findings = launch_preflight.environment_checks(
        "coder",
        _role(_claude_stub(tmp_path), ("write:worktree",)),
        {"product": str(work_root)},
        project_root=project,
        capabilities_activated=True,
    )

    assert "claude-project-path-alias" in _codes(findings)


def test_writable_work_root_rejects_hardlink_name_outside_authorized_roots(
    tmp_path,
):
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

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="hard-link names outside the authorized root set",
    ):
        claude_launch_settings.validate_writable_work_root_hardlinks((work_root,))


def test_writable_work_root_allows_hardlinks_wholly_inside_authorized_roots(
    tmp_path,
):
    first_root = tmp_path / "product-a"
    second_root = tmp_path / "product-b"
    first_root.mkdir()
    second_root.mkdir()
    source = first_root / "source.txt"
    source.write_text("shared\n", encoding="utf-8")
    try:
        os.link(source, second_root / "alias.txt")
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    claude_launch_settings.validate_writable_work_root_hardlinks(
        (first_root, second_root)
    )


def test_protected_regular_file_with_hardlink_alias_is_rejected(tmp_path):
    protected = tmp_path / "protected.txt"
    work_root = tmp_path / "product"
    work_root.mkdir()
    protected.write_text("protected\n", encoding="utf-8")
    try:
        os.link(protected, work_root / "alias.txt")
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="protected shell-write file has multiple hard-link names",
    ):
        claude_launch_settings.validate_writable_work_root_hardlinks(
            (work_root,), protected_roots=(protected,)
        )


def test_protected_implicit_name_does_not_count_as_writable_hardlink(tmp_path):
    project = tmp_path / "project"
    npm_logs = project / ".npm" / "_logs"
    work_root = tmp_path / "product"
    npm_logs.mkdir(parents=True)
    work_root.mkdir()
    protected = npm_logs / "protected.txt"
    protected.write_text("protected\n", encoding="utf-8")
    try:
        os.link(protected, work_root / "alias.txt")
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="observed 1 of 2 names",
    ):
        claude_launch_settings.validate_shell_writable_hardlinks(
            (work_root,),
            {"HOME": str(project)},
            protected_roots=(project,),
        )


def test_writable_root_alias_spellings_do_not_double_count_one_directory_entry():
    regular = mock.Mock(
        st_mode=stat.S_IFREG | 0o600,
        st_dev=7,
        st_ino=99,
        st_nlink=2,
    )
    parent = mock.Mock(st_dev=7, st_ino=42)

    def walk(root, **_kwargs):
        directory = "/work/sub" if root == "/work" else "/mnt/sub"
        return [(directory, [], ["alias.txt"])]

    with (
        mock.patch(
            "cli.claude_launch_settings._minimal_identity_roots",
            return_value=("/work", "/mnt/sub"),
        ),
        mock.patch("cli.claude_launch_settings.os.path.isdir", return_value=True),
        mock.patch("cli.claude_launch_settings.os.walk", side_effect=walk),
        mock.patch("cli.claude_launch_settings.os.stat", return_value=parent),
        mock.patch("cli.claude_launch_settings.os.lstat", return_value=regular),
        pytest.raises(
            claude_launch_settings.SettingsError,
            match="observed 1 of 2 names",
        ),
    ):
        claude_launch_settings.validate_writable_work_root_hardlinks(
            ("/work", "/mnt/sub")
        )


def test_implicit_shell_writable_root_rejects_external_hardlink(tmp_path):
    home = tmp_path / "operator-home"
    npm_logs = home / ".npm" / "_logs"
    outside = tmp_path / "outside"
    npm_logs.mkdir(parents=True)
    outside.mkdir()
    source = outside / "protected.txt"
    source.write_text("protected\n", encoding="utf-8")
    try:
        os.link(source, npm_logs / "alias.txt")
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="hard-link names outside the authorized root set",
    ):
        claude_launch_settings.validate_shell_writable_hardlinks(
            (), {"HOME": str(home)}
        )


def test_implicit_shell_writable_root_must_not_be_a_symlink(tmp_path):
    home = tmp_path / "operator-home"
    external_logs = tmp_path / "external-logs"
    npm = home / ".npm"
    npm.mkdir(parents=True)
    external_logs.mkdir()
    npm.joinpath("_logs").symlink_to(external_logs, target_is_directory=True)

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="must be a direct directory",
    ):
        claude_launch_settings.validate_shell_writable_hardlinks(
            (), {"HOME": str(home)}
        )


def test_linux_mount_boundary_rejects_exact_bind_alias_of_protected_tree(
    tmp_path,
):
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "1 0 8:1 / / rw - ext4 /dev/root rw\n"
        "2 1 8:1 /install /work rw - none /install rw\n",
        encoding="utf-8",
    )

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="filesystem coordinate overlaps a protected root",
    ):
        claude_launch_settings.validate_linux_mount_boundaries(
            ("/work",),
            ("/install/cli/claude_hook.py",),
            platform_name="linux",
            mountinfo_path=mountinfo,
        )


def test_linux_mount_boundary_rejects_nested_mount(tmp_path):
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "1 0 8:1 / / rw - ext4 /dev/root rw\n"
        "2 1 9:1 / /work/cache rw - tmpfs tmpfs rw\n",
        encoding="utf-8",
    )

    with pytest.raises(
        claude_launch_settings.SettingsError,
        match="nested mount.*inside /work",
    ):
        claude_launch_settings.validate_linux_mount_boundaries(
            ("/work",),
            ("/install",),
            platform_name="linux",
            mountinfo_path=mountinfo,
        )


def test_linux_mount_boundary_allows_disjoint_root_mount(tmp_path):
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "1 0 8:1 / / rw - ext4 /dev/root rw\n"
        "2 1 9:1 / /work rw - ext4 /dev/other rw\n",
        encoding="utf-8",
    )

    claude_launch_settings.validate_linux_mount_boundaries(
        ("/work",),
        ("/install",),
        platform_name="linux",
        mountinfo_path=mountinfo,
    )


def test_activated_posix_preflight_rejects_cross_boundary_work_root_hardlink(
    tmp_path,
):
    project = tmp_path / "project"
    work_root = tmp_path / "product"
    outside = tmp_path / "outside"
    project.mkdir()
    work_root.mkdir()
    outside.mkdir()
    source = outside / "protected.txt"
    source.write_text("protected\n", encoding="utf-8")
    try:
        os.link(source, work_root / "alias.txt")
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    findings = launch_preflight.environment_checks(
        "coder",
        _role(_claude_stub(tmp_path), ("write:worktree",)),
        {"product": str(work_root)},
        project_root=project,
        capabilities_activated=True,
    )

    assert "claude-shell-hardlink" in _codes(findings)


def test_activated_posix_preflight_rejects_registry_hardlink(tmp_path):
    project = tmp_path / "project"
    cartopian_home = tmp_path / "operator-home" / ".cartopian"
    outside = tmp_path / "outside"
    project.mkdir()
    cartopian_home.mkdir(parents=True)
    outside.mkdir()
    registry = cartopian_home / "projects.json"
    registry.write_text("[]\n", encoding="utf-8")
    try:
        os.link(registry, outside / "registry-alias.json")
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    environ = dict(os.environ)
    environ["HOME"] = str(cartopian_home.parent)

    findings = launch_preflight.environment_checks(
        "coder",
        _role(_claude_stub(tmp_path), ("write:worktree",)),
        {},
        project_root=project,
        capabilities_activated=True,
        environ=environ,
    )

    assert "claude-project-path-alias" in _codes(findings)


def test_activated_posix_claude_rejects_sandbox_glob_path(tmp_path):
    project = tmp_path / "project[1]"
    project.mkdir()
    agent = _claude_stub(tmp_path)

    with mock.patch(
        "cli.claude_launch_settings.probe_claude_version",
        return_value=((2, 1, 278), "2.1.278"),
    ):
        findings = launch_preflight.environment_checks(
            "coder",
            _role(agent, ("write:worktree",)),
            {},
            project_root=project,
            capabilities_activated=True,
        )

    assert "claude-sandbox-path" in _codes(findings)


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
def test_activated_posix_claude_rejects_nested_root_through_filesystem_alias(
    tmp_path, actual_name, alias_name
):
    project = tmp_path / actual_name
    nested = project / "product"
    nested.mkdir(parents=True)
    alias = tmp_path / alias_name
    try:
        same_project = os.path.samefile(project, alias)
    except OSError:
        same_project = False
    if not same_project:
        pytest.skip("test volume does not provide this alias behavior")
    agent = _claude_stub(tmp_path)

    with mock.patch(
        "cli.claude_launch_settings.probe_claude_version",
        return_value=((2, 1, 278), "2.1.278"),
    ):
        findings = launch_preflight.environment_checks(
            "coder",
            _role(agent, ("write:worktree",)),
            {"product": str(alias / "product")},
            project_root=project,
            capabilities_activated=True,
        )

    assert "claude-nested-work-root" in _codes(findings)


def test_nested_root_check_matches_rehearsal_and_dispatch_exemptions(tmp_path):
    project = tmp_path / "project"
    nested = project / "product"
    external = tmp_path / "external-product"
    nested.mkdir(parents=True)
    external.mkdir()
    agent = _claude_stub(tmp_path)
    coder = _role(agent, ("write:worktree",))
    reviewer = _role(agent, ())

    ungated = launch_preflight.environment_checks(
        "coder",
        coder,
        {"product": str(nested)},
        project_root=project,
        capabilities_activated=False,
    )
    no_write = launch_preflight.environment_checks(
        "reviewer",
        reviewer,
        {"product": str(nested)},
        project_root=project,
        capabilities_activated=True,
    )
    external_ok = launch_preflight.environment_checks(
        "coder",
        coder,
        {"product": str(external)},
        project_root=project,
        capabilities_activated=True,
    )
    rehearsal = dispatch_rehearsal._launch_record(
        "coder",
        coder,
        {"product": str(nested)},
        project_root=project,
        capabilities_activated=True,
    )

    assert "claude-nested-work-root" not in _codes(ungated)
    assert "claude-nested-work-root" not in _codes(no_write)
    assert "claude-nested-work-root" not in _codes(external_ok)
    assert rehearsal["ok"] is False
    assert rehearsal["code"] == "claude-nested-work-root"


def test_work_root_path_list_separator_refuses_lossy_wrapper_transport(tmp_path):
    root = tmp_path / "product:alias"
    root.mkdir()

    findings = launch_preflight.environment_checks(
        "coder",
        {},
        {"product": str(root)},
    )

    assert "work-root-path-list-separator" in _codes(findings)


def test_windows_work_root_separator_seam_uses_semicolon(tmp_path):
    root = tmp_path / "product;alias"
    root.mkdir()

    with mock.patch("cli.launch_preflight._running_on_windows", return_value=True):
        findings = launch_preflight.environment_checks(
            "coder",
            {},
            {"product": str(root)},
        )

    assert "work-root-path-list-separator" in _codes(findings)


def test_work_root_newline_cannot_truncate_to_an_undeclared_prefix(tmp_path):
    allowed_prefix = tmp_path / "allowed-prefix"
    allowed_prefix.mkdir()
    unsafe = str(allowed_prefix) + "\n/undeclared-suffix"

    findings = launch_preflight.environment_checks(
        "coder",
        {},
        {"product": unsafe},
    )

    assert "work-root-path-list-separator" in _codes(findings)
    assert any(repr(unsafe) in finding["message"] for finding in findings)


def test_native_windows_seam_does_not_apply_posix_nested_root_rule(tmp_path):
    project = tmp_path / "project"
    nested = project / "product"
    nested.mkdir(parents=True)
    agent = _windows_claude_stub(tmp_path)

    with (
        mock.patch("cli.launch_preflight._running_on_windows", return_value=True),
        mock.patch("cli.launch_preflight.shutil.which", return_value=str(agent)),
    ):
        findings = launch_preflight.environment_checks(
            "coder",
            _role(agent, ("write:worktree",)),
            {"product": str(nested)},
            project_root=project,
            capabilities_activated=True,
        )

    assert "claude-nested-work-root" not in _codes(findings)
    assert "claude-sandbox-host" in _codes(findings)


def test_native_windows_native_executable_receives_completion_hook_preflight(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    agent = _windows_claude_stub(tmp_path)
    config_dir = tmp_path / "claude-config"
    config_dir.mkdir()
    config_dir.joinpath("settings.json").write_text(
        json.dumps({"env": {"PYTHONPATH": str(tmp_path / "inject")}}),
        encoding="utf-8",
    )
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(config_dir)}

    with (
        mock.patch("cli.launch_preflight._running_on_windows", return_value=True),
        mock.patch("cli.launch_preflight.shutil.which", return_value=str(agent)),
        mock.patch(
            "cli.claude_launch_settings.resolve_claude_executable",
            return_value=r"C:\Program Files\Claude\claude.exe",
        ),
        mock.patch(
            "cli.claude_launch_settings.probe_claude_version",
            return_value=((2, 1, 278), "2.1.278"),
        ),
    ):
        findings = launch_preflight.environment_checks(
            "coder",
            _role(agent, ()),
            {},
            project_root=project,
            capabilities_activated=False,
            environ=env,
        )

    assert "claude-hook-env" in _codes(findings)


@pytest.mark.parametrize("suffix", ("cmd", "bat"))
def test_native_windows_completion_hook_rejects_batch_claude_shim(
    tmp_path, suffix
):
    project = tmp_path / "project"
    project.mkdir()
    agent = _windows_claude_stub(tmp_path)

    with (
        mock.patch("cli.launch_preflight._running_on_windows", return_value=True),
        mock.patch("cli.launch_preflight.shutil.which", return_value=str(agent)),
        mock.patch(
            "cli.claude_launch_settings.resolve_claude_executable",
            return_value=rf"C:\Program Files\Claude\claude.{suffix}",
        ),
    ):
        findings = launch_preflight.environment_checks(
            "coder",
            _role(agent, ()),
            {},
            project_root=project,
            capabilities_activated=False,
        )

    assert _codes(findings) == {"claude-executable-untrusted"}
    assert any(
        "require a native executable" in finding["message"]
        and ".cmd/.bat" in finding["message"]
        for finding in findings
    )


def test_ungated_cartopian_claude_basename_lookalike_stays_a_custom_agent(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    agent = tmp_path / "cartopian-claude"
    agent.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    agent.chmod(agent.stat().st_mode | stat.S_IXUSR)
    bindings: dict[str, str] = {}

    findings = launch_preflight.environment_checks(
        "coder",
        _role(agent, ()),
        {},
        project_root=project,
        capabilities_activated=False,
        launch_bindings=bindings,
    )

    assert findings == []
    assert bindings == {"agent": str(agent)}
    assert launch_preflight._cartopian_claude_install_root(str(agent)) is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink wrapper spelling")
def test_trusted_wrapper_symlink_launch_binding_uses_real_layout(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    link_dir = tmp_path / "path-bin"
    link_dir.mkdir()
    wrapper = _claude_stub(tmp_path)
    agent_link = link_dir / "cartopian-claude"
    agent_link.symlink_to(wrapper)
    bindings: dict[str, str] = {}

    findings = launch_preflight.environment_checks(
        "coder",
        _role(agent_link, ()),
        {},
        project_root=project,
        capabilities_activated=True,
        launch_bindings=bindings,
    )

    assert findings == []
    assert bindings["agent"] == str(wrapper.resolve())


def test_native_windows_activated_wrapper_refuses_unattested_host(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    agent = _windows_claude_stub(tmp_path)
    underlying_claude = r"C:\Program Files\Claude\claude.exe"

    def resolve(command, **_kwargs):
        return str(agent) if str(command) == str(agent) else underlying_claude

    with (
        mock.patch("cli.launch_preflight._running_on_windows", return_value=True),
        mock.patch("cli.launch_preflight.shutil.which", side_effect=resolve),
        mock.patch(
            "cli.claude_launch_settings.probe_claude_version",
            return_value=((2, 1, 277), "2.1.277"),
        ) as probe,
    ):
        findings = launch_preflight.environment_checks(
            "coder",
            _role(agent, ("write:worktree",)),
            {},
            project_root=project,
            capabilities_activated=True,
        )

    assert _codes(findings) == {"claude-sandbox-host"}
    assert any(
        "activated Claude containment is refused on native Windows"
        in finding["message"]
        for finding in findings
    )
    probe.assert_not_called()


def test_git_metadata_resolution_failure_is_a_shared_guard_finding(tmp_path):
    project = tmp_path / "project"
    project.joinpath(".git").mkdir(parents=True)
    agent = _claude_stub(tmp_path)

    with (
        mock.patch(
            "cli.claude_launch_settings.project_git_protected_roots",
            side_effect=claude_launch_settings.SettingsError(
                "cannot resolve governed-project Git metadata"
            ),
        ),
        mock.patch(
            "cli.claude_launch_settings.probe_claude_version",
            return_value=((2, 1, 278), "2.1.278"),
        ),
    ):
        findings = launch_preflight.environment_checks(
            "reviewer",
            _role(agent, ()),
            {},
            project_root=project,
            capabilities_activated=True,
        )

    assert "claude-git-metadata" in _codes(findings)


@pytest.mark.parametrize("activated", (False, True))
def test_normal_scope_hook_registration_is_excluded_only_when_activated(
    tmp_path, activated
):
    project = tmp_path / "project"
    project.mkdir()
    settings_path = project / ".claude" / "settings.json"
    settings_path.parent.mkdir()
    event = "PreToolUse" if activated else "Stop"
    script = "claude_hook.py" if activated else "claude_stop_hook.py"
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    event: [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python /old/install/cli/{script}",
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    agent = _claude_stub(tmp_path)
    role = _role(agent, ("write:worktree",) if activated else ())

    with mock.patch(
        "cli.claude_launch_settings.probe_claude_version",
        return_value=((2, 1, 278), "2.1.278"),
    ):
        launch = dispatch_rehearsal._launch_record(
            "coder",
            role,
            {},
            project_root=project,
            capabilities_activated=activated,
        )

    if activated:
        assert launch["ok"] is True
    else:
        assert launch["ok"] is False
        assert launch["code"] == "claude-legacy-hook"
        assert "--claude-hook" in launch["detail"]


def test_activated_normal_scope_excluded_command_is_ignored(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    agent = _claude_stub(tmp_path)
    config_dir = tmp_path / "claude-config"
    config_dir.mkdir()
    config_dir.joinpath("settings.json").write_text(
        json.dumps({"sandbox": {"excludedCommands": ["python *"]}}),
        encoding="utf-8",
    )
    role = _role(agent, ("write:worktree",))
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(config_dir)}

    findings = launch_preflight.environment_checks(
        "coder",
        role,
        {},
        project_root=project,
        capabilities_activated=True,
        environ=env,
    )
    with mock.patch.dict(os.environ, env, clear=True):
        rehearsal = dispatch_rehearsal._launch_record(
            "coder",
            role,
            {},
            project_root=project,
            capabilities_activated=True,
        )

    assert "claude-sandbox-excluded-command" not in _codes(findings)
    assert rehearsal["ok"] is True


def test_legacy_global_sandbox_field_is_inert_for_activated_launch(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    agent = _claude_stub(tmp_path)
    config_dir = tmp_path / "claude-config"
    config_dir.mkdir()
    config_dir.joinpath(".claude.json").write_text(
        json.dumps({"sandbox": {"excludedCommands": ["python *"]}}),
        encoding="utf-8",
    )
    role = _role(agent, ("write:worktree",))
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(config_dir)}

    findings = launch_preflight.environment_checks(
        "coder",
        role,
        {},
        project_root=project,
        capabilities_activated=True,
        environ=env,
    )
    with mock.patch.dict(os.environ, env, clear=True):
        rehearsal = dispatch_rehearsal._launch_record(
            "coder",
            role,
            {},
            project_root=project,
            capabilities_activated=True,
        )

    assert "claude-sandbox-excluded-command" not in _codes(findings)
    assert rehearsal["ok"] is True


def test_wsl_host_refusal_matches_shared_preflight(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    agent = _claude_stub(tmp_path)
    env = {**os.environ, "WSL_DISTRO_NAME": "Ubuntu"}

    with mock.patch(
        "cli.claude_launch_settings.probe_claude_version",
        return_value=((2, 1, 278), "2.1.278"),
    ):
        findings = launch_preflight.environment_checks(
            "coder",
            _role(agent, ("write:worktree",)),
            {},
            project_root=project,
            capabilities_activated=True,
            environ=env,
        )

    assert "claude-sandbox-host" in _codes(findings)


@pytest.mark.parametrize("activated", (False, True))
def test_normal_scope_hook_environment_is_excluded_only_when_activated(
    tmp_path, activated
):
    project = tmp_path / "project"
    project.mkdir()
    agent = _claude_stub(tmp_path)
    config_dir = tmp_path / "claude-config"
    config_dir.mkdir()
    config_dir.joinpath("settings.json").write_text(
        json.dumps({"env": {"GCONV_PATH": str(tmp_path / "modules")}}),
        encoding="utf-8",
    )
    role = _role(agent, ("write:worktree",) if activated else ())
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(config_dir)}

    with mock.patch(
        "cli.claude_launch_settings.probe_claude_version",
        return_value=((2, 1, 278), "2.1.278"),
    ):
        findings = launch_preflight.environment_checks(
            "coder",
            role,
            {},
            project_root=project,
            capabilities_activated=activated,
            environ=env,
        )

    assert ("claude-hook-env" in _codes(findings)) is (not activated)


def test_legacy_global_hook_environment_refuses_shared_preflight(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    agent = _claude_stub(tmp_path)
    config_dir = tmp_path / "claude-config"
    config_dir.mkdir()
    config_dir.joinpath(".claude.json").write_text(
        json.dumps({"env": {"GCONV_PATH": str(tmp_path / "modules")}}),
        encoding="utf-8",
    )
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(config_dir)}

    with mock.patch(
        "cli.claude_launch_settings.probe_claude_version",
        return_value=((2, 1, 278), "2.1.278"),
    ):
        findings = launch_preflight.environment_checks(
            "coder",
            _role(agent, ("write:worktree",)),
            {},
            project_root=project,
            capabilities_activated=True,
            environ=env,
        )

    assert "claude-hook-env" in _codes(findings)


@pytest.mark.parametrize(
    "unsafe_env",
    (
        {"CARTOPIAN_CLAUDE_BARE": "true"},
        {"CLAUDE_CODE_SIMPLE": "1"},
        {"CLAUDE_CODE_SAFE_MODE": "1"},
    ),
)
def test_inherited_hook_suppression_refuses_shared_preflight(tmp_path, unsafe_env):
    project = tmp_path / "project"
    project.mkdir()
    agent = _claude_stub(tmp_path)
    env = {**os.environ, **unsafe_env}

    with mock.patch(
        "cli.claude_launch_settings.probe_claude_version",
        return_value=((2, 1, 278), "2.1.278"),
    ):
        findings = launch_preflight.environment_checks(
            "coder",
            _role(agent, ("write:worktree",)),
            {},
            project_root=project,
            capabilities_activated=True,
            environ=env,
        )

    assert "claude-hook-env" in _codes(findings)
