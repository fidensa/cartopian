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

Runtime evidence is derived from real installed/process state, never asserted:

- activation comes from the canonical resolved configuration (an ungated
  project renders advisory on every
  host — nothing is refused anywhere, whatever is installed);
- Claude interception evidence requires a readable installed refusal hook, a
  runnable settings helper that emits the process-scoped ``PreToolUse`` entry,
  and a complete platform wrapper chain that passes that entry through
  ``--settings`` at the dispatch role boundary. A project settings
  registration is not required. Older registrations are reported only as
  compatibility state; an incompatible one invalidates the launch chain.

The adapter covers Claude's structured read and mutation tools, not ``Bash``.
Healthy evidence therefore renders ``contained-partial`` rather than claiming
an absolute boundary. Governed writes routed around PreToolUse can be detected
after the fact by ``plan-audit`` provenance. Unauthorized shell reads have no
equivalent reliable detection signal. Completion Stop-hook enforcement is a
separate lifecycle concern and contributes no containment evidence.
"""
import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cli.config_schema import MACHINE_RECORD_SCHEMA_VERSION

from cli.claude_hook import FILE_MUTATION_TOOLS, READ_TOOLS

from cli.commands.resolve_config import (
    _CliError,
    resolve_project_configuration,
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
    # opencode has no filesystem sandbox and no PreToolUse-hook equivalent;
    # an `edit` deny is bypassable in one step via a shell write, so the
    # advisory ceiling is the honest entry. Windows behavior is unverified.
    "opencode": ("opencode (CLI / TUI)", TIER_ADVISORY),
}

_WRITE_RESIDUAL = (
    "Bash/shell is not intercepted; bypassed governed writes may be detected "
    "after the fact by plan-audit provenance"
)

_READ_RESIDUAL = (
    "Bash/shell is not intercepted; unauthorized shell reads are not reliably "
    "detectable"
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


def _install_root() -> Path:
    """Root of the currently executing Cartopian install/layout."""
    return Path(__file__).resolve().parents[2]


def _running_on_windows() -> bool:
    """Patchable platform seam for installed wrapper-chain evidence."""
    return os.name == "nt"


def _claude_hook_matchers(project_path: Path) -> List[str]:
    """Matcher strings from older project-level compatibility entries.

    Current containment does not require this file. Anything unreadable or
    malformed yields no matchers. An entry with no matcher matches every tool
    under Claude semantics and is recorded as ``""``.
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


def _legacy_registration_state(project_path: Path) -> str:
    """Compatibility-only state for an older project registration."""
    settings_path = project_path / ".claude" / "settings.json"
    if not settings_path.exists():
        return "absent"
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "invalid-settings"
    if not isinstance(settings, dict):
        return "invalid-settings"
    return "present" if _claude_hook_matchers(project_path) else "absent"


def _wrapper_chain_valid(root: Path) -> bool:
    """Verify the installed platform wrapper actually wires process settings."""
    if _running_on_windows():
        cmd = root / "wrappers" / "ps1" / "cartopian-claude.cmd"
        wrapper = root / "wrappers" / "ps1" / "cartopian-claude.ps1"
        try:
            cmd_text = cmd.read_text(encoding="utf-8")
            text = wrapper.read_text(encoding="utf-8")
        except OSError:
            return False
        return all(
            marker in text
            for marker in (
                "claude_launch_settings.py",
                "CARTOPIAN_ROLE",
                "--capability",
                "--settings",
                "CARTOPIAN_CLAUDE_BARE",
            )
        ) and "cartopian-claude.ps1" in cmd_text
    wrapper = root / "wrappers" / "bin" / "cartopian-claude"
    try:
        text = wrapper.read_text(encoding="utf-8")
    except OSError:
        return False
    return os.access(wrapper, os.X_OK) and all(
        marker in text
        for marker in (
            "claude_launch_settings.py",
            "CARTOPIAN_ROLE",
            "--capability",
            "--settings",
            "CARTOPIAN_CLAUDE_BARE",
        )
    )


