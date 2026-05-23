"""Cartopian Core CLI dispatcher (FR-014 contract).

Defines the exit-code contract, stderr-prefix helpers, and the argparse
surface every Phase-01 subcommand binds into. Every entry of
``SUBCOMMANDS`` is wired to a real handler in :func:`_real_handlers`.
"""
import argparse
import sys
from typing import List, Optional, Sequence

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_ENV = 3

SUBCOMMANDS: List[str] = [
    # FR-004
    "discover-projects",
    "generate-config",
    "move-task",
    "report-action",
    "register-project",
    "resolve-config",
    "scaffold-project",
    "task-bundle",
    "unregister-project",
    "validate-task-readiness",
    # FR-005
    "close-audit",
    "compose-state",
    "delete-prompt",
    "delete-report",
    "list-tasks",
    # FR-014 aggregator
    "next-action",
    # FR-003 aggregator
    "handoff-packet",
    # lifecycle + provenance audit
    "plan-audit",
]


def stderr_error(msg: str) -> None:
    sys.stderr.write(f"[error] {msg}\n")


def stderr_guard(msg: str) -> None:
    sys.stderr.write(f"[guard] {msg}\n")


def stderr_usage(msg: str) -> None:
    sys.stderr.write(f"[usage] {msg}\n")


class _UsageParser(argparse.ArgumentParser):
    """ArgumentParser that emits the [usage] stderr prefix on errors."""

    def error(self, message: str) -> None:  # pragma: no cover - exercised via subprocess
        if message.startswith("argument ") and "invalid choice" in message:
            try:
                head, tail = message.split(": invalid choice: ", 1)
                arg_name = head[len("argument "):]
                # Top-level subparsers metavar is `<subcommand>`; per-command
                # positionals carry plain identifier names (e.g. `to_status`).
                # Brackets/braces in the arg_name mean it's the subcommand
                # selector, not a real argument.
                if arg_name.startswith("<") or arg_name.startswith("{"):
                    bad = tail.split(" ", 1)[0].strip("'\"")
                    stderr_usage(f"unknown subcommand: {bad}")
                else:
                    stderr_usage(f"invalid {arg_name}: {tail}")
            except Exception:
                stderr_usage(message)
        else:
            stderr_usage(message)
        sys.exit(EXIT_USAGE)


def _real_handlers():
    """Map of subcommand name → (configure_parser, handler) for implemented commands.

    Imported lazily to avoid circular imports (command modules import EXIT_*
    constants from this module).
    """
    from cli.commands import (
        close_audit,
        compose_state,
        delete_prompt,
        delete_report,
        discover_projects,
        generate_config,
        handoff_packet,
        list_tasks,
        move_task,
        next_action,
        plan_audit,
        report_action,
        register_project,
        resolve_config,
        scaffold_project,
        task_bundle,
        unregister_project,
        validate_task_readiness as vtr,
    )

    return {
        "close-audit": (close_audit.configure_parser, close_audit.handler),
        "compose-state": (compose_state.configure_parser, compose_state.handler),
        "delete-prompt": (delete_prompt.configure_parser, delete_prompt.handler),
        "delete-report": (delete_report.configure_parser, delete_report.handler),
        "discover-projects": (discover_projects.configure_parser, discover_projects.handler),
        "generate-config": (generate_config.configure_parser, generate_config.handler),
        "handoff-packet": (handoff_packet.configure_parser, handoff_packet.handler),
        "list-tasks": (list_tasks.configure_parser, list_tasks.handler),
        "move-task": (move_task.configure_parser, move_task.handler),
        "next-action": (next_action.configure_parser, next_action.handler),
        "plan-audit": (plan_audit.configure_parser, plan_audit.handler),
        "report-action": (report_action.configure_parser, report_action.handler),
        "register-project": (register_project.configure_parser, register_project.handler),
        "resolve-config": (resolve_config.configure_parser, resolve_config.handler),
        "scaffold-project": (scaffold_project.configure_parser, scaffold_project.handler),
        "task-bundle": (task_bundle.configure_parser, task_bundle.handler),
        "unregister-project": (unregister_project.configure_parser, unregister_project.handler),
        "validate-task-readiness": (vtr.configure_parser, vtr.handler),
    }


def build_parser() -> _UsageParser:
    parser = _UsageParser(
        prog="cartopian",
        description="Cartopian Core CLI",
        add_help=True,
    )
    subparsers = parser.add_subparsers(dest="cmd", metavar="<subcommand>")
    real = _real_handlers()
    for name in SUBCOMMANDS:
        configure, handler = real[name]
        sub = subparsers.add_parser(name, help=name)
        configure(sub)
        sub.set_defaults(_handler=handler)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(list(argv))
    if not getattr(args, "cmd", None):
        stderr_usage("no subcommand given; try 'cartopian --help'")
        return EXIT_USAGE
    handler = getattr(args, "_handler", None)
    if handler is None:
        stderr_usage(f"unknown subcommand: {args.cmd}")
        return EXIT_USAGE
    return handler(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
