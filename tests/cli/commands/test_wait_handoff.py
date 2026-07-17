"""Tests for `cartopian wait-handoff`.

Exercises the read-only polling state machine with an injected clock: the
report file is the authoritative completion signal, the optional
``<report-path>.status`` wrapper file is an early-exit crash signal, and the
loop is bounded by ``min(--max-block, configured timeout)`` — emitting the
``done`` / ``failed-to-parse`` / ``failed`` / ``timeout`` / ``still-running``
status flags per STANDARDS.md § Wait Command Standards.
"""
import json
from pathlib import Path

import pytest

from cli.commands import wait_handoff
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE, build_parser


@pytest.fixture
def fake_clock(monkeypatch):
    """Patch wait_handoff's monotonic clock so sleeps advance it deterministically."""
    state = {"t": 0.0}
    monkeypatch.setattr(wait_handoff.time, "monotonic", lambda: state["t"])

    def fake_sleep(seconds):
        state["t"] += seconds

    monkeypatch.setattr(wait_handoff.time, "sleep", fake_sleep)
    return state


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


CONFIG_BODY = """[project]
work_roots = ["tool-repo"]

[roles]
coder = "Implements tasks per spec."

[handoffs.coder]
agent = "codex"
auto_start_tasks = true
timeout = "{timeout}"
"""

CONFIG_NO_TIMEOUT = """[project]
work_roots = ["tool-repo"]

[roles]
coder = "Implements tasks per spec."

[handoffs.coder]
agent = "codex"
auto_start_tasks = true
"""

TASK_BODY = """# TASK-01-003: Implement wait-handoff CLI command

Phase: PHASE-01-stdio-wait-primitives
Plan ref: P01-BUILD-003
Work root: tool-repo
Assignee: coder
"""

ACCEPTED_REPORT = "\n".join(
    [
        "# REPORT-01-003",
        "",
        "Status: complete",
        "",
        "## Identity",
        "",
        "- Task ID: TASK-01-003",
        "- Prompt path: /tmp/PROMPT-01-003.md",
        "- Task path: /tmp/TASK-01-003-wait-handoff.md",
        "- Work root: tool-repo",
        "",
        "## Files changed",
        "",
        "- cli/commands/wait_handoff.py — added",
        "",
        "## Test evidence",
        "",
        "- Red test evidence: targeted red",
        "- Green test evidence: targeted green",
        "",
        "## Commit / PR",
        "",
        "- Commit SHA: n/a",
        "- PR URL: n/a",
        "",
        "## Remaining risks",
        "",
        "None.",
        "",
        "## Ready for review",
        "",
        "yes",
        "",
    ]
)

# Present but structurally invalid: no Status line, no variant heading.
INVALID_REPORT = "# REPORT-01-003\n\nsome freeform notes, not a real report\n"


def _make_project(tmp_path: Path, config_body: str = None) -> Path:
    """Scaffold a minimal cartopian project and return the task path."""
    project = tmp_path / "proj"
    body = config_body if config_body is not None else CONFIG_BODY.format(timeout="60m")
    _write(project / "cartopian.toml", body)
    _write(project / "IMPLEMENTATION_PLAN.md", "# Plan\n")
    _write(project / "phases" / "PHASE-01-stdio-wait-primitives.md", "# Phase\n")
    task_path = project / "tasks" / "in-progress" / "TASK-01-003-wait-handoff.md"
    _write(task_path, TASK_BODY)
    return task_path


def _run(task_path: Path, *, role="coder", max_block="30s"):
    parser = build_parser()
    args = parser.parse_args(
        ["wait-handoff", str(task_path), "--role", role, "--max-block", max_block]
    )
    return args._handler(args)


def _report_path(task_path: Path) -> Path:
    return task_path.parents[2] / "reports" / "REPORT-01-003.md"


# --- duration parsing -------------------------------------------------------


def test_parse_duration_accepts_units():
    assert wait_handoff._parse_duration("30s") == 30
    assert wait_handoff._parse_duration("2m") == 120
    assert wait_handoff._parse_duration("1h") == 3600
    assert wait_handoff._parse_duration("1d") == 86400


