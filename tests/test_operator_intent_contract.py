"""Executable contract for independent operator-intent review evidence.

The governing negative regression is explicit here: the v0.7 review path can
close a task when management-authored task, prompt, report, and review agree
with one another even if the review records drift.  The v0.8 path must surface
the independently attested source and refuse that same approval.
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

from cli import operator_intent
from cli.capabilities import PRESETS
from cli.commands import dispatch, handoff_packet, move_task, review_context
from cli.main import EXIT_FAIL, EXIT_OK, OPERATOR_ONLY_SUBCOMMANDS, main
from mcp_server import server
from tests.scaffold import project_scaffold


def _config(version: str = "v0.8.0") -> str:
    return (
        "[project]\n"
        'id = "intent-contract"\n'
        'name = "Intent Contract"\n'
        f'project_schema_version = "{version}"\n'
        "\n"
        "[roles.coder]\n"
        'description = "Implements work."\n'
        "\n"
        "[roles.reviewer]\n"
        'description = "Reviews work."\n'
        'target = "reviewer-wrapper"\n'
        "\n"
        "[reviews]\n"
        'planning = "required"\n'
        'planning_role = "reviewer"\n'
        'task_closure = "required"\n'
        'task_role = "reviewer"\n'
    )


def _decision(
    scaffold,
    number: int,
    body: str,
    *,
    supersedes: str = "none",
) -> Path:
    return scaffold.write(
        f"decisions/DEC-{number:03d}-choice.md",
        (
            f"# DEC-{number:03d}: Choice\n\n"
            "Date: 2026-07-25\n"
            "Status: locked\n"
            f"Supersedes: {supersedes}\n\n"
            "## Context\n\nThe operator chose explicitly.\n\n"
            f"## Decision\n\n{body}\n\n"
            "## Consequences\n\nReviews must follow this choice.\n"
        ),
    )


def _decision_with_exact_bytes(scaffold, number: int, size: int) -> Path:
    prefix = (
        f"# DEC-{number:03d}: Sized\n\n"
        "Date: 2026-07-25\n"
        "Status: locked\n"
        "Supersedes: none\n\n"
        "## Decision\n\n"
    )
    suffix = "\n"
    remaining = size - len((prefix + suffix).encode("utf-8"))
    if remaining < 0:
        raise AssertionError("requested decision size is smaller than its headers")
    path = scaffold.write(
        f"decisions/DEC-{number:03d}-sized.md",
        prefix + ("x" * remaining) + suffix,
    )
    if len(path.read_bytes()) != size:
        raise AssertionError("sized decision fixture is not byte exact")
    return path


def _attestation(
    scaffold,
    number: int,
    source: Path,
    *,
    scopes: tuple[str, ...] = ("project",),
    sections: tuple[str, ...] = (),
    required: bool = True,
    status: str = "current",
    supersedes: str | None = None,
    source_kind: str = "decision",
) -> Path:
    source_relpath = source.relative_to(scaffold.project_root).as_posix()
    raw = source.read_bytes()
    attestation = operator_intent.Attestation(
        attestation_id=f"ATTEST-{number:03d}",
        status=status,
        confirmed_by="operator",
        confirmed_at="2026-07-25",
        source_kind=source_kind,
        source_relpath=source_relpath,
        source_hash=operator_intent.content_identity(raw),
        required=required,
        scopes=tuple(operator_intent.parse_scope(value) for value in scopes),
        sections=sections,
        supersedes=supersedes,
        relpath=f"intent/ATTEST-{number:03d}-choice.md",
    )
    return scaffold.write(
        attestation.relpath,
        operator_intent.render_attestation(attestation, "Choice"),
    )


def _task(scaffold, status: str = "in-review", refs: str = "none") -> Path:
    return scaffold.write(
        f"tasks/{status}/TASK-02-009-review-integrity.md",
        (
            "# TASK-02-009: Review integrity\n\n"
            "Phase: PHASE-02-review-integrity\n"
            "Plan ref: P02-BUILD-009\n"
            f"Intent refs: {refs}\n"
            "Work root: n/a\n"
            "Assignee: coder\n"
            "Evidence gate: required\n\n"
            "## Goal\n\nImplement nested roles.\n\n"
            "## Acceptance\n\n- [ ] Nested roles are implemented.\n"
        ),
    )


def _review(scaffold, alignment: str) -> Path:
    return scaffold.write(
        "reviews/REVIEW-02-009.md",
        (
            "# REVIEW-02-009\n\n"
            "Target: TASK-02-009-review-integrity\n"
            "Plan ref: P02-BUILD-009\n"
            "Verdict: approve\n"
            f"Operator-intent alignment: {alignment}\n\n"
            f"Operator-intent evidence: "
            f"{'ATTEST-001' if 'ATTEST-001' in alignment else 'none recorded'}\n\n"
            "## Summary\n\nAll management-authored artifacts agree.\n"
        ),
    )


def _move(task: Path) -> tuple[int, str]:
    args = argparse.Namespace(
        task_path=str(task),
        to_status="done",
        administrative=False,
        reason=None,
    )
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        result = move_task.handler(args)
    return result, err.getvalue()


class TestOperatorOnlySurface(unittest.TestCase):
    def test_confirmation_is_operator_only_and_not_a_preset_or_mcp_tool(self) -> None:
        self.assertIn("attest-intent", OPERATOR_ONLY_SUBCOMMANDS)
        self.assertNotIn("attest_intent", server._tool_registry())
        self.assertFalse(
            any("attest" in grant or "operator-intent" in grant for grants in PRESETS.values() for grant in grants)
        )

    def test_confirmation_defaults_required_and_missing_requiredness_is_invalid(
        self,
    ) -> None:
        with project_scaffold(cartopian_toml=_config()) as scaffold:
            source = _decision(scaffold, 1, "Roles remain flat.")
            argv = [
                "attest-intent",
                str(scaffold.project_root),
                "--attestation-id",
                "ATTEST-001",
                "--slug",
                "flat-roles",
                "--title",
                "Flat roles",
                "--source-kind",
                "decision",
                "--source",
                source.relative_to(scaffold.project_root).as_posix(),
                "--scope",
                "project",
                "--confirmed-at",
                "2026-07-25",
                "--confirm",
            ]
            with mock.patch.dict(
                os.environ,
                {"CARTOPIAN_ROLE": "", "CARTOPIAN_MCP_TOOL_CALL": ""},
                clear=False,
            ), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(argv), EXIT_OK)
            written = scaffold.project_root / "intent/ATTEST-001-flat-roles.md"
            parsed = operator_intent.parse_attestation(
                written.read_text(encoding="utf-8"),
                "intent/ATTEST-001-flat-roles.md",
                raw_bytes=written.read_bytes(),
            )
            self.assertTrue(parsed.required)

            written.write_text(
                "\n".join(
                    line
                    for line in written.read_text(encoding="utf-8").splitlines()
                    if not line.startswith("Required:")
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                operator_intent.IntentRefusal, "missing-requiredness"
            ):
                operator_intent.context_for_task(
                    scaffold.project_root, _task(scaffold)
                )


class TestResolverContract(unittest.TestCase):
    def test_automatic_scan_surfaces_complete_attributable_evidence(self) -> None:
        with project_scaffold(cartopian_toml=_config()) as scaffold:
            source = _decision(scaffold, 1, "Roles remain flat.")
            _attestation(scaffold, 1, source, scopes=("phase:PHASE-02-review-integrity",))
            task = _task(scaffold)

            context = operator_intent.context_for_task(scaffold.project_root, task)
            record = context.as_record()

            self.assertFalse(context.none_recorded)
            self.assertEqual(context.attestations_scanned, 1)
            self.assertEqual(context.supplemental_references, [])
            evidence = record["operator_intent"]["evidence"][0]
            self.assertEqual(evidence["source_identity"], "DEC-001")
            self.assertRegex(evidence["attestation_hash"], r"^sha256:[0-9a-f]{64}$")
            self.assertEqual(evidence["selected_content"][0]["name"], "whole source")
            self.assertIn("Roles remain flat.", evidence["selected_content"][0]["content"])
            self.assertEqual(evidence["discovery"], ["applicability-scan"])

    def test_none_recorded_only_after_complete_scan(self) -> None:
        with project_scaffold(cartopian_toml=_config()) as scaffold:
            source = _decision(scaffold, 1, "Only phase three.")
            _attestation(scaffold, 1, source, scopes=("phase:PHASE-03-later",))
            task = _task(scaffold)

            context = operator_intent.context_for_task(scaffold.project_root, task)

            self.assertTrue(context.none_recorded)
            self.assertEqual(context.attestations_scanned, 1)
            self.assertEqual(context.evidence, [])
            self.assertIn("none recorded", context.section)

    def test_attestation_byte_change_invalidates_existing_binding(self) -> None:
        with project_scaffold(cartopian_toml=_config()) as scaffold:
            source = _decision(scaffold, 1, "Roles remain flat.")
            attestation_path = _attestation(scaffold, 1, source)
            task = _task(scaffold)
            before = operator_intent.context_for_task(scaffold.project_root, task)
            prompt = operator_intent.upsert_intent_section("# Review\n", before.section)

            attestation_path.write_text(
                attestation_path.read_text(encoding="utf-8")
                + "\nOperator-visible note changed.\n",
                encoding="utf-8",
            )
            after = operator_intent.context_for_task(scaffold.project_root, task)

            self.assertNotEqual(before.context_identity, after.context_identity)
            preflight = operator_intent.preflight_prompt_binding(after, prompt)
            self.assertFalse(preflight["ok"])
            self.assertEqual(preflight["rule"], "stale-prompt-binding")

    def test_automatic_applicability_resolves_unique_attested_successor(self) -> None:
        with project_scaffold(cartopian_toml=_config()) as scaffold:
            old = _decision(scaffold, 1, "Roles may be nested.")
            new = _decision(
                scaffold, 2, "Roles remain flat.", supersedes="DEC-001"
            )
            _attestation(scaffold, 1, old)
            _attestation(scaffold, 2, new)
            task = _task(scaffold)

            context = operator_intent.context_for_task(scaffold.project_root, task)

            self.assertEqual(len(context.evidence), 1)
            evidence = context.evidence[0].as_record()
            self.assertEqual(evidence["source_identity"], "DEC-002")
            self.assertEqual(evidence["superseded_from"], "DEC-001")
            self.assertEqual(
                evidence["provenance_chain"], ["DEC-001", "DEC-002"]
            )

    def test_one_source_cannot_contribute_more_than_eight_kib_across_attestations(
        self,
    ) -> None:
        with project_scaffold(cartopian_toml=_config()) as scaffold:
            source = scaffold.write(
                "decisions/DEC-001-large.md",
                (
                    "# DEC-001: Large\n\n"
                    "Date: 2026-07-25\nStatus: locked\nSupersedes: none\n\n"
                    "## Decision\n\nRoles remain flat.\n\n"
                    "## Constraint A\n\n" + ("a" * 4100) + "\n\n"
                    "## Constraint B\n\n" + ("b" * 4100) + "\n"
                ),
            )
            _attestation(
                scaffold, 1, source, sections=("Decision", "Constraint A")
            )
            _attestation(
                scaffold, 2, source, sections=("Decision", "Constraint B")
            )
            task = _task(scaffold)

            with self.assertRaisesRegex(
                operator_intent.IntentRefusal, "per-source"
            ):
                operator_intent.context_for_task(scaffold.project_root, task)

    def test_requirements_attestation_cannot_promote_unconfirmed_sections(self) -> None:
        with project_scaffold(cartopian_toml=_config()) as scaffold:
            source = scaffold.write(
                "REQUIREMENTS.md",
                (
                    "# Requirements\n\n"
                    "## Confirmed intent\n\nRoles remain flat.\n\n"
                    "## PM proposal\n\nRoles become nested.\n"
                ),
            )
            _attestation(
                scaffold,
                1,
                source,
                source_kind="requirements-intent",
                sections=("Confirmed intent", "PM proposal"),
            )
            task = _task(scaffold)

            with self.assertRaisesRegex(
                operator_intent.IntentRefusal, "ineligible-source"
            ):
                operator_intent.context_for_task(scaffold.project_root, task)

    def test_closed_reference_failures_are_readiness_blockers(self) -> None:
        with self.subTest("malformed"):
            with self.assertRaisesRegex(
                operator_intent.IntentRefusal, "malformed-reference"
            ):
                operator_intent.parse_intent_refs("dec-001")

        with project_scaffold(cartopian_toml=_config()) as scaffold:
            task = _task(scaffold, refs="ATTEST-999")
            with self.subTest("missing"):
                with self.assertRaisesRegex(
                    operator_intent.IntentRefusal, "unresolved-reference"
                ):
                    operator_intent.context_for_task(scaffold.project_root, task)

        with project_scaffold(cartopian_toml=_config()) as scaffold:
            _decision(scaffold, 1, "PM-only locked choice.")
            task = _task(scaffold, refs="DEC-001")
            with self.subTest("unattested"):
                with self.assertRaisesRegex(
                    operator_intent.IntentRefusal, "unattested-source"
                ):
                    operator_intent.context_for_task(scaffold.project_root, task)

        with project_scaffold(cartopian_toml=_config()) as scaffold:
            first = _decision(scaffold, 1, "First copy.")
            scaffold.write(
                "decisions/DEC-001-duplicate.md",
                first.read_text(encoding="utf-8"),
            )
            task = _task(scaffold, refs="DEC-001")
            with self.subTest("ambiguous"):
                with self.assertRaisesRegex(
                    operator_intent.IntentRefusal, "ambiguous-reference"
                ):
                    operator_intent.context_for_task(scaffold.project_root, task)

        with project_scaffold(cartopian_toml=_config()) as scaffold:
            source = _decision(scaffold, 1, "Choice.")
            source.write_text(
                source.read_text(encoding="utf-8").replace(
                    "Status: locked", "Status: proposed"
                ),
                encoding="utf-8",
            )
            _attestation(scaffold, 1, source)
            task = _task(scaffold)
            with self.subTest("open"):
                with self.assertRaisesRegex(
                    operator_intent.IntentRefusal, "open-decision"
                ):
                    operator_intent.context_for_task(scaffold.project_root, task)

        with project_scaffold(cartopian_toml=_config()) as scaffold:
            outside = scaffold.root / "outside.md"
            outside.write_text("outside operator data\n", encoding="utf-8")
            attestation = operator_intent.Attestation(
                attestation_id="ATTEST-001",
                status="current",
                confirmed_by="operator",
                confirmed_at="2026-07-25",
                source_kind="requirements-intent",
                source_relpath="../outside.md",
                source_hash=operator_intent.content_identity(outside.read_bytes()),
                required=True,
                scopes=(operator_intent.parse_scope("project"),),
                sections=(operator_intent.REQUIREMENTS_INTENT_SECTION,),
                supersedes=None,
                relpath="intent/ATTEST-001-outside.md",
            )
            scaffold.write(
                attestation.relpath,
                operator_intent.render_attestation(attestation, "Outside"),
            )
            with self.subTest("outside-project"):
                with self.assertRaisesRegex(
                    operator_intent.IntentRefusal, "outside-project-source"
                ):
                    operator_intent.context_for_task(
                        scaffold.project_root, _task(scaffold)
                    )

    def test_superseded_attestation_ref_resolves_only_to_unique_current_successor(
        self,
    ) -> None:
        with project_scaffold(cartopian_toml=_config()) as scaffold:
            old = _decision(scaffold, 1, "Roles may be nested.")
            new = _decision(scaffold, 2, "Roles remain flat.")
            _attestation(scaffold, 1, old, status="superseded")
            _attestation(
                scaffold,
                2,
                new,
                scopes=("phase:PHASE-03-later",),
                supersedes="ATTEST-001",
            )
            context = operator_intent.context_for_task(
                scaffold.project_root, _task(scaffold, refs="ATTEST-001")
            )
            self.assertEqual(
                [item.attestation.attestation_id for item in context.evidence],
                ["ATTEST-002"],
            )
            record = context.evidence[0].as_record()
            self.assertTrue(record["current"])
            self.assertTrue(record["resolved_from_superseded"])
            self.assertEqual(
                record["provenance_chain"], ["ATTEST-001", "ATTEST-002"]
            )

    def test_exact_context_bounds_pass_and_overflow_refuses_without_truncation(
        self,
    ) -> None:
        with project_scaffold(cartopian_toml=_config()) as scaffold:
            for number in range(1, 4):
                source = _decision_with_exact_bytes(
                    scaffold, number, operator_intent.WHOLE_SOURCE_MAX_BYTES
                )
                _attestation(scaffold, number, source)
            task = _task(scaffold)
            context = operator_intent.context_for_task(scaffold.project_root, task)
            self.assertEqual(
                context.measures["selected_bytes"],
                operator_intent.TOTAL_MAX_BYTES,
            )
            self.assertTrue(
                all(
                    item.selected_bytes == operator_intent.PER_SOURCE_MAX_BYTES
                    for item in context.evidence
                )
            )
            self.assertIn("x" * 128, context.section)

            overflow = _decision(scaffold, 4, "One complete extra clause.")
            _attestation(scaffold, 4, overflow)
            with self.assertRaisesRegex(
                operator_intent.IntentRefusal, "context-overflow"
            ) as raised:
                operator_intent.context_for_task(scaffold.project_root, task)
            self.assertIn("never truncated", raised.exception.detail)

    def test_whole_source_above_eight_kib_requires_complete_selectors(self) -> None:
        with project_scaffold(cartopian_toml=_config()) as scaffold:
            source = _decision_with_exact_bytes(
                scaffold, 1, operator_intent.WHOLE_SOURCE_MAX_BYTES + 1
            )
            _attestation(scaffold, 1, source)
            with self.assertRaisesRegex(
                operator_intent.IntentRefusal, "oversize-source"
            ):
                operator_intent.context_for_task(
                    scaffold.project_root, _task(scaffold)
                )


class TestPromptAndHandoffContract(unittest.TestCase):
    def test_planning_metadata_and_supplemental_refs_round_trip_at_preflight(
        self,
    ) -> None:
        with project_scaffold(cartopian_toml=_config()) as scaffold:
            source = _decision(scaffold, 1, "Roles remain flat.")
            _attestation(
                scaffold,
                1,
                source,
                scopes=("phase:PHASE-02-review-integrity",),
            )
            prompt_path = scaffold.write(
                "prompts/PROMPT-PLAN-003-phases.md",
                (
                    "# Review phases\n\n"
                    "Phase: PHASE-02-review-integrity\n"
                    "Plan ref: P02-BUILD-009\n"
                    "Intent refs: DEC-001\n\n"
                    "## Your task\n\nReview the phase.\n"
                ),
            )
            context = operator_intent.context_for_checkpoint(
                scaffold.project_root,
                "PLAN-003-phases",
                phase_id="PHASE-02-review-integrity",
                plan_ref="P02-BUILD-009",
                checkpoint_text=prompt_path.read_text(encoding="utf-8"),
            )
            prompt_path.write_text(
                operator_intent.upsert_intent_section(
                    prompt_path.read_text(encoding="utf-8"), context.section
                ),
                encoding="utf-8",
            )

            ok, record = dispatch._preflight_operator_intent(
                scaffold.project_root,
                "planning_review",
                None,
                prompt_path,
            )

            self.assertTrue(ok, record)
            self.assertEqual(record["evidence"], ["ATTEST-001"])

    def test_automatic_task_review_dispatch_preflights_attested_evidence(
        self,
    ) -> None:
        from tests.cli.commands.test_dispatch import _dispatch, _make_stub

        with project_scaffold(cartopian_toml="") as scaffold, tempfile.TemporaryDirectory(
            prefix="cartopian-intent-dispatch-"
        ) as raw:
            temp_root = Path(raw)
            stub = _make_stub(temp_root)
            config = _config().replace(
                'target = "reviewer-wrapper"\n',
                f'target = "{stub}"\nauto_launch = ["task_review"]\n',
            )
            scaffold.write("cartopian.toml", config)
            source = _decision(scaffold, 1, "Roles remain flat.")
            _attestation(scaffold, 1, source)
            task = _task(scaffold)
            context = operator_intent.context_for_task(scaffold.project_root, task)
            prompt = scaffold.write(
                "prompts/PROMPT-02-009.md",
                operator_intent.upsert_intent_section(
                    "# Review\n", context.section
                ),
            )
            capture = temp_root / "capture.json"
            fake_home = temp_root / "home"
            fake_home.mkdir()
            with mock.patch.dict(
                os.environ,
                {"STUB_CAPTURE": str(capture), "STUB_NO_REPORT": "1"},
                clear=False,
            ):
                stdout, stderr, result = _dispatch(
                    str(task), "reviewer", fake_home
                )

            self.assertEqual(result, EXIT_OK, stderr)
            record = json.loads(stdout)
            self.assertEqual(record["status"], "dispatched")
            self.assertTrue(record["operator_intent"]["ok"])
            self.assertEqual(
                record["operator_intent"]["evidence"], ["ATTEST-001"]
            )

    def test_manual_task_review_handoff_refuses_missing_prompt(self) -> None:
        with project_scaffold(cartopian_toml=_config()) as scaffold:
            task = _task(scaffold)
            args = argparse.Namespace(task_path=str(task), role="reviewer")
            err = io.StringIO()
            with mock.patch(
                "cli.commands.handoff_packet.Path.home",
                return_value=scaffold.root,
            ), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                result = handoff_packet.handler(args)

            self.assertEqual(result, EXIT_FAIL)
            self.assertIn("missing-prompt", err.getvalue())

    def test_manual_task_review_handoff_emits_the_bound_evidence_context(self) -> None:
        with project_scaffold(cartopian_toml=_config()) as scaffold:
            source = _decision(scaffold, 1, "Roles remain flat.")
            _attestation(scaffold, 1, source)
            task = _task(scaffold)
            context = operator_intent.context_for_task(scaffold.project_root, task)
            scaffold.write(
                "prompts/PROMPT-02-009.md",
                operator_intent.upsert_intent_section(
                    "# Review\n", context.section
                ),
            )
            args = argparse.Namespace(task_path=str(task), role="reviewer")
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(
                io.StringIO()
            ):
                result = handoff_packet.handler(args)
            self.assertEqual(result, EXIT_OK)
            record = json.loads(out.getvalue())
            self.assertTrue(record["operator_intent"]["preflight"]["ok"])
            self.assertEqual(
                record["operator_intent"]["operator_intent"]["evidence"][0][
                    "attestation_id"
                ],
                "ATTEST-001",
            )

    def test_cli_and_mcp_review_context_records_are_semantically_equal(self) -> None:
        with project_scaffold(cartopian_toml=_config()) as scaffold:
            first = _decision(scaffold, 1, "Project-wide advisory.")
            second = _decision(scaffold, 2, "Task-specific required choice.")
            _attestation(scaffold, 1, first, required=False)
            _attestation(
                scaffold,
                2,
                second,
                scopes=("task:TASK-02-009",),
            )
            task = _task(scaffold)
            args = argparse.Namespace(
                project_root=str(scaffold.project_root),
                review_kind="task-closure",
                task=str(task),
                checkpoint=None,
                phase=None,
                plan_ref=None,
                intent_ref=None,
                prompt=None,
            )
            cli_out = io.StringIO()
            with contextlib.redirect_stdout(cli_out), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(review_context.handler(args), EXIT_OK)
            cli_record = json.loads(cli_out.getvalue())

            mcp_result = server.call_tool(
                "review_context",
                {
                    "project_root": str(scaffold.project_root),
                    "review_kind": "task-closure",
                    "task": str(task),
                },
            )
            self.assertFalse(mcp_result["isError"])
            mcp_record = mcp_result["structuredContent"]["records"][0]
            self.assertEqual(mcp_record, cli_record)
            evidence = cli_record["operator_intent"]["evidence"]
            self.assertEqual(
                [item["attestation_id"] for item in evidence],
                ["ATTEST-002", "ATTEST-001"],
            )
            self.assertEqual(
                [item["required"] for item in evidence], [True, False]
            )
            self.assertEqual(
                cli_record["measures"]["selected_bytes"],
                sum(item["selected_bytes"] for item in evidence),
            )

    def test_manual_preflight_refuses_an_outside_project_prompt_before_reading(
        self,
    ) -> None:
        with project_scaffold(cartopian_toml=_config()) as scaffold:
            task = _task(scaffold)
            outside = scaffold.root / "outside-prompt.md"
            outside.write_text("# unrelated operator data\n", encoding="utf-8")
            args = argparse.Namespace(
                project_root=str(scaffold.project_root),
                review_kind="task-closure",
                task=str(task),
                checkpoint=None,
                phase=None,
                plan_ref=None,
                intent_ref=None,
                prompt=str(outside),
            )
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                err
            ):
                result = review_context.handler(args)
            self.assertEqual(result, EXIT_FAIL)
            self.assertIn("outside-project-source", err.getvalue())
            self.assertNotIn("unrelated operator data", err.getvalue())


class TestAlignmentNegativeRegression(unittest.TestCase):
    def test_aligned_current_evidence_is_nonblocking(self) -> None:
        result = operator_intent.parse_alignment(
            "Operator-intent alignment: aligned\n"
            "Operator-intent evidence: ATTEST-001\n",
            required_evidence=True,
            expected_evidence=["ATTEST-001"],
        )
        self.assertFalse(result["blocking"])
        self.assertEqual(result["value"], "aligned")

    def test_failing_before_management_agreement_could_reach_approval(self) -> None:
        with project_scaffold(cartopian_toml=_config("v0.7.0")) as scaffold:
            task = _task(scaffold)
            review = _review(scaffold, "drifted — operator chose flat roles")

            error = move_task._alignment_error(
                scaffold.project_root,
                review,
                review.read_text(encoding="utf-8"),
                "02-009",
            )

            self.assertIsNone(error)

    def test_passing_after_drift_blocks_approval_despite_management_agreement(
        self,
    ) -> None:
        with project_scaffold(cartopian_toml=_config()) as scaffold:
            source = _decision(scaffold, 1, "Roles remain flat.")
            _attestation(scaffold, 1, source)
            task = _task(scaffold)
            context = operator_intent.context_for_task(scaffold.project_root, task)
            scaffold.write(
                "prompts/PROMPT-02-009.md",
                operator_intent.upsert_intent_section(
                    "# Review nested roles\n\n"
                    "## Management guidance\n\nApprove nested roles.\n",
                    context.section,
                ),
            )
            _review(scaffold, "drifted — ATTEST-001 requires flat roles")

            result, error = _move(task)

            self.assertEqual(result, EXIT_FAIL)
            self.assertIn("drift blocks approval", error)
            self.assertTrue(task.is_file())

    def test_advisory_not_assessable_is_explicit_but_nonblocking(self) -> None:
        result = operator_intent.parse_alignment(
            "Operator-intent alignment: not assessable — source unavailable\n"
            "Operator-intent evidence: ATTEST-001\n",
            required_evidence=False,
            expected_evidence=["ATTEST-001"],
        )
        self.assertFalse(result["blocking"])
        self.assertEqual(result["value"], "not assessable")


if __name__ == "__main__":
    unittest.main()
