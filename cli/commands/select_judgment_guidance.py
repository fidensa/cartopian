"""Activate judgment cards and load the one central failure-signal body."""
import argparse

from cli.emit import emit_record
from cli.judgment_guidance import select_judgment_guidance
from cli.main import EXIT_FAIL, EXIT_OK, stderr_guard


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Activate a judgment card only where the task envelope declares both its "
        "lifecycle boundary and its open non-enforceable failure, then load the "
        "one central failure-signal contract or nothing."
    )
    parser.add_argument(
        "--lifecycle-boundary",
        action="append",
        default=[],
        help="A lifecycle boundary this unit of work actually crosses",
    )
    parser.add_argument(
        "--open-failure-condition",
        action="append",
        default=[],
        help=(
            "A named non-enforceable failure still open for this work. A failure a "
            "deterministic guard has already decided is not open"
        ),
    )


def handler(args: argparse.Namespace) -> int:
    result = select_judgment_guidance(
        {
            "lifecycle_boundaries": args.lifecycle_boundary,
            "open_failure_conditions": args.open_failure_condition,
        }
    )
    emit_record({"action": "select-judgment-guidance", **result})
    if result["outcome"] == "invalid":
        error = result["error"]
        stderr_guard(f"{error['code']}: {error['detail']}")
        return EXIT_FAIL
    return EXIT_OK
