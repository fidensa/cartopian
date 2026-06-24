"""`cartopian render-spec <spec-path>` — emit a deidentified spec rendering.

Read-only. Reads a spec file and emits the deidentified rendering an assignee
(coder) may safely receive: Cartopian PM identifiers stripped, the work-contract
prose intact. The PM inlines the ``deidentified_spec`` field into the
self-contained coder prompt instead of handing over the raw, identified spec
file — so PM identifiers never reach product code. No file writes, moves, or
deletes.
"""
import argparse
from pathlib import Path

from cli import deidentify
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE, stderr_error, stderr_usage


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "spec_path",
        help="Absolute path to the spec file to render deidentified",
    )


def handler(args: argparse.Namespace) -> int:
    raw_path = args.spec_path
    if not Path(raw_path).is_absolute():
        stderr_usage(f"spec_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE

    spec_path = Path(raw_path)
    if not spec_path.is_file():
        stderr_error(f"spec file not found: {raw_path}")
        return EXIT_FAIL

    try:
        content = spec_path.read_text(encoding="utf-8")
    except OSError as exc:
        stderr_error(f"spec file unreadable: {raw_path} — {exc}")
        return EXIT_FAIL

    deidentified, redactions = deidentify.deidentify_spec(content)
    emit_record(
        {
            "action": "render-spec",
            "spec_path": str(spec_path.resolve()),
            "deidentified_spec": deidentified,
            "redactions": redactions,
        }
    )
    return EXIT_OK
