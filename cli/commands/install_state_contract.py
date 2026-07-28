"""`cartopian install-state-contract` machine-readable contract projection."""
from __future__ import annotations

import argparse

from cli.emit import emit_record
from cli.install_state import contract_projection
from cli.main import EXIT_OK


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.description = (
        "Emit the authoritative install/update schema, vocabularies, "
        "transitions, and stable/internal field boundary."
    )


def handler(_args: argparse.Namespace) -> int:
    emit_record(contract_projection())
    return EXIT_OK
