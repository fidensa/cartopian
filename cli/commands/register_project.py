"""`cartopian register-project <project-path> [--label STR]`.

Appends an entry ``{id, path, label}`` to ``~/.cartopian/projects.json``
and emits one NDJSON confirmation record. Id is derived exclusively from the
project's ``cartopian.toml`` ``[project] id`` and must be kebab-case.

Guard precedence: path-collision check fires before duplicate-id check.
"""
import argparse
import sys
import tomllib
from pathlib import Path
from typing import Any, Dict, Optional

from cli import claude_hooks
from cli.commands._registry import (
    MalformedRegistry,
    is_kebab_case,
    read_registry,
    registry_path,
    write_registry,
)
from cli.emit import emit_record
from cli.main import EXIT_ENV, EXIT_FAIL, EXIT_OK, EXIT_USAGE


def _stderr(prefix: str, msg: str) -> None:
    sys.stderr.write(f"[{prefix}] {msg}\n")


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "project_path",
        help="Absolute path to the project root",
    )
    subparser.add_argument(
        "--label",
        default=None,
        help="Optional human-readable label (defaults to [project] name)",
    )


def _load_project_toml(project_path: Path) -> Optional[Dict[str, Any]]:
    toml = project_path / "cartopian.toml"
    if not toml.exists():
        return None
    with toml.open("rb") as fh:
        return tomllib.load(fh)


def handler(args: argparse.Namespace) -> int:
    raw_path = args.project_path
    if not Path(raw_path).is_absolute():
        _stderr("usage", f"project_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE

    project_path = Path(raw_path).resolve()
    project_path_str = str(project_path)

    try:
        cfg = _load_project_toml(project_path)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        _stderr("guard", f"cartopian.toml unreadable at {project_path_str}: {exc}")
        return EXIT_FAIL

    if cfg is None:
        _stderr(
            "guard",
            f"not a Cartopian project: no cartopian.toml at {project_path_str}",
        )
        return EXIT_FAIL

    project_table = cfg.get("project")
    if not isinstance(project_table, dict) or "id" not in project_table:
        _stderr(
            "guard",
            f"cartopian.toml missing [project] id at {project_path_str}",
        )
        return EXIT_FAIL

    project_id = project_table["id"]
    if not isinstance(project_id, str) or project_id == "":
        _stderr(
            "guard",
            f"cartopian.toml has malformed [project] id at {project_path_str}",
        )
        return EXIT_FAIL
    if not is_kebab_case(project_id):
        _stderr(
            "guard",
            (
                f"cartopian.toml has malformed [project] id at "
                f"{project_path_str}: {project_id} — must be kebab-case"
            ),
        )
        return EXIT_FAIL

    if args.label is None:
        name = project_table.get("name")
        if not isinstance(name, str) or name == "":
            _stderr(
                "guard",
                f"cartopian.toml missing [project] name at {project_path_str}",
            )
            return EXIT_FAIL
        label = name
    else:
        label = args.label

    reg_path = registry_path()
    try:
        entries = read_registry(reg_path)
    except MalformedRegistry as exc:
        _stderr("error", f"registry file is malformed: {exc}")
        return EXIT_ENV

    # Path-collision guard precedes duplicate-id guard so that re-registering
    # the same project reports the collision, not the (also-true) id clash.
    for entry in entries:
        if entry.get("path") == project_path_str:
            _stderr(
                "guard",
                (
                    f"path already registered: {project_path_str} "
                    f"(existing id: {entry.get('id')})"
                ),
            )
            return EXIT_FAIL
    for entry in entries:
        if entry.get("id") == project_id:
            _stderr(
                "guard",
                (
                    f"duplicate registry id: {project_id} "
                    f"(existing path: {entry.get('path')}) — rename the project "
                    f"or unregister the existing entry"
                ),
            )
            return EXIT_FAIL

    new_entry = {"id": project_id, "path": project_path_str, "label": label}
    entries.append(new_entry)
    try:
        write_registry(reg_path, entries)
    except OSError as exc:
        _stderr("error", f"failed to write registry: {reg_path} — {exc}")
        return EXIT_ENV

    # A project registered after the last install would otherwise carry no
    # assignee hooks until the next upgrade ran. Registration is uniform for
    # every project, so it happens here too rather than waiting.
    hooks_state = _register_hooks(project_path, reg_path.parent)

    emit_record(
        {
            "action": "register-project",
            "details": {
                "id": project_id,
                "path": project_path_str,
                "label": label,
                "project_hooks": hooks_state,
            },
        }
    )
    return EXIT_OK


def _register_hooks(project_path: Path, install_root: Path) -> str:
    """Register the Claude Code assignee hooks; never fail the registration.

    The project is registered either way — a hook that could not be written is
    reported as a residual the next install run repairs, not a reason to leave
    the registry and the project disagreeing about whether the project exists.
    """
    try:
        return claude_hooks.apply_project(project_path, install_root)
    except (ValueError, OSError) as exc:
        _stderr(
            "residual",
            (
                f"project hooks not registered at "
                f"{claude_hooks.settings_path(project_path)}: {exc}; "
                f"the next install or update run repairs this"
            ),
        )
        return "unregistered"
