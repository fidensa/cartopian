"""`cartopian validate-report <report-path>`.

Draft validation for handoff reports: runs the same acceptance checks
``parse-report`` / ``report-action`` apply, but enumerates every failure with
an actionable recovery instead of collapsing to ``failed-to-parse``. Run it
on a written report before the handoff ends (or before acceptance) so a
mechanical defect becomes a bounded correction, not a full rework loop.

Each failed check carries a ``failure_class`` so the caller can route it:

- ``mechanical`` — schema/transcription defect; correct the report in place
  and re-validate. No new work assignment is needed.
- ``missing-input`` — a governing artifact the validation depends on is
  absent or invalid; repair the input before re-dispatching anything.
- ``substantive`` — a recorded judgment or recorded evidence state (drift,
  an unverified decisive claim, an unresolved conflict, a stale or
  out-of-guidance applied source, absent producer evidence) that the
  producer, a reviewer, or the operator must resolve; it is not a
  formatting problem and must not be "fixed" by editing the report.
  Unknown source-evidence blocker codes fail closed into this class.

Read-only; no file writes. Stdlib only.
"""
import argparse
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from cli import report_identity, request_trace, source_guidance
from cli.commands import parse_report
from cli.emit import emit_record
from cli.main import (
    EXIT_FAIL,
    EXIT_OK,
    EXIT_USAGE,
    stderr_error,
    stderr_guard,
    stderr_usage,
)

_TASK_STATUS_DIRS = ("open", "in-progress", "in-review", "done")

# ---------------------------------------------------------------------------
# Source-evidence blocker routing: explicit and fail-closed.
#
# The failure class decides who may act on a defect — `mechanical` authorizes
# the PM's hash-bound in-place correction (`correct-report`), so a blocker is
# mechanical ONLY when its repair cannot assert or erase producer evidence.
# Every code the source-guidance authority emits is audited here by its
# recovery semantics; an unknown code never defaults into the PM-editable
# class (it fails closed as `substantive`, which routes to the review loop
# or the operator).
# ---------------------------------------------------------------------------
_SOURCE_BLOCKER_CLASSES: Dict[str, str] = {
    # Mechanical: restructuring the producer's own recorded claim bullets
    # into the row grammar. The content originates in the producer's report;
    # a decisive claim still blocks separately (`decisive-claim-unverified`,
    # substantive), and an absent disposition is refused by `correct-report`
    # (absent rows route to rework), so grammar repair cannot launder claims.
    "unhandled-unverified-claim": "mechanical",
    # Missing-input: the governing artifact, not the report, is defective —
    # repair the task/spec guidance (or restore the artifact) and rerun
    # readiness before touching any report.
    "governing-source-guidance-invalid": "missing-input",
    "source-guidance-owner-mismatch": "missing-input",
    "source-evidence-unreadable": "missing-input",
    # Substantive: each of these is recorded producer evidence or a recorded
    # state that requires producer work, verification, or named authority.
    # "Fixing" it by editing the report would erase or assert evidence:
    # an unresolved conflict needs the named decision, a stale source needs
    # a current source or authority, an out-of-guidance applied source means
    # the producer applied something ungoverned (producer rework or a
    # guidance amendment — never a PM row swap), and absent sections, rows,
    # contexts, scopes, or conflict records are producer-owned content.
    "decisive-claim-unverified": "substantive",
    "unresolved-source-conflict": "substantive",
    "stale-applicable-context": "substantive",
    "source-evidence-not-in-guidance": "substantive",
    "missing-source-guidance-section": "substantive",
    "missing-authoritative-source": "substantive",
    "missing-applicable-context": "substantive",
    "missing-conflict-resolution": "substantive",
    "missing-source-scope": "substantive",
}

# Fail closed: a blocker code this module has never audited must not become
# PM-editable by omission.
_UNKNOWN_SOURCE_BLOCKER_CLASS = "substantive"


