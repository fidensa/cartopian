"""``cartopian migrate-config <project-path> [--apply]``."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from cli.commands._registry import MalformedRegistry, read_registry, registry_path
from cli.config_migration import (
    MigrationInterrupted,
    MigrationRefused,
    execute_configuration_migration,
    plan_configuration_migration,
)
from cli.emit import emit_record
from cli.main import EXIT_ENV, EXIT_FAIL, EXIT_OK, EXIT_USAGE, stderr_error, stderr_guard


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("project_path", help="Absolute registered project root")
    subparser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the allowlisted plan; without this flag planning is read-only",
    )


def _registered_root(raw_path: str) -> Path:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        raise MigrationRefused(
            "absolute-project-path", "project_path must be absolute"
        )
    try:
        root = candidate.resolve(strict=True)
    except OSError as exc:
        raise MigrationRefused(
            "invalid-project-path", "project path cannot be resolved"
        ) from exc
    if not root.is_dir():
        raise MigrationRefused(
            "invalid-project-path", "project path is not a directory"
        )
    registry = read_registry(registry_path())
    matches = []
    for entry in registry:
        try:
            registered = Path(entry["path"]).resolve(strict=True)
        except OSError:
            continue
        if os.path.normcase(os.fspath(registered)) == os.path.normcase(
            os.fspath(root)
        ):
            matches.append(entry)
    if len(matches) != 1:
        raise MigrationRefused(
            "unregistered-project",
            "migration requires exactly one matching registry entry",
        )
    return root


def handler(args: argparse.Namespace) -> int:
    try:
        project_root = _registered_root(args.project_path)
    except MalformedRegistry:
        stderr_error("project registry is malformed")
        return EXIT_ENV
    except MigrationRefused as exc:
        if exc.rule == "absolute-project-path":
            stderr_error(f"{exc.rule}: {exc.detail}")
            return EXIT_USAGE
        stderr_guard(f"{exc.rule}: {exc.detail}")
        return EXIT_FAIL

    plan = plan_configuration_migration(project_root)
    details = {
        "mode": "apply" if args.apply else "plan",
        "plan": plan.as_record(),
        "result": None,
    }
    if plan.status in ("refused", "pending"):
        emit_record({"action": "migrate-config", "details": details})
        diagnostic = plan.diagnostics[0]
        stderr_guard(
            f"{diagnostic['code']}: {diagnostic['scope']}:{diagnostic['field']}: "
            f"{diagnostic['message']}; recovery={diagnostic['recovery']}"
        )
        return EXIT_FAIL
    if args.apply:
        try:
            details["result"] = execute_configuration_migration(
                project_root, plan
            )
        except MigrationInterrupted as exc:
            emit_record({"action": "migrate-config", "details": details})
            stderr_error(str(exc))
            return EXIT_ENV
        except (MigrationRefused, OSError) as exc:
            emit_record({"action": "migrate-config", "details": details})
            stderr_guard(str(exc))
            return EXIT_FAIL
    emit_record({"action": "migrate-config", "details": details})
    return EXIT_OK
