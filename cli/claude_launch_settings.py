"""Build Cartopian's process-scoped Claude Code completion settings.

The Claude wrappers call this helper only when dispatch exported
``CARTOPIAN_EXPECTED_REPORT_PATH``.  It prints one compact JSON object for
Claude Code's ``--settings`` option; it never reads or writes a Claude settings
file.  File-based user, project, and local settings therefore remain loaded by
Claude Code and the command-line layer adds only Cartopian's Stop hook.

Keeping construction in Python gives POSIX and native Windows one JSON and
command-quoting implementation.  During the compatibility window it reuses an
older project-level Cartopian Stop entry verbatim when one exists. Claude Code
merges, concatenates, and de-duplicates array settings across scopes, so that
structurally identical per-launch entry executes only once.
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


def hook_command(install_root: Path, *, windows: bool) -> str:
    """Return a shell-safe command for the installed completion hook."""
    argv = [sys.executable, str(install_root / "cli" / "claude_stop_hook.py")]
    if windows:
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def _legacy_project_entry(project_dir: Optional[Path]) -> Optional[dict[str, Any]]:
    """Return an older Cartopian project Stop entry, without changing it.

    Invalid settings are left to Claude Code's normal validation behavior. A
    non-Cartopian Stop hook is never copied into the command-line layer.
    """
    if project_dir is None:
        return None
    settings_path = project_dir / ".claude" / "settings.json"
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        entries = settings.get("hooks", {}).get("Stop", [])
    except (AttributeError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        handlers = entry.get("hooks", [])
        if not isinstance(handlers, list):
            continue
        if any(
            isinstance(handler, dict)
            and "claude_stop_hook.py" in str(handler.get("command", ""))
            for handler in handlers
        ):
            return entry
    return None


def build_settings(
    install_root: Path, *, windows: bool, project_dir: Optional[Path] = None
) -> dict:
    """Return the additional, process-scoped Claude settings object."""
    entry = _legacy_project_entry(project_dir)
    if entry is None:
        entry = {
            "hooks": [
                {
                    "type": "command",
                    "command": hook_command(install_root, windows=windows),
                }
            ]
        }
    return {
        "hooks": {
            "Stop": [entry]
        }
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--install-root", required=True, type=Path)
    parser.add_argument("--project-dir", type=Path)
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
    settings = build_settings(
        args.install_root.resolve(), windows=windows, project_dir=project_dir
    )
    sys.stdout.write(json.dumps(settings, separators=(",", ":")))
    return 0


if __name__ == "__main__":  # pragma: no cover - wrapper entry point
    raise SystemExit(main())
