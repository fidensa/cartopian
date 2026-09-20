"""The launch prerequisites ``cartopian dispatch`` enforces, as one shared check.

``dispatch`` refuses a launch when the role has no agent, a set-but-empty
model or effort, an invalid output contract, a host that cannot wait out the
role timeout, a work-root mapping that does not exist on this machine, or an
agent that does not resolve on PATH. The dispatch rehearsal must apply the
same checks before it calls a task dispatchable — a rehearsal that passes
while the real launch would refuse is a false readiness claim. Both consumers
therefore call this module; the wording is the wording dispatch prints.

Each finding is ``{code, prefix, message}``: ``prefix`` is the stderr channel
dispatch uses (``guard`` or ``error``), so dispatch's reporting is unchanged.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from cli import host_capability, output_safety

DEFAULT_TIMEOUT = "60m"
DEFAULT_TIMEOUT_SECONDS = 60 * 60


def _running_on_windows() -> bool:
    return os.name == "nt"


def _is_cartopian_claude_agent(
    agent: object, resolved_agent: Optional[str] = None
) -> bool:
    """Recognize the shipped Claude wrapper across POSIX/Windows shims.

    Configuration may name the command, an absolute shim path, or a symlink to
    the installed wrapper.  Preflight must recognize the same launch that
    dispatch will execute or it can falsely pass work that the wrapper rejects.
    """
    candidates = [str(agent)] if agent else []
    if resolved_agent:
        candidates.extend((resolved_agent, os.path.realpath(resolved_agent)))
    return any(
        Path(candidate).name.lower()
        in {
            "cartopian-claude",
            "cartopian-claude.cmd",
            "cartopian-claude.bat",
            "cartopian-claude.ps1",
        }
        for candidate in candidates
    )


def _cartopian_claude_install_root(
    resolved_agent: Optional[str], *, windows: Optional[bool] = None
) -> Optional[Path]:
    """Return the shipped wrapper's install root, never a basename lookalike."""
    if not resolved_agent:
        return None
    if windows is None:
        windows = _running_on_windows()
    wrapper = Path(os.path.realpath(resolved_agent))
    expected_parent = "ps1" if windows else "bin"
    expected_names = (
        {"cartopian-claude.cmd"}
        if windows
        else {"cartopian-claude"}
    )
    if (
        wrapper.name.lower() not in expected_names
        or wrapper.parent.name.lower() != expected_parent
        or wrapper.parent.parent.name.lower() != "wrappers"
    ):
        return None
    root = wrapper.parents[2]
    expected_root = Path(__file__).resolve().parents[1]
    try:
        same_install = os.path.samefile(root, expected_root)
    except OSError:
        same_install = os.path.realpath(root) == os.path.realpath(expected_root)
    if not same_install:
        return None
    required = (
        root / "cli" / "claude_launch_settings.py",
        root / "cli" / "claude_hook.py",
        root / "cli" / "claude_stop_hook.py",
        *(
            (
                root / "wrappers" / "ps1" / "cartopian-claude.cmd",
                root / "wrappers" / "ps1" / "cartopian-claude.ps1",
                root / "wrappers" / "ps1" / "CartopianStatus.ps1",
            )
            if windows
            else (
                root / "wrappers" / "bin" / "cartopian-claude",
                root / "wrappers" / "bin" / "_cartopian-status.sh",
            )
        ),
    )
    return root if all(path.is_file() for path in required) else None


def _finding(code: str, prefix: str, message: str) -> Dict[str, str]:
    return {"code": code, "prefix": prefix, "message": message}


