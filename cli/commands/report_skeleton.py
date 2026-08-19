"""`cartopian report-skeleton <task-path> [--variant task|review]`.

Generates the machine-owned portion of a handoff report so the assignee
supplies only substantive evidence, findings, and verdicts — never
transcribed identities, paths, evidence tokens, or boilerplate. The PM
includes the returned skeleton in the handoff prompt *instead of* the full
report template: the skeleton carries exactly the sections applicable to
this task (source evidence only when the task is source-backed, deliverable
sections only when one is declared, test evidence only when the evidence
gate requires it), which keeps prompt volume proportional to the work.

Read-only; no file writes. Stdlib only.
"""
import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional

from cli import report_identity, request_trace, source_guidance
from cli.commands.handoff_packet import _extract_task_id, _find_project_root
from cli.commands.resolve_config import (
    _CliError,
    _load_toml,
    _resolve_deliverable,
)
from cli.commands.validate_task_readiness import _parse_headers
from cli.config_schema import MACHINE_RECORD_SCHEMA_VERSION
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

VARIANTS = ("task", "review")


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.description = (
        "Generate the machine-owned report skeleton for one task handoff: "
        "identities, paths, applicable sections, and the assignee-facing "
        "source-evidence rows. Include the skeleton in the handoff prompt "
        "instead of the full report template; the assignee fills in only "
        "substantive evidence, findings, and verdicts."
    )
    subparser.add_argument(
        "task_path",
        help="Absolute path to the task file",
    )
    subparser.add_argument(
        "--variant",
        choices=list(VARIANTS),
        default=None,
        help=(
            "Report variant to generate; defaults to review for an "
            "in-review task and task otherwise"
        ),
    )


def _placeholder_or_value(raw: str, fallback: str = "n/a") -> str:
    value = (raw or "").strip()
    return value if value else fallback


def _source_evidence_section(guidance: Dict[str, Any]) -> List[str]:
    projection = source_guidance.assignee_projection(guidance)
    rendered = source_guidance.render_guidance(
        projection, heading="Source evidence"
    )
    heading, _, body = rendered.partition("\n")
    return [
        heading,
        "",
        "Every row below is transcribed from the governing guidance — do not "
        "edit identities or applicable contexts. Delete the rows for sources "
        "you did not actually apply, and record any remaining unverified "
        "claims; a decisive claim may not remain unverified in a complete "
        "report.",
        "",
        body.strip(),
        "",
    ]


def _task_skeleton(
    headers: Dict[str, str],
    guidance: Dict[str, Any],
    deliverable: Optional[Dict[str, Any]],
) -> str:
    lines: List[str] = [
        "Status: <complete | blocked | failed>",
        "",
        "## Identity",
        "",
        f"- Work root: {_placeholder_or_value(headers.get('Work root', ''))}",
        "",
        "## Completion evidence",
        "",
        "<concrete, verifiable evidence that the outcome exists>",
        "",
    ]
    if guidance["outcome"] == "valid":
        lines.extend(_source_evidence_section(guidance))
    if deliverable is not None and deliverable["mode"] == "project":
        lines.extend(
            [
                "## Deliverable",
                "",
                "- n/a",
                "",
                "## Deliverable content",
                "",
                "<paste the complete work product here; it is persisted to "
                "its durable location after this report is accepted>",
                "",
            ]
        )
    elif deliverable is not None:
        destination = deliverable.get("absolute_path") or (
            f"<absolute path of {deliverable['relpath']} inside the "
            f"{deliverable['root']} work root>"
        )
        lines.extend(
            [
                "## Deliverable",
                "",
                f"- {destination}",
                "",
            ]
        )
    gate = headers.get("Evidence gate", headers.get("Test gate", "")).strip()
    if gate == "required":
        lines.extend(
            [
                "## Test evidence",
                "",
                "- Red test evidence: <pointer to the failing check before "
                "the change>",
                "- Green test evidence: <pointer to the passing check after "
                "the change>",
                "",
            ]
        )
    lines.extend(
        [
            "## Remaining risks",
            "",
            "<known risks, edge cases, or follow-up work — or none.>",
            "",
            "## Ready to close",
            "",
            "<yes | no>",
        ]
    )
    return "\n".join(lines) + "\n"


def _review_request_bindings(
    project_root: Path, task_path: Path
) -> Dict[str, Optional[str]]:
    """Machine-resolved request-trace tokens for a review skeleton.

    The evidence identities and context identity are mechanical facts of the
    bound trace; only the alignment judgment belongs to the reviewer. When
    the trace cannot resolve yet, placeholders are returned instead of a
    refusal — skeleton generation is a composition aid, and the dispatch
    preflight remains the authoritative gate.
    """
    try:
        context = request_trace.context_for_task(
            project_root,
            task_path,
            require_completion_evidence=True,
        )
    except request_trace.RequestRefusal:
        return {"evidence": None, "context_identity": None}
    evidence_ids = [item.record_id for item in context.evidence]
    return {
        "evidence": ", ".join(evidence_ids) if evidence_ids else "none",
        "context_identity": context.context_identity,
    }


def _review_skeleton(
    identity: Dict[str, str],
    bindings: Dict[str, Optional[str]],
) -> str:
    evidence_value = bindings["evidence"] or "<ordered evidence identities | none>"
    lines: List[str] = [
        f"# {identity['report_id']}",
        "",
        "Status: <complete | blocked | failed>",
        "Request alignment: <aligned | drifted>",
        f"Request evidence: {evidence_value}",
        "",
        "## Identity",
        "",
        f"- Review ID: {identity['review_id']}",
        f"- Prompt path: {identity['prompt_path']}",
        f"- Task path: {identity['task_path']}",
        f"- Review file path: {identity['review_path']}",
        "",
        "## Evidence reviewed",
        "",
        "<what was inspected: the preserved completion report, code, specs, "
        "test results, and the bound verbatim request context against the "
        "separate PM-derived guidance>",
        "",
        "## Verdict",
        "",
        "<approve | request-changes | reject>",
        "",
        "## Blocking findings",
        "",
        "<blocking findings, or \"none.\">",
    ]
    return "\n".join(lines) + "\n"


