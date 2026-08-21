"""Review intake: contract audit first, two determinations, bounded evidence.

These tests run the three accepted contracts together through the one command
that joins them, and hold the seams between them: the contract audit is
recorded before implementation framing, D1 and D2 fail independently against
the trace identity the assignment was issued under, and each of those outcomes
becomes exactly one bounded ledger record whose emission never blocks the
lifecycle.
"""
import argparse
import contextlib
import io
import json
import unittest
from pathlib import Path

from cli import acceptance_trace as at
from cli import prompt_evidence as pe
from cli import trace_binding
from cli.commands import acceptance_trace as acceptance_trace_command
from cli.commands import move_task, review_intake
from tests.scaffold import project_scaffold

REQUIREMENTS = "Cartopian REQUIREMENTS.md and STANDARDS.md"
CONTEXT = "active Product Refinement contract as of 2026-08-13"
DATE = "2026-08-19"
UNIT = "TASK-99-001"

SPEC = """# SPEC-99-001: Intake fixture

Status: locked
Profile: general
Source guidance: required

## Source guidance

### Authoritative sources

- Identity: Cartopian REQUIREMENTS.md and STANDARDS.md; Applicable context: active Product Refinement contract as of 2026-08-13; Status: current; Scope: runtime and containment constraints

### Conflict resolution

- Status: resolved; Rule: current requirements and standards govern product boundaries; Decision: implement only the bounded reviewed design

### Unverified claims

- none

## Examples / acceptance

- The fixture passes under the declared contract.
- The fixture reports its measured bodies.
- The fixture fails closed on a drifted criterion.
"""

SPEC_ITEMS = (
    "The fixture passes under the declared contract.",
    "The fixture reports its measured bodies.",
    "The fixture fails closed on a drifted criterion.",
)
TASK_ITEM = "The fixture records its own outcome."


def task_body(block):
    return f"""# TASK-99-001: Intake fixture

Phase: PHASE-99
Plan ref: BUILD-99-001
Work root: n/a
Deliverable: n/a
Spec: SPEC-99-001.md
Evidence gate: n/a
Source guidance: spec
Upstream trace: required

## Goal

Fixture.

## Acceptance

- [ ] The fixture records its own outcome.

## Upstream trace

```trace
{chr(10).join(block)}
```
"""


REVIEW_TEMPLATE = """# REVIEW-99-001

Target: TASK-99-001
Reviewer: {reviewer}
Verdict: {verdict}

## Summary

Reviewed the fixture.

## Request comparison

Aligned.

{contract_quality}
## Implementation evidence

- Commit SHA — abc1234

## Closure determinations

{determinations}

## Findings

{findings}

## Suggested actions

- none
"""


