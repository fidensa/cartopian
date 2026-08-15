"""Red-to-green contract tests for domain-neutral source-backed work."""
from __future__ import annotations

import json
import argparse
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from cli import source_guidance
from cli.commands import task_bundle
from cli.commands import render_spec, report_action
from mcp_server import server
from tests.scaffold import project_scaffold


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "source_guidance"


def _source_section(name: str = "valid-source-backed.md", heading: str = "Source guidance") -> str:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    section = text[text.index("## Source guidance"):]
    return section.replace("## Source guidance", f"## {heading}", 1)


def _task_body(source_section: str, *, source_owner: str = "task") -> str:
    return (
        "# TASK-04-002: Source-backed work\n\n"
        "Phase: PHASE-04\nPlan ref: n/a\nSource: n/a\nWork root: n/a\n"
        "Deliverable: n/a\nAssignee: coder\nSpec: none\nDepends on: none\n"
        "Blocked by: none\nCreated: 2026-08-04\nEvidence gate: required\n"
        f"Source guidance: {source_owner}\n\n"
        "## Goal\n\nComplete source-backed work.\n\n"
        + source_section
        + "\n## Acceptance\n\n- [ ] Source evidence is recorded.\n"
    )


def _single_source_evidence(*, identity: str = "Service operating policy") -> str:
    return (
        "## Source evidence\n\n"
        "### Authoritative sources\n\n"
        f"- Identity: {identity}; Applicable context: version 3, effective "
        "2026-08-01; Status: current; Scope: restart authorization\n\n"
        "### Conflict resolution\n\n"
        "- Status: none; Rule: no conflict among the sources applied to this "
        "task; Decision: n/a\n\n"
        "### Unverified claims\n\n"
        "- none\n"
    )


class SourceGuidanceNegativeFixtureTests(unittest.TestCase):
    def test_missing_authority_fails_closed(self) -> None:
        result = source_guidance.resolve_task_guidance(
            FIXTURES / "missing-authority.md"
        )
        self.assertEqual(result["outcome"], "invalid")
        self.assertIn("missing-authoritative-source", result["blocker_codes"])

    def test_stale_date_or_version_context_fails_closed(self) -> None:
        result = source_guidance.resolve_task_guidance(
            FIXTURES / "stale-context.md"
        )
        self.assertEqual(result["outcome"], "invalid")
        self.assertIn("stale-applicable-context", result["blocker_codes"])

    def test_unresolved_conflict_fails_closed(self) -> None:
        result = source_guidance.resolve_task_guidance(
            FIXTURES / "unresolved-conflict.md"
        )
        self.assertEqual(result["outcome"], "invalid")
        self.assertIn("unresolved-source-conflict", result["blocker_codes"])

    def test_decisive_unverified_claim_fails_closed(self) -> None:
        result = source_guidance.resolve_task_guidance(
            FIXTURES / "decisive-unverified-claim.md"
        )
        self.assertEqual(result["outcome"], "invalid")
        self.assertIn("decisive-claim-unverified", result["blocker_codes"])


