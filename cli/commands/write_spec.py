"""`cartopian write-spec <project-root> --spec-id SPEC-NN-NNN`.

Structured writer for spec files ``specs/SPEC-NN-NNN.md``. The PM
supplies the id, not a path; the destination subtree is the
allowlisted ``spec`` dest_kind.
"""
import argparse
import os

from cli.commands import _writers


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    _writers.add_content_args(subparser)
    subparser.add_argument(
        "--spec-id",
        required=True,
        help="Spec id, e.g. SPEC-NN-NNN",
    )
    _writers.add_source_arg(subparser)


def handler(args: argparse.Namespace) -> int:
    spec_id = args.spec_id
    if not _writers.SPEC_ID_RE.match(spec_id):
        _writers.stderr(
            "usage",
            f"--spec-id must match SPEC-NN-NNN grammar; got: {spec_id!r}",
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

    filename = f"{spec_id}.md"
    matches = _writers.identifier_files(root / "specs", spec_id)
    if len(matches) > 1:
        _writers.stderr(
            "guard",
            f"spec-id-collision: {spec_id} resolves to multiple files: "
            + ", ".join(str(path) for path in matches),
        )
        return _writers.EXIT_FAIL
    renamed_from = None
    if matches and matches[0].name != filename:
        renamed_from = matches[0]
        try:
            os.rename(renamed_from, renamed_from.parent / filename)
        except OSError as exc:
            _writers.stderr("error", f"rename failed: {exc}")
            return _writers.EXIT_FAIL

    extra_details = {"spec_id": spec_id}
    if source_id is not None:
        extra_details["source"] = source_id
    code = _writers.perform_write(
        args,
        action="write-spec",
        dest_kind="spec",
        relative_target=f"{spec_id}.md",
        content=content,
        extra_details=extra_details,
    )
    if code != _writers.EXIT_OK and renamed_from is not None:
        try:
            os.rename(renamed_from.parent / filename, renamed_from)
        except OSError:
            pass
    return code
