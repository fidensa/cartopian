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
"""
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cli import emit

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

# Gemini CLI reads `timeout` (milliseconds) per entry in settings.json
# `mcpServers`, defaulting to 600000ms.
GEMINI_DEFAULT_WALL_SECONDS = 600


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


def _gemini_settings_paths() -> List[Path]:
    return [Path.home() / ".gemini" / "settings.json"]


def _resolve_gemini(client_name: str, client_version: Optional[str]) -> HostBudget:
    wall = GEMINI_DEFAULT_WALL_SECONDS
    source = "host-default"
    evidence: List[str] = []
    name = _server_name()
    for path in _gemini_settings_paths():
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            evidence.append(f"{path}: unreadable; using the Gemini CLI default")
            continue
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        entry = servers.get(name) if isinstance(servers, dict) else None
        if not isinstance(entry, dict):
            evidence.append(
                f"{path}: no mcpServers.{name} entry; using the Gemini CLI default"
            )
            continue
        raw = entry.get("timeout")
        configured = _positive_int(raw)
        if configured is None:
            evidence.append(
                f"{path}: mcpServers.{name} sets no timeout; using the Gemini CLI default"
            )
        else:
            wall = max(1, configured // 1000)  # settings.json stores milliseconds
            source = "host-config"
            evidence.append(f"{path}: mcpServers.{name}.timeout = {raw}ms")
    if not evidence:
        evidence.append("no Gemini CLI settings.json found; using the host default")
    return HostBudget(
        host="gemini-cli",
        display="Gemini CLI",
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
            f"raise the ceiling: set `mcpServers.{name}.timeout` (milliseconds) "
            f"in ~/.gemini/settings.json, then restart Gemini CLI",
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
_HOST_MATCHERS: Tuple[Tuple[str, Any], ...] = (
    ("codex-mcp-client", _resolve_codex),
    ("claude-code", _resolve_claude_code),
    ("gemini-cli", _resolve_gemini),
    ("codex", _resolve_codex),
    ("gemini", _resolve_gemini),
)


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
    if not client_name:
        return _resolve_unknown("", None)
    client_version = os.environ.get(CLIENT_VERSION_ENV, "").strip() or None
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


def check_wait_budget(
    role: str, role_timeout_seconds: int
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
        options = "\n".join(f"  - {item}" for item in budget.remediation)
        return (
            False,
            budget,
            (
                f"host wait budget is unknown, so a blocking wait of "
                f"{format_duration(role_timeout_seconds)} for role {role!r} cannot "
                f"be guaranteed to survive. {detail}.\nResolve by one of:\n{options}"
            ),
        )

    if effective is None or role_timeout_seconds <= effective:
        return True, budget, None

    ceiling = budget.limiting_ceiling()
    detail = "; ".join(budget.evidence)
    options = "\n".join(f"  - {item}" for item in budget.remediation)
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