def source_blocker_class(code: str) -> str:
    """The audited failure class for one source-evidence blocker code."""
    return _SOURCE_BLOCKER_CLASSES.get(code, _UNKNOWN_SOURCE_BLOCKER_CLASS)


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.description = (
        "Validate a written handoff report against the full acceptance "
        "contract and enumerate every defect with its recovery, instead of "
        "collapsing to failed-to-parse. Run before ending a handoff or "
        "before accepting a report; failures are classed mechanical, "
        "missing-input, or substantive so they can be routed."
    )
    subparser.add_argument(
        "report_path",
        help="Absolute path to the report file to validate",
    )
    subparser.add_argument(
        "--variant",
        choices=list(parse_report.VARIANTS),
        default=None,
        help=(
            "Explicit variant; replaces content inference but must agree "
            "with a grammar-matching report filename"
        ),
    )
    subparser.add_argument(
        "--expected-identity",
        dest="expected_identity",
        default=None,
        help=(
            "Bind validation to one accepted publication: the sha256:<hex> "
            "report_content_identity a wait primitive returned. If the "
            "report bytes on disk no longer match, the command refuses "
            "(identity-mismatch) instead of validating different bytes"
        ),
    )


def _check(
    name: str,
    passed: bool,
    reason: Optional[str] = None,
    recovery: Optional[str] = None,
    failure_class: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "name": name,
        "pass": passed,
        "reason": None if passed else reason,
        "recovery": None if passed else recovery,
        "failure_class": None if passed else failure_class,
    }


def _has_heading(content: str, section: str) -> bool:
    heading = section.removeprefix("## ")
    return bool(
        re.search(rf"^##\s+{re.escape(heading)}\s*$", content, re.MULTILINE)
    )


def _sections_check(variant: str, content: str) -> Dict[str, Any]:
    missing: List[str] = [
        section
        for section in parse_report.REQUIRED_SECTIONS[variant]
        if not _has_heading(content, section)
    ]
    for alternatives in parse_report.REQUIRED_ANY_SECTIONS[variant]:
        if not any(_has_heading(content, section) for section in alternatives):
            missing.append(" or ".join(alternatives))
    return _check(
        "required-sections-present",
        not missing,
        f"missing section(s): {', '.join(missing)}",
        "add each named section heading exactly as spelled",
        "mechanical",
    )


def _identity_keys_check(variant: str, content: str) -> Dict[str, Any]:
    missing = [
        key
        for key in parse_report.REQUIRED_IDENTITY_KEYS[variant]
        if key not in content
    ]
    return _check(
        "identity-keys-present",
        not missing,
        f"missing Identity key(s): {', '.join(missing)}",
        "add each key as a `- Key: value` bullet under ## Identity, using "
        "the machine-generated values from `cartopian report-skeleton`",
        "mechanical",
    )


def _status_check(content: str) -> Dict[str, Any]:
    raw = parse_report._extract_status(content)
    return _check(
        "status-valid",
        raw in parse_report.STATUS_VERDICT,
        (
            "Status: header is missing"
            if raw is None
            else f"Status: {raw!r} is not one of complete, blocked, failed"
        ),
        "set the top-of-file Status: field to complete, blocked, or failed",
        "mechanical",
    )


def _review_verdict_check(variant: str, content: str) -> Dict[str, Any]:
    if variant not in parse_report.REVIEW_VARIANTS:
        return _check("review-verdict-valid", True)
    if parse_report._extract_status(content) != "complete":
        return _check("review-verdict-valid", True)
    verdict = parse_report._extract_review_verdict(content)
    return _check(
        "review-verdict-valid",
        verdict is not None,
        "## Verdict does not begin with approve, request-changes, or reject",
        "make the first line under ## Verdict exactly one verdict token",
        "mechanical",
    )


def _report_task_id(report_path: Path, variant: str) -> Optional[str]:
    if variant == "task":
        match = report_identity.TASK_COMPLETION_REPORT_RE.match(report_path.name)
    elif variant == "review":
        match = report_identity.TASK_REVIEW_REPORT_RE.match(report_path.name)
    else:
        return None
    return f"TASK-{match.group(1)}" if match else None


def _find_task_for_report(
    project_root: Path, task_id: str
) -> Optional[Path]:
    for status in _TASK_STATUS_DIRS:
        directory = project_root / "tasks" / status
        if not directory.is_dir():
            continue
        direct = directory / f"{task_id}.md"
        if direct.is_file():
            return direct.resolve()
        for candidate in sorted(directory.glob(f"{task_id}-*.md")):
            if candidate.is_file():
                return candidate.resolve()
    return None


