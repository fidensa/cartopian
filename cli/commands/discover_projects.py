"""`cartopian discover-projects` (FR-003, FR-004 #5, SPEC-01-001).

Reads the registry at ``~/.cartopian/projects.json`` (DEC-009) and emits one
NDJSON record per entry on stdout in registry-insertion order. Empty or
missing registry emits nothing and exits 0. Corrupt registry exits 3 per
FR-014 (environment error).
"""
import argparse
import sys

from cli.commands._registry import (
    MalformedRegistry,
    read_registry,
    registry_path,
)
from cli.emit import emit_record
from cli.main import EXIT_ENV, EXIT_OK


def _stderr(prefix: str, msg: str) -> None:
    sys.stderr.write(f"[{prefix}] {msg}\n")


def configure_parser(subparser: argparse.ArgumentParser) -> None:  # noqa: ARG001
    # No flags. No positional args. Per FR-004 #5 V1 takes no filter flags.
    return None


def handler(_args: argparse.Namespace) -> int:
    try:
        entries = read_registry(registry_path())
    except MalformedRegistry as exc:
        _stderr("error", f"registry file is malformed: {exc}")
        return EXIT_ENV
    for entry in entries:
        emit_record(
            {
                "id": entry["id"],
                "path": entry["path"],
                "label": entry.get("label"),
            }
        )
    return EXIT_OK
