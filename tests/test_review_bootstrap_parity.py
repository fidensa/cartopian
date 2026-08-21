"""The task-closure review bootstrap agrees with the validator and router.

Evidence gate (red-before-green):

- RED: ``request_trace._report_ready`` privately required the readiness line
  to be exactly ``yes``/``no``, while ``report-action`` accepted the
  documented ``yes — rationale`` form and ``validate-report`` never examined
  the value at all. A publication every public surface accepted — routed to
  ``in-review`` as an accepted, ready-for-review task report — was then
  refused by review-context, task-closure write-prompt, and handoff
  preflight as ``malformed-coder-completion-evidence``, while
  ``correct-report`` refused with ``no-defect-to-correct``: a hidden
  review-only acceptance rule with no recovery.
- GREEN: one canonical parser pair
  (``parse_report.extract_ready_for_review`` /
  ``parse_report.extract_routing_status``) is consumed by the router, by
  validate-report's named ``readiness-value-valid`` check, and by the review
  bootstrap. The invariant these tests hold: a task-completion report that
  validate-report accepts and report-action routes as accepted/ready for
  review is admissible as preserved coder completion evidence for
  task-closure review binding; and a report whose readiness value is
  genuinely malformed fails closed on every surface with a named,
  mechanically correctable diagnostic.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli.commands import capture_request, handoff_packet
from cli.main import build_parser

CONFIG = """[project]
name = "Bootstrap parity"
id = "bootstrap-parity"
project_schema_version = "v0.10.0"

[reviews]
planning = "required"
planning_role = "reviewer"
task_closure = "required"
task_role = "reviewer"

[roles.pm]
description = "PM"
grants = ["pm-solo"]

[roles.coder]
description = "Coder"
agent = "cartopian-codex"

[roles.reviewer]
description = "Configured reviewer"
grants = ["reviewer-like"]
agent = "cartopian-codex"
"""

SOURCE_ROW = (
    "- Identity: Cartopian REQUIREMENTS.md and STANDARDS.md; "
    "Applicable context: active contract as of 2026-08-13; "
    "Status: current; Scope: runtime and containment constraints"
)
CONFLICT_ROW = (
    "- Status: resolved; Rule: current requirements govern product "
    "boundaries; Decision: implement only the bounded reviewed design"
)

TASK = f"""# TASK-07-001: Bootstrap parity fixture

Phase: PHASE-07
Plan ref: BUILD-07-001
Work root: n/a
Deliverable: n/a
Source guidance: task
Upstream trace: n/a

## Goal

Exercise the review bootstrap.

## Source guidance

### Authoritative sources

{SOURCE_ROW}

### Conflict resolution

{CONFLICT_ROW}

### Unverified claims

- none

## Acceptance

- [ ] The fixture routes through the review bootstrap.
"""

# A comparatively large evidence body: the divergence reproduced on a real
# 16 KB publication, so the fixture is deliberately not a minimal stub.
EVIDENCE_BODY = "\n\n".join(
    f"Paragraph {index}: the delivered change is proven by focused tests, "
    "the refusal set is closed, and the boundary that records evidence and "
    "the boundary that applies the verdict read one shared predicate, so "
    "no surface can hold a different opinion about the same publication."
    for index in range(1, 9)
)

# The reproduced readiness shape: the documented `yes` token followed by a
# short same-line rationale (the report skeleton explicitly permits it).
READY_LINE = (
    "yes — producer work and evidence are complete; this enters the "
    "required independent closure review and does not approve closure."
)

REPORT = f"""# REPORT-07-001

Status: complete

## Identity

- Work root: n/a

## Completion evidence

{EVIDENCE_BODY}

## Source evidence

### Authoritative sources

{SOURCE_ROW}

How each was applied: the requirements and standards fixed the boundaries
the change had to preserve.

### Conflict resolution

{CONFLICT_ROW}

### Unverified claims

- none

## Files changed

- `cli/example.py` — the bounded change under review.

## Remaining risks

- none

## Ready for review

