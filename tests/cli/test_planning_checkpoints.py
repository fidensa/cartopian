"""Planning gates use reviewed scope, never counter position or artifact presence."""
import argparse
import json
import tempfile
import unittest
from pathlib import Path

from cli import checkpoint_identity, planning_status, request_trace
from cli.commands import delete_prompt, delete_report, next_action, plan_audit, report_action, write_prompt
from tests.cli.commands.test_report_action import _review_report
from tests.scaffold import project_scaffold
from tests.test_kickoff_readiness import _TOML_PLANNING_REVIEW, _capture, _isolated_home, _run


class PlanningCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for directory in ('reviews', 'prompts', 'reports', 'phases', 'tasks/open', 'tasks/done'):
            (self.root / directory).mkdir(parents=True)
        self.write('REQUIREMENTS.md', '# Requirements\n')
        self.write('IMPLEMENTATION_PLAN.md', '# Plan\n')
        self.approve('PLAN-001', 'Planning stage: requirements-and-standards\n')
        self.approve('PLAN-002', 'Planning stage: implementation-plan\n')

    def write(self, name, text):
        (self.root / name).write_text(text, encoding='utf-8')

    def approve(self, checkpoint, scope=''):
        self.write(f'reviews/REVIEW-{checkpoint}.md', scope + 'Verdict: approve\n')

    def derive(self):
        return planning_status.derive(self.root, planning_review_required=True)

    def phase(self, number='01'):
        self.write(f'phases/PHASE-{number}.md', f'# PHASE-{number}\n')

    def task(self, suffix='01-005', kind='BUILD', status='open'):
        self.write(f'tasks/{status}/TASK-{suffix}.md', f'Phase: PHASE-{suffix[:2]}\nPlan ref: {kind}-{suffix}\n')

    def test_distinguishing_reproduction_and_inserted_custom_checkpoint(self):
        self.assertEqual(self.derive()['stage'], 'phases')
        self.phase()
        self.assertEqual(self.derive()['stage'], 'phases-review')
        self.approve('PLAN-003', 'Planning stage: implementation-plan\n')
        self.assertEqual(self.derive()['checkpoint'], 'PLAN-PHASE-01')
        self.approve('PLAN-PHASE-01')
        self.assertEqual(self.derive()['stage'], 'tasks')

    def test_unscoped_counter_is_never_stage_evidence(self):
        self.phase()
        self.approve('PLAN-003')
        self.assertEqual(self.derive()['stage'], 'phases-review')
        self.assertTrue(planning_status.missing_reviews(self.root))

    def test_downstream_files_and_execution_do_not_skip_earlier_gates(self):
        self.phase()
        self.task(status='done')
        self.approve('PLAN-BUILD-01-005')
        self.assertEqual(self.derive()['checkpoint'], 'PLAN-PHASE-01')
        (self.root / 'reviews/REVIEW-PLAN-001.md').unlink()
        self.assertEqual(self.derive()['checkpoint'], 'PLAN-REQUIREMENTS')
        self.assertEqual({x['stage'] for x in planning_status.missing_reviews(self.root)},
                         {'requirements-and-standards', 'phases'})

    def test_each_phase_and_each_plan_ref_requires_its_own_review(self):
        self.phase()
        self.phase('02')
        self.approve('PLAN-PHASE-01')
        self.assertEqual(self.derive()['checkpoint'], 'PLAN-PHASE-02')
        self.approve('PLAN-PHASE-02')
        self.task()
        self.task('01-006', 'TEST')
        self.approve('PLAN-BUILD-01-005')
        self.assertEqual(self.derive()['checkpoint'], 'PLAN-TEST-01-006')
        self.approve('PLAN-TEST-01-006')
        self.assertTrue(self.derive()['complete'])

    def test_conflicting_identity_cannot_expand_approval(self):
        self.phase()
        self.approve('PLAN-PHASE-01')
        self.task()
        self.approve('PLAN-BUILD-01-004', 'Plan ref: BUILD-01-005\n')
        self.assertEqual(self.derive()['checkpoint'], 'PLAN-BUILD-01-005')
        self.approve('PLAN-BUILD-01-005', 'Planning stage: phases\n')
        self.assertFalse(self.derive()['complete'])
        self.approve('PLAN-BUILD-01-005', 'Plan ref: n/a\n')
        self.assertFalse(self.derive()['complete'])

    def test_explicit_legacy_scope_can_cover_a_range_without_counter_semantics(self):
        self.phase()
        self.approve('PLAN-019', 'Planning stage: phases\nPhase: PHASE-01\n')
        self.task()
        self.approve('PLAN-003', 'Planning stage: tasks-and-specs\nPhase: PHASE-01\nPlan ref: BUILD-01-004 through BUILD-01-006\n')
        self.assertTrue(self.derive()['complete'])
        self.task('01-007')
        self.assertEqual(self.derive()['checkpoint'], 'PLAN-BUILD-01-007')

    def test_unrelated_inflight_checkpoint_does_not_capture_next_action(self):
        self.phase()
        self.approve('PLAN-PHASE-01')
        self.task()
        self.write('prompts/PROMPT-PLAN-TEST-02-001.md', '# Other phase\n')
        self.assertEqual(self.derive()['checkpoint'], 'PLAN-BUILD-01-005')
        self.write('prompts/PROMPT-PLAN-BUILD-01-005.md', '# Current review\n')
        self.assertIn('wait-report', self.derive()['next'])
        self.write('reports/REPORT-PLAN-BUILD-01-005.md', 'Status: complete\n')
        self.assertIn('report-action', self.derive()['next'])
        self.write('reviews/REVIEW-PLAN-BUILD-01-005.md', 'Verdict: request-changes\n')
        self.assertIn('request-changes', self.derive()['next'])

    def test_legacy_report_routes_using_prompt_but_approval_cannot_borrow_scope(self):
        self.phase()
        self.approve('PLAN-PHASE-01')
        self.task()
        self.write('prompts/PROMPT-PLAN-007.md',
                   'Planning stage: tasks-and-specs\nPhase: PHASE-01\nPlan ref: BUILD-01-005\n')
        self.write('reports/REPORT-PLAN-007.md', 'Status: complete\n')
        self.assertEqual(self.derive()['checkpoint'], 'PLAN-007')
        self.assertIn('report-action', self.derive()['next'])
        self.approve('PLAN-007')
        self.assertFalse(self.derive()['complete'])
        self.assertEqual(self.derive()['checkpoint'], 'PLAN-BUILD-01-005')

    def test_audit_fails_for_missing_required_stage_and_clears_after_approval(self):
        with _isolated_home():
            self.write('cartopian.toml', _TOML_PLANNING_REVIEW)
            self.phase()
            record, code = plan_audit.evaluate(argparse.Namespace(project_path=str(self.root)))
            self.assertEqual(code, 1)
            self.assertFalse(record['clean'])
            self.assertEqual(record['blockers'][0]['checkpoint'], 'PLAN-PHASE-01')
            self.approve('PLAN-PHASE-01')
            record, code = plan_audit.evaluate(argparse.Namespace(project_path=str(self.root)))
            self.assertEqual(code, 0, record)
            self.assertTrue(record['clean'], record)

    def test_next_action_names_missing_review_after_tasks_exist(self):
        with _isolated_home():
            self.write('cartopian.toml', _TOML_PLANNING_REVIEW)
            self.phase()
            self.task()
            code, records, err = _run(next_action.handler, project_path=str(self.root))
            self.assertEqual(code, 0, err)
            self.assertEqual(records[0]['planning']['checkpoint'], 'PLAN-PHASE-01')
            self.assertEqual(records[0]['startup']['verdict'], 'planning-incomplete')

    def test_review_off_preserves_artifact_driven_workflow(self):
        self.phase()
        self.task()
        self.assertTrue(planning_status.derive(self.root, planning_review_required=False)['complete'])


