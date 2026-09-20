"""Claude Code refusal adapter — capability-keyed PreToolUse read/write gating.

A Claude Code **PreToolUse hook** that denies raw file-mutation tool calls
(``Write``, ``Edit``, ``MultiEdit``, ``NotebookEdit``) *and* raw read tool
calls (``Read``, ``NotebookRead``, and the search tools ``Glob``/``Grep``)
against a registered Cartopian project's governed path-classes — and against
its declared work roots — when the active session lacks the corresponding
capability grant (see ``cli/capabilities.py`` and ``CAPABILITIES.md``).
Enforcement lives here, at the harness's native interception point. For a
mediated Claude handoff the wrapper adds this hook process-scoped after
resolving activation; grant decisions still live only here. This hook does not
parse ``Bash``/shell command text. On native macOS/Linux hosts the same
process-scoped settings enable Claude's OS sandbox: project-directory shell
writes are denied, and declared work roots are writable only for a role holding
``write:worktree``. Even with that grant, activated structured mutations of an
external work root are routed to sandboxed Bash because this separate hook
cannot atomically bind Claude's later macOS pathname open. Activated
native-Windows launches are refused until both
the shell sandbox and an exact native Claude executable chain can be attested.
Activated WSL2 launches are likewise refused pending attestation of the
optional interop-blocking seccomp layer. Unauthorized shell reads remain a
documented residual on every accepted platform.

Decision procedure (per target path; identical for both axes):

1. In an activated dispatched session, first bind ownership to that exact
   project. A target outside its project directory and declared work roots is
   denied, even when another registered project claims it; a duplicate work-
   root claim by another project cannot change the active project's grant
   decision. In an unbound legacy invocation, a target outside every
   registered project/work-root boundary is **allowed untouched** and equally
   specific duplicate claims fail closed as ambiguous.
2. Inside a project whose resolved config is *ungated* (no role
   declares a ``grants`` key) → **allow**.
3. Inside an *activated* project → resolve the session's role(s) to effective
   grants, classify the target, and **deny** unless the matching grant is
   held. The refusal is a single ``[guard]`` message naming the path, the
   path-class, and the missing grant. A held ``write:worktree`` grant admits
   sandboxed shell mutation, not a structured work-root mutation.
4. Fail-safe: an unreadable registry, or an unreadable/unresolvable config for
   the registered project that contains the target, never silently allows —
   it denies with a ``[guard]`` message explaining the resolution failure.
   Errors that belong to *other* projects (or an unparseable hook payload,
   which carries no usable target) never block anything.

Path classification (one spine; the required grant depends on the axis —
write for the mutation tools, read for the read tools):

- ``specs/``, ``phases/``, ``IMPLEMENTATION_PLAN.md``, ``REQUIREMENTS.md``,
  ``ROADMAP.md`` → ``write:plan`` / ``read:governance``
- ``tasks/``, ``STATE.md``, ``BACKLOG.md``, ``STANDARDS.md``,
- ``cartopian.toml`` / ``cartopian.local.toml`` → ``read:governance`` on the
  read axis; on the **write axis** a structured raw-edit tool is always denied
  regardless of grants. The mediated ``cartopian update-config`` command is the
  only edit path, but an activated native macOS/Linux Claude handoff cannot run
  that writer through its read-only project sandbox; it must report the exact
  requested operation for an operator/trusted host to execute outside the
  handoff. Activated native-Windows handoffs refuse preflight; advisory-tier
  hosts retain their shell residual.
- ``prompts/`` → ``write:lifecycle`` (the PM lifecycle surface) /
  ``read:prompts`` (the assignee's handoff)
- ``decisions/`` → ``write:decisions`` / ``read:governance``
- ``requests/`` → raw structured edits are denied; exact records are created
  only by the host intake boundary. Reads gate as ``read:governance``.
- ``reports/``, ``reviews/`` → ``write:reports`` / ``read:reports``
- a declared work root → ``write:worktree`` authorizes sandboxed Bash while
  activated structured mutation is refused / ``read:work-roots`` authorizes
  structured reads
- any other path inside the project directory → ``write:lifecycle`` /
  ``read:governance`` (unclassified project files fall to the PM surface
  rather than passing through an activated boundary ungated)

A ``Glob``/``Grep`` call without an explicit ``path`` searches the session
cwd, so it gates on the cwd; ``Read``-family calls without a usable path
carry no target and pass untouched (protocol failure, nothing attributable).

Session-role identification: ``cartopian dispatch`` exports environment to the
launched wrapper (the same mechanism that carries ``CARTOPIAN_TIMEOUT`` /
``CARTOPIAN_MODEL`` / ``CARTOPIAN_EFFORT``); it additionally exports
``CARTOPIAN_ROLE=<role>`` and the project root. The wrapper captures both,
along with the Cartopian config home and launch-time settings paths, as direct
hook arguments. Normal Claude settings may alter the hook subprocess
environment, but cannot replace those process-scoped bindings. A role value
may contain several comma-separated roles (grants union per
``GrantResolution.grants_for``). An interactive or legacy hook invocation with
no role marker resolves to the project's PM role (``pm``); in an activated
config that declares no grants for ``pm``, it fails closed like any undeclared
role. Enforcement keys on grants only, never on role names or descriptions.

Windows parity: pure ``posixpath``/``ntpath``-parameterized path logic (no
``fcntl``/``os.fork``/POSIX shell); on Windows, membership and classification
are case-insensitive and separator-agnostic (drive letters, backslashes, and
forward slashes all normalize). Live runs use ``os.path``, which is the
correct flavor per OS.

Activation: ``cartopian dispatch`` exports ``CARTOPIAN_ROLE`` and the Claude
wrapper resolves the same project configuration used below. If any role
declares grants, the wrapper supplies this entry through Claude's per-process
``--settings`` layer::

    {
      "hooks": {
        "PreToolUse": [
          {
            "matcher": "Read|NotebookRead|Glob|Grep|Write|Edit|MultiEdit|NotebookEdit",
            "hooks": [
              {
                "type": "command",
                "command": "/current/python",
                "args": [
                  "-I", "-S", "/installed/root/cli/claude_hook.py",
                  "--role=...", "--project-root", "..."
                ]
              }
            ]
          }
        ]
      }
    }

The hook is spawned directly with the current dispatch interpreter in isolated
mode and the installed hook path. No Claude settings file is written. Activated
launches exclude normal user, project, and local settings sources; completion-
only launches keep their normal settings-source behavior. Hook-enabled wrappers refuse
``CARTOPIAN_CLAUDE_BARE=true`` because current Claude releases suppress even
explicit process-settings hooks in bare mode. The legacy
``scripts/install.py --claude-hook <project-dir>`` spelling now removes older
Cartopian project registrations; it does not create one.

Hook I/O contract: the tool-call JSON arrives on stdin; a deny is emitted as
the documented PreToolUse structured output (``permissionDecision: "deny"``
with the ``[guard]`` reason) on stdout with exit 0; an allow produces no
output at all. Standard library only.
"""
import argparse
import functools
import json
import os
import stat
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

if __package__ in (None, ""):  # invoked as a script: `python .../cli/claude_hook.py`
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli.capabilities import GrantResolution  # noqa: E402
from cli.request_trace import REQUESTS_DIRNAME  # noqa: E402
from cli.config_schema import ConfigDiagnostic, resolve_configuration  # noqa: E402
from cli.commands.resolve_config import (  # noqa: E402
    _CliError,
    _load_toml,
)

# The file-mutation tools this hook gates on the write axis. Bash is
# deliberately absent: arbitrary command strings are not safely classifiable.
# The process-scoped Claude OS sandbox is the separate shell-write boundary.
FILE_MUTATION_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})

# The read tools gated on the read axis. FILE_READ_TOOLS carry an explicit
# target path; SEARCH_READ_TOOLS (directory/content search) may omit it, in
# which case they search — and therefore gate on — the session cwd.
FILE_READ_TOOLS = frozenset({"Read", "NotebookRead"})
SEARCH_READ_TOOLS = frozenset({"Glob", "Grep"})
READ_TOOLS = FILE_READ_TOOLS | SEARCH_READ_TOOLS

