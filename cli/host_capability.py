"""Resolve the MCP host's tool-call wait budget from real evidence.

Cartopian's handoff model assumes one blocking wait call survives until the
assignee's report lands (``roles.<role>.timeout``, protocol default ``60m``).
Every MCP host imposes its own ceiling on a single ``tools/call``, and those
ceilings are *shorter than the protocol default on some hosts*. When the host
ceiling is the smaller number, the wait is killed mid-handoff and the PM sees a
transport error rather than a protocol outcome.

This module resolves that ceiling so the mismatch can be refused **before**
launch instead of discovered 300 seconds into a 60-minute handoff. It reports
evidence, never a guess: a host it does not recognize resolves to an unknown
budget, and unknown fails closed at the dispatch gate.

Two independent ceilings matter, and a host may impose either or both:

- **wall clock** — the hard limit on one ``tools/call``, measured from request
  to response. No known host extends this on ``notifications/progress``.
- **idle** — the limit on silence *between* messages. A host with an idle
  ceiling aborts a call that sends neither a response nor a progress
  notification inside the window, even when the wall-clock budget is generous.

The effective budget is the smaller of the two, because either one ends the
call. See ``protocol/CONVENTIONS.md § Handoffs`` for how the budget is
consumed.

Host identity comes from the MCP ``initialize`` handshake's ``clientInfo.name``,
which ``mcp_server.server`` exports into every in-process CLI invocation. Absent
that marker the CLI is not running under an MCP host at all (a direct shell
invocation, a wrapper, a test), no host-imposed ceiling exists, and the gate
does not apply.

One precedence step comes before clientInfo matching: a well-formed
``CARTOPIAN_MCP_HOST`` value in the server's own environment — written into
the host's registration entry by the install workflow, never a runtime
self-report — names the host directly. Hosts whose MCP client sends only the
SDK-default ``clientInfo`` (Hermes sends ``"mcp"``) are unmatchable by name;
the marker is validated against a closed set and an unknown value is ignored,
so fail-closed behavior is unchanged for everyone else.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cli import emit
from cli.bounded_run import CaptureOverflow, run_bounded

# Exported by mcp_server.server around each in-process tool invocation. Absent
# means "not running under an MCP host", which is distinct from "running under
# an unrecognized one" — the first has no host ceiling, the second has an
# unknown one.
# Presence marks an in-process MCP tool invocation even when the connected
# client omitted ``clientInfo``.  That omission is an *unknown connected host*,
# not a direct shell invocation.
CONNECTED_ENV = "CARTOPIAN_MCP_CONNECTED"
CLIENT_ENV = "CARTOPIAN_MCP_CLIENT"
CLIENT_VERSION_ENV = "CARTOPIAN_MCP_CLIENT_VERSION"
CLIENT_TITLE_ENV = "CARTOPIAN_MCP_CLIENT_TITLE"

# Registration-injected host identity marker (D12): written into the server
# entry's env map by the install workflow for hosts whose clientInfo is the
# unmatchable SDK default. Installer-written config, not runtime self-report;
# validated against the closed resolver set below, unknown values ignored.
HOST_MARKER_ENV = "CARTOPIAN_MCP_HOST"

# Registration-injected Hermes profile home (D12): Hermes's filtered stdio
# env never passes HERMES_HOME to a server process, so without this marker a
# capability read under a non-default profile would silently target the wrong
# config. Data, not trust — it only says where to read; anything unreadable
# falls through to an unknown ceiling and the dispatch gate refuses.
HERMES_HOME_ENV = "CARTOPIAN_HERMES_HOME"

# The name the operator registered this server under, which keys the per-server
# timeout entry in the host's own config. `skills/register-mcp.md` registers it
# as `cartopian` on every host; the env var covers a non-default registration.
SERVER_NAME_ENV = "CARTOPIAN_MCP_SERVER_NAME"
DEFAULT_SERVER_NAME = "cartopian"

# Marks the Cartopian server inside a host config when the registered name is
# not the default — matched against the entry's launch command.
_COMMAND_FINGERPRINT = "cartopian"

# Config duration grammar: a positive integer followed by a unit suffix.
_DURATION_RE = re.compile(r"^(\d+)([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class HostBudget:
    """One host's resolved tool-call ceilings, with the evidence behind them."""

    def __init__(
        self,
        *,
        host: str,
        display: str,
        client_name: Optional[str],
        client_version: Optional[str],
        wall_clock_seconds: Optional[int],
        idle_seconds: Optional[int],
        wall_clock_source: str,
        idle_source: str,
        progress_resets_wall_clock: bool,
        progress_resets_idle: bool,
        progress_channel_available: bool,
        evidence: List[str],
        remediation: List[str],
    ) -> None:
        self.host = host
        self.display = display
        self.client_name = client_name
        self.client_version = client_version
        self.wall_clock_seconds = wall_clock_seconds
        self.idle_seconds = idle_seconds
        self.wall_clock_source = wall_clock_source
        self.idle_source = idle_source
        self.progress_resets_wall_clock = progress_resets_wall_clock
        self.progress_resets_idle = progress_resets_idle
        self.progress_channel_available = progress_channel_available
        self.evidence = evidence
        self.remediation = remediation

    def _applicable_ceilings(self) -> List[Tuple[str, int]]:
        """Ceilings that can terminate this invocation's canonical wait."""
        ceilings: List[Tuple[str, int]] = []
        if self.wall_clock_seconds is not None and not (
            self.progress_channel_available and self.progress_resets_wall_clock
        ):
            ceilings.append(("wall-clock", self.wall_clock_seconds))
        if self.idle_seconds is not None and not (
            self.progress_channel_available and self.progress_resets_idle
        ):
            ceilings.append(("idle", self.idle_seconds))
        return ceilings

    @property
    def effective_seconds(self) -> Optional[int]:
        """The sustainable fixed ceiling for one canonical wait.

        A documented progress-resettable idle window is still reported in the
        capability record, but does not cap total wait duration only when this
        invocation actually has an MCP progress channel. Without that channel,
        the raw idle window remains applicable. Progress never extends a fixed
        wall-clock ceiling.

        ``None`` means either unknown (``host == "unknown"``) or no fixed
        ceiling for a known host.  Callers distinguish those through ``known``.
        """
        candidates = self._applicable_ceilings()
        return min(seconds for _label, seconds in candidates) if candidates else None

    @property
    def known(self) -> bool:
        return self.host != "unknown"

    def limiting_ceiling(self) -> Optional[str]:
        """Which ceiling is the binding constraint: ``wall-clock`` or ``idle``."""
        candidates = self._applicable_ceilings()
        if not candidates:
            return None
        # Stable wall-first ordering makes equal applicable ceilings report the
        # fixed wall constraint, while excluded resettable ceilings never label
        # a budget they did not determine.
        return min(candidates, key=lambda item: item[1])[0]

    def record(self) -> Dict[str, Any]:
        raw_candidates = [
            value
            for value in (self.wall_clock_seconds, self.idle_seconds)
            if value is not None
        ]
        return {
            "host": self.host,
            "host_display": self.display,
            "client_name": self.client_name,
            "client_version": self.client_version,
            "wall_clock_seconds": self.wall_clock_seconds,
            "wall_clock_source": self.wall_clock_source,
            "idle_seconds": self.idle_seconds,
            "idle_source": self.idle_source,
            "raw_smallest_ceiling_seconds": (
                min(raw_candidates) if raw_candidates else None
            ),
            "effective_wait_budget_seconds": self.effective_seconds,
            "limiting_ceiling": self.limiting_ceiling(),
            "progress_resets_wall_clock": self.progress_resets_wall_clock,
            "progress_resets_idle": self.progress_resets_idle,
            "progress_channel_available": self.progress_channel_available,
            "evidence": self.evidence,
            "remediation": self.remediation,
        }