class IntakeFixture(unittest.TestCase):
    def setUp(self):
        self.scaffold = project_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root
        self.scaffold.write("specs/SPEC-99-001.md", SPEC)
        self.scaffold.capture_request(
            request_id="REQUEST-001", unit=f"task:{UNIT}", text="Build the fixture."
        )
        request = json.loads(
            (self.root / "requests" / "REQUEST-001.json").read_text(encoding="utf-8")
        )
        self.identity = request["content_identity"]
        self.task = self.scaffold.write(
            f"tasks/in-review/{UNIT}.md", task_body(self.trace_block())
        )
        self.trace = trace_binding.bind(self.root, self.task).trace
        self.assertIsNotNone(self.trace)

    def trace_block(self):
        lines = [
            f"C{index:02d}|{at.digest12(text)}|requirement|{REQUIREMENTS}|{CONTEXT}|1"
            for index, text in enumerate(SPEC_ITEMS, start=1)
        ]
        lines.append(
            f"C04|{at.digest12(TASK_ITEM)}|operator-request|REQ-001 {self.identity}|"
            "evidence order 1, observed 2026-07-27|1"
        )
        return sorted(lines)

    def determinations(self, *, overrides=None, identity=None):
        overrides = overrides or {}
        lines = [f"Trace-identity: {identity or self.trace.trace_identity()}"]
        for criterion in self.trace.criteria:
            for name in ("D1", "D2"):
                key = (name, criterion.ordinal)
                verdict, reason = overrides.get(key, ("pass", "-"))
                lines.append(f"{name} {criterion.ordinal}: {verdict} reason:{reason}")
        for key, (verdict, reason) in overrides.items():
            if key[1] == "task":
                lines.append(f"{key[0]} task: {verdict} reason:{reason}")
        return "\n".join(lines)

    def write_review(
        self,
        *,
        verdict="approve",
        reviewer="independent reviewer",
        contract_quality="## Contract quality\n\nOutcome: adequate\n\n",
        determinations=None,
        findings="- none",
        order="canonical",
    ):
        body = REVIEW_TEMPLATE.format(
            reviewer=reviewer,
            verdict=verdict,
            contract_quality=contract_quality,
            determinations=(
                self.determinations() if determinations is None else determinations
            ),
            findings=findings,
        )
        if order == "inverted":
            body = body.replace(contract_quality, "")
            body = body.replace(
                "## Findings", contract_quality + "## Findings", 1
            )
        return self.scaffold.write("reviews/REVIEW-99-001.md", body)

    def run_intake(self, review, **overrides):
        args = argparse.Namespace(
            project_root=str(self.root),
            task=str(self.task),
            review=str(review),
            date=DATE,
            pass_ordinal=None,
            no_evidence=False,
        )
        for key, value in overrides.items():
            setattr(args, key, value)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = review_intake.handler(args)
        payload = out.getvalue().strip()
        return code, (json.loads(payload) if payload else None), err.getvalue()

    def rules(self, record):
        return {blocker["rule"] for blocker in record["blockers"]}


class HappyPathTests(IntakeFixture):
    def test_a_complete_review_is_approvable(self):
        code, record, err = self.run_intake(self.write_review())
        self.assertEqual(code, 0, err)
        self.assertTrue(record["approvable"])
        self.assertEqual(record["blockers"], [])
        self.assertEqual(record["contract_quality"]["outcome"], "adequate")
        self.assertTrue(record["closure"]["ok"])
        self.assertTrue(record["reviewer_attributed"])

    def test_the_verdict_becomes_exactly_one_event_record(self):
        _, record, _ = self.run_intake(self.write_review())
        rows = pe.project_events(pe.read_ledger(self.root)).rows
        self.assertEqual(rows, [f"E|{UNIT}|{DATE}|RRR|pass 1|approve|REVIEW-99-001"])

    def test_repeated_ingestion_is_idempotent(self):
        review = self.write_review()
        self.run_intake(review)
        _, record, _ = self.run_intake(review)
        self.assertEqual(
            [e["result"] for e in record["evidence"]["emissions"]], ["idempotent"]
        )
        self.assertEqual(len(pe.read_ledger(self.root).records), 1)


