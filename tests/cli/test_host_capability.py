"""Tests for the MCP host wait-budget resolver.

Cartopian waits for a handoff by holding one ``tools/call`` open until the
report lands. Every host caps that call, and on some hosts the cap is shorter
than the protocol's default ``60m`` role timeout. These tests pin the three
behaviors that make the mismatch safe: the budget is read from real host
evidence, an unrecognized host resolves to *unknown* rather than to some other
host's numbers, and the gate refuses only when the role genuinely does not fit.

The distinction that matters most here is **no host** versus **unknown host**.
A plain shell invocation has no ``tools/call`` ceiling at all, so the gate must
not apply; an unrecognized MCP client has a ceiling nobody can read, so the
gate must fail closed.
"""
import json

import pytest

from cli import emit, host_capability
from cli.main import EXIT_OK, build_parser


@pytest.fixture(autouse=True)
def clean_host_env(monkeypatch):
    """Start every test outside an MCP host, with no host config leaking in."""
    prior_sink = emit.set_progress_sink(None)
    # The Hermes ceiling is pinned per server process; tests are not sessions.
    monkeypatch.setattr(host_capability, "_HERMES_SESSION_CEILINGS", {})
    for name in (
        host_capability.CONNECTED_ENV,
        host_capability.CLIENT_ENV,
        host_capability.CLIENT_VERSION_ENV,
        host_capability.CLIENT_TITLE_ENV,
        host_capability.SERVER_NAME_ENV,
        host_capability.HOST_MARKER_ENV,
        host_capability.HERMES_HOME_ENV,
        "MCP_TOOL_TIMEOUT",
        "CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT",
        "CODEX_HOME",
        "OPENCODE_CONFIG",
        "OPENCODE_CONFIG_DIR",
        "OPENCODE_CONFIG_CONTENT",
        "XDG_CONFIG_HOME",
    ):
        monkeypatch.delenv(name, raising=False)
    yield
    emit.set_progress_sink(prior_sink)


def _as_client(monkeypatch, name, *, title=None, version=None):
    monkeypatch.setenv(host_capability.CONNECTED_ENV, "1")
    monkeypatch.setenv(host_capability.CLIENT_ENV, name)
    if title is not None:
        monkeypatch.setenv(host_capability.CLIENT_TITLE_ENV, title)
    if version is not None:
        monkeypatch.setenv(host_capability.CLIENT_VERSION_ENV, version)


def _codex_home(tmp_path, body):
    home = tmp_path / "codex"
    home.mkdir()
    (home / "config.toml").write_text(body, encoding="utf-8")
    return home


# --- No host at all --------------------------------------------------------


def test_no_client_marker_means_no_host_ceiling():
    """A plain shell invocation has no tools/call ceiling to gate on."""
    assert host_capability.resolve_host_budget() is None
    assert host_capability.under_mcp_host() is False


def test_gate_does_not_apply_outside_an_mcp_host():
    ok, budget, refusal = host_capability.check_wait_budget("coder", 86_400)
    assert ok is True
    assert budget is None
    assert refusal is None


# --- Codex -----------------------------------------------------------------


def test_codex_default_ceiling_is_below_the_protocol_default(monkeypatch, tmp_path):
    """Codex's 300s default is shorter than the 60m role default, so it refuses."""
    _as_client(monkeypatch, "codex-mcp-client", title="Codex")
    monkeypatch.setenv("CODEX_HOME", str(_codex_home(tmp_path, "")))

    budget = host_capability.resolve_host_budget()
    assert budget.host == "codex"
    assert budget.effective_seconds == 300
    assert budget.limiting_ceiling() == "wall-clock"

    ok, _budget, refusal = host_capability.check_wait_budget("coder", 3600)
    assert ok is False
    assert "1h" in refusal and "5m" in refusal
    # The refusal must name the exact key that fixes it, not just the problem.
    assert "tool_timeout_sec" in refusal


def test_codex_reads_configured_tool_timeout_sec(monkeypatch, tmp_path):
    home = _codex_home(
        tmp_path,
        '[mcp_servers.cartopian]\ncommand = "/x/cartopian-mcp"\ntool_timeout_sec = 3900\n',
    )
    _as_client(monkeypatch, "codex-mcp-client")
    monkeypatch.setenv("CODEX_HOME", str(home))

    budget = host_capability.resolve_host_budget()
    assert budget.effective_seconds == 3900
    assert budget.wall_clock_source == "host-config"

    ok, _budget, refusal = host_capability.check_wait_budget("coder", 3600)
    assert ok is True
    assert refusal is None