# Claude-managed delegation/worktree tools can change cwd, create Git
# worktrees, or launch children outside the captured project/work-root policy.
# Activated wrappers also remove them at the CLI surface; the hook denial is a
# second fail-closed boundary if a tool call nevertheless reaches PreToolUse.
UNCONTAINED_SESSION_TOOLS = frozenset(
    {
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
    }
)

# Target-path keys across all gated tools (mutation tools never send "path",
# search tools never send file_path/notebook_path, so one tuple serves both).
_PATH_KEYS = ("file_path", "notebook_path", "path")

ROLE_ENV = "CARTOPIAN_ROLE"
PROJECT_ROOT_ENV = "CARTOPIAN_LAUNCH_CWD"
DEFAULT_ROLE = "pm"  # interactive session with no role marker → the PM role

# Config files are never writable through a structured raw-edit tool, regardless
# of grants or activation state. `cartopian update-config` remains the only edit
# path; activated native macOS/Linux Claude handoffs must report that operation
# for execution outside their read-only project sandbox. This hook supplies the
# structured-tool deny; the POSIX Claude sandbox supplies the shell-write deny.
_RAW_CONFIG_BASENAMES = frozenset({"cartopian.toml", "cartopian.local.toml"})

# Governed path-class → required capability grant, per axis. The two axes
# share one classification spine; only the grant lookup differs.
WRITE_CLASS_GRANTS: Dict[str, str] = {
    "plan": "write:plan",
    "lifecycle": "write:lifecycle",
    "prompts": "write:lifecycle",  # prompts are the PM lifecycle surface to write
    "decisions": "write:decisions",
    "reports": "write:reports",
    "project-file": "write:lifecycle",
    "work-root": "write:worktree",
    # No grant unlocks a request-record write: the write axis denies the store
    # unconditionally before grants are consulted. The entry exists so the
    # class always resolves to a grant name for reporting.
    "request-trace": "write:lifecycle",
}

READ_CLASS_GRANTS: Dict[str, str] = {
    "plan": "read:governance",
    "lifecycle": "read:governance",
    "prompts": "read:prompts",  # ...but the assignee's handoff to read
    "decisions": "read:governance",
    "reports": "read:reports",
    "project-file": "read:governance",
    "work-root": "read:work-roots",
    "request-trace": "read:governance",
}

AXIS_GRANTS: Dict[str, Dict[str, str]] = {
    "write": WRITE_CLASS_GRANTS,
    "read": READ_CLASS_GRANTS,
}

# First path segment under the project root → class.
_DIR_CLASSES: Dict[str, str] = {
    "specs": "plan",
    "phases": "plan",
    "tasks": "lifecycle",
    "prompts": "prompts",
    "decisions": "decisions",
    "reports": "reports",
    "reviews": "reports",
    # The reviewer reads the immutable trace as governance; raw writes are
    # denied and the host-boundary command owns creation.
    REQUESTS_DIRNAME: "request-trace",
}

# Named project-root files → class (matched case-insensitively on Windows).
_ROOT_FILE_CLASSES: Dict[str, str] = {
    "IMPLEMENTATION_PLAN.md": "plan",
    "REQUIREMENTS.md": "plan",
    "ROADMAP.md": "plan",
    "STATE.md": "lifecycle",
    "BACKLOG.md": "lifecycle",
    "STANDARDS.md": "lifecycle",
    "cartopian.toml": "lifecycle",
    "cartopian.local.toml": "lifecycle",
}


@dataclass(frozen=True)
class Decision:
    """The hook's verdict for one tool call."""

    action: str  # "allow" | "deny"
    reason: Optional[str] = None


_ALLOW = Decision("allow")


# ---------------------------------------------------------------------------
# Pure, flavor-parameterized path logic. `flavor` is `posixpath` or `ntpath`
# (live runs pass `os.path`), so Windows semantics — case-insensitivity,
# backslash/forward-slash equivalence, drive letters — are unit-testable on
# POSIX.
# ---------------------------------------------------------------------------
def _norm(path: str, flavor) -> str:
    return flavor.normcase(flavor.normpath(path))


def _path_device(path: str) -> Optional[int]:
    """Device identity for a filesystem-semantics probe, or none on failure."""
    try:
        return os.stat(path).st_dev
    except OSError:
        return None


@functools.lru_cache(maxsize=256)
def _darwin_path_is_case_insensitive(path: str) -> bool:
    """Probe the existing filesystem without writing or assuming APFS mode."""
    probe = os.path.abspath(path)
    while not os.path.lexists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            return False
        probe = parent
    device = _path_device(probe)
    if device is None:
        return False
    while True:
        parent = os.path.dirname(probe)
        if parent == probe:
            return False
        # The mountpoint's name is resolved by its parent filesystem. Do not
        # infer the mounted volume's case behavior from an ancestor on another
        # device merely because all in-volume path components are nonletters.
        if _path_device(parent) != device:
            return False
        try:
            names = os.listdir(parent)
        except OSError:
            probe = parent
            continue
        actual = None
        for name in names:
            candidate = os.path.join(parent, name)
            try:
                if os.path.samefile(probe, candidate):
                    actual = name
                    break
            except OSError:
                continue
        if actual:
            for index, character in enumerate(actual):
                if not character.isalpha():
                    continue
                variant_character = character.swapcase()
                if variant_character == character:
                    continue
                variant = actual[:index] + variant_character + actual[index + 1 :]
                # On a case-sensitive volume this could be a distinct real
                # entry. Only an unlisted spelling that resolves to the same
                # inode proves case-insensitive lookup.
                if variant in names:
                    continue
                alternate = os.path.join(parent, variant)
                try:
                    return os.path.lexists(alternate) and os.path.samefile(
                        probe, alternate
                    )
                except OSError:
                    return False
        probe = parent


@functools.lru_cache(maxsize=256)
def _darwin_path_is_normalization_insensitive(path: str) -> bool:
    """Probe canonical-Unicode lookup independently of case sensitivity."""
    probe = os.path.abspath(path)
    while not os.path.lexists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            return False
        probe = parent
    device = _path_device(probe)
    if device is None:
        return False
    while True:
        parent = os.path.dirname(probe)
        if parent == probe:
            return False
        if _path_device(parent) != device:
            return False
        try:
            names = os.listdir(parent)
        except OSError:
            probe = parent
            continue
        actual = None
        for name in names:
            candidate = os.path.join(parent, name)
            try:
                if os.path.samefile(probe, candidate):
                    actual = name
                    break
            except OSError:
                continue
        if actual:
            variants = {
                unicodedata.normalize("NFC", actual),
                unicodedata.normalize("NFD", actual),
            }
            variants.discard(actual)
            for variant in variants:
                # A separately listed spelling may be a distinct real entry.
                # Only an unlisted alias resolving to this inode proves that
                # canonical-equivalent lookup is active on this volume.
                if variant in names:
                    continue
                alternate = os.path.join(parent, variant)
                try:
                    if os.path.lexists(alternate) and os.path.samefile(
                        probe, alternate
                    ):
                        return True
                except OSError:
                    continue
        probe = parent


def _live_path_is_case_insensitive(path: str, flavor) -> bool:
    return bool(
        sys.platform == "darwin"
        and flavor is os.path
        and _darwin_path_is_case_insensitive(path)
    )


def _live_path_is_normalization_insensitive(path: str, flavor) -> bool:
    return bool(
        sys.platform == "darwin"
        and flavor is os.path
        and _darwin_path_is_normalization_insensitive(path)
    )


def _governed_names_case_insensitive(left: str, right: str, flavor) -> bool:
    """Whether closed Cartopian path-class names should compare casefolded.

    At a macOS mount boundary the read-only probe can be inconclusive for a
    nonexistent child. Conservatively treating governed ASCII names as
    case-insensitive prevents a lower-privilege class from creating a
    case-variant alias; on a case-sensitive macOS volume this only over-denies.
    """
    return bool(
        (sys.platform == "darwin" and flavor is os.path)
        or _live_path_is_case_insensitive(left, flavor)
        or _live_path_is_case_insensitive(right, flavor)
    )


