"""`cartopian review-intake <project-root> --task <path> --review <path>`.

The closure-review intake boundary. It does four things, in this order, and
records what it found:

0. **Identity binding before anything is judged.** The review's filename
   suffix only proves the caller named a canonical review; the body is what
   carries the verdict, the determinations, and everything the evidence
   records attribute. So the body's own ``# REVIEW-NN-NNN`` heading and its
   ``Target:`` are bound to the review path and to the task on the command
   line — through the shared numbering-contract seam — and a missing,
   malformed, or mismatched body identity is refused before the review is
   assessed and before any record is emitted.

1. **Contract-quality audit first.** The review must carry
   ``## Contract quality`` — after ``## Request comparison``, before
   ``## Implementation evidence`` — with an outcome coherent with the gaps
   recorded beneath it. A review that records the contract judgment after the
   implementation evidence has not made the judgment the contract asks for.

2. **Two independent closure determinations.** ``D1`` (did the implementation
   satisfy the task and specification) and ``D2`` (do the task and
   specification adequately satisfy the upstream sources reached through the
   trace) are recorded per material criterion, against the trace identity the
   assignment was issued under. A missing, failed, contradictory, or
   unattributed determination blocks approval; neither determination is ever
   inferred from the other.

3. **Bounded effectiveness evidence.** The verdict, each non-pass
   determination, each contract-quality gap, and each implementation finding
   become one bounded ledger record apiece. Emission is fail-closed and never
   fail-blocking: a rejected or suppressed record marks its family ``omitted``
   and the lifecycle proceeds regardless.

Repeated ingestion is idempotent: a record byte-identical to one the unit
already carries is not appended a second time.
"""
import argparse
import datetime
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from cli import (
    acceptance_trace,
    artifact_paths,
    contract_review,
    numbering_contract,
    prompt_evidence,
    trace_binding,
)
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE, stderr_error, stderr_guard, stderr_usage

