"""Regression coverage for the split task-completion / task-review report contract.

Evidence gate (red-before-green):

- RED: under the shared-slot contract, task completion and task-review
  completion both resolved to ``reports/REPORT-NN-NNN.md``. Reviewer dispatch
  captured a snapshot of the coder report into the review prompt and then
  *deleted* the coder report to free the slot
  (``test_review_dispatch_preserves_completion_report_and_targets_review_slot``
  fails on that deletion), so the preserved-evidence contract could only be
  reconstructed from prompt-embedded bytes.
- GREEN: task completion keeps the compatibility filename
  ``REPORT-NN-NNN.md``; task-review completion publishes independently to
  ``REPORT-NN-NNN-review.md``; the review prompt binds the preserved
  completion-report path and the expected review-report path without
  reproducing the report body; waits, parsing, cleanup, and aggregation
  select the correct variant from work type and expected path.

Planning-review report naming and semantics are pinned unchanged.
"""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from cli import handoff_observer, request_trace
from cli.commands import (
    close_audit,
    delete_report,
    dispatch,
    handoff_packet,
    parse_report,
    report_action,
    task_bundle,
    wait_handoff,
    wait_report,
)
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE, build_parser
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


def _review_report(scaffold) -> str:
    root = scaffold.project_root.resolve()
    return f"""# REPORT-01-003-review

Status: complete
Request alignment: unavailable-for-legacy
Request evidence: none

## Identity

- Review ID: REVIEW-01-003
- Prompt path: {root / 'prompts' / 'PROMPT-01-003.md'}
- Task path: {root / 'tasks' / 'in-review' / 'TASK-01-003-demo.md'}
- Review file path: {root / 'reviews' / 'REVIEW-01-003.md'}

## Evidence reviewed

The preserved coder completion report and the delivered implementation.

## Verdict

approve

## Blocking findings

none.
"""


def _config() -> str:
    return """[project]
id = "split-report-contract"
name = "split-report-contract"
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


def _task(scaffold, status: str = "in-review") -> Path:
    return scaffold.write(
        f"tasks/{status}/TASK-01-003-demo.md",
        """# TASK-01-003: Demo