{READY_LINE}
"""


class BootstrapFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "proj"
        for directory in (
            "tasks/open",
            "tasks/in-progress",
            "tasks/in-review",
            "tasks/done",
            "prompts",
            "reviews",
            "reports",
            "specs",
            "phases",
            "decisions",
        ):
            (self.root / directory).mkdir(parents=True)
        (self.root / "cartopian.toml").write_text(CONFIG, encoding="utf-8")
        (self.root / "IMPLEMENTATION_PLAN.md").write_text(
            "# Plan\n\n- `BUILD-07-001` — Bootstrap parity work.\n",
            encoding="utf-8",
        )
        (self.root / "phases/PHASE-07.md").write_text(
            "# PHASE-07\n\n- `BUILD-07-001` — Bootstrap parity work.\n",
            encoding="utf-8",
        )
        source = self.root / "request.txt"
        source.write_text(
            "Deliver the bootstrap parity fixture.", encoding="utf-8"
        )
        args = argparse.Namespace(
            project_root=str(self.root),
            request_id="REQUEST-001",
            unit="task:TASK-07-001",
            content_file=str(source),
            correction_of=None,
            captured_at="2026-07-27T12:00:00Z",
        )
        fixture_env = {
            key: value
            for key, value in os.environ.items()
            if key not in capture_request.NON_OPERATOR_MARKERS
        }
        with (
            mock.patch.dict(os.environ, fixture_env, clear=True),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(capture_request.handler(args), 0)
        self.task = self.root / "tasks/in-progress/TASK-07-001.md"
        self.task.write_text(TASK, encoding="utf-8")
        self.report = self.root / "reports/REPORT-07-001.md"
        self.report.write_text(REPORT, encoding="utf-8")

    def run_cli(self, *argv: str):
        parser = build_parser()
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
            stderr
        ):
            try:
                args = parser.parse_args(list(argv))
                handler = getattr(args, "_handler", None)
                code = handler(args) if handler is not None else 2
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 2
        records = [
            json.loads(line)
            for line in stdout.getvalue().splitlines()
            if line.strip()
        ]
        return code, records, stderr.getvalue()

    def move_to_review(self) -> Path:
        target = self.root / "tasks/in-review/TASK-07-001.md"
        os.replace(self.task, target)
        self.task = target
        return target

    def report_identity(self) -> str:
        from cli import report_identity

        return report_identity.content_identity(
            self.report.read_text(encoding="utf-8")
        )

    def check_map(self, record):
        return {item["name"]: item for item in record["checks"]}


class AcceptedPublicationSeamTests(BootstrapFixture):
    """The complete lifecycle seam for the reproduced accepted shape."""

    def test_every_surface_admits_the_routed_publication(self) -> None:
        identity = self.report_identity()

        # 1. The public validator accepts the publication, and the
        #    readiness value is a named check that passed — not unexamined.
        code, records, err = self.run_cli("validate-report", str(self.report))
        self.assertEqual(code, 0, err)
        record = records[0]
        self.assertTrue(record["ok"])
        self.assertEqual(record["report_content_identity"], identity)
        checks = self.check_map(record)
        self.assertIn("readiness-value-valid", checks)
        self.assertTrue(checks["readiness-value-valid"]["pass"])

        # 2. The router routes it as an accepted, ready-for-review report.
        code, records, err = self.run_cli("report-action", str(self.report))
        self.assertEqual(code, 0, err)
        self.assertEqual(records[0]["verdict"], "accepted")
        self.assertEqual(records[0]["target_task_status"], "in-review")
        self.assertEqual(records[0]["recommended_action"], "assign-review")

        # 3. The task is in review, as routed.
        self.move_to_review()

        # 4. Review-context admits the same bytes as preserved coder
        #    completion evidence, under the same immutable identity.
        code, records, err = self.run_cli(
            "review-context", str(self.root), "--review-kind", "task-closure",
            "--task", str(self.task),
        )
        self.assertEqual(code, 0, err)
        captured = records[0]["captured_completion_evidence"]
        self.assertEqual(captured["content_identity"], identity)
        self.assertEqual(captured["status"], "complete")
        self.assertTrue(captured["ready_to_close"])
        self.assertEqual(captured["source_path"], "reports/REPORT-07-001.md")

        # 5. The task-closure review prompt generates and binds the same
        #    preserved identity.
        code, records, err = self.run_cli(
            "write-prompt", str(self.root), "--prompt-id", "PROMPT-07-001",
            "--review-kind", "task-closure", "--task", str(self.task),
            "--content", "# Review task completion\n",
        )
        self.assertEqual(code, 0, err)
        prompt_text = (self.root / "prompts/PROMPT-07-001.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(f"Content identity: {identity}", prompt_text)
        self.assertIn("Coder status: complete", prompt_text)
        self.assertIn("Ready to close: yes", prompt_text)

        # 6. Handoff preflight re-verifies the bound prompt against the live
        #    artifact and captures the same immutable report identity.
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
            stderr
        ):
            rc = handoff_packet.handler(
                argparse.Namespace(task_path=str(self.task), role="reviewer")
            )
        self.assertEqual(rc, 0, stderr.getvalue())
        packet = json.loads(stdout.getvalue())
        self.assertEqual(
            packet["completion_report_path"], str(self.report.resolve())
        )
        trace = packet["request_trace"]
        self.assertEqual(
            trace["captured_completion_evidence"]["content_identity"],
            identity,
        )
        self.assertTrue(trace["preflight"]["ok"], trace["preflight"])

    def test_correct_report_still_finds_nothing_to_correct(self) -> None:
        """The accepted shape carries no defect — and no longer needs one."""
        code, records, err = self.run_cli(
            "correct-report", str(self.report),
            "--expected-identity", self.report_identity(),
            "--corrected-content",
            REPORT.replace("bounded change", "bounded corrected change"),
        )
        self.assertEqual(code, 1)
        self.assertIn("no-defect-to-correct", err)

    def test_identity_binding_refusals_are_preserved(self) -> None:
        """SHA-256 publication binding still refuses drifted bytes."""
        wrong = "sha256:" + "0" * 64
        code, records, err = self.run_cli(
            "validate-report", str(self.report), "--expected-identity", wrong
        )
        self.assertEqual(code, 1)
        self.assertIn("report-identity-mismatch", err)

        self.move_to_review()
        code, _records, err = self.run_cli(
            "write-prompt", str(self.root), "--prompt-id", "PROMPT-07-001",
            "--review-kind", "task-closure", "--task", str(self.task),
            "--content", "# Review task completion\n",
        )
        self.assertEqual(code, 0, err)
        self.report.write_text(
            REPORT.replace("bounded change", "mutated evidence"),
            encoding="utf-8",
        )
        code, _records, err = self.run_cli(
            "review-context", str(self.root), "--review-kind", "task-closure",
            "--task", str(self.task),
            "--prompt", str(self.root / "prompts/PROMPT-07-001.md"),
        )
        self.assertEqual(code, 1)
        self.assertIn("stale-request-context", err)


class MalformedPublicationParityTests(BootstrapFixture):
    """Genuinely malformed readiness values fail closed on every surface."""

    def write_report(self, ready_line: str, status: str = "complete") -> None:
        self.report.write_text(
            REPORT.replace(READY_LINE, ready_line).replace(
                "Status: complete", f"Status: {status}", 1
            ),
            encoding="utf-8",
        )

    def assert_bootstrap_refuses(self) -> None:
        self.move_to_review()
        code, _records, err = self.run_cli(
            "review-context", str(self.root), "--review-kind", "task-closure",
            "--task", str(self.task),
        )
        self.assertEqual(code, 1)
        self.assertIn("malformed-coder-completion-evidence", err)
        code, _records, err = self.run_cli(
            "write-prompt", str(self.root), "--prompt-id", "PROMPT-07-001",
            "--review-kind", "task-closure", "--task", str(self.task),
            "--content", "# Review task completion\n",
        )
        self.assertEqual(code, 1)
        self.assertIn("malformed-coder-completion-evidence", err)

    def test_unfilled_placeholder_fails_closed_everywhere(self) -> None:
        self.write_report("yes | no")

        code, records, _err = self.run_cli("validate-report", str(self.report))
        self.assertEqual(code, 1)
        check = self.check_map(records[0])["readiness-value-valid"]
        self.assertFalse(check["pass"])
        self.assertEqual(check["failure_class"], "mechanical")
        self.assertIn("yes or no", check["recovery"])

        code, records, _err = self.run_cli("report-action", str(self.report))
        self.assertEqual(code, 0)
        self.assertIsNone(records[0]["target_task_status"])

        self.assert_bootstrap_refuses()

    def test_prose_readiness_value_fails_closed_everywhere(self) -> None:
        self.write_report("ready once the reviewer confirms")

        code, records, _err = self.run_cli("validate-report", str(self.report))
        self.assertEqual(code, 1)
        self.assertFalse(
            self.check_map(records[0])["readiness-value-valid"]["pass"]
        )

        code, records, _err = self.run_cli("report-action", str(self.report))
        self.assertEqual(code, 0)
        self.assertIsNone(records[0]["target_task_status"])

        self.assert_bootstrap_refuses()

    def test_blocked_report_never_reaches_review_binding(self) -> None:
        """An incomplete publication is routed back, and the bootstrap
        refuses it for the same reason the router did not send it to review."""
        self.write_report("no — blocked on a missing input.", status="blocked")

        code, records, _err = self.run_cli("report-action", str(self.report))
        self.assertEqual(code, 0)
        self.assertEqual(records[0]["verdict"], "blocked")
        self.assertEqual(records[0]["target_task_status"], "in-progress")

        self.assert_bootstrap_refuses()

    def test_missing_readiness_section_fails_closed_everywhere(self) -> None:
        self.report.write_text(
            REPORT.replace(f"## Ready for review\n\n{READY_LINE}\n", ""),
            encoding="utf-8",
        )

        code, records, _err = self.run_cli("validate-report", str(self.report))
        self.assertEqual(code, 1)
        checks = self.check_map(records[0])
        self.assertFalse(checks["required-sections-present"]["pass"])
        # The missing heading is named once, by the sections check.
        self.assertTrue(checks["readiness-value-valid"]["pass"])

        self.assert_bootstrap_refuses()

    def test_placeholder_value_is_mechanically_correctable(self) -> None:
        """The named failure carries an actionable, hash-bound recovery."""
        self.write_report("yes | no")
        broken = self.report.read_text(encoding="utf-8")
        corrected = broken.replace("yes | no", READY_LINE)

        from cli import report_identity

        code, records, err = self.run_cli(
            "correct-report", str(self.report),
            "--expected-identity", report_identity.content_identity(broken),
            "--corrected-content", corrected,
        )
        self.assertEqual(code, 0, err)
        self.assertIn("readiness-value-valid", records[0]["resolved_checks"])
        self.assertEqual(records[0]["corrected_regions"], ["Ready for review"])

        # The corrected publication is admissible end to end.
        code, records, err = self.run_cli("validate-report", str(self.report))
        self.assertEqual(code, 0, err)
        self.move_to_review()
        code, records, err = self.run_cli(
            "review-context", str(self.root), "--review-kind", "task-closure",
            "--task", str(self.task),
        )
        self.assertEqual(code, 0, err)
        self.assertTrue(
            records[0]["captured_completion_evidence"]["ready_to_close"]
        )

    def test_correction_may_not_touch_more_than_the_value_line(self) -> None:
        self.write_report("yes | no")
        broken = self.report.read_text(encoding="utf-8")
        corrected = broken.replace("yes | no", READY_LINE).replace(
            "bounded change", "rewritten evidence"
        )

        from cli import report_identity

        code, _records, err = self.run_cli(
            "correct-report", str(self.report),
            "--expected-identity", report_identity.content_identity(broken),
            "--corrected-content", corrected,
        )
        self.assertEqual(code, 1)
        self.assertIn("correction-outside-defect-scope", err)


class McpParityTests(BootstrapFixture):
    """The MCP surface invokes the same handlers; hold the parity anyway."""

    def call(self, tool, arguments):
        from mcp_server import server

        return server.call_tool(tool, arguments)

    def test_cli_and_mcp_agree_on_the_accepted_publication(self) -> None:
        cli_code, cli_records, err = self.run_cli(
            "validate-report", str(self.report)
        )
        self.assertEqual(cli_code, 0, err)
        result = self.call(
            "validate_report", {"report_path": str(self.report)}
        )
        self.assertFalse(result["isError"], json.dumps(result))
        self.assertEqual(result["structuredContent"]["records"], cli_records)

        self.move_to_review()
        result = self.call(
            "review_context",
            {
                "project_root": str(self.root),
                "review_kind": "task-closure",
                "task": str(self.task),
            },
        )
        self.assertFalse(result["isError"], json.dumps(result))
        captured = result["structuredContent"]["records"][0][
            "captured_completion_evidence"
        ]
        self.assertEqual(
            captured["content_identity"], self.report_identity()
        )

    def test_cli_and_mcp_refuse_the_malformed_publication_alike(self) -> None:
        self.report.write_text(
            REPORT.replace(READY_LINE, "yes | no"), encoding="utf-8"
        )
        self.move_to_review()
        cli_code, _records, cli_err = self.run_cli(
            "review-context", str(self.root), "--review-kind", "task-closure",
            "--task", str(self.task),
        )
        result = self.call(
            "review_context",
            {
                "project_root": str(self.root),
                "review_kind": "task-closure",
                "task": str(self.task),
            },
        )
        self.assertEqual(cli_code, 1)
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["exit_code"], cli_code)
        self.assertEqual(
            result["structuredContent"]["stderr_lines"],
            [line for line in cli_err.splitlines() if line],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