def _comparison_pair(left: str, right: str, flavor) -> Tuple[str, str]:
    normalized = [_norm(left, flavor), _norm(right, flavor)]
    if _live_path_is_normalization_insensitive(
        right, flavor
    ) or _live_path_is_normalization_insensitive(left, flavor):
        normalized = [unicodedata.normalize("NFC", item) for item in normalized]
    if _live_path_is_case_insensitive(right, flavor) or _live_path_is_case_insensitive(
        left, flavor
    ):
        normalized = [item.casefold() for item in normalized]
    return normalized[0], normalized[1]


def _filesystem_relative_path(target: str, root: str, flavor) -> Optional[str]:
    """Return target's suffix below an existing root using inode identity.

    On macOS, ``realpath`` preserves a caller's case/Unicode spelling. Walking
    existing ancestors and comparing identities therefore closes aliases that
    cross a mount boundary, where the mountpoint name is resolved by the
    parent filesystem but the mounted volume can have different case rules.
    """
    if flavor is not os.path:
        return None
    protected = os.path.abspath(root)
    if not os.path.lexists(protected):
        return None
    probe = os.path.abspath(target)
    suffix: list[str] = []
    while True:
        if os.path.lexists(probe):
            try:
                if os.path.samefile(probe, protected):
                    return flavor.join(*reversed(suffix)) if suffix else "."
            except OSError:
                pass
        parent = os.path.dirname(probe)
        if parent == probe:
            return None
        suffix.append(os.path.basename(probe))
        probe = parent


def _paths_equal(left: str, right: str, flavor) -> bool:
    if flavor is os.path:
        try:
            if os.path.samefile(left, right):
                return True
        except OSError:
            pass
    normalized_left, normalized_right = _comparison_pair(left, right, flavor)
    return normalized_left == normalized_right


def _path_key(path: str, flavor) -> str:
    normalized = _norm(path, flavor)
    if _live_path_is_normalization_insensitive(path, flavor):
        normalized = unicodedata.normalize("NFC", normalized)
    if _live_path_is_case_insensitive(path, flavor):
        normalized = normalized.casefold()
    return normalized


def _path_identity_key(path: str, flavor) -> object:
    if flavor is os.path:
        try:
            stat_result = os.stat(path)
            return ("inode", stat_result.st_dev, stat_result.st_ino)
        except OSError:
            pass
    return ("path", _path_key(path, flavor))


def _match_specificity(target: str, root: str, flavor) -> int:
    """Rank a containing root by distance to ``target``, alias-independently."""
    relative = _filesystem_relative_path(target, root, flavor)
    if relative is None:
        normalized_target, normalized_root = _comparison_pair(
            target, root, flavor
        )
        relative = flavor.relpath(normalized_target, normalized_root)
    suffix_depth = len(
        [part for part in relative.split(flavor.sep) if part not in ("", ".")]
    )
    # A physically deeper root leaves fewer suffix components. Unlike lexical
    # root depth, this is stable across macOS firmlink spellings such as
    # /Users/... and /System/Volumes/Data/Users/....
    return -suffix_depth


def _is_within(target: str, root: str, flavor) -> bool:
    """True iff ``target`` is ``root`` or lies under it (normalized compare)."""
    if _filesystem_relative_path(target, root, flavor) is not None:
        return True
    t, r = _comparison_pair(target, root, flavor)
    if t == r:
        return True
    if not r.endswith(flavor.sep):
        r += flavor.sep
    return t.startswith(r)


def _glob_first_metachar_index(pattern: str) -> Optional[int]:
    """Return Claude's first Glob metacharacter offset, if any.

    Claude derives an absolute Glob's search directory from the literal prefix
    before the first ``*``, ``?``, ``[``, or ``{``. Keep that exact set here:
    using a normal path dirname on the whole pattern can misclassify a search
    whose metacharacter appears in an earlier directory component.
    """
    offsets = [
        offset
        for marker in "*?[{"
        if (offset := pattern.find(marker)) >= 0
    ]
    return min(offsets) if offsets else None


def _glob_pattern_is_absolute(pattern: str, flavor) -> bool:
    """Match Node's absolute-path treatment used by Claude's Glob tool."""
    if flavor.isabs(pattern):
        return True
    # Python 3.13's ntpath no longer calls ``\foo`` drive-absolute, while
    # Node path.win32.isAbsolute (and Claude) still does. Its eventual resolve
    # uses the cwd drive, which ntpath.join below also preserves.
    if flavor.sep == "\\":
        _drive, tail = flavor.splitdrive(pattern)
        return tail.startswith(("\\", "/"))
    return False


def _node_path_dirname(path: str, flavor) -> str:
    """Model Node path.dirname's trailing-separator behavior."""
    separators = "/\\" if flavor.sep == "\\" else "/"
    trimmed = path.rstrip(separators)
    if not trimmed:
        return flavor.sep
    drive, tail = flavor.splitdrive(trimmed)
    if drive and not tail and _glob_pattern_is_absolute(path, flavor):
        return drive + flavor.sep
    return flavor.dirname(trimmed)


def _filesystem_anchor(path: str, flavor) -> str:
    """Return the filesystem/drive root governing ``path``."""
    drive, _tail = flavor.splitdrive(path)
    return drive + flavor.sep if drive else flavor.sep


def _absolute_glob_search_root(pattern: str, flavor) -> Optional[str]:
    """Return the conservative authorization root for an absolute Glob.

    The initial base mirrors Claude: an exact pattern uses its dirname; a
    metacharacter pattern uses the directory before the first metacharacter.
    A parent traversal in the glob suffix can escape that base after brace or
    wildcard expansion (for example ``prompts/{foo,../reports}/**``), so the
    authorization root is widened by one ancestor for every possible literal
    ``..`` occurrence in the suffix. Counting all occurrences can over-widen
    alternatives, but never authorizes a narrower surface than the tool may
    read.
    """
    if not _glob_pattern_is_absolute(pattern, flavor):
        return None
    metachar = _glob_first_metachar_index(pattern)
    if metachar is None:
        return _node_path_dirname(pattern, flavor)

    prefix = pattern[:metachar]
    boundary = max(prefix.rfind("/"), prefix.rfind(flavor.sep))
    if boundary < 0:
        base = ""
    else:
        base = prefix[:boundary]
        if not base and boundary == 0:
            base = "/"
    # Claude repairs a bare Windows drive extracted before a wildcard.
    if len(base) == 2 and base[0].isalpha() and base[1] == ":":
        base += flavor.sep

    # A parent component after the first metacharacter may be hidden inside a
    # brace alternative, and the preceding expansion could select a symlink.
    # No narrower static pathname is sound, so bind authorization at the
    # filesystem/drive anchor and let dispatched-scope containment refuse it.
    if ".." in pattern[metachar:]:
        return _filesystem_anchor(base, flavor)
    return base


def classify_project_path(
    target: str, project_root: str, flavor, axis: str = "write"
) -> Tuple[str, str]:
    """Classify a path *inside* the project directory → (class, grant).

    ``axis`` selects which grant the class requires (``"write"`` for the
    mutation tools, ``"read"`` for the read tools); classification itself is
    axis-independent.
    """
    grants = AXIS_GRANTS[axis]
    rel = _filesystem_relative_path(target, project_root, flavor)
    if rel is None:
        normalized_target, normalized_root = _comparison_pair(
            target, project_root, flavor
        )
        rel = flavor.relpath(normalized_target, normalized_root)
    segments = [s for s in rel.split(flavor.sep) if s not in ("", ".")]
    normalization_insensitive = _live_path_is_normalization_insensitive(
        target, flavor
    ) or _live_path_is_normalization_insensitive(project_root, flavor)
    case_insensitive = _governed_names_case_insensitive(
        target, project_root, flavor
    )
    if normalization_insensitive:
        segments = [unicodedata.normalize("NFC", segment) for segment in segments]
    if case_insensitive:
        segments = [segment.casefold() for segment in segments]
    if not segments or segments[0] == "..":
        # Caller guarantees membership; treat degenerate input as the broadest
        # governed surface rather than passing it through.
        return "project-file", grants["project-file"]
    if len(segments) == 1:
        # A single segment may be a governed root file, or — for the search
        # tools, which target directories — a governed directory itself.
        name = segments[0]
        for known, klass in _ROOT_FILE_CLASSES.items():
            known_name = (
                known.casefold()
                if case_insensitive
                else flavor.normcase(known)
            )
            if known_name == name:
                return klass, grants[klass]
    klass = _DIR_CLASSES.get(segments[0])
    if klass is None:
        return "project-file", grants["project-file"]
    return klass, grants[klass]


