"""`cartopian parse-report <report-path>`."""
import argparse
import re
from pathlib import Path
from typing import Optional, Tuple

from cli import report_identity, request_trace, source_guidance
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE, stderr_error, stderr_usage

VARIANTS = ("task", "review", "planning-review")

STATUS_VERDICT = {
    "complete": "accepted",
    "blocked": "blocked",
    "failed": "failed",
}

REVIEW_VERDICT_OUTCOME = {
    "approve": "accepted",
    "request-changes": "changes-requested",
    "reject": "rejected",
}

REVIEW_VARIANTS = ("review", "planning-review")

REQUIRED_SECTIONS = {
    "task": (
        "## Identity",
        "## Remaining risks",
    ),
    "review": (
        "## Identity",
        "## Evidence reviewed",
        "## Verdict",
        "## Blocking findings",
    ),
    "planning-review": (
        "## Identity",
        "## Evidence reviewed",
        "## Verdict",
        "## Blocking findings",
    ),
}

# Each tuple is an alternative group: at least one exact heading in the group
# must be present.  The neutral headings are preferred; the specialized and
# legacy headings keep every previously valid task report valid.
REQUIRED_ANY_SECTIONS = {
    "task": (
        ("## Completion evidence", "## Files changed", "## Deliverable"),
        ("## Ready to close", "## Ready for review"),
    ),
    "review": (),
    "planning-review": (),
}

# Identity keys a report must carry to validate. Assignee (task) handoffs are
# deidentified: the task report records no PM identifiers — Cartopian links it
# to its task by the report *filename* (`REPORT-NN-NNN.md`), so no Identity key
# is required. Review handoffs go to a reviewer that works with PM artifacts and
# keep their identity fields.
REQUIRED_IDENTITY_KEYS = {
    "task": (),
    "review": ("Review ID:", "Prompt path:", "Task path:", "Review file path:"),
    "planning-review": ("Review ID:", "Prompt path:", "Review file path:"),
}


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "report_path",
        help="Path to the report file to parse",
    )
    subparser.add_argument(
        "--variant",
        choices=list(VARIANTS),
        default=None,
        help=(
            "Explicit variant; replaces content inference but must agree "
            "with a grammar-matching report filename"
        ),
    )


