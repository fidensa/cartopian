"""`cartopian write-phase <project-root> --phase-id PHASE-NN-slug`.

Structured writer for phase files ``phases/PHASE-NN-slug.md``. The filename
is derived from the validated ``--phase-id`` (the PM supplies an id, not a
path); the destination subtree is the allowlisted ``phase`` dest_kind.
"""
import argparse

from cli.commands import _writers


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    _writers.add_content_args(subparser)
    subparser.add_argument(
        "--phase-id",
        required=True,
        help="Phase id matching the grammar PHASE-NN-slug, e.g. PHASE-foundation",
    )


def handler(args: argparse.Namespace) -> int:
    phase_id = args.phase_id
    if not _writers.PHASE_ID_RE.match(phase_id):
        _writers.stderr(
            "usage",
            f"--phase-id must match PHASE-NN-slug grammar; got: {phase_id!r}",
        )
        return _writers.EXIT_USAGE
    return _writers.perform_write(
        args,
        action="write-phase",
        dest_kind="phase",
        relative_target=f"{phase_id}.md",
        extra_details={"phase_id": phase_id},
    )
