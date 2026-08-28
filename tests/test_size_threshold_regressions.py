"""Regression coverage: no arbitrary size threshold rejects legitimate work.

Cartopian formerly enforced several unauthorized byte ceilings — a 4 KiB
prose-section budget family, a 64 KiB typed-payload section budget, a
24 KiB operator-request ceiling, a 256 KiB preserved-completion-evidence
ceiling, and a replacement 1 MiB typed-input ceiling. None had operator or
external authority, and the 256 KiB ceiling blocked a real project's
task-closure review (a 270,802-byte resource with a 298,122-byte accepted
completion report).

These tests pin the corrected contract at every former boundary:

- assignment composition, mediated payload materialization, and prompt
  validation succeed at and beyond every former threshold, byte-exact;
- preserved coder completion evidence stays byte-identical and reviewable
  at any size through capture, prompt binding, preflight, handoff-packet,
  review-context, and review dispatch — on CLI and MCP equally;
- mutation, missing-file, malformed-schema, wrong-variant, wrong-path, and
  stale-context failures still fail closed;
- diagnostic launch-log retention bounds never bound artifacts.

Large fixtures are generated in-test; nothing huge is committed.
"""
import argparse
import contextlib
import hashlib
import io
import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from cli import assignment_inputs, output_safety, prompt_composer, request_trace
from cli.commands import dispatch, parse_report
from cli.main import EXIT_OK, build_parser
from tests.scaffold import project_scaffold

# The observed production shape.
OBSERVED_RESOURCE_BYTES = 270_802
OBSERVED_REPORT_BYTES = 298_122
# A substantially larger shape proving the fix is architectural, not a
# raised constant: past 4 MiB, an order of magnitude over every former limit.
LARGE_REPORT_BYTES = 4 * 1048576 + 7

FORMER_BOUNDARIES = (
    4095, 4096, 4097,
    65535, 65536, 65537,
    262143, 262144, 262145,
    1048575, 1048576, 1048577,
)

_TOML = """[project]
id = "size-threshold-regressions"
name = "size-threshold-regressions"
project_schema_version = "v0.12.0"
work_roots = ["tool-repo"]

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

_TASK = """# TASK-01-003: Demo

Phase: PHASE-01
Plan ref: BUILD-01-003
Work root: tool-repo
Assignee: coder
"""

_REPORT_HEAD = """# REPORT-01-003

Status: complete

## Identity

- Work root: tool-repo

## Completion evidence

"""

_REPORT_TAIL = """

## Remaining risks

None.

## Ready to close

