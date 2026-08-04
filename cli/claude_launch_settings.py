"""Build Cartopian's process-scoped Claude Code hook settings.

The shipped Claude wrappers call this helper at the dispatch boundary.  It
prints one compact JSON object for Claude Code's ``--settings`` option and
never writes a user, project, or local settings file.  Because the wrappers do
not pass ``--setting-sources``, Claude's normal settings sources remain
available.

The two hooks are independent:

* ``PreToolUse`` is included only when the wrapper supplies the dispatched
  role boundary and the resolved project config activates capability grants.
* ``Stop`` is included only when dispatch supplied an expected report path.

Claude merges hook arrays across settings scopes.  During the project-hook
compatibility window, an existing Cartopian entry is reused only when it
already names the interpreter and installed hook selected for this launch.
That makes Claude's structural de-duplication exact without depending on a
stale interpreter.  An incompatible legacy entry is a hard launch error; the
operator can remove it with ``scripts/install.py --claude-hook PROJECT_DIR``.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

if __package__ in (None, ""):  # invoked from an installed wrapper
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CAPABILITY_MATCHER = "|".join(
    ("Read", "NotebookRead", "Glob", "Grep", "Write", "Edit", "MultiEdit", "NotebookEdit")
)


class SettingsError(RuntimeError):
    """The per-launch settings layer cannot be constructed safely."""


def hook_command(
    install_root: Path,
    script_name: str,
    *,
    windows: bool,
    interpreter: Optional[Path] = None,
) -> str:
    """Return a shell-safe command using this launch's Python interpreter."""
    executable = str(interpreter or Path(sys.executable))
    argv = [executable, str(install_root / "cli" / script_name)]
    if windows:
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def _entry(command: str, *, matcher: Optional[str] = None) -> dict:
    entry: dict[str, Any] = {
        "hooks": [{"type": "command", "command": command}],
    }
    if matcher is not None:
        entry["matcher"] = matcher
    return entry


def _project_entries(project_dir: Optional[Path], event: str) -> list[dict[str, Any]]:
    """Return Cartopian entries for ``event`` from project settings.

    Invalid settings stay Claude's responsibility.  This helper only detects
    recognizable Cartopian entries and never copies unrelated settings into
    the command-line layer.
    """
    if project_dir is None:
        return []
    settings_path = project_dir / ".claude" / "settings.json"
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        entries = settings.get("hooks", {}).get(event, [])
    except (AttributeError, OSError, json.JSONDecodeError):
        return []
    if not isinstance(entries, list):
        return []
    script_name = "claude_hook.py" if event == "PreToolUse" else "claude_stop_hook.py"
    found = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        handlers = entry.get("hooks", [])
        if not isinstance(handlers, list):
            continue
        if any(
            isinstance(handler, dict)
            and script_name in str(handler.get("command", ""))
            for handler in handlers
        ):
            found.append(entry)
    return found


def _compatible_entry(
    project_dir: Optional[Path],
    event: str,
    expected: dict[str, Any],
    compatible: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Reuse an exact legacy entry, or fail rather than execute twice."""
    existing = _project_entries(project_dir, event)
    if not existing:
        return expected
    for candidate in (expected, *compatible):
        if all(entry == candidate for entry in existing):
            return existing[0]
    location = project_dir / ".claude" / "settings.json" if project_dir else "project settings"
    raise SettingsError(
        f"incompatible legacy Cartopian {event} hook in {location}; "
        "run scripts/install.py --claude-hook PROJECT_DIR to remove old "
        "project registrations before dispatch"
    )


def capability_activated(project_dir: Path) -> bool:
    """Resolve the same project capability contract enforced by the hook."""
    # Keep Stop-only construction import-light: completion enforcement can be
    # used outside mediated dispatch, while capability resolution requires the
    # Python 3.11+ Cartopian runtime exported by dispatch.
    from cli.claude_hook import _resolve_project_grants

    # Canonical configuration resolution (and the live hook) read the global
    # config from ~/.cartopian, independent of where a development/copy layout
    # happens to place the executable files.
    config_root = Path.home() / ".cartopian"
    resolution, _work_roots = _resolve_project_grants(project_dir, config_root)
    return resolution.activated


def build_settings(
    install_root: Path,
    *,
    windows: bool,
    project_dir: Optional[Path] = None,
    include_capability: bool = False,
    include_completion: bool = False,
    interpreter: Optional[Path] = None,
) -> dict:
    """Return the additional process-scoped Claude settings object."""
    hooks: dict[str, list[dict[str, Any]]] = {}
    executable = str(interpreter or Path(sys.executable))
    if include_capability:
        if project_dir is None:
            raise SettingsError("capability settings require --project-dir")
        if capability_activated(project_dir):
            command = hook_command(
                install_root,
                "claude_hook.py",
                windows=windows,
                interpreter=interpreter,
            )
            expected = _entry(command, matcher=CAPABILITY_MATCHER)
            legacy = _entry(
                f'"{executable}" "{install_root / "cli" / "claude_hook.py"}"',
                matcher=CAPABILITY_MATCHER,
            )
            hooks["PreToolUse"] = [
                _compatible_entry(project_dir, "PreToolUse", expected, (legacy,))
            ]
    if include_completion:
        command = hook_command(
            install_root,
            "claude_stop_hook.py",
            windows=windows,
            interpreter=interpreter,
        )
        expected = _entry(command)
        legacy = _entry(
            f'"{executable}" "{install_root / "cli" / "claude_stop_hook.py"}"',
        )
        hooks["Stop"] = [
            _compatible_entry(project_dir, "Stop", expected, (legacy,))
        ]
    return {"hooks": hooks} if hooks else {}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--install-root", required=True, type=Path)
    parser.add_argument("--project-dir", type=Path)
    parser.add_argument("--capability", action="store_true")
    parser.add_argument("--completion", action="store_true")
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
        settings = build_settings(
            args.install_root.resolve(),
            windows=windows,
            project_dir=project_dir,
            include_capability=args.capability,
            include_completion=args.completion,
        )
    except Exception as exc:
        sys.stderr.write(f"cartopian Claude settings error: {exc}\n")
        return 1
    sys.stdout.write(json.dumps(settings, separators=(",", ":")))
    return 0


if __name__ == "__main__":  # pragma: no cover - wrapper entry point
    raise SystemExit(main())
