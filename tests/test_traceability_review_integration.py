"""Traceability and review independence: the integrated slice, proven whole.

The three accepted contracts (bounded acceptance-to-source traceability,
contract-before-implementation closure review with two independent
determinations, and bounded prompt-effectiveness evidence) each already carry
a mechanism-level suite. This module proves the *integrated* behavior: one
on-disk project driven through the real CLI dispatcher and the real MCP
`call_tool` entry point, across the whole assignment -> coder -> review ->
closure sequence, with an adversarial variant at every seam.

What is deliberately different here from the per-contract suites:

* the composed behavior under test is driven through a published surface
  (`cartopian <command>` via the real dispatcher, or the MCP tool of the same
  name via the real registry), so a seam that works in isolation and breaks
  when composed is caught. This is not an absolute: a minority of assertions
  read internal helper modules directly — `cli.acceptance_trace`,
  `cli.trace_binding`, `cli.deidentify`, `cli.prompt_evidence`, and
  `cli.commands.capture_request` — to build fixtures, recompute an expected
  digest, or enumerate a contracted constant. Those are helper-level observations, weaker evidence than a
  surface call, and the completion report names them as such;
* the coder-facing and reviewer-facing bodies are *measured* and the
  measurements are asserted as a receipt, so a context regression is a test
  failure rather than a note;
* unrelated project history (other tasks, other decisions, other requests) is
  present in the fixture, so "zero bytes from unrelated history" is a claim
  about a project that actually has history;
* CLI and MCP are run against the same inputs and their records compared for
  equality, so parity is observed rather than assumed from shared plumbing.

Nothing here changes production behavior. Every expectation is read off the
accepted contracts' own printed evidence or off a measurement this module
prints, never off a re-baselined implementation value.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import datetime
import importlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from unittest import mock

from cli import acceptance_trace as at
from cli import deidentify, prompt_evidence as pe, trace_binding
from cli.commands import capture_request as capture_request_command
from cli.main import build_parser
from mcp_server import server as mcp
from tests.scaffold import project_scaffold

# --------------------------------------------------------------------------
# Fixture vocabulary. Source identities and applicable contexts are the exact
# strings an `S|` record keys on, so the fixture and the coverage check read
# the same bytes.
# --------------------------------------------------------------------------
REQUIREMENTS = "Cartopian REQUIREMENTS.md and STANDARDS.md"
CONVENTIONS = "Cartopian protocol/CONVENTIONS.md and protocol/RISK_AND_PRACTICE.md"
CONTEXT = "active Product Refinement contract as of 2026-08-13"
CONVENTIONS_CONTEXT = "installed Cartopian v1.6.47"
DATE = "2026-08-19"
UNIT = "TASK-99-001"
REVIEW_ID = "REVIEW-99-001"

SPEC_ITEMS: Tuple[str, ...] = (
    "The fixture passes under the declared contract.",
    "The fixture reports its measured bodies.",
    "The fixture fails closed on a drifted criterion.",
)
TASK_ITEM = "The fixture records its own outcome."

# Governance vocabulary a coder-facing body may never carry: the authoritative
# source identities and their applicable contexts, and the trace contract's
# own provenance fields. `cli.deidentify`'s identifier grammar is checked
# separately, against the same body.
FORBIDDEN_IN_CODER_CONTEXT: Tuple[str, ...] = (
    REQUIREMENTS,
    CONVENTIONS,
    CONTEXT,
    CONVENTIONS_CONTEXT,
    "sha256:",
    "REQ-",
    "requirement",
    "standard",
    "operator-request",
    "plan-item",
    "decision",
)

CONFIG = """[project]
name = "Integrated"
id = "integrated"
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
description = "Implements tasks per spec"
grants = ["coder-like"]
agent = "cartopian-codex"

[roles.reviewer]
description = "Configured reviewer"
grants = ["reviewer-like"]
agent = "cartopian-codex"
"""


def spec_body(*, sources: Sequence[str]) -> str:
    rows = "\n".join(sources)
    items = "\n".join(f"- {item}" for item in SPEC_ITEMS)
    return f"""# SPEC-99-001: Integrated fixture

Status: locked
Profile: general
Source guidance: required

## Source guidance

### Authoritative sources

{rows}

### Conflict resolution

- Status: resolved; Rule: current requirements and standards govern product boundaries; Decision: implement only the bounded reviewed design

### Unverified claims

- none

## Examples / acceptance

{items}
"""


SOURCE_ROW_REQUIREMENTS = (
    f"- Identity: {REQUIREMENTS}; Applicable context: {CONTEXT}; "
    "Status: current; Scope: runtime and containment constraints"
)
SOURCE_ROW_CONVENTIONS = (
    f"- Identity: {CONVENTIONS}; Applicable context: {CONVENTIONS_CONTEXT}; "
    "Status: current; Scope: lifecycle and containment conventions"
)

# --------------------------------------------------------------------------
# Unrelated project history. Present in the fixture on purpose: the privacy
# claim is only worth something in a project that has a past.
# --------------------------------------------------------------------------
UNRELATED_HISTORY: Dict[str, str] = {
    "tasks/done/TASK-98-004.md": (
        "# TASK-98-004: Retired migration work\n\n"
        "Phase: PHASE-98\n"
        "Plan ref: BUILD-98-004\n"
        "Spec: none\n"
        "Upstream trace: n/a\n\n"
        "## Goal\n\nRetired. Superseded by DEC-041 and FR-031.\n\n"
        "## Acceptance\n\n- [x] The retired migration landed.\n"
    ),
    "decisions/DEC-041.md": (
        "# DEC-041: Retired migration decision\n\n"
        "Status: superseded\n\n"
        "## Decision\n\nThe retired migration used the pre-v0.9 layout.\n"
    ),
    "decisions/DEC-042.md": (
        "# DEC-042: Unrelated retention decision\n\n"
        "Status: accepted\n\n"
        "## Decision\n\nUnrelated retention window stays at one plan.\n"
    ),
    "phases/PHASE-98.md": (
        "# PHASE-98\n\n- `BUILD-98-004` — Retired migration work.\n"
    ),
}

#: Tokens that appear only in unrelated history, never in this task's slice.
UNRELATED_TOKENS: Tuple[str, ...] = (
    "TASK-98-004",
    "BUILD-98-004",
    "PHASE-98",
    "DEC-041",
    "DEC-042",
    "Retired migration",
    "pre-v0.9 layout",
    "Unrelated retention",
)


#: The two headings whose relative position in the finished artifact is the
#: whole of the procedural placement contract. Neither the constant nor any
#: check built on it carries a claim about reading or writing chronology.
CONTRACT_HEADING = "## Contract quality"
IMPLEMENTATION_HEADING = "## Implementation evidence"

REVIEW_TEMPLATE = """# REVIEW-99-001

Target: TASK-99-001
Reviewer: {reviewer}
Verdict: {verdict}
Request alignment: {alignment}
Request evidence: {alignment_evidence}

## Summary

Reviewed the fixture.

## Request comparison

Aligned.

{contract_quality}## Implementation evidence

- Commit SHA — abc1234

## Closure determinations

{determinations}

## Findings

{findings}

## Suggested actions