def _server_name() -> str:
    return os.environ.get(SERVER_NAME_ENV) or DEFAULT_SERVER_NAME


def _positive_int(raw: Any) -> Optional[int]:
    """Coerce a config value to a positive whole number of seconds.

    Rejects zero and negatives: a host config cannot express "no ceiling" as a
    smaller-than-real number, and treating ``0`` as unbounded here would invent
    headroom the host does not grant. Idle-disabling zeros are handled by their
    own host-specific reader.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, float):
        if not raw.is_integer():
            return None
        value = int(raw)
    elif isinstance(raw, str) and re.fullmatch(r"\d+", raw.strip()):
        value = int(raw.strip())
    else:
        return None
    return value if value > 0 else None


def _env_milliseconds(name: str) -> Tuple[Optional[int], bool]:
    """Read a millisecond env var as whole seconds.

    Returns ``(seconds, present)``. ``present`` distinguishes an unset variable
    from one explicitly set to ``0``, which several hosts read as "disable this
    check" rather than "abort immediately".
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None, False
    try:
        value = int(float(raw.strip()))
    except ValueError:
        return None, False
    if value <= 0:
        return None, True  # explicitly disabled
    return max(1, value // 1000), True


# ---------------------------------------------------------------------------
# Per-host resolvers
#
# Each returns the host's ceilings plus the evidence that produced them. The
# defaults below are the hosts' own defaults, read from their documentation or
# source; a resolver upgrades a default to a configured value whenever the
# host's own config is readable from here.
# ---------------------------------------------------------------------------

# Codex reads `tool_timeout_sec` per MCP server from config.toml
# (codex-rs `DEFAULT_TOOL_TIMEOUT`, 300s, applied when the key is absent). Its
# MCP client logs `notifications/progress` and does nothing else with them, so
# progress extends neither ceiling. Codex imposes no idle ceiling.
CODEX_DEFAULT_WALL_SECONDS = 300

# Claude Code's wall-clock ceiling defaults to MCP_TOOL_TIMEOUT (~28h when
# unset) and its stdio idle window to 30 minutes. A response *or* a progress
# notification resets the idle window; nothing resets the wall clock.
CLAUDE_CODE_DEFAULT_WALL_SECONDS = 100_800
CLAUDE_CODE_DEFAULT_IDLE_SECONDS = 1_800

# Antigravity (agy) imposes a hard wall clock on each MCP tools/call.
# Measured against agy 1.1.11 (2026-08-10): a call dies at exactly 3m0s
# ("MCP tool call to server %q timed out after %s: context deadline
# exceeded"), progress notifications do not extend it, and no configuration
# surface changes it — the central mcp_config.json accepts no per-server
# timeout key (the Gemini CLI-era `timeout` ms key is retired upstream), and
# the binary defines no environment override. Re-verify on agy upgrades.
ANTIGRAVITY_DEFAULT_WALL_SECONDS = 180

# opencode passes `resetTimeoutOnProgress: true` with an installed `onprogress`
# hook and no `maxTotalTimeout` (mcp/catalog.ts), so it imposes an idle ceiling
# only — never a wall clock — and a progress notification resets it. The idle
# window is `mcp.<server>.timeout` ?? `experimental.mcp_timeout` ?? the MCP SDK
# default of 60s (mcp/index.ts requestTimeout). Both config keys are stored in
# milliseconds.
OPENCODE_DEFAULT_IDLE_SECONDS = 60


def _codex_config_path() -> Path:
    override = os.environ.get("CODEX_HOME")
    if override:
        return Path(override) / "config.toml"
    return Path.home() / ".codex" / "config.toml"


def _load_toml(path: Path) -> Optional[Dict[str, Any]]:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover — Python < 3.11 is unsupported
        return None
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, ValueError):
        return None


