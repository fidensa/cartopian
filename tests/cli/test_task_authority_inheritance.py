"""Task corrections retain applicable governing authority without rebinding it."""
from cli import request_trace
from cli.request_trace import GovernedUnit, RequestRefusal
from tests.cli.test_evidence_resolver import PROJECT, ResolverCase


class TaskAuthorityFixture(ResolverCase):
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

    def named_checkpoint_ruling(self, session, dec_id, checkpoint):
        ruling = session.exchange(f"For {checkpoint}, retain export provenance.", "Noted.")
        self.write_decision(
            dec_id, f"Operator request evidence for: planning:{checkpoint}: {ruling}\nApplies to TASK-01-001."
        )
        return ruling


class TaskAuthorityInheritanceTests(TaskAuthorityFixture):
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


class NonGoverningDecisionCitationTests(TaskAuthorityFixture):
    """A decision the task only mentions can be classified, not bound.

    RED: every governing-decision-evidence-unbound refusal offered only "bind
    the ruling" or "retain checkpoint evidence", even when the cited decision
    governs nothing in the task — pushing the PM toward fabricating a binding.
    GREEN: a cited-only decision names a third remedy, and a reviewable
    ``A|DEC-NNN|outside-scope|<why>`` record in the task's upstream trace
    releases it. A decision that names the task in its own text, a
    ``governing-constraint`` classification, and an unparseable trace still
    refuse.
    """

    def cite_other_checkpoint_ruling(self, session):
        ruling = session.exchange("For a different checkpoint, add a billing integration.", "Noted.")
        self.write_decision("DEC-003", f"Operator request evidence for: planning:PLAN-BUILD-01-002: {ruling}")
        self.approve_checkpoint("PLAN-BUILD-01-002", "BUILD-01-002", [ruling])
        return ruling

    def classify(self, task, applicability_class, *, fence="```trace"):
        head, body = task.read_text().split("\n\n", 1)
        task.write_text(
            head + "\n\nUpstream trace: required\n" + body
            + f"\n## Upstream trace\n\n{fence}\nA|DEC-003|{applicability_class}|billing belongs to BUILD-01-002\n```\n"
        )

    def refusal(self, task):
        with self.assertRaises(RequestRefusal) as caught:
            request_trace.context_for_task_assignment(self.root, task)
        self.assertEqual(caught.exception.rule, "governing-decision-evidence-unbound")
        return caught.exception

    def test_cited_only_refusal_offers_the_non_governing_branch(self):
        session, _original, task = self.baseline()
        self.cite_other_checkpoint_ruling(session)
        task.write_text(task.read_text() + "\n- DEC-003\n")
        self.task_correction(session)
        refusal = self.refusal(task)
        self.assertIn("mentions DEC-003", refusal.detail)
        self.assertIn("A|DEC-003|outside-scope|<why>", refusal.recovery)
        self.assertIn("remove the mention", refusal.recovery)
        self.assertIn("governing-constraint", refusal.recovery)

    def test_outside_scope_classification_releases_a_cited_only_decision(self):
        session, original, task = self.baseline()
        ruling = self.cite_other_checkpoint_ruling(session)
        task.write_text(task.read_text() + "\n- DEC-003\n")
        correction = self.task_correction(session)
        self.classify(task, "outside-scope")
        for context in (
            request_trace.context_for_task_assignment(self.root, task),
            request_trace.context_for_task(self.root, task),
        ):
            self.assertEqual(context.evidence_ids, [original, correction])
            self.assertNotIn(ruling, context.evidence_ids)

    def test_governing_constraint_classification_still_requires_the_ruling(self):
        session, _original, task = self.baseline()
        self.cite_other_checkpoint_ruling(session)
        self.task_correction(session)
        self.classify(task, "governing-constraint")
        self.assertIn("mentions DEC-003", self.refusal(task).detail)

    def test_unparseable_trace_scopes_nothing_out_and_is_reported_first(self):
        session, _original, task = self.baseline()
        self.cite_other_checkpoint_ruling(session)
        self.task_correction(session)
        self.classify(task, "not-a-class")
        with self.assertRaises(RequestRefusal) as caught:
            request_trace.context_for_task_assignment(self.root, task)
        self.assertEqual(caught.exception.rule, "trace-unparseable")
        self.assertIn("applicability class outside the closed set", caught.exception.detail)
        self.assertIn("DEC-003", caught.exception.detail)

    def test_classification_without_the_trace_declaration_is_ignored(self):
        session, _original, task = self.baseline()
        self.cite_other_checkpoint_ruling(session)
        self.task_correction(session)
        self.classify(task, "outside-scope")
        task.write_text(task.read_text().replace("Upstream trace: required\n", ""))
        self.refusal(task)

    def test_a_decision_that_names_the_task_cannot_be_scoped_out(self):
        session, _original, task = self.baseline()
        ruling = session.exchange("For a different checkpoint, add a billing integration.", "Noted.")
        self.write_decision(
            "DEC-003",
            f"Operator request evidence for: planning:PLAN-BUILD-01-002: {ruling}\nApplies to TASK-01-001.",
        )
        self.approve_checkpoint("PLAN-BUILD-01-002", "BUILD-01-002", [ruling])
        self.task_correction(session)
        self.classify(task, "outside-scope")
        refusal = self.refusal(task)
        self.assertIn("names task:TASK-01-001 in its own text", refusal.detail)
        self.assertIn("superseding decision", refusal.recovery)


