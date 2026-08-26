"""`cartopian validate-delivery <project-root>` — the domain-neutral delivery gate.

Detail on demand for the compact delivery status that ``compose-state`` and
``next-action`` carry. The command reads the plan surface and never crosses an
external-action boundary: it validates a delivery record, it does not deliver.
"""
import argparse
from pathlib import Path

from cli import delivery_contract
from cli.commands.resolve_config import _CliError, _load_toml, _require_project_keys
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


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.description = (
        "Validate the plan's delivery contract — owner, target, acceptance evidence, "
        "success signals, contingency, immediate verification, and follow-up timing — "
        "and return the artifact, outcome, and follow-up states with ordered findings. "
        "It validates a delivery record; it never performs or authorizes a delivery."
    )
    subparser.add_argument(
        "project_root",
        help="Absolute path to the Cartopian project directory",
    )


def handler(args: argparse.Namespace) -> int:
    raw_path = args.project_root
    if not Path(raw_path).is_absolute():
        stderr_usage(f"project_root must be an absolute path; got: {raw_path}")
        return EXIT_USAGE
    try:
        project_root = Path(raw_path).resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError):
        stderr_error(f"project path does not exist: {raw_path}")
        return EXIT_FAIL

    project_toml = project_root / "cartopian.toml"
    try:
        project_cfg = _load_toml(project_toml, "project config")
        if project_cfg is None:
            raise _CliError(
                EXIT_ENV, "error", f"project config not found: {project_toml}"
            )
        project_id = _require_project_keys(project_cfg, project_toml)[0]
    except _CliError as err:
        stderr_error(err.message)
        return err.exit_code

    try:
        result = delivery_contract.validate_plan(project_root)
    except delivery_contract.DeliveryContractError as exc:
        stderr_guard(str(exc))
        return EXIT_FAIL

    emit_record(
        {
            "action": "validate-delivery",
            "project_id": project_id,
            "project_path": str(project_root),
            **result,
        }
    )
    if result["gate"] != "pass":
        first = result["ordered_findings"][0]
        stderr_guard(f"{first['code']}: {first['detail']}")
        return EXIT_FAIL
    return EXIT_OK
