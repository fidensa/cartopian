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
  registration is not required. Older persistent registrations in a normal
  settings scope are reported as excluded compatibility state for activated
  launches because their settings-source list is empty;
- on POSIX, shell-write evidence additionally requires the helper's strict
  process-scoped Claude sandbox: filesystem isolation enabled, unavailable
  enforcement fatal, unsandboxed retry disabled, and the Cartopian project
  root present in ``denyWrite``.

The adapter covers Claude's structured read and mutation tools. On native
macOS/Linux hosts the OS sandbox also contains ``Bash``/shell writes and child
processes. The matrix reports that launch policy as configured, including the
absence of process-scoped ``excludedCommands``, but keeps the write boundary at
``contained-partial`` until operator acceptance behaviorally attests the host
sandbox; administrator-managed exceptions are outside this static probe. Shell
reads remain outside the capability policy. Activated WSL2 launch is refused
pending interop seccomp attestation. Activated native-Windows launch is also
refused pending both shell-sandbox and exact native-executable-chain
attestation. Completion Stop-hook enforcement is a separate lifecycle concern
and contributes no containment evidence.
"""
import argparse
import ast
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
    # Hermes one-shot runs internally set HERMES_YOLO_MODE=1 and
    # HERMES_ACCEPT_HOOKS=1, so wrapper-launched Hermes has no approval layer
    # at all; write guards are documented-bypassable via the terminal tool,
    # and container backends are unattestable host config. No interception,
    # no attestable deny — advisory is the honest ceiling.
    "hermes": ("Hermes (CLI)", TIER_ADVISORY),
}

_WRITE_RESIDUAL = (
    "Bash/shell writes are not OS-contained on this host; bypassed governed "
    "writes may be detected after the fact by plan-audit provenance"
)

_WRITE_SANDBOX_RESIDUAL = (
    "a strict OS-sandbox shell-write policy is configured, but this static "
    "probe does not behaviorally attest the host sandbox or resolve explicit "
    "administrator-managed exceptions"
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
    except (OSError, UnicodeError, json.JSONDecodeError):
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
            if isinstance(hook, dict) and (
                "claude_hook.py" in str(hook.get("command", ""))
                or any(
                    "claude_hook.py" in str(argument)
                    for argument in hook.get("args", [])
                )
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
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "invalid-settings"
    if not isinstance(settings, dict):
        return "invalid-settings"
    return "present" if _claude_hook_matchers(project_path) else "absent"


def _wrapper_chain_valid(root: Path) -> bool:
    """Verify the installed platform wrapper actually wires process settings."""
    helper_source = root / "cli" / "claude_launch_settings.py"
    try:
        tree = ast.parse(helper_source.read_text(encoding="utf-8"))
        required_tools: tuple[str, ...] = ()
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name)
                and target.id == "_UNCONTAINED_SESSION_TOOLS"
                for target in node.targets
            ):
                continue
            value = ast.literal_eval(node.value)
            if isinstance(value, tuple) and all(
                isinstance(item, str) for item in value
            ):
                required_tools = value
            break
    except (OSError, SyntaxError, ValueError):
        return False
    if not required_tools:
        return False
    if _running_on_windows():
        cmd = root / "wrappers" / "ps1" / "cartopian-claude.cmd"
        wrapper = root / "wrappers" / "ps1" / "cartopian-claude.ps1"
        supervisor = root / "wrappers" / "ps1" / "CartopianStatus.ps1"
        try:
            cmd_text = cmd.read_text(encoding="utf-8")
            text = wrapper.read_text(encoding="utf-8")
            supervisor_text = supervisor.read_text(encoding="utf-8")
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
                "CARTOPIAN_CLAUDE_EXECUTABLE",
                "--preflight-only",
                "--setting-sources",
                "--claude-version",
                "--strict-mcp-config",
                "--disallowedTools",
            )
        ) and all(tool in text for tool in required_tools) and all(
            marker in supervisor_text
            for marker in (
                "Get-Command",
                "CommandType Application",
                "ConvertFrom-Json",
                "-EncodedCommand",
                "Kill($true)",
            )
        ) and all(
            marker in cmd_text
            for marker in (
                "cartopian-claude.ps1",
                "System32\\WindowsPowerShell\\v1.0\\powershell.exe",
            )
        ) and "where " not in cmd_text.lower()
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
            "CARTOPIAN_CLAUDE_EXECUTABLE",
            "--preflight-only",
            "--setting-sources",
            "--claude-version",
            "--strict-mcp-config",
            "--disallowedTools",
        )
    ) and all(tool in text for tool in required_tools)


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
        "claude_version": None,
        "claude_version_supported": False,
        "process_scoped": False,
        "shell_write_policy_configured": False,
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
        resolution, _work_roots = module._capability_context(project_path)
        normal_persistent_entries = []
        for event in ("PreToolUse", "Stop"):
            normal_persistent_entries.extend(
                (event, path)
                for path, _entry in module._project_entries(
                    project_path,
                    event,
                    os.environ,
                    windows=_running_on_windows(),
                )
            )
        if normal_persistent_entries:
            if resolution.activated:
                # Activated wrappers launch with an empty settings-source list;
                # normal persistent registrations exist on disk but are excluded
                # from the Claude process and cannot duplicate the bound hook.
                evidence["legacy_project_registration"] = "excluded"
            else:
                evidence["legacy_project_registration"] = "incompatible"
                locations = ", ".join(
                    sorted(
                        {
                            f"{event} in {path}"
                            for event, path in normal_persistent_entries
                        }
                    )
                )
                evidence["detail"] = (
                    "normal Claude settings contain a persistent Cartopian hook "
                    "that a completion-only launch cannot isolate: "
                    + locations
                )
                return evidence
        base_probe_environ = dict(os.environ)
        base_probe_environ.pop(module.CLAUDE_EXECUTABLE_ENV, None)
        if resolution.activated and not _running_on_windows():
            # Model the private host-temp binding dispatch will create without
            # making a diagnostic matrix command write operator state.
            for temp_key in (
                module.CLAUDE_HOST_TMPDIR_ENV,
                "TMPDIR",
                "TMP",
                "TEMP",
            ):
                base_probe_environ.pop(temp_key, None)
            host_tmp = str(
                module.claude_host_tmpdir_path(
                    base_probe_environ,
                    windows=False,
                )
            )
            base_probe_environ[module.CLAUDE_HOST_TMPDIR_ENV] = host_tmp
            base_probe_environ["TMPDIR"] = host_tmp
            module.prepare_claude_host_tmpdir(
                base_probe_environ,
                windows=False,
                allow_missing=True,
                require_binding=True,
            )
        unsafe_path = module.unsafe_precontainment_path_components(
            base_probe_environ,
            project_path,
            list(_work_roots.values()),
        )
        if unsafe_path:
            raise module.SettingsError(
                "pre-containment PATH contains an empty/relative or governed "
                "writable entry: " + ", ".join(unsafe_path)
            )
        claude_roots = module.claude_executable_protected_roots(
            project_path,
            _work_roots,
            base_probe_environ,
            windows=_running_on_windows(),
        )
        base_probe_environ[module.CLAUDE_EXECUTABLE_ENV] = claude_roots[0]
        # A real dispatch binds exactly one configured role. Probe each role
        # separately: a persisted entry that happens to match one role is not
        # project-wide compatible when every other role would refuse launch.
        role_names = sorted(resolution.role_grants) or ["pm"]
        probes = []
        for role_name in role_names:
            probe_environ = dict(base_probe_environ)
            probe_environ["CARTOPIAN_ROLE"] = role_name
            settings = module.build_settings(
                root,
                windows=_running_on_windows(),
                project_dir=project_path,
                include_capability=True,
                environ=probe_environ,
            )
            probes.append((role_name, probe_environ, settings))
        version, raw_version = module.probe_claude_version(
            executable=claude_roots[0],
            windows=_running_on_windows(),
            environ=base_probe_environ,
        )
        if version is None:
            raise module.SettingsError(
                "cannot determine Claude Code version"
                + (f" ({raw_version})" if raw_version else "")
            )
        module.require_claude_version(
            ".".join(map(str, version)),
            activated=any(
                "PreToolUse" in settings.get("hooks", {})
                for _role, _env, settings in probes
            ),
        )
        evidence["claude_version"] = ".".join(map(str, version))
        evidence["claude_version_supported"] = True
    except Exception as exc:
        evidence["detail"] = f"settings helper probe failed: {exc}"
        legacy_error = getattr(locals().get("module"), "LegacyHookError", ())
        if (
            evidence["legacy_project_registration"] == "present"
            and isinstance(exc, legacy_error)
        ):
            evidence["legacy_project_registration"] = "incompatible"
        return evidence
    try:
        probe_entries = [
            settings.get("hooks", {}).get("PreToolUse", [])
            for _role, _env, settings in probes
        ]
    except AttributeError:
        evidence["detail"] = "settings helper returned an invalid settings object"
        return evidence
    matcher_sets = []
    for entries in probe_entries:
        matchers = [
            entry.get("matcher", "")
            for entry in entries
            if isinstance(entry, dict)
            and any(
                isinstance(handler, dict)
                and str(Path(sys.executable)) in str(handler.get("command", ""))
                and (
                    str(hook) in str(handler.get("command", ""))
                    or any(
                        str(hook) in str(argument)
                        for argument in handler.get("args", [])
                    )
                )
                for handler in entry.get("hooks", []) or []
            )
        ]
        matcher_sets.append([m for m in matchers if isinstance(m, str)])
    evidence["matchers"] = sorted(
        {matcher for matchers in matcher_sets for matcher in matchers}
    )
    session_tool_policies = [
        set(module._UNCONTAINED_SESSION_TOOLS).issubset(
            set(settings.get("permissions", {}).get("deny", ()))
        )
        for _role, _env, settings in probes
    ]
    evidence["process_scoped"] = (
        bool(matcher_sets)
        and all(matcher_sets)
        and all(session_tool_policies)
    )

    shell_policies = []
    for _role, probe_environ, settings in probes:
        sandbox = settings.get("sandbox") if isinstance(settings, dict) else None
        filesystem = (
            sandbox.get("filesystem") if isinstance(sandbox, dict) else None
        )
        deny_write = (
            filesystem.get("denyWrite") if isinstance(filesystem, dict) else None
        )
        configured_denies = {
            os.path.abspath(path)
            for path in deny_write or []
            if isinstance(path, str)
        }
        host_tool_environ, _host_tool_sources = (
            module._effective_host_tool_environment(
                project_path, probe_environ
            )
        )
        required_denies = {
            os.path.abspath(project_path),
            *module.enforcement_protected_roots(
                root,
                probe_environ,
                windows=_running_on_windows(),
            ),
            *module.sandbox_host_executable_roots(host_tool_environ),
            *module.claude_executable_protected_roots(
                project_path,
                _work_roots,
                probe_environ,
                windows=_running_on_windows(),
            ),
            *module.project_git_protected_roots(project_path, probe_environ),
            *(
                os.path.abspath(path)
                for path in module._hook_startup_paths(
                    project_path,
                    probe_environ,
                    windows=False,
                    isolated_sources=resolution.activated,
                )
            ),
        }
        network = sandbox.get("network") if isinstance(sandbox, dict) else None
        shell_policies.append(
            bool(
                not _running_on_windows()
                and isinstance(sandbox, dict)
                and sandbox.get("enabled") is True
                and sandbox.get("failIfUnavailable") is True
                and sandbox.get("allowUnsandboxedCommands") is False
                and sandbox.get("allowAppleEvents") is False
                and sandbox.get("enableWeakerNestedSandbox") is False
                and sandbox.get("excludedCommands") == []
                and sandbox.get("ignoreViolations") == {}
                and isinstance(network, dict)
                and network.get("allowUnixSockets") == []
                and network.get("allowAllUnixSockets") is False
                and network.get("allowMachLookup") == []
                and isinstance(filesystem, dict)
                and filesystem.get("disabled") is False
                and required_denies.issubset(configured_denies)
                and evidence["claude_version_supported"]
            )
        )
    evidence["shell_write_policy_configured"] = bool(shell_policies) and all(
        shell_policies
    )
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
        # The installed/process chain is real point-of-use enforcement, but
        # this static command does not execute an OS-sandbox behavior probe.
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
        shell_write = bool(
            host == "claude-code"
            and evidence_detail
            and evidence_detail.get("shell_write_policy_configured")
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
            "disclosure": (
                _WRITE_SANDBOX_RESIDUAL + "."
                if activated and shell_write and write_tier == TIER_PARTIAL
                else _disclosure(
                    write_tier, activated=activated, boundary="write"
                )
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
            # Static construction proves the policy is configured, not that a
            # particular host enforced it behaviorally with no merged operator
            # exception. Keep the historical interception boolean conservative
            # and expose the precise evidence separately.
            write_boundary["shell_interception"] = False
            write_boundary["shell_write_policy_configured"] = shell_write
            read_boundary["shell_interception"] = False
            read_boundary["unauthorized_read_detection"] = False
            row["interception_scope"] = (
                "structured tools; shell-write policy configured, not attested; "
                "shell reads excluded"
                if shell_write
                else "structured-tools; Bash/shell writes and reads excluded"
            )
            public_evidence = dict(evidence_detail or {})
            for internal_key in (
                "matchers",
                "claude_version_supported",
                "shell_write_policy_configured",
            ):
                public_evidence.pop(internal_key, None)
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