yes
"""


def _report_of_size(total_bytes: int) -> str:
    """A valid task-completion publication padded to exactly ``total_bytes``."""
    fixed = len(_REPORT_HEAD.encode("utf-8")) + len(_REPORT_TAIL.encode("utf-8"))
    pad = total_bytes - fixed
    if pad < 1:
        raise ValueError(f"target too small for the report skeleton: {total_bytes}")
    line = "evidence line describing verified behavior in detail\n"
    body = line * (pad // len(line))
    body += "x" * (pad - len(body.encode("utf-8")))
    report = _REPORT_HEAD + body + _REPORT_TAIL
    assert len(report.encode("utf-8")) == total_bytes
    return report


def _payload_of_size(total_bytes: int) -> str:
    head = "# Ledger\n"
    return head + "x" * (total_bytes - len(head.encode("utf-8")))


def _run_cli(*argv):
    parser = build_parser()
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            args = parser.parse_args(list(argv))
            handler = getattr(args, "_handler", None)
            code = handler(args) if handler is not None else 2
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
    records = [
        json.loads(line) for line in out.getvalue().splitlines() if line.strip()
    ]
    return code, records, err.getvalue()


def _map_work_root(scaffold) -> None:
    work_root = scaffold.root / "tool-repo"
    work_root.mkdir(exist_ok=True)
    scaffold.write(
        "cartopian.local.toml", f'[work_roots]\ntool-repo = "{work_root}"\n'
    )


def _review_setup(scaffold, report_text: str):
    """Seed an in-review task with request evidence, coder report, and prompt."""
    _map_work_root(scaffold)
    task_path = scaffold.write("tasks/in-review/TASK-01-003.md", _TASK)
    scaffold.capture_request(
        request_id="REQUEST-001",
        unit="task:TASK-01-003",
        text="Implement the governed change and submit it for review.",
    )
    report_path = scaffold.write("reports/REPORT-01-003.md", report_text)
    context = request_trace.context_for_task(
        scaffold.project_root,
        task_path,
        require_completion_evidence=True,
    )
    prompt_path = scaffold.write(
        "prompts/PROMPT-01-003.md",
        request_trace.upsert_request_sections(
            "# Review task completion\n", context.section
        ),
    )
    return task_path, report_path, prompt_path, context


class TestPreservedCompletionEvidenceSizeIndependence(unittest.TestCase):
    def test_capture_and_rebind_across_former_256kib_boundary(self) -> None:
        for size in (262143, 262144, 262145):
            with self.subTest(size=size):
                report_text = _report_of_size(size)
                with project_scaffold(cartopian_toml=_TOML) as scaffold:
                    task_path, report_path, prompt_path, context = _review_setup(
                        scaffold, report_text
                    )
                    captured = context.captured_completion
                    self.assertIsNotNone(captured)
                    self.assertEqual(captured.content_bytes, size)
                    self.assertEqual(captured.content, report_text)
                    expected_identity = "sha256:" + hashlib.sha256(
                        report_text.encode("utf-8")
                    ).hexdigest()
                    self.assertEqual(captured.content_identity, expected_identity)
                    # Preflight rebind against the generated prompt succeeds
                    # and re-verifies the byte-identical artifact.
                    rebound = request_trace.context_for_task(
                        scaffold.project_root,
                        task_path,
                        prompt_text=prompt_path.read_text(encoding="utf-8"),
                        require_completion_evidence=True,
                    )
                    self.assertEqual(
                        rebound.captured_completion.content_identity,
                        expected_identity,
                    )
                    preflight = request_trace.preflight_prompt_binding(
                        rebound, prompt_path.read_text(encoding="utf-8")
                    )
                    self.assertTrue(preflight["ok"], preflight)
                    self.assertEqual(report_path.read_text(encoding="utf-8"),
                                     report_text)

    def test_observed_production_shape_passes_review_bootstrap(self) -> None:
        """The reproduced 298,122-byte report clears the full bootstrap."""
        report_text = _report_of_size(OBSERVED_REPORT_BYTES)
        resource_text = _payload_of_size(OBSERVED_RESOURCE_BYTES)
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            task_path, report_path, prompt_path, _context = _review_setup(
                scaffold, report_text
            )
            scaffold.write("resources/governed-resource.md", resource_text)
            before = report_path.read_bytes()
            self.assertEqual(len(before), OBSERVED_REPORT_BYTES)

            # review-context (CLI) with prompt-binding preflight.
            code, records, err = _run_cli(
                "review-context",
                str(scaffold.project_root),
                "--review-kind", "task-closure",
                "--task", str(task_path),
                "--prompt", str(prompt_path),
            )
            self.assertEqual(code, EXIT_OK, err)
            captured = records[0]["captured_completion_evidence"]
            self.assertEqual(captured["content_bytes"], OBSERVED_REPORT_BYTES)

            # handoff-packet (CLI) for the review role.
            code_hp, records_hp, err_hp = _run_cli(
                "handoff-packet", str(task_path), "--role", "reviewer"
            )
            self.assertEqual(code_hp, EXIT_OK, err_hp)

            # MCP surfaces return equivalent outcomes.
            from mcp_server import server

            mcp_rc = server._invoke_cli(
                "review-context",
                [
                    str(scaffold.project_root),
                    "--review-kind", "task-closure",
                    "--task", str(task_path),
                    "--prompt", str(prompt_path),
                ],
            )
            self.assertEqual(mcp_rc["exit_code"], EXIT_OK)
            self.assertEqual(
                mcp_rc["records"][0]["captured_completion_evidence"],
                captured,
            )
            mcp_hp = server._invoke_cli(
                "handoff-packet", [str(task_path), "--role", "reviewer"]
            )
            self.assertEqual(mcp_hp["exit_code"], EXIT_OK)

            # The accepted evidence never changed a byte.
            self.assertEqual(report_path.read_bytes(), before)

    def test_substantially_larger_report_is_architecturally_unbounded(self) -> None:
        report_text = _report_of_size(LARGE_REPORT_BYTES)
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            task_path, report_path, prompt_path, context = _review_setup(
                scaffold, report_text
            )
            self.assertEqual(
                context.captured_completion.content_bytes, LARGE_REPORT_BYTES
            )
            code, records, err = _run_cli(
                "review-context",
                str(scaffold.project_root),
                "--review-kind", "task-closure",
                "--task", str(task_path),
                "--prompt", str(prompt_path),
            )
            self.assertEqual(code, EXIT_OK, err)
            self.assertEqual(
                records[0]["captured_completion_evidence"]["content_bytes"],
                LARGE_REPORT_BYTES,
            )
            self.assertEqual(
                len(report_path.read_bytes()), LARGE_REPORT_BYTES
            )

    def test_review_dispatch_launches_and_preserves_large_report(self) -> None:
        report_text = _report_of_size(OBSERVED_REPORT_BYTES)
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            task_path, report_path, _prompt, _context = _review_setup(
                scaffold, report_text
            )
            before = report_path.read_bytes()
            launched = {}

            def popen(*argv, **kwargs):
                launched["argv"] = argv
                return SimpleNamespace(pid=4242)

            args = argparse.Namespace(
                task_path=str(task_path), prompt=None, role="reviewer"
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
            self.assertEqual(rc, EXIT_OK, err.getvalue())
            self.assertIn("argv", launched)
            self.assertEqual(report_path.read_bytes(), before)


class TestCompletionEvidenceStillFailsClosed(unittest.TestCase):
    def _bound_setup(self, scaffold):
        task_path, report_path, prompt_path, _context = _review_setup(
            scaffold, _report_of_size(262145)
        )
        return task_path, report_path, prompt_path

    def _rebind(self, scaffold, task_path, prompt_path):
        return request_trace.context_for_task(
            scaffold.project_root,
            task_path,
            prompt_text=prompt_path.read_text(encoding="utf-8"),
            require_completion_evidence=True,
        )

    def test_mutated_report_fails_closed(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            task_path, report_path, prompt_path = self._bound_setup(scaffold)
            mutated = report_path.read_text(encoding="utf-8").replace(
                "evidence line", "tampered line", 1
            )
            report_path.write_text(mutated, encoding="utf-8")
            with self.assertRaises(request_trace.RequestRefusal) as ctx:
                self._rebind(scaffold, task_path, prompt_path)
            self.assertEqual(ctx.exception.rule, "stale-request-context")

    def test_missing_report_fails_closed(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            task_path, report_path, prompt_path = self._bound_setup(scaffold)
            report_path.unlink()
            with self.assertRaises(request_trace.RequestRefusal) as ctx:
                self._rebind(scaffold, task_path, prompt_path)
            self.assertEqual(
                ctx.exception.rule, "missing-coder-completion-evidence"
            )

    def test_malformed_schema_fails_closed(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            task_path = scaffold.write("tasks/in-review/TASK-01-003.md", _TASK)
            scaffold.capture_request(
                request_id="REQUEST-001",
                unit="task:TASK-01-003",
                text="Implement the governed change.",
            )
            scaffold.write(
                "reports/REPORT-01-003.md",
                _report_of_size(262145).replace("Status: complete\n", ""),
            )
            with self.assertRaises(request_trace.RequestRefusal) as ctx:
                request_trace.context_for_task(
                    scaffold.project_root,
                    task_path,
                    require_completion_evidence=True,
                )
            self.assertEqual(
                ctx.exception.rule, "malformed-coder-completion-evidence"
            )

    def test_wrong_variant_in_completion_slot_fails_closed(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            task_path = scaffold.write("tasks/in-review/TASK-01-003.md", _TASK)
            scaffold.capture_request(
                request_id="REQUEST-001",
                unit="task:TASK-01-003",
                text="Implement the governed change.",
            )
            root = scaffold.project_root.resolve()
            scaffold.write(
                "reports/REPORT-01-003.md",
                f"""# REPORT-01-003-review