class GoverningDecisionReportingTests(TaskAuthorityFixture):
    """Every unresolved governing decision is reported in one pass.

    RED: the refusal named only the first failing decision, so fixing it just
    revealed the next, and a checkpoint review that did not pass its ruling on
    was never explained. GREEN: one refusal lists every decision, and says
    why each checkpoint's retained review did not qualify.
    """

    def test_all_unresolved_named_decisions_are_listed(self):
        session, _original, task = self.baseline()
        self.named_checkpoint_ruling(session, "DEC-003", "PLAN-BUILD-01-002")
        self.named_checkpoint_ruling(session, "DEC-004", "PLAN-BUILD-01-003")
        self.task_correction(session)
        with self.assertRaises(RequestRefusal) as caught:
            request_trace.context_for_task_assignment(self.root, task)
        refusal = caught.exception
        self.assertEqual(refusal.rule, "governing-decision-evidence-unbound")
        self.assertIn("2 governing decisions", refusal.detail)
        self.assertIn("DEC-003, DEC-004", refusal.detail)
        self.assertIn("no retained REVIEW-PLAN-BUILD-01-002.md", refusal.detail)
        self.assertIn("Supersedes: DEC-003, DEC-004", refusal.recovery)
        self.assertIn("separate, non-superseding decision does not clear", refusal.recovery)

    def test_narrow_backfill_is_named_and_widening_restores_inheritance(self):
        from cli import report_identity
        from cli.commands import backfill_review_scope
        from tests.cli.test_fail_open_defects import invoke

        session, original, task = self.baseline()
        ruling = self.named_checkpoint_ruling(session, "DEC-003", "PLAN-007")
        review = self.root / "reviews" / "REVIEW-PLAN-007.md"
        review.write_text(
            "# REVIEW-PLAN-007\n\nTarget: planning:PLAN-007\nPlan ref: BUILD-01-002, BUILD-01-001\n"
            "Verdict: approve\nRequest alignment: aligned\n"
            f"Request evidence: {original}, {ruling}\n",
            encoding="utf-8",
        )
        identity = report_identity.content_identity(review.read_bytes())

        def backfill(*checkpoints):
            return invoke(
                backfill_review_scope.handler, project_root=str(self.root), review=review.stem,
                checkpoint=list(checkpoints), expected_identity=identity,
            )

        self.assertEqual(backfill("PLAN-BUILD-01-002")[0], 0)
        correction = self.task_correction(session)
        with self.assertRaises(RequestRefusal) as caught:
            request_trace.context_for_task_assignment(self.root, task)
        detail = caught.exception.detail
        self.assertIn("names BUILD-01-001 in its Plan ref header", detail)
        self.assertIn("binds only PLAN-BUILD-01-002", detail)

        # Narrowing/replacing and adding a plan ref the review never named refuse.
        self.assertEqual(backfill("PLAN-BUILD-01-001")[0], 1)
        code, _records, err = backfill("PLAN-BUILD-01-002", "PLAN-BUILD-01-003")
        self.assertEqual(code, 1)
        self.assertIn("does not name", err)

        code, records, err = backfill("PLAN-BUILD-01-002", "PLAN-BUILD-01-001")
        self.assertEqual(code, 0, err)
        self.assertEqual(records[0]["status"], "widened")
        self.assertEqual(records[0]["previous_checkpoints"], ["PLAN-BUILD-01-002"])
        self.assertEqual(backfill("PLAN-BUILD-01-002", "PLAN-BUILD-01-001")[2], "")
        context = request_trace.context_for_task_assignment(self.root, task)
        self.assertIn(ruling, context.evidence_ids)
        self.assertIn(correction, context.evidence_ids)