- none
"""

#: The measured routine-context receipt this proof was accepted against.
#: Regenerate with ``CARTOPIAN_PRINT_RECEIPT=1``; changing it is a context
#: decision, not a test maintenance step.
EXPECTED_RECEIPT: Dict[str, Any] = {
    "shape": {"n": 4, "e": 6, "s": 2, "r": 2, "w": 0, "x": 0, "o": 0},
    "coder_bytes": 119,
    "reviewer_bytes": 1752,
    "routine_bytes": 1871,
    "b_coder": 119,
    "b_routine": 1932,
    "coder_identity": (
        "sha256:a7492c7d54d84487e32be344c74f6f79b940e37c88fa601ba4c00e8f8772a13c"
    ),
    "reviewer_identity": (
        "sha256:a954ae9f7ad6e5e92ea1b41348fc4559b8c2ae999ca910f54efd1f96af1224db"
    ),
    "trace_identity": (
        "sha256:7e17e1ceb50b6bed6859a6388dd071bb5582c765494066cdf016845f3c9e051a"
    ),
}


CLEAN_CONTRACT_QUALITY = "## Contract quality\n\nOutcome: adequate\n\n"


class IntegratedSlice(unittest.TestCase):
    """One project, built once per test, carrying the whole slice and a past."""

    #: Extra authoritative source rows beyond the requirements row.
    extra_sources: Tuple[str, ...] = ()
    #: Extra operator corrections of REQUEST-001. A governed unit carries
    #: exactly one initiating request, so additional confirmed intent for the
    #: same unit arrives as a correction and takes the next evidence ordinal.
    extra_requests: Tuple[str, ...] = ()

    def setUp(self) -> None:
        self.scaffold = project_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root
        (self.root / "cartopian.toml").write_text(CONFIG, encoding="utf-8")
        for relative, body in UNRELATED_HISTORY.items():
            self.scaffold.write(relative, body)
        self.scaffold.write(
            "specs/SPEC-99-001.md",
            spec_body(sources=(SOURCE_ROW_REQUIREMENTS, *self.extra_sources)),
        )
        self.scaffold.write(
            "IMPLEMENTATION_PLAN.md",
            "# Plan\n\n- `BUILD-98-004` — Retired migration work.\n"
            "- `BUILD-99-001` — Integrated work.\n",
        )
        self.scaffold.write(
            "phases/PHASE-99.md", "# PHASE-99\n\n- `BUILD-99-001` — Integrated work.\n"
        )
        self.scaffold.capture_request(
            request_id="REQUEST-001",
            unit=f"task:{UNIT}",
            text="Build the integrated fixture.",
        )
        for ordinal, text in enumerate(self.extra_requests, start=1):
            self.capture_correction(ordinal, text)
        self.identities = [self.request_identity("REQUEST-001")] + [
            self.request_identity(f"REQUEST-001-CORRECTION-{ordinal:03d}")
            for ordinal in range(1, len(self.extra_requests) + 1)
        ]
        self.identity = self.identities[0]
        self.task = self.root / "tasks" / "in-progress" / f"{UNIT}.md"
        self.write_task()

    def request_identity(self, record_id: str) -> str:
        return json.loads(
            (self.root / "requests" / f"{record_id}.json").read_text(encoding="utf-8")
        )["content_identity"]

    def capture_correction(self, ordinal: int, text: str) -> None:
        """Capture one operator correction of the unit's initiating request.

        A governed unit has exactly one initiating request, so further
        confirmed intent for the same unit is a correction and takes the next
        evidence ordinal — which is what a second `REQ-` excerpt in the trace
        actually addresses.
        """
        source = self.scaffold.root / f"correction-{ordinal:03d}.txt"
        source.write_text(text, encoding="utf-8")
        args = argparse.Namespace(
            project_root=str(self.root),
            request_id="REQUEST-001",
            unit=f"task:{UNIT}",
            content_file=str(source),
            correction_of="REQUEST-001",
            captured_at=(
                datetime.date(2026, 7, 27) + datetime.timedelta(days=ordinal)
            ).isoformat()
            + "T12:00:00Z",
        )
        out, err = io.StringIO(), io.StringIO()
        fixture_env = {
            key: value
            for key, value in os.environ.items()
            if key not in capture_request_command.NON_OPERATOR_MARKERS
        }
        with (
            mock.patch.dict(os.environ, fixture_env, clear=True),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = capture_request_command.handler(args)
        self.assertEqual(code, 0, err.getvalue())

    # -- fixture construction --------------------------------------------

    def base_records(self, *, drift: bool = False) -> List[str]:
        """Four criteria: three spec-origin, one task-origin operator request."""
        lines = [
            f"C{index:02d}|{at.digest12(text)}|requirement|{REQUIREMENTS}|{CONTEXT}|1"
            for index, text in enumerate(SPEC_ITEMS, start=1)
        ]
        digest = at.digest12("something else entirely" if drift else TASK_ITEM)
        lines.append(
            f"C04|{digest}|operator-request|REQ-001 {self.identity}|"
            "evidence order 1, observed 2026-07-27|1"
        )
        return lines

    def write_task(
        self,
        *,
        records: Optional[Sequence[str]] = None,
        declaration: str = "required",
        drift: bool = False,
        section: bool = True,
        status: str = "in-progress",
    ) -> Path:
        block = sorted(self.base_records(drift=drift) if records is None else records)
        trace_section = (
            "\n## Upstream trace\n\n```trace\n" + "\n".join(block) + "\n```\n"
            if section
            else ""
        )
        target = self.root / "tasks" / status / f"{UNIT}.md"
        for candidate in ("open", "in-progress", "in-review", "done"):
            stale = self.root / "tasks" / candidate / f"{UNIT}.md"
            if stale.exists() and stale != target:
                stale.unlink()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            f"""# TASK-99-001: Integrated fixture

Phase: PHASE-99
Plan ref: BUILD-99-001
Work root: n/a
Deliverable: n/a
Spec: SPEC-99-001.md
Evidence gate: n/a
Source guidance: spec
Upstream trace: {declaration}

## Goal

Fixture.

## Acceptance

- [ ] {TASK_ITEM}
{trace_section}""",
            encoding="utf-8",
        )
        self.task = target
        return target

    def write_report(self, *, status: str = "complete", ready: str = "yes") -> Path:
        return self.scaffold.write(
            "reports/REPORT-99-001.md",
            f"""Status: {status}

## Identity

- Work root: n/a

## Completion evidence

The integrated fixture landed.

## Remaining risks

- none

## Ready for review

