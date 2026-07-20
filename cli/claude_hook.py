"""Claude Code refusal adapter — capability-keyed PreToolUse read/write gating.

A Claude Code **PreToolUse hook** that denies raw file-mutation tool calls
(``Write``, ``Edit``, ``MultiEdit``, ``NotebookEdit``) *and* raw read tool
calls (``Read``, ``NotebookRead``, and the search tools ``Glob``/``Grep``)
against a registered Cartopian project's governed path-classes — and against
its declared work roots — when the active session lacks the corresponding
capability grant (see ``cli/capabilities.py`` and ``CAPABILITIES.md``).
Enforcement lives here, at the harness's native interception point; the
launchers under ``wrappers/`` stay neutral. ``Bash``/shell tool calls are
deliberately never gated — the raw-edit/read detection floor owns that
residual.

Decision procedure (per target path; identical for both axes):

1. Not inside any registered project's directory or declared work root →
   **allow untouched** (zero footprint: no output, exit 0).
2. Inside a registered project whose resolved config is *ungated* (no role
   declares a ``grants`` key) → **allow**.
3. Inside an *activated* project → resolve the session's role(s) to effective
   grants, classify the target, and **deny** unless the matching grant is
   held. The refusal is a single ``[guard]`` message naming the path, the
   path-class, and the missing grant.
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
  ``CONVENTIONS.md`` → ``write:lifecycle`` / ``read:governance``
- ``cartopian.toml`` / ``cartopian.local.toml`` → ``read:governance`` on the
  read axis; on the **write axis** a structured raw-edit tool is always denied
  regardless of grants (the mediated ``cartopian update-config`` command is the
  only edit path). Bash/shell and advisory-tier hosts stay documented residuals.
- ``prompts/`` → ``write:lifecycle`` (the PM lifecycle surface) /
  ``read:prompts`` (the assignee's handoff)
- ``decisions/`` → ``write:decisions`` / ``read:governance``
- ``reports/``, ``reviews/`` → ``write:reports`` / ``read:reports``
- a declared work root → ``write:worktree`` / ``read:work-roots``
- any other path inside the project directory → ``write:lifecycle`` /
  ``read:governance`` (unclassified project files fall to the PM surface
  rather than passing through an activated boundary ungated)

A ``Glob``/``Grep`` call without an explicit ``path`` searches the session
cwd, so it gates on the cwd; ``Read``-family calls without a usable path
carry no target and pass untouched (protocol failure, nothing attributable).

Session-role identification: ``cartopian dispatch`` exports environment to the
launched wrapper (the same mechanism that carries ``CARTOPIAN_TIMEOUT`` /
``CARTOPIAN_MODEL`` / ``CARTOPIAN_EFFORT``); it additionally exports
``CARTOPIAN_ROLE=<role>``, which
the assignee's Claude Code process — and therefore this hook — inherits. The
variable may carry several comma-separated roles (grants union per
``GrantResolution.grants_for``). An interactive session with no role marker
resolves to the project's PM role (``pm``); in an activated config that
declares no grants for ``pm``, it fails closed like any undeclared role.
Enforcement keys on grants only, never on role names or descriptions.

Windows parity: pure ``posixpath``/``ntpath``-parameterized path logic (no
``fcntl``/``os.fork``/POSIX shell); on Windows, membership and classification
are case-insensitive and separator-agnostic (drive letters, backslashes, and
forward slashes all normalize). Live runs use ``os.path``, which is the
correct flavor per OS.

Installation (operator-invoked; never auto-applied to any user-global
settings): register the hook in the *project-level* Claude Code settings —
``.claude/settings.json`` in the directory Claude Code runs in::

    {
      "hooks": {
        "PreToolUse": [
          {
            "matcher": "Read|NotebookRead|Glob|Grep|Write|Edit|MultiEdit|NotebookEdit",
            "hooks": [
              {
                "type": "command",
                "command": "python \\"$HOME/.cartopian/cli/claude_hook.py\\""
              }
            ]
          }
        ]
      }
    }

On native Windows use
``python "%USERPROFILE%\\.cartopian\\cli\\claude_hook.py"`` as the command.
``scripts/install.py --claude-hook <project-dir>`` writes this registration
for you (merging into an existing ``.claude/settings.json``).

Hook I/O contract: the tool-call JSON arrives on stdin; a deny is emitted as
the documented PreToolUse structured output (``permissionDecision: "deny"``
with the ``[guard]`` reason) on stdout with exit 0; an allow produces no
output at all. Standard library only.
"""
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

if __package__ in (None, ""):  # invoked as a script: `python .../cli/claude_hook.py`
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli.capabilities import GrantResolution, resolve_grants  # noqa: E402
from cli.commands.resolve_config import (  # noqa: E402
    _CliError,
    _load_toml,
    _resolve_roles,
    _resolve_work_roots,
)

# The file-mutation tools this hook gates on the write axis. Bash is
# deliberately absent: the raw-edit/read detection floor owns shell-routed
# access.
FILE_MUTATION_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})

