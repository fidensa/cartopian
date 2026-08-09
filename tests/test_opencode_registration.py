"""Unit coverage for the `opencode-json` registration format.

opencode's config schema is a genuine third format (top-level `mcp` key, a
required `type` discriminator, an array `command`), its config location is
environment-driven, and a directory target is a candidate *pair* whose
later-loaded member (`opencode.jsonc`) wins same-key conflicts. These tests pin
the precedence-safe write algorithm (D4), the fail-closed shadow-aware reader,
the D8 installation-target resolver, and the operator-file preservation
guarantees (byte-identical refusals; symlink/hardlink/non-regular targets
inherited unchanged from `_validate_operator_config_target`).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cli.install_workflow import (
    _CLIENTS,
    _REGISTRATION_ADAPTERS,
    OPENCODE_REGISTRATION_TIMEOUT_MS,
    SUPPORTED_CLIENTS,
    WorkflowRefusal,
    _client_config_path,
    _merge_opencode_registration,
    _opencode_config_path,
    _opencode_install_target,
    _opencode_registration,
    _opencode_target_candidates,
)

EXPECTED = "/install/bin/cartopian-mcp"

EXPECTED_ENTRY = {
    "type": "local",
    "command": [EXPECTED],
    "enabled": True,
    "timeout": OPENCODE_REGISTRATION_TIMEOUT_MS,
}


@pytest.fixture(autouse=True)
def clean_opencode_env(monkeypatch):
    for name in ("OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "home"
    (home / ".config" / "opencode").mkdir(parents=True)
    return home


def _pair(home):
    base = home / ".config" / "opencode"
    return base / "opencode.json", base / "opencode.jsonc"


def _write(path: Path, data) -> bytes:
    path.write_text(
        data if isinstance(data, str) else json.dumps(data, indent=2),
        encoding="utf-8",
    )
    return path.read_bytes()


def _merge(home):
    _merge_opencode_registration(_opencode_install_target(home), EXPECTED)


def _state(home):
    return _opencode_registration(_opencode_install_target(home), EXPECTED)


NON_STRICT = '{\n  // operator comment\n  "theme": "dark",\n}\n'


# --- adapter-map closure ----------------------------------------------------


def test_every_client_format_maps_to_a_live_adapter():
    """The three-way dispatch cannot silently mis-route a client: every format
    declared in _CLIENTS resolves to a (reader, writer) adapter pair."""
    for client, descriptor in _CLIENTS.items():
        fmt = descriptor["format"]
        assert fmt in _REGISTRATION_ADAPTERS, (client, fmt)
        reader, writer = _REGISTRATION_ADAPTERS[fmt]
        assert callable(reader) and callable(writer)


def test_static_clients_resolve_to_the_same_paths_as_before(tmp_path):
    """The opt-in config_resolver must not relocate any existing client's
    config file (blast-radius guard for the shared `_client_config_path`)."""
    if os.name == "nt":
        pytest.skip("static expectations below are the POSIX branch")
    expected = {
        "claude-code": ".claude.json",
        "codex": ".codex/config.toml",
        "gemini": ".gemini/settings.json",
        "devin": ".config/devin/config.json",
        "windsurf": ".codeium/windsurf/mcp_config.json",
        "claude-desktop": (
            "Library/Application Support/Claude/claude_desktop_config.json"
        ),
        "cursor": ".cursor/mcp.json",
    }
    for client in SUPPORTED_CLIENTS:
        if client == "opencode":
            continue
        assert _client_config_path(client, tmp_path) == tmp_path / expected[client]


# --- D8 installation-target resolver ---------------------------------------


def test_global_xdg_pair_is_the_default_target(home):
    target = _opencode_install_target(home)
    json_path, jsonc_path = _pair(home)
    assert target["kind"] == "pair"
    assert _opencode_target_candidates(target) == (json_path, jsonc_path)


def test_xdg_config_home_overrides_the_home_derived_pair(home, monkeypatch, tmp_path):
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    target = _opencode_install_target(home)
    assert _opencode_target_candidates(target) == (
        xdg / "opencode" / "opencode.json",
        xdg / "opencode" / "opencode.jsonc",
    )


def test_opencode_config_names_exactly_one_file(home, monkeypatch, tmp_path):
    explicit = tmp_path / "elsewhere" / "my-config.json"
    monkeypatch.setenv("OPENCODE_CONFIG", str(explicit))
    target = _opencode_install_target(home)
    assert target["kind"] == "file"
    assert _opencode_target_candidates(target) == (explicit,)


def test_opencode_config_dir_outranks_opencode_config(home, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCODE_CONFIG", str(tmp_path / "explicit.json"))
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(tmp_path / "dir"))
    target = _opencode_install_target(home)
    assert target["kind"] == "pair"
    assert _opencode_target_candidates(target) == (
        tmp_path / "dir" / "opencode.json",
        tmp_path / "dir" / "opencode.jsonc",
    )


def test_representative_path_prefers_the_later_loaded_existing_member(home):
    json_path, jsonc_path = _pair(home)
    assert _opencode_config_path(home) == json_path  # neither exists -> default
    _write(json_path, {})
    assert _opencode_config_path(home) == json_path
    _write(jsonc_path, {})
    assert _opencode_config_path(home) == jsonc_path


# --- write algorithm (D4 steps 1-5) ----------------------------------------


def test_absent_files_create_opencode_json_with_the_full_entry(home):
    """(a) Nothing exists -> create opencode.json; round-trips as current."""
    json_path, jsonc_path = _pair(home)
    _merge(home)
    assert json_path.is_file() and not jsonc_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["mcp"]["cartopian"] == EXPECTED_ENTRY
    assert _state(home) == ("current", "expected-command")


def test_minimal_strict_jsonc_is_merged_in_place(home):
    """(b) The real-world case: one strict opencode.jsonc; merge into it."""
    json_path, jsonc_path = _pair(home)
    _write(jsonc_path, {"$schema": "https://opencode.ai/config.json", "theme": "dark"})
    _merge(home)
    assert not json_path.exists()
    data = json.loads(jsonc_path.read_text(encoding="utf-8"))
    assert data["$schema"] == "https://opencode.ai/config.json"
    assert data["theme"] == "dark"
    assert data["mcp"]["cartopian"] == EXPECTED_ENTRY
    assert _state(home) == ("current", "expected-command")


def test_unrelated_mcp_siblings_are_preserved(home):
    """(c) Sibling servers under `mcp` survive the merge untouched."""
    json_path, _jsonc_path = _pair(home)
    _write(
        json_path,
        {"mcp": {"other": {"type": "remote", "url": "https://example.test"}}},
    )
    _merge(home)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["mcp"]["other"] == {"type": "remote", "url": "https://example.test"}
    assert data["mcp"]["cartopian"] == EXPECTED_ENTRY


def test_dirty_entry_is_detected_then_repaired(home):
    """(d) A pre-existing different cartopian entry reads dirty; an authorized
    merge replaces it."""
    json_path, _ = _pair(home)
    _write(json_path, {"mcp": {"cartopian": {"type": "local", "command": ["/old"]}}})
    state, evidence = _state(home)
    assert state == "dirty"
    assert evidence.startswith("configuration-fingerprint:")
    _merge(home)
    assert _state(home) == ("current", "expected-command")


def test_nonstrict_jsonc_refuses_and_preserves_both_files(home):
    """(e) A comment-bearing .jsonc is the later-loaded member; a write to
    either sibling could be silently shadowed, so the merge refuses and both
    files stay byte-identical."""
    json_path, jsonc_path = _pair(home)
    json_before = _write(json_path, {"theme": "light"})
    jsonc_before = _write(jsonc_path, NON_STRICT)
    with pytest.raises(WorkflowRefusal, match="malformed and was preserved"):
        _merge(home)
    assert json_path.read_bytes() == json_before
    assert jsonc_path.read_bytes() == jsonc_before


def test_both_nonstrict_refuses_and_preserves(home):
    """(f) Both members unreadable -> refuse; nothing is written anywhere."""
    json_path, jsonc_path = _pair(home)
    json_before = _write(json_path, "{ not json ")
    jsonc_before = _write(jsonc_path, NON_STRICT)
    with pytest.raises(WorkflowRefusal):
        _merge(home)
    assert json_path.read_bytes() == json_before
    assert jsonc_path.read_bytes() == jsonc_before
    state, _evidence = _state(home)
    assert state == "malformed"


def test_readable_shadowing_entry_reads_dirty_not_current(home):
    """(g) Our entry in .json with a different readable cartopian entry in the
    later-loaded .jsonc: the shadowing entry wins in opencode, so the state is
    dirty and the evidence names the shadowing file."""
    json_path, jsonc_path = _pair(home)
    _write(json_path, {"mcp": {"cartopian": dict(EXPECTED_ENTRY)}})
    _write(jsonc_path, {"mcp": {"cartopian": {"type": "local", "command": ["/rogue"]}}})
    state, evidence = _state(home)
    assert state == "dirty"
    assert "shadowed-by:opencode.jsonc" in evidence


def test_nonstrict_json_falls_upward_to_jsonc(home):
    """(h) A non-strict .json cannot shadow a later-loaded write, so the merge
    falls back upward in precedence and leaves the .json byte-identical."""
    json_path, jsonc_path = _pair(home)
    json_before = _write(json_path, "{ // comment\n }")
    _merge(home)
    assert json_path.read_bytes() == json_before
    data = json.loads(jsonc_path.read_text(encoding="utf-8"))
    assert data["mcp"]["cartopian"] == EXPECTED_ENTRY
    assert _state(home) == ("current", "expected-command")


def test_both_strict_prefers_the_later_loaded_jsonc(home):
    """(i) With both members strict, the write goes to .jsonc so the entry
    cannot be shadowed by the sibling."""
    json_path, jsonc_path = _pair(home)
    json_before = _write(json_path, {"theme": "light"})
    _write(jsonc_path, {"theme": "dark"})
    _merge(home)
    assert json_path.read_bytes() == json_before
    data = json.loads(jsonc_path.read_text(encoding="utf-8"))
    assert data["mcp"]["cartopian"] == EXPECTED_ENTRY


def test_explicit_opencode_config_nonstrict_refuses_without_sibling_fallback(
    home, monkeypatch, tmp_path
):
    """(j) $OPENCODE_CONFIG names one file; opencode loads no sibling of it,
    so a non-strict file is refused outright and no sibling appears."""
    explicit = tmp_path / "config" / "settings.json"
    explicit.parent.mkdir()
    before = _write(explicit, NON_STRICT)
    monkeypatch.setenv("OPENCODE_CONFIG", str(explicit))
    with pytest.raises(WorkflowRefusal, match="malformed and was preserved"):
        _merge(home)
    assert explicit.read_bytes() == before
    assert list(explicit.parent.iterdir()) == [explicit]


def test_explicit_opencode_config_strict_merges_that_exact_file(
    home, monkeypatch, tmp_path
):
    explicit = tmp_path / "config" / "settings.json"
    explicit.parent.mkdir()
    _write(explicit, {"keybinds": {"leader": "space"}})
    monkeypatch.setenv("OPENCODE_CONFIG", str(explicit))
    _merge(home)
    data = json.loads(explicit.read_text(encoding="utf-8"))
    assert data["keybinds"] == {"leader": "space"}
    assert data["mcp"]["cartopian"] == EXPECTED_ENTRY
    assert _state(home) == ("current", "expected-command")


def test_mcp_key_that_is_not_an_object_is_preserved_and_refused(home):
    json_path, _ = _pair(home)
    before = _write(json_path, {"mcp": ["not", "an", "object"]})
    with pytest.raises(WorkflowRefusal, match="not an object"):
        _merge(home)
    assert json_path.read_bytes() == before


# --- operator-config target validation (inherited refusals) ------------------


def test_symlinked_write_target_is_preserved_and_refused(home, tmp_path):
    _json_path, jsonc_path = _pair(home)
    actual = tmp_path / "real-config.json"
    _write(actual, {"theme": "dark"})
    jsonc_path.symlink_to(actual)
    with pytest.raises(WorkflowRefusal, match="symlink"):
        _merge(home)
    assert json.loads(actual.read_text(encoding="utf-8")) == {"theme": "dark"}


def test_multi_hardlinked_write_target_is_preserved_and_refused(home, tmp_path):
    json_path, _ = _pair(home)
    before = _write(json_path, {"theme": "dark"})
    os.link(json_path, tmp_path / "hardlink-twin.json")
    with pytest.raises(WorkflowRefusal, match="hard links"):
        _merge(home)
    assert json_path.read_bytes() == before


def test_non_regular_later_loaded_member_is_refused(home):
    """A directory occupying the later-loaded slot is unreadable to us, so the
    pair refuses exactly like a non-strict .jsonc."""
    _json_path, jsonc_path = _pair(home)
    jsonc_path.mkdir()
    with pytest.raises(WorkflowRefusal):
        _merge(home)
    assert jsonc_path.is_dir()


def test_non_regular_explicit_target_is_refused(home, monkeypatch, tmp_path):
    explicit = tmp_path / "config-dir-not-file"
    explicit.mkdir()
    monkeypatch.setenv("OPENCODE_CONFIG", str(explicit))
    with pytest.raises(WorkflowRefusal):
        _merge(home)
    assert explicit.is_dir()


def test_non_regular_earlier_loaded_member_falls_upward(home):
    """A directory at the .json slot cannot shadow a later-loaded write, so
    the merge falls upward to .jsonc — the same safe direction as a non-strict
    .json — and leaves the directory untouched."""
    json_path, jsonc_path = _pair(home)
    json_path.mkdir()
    _merge(home)
    assert json_path.is_dir()
    data = json.loads(jsonc_path.read_text(encoding="utf-8"))
    assert data["mcp"]["cartopian"] == EXPECTED_ENTRY


# --- shadow-aware reader ----------------------------------------------------


def test_reader_reports_missing_when_nothing_exists(home):
    assert _state(home) == ("missing", "absent")


def test_reader_reports_missing_when_strict_files_carry_no_entry(home):
    json_path, _ = _pair(home)
    _write(json_path, {"theme": "dark"})
    assert _state(home) == ("missing", "absent")


def test_nonstrict_candidate_after_our_entry_fails_closed_to_malformed(home):
    """A present-but-unreadable later-loaded candidate may carry a shadowing
    entry this tool cannot see, so `current` may never be claimed."""
    json_path, jsonc_path = _pair(home)
    _write(json_path, {"mcp": {"cartopian": dict(EXPECTED_ENTRY)}})
    _write(jsonc_path, NON_STRICT)
    state, evidence = _state(home)
    assert state == "malformed"
    assert "opencode.jsonc" in evidence


def test_nonstrict_candidate_before_our_entry_cannot_shadow_it(home):
    """An unreadable earlier-loaded .json cannot shadow a later-loaded .jsonc
    entry (V21/V23), so the registration still verifies as current."""
    json_path, jsonc_path = _pair(home)
    _write(json_path, NON_STRICT)
    _write(jsonc_path, {"mcp": {"cartopian": dict(EXPECTED_ENTRY)}})
    assert _state(home) == ("current", "expected-command")


@pytest.mark.parametrize(
    "entry",
    [
        {"type": "local", "command": EXPECTED},  # string command
        {"type": "remote", "command": [EXPECTED]},  # wrong type
        {"type": "local", "command": [EXPECTED, "--extra"]},  # extra element
        None,  # null entry
    ],
)
def test_wrong_shape_entries_read_dirty_with_a_fingerprint(home, entry):
    json_path, _ = _pair(home)
    _write(json_path, {"mcp": {"cartopian": entry}})
    state, evidence = _state(home)
    assert state == "dirty"
    assert "configuration-fingerprint:" in evidence
