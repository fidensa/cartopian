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
    _writers.add_source_arg(subparser)


def handler(args: argparse.Namespace) -> int:
    phase_id = args.phase_id
    if not _writers.PHASE_ID_RE.match(phase_id):
        _writers.stderr(
            "usage",
            f"--phase-id must match PHASE-NN-slug grammar; got: {phase_id!r}",
        )
        return _writers.EXIT_USAGE

    root, err = _writers.validated_root(args.project_root)
    if err is not None:
        _writers.stderr("usage", err)
        return _writers.EXIT_USAGE
    content, cerr = _writers.resolve_content(args)
    if cerr is not None:
        _writers.stderr("usage", cerr)
        return _writers.EXIT_USAGE
    content, source_id, serr = _writers.apply_source_stamp(args, root, content)
    if serr is not None:
        _writers.stderr(*serr)
        return _writers.EXIT_USAGE if serr[0] == "usage" else _writers.EXIT_FAIL

    extra_details = {"phase_id": phase_id}
    if source_id is not None:
        extra_details["source"] = source_id
    return _writers.perform_write(
        args,
        action="write-phase",
        dest_kind="phase",
        relative_target=f"{phase_id}.md",
        content=content,
        extra_details=extra_details,
    )