def test_codex_finds_the_server_registered_under_another_name(monkeypatch, tmp_path):
    """A non-default registration still yields a real reading, not the default."""
    home = _codex_home(
        tmp_path,
        '[mcp_servers.my-cartopian]\n'
        'command = "/opt/cartopian/bin/cartopian-mcp"\n'
        "tool_timeout_sec = 5000\n",
    )
    _as_client(monkeypatch, "codex-mcp-client")
    monkeypatch.setenv("CODEX_HOME", str(home))

    budget = host_capability.resolve_host_budget()
    assert budget.effective_seconds == 5000
    assert budget.wall_clock_source == "host-config"


def test_codex_unreadable_config_falls_back_to_the_documented_default(
    monkeypatch, tmp_path
):
    home = _codex_home(tmp_path, "this is not valid toml {{{")
    _as_client(monkeypatch, "codex-mcp-client")
    monkeypatch.setenv("CODEX_HOME", str(home))

    budget = host_capability.resolve_host_budget()
    assert budget.effective_seconds == 300
    assert budget.wall_clock_source == "host-default"


@pytest.mark.parametrize("invalid_value", ("true", "3.5", "-1", "0"))
def test_codex_invalid_tool_timeout_falls_back_to_documented_default(
    monkeypatch, tmp_path, invalid_value
):
    home = _codex_home(
        tmp_path,
        "[mcp_servers.cartopian]\n"
        'command = "/x/cartopian-mcp"\n'
        f"tool_timeout_sec = {invalid_value}\n",
    )
    _as_client(monkeypatch, "codex-mcp-client")
    monkeypatch.setenv("CODEX_HOME", str(home))

    budget = host_capability.resolve_host_budget()
    assert budget.effective_seconds == 300
    assert budget.wall_clock_source == "host-default"


# --- Claude Code -----------------------------------------------------------


def test_claude_code_progress_maintains_idle_without_extending_wall_clock(monkeypatch):
    """Resettable idle is reported but does not cap a progress-bearing wait."""
    _as_client(monkeypatch, "claude-code")
    emit.set_progress_sink(lambda _progress, _total, _message: None)

    budget = host_capability.resolve_host_budget()
    assert budget.host == "claude-code"
    assert budget.wall_clock_seconds == 100_800
    assert budget.idle_seconds == 1_800
    assert budget.effective_seconds == 100_800
    assert budget.limiting_ceiling() == "wall-clock"
    # Progress notifications count as traffic against the idle timer only.
    assert budget.progress_resets_idle is True
    assert budget.progress_resets_wall_clock is False
    assert budget.record()["progress_channel_available"] is True


def test_claude_code_without_progress_retains_idle_ceiling(monkeypatch):
    """A role beyond the idle default is refused when progress is unavailable."""
    _as_client(monkeypatch, "claude-code")

    budget = host_capability.resolve_host_budget()
    assert budget.progress_channel_available is False
    assert budget.effective_seconds == 1_800
    assert budget.limiting_ceiling() == "idle"
    assert budget.record()["progress_channel_available"] is False

    ok, _budget, refusal = host_capability.check_wait_budget("reviewer", 2_700)
    assert ok is False
    assert "45m" in refusal and "30m" in refusal
    assert "idle ceiling" in refusal


def test_equal_resettable_idle_and_wall_reports_wall_as_binding(monkeypatch):
    """An excluded equal idle value must not mislabel the fixed wall ceiling."""
    _as_client(monkeypatch, "claude-code")
    monkeypatch.setenv("MCP_TOOL_TIMEOUT", "1800000")
    emit.set_progress_sink(lambda _progress, _total, _message: None)

    budget = host_capability.resolve_host_budget()
    assert budget.wall_clock_seconds == budget.idle_seconds == 1_800
    assert budget.effective_seconds == 1_800
    assert budget.limiting_ceiling() == "wall-clock"

    ok, _budget, refusal = host_capability.check_wait_budget("reviewer", 2_700)
    assert ok is False
    assert "wall-clock ceiling" in refusal


def test_claude_code_idle_window_can_be_raised(monkeypatch):
    _as_client(monkeypatch, "claude-code")
    monkeypatch.setenv("CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT", "5400000")

    budget = host_capability.resolve_host_budget()
    assert budget.idle_seconds == 5400
    assert budget.effective_seconds == 5400
    ok, _b, _r = host_capability.check_wait_budget("coder", 3600)
    assert ok is True