def _contains_transport_control(value: str) -> bool:
    """Whether a path cannot survive the wrappers' line-oriented transport."""
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def role_checks(
    role: str,
    role_record: Mapping[str, Any],
    *,
    environ: Optional[Mapping[str, str]] = None,
    context: str = "dispatch",
) -> List[Dict[str, str]]:
    """Role-record prerequisites, in dispatch's order; empty when all hold."""
    launch = role_record.get("launch") or {}
    agent = launch.get("agent")
    if not agent:
        return [
            _finding(
                "agent-not-configured",
                "guard",
                f"roles.{role}.agent is not configured — dispatch this role manually",
            )
        ]
    model = launch.get("model")
    if model is not None and not model:
        return [
            _finding(
                "model-empty",
                "guard",
                f"roles.{role}.model is set but empty — set a model identifier or "
                "remove the key",
            )
        ]
    effort = launch.get("effort")
    if effort is not None and not effort:
        return [
            _finding(
                "effort-empty",
                "guard",
                f"roles.{role}.effort is set but empty — set an effort level or "
                "remove the key",
            )
        ]
    try:
        output_safety.limits_from_environment(
            dict(os.environ if environ is None else environ)
        )
    except output_safety.OutputSafetyError as exc:
        return [
            _finding(
                "output-contract-invalid",
                "guard",
                f"invalid automated-handoff output contract: {exc}",
            )
        ]
    timeout = launch.get("timeout") or DEFAULT_TIMEOUT
    host_ok, _budget, host_refusal = host_capability.check_wait_budget(
        role,
        host_capability.parse_duration(str(timeout)) or DEFAULT_TIMEOUT_SECONDS,
        context=context,
    )
    if not host_ok:
        return [_finding("host-wait-budget", "guard", str(host_refusal))]
    return []


