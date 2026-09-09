"""`cartopian write-requirements <project-root>`.

Structured writer for the project ``REQUIREMENTS.md`` root artifact. A thin
front-end over the mediated-write primitive — the destination is implied by
the verb (``requirements`` dest_kind → project root), never a free-form path.
Re-issuing overwrites in place.

Locking requirements binds the planning confirmation exchange (plan section
4.6/4.7): the writer resolves, from host intake adapter state, the pair the
operator answered last in the bound capture session and records it in the
project binding. Pass ``--handle`` only when more than one session is bound.
"""
import argparse

from cli.commands import _writers


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    _writers.add_content_args(subparser)
    _writers.add_handle_arg(subparser)


def handler(args: argparse.Namespace) -> int:
    root, err = _writers.validated_root(args.project_root)
    if err is not None:
        _writers.stderr("usage", err)
        return _writers.EXIT_USAGE
    # Requirements lock binds the confirmation exchange: the intent summary
    # the operator answered and their confirming or correcting words, taken
    # from adapter state. No further confirmation is asked.
    lock, code = _writers.lock_confirmation(root, getattr(args, "handle", None))
    if code is not None:
        return code
    return _writers.perform_write(
        args,
        action="write-requirements",
        dest_kind="requirements",
        relative_target="REQUIREMENTS.md",
        request_gate_satisfied=lock is not None,
        post_write=lambda project_root, details: _writers.bind_lock_confirmation(
            project_root, lock, details
        ),
    )
