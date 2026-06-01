"""`cartopian write-plan <project-root>` (G2, FR-005, SPEC-01-003).

Structured writer for ``IMPLEMENTATION_PLAN.md``. Front-end over the
SPEC-01-002 mediated-write primitive; destination implied by the verb.
"""
import argparse

from cli.commands import _writers


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    _writers.add_content_args(subparser)


def handler(args: argparse.Namespace) -> int:
    return _writers.perform_write(
        args,
        action="write-plan",
        dest_kind="plan",
        relative_target="IMPLEMENTATION_PLAN.md",
    )