def test_claude_code_zero_idle_timeout_disables_the_check(monkeypatch):
    """`0` means "disable", not "abort immediately" — it must not read as 0s."""
    _as_client(monkeypatch, "claude-code")
    monkeypatch.setenv("CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT", "0")

    budget = host_capability.resolve_host_budget()
    assert budget.idle_seconds is None
    assert budget.effective_seconds == 100_800
    assert budget.idle_source == "host-config-disabled"


# --- Gemini ----------------------------------------------------------------


def test_gemini_default_ceiling(monkeypatch, tmp_path):
    _as_client(monkeypatch, "gemini-cli")
    monkeypatch.setattr(
        host_capability,
        "_gemini_settings_paths",
        lambda: [tmp_path / "missing-settings.json"],
    )
    budget = host_capability.resolve_host_budget()
    assert budget.host == "gemini-cli"
    assert budget.effective_seconds == 600


# --- opencode --------------------------------------------------------------


def _opencode_env(monkeypatch, tmp_path):
    """Isolated opencode config surface: empty XDG home, config-free cwd."""
    _as_client(monkeypatch, "opencode", version="1.18.15")
    xdg = tmp_path / "xdg"
    (xdg / "opencode").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    return xdg / "opencode"


def test_opencode_default_is_a_progress_resettable_idle_window(
    monkeypatch, tmp_path
):
    """opencode imposes no wall clock; unconfigured it has a 60s idle window
    that a progress notification resets."""
    _opencode_env(monkeypatch, tmp_path)

    budget = host_capability.resolve_host_budget()
    assert budget.host == "opencode"
    assert budget.wall_clock_seconds is None
    assert budget.wall_clock_source == "not-imposed"
    assert budget.idle_seconds == 60
    assert budget.idle_source == "host-default"
    assert budget.progress_resets_idle is True
    assert budget.progress_resets_wall_clock is False

    # Without a progress channel the raw idle window is the binding ceiling.
    assert budget.effective_seconds == 60
    assert budget.limiting_ceiling() == "idle"

    # With the Cartopian heartbeat channel installed, the resettable idle
    # window no longer caps total wait duration (D3: heartbeats every 5s).
    emit.set_progress_sink(lambda _progress, _total, _message: None)
    budget = host_capability.resolve_host_budget()
    assert budget.effective_seconds is None
    ok, _budget, refusal = host_capability.check_wait_budget("coder", 3600)
    assert ok is True
    assert refusal is None


