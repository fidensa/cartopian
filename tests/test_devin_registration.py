"""Unit coverage for Devin for Terminal's registration target.

Devin 3000.x keeps user-level MCP servers in ``mcp_config.json`` and migrates
any ``mcpServers`` out of the legacy ``config.json`` on first launch, leaving
only settings behind. These tests pin which file the resolver targets, that a
migrated registration reads as current, that detection still sees a Devin
whose only file is the legacy settings file, and that a write lands in the
store Devin loads while the settings file stays untouched.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cli.install_workflow import (
    _client_config_path,
    _devin_config_dir,
    _registration_candidate_paths,
    _registration_state,
    _write_json_registration,
    plan_workflow,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED = "/install/bin/cartopian-mcp"
SETTINGS = {"devin": {"org_id": "org"}, "theme_mode": "dark", "version": 1}


@pytest.fixture(autouse=True)
def isolated_appdata(monkeypatch):
    # On Windows the Devin directory follows APPDATA; without it the resolver
    # falls back beneath the test home.
    monkeypatch.delenv("APPDATA", raising=False)


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "home"
    _devin_config_dir(home).mkdir(parents=True)
    return home


def _files(home):
    base = _devin_config_dir(home)
    return base / "mcp_config.json", base / "config.json"


def _write(path, data):
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")


def test_fresh_home_targets_the_mcp_store(home):
    mcp_config, _legacy = _files(home)
    assert _client_config_path("devin", home) == mcp_config


def test_unmigrated_legacy_servers_keep_the_legacy_target(home):
    _mcp_config, legacy = _files(home)
    _write(legacy, {**SETTINGS, "mcpServers": {}})
    assert _client_config_path("devin", home) == legacy


def test_settings_only_legacy_file_targets_the_mcp_store(home):
    mcp_config, legacy = _files(home)
    _write(legacy, SETTINGS)
    assert _client_config_path("devin", home) == mcp_config


def test_existing_mcp_store_wins_over_legacy_servers(home):
    mcp_config, legacy = _files(home)
    _write(legacy, {**SETTINGS, "mcpServers": {}})
    _write(mcp_config, {"mcpServers": {}})
    assert _client_config_path("devin", home) == mcp_config


def test_both_files_are_registration_candidates(home):
    assert _registration_candidate_paths("devin", home) == _files(home)


def test_migrated_registration_reads_current(home):
    """Devin moved the entry into ``mcp_config.json``; that is not drift."""
    mcp_config, legacy = _files(home)
    _write(legacy, SETTINGS)
    _write(mcp_config, {"mcpServers": {"cartopian": {"command": EXPECTED}}})
    assert _registration_state("devin", home, EXPECTED) == (
        "current",
        "expected-command",
    )


def test_write_lands_in_the_mcp_store_and_preserves_settings(home):
    mcp_config, legacy = _files(home)
    _write(legacy, SETTINGS)
    before = legacy.read_bytes()
    _write_json_registration("devin", home, EXPECTED)
    assert json.loads(mcp_config.read_text(encoding="utf-8")) == {
        "mcpServers": {"cartopian": {"command": EXPECTED}}
    }
    assert legacy.read_bytes() == before


def test_settings_only_legacy_file_is_still_detected(home, tmp_path, monkeypatch):
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setenv("PATH", os.pathsep.join([str(empty), "/usr/bin", "/bin"]))
    _mcp_config, legacy = _files(home)
    _write(legacy, SETTINGS)
    plan = plan_workflow(
        source_root=REPO_ROOT,
        install_root=tmp_path / ".cartopian",
        operation="fresh-install",
        client_home=home,
    )
    assert "devin" in plan["internal"]["clients"]
    assert plan["internal"]["registration_observations"]["devin"]["state"] == (
        "missing"
    )
