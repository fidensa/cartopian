"""`cartopian report-action <report-path>` aggregator."""
import argparse
import datetime
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from cli import artifact_paths, report_identity, source_guidance
from cli.commands import parse_report
from cli.commands.plan_audit import _resolve_pm_owns_product_branches
from cli.commands.resolve_config import (
    _CliError,
    _load_toml,
    _require_project_keys,
    resolve_review_policy,
)
from cli.emit import emit_record
from cli.main import (
    EXIT_ENV,
    EXIT_FAIL,
    EXIT_OK,
    EXIT_USAGE,
    stderr_error,
    stderr_guard,
    stderr_usage,
)

_TASK_ID_RE = re.compile(r"^TASK-(\d{2}-\d{3})\.md$")
_TASK_STATUS_DIRS = ("open", "in-progress", "in-review", "done")
_IDENTITY_SECTION_RE = re.compile(
    r"^##\s+Identity\s*$(.*?)(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)
_SUMMARY_SECTION_RE = re.compile(
    r"^##\s+Summary\s*$(.*?)(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)
_FINDINGS_SECTION_RE = re.compile(
    r"^##\s+Findings\s*$(.*?)(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)
_CONTRACT_QUALITY_SECTION_RE = re.compile(
    r"^##\s+Contract quality\s*$(.*?)(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)
_BLOCKING_FINDINGS_SECTION_RE = re.compile(
    r"^##\s+Blocking findings\s*$(.*?)(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)
_REVIEW_VERDICT_HEADER_RE = re.compile(r"^Verdict:\s*(\S+)\s*$", re.MULTILINE)
_FINDING_ROW_RE = re.compile(r"^-\s+[CF]\d+\.\s")

# Projection bounds. These cap what enters PM *context*, never what may be
# stored: reports and reviews remain architecturally unbounded on disk (see
# tests/test_size_threshold_regressions.py). The projection exists so the PM
# can route on the verdict, summary, and findings without opening the whole
# artifact; the unbounded body stays on disk as durable evidence.
PM_SUMMARY_MAX_CHARS = 2000
PROJECTED_FINDING_MAX_CHARS = 500
PROJECTED_FINDINGS_MAX = 30
_TRUNCATION_MARK = " …[truncated; read the artifact for the full text]"


def _bounded_text(value: Optional[str], limit: int) -> Tuple[Optional[str], bool]:
    if value is None:
        return None, False
    if len(value) <= limit:
        return value, False
    # The marker lives inside the advertised limit, so a truncated value is
    # never longer than an untruncated one at the bound.
    keep = max(0, limit - len(_TRUNCATION_MARK))
    return value[:keep].rstrip() + _TRUNCATION_MARK, True


def _extract_pm_summary(content: str) -> Tuple[Optional[str], bool]:
    """The bounded `## Summary` body of a report or review, when present."""
    body = _extract_heading_body(_SUMMARY_SECTION_RE, content)
    return _bounded_text(body, PM_SUMMARY_MAX_CHARS)


def _projected_findings(body: str) -> Tuple[list, int]:
    """Bounded `F<n>.`/`C<n>.` rows of a findings section body.

    Each row is one finding per the review template's grammar; a
    continuation line indented under a row stays with its row.
    """
    rows: list = []
    for line in body.splitlines():
        stripped = line.rstrip()
        if _FINDING_ROW_RE.match(stripped.strip()):
            rows.append(stripped.strip())
        elif rows and stripped.startswith((" ", "\t")) and stripped.strip():
            rows[-1] = rows[-1] + " " + stripped.strip()
    omitted = max(0, len(rows) - PROJECTED_FINDINGS_MAX)
    bounded = [
        _bounded_text(row, PROJECTED_FINDING_MAX_CHARS)[0]
        for row in rows[:PROJECTED_FINDINGS_MAX]
    ]
    return bounded, omitted


def _review_projection(
    project_root: Path,
    review_path: Optional[Path],
    report_content: str,
) -> Optional[Dict[str, Any]]:
    """Bounded PM-facing projection of the durable review file.

    ``review_path`` must be the *lexical* canonical review slot derived from
    the report filename — never a report-declared path, which is untrusted
    input that could point the projection at an arbitrary readable file. The
    slot is read through the ``artifact_paths.review`` containment helper, so
    a symlinked or hardlinked slot aliasing an outside-project file is
    refused rather than read. On any refusal (absent slot included), fall
    back to the completion report's `## Blocking findings` body so the PM
    still gets a bounded findings read. Best-effort throughout — the
    projection informs the PM, never the routing verdict.
    """
    verdict: Optional[str] = None
    summary: Optional[str] = None
    summary_truncated = False
    findings: list = []
    findings_omitted = 0
    source = None
    if review_path is not None:
        try:
            _, review_content = artifact_paths.review(project_root, review_path)
        except artifact_paths.ArtifactRefusal:
            review_content = None
        if review_content is not None:
            source = "review-file"
            match = _REVIEW_VERDICT_HEADER_RE.search(review_content)
            verdict = match.group(1) if match else None
            summary, summary_truncated = _extract_pm_summary(review_content)
            # Findings live in two sections of the review template: contract
            # defects (C<n>) under `## Contract quality`, implementation
            # defects (F<n>) under `## Findings`. Project both, in document
            # order, so a contract-only verdict still carries its findings.
            bodies = [
                body
                for body in (
                    _extract_heading_body(
                        _CONTRACT_QUALITY_SECTION_RE, review_content
                    ),
                    _extract_heading_body(_FINDINGS_SECTION_RE, review_content),
                )
                if body
            ]
            if bodies:
                findings, findings_omitted = _projected_findings(
                    "\n".join(bodies)
                )
    if source is None:
        body = _extract_heading_body(
            _BLOCKING_FINDINGS_SECTION_RE, report_content
        )
        if body is None:
            return None
        source = "report-blocking-findings"
        bounded, truncated = _bounded_text(body, PM_SUMMARY_MAX_CHARS)
        summary, summary_truncated = bounded, truncated
    return {
        "source": source,
        "verdict": verdict,
        "summary": summary,
        "summary_truncated": summary_truncated,
        "findings": findings,
        "findings_omitted": findings_omitted,
    }


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    """Add arguments for report-action."""
    subparser.add_argument(
        "report_path",
        help="Path to the report file to parse",
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
            "Bind routing to one accepted publication: the sha256:<hex> "
            "report_content_identity a wait primitive returned. If the "
            "report bytes on disk no longer match, the command refuses "
            "(identity-mismatch) instead of routing on different bytes"
        ),
    )


def _capture_clarification(
    project_root: Optional[Path],
    task_id: Optional[str],
    report_id: str,
    status_value: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Record that work stalled for input, at the boundary that observes it.

    A parsed ``Status: blocked`` is the authoritative clarification event that
    exists today. Routing is read-only and may run more than once over the
    same bytes, so the capture is idempotent: a byte-identical record is not
    appended twice. Best-effort throughout — measurement never changes how a
    report routes.
    """
    if project_root is None or not task_id or status_value != "blocked":
        return None
    try:
        from cli import prompt_evidence

        ledger = prompt_evidence.read_ledger(project_root)
        record = prompt_evidence.event(
            plan=ledger.plan_id,
            unit=task_id,
            date=datetime.date.today().isoformat(),
            family="CLR",
            artifact=report_id,
        )
        line = prompt_evidence.serialize(record)
        if any(
            prompt_evidence.serialize(existing) == line
            for existing in ledger.for_unit(task_id)
        ):
            return {"result": "idempotent", "family": "CLR", "unit": task_id}
        return prompt_evidence.emit(project_root, record, ledger=ledger)
    except Exception:  # pragma: no cover - never changes routing
        return None


def _find_project_root(report_path: Path) -> Optional[Path]:
    if report_path.parent.name == "reports":
        return report_path.parent.parent
    for candidate in report_path.parents:
        if (candidate / "reports").is_dir():
            return candidate
    return None


def _load_project_config(project_root: Path) -> Dict[str, Any]:
    project_toml = project_root / "cartopian.toml"
    if not project_toml.is_file():
        raise _CliError(EXIT_ENV, "error", f"project config not found: {project_toml}")
    project_cfg = _load_toml(project_toml, "project config") or {}
    _require_project_keys(project_cfg, project_toml)
    return project_cfg


def _extract_heading_body(pattern: re.Pattern[str], content: str) -> Optional[str]:
    match = pattern.search(content)
    if not match:
        return None
    body = match.group(1).strip()
    return body if body else None


def _extract_identity_map(content: str) -> Dict[str, str]:
    body = _extract_heading_body(_IDENTITY_SECTION_RE, content)
    if body is None:
        return {}
    result: Dict[str, str] = {}
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        payload = stripped[2:]
        if ":" not in payload:
            continue
        key, value = payload.split(":", 1)
        result[key.strip()] = value.strip()
    return result


def _extract_ready_for_review(content: str) -> Optional[bool]:
    """The readiness value under ``## Ready to close`` / ``## Ready for review``.

    Delegates to the canonical parser so routing, validation, and the
    task-closure review bootstrap cannot hold different opinions about the
    same publication (``parse_report.extract_ready_for_review``).
    """
    return parse_report.extract_ready_for_review(content)


def _parse_report_state(
    report_path: Path,
    content: str,
    explicit_variant: Optional[str],
) -> Tuple[str, str, Optional[str], Optional[str]]:
    if explicit_variant:
        # An explicit variant cannot bypass the filename contract: a
        # grammar-matching task-scoped or planning filename mandates its
        # variant (report_identity.filename_contract_variant).
        contract_variant = report_identity.filename_contract_variant(
            report_path.name
        )
        if contract_variant is not None and explicit_variant != contract_variant:
            raise _CliError(
                EXIT_USAGE,
                "usage",
                f"path/variant mismatch: variant {explicit_variant} "
                f"contradicts the filename contract for {report_path.name} "
                f"(mandates {contract_variant})",
            )
        variant = explicit_variant
    else:
        variant, err = parse_report._infer_variant(report_path, content)
        if variant is None:
            raise _CliError(EXIT_USAGE, "usage", err or "cannot infer variant")

    raw_status = parse_report._extract_status(content)
    status_value = (
        raw_status if raw_status in parse_report.STATUS_VERDICT else None
    )
    if not parse_report._schema_ok(variant, content):
        return "failed-to-parse", variant, status_value, None

    if raw_status is None or raw_status not in parse_report.STATUS_VERDICT:
        return "failed-to-parse", variant, None, None

    if variant in parse_report.REVIEW_VARIANTS:
        raw_verdict = parse_report._extract_review_verdict(content)
        if raw_status == "complete":
            if raw_verdict is None:
                return "failed-to-parse", variant, None, None
            return parse_report.REVIEW_VERDICT_OUTCOME[raw_verdict], variant, raw_status, raw_verdict
        return parse_report.STATUS_VERDICT[raw_status], variant, raw_status, raw_verdict

    if parse_report.readiness_contradiction(variant, content) is not None:
        return "failed-to-parse", variant, raw_status, None
    return parse_report.STATUS_VERDICT[raw_status], variant, raw_status, None


def _report_suffix(report_path: Path, variant: str) -> Optional[str]:
    stem = report_path.stem
    if variant == "planning-review":
        prefix = "REPORT-PLAN-"
        if stem.startswith(prefix):
            return stem[len(prefix):]
        return None
    if variant == "review":
        # The task-review report carries the task identity plus the -review
        # marker (REPORT-NN-NNN-review.md). The filename contract is
        # authoritative: an unmarked or out-of-grammar name never resolves
        # review identities, so a misplaced review report fails closed.
        match = report_identity.TASK_REVIEW_REPORT_RE.match(report_path.name)
        return match.group(1) if match else None
    prefix = "REPORT-"
    if stem.startswith(prefix):
        return stem[len(prefix):]
    return None


def _contained_task_path(project_root: Path, candidate: Path) -> Optional[Path]:
    """The canonical contained path for a filename-derived task candidate.

    Downstream consumers read the expected task path, so every candidate
    passes the artifact containment allowlist first: a symlinked, hardlinked,
    or otherwise aliased slot yields None instead of a path that escapes the
    project boundary.
    """
    try:
        return artifact_paths.resolve(
            project_root,
            candidate,
            subdirs=artifact_paths.TASK_SUBDIRS,
            label="task",
        )
    except artifact_paths.ArtifactRefusal:
        return None


def _find_expected_task_path(project_root: Path, task_id: str) -> Optional[Path]:
    for status in _TASK_STATUS_DIRS:
        status_dir = project_root / "tasks" / status
        if not status_dir.is_dir():
            continue
        direct = _contained_task_path(
            project_root, status_dir / f"{task_id}.md"
        )
        if direct is not None:
            return direct
        for candidate in sorted(status_dir.glob(f"{task_id}-*.md")):
            contained = _contained_task_path(project_root, candidate)
            if contained is not None:
                return contained
    return None


def _task_declares_work_roots(task_content: Optional[str]) -> bool:
    if task_content is None:
        return False
    match = re.search(r"^Work root:\s*(.+)$", task_content, re.MULTILINE)
    if not match:
        return False
    raw = match.group(1).strip().lower()
    return raw not in {"", "n/a", "none"}


def _expected_paths(
    project_root: Path,
    report_path: Path,
    variant: str,
    identity: Dict[str, str],
) -> Dict[str, Optional[Path]]:
    suffix = _report_suffix(report_path, variant)
    expected_prompt_path: Optional[Path] = None
    expected_review_path: Optional[Path] = None
    expected_task_path: Optional[Path] = None
    expected_review_id: Optional[str] = None

    if suffix is not None:
        if variant == "task":
            task_id = f"TASK-{suffix}"
            expected_prompt_path = (project_root / "prompts" / f"PROMPT-{suffix}.md").resolve()
            expected_review_path = (project_root / "reviews" / f"REVIEW-{suffix}.md").resolve()
            expected_task_path = _find_expected_task_path(project_root, task_id)
        elif variant == "review":
            task_id = f"TASK-{suffix}"
            expected_prompt_path = (project_root / "prompts" / f"PROMPT-{suffix}.md").resolve()
            expected_review_id = f"REVIEW-{suffix}"
            expected_review_path = (project_root / "reviews" / f"{expected_review_id}.md").resolve()
            expected_task_path = _find_expected_task_path(project_root, task_id)
        else:
            expected_prompt_path = (project_root / "prompts" / f"PROMPT-PLAN-{suffix}.md").resolve()
            expected_review_id = f"REVIEW-PLAN-{suffix}"
            expected_review_path = (project_root / "reviews" / f"{expected_review_id}.md").resolve()

    return {
        "expected_prompt_path": expected_prompt_path,
        "expected_review_path": expected_review_path,
        "expected_task_path": expected_task_path,
        "expected_review_id": Path(expected_review_id) if expected_review_id is not None else None,
    }


def _normalize_path_value(value: str) -> Optional[str]:
    """Strip cosmetic markdown wrapping from an Identity path value.

    Report authors sometimes wrap path values in a markdown code span
    (`` `…` ``). The backticks are not part of the path; left in place they
    make the value parse as relative and produce a false path mismatch
    (AR-5). Strip surrounding backticks and whitespace before resolving;
    return None for an empty/blank value.
    """
    value = value.strip().strip("`").strip()
    return value or None


def _path_from_identity(identity: Dict[str, str], key: str) -> Optional[Path]:
    value = identity.get(key)
    if value is None:
        return None
    normalized = _normalize_path_value(value)
    if normalized is None:
        return None
    return Path(normalized).resolve()


def _task_path_mismatch(
    identity: Dict[str, str],
    expected_prompt_path: Optional[Path],
    expected_task_path: Optional[Path],
    expected_task_id: Optional[str],
) -> bool:
    """Whether a task report's filename fails to resolve to a real task.

    The coder (task) handoff is deidentified: the report carries no Identity
    ids/paths, and the report *filename* (`REPORT-NN-NNN.md`) is the source of
    truth for the task link. So the only hard requirement is that a task on disk
    matches that filename. Any Identity id/path a legacy report still declares is
    cross-checked when present (a stale or wrong value is a mismatch) but is
    never required.
    """
    if expected_task_path is None:
        return True  # no task on disk matches this report's filename
    declared_prompt_path = _path_from_identity(identity, "Prompt path")
    if declared_prompt_path is not None and declared_prompt_path != expected_prompt_path:
        return True
    declared_task_path = _path_from_identity(identity, "Task path")
    if declared_task_path is not None and declared_task_path != expected_task_path:
        return True
    declared_task_id = identity.get("Task ID")
    if declared_task_id is not None and declared_task_id != expected_task_id:
        return True
    return False


def _review_path_mismatch(
    identity: Dict[str, str],
    expected_prompt_path: Optional[Path],
    expected_task_path: Optional[Path],
    expected_task_id: Optional[str],
    expected_review_path: Optional[Path],
    expected_review_id: Optional[str],
) -> bool:
    declared_prompt_path = _path_from_identity(identity, "Prompt path")
    declared_task_path = _path_from_identity(identity, "Task path")
    declared_review_path = _path_from_identity(identity, "Review file path")
    declared_review_id = identity.get("Review ID")
    if declared_prompt_path != expected_prompt_path:
        return True
    if expected_task_id is not None and (
        expected_task_path is None or declared_task_path != expected_task_path
    ):
        return True
    if expected_review_path is None or declared_review_path != expected_review_path:
        return True
    if declared_review_id != expected_review_id:
        return True
    return not declared_review_path.exists()


def _target_task_status(
    variant: str,
    verdict: str,
    ready_for_review: Optional[bool],
    task_review_required: bool = True,
) -> Optional[str]:
    if verdict == "failed-to-parse":
        return None
    if variant == "task":
        if verdict == "accepted":
            if ready_for_review is True:
                return "in-review" if task_review_required else "done"
            if ready_for_review is False:
                return "in-progress"
            return None
        if verdict in {"blocked", "failed"}:
            return "in-progress"
        return None
    if verdict == "accepted":
        return "done"
    if verdict == "changes-requested":
        return "in-progress"
    if verdict == "rejected":
        return "open"
    if verdict in {"blocked", "failed"}:
        return "in-review"
    return None


def _prompt_to_overwrite(
    variant: str,
    verdict: str,
    ready_for_review: Optional[bool],
    prompt_path: Optional[Path],
    task_review_required: bool = True,
) -> Optional[str]:
    if prompt_path is None:
        return None
    if variant == "task":
        if (
            task_review_required
            and verdict == "accepted"
            and ready_for_review is True
        ):
            return str(prompt_path)
        return None
    if verdict in {"accepted", "changes-requested", "rejected"}:
        return str(prompt_path)
    return None


def _review_path_output(
    variant: str,
    verdict: str,
    ready_for_review: Optional[bool],
    expected_review_path: Optional[Path],
    declared_review_path: Optional[Path],
    task_review_required: bool = True,
) -> Optional[str]:
    if variant == "task":
        if (
            task_review_required
            and verdict == "accepted"
            and ready_for_review is True
            and expected_review_path is not None
        ):
            return str(expected_review_path)
        return None
    if declared_review_path is not None:
        return str(declared_review_path)
    if expected_review_path is not None:
        return str(expected_review_path)
    return None


def _recommended_action(
    variant: str,
    verdict: str,
    ready_for_review: Optional[bool],
    requires_pr_step: bool,
    task_review_required: bool = True,
) -> str:
    if verdict == "failed-to-parse":
        return "stop-for-inspection"
    if variant == "task":
        if verdict == "accepted":
            if ready_for_review is True:
                if not task_review_required:
                    if requires_pr_step:
                        return "prepare-pr-and-close-task"
                    return "close-task"
                if requires_pr_step:
                    return "prepare-pr-and-assign-review"
                return "assign-review"
            if ready_for_review is False:
                return "return-control-to-operator"
        if verdict in {"blocked", "failed"}:
            return "return-control-to-operator"
    else:
        if verdict == "accepted":
            if requires_pr_step:
                return "merge-pr-and-close-task"
            return "close-task"
        if verdict == "changes-requested":
            return "return-task-to-in-progress"
        if verdict == "rejected":
            return "return-task-to-open"
        if verdict in {"blocked", "failed"}:
            return "return-control-to-operator"
    return "return-control-to-operator"


def handler(args: argparse.Namespace) -> int:
    """Parse a handoff report and emit a single routing record."""
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
    except OSError as exc:
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
            # The report was mutated after the observation that produced the
            # expected identity. Routing on these bytes would act on a
            # publication no wait accepted — refuse and re-observe instead.
            emit_record(
                {
                    "verdict": "identity-mismatch",
                    "report_path": str(report_path),
                    "expected_content_identity": expected_identity,
                    "report_content_identity": observed_identity,
                    "recommended_action": "rerun-canonical-wait",
                }
            )
            stderr_guard(
                f"report-identity-mismatch: {report_path} no longer matches "
                f"the accepted publication (expected {expected_identity}, "
                f"observed {observed_identity}) — re-run the canonical wait "
                "and route the identity it returns"
            )
            return EXIT_FAIL

    project_root = _find_project_root(report_path)
    if project_root is None:
        stderr_error(f"project config not found for report: {raw_path}")
        return EXIT_ENV

    try:
        _load_project_config(project_root)
        review_policy = resolve_review_policy(project_root)
    except _CliError as err:
        stderr_error(err.message)
        return err.exit_code

    try:
        verdict, variant, status_value, review_verdict = _parse_report_state(
            report_path,
            content,
            args.variant,
        )
    except _CliError as err:
        if err.exit_code == EXIT_USAGE:
            stderr_usage(err.message)
        else:
            stderr_error(err.message)
        return err.exit_code

    alignment_record = parse_report.review_alignment_record(
        report_path, content, variant
    )
    if (
        verdict == "accepted"
        and alignment_record is not None
        and alignment_record["blocking"]
    ):
        verdict = "failed-to-parse"

    identity = _extract_identity_map(content)
    declared_task_path = _path_from_identity(identity, "Task path")
    declared_review_path = _path_from_identity(identity, "Review file path")
    ready_for_review = _extract_ready_for_review(content) if variant == "task" else None

    expected = _expected_paths(project_root, report_path, variant, identity)
    expected_prompt_path = expected["expected_prompt_path"]
    expected_review_path = expected["expected_review_path"]
    expected_task_path = expected["expected_task_path"]
    # Read the contained task exactly once; every downstream consumer parses
    # this content instead of reopening the path, so a slot swapped after
    # discovery cannot redirect a later read. A refusal here means no
    # readable governed task backs this report.
    expected_task_content: Optional[str] = None
    if expected_task_path is not None:
        try:
            expected_task_path, expected_task_content = artifact_paths.task(
                project_root, expected_task_path
            )
        except artifact_paths.ArtifactRefusal:
            expected_task_path = None
    expected_review_id_obj = expected["expected_review_id"]
    expected_review_id = expected_review_id_obj.name if expected_review_id_obj is not None else None
    expected_task_id = (
        f"TASK-{_report_suffix(report_path, variant)}"
        if variant in {"task", "review"}
        else None
    )
    numbering_trace = {"valid": True, "classification": "not-applicable", "detail": None}
    if expected_task_path is not None and variant in {"task", "review"}:
        from cli import numbering_contract

        refusal = numbering_contract.guard_task_scoped_artifact(
            project_root,
            expected_task_path,
            report_path.stem,
            content,
            task_content=expected_task_content,
        )
        if refusal is not None:
            numbering_trace = {
                "valid": False,
                "classification": refusal[0],
                "detail": refusal[1],
            }
            verdict = "failed-to-parse"
        else:
            numbering_trace = {
                "valid": True,
                "classification": "valid",
                "detail": None,
            }

    source_evidence_record = None
    if variant == "task" and status_value == "complete" and expected_task_path is not None:
        try:
            source_evidence_record = source_guidance.resolve_report_evidence(
                expected_task_path,
                content,
                task_content=expected_task_content,
            )
        except (OSError, UnicodeError, ValueError) as exc:
            source_evidence_record = {
                "required": True,
                "outcome": "invalid",
                "guidance": None,
                "evidence": None,
                "blockers": [{
                    "code": "source-evidence-unreadable",
                    "detail": str(exc),
                    "recovery": "restore the governing task/spec and source evidence",
                }],
                "blocker_codes": ["source-evidence-unreadable"],
            }
        if verdict == "accepted" and source_evidence_record["outcome"] == "invalid":
            verdict = "failed-to-parse"

    if verdict == "failed-to-parse":
        path_mismatch = False
    elif variant == "task":
        path_mismatch = _task_path_mismatch(
            identity,
            expected_prompt_path,
            expected_task_path,
            expected_task_id,
        )
    else:
        path_mismatch = _review_path_mismatch(
            identity,
            expected_prompt_path,
            expected_task_path,
            expected_task_id,
            expected_review_path,
            expected_review_id,
        )

    # The declared task path is untrusted report input: it is recorded and
    # cross-checked, never dereferenced. Only the once-read contained task
    # content is consulted.
    requires_pr_step = False
    if verdict != "failed-to-parse" and expected_task_path is not None:
        if _resolve_pm_owns_product_branches(project_root) and _task_declares_work_roots(expected_task_content):
            if variant == "task":
                requires_pr_step = verdict == "accepted" and ready_for_review is True
            else:
                requires_pr_step = verdict == "accepted"

    task_review_required = review_policy["task_closure"]["mode"] == "required"
    target_task_status = _target_task_status(
        variant, verdict, ready_for_review, task_review_required
    )
    # Use the filename-derived prompt path: the deidentified task report no
    # longer declares a `Prompt path:`, and the expected path is authoritative.
    prompt_to_overwrite = _prompt_to_overwrite(
        variant,
        verdict,
        ready_for_review,
        expected_prompt_path,
        task_review_required,
    )
    review_path = _review_path_output(
        variant,
        verdict,
        ready_for_review,
        expected_review_path,
        declared_review_path,
        task_review_required,
    )
    pm_summary, pm_summary_truncated = _extract_pm_summary(content)
    review_projection = None
    if variant in parse_report.REVIEW_VARIANTS:
        # Project only from the lexical canonical review slot derived from
        # the report filename — never the report-declared path, and never a
        # pre-resolved path (resolving would follow a symlinked slot out of
        # the project before containment can refuse it). Skip the read
        # entirely on a path mismatch; the fallback then projects from the
        # report content already in hand.
        projection_review_path = (
            project_root / "reviews" / f"{expected_review_id}.md"
            if expected_review_id is not None and not path_mismatch
            else None
        )
        review_projection = _review_projection(
            project_root, projection_review_path, content
        )
    record = {
        "verdict": verdict,
        "variant": variant,
        "report_path": str(report_path),
        "report_content_identity": observed_identity,
        "status": status_value,
        "pm_summary": pm_summary,
        "pm_summary_truncated": pm_summary_truncated,
        "review_projection": review_projection,
        "review_verdict": review_verdict,
        "request_alignment": alignment_record,
        "source_evidence": source_evidence_record,
        "numbering_trace": numbering_trace,
        "target_task_status": target_task_status,
        "requires_pr_step": requires_pr_step,
        "prompt_to_overwrite": prompt_to_overwrite,
        "review_path": review_path,
        "declared_report_task_path": str(declared_task_path) if declared_task_path is not None else None,
        "path_mismatch": path_mismatch,
        "report_id": report_path.stem,
        "task_path": str(expected_task_path) if expected_task_path is not None else None,
        "task_id": expected_task_id,
        "expected_prompt_path": str(expected_prompt_path) if expected_prompt_path is not None else None,
        "expected_review_path": str(expected_review_path) if expected_review_path is not None else None,
        "expected_task_path": str(expected_task_path) if expected_task_path is not None else None,
        "recommended_action": _recommended_action(
            variant,
            verdict,
            ready_for_review,
            requires_pr_step,
            task_review_required,
        ),
    }
    record["effectiveness_evidence"] = _capture_clarification(
        project_root, expected_task_id, report_path.stem, status_value
    )
    emit_record(record)
    return EXIT_OK