# ---------------------------------------------------------------------------
# Registry and config resolution.
# ---------------------------------------------------------------------------
def _load_registry_entries(
    registry_file: Path, flavor
) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """Lenient registry read → (entries, error).

    Missing or empty file → ``([], None)`` (nothing registered, nothing to
    gate). An unreadable/corrupt file → ``(None, reason)``: boundaries cannot
    be established, so the caller fails closed. Individual malformed entries
    are skipped — a broken entry defines no boundary and must never block
    unrelated paths.
    """
    if not os.path.lexists(registry_file):
        return [], None
    if registry_file.is_symlink():
        return None, f"{registry_file} must be a direct file, not a symbolic link"
    try:
        registry_stat = registry_file.stat()
        if stat.S_ISREG(registry_stat.st_mode) and registry_stat.st_nlink > 1:
            return None, (
                f"{registry_file} has multiple hard-link names and cannot be "
                "protected as the authoritative registry"
            )
        raw = registry_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return None, f"cannot read {registry_file}: {exc}"
    if raw.strip() == "":
        return [], None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"{registry_file} is not valid JSON: {exc}"
    if not isinstance(data, list):
        return None, f"{registry_file} top-level is not a JSON array"
    entries: List[Dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            continue
        registered_path = entry["path"]
        if "\x00" in registered_path or not flavor.isabs(registered_path):
            continue
        if flavor is os.path:
            try:
                os.fsencode(registered_path)
            except (UnicodeError, ValueError):
                # Syntactically valid JSON can still carry an unpaired
                # surrogate or NUL that live path APIs cannot resolve. Such an
                # entry defines no usable boundary and must not crash the hook.
                continue
        entries.append(entry)
    return entries, None


def _session_roles(environ: Mapping[str, str]) -> Tuple[str, ...]:
    raw = environ.get(ROLE_ENV, "")
    roles = tuple(part.strip() for part in raw.split(",") if part.strip())
    return roles or (DEFAULT_ROLE,)


def _validate_config_file_identity(path: Path, label: str) -> None:
    """Refuse aliases that can mutate a config through another path class."""
    if not os.path.lexists(path):
        return
    if path.is_symlink():
        raise _CliError(
            1,
            "guard",
            f"{label} must be a direct file, not a symbolic link: {path}",
        )
    try:
        info = path.stat()
    except OSError as exc:
        raise _CliError(1, "guard", f"cannot inspect {label} {path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise _CliError(1, "guard", f"{label} must be a regular file: {path}")
    if info.st_nlink > 1:
        raise _CliError(
            1,
            "guard",
            f"{label} has multiple hard-link names and cannot be protected by "
            f"path-class policy: {path}",
        )


def _validate_activated_project_aliases(project_root: Path) -> None:
    """Reject project aliases that escape path-based hook/sandbox policy."""
    walk_errors: list[OSError] = []

    def record_walk_error(exc: OSError) -> None:
        walk_errors.append(exc)

    for directory, subdirs, filenames in os.walk(
        project_root, followlinks=False, onerror=record_walk_error
    ):
        for name in (*subdirs, *filenames):
            candidate = Path(directory) / name
            if candidate.is_symlink():
                resolved = (
                    os.path.realpath(candidate)
                    if candidate.exists()
                    else "<dangling>"
                )
                raise _CliError(
                    1,
                    "guard",
                    "activated project contains a symbolic link whose alternate "
                    "name can cross path-class grants; use a direct file or "
                    f"directory instead ({candidate} -> {resolved})",
                )
            try:
                info = candidate.stat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise _CliError(
                    1, "guard", f"cannot inspect activated project path {candidate}: {exc}"
                ) from exc
            if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
                raise _CliError(
                    1,
                    "guard",
                    "activated project contains a multiply-linked file whose "
                    f"other name could bypass path policy: {candidate}",
                )
    if walk_errors:
        raise _CliError(
            1,
            "guard",
            f"cannot inspect activated project tree {project_root}: {walk_errors[0]}",
        )


def _resolve_project_grants(
    project_root: Path,
    cartopian_home: Path,
    *,
    validate_project_aliases: bool = True,
) -> Tuple[GrantResolution, Dict[str, str]]:
    """Resolve canonical grants and work roots for one registered project.

    Raises on any resolution failure (missing/unreadable config); the caller
    decides whether that fails closed (target inside this project) or is
    skipped (target elsewhere).
    """
    project_toml = project_root / "cartopian.toml"
    if not project_toml.exists():
        raise _CliError(1, "guard", f"project config not found: {project_toml}")
    global_toml = cartopian_home / "cartopian.toml"
    local_toml = project_root / "cartopian.local.toml"
    _validate_config_file_identity(project_toml, "project config")
    for path, label in (
        (global_toml, "global config"),
        (local_toml, "local config"),
    ):
        if os.path.lexists(path) and not path.exists():
            raise _CliError(
                1,
                "guard",
                f"{label} path is a dangling or unresolvable symlink: {path}",
            )
        _validate_config_file_identity(path, label)
    project_cfg = _load_toml(project_toml, "project config") or {}
    global_cfg = _load_toml(global_toml, "global config") or {}
    local_cfg = _load_toml(
        local_toml, "local config"
    ) or {}
    try:
        resolved = resolve_configuration(global_cfg, project_cfg, local_cfg)
    except ConfigDiagnostic as exc:
        raise _CliError(1, "guard", str(exc)) from exc
    for name, work_root in resolved["work_roots"].items():
        try:
            if "\x00" in work_root:
                raise ValueError("embedded NUL")
            os.fsencode(work_root)
            os.path.realpath(work_root)
        except (OSError, UnicodeError, ValueError) as exc:
            raise _CliError(
                1,
                "guard",
                f"work root {name!r} is not representable as a live filesystem "
                f"path: {work_root!r} ({exc})",
            ) from exc
    if resolved["capabilities"]["activated"] and validate_project_aliases:
        _validate_activated_project_aliases(project_root)
    grants = GrantResolution(
        activated=resolved["capabilities"]["activated"],
        role_grants={
            name: frozenset(role["effective_grants"])
            for name, role in resolved["roles"].items()
        },
    )
    return grants, resolved["work_roots"]


def _deny_missing_grant(
    tool_name: str,
    target: str,
    project_id: str,
    klass: str,
    grant: str,
    roles: Sequence[str],
) -> Decision:
    role_list = ", ".join(roles)
    return Decision(
        "deny",
        f"[guard] {tool_name} denied: {target} — path-class '{klass}' in "
        f"Cartopian project '{project_id}' requires capability grant "
        f"'{grant}', which session role(s) [{role_list}] do not hold. "
        f"Use the project's mediated tooling, or have the operator grant "
        f"'{grant}' in cartopian.toml.",
    )


def _deny_raw_config_write(tool_name: str, target: str, project_id: str) -> Decision:
    return Decision(
        "deny",
        f"[guard] {tool_name} denied: {target} — raw edits to Cartopian config "
        f"files in project '{project_id}' are not permitted through structured "
        f"edit tools. The operator or another trusted host must run the mediated "
        f"`cartopian update-config` command outside an activated Claude handoff; "
        f"report the exact requested operation. The command validates and "
        f"atomically writes the change.",
    )


def _deny_raw_request_write(tool_name: str, target: str, project_id: str) -> Decision:
    return Decision(
        "deny",
        f"[guard] {tool_name} denied: {target} — original-request records in "
        f"project '{project_id}' are immutable host-intake evidence. No PM, "
        f"coder, or reviewer grant can write them.",
    )


def _deny_claude_settings_write(tool_name: str, target: str) -> Decision:
    return Decision(
        "deny",
        f"[guard] {tool_name} denied: {target} is an active Claude settings "
        "file. A dispatched session cannot mutate its own enforcement policy; "
        "the operator may change that file outside the handoff.",
    )


def _deny_enforcement_write(tool_name: str, target: str) -> Decision:
    return Decision(
        "deny",
        f"[guard] {tool_name} denied: {target} is inside Cartopian's active "
        "enforcement/configuration installation. A dispatched session cannot "
        "mutate the guard that authorizes it.",
    )


def _is_active_claude_settings(
    target: str,
    project_root: str,
    environ: Mapping[str, str],
    flavor,
    resolve,
    protected_settings_paths: Optional[Sequence[str]] = None,
) -> bool:
    """Whether ``target`` is a user/project settings file loaded by Claude."""
    if protected_settings_paths is not None:
        candidates = list(protected_settings_paths)
    else:
        # Compatibility fallback for manual/legacy invocation. Mediated
        # dispatch passes the exact normally loaded paths as immutable argv,
        # including linked-worktree resolution.
        candidates = [
            flavor.join(project_root, ".claude", "settings.json"),
            flavor.join(project_root, ".claude", "settings.local.json"),
        ]
        config_dir = environ.get("CLAUDE_CONFIG_DIR")
        if not config_dir:
            home = environ.get("HOME") or environ.get("USERPROFILE")
            if home:
                config_dir = flavor.join(home, ".claude")
        if config_dir:
            if not flavor.isabs(config_dir):
                config_dir = flavor.join(project_root, config_dir)
            candidates.append(flavor.join(config_dir, "settings.json"))
    return any(
        _paths_equal(resolve(target), resolve(candidate), flavor)
        for candidate in candidates
    )


def _deny_resolution_failure(
    tool_name: str, target: str, project_id: str, detail: str
) -> Decision:
    return Decision(
        "deny",
        f"[guard] {tool_name} denied: {target} is inside registered Cartopian "
        f"project '{project_id}' but capability resolution failed "
        f"({detail}) — failing closed rather than silently allowing.",
    )


def _deny_bound_work_root_resolution_failure(
    tool_name: str, target: str, project_id: str, detail: str
) -> Decision:
    return Decision(
        "deny",
        f"[guard] {tool_name} denied: {target} may be governed by a declared "
        f"work root of the dispatched Cartopian project '{project_id}', but "
        f"its capability contract could not be resolved ({detail}) — failing "
        "closed rather than losing the active project's external boundary.",
    )


def _deny_cross_project_access(
    tool_name: str,
    target: str,
    active_project_id: str,
    claimed_project_ids: Sequence[str],
) -> Decision:
    claimants = ", ".join(sorted(set(claimed_project_ids)))
    return Decision(
        "deny",
        f"[guard] {tool_name} denied: {target} is governed by Cartopian "
        f"project(s) [{claimants}], not the dispatched project "
        f"'{active_project_id}'. Capability grants never cross project "
        f"boundaries.",
    )


def _deny_outside_dispatched_scope(
    tool_name: str, target: str, active_project_id: str
) -> Decision:
    return Decision(
        "deny",
        f"[guard] {tool_name} denied: {target} is outside dispatched Cartopian "
        f"project '{active_project_id}' and all of its declared work roots. "
        "Activated session grants do not authorize arbitrary external paths.",
    )


def _deny_bound_work_root_structured_write(
    tool_name: str, target: str, project_id: str, work_root_name: str
) -> Decision:
    return Decision(
        "deny",
        f"[guard] {tool_name} denied: {target} is in work-root:{work_root_name} "
        f"for dispatched Cartopian project '{project_id}'. The role holds "
        "write:worktree, but a separate PreToolUse process cannot atomically "
        "bind a mutable work-root pathname to Claude's later structured open. "
        "Use Bash for this work-root mutation; the activated OS sandbox "
        "enforces the captured shell-write boundary at the actual filesystem "
        "operation.",
    )


def _deny_ambiguous_work_root(
    tool_name: str,
    target: str,
    claimed_project_ids: Sequence[str],
) -> Decision:
    claimants = ", ".join(sorted(set(claimed_project_ids)))
    return Decision(
        "deny",
        f"[guard] {tool_name} denied: {target} matches equally specific work "
        f"roots in multiple Cartopian projects [{claimants}], and this "
        f"session has no dispatched-project binding to resolve ownership. "
        f"Launch through `cartopian dispatch` or remove the ambiguous mapping.",
    )


def _session_project_binding(
    entries: Sequence[Dict[str, Any]],
    environ: Mapping[str, str],
    flavor,
    resolve,
) -> Optional[Tuple[Dict[str, Any], str]]:
    """Return the project identity carried by a mediated dispatch.

    ``dispatch`` exports both the role and its absolute launch root.  The root
    is the project identity for this process; unlike registry order it cannot
    change when another project happens to claim the same external work root.
    Registration is deliberately not required for process-scoped containment,
    so an unregistered launch root becomes a synthetic entry.
    """
    raw_root = environ.get(PROJECT_ROOT_ENV)
    if not environ.get(ROLE_ENV) or not raw_root or not flavor.isabs(raw_root):
        return None
    project_root = resolve(raw_root)
    for entry in entries:
        registered_root = resolve(entry["path"])
        if _paths_equal(registered_root, project_root, flavor):
            return entry, registered_root
    project_id = flavor.basename(_norm(project_root, flavor)) or project_root
    return {"id": project_id, "path": project_root}, project_root


def _gate_inside_project(
    tool_name: str,
    target: str,
    entry: Dict[str, Any],
    project_root: str,
    environ: Mapping[str, str],
    cartopian_home: Path,
    flavor,
    resolve,
    axis: str,
    session_project: Optional[Tuple[Dict[str, Any], str]] = None,
    bound_work_roots: Optional[Mapping[str, str]] = None,
    search_descendants: bool = False,
) -> Decision:
    """Gate a target that lies inside a registered project's directory."""
    project_id = entry.get("id") or project_root

    # Config files are never writable through a structured raw-edit tool — the
    # mediated `cartopian update-config` command is the only edit path; activated
    # native macOS/Linux handoffs must have it executed outside their sandbox.
    # This runs before grant resolution so it also holds in ungated projects and
    # when the config cannot be resolved (fail-closed for config writes).
    if axis == "write":
        normalized_target, normalized_root = _comparison_pair(
            target, project_root, flavor
        )
        basename = flavor.basename(normalized_target)
        case_insensitive = _governed_names_case_insensitive(
            target, project_root, flavor
        )
        if case_insensitive:
            basename = basename.casefold()
        raw_config_names = {
            name.casefold()
            if case_insensitive
            else flavor.normcase(name)
            for name in _RAW_CONFIG_BASENAMES
        }
        if basename in raw_config_names:
            return _deny_raw_config_write(tool_name, target, project_id)
        # Request records are never writable through a structured raw-edit tool.
        rel = _filesystem_relative_path(target, project_root, flavor)
        if rel is None:
            rel = flavor.relpath(normalized_target, normalized_root)
        head = [s for s in rel.split(flavor.sep) if s not in ("", ".")]
        normalization_insensitive = _live_path_is_normalization_insensitive(
            target, flavor
        ) or _live_path_is_normalization_insensitive(project_root, flavor)
        if normalization_insensitive:
            head = [unicodedata.normalize("NFC", segment) for segment in head]
        if case_insensitive:
            head = [segment.casefold() for segment in head]
        request_name = (
            REQUESTS_DIRNAME.casefold()
            if case_insensitive
            else flavor.normcase(REQUESTS_DIRNAME)
        )
        if head and head[0] == request_name:
            return _deny_raw_request_write(tool_name, target, project_id)

    try:
        resolution, work_roots = _resolve_project_grants(
            Path(project_root), cartopian_home
        )
    except Exception as exc:
        return _deny_resolution_failure(tool_name, target, project_id, str(exc))

    if not resolution.activated:
        return _ALLOW

    # Work roots may be declared inside the project directory; they gate as
    # worktree, taking precedence over directory classification. In an
    # activated config an unresolvable work-root mapping means the target
    # cannot be classified safely → fail closed.
    klass = grant = None
    classification_work_roots: Mapping[str, str] = work_roots
    if (
        bound_work_roots is not None
        and session_project is not None
        and _paths_equal(project_root, session_project[1], flavor)
    ):
        classification_work_roots = bound_work_roots
    resolved_classification_work_roots: List[Tuple[str, str]] = []
    for name, wr_path in classification_work_roots.items():
        work_root = (
            wr_path
            if bound_work_roots is classification_work_roots
            else resolve(wr_path)
        )
        resolved_classification_work_roots.append((name, work_root))
        if (
            _is_within(target, work_root, flavor)
            and _is_within(work_root, project_root, flavor)
            and not _paths_equal(work_root, project_root, flavor)
        ):
            klass, grant = f"work-root:{name}", AXIS_GRANTS[axis]["work-root"]
            break
    if klass is None:
        klass, grant = classify_project_path(target, project_root, flavor, axis)

    roles = _session_roles(environ)
    requirements = [(klass, grant)]
    if axis == "read" and search_descendants:
        target_is_inside_work_root = klass.startswith("work-root:")
        if not target_is_inside_work_root and _paths_equal(
            target, project_root, flavor
        ):
            # A project-root Glob/Grep can reach every governed directory and
            # root-file class, not merely the root's project-file fallback.
            for reachable_class in (
                "project-file",
                *_DIR_CLASSES.values(),
                *_ROOT_FILE_CLASSES.values(),
            ):
                requirements.append(
                    (reachable_class, AXIS_GRANTS[axis][reachable_class])
                )
        if not target_is_inside_work_root:
            # A declared work root may itself live below the project search
            # directory. Work-root precedence then changes the grant for that
            # subtree, so a broad search must hold both surface grants.
            for name, work_root in resolved_classification_work_roots:
                if _is_within(work_root, target, flavor) and not _paths_equal(
                    work_root, project_root, flavor
                ):
                    requirements.append(
                        (f"work-root:{name}", AXIS_GRANTS[axis]["work-root"])
                    )

    held_grants = resolution.grants_for(roles)
    checked_grants = set()
    for required_class, required_grant in requirements:
        if required_grant in checked_grants:
            continue
        checked_grants.add(required_grant)
        if required_grant not in held_grants:
            return _deny_missing_grant(
                tool_name,
                target,
                project_id,
                required_class,
                required_grant,
                roles,
            )
    return _ALLOW


def _gate_work_root_scan(
    tool_name: str,
    target: str,
    entries: List[Dict[str, Any]],
    environ: Mapping[str, str],
    cartopian_home: Path,
    flavor,
    resolve,
    axis: str,
    session_project: Optional[Tuple[Dict[str, Any], str]] = None,
    bound_work_roots: Optional[Mapping[str, str]] = None,
) -> Decision:
    """Gate a target outside every project directory against declared work roots.

    A foreign project whose config cannot be resolved cannot claim the path:
    it is skipped because errors outside registered boundaries never block.
    In a bound dispatch, failure to resolve the active project's work roots
    fails closed before any target is evaluated. In an unbound legacy call, a
    work root present only in a broken config remains a documented residual;
    the registered project directory itself still fails closed above.

    Work-root ownership is never registry-order dependent. A mediated dispatch
    first uses its bound project when that project claims the target. Otherwise
    a target claimed only by another project is a cross-project denial. Outside
    a dispatched session, the deepest matching root wins; equally specific
    claims from different projects fail closed as ambiguous.
    """
    claims: List[Tuple[Dict[str, Any], str, GrantResolution, str, str]] = []
    for entry in entries:
        project_root = resolve(entry["path"])
        try:
            resolution, work_roots = _resolve_project_grants(
                Path(project_root), cartopian_home
            )
        except Exception as exc:
            if session_project is not None and _paths_equal(
                project_root, session_project[1], flavor
            ):
                active_id = session_project[0].get("id") or session_project[1]
                return _deny_bound_work_root_resolution_failure(
                    tool_name, target, str(active_id), str(exc)
                )
            continue
        classification_work_roots: Mapping[str, str] = work_roots
        active_entry = bool(
            session_project is not None
            and _paths_equal(project_root, session_project[1], flavor)
        )
        if active_entry and bound_work_roots is not None:
            classification_work_roots = bound_work_roots
        for name, wr_path in classification_work_roots.items():
            resolved_work_root = (
                wr_path
                if active_entry and bound_work_roots is not None
                else resolve(wr_path)
            )
            if _is_within(target, resolved_work_root, flavor):
                claims.append(
                    (entry, project_root, resolution, name, resolved_work_root)
                )
    if not claims:
        return _ALLOW

    selected: Optional[
        Tuple[Dict[str, Any], str, GrantResolution, str, str]
    ] = None
    if session_project is not None:
        active_entry, active_root = session_project
        active_claims = [
            claim
            for claim in claims
            if _paths_equal(claim[1], active_root, flavor)
        ]
        if active_claims:
            # A project may declare nested work roots. The most specific one
            # owns classification; the grant is the same either way.
            selected = max(
                active_claims,
                key=lambda claim: (
                    _match_specificity(target, claim[4], flavor),
                    claim[3],
                ),
            )
        else:
            active_id = active_entry.get("id") or active_root
            claimant_ids = [claim[0].get("id") or claim[1] for claim in claims]
            return _deny_cross_project_access(
                tool_name, target, str(active_id), [str(item) for item in claimant_ids]
            )
    else:
        deepest = max(
            _match_specificity(target, claim[4], flavor) for claim in claims
        )
        most_specific = [
            claim
            for claim in claims
            if _match_specificity(target, claim[4], flavor) == deepest
        ]
        project_roots = {
            _path_identity_key(claim[1], flavor) for claim in most_specific
        }
        if len(project_roots) > 1:
            claimant_ids = [
                claim[0].get("id") or claim[1] for claim in most_specific
            ]
            return _deny_ambiguous_work_root(
                tool_name, target, [str(item) for item in claimant_ids]
            )
        selected = max(most_specific, key=lambda claim: claim[3])

    entry, project_root, resolution, name, _work_root = selected
    if not resolution.activated:
        return _ALLOW
    roles = _session_roles(environ)
    grant = AXIS_GRANTS[axis]["work-root"]
    if grant in resolution.grants_for(roles):
        if (
            axis == "write"
            and session_project is not None
            and _paths_equal(project_root, session_project[1], flavor)
        ):
            project_id = entry.get("id") or project_root
            return _deny_bound_work_root_structured_write(
                tool_name, target, str(project_id), name
            )
        return _ALLOW
    project_id = entry.get("id") or project_root
    return _deny_missing_grant(
        tool_name, target, project_id, f"work-root:{name}", grant, roles
    )


def _registered_project_hardlink_aliases(
    target: str,
    entries: Sequence[Dict[str, Any]],
    flavor,
    resolve,
) -> Tuple[Tuple[str, ...], Optional[str]]:
    """Find registered-project file names sharing an external target inode.

    Lexical and resolved path checks close symlink aliases, but hard links have
    no distinguished canonical spelling. The scan runs only for an existing
    multiply-linked regular target, so the common path stays constant-time.
    """
    if flavor is not os.path:
        return (), None
    try:
        target_stat = os.stat(target)
    except FileNotFoundError:
        return (), None
    except OSError as exc:
        return (), f"cannot inspect target inode {target!r}: {exc}"
    if not stat.S_ISREG(target_stat.st_mode):
        return (), None

    aliases: list[str] = []
    scanned_roots: set[object] = set()
    for entry in entries:
        try:
            project_root = resolve(entry["path"])
        except (OSError, UnicodeError, ValueError) as exc:
            return (), f"cannot resolve registered project path {entry['path']!r}: {exc}"
        root_key = _path_identity_key(project_root, flavor)
        for config_name in _RAW_CONFIG_BASENAMES:
            config_path = os.path.join(project_root, config_name)
            try:
                if os.path.samefile(target, config_path):
                    aliases.append(config_path)
            except OSError:
                continue
        if target_stat.st_nlink < 2:
            continue
        if root_key in scanned_roots or _is_within(target, project_root, flavor):
            continue
        scanned_roots.add(root_key)
        walk_errors: list[OSError] = []

        def record_walk_error(exc: OSError) -> None:
            walk_errors.append(exc)

        for directory, _subdirs, filenames in os.walk(
            project_root, followlinks=False, onerror=record_walk_error
        ):
            for filename in filenames:
                candidate = os.path.join(directory, filename)
                try:
                    candidate_stat = os.stat(candidate, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    return (), f"cannot inspect registered project file {candidate!r}: {exc}"
                if (
                    stat.S_ISREG(candidate_stat.st_mode)
                    and candidate_stat.st_dev == target_stat.st_dev
                    and candidate_stat.st_ino == target_stat.st_ino
                ):
                    aliases.append(candidate)
        if walk_errors:
            return (), (
                f"cannot inspect registered project tree {project_root!r}: "
                f"{walk_errors[0]}"
            )
    return tuple(dict.fromkeys(aliases)), None


def _gate_target(
    tool_name: str,
    target: str,
    axis: str,
    scan_entries: Sequence[Dict[str, Any]],
    session_project: Optional[Tuple[Dict[str, Any], str]],
    environ: Mapping[str, str],
    cartopian_home: Path,
    flavor,
    resolve,
    protected_settings_paths: Optional[Sequence[str]],
    protected_roots: Optional[Sequence[str]],
    session_activated: bool,
    active_scope: Optional[Sequence[str]],
    bound_work_roots: Optional[Mapping[str, str]],
    search_descendants: bool = False,
) -> Decision:
    """Gate one lexical, resolved, or inode-alias spelling of a target."""
    if session_activated and flavor is os.path:
        try:
            target_stat = os.stat(target)
        except FileNotFoundError:
            target_stat = None
        except OSError as exc:
            return Decision(
                "deny",
                f"[guard] {tool_name} denied: cannot inspect target identity "
                f"for {target!r} ({exc}); failing closed.",
            )
        if (
            target_stat is not None
            and stat.S_ISREG(target_stat.st_mode)
            and target_stat.st_nlink > 1
        ):
            return Decision(
                "deny",
                f"[guard] {tool_name} denied: {target} has multiple hard-link "
                "names, so path-based project/work-root grants cannot prove "
                "that this operation stays inside the dispatched scope.",
            )
    if (
        axis == "write"
        and session_project is not None
        and _is_active_claude_settings(
            target,
            session_project[1],
            environ,
            flavor,
            resolve,
            protected_settings_paths,
        )
    ):
        return _deny_claude_settings_write(tool_name, target)
    if axis == "write" and session_project is not None and any(
        _is_within(target, resolve(root), flavor)
        for root in (protected_roots or ())
    ):
        return _deny_enforcement_write(tool_name, target)

    if search_descendants and session_project is not None:
        # A search rooted in an ancestor work root can sweep the active project,
        # so require that project's full reachable read surface as well. Exact
        # access to a nested foreign registered project selects that deeper
        # project and is rejected as cross-project; a directory search above it
        # must enforce the same boundary because results do not re-enter hooks.
        active_root = session_project[1]
        if _is_within(active_root, target, flavor) and not _paths_equal(
            active_root, target, flavor
        ):
            active_project_decision = _gate_inside_project(
                tool_name,
                active_root,
                session_project[0],
                active_root,
                environ,
                cartopian_home,
                flavor,
                resolve,
                axis,
                session_project,
                bound_work_roots,
                True,
            )
            if active_project_decision.action == "deny":
                return active_project_decision
        foreign_descendants = []
        for entry in scan_entries:
            registered_root = resolve(entry["path"])
            if _paths_equal(registered_root, active_root, flavor):
                continue
            if _is_within(registered_root, target, flavor):
                foreign_descendants.append(
                    str(entry.get("id") or registered_root)
                )
        if foreign_descendants:
            active_id = session_project[0].get("id") or active_root
            return _deny_cross_project_access(
                tool_name,
                target,
                str(active_id),
                foreign_descendants,
            )

    containing: Optional[Tuple[Dict[str, Any], str]] = None
    for entry in scan_entries:
        project_root = resolve(entry["path"])
        if _is_within(target, project_root, flavor):
            if containing is None or _match_specificity(
                target, project_root, flavor
            ) > _match_specificity(target, containing[1], flavor):
                containing = (entry, project_root)

    if containing is not None:
        if session_project is not None and not _paths_equal(
            containing[1], session_project[1], flavor
        ):
            active_id = session_project[0].get("id") or session_project[1]
            claimed_id = containing[0].get("id") or containing[1]
            return _deny_cross_project_access(
                tool_name,
                target,
                str(active_id),
                [str(claimed_id)],
            )
        return _gate_inside_project(
            tool_name,
            target,
            containing[0],
            containing[1],
            environ,
            cartopian_home,
            flavor,
            resolve,
            axis,
            session_project,
            bound_work_roots,
            search_descendants,
        )
    work_root_decision = _gate_work_root_scan(
        tool_name,
        target,
        list(scan_entries),
        environ,
        cartopian_home,
        flavor,
        resolve,
        axis,
        session_project,
        bound_work_roots,
    )
    if work_root_decision.action == "deny":
        return work_root_decision
    if session_project is not None and active_scope is not None and not any(
        _is_within(target, root, flavor) for root in active_scope
    ):
        active_id = session_project[0].get("id") or session_project[1]
        return _deny_outside_dispatched_scope(tool_name, target, str(active_id))
    return work_root_decision


def evaluate(
    payload: Dict[str, Any],
    *,
    environ: Optional[Mapping[str, str]] = None,
    cartopian_home: Optional[Path] = None,
    flavor=os.path,
    resolve=None,
    protected_settings_paths: Optional[Sequence[str]] = None,
    protected_roots: Optional[Sequence[str]] = None,
    bound_work_roots: Optional[Mapping[str, Tuple[str, int, int]]] = None,
) -> Decision:
    """Decide allow/deny for one PreToolUse payload.

    ``environ``, ``cartopian_home``, ``flavor``, and ``resolve`` are
    injectable for tests; live runs use the process environment,
    ``~/.cartopian``, ``os.path``, and ``os.path.realpath``.
    """
    if environ is None:
        environ = os.environ
    if cartopian_home is None:
        cartopian_home = Path.home() / ".cartopian"
    if resolve is None:
        resolve = os.path.realpath if flavor is os.path else (lambda p: p)

    tool_name = payload.get("tool_name")
    if tool_name in UNCONTAINED_SESSION_TOOLS:
        bound_root = environ.get(PROJECT_ROOT_ENV)
        if not bound_root:
            return Decision(
                "deny",
                f"[guard] {tool_name} denied: delegated/worktree session tools "
                "can relocate execution or mutate Git metadata outside the "
                "captured Cartopian boundary, and this hook has no dispatched-"
                "project binding with which to prove the call safe.",
            )
        try:
            resolution, _work_roots = _resolve_project_grants(
                Path(resolve(bound_root)), cartopian_home
            )
        except Exception as exc:
            return Decision(
                "deny",
                f"[guard] {tool_name} denied: the bound Cartopian capability "
                f"contract could not be resolved ({exc}).",
            )
        if resolution.activated:
            return Decision(
                "deny",
                f"[guard] {tool_name} denied: activated Cartopian handoffs "
                "disable delegated/worktree session tools because they can "
                "relocate execution or mutate Git metadata outside the captured "
                "project and work-root boundary.",
            )
        return _ALLOW
    if tool_name in FILE_MUTATION_TOOLS:
        axis = "write"
    elif tool_name in READ_TOOLS:
        axis = "read"
    else:
        return _ALLOW
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return _ALLOW
    cwd = payload.get("cwd") or os.getcwd()
    raw_targets: List[Tuple[str, bool]] = []
    glob_pattern: Optional[str] = None
    if tool_name == "Glob":
        pattern = tool_input.get("pattern")
        if isinstance(pattern, str) and pattern:
            glob_pattern = pattern
        # Claude treats an absolute Glob pattern as authoritative, ignoring a
        # separately supplied ``path`` and deriving the search directory from
        # the literal prefix before its first metacharacter.
        absolute_pattern_root = (
            _absolute_glob_search_root(pattern, flavor)
            if isinstance(pattern, str)
            and pattern
            and _glob_pattern_is_absolute(pattern, flavor)
            else None
        )
        if absolute_pattern_root is not None:
            raw_targets.append((absolute_pattern_root, True))
        else:
            authored_path = tool_input.get("path")
            raw_targets.append(
                (
                    authored_path
                    if isinstance(authored_path, str) and authored_path
                    else cwd,
                    True,
                )
            )
    elif tool_name == "Grep":
        authored_path = tool_input.get("path")
        raw_targets.append(
            (
                authored_path
                if isinstance(authored_path, str) and authored_path
                else cwd,
                True,
            )
        )
    else:
        raw_targets = [
            (tool_input[key], False)
            for key in _PATH_KEYS
            if isinstance(tool_input.get(key), str) and tool_input[key]
        ]
        if not raw_targets:
            return _ALLOW

    registry_file = Path(cartopian_home) / "projects.json"
    entries, registry_error = _load_registry_entries(registry_file, flavor)
    if entries is None:
        return Decision(
            "deny",
            f"[guard] {tool_name} denied: the Cartopian project registry is "
            f"unreadable ({registry_error}) — project boundaries cannot be "
            f"established, failing closed rather than silently allowing. "
            f"Repair {registry_file} to restore gated tool access.",
        )
    session_project = _session_project_binding(
        entries, environ, flavor, resolve
    )
    scan_entries = list(entries)
    if session_project is not None and not any(
        _paths_equal(resolve(entry["path"]), session_project[1], flavor)
        for entry in scan_entries
    ):
        # Process-scoped containment is bound directly by dispatch; registry
        # membership is discovery metadata, not an authorization prerequisite.
        scan_entries.append(session_project[0])
    if not scan_entries:
        return _ALLOW
    active_scope: Optional[Tuple[str, ...]] = None
    active_bound_paths: Optional[Dict[str, str]] = None
    session_activated = False
    if session_project is not None:
        try:
            active_resolution, active_work_roots = _resolve_project_grants(
                Path(session_project[1]), cartopian_home
            )
        except Exception as exc:
            active_id = session_project[0].get("id") or session_project[1]
            return _deny_bound_work_root_resolution_failure(
                str(tool_name), "<session boundary>", str(active_id), str(exc)
            )
        session_activated = active_resolution.activated
        if session_activated:
            if bound_work_roots is not None:
                active_id = session_project[0].get("id") or session_project[1]
                if set(bound_work_roots) != set(active_work_roots):
                    return _deny_bound_work_root_resolution_failure(
                        str(tool_name),
                        "<session boundary>",
                        str(active_id),
                        "launch-captured work-root names no longer match the "
                        "active capability contract",
                    )
                active_bound_paths = {}
                for name, captured in bound_work_roots.items():
                    try:
                        captured_path, captured_device, captured_inode = captured
                    except (TypeError, ValueError):
                        return _deny_bound_work_root_resolution_failure(
                            str(tool_name),
                            "<session boundary>",
                            str(active_id),
                            f"invalid launch-captured identity for work root {name!r}",
                        )
                    if not flavor.isabs(captured_path):
                        return _deny_bound_work_root_resolution_failure(
                            str(tool_name),
                            "<session boundary>",
                            str(active_id),
                            f"launch-captured work root {name!r} is not absolute",
                        )
                    if flavor is os.path:
                        try:
                            current = os.lstat(captured_path)
                        except OSError as exc:
                            return _deny_bound_work_root_resolution_failure(
                                str(tool_name),
                                "<session boundary>",
                                str(active_id),
                                f"launch-captured work root {name!r} changed or "
                                f"disappeared ({exc})",
                            )
                        if (
                            not stat.S_ISDIR(current.st_mode)
                            or stat.S_ISLNK(current.st_mode)
                            or (current.st_dev, current.st_ino)
                            != (captured_device, captured_inode)
                        ):
                            return _deny_bound_work_root_resolution_failure(
                                str(tool_name),
                                "<session boundary>",
                                str(active_id),
                                f"launch-captured work root {name!r} changed "
                                "filesystem identity",
                            )
                    active_bound_paths[name] = flavor.normpath(captured_path)
            else:
                active_bound_paths = {
                    name: resolve(path) for name, path in active_work_roots.items()
                }
            active_scope = (
                session_project[1],
                *active_bound_paths.values(),
            )
    for raw_target, search_descendants in raw_targets:
        try:
            if (
                glob_pattern is not None
                and "\x00" in glob_pattern
            ) or "\x00" in raw_target or (
                not flavor.isabs(raw_target) and "\x00" in cwd
            ):
                raise ValueError("embedded NUL")
            if flavor is os.path:
                if glob_pattern is not None:
                    os.fsencode(glob_pattern)
                os.fsencode(raw_target)
                if not flavor.isabs(raw_target):
                    os.fsencode(cwd)
            absolute_target = (
                raw_target
                if flavor.isabs(raw_target)
                else flavor.join(cwd, raw_target)
            )
            # Resolve the authored spelling before collapsing ``..``.
            # Filesystems follow a preceding symlink first, whereas lexical
            # normpath would erase that traversal and could misclassify the
            # actual destination.
            resolved_target = resolve(absolute_target)
            lexical_target = flavor.normpath(absolute_target)
        except (OSError, UnicodeError, ValueError) as exc:
            return Decision(
                "deny",
                f"[guard] {tool_name} denied: target path cannot be represented "
                f"safely on this filesystem ({exc}); failing closed.",
            )
        target_spellings = list(
            dict.fromkeys((lexical_target, resolved_target))
        )
        hardlink_aliases, hardlink_error = _registered_project_hardlink_aliases(
            resolved_target, scan_entries, flavor, resolve
        )
        if hardlink_error is not None:
            return Decision(
                "deny",
                f"[guard] {tool_name} denied: {raw_target} — registered-project "
                f"inode aliases could not be inspected safely ({hardlink_error}); "
                "failing closed.",
            )
        target_spellings.extend(
            alias for alias in hardlink_aliases if alias not in target_spellings
        )
        # A symlink or hard link can expose one inode under path classes with
        # different grants. Every spelling must authorize the operation; the
        # strictest (any deny) decision wins for both read and write axes.
        for target in target_spellings:
            decision = _gate_target(
                tool_name,
                target,
                axis,
                scan_entries,
                session_project,
                environ,
                cartopian_home,
                flavor,
                resolve,
                protected_settings_paths,
                protected_roots,
                session_activated,
                active_scope,
                active_bound_paths,
                search_descendants,
            )
            if decision.action == "deny":
                return decision
    return _ALLOW


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--role")
    parser.add_argument("--project-root")
    parser.add_argument("--cartopian-home", type=Path)
    parser.add_argument("--work-root", action="append", nargs=4)
    parser.add_argument("--settings-path", action="append")
    parser.add_argument("--protected-root", action="append")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Hook entry point: tool-call JSON on stdin; structured deny on stdout.

    Allows are perfectly silent (no output, exit 0). An unparseable payload
    carries no usable target, so it can never be attributed to a registered
    project — it is allowed with a stderr note rather than blocking every
    tool call on the machine.
    """
    args = _parser().parse_args(() if argv is None else argv)
    try:
        raw = sys.stdin.buffer.read()
        payload = json.loads(raw.decode("utf-8")) if raw.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError("hook payload is not a JSON object")
    except Exception as exc:  # zero footprint on protocol failure
        sys.stderr.write(f"[guard] cartopian claude_hook: unreadable hook payload ({exc}); not interfering\n")
        return 0

    effective_environ = dict(os.environ)
    if args.role is not None:
        effective_environ[ROLE_ENV] = args.role
    if args.project_root is not None:
        effective_environ[PROJECT_ROOT_ENV] = args.project_root
    bound_work_roots: Dict[str, Tuple[str, int, int]] = {}
    for name, path, raw_device, raw_inode in args.work_root or ():
        try:
            identity = (path, int(raw_device), int(raw_inode))
        except ValueError:
            identity = (path, -1, -1)
        if name in bound_work_roots:
            identity = (path, -1, -1)
        bound_work_roots[name] = identity
    protected_roots = [
        str(Path(__file__).resolve().parents[1]),
        str(Path(sys.executable).resolve()),
        str(Path(sys.prefix).resolve()),
        str(Path(sys.base_prefix).resolve()),
        *(args.protected_root or ()),
    ]
    if args.cartopian_home is not None:
        protected_roots.append(str(args.cartopian_home.resolve()))
    decision = evaluate(
        payload,
        environ=effective_environ,
        cartopian_home=args.cartopian_home,
        protected_settings_paths=args.settings_path,
        protected_roots=tuple(dict.fromkeys(protected_roots)),
        bound_work_roots=bound_work_roots,
    )
    if decision.action == "deny":
        sys.stdout.write(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": decision.reason,
                    }
                }
            )
            + "\n"
        )
        sys.stderr.write(decision.reason + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