class ContractAuditTests(IntakeFixture):
    def test_a_missing_contract_audit_blocks(self):
        code, record, _ = self.run_intake(self.write_review(contract_quality=""))
        self.assertEqual(code, 1)
        self.assertIn("contract-quality-missing", self.rules(record))

    def test_the_audit_recorded_after_implementation_evidence_blocks(self):
        code, record, _ = self.run_intake(self.write_review(order="inverted"))
        self.assertEqual(code, 1)
        self.assertIn("review-order-violation", self.rules(record))

    def test_an_upstream_alignment_gap_is_recorded_as_omitted_requirement(self):
        section = (
            "## Contract quality\n\nOutcome: needs changes\n\n"
            "- C1. [major] Upstream alignment — the atomic-write standard is "
            "reached by no acceptance item.\n\n"
        )
        _, record, _ = self.run_intake(
            self.write_review(contract_quality=section, verdict="request-changes")
        )
        rows = pe.project_determinations(pe.read_ledger(self.root)).rows
        self.assertIn(
            f"D|{UNIT}|CQ C1|upstream-alignment major|REVIEW-99-001", rows
        )
        ledger = pe.read_ledger(self.root)
        gap = [r for r in ledger.records if r.get("t") == "cq"][0]
        self.assertEqual(gap["f"], "OMR")

    def test_a_gap_on_any_other_check_is_a_rejection_reason(self):
        section = (
            "## Contract quality\n\nOutcome: needs changes\n\n"
            "- C1. [major] Acceptance clarity — item 2 fuses two conditions.\n\n"
        )
        self.run_intake(
            self.write_review(contract_quality=section, verdict="request-changes")
        )
        ledger = pe.read_ledger(self.root)
        gap = [r for r in ledger.records if r.get("t") == "cq"][0]
        self.assertEqual(gap["f"], "RRR")
        self.assertEqual(gap["h"], "acceptance-clarity")

    def test_a_deficient_contract_with_a_sound_implementation_says_both(self):
        section = (
            "## Contract quality\n\nOutcome: needs changes\n\n"
            "- C1. [major] Completeness — the interface stops before its edge "
            "cases.\n\n"
        )
        code, record, _ = self.run_intake(
            self.write_review(contract_quality=section, verdict="approve")
        )
        # The contract-level outcome is recorded and the implementation-level
        # determinations still pass; neither excuses the other.
        self.assertEqual(code, 0)
        self.assertEqual(record["contract_quality"]["outcome"], "needs changes")
        self.assertTrue(record["closure"]["ok"])


class DeterminationTests(IntakeFixture):
    def test_a_missing_determination_blocks_and_never_defaults_to_pass(self):
        lines = self.determinations().splitlines()
        trimmed = "\n".join(line for line in lines if not line.startswith("D2 C02"))
        code, record, _ = self.run_intake(self.write_review(determinations=trimmed))
        self.assertEqual(code, 1)
        self.assertIn("upstream-intent-uncovered", self.rules(record))

    def test_d1_passing_while_d2_fails_blocks_closure(self):
        code, record, _ = self.run_intake(
            self.write_review(
                determinations=self.determinations(
                    overrides={("D2", "C03"): ("fail", "upstream-intent-uncovered")}
                ),
                verdict="request-changes",
            )
        )
        self.assertEqual(code, 1)
        self.assertIn("upstream-intent-uncovered", self.rules(record))

    def test_a_failed_d2_is_recorded_as_an_omitted_requirement_address(self):
        self.run_intake(
            self.write_review(
                determinations=self.determinations(
                    overrides={("D2", "C03"): ("fail", "upstream-intent-uncovered")}
                ),
                verdict="request-changes",
            )
        )
        rows = pe.project_determinations(pe.read_ledger(self.root)).rows
        self.assertIn(
            f"D|{UNIT}|D2 C03|upstream-intent-uncovered|REVIEW-99-001", rows
        )
        record = [
            r for r in pe.read_ledger(self.root).records if r.get("t") == "det"
        ][0]
        self.assertEqual(record["f"], "OMR")

    def test_a_failed_d1_is_recorded_as_a_rejection_reason_address(self):
        self.run_intake(
            self.write_review(
                determinations=self.determinations(
                    overrides={("D1", "C01"): ("fail", "acceptance-item-unmet")}
                ),
                verdict="request-changes",
            )
        )
        record = [
            r for r in pe.read_ledger(self.root).records if r.get("t") == "det"
        ][0]
        self.assertEqual(record["f"], "RRR")
        self.assertEqual(record["n"], "D1")

    def test_a_stale_trace_identity_blocks_at_closure(self):
        code, record, _ = self.run_intake(
            self.write_review(
                determinations=self.determinations(identity="sha256:" + "0" * 64)
            )
        )
        self.assertEqual(code, 1)
        self.assertIn("trace-identity-mismatch", self.rules(record))

    def test_an_unattributed_review_blocks(self):
        code, record, _ = self.run_intake(self.write_review(reviewer="<free text>"))
        self.assertEqual(code, 1)
        self.assertIn("review-unattributed", self.rules(record))

    def test_an_unrecorded_verdict_blocks(self):
        code, record, _ = self.run_intake(
            self.write_review(verdict="approve | request-changes | reject")
        )
        self.assertEqual(code, 1)
        self.assertIn("verdict-unrecorded", self.rules(record))