Phase: PHASE-01-demo
Plan ref: BUILD-01-003
Work root: tool-repo
Assignee: coder
""",
    )


def _captured_review_setup(scaffold):
    """Seed an in-review task with request evidence, coder report, and prompt."""
    task_path = _task(scaffold)
    scaffold.capture_request(
        request_id="REQUEST-001",
        unit="task:TASK-01-003",
        text="Implement the split report contract.",
    )
    report_path = scaffold.write("reports/REPORT-01-003.md", TASK_REPORT)
    context = request_trace.context_for_task(
        scaffold.project_root,
        task_path,
        require_completion_evidence=True,
    )
    prompt_path = scaffold.write(
        "prompts/PROMPT-01-003.md",
        request_trace.upsert_request_sections(
            "# Review task completion\n",
            context.section,
        ),
    )
    return task_path, report_path, prompt_path, context


def _dispatch_review(task_path):
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
        mock.patch.object(dispatch.subprocess, "Popen", side_effect=popen),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
    ):
        rc = dispatch.handler(args)
    return rc, out.getvalue(), err.getvalue(), launched


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


# --- Authoritative identity model ------------------------------------------


def test_identity_model_derives_both_task_report_paths():
    from cli import report_identity

    root = Path("/proj")
    completion = report_identity.completion_report_path(root, "01-003")
    review = report_identity.review_report_path(root, "01-003")
    assert completion == root / "reports" / "REPORT-01-003.md"
    assert review == root / "reports" / "REPORT-01-003-review.md"


def test_identity_model_classifies_report_filenames():
    from cli import report_identity

    assert report_identity.variant_for_report_name("REPORT-01-003.md") == "task"
    assert (
        report_identity.variant_for_report_name("REPORT-01-003-review.md")
        == "review"
    )
    assert (
        report_identity.variant_for_report_name("REPORT-PLAN-001-baseline.md")
        == "planning-review"
    )
    assert report_identity.nn_nnn_for_report_name("REPORT-01-003.md") == "01-003"
    assert (
        report_identity.nn_nnn_for_report_name("REPORT-01-003-review.md")
        == "01-003"
    )
    assert report_identity.nn_nnn_for_report_name("REPORT-PLAN-001.md") is None


# --- Dispatch: preservation and independent review slot ---------------------


def test_review_dispatch_preserves_completion_report_and_targets_review_slot():
    """The evidence-loss regression: review launch must not clear coder evidence.

    Under the shared-slot contract this fails: dispatch deleted
    ``reports/REPORT-01-003.md`` to free the slot for the reviewer.
    """
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        task_path, report_path, _prompt, _context = _captured_review_setup(scaffold)
        before = report_path.read_bytes()

        rc, out, err, launched = _dispatch_review(task_path)

        record = json.loads(out)
        review_path = scaffold.reports / "REPORT-01-003-review.md"
        assert rc == EXIT_OK, err
        # Coder completion evidence is preserved byte-identically.
        assert report_path.is_file()
        assert report_path.read_bytes() == before
        # The reviewer handoff has its own report identity.
        assert record["expected_report_path"] == str(review_path.resolve())
        assert record["expected_report_variant"] == "review"
        assert launched["env"]["CARTOPIAN_EXPECTED_REPORT_PATH"] == str(
            review_path.resolve()
        )
        assert launched["env"]["CARTOPIAN_EXPECTED_REPORT_VARIANT"] == "review"
        # The running marker coordinates the review slot, not the coder slot.
        status_fields = wait_handoff._read_status_fields(
            Path(str(review_path) + ".status")
        )
        assert status_fields["state"] == "running"
        assert status_fields["expected_variant"] == "review"
        assert not Path(str(report_path) + ".status").exists()


def test_review_dispatch_retry_clears_only_stale_review_state():
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        task_path, report_path, _prompt, _context = _captured_review_setup(scaffold)
        before = report_path.read_bytes()
        review_path = scaffold.write(
            "reports/REPORT-01-003-review.md", "# stale partial review\n"
        )
        Path(str(review_path) + ".status").write_text(
            "state=exited\nexit_code=1\nlaunch_id=stale-review\n"
            "expected_variant=review\n",
            encoding="utf-8",
        )

        rc, out, err, _launched = _dispatch_review(task_path)

        record = json.loads(out)
        assert rc == EXIT_OK, err
        assert record["slot_clear"] == {
            "report_deleted": True,
            "status_deleted": True,
        }
        # Only transient review-attempt state was replaced; the completion
        # artifact is byte-identical before and after the retry reset.
        assert report_path.read_bytes() == before
        assert not review_path.exists()


def test_review_dispatch_blocks_when_completion_evidence_missing():
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        task_path, report_path, _prompt, _context = _captured_review_setup(scaffold)
        report_path.unlink()

        rc, out, err, launched = _dispatch_review(task_path)

        assert rc == EXIT_FAIL
        assert "missing-coder-completion-evidence" in err
        assert "argv" not in launched


# --- Review prompt binding: reference, not reproduction ---------------------


def test_review_prompt_binds_paths_without_reproducing_report_body():
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        _task_path, report_path, _prompt, context = _captured_review_setup(scaffold)
        review_path = scaffold.reports / "REPORT-01-003-review.md"

        section = context.section
        assert str(report_path.resolve()) in section
        assert str(review_path.resolve()) in section
        # The report body is read from the preserved artifact, never embedded.
        assert "The requested implementation and its focused checks" not in section
        assert request_trace.CAPTURED_REPORT_MARKER not in section

        captured = context.as_record()["captured_completion_evidence"]
        assert captured["source_path"] == "reports/REPORT-01-003.md"
        assert captured["review_path"] == "reports/REPORT-01-003-review.md"
        assert "content" not in captured


def test_review_binding_blocks_when_preserved_report_is_missing():
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        task_path, report_path, prompt_path, _context = _captured_review_setup(
            scaffold
        )
        prompt_text = prompt_path.read_text(encoding="utf-8")
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


def test_review_binding_blocks_when_preserved_report_mutates():
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        task_path, report_path, prompt_path, _context = _captured_review_setup(
            scaffold
        )
        prompt_text = prompt_path.read_text(encoding="utf-8")
        report_path.write_text(
            TASK_REPORT.replace("focused checks", "rewritten evidence"),
            encoding="utf-8",
        )

        with pytest.raises(
            request_trace.RequestRefusal,
            match="stale-request-context",
        ):
            request_trace.context_for_task(
                scaffold.project_root,
                task_path,
                prompt_text=prompt_text,
            )


def test_legacy_embedded_completion_prompt_is_refused():
    """A pre-activation prompt embedding the report body cannot bind a new review."""
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        task_path, _report, _prompt, _context = _captured_review_setup(scaffold)
        legacy_prompt = "\n".join(
            [
                "# Review task completion",
                "",
                request_trace.REQUEST_SECTION_HEADING,
                "",
                "Request-context identity: sha256:0000",
                "Review target: task:TASK-01-003",
                "Request state: resolved",
                "Request evidence: REQUEST-001",
                "",
                request_trace.CAPTURED_COMPLETION_HEADING,
                "",
                "Source path: reports/REPORT-01-003.md",
                "Content identity: sha256:0000",
                "Content bytes: 4",
                "Coder status: complete",
                "Ready to close: yes",
                "Trailing newline: yes",
                "",
                request_trace.CAPTURED_REPORT_MARKER,
                "",
                "> # REPORT-01-003",
                "",
                request_trace.MANAGEMENT_SECTION_HEADING,
                "",
                "- IMPLEMENTATION_PLAN.md",
                "",
            ]
        )
        with pytest.raises(
            request_trace.RequestRefusal,
            match="stale-request-context",
        ):
            request_trace.context_for_task(
                scaffold.project_root,
                task_path,
                prompt_text=legacy_prompt,
            )


# --- Waits: each slot accepts only its expected artifact ---------------------


def test_wait_handoff_review_watches_review_slot_and_preserves_completion(capsys):
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        task_path = _task(scaffold)
        completion = scaffold.write("reports/REPORT-01-003.md", TASK_REPORT)
        before = completion.read_bytes()
        review_path = scaffold.reports / "REPORT-01-003-review.md"
        Path(str(review_path) + ".status").write_text(
            "state=running\nlaunch_id=review-launch\nexpected_variant=review\n",
            encoding="utf-8",
        )
        rc = _run_with_staged_publication(
            wait_handoff,
            [
                "wait-handoff",
                str(task_path),
                "--role",
                "reviewer",
                "--max-block",
                "10s",
                "--poll-interval",
                "1",
            ],
            lambda: review_path.write_text(
                _review_report(scaffold), encoding="utf-8"
            ),
        )
        preserved = completion.read_bytes()
        resolved_review = str(review_path.resolve())

    record = json.loads(capsys.readouterr().out)
    assert rc == EXIT_OK
    assert record["status"] == "done"
    assert record["report_path"] == resolved_review
    assert record["report_variant"] == "review"
    assert record["expected_report_variant"] == "review"
    assert preserved == before


def test_completion_report_cannot_satisfy_review_wait(capsys):
    """A task-completion publication at the review slot is a variant mismatch."""
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        task_path = _task(scaffold)
        scaffold.write("reports/REPORT-01-003.md", TASK_REPORT)
        review_path = scaffold.write(
            "reports/REPORT-01-003-review.md",
            TASK_REPORT.replace("# REPORT-01-003", "# REPORT-01-003-review"),
        )
        Path(str(review_path) + ".status").write_text(
            "state=exited\nexit_code=0\nlaunch_id=review-launch\n"
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


def test_review_report_cannot_satisfy_task_wait(capsys):
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        task_path = _task(scaffold, "in-progress")
        report_path = scaffold.write(
            "reports/REPORT-01-003.md",
            _review_report(scaffold).replace(
                "# REPORT-01-003-review", "# REPORT-01-003"
            ),
        )
        Path(str(report_path) + ".status").write_text(
            "state=exited\nexit_code=0\nlaunch_id=coder-launch\n"
            "expected_variant=task\n",
            encoding="utf-8",
        )
        args = argparse.Namespace(
            task_path=str(task_path),
            role="coder",
            max_block="10s",
            poll_interval=1.0,
        )
        rc = wait_handoff.handler(args)

    record = json.loads(capsys.readouterr().out)
    assert rc == EXIT_FAIL
    assert record["classification"] == "failed-to-parse"
    assert record["expected_report_variant"] == "task"


def test_wait_report_infers_review_variant_from_review_filename(capsys):
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        _task(scaffold)
        review_path = scaffold.write(
            "reports/REPORT-01-003-review.md", _review_report(scaffold)
        )
        args = argparse.Namespace(
            report_path=str(review_path),
            role=None,
            variant=None,
            max_block="10s",
            poll_interval=1.0,
        )
        rc = wait_report.handler(args)

    record = json.loads(capsys.readouterr().out)
    assert rc == EXIT_OK
    assert record["expected_report_variant"] == "review"
    assert record["status"] == "accepted"


def test_wait_review_partial_publication_stays_nonterminal_while_running(capsys):
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        task_path = _task(scaffold)
        scaffold.write("reports/REPORT-01-003.md", TASK_REPORT)
        review_path = scaffold.write(
            "reports/REPORT-01-003-review.md",
            "# REPORT-01-003-review\n\nStatus: complete\n",
        )
        Path(str(review_path) + ".status").write_text(
            "state=running\nlaunch_id=review-launch\nexpected_variant=review\n",
            encoding="utf-8",
        )
        rc = _run_with_staged_publication(
            wait_handoff,
            [
                "wait-handoff",
                str(task_path),
                "--role",
                "reviewer",
                "--max-block",
                "10s",
                "--poll-interval",
                "1",
            ],
            lambda: review_path.write_text(
                _review_report(scaffold), encoding="utf-8"
            ),
        )

    record = json.loads(capsys.readouterr().out)
    assert rc == EXIT_OK
    assert record["classification"] == "accepted"
    assert record["report_variant"] == "review"


# --- Parsing and aggregation -------------------------------------------------


def test_parse_report_infers_review_from_review_filename():
    variant, err = parse_report._infer_variant(
        Path("/proj/reports/REPORT-01-003-review.md"),
        "Status: complete\n\n## Identity\n\n- Review ID: REVIEW-01-003\n\n"
        "## Verdict\n\napprove\n",
    )
    assert err is None
    assert variant == "review"


def test_parse_report_rejects_task_shape_at_review_filename():
    variant, err = parse_report._infer_variant(
        Path("/proj/reports/REPORT-01-003-review.md"),
        TASK_REPORT,
    )
    assert variant is None
    assert err is not None
    assert "path/variant mismatch" in err


def test_parse_report_rejects_review_shape_at_completion_filename():
    """Symmetric wrong-path direction: the unmarked filename is authoritative."""
    variant, err = parse_report._infer_variant(
        Path("/proj/reports/REPORT-01-003.md"),
        "Status: complete\n\n## Identity\n\n- Review ID: REVIEW-01-003\n\n"
        "## Verdict\n\napprove\n",
    )
    assert variant is None
    assert err is not None
    assert "path/variant mismatch" in err


def test_parse_report_explicit_variant_cannot_bypass_filename_contract(capsys):
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        completion = scaffold.write("reports/REPORT-01-003.md", TASK_REPORT)
        review = scaffold.write(
            "reports/REPORT-01-003-review.md", _review_report(scaffold)
        )
        rc_completion = parse_report.handler(
            argparse.Namespace(report_path=str(completion), variant="review")
        )
        rc_review = parse_report.handler(
            argparse.Namespace(report_path=str(review), variant="task")
        )
        # A matching explicit variant remains valid on both paths.
        rc_match = parse_report.handler(
            argparse.Namespace(report_path=str(completion), variant="task")
        )

    capsys.readouterr()
    assert rc_completion == EXIT_USAGE
    assert rc_review == EXIT_USAGE
    assert rc_match == EXIT_OK


def test_report_action_rejects_review_shape_at_completion_path(capsys):
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        _task(scaffold)
        report_path = scaffold.write(
            "reports/REPORT-01-003.md",
            _review_report(scaffold).replace(
                "# REPORT-01-003-review", "# REPORT-01-003"
            ),
        )
        rc = report_action.handler(
            argparse.Namespace(report_path=str(report_path), variant=None)
        )

    capsys.readouterr()
    assert rc == EXIT_USAGE


def test_report_action_rejects_task_shape_at_review_path(capsys):
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        _task(scaffold)
        review_path = scaffold.write(
            "reports/REPORT-01-003-review.md",
            TASK_REPORT.replace("# REPORT-01-003", "# REPORT-01-003-review"),
        )
        rc = report_action.handler(
            argparse.Namespace(report_path=str(review_path), variant=None)
        )

    capsys.readouterr()
    assert rc == EXIT_USAGE


def test_report_action_explicit_variant_cannot_bypass_filename_contract(capsys):
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        _task(scaffold)
        completion = scaffold.write("reports/REPORT-01-003.md", TASK_REPORT)
        review = scaffold.write(
            "reports/REPORT-01-003-review.md", _review_report(scaffold)
        )
        rc_completion = report_action.handler(
            argparse.Namespace(report_path=str(completion), variant="review")
        )
        rc_review = report_action.handler(
            argparse.Namespace(report_path=str(review), variant="task")
        )

    capsys.readouterr()
    assert rc_completion == EXIT_USAGE
    assert rc_review == EXIT_USAGE


def test_wait_report_explicit_review_variant_never_accepts_completion_slot(capsys):
    """An explicit review wait on the unmarked slot fails closed, even for
    review-shaped bytes: the filename contract wins over the override."""
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        _task(scaffold)
        report_path = scaffold.write(
            "reports/REPORT-01-003.md",
            _review_report(scaffold).replace(
                "# REPORT-01-003-review", "# REPORT-01-003"
            ),
        )
        Path(str(report_path) + ".status").write_text(
            "state=exited\nexit_code=0\nlaunch_id=review-launch\n"
            "expected_variant=review\n",
            encoding="utf-8",
        )
        args = argparse.Namespace(
            report_path=str(report_path),
            role=None,
            variant="review",
            max_block="10s",
            poll_interval=1.0,
        )
        rc = wait_report.handler(args)

    record = json.loads(capsys.readouterr().out)
    assert rc == EXIT_FAIL
    assert record["classification"] == "failed-to-parse"
    assert record["accepted"] is False


def test_wait_report_explicit_task_variant_never_accepts_review_slot(capsys):
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        _task(scaffold)
        review_path = scaffold.write(
            "reports/REPORT-01-003-review.md",
            TASK_REPORT.replace("# REPORT-01-003", "# REPORT-01-003-review"),
        )
        Path(str(review_path) + ".status").write_text(
            "state=exited\nexit_code=0\nlaunch_id=coder-launch\n"
            "expected_variant=task\n",
            encoding="utf-8",
        )
        args = argparse.Namespace(
            report_path=str(review_path),
            role=None,
            variant="task",
            max_block="10s",
            poll_interval=1.0,
        )
        rc = wait_report.handler(args)

    record = json.loads(capsys.readouterr().out)
    assert rc == EXIT_FAIL
    assert record["classification"] == "failed-to-parse"
    assert record["accepted"] is False


def test_report_action_resolves_review_identities_at_review_path(capsys):
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        task_path, _report, prompt_path, _context = _captured_review_setup(scaffold)
        root = scaffold.project_root.resolve()
        review_file = scaffold.write("reviews/REVIEW-01-003.md", "# REVIEW-01-003\n")
        review_report = scaffold.write(
            "reports/REPORT-01-003-review.md",
            f"""# REPORT-01-003-review

