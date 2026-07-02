"""`cartopian containment-matrix <project-path>` — honest per-host containment matrix.

For each supported host application, render the containment tier the host
currently provides for the target project — separately for the **write
boundary** and the **read boundary**, plus their floor as the overall tier.
Each boundary's tier is the *floor* (more conservative) of the host's static
tier ceiling and the project's runtime evidence tier for that boundary.

The ceiling table (:data:`HOST_CEILINGS`) is the authoritative
operator-acceptance clearance source, encoded in code — never a config field,
never parsed from documents. A host whose clearance has not been earned renders
at most ``advisory+detection`` regardless of any runtime signal (fail closed).

Runtime evidence is derived from real state, never asserted:

- activation comes from :func:`cli.capabilities.resolve_grants` over the
  resolved ``[roles]`` config (an ungated project renders advisory on every
  host — nothing is refused anywhere, whatever is installed);
- interception evidence for a host means that host's native refusal adapter is
  actually present and registered for *this* project, per boundary: the
  registered PreToolUse matcher must cover the mutation tools for the write
  boundary and the read tools for the read boundary. A write-only matcher
  never claims read enforcement. The one implemented adapter is the Claude
  Code PreToolUse hook (``cli/claude_hook.py``), registered in the project's
  ``.claude/settings.json``. Hosts with no implemented adapter can never show
  interception evidence for either boundary.

Every ``advisory+detection`` boundary carries a plain-language disclosure
naming the residual: out-of-band writes are detected after the fact by the
raw-edit detection floor (``plan-audit`` provenance), not prevented at the
point of write; ungranted reads are not prevented at the point of read, with
the detection floor as the fail-safe. When the cause is an ungated config,
the disclosure names that too.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cli.claude_hook import FILE_MUTATION_TOOLS, READ_TOOLS

from cli.capabilities import resolve_grants
from cli.commands.resolve_config import (
    _CliError,
    _load_project_config,
    _load_toml,
    _require_project_keys,
    _resolve_roles,
)
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE

TIER_CONTAINED = "contained"
TIER_PARTIAL = "contained-partial"
TIER_ADVISORY = "advisory+detection"

# Lower rank = more conservative. floor() renders the lower-ranked tier.
_TIER_RANK: Dict[str, int] = {
    TIER_ADVISORY: 0,
    TIER_PARTIAL: 1,
    TIER_CONTAINED: 2,
}

# Authoritative per-host tier ceilings: the maximum tier each supported host
# may attain, reflecting which operator-executed acceptance clearances have
# been earned. This table in code is the source of truth for clearance — a
# host absent from it is unsupported, and a ceiling below `contained` can
# never be out-rendered by runtime evidence.
HOST_CEILINGS: Dict[str, Tuple[str, str]] = {
    "claude-code": ("Claude Code (CLI)", TIER_CONTAINED),
    "codex-cli": ("Codex CLI", TIER_PARTIAL),
    "antigravity-tui": ("Antigravity standalone TUI", TIER_ADVISORY),
    "claude-desktop": ("Claude Desktop", TIER_ADVISORY),
    "chatgpt-app": ("ChatGPT app", TIER_ADVISORY),
    "antigravity-ide": ("Antigravity graphical IDE", TIER_ADVISORY),
    "devin": ("Devin", TIER_ADVISORY),
}

_WRITE_RESIDUAL = (
    "out-of-band writes are detected after the fact by the raw-edit "
    "detection floor (plan-audit provenance), not prevented at the point "
    "of write"
)

_READ_RESIDUAL = (
    "ungranted reads are not prevented at the point of read; the detection "
    "floor remains the fail-safe"
)

_BOUNDARY_RESIDUALS: Dict[str, str] = {
    "write": _WRITE_RESIDUAL,
    "read": _READ_RESIDUAL,
}


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "project_path",
        help="Absolute path to the project root",
    )


def _stderr(prefix: str, msg: str) -> None:
    sys.stderr.write(f"[{prefix}] {msg}\n")


def _claude_hook_file() -> Path:
    """The refusal-adapter hook shipped with this install (cli/claude_hook.py)."""
    return Path(__file__).resolve().parent.parent / "claude_hook.py"


def _claude_hook_matchers(project_path: Path) -> List[str]:
    """PreToolUse matcher strings of registered claude_hook.py entries.

    Registration lives in the project's ``.claude/settings.json`` (the form
    ``scripts/install.py --claude-hook`` writes). Anything unreadable or
    malformed yields no matchers — evidence must be positive. An entry with
    no ``matcher`` key matches every tool (Claude Code semantics), recorded
    here as ``""``.
    """
    settings_path = project_path / ".claude" / "settings.json"
    if not settings_path.is_file():
        return []
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(settings, dict):
        return []
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return []
    pre = hooks.get("PreToolUse")
    if not isinstance(pre, list):
        return []
    matchers: List[str] = []
    for item in pre:
        if not isinstance(item, dict):
            continue
        for hook in item.get("hooks", []) or []:
            if isinstance(hook, dict) and "claude_hook.py" in str(
                hook.get("command", "")
            ):
                matcher = item.get("matcher", "")
                matchers.append(matcher if isinstance(matcher, str) else "")
                break
    return matchers


def _matcher_covers(matcher: str, tool: str) -> bool:
    """Whether a PreToolUse matcher intercepts `tool` (Claude Code semantics:
    the matcher is a regex over the tool name; empty or ``"*"`` matches all;
    an unparseable matcher matches nothing — evidence must be positive)."""
    if matcher in ("", "*"):
        return True
    try:
        return re.fullmatch(matcher, tool) is not None
    except re.error:
        return False


def _boundary_registered(matchers: List[str], tools) -> bool:
    """A boundary counts as registered only when every tool the hook gates on
    that boundary is covered by some registered matcher."""
    return bool(matchers) and all(
        any(_matcher_covers(m, tool) for m in matchers) for tool in sorted(tools)
    )


def _interception_evidence(host: str, project_path: Path) -> Tuple[bool, bool, bool]:
    """(present, write_registered, read_registered) for one host.

    Only Claude Code has an implemented refusal adapter; every other host has
    no interception point this tool can verify, so its evidence is negative
    by construction — for both boundaries.
    """
    if host == "claude-code":
        present = _claude_hook_file().is_file()
        matchers = _claude_hook_matchers(project_path) if present else []
        return (
            present,
            _boundary_registered(matchers, FILE_MUTATION_TOOLS),
            _boundary_registered(matchers, READ_TOOLS),
        )
    return False, False, False


def render_tier(
    ceiling: str,
    *,
    activated: bool,
    interception_present: bool,
    interception_registered: bool,
) -> str:
    """floor(host ceiling, runtime evidence tier) — never above the ceiling.

    Runtime evidence reaches `contained` only when the project is activated
    AND the host's interception is present and registered; otherwise the
    detection floor is the only protection and evidence is advisory. The
    static ceiling then caps the render, so a gated (below-`contained`)
    ceiling never renders `contained` even with full runtime evidence.
    """
    if activated and interception_present and interception_registered:
        evidence = TIER_CONTAINED
    else:
        evidence = TIER_ADVISORY
    return ceiling if _TIER_RANK[ceiling] <= _TIER_RANK[evidence] else evidence


def _disclosure(tier: str, *, activated: bool, boundary: str = "write") -> Optional[str]:
    """Plain-language residual disclosure for an advisory boundary; None otherwise."""
    if tier != TIER_ADVISORY:
        return None
    residual = _BOUNDARY_RESIDUALS[boundary]
    if not activated:
        return (
            "the project config is ungated (no capability grants are "
            "declared for any role), so no host refuses anything for this "
            f"project; {residual}."
        )
    return (
        f"no cleared native {boundary} interception is active on this host "
        f"for this project; {residual}."
    )


def _overall_disclosure(
    write_tier: str, read_tier: str, *, activated: bool
) -> Optional[str]:
    """The row-level disclosure: the write residual when the write boundary
    (or the whole config) is advisory, else the read residual when only the
    read boundary degrades the row."""
    if not activated or write_tier == TIER_ADVISORY:
        return _disclosure(write_tier, activated=activated, boundary="write")
    return _disclosure(read_tier, activated=activated, boundary="read")


def handler(args: argparse.Namespace) -> int:
    raw_path = args.project_path
    if not Path(raw_path).is_absolute():
        _stderr("usage", f"project_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE

    project_path = Path(raw_path)
    if not project_path.is_dir():
        _stderr("error", f"project path not found: {raw_path}")
        return EXIT_FAIL

    try:
        project_cfg = _load_project_config(project_path)
        _require_project_keys(project_cfg, project_path / "cartopian.toml")
        global_toml = Path.home() / ".cartopian" / "cartopian.toml"
        global_cfg = _load_toml(global_toml, "global config") or {}
    except _CliError as err:
        _stderr(err.prefix, err.message)
        return err.exit_code

    roles_raw = _resolve_roles(global_cfg, project_cfg)
    activated = resolve_grants(roles_raw).activated

    hosts = []
    for host, (label, ceiling) in HOST_CEILINGS.items():
        present, write_reg, read_reg = _interception_evidence(host, project_path)
        write_tier = render_tier(
            ceiling,
            activated=activated,
            interception_present=present,
            interception_registered=write_reg,
        )
        read_tier = render_tier(
            ceiling,
            activated=activated,
            interception_present=present,
            interception_registered=read_reg,
        )
        # The row tier is the floor of the two boundaries: a host is only as
        # contained as its weakest enforced boundary.
        tier = min((write_tier, read_tier), key=_TIER_RANK.get)
        hosts.append(
            {
                "host": host,
                "label": label,
                "tier": tier,
                "ceiling": ceiling,
                "interception_present": present,
                "interception_registered": write_reg,
                "activated": activated,
                "disclosure": _overall_disclosure(
                    write_tier, read_tier, activated=activated
                ),
                "boundaries": {
                    "write": {
                        "tier": write_tier,
                        "interception_registered": write_reg,
                        "disclosure": _disclosure(
                            write_tier, activated=activated, boundary="write"
                        ),
                    },
                    "read": {
                        "tier": read_tier,
                        "interception_registered": read_reg,
                        "disclosure": _disclosure(
                            read_tier, activated=activated, boundary="read"
                        ),
                    },
                },
            }
        )

    record: Dict[str, Any] = {
        "action": "containment-matrix",
        "project_path": str(project_path),
        "activated": activated,
        "hosts": hosts,
    }
    emit_record(record)

    for row in hosts:
        if row["disclosure"]:
            _stderr("advisory", f"{row['label']}: {row['disclosure']}")
    return EXIT_OK
