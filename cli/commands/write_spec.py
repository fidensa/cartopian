"""`cartopian write-spec <project-root> --spec-id SPEC-NN-NNN --slug ...` (G6, FR-005, SPEC-01-003).

Structured writer for spec files ``specs/SPEC-NN-NNN-slug.md``. The PM
supplies the id + slug, not a path; the destination subtree is the
allowlisted ``spec`` dest_kind.
"""
import argparse

from cli.commands import _writers


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    _writers.add_content_args(subparser)
    subparser.add_argument(
        "--spec-id",
        required=True,
        help="Spec id, e.g. SPEC-01-001 (grammar SPEC-NN-NNN)",
    )
    subparser.add_argument(
        "--slug",
        required=True,
        help="Kebab-case slug for the filename (SPEC-NN-NNN-<slug>.md)",
    )


def handler(args: argparse.Namespace) -> int:
    spec_id = args.spec_id
    slug = args.slug
    if not _writers.SPEC_ID_RE.match(spec_id):
        _writers.stderr(
            "usage",
            f"--spec-id must match SPEC-NN-NNN grammar; got: {spec_id!r}",
        )
        return _writers.EXIT_USAGE
    if not _writers.SLUG_RE.match(slug):
        _writers.stderr(
            "usage",
            f"--slug must be kebab-case [a-z0-9][a-z0-9-]*; got: {slug!r}",
        )
        return _writers.EXIT_USAGE
    return _writers.perform_write(
        args,
        action="write-spec",
        dest_kind="spec",
        relative_target=f"{spec_id}-{slug}.md",
        extra_details={"spec_id": spec_id, "slug": slug},
    )