{ready}
""",
        )

    def bound_trace(self) -> at.Trace:
        binding = trace_binding.bind(self.root, self.task)
        self.assertIsNone(binding.refusal, binding.refusal)
        self.assertIsNotNone(binding.trace)
        return binding.trace

    def determinations(
        self,
        *,
        overrides: Optional[Dict[Tuple[str, str], Tuple[str, str]]] = None,
        identity: Optional[str] = None,
        omit: Sequence[Tuple[str, str]] = (),
    ) -> str:
        overrides = overrides or {}
        trace = self.bound_trace()
        lines = [f"Trace-identity: {identity or trace.trace_identity()}"]
        for criterion in trace.criteria:
            for name in ("D1", "D2"):
                key = (name, criterion.ordinal)
                if key in omit:
                    continue
                verdict, reason = overrides.get(key, ("pass", "-"))
                lines.append(f"{name} {criterion.ordinal}: {verdict} reason:{reason}")
        for key, (verdict, reason) in overrides.items():
            if key[1] == "task":
                lines.append(f"{key[0]} task: {verdict} reason:{reason}")
        return "\n".join(lines)

    def write_review(
        self,
        *,
        verdict: str = "approve",
        reviewer: str = "independent reviewer",
        alignment: str = "aligned",
        alignment_evidence: str = "REQUEST-001",
        contract_quality: str = CLEAN_CONTRACT_QUALITY,
        determinations: Optional[str] = None,
        findings: str = "- none",
        order: str = "canonical",
    ) -> Path:
        """Write one review artifact.

        ``order`` selects where `## Contract quality` sits in the finished
        file: ``"canonical"`` before `## Implementation evidence`, or
        ``"misplaced-final"`` after it. It is a placement knob, not a
        chronology knob — both variants are written in a single pass.
        """
        body = REVIEW_TEMPLATE.format(
            reviewer=reviewer,
            verdict=verdict,
            alignment=alignment,
            alignment_evidence=alignment_evidence,
            contract_quality=contract_quality,
            determinations=(
                self.determinations() if determinations is None else determinations
            ),
            findings=findings,
        )
        if order == "misplaced-final":
            # The same audit text, placed *after* `## Implementation evidence`
            # in the finished artifact. This varies one thing only — where the
            # heading sits in the file. It says nothing about when the section
            # was read, judged, or written, and is not a backfill simulation.
            body = body.replace(contract_quality, "", 1)
            body = body.replace("## Findings", contract_quality + "## Findings", 1)
        return self.scaffold.write(f"reviews/{REVIEW_ID}.md", body)

    # -- surfaces ---------------------------------------------------------

    def run_cli(self, *argv: str) -> Tuple[int, List[Dict[str, Any]], str]:
        """Invoke the real CLI dispatcher and return (exit, records, stderr)."""
        parser = build_parser()
        out, err = io.StringIO(), io.StringIO()
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

    def run_mcp(self, tool: str, **kwargs: Any) -> Tuple[int, List[Dict[str, Any]], List[str]]:
        """Invoke the MCP tool of the same name through the real registry."""
        result = mcp.call_tool(tool, kwargs)
        structured = result["structuredContent"]
        return (
            structured["exit_code"],
            structured["records"],
            structured["stderr_lines"],
        )

    def run_intake(self, review: Path, **overrides: Any) -> Tuple[int, Optional[Dict[str, Any]], str]:
        argv = [
            "review-intake",
            str(self.root),
            "--task",
            str(self.task),
            "--review",
            str(review),
            "--date",
            DATE,
        ]
        for key, value in overrides.items():
            flag = "--" + key.replace("_", "-")
            if value is True:
                argv.append(flag)
            elif value is not None and value is not False:
                argv.extend([flag, str(value)])
        code, records, err = self.run_cli(*argv)
        return code, (records[0] if records else None), err

    # -- assertions -------------------------------------------------------

    def assertBlocks(self, record: Optional[Dict[str, Any]], rule: str) -> None:
        self.assertIsNotNone(record)
        rules = {blocker["rule"] for blocker in record["blockers"]}
        self.assertIn(rule, rules, record["blockers"])

    def trace_record(self, *extra: str) -> Dict[str, Any]:
        code, records, err = self.run_cli(
            "acceptance-trace", str(self.root), "--task", str(self.task), *extra
        )
        self.assertEqual(code, 0, err)
        return records[0]


# ==========================================================================
# 1. Trace construction: complete, multi-source, duplicate, conflict.
# ==========================================================================
class TraceConstructionTests(IntegratedSlice):
    extra_sources = (SOURCE_ROW_CONVENTIONS,)
    extra_requests = ("Also record the measured bodies.",)

    def multi_source_records(self) -> List[str]:
        """Both authoritative sources and both operator excerpts, across four
        criteria. C01 is deliberately reached by two *different* precedence
        classes (behavior and intent), which the contract permits; two
        same-class identities on one criterion is the conflict case below."""
        return [
            f"C01|{at.digest12(SPEC_ITEMS[0])}|requirement|{REQUIREMENTS}|{CONTEXT}|1",
            f"C01|{at.digest12(SPEC_ITEMS[0])}|operator-request|"
            f"REQ-002 {self.identities[1]}|evidence order 2, observed 2026-07-28|1",
            f"C02|{at.digest12(SPEC_ITEMS[1])}|standard|{CONVENTIONS}|"
            f"{CONVENTIONS_CONTEXT}|1",
            f"C03|{at.digest12(SPEC_ITEMS[2])}|requirement|{REQUIREMENTS}|{CONTEXT}|1",
            f"C04|{at.digest12(TASK_ITEM)}|operator-request|REQ-001 {self.identity}|"
            "evidence order 1, observed 2026-07-27|1",
        ]

    def test_a_complete_trace_reports_complete_coverage_on_every_axis(self):
        self.write_task(records=self.multi_source_records())
        record = self.trace_record()
        self.assertTrue(record["ok"], record)
        trace = record["trace"]
        self.assertEqual(trace["criterion_coverage"], "complete")
        self.assertEqual(trace["source_coverage"], "complete")
        self.assertEqual(trace["request_coverage"], "complete")
        self.assertEqual(trace["uncovered_sources"], [])
        self.assertEqual(trace["uncovered_requests"], [])
        self.assertEqual(trace["closure_findings"], [])

    def test_multi_source_coverage_reaches_both_sources_and_both_requests(self):
        self.write_task(records=self.multi_source_records())
        trace = self.trace_record()["trace"]
        # Two authoritative sources, two operator excerpts, five edges.
        self.assertEqual(trace["bounds"]["s"], 2)
        self.assertEqual(trace["bounds"]["r"], 2)
        self.assertEqual(trace["bounds"]["e"], 5)
        self.assertEqual(trace["bounds"]["n"], 4)

    def test_a_criterion_may_carry_two_precedence_classes_without_conflict(self):
        """C01 is reached by a behavior-class and an intent-class source."""
        self.write_task(records=self.multi_source_records())
        record = self.trace_record()
        self.assertTrue(record["ok"], record)
        self.assertEqual(record["trace"]["closure_findings"], [])

    def test_a_byte_identical_duplicate_record_collapses_silently(self):
        records = self.multi_source_records()
        duplicated = records + [records[0]]
        self.write_task(records=duplicated)
        with_duplicate = self.trace_record()["trace"]
        self.write_task(records=records)
        without_duplicate = self.trace_record()["trace"]
        self.assertEqual(
            with_duplicate["trace_identity"], without_duplicate["trace_identity"]
        )
        self.assertEqual(with_duplicate["bounds"]["e"], without_duplicate["bounds"]["e"])

    def test_a_repeated_occurrence_is_not_a_duplicate_and_stays_observable(self):
        records = self.multi_source_records()
        repeated = [
            line.rsplit("|", 1)[0] + "|2" if line.startswith("C03|") else line
            for line in records
        ]
        self.write_task(records=repeated)
        repeated_identity = self.trace_record()["trace"]["trace_identity"]
        self.write_task(records=records)
        self.assertNotEqual(
            repeated_identity, self.trace_record()["trace"]["trace_identity"]
        )

    def test_an_unresolved_same_class_conflict_fails_closed_at_closure(self):
        """Two behavior-class sources on one criterion need a disposition."""
        records = self.multi_source_records()
        conflicted = records + [
            f"C03|{at.digest12(SPEC_ITEMS[2])}|standard|{CONVENTIONS}|"
            f"{CONVENTIONS_CONTEXT}|1"
        ]
        self.write_task(records=conflicted)
        record = self.trace_record()
        codes = {finding["code"] for finding in record["trace"]["closure_findings"]}
        self.assertIn("unresolved-source-conflict", codes, record["trace"])

    def test_a_recorded_disposition_resolves_the_same_conflict(self):
        records = self.multi_source_records()
        resolved = records + [
            f"C03|{at.digest12(SPEC_ITEMS[2])}|standard|{CONVENTIONS}|"
            f"{CONVENTIONS_CONTEXT}|1",
            "X|C03|precedence|the requirement governs where the convention is silent",
        ]
        self.write_task(records=resolved)
        record = self.trace_record()
        codes = {finding["code"] for finding in record["trace"]["closure_findings"]}
        self.assertNotIn("unresolved-source-conflict", codes, record["trace"])

    def test_the_disposition_is_inside_the_hashed_body(self):
        records = self.multi_source_records() + [
            f"C03|{at.digest12(SPEC_ITEMS[2])}|standard|{CONVENTIONS}|"
            f"{CONVENTIONS_CONTEXT}|1"
        ]
        self.write_task(records=records)
        unresolved = self.trace_record()["trace"]["trace_identity"]
        self.write_task(
            records=records
            + ["X|C03|precedence|the requirement governs where the convention is silent"]
        )
        self.assertNotEqual(unresolved, self.trace_record()["trace"]["trace_identity"])


# ==========================================================================
# 2. Every supported omission class, and the classes that are not supported.
# ==========================================================================
class OmissionClassTests(IntegratedSlice):
    extra_sources = (SOURCE_ROW_CONVENTIONS,)
    extra_requests = ("Also record the measured bodies.",)

    def covered_records(self) -> List[str]:
        return [
            f"C01|{at.digest12(SPEC_ITEMS[0])}|requirement|{REQUIREMENTS}|{CONTEXT}|1",
            f"C02|{at.digest12(SPEC_ITEMS[1])}|standard|{CONVENTIONS}|"
            f"{CONVENTIONS_CONTEXT}|1",
            f"C03|{at.digest12(SPEC_ITEMS[2])}|operator-request|"
            f"REQ-002 {self.identities[1]}|evidence order 2, observed 2026-07-27|1",
            f"C04|{at.digest12(TASK_ITEM)}|operator-request|REQ-001 {self.identity}|"
            "evidence order 1, observed 2026-07-27|1",
        ]

    def closure_codes(self) -> set:
        return {
            finding["code"]
            for finding in self.trace_record()["trace"]["closure_findings"]
        }

    # -- the three justified omission classes (exemptions) ----------------

    def test_each_exemption_reason_is_a_supported_omission(self):
        for reason in at.EXEMPTION_REASONS:
            with self.subTest(reason=reason):
                records = self.covered_records()
                records[0] = (
                    f"C01|{at.digest12(SPEC_ITEMS[0])}|none:{reason}|-|-|1"
                )
                self.write_task(records=records)
                record = self.trace_record()
                self.assertTrue(record["ok"], record)
                self.assertEqual(record["trace"]["exemptions"], 1)
                self.assertEqual(record["trace"]["criterion_coverage"], "complete")

    def test_an_exemption_reason_outside_the_closed_set_fails_closed(self):
        records = self.covered_records()
        records[0] = f"C01|{at.digest12(SPEC_ITEMS[0])}|none:convenient|-|-|1"
        self.write_task(records=records)
        code, records_out, err = self.run_cli(
            "acceptance-trace", str(self.root), "--task", str(self.task)
        )
        self.assertEqual(code, 1)
        self.assertIn("unknown-source-type", err)

    def test_a_typed_edge_beside_an_exemption_is_an_exemption_conflict(self):
        records = self.covered_records() + [
            f"C01|{at.digest12(SPEC_ITEMS[0])}|none:template-fixed|-|-|1"
        ]
        self.write_task(records=records)
        code, _, err = self.run_cli(
            "acceptance-trace", str(self.root), "--task", str(self.task)
        )
        self.assertEqual(code, 1)
        self.assertIn("exemption-conflict", err)

    # -- the two waiver classes -------------------------------------------

    def test_each_waiver_class_omits_a_source_from_coverage(self):
        for waiver_class in at.WAIVER_CLASSES:
            with self.subTest(waiver_class=waiver_class):
                records = self.covered_records()
                # Drop the standard edge; waive the source it would have covered.
                records[1] = (
                    f"C02|{at.digest12(SPEC_ITEMS[1])}|requirement|{REQUIREMENTS}|"
                    f"{CONTEXT}|1"
                )
                records.append(
                    f"W|{CONVENTIONS}|{waiver_class}|"
                    "grants documentation access for this assignment; "
                    "states no product behavior"
                )
                self.write_task(records=records)
                record = self.trace_record()
                self.assertTrue(record["ok"], record)
                self.assertEqual(record["trace"]["source_coverage"], "complete")
                self.assertNotIn(
                    "source-uncovered",
                    {f["code"] for f in record["trace"]["closure_findings"]},
                )

    def test_a_waiver_class_outside_the_closed_set_fails_closed(self):
        records = self.covered_records()
        records.append(f"W|{CONVENTIONS}|convenient|because it is easier")
        self.write_task(records=records)
        code, _, err = self.run_cli(
            "acceptance-trace", str(self.root), "--task", str(self.task)
        )
        self.assertEqual(code, 1)
        self.assertIn("unknown-waiver-class", err)

    # -- unjustified omissions --------------------------------------------

    def test_an_omitted_authoritative_source_is_source_uncovered(self):
        records = self.covered_records()
        records[1] = (
            f"C02|{at.digest12(SPEC_ITEMS[1])}|requirement|{REQUIREMENTS}|{CONTEXT}|1"
        )
        self.write_task(records=records)
        record = self.trace_record()
        self.assertEqual(record["trace"]["source_coverage"], "incomplete")
        self.assertEqual(record["trace"]["uncovered_sources"], [CONVENTIONS])
        self.assertIn("source-uncovered", self.closure_codes())

    def test_an_omitted_operator_excerpt_is_request_uncovered(self):
        records = self.covered_records()
        records[2] = (
            f"C03|{at.digest12(SPEC_ITEMS[2])}|requirement|{REQUIREMENTS}|{CONTEXT}|1"
        )
        self.write_task(records=records)
        record = self.trace_record()
        self.assertEqual(record["trace"]["request_coverage"], "incomplete")
        self.assertEqual(len(record["trace"]["uncovered_requests"]), 1)
        self.assertIn(
            self.identities[1], record["trace"]["uncovered_requests"][0]
        )
        self.assertIn("request-uncovered", self.closure_codes())

    def test_an_unclaimed_acceptance_criterion_blocks_at_readiness(self):
        records = self.covered_records()[:-1]
        self.write_task(records=records)
        code, _, err = self.run_cli(
            "acceptance-trace", str(self.root), "--task", str(self.task)
        )
        self.assertEqual(code, 1)
        self.assertIn("trace-incomplete", err)

    def test_a_missing_record_block_under_a_required_declaration_blocks(self):
        self.write_task(section=False)
        code, _, err = self.run_cli(
            "acceptance-trace", str(self.root), "--task", str(self.task)
        )
        self.assertEqual(code, 1)
        self.assertIn("trace-missing", err)

    def test_a_task_declaring_n_a_may_not_also_carry_a_record_block(self):
        self.write_task(declaration="n/a")
        code, _, err = self.run_cli(
            "acceptance-trace", str(self.root), "--task", str(self.task)
        )
        self.assertEqual(code, 1)
        self.assertIn("trace-unparseable", err)

    def test_an_undeclared_task_reports_its_declaration_and_invents_nothing(self):
        self.write_task(declaration="n/a", section=False)
        code, records, err = self.run_cli(
            "acceptance-trace", str(self.root), "--task", str(self.task)
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(records[0]["declaration"], "n/a")
        self.assertNotIn("trace", records[0])

    def test_a_drifted_criterion_is_detected_by_its_own_digest(self):
        self.write_task(drift=True)
        code, _, err = self.run_cli(
            "acceptance-trace", str(self.root), "--task", str(self.task)
        )
        self.assertEqual(code, 1)
        self.assertIn("criterion-digest-mismatch", err)


# ==========================================================================
# 3. Contract-quality placement in the final review artifact.
# ==========================================================================
class ContractQualityPlacementTests(IntegratedSlice):
    """Canonical section placement, and findings kept on distinct channels.

    Scope limitation, stated here so it travels with the evidence rather
    than living only in a report: every check in this class reads the
    *finished review artifact*. It observes where the `## Contract quality`
    heading sits relative to `## Implementation evidence`, and that contract
    findings are carried separately from implementation findings.

    Placement is a quality heuristic about artifact shape. It does not
    establish — and nothing here asserts — what the reviewer read, when they
    read it, when they formed a judgment, or when any section was written.
    The review is single-pass by contract; no temporal ordering, access
    isolation, process isolation, or anti-backfill property is proven or
    implied by these tests.
    """

    def setUp(self) -> None:
        super().setUp()
        self.write_task(status="in-review")
        self.write_report()

    def heading_offsets(self, review: Path) -> Tuple[int, int]:
        """Offsets of the two headings in the artifact as written to disk.

        The observable is a position in a file. Reading it back off disk
        keeps that explicit: there is no event stream here to order.
        """
        body = review.read_text(encoding="utf-8")
        return body.index(CONTRACT_HEADING), body.index(IMPLEMENTATION_HEADING)

    def test_the_canonical_artifact_places_contract_quality_before_implementation_evidence(self):
        review = self.write_review()
        contract_at, implementation_at = self.heading_offsets(review)
        # Shape only: the heading precedes the other heading in the file.
        self.assertLess(contract_at, implementation_at)
        code, record, err = self.run_intake(review)
        self.assertEqual(code, 0, err)
        self.assertTrue(record["approvable"], record)
        self.assertEqual(record["contract_quality"]["outcome"], "adequate")

    def test_contract_quality_placed_after_implementation_evidence_is_an_order_violation(self):
        review = self.write_review(order="misplaced-final")
        contract_at, implementation_at = self.heading_offsets(review)
        # The only difference from the canonical artifact is this offset.
        self.assertGreater(contract_at, implementation_at)
        code, record, err = self.run_intake(review)
        self.assertEqual(code, 1, err)
        self.assertFalse(record["approvable"], record)
        # A misplaced final section is rejected as a procedural
        # artifact-order violation. The rule reads heading positions in the
        # submitted text; it makes no chronology finding about the reviewer.
        self.assertBlocks(record, "review-order-violation")

    def test_the_two_artifacts_differ_only_in_where_the_section_sits(self):
        """Guards the limitation: same bytes, one moved section, no more."""
        canonical = self.write_review().read_text(encoding="utf-8")
        misplaced = self.write_review(order="misplaced-final").read_text(
            encoding="utf-8"
        )
        self.assertNotEqual(canonical, misplaced)
        # Same sections, same bytes in each, different order. Nothing about
        # the misplaced artifact is degraded content — only its shape.
        self.assertEqual(
            sorted(canonical.split("\n## ")), sorted(misplaced.split("\n## "))
        )

    def test_a_missing_contract_audit_blocks_and_is_never_inferred(self):
        review = self.write_review(contract_quality="")
        code, record, err = self.run_intake(review)
        self.assertEqual(code, 1, err)
        self.assertFalse(record["approvable"], record)
        self.assertIsNone(record["contract_quality"]["outcome"])

    def test_a_deficient_contract_and_a_sound_implementation_are_both_recorded(self):
        review = self.write_review(
            verdict="request-changes",
            contract_quality=(
                "## Contract quality\n\nOutcome: needs changes\n\n"
                "- C1. [major] Upstream alignment — the contract omits an "
                "applicable requirement named by the trace\n\n"
            ),
        )
        code, record, err = self.run_intake(review)
        self.assertEqual(code, 0, err)
        # Both judgments are carried on their own channel: the contract is
        # deficient, and the implementation of it is not what failed.
        self.assertEqual(record["contract_quality"]["outcome"], "needs changes")
        self.assertEqual(len(record["contract_quality"]["gaps"]), 1)
        self.assertEqual(
            record["contract_quality"]["gaps"][0]["check"], "upstream-alignment"
        )
        self.assertTrue(record["closure"]["ok"], record["closure"])
        self.assertEqual(record["closure"]["blockers"], [])
        self.assertEqual(record["verdict"], "request-changes")

    def test_an_adequate_outcome_with_a_blocking_gap_is_incoherent(self):
        review = self.write_review(
            contract_quality=(
                "## Contract quality\n\nOutcome: adequate\n\n"
                "- C1. [blocker] Upstream alignment — the contract contradicts "
                "the source the trace names\n\n"
            ),
        )
        code, record, err = self.run_intake(review)
        self.assertEqual(code, 1, err)
        self.assertFalse(record["approvable"], record)


# ==========================================================================
# 4. Two independent, independently failing closure determinations.
# ==========================================================================
class IndependentDeterminationTests(IntegratedSlice):
    def setUp(self) -> None:
        super().setUp()
        self.write_task(status="in-review")
        self.write_report()

    def test_a_complete_passing_block_clears_closure(self):
        code, record, err = self.run_intake(self.write_review())
        self.assertEqual(code, 0, err)
        self.assertTrue(record["approvable"], record)

    def test_d1_may_pass_while_d2_fails(self):
        review = self.write_review(
            verdict="request-changes",
            determinations=self.determinations(
                overrides={("D2", "C02"): ("fail", "upstream-intent-uncovered")}
            ),
        )
        code, record, err = self.run_intake(review)
        self.assertEqual(code, 1, err)
        self.assertFalse(record["approvable"], record)

    def test_d2_may_pass_while_d1_fails(self):
        review = self.write_review(
            verdict="request-changes",
            determinations=self.determinations(
                overrides={("D1", "C02"): ("fail", "acceptance-item-unmet")}
            ),
        )
        code, record, err = self.run_intake(review)
        self.assertEqual(code, 1, err)
        self.assertFalse(record["approvable"], record)

    def test_a_missing_d2_never_defaults_to_pass(self):
        review = self.write_review(determinations=self.determinations(omit=[("D2", "C02")]))
        code, record, err = self.run_intake(review)
        self.assertEqual(code, 1, err)
        self.assertFalse(record["approvable"], record)

    def test_a_missing_d1_never_defaults_to_pass(self):
        review = self.write_review(determinations=self.determinations(omit=[("D1", "C02")]))
        code, record, err = self.run_intake(review)
        self.assertEqual(code, 1, err)
        self.assertFalse(record["approvable"], record)

    def test_both_determinations_are_recorded_against_the_issued_trace_identity(self):
        stale = "sha256:" + "0" * 64
        review = self.write_review(determinations=self.determinations(identity=stale))
        code, record, err = self.run_intake(review)
        self.assertEqual(code, 1, err)
        self.assertBlocks(record, "trace-identity-mismatch")

    def test_a_reason_code_outside_its_determination_blocks(self):
        review = self.write_review(
            verdict="request-changes",
            determinations=self.determinations(
                # A D1-only reason recorded on D2.
                overrides={("D2", "C02"): ("fail", "acceptance-item-unmet")}
            ),
        )
        code, record, err = self.run_intake(review)
        self.assertEqual(code, 1, err)
        self.assertFalse(record["approvable"], record)


# ==========================================================================
# 5. Request attribution: fail-closed drift and stale context.
# ==========================================================================
class RequestAttributionTests(IntegratedSlice):
    def setUp(self) -> None:
        super().setUp()
        self.write_task(status="in-review")
        self.write_report()

    def review_context(self, *extra: str) -> Tuple[int, List[Dict[str, Any]], str]:
        return self.run_cli(
            "review-context",
            str(self.root),
            "--review-kind",
            "task-closure",
            "--task",
            str(self.task),
            *extra,
        )

    def test_the_bound_request_is_attributable_to_its_own_content_identity(self):
        code, records, err = self.review_context()
        self.assertEqual(code, 0, err)
        trace = records[0]["request_trace"]
        self.assertEqual(trace["state"], "resolved")
        self.assertEqual(
            [record["content_identity"] for record in trace["records"]],
            [self.identity],
        )

    def test_a_prompt_carrying_the_generated_context_passes_preflight(self):
        code, records, err = self.run_cli(
            "write-prompt",
            str(self.root),
            "--prompt-id",
            "PROMPT-99-001",
            "--review-kind",
            "task-closure",
            "--task",
            str(self.task),
            "--content",
            "# Review prompt\n",
        )
        self.assertEqual(code, 0, err)
        prompt = self.root / "prompts" / "PROMPT-99-001.md"
        code, records, err = self.review_context("--prompt", str(prompt))
        self.assertEqual(code, 0, err)
        self.assertTrue(records[0]["preflight"]["ok"], records[0]["preflight"])

    def test_a_prompt_whose_request_context_drifted_fails_closed(self):
        code, _, err = self.run_cli(
            "write-prompt",
            str(self.root),
            "--prompt-id",
            "PROMPT-99-001",
            "--review-kind",
            "task-closure",
            "--task",
            str(self.task),
            "--content",
            "# Review prompt\n",
        )
        self.assertEqual(code, 0, err)
        prompt = self.root / "prompts" / "PROMPT-99-001.md"
        text = prompt.read_text(encoding="utf-8")
        prompt.write_text(
            text.replace("Build the integrated fixture.", "Build something else."),
            encoding="utf-8",
        )
        code, records, err = self.review_context("--prompt", str(prompt))
        preflight = records[0]["preflight"]
        self.assertFalse(preflight["ok"], preflight)
        self.assertEqual(preflight["rule"], "stale-request-context")
        self.assertTrue(preflight["recovery"])

    def test_a_prompt_that_dropped_the_request_section_fails_closed(self):
        code, _, err = self.run_cli(
            "write-prompt",
            str(self.root),
            "--prompt-id",
            "PROMPT-99-001",
            "--review-kind",
            "task-closure",
            "--task",
            str(self.task),
            "--content",
            "# Review prompt\n",
        )
        self.assertEqual(code, 0, err)
        prompt = self.root / "prompts" / "PROMPT-99-001.md"
        prompt.write_text("# Review prompt\n\nNothing else.\n", encoding="utf-8")
        code, records, err = self.review_context("--prompt", str(prompt))
        self.assertFalse(records[0]["preflight"]["ok"])
        self.assertEqual(records[0]["preflight"]["rule"], "stale-request-context")

    def test_the_context_identity_changes_when_the_bound_evidence_changes(self):
        _, records, _ = self.review_context()
        first = records[0]["context_identity"]
        self.capture_correction(1, "Add one more confirmed instruction.")
        _, records, _ = self.review_context()
        self.assertNotEqual(first, records[0]["context_identity"])

    def test_the_same_inputs_produce_the_same_context_identity(self):
        _, first, _ = self.review_context()
        _, second, _ = self.review_context()
        self.assertEqual(
            first[0]["context_identity"], second[0]["context_identity"]
        )


# ==========================================================================
# 6. Coder context: deidentified, and zero bytes from unrelated history.
# ==========================================================================
class CoderContextPrivacyTests(IntegratedSlice):
    extra_sources = (SOURCE_ROW_CONVENTIONS,)

    def base_records(self, *, drift: bool = False) -> List[str]:
        records = super().base_records(drift=drift)
        records.append(
            f"C02|{at.digest12(SPEC_ITEMS[1])}|standard|{CONVENTIONS}|"
            f"{CONVENTIONS_CONTEXT}|1"
        )
        return records

    def assignment_prompt(self) -> str:
        code, _, err = self.run_cli(
            "write-prompt",
            str(self.root),
            "--prompt-id",
            "PROMPT-99-001",
            "--task",
            str(self.task),
            "--content",
            "# Assignment prompt\n",
        )
        self.assertEqual(code, 0, err)
        return (self.root / "prompts" / "PROMPT-99-001.md").read_text(encoding="utf-8")

    def coder_body(self) -> str:
        record = self.trace_record("--projection", "coder")
        return record["projection"]["body"]

    def test_the_coder_projection_carries_no_governance_identity(self):
        body = self.coder_body()
        for token in FORBIDDEN_IN_CODER_CONTEXT:
            with self.subTest(token=token):
                self.assertNotIn(token, body)

    def test_the_coder_projection_carries_no_deidentifier_grammar_identifier(self):
        self.assertEqual(deidentify.list_identifiers(self.coder_body()), [])

    def test_the_coder_projection_is_still_complete(self):
        """Deidentified does not mean lossy: every criterion is addressed."""
        trace = self.bound_trace()
        body = self.coder_body()
        for criterion in trace.criteria:
            self.assertIn(criterion.ordinal, body)
            self.assertIn(criterion.digest, body)

    def test_unrelated_history_contributes_zero_bytes_to_the_coder_projection(self):
        body = self.coder_body()
        for token in UNRELATED_TOKENS:
            with self.subTest(token=token):
                self.assertNotIn(token, body)

    def test_unrelated_history_contributes_zero_bytes_to_the_assignment_prompt(self):
        section = self.assignment_prompt().split("```trace-projection\n")[1].split(
            "```"
        )[0]
        for token in UNRELATED_TOKENS:
            with self.subTest(token=token):
                self.assertNotIn(token, section)
        for token in FORBIDDEN_IN_CODER_CONTEXT:
            with self.subTest(token=token):
                self.assertNotIn(token, section)

    def test_the_coder_body_is_exactly_the_projection_the_prompt_carries(self):
        self.assertIn(self.coder_body(), self.assignment_prompt())

    def test_the_coder_bound_is_an_exact_equality_not_a_ceiling(self):
        bounds = self.trace_record()["trace"]["bounds"]
        self.assertEqual(bounds["coder_bytes"], bounds["b_coder"])

    def test_a_structurally_invalid_trace_never_reaches_a_coder(self):
        self.write_task(drift=True)
        code, _, err = self.run_cli(
            "write-prompt",
            str(self.root),
            "--prompt-id",
            "PROMPT-99-001",
            "--task",
            str(self.task),
            "--content",
            "# Assignment prompt\n",
        )
        self.assertEqual(code, 1)
        self.assertIn("criterion-digest-mismatch", err)
        self.assertFalse((self.root / "prompts" / "PROMPT-99-001.md").exists())


# ==========================================================================
# 7. Reviewer context: authoritative, bounded, and measured.
# ==========================================================================
class ReviewerContextBoundTests(IntegratedSlice):
    extra_sources = (SOURCE_ROW_CONVENTIONS,)

    def base_records(self, *, drift: bool = False) -> List[str]:
        records = super().base_records(drift=drift)
        records.append(
            f"C02|{at.digest12(SPEC_ITEMS[1])}|standard|{CONVENTIONS}|"
            f"{CONVENTIONS_CONTEXT}|1"
        )
        return records

    def test_the_reviewer_projection_retains_the_full_typed_record_set(self):
        record = self.trace_record("--projection", "reviewer")
        body = record["projection"]["body"]
        for line in sorted(self.base_records()):
            self.assertIn(line, body)

    def test_the_reviewer_body_recovers_the_hashed_trace_identity(self):
        reviewer = self.trace_record("--projection", "reviewer")["projection"]["body"]
        identity = self.trace_record()["trace"]["trace_identity"]
        self.assertIn(identity, reviewer)

    def test_the_routine_reviewer_body_stays_within_its_declared_bound(self):
        bounds = self.trace_record()["trace"]["bounds"]
        self.assertLessEqual(bounds["routine_bytes"], bounds["b_routine"])
        self.assertEqual(
            bounds["routine_bytes"], bounds["coder_bytes"] + bounds["reviewer_bytes"]
        )

    def test_a_shape_that_overruns_its_bound_fails_closed_at_readiness(self):
        wide = "x" * (at.CAP_APPLICABLE_CONTEXT - 1)
        records = [
            f"C{index:02d}|{at.digest12(text)}|requirement|{REQUIREMENTS}|{wide}|1"
            for index, text in enumerate(SPEC_ITEMS, start=1)
        ]
        records.append(
            f"C04|{at.digest12(TASK_ITEM)}|operator-request|REQ-001 {self.identity}|"
            f"{wide}|1"
        )
        records.append(
            f"C02|{at.digest12(SPEC_ITEMS[1])}|standard|{CONVENTIONS}|{wide}|1"
        )
        self.write_task(records=records)
        code, _, err = self.run_cli(
            "acceptance-trace", str(self.root), "--task", str(self.task)
        )
        self.assertEqual(code, 1)
        self.assertIn("bound-exceeded", err)

    def test_the_diagnostic_body_is_on_demand_and_not_a_routine_cost(self):
        routine = self.trace_record()["trace"]["bounds"]["routine_bytes"]
        diagnostic = self.trace_record("--projection", "diagnostic")["projection"]
        self.assertGreater(diagnostic["bytes"], 0)
        # The routine budget is unchanged by asking for the diagnostic body.
        self.assertEqual(
            self.trace_record()["trace"]["bounds"]["routine_bytes"], routine
        )

    def test_review_context_carries_the_reviewer_provenance_and_its_measure(self):
        self.write_task(status="in-review")
        self.write_report()
        code, records, err = self.run_cli(
            "review-context",
            str(self.root),
            "--review-kind",
            "task-closure",
            "--task",
            str(self.task),
        )
        self.assertEqual(code, 0, err)
        projection = records[0]["upstream_trace"]["reviewer_projection"]
        self.assertEqual(
            projection["bytes"], len(projection["body"].encode("utf-8"))
        )
        self.assertIn(REQUIREMENTS, projection["body"])


# ==========================================================================
# 8. Effectiveness evidence: six families, zero versus unavailable.
# ==========================================================================
class EffectivenessEvidenceTests(IntegratedSlice):
    """Six families, and the three states a count of zero must never blur.

    The rendered `U` row is the observable contract: each family carries a
    count *and* a state, so `0 observed` (the boundary ran and found nothing)
    is a different answer from `0 unavailable` (the boundary never ran) and
    from `0 not-applicable` / `0 not-yet-observable`. These tests assert the
    rendered row, because the row is what a reader actually consumes.
    """

    def setUp(self) -> None:
        super().setUp()
        self.write_task(status="in-review")
        self.write_report()
        self.plan = pe.current_plan_id(self.root)

    # -- surfaces ---------------------------------------------------------

    def query(self, projection: str, *extra: str) -> Dict[str, Any]:
        code, records, err = self.run_cli(
            "prompt-evidence", str(self.root), "--projection", projection, *extra
        )
        self.assertEqual(code, 0, err)
        return records[0]

    def rows(self, projection: str, *extra: str) -> List[str]:
        return self.query(projection, *extra)["answer"]["rows"]

    def summary_row(self) -> str:
        code, records, err = self.run_cli(
            "prompt-evidence",
            str(self.root),
            "--summarize",
            "--unit",
            UNIT,
            "--date",
            DATE,
        )
        self.assertEqual(code, 0, err)
        return records[0]["row"]

    def family_state(self, row: str, family: str) -> str:
        for cell in row.split("|"):
            if cell.startswith(family + " "):
                return cell
        raise AssertionError(f"{family} missing from {row!r}")

    def record_event(self, family: str, artifact: str) -> None:
        code, _, err = self.run_cli(
            "prompt-evidence",
            str(self.root),
            "--record-event",
            "--family",
            family,
            "--unit",
            UNIT,
            "--date",
            DATE,
            "--artifact",
            artifact,
        )
        self.assertEqual(code, 0, err)

    # -- the closed family set --------------------------------------------

    def test_the_ledger_carries_exactly_the_six_contracted_families(self):
        self.assertEqual(
            sorted(pe.FAMILIES), sorted(("CLR", "OMR", "RRR", "RRG", "PCC", "PAD"))
        )

    def test_every_summary_row_names_all_six_families_with_a_state(self):
        row = self.summary_row()
        for family in pe.FAMILIES:
            with self.subTest(family=family):
                cell = self.family_state(row, family)
                self.assertTrue(
                    any(f" {state}" in f"{cell} " for state in pe.STATES), cell
                )

    # -- one family per boundary ------------------------------------------

    def mediated_write(self) -> None:
        """One mediated write, so the provenance journal the ledger reads off
        of exists. Availability is derived from retained artifacts, never from
        the ledger itself — which is what keeps a missing emission from being
        rounded into a true zero."""
        code, _, err = self.run_cli(
            "write-prompt",
            str(self.root),
            "--prompt-id",
            "PROMPT-99-001",
            "--task",
            str(self.task),
            "--content",
            "# Assignment prompt\n",
        )
        self.assertEqual(code, 0, err)

    def test_a_clarification_event_is_recorded_at_its_own_boundary(self):
        self.record_event("CLR", "DEC-042")
        self.assertIn(
            "E|TASK-99-001|2026-08-19|CLR|-|-|DEC-042", self.rows("E", "--unit", UNIT)
        )

    def test_a_clarification_reads_observed_once_its_boundary_is_retained(self):
        self.record_event("CLR", "DEC-042")
        self.mediated_write()
        self.assertIn("CLR 1 observed", self.summary_row())

    def test_a_recorded_event_still_reads_unavailable_with_no_retained_boundary(self):
        """The count is never derived from the ledger alone.

        With no provenance journal there is nothing to say the clarification
        boundary ran, so the family reads `unavailable` — not `1 observed`,
        and not a true zero either.
        """
        self.record_event("CLR", "DEC-042")
        row = self.summary_row()
        self.assertIn("CLR 0 unavailable", row)
        self.assertNotIn("CLR 1 observed", row)

    def test_a_post_approval_defect_is_recorded_at_its_own_boundary(self):
        self.record_event("PAD", "BL-031")
        self.assertIn("E|TASK-99-001|2026-08-19|PAD|-|-|BL-031", self.rows("E", "--unit", UNIT))

    def test_an_upstream_alignment_gap_is_addressed_as_an_omitted_requirement(self):
        review = self.write_review(
            verdict="request-changes",
            contract_quality=(
                "## Contract quality\n\nOutcome: needs changes\n\n"
                "- C1. [major] Upstream alignment — the contract omits an "
                "applicable requirement named by the trace\n\n"
            ),
        )
        self.run_intake(review)
        self.assertIn(
            "D|TASK-99-001|CQ C1|upstream-alignment major|REVIEW-99-001",
            self.rows("D", "--unit", UNIT),
        )
        self.assertIn("OMR 1 observed of 4", self.summary_row())

    def test_a_non_approving_verdict_is_recorded_as_a_rejection_reason(self):
        self.run_intake(self.write_review(verdict="request-changes"))
        self.assertIn(
            "E|TASK-99-001|2026-08-19|RRR|pass 1|request-changes|REVIEW-99-001",
            self.rows("E", "--unit", UNIT),
        )

    def test_a_prompt_write_records_its_measured_context_cost(self):
        code, _, err = self.run_cli(
            "write-prompt",
            str(self.root),
            "--prompt-id",
            "PROMPT-99-001",
            "--task",
            str(self.task),
            "--content",
            "# Assignment prompt\n",
        )
        self.assertEqual(code, 0, err)
        pcc = [row for row in self.rows("E", "--unit", UNIT) if "|PCC|" in row]
        self.assertEqual(len(pcc), 1, pcc)
        prompt_bytes = len(
            (self.root / "prompts" / "PROMPT-99-001.md")
            .read_text(encoding="utf-8")
            .encode("utf-8")
        )
        self.assertIn(f"{prompt_bytes} B", pcc[0])
        self.assertIn(f"PCC {prompt_bytes} B observed", self.summary_row())

    def test_a_reopen_is_labeled_at_the_transition_that_reopened_it(self):
        self.write_review(verdict="request-changes")
        code, _, err = self.run_cli("move-task", str(self.task), "in-progress")
        self.assertEqual(code, 0, err)
        rrg = [row for row in self.rows("E") if "|RRG|" in row]
        self.assertEqual(len(rrg), 1, self.rows("E"))
        self.assertIn("in-review>in-progress", rrg[0])

    def test_an_ordinary_transition_labels_no_reopen(self):
        self.write_review()
        self.write_report()
        code, _, err = self.run_cli(
            "write-prompt",
            str(self.root),
            "--prompt-id",
            "PROMPT-99-001",
            "--review-kind",
            "task-closure",
            "--task",
            str(self.task),
            "--content",
            "# Review prompt\n",
        )
        self.assertEqual(code, 0, err)
        code, _, err = self.run_cli("move-task", str(self.task), "done")
        self.assertEqual(code, 0, err)
        self.assertEqual([row for row in self.rows("E") if "|RRG|" in row], [])

    # -- zero versus unavailable ------------------------------------------

    def test_a_boundary_that_ran_and_found_nothing_reads_observed_zero(self):
        """OMR runs over the trace's four criteria; nothing omitted is a zero."""
        self.assertIn("OMR 0 observed of 4", self.summary_row())

    def test_a_boundary_that_never_ran_reads_unavailable_not_zero(self):
        row = self.summary_row()
        self.assertIn("CLR 0 unavailable", row)
        self.assertNotIn("CLR 0 observed", row)

    def test_observed_zero_and_unavailable_are_distinguishable_in_one_row(self):
        row = self.summary_row()
        self.assertIn("OMR 0 observed", row)
        self.assertIn("CLR 0 unavailable", row)

    def test_a_family_that_cannot_apply_reads_not_applicable(self):
        self.assertIn("PAD 0 not-applicable", self.summary_row())

    def test_an_open_window_reads_not_yet_observable_rather_than_zero(self):
        self.run_intake(self.write_review())
        row = self.summary_row()
        self.assertIn("PAD 0 not-yet-observable", row)
        self.assertNotIn("PAD 0 observed", row)

    def test_the_five_states_never_collapse_into_one_another(self):
        self.assertEqual(len(set(pe.STATES)), len(pe.STATES))
        for state in ("observed", "unavailable", "omitted", "not-applicable",
                      "not-yet-observable"):
            with self.subTest(state=state):
                self.assertIn(state, pe.STATES)

    def test_an_unsummarized_unit_reads_unavailable_and_is_never_dropped(self):
        answer = self.query("U", "--unit", UNIT)["answer"]
        self.assertEqual(answer["rows"], [])
        self.assertEqual(answer["unavailable_units"], [UNIT])

    # -- the ledger is never a routine cost, and never a lifecycle gate ----

    def test_the_ledger_reports_a_zero_byte_routine_context_budget(self):
        self.assertEqual(self.query("U")["routine_context_bytes"], 0)
        self.assertEqual(pe.ROUTINE_CONTEXT_BUDGET_BYTES, 0)

    def test_no_answer_exceeds_its_published_bound(self):
        self.record_event("CLR", "DEC-042")
        self.run_intake(self.write_review(verdict="request-changes"))
        for projection in ("U", "D", "E"):
            with self.subTest(projection=projection):
                answer = self.query(projection)["answer"]
                self.assertLessEqual(answer["bytes"], answer["bound_bytes"])

    def test_evidence_emission_never_blocks_the_closure_decision(self):
        review = self.write_review()
        with_evidence, record_a, _ = self.run_intake(review)
        without_evidence, record_b, _ = self.run_intake(review, no_evidence=True)
        self.assertEqual(with_evidence, without_evidence)
        self.assertEqual(record_a["approvable"], record_b["approvable"])


