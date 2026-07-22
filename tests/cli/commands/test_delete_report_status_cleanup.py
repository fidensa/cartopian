"""Tests for delete-report transient-companion cleanup.

Red-before-green coverage for the acceptance assertions:

1. ``cartopian delete-report <report-path>`` removes the companion
   ``<report-path>.status`` when present, and is a no-op when absent.
2. After a handoff is processed/closed through the lifecycle, no
   ``<report>.status`` remains for that task — including when the report
   ``.md`` is intentionally retained as evidence (``--status-only``).
3. The dispatch-owned ``<report-path>.launch.log`` diagnostic sidecar gets
   the same transient lifecycle: removed on report-clear, on ``--status-only``
   task close, and on the report-absent cleanup path (a crash-only handoff
   whose wrapper died before writing any report), never following a leaf
   symlink. RED: delete-report removed the report and ``.status`` but left
   ``.launch.log`` behind as an orphan.

The registry lookup is monkeypatched so the test is hermetic: it never reads
or writes the operator's real ``~/.cartopian`` registry.
"""
import argparse
from pathlib import Path

import pytest

from cli.commands import delete_report

REPORT_NAME = "REPORT-01-008.md"


@pytest.fixture
def project(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    monkeypatch.setattr(delete_report, "registry_path", lambda: tmp_path / "registry")
    monkeypatch.setattr(
        delete_report, "read_registry", lambda _p: [{"path": str(proj)}]
    )
    # Keep emission side-effect free during the test.
    monkeypatch.setattr(delete_report, "emit_record", lambda _record: None)
    return proj


def _report(proj):
    return proj / "reports" / REPORT_NAME


def _status(proj):
    return Path(str(_report(proj)) + ".status")


def _launch_log(proj):
    return Path(str(_report(proj)) + ".launch.log")


def _run(report_path, status_only=False):
    args = argparse.Namespace(report_path=str(report_path), status_only=status_only)
    return delete_report.handler(args)


def _write(path, text="x"):
    path.write_text(text, encoding="utf-8")


def test_delete_report_removes_companion_status(project):
    _write(_report(project), "# REPORT-01-008\n")
    _write(_status(project), "state=exited\nexit_code=0\nreason=clean\n")

    rc = _run(_report(project))

    assert rc == delete_report.EXIT_OK
    assert not _report(project).exists()
    assert not _status(project).exists(), "companion .status must be removed"


def test_delete_report_status_absent_is_noop(project):
    _write(_report(project), "# REPORT-01-008\n")

    rc = _run(_report(project))

    assert rc == delete_report.EXIT_OK
    assert not _report(project).exists()
    assert not _status(project).exists()


def test_status_only_retains_report_md(project):
    _write(_report(project), "# REPORT-01-008\n")
    _write(_status(project), "state=exited\nexit_code=1\nreason=error\n")

    rc = _run(_report(project), status_only=True)

    assert rc == delete_report.EXIT_OK
    assert _report(project).exists(), "report .md must be retained at task close"
    assert not _status(project).exists(), "companion .status must not outlive close"


def test_status_only_absent_is_noop(project):
    _write(_report(project), "# REPORT-01-008\n")

    rc = _run(_report(project), status_only=True)

    assert rc == delete_report.EXIT_OK
    assert _report(project).exists()
    assert not _status(project).exists()


def test_delete_report_removes_companion_launch_log(project):
    _write(_report(project), "# REPORT-01-008\n")
    _write(_launch_log(project), "wrapper diagnostics\n")

    rc = _run(_report(project))

    assert rc == delete_report.EXIT_OK
    assert not _report(project).exists()
    assert not _launch_log(project).exists(), (
        "companion .launch.log must be removed on report-clear"
    )


def test_status_only_removes_launch_log_and_retains_report(project):
    _write(_report(project), "# REPORT-01-008\n")
    _write(_status(project), "state=exited\nexit_code=0\nreason=clean\n")
    _write(_launch_log(project), "wrapper diagnostics\n")

    rc = _run(_report(project), status_only=True)

    assert rc == delete_report.EXIT_OK
    assert _report(project).exists(), "report .md must be retained at task close"
    assert not _status(project).exists()
    assert not _launch_log(project).exists(), (
        "companion .launch.log must not outlive task close"
    )


def test_launch_log_absent_is_noop(project):
    _write(_report(project), "# REPORT-01-008\n")

    assert _run(_report(project), status_only=True) == delete_report.EXIT_OK
    assert _report(project).exists()

    _write(_report(project), "# REPORT-01-008\n")
    assert _run(_report(project)) == delete_report.EXIT_OK
    assert not _report(project).exists()


def test_report_absent_cleanup_still_clears_transient_companions(project):
    # Crash-only handoff: the wrapper (or the launch itself) died before any
    # report was written, leaving only the transient sidecars. Report-clear on
    # the empty slot still fails on the missing .md, but the sidecars must not
    # be orphaned by that failure — otherwise a reused slot inherits them.
    _write(_status(project), "state=exited\nexit_code=1\nreason=error\n")
    _write(_launch_log(project), "Traceback (most recent call last): boom\n")

    rc = _run(_report(project))

    assert rc == delete_report.EXIT_FAIL
    assert not _status(project).exists(), (
        "crash-only .status must be cleared even when the report is absent"
    )
    assert not _launch_log(project).exists(), (
        "crash-only .launch.log must be cleared even when the report is absent"
    )


def test_launch_log_symlink_left_untouched(project):
    # Same leaf-symlink caution as the .status companion: cleanup only ever
    # unlinks a real sidecar in place, never a link (whose target may be an
    # arbitrary file outside the project).
    _write(_report(project), "# REPORT-01-008\n")
    target = project / "elsewhere.log"
    _write(target, "not a sidecar\n")
    _launch_log(project).symlink_to(target)

    rc = _run(_report(project))

    assert rc == delete_report.EXIT_OK
    assert not _report(project).exists()
    assert _launch_log(project).is_symlink(), (
        "a symlink at the .launch.log path is not a real sidecar and must "
        "be left untouched"
    )
    assert target.exists()


def test_lifecycle_close_leaves_no_status(project):
    # A wrapper-launched handoff produced both the report and its status file.
    _write(_report(project), "# REPORT-01-008\nStatus: complete\n")
    _write(_status(project), "state=exited\nexit_code=0\nreason=clean\n")

    # Task close: the report .md is retained as evidence, the status cleared.
    assert _run(_report(project), status_only=True) == delete_report.EXIT_OK
    assert _report(project).exists()
    assert not _status(project).exists()

    # Idempotent: a second close is a clean no-op.
    assert _run(_report(project), status_only=True) == delete_report.EXIT_OK
    assert not _status(project).exists()
