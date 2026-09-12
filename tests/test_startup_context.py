"""Portable startup: same decisions/checks, bounded discovery and presentation."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli.commands import next_action, plan_audit
from cli.main import main
from evaluations.startup_context import BUDGETS, measure
from mcp_server import server
from mcp_server.skill_metadata import BRIDGE_TARGETS, load_metadata
from tests import test_kickoff_readiness as kickoff
from tests.scaffold import project_scaffold


ROOT = Path(__file__).resolve().parents[1]
CONFIG = (
    '[project]\nid = "context-test"\nname = "Context Test"\n'
    'project_schema_version = "v0.13.0"\n'
    '[roles.worker]\ndescription = "Performs assigned work."\n'
)


def invoke(command, root, *flags):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([command, str(root), *flags])
    records = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    return code, records, err.getvalue()


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


class StartupContextTests(unittest.TestCase):
    def setUp(self):
        self.home = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.object(Path, 'home', return_value=Path(self.home)))
        self.scaffold = self.enterContext(project_scaffold(cartopian_toml=CONFIG))
        self.root = self.scaffold.project_root

    def test_compact_audit_has_same_checks_exit_and_all_blocking_evidence(self):
        # Exercise the real evaluator with repeatable results at its external
        # provenance seam. Warnings may grow without growing startup linearly.
        guard = {'kind': 'raw-edit', 'path': 'STATE.md', 'detail': 'State changed outside the writer.'}
        warnings = [{'kind': 'identifier-leak', 'detail': f'Identifier in product file {n}.'} for n in range(300)]
        provenance = {'guard': [guard], 'advisory': [{'kind': 'untracked', 'detail': 'No baseline.'}]}
        with mock.patch.object(plan_audit, 'audit_provenance', return_value=provenance) as audit:
            with mock.patch.object(plan_audit, '_check_scoped_request_coverage', return_value=warnings):
                full_code, full, _ = invoke('plan-audit', self.root)
                compact_code, compact, stderr = invoke('plan-audit', self.root, '--compact')
        self.assertEqual(audit.call_count, 2)
        self.assertNotEqual(full_code, 0)
        self.assertEqual(compact_code, full_code)
        self.assertEqual(compact[0]['blockers'], full[0]['blockers'])
        self.assertEqual(compact[0]['provenance']['guard'], [guard])
        self.assertEqual(compact[0]['warning_summary']['identifier-leak'], 300)
        self.assertEqual(compact[0]['provenance']['advisory_summary'], {'untracked': 1})
        self.assertEqual(provenance['advisory'][0]['detail'], 'No baseline.')
        self.assertEqual(stderr, '')
        self.assertLess(len(json.dumps(compact)), len(json.dumps(full)) // 4)
        details = compact[0]['details']
        self.assertEqual(details, {'command': 'plan-audit', 'arguments': {'project_path': str(self.root.resolve())}})

    def test_compact_orientation_preserves_policy_grants_and_decisions_read_only(self):
        for initiation in ('operator', 'auto'):
            with self.subTest(initiation=initiation):
                self.scaffold.write('cartopian.toml', CONFIG + f'[automation]\ninitiation = "{initiation}"\n')
                before = snapshot(self.root)
                full_code, full, _ = invoke('next-action', self.root)
                code, compact, _ = invoke('next-action', self.root, '--compact', '--audit')
                self.assertEqual(code, full_code)
                for key in ('startup', 'planning', 'automation', 'reviews', 'pm_role_declared',
                            'blockers', 'state_filesystem_disagreement', 'delivery'):
                    self.assertEqual(compact[0][key], full[0][key], key)
                self.assertEqual(compact[0]['pm_effective_grants'], full[0]['roles']['pm']['effective_grants'])
                self.assertEqual(compact[0]['role_names'], sorted(full[0]['roles']))
                self.assertEqual(compact[0]['audit']['exit_code'], 0)
                self.assertEqual(snapshot(self.root), before)

    def test_combined_startup_cannot_report_ready_after_audit_failure(self):
        ready = {'verdict': 'ready', 'task': 'TASK-01-001', 'action': 'dispatch', 'owner': 'pm', 'detail': 'Ready.'}
        guard = {'kind': 'raw-edit', 'detail': 'Critical provenance guard.'}
        with mock.patch.object(next_action, '_startup_verdict', return_value=ready):
            with mock.patch.object(plan_audit, 'audit_provenance', return_value={'guard': [guard], 'advisory': []}):
                code, records, _ = invoke('next-action', self.root, '--compact', '--audit')
        self.assertNotEqual(code, 0)
        self.assertEqual(records[0]['startup']['verdict'], 'blocked')
        self.assertIn(guard['detail'], records[0]['blockers'])
        self.assertEqual(records[0]['audit']['provenance']['guard'], [guard])

    def test_incomplete_audit_fails_closed(self):
        for audit_code in (0, 1):
            with self.subTest(audit_code=audit_code), mock.patch.object(plan_audit, 'evaluate', return_value=(None, audit_code)):
                code, records, _ = invoke('next-action', self.root, '--compact', '--audit')
            self.assertEqual(code, 1)
            self.assertFalse(records[0]['audit']['evaluation_complete'])
            self.assertEqual(records[0]['startup']['verdict'], 'blocked')

    def test_situation_notes_and_migration_still_block(self):
        for config, state in (
            (CONFIG, '# State\n\n## Situation\n\n- Worker is blocked pending operator decision.\n'),
            (CONFIG.replace('v0.13.0', 'v0.12.0'), '# State\n'),
        ):
            with self.subTest(config=config, state=state):
                self.scaffold.write('cartopian.toml', config)
                self.scaffold.write('STATE.md', state)
                before = snapshot(self.root)
                _, full, _ = invoke('next-action', self.root)
                _, compact, _ = invoke('next-action', self.root, '--compact', '--audit')
                self.assertEqual(compact[0]['startup'], full[0]['startup'])
                self.assertEqual(compact[0]['startup']['verdict'], 'blocked')
                self.assertTrue(set(full[0]['blockers']).issubset(compact[0]['blockers']))
                self.assertEqual(snapshot(self.root), before)

    def test_mcp_and_cli_compact_records_match(self):
        # Installed/running proof is host-specific by design; hold that
        # observation constant when comparing the two transport projections.
        with mock.patch.object(plan_audit, '_check_numbering_contract', return_value=([], {'active': False})):
            _, records, _ = invoke('next-action', self.root, '--compact', '--audit')
            result = server.call_tool('next_action', {'project_path': str(self.root), 'compact': True, 'audit': True})
        actual = json.loads(result['content'][0]['text'])
        self.assertEqual(actual, records[0])
        self.assertFalse(result['isError'])

    def test_combined_result_matches_separate_orientation_and_audit_across_task_states(self):
        for status in ('open', 'in-progress', 'in-review', 'done'):
            with self.subTest(status=status), project_scaffold(cartopian_toml=kickoff._TOML_REVIEW_OFF) as scaffold:
                root = kickoff._plan_project(scaffold)
                if status != 'open':
                    (root / 'tasks/open/TASK-01-001.md').rename(root / f'tasks/{status}/TASK-01-001.md')
                before = snapshot(root)
                _, orientation, _ = invoke('next-action', root)
                audit_code, audit, _ = invoke('plan-audit', root)
                code, combined, _ = invoke('next-action', root, '--compact', '--audit')
                self.assertEqual(code, audit_code)
                self.assertEqual(combined[0]['audit']['blockers'], audit[0]['blockers'])
                self.assertEqual(combined[0]['audit']['provenance']['guard'], audit[0]['provenance']['guard'])
                if audit_code:
                    self.assertEqual(combined[0]['startup']['verdict'], 'blocked')
                else:
                    self.assertEqual(combined[0]['startup'], orientation[0]['startup'])
                self.assertEqual(snapshot(root), before)


class CompactStartupVerdictTests(kickoff.StartupVerdictTests):
    """Replay existing readiness, manual dispatch, closeout and reconcile cases
    with the new presentation, retaining their existing behavior assertions.
    """

    def _next_action(self, root, reconcile=False):
        return kickoff._run(next_action.handler, project_path=str(root), compact=True, reconcile=reconcile)


class ContextResourceTests(unittest.TestCase):
    def test_startup_transport_budgets(self):
        sizes = measure()
        for name, size in sizes.items():
            self.assertLessEqual(size, BUDGETS[name], name)

    def test_named_resource_reads_match_existing_reader_without_catalogs(self):
        uris = ['cartopian://skills/use_cartopian', 'cartopian://skills/start_session',
                'cartopian://protocol/CONVENTIONS/startup',
                'cartopian://protocol/CONVENTIONS/tasks/task-execution-order']
        with mock.patch.object(server, 'list_resources', side_effect=AssertionError('catalog loaded')):
            for uri in uris:
                with self.subTest(uri=uri):
                    expected = server.read_resource(uri)['contents'][0]['text']
                    result = server.call_tool('read_context', {'uri': uri})
                    self.assertEqual(result['content'], [{'type': 'text', 'text': expected}])

    def test_invalid_reads_keep_existing_resource_boundaries(self):
        for args in (None, {}, {'uri': 1}, {'uri': 'cartopian://skills/../../secret'},
                     {'uri': 'file:///etc/passwd'}, {'uri': 'cartopian://skills/missing'},
                     {'uri': 'cartopian://skills/start_session', 'extra': True}):
            with self.subTest(args=args), self.assertRaises(server.McpError):
                server.call_tool('read_context', args)

    def test_all_bridges_route_to_bounded_discovery(self):
        load_metadata(ROOT)
        for target in BRIDGE_TARGETS.values():
            text = (ROOT / target.path).read_text()
            self.assertIn('`read_context` MCP tool', text)
            self.assertIn('do not dump tool or resource catalogs', text)

    def test_startup_subsection_selector_fails_closed(self):
        with mock.patch.object(server, 'STARTUP_SUBSECTIONS', {'Tasks': ('missing',)}):
            with self.assertRaises(server.McpError):
                server.read_resource('cartopian://protocol/CONVENTIONS/startup')


if __name__ == '__main__':
    unittest.main()