class BlockedTaskTraceDeferralTests(TaskAuthorityFixture):
    """A trace refusal on a task that cannot yet dispatch does not block the project.

    RED: plan-audit made every task's request-trace refusal a blocker, so an
    open task waiting on unfinished work stopped startup for the active task.
    GREEN: that finding is a ``deferred-request-trace`` warning; readiness
    still refuses the task, and in-flight or unblocked tasks still block.
    """

    def refusing_task(self):
        session, _original, task = self.baseline()
        self.named_checkpoint_ruling(session, "DEC-003", "PLAN-BUILD-01-002")
        self.task_correction(session)
        return task

    def audit(self):
        from cli.commands import plan_audit

        return plan_audit._check_request_trace(self.root, "v0.14.0", True)

    def test_open_task_with_unfinished_blocker_is_deferred(self):
        from cli.commands import validate_task_readiness

        task = self.refusing_task()
        text = task.read_text().replace("Plan ref: BUILD-01-001\n", "Plan ref: BUILD-01-001\nBlocked by: TASK-01-009\n")
        task.write_text(text)
        blockers, warnings = self.audit()
        self.assertEqual([b for b in blockers if b.get("task_id") == "TASK-01-001"], [])
        deferred = [w for w in warnings if w["kind"] == "deferred-request-trace"]
        self.assertEqual([w["task_id"] for w in deferred], ["TASK-01-001"])
        self.assertEqual(deferred[0]["blocked_by"], ["TASK-01-009"])
        self.assertEqual(deferred[0]["failure_class"], "governing-decision-evidence-unbound")
        record, _ = validate_task_readiness.evaluate(self.root, task, task.read_text())
        check = next(c for c in record["checks"] if c["name"] == "request-trace-valid")
        self.assertFalse(check["pass"])

    def test_unblocked_or_in_flight_task_still_blocks(self):
        task = self.refusing_task()
        blockers, _ = self.audit()
        self.assertEqual([b["kind"] for b in blockers if b.get("task_id") == "TASK-01-001"], ["invalid-request-trace"])
        text = task.read_text().replace("Plan ref: BUILD-01-001\n", "Plan ref: BUILD-01-001\nBlocked by: TASK-01-009\n")
        moved = self.root / "tasks/in-progress/TASK-01-001.md"
        moved.parent.mkdir(exist_ok=True)
        moved.write_text(text)
        task.unlink()
        blockers, _ = self.audit()
        self.assertEqual([b["kind"] for b in blockers if b.get("task_id") == "TASK-01-001"], ["invalid-request-trace"])


class EnumerateWhileRefusingTests(TaskAuthorityFixture):
    """``acceptance-trace --enumerate`` serves the PM fixing a refusing task.

    RED: the authoring aid refused with ``trace-incomplete`` and no output
    while the task's evidence refused, so the ``A|`` record that would fix it
    could not be composed. GREEN: criteria and sources are listed, excerpts
    are omitted, and the refusal rides along.
    """

    def test_enumerate_lists_criteria_and_reports_the_refusal(self):
        from cli.commands import acceptance_trace
        from tests.cli.test_fail_open_defects import invoke

        session, _original, task = self.baseline()
        self.named_checkpoint_ruling(session, "DEC-003", "PLAN-BUILD-01-002")
        self.task_correction(session)
        task.write_text(task.read_text() + "\n## Acceptance\n\n- [ ] Notes sync to Markdown\n")
        code, records, err = invoke(
            acceptance_trace.handler, project_root=str(self.root), task=str(task),
            projection=None, anchor=False, enumerate_inputs=True, compose_from=None,
        )
        self.assertEqual(code, 0, err)
        self.assertEqual([c["text"] for c in records[0]["criteria"]], ["Notes sync to Markdown"])
        self.assertEqual(records[0]["excerpts"], [])
        self.assertEqual(records[0]["excerpts_refusal"]["rule"], "governing-decision-evidence-unbound")
        self.assertIn("excerpts unavailable", err)
