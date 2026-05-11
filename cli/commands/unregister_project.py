"""`cartopian unregister-project <id-or-path>` (FR-003, SPEC-01-001).

Removes a single entry from ``~/.cartopian/projects.json`` (DEC-009) and emits
one NDJSON confirmation record. Does not touch the project's filesystem.

Path/id discriminator: the positional is treated as a **path** if it contains
``/``, ``\\``, or starts with ``~``; otherwise it is treated as an **id** and
matched exactly against ``entry.id``.
"""
import argparse
import os
import sys
from pathlib import Path

from cli.commands._registry import (
    MalformedRegistry,
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
        "id_or_path",
        help=(
            "Project id, or absolute path to the project root. "
            "Inputs containing '/', '\\', or starting with '~' are treated "
            "as paths; everything else is treated as an id."
        ),
    )


def _looks_like_path(value: str) -> bool:
    return "/" in value or "\\" in value or value.startswith("~")


def handler(args: argparse.Namespace) -> int:
    raw_input = args.id_or_path
    is_path = _looks_like_path(raw_input)

    if is_path:
        expanded = os.path.expanduser(raw_input)
        if not Path(expanded).is_absolute():
            _stderr(
                "usage",
                f"id_or_path must be an absolute path; got: {raw_input}",
            )
            return EXIT_USAGE
        match_key = "path"
        match_value = str(Path(expanded).resolve())
    else:
        match_key = "id"
        match_value = raw_input

    reg_path = registry_path()
    try:
        entries = read_registry(reg_path)
    except MalformedRegistry as exc:
        _stderr("error", f"registry file is malformed: {exc}")
        return EXIT_ENV

    matches = [e for e in entries if e.get(match_key) == match_value]
    if len(matches) == 0:
        _stderr("guard", f"no registry entry matches: {raw_input}")
        return EXIT_FAIL
    if len(matches) > 1:
        _stderr(
            "guard",
            (
                f"ambiguous registry entry: {raw_input} matches {len(matches)} "
                f"entries — operator must edit ~/.cartopian/projects.json manually"
            ),
        )
        return EXIT_FAIL

    matched = matches[0]
    remaining = [e for e in entries if e is not matched]
    try:
        write_registry(reg_path, remaining)
    except OSError as exc:
        _stderr("error", f"failed to write registry: {reg_path} — {exc}")
        return EXIT_ENV

    emit_record(
        {
            "action": "unregister-project",
            "details": {
                "id": matched.get("id"),
                "path": matched.get("path"),
            },
        }
    )
    return EXIT_OK
