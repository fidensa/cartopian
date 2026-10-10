"""`cartopian report-skeleton <task-path> [--variant task|review]`.

Generates the machine-owned portion of a handoff report so the assignee
supplies only substantive evidence, findings, and verdicts — never
transcribed identities, paths, evidence tokens, or boilerplate. The prompt
writers embed these skeletons *instead of* the full report template — the
assignment composer under ``## Completion report``, and ``write-prompt
--review-kind`` in its generated review-file and review-report sections
(``render_review_prompt_sections``) — so no PM pastes one. The skeleton
carries exactly the sections applicable to this task (source evidence only
when the task is source-backed, deliverable sections only when one is
declared, test evidence only when the evidence gate requires it), which
keeps prompt volume proportional to the work. This command exposes the same
task-scoped builders for inspection.

Read-only; no file writes. Stdlib only.
"""
import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional

from cli import (
    acceptance_trace,
    checkpoint_identity,
    contract_review,
    report_identity,
    request_trace,
    source_guidance,
    trace_binding,
)
from cli.commands.handoff_packet import _extract_task_id, _find_project_root
from cli.commands.resolve_config import (
    _CliError,
    _load_toml,
    _resolve_deliverable,
    resolve_review_policy,
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
from cli.markdown_fences import FenceTracker

VARIANTS = ("task", "review")


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.description = (
        "Generate the machine-owned report skeleton for one task handoff: "
        "identities, paths, applicable sections, and the assignee-facing "
        "source-evidence rows. The prompt writers embed the same skeletons "
        "in assignment and review prompts automatically; the assignee fills "
        "in only substantive evidence, findings, and verdicts."
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


# The exact non-empty unverified-claim row grammar the validator enforces.
# Rendered as backtick-wrapped prose (never a `- ` bullet and never indented)
# so the instructional exemplar cannot be parsed as an evidence row.
_UNVERIFIED_CLAIM_GRAMMAR = (
    "`- Claim: <unverified claim>; Decisiveness: <decisive | non-decisive>; "
    "Missing: <authority or evidence>; Consequence: <consequence of "
    "proceeding>; Next: <decision or proof required>`"
)

_UNVERIFIED_CLAIM_INSTRUCTIONS = (
    "Keep `- none` when no claim remains unverified. Otherwise replace it "
    "with one row per remaining claim, each carrying all five fields in "
    "exactly this form:\n"
    + _UNVERIFIED_CLAIM_GRAMMAR
    + "\nA `decisive` claim may not remain unverified in a complete report; "
    "prose that is not a `- ` row is not parsed as a claim."
)


def _source_evidence_section(guidance: Dict[str, Any]) -> List[str]:
    projection = source_guidance.assignee_projection(guidance)
    rendered = source_guidance.render_guidance(
        projection, heading="Source evidence"
    )
    heading, _, body = rendered.partition("\n")
    body = body.strip().replace(
        "### Unverified claims\n",
        "### Unverified claims\n\n" + _UNVERIFIED_CLAIM_INSTRUCTIONS + "\n",
        1,
    )
    return [
        heading,
        "",
        "Every row below is transcribed from the governing guidance — do not "
        "edit identities or applicable contexts. Delete the rows for sources "
        "you did not actually apply, and record any remaining unverified "
        "claims; a decisive claim may not remain unverified in a complete "
        "report.",
        "",
        body,
        "",
    ]


def _task_skeleton(
    headers: Dict[str, str],
    guidance: Dict[str, Any],
    deliverable: Optional[Dict[str, Any]],
    task_review_required: bool,
    *,
    source_evidence_lines: Optional[List[str]] = None,
    risk_scaled_lines: Optional[List[str]] = None,
) -> str:
    """Render the machine-owned task-report skeleton.

    ``source_evidence_lines`` lets the assignment-prompt composer substitute a
    reference-form source-evidence contract (the full record is rendered once
    in the prompt's Source guidance section). ``risk_scaled_lines`` injects the
    prefilled Risk-scaled evidence section when a classified risk result
    accompanies the handoff; the standalone command has no risk input and
    omits it.
    """
    lines: List[str] = [
        "Status: <complete | blocked | failed>",
        "",
        "## Summary",
        "",
        "<PM-facing summary, at most 10 short lines: what was done, where "
        "the evidence and work product live, and anything the PM must act "
        "on. The PM routes on this section instead of re-reading the whole "
        "report; full detail belongs in the sections below.>",
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
        lines.extend(
            source_evidence_lines
            if source_evidence_lines is not None
            else _source_evidence_section(guidance)
        )
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
    if risk_scaled_lines:
        lines.extend(risk_scaled_lines)
    lines.extend(
        [
            "## Remaining risks",
            "",
            "<known risks, edge cases, or follow-up work — or none.>",
            "",
        ]
    )
    if task_review_required:
        # This project requires independent task-closure review, so the
        # producer's readiness value routes into that review — it never
        # certifies the reviewer's future verdict.
        lines.extend(
            [
                "## Ready for review",
                "",
                "<yes | no>",
                "",
                "`yes` means your own work is complete and enters the "
                "required independent closure review — it does not approve "
                "closure and does not certify the reviewer's verdict. "
                "`no` is only for genuinely incomplete or blocked work, and "
                "`Status: blocked` or `failed` requires `no`. A short "
                "rationale may follow the token on the same line.",
            ]
        )
    else:
        lines.extend(
            [
                "## Ready to close",
                "",
                "<yes | no>",
                "",
                "`yes` means your work is complete; with task-closure "
                "review off it routes the accepted task toward direct "
                "closure. `no` is only for genuinely incomplete or blocked "
                "work, and `Status: blocked` or `failed` requires `no`. A "
                "short rationale may follow the token on the same line.",
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
    return bindings_from_context(context)


def bindings_from_context(context: request_trace.ReviewContext) -> Dict[str, Any]:
    """The skeleton request-trace tokens of one resolved review context.

    The writer passes the exact context it binds into the prompt, so the
    skeleton's identities are the prompt's identities by construction.
    """
    evidence_ids = context.evidence_ids
    return {
        "evidence": ", ".join(evidence_ids) if evidence_ids else "none",
        "context_identity": context.context_identity,
        "legacy": context.legacy,
    }


def _alignment_placeholder(bindings: Dict[str, Any]) -> str:
    # A legacy context cannot claim `aligned` (parse_alignment blocks it), so
    # the skeleton offers only the values the validator can accept.
    if bindings.get("legacy"):
        return f"<{request_trace.LEGACY_STATE} | drifted>"
    return "<aligned | drifted>"


def _review_skeleton(
    identity: Dict[str, str],
    bindings: Dict[str, Optional[str]],
) -> str:
    evidence_value = bindings["evidence"] or "<ordered evidence identities | none>"
    context_identity = bindings["context_identity"] or "<sha256:...>"
    lines: List[str] = [
        f"# {identity['report_id']}",
        "",
        "Status: <complete | blocked | failed>",
        f"Request alignment: {_alignment_placeholder(bindings)}",
        f"Request evidence: {evidence_value}",
        f"Request-context identity: {context_identity}",
        "",
        "## Summary",
        "",
        "<PM-facing summary, at most 10 short lines: the verdict, what it "
        "rests on, and anything the PM must act on. The PM routes on this "
        "section plus the bounded review projection instead of re-reading "
        "the artifacts in full.>",
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
    binding: trace_binding.Binding,
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
        f"Request alignment: {_alignment_placeholder(bindings)}",
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
        contract_review.SECTION_HEADING,
        "",
        "Audit the governing operator request, task, and specification as "
        "written before evaluating implementation. Do not silently repair "
        "the contract or credit it for what the implementation does.",
        "",
        "Checks: " + "; ".join(name for _, name in contract_review.CHECKS) + ".",
        "",
        "Outcome: <" + " | ".join(contract_review.OUTCOMES) + ">",
        "",
        "<adequate means every check passes; record no gaps or only nits. "
        "For needs changes, locate each deficient clause and state what "
        "would resolve it. Do not score, count, or rank the checks. "
        "Weigh contract and implementation together when setting Verdict.>",
        "",
        "<Contract gaps use `- C1. [blocker | major | minor | nit] "
        "<check name> — <defect, location, and resolution>` here; "
        "implementation findings use F<n> under Findings. "
        "Remove this placeholder and record only actual gaps, or none.>",
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
    lines.extend(["", *_closure_lines(binding)])
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


def _closure_lines(binding: trace_binding.Binding) -> List[str]:
    """The determination output slot intake reads, or its n/a line.

    A traced task gets the determinations projection already placed under
    the heading intake parses, so the reviewer never has to choose between
    the PM provenance block and a template example. A trace that cannot be
    bound still gets the heading, with the refusal named, rather than a
    silently missing slot.
    """
    heading = acceptance_trace.DETERMINATION_SECTION_HEADING
    if binding.trace is not None:
        return trace_binding.closure_section(binding.trace).rstrip("\n").split("\n")
    if binding.refusal is not None:
        return [
            heading,
            "",
            f"<the upstream trace does not bind ({binding.refusal.code}): "
            f"{binding.refusal.detail}. The PM must repair the trace and "
            "regenerate this skeleton before determinations can be recorded.>",
        ]
    return [heading, "", "n/a — task does not declare an upstream trace"]


def task_review_skeletons(
    project_root: Path,
    task_path: Path,
    task_text: str,
    bindings: Dict[str, Any],
    *,
    guidance: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """The task-closure review report and review-file skeletons.

    The one builder behind ``report-skeleton --variant review`` and the
    review-prompt writer. ``guidance`` is the task's resolved source guidance
    (resolved here when omitted); the caller decides how to treat an invalid
    record before calling.
    """
    headers, _presence = _parse_headers(task_text)
    task_id = _extract_task_id(task_path) or task_path.stem
    nn_nnn = task_id.removeprefix("TASK-") if task_id.startswith("TASK-") else task_id
    if guidance is None:
        guidance = source_guidance.resolve_task_guidance(task_path, content=task_text)
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
    return {
        "identity": identity,
        "report_path": str(
            report_identity.review_report_path(project_root, nn_nnn).resolve()
        ),
        "review_path": identity["review_path"],
        "skeleton": _review_skeleton(identity, bindings),
        "review_file_skeleton": _review_file_skeleton(
            identity,
            headers,
            bindings,
            guidance["outcome"] == "valid",
            trace_binding.bind(project_root, task_path, task_text=task_text),
        ),
    }


# ---------------------------------------------------------------------------
# Planning-checkpoint review skeletons
# ---------------------------------------------------------------------------

_STAGE_PLACEHOLDER = "<" + " | ".join(checkpoint_identity.STAGES) + ">"


def _planning_scope_lines(scope: Dict[str, Optional[str]]) -> List[str]:
    """The review file's scope headers, machine-filled where scope is known.

    ``planning_status`` reads exactly these headers to decide which stage an
    approval covers. Canonical checkpoints carry their scope in the identity;
    a legacy counter checkpoint carries only what the prompt declared, and
    anything undeclared stays a placeholder rather than a guess.
    """
    stage = scope.get("stage")
    stages = checkpoint_identity.STAGES
    phase_required = stage is None or stage in stages[2:]
    plan_ref_required = stage is None or stage == stages[3]

    def value(raw: Optional[str], required: bool, placeholder: str) -> str:
        if raw:
            return raw
        return placeholder if required else "n/a"

    return [
        f"Planning stage: {stage or _STAGE_PLACEHOLDER}",
        f"Phase: {value(scope.get('phase'), phase_required, '<PHASE-NN | n/a>')}",
        f"Plan ref: {value(scope.get('plan_ref'), plan_ref_required, '<KIND-NN-NNN | n/a>')}",
    ]


def _planning_review_skeleton(
    identity: Dict[str, str], bindings: Dict[str, Any]
) -> str:
    evidence_value = bindings["evidence"] or "<ordered evidence identities | none>"
    context_identity = bindings["context_identity"] or "<sha256:...>"
    lines: List[str] = [
        f"# {identity['report_id']}",
        "",
        "Status: <complete | blocked | failed>",
        f"Request alignment: {_alignment_placeholder(bindings)}",
        f"Request evidence: {evidence_value}",
        f"Request-context identity: {context_identity}",
        "",
        "## Summary",
        "",
        "<PM-facing summary, at most 10 short lines: the verdict, what it "
        "rests on, and anything the PM must act on.>",
        "",
        "## Identity",
        "",
        f"- Review ID: {identity['review_id']}",
        f"- Prompt path: {identity['prompt_path']}",
        f"- Review file path: {identity['review_path']}",
        "",
        "## Evidence reviewed",
        "",
        "<what was inspected: the planning artifacts under review, and the "
        "bound verbatim request context against the separate PM-derived "
        "guidance>",
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


def _planning_review_file_skeleton(
    identity: Dict[str, str],
    scope: Dict[str, Optional[str]],
    bindings: Dict[str, Any],
) -> str:
    evidence_value = bindings["evidence"] or "<ordered evidence identities | none>"
    context_identity = bindings["context_identity"] or "<sha256:...>"
    lines: List[str] = [
        f"# {identity['review_id']}",
        "",
        f"Target: {identity['checkpoint']}",
        *_planning_scope_lines(scope),
        "Reviewer: <free text>",
        "Verdict: <approve | request-changes | reject>",
        f"Request alignment: {_alignment_placeholder(bindings)}",
        f"Request evidence: {evidence_value}",
        f"Request-context identity: {context_identity}",
        "",
        "## Summary",
        "",
        "<two lines: what was reviewed, and what the verdict rests on>",
        "",
        "## Request comparison",
        "",
        "<why the reviewed planning artifacts are aligned with — or drifted "
        "from — the verbatim original request, compared against the separate "
        "PM-derived guidance>",
        "",
        "## Findings",
        "",
        "<one row per finding, `- F1. [blocker | major | minor | nit] — "
        "<defect, artifact and section, and what would resolve it>`, or "
        "\"none.\">",
        "",
        "## Suggested actions",
        "",
        "<for request-changes or reject: what to revise before the "
        "checkpoint reruns; otherwise n/a>",
    ]
    return "\n".join(lines) + "\n"


def planning_review_skeletons(
    project_root: Path,
    checkpoint_id: str,
    bindings: Dict[str, Any],
    *,
    scope: Dict[str, Optional[str]],
) -> Dict[str, Any]:
    """The planning-checkpoint review report and review-file skeletons.

    ``scope`` carries the checkpoint's stage, phase, and plan ref as the
    prompt resolved them (``checkpoint_identity.scope``).
    """
    root = Path(project_root)
    review_path = str((root / "reviews" / f"REVIEW-{checkpoint_id}.md").resolve())
    identity = {
        "checkpoint": checkpoint_id,
        "report_id": f"REPORT-{checkpoint_id}",
        "review_id": f"REVIEW-{checkpoint_id}",
        "prompt_path": str((root / "prompts" / f"PROMPT-{checkpoint_id}.md").resolve()),
        "review_path": review_path,
    }
    return {
        "identity": identity,
        "report_path": str(
            report_identity.planning_report_path(root, checkpoint_id).resolve()
        ),
        "review_path": review_path,
        "skeleton": _planning_review_skeleton(identity, bindings),
        "review_file_skeleton": _planning_review_file_skeleton(
            identity, scope, bindings
        ),
    }


# ---------------------------------------------------------------------------
# Generated review-prompt sections
# ---------------------------------------------------------------------------

REVIEW_FILE_SECTION_HEADING = "## Review file skeleton"
REVIEW_REPORT_SECTION_HEADING = "## Review completion report skeleton"
PLANNING_EVIDENCE_SECTION_HEADING = "## Planning review evidence"
REVIEW_PROMPT_SECTION_HEADINGS = (
    PLANNING_EVIDENCE_SECTION_HEADING,
    REVIEW_FILE_SECTION_HEADING,
    REVIEW_REPORT_SECTION_HEADING,
)


def planning_review_evidence(project_root: Path) -> str:
    """The planning reviewer's machine-computed evidence, as a prompt section.

    Two inputs only the planning review receives: the plan's delivery-contract
    result with the contract section itself, and the locked decision pairs
    that are mutually near and have never referenced each other. Both are
    evidence for the reviewer's judgment, never a verdict. The section is a
    snapshot taken when the prompt is written and sits outside the
    request-context binding, so a later decision or plan edit never makes the
    prompt stale; regenerating the prompt refreshes it.
    """
    from cli import decision_neighbors, delivery_contract
    from cli.prompt_composer import _fenced

    delivery = delivery_contract.review_projection_for_plan(project_root)
    plan_path = Path(project_root).resolve() / delivery_contract.PLAN_SURFACE
    parts = [
        PLANNING_EVIDENCE_SECTION_HEADING,
        "",
        "Computed when this prompt was written. This is evidence for your "
        "judgment, not a verdict; weigh it alongside the target artifacts.",
        "",
        "### Delivery contract",
        "",
        f"Gate: {delivery.get('gate')}; artifact: {delivery.get('artifact_state')}; "
        f"outcome: {delivery.get('outcome_state')}; follow-up: "
        f"{delivery.get('follow_up_state')}.",
        "",
    ]
    findings = delivery.get("ordered_findings") or []
    if findings:
        parts.append("Open findings, in order:")
        parts.extend(f"- `{item.get('code')}`: {item.get('detail')}" for item in findings)
    else:
        parts.append("Open findings: none.")
    parts.append("")
    if isinstance(delivery.get("section"), str):
        parts += [f"The contract as declared in {plan_path}:", "", _fenced(delivery["section"], "markdown")]
    else:
        parts.append(
            f"{plan_path} carries no single delivery-contract section "
            f"({delivery.get('section_omitted')})."
        )
    parts += [
        "",
        "### Locked decisions that sit near each other",
        "",
    ]
    pairs = decision_neighbors.unreferenced_pairs(project_root)
    if pairs:
        parts += [
            "Each pair below is two live locked decisions on a close subject "
            "that have never referenced each other. Closeness is not "
            "contradiction. Check whether the work under review relies on one "
            "ruling in a way the other contradicts.",
            "",
        ]
        for pair in pairs:
            left, right = pair["decisions"]
            left_title, right_title = pair["titles"]
            parts.append(
                f"- {left} ({left_title}) / {right} ({right_title}); "
                f"shared terms: {', '.join(pair['shared_terms'])}"
            )
    else:
        parts.append("None: no live locked decisions are near each other without a cross-reference.")
    return "\n".join(parts).rstrip() + "\n"


def render_review_prompt_sections(skeletons: Dict[str, Any]) -> str:
    """The machine-owned output-format sections of one review prompt.

    Mirrors the assignment composer's ``## Completion report``: brief
    instructions, the exact output path, and the fenced skeleton, so the
    reviewer never has to look up a template or the protocol to learn the
    format.
    """
    from cli.prompt_composer import _fenced

    evidence = skeletons.get("planning_evidence")
    parts = [evidence.rstrip(), ""] if evidence else []
    parts += [
        REVIEW_FILE_SECTION_HEADING,
        "",
        f"Write the review file first, to: {skeletons['review_path']}",
        "",
        "Overwrite any earlier review at that path. Fill in the skeleton "
        "below: keep every machine-generated value (identities, paths, scope, "
        "request evidence, and the Request-context identity) exactly as "
        "given, and replace each `<...>` placeholder with your own findings "
        "and judgment. Its `Verdict:`, `Request alignment:`, `Request "
        "evidence:`, and `Request-context identity:` values must match the "
        "review completion report's.",
        "",
        _fenced(skeletons["review_file_skeleton"]),
        "",
        REVIEW_REPORT_SECTION_HEADING,
        "",
        f"Write the review completion report last, to: {skeletons['report_path']}",
        "",
        "- Fill in the skeleton below, keeping every machine-generated value "
        "exactly as given; the verdict under `## Verdict` is the review "
        "file's `Verdict:`.",
        "- Writing the report is the last thing you do. If the review cannot "
        "be finished, still write the report with `Status: blocked` and "
        "record what stopped you.",
        "- If the `cartopian` CLI is available, run `cartopian "
        "validate-report <report path>` after writing and apply the named "
        "recovery for any `mechanical` finding, and for "
        "`review-findings-projectable` (a request-changes or reject verdict "
        "needs `F<n>` rows under the review file's `## Findings`).",
        "- Do not include secrets: API keys, credentials, tokens, or private "
        "connection strings.",
        "",
        _fenced(skeletons["skeleton"]),
    ]
    return "\n".join(parts).rstrip() + "\n"


def strip_review_prompt_sections(text: str) -> str:
    """Remove every copy of the generated sections, generated or authored.

    Section bounds are fence-aware: the skeletons themselves contain ``## ``
    lines inside their fences, so a naive heading scan would end a section
    early and leave skeleton fragments behind. A section ends at the next
    ``## `` heading outside any fence.
    """
    lines = text.splitlines(keepends=True)
    tracker = FenceTracker()
    kept: List[str] = []
    skipping = False
    removed = False
    for line in lines:
        bare = line.rstrip("\r\n")
        was_in_fence = tracker.in_fence
        is_delimiter = tracker.feed(bare)
        if not was_in_fence and not is_delimiter and bare.startswith("## "):
            skipping = bare.rstrip() in REVIEW_PROMPT_SECTION_HEADINGS
            removed = removed or skipping
        if not skipping:
            kept.append(line)
    if not removed:
        return text
    stripped = "".join(kept).rstrip()
    return stripped + "\n" if stripped else ""


def upsert_review_prompt_sections(text: str, skeletons: Dict[str, Any]) -> str:
    """Replace any copies of the generated sections with one current copy.

    The sections always land at the end of the prompt, after every other
    generated section, so they never fall inside the request-context
    section bounds or the authored text the request trace reads.
    """
    body = strip_review_prompt_sections(text).rstrip()
    section = render_review_prompt_sections(skeletons)
    return (body + "\n\n" if body else "") + section


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
        try:
            review_policy = resolve_review_policy(project_root)
        except _CliError as err:
            stderr_error(err.message)
            return err.exit_code
        task_review_required = (
            review_policy["task_closure"]["mode"] == "required"
        )
        skeleton = _task_skeleton(
            headers, guidance, deliverable, task_review_required
        )
        expected_report_path = completion_report_path
        review_file_skeleton = None
        machine_fields: Dict[str, Any] = {
            "work_root": _placeholder_or_value(headers.get("Work root", "")),
            "task_review_required": task_review_required,
            "ready_heading": (
                "Ready for review" if task_review_required else "Ready to close"
            ),
        }
    else:
        bindings = _review_request_bindings(project_root, task_path)
        built = task_review_skeletons(
            project_root, task_path, content, bindings, guidance=guidance
        )
        identity = built["identity"]
        skeleton = built["skeleton"]
        review_file_skeleton = built["review_file_skeleton"]
        expected_report_path = review_report_path
        machine_fields = {
            **identity,
            "request_evidence": bindings["evidence"],
            "request_context_identity": bindings["context_identity"],
        }

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
