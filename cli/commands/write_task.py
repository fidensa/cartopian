"""`cartopian write-task <project-root> --task-id TASK-NN-NNN --slug ...`.

Structured writer for task files. New tasks land in ``tasks/open/`` —
``TASK-NN-NNN-slug.md`` — matching the lifecycle entry point (``move-task``
advances them from there). The PM supplies the id + slug, not a path; the
destination subtree is the allowlisted ``task`` dest_kind.
"""
import argparse

from cli.commands import _writers


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    _writers.add_content_args(subparser)
    subparser.add_argument(
        "--task-id",
        required=True,
        help="Task id, e.g. TASK-NN-NNN",
    )
    subparser.add_argument(
        "--slug",
        required=True,
        help="Kebab-case slug for the filename (TASK-NN-NNN-<slug>.md)",
    )


def handler(args: argparse.Namespace) -> int:
    task_id = args.task_id
    slug = args.slug
    if not _writers.TASK_ID_RE.match(task_id):
        _writers.stderr(
            "usage",
            f"--task-id must match TASK-NN-NNN grammar; got: {task_id!r}",
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
        action="write-task",
        dest_kind="task",
        relative_target=f"open/{task_id}-{slug}.md",
        extra_details={"task_id": task_id, "slug": slug},
    )
