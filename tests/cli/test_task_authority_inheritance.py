"""Task corrections retain applicable governing authority without rebinding it."""
from cli import request_trace
from cli.request_trace import GovernedUnit, RequestRefusal
from tests.cli.test_evidence_resolver import PROJECT, ResolverCase


class TaskAuthorityInheritanceTests(ResolverCase):
    def baseline(self):
        session = self.session()
        self.bind(session)
        original = self.planning_exchange(session)
        self.assertEqual(self.lock_requirements()[0], 0)
        session.stop("The baseline is locked.")
        task = self.seed_planned_task()
        self.write_decision("DEC-001", f"Operator request evidence for: project:project: {original}")
        task.write_text(task.read_text() + "\n## References\n\n- DEC-001\n")
        return session, original, task

    def task_correction(self, session):
        correction = session.exchange(
            "For TASK-01-001, retain documented host gaps and verify the new installer later.",
            "The research completion boundary is clarified.",
        )
        self.write_decision("DEC-002", f"Operator request evidence for: task:TASK-01-001: {correction}")
        return correction

    def test_project_ruling_survives_a_task_correction_in_assignment_and_review(self):
        session, original, task = self.baseline()
        correction = self.task_correction(session)
        for context in (
            request_trace.context_for_task_assignment(self.root, task),
            request_trace.context_for_task(self.root, task),
        ):
            self.assertEqual(context.evidence_ids, [original, correction])
            self.assertEqual([r.unit for r in context.trace], [PROJECT, GovernedUnit("task", "TASK-01-001")])
            self.assertEqual([r.kind for r in context.trace], ["confirmation", "correction"])
            self.assertIn("no cloud storage", context.section)
            self.assertIn("verify the new installer later", context.section)
            self.assertEqual(context.summary.omitted(context.trace), context.summary.candidates_total - 2)

    def test_lookup_and_readiness_use_the_combined_authority_channel(self):
        from cli.commands import validate_task_readiness

        session, original, task = self.baseline()
        correction = self.task_correction(session)
        trace, summary, identity = request_trace.lookup_unit(self.root, GovernedUnit("task", "TASK-01-001"))
        context = request_trace.context_for_task_assignment(self.root, task)
        self.assertEqual([r.record_id for r in trace], [original, correction])
        self.assertEqual(identity, context.context_identity)
        record, _ = validate_task_readiness.evaluate(self.root, task, task.read_text())
        check = next(c for c in record["checks"] if c["name"] == "request-trace-valid")
        self.assertTrue(check["pass"], check["reason"])
        prompt = request_trace.upsert_request_sections("# Assignment\n", context.section)
        self.assertTrue(request_trace.preflight_prompt_binding(context, prompt)["ok"])
        self.assertFalse(request_trace.preflight_prompt_binding(context, prompt.replace("no cloud storage", "cloud storage allowed"))["ok"])

    def test_unrelated_project_rulings_and_other_tasks_do_not_gain_the_correction(self):
        session, original, task = self.baseline()
        unrelated = session.exchange("The future website will have a blue theme.", "Website context noted.")
        self.write_decision("DEC-003", f"Operator request evidence for: project:project: {unrelated}")
        correction = self.task_correction(session)
        self.assertEqual(request_trace.context_for_task_assignment(self.root, task).evidence_ids, [original, correction])
        second = self.seed_planned_task("TASK-01-002", "BUILD-01-002")
        (self.root / "IMPLEMENTATION_PLAN.md").write_text("# Plan\n- `BUILD-01-001`\n- `BUILD-01-002`\n")
        (self.root / "phases/PHASE-01.md").write_text("# Phase\n- `BUILD-01-001`\n- `BUILD-01-002`\n")
        self.assertNotIn(correction, request_trace.context_for_task_assignment(self.root, second).evidence_ids)

    def test_named_governing_decision_is_retained_without_a_task_reference(self):
        session, original, task = self.baseline()
        task.write_text(task.read_text().split("## References", 1)[0])
        self.write_decision("DEC-001", f"Operator request evidence for: project:project: {original}\nApplies to TASK-01-001.")
        correction = self.task_correction(session)
        self.assertEqual(request_trace.context_for_task_assignment(self.root, task).evidence_ids, [original, correction])

    def test_referenced_task_correction_does_not_need_a_new_decision(self):
        session, original, task = self.baseline()
        correction = session.exchange("For the current task, also record the remaining installer risks.", "Noted.")
        task.write_text(task.read_text() + f"\n## Request evidence\n\n- {correction}\n")
        self.assertEqual(request_trace.context_for_task_assignment(self.root, task).evidence_ids, [original, correction])

    def test_ad_hoc_or_mismatched_task_cannot_inherit_project_authority(self):
        session, _original, task = self.baseline()
        self.task_correction(session)
        for declaration in ("Plan ref: n/a", "Plan ref: BUILD-02-001"):
            task.write_text(task.read_text().replace("Plan ref: BUILD-01-001", declaration).replace("Plan ref: n/a", declaration))
            with self.assertRaises(RequestRefusal) as caught:
                request_trace.context_for_task_assignment(self.root, task)
            self.assertEqual(caught.exception.rule, "governing-decision-evidence-unbound")

    def test_revoked_governing_ruling_remains_a_blocker(self):
        session, original, task = self.baseline()
        self.task_correction(session)
        self.revoke([original])
        with self.assertRaises(RequestRefusal) as caught:
            request_trace.context_for_task_assignment(self.root, task)
        self.assertEqual(caught.exception.rule, "governing-decision-evidence-unbound")

    def test_cross_unit_governing_ruling_remains_a_blocker(self):
        session, original, task = self.baseline()
        self.task_correction(session)
        self.write_decision("DEC-003", f"Operator request evidence for: task:TASK-01-002: {original}")
        with self.assertRaises(RequestRefusal) as caught:
            request_trace.context_for_task_assignment(self.root, task)
        self.assertEqual(caught.exception.rule, "governing-decision-evidence-unbound")

    def test_uncaptured_governing_ruling_remains_a_blocker(self):
        session, _original, task = self.baseline()
        self.task_correction(session)
        absent = f"{session.handle}/turn-999"
        self.write_decision("DEC-001", f"Operator request evidence for: project:project: {absent}")
        with self.assertRaises(RequestRefusal) as caught:
            request_trace.context_for_task_assignment(self.root, task)
        self.assertEqual(caught.exception.rule, "governing-decision-evidence-unbound")

    def test_partial_governing_quote_remains_a_blocker(self):
        session, original, task = self.baseline()
        self.task_correction(session)
        self.write_decision("DEC-001", f"Operator request evidence for: project:project: {original}", quote="yes but allow cloud storage")
        with self.assertRaises(RequestRefusal) as caught:
            request_trace.context_for_task_assignment(self.root, task)
        self.assertEqual(caught.exception.rule, "governing-decision-evidence-unbound")

    def test_approved_checkpoint_ruling_survives_a_task_correction(self):
        session, original, task = self.baseline()
        ruling = session.exchange("For the approved checkpoint, retain export provenance.", "Noted.")
        self.write_decision("DEC-003", f"Operator request evidence for: planning:PLAN-BUILD-01-001: {ruling}")
        self.approve_checkpoint("PLAN-BUILD-01-001", "BUILD-01-001", [original, ruling])
        task.write_text(task.read_text() + "\n- DEC-003\n")
        correction = self.task_correction(session)
        self.assertEqual(request_trace.context_for_task_assignment(self.root, task).evidence_ids, [original, ruling, correction])

    def test_a_different_checkpoint_cannot_supply_governing_authority(self):
        session, _original, task = self.baseline()
        ruling = session.exchange("For a different checkpoint, add a billing integration.", "Noted.")
        self.write_decision("DEC-003", f"Operator request evidence for: planning:PLAN-BUILD-01-002: {ruling}")
        self.approve_checkpoint("PLAN-BUILD-01-002", "BUILD-01-002", [ruling])
        task.write_text(task.read_text() + "\n- DEC-003\n")
        self.task_correction(session)
        with self.assertRaises(RequestRefusal) as caught:
            request_trace.context_for_task_assignment(self.root, task)
        self.assertEqual(caught.exception.rule, "governing-decision-evidence-unbound")