def _codex_server_entry(config: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Find this server's `[mcp_servers.*]` table and the name it is under.

    Prefers the registered name, then falls back to any entry whose launch
    command points at a Cartopian binary — so an operator who registered the
    server under a different name still gets a real reading instead of a
    silently-defaulted one.
    """
    servers = config.get("mcp_servers")
    if not isinstance(servers, dict):
        return None, None
    name = _server_name()
    entry = servers.get(name)
    if isinstance(entry, dict):
        return entry, name
    for candidate_name, candidate in servers.items():
        if not isinstance(candidate, dict):
            continue
        command = str(candidate.get("command") or "")
        if _COMMAND_FINGERPRINT in command.lower():
            return candidate, candidate_name
    return None, None


def _resolve_codex(client_name: str, client_version: Optional[str]) -> HostBudget:
    path = _codex_config_path()
    wall = CODEX_DEFAULT_WALL_SECONDS
    source = "host-default"
    evidence: List[str] = []
    config = _load_toml(path) if path.is_file() else None
    if config is None:
        evidence.append(f"{path}: unreadable or absent; using the Codex default")
    else:
        entry, entry_name = _codex_server_entry(config)
        if entry is None:
            evidence.append(
                f"{path}: no [mcp_servers.*] entry matched; using the Codex default"
            )
        else:
            configured = _positive_int(entry.get("tool_timeout_sec"))
            if configured is None:
                evidence.append(
                    f"{path}: [mcp_servers.{entry_name}] sets no tool_timeout_sec; "
                    f"using the Codex default"
                )
            else:
                wall = configured
                source = "host-config"
                evidence.append(
                    f"{path}: [mcp_servers.{entry_name}] tool_timeout_sec = {configured}"
                )
    return HostBudget(
        host="codex",
        display="Codex (CLI / desktop)",
        client_name=client_name,
        client_version=client_version,
        wall_clock_seconds=wall,
        idle_seconds=None,
        wall_clock_source=source,
        idle_source="not-imposed",
        progress_resets_wall_clock=False,
        progress_resets_idle=False,
        progress_channel_available=emit.progress_available(),
        evidence=evidence,
        remediation=[
            f"raise the ceiling: add `tool_timeout_sec = <seconds>` under "
            f"[mcp_servers.{_server_name()}] in {path}, then restart Codex",
            "or lower `roles.<role>.timeout` in cartopian.toml to fit the ceiling",
        ],
    )


def _resolve_claude_code(client_name: str, client_version: Optional[str]) -> HostBudget:
    wall_env, wall_present = _env_milliseconds("MCP_TOOL_TIMEOUT")
    if wall_present and wall_env is None:
        # Explicitly zeroed: Claude Code treats this as no wall-clock ceiling.
        wall: Optional[int] = None
        wall_source = "host-config-disabled"
        wall_evidence = "MCP_TOOL_TIMEOUT=0 — wall-clock ceiling disabled"
    elif wall_env is not None:
        wall = wall_env
        wall_source = "host-config"
        wall_evidence = f"MCP_TOOL_TIMEOUT={wall_env}s (from the environment)"
    else:
        wall = CLAUDE_CODE_DEFAULT_WALL_SECONDS
        wall_source = "host-default"
        wall_evidence = "MCP_TOOL_TIMEOUT unset; using the Claude Code default"

    idle_env, idle_present = _env_milliseconds("CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT")
    if idle_present and idle_env is None:
        idle: Optional[int] = None
        idle_source = "host-config-disabled"
        idle_evidence = "CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT=0 — idle check disabled"
    elif idle_env is not None:
        idle = idle_env
        idle_source = "host-config"
        idle_evidence = (
            f"CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT={idle_env}s (from the environment)"
        )
    else:
        idle = CLAUDE_CODE_DEFAULT_IDLE_SECONDS
        idle_source = "host-default"
        idle_evidence = (
            "CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT unset; using the Claude Code "
            "stdio default"
        )

    return HostBudget(
        host="claude-code",
        display="Claude Code",
        client_name=client_name,
        client_version=client_version,
        wall_clock_seconds=wall,
        idle_seconds=idle,
        wall_clock_source=wall_source,
        idle_source=idle_source,
        progress_resets_wall_clock=False,
        # A progress notification counts as traffic for the idle check, so a
        # wait that emits them keeps the call alive against the idle ceiling.
        progress_resets_idle=True,
        progress_channel_available=emit.progress_available(),
        evidence=[wall_evidence, idle_evidence],
        remediation=[
            "raise the idle window: set CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT to a "
            "millisecond value above the role timeout (or 0 to disable it) in "
            "the environment Claude Code launches with",
            "raise the wall clock: set MCP_TOOL_TIMEOUT (milliseconds), or add "
            "a per-server `timeout` to the .mcp.json entry",
            "or lower `roles.<role>.timeout` in cartopian.toml to fit the ceiling",
        ],
    )


def _resolve_antigravity(client_name: str, client_version: Optional[str]) -> HostBudget:
    """Antigravity CLI (agy) — clientInfo.name `antigravity-client`.

    The ceiling is a fixed host constant: agy exposes no per-server timeout
    key in its central MCP config (`~/.gemini/config/mcp_config.json`) and no
    environment override, so there is nothing to read and nothing to raise.
    Canonical waits on this host must slice observation with `max_block`
    below the ceiling instead of holding one long blocking call.
    """
    return HostBudget(
        host="antigravity",
        display="Antigravity (agy)",
        client_name=client_name,
        client_version=client_version,
        wall_clock_seconds=ANTIGRAVITY_DEFAULT_WALL_SECONDS,
        idle_seconds=None,
        wall_clock_source="host-default",
        idle_source="not-imposed",
        progress_resets_wall_clock=False,
        progress_resets_idle=False,
        progress_channel_available=emit.progress_available(),
        evidence=[
            "Antigravity (agy) enforces a fixed 180s wall clock per MCP "
            "tools/call; progress notifications do not extend it, and its "
            "central MCP config exposes no per-server timeout key "
            "(verified against agy 1.1.11)"
        ],
        remediation=[
            "the Antigravity ceiling cannot be raised: for a longer role, "
            "launch it manually and observe with `max_block` below 180s "
            "(e.g. 2m) until a terminal outcome",
            "or lower `roles.<role>.timeout` in cartopian.toml so automatic "
            "dispatch and its terminal wait fit the ceiling",
        ],
    )


def _opencode_config_layers() -> List[Tuple[str, Path]]:
    """Every inspectable opencode config *file* layer, in load order.

    Mirrors the opencode v1.18.15 loader: global pair (`opencode.json` then
    `opencode.jsonc`, later-loaded wins) → `$OPENCODE_CONFIG` (explicit file)
    → project pair → `$OPENCODE_CONFIG_DIR` pair. Inline and managed layers are
    not files and are handled by the caller.
    """
    layers: List[Tuple[str, Path]] = []
    xdg = os.environ.get("XDG_CONFIG_HOME")
    global_dir = (
        Path(xdg).expanduser() if xdg else Path.home() / ".config"
    ) / "opencode"
    layers.append(("global", global_dir / "opencode.json"))
    layers.append(("global", global_dir / "opencode.jsonc"))
    explicit = os.environ.get("OPENCODE_CONFIG")
    if explicit:
        layers.append(("OPENCODE_CONFIG", Path(explicit).expanduser()))
    project_dir = _opencode_project_config_dir()
    if project_dir is not None:
        layers.append(("project", project_dir / "opencode.json"))
        layers.append(("project", project_dir / "opencode.jsonc"))
    config_dir = os.environ.get("OPENCODE_CONFIG_DIR")
    if config_dir:
        base = Path(config_dir).expanduser()
        layers.append(("OPENCODE_CONFIG_DIR", base / "opencode.json"))
        layers.append(("OPENCODE_CONFIG_DIR", base / "opencode.jsonc"))
    return layers


def _opencode_project_config_dir() -> Optional[Path]:
    """Nearest ancestor of the working directory carrying an opencode config."""
    try:
        current = Path.cwd()
    except OSError:
        return None
    for candidate in (current, *current.parents):
        if (candidate / "opencode.json").is_file() or (
            candidate / "opencode.jsonc"
        ).is_file():
            return candidate
    return None


def _opencode_timeouts(data: Dict[str, Any]) -> Tuple[Optional[Any], Optional[Any], bool]:
    """Extract (server timeout, experimental timeout, server entry present)."""
    server_timeout: Optional[Any] = None
    entry_present = False
    servers = data.get("mcp")
    if isinstance(servers, dict):
        name = _server_name()
        entry = servers.get(name)
        if not isinstance(entry, dict):
            entry = None
            for candidate in servers.values():
                if not isinstance(candidate, dict):
                    continue
                command = candidate.get("command")
                if isinstance(command, list):
                    haystack = " ".join(str(part) for part in command)
                else:
                    haystack = str(command or "")
                if _COMMAND_FINGERPRINT in haystack.lower():
                    entry = candidate
                    break
        if isinstance(entry, dict):
            entry_present = True
            if "timeout" in entry:
                server_timeout = entry.get("timeout")
    experimental_timeout: Optional[Any] = None
    experimental = data.get("experimental")
    if isinstance(experimental, dict) and "mcp_timeout" in experimental:
        experimental_timeout = experimental.get("mcp_timeout")
    return server_timeout, experimental_timeout, entry_present


def _resolve_opencode(client_name: str, client_version: Optional[str]) -> HostBudget:
    """D8 effective-configuration resolver for opencode's idle ceiling.

    Merges every inspectable file layer in stack order (later over earlier),
    then resolves `mcp.<server>.timeout` ?? `experimental.mcp_timeout` ??
    the 60s SDK default from the merged result. Fails closed: a layer that is
    present but not strictly parseable could carry either key under a
    higher-precedence position, so the ceiling is reported unknown rather than
    invented.
    """
    evidence: List[str] = []
    unreadable: List[str] = []
    server_timeout_raw: Optional[Any] = None
    server_timeout_path: Optional[Path] = None
    experimental_raw: Optional[Any] = None
    experimental_path: Optional[Path] = None
    consulted = 0
    for _label, path in _opencode_config_layers():
        if not path.exists():
            continue
        consulted += 1
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            unreadable.append(str(path))
            continue
        if not isinstance(data, dict):
            unreadable.append(str(path))
            continue
        server_value, experimental_value, _present = _opencode_timeouts(data)
        if server_value is not None:
            server_timeout_raw = server_value
            server_timeout_path = path
        if experimental_value is not None:
            experimental_raw = experimental_value
            experimental_path = path
    inline = os.environ.get("OPENCODE_CONFIG_CONTENT")
    if inline and inline.strip():
        consulted += 1
        try:
            data = json.loads(inline)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            unreadable.append("OPENCODE_CONFIG_CONTENT (inline configuration)")
        else:
            server_value, experimental_value, _present = _opencode_timeouts(data)
            if server_value is not None:
                server_timeout_raw = server_value
                server_timeout_path = None
                evidence.append(
                    "OPENCODE_CONFIG_CONTENT sets an inline per-server timeout"
                )
            if experimental_value is not None:
                experimental_raw = experimental_value
                experimental_path = None

    name = _server_name()
    remediation_dir = (
        Path(os.environ.get("OPENCODE_CONFIG_DIR")).expanduser()
        if os.environ.get("OPENCODE_CONFIG_DIR")
        else (
            Path(os.environ["XDG_CONFIG_HOME"]).expanduser()
            if os.environ.get("XDG_CONFIG_HOME")
            else Path.home() / ".config"
        )
        / "opencode"
    )
    remediation = [
        f'raise the ceiling: set "timeout" (milliseconds) on the {name} entry '
        f'under "mcp" in {remediation_dir / "opencode.json"}, then restart opencode',
        "or lower `roles.<role>.timeout` in cartopian.toml to fit the ceiling",
    ]

    if unreadable:
        # Fail closed: an uninspectable layer may override either timeout key.
        evidence.append(
            "opencode configuration layer(s) could not be strictly read, so "
            "the effective idle ceiling cannot be resolved: "
            + "; ".join(unreadable)
        )
        return HostBudget(
            host="opencode",
            display="opencode (CLI / TUI)",
            client_name=client_name,
            client_version=client_version,
            wall_clock_seconds=None,
            idle_seconds=None,
            wall_clock_source="not-imposed",
            idle_source="unknown",
            progress_resets_wall_clock=False,
            progress_resets_idle=True,
            progress_channel_available=emit.progress_available(),
            evidence=evidence,
            remediation=remediation,
        )

    idle = OPENCODE_DEFAULT_IDLE_SECONDS
    source = "host-default"
    if server_timeout_raw is not None:
        configured = _positive_int(server_timeout_raw)
        if configured is None:
            evidence.append(
                f"mcp.{name}.timeout is not a positive integer; "
                "using the opencode default"
            )
        else:
            idle = max(1, configured // 1000)
            source = "host-config"
            evidence.append(
                f"{server_timeout_path or 'inline configuration'}: "
                f"mcp.{name}.timeout = {server_timeout_raw}ms"
            )
    elif experimental_raw is not None:
        configured = _positive_int(experimental_raw)
        if configured is None:
            evidence.append(
                "experimental.mcp_timeout is not a positive integer; "
                "using the opencode default"
            )
        else:
            idle = max(1, configured // 1000)
            source = "host-config"
            evidence.append(
                f"{experimental_path or 'inline configuration'}: "
                f"experimental.mcp_timeout = {experimental_raw}ms"
            )
    if source == "host-default":
        evidence.append(
            "no opencode configuration sets a Cartopian tool-call timeout "
            f"({consulted} layer(s) consulted); using the MCP SDK default"
        )
    return HostBudget(
        host="opencode",
        display="opencode (CLI / TUI)",
        client_name=client_name,
        client_version=client_version,
        wall_clock_seconds=None,
        idle_seconds=idle,
        wall_clock_source="not-imposed",
        idle_source=source,
        progress_resets_wall_clock=False,
        # opencode passes resetTimeoutOnProgress with an onprogress hook and no
        # maxTotalTimeout, so a progress notification resets the idle window
        # and no wall clock exists.
        progress_resets_idle=True,
        progress_channel_available=emit.progress_available(),
        evidence=evidence,
        remediation=remediation,
    )


# Hermes enforces a HARD WALL-CLOCK total timeout per tool call around
# session.call_tool (mcp_servers.<name>.timeout ?? 300 s;
# tools/mcp_tool.py:338,3150,5302) with no progress callback — nothing
# resets it, so it is wall_clock_seconds, idle_seconds=None.
# idle_timeout_seconds / max_lifetime_seconds recycle the server process
# between calls — evidence only, never a ceiling.
HERMES_DEFAULT_CALL_TIMEOUT_SECONDS = 300

# Subprocess hygiene for the Hermes config read, mirroring the registration
# adapter's posture: fixed timeout, stdin closed, bounded capture, shell-free.
_HERMES_READ_TIMEOUT_SECONDS = 30
_HERMES_READ_MAX_BYTES = 1_000_000

# Hermes hands each tool wrapper its timeout when the session's MCP handler
# is created (session start / /reload-mcp), not when a call is made: a later
# `hermes config set ...timeout` does not reach the running session until it
# reloads, and a reload also respawns this server process. Cartopian therefore
# snapshots the entry when this MCP server process starts, before accepting
# requests, and pins even an unknown result for the process lifetime. Reading
# later at first dispatch could observe a freshly raised value the already-
# running Hermes handler does not honor and approve a wait the host kills
# early.
_HERMES_SESSION_CEILINGS: Dict[str, Tuple[str, Optional[int], Tuple[str, ...]]] = {}


def snapshot_process_host_budget() -> None:
    """Pin startup-time host evidence whose lifetime is the server process.

    Most host budgets come from environment or static config that the host
    supplies directly. Hermes is different: Cartopian must query Hermes's
    profile-scoped registration, while Hermes itself captured that entry's
    timeout immediately before spawning this server. Snapshotting here keeps
    both sides on the same configuration generation. An unreadable outcome is
    pinned too; repairing disk config requires the same new-session or
    ``/reload-mcp`` boundary as changing a resolved timeout.
    """
    marker = os.environ.get(HOST_MARKER_ENV, "").strip().lower()
    if marker != "hermes":
        return
    name = _server_name()
    if name in _HERMES_SESSION_CEILINGS:
        return
    source, wall, evidence = _hermes_entry_timeout_now(name)
    evidence = evidence + [
        "ceiling snapshotted when this Cartopian MCP server process started: "
        "Hermes captures each tool's timeout when the session's MCP handler "
        "is created, so any later config edit takes effect only after a new "
        "Hermes session or /reload-mcp (which restarts this server)"
    ]
    _HERMES_SESSION_CEILINGS[name] = (source, wall, tuple(evidence))


def _hermes_entry_timeout(name: str) -> Tuple[str, Optional[int], List[str]]:
    """Return the session-pinned Hermes ceiling.

    The MCP entry point calls :func:`snapshot_process_host_budget` before it
    accepts requests. The lazy branch is retained for direct library callers;
    it pins resolved values while leaving an unknown value retryable because
    those callers have no Hermes session boundary. Production MCP dispatch
    always observes the startup snapshot, including a pinned unknown result.
    """
    cached = _HERMES_SESSION_CEILINGS.get(name)
    if cached is not None:
        source, wall, evidence = cached
        return source, wall, list(evidence)
    source, wall, evidence = _hermes_entry_timeout_now(name)
    if source != "unknown":
        evidence = evidence + [
            "ceiling pinned for this server process by a direct library "
            "caller; MCP server entry points snapshot this value before "
            "accepting requests"
        ]
        _HERMES_SESSION_CEILINGS[name] = (source, wall, tuple(evidence))
    return source, wall, list(evidence)


def _hermes_entry_timeout_now(
    name: str,
) -> Tuple[str, Optional[int], List[str]]:
    """Read ``mcp_servers.<name>`` via ``hermes config get --json``.

    Returns ``(source, wall_clock_seconds, evidence)`` where source is
    ``host-config``, ``host-default``, or ``unknown`` (fail closed: a missing
    CLI, an unusable profile-home marker, a hanging or flooding subprocess, or
    unparseable output all mean the ceiling cannot be resolved).
    """
    evidence: List[str] = []
    executable = shutil.which("hermes")
    if executable is None:
        return "unknown", None, ["the 'hermes' CLI is not on PATH, so the "
                                 "registered tool-call ceiling cannot be read"]
    env = None
    pin: Tuple[str, ...] = ()
    marker_home = os.environ.get(HERMES_HOME_ENV, "").strip()
    if marker_home:
        home = Path(marker_home).expanduser()
        if not home.is_absolute() or not home.is_dir():
            return "unknown", None, [
                f"{HERMES_HOME_ENV}={marker_home!r} is not an existing "
                "absolute directory, so the registering profile's config "
                "cannot be read"
            ]
        # Hermes's own filtered stdio env never delivers HERMES_HOME; the
        # registration-injected marker names the home the read must target.
        env = {**os.environ, "HERMES_HOME": str(home)}
        if home.parent.name == "profiles":
            # Hermes's -p pre-parse trusts a profile-parented HERMES_HOME
            # verbatim (it returns before consulting the sticky
            # active_profile), so the env alone pins the read.
            evidence.append(
                f"reading the profile home from {HERMES_HOME_ENV}={home}"
            )
        else:
            # A root/default HERMES_HOME does NOT pin by itself: Hermes
            # intentionally lets the sticky active_profile override it. The
            # explicit `-p default` identity names the root profile, and it
            # resolves to the marker home even when that is a custom root.
            pin = ("-p", "default")
            evidence.append(
                f"reading the profile home from {HERMES_HOME_ENV}={home}, "
                "pinned with `-p default` so the sticky active_profile "
                "cannot redirect the read"
            )
    try:
        returncode, stdout_bytes, stderr_bytes = run_bounded(
            [executable, *pin, "config", "get", "--json", f"mcp_servers.{name}"],
            timeout=_HERMES_READ_TIMEOUT_SECONDS,
            max_bytes=_HERMES_READ_MAX_BYTES,
            env=env,
        )
    except CaptureOverflow:
        # The bound is enforced while the child runs: a flooding process is
        # killed at the cap instead of buffered into memory first.
        return "unknown", None, evidence + [
            "`hermes config get` produced an implausibly large capture and "
            "was killed; refusing to parse it"
        ]
    except (subprocess.TimeoutExpired, OSError) as exc:
        return "unknown", None, evidence + [
            f"`hermes config get --json mcp_servers.{name}` failed: {exc}"
        ]
    stdout = stdout_bytes.decode("utf-8", "replace")
    stderr = stderr_bytes.decode("utf-8", "replace")
    if returncode != 0:
        if (
            returncode == 1
            and "config key not set" in (stdout + stderr).lower()
        ):
            evidence.append(
                f"no mcp_servers.{name} entry; using the Hermes default"
            )
            return "host-default", HERMES_DEFAULT_CALL_TIMEOUT_SECONDS, evidence
        return "unknown", None, evidence + [
            f"`hermes config get --json mcp_servers.{name}` exited "
            f"{returncode} without a recognizable outcome"
        ]
    try:
        entry = json.loads(stdout)
    except ValueError:
        return "unknown", None, evidence + [
            f"mcp_servers.{name} did not parse as JSON; the configured "
            "ceiling cannot be resolved"
        ]
    if not isinstance(entry, dict):
        return "unknown", None, evidence + [
            f"mcp_servers.{name} is not an object; the configured ceiling "
            "cannot be resolved"
        ]
    if "timeout" not in entry:
        evidence.append(
            f"mcp_servers.{name} sets no timeout; using the Hermes default"
        )
        return "host-default", HERMES_DEFAULT_CALL_TIMEOUT_SECONDS, evidence
    configured = _positive_int(entry.get("timeout"))
    if configured is None:
        # Fail closed, never substitute the default: Hermes hands the
        # configured value to its own timeout machinery as-is, so a malformed
        # value does not behave like an absent one — the call may die
        # immediately rather than at 300s.
        return "unknown", None, evidence + [
            f"mcp_servers.{name}.timeout is set but is not a positive "
            "integer, so the configured ceiling cannot be resolved; fix or "
            "remove the timeout key"
        ]
    evidence.append(f"mcp_servers.{name}.timeout = {configured}s")
    return "host-config", configured, evidence


def _resolve_hermes(client_name: str, client_version: Optional[str]) -> HostBudget:
    """D6 resolver for Hermes's hard wall-clock per-call ceiling.

    Hermes wraps ``session.call_tool`` in a fixed total timeout with no
    progress callback: nothing resets it, so the ceiling is wall-clock, an
    idle ceiling is not imposed, and progress notifications buy nothing.
    Anything ambiguous reports ``wall_clock_source = "unknown"``, which the
    dispatch gate refuses.
    """
    name = _server_name()
    source, wall, evidence = _hermes_entry_timeout(name)
    return HostBudget(
        host="hermes",
        display="Hermes (CLI)",
        client_name=client_name,
        client_version=client_version,
        wall_clock_seconds=wall,
        idle_seconds=None,
        wall_clock_source=source,
        idle_source="not-imposed",
        progress_resets_wall_clock=False,
        progress_resets_idle=False,
        progress_channel_available=emit.progress_available(),
        evidence=evidence,
        remediation=[
            f"raise the ceiling: `hermes config set mcp_servers.{name}.timeout "
            f"<seconds>` (seconds, hard wall clock per call), then start a new "
            f"Hermes session or run /reload-mcp",
            "or lower `roles.<role>.timeout` in cartopian.toml to fit the ceiling",
        ],
    )


def _resolve_unknown(client_name: str, client_version: Optional[str]) -> HostBudget:
    return HostBudget(
        host="unknown",
        display=client_name or "unrecognized MCP host",
        client_name=client_name,
        client_version=client_version,
        wall_clock_seconds=None,
        idle_seconds=None,
        wall_clock_source="unknown",
        idle_source="unknown",
        progress_resets_wall_clock=False,
        progress_resets_idle=False,
        progress_channel_available=emit.progress_available(),
        evidence=[
            f"MCP client {client_name!r} is not in the known-host table, so its "
            f"tool-call ceilings cannot be read"
        ],
        remediation=[
            "dispatch this role manually and monitor the report path yourself",
            "or lower `roles.<role>.timeout` below the host's documented "
            "tools/call ceiling and confirm a blocking call of that length survives",
        ],
    )


# clientInfo.name → resolver. Matched as a case-insensitive substring, longest
# pattern first, because hosts version their client names (Codex identifies as
# `codex-mcp-client`). A host absent from this table resolves to `unknown` and
# fails the dispatch gate rather than inheriting some other host's numbers.
# Hermes deliberately has NO entry here: it identifies as the SDK default
# clientInfo name "mcp", and matching that substring would claim every
# default-SDK client. Hermes resolves only through the registration-injected
# CARTOPIAN_MCP_HOST marker below.
_HOST_MATCHERS: Tuple[Tuple[str, Any], ...] = (
    ("antigravity-client", _resolve_antigravity),
    ("codex-mcp-client", _resolve_codex),
    ("claude-code", _resolve_claude_code),
    ("antigravity", _resolve_antigravity),
    ("opencode", _resolve_opencode),
    ("codex", _resolve_codex),
)

# Closed set of host ids a CARTOPIAN_MCP_HOST marker may name. A well-formed
# marker beats clientInfo matching; an unknown value is ignored and resolution
# falls through to clientInfo, so nothing outside this set gains a budget.
_MARKER_RESOLVERS: Dict[str, Any] = {
    "codex": _resolve_codex,
    "claude-code": _resolve_claude_code,
    "antigravity": _resolve_antigravity,
    "opencode": _resolve_opencode,
    "hermes": _resolve_hermes,
}


def under_mcp_host() -> bool:
    """True when this CLI call is an in-process MCP tool invocation."""
    return bool(os.environ.get(CONNECTED_ENV, "").strip())


def resolve_host_budget() -> Optional[HostBudget]:
    """Resolve the current MCP host's wait budget.

    Returns ``None`` when the CLI is not running under an MCP host — a direct
    shell invocation has no host-imposed ``tools/call`` ceiling, so there is
    nothing to gate on. Under a host, always returns a budget; an unrecognized
    client yields the ``unknown`` budget, whose ``effective_seconds`` is
    ``None`` and which callers must treat as fail-closed.
    """
    if not under_mcp_host():
        return None
    client_name = os.environ.get(CLIENT_ENV, "").strip()
    client_version = os.environ.get(CLIENT_VERSION_ENV, "").strip() or None
    marker = os.environ.get(HOST_MARKER_ENV, "").strip().lower()
    if marker:
        resolver = _MARKER_RESOLVERS.get(marker)
        if resolver is not None:
            # Registration-injected identity beats clientInfo matching (D12);
            # an unknown marker value falls through to clientInfo instead.
            return resolver(client_name or marker, client_version)
    if not client_name:
        return _resolve_unknown("", None)
    haystack = f"{client_name} {os.environ.get(CLIENT_TITLE_ENV, '')}".lower()
    for pattern, resolver in _HOST_MATCHERS:
        if pattern in haystack:
            return resolver(client_name, client_version)
    return _resolve_unknown(client_name, client_version)


def parse_duration(raw: str) -> Optional[int]:
    """Parse the config duration grammar into whole seconds, or None.

    Accepts ``<positive-int><unit>`` where unit is one of s/m/h/d. This is the
    single grammar shared by ``roles.<role>.timeout`` and the wait primitives'
    ``--max-block``.
    """
    match = _DURATION_RE.match(raw.strip())
    if not match:
        return None
    value = int(match.group(1))
    if value <= 0:
        return None
    return value * _UNIT_SECONDS[match.group(2)]


def format_duration(seconds: Optional[int]) -> str:
    """Render whole seconds in the config's own duration grammar (e.g. ``60m``)."""
    if seconds is None:
        return "unknown"
    for unit_seconds, suffix in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= unit_seconds and seconds % unit_seconds == 0:
            return f"{seconds // unit_seconds}{suffix}"
    return f"{seconds}s"


def _remediation_for_context(budget: HostBudget, context: str) -> List[str]:
    """Return remedies that are actionable at the caller's exact boundary."""
    if budget.host != "antigravity":
        return budget.remediation
    if context == "dispatch":
        return [
            "lower `roles.<role>.timeout` in cartopian.toml to fit the 180s "
            "ceiling",
            "or launch this role manually, then observe its report with "
            "`max_block` below 180s (e.g. 2m), repeating until a terminal "
            "outcome",
        ]
    return [
        "retry this wait with `max_block` below 180s (e.g. 2m), repeating "
        "until a terminal outcome",
        "or lower `roles.<role>.timeout` in cartopian.toml to fit the ceiling",
    ]


def check_wait_budget(
    role: str, role_timeout_seconds: int, *, context: str = "wait"
) -> Tuple[bool, Optional[HostBudget], Optional[str]]:
    """Decide whether a blocking wait of ``role_timeout_seconds`` can survive.

    Returns ``(ok, budget, refusal)``. ``ok`` is True when no host ceiling
    applies (not under an MCP host) or the ceiling accommodates the role
    timeout. Otherwise ``refusal`` carries an operator-actionable message
    naming both the mismatch and every way to resolve it.
    """
    budget = resolve_host_budget()
    if budget is None:
        return True, None, None

    effective = budget.effective_seconds
    if effective is None and not budget.known:
        detail = "; ".join(budget.evidence)
        options = "\n".join(
            f"  - {item}" for item in _remediation_for_context(budget, context)
        )
        return (
            False,
            budget,
            (
                f"host wait budget is unknown, so a blocking wait of "
                f"{format_duration(role_timeout_seconds)} for role {role!r} cannot "
                f"be guaranteed to survive. {detail}.\nResolve by one of:\n{options}"
            ),
        )

    unresolved = [
        label
        for label, source in (
            ("wall-clock", budget.wall_clock_source),
            ("idle", budget.idle_source),
        )
        if source == "unknown"
    ]
    if unresolved:
        # A known host whose ceiling could not be resolved fails closed: the
        # host imposes a ceiling, but an uninspectable configuration layer may
        # set it below the role timeout.
        detail = "; ".join(budget.evidence)
        options = "\n".join(
            f"  - {item}" for item in _remediation_for_context(budget, context)
        )
        return (
            False,
            budget,
            (
                f"{budget.display} imposes a {' and '.join(unresolved)} ceiling "
                f"whose configured value cannot be resolved, so a blocking wait "
                f"of {format_duration(role_timeout_seconds)} for role {role!r} "
                f"cannot be guaranteed to survive. {detail}.\n"
                f"Resolve by one of:\n{options}"
            ),
        )

    if effective is None or role_timeout_seconds <= effective:
        return True, budget, None

    ceiling = budget.limiting_ceiling()
    detail = "; ".join(budget.evidence)
    options = "\n".join(
        f"  - {item}" for item in _remediation_for_context(budget, context)
    )
    return (
        False,
        budget,
        (
            f"roles.{role}.timeout is {format_duration(role_timeout_seconds)} but "
            f"{budget.display} ends a tools/call at "
            f"{format_duration(effective)} ({ceiling} ceiling), so the wait for "
            f"this handoff would be killed before the report could land. "
            f"{detail}.\nResolve by one of:\n{options}"
        ),
    )