class FindingsTests(IntakeFixture):
    def test_findings_on_a_non_approving_pass_become_rejection_addresses(self):
        self.run_intake(
            self.write_review(
                verdict="request-changes",
                findings=(
                    "- F1. [blocker] — cli/x.py:10 leaks a path.\n"
                    "- F2. [minor] — a stale comment."
                ),
            )
        )
        rows = pe.project_determinations(pe.read_ledger(self.root)).rows
        self.assertIn(f"D|{UNIT}|F1|blocker|REVIEW-99-001", rows)
        self.assertIn(f"D|{UNIT}|F2|minor|REVIEW-99-001", rows)

    def test_findings_on_an_approving_pass_are_not_rejection_reasons(self):
        self.run_intake(
            self.write_review(
                verdict="approve", findings="- F1. [nit] — a wording nit."
            )
        )
        rows = pe.project_determinations(pe.read_ledger(self.root)).rows
        self.assertEqual(rows, [])


class FailClosedNeverBlockingTests(IntakeFixture):
    def test_evidence_is_still_recorded_when_the_review_blocks(self):
        code, record, _ = self.run_intake(self.write_review(contract_quality=""))
        self.assertEqual(code, 1)
        written = [
            e for e in record["evidence"]["emissions"] if e["result"] == pe.WRITTEN
        ]
        self.assertTrue(written)

    def test_no_evidence_mode_validates_without_appending(self):
        code, record, _ = self.run_intake(self.write_review(), no_evidence=True)
        self.assertEqual(code, 0)
        self.assertEqual(record["evidence"]["emissions"], [])
        self.assertFalse(pe.log_path(self.root).exists())

    def test_a_structurally_invalid_trace_blocks_intake(self):
        drifted = task_body(self.trace_block()).replace(
            "The fixture records its own outcome.",
            "The fixture records its own outcome, eventually.",
        )
        self.scaffold.write(f"tasks/in-review/{UNIT}.md", drifted)
        code, record, _ = self.run_intake(self.write_review())
        self.assertEqual(code, 1)
        self.assertIn("criterion-digest-mismatch", self.rules(record))


class PassOrdinalTests(IntakeFixture):
    def test_successive_passes_carry_successive_ordinals(self):
        self.run_intake(
            self.write_review(verdict="request-changes"), pass_ordinal=1
        )
        self.run_intake(self.write_review(verdict="approve"), pass_ordinal=2)
        rows = pe.project_events(pe.read_ledger(self.root)).rows
        self.assertEqual(
            rows,
            [
                f"E|{UNIT}|{DATE}|RRR|pass 1|request-changes|REVIEW-99-001",
                f"E|{UNIT}|{DATE}|RRR|pass 2|approve|REVIEW-99-001",
            ],
        )

    def test_the_ordinal_is_derived_from_the_ledger_when_not_given(self):
        self.run_intake(self.write_review(verdict="request-changes"))
        self.run_intake(self.write_review(verdict="approve"))
        ledger = pe.read_ledger(self.root)
        ordinals = [r["o"] for r in ledger.records if r.get("f") == "RRR"]
        self.assertEqual(ordinals, [1, 2])


