"""`cartopian delete-report <report-path>` (FR-005, SPEC-01-001).

Deletes a report file that lives under a registered project's ``reports/``
directory and emits one NDJSON confirmation record. Filename must match the
Cartopian report grammar: ``REPORT-NN-NNN.md`` or ``REPORT-PLAN-NNN.md``.
"""
import argparse
import os
import re
import sys
from pathlib import Path

from cli.commands._registry import (
    MalformedRegistry,
    read_registry,
    registry_path,
)
from cli.emit import emit_record
from cli.main import EXIT_ENV, EXIT_FAIL, EXIT_OK, EXIT_USAGE

REPORT_FILENAME_RE = re.compile(
    r"^REPORT-(?:\d{2}-\d{3}|PLAN-\d{3})\.md$"
)


def _stderr(prefix: str, msg: str) -> None:
    sys.stderr.write(f"[{prefix}] {msg}\n")


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "report_path",
        help="Absolute path to the report file under a project's reports/ directory",
    )


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def handler(args: argparse.Namespace) -> int:
    raw_path = args.report_path
    if not Path(raw_path).is_absolute():
        _stderr("usage", f"report_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE

    report_path = Path(os.path.normpath(raw_path))

    if not REPORT_FILENAME_RE.match(report_path.name):
        _stderr(
            "guard",
            f"report filename does not match REPORT-NN-NNN.md or "
            f"REPORT-PLAN-NNN.md grammar: {report_path.name}",
        )
        return EXIT_FAIL

    # Reject leaf symlinks outright so the path we validate is the path we
    # delete. Without this, an outside symlink whose target resolves into a
    # registered project would pass the under-check and unlink only the
    # symlink while the real in-project file survived untouched. This also
    # rejects in-project symlinks pointing at outside files (which aren't
    # real prompts/reports).
    if report_path.is_symlink():
        _stderr("guard", f"report_path must not be a symlink: {report_path}")
        return EXIT_FAIL

    try:
        entries = read_registry(registry_path())
    except MalformedRegistry as exc:
        _stderr("error", f"registry file is malformed: {exc}")
        return EXIT_ENV

    # Under-check uses the canonical (realpath) form of both sides so that the
    # macOS /var → /private/var quirk does not split equivalent paths apart,
    # and so that any parent-dir symlinks are normalized. Leaf-symlink
    # confusion is already excluded by the is_symlink() guard above.
    report_canonical = Path(os.path.realpath(report_path))

    matched = None
    for entry in entries:
        project_root = Path(os.path.realpath(entry["path"]))
        reports_dir = project_root / "reports"
        if _is_under(report_canonical, reports_dir):
            matched = entry
            break

    if matched is None:
        _stderr(
            "guard",
            f"report path is not under any registered project's reports/ "
            f"directory: {report_path}",
        )
        return EXIT_FAIL

    if not report_path.is_file():
        _stderr("guard", f"report file not found: {report_path}")
        return EXIT_FAIL

    try:
        report_path.unlink()
    except OSError as exc:
        _stderr("error", f"failed to delete report: {report_path} — {exc}")
        return EXIT_FAIL

    emit_record(
        {
            "action": "delete-report",
            "details": {"deleted_path": str(report_path)},
        }
    )
    return EXIT_OK