TASK_ID_RE = re.compile(r"^TASK-\d{2}-\d{3}$")
REVIEW_ID_RE = re.compile(r"^REVIEW-\d{2}-\d{3}$")
_VERDICT_RE = re.compile(r"^Verdict:\s*(.+?)\s*$", re.M)
_REVIEWER_RE = re.compile(r"^Reviewer:\s*(.+?)\s*$", re.M)
_PLACEHOLDER_RE = re.compile(r"^\s*<.*>\s*$")


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Validate a closure review's contract-quality audit and its two "
        "independent closure determinations, and record the bounded "
        "prompt-effectiveness evidence for that pass."
    )
    parser.add_argument(
        "project_root", help="Absolute path to the Cartopian project root"
    )
    parser.add_argument("--task", required=True, help="Absolute path to the task file")
    parser.add_argument(
        "--review", required=True, help="Absolute path to the review file"
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Project-local event date (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--pass-ordinal",
        type=int,
        default=None,
        help=(
            "Review pass number for this intake. Derived from the ledger when "
            "omitted; give it explicitly when two passes share one date."
        ),
    )
    parser.add_argument(
        "--no-evidence",
        action="store_true",
        help="Validate only; append no prompt-effectiveness records.",
    )


def _header(text: str, pattern: re.Pattern) -> str:
    match = pattern.search(text)
    if not match:
        return ""
    value = match.group(1).strip()
    if _PLACEHOLDER_RE.match(value) or "|" in value:
        return ""
    return value


def _unit_id(task_path: Path) -> Optional[str]:
    stem = "-".join(task_path.stem.split("-")[:3])
    return stem if TASK_ID_RE.match(stem) else None


def _review_id(review_path: Path) -> Optional[str]:
    stem = "-".join(review_path.stem.split("-")[:3])
    return stem if REVIEW_ID_RE.match(stem) else None


def _identity_key(record: Dict[str, Any]) -> tuple:
    """The observation a record stands for, independent of its event ordinal.

    Re-running intake over unchanged bytes must not double-count the pass, and
    the derived ordinal would otherwise advance on every run. Two passes that
    record the same verdict against the same review artifact on the same date
    are therefore indistinguishable here and collapse to one; pass
    ``--pass-ordinal`` to record them separately.
    """
    return tuple(
        (key, value) for key, value in sorted(record.items()) if key != "o"
    )


def _existing(ledger: prompt_evidence.Ledger, record: Dict[str, Any]) -> bool:
    key = _identity_key(record)
    return any(
        _identity_key(existing) == key for existing in ledger.for_unit(record["u"])
    )


def _emit(
    root: Path,
    ledger: prompt_evidence.Ledger,
    record: Dict[str, Any],
    results: List[Dict[str, Any]],
    omitted: set,
) -> None:
    if _existing(ledger, record):
        results.append(
            {
                "result": "idempotent",
                "family": record.get("f"),
                "unit": record["u"],
                "reason": "a byte-identical record is already recorded",
            }
        )
        return
    outcome = prompt_evidence.emit(root, record, ledger=ledger)
    results.append(outcome)
    if outcome["result"] != prompt_evidence.WRITTEN and outcome.get("family"):
        omitted.add(outcome["family"])


def _evidence_records(
    *,
    plan: str,
    unit: str,
    date: str,
    review_id: str,
    verdict: str,
    ordinal: int,
    quality: contract_review.ContractQuality,
    closure: Optional[acceptance_trace.ClosureResult],
) -> List[Dict[str, Any]]:
    """Build every ledger record this pass produces, in emission order."""
    records: List[Dict[str, Any]] = [
        prompt_evidence.event(
            plan=plan,
            unit=unit,
            date=date,
            family="RRR",
            ordinal=ordinal,
            marker=verdict,
            artifact=review_id,
        )
    ]
    if closure is not None:
        for det in closure.determinations:
            if det.verdict != "fail":
                continue
            records.append(
                prompt_evidence.determination_address(
                    plan=plan,
                    unit=unit,
                    date=date,
                    # A D2 non-pass is an omitted-requirement finding; a D1
                    # non-pass is a rejection reason. The split is closed.
                    family="OMR" if det.determination == "D2" else "RRR",
                    kind="det",
                    determination=det.determination,
                    criterion="-" if det.scope == "task" else det.scope,
                    reason=det.reason,
                    artifact=review_id,
                )
            )
    for gap in quality.gaps:
        records.append(
            prompt_evidence.determination_address(
                plan=plan,
                unit=unit,
                date=date,
                family=(
                    "OMR"
                    if gap.check == contract_review.UPSTREAM_ALIGNMENT
                    else "RRR"
                ),
                kind="cq",
                gap=gap.ordinal,
                check=gap.check,
                severity=gap.severity,
                artifact=review_id,
            )
        )
    if verdict != "approve":
        for finding in quality.findings:
            records.append(
                prompt_evidence.determination_address(
                    plan=plan,
                    unit=unit,
                    date=date,
                    family="RRR",
                    kind="fnd",
                    gap=finding.ordinal,
                    severity=finding.severity,
                    artifact=review_id,
                )
            )
    return records


@dataclass
class Assessment:
    """One closure review, judged against both accepted contracts.

    The intake command and the ``in-review -> done`` move guard read the same
    assessment, so the boundary that records the evidence and the boundary
    that applies the verdict cannot drift apart on what blocks approval.
    """

    quality: contract_review.ContractQuality
    verdict: str
    reviewer: str
    binding: trace_binding.Binding
    closure: Optional[acceptance_trace.ClosureResult]
    blockers: List[Dict[str, str]]


def assess(
    project_root: Path,
    task_path: Path,
    review_text: str,
    *,
    task_text: Optional[str] = None,
) -> Assessment:
    """Judge one review: contract audit first, then the two determinations."""
    quality = contract_review.evaluate(review_text)
    verdict = _header(review_text, _VERDICT_RE)
    reviewer = _header(review_text, _REVIEWER_RE)
    blockers: List[Dict[str, str]] = list(quality.violations)
    if verdict not in prompt_evidence.VERDICTS:
        blockers.append(
            {
                "rule": "verdict-unrecorded",
                "detail": "the review carries no `Verdict:` drawn from "
                f"{prompt_evidence.VERDICTS}",
            }
        )
    if not reviewer:
        blockers.append(
            {
                "rule": "review-unattributed",
                "detail": "the review carries no `Reviewer:` value; an "
                "unattributed determination is not an independent one",
            }
        )

    binding = trace_binding.bind(project_root, task_path, task_text=task_text)
    closure: Optional[acceptance_trace.ClosureResult] = None
    if binding.refusal is not None:
        blockers.append(
            {"rule": binding.refusal.code, "detail": binding.refusal.detail}
        )
    elif binding.trace is not None:
        closure = acceptance_trace.evaluate_closure(
            binding.trace, review_text, attributed_to=reviewer
        )
        blockers.extend(
            {"rule": b.code, "detail": f"{b.detail} [{b.identity or '-'}]"}
            for b in closure.blockers
        )
    return Assessment(quality, verdict, reviewer, binding, closure, blockers)


def approval_blocker(
    project_root: Path, task_path: Path, review_text: str
) -> Optional[str]:
    """The first reason this review may not close its task, or ``None``.

    Inert for a task that does not declare ``Upstream trace: required``: the
    approval path of a legacy unit is unchanged, and no acknowledgment point
    is added to an existing unattended run.
    """
    assessment = assess(project_root, task_path, review_text)
    if not assessment.binding.enforced:
        return None
    # Same binding the intake boundary applies, so the boundary that records
    # the evidence and the boundary that applies the verdict cannot disagree
    # about whose review this is. Kept inside the enforcement gate: a unit
    # that does not declare `Upstream trace: required` gains no new stop.
    unit = _unit_id(task_path)
    if unit is not None:
        refusal = numbering_contract.review_identity_refusal(
            f"REVIEW-{unit[len('TASK-'):]}", unit, review_text
        )
        if refusal is not None:
            return f"{refusal[0]}: {refusal[1]}"
    for blocker in assessment.blockers:
        # The verdict itself is the move guard's own precondition; reporting
        # it here would restate an error the caller has already made.
        if blocker["rule"] == "verdict-unrecorded":
            continue
        return f"{blocker['rule']}: {blocker['detail']}"
    return None


def handler(args: argparse.Namespace) -> int:
    root = Path(args.project_root)
    if not root.is_absolute():
        stderr_usage("project_root must be absolute")
        return EXIT_USAGE
    root = root.resolve()
    if not (root / "cartopian.toml").is_file():
        stderr_error(f"project config not found: {root / 'cartopian.toml'}")
        return EXIT_FAIL
    # Both inputs are project artifacts. Reading whatever absolute file the
    # caller names would let an unrelated document — or a symlink planted in
    # `tasks/` or `reviews/` — supply the determinations, gaps, and evidence
    # records this intake writes against a real unit.
    try:
        task, task_text = artifact_paths.task(root, args.task)
        review, review_text = artifact_paths.review(root, args.review)
    except artifact_paths.ArtifactRefusal as refusal:
        stderr_guard(f"{refusal.rule}: {refusal.detail}")
        return EXIT_FAIL
    unit = _unit_id(task)
    review_id = _review_id(review)
    if unit is None:
        stderr_usage(f"task path does not name a TASK-NN-NNN unit: {task.name}")
        return EXIT_USAGE
    if review_id is None:
        stderr_usage(f"review path does not name a REVIEW-NN-NNN: {review.name}")
        return EXIT_USAGE
    # One intake covers one unit. A review of a different unit would address
    # its determinations and evidence to the task named on the command line.
    if unit[len("TASK-"):] != review_id[len("REVIEW-"):]:
        stderr_guard(
            f"identity-mismatch: {review_id} does not review {unit}"
        )
        return EXIT_FAIL
    # A canonical filename says nothing about what the body inside it claims
    # to be. Bind the body's declared identity to both artifacts here, before
    # the review is assessed and before any evidence record is written, so a
    # review of another unit cannot supply this unit's determinations.
    refusal = numbering_contract.review_identity_refusal(review_id, unit, review_text)
    if refusal is not None:
        stderr_guard(f"{refusal[0]}: {refusal[1]}")
        return EXIT_FAIL
    date = args.date or datetime.date.today().isoformat()
    if not prompt_evidence.DATE_RE.match(date):
        stderr_usage("--date must be YYYY-MM-DD")
        return EXIT_USAGE

    assessment = assess(root, task, review_text, task_text=task_text)
    quality = assessment.quality
    verdict = assessment.verdict
    reviewer = assessment.reviewer
    binding = assessment.binding
    closure = assessment.closure
    blockers = assessment.blockers

    ledger = prompt_evidence.read_ledger(root)
    emissions: List[Dict[str, Any]] = []
    omitted: set = set()
    if not args.no_evidence:
        ordinal = args.pass_ordinal
        if ordinal is None:
            ordinal = 1 + sum(
                1
                for rec in ledger.for_unit(unit)
                if rec.get("k") == "E" and rec.get("f") == "RRR"
            )
        if not 1 <= ordinal <= 999:
            stderr_usage("--pass-ordinal must be 1..999")
            return EXIT_USAGE
        for record in _evidence_records(
            plan=ledger.plan_id,
            unit=unit,
            date=date,
            review_id=review_id,
            verdict=verdict if verdict in prompt_evidence.VERDICTS else "request-changes",
            ordinal=ordinal,
            quality=quality,
            closure=closure,
        ):
            _emit(root, ledger, record, emissions, omitted)

    approvable = not blockers and verdict == "approve"
    emit_record(
        {
            "action": "review-intake",
            "project_path": str(root),
            "unit": unit,
            "review": review_id,
            "date": date,
            "verdict": verdict or None,
            "reviewer_attributed": bool(reviewer),
            "contract_quality": quality.as_record(),
            "closure": closure.as_record() if closure is not None else None,
            "trace": binding.as_record(),
            "evidence": {
                "plan": ledger.plan_id,
                "emissions": emissions,
                "omitted_families": sorted(omitted),
                "ledger_errors": ledger.errors,
            },
            "blockers": blockers,
            "approvable": approvable,
        }
    )
    if blockers:
        for blocker in blockers:
            stderr_guard(f"{blocker['rule']}: {blocker['detail']}")
        return EXIT_FAIL
    return EXIT_OK