class ApprovalGateTests(IntakeFixture):
    """The `in-review -> done` move is the closure boundary, so it enforces.

    Recording the determinations is not the same as their passing. These hold
    that an approving verdict over a failing, missing, or unattributed
    determination is not executable, and that a task which never declared the
    contract keeps its existing approval path.
    """

    def blocker(self, review):
        return review_intake.approval_blocker(
            self.root, self.task, review.read_text(encoding="utf-8")
        )

    def move_error(self, review):
        return move_task._closure_error(
            self.root, review, review.read_text(encoding="utf-8"), self.task
        )

    def test_a_clean_approving_review_clears_the_gate(self):
        review = self.write_review(verdict="approve")
        self.assertIsNone(self.blocker(review))
        self.assertIsNone(self.move_error(review))

    def test_a_failed_determination_is_not_executable_as_an_approval(self):
        review = self.write_review(
            verdict="approve",
            determinations=self.determinations(
                overrides={("D2", "C03"): ("fail", "upstream-intent-uncovered")}
            ),
        )
        self.assertIn("upstream-intent-uncovered", self.blocker(review))
        self.assertIn("upstream-intent-uncovered", self.move_error(review))

    def test_a_missing_determination_is_not_executable_as_an_approval(self):
        lines = self.determinations().splitlines()
        review = self.write_review(
            verdict="approve",
            determinations="\n".join(
                line for line in lines if not line.startswith("D1 C02")
            ),
        )
        self.assertIn("acceptance-item-unmet", self.blocker(review))

    def test_an_unattributed_approval_is_not_executable(self):
        review = self.write_review(verdict="approve", reviewer="<free text>")
        self.assertIn("review-unattributed", self.blocker(review))

    def test_a_missing_contract_audit_is_not_executable_as_an_approval(self):
        review = self.write_review(verdict="approve", contract_quality="")
        self.assertIn("contract-quality", self.blocker(review))

    def test_an_undeclared_task_keeps_its_existing_approval_path(self):
        review = self.write_review(verdict="approve", contract_quality="")
        self.task.write_text(
            self.task.read_text(encoding="utf-8")
            .replace("Upstream trace: required", "Upstream trace: n/a")
            .split("## Upstream trace")[0],
            encoding="utf-8",
        )
        self.assertIsNone(self.blocker(review))
        self.assertIsNone(self.move_error(review))


