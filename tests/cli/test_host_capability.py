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

from cli import host_capability
from cli.main import EXIT_OK, build_parser


@pytest.fixture(autouse=True)
def clean_host_env(monkeypatch):
    """Start every test outside an MCP host, with no host config leaking in."""
    for name in (
        host_capability.CLIENT_ENV,
        host_capability.CLIENT_VERSION_ENV,
        host_capability.CLIENT_TITLE_ENV,
        host_capability.SERVER_NAME_ENV,
        "MCP_TOOL_TIMEOUT",
        "CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT",
        "CODEX_HOME",
    ):
        monkeypatch.delenv(name, raising=False)


def _as_client(monkeypatch, name, *, title=None, version=None):
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


# --- Claude Code -----------------------------------------------------------


def test_claude_code_idle_window_is_the_binding_ceiling(monkeypatch):
    """Claude Code's wall clock is generous; its 30m stdio idle window is not."""
    _as_client(monkeypatch, "claude-code")

    budget = host_capability.resolve_host_budget()
    assert budget.host == "claude-code"
    assert budget.wall_clock_seconds == 100_800
    assert budget.idle_seconds == 1_800
    assert budget.effective_seconds == 1_800
    assert budget.limiting_ceiling() == "idle"
    # Progress notifications count as traffic against the idle timer only.
    assert budget.progress_resets_idle is True
    assert budget.progress_resets_wall_clock is False


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


def test_gemini_default_ceiling(monkeypatch):
    _as_client(monkeypatch, "gemini-cli")
    budget = host_capability.resolve_host_budget()
    assert budget.host == "gemini-cli"
    assert budget.effective_seconds == 600


# --- Unknown host: fail closed --------------------------------------------


def test_unrecognized_host_resolves_to_unknown_not_to_a_default(monkeypatch):
    _as_client(monkeypatch, "some-brand-new-ide")
    budget = host_capability.resolve_host_budget()

    assert budget.host == "unknown"
    assert budget.known is False
    # Unknown is not unbounded: no number may be invented for it.
    assert budget.effective_seconds is None
    assert budget.wall_clock_seconds is None


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