Status: complete
Request alignment: aligned — matches the initiating request
Request evidence: REQUEST-001

## Identity

- Review ID: REVIEW-01-003
- Prompt path: {prompt_path.resolve()}
- Task path: {task_path.resolve()}
- Review file path: {review_file.resolve()}

## Evidence reviewed

The preserved coder completion report.

## Verdict

approve

## Blocking findings

none.
""",
        )
        args = argparse.Namespace(report_path=str(review_report), variant=None)
        rc = report_action.handler(args)

    record = json.loads(capsys.readouterr().out)
    assert rc == EXIT_OK
    assert record["variant"] == "review"
    assert record["verdict"] == "accepted"
    assert record["task_id"] == "TASK-01-003"
    assert record["task_path"] == str(task_path.resolve())
    assert record["expected_prompt_path"] == str(
        (root / "prompts" / "PROMPT-01-003.md").resolve()
    )
    assert record["path_mismatch"] is False
    assert record["recommended_action"] == "close-task"


# --- Cleanup ------------------------------------------------------------------


def test_delete_report_accepts_review_filename_and_companions(capsys):
    with project_scaffold(cartopian_toml=_config()) as scaffold, mock.patch.object(
        delete_report,
        "read_registry",
        return_value=[{"path": str(scaffold.project_root)}],
    ):
        review_path = scaffold.write(
            "reports/REPORT-01-003-review.md", "# REPORT-01-003-review\n"
        )
        Path(str(review_path) + ".status").write_text("state=exited\n", encoding="utf-8")
        Path(str(review_path) + ".launch.log").write_text("log\n", encoding="utf-8")
        args = argparse.Namespace(
            report_path=str(review_path), status_only=False
        )
        rc = delete_report.handler(args)

    record = json.loads(capsys.readouterr().out)
    assert rc == EXIT_OK
    assert record["details"]["deleted_path"] == str(review_path)
    assert not review_path.exists()
    assert not Path(str(review_path) + ".status").exists()
    assert not Path(str(review_path) + ".launch.log").exists()


def test_delete_report_clears_review_companions_without_review_report(capsys):
    """A crash-only review attempt leaves only transients; cleanup is a
    successful idempotent no-op once the owned companions are cleared."""
    with project_scaffold(cartopian_toml=_config()) as scaffold, mock.patch.object(
        delete_report,
        "read_registry",
        return_value=[{"path": str(scaffold.project_root)}],
    ):
        completion = scaffold.write("reports/REPORT-01-003.md", TASK_REPORT)
        before = completion.read_bytes()
        review_path = scaffold.reports / "REPORT-01-003-review.md"
        Path(str(review_path) + ".status").write_text("state=exited\n", encoding="utf-8")
        args = argparse.Namespace(
            report_path=str(review_path), status_only=False
        )
        rc = delete_report.handler(args)
        status_remains = Path(str(review_path) + ".status").exists()
        preserved = completion.read_bytes()

    record = json.loads(capsys.readouterr().out)
    assert rc == EXIT_OK  # the absent optional review .md is a no-op…
    assert not status_remains  # …after the transient companions clear
    assert record["details"]["deleted_path"] is None
    assert record["details"]["already_absent"] is True
    assert record["details"]["status_deleted"] is True
    assert preserved == before


def test_delete_report_review_cleanup_is_repeatable(capsys):
    """Rerunning review-slot cleanup after a successful pass stays green."""
    with project_scaffold(cartopian_toml=_config()) as scaffold, mock.patch.object(
        delete_report,
        "read_registry",
        return_value=[{"path": str(scaffold.project_root)}],
    ):
        review_path = scaffold.write(
            "reports/REPORT-01-003-review.md", "# REPORT-01-003-review\n"
        )
        Path(str(review_path) + ".status").write_text("state=exited\n", encoding="utf-8")
        args = argparse.Namespace(report_path=str(review_path), status_only=False)
        first = delete_report.handler(args)
        second = delete_report.handler(args)

    records = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line
    ]
    assert first == EXIT_OK
    assert second == EXIT_OK
    assert records[0]["details"]["deleted_path"] == str(review_path)
    assert records[1]["details"]["deleted_path"] is None
    assert records[1]["details"]["already_absent"] is True


def test_delete_report_review_off_absent_review_artifact_is_noop(capsys):
    """With task-closure review off, no review artifact ever exists; cleanup
    of the canonical review path still succeeds without touching evidence."""
    with project_scaffold(cartopian_toml=_config()) as scaffold, mock.patch.object(
        delete_report,
        "read_registry",
        return_value=[{"path": str(scaffold.project_root)}],
    ):
        completion = scaffold.write("reports/REPORT-01-003.md", TASK_REPORT)
        before = completion.read_bytes()
        review_path = scaffold.reports / "REPORT-01-003-review.md"
        rc = delete_report.handler(
            argparse.Namespace(report_path=str(review_path), status_only=False)
        )
        preserved = completion.read_bytes()

    record = json.loads(capsys.readouterr().out)
    assert rc == EXIT_OK
    assert record["details"]["already_absent"] is True
    assert record["details"]["status_deleted"] is False
    assert record["details"]["launch_log_deleted"] is False
    assert preserved == before


def test_interrupted_two_artifact_cleanup_reruns_to_completion(capsys):
    """An interruption between the review and completion deletions leaves a
    recoverable subset; the rerun finishes without failing on the absent
    review artifact and without touching unrelated reports."""
    with project_scaffold(cartopian_toml=_config()) as scaffold, mock.patch.object(
        delete_report,
        "read_registry",
        return_value=[{"path": str(scaffold.project_root)}],
    ):
        completion = scaffold.write("reports/REPORT-01-003.md", TASK_REPORT)
        review_path = scaffold.write(
            "reports/REPORT-01-003-review.md", "# REPORT-01-003-review\n"
        )
        unrelated = scaffold.write("reports/REPORT-01-002.md", "# REPORT-01-002\n")
        unrelated_before = unrelated.read_bytes()
        Path(str(completion) + ".status").write_text("state=exited\n", encoding="utf-8")
        Path(str(review_path) + ".status").write_text("state=exited\n", encoding="utf-8")

        # First pass removes the review artifact, then is interrupted before
        # the completion artifact is cleared.
        assert (
            delete_report.handler(
                argparse.Namespace(report_path=str(review_path), status_only=False)
            )
            == EXIT_OK
        )

        # The rerun repeats the full two-artifact cleanup set.
        rerun_review = delete_report.handler(
            argparse.Namespace(report_path=str(review_path), status_only=False)
        )
        rerun_completion = delete_report.handler(
            argparse.Namespace(report_path=str(completion), status_only=False)
        )

        assert rerun_review == EXIT_OK
        assert rerun_completion == EXIT_OK
        assert not completion.exists()
        assert not review_path.exists()
        assert not Path(str(completion) + ".status").exists()
        assert not Path(str(review_path) + ".status").exists()
        assert unrelated.read_bytes() == unrelated_before

    capsys.readouterr()


def test_close_audit_and_cleanup_reject_malformed_report_names_in_parity(capsys):
    """Audit and cleanup consume one authoritative report-name grammar: a
    malformed name is neither an audit blocker nor a deletable report, so the
    two surfaces can never deadlock on it."""
    from cli import report_identity

    with project_scaffold(cartopian_toml=_config()) as scaffold, mock.patch.object(
        delete_report,
        "read_registry",
        return_value=[{"path": str(scaffold.project_root)}],
    ):
        scaffold.write("prompts/PROMPT-01-003.md", "# PROMPT-01-003\n")
        scaffold.write("reports/REPORT-01-003.md", TASK_REPORT)
        scaffold.write(
            "reports/REPORT-01-003-review.md", "# REPORT-01-003-review\n"
        )
        garbage = scaffold.write(
            "reports/REPORT-01-003-garbage.md", "# not a task report\n"
        )

        rc_audit = close_audit.handler(
            argparse.Namespace(project_path=str(scaffold.project_root))
        )
        rc_delete = delete_report.handler(
            argparse.Namespace(report_path=str(garbage), status_only=False)
        )
        garbage_survives = garbage.exists()

    record = json.loads(capsys.readouterr().out.splitlines()[0])
    assert rc_audit == EXIT_OK
    unresolved = {
        Path(item["path"]).name for item in record["unresolved_reports"]
    }
    # Both well-formed task-scoped identities surface; the malformed name is
    # not classified as a task report at all.
    assert unresolved == {"REPORT-01-003.md", "REPORT-01-003-review.md"}
    assert all(
        "REPORT-01-003-garbage.md" not in reason
        for reason in record["blocking_reasons"]
    )
    # Cleanup rejects the same malformed name the audit ignored.
    assert rc_delete == EXIT_FAIL
    assert garbage_survives
    # One grammar governs both surfaces.
    assert report_identity.nn_nnn_for_report_name("REPORT-01-003-garbage.md") is None
    assert not report_identity.REPORT_FILENAME_RE.match("REPORT-01-003-garbage.md")
    assert not hasattr(close_audit, "_REPORT_TASK_RE")


# --- Bundles and packets ------------------------------------------------------


def test_task_bundle_emits_both_task_report_identities(capsys):
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        task_path = _task(scaffold, "open")
        scaffold.write("phases/PHASE-01-demo.md", "# PHASE-01: Demo\n")
        args = argparse.Namespace(task_path=str(task_path))
        rc = task_bundle.handler(args)

    record = json.loads(capsys.readouterr().out)
    root = None
    assert rc == EXIT_OK
    assert record["expected_report_path"].endswith("/reports/REPORT-01-003.md")
    assert record["expected_review_report_path"].endswith(
        "/reports/REPORT-01-003-review.md"
    )


def test_handoff_packet_review_names_review_slot_and_preserved_completion(capsys):
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        task_path, report_path, _prompt, _context = _captured_review_setup(scaffold)
        args = argparse.Namespace(task_path=str(task_path), role="reviewer")
        rc = handoff_packet.handler(args)

    record = json.loads(capsys.readouterr().out)
    assert rc == EXIT_OK
    assert record["expected_report_path"].endswith(
        "/reports/REPORT-01-003-review.md"
    )
    assert record["expected_report_variant"] == "review"
    assert record["completion_report_path"] == str(report_path.resolve())


def test_handoff_packet_task_run_keeps_compatibility_report_path(capsys):
    with project_scaffold(cartopian_toml=_config()) as scaffold:
        task_path = _task(scaffold, "in-progress")
        args = argparse.Namespace(task_path=str(task_path), role="coder")
        rc = handoff_packet.handler(args)

    record = json.loads(capsys.readouterr().out)
    assert rc == EXIT_OK
    assert record["expected_report_path"].endswith("/reports/REPORT-01-003.md")
    assert record["expected_report_variant"] == "task"
    assert record["completion_report_path"] == record["expected_report_path"]


# --- Planning-review non-regression -------------------------------------------


def test_planning_review_report_identity_is_unchanged():
    from cli import report_identity

    assert (
        report_identity.variant_for_report_name("REPORT-PLAN-001-baseline.md")
        == "planning-review"
    )
    assert delete_report.REPORT_FILENAME_RE.match("REPORT-PLAN-001-baseline.md")
    # A planning slug ending in "-review" stays a planning identity; the task
    # grammar's -review marker binds only to the NN-NNN form.
    assert (
        report_identity.variant_for_report_name("REPORT-PLAN-001-baseline-review.md")
        == "planning-review"
    )


def test_planning_review_filenames_never_gain_review_suffix():
    variant, err = parse_report._infer_variant(
        Path("/proj/reports/REPORT-PLAN-001-baseline.md"),
        "Status: complete\n\n## Identity\n\n- Review ID: REVIEW-PLAN-001\n\n"
        "## Verdict\n\napprove\n",
    )
    assert err is None
    assert variant == "planning-review"


# --- Review-off flow and activation boundary ----------------------------------


def test_review_off_flow_requires_only_completion_report(capsys):
    """With task-closure review off, the compatibility report is sufficient.

    An accepted completion report at ``REPORT-NN-NNN.md`` routes the task
    directly toward closure; no review report is created, expected, or
    required, and the review-report slot stays empty.
    """
    off_config = (
        _config()
        .replace(
            'auto_launch = ["task_review", "planning_review"]',
            'auto_launch = ["planning_review"]',
        )
        .replace(
            'task_closure = "required"\ntask_role = "reviewer"',
            'task_closure = "off"',
        )
    )
    with project_scaffold(cartopian_toml=off_config) as scaffold:
        _task(scaffold, "in-progress")
        report_path = scaffold.write("reports/REPORT-01-003.md", TASK_REPORT)
        review_report_path = scaffold.reports / "REPORT-01-003-review.md"
        args = argparse.Namespace(report_path=str(report_path), variant=None)
        rc = report_action.handler(args)
        review_slot_used = review_report_path.exists()

    record = json.loads(capsys.readouterr().out)
    assert rc == EXIT_OK
    assert record["variant"] == "task"
    assert record["verdict"] == "accepted"
    assert record["target_task_status"] == "done"
    assert record["review_path"] is None
    assert not review_slot_used


def test_install_update_guidance_names_split_report_activation_boundary():
    """Install/update guidance communicates the activation boundary.

    Split-report behavior activates only when the corrected release is
    installed and any affected running process is proven fresh; a
    pre-activation session must not claim the new behavior or reference the
    future review-report path.
    """
    repo_root = Path(__file__).resolve().parents[3]
    text = (repo_root / "install-cartopian.md").read_text(encoding="utf-8")
    assert "REPORT-NN-NNN-review.md" in text
    assert "proven fresh" in text
    assert "do not claim split-report behavior" in text