class SourceGuidanceGreenFixtureTests(unittest.TestCase):
    def test_machine_authority_declares_the_human_and_runtime_contract(self) -> None:
        declared = source_guidance.contract()
        self.assertEqual(declared["contract_id"], "source-guidance")
        self.assertEqual(declared["task_declarations"], ["task", "spec", "n/a"])
        self.assertEqual(declared["conflict_statuses"], ["none", "resolved", "unresolved"])
        self.assertEqual(declared["claim_decisiveness"], ["decisive", "non-decisive"])
        expected = {
            "protocol/CONVENTIONS.md": "Source guidance",
            "protocol/RISK_AND_PRACTICE.md": "Source guidance",
            "templates/TASK.md": "Source guidance",
            "templates/SPEC.md": "Source guidance",
            "templates/PROMPT.md": "Source guidance",
            "templates/REPORT.md": "Source evidence",
            "skills/run-task.md": "source_guidance",
            "skills/run-handoff.md": "source_guidance",
        }
        for relative, needle in expected.items():
            text = (REPO_ROOT / relative).read_text(encoding="utf-8")
            self.assertIn(needle, text, relative)

    def test_valid_record_preserves_sources_context_conflicts_and_claims(self) -> None:
        result = source_guidance.resolve_task_guidance(
            FIXTURES / "valid-source-backed.md"
        )
        self.assertEqual(result["outcome"], "valid")
        self.assertEqual(len(result["authoritative_sources"]), 2)
        self.assertEqual(result["conflict_resolution"]["status"], "resolved")
        self.assertEqual(
            result["unverified_claims"][0]["decisiveness"], "non-decisive"
        )
        self.assertNotIn("confidence", json.dumps(result).lower())

    def test_task_may_delegate_the_single_record_to_a_contained_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "phases").mkdir()
            (root / "specs").mkdir()
            (root / "tasks" / "open").mkdir(parents=True)
            spec = root / "specs" / "SPEC-04-002.md"
            spec.write_text(
                "# SPEC-04-002\n\nSource guidance: required\n\n"
                + _source_section(),
                encoding="utf-8",
            )
            task = root / "tasks" / "open" / "TASK-04-002.md"
            task.write_text(
                "# TASK-04-002\n\nSpec: SPEC-04-002.md\nSource guidance: spec\n",
                encoding="utf-8",
            )
            result = source_guidance.resolve_task_guidance(task)
        self.assertEqual(result["outcome"], "valid")
        self.assertEqual(result["owner_kind"], "spec")
        self.assertEqual(result["owner_path"], str(spec.resolve()))

    def test_render_spec_projects_and_deidentifies_the_same_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "SPEC-04-002.md"
            spec.write_text(
                "# SPEC-04-002: Source contract\n\nSource guidance: required\n\n"
                + _source_section(),
                encoding="utf-8",
            )
            out = io.StringIO()
            err = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = render_spec.handler(argparse.Namespace(spec_path=str(spec)))
        self.assertEqual(rc, 0, err.getvalue())
        record = json.loads(out.getvalue())
        self.assertEqual(record["source_guidance"]["outcome"], "valid")
        self.assertNotIn("SPEC-04-002", record["source_guidance"]["deidentified_guidance"])

    def test_management_source_identity_has_stable_deidentified_alias(self) -> None:
        section = _source_section().replace(
            "Identity: Service operating policy",
            "Identity: decisions/DEC-050.md",
        )
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "TASK-04-002.md"
            task.write_text(_task_body(section), encoding="utf-8")
            guidance = source_guidance.resolve_task_guidance(task)
            projected = guidance["deidentified_guidance"]
            report = projected.replace(
                "## Source guidance", "## Source evidence", 1
            )
            evidence = source_guidance.resolve_report_evidence(task, report)

        self.assertNotIn("DEC-050", projected)
        self.assertNotIn("decisions/.md", projected)
        self.assertIn("project-management-source sha256:", projected)
        self.assertEqual(evidence["outcome"], "valid")

    def test_report_evidence_may_name_only_the_sources_actually_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "TASK-04-002.md"
            task.write_text(
                _task_body(_source_section()),
                encoding="utf-8",
            )
            evidence = source_guidance.resolve_report_evidence(
                task,
                _single_source_evidence(),
            )

        self.assertEqual(evidence["outcome"], "valid")
        self.assertEqual(len(evidence["evidence"]["authoritative_sources"]), 1)

    def test_report_evidence_accepts_indented_markdown_continuations(self) -> None:
        report = (
            "## Source evidence\n\n"
            "### Authoritative sources\n\n"
            "- Identity: Service operating policy; Applicable context: version 3, effective\n"
            "  2026-08-01; Status: current; Scope: restart authorization\n"
            "- Identity: Approved runbook; Applicable context: revision 7, reviewed\n"
            "  2026-07-28; Status: current; Scope: restart sequence and rollback\n\n"
            "### Conflict resolution\n\n"
            "- Status: resolved; Rule: the operating policy governs authority and the\n"
            "  runbook governs execution; Decision: use version 3 policy with revision 7\n"
            "  runbook\n\n"
            "### Unverified claims\n\n"
            "- none\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "TASK-04-002.md"
            task.write_text(_task_body(_source_section()), encoding="utf-8")
            evidence = source_guidance.resolve_report_evidence(task, report)

        self.assertEqual(evidence["outcome"], "valid")
        parsed = evidence["evidence"]
        self.assertEqual(len(parsed["authoritative_sources"]), 2)
        self.assertEqual(
            parsed["authoritative_sources"][0]["applicable_context"],
            "version 3, effective 2026-08-01",
        )
        self.assertEqual(
            parsed["conflict_resolution"]["decision"],
            "use version 3 policy with revision 7 runbook",
        )

    def test_unindented_prose_is_not_folded_into_a_source_row(self) -> None:
        rows = source_guidance._rows(
            [
                "- Identity: Service operating policy; Applicable context: version 3",
                "unrelated prose; Status: current; Scope: restart authorization",
            ]
        )

        self.assertEqual(
            rows,
            ["Identity: Service operating policy; Applicable context: version 3"],
        )

    def test_report_evidence_rejects_a_source_outside_governing_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "TASK-04-002.md"
            task.write_text(
                _task_body(_source_section()),
                encoding="utf-8",
            )
            evidence = source_guidance.resolve_report_evidence(
                task,
                _single_source_evidence(identity="Unapproved operating memo"),
            )

        self.assertEqual(evidence["outcome"], "invalid")
        self.assertIn(
            "source-evidence-not-in-guidance",
            evidence["blocker_codes"],
        )


class SourceGuidanceProjectionTests(unittest.TestCase):
    def _scaffold(self):
        toml = (
            "[project]\n"
            'id = "source-guidance"\n'
            'name = "Source guidance"\n'
            'project_schema_version = "v0.10.0"\n'
            "\n[reviews]\nplanning = \"off\"\ntask_closure = \"off\"\n"
        )
        return project_scaffold(cartopian_toml=toml)

    def test_task_bundle_projects_the_shared_record(self) -> None:
        with self._scaffold() as scaffold:
            scaffold.write("phases/PHASE-04.md", "# PHASE-04\n")
            task = scaffold.write(
                "tasks/open/TASK-04-002.md",
                _task_body(_source_section()),
            )
            scaffold.capture_request(
                request_id="REQUEST-001",
                unit="task:TASK-04-002",
                text="Complete the source-backed work.",
            )
            content = task.read_text(encoding="utf-8")
            headers, presence = task_bundle._parse_headers(content)
            record = source_guidance.resolve_task_guidance(task, content=content)
            check = source_guidance.readiness_check(record)
        self.assertEqual(headers["Source guidance"], "task")
        self.assertTrue(presence["Source guidance"])
        self.assertTrue(check["pass"])
        self.assertEqual(record["outcome"], "valid")

    def test_cli_and_mcp_task_bundle_project_identical_source_records(self) -> None:
        with self._scaffold() as scaffold:
            scaffold.write("phases/PHASE-04.md", "# PHASE-04\n")
            task = scaffold.write(
                "tasks/open/TASK-04-002.md", _task_body(_source_section())
            )
            scaffold.capture_request(
                request_id="REQUEST-001",
                unit="task:TASK-04-002",
                text="Complete the source-backed work.",
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                rc = task_bundle.handler(argparse.Namespace(task_path=str(task)))
            self.assertEqual(rc, 0, stderr.getvalue())
            cli_record = json.loads(stdout.getvalue())
            mcp_result = server.handle_request(
                "tools/call",
                {"name": "task_bundle", "arguments": {"task_path": str(task)}},
            )
            mcp_record = mcp_result["structuredContent"]["records"][0]
        self.assertEqual(cli_record["source_guidance"], mcp_record["source_guidance"])
        self.assertEqual(cli_record["ready"], mcp_record["ready"])

    def test_complete_report_requires_matching_source_evidence(self) -> None:
        with self._scaffold() as scaffold:
            scaffold.write("phases/PHASE-04.md", "# PHASE-04\n")
            task = scaffold.write(
                "tasks/in-progress/TASK-04-002.md", _task_body(_source_section())
            )
            base = (
                "# REPORT-04-002\n\nStatus: complete\n\n"
                "## Identity\n\n- Work root: n/a\n\n"
                "## Completion evidence\n\nSource checks completed.\n\n"
            )
            tail = "\n## Remaining risks\n\nnone.\n\n## Ready to close\n\nyes\n"
            report = scaffold.write(
                "reports/REPORT-04-002.md",
                base + _single_source_evidence() + tail,
            )
            good_out = io.StringIO()
            with contextlib.redirect_stdout(good_out):
                good_rc = report_action.handler(
                    argparse.Namespace(report_path=str(report), variant=None)
                )
            report.write_text(base + tail, encoding="utf-8")
            bad_out = io.StringIO()
            with contextlib.redirect_stdout(bad_out):
                bad_rc = report_action.handler(
                    argparse.Namespace(report_path=str(report), variant=None)
                )
        self.assertEqual(good_rc, 0)
        self.assertEqual(json.loads(good_out.getvalue())["verdict"], "accepted")
        self.assertEqual(bad_rc, 0)
        bad_record = json.loads(bad_out.getvalue())
        self.assertEqual(bad_record["verdict"], "failed-to-parse")
        self.assertIn(
            "missing-source-guidance-section",
            bad_record["source_evidence"]["blocker_codes"],
        )


if __name__ == "__main__":
    unittest.main()