# ==========================================================================
# 9. CLI/MCP parity: identical records, equivalent errors.
# ==========================================================================
class SurfaceParityTests(IntegratedSlice):
    def setUp(self) -> None:
        super().setUp()
        self.write_task(status="in-review")
        self.write_report()

    def parity(self, cli_argv: Sequence[str], tool: str, kwargs: Dict[str, Any]) -> None:
        cli_code, cli_records, cli_err = self.run_cli(*cli_argv)
        mcp_code, mcp_records, mcp_err = self.run_mcp(tool, **kwargs)
        self.assertEqual(cli_code, mcp_code, (cli_err, mcp_err))
        self.assertEqual(cli_records, mcp_records)
        self.assertEqual(
            [line for line in cli_err.splitlines() if line.strip()], mcp_err
        )

    def test_acceptance_trace_is_identical_on_both_surfaces(self):
        for projection in ("coder", "reviewer", "trace", "criteria", "determinations"):
            with self.subTest(projection=projection):
                self.parity(
                    (
                        "acceptance-trace",
                        str(self.root),
                        "--task",
                        str(self.task),
                        "--projection",
                        projection,
                    ),
                    "acceptance_trace",
                    {
                        "project_root": str(self.root),
                        "task": str(self.task),
                        "projection": projection,
                    },
                )

    def test_review_context_is_identical_on_both_surfaces(self):
        self.parity(
            (
                "review-context",
                str(self.root),
                "--review-kind",
                "task-closure",
                "--task",
                str(self.task),
            ),
            "review_context",
            {
                "project_root": str(self.root),
                "review_kind": "task-closure",
                "task": str(self.task),
            },
        )

    def test_prompt_evidence_is_identical_on_both_surfaces(self):
        for projection in ("U", "D", "E"):
            with self.subTest(projection=projection):
                self.parity(
                    ("prompt-evidence", str(self.root), "--projection", projection),
                    "prompt_evidence",
                    {"project_root": str(self.root), "projection": projection},
                )

    def test_review_intake_is_identical_on_both_surfaces(self):
        review = self.write_review()
        # `review-intake` appends to the ledger, so parity is compared on the
        # decision record, not on a second identical write.
        cli_code, cli_record, _ = self.run_intake(review)
        mcp_code, mcp_records, _ = self.run_mcp(
            "review_intake",
            project_root=str(self.root),
            task=str(self.task),
            review=str(review),
            date=DATE,
        )
        self.assertEqual(cli_code, mcp_code)
        self.assertEqual(cli_record["approvable"], mcp_records[0]["approvable"])
        self.assertEqual(cli_record["blockers"], mcp_records[0]["blockers"])
        self.assertEqual(
            cli_record["contract_quality"], mcp_records[0]["contract_quality"]
        )

    def test_a_structural_refusal_is_equivalent_on_both_surfaces(self):
        self.write_task(drift=True, status="in-review")
        self.parity(
            ("acceptance-trace", str(self.root), "--task", str(self.task)),
            "acceptance_trace",
            {"project_root": str(self.root), "task": str(self.task)},
        )

    def test_a_usage_error_is_equivalent_on_both_surfaces(self):
        missing = self.root / "tasks" / "in-review" / "TASK-99-404.md"
        cli_code, _, cli_err = self.run_cli(
            "review-context",
            str(self.root),
            "--review-kind",
            "task-closure",
            "--task",
            str(missing),
        )
        mcp_code, _, mcp_err = self.run_mcp(
            "review_context",
            project_root=str(self.root),
            review_kind="task-closure",
            task=str(missing),
        )
        self.assertNotEqual(cli_code, 0)
        self.assertEqual(cli_code, mcp_code)
        self.assertEqual(
            [line for line in cli_err.splitlines() if line.strip()], mcp_err
        )

    def test_repeated_reads_are_deterministic_on_both_surfaces(self):
        argv = ("acceptance-trace", str(self.root), "--task", str(self.task))
        kwargs = {"project_root": str(self.root), "task": str(self.task)}
        first_cli = self.run_cli(*argv)[1]
        second_cli = self.run_cli(*argv)[1]
        first_mcp = self.run_mcp("acceptance_trace", **kwargs)[1]
        self.assertEqual(first_cli, second_cli)
        self.assertEqual(first_cli, first_mcp)

    def test_every_new_surface_is_registered_as_an_mcp_tool(self):
        names = {tool["name"] for tool in mcp.list_tools()}
        for tool in (
            "acceptance_trace",
            "review_context",
            "review_intake",
            "prompt_evidence",
            "adversarial_review_context",
        ):
            with self.subTest(tool=tool):
                self.assertIn(tool, names)


