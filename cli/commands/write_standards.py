"""`cartopian write-standards <project-root>` (G3, FR-005, SPEC-01-003).

Structured writer for ``STANDARDS.md``. Front-end over the SPEC-01-002
mediated-write primitive; destination implied by the verb.
"""
import argparse

from cli.commands import _writers


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    _writers.add_content_args(subparser)


def handler(args: argparse.Namespace) -> int:
    return _writers.perform_write(
        args,
        action="write-standards",
        dest_kind="standards",
        relative_target="STANDARDS.md",
    )
