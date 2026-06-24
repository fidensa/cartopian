"""`cartopian write-conventions <project-root>`.

Structured writer for the project ``CONVENTIONS.md``. Front-end over the
mediated-write primitive; destination implied by the verb. Pairs with
``reset-plan``'s conditional conventions reseed at close.
"""
import argparse

from cli.commands import _writers


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    _writers.add_content_args(subparser)


def handler(args: argparse.Namespace) -> int:
    return _writers.perform_write(
        args,
        action="write-conventions",
        dest_kind="conventions",
        relative_target="CONVENTIONS.md",
    )
