"""Mediated prompt writer with generated intake-to-review context."""
import argparse
import datetime
from pathlib import Path
from typing import Optional

from cli import prompt_evidence, trace_binding
from cli.commands import _writers
from cli.request_trace import (
    CHECKPOINT_ID_RE, PHASE_ID_RE, PLAN_REF_RE, REVIEW_KINDS, RequestRefusal,
    context_for_checkpoint, context_for_task, context_for_task_assignment,
    upsert_request_sections,
)


def configure_parser(parser: argparse.ArgumentParser) -> None:
    _writers.add_content_args(parser)
    parser.add_argument("--prompt-id", required=True)
    parser.add_argument("--review-kind", default=None, choices=list(REVIEW_KINDS), help="Generate separated original-request and PM-derived channels")
    parser.add_argument("--task", default=None)
    parser.add_argument("--checkpoint", default=None)
    parser.add_argument("--phase", default=None)
    parser.add_argument("--plan-ref", default=None)


def _append_trace_projection(
    root: Path, task: Path, content: str, details: dict, *, audience: str
) -> str:
    """Append the audience's trace projection, or refuse to issue the prompt.

    The assignment seam carries the coder projection: complete, bounded, and
    carrying no governance identity. The review-context seam carries the
    reviewer provenance block: PM-computed, independently attributable, and
    carrying the full typed record set and coverage results.

    A task that declares the contract and whose trace is structurally invalid
    does not get a prompt at all — that is what fail-closed at the detecting
    boundary means, and it is why an invalid trace never reaches a coder.
    """
    binding = trace_binding.bind(root, task)
    if binding.refusal is not None:
        raise RequestRefusal(binding.refusal.code, binding.refusal.detail)
    if binding.trace is None:
        details["upstream_trace"] = {"declaration": binding.declaration}
        return content
    section = (
        trace_binding.coder_section(binding.trace)
        if audience == "coder"
        else trace_binding.reviewer_section(binding.trace)
    )
    heading = (
        trace_binding.CODER_SECTION_HEADING
        if audience == "coder"
        else trace_binding.REVIEWER_SECTION_HEADING
    )
    details["upstream_trace"] = {
        "declaration": binding.declaration,
        "audience": audience,
        "trace_identity": binding.trace.trace_identity(),
        "bounds": binding.trace.bounds(),
    }
    return trace_binding.upsert_section(content, heading, section)


def _capture_prompt_size(root: Path, prompt_id: str, content) -> dict:
    """Record the prompt's exact byte count at the one boundary that can.

    Every other family can in principle be rebuilt from retained artifacts.
    This one cannot: the prompt is deleted at approval and the journal keeps a
    content hash, not a length.
    """
    body = content if isinstance(content, bytes) else str(content).encode("utf-8")
    return prompt_evidence.record_prompt_write(
        root, prompt_id, body, datetime.date.today().isoformat()
    )