def _review_file_skeleton(
    identity: Dict[str, str],
    headers: Dict[str, str],
    bindings: Dict[str, Optional[str]],
    source_backed: bool,
) -> str:
    evidence_value = bindings["evidence"] or "<ordered evidence identities | none>"
    context_identity = bindings["context_identity"] or "<sha256:...>"
    lines: List[str] = [
        f"# {identity['review_id']}",
        "",
        f"Target: {identity['task_id']}",
        f"Plan ref: {_placeholder_or_value(headers.get('Plan ref', ''))}",
        f"Work root: {_placeholder_or_value(headers.get('Work root', ''))}",
        "Reviewer: <free text>",
        "Verdict: <approve | request-changes | reject>",
        "Request alignment: <aligned | drifted>",
        f"Request evidence: {evidence_value}",
        f"Request-context identity: {context_identity}",
        "",
        "## Summary",
        "",
        "<two lines: what was reviewed, and what the verdict rests on>",
        "",
        "## Request comparison",
        "",
        "<why the outcome is aligned with — or drifted from — the verbatim "
        "original request, compared against the separate PM-derived guidance>",
        "",
        "## Implementation evidence",
        "",
        "<commit/PR/acceptance evidence, or n/a per field for non-repo work>",
        "",
        "## Source evidence review",
    ]
    if source_backed:
        lines.extend(
            [
                "",
                "<compare the governing source guidance with the completion "
                "report's Source evidence: identities and contexts must come "
                "from the guidance, conflicts resolved by the declared rule, "
                "and decisive claims verified>",
            ]
        )
    else:
        lines.extend(["", "n/a — task is not source-backed"])
    lines.extend(
        [
            "",
            "## Findings",
            "",
            "<each finding with a severity: blocker | major | minor — or "
            "\"none.\">",
        ]
    )
    return "\n".join(lines) + "\n"


def handler(args: argparse.Namespace) -> int:
    raw_path = args.task_path
    if not Path(raw_path).is_absolute():
        stderr_usage(f"task_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE

    task_path = Path(raw_path)
    if not task_path.is_file():
        stderr_error(f"task file not found: {raw_path}")
        return EXIT_FAIL
    task_path = task_path.resolve()

    try:
        content = task_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        stderr_error(f"task file unreadable: {raw_path} — {exc}")
        return EXIT_FAIL

    project_root = _find_project_root(task_path)
    if project_root is None:
        stderr_error(f"project config not found for task: {raw_path}")
        return EXIT_ENV

    variant = args.variant or (
        "review" if task_path.parent.name == "in-review" else "task"
    )
    headers, _presence = _parse_headers(content)
    task_id = _extract_task_id(task_path) or task_path.stem
    nn_nnn = task_id.removeprefix("TASK-") if task_id.startswith("TASK-") else task_id

    guidance = source_guidance.resolve_task_guidance(task_path, content=content)
    if guidance["outcome"] == "invalid":
        for blocker in guidance["blockers"]:
            stderr_guard(
                f"{blocker['code']}: {blocker['detail']} — {blocker['recovery']}"
            )
        return EXIT_FAIL

    try:
        project_cfg = _load_toml(
            project_root / "cartopian.toml", "project config"
        ) or {}
        deliverable = _resolve_deliverable(
            project_cfg, project_root, headers.get("Deliverable", "")
        )
    except _CliError as err:
        stderr_error(err.message)
        return err.exit_code

    completion_report_path = report_identity.completion_report_path(
        project_root, nn_nnn
    ).resolve()
    review_report_path = report_identity.review_report_path(
        project_root, nn_nnn
    ).resolve()
    source_backed = guidance["outcome"] == "valid"

    if variant == "task":
        skeleton = _task_skeleton(headers, guidance, deliverable)
        expected_report_path = completion_report_path
        review_file_skeleton = None
        machine_fields: Dict[str, Any] = {
            "work_root": _placeholder_or_value(headers.get("Work root", "")),
        }
    else:
        identity = {
            "task_id": task_id,
            "report_id": f"REPORT-{nn_nnn}-review",
            "review_id": f"REVIEW-{nn_nnn}",
            "prompt_path": str(
                (project_root / "prompts" / f"PROMPT-{nn_nnn}.md").resolve()
            ),
            "task_path": str(task_path),
            "review_path": str(
                (project_root / "reviews" / f"REVIEW-{nn_nnn}.md").resolve()
            ),
        }
        bindings = _review_request_bindings(project_root, task_path)
        skeleton = _review_skeleton(identity, bindings)
        review_file_skeleton = _review_file_skeleton(
            identity, headers, bindings, source_backed
        )
        expected_report_path = review_report_path
        machine_fields = {**identity, "request_evidence": bindings["evidence"]}

    emit_record(
        {
            "record_schema_version": MACHINE_RECORD_SCHEMA_VERSION,
            "task_id": task_id,
            "task_path": str(task_path),
            "variant": variant,
            "expected_report_path": str(expected_report_path),
            "completion_report_path": str(completion_report_path),
            "source_backed": source_backed,
            "machine_fields": machine_fields,
            "skeleton": skeleton,
            "review_file_skeleton": review_file_skeleton,
        }
    )
    return EXIT_OK
