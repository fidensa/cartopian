"""`cartopian write-state <project-root> --content ...`.

Structured writer for ``STATE.md``. ``compose-state`` stays the renderer —
this is the missing *writer* that persists its ``rendered_body`` (or a
closeout-authored no-plan body). The 5KB ceiling from the close/run-task
skills is enforced here: STATE.md is a small navigational pointer, not a
content store, so a body over 5 KiB is refused fail-closed.

Destination implied by the verb (``state`` dest_kind → project root); the PM
never supplies a path.
"""
import argparse

from cli.commands import _writers

# STATE.md ceiling — "under 5KB" per skills/close-plan.md and skills/run-task.md.
STATE_MAX_BYTES = 5 * 1024


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    _writers.add_content_args(subparser)


def handler(args: argparse.Namespace) -> int:
    root, err = _writers.validated_root(args.project_root)
    if err is not None:
        _writers.stderr("usage", err)
        return _writers.EXIT_USAGE

    content, cerr = _writers.resolve_content(args)
    if cerr is not None:
        _writers.stderr("usage", cerr)
        return _writers.EXIT_USAGE

    data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
    if len(data) > STATE_MAX_BYTES:
        _writers.stderr(
            "guard",
            f"state-too-large: STATE.md body is {len(data)} bytes; "
            f"ceiling is {STATE_MAX_BYTES} bytes (5KB)",
        )
        return _writers.EXIT_FAIL

    return _writers.perform_write(
        args,
        action="write-state",
        dest_kind="state",
        relative_target="STATE.md",
        content=content,
        extra_details={"ceiling_bytes": STATE_MAX_BYTES},
    )