def test_opencode_reads_the_registered_timeout(monkeypatch, tmp_path):
    config_dir = _opencode_env(monkeypatch, tmp_path)
    (config_dir / "opencode.json").write_text(
        json.dumps(
            {
                "mcp": {
                    "cartopian": {
                        "type": "local",
                        "command": ["/x/bin/cartopian-mcp"],
                        "timeout": 600000,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    budget = host_capability.resolve_host_budget()
    assert budget.idle_seconds == 600
    assert budget.idle_source == "host-config"


def test_opencode_server_timeout_wins_over_experimental(monkeypatch, tmp_path):
    """Resolution chain is mcp.<server>.timeout ?? experimental.mcp_timeout,
    regardless of which layer supplied each key."""
    config_dir = _opencode_env(monkeypatch, tmp_path)
    (config_dir / "opencode.json").write_text(
        json.dumps(
            {
                "experimental": {"mcp_timeout": 120000},
                "mcp": {"cartopian": {"timeout": 600000}},
            }
        ),
        encoding="utf-8",
    )
    budget = host_capability.resolve_host_budget()
    assert budget.idle_seconds == 600
    assert budget.idle_source == "host-config"


def test_opencode_experimental_timeout_applies_without_a_server_entry(
    monkeypatch, tmp_path
):
    config_dir = _opencode_env(monkeypatch, tmp_path)
    (config_dir / "opencode.jsonc").write_text(
        json.dumps({"experimental": {"mcp_timeout": 90000}}),
        encoding="utf-8",
    )
    budget = host_capability.resolve_host_budget()
    assert budget.idle_seconds == 90
    assert budget.idle_source == "host-config"


def test_opencode_finds_the_server_registered_under_another_name(
    monkeypatch, tmp_path
):
    config_dir = _opencode_env(monkeypatch, tmp_path)
    (config_dir / "opencode.json").write_text(
        json.dumps(
            {
                "mcp": {
                    "my-cartopian": {
                        "type": "local",
                        "command": ["/opt/cartopian/bin/cartopian-mcp"],
                        "timeout": 300000,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    budget = host_capability.resolve_host_budget()
    assert budget.idle_seconds == 300
    assert budget.idle_source == "host-config"


def test_opencode_later_loaded_jsonc_overrides_json(monkeypatch, tmp_path):
    """Within a directory pair, opencode.jsonc loads after opencode.json and
    wins a same-key conflict (V21/V23)."""
    config_dir = _opencode_env(monkeypatch, tmp_path)
    (config_dir / "opencode.json").write_text(
        json.dumps({"mcp": {"cartopian": {"timeout": 120000}}}),
        encoding="utf-8",
    )
    (config_dir / "opencode.jsonc").write_text(
        json.dumps({"mcp": {"cartopian": {"timeout": 600000}}}),
        encoding="utf-8",
    )
    budget = host_capability.resolve_host_budget()
    assert budget.idle_seconds == 600


def test_opencode_project_layer_overrides_global(monkeypatch, tmp_path):
    config_dir = _opencode_env(monkeypatch, tmp_path)
    (config_dir / "opencode.json").write_text(
        json.dumps({"mcp": {"cartopian": {"timeout": 120000}}}),
        encoding="utf-8",
    )
    project = tmp_path / "cwd"  # created by _opencode_env; already the cwd
    (project / "opencode.json").write_text(
        json.dumps({"mcp": {"cartopian": {"timeout": 480000}}}),
        encoding="utf-8",
    )
    budget = host_capability.resolve_host_budget()
    assert budget.idle_seconds == 480
    assert budget.idle_source == "host-config"


def test_opencode_config_dir_layer_overrides_everything(monkeypatch, tmp_path):
    config_dir = _opencode_env(monkeypatch, tmp_path)
    (config_dir / "opencode.json").write_text(
        json.dumps({"mcp": {"cartopian": {"timeout": 120000}}}),
        encoding="utf-8",
    )
    override_dir = tmp_path / "override"
    override_dir.mkdir()
    (override_dir / "opencode.jsonc").write_text(
        json.dumps({"mcp": {"cartopian": {"timeout": 900000}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(override_dir))
    budget = host_capability.resolve_host_budget()
    assert budget.idle_seconds == 900


def test_opencode_nonstrict_layer_fails_closed_to_unknown(monkeypatch, tmp_path):
    """A present-but-unparseable layer could shadow either timeout key, so the
    ceiling is reported unknown — never an invented number — and the dispatch
    gate refuses even a short role timeout."""
    config_dir = _opencode_env(monkeypatch, tmp_path)
    (config_dir / "opencode.json").write_text(
        json.dumps({"mcp": {"cartopian": {"timeout": 600000}}}),
        encoding="utf-8",
    )
    (config_dir / "opencode.jsonc").write_text(
        '{\n  // an operator comment\n  "mcp": {},\n}\n', encoding="utf-8"
    )
    budget = host_capability.resolve_host_budget()
    assert budget.host == "opencode"
    assert budget.idle_seconds is None
    assert budget.idle_source == "unknown"
    assert any("opencode.jsonc" in item for item in budget.evidence)

    ok, gate_budget, refusal = host_capability.check_wait_budget("coder", 30)
    assert ok is False
    assert gate_budget.host == "opencode"
    assert "cannot be resolved" in refusal


def test_opencode_uninspectable_inline_layer_fails_closed(monkeypatch, tmp_path):
    _opencode_env(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", "{ not json }")
    budget = host_capability.resolve_host_budget()
    assert budget.idle_seconds is None
    assert budget.idle_source == "unknown"


def test_opencode_without_progress_channel_refuses_an_oversized_role(
    monkeypatch, tmp_path
):
    config_dir = _opencode_env(monkeypatch, tmp_path)
    (config_dir / "opencode.json").write_text(
        json.dumps({"mcp": {"cartopian": {"timeout": 600000}}}),
        encoding="utf-8",
    )
    ok, budget, refusal = host_capability.check_wait_budget("coder", 3600)
    assert ok is False
    assert budget.effective_seconds == 600
    assert "idle ceiling" in refusal
    # The refusal names the exact key that fixes it.
    assert "timeout" in refusal


# --- Hermes ----------------------------------------------------------------
#
# Hermes identifies as the SDK-default clientInfo name "mcp", which no matcher
# may claim, so resolution happens only through the registration-injected
# CARTOPIAN_MCP_HOST env marker (D12). Its ceiling is a HARD WALL CLOCK per
# tool call — no progress callback, nothing resets it — read via a stub
# `hermes config get --json` on a restricted PATH.


import os as _os
import stat as _stat


def _hermes_stub(tmp_path, monkeypatch, body):
    """Install a stub `hermes` as the only hermes on PATH; return its dir."""
    bin_dir = tmp_path / "hermesbin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "hermes"
    stub.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    stub.chmod(stub.stat().st_mode | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH)
    monkeypatch.setenv(
        "PATH", _os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"])
    )
    return bin_dir


def _as_hermes(monkeypatch):
    """The environment a Hermes-launched Cartopian server actually sees:
    default-SDK clientInfo plus the registration-injected marker."""
    _as_client(monkeypatch, "mcp", version="0.1.0")
    monkeypatch.setenv(host_capability.HOST_MARKER_ENV, "hermes")


def _entry_stdout(timeout=None):
    entry = {"command": "/x/cartopian-mcp", "enabled": True}
    if timeout is not None:
        entry["timeout"] = timeout
    return json.dumps(entry)


def test_bare_mcp_client_info_stays_unknown(monkeypatch):
    """Regression: no host matcher may claim the SDK default name "mcp" —
    that string is what any default-SDK client sends."""
    _as_client(monkeypatch, "mcp", version="0.1.0")
    budget = host_capability.resolve_host_budget()
    assert budget.host == "unknown"

    ok, _budget, refusal = host_capability.check_wait_budget("coder", 30)
    assert ok is False
    assert "unknown" in refusal


def test_hermes_marker_beats_client_info(monkeypatch, tmp_path):
    """The registration-injected marker resolves Hermes; the classification is
    a hard wall clock (idle is not imposed, progress resets nothing)."""
    _as_hermes(monkeypatch)
    _hermes_stub(tmp_path, monkeypatch, f"echo '{_entry_stdout(3900)}'")
    budget = host_capability.resolve_host_budget()
    assert budget.host == "hermes"
    assert budget.wall_clock_seconds == 3900
    assert budget.wall_clock_source == "host-config"
    assert budget.idle_seconds is None
    assert budget.progress_resets_wall_clock is False
    assert budget.progress_resets_idle is False
    assert budget.limiting_ceiling() == "wall-clock"


def test_invalid_marker_value_is_ignored(monkeypatch):
    """A marker outside the closed set falls through to clientInfo matching —
    nothing outside the set may gain a budget."""
    _as_client(monkeypatch, "mcp")
    monkeypatch.setenv(host_capability.HOST_MARKER_ENV, "rogue-host")
    budget = host_capability.resolve_host_budget()
    assert budget.host == "unknown"


def test_hermes_absent_entry_uses_the_hermes_default(monkeypatch, tmp_path):
    _as_hermes(monkeypatch)
    _hermes_stub(
        tmp_path, monkeypatch, "echo 'Config key not set' >&2\nexit 1"
    )
    budget = host_capability.resolve_host_budget()
    assert budget.host == "hermes"
    assert (
        budget.wall_clock_seconds
        == host_capability.HERMES_DEFAULT_CALL_TIMEOUT_SECONDS
    )
    assert budget.wall_clock_source == "host-default"


def test_hermes_exit_two_with_absent_text_fails_closed(monkeypatch, tmp_path):
    """Only Hermes's documented exit 1 means an absent config key.

    A different failure may repeat the same text as incidental diagnostics;
    treating it as absence would invent the 300-second default.
    """
    _as_hermes(monkeypatch)
    _hermes_stub(
        tmp_path, monkeypatch, "echo 'Config key not set' >&2\nexit 2"
    )
    budget = host_capability.resolve_host_budget()
    assert budget.wall_clock_source == "unknown"
    assert budget.wall_clock_seconds is None


def test_hermes_process_start_snapshot_precedes_first_dispatch(monkeypatch):
    """A config raise after process start cannot widen the live session.

    Hermes already captured the old value when it created the handler that
    spawned this process, so Cartopian must retain the startup value even when
    first dispatch occurs after the on-disk entry changes.
    """
    state = {"value": ("host-config", 300, ["timeout = 300s"])}
    monkeypatch.setattr(
        host_capability,
        "_hermes_entry_timeout_now",
        lambda _name: state["value"],
    )
    monkeypatch.setenv(host_capability.HOST_MARKER_ENV, "hermes")
    host_capability.snapshot_process_host_budget()

    state["value"] = ("host-config", 3900, ["timeout = 3900s"])
    _as_client(monkeypatch, "mcp", version="0.1.0")
    budget = host_capability.resolve_host_budget()

    assert budget.wall_clock_seconds == 300
    assert any("process started" in item for item in budget.evidence)


def test_hermes_process_start_unknown_stays_fail_closed(monkeypatch):
    """Repairing an unreadable entry also requires a new Hermes session."""
    state = {"value": ("unknown", None, ["unreadable at startup"])}
    monkeypatch.setattr(
        host_capability,
        "_hermes_entry_timeout_now",
        lambda _name: state["value"],
    )
    monkeypatch.setenv(host_capability.HOST_MARKER_ENV, "hermes")
    host_capability.snapshot_process_host_budget()

    state["value"] = ("host-config", 3900, ["timeout = 3900s"])
    _as_client(monkeypatch, "mcp", version="0.1.0")
    budget = host_capability.resolve_host_budget()

    assert budget.wall_clock_source == "unknown"
    assert budget.wall_clock_seconds is None


def test_hermes_reads_the_registered_timeout_key(monkeypatch, tmp_path):
    _as_hermes(monkeypatch)
    _hermes_stub(tmp_path, monkeypatch, f"echo '{_entry_stdout(1200)}'")
    budget = host_capability.resolve_host_budget()
    assert budget.effective_seconds == 1200
    assert any("timeout = 1200s" in item for item in budget.evidence)


@pytest.mark.parametrize("bad_timeout", ['"soon"', "0", "-5", "3.5", "null"])
def test_hermes_malformed_timeout_fails_closed(monkeypatch, tmp_path, bad_timeout):
    """A timeout key that is present but not a positive integer must resolve
    to unknown, never to the 300s default: Hermes uses configured values
    as-is, so a malformed value does not behave like an absent one."""
    _as_hermes(monkeypatch)
    entry = (
        '{"command": "/x/cartopian-mcp", "enabled": true, '
        f'"timeout": {bad_timeout}}}'
    )
    _hermes_stub(tmp_path, monkeypatch, f"echo '{entry}'")
    budget = host_capability.resolve_host_budget()
    assert budget.wall_clock_source == "unknown"
    assert budget.wall_clock_seconds is None

    ok, _budget, refusal = host_capability.check_wait_budget("coder", 30)
    assert ok is False
    assert "cannot be resolved" in refusal


def test_hermes_read_targets_the_marker_home(monkeypatch, tmp_path):
    """The resolver's subprocess sets HERMES_HOME from CARTOPIAN_HERMES_HOME:
    Hermes's filtered stdio env never delivers it, so without the marker a
    non-default profile's config would silently not be read."""
    _as_hermes(monkeypatch)
    profile_home = tmp_path / "profile-home"
    profile_home.mkdir()
    seen = tmp_path / "seen-home.txt"
    _hermes_stub(
        tmp_path,
        monkeypatch,
        f'printf "%s" "$HERMES_HOME" > "{seen}"\n'
        f"echo '{_entry_stdout(3900)}'",
    )
    monkeypatch.setenv(host_capability.HERMES_HOME_ENV, str(profile_home))
    budget = host_capability.resolve_host_budget()
    assert budget.wall_clock_seconds == 3900
    assert seen.read_text(encoding="utf-8") == str(profile_home)


def test_hermes_root_marker_home_is_pinned_with_p_default(monkeypatch, tmp_path):
    """Hermes v0.20 intentionally lets the sticky active_profile override a
    root/default HERMES_HOME, so the env alone cannot target the registering
    config — the read must carry the explicit `-p default` identity."""
    _as_hermes(monkeypatch)
    root_home = tmp_path / "root-home"
    root_home.mkdir()
    seen = tmp_path / "seen-argv.txt"
    _hermes_stub(
        tmp_path,
        monkeypatch,
        f'printf "%s" "$*" > "{seen}"\n'
        f"echo '{_entry_stdout(3900)}'",
    )
    monkeypatch.setenv(host_capability.HERMES_HOME_ENV, str(root_home))
    budget = host_capability.resolve_host_budget()
    assert budget.wall_clock_seconds == 3900
    argv = seen.read_text(encoding="utf-8")
    assert argv.startswith("-p default config get ")
    assert any("-p default" in item for item in budget.evidence)


def test_hermes_profile_parented_marker_home_pins_via_env(monkeypatch, tmp_path):
    """A HERMES_HOME whose parent directory is named `profiles` is trusted
    verbatim by Hermes's pre-parse (active_profile is never consulted), so the
    env is the pin and no `-p` flag is added — `-p default` there would
    wrongly redirect the read to the root profile."""
    _as_hermes(monkeypatch)
    profile_home = tmp_path / "profiles" / "coder"
    profile_home.mkdir(parents=True)
    seen_argv = tmp_path / "seen-argv.txt"
    seen_home = tmp_path / "seen-home.txt"
    _hermes_stub(
        tmp_path,
        monkeypatch,
        f'printf "%s" "$*" > "{seen_argv}"\n'
        f'printf "%s" "$HERMES_HOME" > "{seen_home}"\n'
        f"echo '{_entry_stdout(3900)}'",
    )
    monkeypatch.setenv(host_capability.HERMES_HOME_ENV, str(profile_home))
    budget = host_capability.resolve_host_budget()
    assert budget.wall_clock_seconds == 3900
    assert seen_argv.read_text(encoding="utf-8").startswith("config get ")
    assert seen_home.read_text(encoding="utf-8") == str(profile_home)


def test_hermes_nonsensical_marker_home_fails_closed(monkeypatch, tmp_path):
    _as_hermes(monkeypatch)
    _hermes_stub(tmp_path, monkeypatch, f"echo '{_entry_stdout(3900)}'")
    monkeypatch.setenv(
        host_capability.HERMES_HOME_ENV, str(tmp_path / "does-not-exist")
    )
    budget = host_capability.resolve_host_budget()
    assert budget.wall_clock_source == "unknown"
    assert budget.wall_clock_seconds is None

    ok, _budget, refusal = host_capability.check_wait_budget("coder", 30)
    assert ok is False
    assert "cannot be resolved" in refusal


def test_hermes_cli_absent_fails_closed(monkeypatch, tmp_path):
    _as_hermes(monkeypatch)
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    budget = host_capability.resolve_host_budget()
    assert budget.host == "hermes"
    assert budget.wall_clock_source == "unknown"

    ok, _budget, refusal = host_capability.check_wait_budget("coder", 30)
    assert ok is False


def test_hermes_malformed_entry_fails_closed(monkeypatch, tmp_path):
    _as_hermes(monkeypatch)
    _hermes_stub(tmp_path, monkeypatch, "echo 'not json at all'")
    budget = host_capability.resolve_host_budget()
    assert budget.wall_clock_source == "unknown"

    ok, _budget, refusal = host_capability.check_wait_budget("coder", 30)
    assert ok is False
    assert "cannot be resolved" in refusal


def test_hermes_ceiling_is_pinned_for_the_server_process(monkeypatch, tmp_path):
    """A config raise without a reload must not widen the running session's
    budget: Hermes captured its ceiling when the session's MCP handler was
    created, so the resolver holds its first successful reading for the
    process lifetime instead of re-reading a disk value the live handler
    does not enforce."""
    _as_hermes(monkeypatch)
    bin_dir = _hermes_stub(tmp_path, monkeypatch, f"echo '{_entry_stdout(300)}'")
    assert host_capability.resolve_host_budget().wall_clock_seconds == 300

    # The operator raises the timeout on disk mid-session, without a reload.
    (bin_dir / "hermes").write_text(
        f"#!/bin/sh\necho '{_entry_stdout(3900)}'\n", encoding="utf-8"
    )
    budget = host_capability.resolve_host_budget()
    assert budget.wall_clock_seconds == 300
    assert any("pinned for this server process" in item for item in budget.evidence)

    # The gate still refuses the 60-minute wait the live handler would kill.
    ok, _budget, refusal = host_capability.check_wait_budget("coder", 3600)
    assert ok is False
    assert "wall-clock" in refusal


def test_hermes_unresolved_ceiling_is_never_pinned(monkeypatch, tmp_path):
    """Only a successful resolution is held: an unreadable configuration must
    stay fail-closed *and* retryable, so a repaired config (plus the session
    reload the remediation demands) resolves later in the same process."""
    _as_hermes(monkeypatch)
    bin_dir = _hermes_stub(tmp_path, monkeypatch, "echo 'not json at all'")
    assert host_capability.resolve_host_budget().wall_clock_source == "unknown"

    (bin_dir / "hermes").write_text(
        f"#!/bin/sh\necho '{_entry_stdout(1200)}'\n", encoding="utf-8"
    )
    budget = host_capability.resolve_host_budget()
    assert budget.wall_clock_source == "host-config"
    assert budget.wall_clock_seconds == 1200


def test_hermes_budget_contract_default_role_fits_registered_ceiling(
    monkeypatch, tmp_path
):
    """The D5 sizing fix: under the installed 3,900s entry a default 60-minute
    role passes the gate in one terminal wait; a role above it refuses cleanly
    before launch with the config key named."""
    from cli.install_workflow import HERMES_REGISTRATION_TIMEOUT_SECONDS

    _as_hermes(monkeypatch)
    _hermes_stub(
        tmp_path,
        monkeypatch,
        f"echo '{_entry_stdout(HERMES_REGISTRATION_TIMEOUT_SECONDS)}'",
    )
    ok, budget, refusal = host_capability.check_wait_budget("coder", 3600)
    assert ok is True
    assert budget.effective_seconds == HERMES_REGISTRATION_TIMEOUT_SECONDS
    assert refusal is None

    ok, _budget, refusal = host_capability.check_wait_budget("coder", 7200)
    assert ok is False
    assert "wall-clock" in refusal
    assert "config set" in refusal


# --- Unknown host: fail closed --------------------------------------------


def test_unrecognized_host_resolves_to_unknown_not_to_a_default(monkeypatch):
    _as_client(monkeypatch, "some-brand-new-ide")
    budget = host_capability.resolve_host_budget()

    assert budget.host == "unknown"
    assert budget.known is False
    # Unknown is not unbounded: no number may be invented for it.
    assert budget.effective_seconds is None
    assert budget.wall_clock_seconds is None


def test_connected_mcp_without_client_identity_is_unknown(monkeypatch):
    monkeypatch.setenv(host_capability.CONNECTED_ENV, "1")
    budget = host_capability.resolve_host_budget()
    assert budget is not None
    assert budget.host == "unknown"
    assert host_capability.under_mcp_host() is True


def test_unknown_host_fails_the_gate_closed(monkeypatch):
    _as_client(monkeypatch, "some-brand-new-ide")
    # Even a very short timeout is refused: the ceiling is unreadable, so no
    # duration can be shown to survive it.
    ok, budget, refusal = host_capability.check_wait_budget("coder", 30)

    assert ok is False
    assert budget.host == "unknown"
    assert "unknown" in refusal
    assert "manually" in refusal


# --- Duration helpers ------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [("60m", 3600), ("1h", 3600), ("30s", 30), ("2d", 172_800)],
)
def test_parse_duration_accepts_the_config_grammar(raw, expected):
    assert host_capability.parse_duration(raw) == expected


@pytest.mark.parametrize("raw", ["0m", "-5m", "60", "m", "", "1w", "1.5h"])
def test_parse_duration_rejects_malformed_values(raw):
    assert host_capability.parse_duration(raw) is None


def test_format_duration_round_trips_into_the_config_grammar():
    assert host_capability.format_duration(3600) == "1h"
    assert host_capability.format_duration(300) == "5m"
    assert host_capability.format_duration(90) == "90s"
    assert host_capability.format_duration(None) == "unknown"


# --- The reporting command -------------------------------------------------


def _run(argv, capsys):
    parser = build_parser()
    args = parser.parse_args(argv)
    code = args._handler(args)
    return code, capsys.readouterr()


def test_host_capability_command_reports_no_host(capsys):
    code, captured = _run(["host-capability"], capsys)
    assert code == EXIT_OK
    record = json.loads(captured.out.strip())
    assert record["under_mcp_host"] is False
    assert record["host_wait_budget"] is None


def test_host_capability_command_reports_the_resolved_budget(monkeypatch, capsys):
    _as_client(monkeypatch, "codex-mcp-client")
    code, captured = _run(["host-capability"], capsys)

    assert code == EXIT_OK
    record = json.loads(captured.out.strip())
    assert record["under_mcp_host"] is True
    assert record["host_wait_budget"]["host"] == "codex"


def test_host_capability_command_requires_a_project_with_role(capsys):
    parser = build_parser()
    args = parser.parse_args(["host-capability", "--role", "coder"])
    code = args._handler(args)
    captured = capsys.readouterr()
    assert code != EXIT_OK
    assert "--project" in captured.err