def handler(args: argparse.Namespace) -> int:
    if not _writers.PROMPT_CANONICAL_ID_RE.fullmatch(args.prompt_id):
        _writers.stderr("usage", "--prompt-id has invalid grammar")
        return _writers.EXIT_USAGE
    variant = "planning" if args.prompt_id.startswith("PROMPT-PLAN-") else "task"
    root, error = _writers.validated_root(args.project_root)
    body, body_error = _writers.resolve_content(args)
    if error or body_error:
        _writers.stderr("usage", error or body_error or "invalid input")
        return _writers.EXIT_USAGE
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8")
        except UnicodeDecodeError:
            _writers.stderr("usage", "prompt body must be valid UTF-8")
            return _writers.EXIT_USAGE
    assert root is not None
    assert isinstance(body, str)
    content: Optional[object] = body
    details = {"prompt_id": args.prompt_id, "variant": variant}
    if variant == "task" and not args.review_kind:
        try:
            task = Path(args.task or "")
            if not task.is_absolute() or not task.is_file():
                raise RequestRefusal(
                    "missing-assignment-target",
                    "task assignment prompts require --task naming an existing absolute task",
                )
            task_identity = "-".join(task.stem.split("-")[:3])
            expected_prompt = f"PROMPT-{task_identity.removeprefix('TASK-')}"
            if args.prompt_id != expected_prompt:
                raise RequestRefusal(
                    "prompt-target-mismatch",
                    "task prompt identity must match the target task identity",
                )
            from cli import numbering_contract
            refusal = numbering_contract.guard_existing_task_trace(
                root, task.resolve()
            )
            if refusal is not None:
                raise RequestRefusal(refusal[0], refusal[1])
            context = context_for_task_assignment(root, task.resolve())
            content = _append_trace_projection(
                root,
                task.resolve(),
                upsert_request_sections(body, context.section),
                details,
                audience="coder",
            )
        except RequestRefusal as refusal:
            _writers.stderr("guard", f"{refusal.rule}: {refusal.detail}")
            return _writers.EXIT_FAIL
        details.update({
            "request_kind": context.review_kind,
            "request_context_identity": context.context_identity,
            "request_evidence": context.evidence_ids,
            "request_state": "unavailable-for-legacy" if context.legacy else "resolved",
            "request_measures": context.as_record()["measures"],
        })
    elif args.review_kind:
        try:
            if args.review_kind == "task-closure":
                task = Path(args.task or "")
                if not task.is_absolute() or not task.is_file():
                    raise RequestRefusal("missing-review-target", "--task must name an existing absolute task")
                task_identity = "-".join(task.stem.split("-")[:3])
                if args.prompt_id != f"PROMPT-{task_identity.removeprefix('TASK-')}":
                    raise RequestRefusal(
                        "prompt-target-mismatch",
                        "task prompt identity must match the target task identity",
                    )
                from cli import numbering_contract
                refusal = numbering_contract.guard_existing_task_trace(
                    root, task.resolve()
                )
                if refusal is not None:
                    raise RequestRefusal(refusal[0], refusal[1])
                context = context_for_task(
                    root,
                    task.resolve(),
                    require_completion_evidence=True,
                )
            else:
                if not args.checkpoint or not CHECKPOINT_ID_RE.fullmatch(args.checkpoint):
                    raise RequestRefusal("missing-review-target", "--checkpoint must match PLAN-NNN")
                if args.prompt_id != f"PROMPT-{args.checkpoint}":
                    raise RequestRefusal(
                        "prompt-target-mismatch",
                        "planning prompt identity must match the checkpoint identity",
                    )
                context = context_for_checkpoint(root, args.checkpoint, phase_id=args.phase, plan_ref=args.plan_ref)
            content = upsert_request_sections(body, context.section)
            if args.review_kind == "task-closure":
                content = _append_trace_projection(
                    root, task.resolve(), content, details, audience="reviewer"
                )
        except RequestRefusal as refusal:
            _writers.stderr("guard", f"{refusal.rule}: {refusal.detail}")
            return _writers.EXIT_FAIL
        details.update({
            "review_kind": context.review_kind,
            "request_context_identity": context.context_identity,
            "request_evidence": context.evidence_ids,
            "request_state": "unavailable-for-legacy" if context.legacy else "resolved",
            "request_measures": context.as_record()["measures"],
        })
    if variant == "task":
        from cli import numbering_contract

        task_path = Path(args.task or "")
        if task_path.is_absolute() and task_path.is_file():
            refusal = numbering_contract.guard_task_scoped_artifact(
                root,
                task_path.resolve(),
                args.prompt_id,
                content if isinstance(content, str) else "",
            )
            if refusal is not None:
                _writers.stderr("guard", f"{refusal[0]}: {refusal[1]}")
                return _writers.EXIT_FAIL
    def _record_size(project_root: Path, written: dict) -> None:
        written["prompt_evidence"] = _capture_prompt_size(
            project_root, args.prompt_id, content
        )

    return _writers.perform_write(args, action="write-prompt", dest_kind="prompt", relative_target=f"{args.prompt_id}.md", content=content, extra_details=details, post_write=_record_size)
