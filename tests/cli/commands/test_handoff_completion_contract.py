"""Regression coverage for publication-safe handoff completion observation."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from cli import emit, host_capability, request_trace
from cli.commands import dispatch, wait_handoff, wait_report
from cli.main import EXIT_FAIL, EXIT_OK, build_parser
from tests.scaffold import project_scaffold


TASK_REPORT = """# REPORT-01-003

Status: complete

## Identity

- Work root: tool-repo

## Completion evidence

The requested implementation and its focused checks are complete.

## Remaining risks

None.

## Ready to close

yes
"""

REVIEW_REPORT = """# REPORT-01-003

Status: complete
Request alignment: unavailable-for-legacy
Request evidence: none

## Identity

- Review ID: REVIEW-01-003
- Prompt path: /tmp/PROMPT-01-003.md
- Task path: /tmp/TASK-01-003-demo.md
- Review file path: /tmp/REVIEW-01-003.md

## Evidence reviewed

Captured coder completion evidence and the delivered implementation.

## Verdict

approve

## Blocking findings

none.
"""

PLANNING_REPORT = """# REPORT-PLAN-001-baseline

Status: complete
Request alignment: unavailable-for-legacy
Request evidence: none

## Identity

- Review ID: REVIEW-PLAN-001-baseline
- Prompt path: /tmp/PROMPT-PLAN-001-baseline.md
- Review file path: /tmp/REVIEW-PLAN-001-baseline.md

## Evidence reviewed

The bound planning artifacts.

## Verdict

approve

## Blocking findings

none.
"""


def _config() -> str:
    return """[project]
id = "handoff-contract"
name = "handoff-contract"
project_schema_version = "v0.9.0"

[roles.coder]
description = "Implements tasks."
agent = "codex"
auto_launch = ["task_run"]
timeout = "30s"

[roles.reviewer]
description = "Reviews tasks and plans."
agent = "codex"
auto_launch = ["task_review", "planning_review"]
timeout = "30s"

[reviews]
planning = "required"
planning_role = "reviewer"
task_closure = "required"
task_role = "reviewer"
"""


def _task(scaffold, status: str = "in-progress") -> Path:
    return scaffold.write(
        f"tasks/{status}/TASK-01-003-demo.md",
        """# TASK-01-003: Demo