Status: complete
Request alignment: unavailable-for-legacy
Request evidence: none

## Identity

- Review ID: REVIEW-01-003
- Prompt path: {root / 'prompts' / 'PROMPT-01-003.md'}
- Task path: {root / 'tasks' / 'in-review' / 'TASK-01-003.md'}
- Review file path: {root / 'reviews' / 'REVIEW-01-003.md'}

## Evidence reviewed

The preserved coder completion report.

## Verdict

approve

## Blocking findings

none.
""",
            )
            with self.assertRaises(request_trace.RequestRefusal) as ctx:
                request_trace.context_for_task(
                    scaffold.project_root,
                    task_path,
                    require_completion_evidence=True,
                )
            self.assertEqual(
                ctx.exception.rule, "malformed-coder-completion-evidence"
            )

    def test_wrong_bound_path_fails_closed(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            task_path, _report, prompt_path = self._bound_setup(scaffold)
            text = prompt_path.read_text(encoding="utf-8")
            target = [
                line for line in text.splitlines()
                if line.startswith("Completion report path: ")
            ][0]
            prompt_path.write_text(
                text.replace(target, "Completion report path: /elsewhere/REPORT-01-003.md"),
                encoding="utf-8",
            )
            with self.assertRaises(request_trace.RequestRefusal) as ctx:
                self._rebind(scaffold, task_path, prompt_path)
            self.assertEqual(ctx.exception.rule, "stale-request-context")

    def test_stale_context_fails_preflight(self) -> None:
        with project_scaffold(cartopian_toml=_TOML) as scaffold:
            task_path, _report, prompt_path = self._bound_setup(scaffold)
            context = self._rebind(scaffold, task_path, prompt_path)
            edited = prompt_path.read_text(encoding="utf-8").replace(
                "Request state: resolved", "Request state: resolved (edited)"
            )
            preflight = request_trace.preflight_prompt_binding(context, edited)
            self.assertFalse(preflight["ok"])
            self.assertEqual(preflight["rule"], "stale-request-context")


class TestAssignmentInputBoundaries(unittest.TestCase):
    _TASK_WITH_DELIVERABLE = """# TASK-01-002: Update the ledger

