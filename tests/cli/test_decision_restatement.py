"""A superseding decision may restate a ruling by pinned pointer, not by copy.

RED: extending a locked ruling to one more unit required a superseding
decision that copied the retired decision word for word and repeated every
binding. The copy carried no information the retired file did not already
hold, cost a full decision to author, and could be mistranscribed.
GREEN: ``Restates: DEC-NNN`` names the ruling instead. ``write-decision`` pins
it to the target's exact bytes, every reader treats the restated text --
ruling, markers, and bindings -- as the restating decision's own, and a
pointer that no longer resolves fails closed.
"""
from __future__ import annotations

import re

from cli import request_trace, trace_binding
from cli.commands import write_decision
from cli.request_trace import RequestRefusal
from tests.cli.test_fail_open_defects import invoke
from tests.cli.test_task_authority_inheritance import TaskAuthorityFixture

_DIGEST_LINE = re.compile(r"^Restates: DEC-003 sha256:[0-9a-f]{64}$", re.MULTILINE)


class RestatementFixture(TaskAuthorityFixture):
    def write(self, dec_id: str, body: str):
        return invoke(
            write_decision.handler, project_root=str(self.root), dec_id=dec_id,
            title=f"{dec_id} title", date="2026-10-10", status=None,
            supersedes=None, content=body, content_file=None,
        )

    def extension(self, dec_id: str, restates: str, supersedes: str, binding: str) -> str:
        return (
            f"# {dec_id}: Extend {restates} to TASK-01-001\n\n"
            f"Date: 2026-10-10\nStatus: locked\nSupersedes: {supersedes}\n"
            f"Restates: {restates}\n\n"
            f"## Extension\n\n{binding}\n"
        )

    def governed_by_unbound_ruling(self):
        """DEC-003 names the task but binds its ruling only to a checkpoint."""
        session, original, task = self.baseline()
        ruling = self.named_checkpoint_ruling(session, "DEC-003", "PLAN-BUILD-01-002")
        self.task_correction(session)
        with self.assertRaises(RequestRefusal) as caught:
            request_trace.context_for_task_assignment(self.root, task)
        self.assertEqual(caught.exception.rule, "governing-decision-evidence-unbound")
        return session, task, ruling


class PointerRestatementTests(RestatementFixture):
    def test_a_pointer_extends_the_ruling_to_a_new_unit(self) -> None:
        _session, task, ruling = self.governed_by_unbound_ruling()
        code, records, err = self.write(
            "DEC-004",
            self.extension("DEC-004", "DEC-003", "DEC-003",
                           f"Operator request evidence for: task:TASK-01-001: {ruling}"),
        )
        self.assertEqual(code, 0, err)
        written = (self.root / "decisions/DEC-004.md").read_text(encoding="utf-8")
        self.assertRegex(written, _DIGEST_LINE)
        self.assertNotIn("retain export provenance", written)  # no copy of the ruling
        self.assertEqual(records[0]["details"]["restates"]["decision"], "DEC-003")

        context = request_trace.context_for_task_assignment(self.root, task)
        self.assertIn(ruling, context.evidence_ids)
        # The retired decision's own binding is inherited, not repeated.
        effective = trace_binding.effective_decision_text(self.root, "DEC-004")
        self.assertIn("Operator request evidence for: planning:PLAN-BUILD-01-002", effective)
        self.assertEqual(trace_binding.decision_header(effective, "Supersedes"), "DEC-003")

    def test_a_restatement_can_itself_be_restated(self) -> None:
        session, task, ruling = self.governed_by_unbound_ruling()
        self.assertEqual(self.write("DEC-004", self.extension(
            "DEC-004", "DEC-003", "DEC-003",
            f"Operator request evidence for: task:TASK-01-001: {ruling}"))[0], 0)
        self.seed_planned_task("TASK-01-002", "BUILD-01-002")
        (self.root / "IMPLEMENTATION_PLAN.md").write_text("# Plan\n- `BUILD-01-001`\n- `BUILD-01-002`\n")
        (self.root / "phases/PHASE-01.md").write_text("# Phase\n- `BUILD-01-001`\n- `BUILD-01-002`\n")
        code, _records, err = self.write("DEC-005", self.extension(
            "DEC-005", "DEC-004", "DEC-004",
            f"Operator request evidence for: task:TASK-01-002: {ruling}"))
        self.assertEqual(code, 0, err)
        effective = trace_binding.effective_decision_text(self.root, "DEC-005")
        for marker in ("task:TASK-01-002", "task:TASK-01-001", "planning:PLAN-BUILD-01-002"):
            self.assertIn(f"Operator request evidence for: {marker}", effective)
        self.assertIn(ruling, request_trace.context_for_task_assignment(self.root, task).evidence_ids)

    def test_restated_markers_authorize_through_the_restating_decision(self) -> None:
        identity = "sha256:" + "a" * 64
        (self.root / "decisions").mkdir(exist_ok=True)
        (self.root / "decisions/DEC-001.md").write_text(
            "# DEC-001: Leave it out\n\nDate: 2026-10-01\nStatus: locked\nSupersedes: none\n\n"
            f"## Decision\n\nOut-of-plan request: {identity}\n",
            encoding="utf-8",
        )
        self.assertEqual(trace_binding.out_of_plan_dispositions(self.root), {identity: "decisions/DEC-001.md"})
        code, _records, err = self.write(
            "DEC-002", self.extension("DEC-002", "DEC-001", "DEC-001", "Extends the ruling.")
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(trace_binding.out_of_plan_dispositions(self.root), {identity: "decisions/DEC-002.md"})


class RestatementRefusalTests(RestatementFixture):
    def test_writer_refuses_a_pointer_it_cannot_pin(self) -> None:
        _session, _task, ruling = self.governed_by_unbound_ruling()
        binding = f"Operator request evidence for: task:TASK-01-001: {ruling}"
        cases = {
            "decision-restatement-not-superseded": self.extension("DEC-004", "DEC-003", "none", binding),
            "decision-restatement-target-missing": self.extension("DEC-004", "DEC-009", "DEC-009", binding),
            "decision-restatement-digest-mismatch": self.extension(
                "DEC-004", "DEC-003 sha256:" + "0" * 64, "DEC-003", binding),
            "decision-restatement-malformed": self.extension("DEC-004", "the earlier ruling", "DEC-003", binding),
            "decision-restatement-cycle": self.extension("DEC-004", "DEC-004", "DEC-004", binding),
        }
        for code_name, body in cases.items():
            with self.subTest(code_name):
                code, _records, err = self.write("DEC-004", body)
                self.assertEqual(code, 1)
                self.assertIn(code_name, err)
                self.assertFalse((self.root / "decisions/DEC-004.md").exists())

    def test_a_changed_target_fails_closed(self) -> None:
        _session, task, ruling = self.governed_by_unbound_ruling()
        self.assertEqual(self.write("DEC-004", self.extension(
            "DEC-004", "DEC-003", "DEC-003",
            f"Operator request evidence for: task:TASK-01-001: {ruling}"))[0], 0)
        target = self.root / "decisions/DEC-003.md"
        target.write_text(target.read_text(encoding="utf-8") + "\nAn edit.\n", encoding="utf-8")

        with self.assertRaises(RequestRefusal) as caught:
            request_trace.context_for_task_assignment(self.root, task)
        self.assertEqual(caught.exception.rule, "decision-restatement-digest-mismatch")
        problems = trace_binding.restatement_problems(self.root)
        self.assertEqual(
            [(item["decision"], item["code"]) for item in problems],
            [("decisions/DEC-004.md", "decision-restatement-digest-mismatch")],
        )
