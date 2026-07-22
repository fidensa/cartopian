"""`cartopian delete-report <report-path>`.

Deletes a report file that lives under a registered project's ``reports/``
directory and emits one NDJSON confirmation record. Filename must match the
Cartopian report grammar: ``REPORT-NN-NNN.md`` or
``REPORT-PLAN-NNN[-kebab-slug].md`` (planning-checkpoint reports carry an
operator-authored slug per CONVENTIONS.md).

It also removes the transient companion files that a handoff leaves next to
its report:

- ``<report-path>.status`` — early-crash-detection enrichment written by the
  agent wrappers and consumed by ``cartopian wait-handoff`` (see
  ``wrappers/README.md`` and ``protocol/CONVENTIONS.md`` § Handoffs);
- ``<report-path>.launch.log`` — the diagnostic sidecar ``cartopian
  dispatch`` points the detached child's stdout/stderr at on POSIX (see
  ``cli/commands/dispatch.py``; native-Windows dispatch deliberately uses
  the null device and writes no sidecar, so its absence there is the
  ordinary no-op).

Both are per-handoff transients that must never outlive the handoff they
describe. Because the PM is markdown-only, this command is the PM-sanctioned
hook for clearing those non-markdown files.

Two cleanup moments share this command:

- **report-clear** (default): the report ``.md`` and its transient
  companions are all removed before a (re)handoff reuses the slot — even
  when the report ``.md`` itself is absent (a crash-only handoff that died
  before reporting still leaves ``.status``/``.launch.log`` behind).
- **task-close** (``--status-only``): the report ``.md`` is intentionally
  retained as evidence while the transient companions are removed, so a
  lingering report never keeps them alive.

Absence of either companion file is always a successful no-op.
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
    r"^REPORT-(?:\d{2}-\d{3}|PLAN-\d{3}(?:-[a-z0-9][a-z0-9-]*)?)\.md$"
)


def _stderr(prefix: str, msg: str) -> None:
    sys.stderr.write(f"[{prefix}] {msg}\n")


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "report_path",
        help="Absolute path to the report file under a project's reports/ directory",
    )
    subparser.add_argument(
        "--status-only",
        action="store_true",
        help=(
            "Remove only the transient companion files (<report-path>.status "
            "and <report-path>.launch.log) and leave the report .md in place. "
            "Used at task close, when the report lingers as evidence but its "
            "transient handoff files must not."
        ),
    )


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _remove_transient_companion(companion_path: Path) -> bool:
    """Best-effort removal of a transient handoff companion file — the
    wrapper's ``<report>.status`` or dispatch's ``<report>.launch.log``.

    Returns True when a companion file was present and removed, False
    otherwise. Never raises and never treats absence as an error: both files
    are optional enrichment, so their cleanup degrades gracefully — mirroring
    the fail-open posture of the wrappers/dispatch that write them. A symlink
    at the companion path is left untouched (the same leaf-symlink caution
    the report path itself applies), so cleanup only ever unlinks a real
    companion file in place.
    """
    try:
        if companion_path.is_symlink():
            return False
        if not companion_path.is_file():
            return False
        companion_path.unlink()
        return True
    except OSError:
        return False


def handler(args: argparse.Namespace) -> int:
    raw_path = args.report_path
    # Tolerate callers that build args without the optional flag (e.g. tests or
    # programmatic invocation): default to the report-clear behavior.
    status_only = getattr(args, "status_only", False)

    if not Path(raw_path).is_absolute():
        _stderr("usage", f"report_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE

    report_path = Path(os.path.normpath(raw_path))

    if not REPORT_FILENAME_RE.match(report_path.name):
        _stderr(
            "guard",
            f"report filename does not match REPORT-NN-NNN.md or "
            f"REPORT-PLAN-NNN[-slug].md grammar: {report_path.name}",
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

    # The wrapper early-crash signal lives at "<report-path>.status" — the same
    # path wait_handoff.py derives — and dispatch's diagnostic sidecar at
    # "<report-path>.launch.log" — the same path dispatch.py derives.
    # delete-report owns their removal so neither transient file ever
    # outlives the handoff it describes.
    status_path = Path(str(report_path) + ".status")
    launch_log_path = Path(str(report_path) + ".launch.log")

    if status_only:
        # Task-close cleanup: the report .md is intentionally retained as
        # evidence; only the transient companion files are removed. Missing
        # companions are a successful no-op so close stays idempotent.
        status_deleted = _remove_transient_companion(status_path)
        launch_log_deleted = _remove_transient_companion(launch_log_path)
        emit_record(
            {
                "action": "delete-report",
                "details": {
                    "deleted_path": None,
                    "status_path": str(status_path),
                    "status_deleted": status_deleted,
                    "launch_log_path": str(launch_log_path),
                    "launch_log_deleted": launch_log_deleted,
                    "status_only": True,
                },
            }
        )
        return EXIT_OK

    # Report-clear: remove the transient companions (best-effort; never an
    # error when absent) alongside the report .md, so a reused report slot
    # never carries a stale status file or a prior run's launch log into the
    # next handoff. This runs before the report-exists guard below: a
    # crash-only handoff (died before reporting) leaves only the companions,
    # and they must be cleared even though the delete itself then fails.
    _remove_transient_companion(status_path)
    _remove_transient_companion(launch_log_path)

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
