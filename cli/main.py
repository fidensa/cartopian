"""Cartopian Core CLI dispatcher.

Defines the exit-code contract, stderr-prefix helpers, and the argparse
surface every subcommand binds into. Every entry of
``SUBCOMMANDS`` is wired to a real handler in :func:`_real_handlers`.
"""
import argparse
import os
import sys
from typing import List, Optional, Sequence

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_ENV = 3

SUBCOMMANDS: List[str] = [
    "apply-migration-entry",
    "discover-projects",
    "generate-config",
    "move-task",
    "report-action",
    "register-project",
    "resolve-config",
    "scaffold-project",
    "task-bundle",
    "unregister-project",
    "update-config",
    "validate-task-readiness",
    # Deidentified spec rendering for coder handoffs
    "render-spec",
    "close-audit",
    "compose-state",
    "delete-prompt",
    "delete-report",
    "list-tasks",
    # Structured PM authoring commands
    "write-requirements",
    "write-plan",
    "write-standards",
    "write-phase",
    "write-task",
    "write-spec",
    "write-prompt",
    "write-decision",
    "write-state",
    # Mediated transcription into resources/ (project supporting artifacts)
    "write-resource",
    # Durable CLI-supported home for PM/reviewer follow-up notes
    "write-backlog",
    "delete-backlog",
    "archive-plan",
    "reset-plan",
    # Aggregator: next action to take
    "next-action",
    # stdio wait primitives
    "wait-report",
    "wait-handoff",
    # Handoff packet aggregator
    "handoff-packet",
    # Mediated handoff dispatch
    "dispatch",
    # lifecycle + provenance audit
    "plan-audit",
    # honest per-host containment matrix
    "containment-matrix",
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
        apply_migration_entry,
        archive_plan,
        close_audit,
        compose_state,
        containment_matrix,
        delete_backlog,
        delete_prompt,
        delete_report,
        dispatch,
        discover_projects,
        generate_config,
        handoff_packet,
        list_tasks,
        move_task,
        next_action,
        plan_audit,
        report_action,
        register_project,
        render_spec,
        reset_plan,
        resolve_config,
        scaffold_project,
        task_bundle,
        unregister_project,
        update_config,
        validate_task_readiness as vtr,
        wait_handoff,
        wait_report,
        write_backlog,
        write_decision,
        write_phase,
        write_plan,
        write_prompt,
        write_requirements,
        write_resource,
        write_spec,
        write_standards,
        write_state,
        write_task,
    )

    return {
        "apply-migration-entry": (
            apply_migration_entry.configure_parser,
            apply_migration_entry.handler,
        ),
        "archive-plan": (archive_plan.configure_parser, archive_plan.handler),
        "close-audit": (close_audit.configure_parser, close_audit.handler),
        "compose-state": (compose_state.configure_parser, compose_state.handler),
        "containment-matrix": (containment_matrix.configure_parser, containment_matrix.handler),
        "delete-backlog": (delete_backlog.configure_parser, delete_backlog.handler),
        "delete-prompt": (delete_prompt.configure_parser, delete_prompt.handler),
        "delete-report": (delete_report.configure_parser, delete_report.handler),
        "dispatch": (dispatch.configure_parser, dispatch.handler),
        "discover-projects": (discover_projects.configure_parser, discover_projects.handler),
        "generate-config": (generate_config.configure_parser, generate_config.handler),
        "handoff-packet": (handoff_packet.configure_parser, handoff_packet.handler),
        "list-tasks": (list_tasks.configure_parser, list_tasks.handler),
        "move-task": (move_task.configure_parser, move_task.handler),
        "next-action": (next_action.configure_parser, next_action.handler),
        "plan-audit": (plan_audit.configure_parser, plan_audit.handler),
        "report-action": (report_action.configure_parser, report_action.handler),
        "register-project": (register_project.configure_parser, register_project.handler),
        "render-spec": (render_spec.configure_parser, render_spec.handler),
        "reset-plan": (reset_plan.configure_parser, reset_plan.handler),
        "resolve-config": (resolve_config.configure_parser, resolve_config.handler),
        "scaffold-project": (scaffold_project.configure_parser, scaffold_project.handler),
        "task-bundle": (task_bundle.configure_parser, task_bundle.handler),
        "unregister-project": (unregister_project.configure_parser, unregister_project.handler),
        "update-config": (update_config.configure_parser, update_config.handler),
        "validate-task-readiness": (vtr.configure_parser, vtr.handler),
        "wait-handoff": (wait_handoff.configure_parser, wait_handoff.handler),
        "wait-report": (wait_report.configure_parser, wait_report.handler),
        "write-backlog": (write_backlog.configure_parser, write_backlog.handler),
        "write-decision": (write_decision.configure_parser, write_decision.handler),
        "write-phase": (write_phase.configure_parser, write_phase.handler),
        "write-plan": (write_plan.configure_parser, write_plan.handler),
        "write-prompt": (write_prompt.configure_parser, write_prompt.handler),
        "write-requirements": (write_requirements.configure_parser, write_requirements.handler),
        "write-resource": (write_resource.configure_parser, write_resource.handler),
        "write-spec": (write_spec.configure_parser, write_spec.handler),
        "write-standards": (write_standards.configure_parser, write_standards.handler),
        "write-state": (write_state.configure_parser, write_state.handler),
        "write-task": (write_task.configure_parser, write_task.handler),
    }


def build_parser() -> _UsageParser:
    parser = _UsageParser(
        prog="cartopian",
        description="Cartopian Core CLI",
        add_help=True,
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the installed Cartopian version and exit",
    )
    subparsers = parser.add_subparsers(dest="cmd", metavar="<subcommand>")
    real = _real_handlers()
    for name in SUBCOMMANDS:
        configure, handler = real[name]
        sub = subparsers.add_parser(name, help=name)
        configure(sub)
        sub.set_defaults(_handler=handler)
    return parser


def _resolve_version() -> str:
    """The installed Cartopian ref. Read from the install root's ``VERSION`` file
    (written by the installer); fall back to ``git describe`` in a dev checkout,
    then to ``"unknown"``. Resolved lazily so it never runs git on a normal
    command — only when ``--version`` is requested."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(root, "VERSION"), encoding="utf-8") as fh:
            ref = fh.read().strip()
        if ref:
            return ref
    except OSError:
        pass
    try:
        import subprocess

        out = subprocess.run(
            ["git", "-C", root, "describe", "--tags", "--always", "--dirty"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # noqa: BLE001 — best-effort; any failure degrades to "unknown"
        pass
    return "unknown"


def main(argv: Optional[Sequence[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(list(argv))
    if getattr(args, "version", False):
        print(f"cartopian {_resolve_version()}")
        return EXIT_OK
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