# The read tools gated on the read axis. FILE_READ_TOOLS carry an explicit
# target path; SEARCH_READ_TOOLS (directory/content search) may omit it, in
# which case they search — and therefore gate on — the session cwd.
FILE_READ_TOOLS = frozenset({"Read", "NotebookRead"})
SEARCH_READ_TOOLS = frozenset({"Glob", "Grep"})
READ_TOOLS = FILE_READ_TOOLS | SEARCH_READ_TOOLS

# Target-path keys across all gated tools (mutation tools never send "path",
# search tools never send file_path/notebook_path, so one tuple serves both).
_PATH_KEYS = ("file_path", "notebook_path", "path")

ROLE_ENV = "CARTOPIAN_ROLE"
DEFAULT_ROLE = "pm"  # interactive session with no role marker → the PM role

# Config files are never writable through a structured raw-edit tool, regardless
# of grants or activation state — the mediated `cartopian update-config` command
# is the only edit path (it writes via the CLI subprocess, which this hook never
# gates). This is a structured-tool deny, not an absolute boundary: Bash/shell
# and advisory-tier hosts remain documented residuals, exactly as for every other
# governed path-class.
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
}

READ_CLASS_GRANTS: Dict[str, str] = {
    "plan": "read:governance",
    "lifecycle": "read:governance",
    "prompts": "read:prompts",  # ...but the assignee's handoff to read
    "decisions": "read:governance",
    "reports": "read:reports",
    "project-file": "read:governance",
    "work-root": "read:work-roots",
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
}

# Named project-root files → class (matched case-insensitively on Windows).
_ROOT_FILE_CLASSES: Dict[str, str] = {
    "IMPLEMENTATION_PLAN.md": "plan",
    "REQUIREMENTS.md": "plan",
    "ROADMAP.md": "plan",
    "STATE.md": "lifecycle",
    "BACKLOG.md": "lifecycle",
    "STANDARDS.md": "lifecycle",
    "CONVENTIONS.md": "lifecycle",
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


def _is_within(target: str, root: str, flavor) -> bool:
    """True iff ``target`` is ``root`` or lies under it (normalized compare)."""
    t, r = _norm(target, flavor), _norm(root, flavor)
    if t == r:
        return True
    if not r.endswith(flavor.sep):
        r += flavor.sep
    return t.startswith(r)


def classify_project_path(
    target: str, project_root: str, flavor, axis: str = "write"
) -> Tuple[str, str]:
    """Classify a path *inside* the project directory → (class, grant).

    ``axis`` selects which grant the class requires (``"write"`` for the
    mutation tools, ``"read"`` for the read tools); classification itself is
    axis-independent.
    """
    grants = AXIS_GRANTS[axis]
    rel = flavor.relpath(_norm(target, flavor), _norm(project_root, flavor))
    segments = [s for s in rel.split(flavor.sep) if s not in ("", ".")]
    if not segments or segments[0] == "..":
        # Caller guarantees membership; treat degenerate input as the broadest
        # governed surface rather than passing it through.
        return "project-file", grants["project-file"]
    if len(segments) == 1:
        # A single segment may be a governed root file, or — for the search
        # tools, which target directories — a governed directory itself.
        name = segments[0]
        for known, klass in _ROOT_FILE_CLASSES.items():
            if flavor.normcase(known) == name:
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
    if not registry_file.exists():
        return [], None
    try:
        raw = registry_file.read_text(encoding="utf-8")
    except OSError as exc:
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
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("path"), str)
            and flavor.isabs(entry["path"])
        ):
            entries.append(entry)
    return entries, None


def _session_roles(environ: Mapping[str, str]) -> Tuple[str, ...]:
    raw = environ.get(ROLE_ENV, "")
    roles = tuple(part.strip() for part in raw.split(",") if part.strip())
    return roles or (DEFAULT_ROLE,)


