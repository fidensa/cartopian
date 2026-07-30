"""CLI/MCP projection of the coordinated install/update workflow."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict

from cli.emit import emit_record
from cli import host_capability
from cli.install_state import stable_projection
from cli.install_workflow import (
    WorkflowRefusal,
    apply_workflow,
    plan_workflow,
)
from cli.main import EXIT_FAIL, EXIT_OK, stderr_error
from cli.restart_state import (
    client_context_from_environment,
    running_server_from_environment,
)


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.description = (
        "Inventory every installed surface, emit a deterministic plan before "
        "mutation, and optionally apply only required or explicitly authorized "
        "repairs before portable verification."
    )
    subparser.add_argument("source_root", type=Path)
    subparser.add_argument("install_root", type=Path)
    subparser.add_argument(
        "--operation",
        choices=("fresh-install", "update", "repair", "verification"),
        default="update",
    )
    subparser.add_argument(
        "--mode", choices=("copy", "symlink"), default="copy"
    )
    subparser.add_argument(
        "--apply",
        action="store_true",
        help="apply required and explicitly authorized plan actions",
    )
    subparser.add_argument(
        "--client",
        action="append",
        default=[],
        help="supported client identifier; repeat for more than one client",
    )
    subparser.add_argument(
        "--decision",
        action="append",
        default=[],
        metavar="SURFACE=accept|decline|defer",
        help=(
            "bounded caller disposition for one optional affected surface; "
            "registration and configuration share one disposition"
        ),
    )


def _decisions(values: list[str]) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise WorkflowRefusal(
                f"invalid decision {value!r}; expected SURFACE=DISPOSITION"
            )
        surface, disposition = value.split("=", 1)
        if surface in parsed:
            raise WorkflowRefusal(f"duplicate decision for surface: {surface}")
        parsed[surface] = disposition
    return parsed


def handler(args: argparse.Namespace) -> int:
    try:
        connected_apply = args.apply and host_capability.under_mcp_host()
        running_fact = (
            running_server_from_environment() if connected_apply else None
        )
        current_client = (
            client_context_from_environment(tuple(args.client))
            if connected_apply
            else None
        )
        plan = plan_workflow(
            source_root=args.source_root,
            install_root=args.install_root,
            operation=args.operation,
            mode=args.mode,
            clients=tuple(args.client),
            decisions=_decisions(args.decision),
            running_server_fact=running_fact,
            client_context=current_client,
        )
        result = apply_workflow(plan) if args.apply else plan
    except WorkflowRefusal as exc:
        stderr_error(str(exc))
        return EXIT_FAIL
    emit_record(
        {
            "affected_surface_plan": result["internal"][
                "affected_surface_plan"
            ],
            "workflow": stable_projection(result),
        }
    )
    return (
        EXIT_FAIL
        if result["outcome"]["status"] in ("blocked", "failed")
        else EXIT_OK
    )