# ==========================================================================
# 10. Unattended automation: unchanged except at true governed blockers.
# ==========================================================================
class UnattendedAutomationTests(IntegratedSlice):
    def routine_surfaces(self) -> List[Tuple[str, Tuple[str, ...]]]:
        return [
            ("next-action", ("next-action", str(self.root))),
            ("task-bundle", ("task-bundle", str(self.task))),
            (
                "handoff-packet",
                ("handoff-packet", str(self.task), "--role", "coder"),
            ),
        ]

    def test_a_valid_trace_leaves_every_routine_surface_passing(self):
        for name, argv in self.routine_surfaces():
            with self.subTest(surface=name):
                code, records, err = self.run_cli(*argv)
                self.assertEqual(code, 0, err)
                self.assertTrue(records)

    def test_readiness_passes_with_a_valid_trace(self):
        code, records, err = self.run_cli(
            "validate-task-readiness", str(self.task)
        )
        self.assertEqual(code, 0, err)
        self.assertTrue(records[0]["ready"], records[0])

    def test_a_drifted_trace_is_a_true_governed_blocker_at_readiness(self):
        self.write_task(drift=True)
        code, records, err = self.run_cli(
            "validate-task-readiness", str(self.task)
        )
        self.assertNotEqual(code, 0)
        blob = json.dumps(records) + err
        self.assertIn("criterion-digest-mismatch", blob)

    def test_an_undeclared_task_still_reads_ready(self):
        """The contract adds no automation cost to a task that never opted in."""
        self.write_task(declaration="n/a", section=False)
        code, records, err = self.run_cli(
            "validate-task-readiness", str(self.task)
        )
        self.assertEqual(code, 0, err)
        self.assertTrue(records[0]["ready"], records[0])

    def test_no_routine_surface_reads_or_reports_the_effectiveness_ledger(self):
        pe.emit(
            self.root,
            pe.event(
                plan=pe.current_plan_id(self.root),
                unit=UNIT,
                date=DATE,
                family="PCC",
                ordinal=1,
                size=4812,
            ),
        )
        for name, argv in self.routine_surfaces():
            with self.subTest(surface=name):
                _, records, _ = self.run_cli(*argv)
                blob = json.dumps(records)
                self.assertNotIn("prompt-evidence", blob)
                self.assertNotIn('"PCC"', blob)

    def test_a_blocked_report_is_a_true_governed_blocker(self):
        """The one thing that does stop the unattended path, and it says why."""
        self.write_task(status="in-review")
        self.write_report(status="blocked", ready="no")
        code, _, err = self.run_cli(
            "write-prompt",
            str(self.root),
            "--prompt-id",
            "PROMPT-99-001",
            "--review-kind",
            "task-closure",
            "--task",
            str(self.task),
            "--content",
            "# Review prompt\n",
        )
        self.assertNotEqual(code, 0, err)
        self.assertFalse((self.root / "prompts" / "PROMPT-99-001.md").exists())

    def test_a_complete_report_leaves_the_same_path_open(self):
        """The contrast case: nothing else in the slice stops the run."""
        self.write_task(status="in-review")
        self.write_report()
        code, _, err = self.run_cli(
            "write-prompt",
            str(self.root),
            "--prompt-id",
            "PROMPT-99-001",
            "--review-kind",
            "task-closure",
            "--task",
            str(self.task),
            "--content",
            "# Review prompt\n",
        )
        self.assertEqual(code, 0, err)
        self.assertTrue((self.root / "prompts" / "PROMPT-99-001.md").exists())

    def test_the_automation_policy_record_is_unchanged_by_the_contract(self):
        _, with_trace, _ = self.run_cli("next-action", str(self.root))
        self.write_task(declaration="n/a", section=False)
        _, without_trace, _ = self.run_cli("next-action", str(self.root))
        self.assertEqual(
            with_trace[0]["automation"], without_trace[0]["automation"]
        )

    def test_an_unattended_move_is_not_blocked_by_evidence_emission(self):
        self.write_task(status="in-review")
        self.write_report()
        self.write_review()
        code, _, err = self.run_cli(
            "write-prompt",
            str(self.root),
            "--prompt-id",
            "PROMPT-99-001",
            "--review-kind",
            "task-closure",
            "--task",
            str(self.task),
            "--content",
            "# Review prompt\n",
        )
        self.assertEqual(code, 0, err)
        code, _, err = self.run_cli("move-task", str(self.task), "done")
        self.assertEqual(code, 0, err)
        self.assertTrue((self.root / "tasks" / "done" / f"{UNIT}.md").exists())


