"""Mediated prompt writer with generated intake-to-review context."""
import argparse
from pathlib import Path
from typing import Optional

from cli.commands import _writers
from cli.request_trace import (
    CHECKPOINT_ID_RE, PHASE_ID_RE, PLAN_REF_RE, REVIEW_KINDS, RequestRefusal,
    context_for_checkpoint, context_for_task, upsert_request_sections,
)


def configure_parser(parser: argparse.ArgumentParser) -> None:
    _writers.add_content_args(parser)
    parser.add_argument("--prompt-id", required=True)
    parser.add_argument("--review-kind", default=None, choices=list(REVIEW_KINDS), help="Generate separated original-request and PM-derived channels")
    parser.add_argument("--task", default=None)
    parser.add_argument("--checkpoint", default=None)
    parser.add_argument("--phase", default=None)
    parser.add_argument("--plan-ref", default=None)


def handler(args: argparse.Namespace) -> int:
    if not _writers.PROMPT_ID_RE.fullmatch(args.prompt_id):
        _writers.stderr("usage", "--prompt-id has invalid grammar")
        return _writers.EXIT_USAGE
    variant = "planning" if args.prompt_id.startswith("PROMPT-PLAN-") else "task"
    content: Optional[object] = None
    details = {"prompt_id": args.prompt_id, "variant": variant}
    if args.review_kind:
        root, error = _writers.validated_root(args.project_root)
        body, body_error = _writers.resolve_content(args)
        if error or body_error:
            _writers.stderr("usage", error or body_error or "invalid input")
            return _writers.EXIT_USAGE
        if isinstance(body, bytes):
            try:
                body = body.decode("utf-8")
            except UnicodeDecodeError:
                _writers.stderr("usage", "review prompt must be valid UTF-8")
                return _writers.EXIT_USAGE
        try:
            if args.review_kind == "task-closure":
                task = Path(args.task or "")
                if not task.is_absolute() or not task.is_file():
                    raise RequestRefusal("missing-review-target", "--task must name an existing absolute task")
                context = context_for_task(root, task.resolve())
            else:
                if not args.checkpoint or not CHECKPOINT_ID_RE.fullmatch(args.checkpoint):
                    raise RequestRefusal("missing-review-target", "--checkpoint must match PLAN-NNN[-slug]")
                context = context_for_checkpoint(root, args.checkpoint, phase_id=args.phase, plan_ref=args.plan_ref)
        except RequestRefusal as refusal:
            _writers.stderr("guard", f"{refusal.rule}: {refusal.detail}")
            return _writers.EXIT_FAIL
        content = upsert_request_sections(body, context.section)
        details.update({
            "review_kind": context.review_kind,
            "request_context_identity": context.context_identity,
            "request_evidence": context.evidence_ids,
            "request_state": "unavailable-for-legacy" if context.legacy else "resolved",
            "request_measures": context.as_record()["measures"],
        })
    return _writers.perform_write(args, action="write-prompt", dest_kind="prompt", relative_target=f"{args.prompt_id}.md", content=content, extra_details=details)
