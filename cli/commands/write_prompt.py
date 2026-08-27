"""Mediated prompt writer with generated intake-to-review context."""
import argparse
import datetime
import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from cli import prompt_composer, prompt_evidence, trace_binding
from cli.commands import _writers
from cli.request_trace import (
    CHECKPOINT_ID_RE, PHASE_ID_RE, PLAN_REF_RE, REVIEW_KINDS, RequestRefusal,
    context_for_checkpoint, context_for_task, context_for_task_assignment,
    upsert_request_sections,
)


def configure_parser(parser: argparse.ArgumentParser) -> None:
    _writers.add_content_args(parser)
    parser.add_argument(
        "--composed-file",
        default=None,
        help=(
            "Path to a saved `cartopian compose-assignment-prompt` record "
            "(JSON or NDJSON). The writer verifies the record's content "
            "identity and validation state and writes its assignee prompt; "
            "mutually exclusive with --content/--content-file. Task "
            "assignment prompts only."
        ),
    )
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


def _load_composed_record(path: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Load and integrity-check a compose-assignment-prompt record.

    Returns ``(record, error)``. The record's prompt bytes, trace receipt, and
    binding identity are all recomputed — a hand-edited prompt or receipt no
    longer matches its recorded identity and is refused.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"cannot read --composed-file {path}: {exc}"
    record: Optional[Dict[str, Any]] = None
    try:
        candidate = json.loads(raw)
        if isinstance(candidate, dict):
            record = candidate
    except json.JSONDecodeError:
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(candidate, dict)
                and candidate.get("action") == "compose-assignment-prompt"
            ):
                record = candidate
                break
    if record is None or "assignee_prompt" not in record:
        return None, "--composed-file carries no compose-assignment-prompt record"
    if record.get("outcome") != "composed":
        return None, (
            "the composed record was refused by validation "
            f"(outcome: {record.get('outcome')!r}); recompose after fixing "
            "its findings"
        )
    prompt = record["assignee_prompt"]
    receipt = record.get("trace_receipt")
    if not isinstance(prompt, str) or not isinstance(receipt, dict):
        return None, "the composed record is structurally incomplete"
    prompt_identity = prompt_composer.sha256_identity(prompt.encode("utf-8"))
    if prompt_identity != record.get("prompt_content_identity"):
        return None, "the composed prompt no longer matches its recorded identity"
    receipt_identity = prompt_composer.canonical_identity(receipt)
    if receipt_identity != record.get("receipt_content_identity"):
        return None, "the trace receipt no longer matches its recorded identity"
    binding = prompt_composer.canonical_identity(
        {"prompt": prompt_identity, "trace_receipt": receipt_identity}
    )
    if binding != record.get("content_identity"):
        return None, "the prompt/receipt binding identity does not verify"
    return record, None


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
    composed_record: Optional[Dict[str, Any]] = None
    composed_file = getattr(args, "composed_file", None)
    if composed_file is not None:
        if (
            getattr(args, "content", None) is not None
            or getattr(args, "content_file", None) is not None
        ):
            _writers.stderr(
                "usage", "--composed-file is mutually exclusive with --content/--content-file"
            )
            return _writers.EXIT_USAGE
        if args.review_kind or variant != "task":
            _writers.stderr(
                "usage", "--composed-file applies to task assignment prompts only"
            )
            return _writers.EXIT_USAGE
        composed_record, composed_error = _load_composed_record(composed_file)
        if composed_error:
            _writers.stderr("guard", f"composed-record-invalid: {composed_error}")
            return _writers.EXIT_FAIL
        assert composed_record is not None
        body, body_error = composed_record["assignee_prompt"], None
    else:
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
    if composed_record is not None:
        supplied_task = Path(args.task) if args.task else None
        resolved_task = (
            str(supplied_task.resolve())
            if supplied_task is not None and supplied_task.is_absolute()
            else None
        )
        if resolved_task != composed_record.get("task_path"):
            _writers.stderr(
                "guard",
                "composed-target-mismatch: --task does not name the task the "
                "record was composed for",
            )
            return _writers.EXIT_FAIL
        expected_stem = Path(
            composed_record.get("expected_prompt_path", "")
        ).stem
        if expected_stem and args.prompt_id != expected_stem:
            _writers.stderr(
                "guard",
                "composed-target-mismatch: --prompt-id does not match the "
                "composed record's expected prompt identity",
            )
            return _writers.EXIT_FAIL
        # Defense in depth: the identity check proves the bytes are the
        # composed ones; this re-proves the composed ones still validate
        # against the current contract.
        try:
            fresh = prompt_composer.validate_prompt(
                body,
                prompt_composer.load_contract(),
                input_payloads=composed_record["trace_receipt"].get(
                    "input_payloads"
                ) or [],
            )
        except prompt_composer.ComposeRefusal as refusal:
            _writers.stderr("guard", f"{refusal.code}: {refusal.detail}")
            return _writers.EXIT_FAIL
        failed = [item for item in fresh if item["severity"] == "fail"]
        if failed:
            first = failed[0]
            _writers.stderr(
                "guard", f"{first['code']}: {first['detail']} — {first['recovery']}"
            )
            return _writers.EXIT_FAIL
        details["composed"] = {
            "content_identity": composed_record["content_identity"],
            "prompt_content_identity": composed_record["prompt_content_identity"],
            "receipt_content_identity": composed_record["receipt_content_identity"],
            "section_sizes": composed_record.get("section_sizes"),
        }
    elif variant == "task" and not args.review_kind:
        # A hand-assembled assignment body is held to the contamination
        # subset of the composition contract: no raw diagnostic JSON,
        # duplicated source guidance, reviewer-audience material, PM
        # lifecycle instructions, or blanket governance reads.
        try:
            contamination = prompt_composer.validate_authored_body(body)
        except prompt_composer.ComposeRefusal:
            contamination = []
        if contamination:
            first = contamination[0]
            _writers.stderr(
                "guard", f"{first['code']}: {first['detail']} — {first['recovery']}"
            )
            return _writers.EXIT_FAIL
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
            if composed_record is None:
                # The typed input-payload sections are machine-owned: the
                # writer materializes them from the machine-resolved
                # assignment inputs; hand-authored declarations were refused
                # above.
                body, materialized = prompt_composer.materialize_input_sections(
                    root, task.resolve(), body
                )
                details["input_payloads"] = materialized
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
        except prompt_composer.ComposeRefusal as refusal:
            _writers.stderr("guard", f"{refusal.code}: {refusal.detail}")
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
                # Review prompts consume the same governance-scoped inputs;
                # the writer materializes the typed payload sections for a
                # reviewer exactly as it does for a coder.
                body, materialized = prompt_composer.materialize_input_sections(
                    root, task.resolve(), body
                )
                details["input_payloads"] = materialized
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
        except prompt_composer.ComposeRefusal as refusal:
            _writers.stderr("guard", f"{refusal.code}: {refusal.detail}")
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