# ==========================================================================
# 11. Containment: the new surfaces obey the project boundary.
# ==========================================================================
class ContainmentTests(IntegratedSlice):
    def test_a_task_outside_the_project_is_refused(self):
        outside = self.scaffold.root / "outside-TASK-99-001.md"
        outside.write_text("# TASK-99-001: Outside\n", encoding="utf-8")
        code, _, err = self.run_cli(
            "review-context",
            str(self.root),
            "--review-kind",
            "task-closure",
            "--task",
            str(outside),
        )
        self.assertNotEqual(code, 0, err)

    def test_a_traversal_prompt_path_is_refused(self):
        code, _, err = self.run_cli(
            "review-context",
            str(self.root),
            "--review-kind",
            "task-closure",
            "--task",
            str(self.task),
            "--prompt",
            str(self.root / ".." / "escape.md"),
        )
        self.assertNotEqual(code, 0, err)

    def test_a_relative_project_root_is_refused(self):
        code, _, err = self.run_cli(
            "acceptance-trace", "relative/root", "--task", str(self.task)
        )
        self.assertNotEqual(code, 0, err)

    def test_a_spec_header_escaping_the_specs_directory_is_refused(self):
        text = self.task.read_text(encoding="utf-8")
        self.task.write_text(
            text.replace("Spec: SPEC-99-001.md", "Spec: ../../etc/passwd"),
            encoding="utf-8",
        )
        code, _, err = self.run_cli(
            "acceptance-trace", str(self.root), "--task", str(self.task)
        )
        self.assertEqual(code, 1)
        self.assertIn("trace-unparseable", err)

    #: Every module the integrated slice actually executes.
    SLICE_MODULES: Tuple[str, ...] = (
        "cli.acceptance_trace",
        "cli.contract_review",
        "cli.prompt_evidence",
        "cli.request_trace",
        "cli.trace_binding",
        "cli.deidentify",
        "cli.commands.acceptance_trace",
        "cli.commands.review_context",
        "cli.commands.review_intake",
        "cli.commands.prompt_evidence",
        "cli.commands.adversarial_review_context",
    )

    def test_every_slice_module_resolves_inside_this_repository(self):
        repo_root = Path(__file__).resolve().parent.parent
        for name in self.SLICE_MODULES:
            module = importlib.import_module(name)
            origin = Path(module.__file__).resolve()
            with self.subTest(module=name):
                self.assertTrue(
                    origin.is_relative_to(repo_root),
                    f"{name} resolved outside the repository: {origin}",
                )

    def test_the_slice_imports_no_third_party_runtime_dependency(self):
        """Every import the slice reaches is stdlib or repo-local.

        The repository ships no third-party runtime dependency, so a module in
        this slice that reaches one would be a regression the packaging cannot
        catch. `site-packages` is the observable signal for "not stdlib and not
        ours" on every supported platform.
        """
        repo_root = Path(__file__).resolve().parent.parent
        stdlib_names = set(sys.stdlib_module_names)
        for name in self.SLICE_MODULES:
            module = importlib.import_module(name)
            source = Path(module.__file__).read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:  # relative import: repo-local by definition
                        continue
                    roots = [(node.module or "").split(".")[0]]
                else:
                    continue
                for root in roots:
                    if not root:
                        continue
                    with self.subTest(module=name, imported=root):
                        self.assertTrue(
                            root in stdlib_names
                            or (repo_root / root).exists()
                            or root == "__future__",
                            f"{name} imports third-party module {root!r}",
                        )


