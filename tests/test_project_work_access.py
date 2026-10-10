"""Shared access contract and adapter conformance; real wrappers, not argv grants."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from cli import claude_hook, claude_launch_settings, dispatch_rehearsal, launch_preflight
from cli.commands.resolve_config import resolve_project_configuration
from cli.commands.report_action import _task_declares_work_roots
from cli.config_schema import ConfigDiagnostic, resolve_configuration
from cli.work_access import WorkAccessError, adapter_problem, effective_access, wrapper_preflight

REPO = Path(__file__).resolve().parents[1]
ADAPTERS = ("claude", "codex", "agy", "devin", "opencode", "hermes")


def project_fixture(tmp_path, *, configured=False):
    project = (tmp_path / "project").resolve()
    work = project / "resources"
    if configured:
        work /= "spikes/packaging-comparison"
    home = tmp_path / "home"
    for path in (work, project / "prompts", project / "reports", home / ".cartopian"):
        path.mkdir(parents=True)
    (home / ".cartopian/projects.json").write_text("[]")
    header = '[project]\nid="demo"\nname="Demo"\nproject_schema_version="v0.14.0"\n'
    if configured:
        header += 'work_roots=["work"]\n'
        (project / "cartopian.local.toml").write_text('[work_roots]\nwork=' + json.dumps(str(work)) + '\n')
    (project / "cartopian.toml").write_text(header + '[roles.worker]\ndescription="Builds project content"\ngrants=["coder-like"]\n[roles.reader]\ndescription="Reads project content"\ngrants=["read:work-roots"]\n[roles.none]\ndescription="No access"\ngrants=[]\n')
    (project / "prompts/PROMPT-01-999.md").write_text("Do the task")
    return project, work, home


@pytest.mark.parametrize("configured", (False, True))
def test_file_backed_resolution_and_hook_agree(tmp_path, configured):
    project, work, home = project_fixture(tmp_path, configured=configured)
    with mock.patch("cli.commands.resolve_config.Path.home", return_value=home):
        resolved = resolve_project_configuration(project)
    grants, roots = claude_hook._resolve_project_grants(project, home / ".cartopian")
    assert list(roots.values()) == [str(work)] == list(resolved["work_roots"].values())
    access = effective_access(project, roots, grants.grants_for(("worker",)), activated=grants.activated)
    assert access.launch_cwd == str(work)
    assert access.writable == (str(work),)


@pytest.mark.parametrize("grants,activated,writable", [(["coder-like"], False, False), (["write:worktree"], True, False), (["read:work-roots"], True, False), (["read:work-roots", "write:worktree"], True, True), ([], True, False)])
def test_writing_requires_explicit_read_and_write_grants(tmp_path, grants, activated, writable):
    project, work, _home = project_fixture(tmp_path)
    access = effective_access(project, {"resources": str(work)}, grants, activated=activated)
    assert bool(access.writable) is writable


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.parametrize("platform", ("darwin", "linux", "win32"))
def test_all_adapters_use_same_contract_or_refuse(tmp_path, adapter, platform):
    project, work, _home = project_fixture(tmp_path)
    access = effective_access(project, {"resources": str(work)}, ["read:work-roots", "write:worktree"], activated=True)
    problem = adapter_problem(adapter, platform, access, activated=True)
    assert (problem is None) == (platform == "darwin")
    if problem:
        assert "No unsandboxed fallback" in problem
        assert "verified native containment backend" in problem


@pytest.mark.parametrize("adapter", ADAPTERS[1:])
def test_real_posix_wrappers_refuse_unbound_cwd_before_any_agent_probe(tmp_path, adapter):
    project, work, home = project_fixture(tmp_path)
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    marker = tmp_path / "agent-started"
    executable = fakebin / {"agy": "agy"}.get(adapter, adapter)
    executable.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 0\n')
    executable.chmod(0o755)
    result = subprocess.run(["/bin/bash", str(REPO / "wrappers/bin" / ("cartopian-" + adapter)), str(project / "prompts/PROMPT-01-999.md")], capture_output=True, text=True, env={"PATH": str(fakebin) + ":/usr/bin:/bin", "HOME": str(home), "CARTOPIAN_ROLE": "worker", "CARTOPIAN_PROJECT_ROOT": str(project), "CARTOPIAN_LAUNCH_CWD": str(project), "CARTOPIAN_PYTHON": sys.executable}, timeout=20)
    assert result.returncode != 0
    assert "launch cwd" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_undeclared_grants_refuse_before_any_agent_probe(tmp_path, adapter):
    project, work, home = project_fixture(tmp_path)
    (project / "cartopian.toml").write_text('[project]\nid="demo"\nname="Demo"\nproject_schema_version="v0.14.0"\n[roles.coder]\ndescription="Implements tasks"\n')
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    marker = tmp_path / "agent-started"
    executable = fakebin / adapter
    executable.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 0\n')
    executable.chmod(0o755)
    result = subprocess.run(["/bin/bash", str(REPO / "wrappers/bin" / ("cartopian-" + adapter)), str(project / "prompts/PROMPT-01-999.md")], capture_output=True, text=True, env={"PATH": str(fakebin) + ":/usr/bin:/bin", "HOME": str(home), "CARTOPIAN_ROLE": "coder", "CARTOPIAN_PROJECT_ROOT": str(project), "CARTOPIAN_LAUNCH_CWD": str(work), "CARTOPIAN_PYTHON": sys.executable}, timeout=20)
    assert result.returncode != 0
    assert "require explicit role grants" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_powershell_shims_share_preflight_before_agent_discovery(adapter):
    source = (REPO / "wrappers/ps1" / ("cartopian-" + adapter + ".ps1")).read_text()
    assert "CartopianWorkAccess.ps1" in source
    assert source.index("& $WorkAccessHelper") < source.index("Get-Command")
    shim = (REPO / "wrappers/ps1" / ("cartopian-" + adapter + ".cmd")).read_text()
    assert "cartopian-" + adapter + ".ps1" in shim


@pytest.mark.parametrize("relative", (".", "tasks/work", "specs/work", "reports/work", "prompts/work", "plans/work", "requirements/work", "standards/work", ".git/work", ".requests/work", "STATE.md/work"))
def test_governance_cannot_be_reclassified_as_work(tmp_path, relative):
    project, _work, _home = project_fixture(tmp_path)
    with pytest.raises(WorkAccessError, match="project root|governance"):
        effective_access(project, {"bad": str(project / relative)}, ["write:worktree"], activated=True)


@pytest.mark.parametrize("role", ("worker", "reader", "none"))
@pytest.mark.skipif(sys.platform != "darwin", reason="native macOS policy")
def test_claude_policy_preserves_role_boundary_and_pins_ancestors(tmp_path, role):
    project, work, home = project_fixture(tmp_path, configured=True)
    writer = role == "worker"
    settings = claude_launch_settings.build_settings(REPO, windows=False, project_dir=project, include_capability=True, environ={"HOME": str(home), "CARTOPIAN_ROLE": role, "CARTOPIAN_LAUNCH_CWD": str(work if writer else project)})
    fs = settings["sandbox"]["filesystem"]
    if writer:
        assert str(project) not in fs["denyWrite"]
        assert str(work) in fs["allowWrite"]
        assert str(work / ".cartopian-work-root-boundary") in fs["denyWrite"]
        args = settings["hooks"]["PreToolUse"][0]["hooks"][0]["args"]
        assert args[args.index("--shell-cwd") + 1] == str(work)
        assert "Bash" in settings["hooks"]["PreToolUse"][0]["matcher"]
    else:
        assert str(project) in fs["denyWrite"]
        assert str(work) in fs["denyWrite"]
    if role == "none":
        assert str(work) in fs["denyRead"]


def test_bash_cwd_drift_and_identity_replacement_refuse(tmp_path):
    project, work, home = project_fixture(tmp_path)
    info = work.stat()
    captured = {"resources": (str(work), info.st_dev, info.st_ino)}
    env = {"CARTOPIAN_ROLE": "worker", "CARTOPIAN_LAUNCH_CWD": str(project)}
    payload = {"tool_name": "Bash", "cwd": str(work), "tool_input": {"command": "pwd"}}
    assert claude_hook.evaluate(payload, environ=env, cartopian_home=home / ".cartopian", bound_work_roots=captured, shell_cwd=str(work)).action == "allow"
    payload["cwd"] = str(project)
    assert "shell cwd changed" in claude_hook.evaluate(payload, environ=env, cartopian_home=home / ".cartopian", bound_work_roots=captured, shell_cwd=str(work)).reason
    payload["cwd"] = str(work)
    work.rename(project / "old-resources")
    work.mkdir()
    assert "identity changed" in claude_hook.evaluate(payload, environ=env, cartopian_home=home / ".cartopian", bound_work_roots=captured, shell_cwd=str(work)).reason


@pytest.mark.skipif(sys.platform != "darwin", reason="native macOS policy")
def test_linked_inputs_do_not_bypass_governance_and_internal_links_are_supported(tmp_path):
    project, work, home = project_fixture(tmp_path)
    (work / "data").write_text("data")
    os.link(work / "data", work / "internal-link")
    (work / "venv-python").symlink_to(sys.executable)
    env = {"HOME": str(home), "CARTOPIAN_ROLE": "worker", "CARTOPIAN_LAUNCH_CWD": str(work)}
    claude_launch_settings.build_settings(REPO, windows=False, project_dir=project, include_capability=True, environ=env)
    (project / "STATE.md").write_text("protected")
    os.link(project / "STATE.md", work / "escape")
    with pytest.raises(Exception, match="multiply-linked|hard.link"):
        claude_launch_settings.build_settings(REPO, windows=False, project_dir=project, include_capability=True, environ=env)


def test_external_work_root_contract_is_unchanged(tmp_path):
    project, _work, _home = project_fixture(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    access = effective_access(project, {"product": str(external)}, [], activated=False)
    assert not access.contained
    for adapter in ADAPTERS:
        assert adapter_problem(adapter, "win32", access, activated=False) is None


@pytest.mark.parametrize("configured", (False, True))
@pytest.mark.parametrize("adapter", ADAPTERS[1:])
def test_rehearsal_and_preflight_refuse_same_unavailable_backend(tmp_path, configured, adapter, monkeypatch):
    monkeypatch.setattr("cli.native_work_sandbox.check_substrate", lambda access: (_ for _ in ()).throw(WorkAccessError("native containment backend unavailable")))
    project, work, _home = project_fixture(tmp_path, configured=configured)
    record = {"launch": {"agent": str(REPO / "wrappers/bin" / ("cartopian-" + adapter))}, "effective_grants": ["read:work-roots", "write:worktree"], "auto_launch": ["task_run"]}
    roots = {"work" if configured else "resources": str(work)}
    findings = launch_preflight.environment_checks("worker", record, roots, project_root=project, capabilities_activated=True)
    assert findings[0]["code"] == "work-root-policy-invalid"
    for mode in ("auto", "native-interactive"):
        launch = dispatch_rehearsal._launch_record("worker", record, roots, project_root=project, capabilities_activated=True, task_launch_mode=mode)
        assert not launch["ok"]
        assert findings[0]["message"] in launch["detail"]


@pytest.mark.parametrize("override", ("cwd", "roots"))
def test_wrapper_refuses_permission_transport_overrides(tmp_path, override):
    project, work, home = project_fixture(tmp_path)
    env = {"HOME": str(home), "CARTOPIAN_ROLE": "worker", "CARTOPIAN_LAUNCH_CWD": str(work), "CARTOPIAN_WORK_ROOTS": str(work)}
    if override == "cwd":
        env["CARTOPIAN_LAUNCH_CWD"] = str(project)
    else:
        env["CARTOPIAN_WORK_ROOTS"] += os.pathsep + str(project)
    with pytest.raises(WorkAccessError, match="cwd bound|does not match configured"):
        wrapper_preflight("claude", project, platform="darwin", environ=env)


@pytest.mark.parametrize("configured", (False, True))
@pytest.mark.skipif(sys.platform != "darwin", reason="native macOS preflight")
def test_supported_preflight_and_wrapper_resolve_same_launch(tmp_path, configured):
    project, work, home = project_fixture(tmp_path, configured=configured)
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    claude = fakebin / "claude"
    claude.write_text("#!/bin/sh\nprintf '2.1.295 (Claude Code)\\n'\n")
    claude.chmod(0o755)
    env = {"HOME": str(home), "PATH": str(fakebin) + os.pathsep + os.environ["PATH"], "CARTOPIAN_ROLE": "worker", "CARTOPIAN_LAUNCH_CWD": str(work), "CARTOPIAN_WORK_ROOTS": str(work)}
    record = {"launch": {"agent": str(REPO / "wrappers/bin/cartopian-claude")}, "effective_grants": ["read:work-roots", "write:worktree"]}
    roots = {"work" if configured else "resources": str(work)}
    bindings = {}
    with mock.patch.dict(os.environ, env):
        findings = launch_preflight.environment_checks("worker", record, roots, project_root=project, capabilities_activated=True, environ=env, launch_bindings=bindings)
        access = wrapper_preflight("claude", project, environ=env)
    assert findings == []
    assert access.launch_cwd == str(work)
    assert bindings["claude_executable"] == str(claude.resolve())


def test_composed_prompt_names_contained_launch_directory(tmp_path):
    from cli.prompt_composer import compose
    from tests.cli.commands.test_compose_assignment_prompt import _build_minimal, _TOML_REVIEW_OFF
    from tests.scaffold import project_scaffold

    with project_scaffold(cartopian_toml=_TOML_REVIEW_OFF) as scaffold:
        task = _build_minimal(scaffold)
        config = scaffold.config.read_text().replace('[roles.coder]\n', '[roles.coder]\ngrants=["coder-like"]\n')
        scaffold.config.write_text(config)
        work = (scaffold.project_root / "resources").resolve()
        scaffold.write("cartopian.local.toml", '[work_roots]\ntool-repo=' + json.dumps(str(work)) + '\n')
        with mock.patch.dict(os.environ, {"HOME": str(tmp_path)}):
            result = compose(task, "coder")
        assert "Launch working directory: " + str(work) in result["assignee_prompt"]
        assert "publication remains mediated" in result["assignee_prompt"]


def test_mediated_config_validation_cannot_map_governance_as_content(tmp_path):
    project, _work, _home = project_fixture(tmp_path)
    with pytest.raises(ConfigDiagnostic, match="protected-work-root"):
        resolve_configuration({}, {"project": {"id": "test", "name": "Test", "project_schema_version": "v0.14.0", "work_roots": ["work"]}}, {"work_roots": {"work": str(project / "specs")}}, project_root=project)


def test_mediated_mapping_writer_accepts_supporting_sibling_and_refuses_governance(tmp_path, capsys):
    from cli.main import main

    project, _work, home = project_fixture(tmp_path, configured=True)
    sibling = project / "support"
    sibling.mkdir()
    mapping = project / "cartopian.local.toml"
    with mock.patch.dict(os.environ, {"HOME": str(home)}):
        assert main(["update-config", str(project), "--local", "--set-work-root", "work=" + str(sibling)]) == 0
        before = mapping.read_bytes()
        assert main(["update-config", str(project), "--local", "--set-work-root", "work=" + str(project / "plans")]) != 0
    assert mapping.read_bytes() == before
    assert "protected-work-root" in capsys.readouterr().err


def test_default_root_cannot_be_redirected_through_link(tmp_path):
    project, work, home = project_fixture(tmp_path)
    work.rmdir()
    work.symlink_to(home, target_is_directory=True)
    with mock.patch.dict(os.environ, {"HOME": str(home)}), pytest.raises(Exception, match="escapes the project"):
        resolve_project_configuration(project)


@pytest.mark.parametrize("kind", ("file", "directory", "symlink"))
def test_reserved_boundary_entry_cannot_redirect_ancestor_protection(tmp_path, kind):
    project, work, home = project_fixture(tmp_path)
    marker = work / ".cartopian-work-root-boundary"
    if kind == "file":
        marker.write_text("untrusted content")
    elif kind == "directory":
        marker.mkdir()
    else:
        marker.symlink_to(home)
    with pytest.raises(WorkAccessError, match="reserved.*must be absent"):
        effective_access(project, {"resources": str(work)}, ["read:work-roots", "write:worktree"], activated=True)


def test_contained_content_never_requires_product_git_workflow(tmp_path):
    project, _work, _home = project_fixture(tmp_path)
    assert not _task_declares_work_roots("Work root: resources\n", project)
    external = tmp_path / "external"
    external.mkdir()
    (project / "cartopian.toml").write_text('[project]\nwork_roots=["product"]\n')
    (project / "cartopian.local.toml").write_text('[work_roots]\nproduct=' + json.dumps(str(external)) + '\n')
    assert _task_declares_work_roots("Work root: product\n", project)


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_cwd_override_cannot_hide_project_from_posix_gate(tmp_path, adapter):
    project, work, home = project_fixture(tmp_path)
    result = subprocess.run(["/bin/bash", str(REPO / "wrappers/bin" / ("cartopian-" + adapter)), str(project / "prompts/PROMPT-01-999.md")], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "HOME": str(home), "CARTOPIAN_LAUNCH_CWD": str(work)}, timeout=20)
    assert result.returncode != 0
    assert "dispatch-bound CARTOPIAN_PYTHON" in result.stderr


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell unavailable; source parity is separate")
def test_real_powershell_wrapper_refuses_contained_access(tmp_path, adapter):
    project, work, home = project_fixture(tmp_path)
    result = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-File", str(REPO / "wrappers/ps1" / ("cartopian-" + adapter + ".ps1")), str(project / "prompts/PROMPT-01-999.md")], capture_output=True, text=True, env={**os.environ, "HOME": str(home), "CARTOPIAN_ROLE": "worker", "CARTOPIAN_PROJECT_ROOT": str(project), "CARTOPIAN_LAUNCH_CWD": str(project), "CARTOPIAN_PYTHON": sys.executable}, timeout=20)
    assert result.returncode != 0
    assert "launch cwd" in result.stderr
