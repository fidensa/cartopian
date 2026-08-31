"""`cartopian write-continuity <project-root> --content <summary>`.

Plan close's one write of the project-root ``CONTINUITY.md`` summary: a plain
UTF-8 Markdown body the PM authored, rendered through the mediated-write
primitive (``continuity`` dest_kind → the allowlisted root file
``CONTINUITY.md``). Re-issuing it replaces the summary in place.

The command composes nothing. There is no mode, no plan binding, no row
grammar, no index, and no projection: the body the operator's closeout wrote
is the artifact, byte for byte — the supplied UTF-8 bytes are written as
given, and not even a trailing newline is added. It reads no other artifact,
so writing the summary neither depends on nor alters ordinary plan-archive
behavior, and a closeout that never runs it simply leaves the project without
a summary.
"""
import argparse

from cli import continuity
from cli.commands import _writers
from cli.mediated_write import GuardRefusal, mediated_write


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
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8")
        except UnicodeDecodeError:
            _writers.stderr("usage", "project summary must be valid UTF-8")
            return _writers.EXIT_USAGE
    if not content.strip():
        _writers.stderr("usage", "project summary must be non-empty")
        return _writers.EXIT_USAGE

    existed = continuity.artifact_path(root).exists()
    try:
        result = mediated_write(
            root,
            continuity.DEST_KIND,
            continuity.CONTINUITY_BASENAME,
            content,
        )
    except GuardRefusal as refusal:
        _writers.stderr("guard", f"{refusal.rule}: {refusal.detail}")
        return _writers.EXIT_FAIL

    _writers.emit_record({
        "action": "write-continuity",
        "details": {
            "dest_kind": continuity.DEST_KIND,
            "path": result["path"],
            "bytes": result["bytes"],
            "replaced": existed,
        },
    })
    return _writers.EXIT_OK
