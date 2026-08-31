"""`cartopian read-continuity <project-root>`.

The **one** surface that reads the project-root ``CONTINUITY.md`` summary, and
it runs only when the operator explicitly asks for the summary. No startup,
status, state-composition, assignment, or task-execution path invokes it, and
no other command opens the artifact.

Absence is a success: the record carries ``present: false`` and no body, the
command exits 0, and the session gains no context from it. A present artifact
that is unsafe to read as text (a symlink or other non-regular file, or bytes
that are not UTF-8) is refused by name — a refusal that can only ever be
reached through this explicit request.
"""
import argparse

from cli import continuity
from cli.commands import _writers
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE, stderr_guard, stderr_usage


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "project_root",
        help="Absolute path to the Cartopian project root",
    )


def handler(args: argparse.Namespace) -> int:
    root, err = _writers.validated_root(args.project_root)
    if err is not None:
        stderr_usage(err)
        return EXIT_USAGE

    try:
        summary = continuity.read_summary(root)
    except continuity.ContinuityRefusal as refusal:
        stderr_guard(f"{refusal.rule}: {refusal.detail}")
        return EXIT_FAIL

    record = {
        "path": continuity.CONTINUITY_BASENAME,
        "present": summary is not None,
    }
    # An absent summary contributes nothing: the record gains no body key at
    # all rather than a null one, so "no summary" cannot be read as content.
    if summary is not None:
        record["content"] = summary
    emit_record(record)
    return EXIT_OK