def _source_evidence_checks(
    project_root: Optional[Path],
    report_path: Path,
    content: str,
    variant: str,
) -> List[Dict[str, Any]]:
    if variant != "task" or parse_report._extract_status(content) != "complete":
        return [_check("source-evidence-valid", True)]
    task_id = _report_task_id(report_path, variant)
    if task_id is None:
        # A filename outside the task-scoped grammar carries no task link, so
        # source-evidence resolution does not apply (mirrors parse-report).
        return [_check("source-evidence-valid", True)]
    if project_root is None:
        return [
            _check(
                "source-evidence-valid",
                False,
                "no enclosing Cartopian project found for the report",
                "validate the report at its expected reports/ path inside "
                "the project",
                "missing-input",
            )
        ]
    task_path = _find_task_for_report(project_root, task_id)
    if task_path is None:
        return [
            _check(
                "source-evidence-valid",
                False,
                f"no task on disk matches {task_id}",
                "write the report to the exact expected report path for its "
                "task",
                "missing-input",
            )
        ]
    try:
        record = source_guidance.resolve_report_evidence(task_path, content)
    except (OSError, UnicodeError, ValueError) as exc:
        return [
            _check(
                "source-evidence-valid",
                False,
                f"source evidence could not be resolved: {exc}",
                "restore the governing task/spec and source evidence",
                "missing-input",
            )
        ]
    if record["outcome"] in ("not-required", "valid"):
        return [_check("source-evidence-valid", True)]
    checks: List[Dict[str, Any]] = []
    for blocker in record["blockers"]:
        checks.append(
            _check(
                f"source-evidence:{blocker['code']}",
                False,
                blocker["detail"],
                blocker["recovery"],
                source_blocker_class(blocker["code"]),
            )
        )
    return checks


def _identity_alignment_mismatches(
    project_root: Optional[Path],
    report_path: Path,
    content: str,
    variant: str,
) -> List[tuple]:
    """Structured Identity mismatches: ``(key_or_None, message)`` pairs.

    ``key`` names the defective Identity bullet (the correction surface uses
    it to authorize exactly that bullet and nothing else); a non-key defect
    such as a missing review file carries ``None``.
    """
    if variant not in ("task", "review") or project_root is None:
        return []
    if _report_task_id(report_path, variant) is None:
        return []
    from cli.commands import report_action

    identity = report_action._extract_identity_map(content)
    expected = report_action._expected_paths(
        project_root, report_path, variant, identity
    )
    expected_task_id = _report_task_id(report_path, variant)
    expected_values: Dict[str, Optional[str]] = {
        "Task ID": expected_task_id,
        "Prompt path": (
            str(expected["expected_prompt_path"])
            if expected["expected_prompt_path"] is not None
            else None
        ),
        "Task path": (
            str(expected["expected_task_path"])
            if expected["expected_task_path"] is not None
            else None
        ),
        "Review ID": (
            expected["expected_review_id"].name
            if expected["expected_review_id"] is not None
            else None
        ),
        "Review file path": (
            str(expected["expected_review_path"])
            if expected["expected_review_path"] is not None
            else None
        ),
    }
    required_keys = (
        ("Review ID", "Prompt path", "Task path", "Review file path")
        if variant == "review"
        else ()
    )
    mismatches: List[tuple] = []
    for key, expected_value in expected_values.items():
        declared = identity.get(key)
        if declared is None:
            if key in required_keys:
                mismatches.append((key, f"{key} is missing"))
            continue
        normalized = report_action._normalize_path_value(declared)
        if expected_value is None:
            continue
        if key.endswith("path"):
            declared_resolved = (
                str(Path(normalized).resolve()) if normalized else None
            )
        else:
            declared_resolved = normalized
        if declared_resolved != expected_value:
            mismatches.append(
                (key, f"{key} is {declared!r}; expected {expected_value}")
            )
    if variant == "review":
        review_path = expected["expected_review_path"]
        if review_path is not None and not Path(review_path).exists():
            mismatches.append(
                (None, f"review file does not exist: {review_path}")
            )
    return mismatches


def _identity_alignment_check(
    project_root: Optional[Path],
    report_path: Path,
    content: str,
    variant: str,
) -> Dict[str, Any]:
    """Compare declared Identity ids/paths to their machine-expected values.

    Task-report Identity values are optional (the coder handoff is
    deidentified) and cross-checked only when declared; review-report values
    are required and must match exactly. Each mismatch is named field by
    field so a transcription defect is a bounded correction.
    """
    mismatches = _identity_alignment_mismatches(
        project_root, report_path, content, variant
    )
    return _check(
        "identity-values-aligned",
        not mismatches,
        "; ".join(message for _key, message in mismatches),
        "use the machine-generated Identity values from "
        "`cartopian report-skeleton` verbatim, and write the review file "
        "before the review report",
        "mechanical",
    )


