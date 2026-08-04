"""Canonical Claude Code hook registration for Cartopian-governed projects.

Two adapters gate a Claude Code assignee, and both live in the project's
``.claude/settings.json``:

* ``PreToolUse`` -> ``cli/claude_hook.py``      capability-keyed read/write refusal
* ``Stop``       -> ``cli/claude_stop_hook.py`` report-less-stop refusal

Registration is **uniform and unelected**. Every registered project gets the
same two entries: there is no per-project choice, no operator flag, and no
opt-in. The PM runs assignees, not the operator, so an operator who never
learns a flag exists must not be the reason a handoff can stop without a
report. This module is the single definition of what "registered" means, so
the installer's coordinated surface, project registration, and the containment
matrix all read the same fact.

Operator-authored hooks are never destroyed. A merge replaces only the entry
that already names the Cartopian script and preserves every sibling.

This layer is pure standard library and performs no registry discovery of its
own beyond reading the operator registry it is handed.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

# Read tools first, then the mutation tools: the PreToolUse hook gates both
# axes, and the containment matrix claims read enforcement only when the
# registered matcher actually covers the read tools.
CLAUDE_HOOK_MATCHER = "Read|NotebookRead|Glob|Grep|Write|Edit|MultiEdit|NotebookEdit"

#: ``(event, script, matcher)`` — matcher ``None`` means the event carries no
#: tool name, so the entry is written without a ``matcher`` key.
HOOK_SPECS: Tuple[Tuple[str, str, Optional[str]], ...] = (
    ("PreToolUse", "claude_hook.py", CLAUDE_HOOK_MATCHER),
    ("Stop", "claude_stop_hook.py", None),
)

#: Observation states this module reports, ordered by severity. The coordinated
#: installer surface aggregates with the same precedence.
STATE_PRECEDENCE = ("malformed", "dirty", "missing", "current")


def settings_path(project_path: Path) -> Path:
    """Return the project-level Claude Code settings file."""
    return project_path / ".claude" / "settings.json"


def hook_command(install_root: Path, script: str) -> str:
    """Return the command string for one shipped hook script.

    The interpreter is pinned to the one running the installer: a project may
    later be opened from a shell whose ``python`` is not this one, and a hook
    that cannot start is a hook that silently does not gate.
    """
    return f'"{sys.executable}" "{install_root / "cli" / script}"'


def desired_entries(install_root: Path) -> Dict[str, Dict[str, Any]]:
    """Return ``{event: entry}`` for the hooks this install should register."""
    entries: Dict[str, Dict[str, Any]] = {}
    for event, script, matcher in HOOK_SPECS:
        entry: Dict[str, Any] = {
            "hooks": [
                {"type": "command", "command": hook_command(install_root, script)}
            ]
        }
        if matcher is not None:
            entry["matcher"] = matcher
        entries[event] = entry
    return entries


def _entry_names_script(item: Any, script: str) -> bool:
    if not isinstance(item, Mapping):
        return False
    hooks = item.get("hooks")
    if not isinstance(hooks, list):
        return False
    return any(
        isinstance(hook, Mapping) and script in str(hook.get("command", ""))
        for hook in hooks
    )


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(pairs) -> str:
    digest = hashlib.sha256()
    found = False
    for name, payload in pairs:
        found = True
        encoded = name.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        body = payload.encode("utf-8")
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
    return "sha256:" + digest.hexdigest() if found else "absent"


def read_settings(project_path: Path) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Return ``(state, document)`` for a project's Claude Code settings.

    ``state`` is ``"absent"``, ``"malformed"``, or ``"present"``. A malformed
    document yields no parsed body: silently replacing operator settings would
    be worse than refusing, and every caller here fails closed on it.
    """
    path = settings_path(project_path)
    if not path.is_file():
        return "absent", None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "malformed", None
    if not isinstance(document, dict):
        return "malformed", None
    return "present", document


def observe_project(project_path: Path, install_root: Path) -> Dict[str, str]:
    """Report how one project's registered hooks compare to the desired set.

    ``current`` means every event carries exactly the expected Cartopian entry.
    ``missing`` means at least one event has no Cartopian entry at all;
    ``dirty`` means one is registered but does not match this install (a stale
    interpreter or an install root that moved). Evidence is positive: an
    unreadable document is ``malformed``, never assumed current.
    """
    state, document = read_settings(project_path)
    if state == "malformed":
        return {"state": "malformed", "identity": "malformed"}

    desired = desired_entries(install_root)
    hooks = (document or {}).get("hooks")
    hooks = hooks if isinstance(hooks, dict) else {}

    observed_parts: List[Tuple[str, str]] = []
    missing = False
    dirty = False
    for event, script, _matcher in HOOK_SPECS:
        bucket = hooks.get(event)
        bucket = bucket if isinstance(bucket, list) else []
        found = [item for item in bucket if _entry_names_script(item, script)]
        if not found:
            missing = True
            observed_parts.append((event, "absent"))
            continue
        # More than one Cartopian entry for an event is itself divergence: the
        # merge writes exactly one, so a duplicate means something else edited
        # it and the surface is not what this install would produce.
        if len(found) > 1 or _canonical(found[0]) != _canonical(desired[event]):
            dirty = True
        observed_parts.append((event, _canonical(found[0])))

    if missing:
        resolved = "missing"
    elif dirty:
        resolved = "dirty"
    else:
        resolved = "current"
    return {"state": resolved, "identity": _digest(observed_parts)}


def project_desired_identity(install_root: Path) -> str:
    """Digest of the entries every project is expected to carry."""
    desired = desired_entries(install_root)
    return _digest(
        (event, _canonical(desired[event])) for event, _script, _m in HOOK_SPECS
    )


def merge_entries(document: Dict[str, Any], install_root: Path) -> Dict[str, Any]:
    """Return ``document`` with the Cartopian hook entries merged in place.

    Idempotent: an existing Cartopian entry for an event is replaced, and every
    other hook the operator registered — including their own ``Stop`` hooks —
    is preserved in its original order.
    """
    hooks = document.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks is not a JSON object")
    desired = desired_entries(install_root)
    for event, script, _matcher in HOOK_SPECS:
        bucket = hooks.get(event)
        bucket = bucket if isinstance(bucket, list) else []
        kept = [item for item in bucket if not _entry_names_script(item, script)]
        kept.append(desired[event])
        hooks[event] = kept
    return document


def apply_project(project_path: Path, install_root: Path) -> str:
    """Register both hooks in one project. Returns the resulting state.

    Raises ``ValueError`` when the existing document cannot be safely merged;
    callers surface that as a refusal rather than overwriting operator content.
    """
    state, document = read_settings(project_path)
    if state == "malformed":
        raise ValueError(
            f"{settings_path(project_path)} is unreadable or not a JSON object; "
            "fix or remove it, then re-run"
        )
    merged = merge_entries(document or {}, install_root)
    path = settings_path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return "current"


def registered_projects(install_root: Path) -> Tuple[List[Dict[str, str]], bool]:
    """Return ``(entries, readable)`` from the operator registry.

    ``readable`` is ``False`` when the registry exists but cannot be parsed —
    the caller must not then conclude that no project needs hooks.
    """
    registry = install_root / "projects.json"
    if not registry.exists():
        return [], True
    try:
        raw = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], False
    if not isinstance(raw, list):
        return [], False
    entries: List[Dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("path"):
            continue
        entries.append(
            {
                "id": str(entry.get("id") or "unidentified-project"),
                "path": str(entry["path"]),
            }
        )
    entries.sort(key=lambda item: item["id"])
    return entries, True
