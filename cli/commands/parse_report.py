"""`cartopian parse-report <report-path>`."""
import argparse
import re
import tomllib
from pathlib import Path
from typing import Optional, Tuple

from cli import operator_intent
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
        help="Explicit variant; overrides filename/content inference",
    )


_VERDICT_SECTION_RE = re.compile(r"^##\s+Verdict\s*$", re.MULTILINE)
_READY_SECTION_RE = re.compile(r"^##\s+Ready for review\s*$", re.MULTILINE)
_READY_TO_CLOSE_SECTION_RE = re.compile(r"^##\s+Ready to close\s*$", re.MULTILINE)


def _infer_variant(report_path: Path, content: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (variant, error_message). variant is None on conflict or unresolvable.

    Task and task-review completion reports share the ``REPORT-NN-NNN.md`` name
    (CONVENTIONS § Reports), so the filename alone cannot decide between them.
    Resolve ``task`` vs ``review`` from report *content*: a review report carries
    a ``Review ID:`` and a ``## Verdict`` section; a task report carries a
    ``## Ready to close`` section (or its legacy alias). The assignee handoff is
    deidentified, so a task report carries no ``Task ID:`` — its readiness section
    is the distinguishing signal. A review report may legitimately cite a reviewed
    ``Task ID:``, so that string never marks a report as a task report. Only a
    report shaped as *both* (a verdict *and* a readiness section) is
    genuinely ambiguous.
    """
    filename_is_plan = report_path.name.startswith("REPORT-PLAN-")
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


def _alignment_enforced_for(report_path: Path) -> bool:
    """True when the enclosing project has migrated to the alignment contract.

    Reports outside any project, and projects below the schema version that
    introduced the contract, keep their historical behavior — no historical
    review is rewritten and no legacy report retroactively fails to parse.
    """
    root = operator_intent.find_project_root(report_path)
    if root is None:
        return False
    try:
        with (root / "cartopian.toml").open("rb") as handle:
            declared = (tomllib.load(handle).get("project", {}) or {}).get(
                "project_schema_version"
            )
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return operator_intent.alignment_enforced(declared)


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
    binding used at dispatch, so a source/attestation/prompt change between
    launch and completion cannot turn into accepted review evidence.
    """
    if variant not in REVIEW_VARIANTS or not _alignment_enforced_for(report_path):
        return None
    root = operator_intent.find_project_root(report_path)
    if root is None:
        return {
            "value": None,
            "reason": None,
            "present": False,
            "blocking": True,
            "detail": "operator-intent project context cannot be resolved",
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
        prompt_text = operator_intent.read_contained_text(
            root, prompt_path, what="review prompt"
        )
        if variant == "review":
            task_value = _identity_value(content, "Task path")
            if task_value is None:
                raise operator_intent.IntentRefusal(
                    "missing-review-target",
                    "task review report declares no Task path",
                    "regenerate the review report from the bound prompt",
                )
            task_path = Path(task_value)
            if not task_path.is_absolute() or not task_path.is_file():
                raise operator_intent.IntentRefusal(
                    "missing-review-target",
                    f"task review target is missing or not absolute: {task_value}",
                    "regenerate the review report from the bound prompt",
                )
            context = operator_intent.context_for_task(
                root,
                task_path,
                operator_intent.artifact_supplemental_refs(prompt_text),
            )
        else:
            checkpoint_id = prompt_path.stem.removeprefix("PROMPT-")
            context = operator_intent.context_for_checkpoint(
                root,
                checkpoint_id,
                checkpoint_text=prompt_text,
            )
        preflight = operator_intent.preflight_prompt_binding(context, prompt_text)
        if not preflight["ok"]:
            raise operator_intent.IntentRefusal(
                preflight["rule"],
                preflight["detail"],
                preflight.get("recovery", ""),
            )
    except operator_intent.IntentRefusal as refusal:
        return {
            "value": None,
            "reason": None,
            "present": False,
            "blocking": True,
            "detail": f"{refusal.rule}: {refusal.detail}",
            "context_identity": None,
            "evidence": [],
        }
    alignment = operator_intent.parse_alignment(
        content,
        required_evidence=any(item.required for item in context.evidence),
        expected_evidence=[
            item.attestation.attestation_id for item in context.evidence
        ],
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
            item.attestation.attestation_id for item in context.evidence
        ],
    }


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

    # Operator-intent alignment travels with review-shaped reports. It is
    # surfaced, not inferred: an approving review report whose alignment is
    # `drifted`, missing, or a non-`none recorded` `not assessable` cannot be
    # accepted completion evidence, because agreement among management artifacts
    # must not by itself produce approval. Task reports carry no alignment —
    # alignment is the reviewer's recorded judgement, not the assignee's.
    alignment_record = review_alignment_record(report_path, content, variant)
    if (
        verdict == "accepted"
        and alignment_record is not None
        and alignment_record["blocking"]
    ):
        verdict = "failed-to-parse"

    record = {
        "verdict": verdict,
        "variant": variant,
        "report_path": str(report_path),
        "status": status_value,
        "review_verdict": review_verdict,
        "operator_intent_alignment": alignment_record,
    }
    emit_record(record)
    return EXIT_OK