class ArtifactContainmentTests(IntakeFixture):
    """Task and review inputs are project artifacts, or they are refused.

    Both new command paths take a caller-supplied path. Reading whatever
    absolute file is named would let an unrelated document — or a link planted
    inside `tasks/` or `reviews/` — supply the identities, determinations, and
    evidence records these commands write against a real unit. Every refusal
    below is fail-closed: the command exits non-zero, names its rule, and
    writes nothing.
    """

    def run_trace(self, task):
        args = argparse.Namespace(
            project_root=str(self.root), task=str(task),
            projection=None, anchor=False,
        )
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = acceptance_trace_command.handler(args)
        payload = out.getvalue().strip()
        return code, (json.loads(payload) if payload else None), err.getvalue()

    def outside_task(self):
        outside = self.scaffold.root / "outside"
        outside.mkdir(exist_ok=True)
        path = outside / f"{UNIT}.md"
        path.write_text(self.task.read_text(encoding="utf-8"), encoding="utf-8")
        return path

    def assert_refused(self, rule, *, task=None, review=None):
        task = task if task is not None else self.task
        review = review if review is not None else self.write_review()
        trace_code, trace_record, trace_err = self.run_trace(task)
        self.assertEqual(trace_code, 1, trace_err)
        self.assertIsNone(trace_record)
        self.assertIn(rule, trace_err)
        intake_code, intake_record, intake_err = self.run_intake(review, task=str(task))
        self.assertEqual(intake_code, 1, intake_err)
        self.assertIsNone(intake_record)
        self.assertIn(rule, intake_err)
        self.assertFalse(pe.log_path(self.root).exists())

    def test_a_traversal_out_of_the_task_directory_is_refused(self):
        # The target exists and is a well-formed task, so only the containment
        # rule stands between it and being read as this project's governance.
        reachable = self.outside_task()
        self.assert_refused(
            "outside-allowlist",
            task=self.root / "tasks/in-review" / ".." / ".." / ".."
            / reachable.parent.name / reachable.name,
        )

    def test_an_external_absolute_path_is_not_a_project_artifact(self):
        self.assert_refused("outside-allowlist", task=self.outside_task())

    def test_a_task_outside_the_status_directories_is_refused(self):
        stray = self.scaffold.write(f"specs/{UNIT}.md", self.task.read_text("utf-8"))
        self.assert_refused("outside-allowlist", task=stray)

    def test_a_symlinked_leaf_is_refused(self):
        link = self.root / "tasks/open" / f"{UNIT}.md"
        link.symlink_to(self.outside_task())
        self.assert_refused("symlink", task=link)

    def test_a_symlinked_parent_component_is_refused(self):
        # `tasks/done` is made a link to a directory that *is* on the
        # allowlist, so only a per-component check can catch it.
        (self.root / "tasks/done").rmdir()
        (self.root / "tasks/done").symlink_to(
            self.root / "tasks/in-review", target_is_directory=True
        )
        self.assert_refused("symlink", task=self.root / "tasks/done" / f"{UNIT}.md")

    def test_a_task_that_is_not_utf_8_is_refused(self):
        path = self.root / "tasks/open" / f"{UNIT}.md"
        path.write_bytes(b"# TASK-99-001\n\nUpstream trace: required\n\xff\xfe\n")
        self.assert_refused("invalid-utf-8", task=path)

    def test_a_review_that_is_not_utf_8_is_refused(self):
        path = self.scaffold.project_root / "reviews/REVIEW-99-001.md"
        path.write_bytes(b"# REVIEW-99-001\n\nVerdict: approve\n\xff\xfe\n")
        code, record, err = self.run_intake(path)
        self.assertEqual(code, 1)
        self.assertIsNone(record)
        self.assertIn("invalid-utf-8", err)

    def test_a_symlinked_review_is_refused(self):
        outside = self.scaffold.root / "outside"
        outside.mkdir(exist_ok=True)
        target = outside / "REVIEW-99-001.md"
        target.write_text(self.write_review().read_text("utf-8"), encoding="utf-8")
        (self.root / "reviews/REVIEW-99-001.md").unlink()
        link = self.root / "reviews/REVIEW-99-001.md"
        link.symlink_to(target)
        code, record, err = self.run_intake(link)
        self.assertEqual(code, 1)
        self.assertIn("symlink", err)

    def test_an_external_absolute_review_is_refused(self):
        outside = self.scaffold.root / "outside"
        outside.mkdir(exist_ok=True)
        target = outside / "REVIEW-99-001.md"
        target.write_text(self.write_review().read_text("utf-8"), encoding="utf-8")
        code, record, err = self.run_intake(target)
        self.assertEqual(code, 1)
        self.assertIn("outside-allowlist", err)

    def test_a_review_of_another_unit_may_not_close_this_one(self):
        other = self.scaffold.write(
            "reviews/REVIEW-99-002.md",
            self.write_review().read_text("utf-8").replace(
                "REVIEW-99-001", "REVIEW-99-002"
            ),
        )
        code, record, err = self.run_intake(other)
        self.assertEqual(code, 1)
        self.assertIsNone(record)
        self.assertIn("identity-mismatch", err)
        self.assertFalse(pe.log_path(self.root).exists())

    def test_the_contained_paths_still_accept_the_real_artifacts(self):
        code, record, err = self.run_trace(self.task)
        self.assertEqual(code, 0, err)
        self.assertTrue(record["ok"])
        code, record, err = self.run_intake(self.write_review())
        self.assertEqual(code, 0, err)
        self.assertTrue(record["approvable"])