def _resolve_project_grants(
    project_root: Path, cartopian_home: Path
) -> Tuple[Dict[str, Any], GrantResolution]:
    """Resolve (project_cfg, GrantResolution) for one registered project.

    Raises on any resolution failure (missing/unreadable config); the caller
    decides whether that fails closed (target inside this project) or is
    skipped (target elsewhere).
    """
    project_toml = project_root / "cartopian.toml"
    if not project_toml.exists():
        raise _CliError(1, "guard", f"project config not found: {project_toml}")
    project_cfg = _load_toml(project_toml, "project config") or {}
    global_cfg = _load_toml(cartopian_home / "cartopian.toml", "global config") or {}
    roles_raw = _resolve_roles(global_cfg, project_cfg)
    return project_cfg, resolve_grants(roles_raw)


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
        f"edit tools. Use the mediated `cartopian update-config` command "
        f"(the `update_config` MCP tool), which validates and atomically writes "
        f"the change.",
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
) -> Decision:
    """Gate a target that lies inside a registered project's directory."""
    project_id = entry.get("id") or project_root

    # Config files are never writable through a structured raw-edit tool — the
    # mediated `cartopian update-config` command is the only edit path. This runs
    # before grant resolution so it holds in ungated projects and even when the
    # config cannot be resolved (fail-closed for config writes specifically).
    if axis == "write":
        basename = flavor.normcase(flavor.basename(_norm(target, flavor)))
        if basename in {flavor.normcase(n) for n in _RAW_CONFIG_BASENAMES}:
            return _deny_raw_config_write(tool_name, target, project_id)

    try:
        project_cfg, resolution = _resolve_project_grants(
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
    try:
        work_roots = _resolve_work_roots(project_cfg, Path(project_root))
    except Exception as exc:
        return _deny_resolution_failure(tool_name, target, project_id, str(exc))

    klass = grant = None
    for name, wr_path in work_roots.items():
        if _is_within(target, resolve(wr_path), flavor):
            klass, grant = f"work-root:{name}", AXIS_GRANTS[axis]["work-root"]
            break
    if klass is None:
        klass, grant = classify_project_path(target, project_root, flavor, axis)

    roles = _session_roles(environ)
    if grant in resolution.grants_for(roles):
        return _ALLOW
    return _deny_missing_grant(tool_name, target, project_id, klass, grant, roles)


def _gate_work_root_scan(
    tool_name: str,
    target: str,
    entries: List[Dict[str, Any]],
    environ: Mapping[str, str],
    cartopian_home: Path,
    flavor,
    resolve,
    axis: str,
) -> Decision:
    """Gate a target outside every project directory against declared work roots.

    A project whose config cannot be resolved cannot claim the path: it is
    skipped (errors outside registered boundaries never block). This means a
    work root of a *broken* project config is not protected — a documented
    residual; the project directory itself still fails closed above.
    """
    for entry in entries:
        project_root = resolve(entry["path"])
        try:
            project_cfg, resolution = _resolve_project_grants(
                Path(project_root), cartopian_home
            )
            work_roots = _resolve_work_roots(project_cfg, Path(project_root))
        except Exception:
            continue
        for name, wr_path in work_roots.items():
            if not _is_within(target, resolve(wr_path), flavor):
                continue
            if not resolution.activated:
                return _ALLOW
            roles = _session_roles(environ)
            grant = AXIS_GRANTS[axis]["work-root"]
            if grant in resolution.grants_for(roles):
                return _ALLOW
            project_id = entry.get("id") or project_root
            return _deny_missing_grant(
                tool_name, target, project_id, f"work-root:{name}", grant, roles
            )
    return _ALLOW


def evaluate(
    payload: Dict[str, Any],
    *,
    environ: Optional[Mapping[str, str]] = None,
    cartopian_home: Optional[Path] = None,
    flavor=os.path,
    resolve=None,
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
    raw_targets = [
        tool_input[key]
        for key in _PATH_KEYS
        if isinstance(tool_input.get(key), str) and tool_input[key]
    ]
    if not raw_targets:
        if tool_name in SEARCH_READ_TOOLS:
            # A pathless directory/content search runs over the session cwd —
            # that is the surface it reads, so that is what gates it.
            raw_targets = [cwd]
        else:
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
    if not entries:
        return _ALLOW
    for raw_target in raw_targets:
        target = raw_target if flavor.isabs(raw_target) else flavor.join(cwd, raw_target)
        target = resolve(target)

        # Deepest registered project directory containing the target governs it.
        containing: Optional[Tuple[Dict[str, Any], str]] = None
        for entry in entries:
            project_root = resolve(entry["path"])
            if _is_within(target, project_root, flavor):
                if containing is None or len(_norm(project_root, flavor)) > len(
                    _norm(containing[1], flavor)
                ):
                    containing = (entry, project_root)

        if containing is not None:
            decision = _gate_inside_project(
                tool_name,
                target,
                containing[0],
                containing[1],
                environ,
                cartopian_home,
                flavor,
                resolve,
                axis,
            )
        else:
            decision = _gate_work_root_scan(
                tool_name,
                target,
                entries,
                environ,
                cartopian_home,
                flavor,
                resolve,
                axis,
            )
        if decision.action == "deny":
            return decision
    return _ALLOW


def main() -> int:
    """Hook entry point: tool-call JSON on stdin; structured deny on stdout.

    Allows are perfectly silent (no output, exit 0). An unparseable payload
    carries no usable target, so it can never be attributed to a registered
    project — it is allowed with a stderr note rather than blocking every
    tool call on the machine.
    """
    try:
        raw = sys.stdin.buffer.read()
        payload = json.loads(raw.decode("utf-8")) if raw.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError("hook payload is not a JSON object")
    except Exception as exc:  # zero footprint on protocol failure
        sys.stderr.write(f"[guard] cartopian claude_hook: unreadable hook payload ({exc}); not interfering\n")
        return 0

    decision = evaluate(payload)
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
    sys.exit(main())
