"""``cartopian apply-migration-entry <project-path> <entry-version>``.

Executes only the deterministic, tool-owned filesystem actions registered for
one shipped migration entry.  Config edits and operator choices remain outside
this command.  The project root must exactly match a registered project.
"""
import argparse
import os
import stat
from pathlib import Path

from cli.atomic_write import GuardRefusal
from cli.commands._registry import MalformedRegistry, read_registry, registry_path
from cli.emit import emit_record
from cli.main import (
    EXIT_ENV,
    EXIT_FAIL,
    EXIT_OK,
    EXIT_USAGE,
    stderr_error,
    stderr_guard,
    stderr_usage,
)
from cli.migrations import (
    ENTRY_VERSIONS,
    MigrationApplyError,
    apply_plan,
    plan_entry,
    record_pending_actions,
)


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "project_path", help="Absolute path of the registered Cartopian project"
    )
    subparser.add_argument(
        "entry_version", help="Shipped migration entry version (for example v0.6.0)"
    )


def _registered_root(raw_path: str) -> Path:
    supplied = Path(raw_path)
    if not supplied.is_absolute():
        raise ValueError("project_path must be absolute")
    canonical = Path(os.path.realpath(os.path.normpath(raw_path)))
    try:
        entries = read_registry(registry_path())
    except MalformedRegistry:
        raise
    matches = [
        entry
        for entry in entries
        if Path(os.path.realpath(entry["path"])) == canonical
    ]
    if len(matches) != 1:
        raise GuardRefusal(
            "unregistered-project",
            f"project_path must resolve to exactly one registered project: {canonical}",
        )
    if not canonical.is_dir():
        raise GuardRefusal(
            "bad-root", f"registered project root is not a directory: {canonical}"
        )
    config = canonical / "cartopian.toml"
    if config.is_symlink() or not config.is_file():
        raise GuardRefusal(
            "bad-root", f"registered project has no real cartopian.toml: {canonical}"
        )
    config_st = os.lstat(config)
    if not stat.S_ISREG(config_st.st_mode) or config_st.st_nlink > 1:
        raise GuardRefusal(
            "bad-root",
            "registered project cartopian.toml is not a single-link regular "
            f"file: {canonical}",
        )
    return canonical


def _emit_result(
    project_path: str,
    entry_version: str,
    status: str,
    operations,
    pending_actions,
    validation_status: str,
    *,
    guard=None,
) -> None:
    details = {
        "project_path": project_path,
        "entry_version": entry_version,
        "status": status,
        "operations": list(operations),
        "pending_actions": list(pending_actions),
        "validation": {
            "status": validation_status,
            "scope": "registered-filesystem-actions",
        },
    }
    if guard is not None:
        details["guard"] = guard
    emit_record({"action": "apply-migration-entry", "details": details})


def handler(args: argparse.Namespace) -> int:
    if not Path(args.project_path).is_absolute():
        stderr_usage(f"project_path must be absolute; got: {args.project_path}")
        return EXIT_USAGE
    if args.entry_version not in ENTRY_VERSIONS:
        canonical = os.path.realpath(os.path.normpath(args.project_path))
        _emit_result(
            canonical,
            args.entry_version,
            "blocked",
            (
                {
                    "kind": "entry",
                    "target": ".",
                    "status": "blocked",
                    "reason": "unknown-entry",
                },
            ),
            (),
            "blocked",
            guard={
                "rule": "unknown-entry",
                "detail": (
                    "no filesystem migration registry entry for "
                    f"{args.entry_version}"
                ),
            },
        )
        stderr_guard(
            "unknown-entry: no filesystem migration registry entry for "
            f"{args.entry_version}"
        )
        return EXIT_FAIL
    try:
        root = _registered_root(args.project_path)
        plan = plan_entry(root, args.entry_version)
        if plan.pending:
            record_pending_actions(root, args.entry_version, plan)
            _emit_result(
                str(root),
                args.entry_version,
                "pending",
                plan.skipped,
                plan.pending,
                "blocked",
            )
            stderr_guard(
                "migration entry needs PM resolution before deterministic "
                "actions can run"
            )
            return EXIT_FAIL
        try:
            operations = apply_plan(root, args.entry_version, plan)
        except MigrationApplyError as exc:
            applied = any(
                operation.get("status") == "applied"
                for operation in exc.operations
            )
            _emit_result(
                str(root),
                args.entry_version,
                "partial" if applied else "blocked",
                exc.operations,
                (),
                "blocked",
                guard={"rule": exc.rule, "detail": exc.detail},
            )
            stderr_guard(f"{exc.rule}: {exc.detail}")
            return EXIT_FAIL
        try:
            verification = plan_entry(root, args.entry_version)
        except GuardRefusal as exc:
            operations.append(
                {
                    "kind": "validation",
                    "target": ".",
                    "status": "blocked",
                    "reason": exc.rule,
                }
            )
            _emit_result(
                str(root),
                args.entry_version,
                "partial" if any(
                    operation.get("status") == "applied"
                    for operation in operations
                ) else "blocked",
                operations,
                (),
                "blocked",
                guard={"rule": exc.rule, "detail": exc.detail},
            )
            stderr_guard(f"{exc.rule}: {exc.detail}")
            return EXIT_FAIL
        if verification.writes or verification.deletes or verification.pending:
            operations.append(
                {
                    "kind": "validation",
                    "target": ".",
                    "status": "blocked",
                    "reason": "postcondition-failed",
                }
            )
            _emit_result(
                str(root),
                args.entry_version,
                "partial" if any(
                    operation.get("status") == "applied"
                    for operation in operations
                ) else "blocked",
                operations,
                verification.pending,
                "blocked",
                guard={
                    "rule": "postcondition-failed",
                    "detail": "registered filesystem actions remain after application",
                },
            )
            stderr_guard(
                "postcondition-failed: registered filesystem actions remain "
                "after application"
            )
            return EXIT_FAIL
    except MalformedRegistry as exc:
        _emit_result(
            os.path.realpath(os.path.normpath(args.project_path)),
            args.entry_version,
            "blocked",
            ({"kind": "entry", "target": ".", "status": "blocked", "reason": "malformed-registry"},),
            (),
            "blocked",
            guard={"rule": "malformed-registry", "detail": str(exc)},
        )
        stderr_error(f"registry file is malformed: {exc}")
        return EXIT_ENV
    except GuardRefusal as exc:
        candidate_root = str(locals().get("root", Path(os.path.realpath(args.project_path))))
        _emit_result(
            candidate_root,
            args.entry_version,
            "blocked",
            ({"kind": "entry", "target": ".", "status": "blocked", "reason": exc.rule},),
            (),
            "blocked",
            guard={"rule": exc.rule, "detail": exc.detail},
        )
        stderr_guard(f"{exc.rule}: {exc.detail}")
        return EXIT_FAIL
    except OSError as exc:
        candidate_root = str(locals().get("root", Path(os.path.realpath(args.project_path))))
        _emit_result(
            candidate_root,
            args.entry_version,
            "blocked",
            ({"kind": "entry", "target": ".", "status": "blocked", "reason": "migration-failed"},),
            (),
            "blocked",
            guard={"rule": "migration-failed", "detail": str(exc)},
        )
        stderr_error(f"migration entry failed: {exc}")
        return EXIT_FAIL

    _emit_result(
        str(root),
        args.entry_version,
        "complete",
        operations,
        (),
        "passed",
    )
    return EXIT_OK