class BodyIdentityTests(IntakeFixture):
    """The review body's own identity is bound before anything is judged.

    A canonical filename is caller-supplied metadata; the body is what carries
    the verdict, the determinations, and everything the ledger attributes. A
    file named for this unit whose body names another one has to be refused at
    the door — not later, by a downstream numbering guard that has already
    been handed the wrong evidence and written it against the wrong unit.
    """

    def variant(self, old, new, **overrides):
        body = self.write_review(**overrides).read_text(encoding="utf-8")
        self.assertIn(old, body)
        return self.scaffold.write(
            "reviews/REVIEW-99-001.md", body.replace(old, new, 1)
        )

    def assert_refused(self, rule, review):
        code, record, err = self.run_intake(review)
        self.assertEqual(code, 1, err)
        self.assertIsNone(record)
        self.assertIn(rule, err)
        # Refused before assessment, so nothing was attributed to this unit.
        self.assertFalse(pe.log_path(self.root).exists())
        return err

    def assert_accepted(self, review):
        code, record, err = self.run_intake(review)
        self.assertEqual(code, 0, err)
        self.assertTrue(record["approvable"])
        return record

    def test_a_matching_body_identity_is_accepted(self):
        self.assert_accepted(self.write_review())

    def test_a_body_targeting_another_unit_is_refused(self):
        # The reviewer's probe: only the contained body's `Target:` differs,
        # and the filename it arrives under is this unit's canonical review.
        self.assert_refused(
            "review-target-mismatch",
            self.variant(f"Target: {UNIT}", "Target: TASK-99-002"),
        )

    def test_a_body_with_no_target_is_refused(self):
        self.assert_refused(
            "review-target-missing", self.variant(f"Target: {UNIT}\n", "")
        )

    def test_a_target_naming_no_canonical_unit_is_refused(self):
        # The unfilled template placeholder is the shape this actually takes.
        self.assert_refused(
            "review-target-malformed",
            self.variant(
                f"Target: {UNIT}", "Target: <TASK-NN-NNN or SPEC-NN-NNN>"
            ),
        )

    def test_a_body_with_no_canonical_heading_is_refused(self):
        self.assert_refused(
            "review-identity-missing",
            self.variant("# REVIEW-99-001\n", "# Closure review\n"),
        )

    def test_a_body_declaring_another_review_is_refused(self):
        self.assert_refused(
            "review-identity-mismatch",
            self.variant("# REVIEW-99-001\n", "# REVIEW-99-002\n"),
        )

    def test_the_binding_is_applied_before_the_review_is_assessed(self):
        # The body is mismatched *and* the contract audit is missing. Intake
        # must refuse on identity: assessing a review of another unit at all
        # is the defect, and its findings are not this unit's.
        err = self.assert_refused(
            "review-target-mismatch",
            self.variant(f"Target: {UNIT}", "Target: TASK-99-002",
                         contract_quality=""),
        )
        self.assertNotIn("contract-quality-missing", err)

    def test_no_evidence_is_emitted_for_a_mismatched_body(self):
        review = self.variant(f"Target: {UNIT}", "Target: TASK-99-002")
        self.run_intake(review)
        self.assertEqual(pe.read_ledger(self.root).records, [])

    def test_a_titled_heading_and_titled_target_name_the_same_unit(self):
        review = self.variant("# REVIEW-99-001\n", "# REVIEW-99-001: Intake\n")
        review = self.scaffold.write(
            "reviews/REVIEW-99-001.md",
            review.read_text(encoding="utf-8").replace(
                f"Target: {UNIT}", f"Target: {UNIT} — Intake fixture", 1
            ),
        )
        self.assert_accepted(review)

    def test_the_specification_of_the_same_unit_is_an_accepted_target(self):
        self.assert_accepted(
            self.variant(f"Target: {UNIT}", "Target: SPEC-99-001")
        )

    def test_a_target_written_as_the_artifact_path_names_the_same_unit(self):
        self.assert_accepted(
            self.variant(f"Target: {UNIT}", f"Target: tasks/in-review/{UNIT}.md")
        )

    def test_the_approval_gate_reads_the_same_binding(self):
        review = self.variant(f"Target: {UNIT}", "Target: TASK-99-002")
        text = review.read_text(encoding="utf-8")
        blocker = review_intake.approval_blocker(self.root, self.task, text)
        self.assertIn("review-target-mismatch", blocker)
        self.assertIn(
            "review-target-mismatch",
            move_task._closure_error(self.root, review, text, self.task),
        )