Phase: PHASE-01
Plan ref: BUILD-01-002
Work root: tool-repo
Assignee: coder
Deliverable: project:resources/governed-resource.md

## Goal

Update the governed resource in place.
"""

    def test_payloads_are_byte_exact_at_every_former_boundary(self) -> None:
        sizes = FORMER_BOUNDARIES + (OBSERVED_RESOURCE_BYTES,)
        for size in sizes:
            with self.subTest(size=size):
                resource = _payload_of_size(size)
                with project_scaffold(cartopian_toml=_TOML) as scaffold:
                    task_path = scaffold.write(
                        "tasks/in-progress/TASK-01-002.md",
                        self._TASK_WITH_DELIVERABLE,
                    )
                    scaffold.write("resources/governed-resource.md", resource)
                    body, manifest = prompt_composer.materialize_input_sections(
                        scaffold.project_root,
                        task_path,
                        "# Prompt\n\n## Your task\n\nUpdate the resource.\n",
                    )
                    (entry,) = assignment_inputs.extract_payload_blocks(body)
                    self.assertTrue(entry["verified"], entry["error"])
                    self.assertEqual(entry["content"], resource)
                    self.assertEqual(entry["declared_bytes"], size)
                    self.assertEqual(manifest[0]["content_bytes"], size)
                    self.assertEqual(
                        manifest[0]["content_sha256"],
                        hashlib.sha256(resource.encode("utf-8")).hexdigest(),
                    )

    def test_prompt_validation_raises_no_size_findings(self) -> None:
        contract = prompt_composer.load_contract()
        order = contract["sections"]["order"]
        for size in (4095, 4096, 4097, 65537):
            with self.subTest(size=size):
                sections = []
                for name in order:
                    if name in assignment_inputs.CHANNEL_SECTIONS.values():
                        continue
                    heading = f"## {name}\n\n"
                    filler_len = max(
                        1, size - len(heading.encode("utf-8")) - 1
                    )
                    body = "y" * filler_len + "\n"
                    if name == "Outcome and done criteria":
                        body = "- [ ] measurable outcome holds\n" + body
                    sections.append(heading + body)
                prompt = "# Assignment\n\n" + "\n".join(sections)
                findings = prompt_composer.validate_prompt(prompt, contract)
                self.assertEqual(
                    [f for f in findings if "budget" in f["code"]
                     or "oversize" in f["code"]],
                    [],
                    findings,
                )

    def test_operator_request_capture_is_unbounded(self) -> None:
        for size in (24 * 1024 - 1, 24 * 1024, 24 * 1024 + 1, 1048577):
            with self.subTest(size=size):
                text = "requirement detail " * (size // 19)
                text += "z" * (size - len(text.encode("utf-8")))
                with project_scaffold(cartopian_toml=_TOML) as scaffold:
                    scaffold.write("tasks/in-review/TASK-01-003.md", _TASK)
                    scaffold.capture_request(
                        request_id="REQUEST-001",
                        unit="task:TASK-01-003",
                        text=text,
                    )
                    (record,) = request_trace.load_records(
                        scaffold.project_root
                    )
                    self.assertEqual(record.text, text)


class TestDiagnosticBoundsAreNotArtifactBounds(unittest.TestCase):
    def test_retained_log_limits_do_not_bound_reports(self) -> None:
        # The launch-log retention defaults stay diagnostic-only: a report
        # far larger than the retained-log byte limit still parses as an
        # accepted task-completion publication.
        report_text = _report_of_size(OBSERVED_REPORT_BYTES)
        self.assertGreater(
            len(report_text.encode("utf-8")), output_safety.DEFAULT_LOG_BYTE_LIMIT
        )
        self.assertTrue(parse_report._schema_ok("task", report_text))
        self.assertEqual(
            parse_report.extract_routing_status(report_text), "complete"
        )
        self.assertIs(parse_report.extract_ready_for_review(report_text), True)


if __name__ == "__main__":
    unittest.main()
