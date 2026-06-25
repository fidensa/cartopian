"""Tests for delete-report companion status-file cleanup.

Red-before-green coverage for the two acceptance assertions:

1. ``cartopian delete-report <report-path>`` removes the companion
   ``<report-path>.status`` when present, and is a no-op when absent.
2. After a handoff is processed/closed through the lifecycle, no
   ``<report>.status`` remains for that task — including when the report
   ``.md`` is intentionally retained as evidence (``--status-only``).

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
