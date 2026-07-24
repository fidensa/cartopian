"""Command-line entry point for deterministic repository evaluations."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from evaluations.categories import default_registry
from evaluations.runner import (
    DEFAULT_CASES_DIRECTORY,
    REPOSITORY_ROOT,
    SelectionError,
    ValidationError,
    discover_cases,
    render_human,
    render_machine,
    run_cases,
    select_cases,
)

EXIT_MATCH = 0
EXIT_MISMATCH = 1
EXIT_INVALID = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m evaluations",
        description="Run free, deterministic repository evaluations.",
    )
    parser.add_argument(
        "--category",
        action="append",
        default=[],
        help="select a registered category; repeat to select more than one",
    )
    parser.add_argument(
        "--case",
        dest="identifiers",
        action="append",
        default=[],
        help="select a case identifier; repeat to select more than one",
    )
    parser.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="result rendering format (default: human)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    registry = default_registry()
    try:
        cases = discover_cases(
            REPOSITORY_ROOT,
            DEFAULT_CASES_DIRECTORY,
            registry,
        )
        selected = select_cases(
            cases,
            categories=args.category,
            identifiers=args.identifiers,
        )
    except (ValidationError, SelectionError) as exc:
        sys.stderr.write(exc.render())
        return EXIT_INVALID

    aggregate = run_cases(selected, registry, REPOSITORY_ROOT)
    if args.format == "json":
        sys.stdout.write(render_machine(aggregate))
    else:
        sys.stdout.write(render_human(aggregate))
    return EXIT_MATCH if aggregate.mismatched == 0 else EXIT_MISMATCH


if __name__ == "__main__":
    raise SystemExit(main())