class ContainmentParityTests(IntakeFixture):
    """CLI and MCP refuse the same paths for the same reason.

    The MCP registry derives its tools from this parser and invokes the same
    handler, so parity is a property to hold rather than a second code path —
    these tests are what keeps it from silently drifting.
    """

    def call(self, tool, arguments):
        from mcp_server import server

        return server.call_tool(tool, arguments)

    def test_the_trace_tool_refuses_an_external_task(self):
        outside = self.scaffold.root / "outside"
        outside.mkdir(exist_ok=True)
        stray = outside / f"{UNIT}.md"
        stray.write_text(self.task.read_text("utf-8"), encoding="utf-8")
        result = self.call(
            "acceptance_trace",
            {"project_root": str(self.root), "task": str(stray)},
        )
        self.assertTrue(result["isError"])
        self.assertIn("outside-allowlist", json.dumps(result))

    def test_the_intake_tool_refuses_a_mismatched_review(self):
        other = self.scaffold.write(
            "reviews/REVIEW-99-002.md",
            self.write_review().read_text("utf-8").replace(
                "REVIEW-99-001", "REVIEW-99-002"
            ),
        )
        result = self.call(
            "review_intake",
            {
                "project_root": str(self.root),
                "task": str(self.task),
                "review": str(other),
            },
        )
        self.assertTrue(result["isError"])
        self.assertIn("identity-mismatch", json.dumps(result))

    def intake_tool(self, review, **overrides):
        arguments = {
            "project_root": str(self.root),
            "task": str(self.task),
            "review": str(review),
            "date": DATE,
        }
        arguments.update(overrides)
        return self.call("review_intake", arguments)

    def test_the_intake_tool_refuses_a_mismatched_review_body(self):
        review = BodyIdentityTests.variant(
            self, f"Target: {UNIT}", "Target: TASK-99-002"
        )
        result = self.intake_tool(review)
        self.assertTrue(result["isError"])
        self.assertIn("review-target-mismatch", json.dumps(result))
        self.assertFalse(pe.log_path(self.root).exists())

    def test_the_intake_tool_refuses_a_body_with_no_declared_identity(self):
        review = BodyIdentityTests.variant(
            self, "# REVIEW-99-001\n", "# Closure review\n"
        )
        result = self.intake_tool(review)
        self.assertTrue(result["isError"])
        self.assertIn("review-identity-missing", json.dumps(result))

    def test_cli_and_mcp_agree_on_a_matching_review_body(self):
        review = self.write_review()
        cli_code, cli_record, err = self.run_intake(review, no_evidence=True)
        self.assertEqual(cli_code, 0, err)
        result = self.intake_tool(review, no_evidence=True)
        self.assertFalse(result["isError"], json.dumps(result))
        self.assertEqual(result["structuredContent"]["records"], [cli_record])

    def test_cli_and_mcp_agree_on_a_mismatched_review_body(self):
        review = BodyIdentityTests.variant(
            self, f"Target: {UNIT}", "Target: TASK-99-002"
        )
        cli_code, cli_record, cli_err = self.run_intake(review)
        result = self.intake_tool(review)
        self.assertEqual(cli_code, 1)
        self.assertEqual(result["structuredContent"]["exit_code"], cli_code)
        self.assertIsNone(cli_record)
        self.assertEqual(result["structuredContent"]["records"], [])
        self.assertEqual(
            result["structuredContent"]["stderr_lines"],
            [line for line in cli_err.splitlines() if line],
        )

    def test_cli_and_mcp_agree_on_the_accepted_trace(self):
        cli_code, cli_record, err = ArtifactContainmentTests.run_trace(self, self.task)
        self.assertEqual(cli_code, 0, err)
        result = self.call(
            "acceptance_trace",
            {"project_root": str(self.root), "task": str(self.task)},
        )
        self.assertFalse(result["isError"], json.dumps(result))
        self.assertEqual(result["structuredContent"]["records"], [cli_record])
if __name__ == "__main__":  # pragma: no cover
    unittest.main()
