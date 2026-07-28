"""`cartopian host-capability [--role <role>] [--project <project-path>]`.

Report the MCP host's tool-call wait budget as one NDJSON record: the ceilings
that end a single ``tools/call``, where each number came from, and — with
``--role`` — whether that role's handoff timeout fits inside them.

This is the readable form of the gate ``dispatch`` applies before launching
(``cli/host_capability.py``). Run it when a handoff dies with a transport error
instead of a protocol outcome, or before configuring a long role timeout, to
see what the host will actually allow. Read-only: touches no project state and
launches nothing.

Outside an MCP host (a plain shell invocation) there is no host-imposed
ceiling, and the record says so rather than inventing one.
"""
import argparse
from pathlib import Path
from typing import Any, Dict, Optional

from cli import host_capability
from cli.commands.resolve_config import _CliError
from cli.commands.wait_handoff import _resolve_timeout_seconds
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE, stderr_error, stderr_usage


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    """Add arguments for host-capability."""
    subparser.add_argument(
        "--role",
        default=None,
        help=(
            "Optional role whose configured launch timeout is checked against "
            "the host budget; requires --project"
        ),
    )
    subparser.add_argument(
        "--project",
        default=None,
        help="Absolute path to the project root that declares --role",
    )


def _role_timeout_seconds(project_root: Path, role: str) -> int:
    return _resolve_timeout_seconds(project_root, role)


def handler(args: argparse.Namespace) -> int:
    """Emit the resolved host budget, and the role fit when a role is named."""
    role: Optional[str] = args.role
    raw_project: Optional[str] = args.project

    if role is not None and raw_project is None:
        stderr_usage("--role requires --project <project-path>")
        return EXIT_USAGE

    project_root: Optional[Path] = None
    if raw_project is not None:
        if not Path(raw_project).is_absolute():
            stderr_usage(f"--project must be an absolute path; got: {raw_project}")
            return EXIT_USAGE
        project_root = Path(raw_project)
        if not (project_root / "cartopian.toml").is_file():
            stderr_error(f"project config not found: {project_root / 'cartopian.toml'}")
            return EXIT_FAIL
        project_root = project_root.resolve()

    budget = host_capability.resolve_host_budget()
    record: Dict[str, Any] = {
        "under_mcp_host": budget is not None,
        "host_wait_budget": budget.record() if budget is not None else None,
        "role": role,
        "role_timeout_seconds": None,
        "fits": None,
        "refusal": None,
    }

    if role is None:
        emit_record(record)
        return EXIT_OK

    assert project_root is not None  # guarded above
    try:
        role_seconds = _role_timeout_seconds(project_root, role)
    except _CliError as err:
        stderr_error(err.message)
        return err.exit_code

    fits, _budget, refusal = host_capability.check_wait_budget(role, role_seconds)
    record["role_timeout_seconds"] = role_seconds
    record["fits"] = fits
    record["refusal"] = refusal
    emit_record(record)
    # Reporting a bad fit is a successful report, not a failed command — the
    # gate that refuses a launch lives in `dispatch`, not here.
    return EXIT_OK