def environment_checks(
    role: str,
    role_record: Mapping[str, Any],
    resolved_work_roots: Mapping[str, str],
    *,
    project_root: Optional[Path] = None,
    capabilities_activated: bool = False,
    environ: Optional[Mapping[str, str]] = None,
    launch_bindings: Optional[Dict[str, str]] = None,
) -> List[Dict[str, str]]:
    """Machine prerequisites: mapped work roots exist and the agent resolves."""
    findings: List[Dict[str, str]] = []
    transport_separator = ";" if _running_on_windows() else ":"
    unsafe_transport_roots = [
        path
        for path in resolved_work_roots.values()
        if transport_separator in path or _contains_transport_control(path)
    ]
    if unsafe_transport_roots:
        findings.append(
            _finding(
                "work-root-path-list-separator",
                "guard",
                "declared work-root path contains a control character or the "
                f"platform path-list separator {transport_separator!r} and cannot "
                "be transported losslessly "
                "to agent wrappers: "
                + ", ".join(repr(path) for path in unsafe_transport_roots)
                + " — relocate the work root to a path without that character",
            )
        )
    missing_roots = [
        path for path in resolved_work_roots.values() if not Path(path).is_dir()
    ]
    if missing_roots:
        findings.append(
            _finding(
                "work-root-missing",
                "guard",
                "work root path(s) do not exist on this machine: "
                + ", ".join(missing_roots)
                + " — fix the [work_roots] mapping in cartopian.local.toml",
            )
        )
    agent = (role_record.get("launch") or {}).get("agent")
    resolved_agent = shutil.which(str(agent)) if agent else None
    if launch_bindings is not None:
        launch_bindings.clear()
        if resolved_agent is not None:
            launch_bindings["agent"] = resolved_agent
    cartopian_claude = _is_cartopian_claude_agent(agent, resolved_agent)
    wrapper_install_root = (
        _cartopian_claude_install_root(resolved_agent)
        if cartopian_claude
        else None
    )
    trusted_cartopian_claude = cartopian_claude and wrapper_install_root is not None
    if trusted_cartopian_claude and launch_bindings is not None:
        # The wrapper discovers its sibling helper chain from its invoked path.
        # Launch the certified real layout, not an individual PATH symlink whose
        # parent would make the wrapper derive the wrong installation root.
        launch_bindings["agent"] = os.path.realpath(str(resolved_agent))
    activated_claude = (
        capabilities_activated
        and project_root is not None
        and trusted_cartopian_claude
    )
    capability_claude = (
        not _running_on_windows()
        and activated_claude
    )
    git_protected_roots: tuple[str, ...] = ()
    enforcement_roots: tuple[str, ...] = ()
    host_executable_roots: tuple[str, ...] = ()
    claude_executable_roots: tuple[str, ...] = ()
    registry_project_roots: tuple[str, ...] = ()
    foreign_work_roots: tuple[str, ...] = ()
    active_environ = os.environ if environ is None else environ
    if capability_claude:
        # A real dispatch replaces inherited temp controls with a protected
        # host-only directory before the wrapper or Claude starts. Model that
        # same effective environment here without creating the directory, so
        # rehearsals remain read-only while checking the launch Cartopian will
        # actually perform.
        from cli.claude_launch_settings import (
            CLAUDE_HOST_TMPDIR_ENV,
            SettingsError,
            claude_host_tmpdir_path,
            prepare_claude_host_tmpdir,
        )

        modeled_environ = dict(active_environ)
        for temp_key in (CLAUDE_HOST_TMPDIR_ENV, "TMPDIR", "TMP", "TEMP"):
            modeled_environ.pop(temp_key, None)
        host_tmp = str(
            claude_host_tmpdir_path(modeled_environ, windows=False)
        )
        modeled_environ[CLAUDE_HOST_TMPDIR_ENV] = host_tmp
        modeled_environ["TMPDIR"] = host_tmp
        try:
            prepare_claude_host_tmpdir(
                modeled_environ,
                windows=False,
                allow_missing=True,
                require_binding=True,
            )
        except SettingsError as exc:
            findings.append(
                _finding("claude-host-temp", "guard", str(exc))
            )
            return findings
        active_environ = modeled_environ
    claude_probe_environ = dict(active_environ)
    claude_probe_environ.pop("CARTOPIAN_CLAUDE_EXECUTABLE", None)
    if cartopian_claude and wrapper_install_root is None and capabilities_activated:
        findings.append(
            _finding(
                "claude-wrapper-untrusted",
                "guard",
                "activated Claude containment requires the shipped Cartopian "
                f"wrapper layout; resolved {resolved_agent!r} is a basename "
                "lookalike or has an incomplete install chain",
            )
        )
        return findings
    if activated_claude and _running_on_windows():
        findings.append(
            _finding(
                "claude-sandbox-host",
                "guard",
                "activated Claude containment is refused on native Windows: "
                "Cartopian has not attested a shell sandbox or an exact native "
                "Claude launch chain on that host; use native macOS/Linux or a "
                "separately isolated manual launch",
            )
        )
        return findings
    if capability_claude or (activated_claude and _running_on_windows()):
        from cli.claude_launch_settings import (
            SettingsError,
            _cartopian_home,
            claude_executable_protected_roots,
            enforcement_protected_roots,
            filesystem_path_is_within,
            unsafe_precontainment_path_components,
        )

        governed_work_roots = list(resolved_work_roots.values())
        wrapper_candidates = (
            os.path.abspath(str(resolved_agent)),
            os.path.realpath(str(resolved_agent)),
            os.path.abspath(str(wrapper_install_root)),
            os.path.realpath(str(wrapper_install_root)),
        )
        governed_launch_roots = (str(project_root), *governed_work_roots)
        if any(
            filesystem_path_is_within(candidate, root)
            for candidate in wrapper_candidates
            for root in governed_launch_roots
        ):
            findings.append(
                _finding(
                    "claude-wrapper-governed",
                    "guard",
                    "activated Claude containment refuses a wrapper/install root "
                    "inside the project or any declared work root before startup",
                )
            )
            return findings
        try:
            unsafe_path = unsafe_precontainment_path_components(
                active_environ, project_root, governed_work_roots
            )
        except SettingsError as exc:
            findings.append(
                _finding("claude-launch-path-untrusted", "guard", str(exc))
            )
            return findings
        if unsafe_path:
            findings.append(
                _finding(
                    "claude-launch-path-untrusted",
                    "guard",
                    "activated Claude launch refuses empty/relative PATH entries "
                    "and entries inside the project, any declared work root, "
                    "or any implicit shell-writable root "
                    "before pre-containment helper execution: "
                    + ", ".join(unsafe_path),
                )
            )
            return findings
        try:
            claude_executable_roots = claude_executable_protected_roots(
                project_root,
                resolved_work_roots,
                claude_probe_environ,
                windows=_running_on_windows(),
            )
            claude_probe_environ["CARTOPIAN_CLAUDE_EXECUTABLE"] = (
                claude_executable_roots[0]
            )
            if launch_bindings is not None:
                launch_bindings["claude_executable"] = claude_executable_roots[0]
            enforcement_roots = tuple(
                dict.fromkeys(
                    (
                        *enforcement_protected_roots(
                            wrapper_install_root,
                            active_environ,
                            windows=_running_on_windows(),
                        ),
                        *claude_executable_roots,
                    )
                )
            )
        except SettingsError as exc:
            findings.append(
                _finding("claude-executable-untrusted", "guard", str(exc))
            )
            return findings
    elif trusted_cartopian_claude:
        # Completion-only launches still freeze the exact executable that was
        # version-checked.  Resolve it once here and pass the binding through
        # dispatch instead of allowing the wrapper to repeat a PATH lookup.
        from cli.claude_launch_settings import (
            SettingsError,
            resolve_claude_executable,
        )

        try:
            claude_executable = resolve_claude_executable(
                claude_probe_environ,
                windows=_running_on_windows(),
            )
            claude_executable_roots = (claude_executable,)
            if _running_on_windows() and claude_executable.lower().endswith(
                (".cmd", ".bat")
            ):
                raise SettingsError(
                    "hook-enabled native-Windows Claude launches require a native "
                    "executable; .cmd/.bat shims cannot preserve the exact settings "
                    "argv boundary"
                )
            claude_probe_environ["CARTOPIAN_CLAUDE_EXECUTABLE"] = claude_executable
            if launch_bindings is not None:
                launch_bindings["claude_executable"] = claude_executable
        except SettingsError as exc:
            findings.append(
                _finding("claude-executable-untrusted", "guard", str(exc))
            )
            return findings
    if (
        trusted_cartopian_claude
        and project_root is not None
        and not capabilities_activated
    ):
        from cli.claude_launch_settings import SettingsError, _project_entries

        legacy_events = ["Stop"]
        legacy_locations: list[str] = []
        try:
            for event in legacy_events:
                legacy_locations.extend(
                    str(path)
                    for path, _entry in _project_entries(
                        project_root,
                        event,
                        active_environ,
                        windows=_running_on_windows(),
                    )
                )
        except SettingsError as exc:
            findings.append(_finding("claude-legacy-hook", "guard", str(exc)))
        if legacy_locations:
            findings.append(
                _finding(
                    "claude-legacy-hook",
                    "guard",
                    "persistent Cartopian Claude hook registration is incompatible "
                    "with role/report-bound process settings: "
                    + ", ".join(sorted(set(legacy_locations)))
                    + "; remove it with scripts/install.py --claude-hook "
                    + str(project_root),
                )
            )
    if capability_claude:
        # Activated launches exclude the normal user/project/local settings
        # scopes.  Only Claude's always-loaded legacy global file and the
        # inherited host environment can affect the process before containment,
        # so validate those inputs at the shared dispatch/rehearsal boundary.
        from cli.claude_launch_settings import (
            SettingsError,
            _effective_host_tool_environment,
            _hook_startup_paths,
            _refuse_external_host_tool_overrides,
            _refuse_hook_startup_env,
            _refuse_merged_sandbox_exclusions,
            _refuse_unattested_wsl,
            enforcement_protected_roots,
            filesystem_path_is_within,
            refuse_sandbox_glob_paths,
            registered_foreign_work_roots,
            registered_project_roots,
            sandbox_implicit_writable_roots,
            sandbox_host_executable_roots,
            validate_foreign_work_root_aliases,
            validate_implicit_writable_overlaps,
            validate_linux_mount_boundaries,
            validate_shell_writable_hardlinks,
        )
        from cli.claude_hook import (
            _validate_activated_project_aliases,
            _validate_config_file_identity,
        )

        alias_environ = os.environ if environ is None else environ
        try:
            _validate_config_file_identity(
                project_root / "cartopian.toml", "project config"
            )
            _validate_config_file_identity(
                project_root / "cartopian.local.toml", "local config"
            )
            _validate_config_file_identity(
                _cartopian_home(alias_environ, windows=False) / "cartopian.toml",
                "global config",
            )
            _validate_config_file_identity(
                _cartopian_home(alias_environ, windows=False) / "projects.json",
                "project registry",
            )
            _validate_activated_project_aliases(project_root)
        except Exception as exc:
            findings.append(
                _finding("claude-project-path-alias", "guard", str(exc))
            )
        try:
            registry_project_roots = registered_project_roots(alias_environ)
            foreign_work_roots = registered_foreign_work_roots(
                alias_environ,
                active_project=project_root,
                active_work_roots=tuple(resolved_work_roots.values()),
            )
        except SettingsError as exc:
            findings.append(
                _finding("claude-project-path-alias", "guard", str(exc))
            )

        if wrapper_install_root is not None:
            try:
                host_tool_environ, _host_tool_sources = (
                    _effective_host_tool_environment(
                        project_root,
                        os.environ if environ is None else environ,
                    )
                )
                host_executable_roots = sandbox_host_executable_roots(
                    host_tool_environ
                )
                enforcement_roots = tuple(
                    dict.fromkeys(
                        (
                            *enforcement_protected_roots(
                                wrapper_install_root,
                                os.environ if environ is None else environ,
                                windows=False,
                            ),
                            *host_executable_roots,
                            *claude_executable_roots,
                        )
                    )
                )
            except SettingsError as exc:
                findings.append(
                    _finding("claude-sandbox-host-executable", "guard", str(exc))
                )

        try:
            _refuse_unattested_wsl(
                os.environ if environ is None else environ
            )
        except SettingsError as exc:
            findings.append(_finding("claude-sandbox-host", "guard", str(exc)))
        try:
            _refuse_hook_startup_env(
                project_root,
                os.environ if environ is None else environ,
                windows=False,
                isolated_sources=True,
            )
        except SettingsError as exc:
            findings.append(_finding("claude-hook-env", "guard", str(exc)))
        try:
            _refuse_merged_sandbox_exclusions(
                project_root,
                os.environ if environ is None else environ,
            )
        except SettingsError as exc:
            findings.append(
                _finding(
                    "claude-sandbox-excluded-command",
                    "guard",
                    str(exc),
                )
            )
        try:
            _refuse_external_host_tool_overrides(
                project_root,
                os.environ if environ is None else environ,
            )
        except SettingsError as exc:
            findings.append(
                _finding("claude-host-tool-override", "guard", str(exc))
            )
    elif trusted_cartopian_claude and project_root is not None:
        # Dispatch always installs the Stop hook, even for an ungated project.
        from cli.claude_launch_settings import SettingsError, _refuse_hook_startup_env

        try:
            _refuse_hook_startup_env(
                project_root,
                os.environ if environ is None else environ,
                windows=_running_on_windows(),
                isolated_sources=activated_claude,
            )
        except SettingsError as exc:
            findings.append(_finding("claude-hook-env", "guard", str(exc)))
    if activated_claude:
        from cli.claude_launch_settings import (
            SettingsError,
            project_git_protected_roots,
        )

        try:
            git_protected_roots = project_git_protected_roots(
                project_root,
                active_environ,
            )
        except SettingsError as exc:
            findings.append(
                _finding("claude-git-metadata", "guard", str(exc))
            )
    if capability_claude:
        active_environ = os.environ if environ is None else environ
        try:
            startup_paths = _hook_startup_paths(
                project_root,
                active_environ,
                windows=False,
                isolated_sources=True,
            )
        except SettingsError:
            # The startup-environment check above already records the more
            # specific configuration-path diagnostic.
            startup_paths = ()
        protected_policy_paths = (
            project_root.resolve(),
            *registry_project_roots,
            *enforcement_roots,
            *git_protected_roots,
            *startup_paths,
        )
        implicit_roots = tuple(
            path
            for path in sandbox_implicit_writable_roots(active_environ)
            if os.path.lexists(path)
        )
        try:
            validate_foreign_work_root_aliases(
                tuple(resolved_work_roots.values()), foreign_work_roots
            )
        except SettingsError as exc:
            findings.append(
                _finding("claude-work-root-alias", "guard", str(exc))
            )
        try:
            validate_implicit_writable_overlaps(
                protected_policy_paths, active_environ
            )
        except SettingsError as exc:
            findings.append(
                _finding("claude-implicit-write-overlap", "guard", str(exc))
            )
        try:
            validate_linux_mount_boundaries(
                (
                    project_root.resolve(),
                    *resolved_work_roots.values(),
                    *implicit_roots,
                ),
                (*protected_policy_paths, *foreign_work_roots),
            )
        except SettingsError as exc:
            findings.append(
                _finding("claude-mount-alias", "guard", str(exc))
            )
        try:
            validate_shell_writable_hardlinks(
                tuple(resolved_work_roots.values()),
                active_environ,
                protected_roots=protected_policy_paths,
            )
        except SettingsError as exc:
            findings.append(
                _finding("claude-shell-hardlink", "guard", str(exc))
            )
        try:
            refuse_sandbox_glob_paths(
                (
                    *protected_policy_paths,
                    *(Path(path).resolve() for path in resolved_work_roots.values()),
                )
            )
        except SettingsError as exc:
            findings.append(_finding("claude-sandbox-path", "guard", str(exc)))
    if activated_claude and "write:worktree" in (
        role_record.get("effective_grants") or []
    ):
        protected_roots = [Path(path) for path in enforcement_roots]
        if capability_claude:
            protected_roots = [Path(path) for path in protected_policy_paths]
        nested_roots = []
        executable_conflicts = []
        for path in resolved_work_roots.values():
            canonical_root = Path(path).resolve()
            root_conflict = False
            for protected_root in protected_roots:
                if filesystem_path_is_within(canonical_root, protected_root):
                    nested_roots.append(
                        f"{canonical_root} (inside protected {protected_root})"
                    )
                    root_conflict = True
                    break
                if canonical_root != protected_root and filesystem_path_is_within(
                    protected_root, canonical_root
                ):
                    nested_roots.append(
                        f"protected {protected_root} (inside writable "
                        f"{canonical_root})"
                    )
                    root_conflict = True
                    break
            if not root_conflict:
                writable_parents = list(resolved_work_roots.values())
                if capability_claude:
                    writable_parents.extend(
                        sandbox_implicit_writable_roots(active_environ)
                    )
                for writable_parent in writable_parents:
                    canonical_parent = Path(writable_parent).resolve()
                    if canonical_parent == canonical_root:
                        continue
                    if filesystem_path_is_within(canonical_root, canonical_parent):
                        nested_roots.append(
                            f"{canonical_root} (inside shell-writable "
                            f"{canonical_parent})"
                        )
                        break
            if capability_claude:
                for executable_root in host_executable_roots:
                    if filesystem_path_is_within(executable_root, canonical_root):
                        executable_conflicts.append(
                            f"{executable_root} (inside writable {canonical_root})"
                        )
                        break
        if nested_roots:
            findings.append(
                _finding(
                    "claude-nested-work-root",
                    "guard",
                    "activated Claude containment cannot grant write:worktree "
                    "where a work-root directory entry is inside another "
                    "shell-writable/protected root, or a protected root is "
                    "inside the writable tree: "
                    + ", ".join(nested_roots)
                    + " — keep writable product roots disjoint from every "
                    "protected or implicit-writable root",
                )
            )
        if executable_conflicts:
            findings.append(
                _finding(
                    "claude-writable-host-executable",
                    "guard",
                    "activated POSIX Claude sandbox refuses a host executable/PATH "
                    "search directory inside an authorized writable work root: "
                    + ", ".join(executable_conflicts)
                    + " — keep Claude host helpers outside every work root",
                )
            )
    if trusted_cartopian_claude and resolved_agent is not None:
        from cli.claude_launch_settings import (
            SettingsError,
            probe_claude_version,
            require_claude_version,
        )

        version, raw_version = probe_claude_version(
            executable=(
                claude_executable_roots[0]
                if claude_executable_roots
                else "claude"
            ),
            windows=_running_on_windows(),
            environ=claude_probe_environ,
        )
        try:
            if version is None:
                raise SettingsError(
                    "cannot determine Claude Code version"
                    + (f" ({raw_version})" if raw_version else "")
                )
            require_claude_version(
                ".".join(map(str, version)),
                activated=bool(activated_claude),
            )
        except SettingsError as exc:
            findings.append(_finding("claude-version-unsupported", "guard", str(exc)))
    if agent and resolved_agent is None:
        findings.append(
            _finding(
                "agent-not-found",
                "error",
                f"handoff agent not found on PATH: {agent} — install the wrapper "
                f"(on native Windows the `.cmd` shim in wrappers/ps1 must be on PATH), "
                f"or set roles.{role}.agent to an absolute path",
            )
        )
    return findings
