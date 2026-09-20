"""Build Cartopian's process-scoped Claude Code hook settings.

The shipped Claude wrappers call this helper at the dispatch boundary.  It
prints one compact JSON object for Claude Code's ``--settings`` option and
never writes a user, project, or local settings file. Completion-only launches
retain Claude's normal settings sources. An activated capability launch
disables user, project, and local filesystem settings sources. Its explicit
process settings and administrator-managed policy remain active, while
filesystem hooks/plugins cannot execute outside containment and project
settings cannot hot-load a new exception after a cwd change.

The two hooks are independent:

* ``PreToolUse`` is included only when the wrapper supplies the dispatched
  role boundary and the resolved project config activates capability grants.
* ``Stop`` is included only when dispatch supplied an expected report path.

An activated POSIX handoff also receives a strict, process-scoped Claude
sandbox policy. Shell commands and their children cannot write the Cartopian
project directory at all; authorized project-artifact mutations continue
through the capability hook's structured tools, while project-writing mediated
commands must run outside that handoff. Declared work roots remain
shell-writable only when the dispatched role holds ``write:worktree``.
The sandbox refuses launch when OS enforcement is unavailable and disables the
unsandboxed retry escape hatch. Activated native-Windows handoffs refuse before
launch until Cartopian can attest both a shell sandbox and an exact native
Claude executable chain.

Claude merges hook arrays across settings scopes. Completion-only launches
therefore reuse a normally loaded Cartopian entry only when its whole command
exactly matches this launch, including immutable report bindings. Activated
launches exclude those normal scopes, so an old registration is inert and does
not require cleanup. The operator can still remove project registrations with
``scripts/install.py --claude-hook PROJECT_DIR``.
"""
from __future__ import annotations

import argparse
import json
import math
import ntpath
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

if __package__ in (None, ""):  # invoked from an installed wrapper
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_UNCONTAINED_SESSION_TOOLS = (
    "Agent",
    "Task",
    "EnterWorktree",
    "ExitWorktree",
    "TeamCreate",
    "TeamDelete",
    "CronCreate",
    "CronDelete",
    "CronList",
    "SendMessage",
    "SendFile",
    "RemoteTrigger",
)
CAPABILITY_MATCHER = "|".join(
    (
        "Read",
        "NotebookRead",
        "Glob",
        "Grep",
        "Write",
        "Edit",
        "MultiEdit",
        "NotebookEdit",
        *_UNCONTAINED_SESSION_TOOLS,
    )
)

ROLE_ENV = "CARTOPIAN_ROLE"
REPORT_ENV = "CARTOPIAN_EXPECTED_REPORT_PATH"
VARIANT_ENV = "CARTOPIAN_EXPECTED_REPORT_VARIANT"
CLAUDE_EXECUTABLE_ENV = "CARTOPIAN_CLAUDE_EXECUTABLE"
CLAUDE_HOST_TMPDIR_ENV = "CARTOPIAN_CLAUDE_HOST_TMPDIR"

# A settings ``env`` block reaches command-hook subprocesses.  Isolated Python
# ignores Python's own environment controls, but the platform loader acts
# before Python can do so.  Claude startup modes can also suppress the hooks
# or weaken Stop enforcement before the inline settings layer is applied.
_HOOK_ENV_PREFIXES = ("PYTHON", "LD_", "DYLD_", "OPENSSL_", "BUN_")
_HOOK_ENV_KEYS = {
    "__PYVENV_LAUNCHER__",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_CODE_PROCESS_WRAPPER",
    "CLAUDE_CODE_SAFE_MODE",
    "CLAUDE_CODE_SIMPLE",
    "CLAUDE_CODE_STOP_HOOK_BLOCK_CAP",
    "CLAUDE_CODE_TMPDIR",
    "CLAUDE_TMPDIR",
    "GCONV_PATH",
    "GLIBC_TUNABLES",
    "HOME",
    "HOMEDRIVE",
    "HOMEPATH",
    "NODE_OPTIONS",
    "NODE_PATH",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
}
_INHERITED_HOOK_ENV_KEYS = {
    "CLAUDE_CODE_PROCESS_WRAPPER",
    "CLAUDE_CODE_SAFE_MODE",
    "CLAUDE_CODE_SIMPLE",
    "CLAUDE_CODE_STOP_HOOK_BLOCK_CAP",
    "CLAUDE_CODE_TMPDIR",
    "CLAUDE_TMPDIR",
}

# Child processes executed before Claude's sandbox exists must receive an
# explicit environment with every interpreter/loader startup injection knob
# removed. Dispatch performs the same cleanup for the detached launch chain;
# this local copy keeps direct helper probes safe and makes that guarantee
# independent of a caller remembering to sanitize first.
_PRECONTAINMENT_CHILD_ENV_KEYS = frozenset(
    {
        "BASHOPTS",
        "BASH_ENV",
        "BASH_XTRACEFD",
        "ENV",
        "GCONV_PATH",
        "GLIBC_TUNABLES",
        "__PYVENV_LAUNCHER__",
        "NODE_OPTIONS",
        "NODE_PATH",
        "OPENSSL_CONF",
        "OPENSSL_MODULES",
        "PS4",
        "SHELLOPTS",
    }
)
_PRECONTAINMENT_CHILD_ENV_PREFIXES = (
    "BASH_FUNC_",
    "BUN_",
    "DYLD_",
    "LD_",
    "PYTHON",
)
STOP_MAX_BLOCKS_ENV = "CARTOPIAN_STOP_GUARD_MAX_BLOCKS"
DEFAULT_STOP_MAX_BLOCKS = 3
CLAUDE_EXEC_HOOK_MIN_VERSION = (2, 1, 139)
# 2.1.278 is the first activated build Cartopian has behaviorally attested for
# the deny-default macOS Seatbelt profile used below. In particular, the
# profile blocks creation of fresh hard links; launch-time alias validation
# separately rejects pre-existing links that leave authorized work roots.
CLAUDE_ACTIVATED_MIN_VERSION = (2, 1, 278)
_SANDBOX_GLOB_CHARACTERS = frozenset("*?[]")
_EXPLICIT_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


class SettingsError(RuntimeError):
    """The per-launch settings layer cannot be constructed safely."""


class LegacyHookError(SettingsError):
    """A normally loaded settings file would duplicate Cartopian's hook."""


def _sanitized_precontainment_environment(
    environ: Mapping[str, str],
) -> dict[str, str]:
    """Return an explicit safe environment for unsandboxed helper children."""
    return {
        key: value
        for key, value in environ.items()
        if key not in _PRECONTAINMENT_CHILD_ENV_KEYS
        and not key.startswith(_PRECONTAINMENT_CHILD_ENV_PREFIXES)
    }


def parse_claude_version(raw: str) -> Optional[tuple[int, int, int]]:
    """Extract Claude Code's semantic core from ``claude --version``."""
    match = re.search(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)", raw)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def _windows_comspec(environ: Mapping[str, str]) -> str:
    """Resolve the command interpreter used for a native-Windows shim."""
    if environ.get("COMSPEC"):
        return environ["COMSPEC"]
    system_root = environ.get("SystemRoot") or environ.get("windir")
    if system_root:
        candidate = os.path.join(system_root, "System32", "cmd.exe")
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("cmd.exe", path=environ.get("PATH")) or "cmd.exe"