# ==========================================================================
# 12. The measured context receipt this proof is accountable to.
# ==========================================================================
class ContextReceiptTests(IntegratedSlice):
    """The receipt is derived from the run, then asserted against itself.

    Nothing here re-baselines onto whatever the serializer now emits: each
    figure is cross-checked against an independently computed measurement of
    the same body, so a drifted serializer produces a mismatch rather than a
    quietly updated number.
    """

    extra_sources = (SOURCE_ROW_CONVENTIONS,)
    extra_requests = ("Also record the measured bodies.",)

    def base_records(self, *, drift: bool = False) -> List[str]:
        records = super().base_records(drift=drift)
        records.append(
            f"C02|{at.digest12(SPEC_ITEMS[1])}|standard|{CONVENTIONS}|"
            f"{CONVENTIONS_CONTEXT}|1"
        )
        records.append(
            f"C03|{at.digest12(SPEC_ITEMS[2])}|operator-request|"
            f"REQ-002 {self.identities[1]}|evidence order 2, observed 2026-07-27|1"
        )
        return records

    def receipt(self) -> Dict[str, Any]:
        record = self.trace_record()
        bounds = record["trace"]["bounds"]
        coder = self.trace_record("--projection", "coder")["projection"]
        reviewer = self.trace_record("--projection", "reviewer")["projection"]
        return {
            "shape": {key: bounds[key] for key in ("n", "e", "s", "r", "w", "x", "o")},
            "coder_bytes": coder["bytes"],
            "reviewer_bytes": reviewer["bytes"],
            "routine_bytes": bounds["routine_bytes"],
            "b_coder": bounds["b_coder"],
            "b_routine": bounds["b_routine"],
            "coder_identity": coder["identity"],
            "reviewer_identity": reviewer["identity"],
            "trace_identity": record["trace"]["trace_identity"],
        }

    def test_each_projected_byte_count_equals_its_own_measured_body(self):
        for projection in ("coder", "reviewer", "trace", "diagnostic"):
            with self.subTest(projection=projection):
                body = self.trace_record("--projection", projection)["projection"]
                self.assertEqual(
                    body["bytes"], len(body["body"].encode("utf-8")), projection
                )

    def test_each_projected_identity_is_the_identity_of_its_own_body(self):
        for projection in ("coder", "reviewer"):
            with self.subTest(projection=projection):
                body = self.trace_record("--projection", projection)["projection"]
                self.assertEqual(body["identity"], at.body_identity(body["body"]))

    def test_the_routine_receipt_is_the_sum_of_its_two_routine_bodies(self):
        receipt = self.receipt()
        self.assertEqual(
            receipt["routine_bytes"],
            receipt["coder_bytes"] + receipt["reviewer_bytes"],
        )

    def test_the_measured_receipt_stays_inside_its_declared_bound(self):
        receipt = self.receipt()
        self.assertEqual(receipt["coder_bytes"], receipt["b_coder"])
        self.assertLessEqual(receipt["routine_bytes"], receipt["b_routine"])

    def test_the_receipt_is_reproducible_across_runs(self):
        self.assertEqual(self.receipt(), self.receipt())

    def test_the_receipt_is_reproducible_across_the_mcp_surface(self):
        _, records, _ = self.run_mcp(
            "acceptance_trace",
            project_root=str(self.root),
            task=str(self.task),
            projection="coder",
        )
        self.assertEqual(
            records[0]["projection"]["bytes"], self.receipt()["coder_bytes"]
        )

    def test_the_token_estimate_is_labeled_and_never_enforced(self):
        bounds = self.trace_record()["trace"]["bounds"]
        self.assertEqual(bounds["est_tokens_coder"], at.est_tokens(bounds["coder_bytes"]))
        self.assertEqual(
            bounds["est_tokens_reviewer"], at.est_tokens(bounds["reviewer_bytes"])
        )

    def test_the_receipt_reproduces_the_transcribed_figures(self):
        """The receipt this proof was accepted against, pinned.

        Set ``CARTOPIAN_PRINT_RECEIPT=1`` to print it. A change here is a
        deliberate context decision that needs its own measurement, not an
        expectation to quietly update: the numbers below are what the
        completion report transcribes.
        """
        receipt = self.receipt()
        if os.environ.get("CARTOPIAN_PRINT_RECEIPT"):
            print(json.dumps(receipt, indent=2, sort_keys=True))
        self.assertEqual(receipt, EXPECTED_RECEIPT)

    def test_the_conformance_anchor_still_reproduces_the_accepted_budget(self):
        """The accepted contract's own published figures, unchanged."""
        code, records, err = self.run_cli(
            "acceptance-trace", str(self.root), "--task", str(self.task), "--anchor"
        )
        self.assertEqual(code, 0, err)
        anchor = records[0]
        self.assertTrue(anchor["conforms"], anchor)
        self.assertEqual(anchor["coder_bytes"], 143)
        self.assertEqual(anchor["reviewer_bytes"], 2438)
        self.assertEqual(anchor["routine_bytes"], 2581)


if __name__ == "__main__":
    unittest.main()