_VERDICT_SECTION_RE = re.compile(r"^##\s+Verdict\s*$", re.MULTILINE)
_READY_SECTION_RE = re.compile(r"^##\s+Ready for review\s*$", re.MULTILINE)
_READY_TO_CLOSE_SECTION_RE = re.compile(r"^##\s+Ready to close\s*$", re.MULTILINE)
_READY_SECTION_BODY_RE = re.compile(
    r"^##\s+(?:Ready to close|Ready for review)\s*$(.*?)(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)
_READY_VALUE_RE = re.compile(r"^(yes|no)\b(.*)$", re.IGNORECASE | re.DOTALL)
# An unfilled choice ("yes | no", "yes/no", "yes or no") is not a value.
_READY_ALTERNATION_RE = re.compile(r"^\s*(?:[|/]|or\b)", re.IGNORECASE)


def has_readiness_section(content: str) -> bool:
    """Whether a ``## Ready for review`` / ``## Ready to close`` heading exists."""
    return bool(
        _READY_SECTION_RE.search(content)
        or _READY_TO_CLOSE_SECTION_RE.search(content)
    )


def extract_ready_for_review(content: str) -> Optional[bool]:
    """The canonical readiness value under the report's readiness section.

    This is the single source of truth for every surface that reads the
    value — the report-action router, validate-report's readiness check, and
    the task-closure review bootstrap (review-context, write-prompt,
    handoff preflight). The value is the leading ``yes``/``no`` token of the
    section's first line; the documented skeleton permits a short same-line
    rationale after the token (``yes — producer work and evidence are
    complete``). An unfilled placeholder alternation is not a value. Under
    required task-closure review, ``yes`` declares the producer's own work
    complete and routes the task into independent review — it is not
    self-approval of closure; ``no`` is for genuinely incomplete or blocked
    work.
    """
    match = _READY_SECTION_BODY_RE.search(content)
    if match is None:
        return None
    body = match.group(1).strip()
    if not body:
        return None
    value = _READY_VALUE_RE.match(body.splitlines()[0].strip())
    if value is None or _READY_ALTERNATION_RE.match(value.group(2)):
        return None
    return value.group(1).lower() == "yes"


def extract_routing_status(content: str) -> Optional[str]:
    """The report's ``Status:`` token iff it is a valid routing status.

    Shared with the review bootstrap so acceptance and review binding read
    one predicate: the first non-empty ``Status:`` header (the same line
    ``_extract_status`` reads), gated to the closed routing vocabulary.
    """
    raw = _extract_status(content)
    return raw if raw in STATUS_VERDICT else None


def _infer_variant(report_path: Path, content: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (variant, error_message). variant is None on conflict or unresolvable.

    Task-scoped report filenames are authoritative (CONVENTIONS § Reports):
    ``REPORT-NN-NNN.md`` carries only task completion and
    ``REPORT-NN-NNN-review.md`` only task-review completion. Content of the
    other shape at either path is a path/variant mismatch — neither artifact
    can satisfy the other's completion signal, and no explicit override
    bypasses the filename contract. A review report carries a ``Review ID:``
    and a ``## Verdict`` section; a task report carries a ``## Ready to
    close`` section (or its legacy alias). The assignee handoff is
    deidentified, so a task report carries no ``Task ID:`` — its readiness
    section is the distinguishing signal, and a review report may legitimately
    cite a reviewed ``Task ID:``. Content inference survives only for names
    outside the task-scoped grammar.
    """
    filename_is_plan = report_path.name.startswith("REPORT-PLAN-")
    filename_is_review = bool(
        report_identity.TASK_REVIEW_REPORT_RE.match(report_path.name)
    )
    filename_is_completion = bool(
        report_identity.TASK_COMPLETION_REPORT_RE.match(report_path.name)
    )
    has_review_id = "Review ID:" in content
    has_task_id = "Task ID:" in content
    review_shaped = has_review_id and bool(_VERDICT_SECTION_RE.search(content))
    task_shaped = bool(
        _READY_SECTION_RE.search(content) or _READY_TO_CLOSE_SECTION_RE.search(content)
    )

    _ambiguous = (
        "ambiguous variant: filename and content disagree; "
        "pass --variant explicitly"
    )

    if filename_is_plan:
        if has_task_id and not has_review_id:
            return None, _ambiguous
        return "planning-review", None

    if filename_is_review:
        # The review filename is authoritative for its slot; task-completion
        # content there cannot satisfy the review signal. A report shaped as
        # *both* (a verdict *and* a readiness section) stays genuinely
        # ambiguous rather than silently adopting the slot's variant.
        if review_shaped and task_shaped:
            return None, _ambiguous
        if task_shaped:
            return None, (
                "path/variant mismatch: task-completion content occupies the "
                "task-review report path (REPORT-NN-NNN-review.md)"
            )
        return "review", None

    if filename_is_completion:
        # Symmetric direction: the unmarked filename is the completion slot;
        # review-shaped bytes there cannot satisfy the completion signal.
        if review_shaped and task_shaped:
            return None, _ambiguous
        if review_shaped:
            return None, (
                "path/variant mismatch: task-review content occupies the "
                "task-completion report path (REPORT-NN-NNN.md)"
            )
        return "task", None

    if review_shaped and task_shaped:
        return None, _ambiguous
    if review_shaped:
        return "review", None
    if task_shaped:
        return "task", None
    if has_review_id and has_task_id:
        return None, _ambiguous
    if has_review_id:
        return "review", None
    if has_task_id:
        return "task", None
    return None, "cannot infer variant; pass --variant {task|review|planning-review}"


def _extract_status(content: str) -> Optional[str]:
    for match in re.finditer(r"^Status:\s*(.*)$", content, re.MULTILINE):
        value = match.group(1).strip()
        if value:
            return value
    return None


def _extract_review_verdict(content: str) -> Optional[str]:
    """Return the first valid token under `## Verdict`, or None if missing/unrecognized."""
    match = re.search(
        r"^##\s+Verdict\s*$(.*?)(?=^##\s|\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        return None
    body = match.group(1).strip()
    if not body:
        return None
    first_line = body.splitlines()[0].strip()
    if first_line in REVIEW_VERDICT_OUTCOME:
        return first_line
    return None


def _schema_ok(variant: str, content: str) -> bool:
    def has_heading(section: str) -> bool:
        heading = section.removeprefix("## ")
        return bool(
            re.search(rf"^##\s+{re.escape(heading)}\s*$", content, re.MULTILINE)
        )

    for section in REQUIRED_SECTIONS[variant]:
        if not has_heading(section):
            return False
    for alternatives in REQUIRED_ANY_SECTIONS[variant]:
        if not any(has_heading(section) for section in alternatives):
            return False
    for key in REQUIRED_IDENTITY_KEYS[variant]:
        if key not in content:
            return False
    return True


def _identity_value(content: str, key: str) -> Optional[str]:
    match = re.search(
        rf"^\s*-\s*{re.escape(key)}:\s*(.*?)\s*$",
        content,
        re.MULTILINE,
    )
    if match is None:
        return None
    value = match.group(1).strip().strip("`").strip()
    return value or None


def review_alignment_record(
    report_path: Path, content: str, variant: str
) -> Optional[dict]:
    """Resolve current evidence and classify a review report's alignment.

    Both report parsers consume this helper.  It recomputes the same prompt
    binding used at dispatch, so a request-record/prompt change between
    launch and completion cannot turn into accepted review evidence.
    """
    if variant not in REVIEW_VARIANTS:
        return None
    root = request_trace.find_project_root(report_path)
    if root is None:
        return {
            "value": None,
            "reason": None,
            "present": False,
            "blocking": True,
            "detail": "request-trace project context cannot be resolved",
            "context_identity": None,
            "evidence": [],
        }
    prompt_value = _identity_value(content, "Prompt path")
    if prompt_value is None:
        return {
            "value": None,
            "reason": None,
            "present": False,
            "blocking": True,
            "detail": "review report declares no Prompt path for binding preflight",
            "context_identity": None,
            "evidence": [],
        }
    prompt_path = Path(prompt_value)
    if not prompt_path.is_absolute() or not prompt_path.is_file():
        return {
            "value": None,
            "reason": None,
            "present": False,
            "blocking": True,
            "detail": f"review prompt is missing or not absolute: {prompt_value}",
            "context_identity": None,
            "evidence": [],
        }
    try:
        prompt_text = request_trace.read_contained_text(
            root, prompt_path, what="review prompt"
        )
        if not request_trace.review_contract_applies(prompt_text):
            return None
        if variant == "review":
            task_value = _identity_value(content, "Task path")
            if task_value is None:
                raise request_trace.RequestRefusal(
                    "missing-review-target",
                    "task review report declares no Task path",
                    "regenerate the review report from the bound prompt",
                )
            task_path = Path(task_value)
            if not task_path.is_absolute() or not task_path.is_file():
                raise request_trace.RequestRefusal(
                    "missing-review-target",
                    f"task review target is missing or not absolute: {task_value}",
                    "regenerate the review report from the bound prompt",
                )
            context = request_trace.context_for_task(
                root,
                task_path,
                prompt_text=prompt_text,
            )
        else:
            checkpoint_id = prompt_path.stem.removeprefix("PROMPT-")
            context = request_trace.context_for_checkpoint(
                root,
                checkpoint_id,
                checkpoint_text=prompt_text,
            )
        preflight = request_trace.preflight_prompt_binding(context, prompt_text)
        if not preflight["ok"]:
            raise request_trace.RequestRefusal(
                preflight["rule"],
                preflight["detail"],
                preflight.get("recovery", ""),
            )
    except request_trace.RequestRefusal as refusal:
        return {
            "value": None,
            "reason": None,
            "present": False,
            "blocking": True,
            "detail": f"{refusal.rule}: {refusal.detail}",
            "context_identity": None,
            "evidence": [],
        }
    alignment = request_trace.parse_alignment(
        content,
        expected_evidence=[
            item.record_id for item in context.evidence
        ],
        legacy=context.legacy,
    )
    return {
        "value": alignment["value"],
        "reason": alignment["reason"],
        "evidence": alignment["evidence"],
        "present": alignment["present"],
        "blocking": alignment["blocking"],
        "detail": alignment["detail"],
        "context_identity": context.context_identity,
        "evidence": [
            item.record_id for item in context.evidence
        ],
    }


def _source_evidence_record(
    report_path: Path, content: str, variant: str, status: Optional[str]
) -> Optional[dict]:
    """Resolve source evidence for a complete task report when its task exists."""
    if variant != "task" or status != "complete":
        return None
    match = report_identity.TASK_COMPLETION_REPORT_RE.match(report_path.name)
    root = request_trace.find_project_root(report_path)
    if match is None or root is None:
        return None
    task_id = f"TASK-{match.group(1)}"
    for task_status in ("open", "in-progress", "in-review", "done"):
        directory = root / "tasks" / task_status
        direct = directory / f"{task_id}.md"
        candidates = [direct] if direct.is_file() else sorted(directory.glob(f"{task_id}-*.md"))
        for candidate in candidates:
            if candidate.is_file():
                try:
                    return source_guidance.resolve_report_evidence(
                        candidate.resolve(), content
                    )
                except (OSError, UnicodeError, ValueError) as exc:
                    return {
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
    return None


def handler(args: argparse.Namespace) -> int:
    raw_path = args.report_path
    if not Path(raw_path).is_absolute():
        stderr_usage(f"report_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE
    report_path = Path(raw_path)
    if not report_path.exists():
        stderr_error(f"report not found: {raw_path}")
        return EXIT_FAIL
    report_path = report_path.resolve()

    try:
        content = report_path.read_text(encoding="utf-8")
    except OSError:
        content = ""

    if args.variant:
        # An explicit variant cannot bypass the filename contract: a
        # grammar-matching task-scoped or planning filename mandates its
        # variant (report_identity.filename_contract_variant).
        contract_variant = report_identity.filename_contract_variant(
            report_path.name
        )
        if contract_variant is not None and args.variant != contract_variant:
            stderr_usage(
                f"path/variant mismatch: --variant {args.variant} contradicts "
                f"the filename contract for {report_path.name} "
                f"(mandates {contract_variant})"
            )
            return EXIT_USAGE
        variant = args.variant
    else:
        variant, err = _infer_variant(report_path, content)
        if variant is None:
            stderr_usage(err)
            return EXIT_USAGE

    review_verdict: Optional[str] = None
    raw_status = _extract_status(content)
    status_value: Optional[str] = (
        raw_status if raw_status in STATUS_VERDICT else None
    )
    if not _schema_ok(variant, content):
        verdict = "failed-to-parse"
    else:
        if raw_status is None or raw_status not in STATUS_VERDICT:
            verdict = "failed-to-parse"
            status_value = None
        else:
            status_value = raw_status
            if variant in REVIEW_VARIANTS:
                raw_verdict = _extract_review_verdict(content)
                if raw_status == "complete":
                    if raw_verdict is None:
                        verdict = "failed-to-parse"
                        status_value = None
                    else:
                        verdict = REVIEW_VERDICT_OUTCOME[raw_verdict]
                        review_verdict = raw_verdict
                else:
                    verdict = STATUS_VERDICT[raw_status]
                    review_verdict = raw_verdict
            else:
                verdict = STATUS_VERDICT[raw_status]

    # Request alignment travels with review-shaped reports. It is
    # surfaced, not inferred: an approving review report whose alignment is
    # `drifted` or missing evidence cannot be accepted completion evidence,
    # because agreement among management artifacts
    # must not by itself produce approval. Task reports carry no alignment —
    # alignment is the reviewer's recorded judgement, not the assignee's.
    alignment_record = review_alignment_record(report_path, content, variant)
    if (
        verdict == "accepted"
        and alignment_record is not None
        and alignment_record["blocking"]
    ):
        verdict = "failed-to-parse"
    source_evidence_record = _source_evidence_record(
        report_path, content, variant, status_value
    )
    if (
        verdict == "accepted"
        and source_evidence_record is not None
        and source_evidence_record["outcome"] == "invalid"
    ):
        verdict = "failed-to-parse"

    record = {
        "verdict": verdict,
        "variant": variant,
        "report_path": str(report_path),
        "status": status_value,
        "review_verdict": review_verdict,
        "request_alignment": alignment_record,
        "source_evidence": source_evidence_record,
    }
    emit_record(record)
    return EXIT_OK