def probe_claude_version(
    executable: str = "claude",
    *,
    windows: Optional[bool] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> tuple[Optional[tuple[int, int, int]], str]:
    """Return the installed Claude version and its raw diagnostic text.

    npm normally exposes Claude as a ``.cmd`` shim on native Windows. Python's
    ``CreateProcess`` path cannot execute that shim directly, so resolve it
    with PATHEXT-aware ``which`` and invoke it through ``cmd.exe``. The shim
    path travels in an environment value rather than command text, avoiding
    command metacharacter interpretation in unusual installation paths.
    """
    if windows is None:
        windows = os.name == "nt"
    active_environ = _sanitized_precontainment_environment(
        os.environ if environ is None else environ
    )
    try:
        resolved = resolve_claude_executable(
            active_environ,
            executable=executable,
            windows=windows,
        )
    except SettingsError as exc:
        return None, str(exc)
    command = [resolved, "--version"]
    run_environ: Mapping[str, str] = active_environ
    if windows and resolved.lower().endswith((".cmd", ".bat")):
        executable_env = "CARTOPIAN_CLAUDE_VERSION_EXECUTABLE"
        active_environ[executable_env] = resolved
        command = [
            _windows_comspec(active_environ),
            "/d",
            "/v:off",
            "/s",
            "/c",
            # Standard outer-quoted batch invocation. CALL would perform a
            # dangerous second expansion pass over percent-shaped path text.
            f'""%{executable_env}%" --version"',
        ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            env=run_environ,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    raw = (result.stdout or result.stderr).strip()
    if result.returncode != 0:
        return None, raw or f"exit {result.returncode}"
    return parse_claude_version(raw), raw


def resolve_claude_executable(
    environ: Mapping[str, str],
    *,
    executable: str = "claude",
    windows: Optional[bool] = None,
) -> str:
    """Resolve Claude without executing it and return one absolute spelling."""
    if windows is None:
        windows = os.name == "nt"
    configured = (environ.get(CLAUDE_EXECUTABLE_ENV) or "").strip()
    candidate = configured or shutil.which(executable, path=environ.get("PATH"))
    if not candidate:
        raise SettingsError(f"{executable!r} was not found on PATH")
    path_flavor = ntpath if windows else os.path
    if not path_flavor.isabs(candidate):
        raise SettingsError(
            "activated Claude launch requires an absolute underlying executable "
            f"path (resolved {candidate!r})"
        )
    lexical = path_flavor.abspath(candidate)
    real = os.path.realpath(lexical) if not windows or os.name == "nt" else lexical
    if (not windows or os.name == "nt") and not os.path.isfile(real):
        raise SettingsError(
            f"resolved Claude executable is not a regular file: {lexical}"
        )
    if not windows and not os.access(real, os.X_OK):
        raise SettingsError(f"resolved Claude executable is not executable: {lexical}")
    return lexical


def claude_executable_protected_roots(
    project_dir: Path,
    work_roots: Mapping[str, str],
    environ: Mapping[str, str],
    *,
    windows: Optional[bool] = None,
) -> tuple[str, ...]:
    """Bind Claude plus every PATH search directory for the session.

    Protecting only today's selected file is insufficient: the session could
    plant a new ``claude`` in a search directory and win a later dispatch if
    PATH ordering changes. Freezing the full chain also makes the dispatch,
    wrapper helper, hook, and containment-matrix calculations identical after
    the exact executable is carried in the environment.
    """
    if windows is None:
        windows = os.name == "nt"
    lexical = resolve_claude_executable(environ, windows=windows)
    real = os.path.realpath(lexical)
    governed = (
        os.path.realpath(project_dir),
        *(os.path.realpath(path) for path in work_roots.values()),
        *(os.path.realpath(path) for path in sandbox_implicit_writable_roots(environ)),
    )
    for candidate in dict.fromkeys((lexical, real)):
        for root in governed:
            if filesystem_path_is_within(candidate, root):
                raise SettingsError(
                    "activated Claude launch refuses an underlying Claude "
                    "executable inside a project, work, or implicit shell-writable "
                    f"root ({candidate} inside {root})"
                )
    protected: list[str] = [lexical, real]
    raw_path = environ.get("PATH", "")
    separator = ";" if windows else os.pathsep
    path_flavor = ntpath if windows else os.path
    for raw_component in raw_path.split(separator):
        if not raw_component or not path_flavor.isabs(raw_component):
            continue
        lexical_component = path_flavor.abspath(raw_component)
        real_component = (
            os.path.realpath(lexical_component)
            if not windows or os.name == "nt"
            else lexical_component
        )
        for candidate in (lexical_component, real_component):
            if candidate not in protected:
                protected.append(candidate)
    return tuple(protected)


def require_claude_version(raw: str, *, activated: bool) -> tuple[int, int, int]:
    """Fail closed if this launch relies on unsupported Claude behavior."""
    found = parse_claude_version(raw)
    minimum = (
        CLAUDE_ACTIVATED_MIN_VERSION
        if activated
        else CLAUDE_EXEC_HOOK_MIN_VERSION
    )
    rendered_minimum = ".".join(map(str, minimum))
    if found is None:
        raise SettingsError(
            "cannot determine Claude Code version; Cartopian hooks require "
            f"Claude Code {rendered_minimum}+"
        )
    if found < minimum:
        rendered_found = ".".join(map(str, found))
        raise SettingsError(
            f"Claude Code {rendered_found} is too old for this Cartopian launch; "
            f"upgrade to {rendered_minimum}+"
        )
    return found


def hook_handler(
    install_root: Path,
    script_name: str,
    *,
    interpreter: Optional[Path] = None,
    arguments: Sequence[str] = (),
) -> dict[str, Any]:
    """Return Claude's shell-free exec form for an installed Python hook."""
    executable = os.path.abspath(interpreter or Path(sys.executable))
    return {
        "type": "command",
        "command": executable,
        # -I ignores PYTHON* environment controls and unsafe import paths; -S
        # prevents executable site/customization files from running before the
        # hook installs Cartopian's own absolute import root.
        "args": ["-I", "-S", str(install_root / "cli" / script_name), *arguments],
    }


def _python_protected_paths(
    interpreter: Optional[Path] = None,
) -> tuple[str, ...]:
    """Runtime paths a dispatched session must not mutate between hooks."""
    selected = Path(os.path.abspath(interpreter or sys.executable))
    if os.path.realpath(selected) == os.path.realpath(sys.executable):
        candidates = (selected, Path(sys.prefix), Path(sys.base_prefix))
    else:
        # A caller-supplied interpreter is primarily a test/embedding seam.
        # Its prefix is not introspectable without executing it, so protect
        # the executable and the conventional venv root around bin/Scripts.
        candidates = (selected, selected.parent, selected.parent.parent)
    protected: list[str] = []
    for candidate in candidates:
        for path in (os.path.abspath(candidate), os.path.realpath(candidate)):
            if path not in protected:
                protected.append(path)
    return tuple(protected)


def enforcement_protected_roots(
    install_root: Path,
    environ: Mapping[str, str],
    *,
    windows: Optional[bool] = None,
    interpreter: Optional[Path] = None,
) -> tuple[str, ...]:
    """Roots whose mutation would let a session replace its own guard."""
    return tuple(
        dict.fromkeys(
            (
                os.path.realpath(install_root),
                os.path.realpath(_cartopian_home(environ, windows=windows)),
                *_python_protected_paths(interpreter),
            )
        )
    )


def sandbox_host_executable_roots(
    environ: Mapping[str, str],
    *,
    platform_name: Optional[str] = None,
) -> tuple[str, ...]:
    """Return immutable roots for host-side POSIX sandbox executables.

    Claude resolves ``bwrap`` for each sandboxed Linux command, launches
    ``socat`` in its unsandboxed Linux host process, and may use a host ``rg``
    on POSIX. Protect the selected executable plus every earlier PATH search
    directory so a writable directory cannot plant a higher-precedence binary,
    replace a symlink, or replace an executable between tool calls.

    Administrator-managed absolute executable overrides remain part of
    Claude's trusted machine policy.  Cartopian protects the ordinary PATH
    resolution used when no such managed override is installed.
    """
    platform_name = sys.platform if platform_name is None else platform_name
    linux = platform_name.startswith("linux")
    if not linux and platform_name != "darwin":
        return ()
    raw_path = environ.get("PATH")
    if raw_path is None:
        return ()
    path_components: list[tuple[str, str]] = []
    for raw_component in raw_path.split(os.pathsep):
        if not raw_component or not os.path.isabs(raw_component):
            raise SettingsError(
                "activated POSIX Claude sandbox refuses an empty or relative "
                f"PATH search component ({raw_component!r})"
            )
        lexical_component = os.path.abspath(raw_component)
        path_components.append(
            (lexical_component, os.path.realpath(lexical_component))
        )
    protected: list[str] = []
    executables = ("bwrap", "socat", "rg") if linux else ("rg",)
    for executable in executables:
        resolved = shutil.which(executable, path=raw_path)
        resolved_dir = (
            os.path.abspath(os.path.dirname(resolved))
            if resolved is not None
            else None
        )
        # Freeze every search directory that precedes the selected executable.
        # Otherwise a session could plant an earlier same-named binary after
        # launch and win Claude's next bare-name lookup. If the executable is
        # absent, freeze the entire search path and let failIfUnavailable reject
        # the effective backend when Claude starts it.
        for lexical_component, real_component in path_components:
            for candidate in (lexical_component, real_component):
                if candidate not in protected:
                    protected.append(candidate)
            if resolved_dir is not None and lexical_component == resolved_dir:
                break
        if resolved is None:
            continue
        lexical = os.path.abspath(resolved)
        real = os.path.realpath(lexical)
        if not os.path.isfile(real) or not os.access(real, os.X_OK):
            raise SettingsError(
                "activated Linux Claude sandbox requires an executable, regular "
                f"{executable!r} host dependency (resolved {lexical!r})"
            )
        for candidate in (
            lexical,
            real,
        ):
            if candidate not in protected:
                protected.append(candidate)
    return tuple(protected)


def _project_git_marker(project_dir: Path) -> Optional[Path]:
    return next(
        (
            ancestor / ".git"
            for ancestor in (project_dir, *project_dir.parents)
            if os.path.lexists(ancestor / ".git")
        ),
        None,
    )


def _contains_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _git_absolute_path(
    project_dir: Path,
    selector: str,
    *,
    required: bool,
    environ: Optional[Mapping[str, str]] = None,
) -> Optional[str]:
    """Resolve one Git path without line-splitting a path-shaped record."""
    source_env = _sanitized_precontainment_environment(
        os.environ if environ is None else environ
    )
    clean_env = {
        key: value
        for key, value in source_env.items()
        if not key.upper().startswith("GIT_")
    }
    try:
        probe = subprocess.run(
            [
                "git",
                "-C",
                str(project_dir),
                "rev-parse",
                "--path-format=absolute",
                selector,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
            env=clean_env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if required:
            raise SettingsError(
                "cannot resolve governed-project Git metadata for Claude sandbox "
                f"protection: {exc}"
            ) from exc
        return None
    if probe.returncode != 0:
        if required:
            raise SettingsError(
                "cannot resolve governed-project Git metadata for Claude sandbox "
                f"protection: {probe.stderr.strip() or f'exit {probe.returncode}'}"
            )
        return None
    raw = probe.stdout
    if raw.endswith("\r\n"):
        value = raw[:-2]
    elif raw.endswith("\n"):
        value = raw[:-1]
    else:
        raise SettingsError(
            f"git rev-parse {selector} returned an unterminated path record"
        )
    if not value or _contains_control_character(value):
        raise SettingsError(
            "governed-project Git metadata path contains a control character "
            f"that cannot be represented safely in Claude policy: {value!r}"
        )
    if not os.path.isabs(value) or not os.path.exists(value):
        raise SettingsError(
            f"git rev-parse {selector} returned an invalid metadata path: {value!r}"
        )
    return os.path.realpath(value)


def project_git_protected_roots(
    project_dir: Path,
    environ: Optional[Mapping[str, str]] = None,
) -> tuple[str, ...]:
    """Return external Git metadata Claude may auto-allow for this cwd."""
    marker = _project_git_marker(project_dir)
    git_dir = _git_absolute_path(
        project_dir,
        "--git-dir",
        required=marker is not None,
        environ=environ,
    )
    if git_dir is None:
        return ()
    common_dir = _git_absolute_path(
        project_dir,
        "--git-common-dir",
        required=True,
        environ=environ,
    )
    assert common_dir is not None
    return tuple(
        dict.fromkeys(
            (
                *((os.path.abspath(marker),) if marker is not None else ()),
                git_dir,
                common_dir,
            )
        )
    )


def filesystem_path_is_within(
    path: os.PathLike[str] | str,
    root: os.PathLike[str] | str,
) -> bool:
    """Compare existing path ancestry by identity, with a lexical fallback."""
    candidate = os.path.abspath(path)
    protected = os.path.abspath(root)
    probe = candidate
    while True:
        try:
            if os.path.samefile(probe, protected):
                return True
        except OSError:
            pass
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    candidate = os.path.realpath(candidate)
    protected = os.path.realpath(protected)
    try:
        return os.path.commonpath((candidate, protected)) == protected
    except ValueError:
        return False


def _minimal_identity_roots(
    paths: Sequence[os.PathLike[str] | str],
) -> tuple[str, ...]:
    """Return existing roots once, dropping aliases and nested duplicates."""
    candidates = sorted(
        {os.path.realpath(os.path.abspath(path)) for path in paths},
        key=lambda path: (len(Path(path).parts), path),
    )
    roots: list[str] = []
    root_identities: set[tuple[int, int]] = set()
    for candidate in candidates:
        try:
            info = os.stat(candidate)
        except OSError:
            info = None
        identity = None if info is None else (info.st_dev, info.st_ino)
        if identity is not None and identity in root_identities:
            continue
        if any(filesystem_path_is_within(candidate, root) for root in roots):
            continue
        roots.append(candidate)
        if identity is not None:
            root_identities.add(identity)
    return tuple(roots)


def validate_writable_work_root_hardlinks(
    paths: Sequence[os.PathLike[str] | str],
    *,
    protected_roots: Sequence[os.PathLike[str] | str] = (),
) -> None:
    """Reject regular-file hard links that leave a writable root set.

    macOS Seatbelt prevents a sandboxed command from creating a new hard link
    under Cartopian's deny-default profile, and Linux bind mounts put protected
    and writable roots on distinct mounts. Neither mechanism makes a
    *pre-existing* work-root hard link read-only: writing that authorized name
    mutates every other name for the inode. Count every name visible inside the
    complete authorized root set and require it to equal ``st_nlink``. Internal
    hard links remain usable; an unobserved name means the inode crosses the
    captured write boundary and launch must fail closed.
    """
    roots = _minimal_identity_roots(paths)
    if not roots:
        return

    protected_directories: list[str] = []
    protected_files: list[str] = []
    for raw_root in protected_roots:
        root = os.path.realpath(os.path.abspath(raw_root))
        if os.path.isdir(root):
            protected_directories.append(root)
            continue
        if os.path.isfile(root):
            try:
                root_info = os.stat(root)
            except OSError as exc:
                raise SettingsError(
                    f"cannot inspect protected shell-write path {root}: {exc}"
                ) from exc
            if root_info.st_nlink > 1:
                raise SettingsError(
                    "protected shell-write file has multiple hard-link names "
                    f"and cannot be secured by path policy: {root}"
                )
        protected_files.append(root)

    def path_is_protected(candidate: str) -> bool:
        if any(
            filesystem_path_is_within(candidate, root)
            for root in protected_directories
        ):
            return True
        for protected_file in protected_files:
            try:
                if os.path.samefile(candidate, protected_file):
                    return True
            except OSError:
                if os.path.normcase(os.path.realpath(candidate)) == os.path.normcase(
                    protected_file
                ):
                    return True
        return False

    EntryIdentity = tuple[int, int, bytes]
    names_by_inode: dict[tuple[int, int], set[EntryIdentity]] = {}
    path_by_entry: dict[EntryIdentity, str] = {}
    regular_by_entry: dict[EntryIdentity, tuple[tuple[int, int], int]] = {}
    link_counts: dict[tuple[int, int], int] = {}

    def walk_error(exc: OSError) -> None:
        raise SettingsError(
            "cannot inspect shell-writable root for hard-link aliases: "
            f"{exc}"
        ) from exc

    for root in roots:
        if not os.path.isdir(root):
            raise SettingsError(
                f"shell-writable root is not an existing directory: {root}"
            )
        for directory, _subdirs, filenames in os.walk(
            root, followlinks=False, onerror=walk_error
        ):
            try:
                directory_info = os.stat(directory)
            except OSError as exc:
                raise SettingsError(
                    "cannot inspect shell-writable directory "
                    f"{directory}: {exc}"
                ) from exc
            for filename in filenames:
                candidate = os.path.abspath(os.path.join(directory, filename))
                entry = (
                    directory_info.st_dev,
                    directory_info.st_ino,
                    os.fsencode(filename),
                )
                # A firmlink or bind mount can expose one directory entry under
                # two pathnames. Count the directory entry, not its spellings;
                # otherwise duplicate mount views could mask one hard-link name
                # outside the writable set.
                record = regular_by_entry.get(entry)
                if record is None:
                    try:
                        info = os.lstat(candidate)
                    except OSError as exc:
                        raise SettingsError(
                            "cannot inspect shell-writable entry "
                            f"{candidate}: {exc}"
                        ) from exc
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink <= 1:
                        continue
                    inode = (info.st_dev, info.st_ino)
                    record = (inode, info.st_nlink)
                    regular_by_entry[entry] = record
                inode, observed_links = record
                if path_is_protected(candidate):
                    continue
                previous_count = link_counts.setdefault(inode, observed_links)
                if previous_count != observed_links:
                    raise SettingsError(
                        "shell-writable inode changed while its "
                        f"hard-link names were inspected: {candidate}"
                    )
                names_by_inode.setdefault(inode, set()).add(entry)
                path_by_entry[entry] = candidate

    for inode, entries in names_by_inode.items():
        expected = link_counts[inode]
        for entry in entries:
            candidate = path_by_entry[entry]
            try:
                current = os.lstat(candidate)
                current_parent = os.stat(os.path.dirname(candidate))
            except OSError as exc:
                raise SettingsError(
                    "shell-writable hard link changed during "
                    f"inspection ({candidate}: {exc})"
                ) from exc
            if (
                not stat.S_ISREG(current.st_mode)
                or (current.st_dev, current.st_ino) != inode
                or current.st_nlink != expected
                or (current_parent.st_dev, current_parent.st_ino) != entry[:2]
                or os.fsencode(os.path.basename(candidate)) != entry[2]
            ):
                raise SettingsError(
                    "shell-writable hard link changed during "
                    f"inspection: {candidate}"
                )
        if len(entries) != expected:
            example = min(path_by_entry[entry] for entry in entries)
            raise SettingsError(
                "shell-writable roots contain a regular file with "
                "hard-link names outside the authorized root set: "
                f"{example} (observed {len(entries)} of {expected} names); replace "
                "the cross-boundary hard link with an independent copy"
            )


def sandbox_implicit_writable_roots(
    environ: Mapping[str, str],
) -> tuple[str, ...]:
    """Return Claude/SRT's attested implicit POSIX write directories.

    Claude 2.1.278 combines Sandbox Runtime's fixed temp/npm/debug exceptions
    with its per-uid Claude temp directory in the effective shell allowlist.
    """
    home = _user_home(environ, windows=False)
    roots: list[os.PathLike[str] | str] = [
        "/tmp/claude",
        "/private/tmp/claude",
        home / ".npm" / "_logs",
        home / ".claude" / "debug",
    ]
    getuid = getattr(os, "getuid", None)
    if getuid is not None:
        uid = getuid()
        roots.extend((f"/tmp/claude-{uid}", f"/private/tmp/claude-{uid}"))
    # Preserve each lexical entry long enough for the caller to reject an
    # implicit root that is itself a symlink. Root scanning canonicalizes and
    # identity-deduplicates direct-directory aliases such as /tmp and
    # /private/tmp after that check.
    return tuple(dict.fromkeys(os.path.abspath(path) for path in roots))


def validate_implicit_writable_overlaps(
    protected_roots: Sequence[os.PathLike[str] | str],
    environ: Mapping[str, str],
) -> None:
    """Refuse protected policy paths that overlap Claude write exceptions."""
    implicit_roots = tuple(
        os.path.realpath(path)
        for path in sandbox_implicit_writable_roots(environ)
        if os.path.lexists(path)
    )
    for raw_protected in protected_roots:
        protected = os.path.realpath(os.path.abspath(raw_protected))
        for implicit in implicit_roots:
            if filesystem_path_is_within(
                protected, implicit
            ) or filesystem_path_is_within(implicit, protected):
                raise SettingsError(
                    "protected Claude policy path overlaps an implicit "
                    "shell-writable temp/npm/debug root: "
                    f"{protected} versus {implicit}; relocate the project, "
                    "Cartopian config/runtime, or implicit writable directory"
                )


def validate_shell_writable_hardlinks(
    work_roots: Sequence[os.PathLike[str] | str],
    environ: Mapping[str, str],
    *,
    protected_roots: Sequence[os.PathLike[str] | str] = (),
) -> None:
    """Validate aliases across every path a sandboxed shell can write."""
    writable: list[os.PathLike[str] | str] = list(work_roots)
    for implicit in sandbox_implicit_writable_roots(environ):
        if not os.path.lexists(implicit):
            continue
        path = Path(implicit)
        if path.is_symlink() or not path.is_dir():
            raise SettingsError(
                "Claude's implicit shell-writable path must be a direct "
                f"directory before activated launch: {implicit}"
            )
        writable.append(implicit)
    validate_writable_work_root_hardlinks(
        writable, protected_roots=protected_roots
    )


def _decode_mountinfo_path(raw: bytes) -> str:
    """Decode the kernel's octal mountinfo path escaping."""
    if re.search(rb"\\(?![0-7]{3})", raw):
        raise SettingsError(
            f"cannot decode Linux mountinfo path field safely: {raw!r}"
        )
    decoded = re.sub(
        rb"\\([0-7]{3})",
        lambda match: bytes((int(match.group(1), 8),)),
        raw,
    )
    path = os.fsdecode(decoded)
    if not path.startswith("/") or "\x00" in path:
        raise SettingsError(f"invalid Linux mountinfo path field: {raw!r}")
    return os.path.normpath(path)


def linux_mountinfo_records(
    mountinfo_path: Path = Path("/proc/self/mountinfo"),
) -> tuple[tuple[str, str, str], ...]:
    """Return ``(major:minor, filesystem-root, mountpoint)`` records."""
    try:
        lines = mountinfo_path.read_bytes().splitlines()
    except OSError as exc:
        raise SettingsError(
            f"cannot inspect Linux mount namespace at {mountinfo_path}: {exc}"
        ) from exc
    records: list[tuple[str, str, str]] = []
    for line_number, line in enumerate(lines, 1):
        fields = line.split()
        try:
            separator = fields.index(b"-")
        except ValueError as exc:
            raise SettingsError(
                f"malformed Linux mountinfo record {line_number}: missing separator"
            ) from exc
        if separator < 6 or len(fields) < separator + 4:
            raise SettingsError(
                f"malformed Linux mountinfo record {line_number}: too few fields"
            )
        try:
            device = fields[2].decode("ascii")
        except UnicodeDecodeError as exc:
            raise SettingsError(
                f"malformed Linux mountinfo device at record {line_number}"
            ) from exc
        if re.fullmatch(r"\d+:\d+", device) is None:
            raise SettingsError(
                f"malformed Linux mountinfo device at record {line_number}: "
                f"{device!r}"
            )
        records.append(
            (
                device,
                _decode_mountinfo_path(fields[3]),
                _decode_mountinfo_path(fields[4]),
            )
        )
    if not records:
        raise SettingsError(f"Linux mountinfo is empty: {mountinfo_path}")
    return tuple(records)


def _lexically_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:
        return False


def _mount_coordinates(
    path: os.PathLike[str] | str,
    records: Sequence[tuple[str, str, str]],
) -> tuple[tuple[str, str], ...]:
    """Map a namespace path to every longest matching filesystem coordinate."""
    candidate = os.path.realpath(os.path.abspath(path))
    matching = [
        record for record in records if _lexically_within(candidate, record[2])
    ]
    if not matching:
        raise SettingsError(
            f"Linux mount namespace has no containing mount for {candidate}"
        )
    deepest = max(len(Path(record[2]).parts) for record in matching)
    coordinates: list[tuple[str, str]] = []
    for device, filesystem_root, mountpoint in matching:
        if len(Path(mountpoint).parts) != deepest:
            continue
        relative = os.path.relpath(candidate, mountpoint)
        coordinate = filesystem_root
        if relative != ".":
            coordinate = posixpath.join(
                filesystem_root, relative.replace(os.path.sep, "/")
            )
        normalized = posixpath.normpath(coordinate)
        item = (device, normalized)
        if item not in coordinates:
            coordinates.append(item)
    return tuple(coordinates)


def _coordinate_relation(left: tuple[str, str], right: tuple[str, str]) -> int:
    """Return -1/0/1 for left-below/equal/right-below, or 2 if disjoint."""
    if left[0] != right[0]:
        return 2
    try:
        common = posixpath.commonpath((left[1], right[1]))
    except ValueError:
        return 2
    if left[1] == right[1]:
        return 0
    if common == right[1]:
        return -1
    if common == left[1]:
        return 1
    return 2


def validate_linux_mount_boundaries(
    governed_roots: Sequence[os.PathLike[str] | str],
    protected_roots: Sequence[os.PathLike[str] | str],
    *,
    platform_name: Optional[str] = None,
    mountinfo_path: Path = Path("/proc/self/mountinfo"),
) -> None:
    """Reject mount aliases that can bypass path-class or denyWrite policy."""
    platform_name = sys.platform if platform_name is None else platform_name
    if not platform_name.startswith("linux"):
        return
    records = linux_mountinfo_records(mountinfo_path)
    governed = tuple(
        dict.fromkeys(os.path.realpath(os.path.abspath(path)) for path in governed_roots)
    )
    protected = tuple(
        dict.fromkeys(os.path.realpath(os.path.abspath(path)) for path in protected_roots)
    )

    for _device, _filesystem_root, mountpoint in records:
        visible_mountpoint = os.path.realpath(mountpoint)
        for root in governed:
            if visible_mountpoint != root and _lexically_within(
                visible_mountpoint, root
            ):
                raise SettingsError(
                    "activated Linux Claude containment refuses a nested mount "
                    f"inside a governed or shell-writable root: {mountpoint} "
                    f"inside {root}"
                )

    coordinates = {
        path: _mount_coordinates(path, records)
        for path in (*governed, *protected)
    }
    for governed_path in governed:
        for protected_path in protected:
            lexical_relation = (
                -1
                if _lexically_within(governed_path, protected_path)
                and governed_path != protected_path
                else 1
                if _lexically_within(protected_path, governed_path)
                and governed_path != protected_path
                else 0
                if governed_path == protected_path
                else 2
            )
            for governed_coordinate in coordinates[governed_path]:
                for protected_coordinate in coordinates[protected_path]:
                    coordinate_relation = _coordinate_relation(
                        governed_coordinate, protected_coordinate
                    )
                    if coordinate_relation == 2:
                        continue
                    if coordinate_relation == lexical_relation:
                        continue
                    raise SettingsError(
                        "activated Linux Claude containment refuses a mount "
                        "alias whose filesystem coordinate overlaps a protected "
                        f"root: {governed_path} {governed_coordinate} versus "
                        f"{protected_path} {protected_coordinate}"
                    )


def unsafe_precontainment_path_components(
    environ: Mapping[str, str],
    project_dir: Path,
    work_roots: Sequence[str],
) -> tuple[str, ...]:
    """PATH entries sourced from any governed tree before sandbox startup."""
    unsafe: list[str] = []
    governed = (
        str(project_dir),
        *work_roots,
        *sandbox_implicit_writable_roots(environ),
    )
    for component in environ.get("PATH", "").split(os.pathsep):
        if not component or not os.path.isabs(component):
            unsafe.append(repr(component))
            continue
        lexical = os.path.abspath(component)
        real = os.path.realpath(lexical)
        if any(
            filesystem_path_is_within(candidate, root)
            for candidate in (lexical, real)
            for root in governed
        ):
            unsafe.append(lexical)
    return tuple(dict.fromkeys(unsafe))


def refuse_sandbox_glob_paths(paths: Sequence[os.PathLike[str] | str]) -> None:
    """Fail closed when Claude cannot encode a policy path as a literal."""
    control_offenders = [
        os.fspath(path)
        for path in paths
        if _contains_control_character(os.fspath(path))
    ]
    if control_offenders:
        raise SettingsError(
            "activated Claude sandbox cannot safely encode literal policy paths "
            "containing control characters: "
            + ", ".join(repr(path) for path in dict.fromkeys(control_offenders))
            + "; relocate the project, work root, Cartopian install/config, or "
            "runtime to paths without control characters"
        )
    offenders = [
        os.fspath(path)
        for path in paths
        if any(character in os.fspath(path) for character in _SANDBOX_GLOB_CHARACTERS)
    ]
    if offenders:
        raise SettingsError(
            "activated Claude sandbox cannot safely encode literal policy paths "
            "containing glob metacharacters (*, ?, [, ]): "
            + ", ".join(dict.fromkeys(offenders))
            + "; relocate the project, work root, Cartopian install/config, or "
            "runtime to paths without those characters"
        )


def refuse_work_root_transport_paths(
    paths: Sequence[os.PathLike[str] | str],
    *,
    windows: bool,
) -> None:
    """Reject roots the wrapper's path-list environment cannot encode."""
    separator = ";" if windows else ":"
    offenders = [
        os.fspath(path)
        for path in paths
        if separator in os.fspath(path)
        or _contains_control_character(os.fspath(path))
    ]
    if offenders:
        raise SettingsError(
            "declared work-root path contains a control character or the platform "
            f"path-list separator {separator!r} and cannot be transported "
            "losslessly to wrappers: "
            + ", ".join(repr(path) for path in dict.fromkeys(offenders))
            + "; relocate the work root to a path without that character"
        )


def _entry(handler: Mapping[str, Any], *, matcher: Optional[str] = None) -> dict:
    entry: dict[str, Any] = {
        "hooks": [dict(handler)],
    }
    if matcher is not None:
        entry["matcher"] = matcher
    return entry


def _read_claude_json(path: Path) -> Any:
    """Read one Claude JSON file with its BOM rule and a strict UTF-8 seam.

    Claude strips exactly one leading UTF-8 BOM and its JavaScript runtime
    replaces malformed UTF-8. Replacement can leave syntactically valid JSON
    with a security-sensitive environment value, so Cartopian fails closed on
    malformed bytes rather than silently modeling a different configuration.
    Invalid JSON remains inert, matching Claude's parse failure.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SettingsError(f"cannot read Claude configuration {path}: {exc}") from exc
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SettingsError(
            f"Claude configuration is not valid UTF-8 and cannot be modeled "
            f"safely: {path} ({exc})"
        ) from exc
    try:
        return json.loads(
            text,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant {value}")
            ),
        )
    except (json.JSONDecodeError, ValueError):
        return None


def _project_entries(
    project_dir: Optional[Path],
    event: str,
    environ: Mapping[str, str],
    *,
    windows: bool,
    isolated_sources: bool = False,
) -> list[tuple[Path, dict[str, Any]]]:
    """Return Cartopian entries for ``event`` from normally loaded settings.

    Invalid settings stay Claude's responsibility.  This helper only detects
    recognizable Cartopian entries and never copies unrelated settings into
    the command-line layer.
    """
    if project_dir is None or isolated_sources:
        return []
    script_name = "claude_hook.py" if event == "PreToolUse" else "claude_stop_hook.py"
    found: list[tuple[Path, dict[str, Any]]] = []
    for settings_path in _normal_settings_paths(
        project_dir, environ, windows=windows
    ):
        settings = _read_claude_json(settings_path)
        try:
            entries = settings.get("hooks", {}).get(event, [])
        except AttributeError:
            continue
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            handlers = entry.get("hooks", [])
            if not isinstance(handlers, list):
                continue
            if any(
                isinstance(handler, dict)
                and (
                    script_name in str(handler.get("command", ""))
                    or any(
                        script_name in str(argument)
                        for argument in handler.get("args", [])
                    )
                )
                for handler in handlers
            ):
                found.append((settings_path, entry))
    return found


def _compatible_entry(
    project_dir: Optional[Path],
    event: str,
    expected: dict[str, Any],
    environ: Mapping[str, str],
    *,
    windows: bool,
    isolated_sources: bool = False,
) -> dict[str, Any]:
    """Reuse an exact bound entry, or fail rather than execute twice."""
    existing = _project_entries(
        project_dir,
        event,
        environ,
        windows=windows,
        isolated_sources=isolated_sources,
    )
    if not existing:
        return expected
    if all(entry == expected for _path, entry in existing):
        return existing[0][1]
    locations = ", ".join(sorted({str(path) for path, _entry in existing}))
    raise LegacyHookError(
        f"incompatible legacy Cartopian {event} hook in {locations}; "
        "remove the old registration before dispatch (for project settings, "
        "run scripts/install.py --claude-hook PROJECT_DIR)"
    )


def _capability_context(
    project_dir: Path,
    *,
    environ: Optional[Mapping[str, str]] = None,
    windows: Optional[bool] = None,
):
    """Resolve the same project capability contract enforced by the hook."""
    # Keep Stop-only construction import-light: completion enforcement can be
    # used outside mediated dispatch, while capability resolution requires the
    # Python 3.11+ Cartopian runtime exported by dispatch.
    from cli.claude_hook import (
        _resolve_project_grants,
        _validate_config_file_identity,
    )

    # Canonical configuration resolution (and the live hook) read the global
    # config from ~/.cartopian, independent of where a development/copy layout
    # happens to place the executable files.
    config_root = _cartopian_home(
        os.environ if environ is None else environ,
        windows=windows,
    )
    context = _resolve_project_grants(project_dir, config_root)
    if context[0].activated:
        # The hook reads this registry on every structured call. A second name
        # in a writable tree would otherwise let a shell mutate the registry
        # between hook invocations without touching its protected pathname.
        _validate_config_file_identity(
            config_root / "projects.json", "project registry"
        )
    return context


def registered_project_roots(
    environ: Mapping[str, str],
) -> tuple[str, ...]:
    """Return registry project directories as shell-protected boundaries."""
    from cli.claude_hook import _load_registry_entries

    registry = _cartopian_home(environ, windows=False) / "projects.json"
    entries, error = _load_registry_entries(registry, os.path)
    if entries is None:
        raise SettingsError(
            f"cannot resolve registered project boundaries from {registry}: {error}"
        )
    return tuple(
        dict.fromkeys(os.path.realpath(entry["path"]) for entry in entries)
    )


def registered_foreign_work_roots(
    environ: Mapping[str, str],
    *,
    active_project: os.PathLike[str] | str,
    active_work_roots: Sequence[os.PathLike[str] | str],
) -> tuple[str, ...]:
    """Resolve foreign external roots that must not alias the active scope.

    An exact duplicate authored mapping is intentionally excluded: the bound
    project's claim wins that registry ambiguity. A differently spelled
    filesystem alias remains protected and is rejected by identity/mount
    validation rather than being mistaken for the same registry claim.
    """
    from cli.claude_hook import _load_registry_entries, _resolve_project_grants

    cartopian_home = _cartopian_home(environ, windows=False)
    registry = cartopian_home / "projects.json"
    entries, error = _load_registry_entries(registry, os.path)
    if entries is None:
        raise SettingsError(
            f"cannot resolve registered project boundaries from {registry}: {error}"
        )
    active_project_real = os.path.realpath(os.path.abspath(active_project))
    active_spellings = {
        os.path.abspath(os.fspath(path)) for path in active_work_roots
    }
    foreign: list[str] = []
    for entry in entries:
        project_root = os.path.realpath(entry["path"])
        if project_root == active_project_real:
            continue
        try:
            _resolution, work_roots = _resolve_project_grants(
                Path(project_root),
                cartopian_home,
                validate_project_aliases=False,
            )
        except Exception:
            # Bound-session ownership does not depend on unrelated project
            # health. Its registered project directory remains protected, but
            # an unresolvable foreign config contributes no external claim.
            continue
        for path in work_roots.values():
            lexical = os.path.abspath(path)
            if lexical in active_spellings or lexical in foreign:
                continue
            foreign.append(lexical)
    return tuple(foreign)


def validate_foreign_work_root_aliases(
    active_work_roots: Sequence[os.PathLike[str] | str],
    foreign_work_roots: Sequence[os.PathLike[str] | str],
) -> None:
    """Reject aliases whose physical and authored namespace relations differ."""
    active = tuple(os.path.abspath(path) for path in active_work_roots)
    foreign = tuple(os.path.abspath(path) for path in foreign_work_roots)
    for active_root in active:
        for foreign_root in foreign:
            lexical_active_in_foreign = _lexically_within(active_root, foreign_root)
            lexical_foreign_in_active = _lexically_within(foreign_root, active_root)
            physical_active_in_foreign = filesystem_path_is_within(
                active_root, foreign_root
            )
            physical_foreign_in_active = filesystem_path_is_within(
                foreign_root, active_root
            )
            if (
                lexical_active_in_foreign != physical_active_in_foreign
                or lexical_foreign_in_active != physical_foreign_in_active
            ):
                raise SettingsError(
                    "registered foreign work root is a differently spelled "
                    "filesystem alias of the active scope: "
                    f"{foreign_root} versus {active_root}"
                )


def capability_activated(project_dir: Path) -> bool:
    resolution, _work_roots = _capability_context(project_dir)
    return resolution.activated


def validate_precontainment_launch(
    install_root: Path,
    project_dir: Path,
    environ: Mapping[str, str],
    *,
    windows: bool,
    interpreter: Optional[Path] = None,
) -> None:
    """Validate executable discovery before any PATH-resolved helper runs.

    The wrapper invokes this through an absolute Python interpreter before it
    probes Claude's version. All declared work roots are untrusted executable
    sources even for a read-only role: product content can pre-exist the
    launch, so current write authority is not the relevant boundary.
    """
    resolution, work_roots = _capability_context(
        project_dir,
        environ=environ,
        windows=windows,
    )
    if not resolution.activated:
        return
    if windows:
        raise SettingsError(
            "activated Claude containment is refused on native Windows until "
            "Cartopian can attest both the shell sandbox and an exact native "
            "Claude executable chain"
        )
    # Claude's host-side Sandbox Runtime proxy creates a Unix-domain socket
    # from the process TMPDIR before a Bash tool is sandboxed. Bind that
    # unsandboxed host path to Cartopian's protected config tree and validate
    # it before even the Claude version probe starts.
    prepare_claude_host_tmpdir(
        environ,
        windows=False,
        require_binding=True,
    )
    governed_roots = (str(project_dir), *work_roots.values())
    unsafe_path = unsafe_precontainment_path_components(
        environ,
        project_dir,
        tuple(work_roots.values()),
    )
    if unsafe_path:
        raise SettingsError(
            "activated Claude launch refuses empty/relative PATH entries and "
            "entries inside the project, any declared work root, or any "
            "implicit shell-writable root before "
            "pre-containment helper execution: "
            + ", ".join(unsafe_path)
        )
    if not (environ.get(CLAUDE_EXECUTABLE_ENV) or "").strip():
        raise SettingsError(
            "activated Claude launch requires an absolute dispatch-bound "
            f"{CLAUDE_EXECUTABLE_ENV} before pre-containment execution"
        )
    claude_executable_protected_roots(
        project_dir,
        work_roots,
        environ,
        windows=windows,
    )
    runtime_conflicts: list[tuple[str, str]] = []
    for runtime_root in enforcement_protected_roots(
        install_root,
        environ,
        windows=windows,
        interpreter=interpreter,
    ):
        for governed_root in governed_roots:
            if filesystem_path_is_within(runtime_root, governed_root):
                runtime_conflicts.append((runtime_root, governed_root))
                break
    if runtime_conflicts:
        rendered = ", ".join(
            f"{runtime_root} (inside {governed_root})"
            for runtime_root, governed_root in runtime_conflicts
        )
        raise SettingsError(
            "activated Claude launch refuses its wrapper, Cartopian config, "
            "or Python runtime inside the project or a declared work root: "
            + rendered
        )


def _session_roles(environ: Mapping[str, str]) -> tuple[str, ...]:
    raw = environ.get(ROLE_ENV, "")
    roles = tuple(part.strip() for part in raw.split(",") if part.strip())
    return roles or ("pm",)


def _stop_max_blocks(environ: Mapping[str, str]) -> int:
    """Resolve Stop's operator ceiling once, before Claude settings mutate env."""
    raw = (environ.get(STOP_MAX_BLOCKS_ENV) or "").strip()
    if not raw:
        return DEFAULT_STOP_MAX_BLOCKS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_STOP_MAX_BLOCKS
    return value if value >= 0 else DEFAULT_STOP_MAX_BLOCKS


def _stop_state_directory(
    environ: Mapping[str, str], *, windows: Optional[bool] = None
) -> str:
    """Return a direct private state path below the protected config root."""
    config_root = Path(
        os.path.realpath(_cartopian_home(environ, windows=windows))
    )
    state_dir = config_root / "stop-state"
    if not os.path.lexists(state_dir):
        return str(state_dir)
    try:
        info = state_dir.lstat()
    except OSError as exc:
        raise SettingsError(
            f"cannot inspect protected Stop state directory {state_dir}: {exc}"
        ) from exc
    if not stat.S_ISDIR(info.st_mode) or state_dir.is_symlink():
        raise SettingsError(
            "activated Claude Stop state must be a direct directory below "
            f"Cartopian's protected config root: {state_dir}"
        )
    getuid = getattr(os, "getuid", None)
    if getuid is not None and info.st_uid != getuid():
        raise SettingsError(
            f"activated Claude Stop state is not owned by this user: {state_dir}"
        )
    if not windows and info.st_mode & 0o077:
        raise SettingsError(
            "activated Claude Stop state must not be accessible by group/other: "
            f"{state_dir}"
        )
    return str(state_dir)


def prepare_claude_host_tmpdir(
    environ: Mapping[str, str],
    *,
    windows: Optional[bool] = None,
    create: bool = False,
    allow_missing: bool = False,
    require_binding: bool = False,
) -> str:
    """Create/validate Claude's protected host-side SRT socket directory."""
    expected = claude_host_tmpdir_path(environ, windows=windows)
    bound = (environ.get(CLAUDE_HOST_TMPDIR_ENV) or "").strip()
    if require_binding and not bound:
        raise SettingsError(
            "activated Claude requires a dispatch-bound protected host temp "
            f"directory in {CLAUDE_HOST_TMPDIR_ENV}"
        )
    if bound and os.path.abspath(bound) != str(expected):
        raise SettingsError(
            f"{CLAUDE_HOST_TMPDIR_ENV} does not match the protected directory: "
            f"{bound!r} versus {expected}"
        )
    if bound and environ.get("TMPDIR") != bound:
        raise SettingsError(
            "activated Claude requires TMPDIR to equal its dispatch-bound "
            f"protected host temp directory ({bound})"
        )
    if create:
        try:
            expected.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise SettingsError(
                f"cannot create protected Claude host temp directory "
                f"{expected}: {exc}"
            ) from exc
    if not os.path.lexists(expected):
        if allow_missing:
            return str(expected)
        raise SettingsError(
            f"protected Claude host temp directory does not exist: {expected}"
        )
    try:
        info = expected.lstat()
    except OSError as exc:
        raise SettingsError(
            f"cannot inspect protected Claude host temp directory {expected}: {exc}"
        ) from exc
    if not stat.S_ISDIR(info.st_mode) or expected.is_symlink():
        raise SettingsError(
            "Claude host temp must be a direct directory below Cartopian's "
            f"protected config root: {expected}"
        )
    getuid = getattr(os, "getuid", None)
    if getuid is not None and info.st_uid != getuid():
        raise SettingsError(
            f"Claude host temp is not owned by this user: {expected}"
        )
    if not windows and stat.S_IMODE(info.st_mode) != 0o700:
        raise SettingsError(
            f"Claude host temp must have mode 0700: {expected}"
        )
    return str(expected)


def claude_host_tmpdir_path(
    environ: Mapping[str, str], *, windows: Optional[bool] = None
) -> Path:
    """Return the canonical protected host-temp path without changing disk."""
    config_root = Path(
        os.path.realpath(_cartopian_home(environ, windows=windows))
    )
    return config_root / "claude-host-tmp"


def _user_home(
    environ: Mapping[str, str], *, windows: Optional[bool] = None
) -> Path:
    """Return the platform-canonical user home for one launch environment."""
    if windows is None:
        windows = os.name == "nt"
    if windows:
        raw = environ.get("USERPROFILE") or environ.get("HOME")
        return Path(raw).expanduser() if raw else Path.home()

    if "HOME" in environ:
        raw = environ["HOME"]
        if not raw or not os.path.isabs(raw):
            raise SettingsError(
                "POSIX HOME must be an absolute literal path for Claude "
                f"containment binding (got {raw!r})"
            )
        return Path(raw)
    try:
        import pwd

        passwd_home = pwd.getpwuid(os.getuid()).pw_dir
    except (ImportError, KeyError, OSError) as exc:
        raise SettingsError(
            "cannot resolve the POSIX passwd home used by Claude os.homedir()"
        ) from exc
    if not passwd_home or not os.path.isabs(passwd_home):
        raise SettingsError(
            "POSIX passwd home is not an absolute path and cannot bind Claude "
            f"containment safely: {passwd_home!r}"
        )
    return Path(passwd_home)


def _cartopian_home(
    environ: Mapping[str, str], *, windows: Optional[bool] = None
) -> Path:
    """Return the config home visible at the trusted wrapper boundary."""
    return _user_home(environ, windows=windows) / ".cartopian"


def _claude_config_root(environ: Mapping[str, str]) -> Optional[Path]:
    """Return an exact custom Claude config root, rejecting ambiguous syntax.

    Claude treats ``CLAUDE_CONFIG_DIR`` literally; shell-like ``~`` expansion
    is not part of that contract. Security checks must bind the exact same path
    Claude loads, so require callers to supply an absolute value.
    """
    configured = environ.get("CLAUDE_CONFIG_DIR")
    if not configured:
        return None
    root = Path(configured)
    if not root.is_absolute():
        raise SettingsError(
            "CLAUDE_CONFIG_DIR must be an absolute path for Cartopian hook "
            f"source binding (got {configured!r})"
        )
    return root


def _normal_settings_paths(
    project_dir: Path,
    environ: Mapping[str, str],
    *,
    windows: Optional[bool] = None,
) -> tuple[Path, ...]:
    """Return operator-controlled settings files Claude normally merges.

    Claude has one user settings file and shared/local settings at the session's
    primary working directory. For linked Git worktrees it can additionally
    read the local settings file at the main checkout root, identified by
    ``--git-common-dir``. Non-Git projects use ``project_dir`` only.
    """
    if windows is None:
        windows = os.name == "nt"
    configured_root = _claude_config_root(environ)
    user_config = (
        configured_root
        if configured_root is not None
        else _user_home(environ, windows=windows) / ".claude"
    )

    project_root = project_dir.resolve()
    main_checkout_root = project_root
    repository_root: Optional[Path] = None
    local_stays_with_shared = windows
    if not windows:
        marker = _project_git_marker(project_dir)
        resolved_repository = _git_absolute_path(
            project_dir,
            "--show-toplevel",
            required=marker is not None,
            environ=environ,
        )
        if resolved_repository is not None:
            resolved_git_dir = _git_absolute_path(
                project_dir,
                "--git-dir",
                required=True,
                environ=environ,
            )
            resolved_common_dir = _git_absolute_path(
                project_dir,
                "--git-common-dir",
                required=True,
                environ=environ,
            )
            assert resolved_git_dir is not None and resolved_common_dir is not None
            repository_root = Path(resolved_repository)
            git_dir = Path(resolved_git_dir)
            common_dir = Path(resolved_common_dir)
            main_checkout_root = repository_root
            if common_dir.name == ".git" and git_dir != common_dir:
                main_checkout_root = common_dir.parent

        if repository_root is None:
            local_stays_with_shared = True
        else:
            home = _user_home(environ, windows=False)
            if repository_root == home.resolve():
                local_stays_with_shared = True
            getuid = getattr(os, "getuid", None)
            if getuid is not None:
                owner = getuid()
                ownership_paths = (
                    repository_root,
                    repository_root / ".git",
                    repository_root / ".claude",
                )
                for candidate in ownership_paths:
                    try:
                        if candidate.stat().st_uid != owner:
                            local_stays_with_shared = True
                            break
                    except FileNotFoundError:
                        continue
                    except OSError:
                        # If ownership cannot be established, follow Claude's
                        # conservative alongside-the-shared-file fallback.
                        local_stays_with_shared = True
                        break

    normal_local_root = project_root if local_stays_with_shared else main_checkout_root

    candidates = (
        user_config / "settings.json",
        project_root / ".claude" / "settings.json",
        # Legacy starting-directory local settings remain readable.
        project_root / ".claude" / "settings.local.json",
        normal_local_root / ".claude" / "settings.local.json",
    )
    # A normal checkout can make the shared and local parent identical; the
    # filenames remain distinct. Preserve scope order while de-duplicating any
    # unusual path aliases.
    return tuple(dict.fromkeys(path.resolve() for path in candidates))


def _legacy_global_config_path(
    project_dir: Path,
    environ: Mapping[str, str],
    *,
    windows: Optional[bool] = None,
) -> Path:
    """Return Claude's legacy global config, whose top-level env still loads."""
    configured_root = _claude_config_root(environ)
    if configured_root is not None:
        return (configured_root / ".claude.json").resolve()
    return (_user_home(environ, windows=windows) / ".claude.json").resolve()


def _hook_startup_paths(
    project_dir: Path,
    environ: Mapping[str, str],
    *,
    windows: Optional[bool] = None,
    isolated_sources: bool = False,
) -> tuple[Path, ...]:
    """Files that can configure settings or the command-hook environment."""
    legacy = _legacy_global_config_path(
        project_dir, environ, windows=windows
    )
    if isolated_sources:
        return (legacy,)
    normal = _normal_settings_paths(project_dir, environ, windows=windows)
    return tuple(dict.fromkeys((*normal, legacy)))


def _is_explicit_false(value: Any) -> bool:
    """Match Claude's explicit-false environment parsing."""
    if value is False:
        return True
    return isinstance(value, str) and value.strip().lower() in _EXPLICIT_FALSE_VALUES


def _effective_host_tool_environment(
    project_dir: Path,
    environ: Mapping[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Apply host env plus the always-loaded legacy global config.

    Activated launches pass an empty ``--setting-sources`` value, so user,
    project, and local ``settings.json`` env blocks are not loaded. Claude's
    legacy global ``.claude.json`` remains active independently of that flag.
    Invalid env value types are ignored by Claude and therefore must not erase
    a lower-precedence unsafe value in this model.
    """
    active = dict(environ)
    sources = {
        "PATH": "inherited environment",
        "USE_BUILTIN_RIPGREP": "inherited environment",
        "RIPGREP_CONFIG_PATH": "inherited environment",
    }
    legacy_path = _legacy_global_config_path(
        project_dir, environ, windows=False
    )
    for path in (legacy_path,):
        data = _read_claude_json(path)
        if data is None:
            continue
        settings_env = data.get("env") if isinstance(data, dict) else None
        if not isinstance(settings_env, dict):
            continue
        for key in ("PATH", "USE_BUILTIN_RIPGREP", "RIPGREP_CONFIG_PATH"):
            if key not in settings_env:
                continue
            value = settings_env[key]
            if isinstance(value, bool):
                active[key] = str(value).lower()
            elif isinstance(value, str):
                active[key] = value
            elif isinstance(value, int):
                active[key] = str(value)
            elif isinstance(value, float):
                # A valid JSON exponent can overflow both runtimes' Number /
                # float to infinity. Claude applies JavaScript String(), so
                # model those spellings instead of silently ignoring an
                # effective PATH or ripgrep override. JSON NaN/Infinity
                # literals are rejected above by parse_constant.
                if math.isinf(value):
                    active[key] = "Infinity" if value > 0 else "-Infinity"
                else:
                    # JavaScript String(-0.0) is "0", unlike Python's
                    # "-0.0". Exact formatting of other finite numbers is
                    # immaterial here: every non-zero value stays non-empty.
                    active[key] = "0" if value == 0 else format(value, ".15g")
            else:
                # Claude ignores null, arrays, and objects in an env block.
                continue
            sources[key] = str(path)
    return active, sources


def _refuse_external_host_tool_overrides(
    project_dir: Path,
    environ: Mapping[str, str],
    *,
    platform_name: Optional[str] = None,
) -> None:
    """Keep activated POSIX host helpers inside the attested process chain.

    Claude's Grep tool runs a PATH-resolved ``rg`` in the unsandboxed host
    process when ``USE_BUILTIN_RIPGREP`` is explicitly false on POSIX. Its
    Linux sandbox can additionally use a custom host-side ripgrep command and
    host PATH. Refuse those applicable modes; project/local settings are
    excluded from an activated launch and administrator-managed policy remains
    a trusted machine boundary.
    """
    platform_name = sys.platform if platform_name is None else platform_name
    linux = platform_name.startswith("linux")
    effective, sources = _effective_host_tool_environment(project_dir, environ)
    offenders: list[str] = []
    if _is_explicit_false(effective.get("USE_BUILTIN_RIPGREP")):
        offenders.append(
            sources["USE_BUILTIN_RIPGREP"] + " (USE_BUILTIN_RIPGREP=false)"
        )
    if effective.get("RIPGREP_CONFIG_PATH", "") != "":
        offenders.append(sources["RIPGREP_CONFIG_PATH"] + " (RIPGREP_CONFIG_PATH)")
    if linux and effective.get("PATH") != environ.get("PATH"):
        offenders.append(sources["PATH"] + " (env.PATH)")

    if offenders:
        raise SettingsError(
            "activated POSIX Claude sandbox refuses host-tool overrides that "
            "can execute outside the Bash sandbox: "
            + ", ".join(offenders)
            + "; use Claude's built-in ripgrep and the inherited host PATH"
        )


def _refuse_merged_sandbox_exclusions(
    project_dir: Path, environ: Mapping[str, str]
) -> None:
    """Compatibility seam for the now-isolated filesystem settings scopes.

    With an empty ``--setting-sources`` value, Claude does not merge sandbox
    or permission fields from user/project/local settings.  Its always-loaded
    legacy ``.claude.json`` contributes only ``env``; sandbox fields there are
    inert.  Managed policy is the trusted administrator boundary, so there is
    no untrusted settings-scope sandbox exception left to reject here.
    """
    return None


def _refuse_hook_startup_env(
    project_dir: Path,
    environ: Mapping[str, str],
    *,
    windows: Optional[bool] = None,
    isolated_sources: bool = False,
) -> None:
    """Reject settings env values that can suppress or preempt a hook."""
    inherited_unsafe = sorted(
        key
        for key in _INHERITED_HOOK_ENV_KEYS
        if (environ.get(key) or "").strip()
    )
    if (environ.get("CARTOPIAN_CLAUDE_BARE") or "").strip().lower() == "true":
        inherited_unsafe.append("CARTOPIAN_CLAUDE_BARE")
    if inherited_unsafe:
        raise SettingsError(
            "Cartopian Claude hooks refuse inherited launch modes that suppress "
            "or preempt process-scoped hooks: "
            + ", ".join(inherited_unsafe)
            + "; unset them before dispatch"
        )
    offenders: list[str] = []
    legacy_path = _legacy_global_config_path(
        project_dir,
        environ,
        windows=windows,
    )
    for path in _hook_startup_paths(
        project_dir,
        environ,
        windows=windows,
        isolated_sources=isolated_sources,
    ):
        data = _read_claude_json(path)
        if data is None:
            continue
        settings_env = data.get("env") if isinstance(data, dict) else None
        if not isinstance(settings_env, dict):
            settings_env = {}
        unsafe = sorted(
            key
            for key in settings_env
            if isinstance(key, str)
            and (
                key.upper() in _HOOK_ENV_KEYS
                or key.upper().startswith(_HOOK_ENV_PREFIXES)
            )
        )
        # Claude reads only ``env`` from legacy ~/.claude.json. A top-level
        # processWrapper there is inert; normal settings files can still supply
        # one on completion-only launches and must be refused.
        if path != legacy_path and isinstance(data, dict) and data.get("processWrapper"):
            unsafe.append("processWrapper")
        if unsafe:
            offenders.append(f"{path} ({', '.join(unsafe)})")
    if offenders:
        raise SettingsError(
            "Cartopian Claude hooks refuse Claude config env values that can "
            "suppress or alter hook startup: "
            + ", ".join(offenders)
            + "; remove those variables or use a separately isolated manual launch"
        )


def _refuse_unattested_wsl(environ: Mapping[str, str]) -> None:
    """Refuse WSL until its optional Unix-socket seccomp layer is attested."""
    wsl = bool(environ.get("WSL_DISTRO_NAME") or environ.get("WSL_INTEROP"))
    if not wsl:
        try:
            release = Path("/proc/sys/kernel/osrelease").read_text(
                encoding="utf-8"
            )
        except OSError:
            release = ""
        wsl = "microsoft" in release.lower()
    if wsl:
        raise SettingsError(
            "activated Claude shell containment refuses WSL2 until Cartopian "
            "can attest the optional sandbox-runtime seccomp filter that blocks "
            "Windows interop socket escapes; use native macOS/Linux or a "
            "separately isolated manual launch"
        )


def capability_sandbox(
    install_root: Path,
    project_dir: Path,
    resolution,
    work_roots: Mapping[str, str],
    *,
    environ: Mapping[str, str],
    interpreter: Optional[Path] = None,
) -> dict[str, Any]:
    """Return the OS-enforced shell write boundary for one dispatched role.

    Governance is deliberately structured-tool-only: no role writes inside
    the Cartopian project directory through a shell. External work roots are
    writable only with ``write:worktree``; otherwise ``denyWrite`` counteracts
    the wrapper's ``--add-dir`` visibility grant.
    """
    _refuse_unattested_wsl(environ)
    if (environ.get(CLAUDE_HOST_TMPDIR_ENV) or "").strip():
        # Real wrapper launches already required the directory during
        # ``validate_precontainment_launch``. ``allow_missing`` lets
        # read-only rehearsal/matrix callers model that exact binding without
        # creating operator state merely to inspect the policy.
        prepare_claude_host_tmpdir(
            environ,
            windows=False,
            allow_missing=True,
            require_binding=True,
        )
    _refuse_merged_sandbox_exclusions(project_dir, environ)
    _refuse_external_host_tool_overrides(project_dir, environ)
    project_root = os.path.realpath(project_dir)
    resolved_work_roots = sorted(
        {os.path.realpath(path) for path in work_roots.values()}
    )
    held = resolution.grants_for(_session_roles(environ))
    deny_write: list[str] = []
    host_tool_environ, _host_tool_sources = _effective_host_tool_environment(
        project_dir, environ
    )
    host_executable_roots = sandbox_host_executable_roots(host_tool_environ)
    claude_roots = (
        claude_executable_protected_roots(
            project_dir,
            work_roots,
            environ,
            windows=False,
        )
        if (environ.get(CLAUDE_EXECUTABLE_ENV) or "").strip()
        else ()
    )
    enforcement_roots = tuple(
        dict.fromkeys(
            (
                *enforcement_protected_roots(
                    install_root,
                    environ,
                    windows=False,
                    interpreter=interpreter,
                ),
                *host_executable_roots,
                *claude_roots,
            )
        )
    )
    git_roots = project_git_protected_roots(project_dir, environ)
    registry_roots = registered_project_roots(environ)
    foreign_work_roots = registered_foreign_work_roots(
        environ,
        active_project=project_dir,
        active_work_roots=tuple(work_roots.values()),
    )
    startup_paths = tuple(
        os.path.realpath(path)
        for path in _hook_startup_paths(
            project_dir,
            environ,
            isolated_sources=True,
        )
    )
    protected_policy_paths = tuple(
        dict.fromkeys(
            (
                project_root,
                *registry_roots,
                *enforcement_roots,
                *git_roots,
                *startup_paths,
            )
        )
    )
    implicit_roots = tuple(
        path
        for path in sandbox_implicit_writable_roots(environ)
        if os.path.lexists(path)
    )
    validate_foreign_work_root_aliases(
        tuple(work_roots.values()), foreign_work_roots
    )
    validate_implicit_writable_overlaps(protected_policy_paths, environ)
    validate_linux_mount_boundaries(
        (project_root, *resolved_work_roots, *implicit_roots),
        (*protected_policy_paths, *foreign_work_roots),
    )
    validate_shell_writable_hardlinks(
        tuple(work_roots.values()),
        environ,
        protected_roots=protected_policy_paths,
    )
    refuse_sandbox_glob_paths((*protected_policy_paths, *resolved_work_roots))
    for path in protected_policy_paths:
        if path not in deny_write:
            deny_write.append(path)
    allow_write: list[str] = []
    if "write:worktree" in held:
        nested: list[str] = []
        protected_roots = protected_policy_paths
        for path in resolved_work_roots:
            for protected_root in protected_roots:
                if filesystem_path_is_within(path, protected_root):
                    nested.append(f"{path} (inside protected {protected_root})")
                    break
                if path != protected_root and filesystem_path_is_within(
                    protected_root, path
                ):
                    nested.append(
                        f"protected {protected_root} (inside writable {path})"
                    )
                    break
            if nested and (
                nested[-1].startswith(f"{path} (")
                or nested[-1].endswith(f"(inside writable {path})")
            ):
                continue
            for writable_parent in (
                *resolved_work_roots,
                *sandbox_implicit_writable_roots(environ),
            ):
                canonical_parent = os.path.realpath(writable_parent)
                if path == canonical_parent:
                    continue
                if filesystem_path_is_within(path, canonical_parent):
                    nested.append(
                        f"{path} (inside shell-writable {canonical_parent})"
                    )
                    break
        if nested:
            # Claude's write rules are deny-over-allow. A nested allowWrite
            # therefore cannot reopen any part of the project-root denyWrite.
            # Refuse the launch instead of silently breaking write:worktree or
            # weakening the governance boundary for the whole project root.
            rendered = ", ".join(nested)
            raise SettingsError(
                "activated POSIX Claude sandbox cannot grant write:worktree "
                "to a work root whose directory entry is inside another "
                "shell-writable or protected enforcement/runtime root "
                f"({rendered}); keep writable product roots disjoint from "
                "every protected or implicit-writable root"
            )
        executable_conflicts = []
        for path in resolved_work_roots:
            for protected_root in host_executable_roots:
                if filesystem_path_is_within(protected_root, path):
                    executable_conflicts.append((protected_root, path))
                    break
        if executable_conflicts:
            rendered = ", ".join(
                f"{protected_root} (inside writable {path})"
                for protected_root, path in executable_conflicts
            )
            raise SettingsError(
                "activated POSIX Claude sandbox refuses a host executable/PATH "
                "search directory inside an authorized writable work root "
                f"({rendered}); keep Claude host helpers outside every work root"
            )
        allow_write.extend(resolved_work_roots)
    else:
        for path in resolved_work_roots:
            if path not in deny_write:
                deny_write.append(path)
    filesystem: dict[str, Any] = {
        # A higher-precedence process setting must not inherit a user setting
        # that disabled filesystem isolation.
        "disabled": False,
        "denyWrite": deny_write,
    }
    if allow_write:
        filesystem["allowWrite"] = allow_write
    return {
        "enabled": True,
        "failIfUnavailable": True,
        "allowUnsandboxedCommands": False,
        # Keep macOS Apple Events and command/path violation exceptions from
        # becoming process escapes in an unattended handoff. The empty
        # excluded-command list also records that Cartopian itself adds no
        # unsandboxed executable exception.
        "allowAppleEvents": False,
        "enableWeakerNestedSandbox": False,
        "excludedCommands": [],
        "ignoreViolations": {},
        "network": {
            "allowUnixSockets": [],
            "allowAllUnixSockets": False,
            "allowMachLookup": [],
        },
        "filesystem": filesystem,
    }


def build_settings(
    install_root: Path,
    *,
    windows: bool,
    project_dir: Optional[Path] = None,
    include_capability: bool = False,
    include_completion: bool = False,
    interpreter: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> dict:
    """Return the additional process-scoped Claude settings object."""
    if environ is None:
        environ = os.environ
    hooks: dict[str, list[dict[str, Any]]] = {}
    sandbox: Optional[dict[str, Any]] = None
    capability_active = False
    if include_capability:
        if project_dir is None:
            raise SettingsError("capability settings require --project-dir")
        resolution, work_roots = _capability_context(
            project_dir,
            environ=environ,
            windows=windows,
        )
        refuse_work_root_transport_paths(
            tuple(work_roots.values()), windows=windows
        )
        if resolution.activated:
            if windows:
                raise SettingsError(
                    "activated Claude containment is refused on native Windows "
                    "until Cartopian can attest both the shell sandbox and an "
                    "exact native Claude executable chain"
                )
            capability_active = True
            settings_paths = _hook_startup_paths(
                project_dir,
                environ,
                windows=windows,
                isolated_sources=True,
            )
            hook_arguments = [
                "--role=" + ",".join(_session_roles(environ)),
                "--project-root",
                os.path.realpath(project_dir),
                "--cartopian-home",
                os.path.realpath(_cartopian_home(environ, windows=windows)),
            ]
            for name, work_root in sorted(work_roots.items()):
                captured_path = os.path.realpath(work_root)
                try:
                    captured_info = os.lstat(captured_path)
                except OSError as exc:
                    raise SettingsError(
                        f"cannot capture work-root identity for {name!r}: {exc}"
                    ) from exc
                if not stat.S_ISDIR(captured_info.st_mode):
                    raise SettingsError(
                        f"work root {name!r} is not a direct directory at launch: "
                        f"{captured_path}"
                    )
                hook_arguments.extend(
                    (
                        "--work-root",
                        name,
                        captured_path,
                        str(captured_info.st_dev),
                        str(captured_info.st_ino),
                    )
                )
            protected_roots = (
                *enforcement_protected_roots(
                    install_root,
                    environ,
                    windows=windows,
                    interpreter=interpreter,
                ),
                *project_git_protected_roots(project_dir, environ),
            )
            if (environ.get(CLAUDE_EXECUTABLE_ENV) or "").strip():
                protected_roots = (
                    *protected_roots,
                    *claude_executable_protected_roots(
                        project_dir,
                        work_roots,
                        environ,
                        windows=windows,
                    ),
                )
            if not windows:
                host_tool_environ, _host_tool_sources = (
                    _effective_host_tool_environment(project_dir, environ)
                )
                protected_roots = (
                    *protected_roots,
                    *sandbox_host_executable_roots(host_tool_environ),
                )
            for protected_root in dict.fromkeys(protected_roots):
                hook_arguments.extend(("--protected-root", protected_root))
            for settings_path in settings_paths:
                hook_arguments.extend(("--settings-path", str(settings_path)))
            handler = hook_handler(
                install_root,
                "claude_hook.py",
                interpreter=interpreter,
                arguments=hook_arguments,
            )
            expected = _entry(handler, matcher=CAPABILITY_MATCHER)
            hooks["PreToolUse"] = [
                _compatible_entry(
                    project_dir,
                    "PreToolUse",
                    expected,
                    environ,
                    windows=windows,
                    isolated_sources=True,
                )
            ]
            if not windows:
                sandbox = capability_sandbox(
                    install_root,
                    project_dir,
                    resolution,
                    work_roots,
                    environ=environ,
                    interpreter=interpreter,
                )
    if include_completion:
        stop_arguments: list[str] = [
            "--max-blocks",
            str(_stop_max_blocks(environ)),
        ]
        if capability_active:
            stop_arguments.extend(
                (
                    "--state-dir",
                    _stop_state_directory(environ, windows=windows),
                )
            )
        expected_report = (environ.get(REPORT_ENV) or "").strip()
        if expected_report:
            stop_arguments.extend(("--expected-report", expected_report))
        expected_variant = (environ.get(VARIANT_ENV) or "").strip()
        if expected_variant:
            stop_arguments.extend(("--expected-variant", expected_variant))
        handler = hook_handler(
            install_root,
            "claude_stop_hook.py",
            interpreter=interpreter,
            arguments=stop_arguments,
        )
        expected = _entry(handler)
        hooks["Stop"] = [
            _compatible_entry(
                project_dir,
                "Stop",
                expected,
                environ,
                windows=windows,
                isolated_sources=capability_active,
            )
        ]
    settings: dict[str, Any] = {}
    if hooks:
        if project_dir is not None:
            _refuse_hook_startup_env(
                project_dir,
                environ,
                windows=windows,
                isolated_sources=capability_active,
            )
        settings["hooks"] = hooks
        # Normal user/project/local settings may disable every hook. The
        # process-scoped layer has higher precedence and explicitly restores
        # the two Cartopian entries it just installed.
        settings["disableAllHooks"] = False
    if capability_active:
        settings["permissions"] = {"deny": list(_UNCONTAINED_SESSION_TOOLS)}
        # ``settingSources: []`` does not disable Claude's always-loaded auto
        # memory. Without this override, Write/Edit can persist instructions
        # under ~/.claude/projects outside every Cartopian project boundary.
        settings["env"] = {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
        bound_host_tmp = (environ.get(CLAUDE_HOST_TMPDIR_ENV) or "").strip()
        if bound_host_tmp:
            settings["env"]["TMPDIR"] = bound_host_tmp
    if sandbox is not None:
        settings["sandbox"] = sandbox
    return settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--install-root", required=True, type=Path)
    parser.add_argument("--project-dir", type=Path)
    parser.add_argument("--capability", action="store_true")
    parser.add_argument("--completion", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--claude-version")
    parser.add_argument(
        "--platform",
        choices=("native", "posix", "windows"),
        default="native",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    windows = os.name == "nt" if args.platform == "native" else args.platform == "windows"
    project_dir = args.project_dir.resolve() if args.project_dir else None
    try:
        if args.capability and project_dir is not None:
            validate_precontainment_launch(
                args.install_root.resolve(),
                project_dir,
                os.environ,
                windows=windows,
                interpreter=Path(sys.executable),
            )
        settings = build_settings(
            args.install_root.resolve(),
            windows=windows,
            project_dir=project_dir,
            include_capability=args.capability,
            include_completion=args.completion,
        )
        if args.preflight_only:
            return 0
        if settings.get("hooks"):
            if not args.claude_version:
                raise SettingsError(
                    "wrapper supplied no Claude Code version for a hook-enabled launch"
                )
            require_claude_version(
                args.claude_version,
                activated="PreToolUse" in settings.get("hooks", {}),
            )
    except Exception as exc:
        sys.stderr.write(f"cartopian Claude settings error: {exc}\n")
        return 1
    sys.stdout.write(json.dumps(settings, separators=(",", ":")))
    return 0


if __name__ == "__main__":  # pragma: no cover - wrapper entry point
    raise SystemExit(main())
