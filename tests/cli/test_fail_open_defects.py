"""Regression coverage for the six reported fail-open defects."""
import argparse
import contextlib
import io
import json
from pathlib import Path
from unittest import mock

import pytest

from cli import assignment_inputs, dispatch_rehearsal, planning_status, prompt_composer, provenance, report_identity, request_trace
from cli.commands import backfill_review_scope, dispatch, handoff_packet, report_action, validate_task_readiness, write_prompt
from cli.request_trace import GovernedUnit, RequestRefusal
from tests.cli.commands.test_compose_assignment_prompt import _build_minimal, _TOML_REVIEW_OFF, _write_prompt_args
from tests.cli.commands.test_report_action import _bound_task_prompt, _PROJECT_TOML, _review_report
from tests.cli.test_evidence_resolver import ResolverCase
from tests.scaffold import project_scaffold


def invoke(handler, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = handler(argparse.Namespace(**kwargs))
    return code, [json.loads(line) for line in out.getvalue().splitlines()], err.getvalue()


@pytest.mark.parametrize("mutation", ["missing", "stale-context", "wrong-verdict", "changed-identity"])
def test_complete_review_refuses_a_different_or_missing_round(mutation):
    with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
        task = scaffold.write("tasks/in-review/TASK-01-001.md", "# TASK-01-001: Demo\nWork root: n/a\n")
        prompt = _bound_task_prompt(scaffold, task, "01-001")
        review = scaffold.write("reviews/REVIEW-01-001.md", "# REVIEW-01-001\nTarget: TASK-01-001\nVerdict: request-changes\n## Findings\n- F1. [major] live blocker\n")
        report = scaffold.write("reports/REPORT-01-001-review.md", _review_report(
            report_stem="REPORT-01-001-review", review_id=review.stem, prompt_path=prompt,
            task_path=task, review_path=review, status="complete", verdict="request-changes"))
        pinned = report_identity.content_identity(review.read_text())
        if mutation == "missing":
            review.unlink()
        elif mutation == "stale-context":
            review.write_text(review.read_text().replace(request_trace._header(review.read_text(), "Request-context identity"), "sha256:" + "a" * 64))
        elif mutation == "wrong-verdict":
            review.write_text(review.read_text().replace("Verdict: request-changes", "Verdict: approve"))
        else:
            review.write_text(review.read_text() + "Changed after observation.\n")
        code, records, err = invoke(report_action.handler, report_path=str(report), variant=None,
                                   expected_identity=None, expected_review_identity=pinned if mutation == "changed-identity" else None)
        assert code == 1, err
        assert records[0]["review_projection"] is None
        assert records[0]["target_task_status"] is None
        assert not records[0]["review_binding"]["ok"]


def test_review_rounds_remain_reconstructible_after_overwrite_and_deletion():
    with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
        task = scaffold.write("tasks/in-review/TASK-01-001.md", "# TASK-01-001: Demo\nWork root: n/a\n")
        prompt = _bound_task_prompt(scaffold, task, "01-001")
        review = scaffold.write("reviews/REVIEW-01-001.md", "# REVIEW-01-001\nTarget: TASK-01-001\nVerdict: request-changes\n## Findings\n- F1. [major] first blocker\n")
        report = scaffold.write("reports/REPORT-01-001-review.md", _review_report(
            report_stem="REPORT-01-001-review", review_id=review.stem, prompt_path=prompt,
            task_path=task, review_path=review, status="complete", verdict="request-changes"))
        first = review.read_text()
        for body in (first, first, first.replace("first blocker", "second blocker")):
            review.write_text(body)
            code, records, err = invoke(report_action.handler, report_path=str(report), variant=None, expected_identity=None)
            assert code == 0, err
            assert records[0]["review_content_identity"] == report_identity.content_identity(body)
        review.unlink()
        retained = [r["content"] for r in provenance._read_log(scaffold.project_root)
                    if r.get("action") == "review-publication"]
        assert retained == [first, first.replace("first blocker", "second blocker")]


class TestGoverningDecisionBindings(ResolverCase):
    def test_shared_ruling_requires_a_binding_for_each_governed_checkpoint(self):
        session = self.session()
        self.bind(session)
        self.planning_exchange(session)
        self.lock_requirements()
        ruling = session.exchange("Add both corrective items to the plan.", "Noted.")
        target = GovernedUnit("planning", "PLAN-CORRECTIVE-03-035")
        marker = f"Operator request evidence for: planning:PLAN-CORRECTIVE-03-034: {ruling}"
        self.write_decision("DEC-001", marker + "\nApplies to PLAN-CORRECTIVE-03-034 and PLAN-CORRECTIVE-03-035.")
        with self.assertRaises(RequestRefusal) as caught:
            request_trace.lookup_unit(self.root, target)
        self.assertEqual(caught.exception.rule, "governing-decision-evidence-unbound")
        self.write_decision("DEC-001", marker + f"\nOperator request evidence for: planning:PLAN-CORRECTIVE-03-035: {ruling}")
        trace, _, _ = request_trace.lookup_unit(self.root, target)
        self.assertIn(ruling, [r.record_id for r in trace])
        self.assertTrue(any(r.unit == target for r in trace))


def test_rework_composer_binds_verbatim_findings_and_preflight_refuses_changes():
    with project_scaffold(cartopian_toml=_TOML_REVIEW_OFF) as scaffold:
        task = _build_minimal(scaffold)
        review = scaffold.write("reviews/REVIEW-02-001.md", "# REVIEW-02-001\nTarget: TASK-02-001\nVerdict: request-changes\n## Findings\n- F1. [major] Fix TASK-02-001 output.\n- F2. [minor] Closed in the previous round.\n")
        record = prompt_composer.compose(task, "coder")
        assert record["outcome"] == "composed", record["findings"]
        body = record["assignee_prompt"]
        assert "## Review findings input" in body
        payload = assignment_inputs.extract_payload_blocks(body)[0]
        assert payload["content"] == review.read_text()
        cfg = {"project": {}}
        assert handoff_packet.audit_prompt_payloads(scaffold.project_root, cfg, task.read_text(), None, body)["ok"]
        assert not handoff_packet.audit_prompt_payloads(scaffold.project_root, cfg, task.read_text(), None, "# Missing input")["ok"]
        composed = scaffold.root / "composed.json"
        composed.write_text(json.dumps(record))
        review.write_text(review.read_text().replace("Fix TASK", "Now fix TASK"))
        args = _write_prompt_args(scaffold, task, prompt_id="PROMPT-02-001", composed_file=str(composed), content=None, content_file=None)
        code, _, err = invoke(write_prompt.handler, **vars(args))
        assert code == 1 and "stale" in err
        assert not (scaffold.prompts / "PROMPT-02-001.md").exists()


def test_legacy_scope_backfill_preserves_bytes_and_satisfies_only_the_named_gate():
    with project_scaffold() as scaffold:
        root = scaffold.project_root
        review = scaffold.write("reviews/REVIEW-PLAN-034.md", "# REVIEW-PLAN-034\nVerdict: approve\n## Summary\nApproved corrective scope.\n")
        original = review.read_bytes()
        args = dict(project_root=str(root), review=review.stem, checkpoint="PLAN-CORRECTIVE-03-035",
                    expected_identity=report_identity.content_identity(original))
        for _ in range(2):
            code, _, err = invoke(backfill_review_scope.handler, **args)
            assert code == 0, err
        assert review.read_bytes() == original
        records = planning_status.checkpoint_records(root)
        assert planning_status._gate_record(records, args["checkpoint"], "tasks-and-specs", "PHASE-03", "CORRECTIVE-03-035") is None
        assert planning_status._gate_record(records, "PLAN-CORRECTIVE-03-036", "tasks-and-specs", "PHASE-03", "CORRECTIVE-03-036") is not None
        code, _, _ = invoke(backfill_review_scope.handler, **{**args, "checkpoint": "PLAN-CORRECTIVE-03-036"})
        assert code == 1
        review.write_text(review.read_text() + "Altered\n")
        assert planning_status._gate_record(planning_status.checkpoint_records(root), args["checkpoint"], "tasks-and-specs", "PHASE-03", "CORRECTIVE-03-035") is not None


def test_native_launch_reservation_overrides_role_auto_launch():
    with project_scaffold(cartopian_toml=_TOML_REVIEW_OFF) as scaffold:
        task = _build_minimal(scaffold)
        task.write_text(task.read_text().replace("Assignee: coder", "Launch mode: native-interactive\nAssignee: coder"))
        with mock.patch("cli.launch_preflight.environment_checks", return_value=[]):
            rehearsal = dispatch_rehearsal.rehearse(task)
        assert rehearsal["ok"], rehearsal["blockers"]
        assert rehearsal["launch"]["mode"] == "manual"
        assert not rehearsal["launch"]["auto_launch_task_run"]
        code, packets, err = invoke(handoff_packet.handler, task_path=str(task), role="coder")
        assert code == 0, err
        assert packets[0]["native_interactive_required"]
        assert "task_run" not in packets[0]["auto_launch"]
        with mock.patch("cli.launch_preflight.role_checks", return_value=[]):
            code, _, err = invoke(dispatch.handler, task_path=str(task), role="coder", prompt=None)
        assert code == 1 and "native interactive" in err
        assert not list(scaffold.reports.iterdir())
        task.write_text(task.read_text().replace("native-interactive", "typo"))
        assert not validate_task_readiness._check_launch_mode(task.read_text())["pass"]


@pytest.mark.parametrize("identifier", ["TASK-04-005", "SPEC-04-005"])
def test_plan_prose_reference_diagnostic_names_the_reference_rule(identifier):
    from cli import numbering_contract
    finding = numbering_contract.validate_plan_revision("# Plan\n", f"# Plan\nSee {identifier} for context.\n")[0]
    assert finding["classification"] == "plan-ref-kind-unsupported"
    assert "plan documents cite plan refs" in finding["detail"]
    assert "BUILD-04-005" in finding["detail"]
    assert "new allocation" not in finding["detail"]


def test_terminal_review_observation_binds_both_publications():
    from cli import handoff_observer
    with project_scaffold(cartopian_toml=_PROJECT_TOML) as scaffold:
        task = scaffold.write("tasks/in-review/TASK-01-001.md", "# TASK-01-001: Demo\nWork root: n/a\n")
        prompt = _bound_task_prompt(scaffold, task, "01-001")
        review = scaffold.write("reviews/REVIEW-01-001.md", "# REVIEW-01-001\nVerdict: approve\n")
        report = scaffold.write("reports/REPORT-01-001-review.md", _review_report(
            report_stem="REPORT-01-001-review", review_id=review.stem, prompt_path=prompt,
            task_path=task, review_path=review, status="complete", verdict="approve"))
        observed = handoff_observer.observe_once(report, expected_variant="review")
        assert observed.terminal
        fields = handoff_observer.record_fields(observed, report)
        assert fields["report_content_identity"] == report_identity.content_identity(report.read_bytes())
        assert fields["review_content_identity"] == report_identity.content_identity(review.read_bytes())
        review.write_text(review.read_text() + "Mutated after wait.\n")
        code, records, _ = invoke(report_action.handler, report_path=str(report), variant=None,
            expected_identity=fields["report_content_identity"], expected_review_identity=fields["review_content_identity"])
        assert code == 1 and records[0]["review_projection"] is None


def test_backfill_can_address_every_checkpoint_of_one_historical_batch():
    with project_scaffold() as scaffold:
        review = scaffold.write("reviews/REVIEW-PLAN-001.md", "# REVIEW-PLAN-001\nVerdict: approve\n## Summary\nApproved both task contracts.\n")
        checkpoints = ["PLAN-BUILD-01-001", "PLAN-BUILD-01-002"]
        code, _, err = invoke(backfill_review_scope.handler, project_root=str(scaffold.project_root),
            review=review.stem, checkpoint=checkpoints, expected_identity=report_identity.content_identity(review.read_bytes()))
        assert code == 0, err
        records = planning_status.checkpoint_records(scaffold.project_root)
        for number in (1, 2):
            assert planning_status._gate_record(records, checkpoints[number - 1], "tasks-and-specs",
                                                "PHASE-01", f"BUILD-01-00{number}") is None


@pytest.mark.parametrize("declaration", ["Launch mode: typo", "Launch mode: auto\nLaunch mode: native-interactive", " Launch mode: typo", "Launch mode : typo"])
def test_invalid_launch_declarations_fail_readiness(declaration):
    assert not validate_task_readiness._check_launch_mode("# Task\n" + declaration + "\n## Goal\nWork.")["pass"]


class TestBackfilledAuthority(ResolverCase):
    def test_backfilled_review_retains_the_original_request_evidence_ancestry(self):
        session = self.session()
        self.bind(session)
        original = self.planning_exchange(session)
        self.lock_requirements()
        task = self.seed_planned_task()
        correction = session.exchange("Also retain duplicate notes in the export.", "Noted.")
        review = self.root / "reviews" / "REVIEW-PLAN-034.md"
        review.write_text("# REVIEW-PLAN-034\nVerdict: approve\nRequest alignment: aligned\n"
                          f"Request evidence: {original}, {correction}\nRequest-context identity: sha256:{'a' * 64}\n")
        code, _, err = invoke(backfill_review_scope.handler, project_root=str(self.root), review=review.stem,
            checkpoint="PLAN-BUILD-01-001", expected_identity=report_identity.content_identity(review.read_bytes()))
        assert code == 0, err
        context = request_trace.context_for_task_assignment(self.root, task)
        assert context.evidence_ids == [original, correction]
        assert "retain duplicate notes" in context.section