def test_parse_duration_rejects_bad_input():
    assert wait_handoff._parse_duration("0s") is None
    assert wait_handoff._parse_duration("-5m") is None
    assert wait_handoff._parse_duration("abc") is None
    assert wait_handoff._parse_duration("") is None
    assert wait_handoff._parse_duration("10") is None


# --- report path parity with handoff-packet ---------------------------------


def test_report_path_matches_handoff_packet_logic(tmp_path):
    task_path = _make_project(tmp_path).resolve()
    from cli.commands import handoff_packet

    project_root = handoff_packet._find_project_root(task_path)
    task_id = handoff_packet._extract_task_id(task_path)
    expected = handoff_packet._expected_report_path(project_root, task_id)
    assert expected == _report_path(task_path).resolve()
    assert expected.name == "REPORT-01-003.md"


# --- terminal outcomes ------------------------------------------------------


def test_done_when_report_accepted(tmp_path, capsys, fake_clock):
    task_path = _make_project(tmp_path)
    _write(_report_path(task_path), ACCEPTED_REPORT)

    exit_code = _run(task_path)
    record = json.loads(capsys.readouterr().out.strip())
    assert exit_code == EXIT_OK
    assert record["status"] == "done"
    assert record["report_verdict"] == "accepted"
    assert record["task_id"] == "TASK-01-003"
    assert record["report_path"].endswith("reports/REPORT-01-003.md")


def test_failed_to_parse_when_report_invalid(tmp_path, capsys, fake_clock):
    task_path = _make_project(tmp_path)
    _write(_report_path(task_path), INVALID_REPORT)

    exit_code = _run(task_path)
    record = json.loads(capsys.readouterr().out.strip())
    assert exit_code == EXIT_FAIL
    assert record["status"] == "failed-to-parse"
    assert record["report_verdict"] == "failed-to-parse"


def test_failed_when_status_file_exited_nonzero(tmp_path, capsys, fake_clock):
    task_path = _make_project(tmp_path)
    status_path = Path(str(_report_path(task_path).resolve()) + ".status")
    _write(status_path, "state=exited\nexit_code=1\npid=4242\n")

    exit_code = _run(task_path)
    record = json.loads(capsys.readouterr().out.strip())
    assert exit_code == EXIT_FAIL
    assert record["status"] == "failed"
    assert record["exit_code"] == 1


def test_failed_when_exited_clean_but_no_report(tmp_path, capsys, fake_clock):
    """A clean exit that produced no report is terminal, not still-running.

    The assignee process is gone (state=exited), so no report will ever appear.
    wait-handoff must report `failed` promptly rather than blocking to the
    deadline — this is the exited-without-report zombie (e.g. a reviewer that
    wrote its REVIEW file but never wrote the REPORT the PM waits on).
    """
    task_path = _make_project(tmp_path, config_body=CONFIG_NO_TIMEOUT)
    status_path = Path(str(_report_path(task_path).resolve()) + ".status")
    _write(status_path, "state=exited\nexit_code=0\nreason=clean\npid=4242\n")

    exit_code = _run(task_path, max_block="30s")
    record = json.loads(capsys.readouterr().out.strip())
    assert exit_code == EXIT_FAIL
    assert record["status"] == "failed"
    assert record["exit_code"] == 0
    assert record["report_verdict"] is None
    # Failed immediately on the clean-exit signal, without blocking the budget.
    assert fake_clock["t"] == 0


def test_done_when_clean_exit_and_report_present(tmp_path, capsys, fake_clock):
    """Clean exit WITH a valid report is the success path: report wins → done."""
    task_path = _make_project(tmp_path)
    _write(_report_path(task_path), ACCEPTED_REPORT)
    status_path = Path(str(_report_path(task_path).resolve()) + ".status")
    _write(status_path, "state=exited\nexit_code=0\nreason=clean\n")

    exit_code = _run(task_path)
    record = json.loads(capsys.readouterr().out.strip())
    assert exit_code == EXIT_OK
    assert record["status"] == "done"


