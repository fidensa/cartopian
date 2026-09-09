"""`cartopian select-project <project-root> --handle <handle>` — bind a capture session.

The explicit end of ``start_session`` Stage 0. The PM passes the opaque
handle the host intake adapter printed on the session's first prompt
(``cartopian-session: cs-...``). This command resolves it against adapter
state (``cli/intake_adapter.py``) and takes host and session identity from
the session record — never from arguments: there is no session-id option,
so a PM-supplied identity has nowhere to go.

What binding does (plan sections 4.3, 4.4, 4.6):

- promotes the session's preselection buffer to the project's captured set
  (from receipt ordinal 1, or from the ordinal after the previous binding
  when the session switches projects) and keeps capture running for it;
- writes the binding into the session record (eviction stops; the session
  survives ``SessionEnd``) and into the project at
  ``requests/bindings.json`` — written only here, read by the resolver;
- on a project switch, closes the previous binding at the current ordinal
  in both places and asks the adapter to print the routing line again;
- ``--unbind`` ends capture for the current project without binding another.

Refusals name one missing item each. ``session-unbound`` means no capture
session routes from the handle: the remedy is a fresh session on a host with
the intake hooks installed (and, on Codex, trusted), never a record written
by hand. A dispatched role (``CARTOPIAN_ROLE``) cannot bind.
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cli import intake_adapter
from cli.commands import _registry, _writers
from cli.emit import emit_record
from cli.evidence_resolver import (
    BINDINGS_BASENAME,
    BINDINGS_SCHEMA,
    bindings_path,
    read_bindings,
    write_bindings as _write_bindings,
)
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE, stderr_guard, stderr_usage

SESSION_UNBOUND_REMEDY = (
    "Stop and report to the operator: no capture session routes from this "
    "handle. Capture is supplied only by the host intake hooks in the "
    "operator's own session (`scripts/install.py --intake-hooks`; on Codex "
    "the operator must also trust the hooks in `/hooks`, on Hermes enable "
    "the plugin with `hermes plugins enable cartopian-intake`). A fresh session "
    "prints its `cartopian-session:` line on the first prompt. Never create, "
    "copy, or edit records under `requests/` or the intake directory by any "
    "means, including shell."
)


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "project_root",
        help="Absolute path to the registered Cartopian project root",
    )
    subparser.add_argument(
        "--handle",
        required=True,
        help="The cartopian-session handle injected into this session's context",
    )
    subparser.add_argument(
        "--unbind",
        action="store_true",
        help="End capture for the project this session is bound to; bind nothing",
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _registered_entry(root: Path) -> Optional[Dict[str, Any]]:
    try:
        entries = _registry.read_registry(_registry.registry_path())
    except _registry.MalformedRegistry:
        return None
    target = os.path.realpath(str(root))
    for entry in entries:
        if os.path.realpath(entry["path"]) == target:
            return entry
    return None


def _last_ordinal(store: intake_adapter.SessionStore, record: Dict[str, Any]) -> int:
    events = store.read_events(record["host"], record["session_id"])
    return max((int(e.get("ordinal", 0)) for e in events), default=0)


def _buffer_summary(
    store: intake_adapter.SessionStore, record: Dict[str, Any]
) -> Dict[str, Any]:
    events = store.read_events(record["host"], record["session_id"])
    marker = next((e for e in events if e.get("event") == "evicted"), None)
    return {
        "events": sum(1 for e in events if e.get("event") != "evicted"),
        "evicted": (
            {"count": marker.get("count"), "bytes": marker.get("bytes")}
            if marker
            else None
        ),
    }


def _active_binding(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for binding in reversed(record.get("bindings") or []):
        if binding.get("to_ordinal") is None:
            return binding
    return None


def _close_binding(
    binding: Dict[str, Any], at_ordinal: int, when: str
) -> None:
    binding["to_ordinal"] = at_ordinal
    binding["unbound_at"] = when
    project_root = Path(binding["project_path"])
    if not project_root.is_dir():
        return
    project_bindings = read_bindings(project_root)
    for item in project_bindings:
        if item.get("binding_id") == binding["binding_id"]:
            item["to_ordinal"] = at_ordinal
            item["unbound_at"] = when
    _write_bindings(project_root, project_bindings)


def _open_binding(
    record: Dict[str, Any],
    entry: Dict[str, Any],
    project_root: Path,
    from_ordinal: int,
    when: str,
) -> Dict[str, Any]:
    binding = {
        "binding_id": f"{record['handle']}/binding-{from_ordinal}",
        "handle": record["handle"],
        "host": record["host"],
        "session_id": record["session_id"],
        "project_id": entry["id"],
        "project_path": str(project_root),
        "from_ordinal": from_ordinal,
        "to_ordinal": None,
        "bound_at": when,
        "unbound_at": None,
        # Appended by the requirements/plan writers at lock (plan 4.6).
        "confirmation": None,
    }
    record.setdefault("bindings", []).append(binding)
    project_bindings = read_bindings(project_root)
    project_bindings.append(dict(binding))
    _write_bindings(project_root, project_bindings)
    return binding


def handler(args: argparse.Namespace) -> int:
    if os.environ.get(intake_adapter.ROLE_ENV):
        stderr_guard(
            "non-operator-session: CARTOPIAN_ROLE is set; a dispatched role "
            "cannot bind a capture session to a project"
        )
        return EXIT_FAIL
    root, err = _writers.validated_root(args.project_root)
    if err is not None:
        stderr_usage(err)
        return EXIT_USAGE
    entry = _registered_entry(root)
    if entry is None:
        stderr_guard(
            f"project-unregistered: {root} is not a registered project; "
            "project selection is registry-only (`cartopian discover-projects`)"
        )
        return EXIT_FAIL

    store = intake_adapter.SessionStore(intake_adapter.default_intake_root())
    record = store.resolve_handle(args.handle)
    if record is None:
        stderr_guard(f"session-unbound: {SESSION_UNBOUND_REMEDY}")
        return EXIT_FAIL

    when = _now()
    last = _last_ordinal(store, record)
    active = _active_binding(record)

    if args.unbind:
        if active is None:
            stderr_guard(
                "not-bound: this session is bound to no project; nothing to unbind"
            )
            return EXIT_FAIL
        if os.path.realpath(active["project_path"]) != os.path.realpath(str(root)):
            stderr_guard(
                f"not-bound: this session is bound to {active['project_id']}, "
                f"not {entry['id']}; name the bound project to unbind it"
            )
            return EXIT_FAIL
        _close_binding(active, last, when)
        store.save_session(record)
        _emit(record, entry, root, active, "unbound", store)
        return EXIT_OK

    if active is not None and os.path.realpath(active["project_path"]) == os.path.realpath(str(root)):
        _emit(record, entry, root, active, "already-bound", store)
        return EXIT_OK

    outcome = "bound"
    from_ordinal = 1
    if active is not None:
        _close_binding(active, last, when)
        from_ordinal = last + 1
        record["announce_pending"] = True
        outcome = "switched"
    elif record.get("bindings"):
        # Re-binding after an explicit unbind: only events from now on.
        from_ordinal = last + 1
        record["announce_pending"] = True
    binding = _open_binding(record, entry, root, from_ordinal, when)
    store.save_session(record)
    _emit(record, entry, root, binding, outcome, store)
    return EXIT_OK


def _emit(
    record: Dict[str, Any],
    entry: Dict[str, Any],
    root: Path,
    binding: Dict[str, Any],
    outcome: str,
    store: intake_adapter.SessionStore,
) -> None:
    emit_record(
        {
            "action": "select-project",
            "outcome": outcome,
            "handle": record["handle"],
            "host": record["host"],
            "session_id": record["session_id"],
            "project_id": entry["id"],
            "project_path": str(root),
            "binding": {
                "binding_id": binding["binding_id"],
                "from_ordinal": binding["from_ordinal"],
                "to_ordinal": binding.get("to_ordinal"),
            },
            "capture_active": binding.get("to_ordinal") is None,
            "buffer": _buffer_summary(store, record),
            "bindings_path": str(bindings_path(root)),
        }
    )