def _claude_process_evidence(project_path: Path) -> Dict[str, Any]:
    """Probe the installed helper and wrapper chain used by a real dispatch."""
    root = _install_root()
    hook = root / "cli" / "claude_hook.py"
    helper = root / "cli" / "claude_launch_settings.py"
    hook_present = hook.is_file() and os.access(hook, os.R_OK)
    hook_valid = False
    if hook_present:
        try:
            hook_source = hook.read_text(encoding="utf-8")
            compile(hook_source, str(hook), "exec")
            hook_valid = all(
                marker in hook_source
                for marker in (
                    "def evaluate(",
                    "def main(",
                    "FILE_MUTATION_TOOLS",
                    "READ_TOOLS",
                    "PreToolUse",
                )
            )
        except (OSError, SyntaxError, UnicodeError):
            hook_valid = False
    evidence: Dict[str, Any] = {
        "hook_present": hook_present,
        "hook_valid": hook_valid,
        "settings_helper_present": helper.is_file() and os.access(helper, os.R_OK),
        "wrapper_chain_valid": _wrapper_chain_valid(root),
        "process_scoped": False,
        "legacy_project_registration": _legacy_registration_state(project_path),
        "detail": None,
        "matchers": [],
    }
    if not all(
        evidence[key]
        for key in (
            "hook_present",
            "hook_valid",
            "settings_helper_present",
            "wrapper_chain_valid",
        )
    ):
        evidence["detail"] = "installed Claude hook/helper/wrapper chain is incomplete"
        return evidence
    try:
        spec = importlib.util.spec_from_file_location(
            "_cartopian_installed_claude_launch_settings", helper
        )
        if spec is None or spec.loader is None:
            raise ImportError("could not load settings helper")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        settings = module.build_settings(
            root,
            windows=_running_on_windows(),
            project_dir=project_path,
            include_capability=True,
        )
    except Exception as exc:
        evidence["detail"] = f"settings helper probe failed: {exc}"
        if evidence["legacy_project_registration"] == "present":
            evidence["legacy_project_registration"] = "incompatible"
        return evidence
    try:
        entries = settings.get("hooks", {}).get("PreToolUse", [])
    except AttributeError:
        evidence["detail"] = "settings helper returned an invalid settings object"
        return evidence
    matchers = [
        entry.get("matcher", "")
        for entry in entries
        if isinstance(entry, dict)
        and any(
            isinstance(handler, dict)
            and str(hook) in str(handler.get("command", ""))
            and str(Path(sys.executable)) in str(handler.get("command", ""))
            for handler in entry.get("hooks", []) or []
        )
    ]
    evidence["matchers"] = [m for m in matchers if isinstance(m, str)]
    evidence["process_scoped"] = bool(evidence["matchers"])
    if evidence["legacy_project_registration"] == "present":
        evidence["legacy_project_registration"] = "compatible"
    if not evidence["process_scoped"]:
        evidence["detail"] = "settings helper did not emit the installed capability hook"
    return evidence


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


def _interception_evidence(
    host: str, project_path: Path
) -> Tuple[bool, bool, bool, Optional[Dict[str, Any]]]:
    """(present, write_active, read_active, detail) for one host.

    Only Claude Code has an implemented refusal adapter; every other host has
    no interception point this tool can verify, so its evidence is negative
    by construction — for both boundaries.
    """
    if host == "claude-code":
        detail = _claude_process_evidence(project_path)
        present = bool(detail["hook_present"] and detail["hook_valid"])
        matchers = detail["matchers"] if detail["process_scoped"] else []
        return (
            present,
            _boundary_registered(matchers, FILE_MUTATION_TOOLS),
            _boundary_registered(matchers, READ_TOOLS),
            detail,
        )
    return False, False, False, None


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
        # The native adapter does not intercept Bash/shell. Point-of-use
        # coverage is real but partial on both axes.
        evidence = TIER_PARTIAL
    else:
        evidence = TIER_ADVISORY
    return ceiling if _TIER_RANK[ceiling] <= _TIER_RANK[evidence] else evidence


def _disclosure(tier: str, *, activated: bool, boundary: str = "write") -> Optional[str]:
    """Plain-language residual disclosure for any non-absolute boundary."""
    if tier == TIER_CONTAINED:
        return None
    residual = _BOUNDARY_RESIDUALS[boundary]
    if not activated:
        return (
            "the project config is ungated (no capability grants), so no host refuses this "
            f"project; {residual}."
        )
    if tier == TIER_PARTIAL:
        return residual + "."
    if boundary == "write":
        return (
            "no cleared native write interception is active; out-of-band writes "
            "may be detected after the fact by plan-audit provenance."
        )
    return "no cleared native read interception is active; unauthorized reads are not reliably detectable."


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

    project_path = Path(raw_path).resolve()
    if not project_path.is_dir():
        _stderr("error", f"project path not found: {raw_path}")
        return EXIT_FAIL

    try:
        resolved = resolve_project_configuration(project_path)
    except _CliError as err:
        _stderr(err.prefix, err.message)
        return err.exit_code
    activated = resolved["capabilities"]["activated"]

    hosts = []
    for host, (label, ceiling) in HOST_CEILINGS.items():
        present, write_reg, read_reg, evidence_detail = _interception_evidence(
            host, project_path
        )
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
        write_boundary = {
            "tier": write_tier,
            "interception_registered": write_reg,
            "disclosure": _disclosure(
                write_tier, activated=activated, boundary="write"
            ),
        }
        read_boundary = {
            "tier": read_tier,
            "interception_registered": read_reg,
            "disclosure": _disclosure(
                read_tier, activated=activated, boundary="read"
            ),
        }
        row = {
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
            "boundaries": {"write": write_boundary, "read": read_boundary},
        }
        if host == "claude-code":
            write_boundary["shell_interception"] = False
            read_boundary["shell_interception"] = False
            read_boundary["unauthorized_read_detection"] = False
            row["interception_scope"] = "structured-tools; Bash/shell excluded"
            public_evidence = dict(evidence_detail or {})
            public_evidence.pop("matchers", None)
            if public_evidence.get("detail") is None:
                public_evidence.pop("detail", None)
            row["process_scoped_evidence"] = public_evidence
        hosts.append(row)

    record: Dict[str, Any] = {
        "record_schema_version": MACHINE_RECORD_SCHEMA_VERSION,
        "schema_identity": resolved["schema_identity"],
        "project_schema_version": resolved["project_schema_version"],
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