def test_failed_to_parse_wins_over_clean_exit(tmp_path, capsys, fake_clock):
    """A present-but-invalid report classifies as failed-to-parse, not failed.

    Even when the wrapper reports a clean exit, an invalid report on disk is a
    parse failure — the exited-without-report path only applies when no report
    is present at all.
    """
    task_path = _make_project(tmp_path)
    _write(_report_path(task_path), INVALID_REPORT)
    status_path = Path(str(_report_path(task_path).resolve()) + ".status")
    _write(status_path, "state=exited\nexit_code=0\nreason=clean\n")

    exit_code = _run(task_path)
    record = json.loads(capsys.readouterr().out.strip())
    assert exit_code == EXIT_FAIL
    assert record["status"] == "failed-to-parse"


def test_done_takes_precedence_over_crash(tmp_path, capsys, fake_clock):
    """A valid report wins even when the status file reports a non-zero exit."""
    task_path = _make_project(tmp_path)
    _write(_report_path(task_path), ACCEPTED_REPORT)
    status_path = Path(str(_report_path(task_path).resolve()) + ".status")
    _write(status_path, "state=exited\nexit_code=7\n")

    exit_code = _run(task_path)
    record = json.loads(capsys.readouterr().out.strip())
    assert exit_code == EXIT_OK
    assert record["status"] == "done"


# --- deadline outcomes ------------------------------------------------------


def test_still_running_when_max_block_is_limiting(tmp_path, capsys, fake_clock):
    """--max-block smaller than the configured timeout → still-running on expiry."""
    task_path = _make_project(tmp_path)  # configured timeout 60m
    exit_code = _run(task_path, max_block="30s")
    record = json.loads(capsys.readouterr().out.strip())
    assert exit_code == EXIT_OK
    assert record["status"] == "still-running"
    assert record["effective_block_seconds"] == 30
    # The loop blocked (advanced the clock) before giving up.
    assert fake_clock["t"] >= 30


def test_timeout_when_configured_timeout_is_limiting(tmp_path, capsys, fake_clock):
    """Configured timeout smaller than --max-block → timeout on expiry."""
    task_path = _make_project(tmp_path, config_body=CONFIG_BODY.format(timeout="10s"))
    exit_code = _run(task_path, max_block="30s")
    record = json.loads(capsys.readouterr().out.strip())
    assert exit_code == EXIT_FAIL
    assert record["status"] == "timeout"
    assert record["effective_block_seconds"] == 10
    assert record["timeout_seconds"] == 10


# --- blocking until the assignee state changes ------------------------------


def test_blocks_until_report_appears(tmp_path, capsys, fake_clock, monkeypatch):
    """It blocks across polls and reports `done` once the report materializes."""
    task_path = _make_project(tmp_path)
    calls = {"n": 0}
    real_verdict = wait_handoff._report_verdict

    def staged_verdict(report_path):
        calls["n"] += 1
        if calls["n"] < 3:
            return None  # report not present yet — assignee still running
        return "accepted"  # assignee finished; report now parses

    monkeypatch.setattr(wait_handoff, "_report_verdict", staged_verdict)

    exit_code = _run(task_path, max_block="5m")
    record = json.loads(capsys.readouterr().out.strip())
    assert exit_code == EXIT_OK
    assert record["status"] == "done"
    assert calls["n"] >= 3  # polled multiple times before the state change
    assert fake_clock["t"] > 0  # blocked (clock advanced) before returning
    assert real_verdict is not staged_verdict


# --- usage / environment guards ---------------------------------------------


def test_requires_absolute_task_path(capsys):
    parser = build_parser()
    args = parser.parse_args(
        ["wait-handoff", "tasks/TASK-01-003.md", "--role", "coder", "--max-block", "30s"]
    )
    exit_code = args._handler(args)
    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert "must be an absolute path" in captured.err


def test_rejects_bad_max_block(tmp_path, capsys):
    task_path = _make_project(tmp_path)
    parser = build_parser()
    args = parser.parse_args(
        ["wait-handoff", str(task_path), "--role", "coder", "--max-block", "soon"]
    )
    exit_code = args._handler(args)
    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert "invalid --max-block" in captured.err


def test_missing_task_file_fails(tmp_path, capsys):
    missing = (tmp_path / "proj" / "tasks" / "TASK-01-003.md").resolve()
    parser = build_parser()
    args = parser.parse_args(
        ["wait-handoff", str(missing), "--role", "coder", "--max-block", "30s"]
    )
    exit_code = args._handler(args)
    captured = capsys.readouterr()
    assert exit_code == EXIT_FAIL
    assert "task file not found" in captured.err
