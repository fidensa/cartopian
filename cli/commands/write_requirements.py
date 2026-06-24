"""`cartopian write-requirements <project-root>`.

Structured writer for the project ``REQUIREMENTS.md`` root artifact. A thin
front-end over the mediated-write primitive — the destination is implied by
the verb (``requirements`` dest_kind → project root), never a free-form path.
Re-issuing overwrites in place.
"""
import argparse

from cli.commands import _writers


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    _writers.add_content_args(subparser)


def handler(args: argparse.Namespace) -> int:
    return _writers.perform_write(
        args,
        action="write-requirements",
        dest_kind="requirements",
        relative_target="REQUIREMENTS.md",
    )