class CheckpointLifecycleTests(unittest.TestCase):
    def test_scope_round_trip_prompt_context_report_and_cleanup_grammar(self):
        checkpoint = 'PLAN-BUILD-01-005'
        with _isolated_home() as home, project_scaffold(cartopian_toml=_TOML_PLANNING_REVIEW) as scaffold:
            root = scaffold.project_root
            _capture(root, "Build the scoped feature.", record_id="REQUEST-001", sequence=0)
            scaffold.write('tasks/open/TASK-01-005.md', 'Phase: PHASE-01\nPlan ref: BUILD-01-005\n')
            scaffold.write('specs/SPEC-01-005.md', '# Spec\n')
            code, records, err = _run(write_prompt.handler, project_root=str(root),
                prompt_id='PROMPT-' + checkpoint, content='# Review\n\n## Your role\nReview this scope.\n',
                content_file=None, review_kind='planning', task=None, checkpoint=checkpoint,
                phase='PHASE-01', plan_ref='BUILD-01-005')
            self.assertEqual(code, 0, err)
            prompt = root / f'prompts/PROMPT-{checkpoint}.md'
            body = prompt.read_text()
            self.assertIn('Planning stage: tasks-and-specs', body)
            self.assertIn('Plan ref: BUILD-01-005', body)
            context = request_trace.context_for_checkpoint(root, checkpoint, checkpoint_text=body)
            self.assertTrue(request_trace.preflight_prompt_binding(context, body)['ok'])
            self.assertIn('tasks/open/TASK-01-005.md', context.management_artifacts)
            self.assertIn('specs/SPEC-01-005.md', context.management_artifacts)
            self.assertTrue(delete_prompt.PROMPT_FILENAME_RE.fullmatch(prompt.name))
            report = root / f'reports/REPORT-{checkpoint}.md'
            self.assertEqual(report_action._report_suffix(report, 'planning-review'), 'BUILD-01-005')
            self.assertEqual(checkpoint_identity.artifact_checkpoint(report.name, 'REPORT'), checkpoint)
            review = root / f'reviews/REVIEW-{checkpoint}.md'
            review.write_text(f'Request-context identity: {context.context_identity}\nVerdict: approve\nRequest alignment: aligned\nRequest evidence: REQUEST-001\n')
            report.write_text(_review_report(
                report_stem=report.stem, review_id=review.stem, prompt_path=prompt,
                task_path=None, review_path=review, status='complete', verdict='approve'))
            code, records, err = _run(report_action.handler, report_path=str(report), variant=None, expected_identity=None)
            self.assertEqual(code, 0, err)
            self.assertEqual(records[0]['verdict'], 'accepted')
            self.assertEqual(records[0]['review_path'], str(review.resolve()))
            registry = home / '.cartopian/projects.json'
            registry.parent.mkdir(exist_ok=True)
            registry.write_text(json.dumps([{'id': 'demo', 'path': str(root), 'label': 'Demo'}]))
            code, _, err = _run(delete_prompt.handler, prompt_path=str(prompt))
            self.assertEqual(code, 0, err)
            code, _, err = _run(delete_report.handler, report_path=str(report))
            self.assertEqual(code, 0, err)
            self.assertFalse(prompt.exists())
            self.assertFalse(report.exists())
            self.assertTrue(review.exists())
            with self.assertRaises(request_trace.RequestRefusal):
                request_trace.context_for_checkpoint(root, checkpoint, phase_id='PHASE-02')


if __name__ == '__main__':
    unittest.main()