def _alignment_check(
    report_path: Path, content: str, variant: str
) -> Dict[str, Any]:
    record = parse_report.review_alignment_record(report_path, content, variant)
    if record is None or not record["blocking"]:
        return _check("request-alignment-valid", True)
    failure_class = (
        "substantive" if record.get("value") == "drifted" else "mechanical"
    )
    return _check(
        "request-alignment-valid",
        False,
        record["detail"],
        "carry the bound request evidence identities verbatim (see "
        "`cartopian report-skeleton`); a recorded drift is a reviewer "
        "judgment for the operator, not a formatting fix",
        failure_class,
    )


def resolve_variant(
    report_path: Path,
    content: str,
    explicit_variant: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Resolve the report variant, honoring the filename contract.

    Returns ``(variant, error)``: exactly one is non-None. Shared with the
    mediated mechanical-correction surface so both resolve identically.
    """
    if explicit_variant:
        contract_variant = report_identity.filename_contract_variant(
            report_path.name
        )
        if contract_variant is not None and explicit_variant != contract_variant:
            return None, (
                f"path/variant mismatch: variant {explicit_variant} "
                f"contradicts the filename contract for {report_path.name} "
                f"(mandates {contract_variant})"
            )
        return explicit_variant, None
    return parse_report._infer_variant(report_path, content)


def collect_checks(
    project_root: Optional[Path],
    report_path: Path,
    content: str,
    variant: str,
) -> List[Dict[str, Any]]:
    """Run every acceptance check against one report body.

    The single check set behind ``validate-report`` and the mediated
    mechanical-correction surface, so a corrected report is judged by exactly
    the contract it failed.
    """
    return [
        _sections_check(variant, content),
        _identity_keys_check(variant, content),
        _status_check(content),
        _review_verdict_check(variant, content),
        _identity_alignment_check(project_root, report_path, content, variant),
        _alignment_check(report_path, content, variant),
        *_source_evidence_checks(project_root, report_path, content, variant),
    ]


def handler(args: argparse.Namespace) -> int:
    raw_path = args.report_path
    if not Path(raw_path).is_absolute():
        stderr_usage(f"report_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE
    report_path = Path(raw_path)
    if not report_path.is_file():
        stderr_error(f"report not found: {raw_path}")
        return EXIT_FAIL
    report_path = report_path.resolve()

    try:
        content = report_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        stderr_error(f"report unreadable: {raw_path} — {exc}")
        return EXIT_FAIL

    observed_identity = report_identity.content_identity(content)
    expected_identity = getattr(args, "expected_identity", None)
    if expected_identity is not None:
        if not report_identity.CONTENT_IDENTITY_RE.match(expected_identity):
            stderr_usage(
                f"invalid --expected-identity {expected_identity!r}; "
                "expected sha256:<64 lowercase hex digits>"
            )
            return EXIT_USAGE
        if expected_identity != observed_identity:
            emit_record(
                {
                    "report_path": str(report_path),
                    "ok": False,
                    "identity_mismatch": True,
                    "expected_content_identity": expected_identity,
                    "report_content_identity": observed_identity,
                }
            )
            stderr_guard(
                f"report-identity-mismatch: {report_path} no longer matches "
                f"the accepted publication (expected {expected_identity}, "
                f"observed {observed_identity}) — re-run the canonical wait "
                "and validate the identity it returns"
            )
            return EXIT_FAIL

    variant, err = resolve_variant(report_path, content, args.variant)
    if variant is None:
        stderr_usage(err)
        return EXIT_USAGE

    project_root = request_trace.find_project_root(report_path)
    checks = collect_checks(project_root, report_path, content, variant)
    ok = all(item["pass"] for item in checks)

    emit_record(
        {
            "report_path": str(report_path),
            "variant": variant,
            "report_content_identity": observed_identity,
            "ok": ok,
            "checks": checks,
        }
    )
    if not ok:
        for item in checks:
            if item["pass"]:
                continue
            stderr_guard(
                f"{item['name']} [{item['failure_class']}]: {item['reason']} "
                f"— {item['recovery']}"
            )
        return EXIT_FAIL
    return EXIT_OK