Phase: PHASE-01-demo
Plan ref: BUILD-01-003
Work root: tool-repo
Assignee: coder
""",
    )


def _run_with_staged_publication(module, argv, publish):
    """Run a wait with a deterministic clock and publish after its first sleep."""
    parser = build_parser()
    args = parser.parse_args(argv)
    clock = {"t": 0.0, "published": False}

    def sleep(seconds: float) -> None:
        clock["t"] += seconds
        if not clock["published"]:
            clock["published"] = True
            publish()

    original_monotonic = module.time.monotonic
    original_sleep = module.time.sleep
    module.time.monotonic = lambda: clock["t"]
    module.time.sleep = sleep
    try:
        return args._handler(args)
    finally:
        module.time.monotonic = original_monotonic
        module.time.sleep = original_sleep


def test_task_wait_treats_partial_report_as_nonterminal_while_writer_runs(
    capsys,
):
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        task_path = _task(scaffold)
        report_path = scaffold.write(
            "reports/REPORT-01-003.md",
            "# REPORT-01-003\n\nStatus: complete\n",
        )
        Path(str(report_path) + ".status").write_text(
            "state=running\nlaunch_id=launch-current\nexpected_variant=task\n",
            encoding="utf-8",
        )
        rc = _run_with_staged_publication(
            wait_handoff,
            [
                "wait-handoff",
                str(task_path),
                "--role",
                "coder",
                "--max-block",
                "10s",
                "--poll-interval",
                "1",
            ],
            lambda: report_path.write_text(TASK_REPORT, encoding="utf-8"),
        )

    record = json.loads(capsys.readouterr().out)
    assert rc == EXIT_OK
    assert record["classification"] == "accepted"
    assert record["report_variant"] == "task"


def test_task_review_wait_rejects_reintroduced_coder_report(capsys):
    """Coder-shaped bytes in the review-report slot cannot satisfy review.

    A task-review wait observes the independent review slot
    (``REPORT-NN-NNN-review.md``); task-completion content occupying it is a
    path/variant mismatch, never reviewer completion evidence.
    """
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        task_path = _task(scaffold, "in-review")
        report_path = scaffold.write(
            "reports/REPORT-01-003-review.md",
            TASK_REPORT.replace("# REPORT-01-003", "# REPORT-01-003-review"),
        )
        Path(str(report_path) + ".status").write_text(
            "state=exited\n"
            "exit_code=0\n"
            "launch_id=review-launch\n"
            "expected_variant=review\n",
            encoding="utf-8",
        )
        args = argparse.Namespace(
            task_path=str(task_path),
            role="reviewer",
            max_block="10s",
            poll_interval=1.0,
        )
        rc = wait_handoff.handler(args)

    record = json.loads(capsys.readouterr().out)
    assert rc == EXIT_FAIL
    assert record["classification"] == "failed-to-parse"
    assert record["expected_report_variant"] == "review"


def test_report_path_wait_observes_partial_publication_and_wrapper_exit(capsys):
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        report_path = scaffold.write(
            "reports/REPORT-PLAN-001-baseline.md",
            "# REPORT-PLAN-001-baseline\n\nStatus: complete\n",
        )
        Path(str(report_path) + ".status").write_text(
            "state=running\nlaunch_id=planning-launch\n"
            "expected_variant=planning-review\n",
            encoding="utf-8",
        )
        rc = _run_with_staged_publication(
            wait_report,
            [
                "wait-report",
                str(report_path),
                "--role",
                "reviewer",
                "--max-block",
                "10s",
                "--poll-interval",
                "1",
            ],
            lambda: report_path.write_text(PLANNING_REPORT, encoding="utf-8"),
        )

    record = json.loads(capsys.readouterr().out)
    assert rc == EXIT_OK
    assert record["classification"] == "accepted"
    assert record["report_variant"] == "planning-review"
    assert record["launch_id"] == "planning-launch"


def test_report_path_wait_classifies_clean_exit_without_report(capsys):
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        report_path = scaffold.reports / "REPORT-PLAN-001-baseline.md"
        Path(str(report_path) + ".status").write_text(
            "state=exited\nexit_code=0\nlaunch_id=planning-launch\n",
            encoding="utf-8",
        )
        args = argparse.Namespace(
            report_path=str(report_path),
            role="reviewer",
            max_block="10s",
            poll_interval=1.0,
        )
        rc = wait_report.handler(args)

    record = json.loads(capsys.readouterr().out)
    assert rc == EXIT_FAIL
    assert record["classification"] == "exited-without-report"
    assert record["exit_code"] == 0


@pytest.mark.parametrize("wait_kind", ("task", "report"))
@pytest.mark.parametrize("status_mode", ("manual", "exited-pending"))
def test_wait_surfaces_accept_complete_report_without_live_retention_barrier(
    capsys,
    wait_kind,
    status_mode,
):
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        task_path = _task(scaffold)
        report_path = scaffold.write("reports/REPORT-01-003.md", TASK_REPORT)
        if status_mode == "exited-pending":
            Path(str(report_path) + ".status").write_text(
                "state=exited\n"
                "exit_code=0\n"
                "reason=clean\n"
                "launch_id=launch-current\n"
                "expected_variant=task\n"
                "guarantee_scope=retained-launch-log\n"
                "retained_log_ready=false\n",
                encoding="utf-8",
            )

        if wait_kind == "task":
            args = argparse.Namespace(
                task_path=str(task_path),
                role="coder",
                max_block="1s",
                poll_interval=0.01,
            )
            rc = wait_handoff.handler(args)
        else:
            args = argparse.Namespace(
                report_path=str(report_path),
                role="coder",
                variant="task",
                max_block="1s",
                poll_interval=0.01,
            )
            rc = wait_report.handler(args)

    record = json.loads(capsys.readouterr().out)
    assert rc == EXIT_OK
    assert record["classification"] == "accepted"
    assert record["publication_state"] == "complete"
    assert record["wrapper_state"] == (
        None if status_mode == "manual" else "exited"
    )


@pytest.mark.parametrize("wait_kind", ("task", "report"))
def test_wait_surfaces_hold_complete_report_at_live_retention_barrier(
    capsys,
    wait_kind,
):
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        task_path = _task(scaffold)
        report_path = scaffold.write("reports/REPORT-01-003.md", TASK_REPORT)
        Path(str(report_path) + ".status").write_text(
            "state=running\n"
            "launch_id=launch-current\n"
            "expected_variant=task\n"
            "guarantee_scope=retained-launch-log\n"
            "retained_log_ready=false\n",
            encoding="utf-8",
        )
        if wait_kind == "task":
            module = wait_handoff
            argv = [
                "wait-handoff",
                str(task_path),
                "--role",
                "coder",
                "--max-block",
                "1s",
                "--poll-interval",
                "1",
            ]
        else:
            module = wait_report
            argv = [
                "wait-report",
                str(report_path),
                "--role",
                "coder",
                "--variant",
                "task",
                "--max-block",
                "1s",
                "--poll-interval",
                "1",
            ]
        rc = _run_with_staged_publication(module, argv, lambda: None)

    record = json.loads(capsys.readouterr().out)
    assert rc == EXIT_OK
    assert record["classification"] == "still-running"
    assert record["publication_state"] == "complete"
    assert record["wrapper_state"] == "running"
    if wait_kind == "task":
        assert record["status"] == "still-running"
    else:
        assert record["still_running"] is True


def test_report_path_wait_without_variant_rejects_stale_review_content(capsys):
    """An unmarked ordinary report slot defaults to task, not its own bytes."""
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        report_path = scaffold.write("reports/REPORT-01-003.md", REVIEW_REPORT)
        args = argparse.Namespace(
            report_path=str(report_path),
            role="reviewer",
            max_block="1s",
            poll_interval=1.0,
        )
        rc = wait_report.handler(args)

    record = json.loads(capsys.readouterr().out)
    assert rc == EXIT_OK
    assert record["classification"] == "still-running"
    assert record["expected_report_variant"] == "task"
    assert record["publication_state"] == "partial"


@pytest.mark.parametrize("wait_kind", ("task", "report"))
def test_no_progress_wait_refuses_role_beyond_claude_idle(
    capsys,
    monkeypatch,
    wait_kind,
):
    long_config = _config().replace('timeout = "30s"', 'timeout = "45m"')
    with project_scaffold(cartopian_toml=long_config) as scaffold:
        task_path = _task(scaffold)
        report_path = scaffold.reports / "REPORT-01-003.md"
        monkeypatch.setenv(host_capability.CONNECTED_ENV, "1")
        monkeypatch.setenv(host_capability.CLIENT_ENV, "claude-code")
        emit.set_progress_sink(None)

        if wait_kind == "task":
            args = argparse.Namespace(
                task_path=str(task_path),
                role="coder",
                max_block=None,
                poll_interval=1.0,
            )
            rc = wait_handoff.handler(args)
        else:
            args = argparse.Namespace(
                report_path=str(report_path),
                role="reviewer",
                variant=None,
                max_block=None,
                poll_interval=1.0,
            )
            rc = wait_report.handler(args)

    captured = capsys.readouterr()
    assert rc == EXIT_FAIL
    assert captured.out == ""
    assert "45m" in captured.err and "30m" in captured.err
    assert "idle ceiling" in captured.err


def test_review_context_binds_preserved_coder_evidence_by_path():
    """Review binding references the preserved completion report on disk.

    The completion report stays in place throughout task review; rebinding
    verifies the live artifact against the bound content identity instead of
    reconstructing evidence from prompt-embedded bytes. Removing the artifact
    blocks the binding (the split-report contract never tolerates evidence
    loss).
    """
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        task_path = _task(scaffold, "in-review")
        scaffold.capture_request(
            request_id="REQUEST-001",
            unit="task:TASK-01-003",
            text="Implement the handoff completion contract.",
        )
        report_path = scaffold.write("reports/REPORT-01-003.md", TASK_REPORT)

        initial = request_trace.context_for_task(
            scaffold.project_root,
            task_path,
            require_completion_evidence=True,
        )
        prompt_text = request_trace.upsert_request_sections(
            "# Review task completion\n",
            initial.section,
        )

        rebound = request_trace.context_for_task(
            scaffold.project_root,
            task_path,
            prompt_text=prompt_text,
        )
        preflight = request_trace.preflight_prompt_binding(rebound, prompt_text)

        captured = rebound.as_record()["captured_completion_evidence"]
        assert preflight["ok"] is True
        assert rebound.context_identity == initial.context_identity
        assert captured["source_path"] == "reports/REPORT-01-003.md"
        assert captured["review_path"] == "reports/REPORT-01-003-review.md"
        assert captured["completion_report_path"] == str(report_path.resolve())

        report_path.unlink()
        with pytest.raises(
            request_trace.RequestRefusal,
            match="missing-coder-completion-evidence",
        ):
            request_trace.context_for_task(
                scaffold.project_root,
                task_path,
                prompt_text=prompt_text,
            )


@pytest.mark.parametrize(
    "report_text",
    (
        TASK_REPORT.replace("Status: complete", "Status: blocked"),
        TASK_REPORT.replace("## Remaining risks\n\nNone.\n\n", ""),
        REVIEW_REPORT
        + "\n## Completion evidence\n\nNot coder evidence.\n"
        + "\n## Remaining risks\n\nNone.\n"
        + "\n## Ready to close\n\nyes\n",
    ),
    ids=("blocked", "missing-task-section", "review-shaped"),
)
def test_review_context_rejects_nonaccepted_or_non_task_coder_evidence(report_text):
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        task_path = _task(scaffold, "in-review")
        scaffold.capture_request(
            request_id="REQUEST-001",
            unit="task:TASK-01-003",
            text="Implement the handoff completion contract.",
        )
        scaffold.write("reports/REPORT-01-003.md", report_text)

        with pytest.raises(
            request_trace.RequestRefusal,
            match="malformed-coder-completion-evidence",
        ):
            request_trace.context_for_task(
                scaffold.project_root,
                task_path,
                require_completion_evidence=True,
            )


def test_reviewer_dispatch_preserves_completion_and_clears_review_slot():
    """Review launch targets the independent review slot only.

    The coder's completion report is preserved byte-identically for direct
    reviewer access; slot clearing before the launch touches nothing but the
    review-report slot and its transient companions (including the coder
    handoff's leftover status marker cleanup being unnecessary — it lives on
    the completion path, which dispatch no longer clears; the review slot's
    own stale state is what resets).
    """
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        task_path = _task(scaffold, "in-review")
        scaffold.capture_request(
            request_id="REQUEST-001",
            unit="task:TASK-01-003",
            text="Implement the handoff completion contract.",
        )
        report_path = scaffold.write("reports/REPORT-01-003.md", TASK_REPORT)
        completion_before = report_path.read_bytes()
        review_report_path = scaffold.reports / "REPORT-01-003-review.md"
        context = request_trace.context_for_task(
            scaffold.project_root,
            task_path,
            require_completion_evidence=True,
        )
        scaffold.write(
            "prompts/PROMPT-01-003.md",
            request_trace.upsert_request_sections(
                "# Review task completion\n",
                context.section,
            ),
        )
        review_report_path.write_text("# stale review attempt\n", encoding="utf-8")
        Path(str(review_report_path) + ".status").write_text(
            "state=exited\nexit_code=1\nlaunch_id=stale-review-launch\n"
            "expected_variant=review\n",
            encoding="utf-8",
        )
        launched = {}

        def popen(*argv, **kwargs):
            launched["argv"] = argv
            launched["env"] = kwargs["env"]
            return SimpleNamespace(pid=4242)

        args = argparse.Namespace(
            task_path=str(task_path),
            prompt=None,
            role="reviewer",
        )
        direct_env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("CARTOPIAN_MCP_")
        }
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.dict(os.environ, direct_env, clear=True),
            mock.patch.object(dispatch.shutil, "which", return_value="/bin/true"),
            mock.patch.object(dispatch, "_running_on_windows", return_value=True),
            mock.patch.object(dispatch.subprocess, "Popen", side_effect=popen),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            rc = dispatch.handler(args)

        record = json.loads(out.getvalue())
        status_fields = wait_handoff._read_status_fields(
            Path(str(review_report_path) + ".status")
        )
        completion_after = report_path.read_bytes()
        stale_review_cleared = not review_report_path.exists()

    assert rc == EXIT_OK, err.getvalue()
    # Coder completion evidence is preserved byte-identically…
    assert completion_after == completion_before
    # …while only stale review-attempt state is cleared for the new launch.
    assert stale_review_cleared
    assert record["slot_clear"] == {
        "report_deleted": True,
        "status_deleted": True,
    }
    assert record["expected_report_path"] == str(review_report_path.resolve())
    assert record["expected_report_variant"] == "review"
    assert status_fields["state"] == "running"
    assert status_fields["expected_variant"] == "review"
    assert status_fields["launch_id"] == record["launch_id"]
    assert launched["env"][dispatch.HANDOFF_ID_ENV] == record["launch_id"]
    assert launched["env"][dispatch.EXPECTED_VARIANT_ENV] == "review"
    assert not any(
        name in launched["env"]
        for name in (
            "CARTOPIAN_MCP_CONNECTED",
            "CARTOPIAN_MCP_CLIENT",
            "CARTOPIAN_MCP_CLIENT_VERSION",
            "CARTOPIAN_MCP_CLIENT_TITLE",
            "CARTOPIAN_MCP_TOOL_CALL",
        )
    )
