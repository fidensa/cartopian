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
    "adversarial-review-context",
    "apply-migration-entry",
    "classify-risk",
    "discover-projects",
    "generate-config",
    "install-workflow",
    "install-state-contract",
    "resume-install",
    "verify-restart-state",
    "migrate-config",
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
    # host tools/call wait budget behind the dispatch gate
    "host-capability",
    # deterministic two-channel review context (original request + PM guidance)
    "review-context",
    # host intake-boundary capture; excluded from managed-agent MCP tools
    "capture-request",
]

# ---------------------------------------------------------------------------
# Operator-only subcommands.
#
# These belong to the host/operator boundary and are never part of any managed
# agent's tool surface. Request intake is automatic host plumbing; it is not a
# second command the operator performs. The MCP server excludes them from its tool
# registry (``mcp_server.server._tool_registry``), so no project-management,
# coder, or reviewer session can call them as an MCP tool; the commands
# themselves additionally refuse to run inside a dispatched handoff or an
# in-process MCP tool invocation. No capability grant and no shipped role
# preset confers them — they are not gated by grants, they are absent from the
# surface.
# ---------------------------------------------------------------------------
OPERATOR_ONLY_SUBCOMMANDS: List[str] = [
    "capture-request",
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
        adversarial_review_context,
        apply_migration_entry,
        archive_plan,
        capture_request,
        classify_risk,
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
        host_capability,
        install_workflow,
        install_state_contract,
        list_tasks,
        migrate_config,
        move_task,
        next_action,
        plan_audit,
        report_action,
        register_project,
        render_spec,
        reset_plan,
        resolve_config,
        resume_install,
        review_context,
        scaffold_project,
        task_bundle,
        unregister_project,
        update_config,
        validate_task_readiness as vtr,
        verify_restart_state,
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
        "adversarial-review-context": (
            adversarial_review_context.configure_parser,
            adversarial_review_context.handler,
        ),
        "apply-migration-entry": (
            apply_migration_entry.configure_parser,
            apply_migration_entry.handler,
        ),
        "archive-plan": (archive_plan.configure_parser, archive_plan.handler),
        "capture-request": (capture_request.configure_parser, capture_request.handler),
        "classify-risk": (classify_risk.configure_parser, classify_risk.handler),
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
        "host-capability": (host_capability.configure_parser, host_capability.handler),
        "install-workflow": (
            install_workflow.configure_parser,
            install_workflow.handler,
        ),
        "install-state-contract": (
            install_state_contract.configure_parser,
            install_state_contract.handler,
        ),
        "verify-restart-state": (
            verify_restart_state.configure_parser,
            verify_restart_state.handler,
        ),
        "list-tasks": (list_tasks.configure_parser, list_tasks.handler),
        "migrate-config": (migrate_config.configure_parser, migrate_config.handler),
        "move-task": (move_task.configure_parser, move_task.handler),
        "next-action": (next_action.configure_parser, next_action.handler),
        "plan-audit": (plan_audit.configure_parser, plan_audit.handler),
        "report-action": (report_action.configure_parser, report_action.handler),
        "register-project": (register_project.configure_parser, register_project.handler),
        "render-spec": (render_spec.configure_parser, render_spec.handler),
        "reset-plan": (reset_plan.configure_parser, reset_plan.handler),
        "resolve-config": (resolve_config.configure_parser, resolve_config.handler),
        "resume-install": (
            resume_install.configure_parser,
            resume_install.handler,
        ),
        "review-context": (review_context.configure_parser, review_context.handler),
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
        # A command that needs prose sets `subparser.description` in its own
        # `configure_parser`; that string is what the MCP `tools/list` surface
        # carries. It is deliberately opt-in rather than derived from the module
        # docstring: every connected session pays for `tools/list` whether or
        # not it ever enters PM mode, so only a command whose behavior the model
        # routinely gets wrong is worth that standing cost.
        sub = subparsers.add_parser(name, help=name)
        configure(sub)
        sub.set_defaults(_handler=handler)
    return parser


def _resolve_version() -> str:
    """Compatibility display of release_version only."""
    from pathlib import Path

    from cli.version_identities import release_version

    root = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return release_version(root)["value"] or "unknown"


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
