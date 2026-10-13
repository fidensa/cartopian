"""`cartopian write-continuity <project-root> --content <summary>`.

Plan close's one write of the project-root ``CONTINUITY.md`` summary: a plain
UTF-8 Markdown body the PM authored, rendered through the mediated-write
primitive (``continuity`` dest_kind → the allowlisted root file
``CONTINUITY.md``). Re-issuing it replaces the summary in place.

The command composes nothing. There is no mode, no plan binding, no row
grammar, no index, and no projection: the body the operator's closeout wrote
is the artifact, byte for byte — the supplied UTF-8 bytes are written as
given, and not even a trailing newline is added.

The summary belongs to plan close alone, so the write is refused until
``close-audit`` reports the plan closable — the verdict ``close plan``
requires before it reaches the summary stage — and the plan is complete as
``next-action`` defines it, or the operator has closed it early
(``Closes plan: early``). A session boundary is not a plan close: an open
plan, an unstarted one, one with a phase whose tasks were never generated, and
a project with no plan at all are all refused, so the summary can never stand
in for a session handoff. Beyond that readiness check the command reads no
other artifact, and writing the summary neither depends on nor alters ordinary
plan-archive behavior.
"""
import argparse
from pathlib import Path
from typing import Optional

from cli import continuity
from cli.commands import _writers, close_audit, next_action
from cli.commands.resolve_config import _CliError
from cli.mediated_write import GuardRefusal, mediated_write

_NOT_A_HANDOFF = (
    "the project summary is written only at plan close (close plan Stage 3b), "
    "never as a session handoff; to end a session, refresh STATE.md with "
    "write-state and stop (run task Stage 8)"
)


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.description = (
        "Write the plan-close project summary (close plan Stage 3b, after "
        "close-audit and before reset-plan). Refused until the plan is "
        "complete and closable. Never a session handoff: ending a session is "
        "write_state alone, and no document carries context forward."
    )
    _writers.add_content_args(subparser)


def _plan_close_refusal(root: Path) -> Optional[str]:
    """Return why the plan is not at close, or ``None`` when it is."""
    try:
        record = close_audit.audit_project(root)
    except _CliError as err:
        return err.message
    if record["closable"] is None:
        return "this project has no plan to close"
    if not record["closable"]:
        reasons = record["blocking_reasons"]
        return (
            f"close-audit reports {len(reasons)} blocking reason(s), first: "
            f"{reasons[0]}"
        )
    gap = next_action.completion_gap(root)
    if gap is not None and record.get("early_close") is None:
        return f"the plan is not complete: {gap}"
    return None


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

    refusal = _plan_close_refusal(root)
    if refusal is not None:
        _writers.stderr(
            "guard", f"continuity-plan-not-closable: {refusal} — {_NOT_A_HANDOFF}"
        )
        return _writers.EXIT_FAIL

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
