"""Operator-owned per-role exceptions to the activated Claude shell sandbox."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from cli import claude_launch_settings
from cli.config_schema import ConfigDiagnostic, resolve_configuration

REPO_ROOT = Path(__file__).resolve().parents[2]

_PROJECT_HEADER = (
    "[project]\n"
    'id = "demo"\n'
    'name = "Demo"\n'
    'project_schema_version = "v0.14.0"\n'
    'work_roots = ["product"]\n\n'
)
_ROLES = (
    "[roles.coder]\n"
    'description = "Implements tasks."\n'
    'grants = ["coder-like"]\n\n'
    "[roles.reviewer]\n"
    'description = "Reviews tasks."\n'
    'grants = ["reviewer-like"]\n\n'
)


def _project(tmp_path: Path, sandbox: str = "") -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    work_root = tmp_path / "product"
    home = tmp_path / "home"
    for directory in (project / "prompts", project / "reports", work_root):
        directory.mkdir(parents=True)
    registry = home / ".cartopian" / "projects.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(f'[{{"id": "demo", "path": "{project}"}}]', encoding="utf-8")
    project.joinpath("cartopian.toml").write_text(
        _PROJECT_HEADER + _ROLES + sandbox, encoding="utf-8"
    )
    project.joinpath("cartopian.local.toml").write_text(
        f'[work_roots]\nproduct = "{work_root}"\n', encoding="utf-8"
    )
    return project, work_root, home


def _sandbox(project: Path, home: Path, role: str) -> dict:
    return claude_launch_settings.build_settings(
        REPO_ROOT,
        windows=False,
        project_dir=project,
        include_capability=True,
        environ={"CARTOPIAN_ROLE": role, "HOME": str(home)},
    )["sandbox"]


def test_strict_defaults_without_a_sandbox_table(tmp_path):
    project, _work_root, home = _project(tmp_path)
    sandbox = _sandbox(project, home, "coder")
    assert sandbox["allowUnsandboxedCommands"] is False
    assert sandbox["failIfUnavailable"] is True
    assert sandbox["network"] == {
        "allowUnixSockets": [],
        "allowAllUnixSockets": False,
        "allowMachLookup": [],
    }


def test_declared_exceptions_apply_only_to_their_role(tmp_path):
    cache = tmp_path / "home" / "go" / "pkg" / "mod"
    cache.mkdir(parents=True)
    project, work_root, home = _project(
        tmp_path,
        "[roles.coder.sandbox]\n"
        "allow_local_binding = true\n"
        'allowed_domains = ["proxy.golang.org", "*.example.com"]\n'
        'writable_paths = ["~/go/pkg/mod"]\n',
    )
    coder = _sandbox(project, home, "coder")
    assert coder["network"]["allowLocalBinding"] is True
    assert coder["network"]["allowedDomains"] == ["proxy.golang.org", "*.example.com"]
    assert coder["network"]["allowAllUnixSockets"] is False
    assert coder["filesystem"]["allowWrite"] == [
        str(work_root.resolve()),
        str(cache.resolve()),
    ]
    # The strict policy is otherwise unchanged.
    assert str(project.resolve()) in coder["filesystem"]["denyWrite"]
    assert coder["allowUnsandboxedCommands"] is False

    reviewer = _sandbox(project, home, "reviewer")
    assert "allowLocalBinding" not in reviewer["network"]
    assert "allowedDomains" not in reviewer["network"]
    assert "allowWrite" not in reviewer["filesystem"]


def test_unix_socket_exception_is_explicit(tmp_path):
    project, _work_root, home = _project(
        tmp_path, "[roles.coder.sandbox]\nallow_unix_sockets = true\n"
    )
    assert _sandbox(project, home, "coder")["network"]["allowAllUnixSockets"] is True
    assert _sandbox(project, home, "reviewer")["network"]["allowAllUnixSockets"] is False


def test_writable_path_must_exist(tmp_path):
    project, _work_root, home = _project(
        tmp_path, '[roles.coder.sandbox]\nwritable_paths = ["~/missing-cache"]\n'
    )
    with pytest.raises(claude_launch_settings.SettingsError, match="must exist"):
        _sandbox(project, home, "coder")


def test_writable_path_symlink_is_refused(tmp_path):
    real = tmp_path / "real-cache"
    real.mkdir()
    (tmp_path / "home").mkdir()
    os.symlink(real, tmp_path / "home" / "cache")
    project, _work_root, home = _project(
        tmp_path, '[roles.coder.sandbox]\nwritable_paths = ["~/cache"]\n'
    )
    with pytest.raises(claude_launch_settings.SettingsError, match="direct directory|symlink"):
        _sandbox(project, home, "coder")


@pytest.mark.parametrize("relative", ["project", "project/reports", "product", "home/.cartopian"])
def test_writable_path_overlapping_a_governed_root_is_refused(tmp_path, relative):
    project, _work_root, home = _project(tmp_path)
    target = tmp_path / relative
    target.mkdir(parents=True, exist_ok=True)
    project.joinpath("cartopian.toml").write_text(
        _PROJECT_HEADER
        + _ROLES
        + f'[roles.coder.sandbox]\nwritable_paths = ["{target}"]\n',
        encoding="utf-8",
    )
    with pytest.raises(claude_launch_settings.SettingsError, match="overlaps"):
        _sandbox(project, home, "coder")


def test_writable_path_enclosing_the_project_is_refused(tmp_path):
    project, _work_root, home = _project(tmp_path)
    project.joinpath("cartopian.toml").write_text(
        _PROJECT_HEADER
        + _ROLES
        + f'[roles.coder.sandbox]\nwritable_paths = ["{tmp_path}"]\n',
        encoding="utf-8",
    )
    with pytest.raises(claude_launch_settings.SettingsError, match="overlaps"):
        _sandbox(project, home, "coder")


def _resolve(sandbox: dict, role: str = "coder") -> dict:
    project = {
        "project": {"id": "x", "name": "X", "project_schema_version": "v1.0.0"},
        "roles": {role: {"description": "d", "grants": ["coder-like"], "sandbox": sandbox}},
    }
    return resolve_configuration({}, project, {})


@pytest.mark.parametrize(
    "sandbox",
    [
        {"allow_local_binding": "yes"},
        {"allowed_domains": ["https://proxy.golang.org"]},
        {"allowed_domains": ["proxy.golang.org:443"]},
        {"allowed_domains": ["Proxy.Golang.org"]},
        {"allowed_domains": ["a.com", "a.com"]},
        {"writable_paths": ["relative/cache"]},
        {"writable_paths": ["~"]},
        {"writable_paths": ["/"]},
        {"writable_paths": ["~/a/../b"]},
        {"writable_paths": ["~/cache/"]},
        {"writable_paths": ["~/cache/*"]},
        {"unknown": True},
    ],
)
def test_schema_refuses_malformed_exceptions(sandbox):
    with pytest.raises(ConfigDiagnostic):
        _resolve(sandbox)


def test_pm_cannot_declare_sandbox_exceptions():
    with pytest.raises(ConfigDiagnostic, match="pm-sandbox-forbidden"):
        _resolve({"allow_local_binding": True}, role="pm")


def test_resolved_projection_is_null_until_declared():
    resolved = _resolve({})
    assert resolved["roles"]["coder"]["sandbox"] is None
    assert "sandbox" not in resolved["roles"]["coder"]["attribution"]
    resolved = _resolve({"allow_local_binding": True})
    assert resolved["roles"]["coder"]["sandbox"] == {"allow_local_binding": True}
    assert resolved["roles"]["coder"]["attribution"]["sandbox"] == {
        "allow_local_binding": "project"
    }
