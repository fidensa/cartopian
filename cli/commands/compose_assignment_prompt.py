"""`cartopian compose-assignment-prompt <task-path> --role <role>`.

Deterministically composes one task's assignee (coder) assignment prompt from
authoritative project inputs and emits a single NDJSON record carrying the
audience-scoped prompt, the machine-readable trace receipt, the content
identity binding the two, per-section size measurements, and validation
findings. Read-only; no file writes. The mediated writer consumes the result
via ``cartopian write-prompt --composed-file``.
"""
import argparse
from pathlib import Path

from cli import prompt_composer
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE, stderr_guard, stderr_usage


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Compose the audience-scoped assignee prompt for one task handoff from "
        "authoritative inputs (task, role, spec projection, applicable "
        "standards, selector projections, source guidance, report skeleton) "
        "plus a bound machine trace receipt. Fails closed on unresolvable "
        "inputs or validation findings; write the result with "
        "`cartopian write-prompt --composed-file`."
    )
    parser.add_argument(
        "task_path",
        help="Absolute path to the task file",
    )
    parser.add_argument(
        "--role",
        required=True,
        help="Declared role the assignment addresses",
    )


def handler(args: argparse.Namespace) -> int:
    if not Path(args.task_path).is_absolute():
        stderr_usage(f"task_path must be an absolute path; got: {args.task_path}")
        return EXIT_USAGE
    try:
        record = prompt_composer.compose(Path(args.task_path), args.role)
    except prompt_composer.ComposeRefusal as refusal:
        stderr_guard(f"{refusal.code}: {refusal.detail}")
        return EXIT_FAIL
    emit_record({"action": "compose-assignment-prompt", **record})
    if record["outcome"] != "composed":
        for finding in record["findings"]:
            if finding["severity"] == "fail":
                stderr_guard(
                    f"{finding['code']}: {finding['detail']} — "
                    f"{finding['recovery']}"
                )
        return EXIT_FAIL
    return EXIT_OK
