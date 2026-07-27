"""`cartopian write-prompt <project-root> --prompt-id PROMPT-...`.

Structured writer for prompt files ``prompts/PROMPT-*.md``, covering both
variants:

- **task** prompts — ``PROMPT-NN-NNN.md``
- **planning** prompts — ``PROMPT-PLAN-NNN[-slug].md``

The grammar mirrors ``delete-prompt`` so the writer and the deleter agree on
what a valid prompt filename is. The PM supplies the id, not a path; the
destination subtree is the allowlisted ``prompt`` dest_kind. Re-issuing
overwrites in place (assign/re-handoff revision).

**Review prompts carry a generated operator-intent section.** When
``--review-kind`` is supplied, the writer resolves the deterministic review
context itself and renders the ``## Operator intent`` section from it, bound to
the current context identity — replacing any section the caller authored. The
PM therefore cannot omit, narrow, or paraphrase the independent evidence
channel: the section is tool-rendered from operator-confirmed attestations, and
``cartopian dispatch`` recomputes it at the handoff boundary. When a complete
applicability scan and supplemental-reference resolution find nothing, the
generated section says ``none recorded``.
"""
import argparse
from pathlib import Path
from typing import Optional

from cli.commands import _writers
from cli.operator_intent import (
    CHECKPOINT_ID_RE,
    PHASE_ID_RE,
    PLAN_REF_RE,
    REVIEW_KINDS,
    IntentRefusal,
    artifact_supplemental_refs,
    context_for_checkpoint,
    context_for_task,
    upsert_intent_section,
)


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    _writers.add_content_args(subparser)
    subparser.add_argument(
        "--prompt-id",
        required=True,
        help=(
            "Prompt id without extension, e.g. PROMPT-NN-NNN (task) or "
            "PROMPT-PLAN-NNN-some-slug (planning checkpoint)"
        ),
    )
    subparser.add_argument(
        "--review-kind",
        default=None,
        choices=list(REVIEW_KINDS),
        help=(
            "Generate the bound `## Operator intent` section for a review "
            "prompt of this kind"
        ),
    )
    subparser.add_argument(
        "--task",
        default=None,
        help="Absolute path to the task under review (task-closure review prompts)",
    )
    subparser.add_argument(
        "--checkpoint",
        default=None,
        help="Planning-checkpoint id, PLAN-NNN[-slug] (planning review prompts)",
    )
    subparser.add_argument(
        "--phase", default=None, help="PHASE-NN-slug this review attaches to"
    )
    subparser.add_argument(
        "--plan-ref", default=None, help="PNN-KIND-NNN this review attaches to"
    )
    subparser.add_argument(
        "--intent-ref",
        action="append",
        default=None,
        help=(
            "Additional supplemental reference (ATTEST-NNN | DEC-NNN | "
            "OIR-NNN | REQUIREMENTS.md#Confirmed-intent), "
            "repeatable. Additive only; it cannot suppress the applicability scan"
        ),
    )


def handler(args: argparse.Namespace) -> int:
    prompt_id = args.prompt_id
    if not _writers.PROMPT_ID_RE.match(prompt_id):
        _writers.stderr(
            "usage",
            "--prompt-id must match PROMPT-NN-NNN or PROMPT-PLAN-NNN[-slug]; "
            f"got: {prompt_id!r}",
        )
        return _writers.EXIT_USAGE
    variant = "planning" if prompt_id.startswith("PROMPT-PLAN-") else "task"

    extra_details = {"prompt_id": prompt_id, "variant": variant}
    content: Optional[object] = None

    if args.review_kind is not None:
        root, err = _writers.validated_root(args.project_root)
        if err is not None:
            _writers.stderr("usage", err)
            return _writers.EXIT_USAGE
        body, cerr = _writers.resolve_content(args)
        if cerr is not None:
            _writers.stderr("usage", cerr)
            return _writers.EXIT_USAGE
        if isinstance(body, bytes):
            try:
                body = body.decode("utf-8")
            except UnicodeDecodeError:
                _writers.stderr(
                    "usage",
                    "a review prompt body must be valid UTF-8 to carry the "
                    "generated operator-intent section",
                )
                return _writers.EXIT_USAGE
        if args.phase is not None and not PHASE_ID_RE.match(args.phase):
            _writers.stderr(
                "usage", f"--phase must match PHASE-NN-slug; got: {args.phase!r}"
            )
            return _writers.EXIT_USAGE
        if args.plan_ref is not None and not PLAN_REF_RE.match(args.plan_ref):
            _writers.stderr(
                "usage", f"--plan-ref must match PNN-KIND-NNN; got: {args.plan_ref!r}"
            )
            return _writers.EXIT_USAGE
        extra_refs = tuple(args.intent_ref or ())
        try:
            if args.review_kind == "task-closure":
                if not args.task:
                    _writers.stderr(
                        "usage",
                        "--task is required with --review-kind task-closure",
                    )
                    return _writers.EXIT_USAGE
                task_path = Path(args.task)
                if not task_path.is_absolute():
                    _writers.stderr(
                        "usage", f"--task must be an absolute path; got: {args.task!r}"
                    )
                    return _writers.EXIT_USAGE
                if not task_path.is_file():
                    _writers.stderr("guard", f"task file not found: {args.task}")
                    return _writers.EXIT_FAIL
                prompt_refs = tuple(
                    artifact_supplemental_refs(body)
                )
                context = context_for_task(
                    root,
                    task_path.resolve(),
                    (*prompt_refs, *extra_refs),
                )
            else:
                if not args.checkpoint:
                    _writers.stderr(
                        "usage",
                        "--checkpoint is required with --review-kind planning",
                    )
                    return _writers.EXIT_USAGE
                if not CHECKPOINT_ID_RE.match(args.checkpoint):
                    _writers.stderr(
                        "usage",
                        "--checkpoint must match PLAN-NNN[-slug]; got: "
                        f"{args.checkpoint!r}",
                    )
                    return _writers.EXIT_USAGE
                context = context_for_checkpoint(
                    root,
                    args.checkpoint,
                    phase_id=args.phase,
                    plan_ref=args.plan_ref,
                    extra_refs=extra_refs,
                    checkpoint_text=body,
                )
        except IntentRefusal as refusal:
            _writers.stderr("guard", f"{refusal.rule}: {refusal.detail}")
            if refusal.recovery:
                _writers.stderr("guard", f"recovery: {refusal.recovery}")
            return _writers.EXIT_FAIL
        content = upsert_intent_section(body, context.section)
        extra_details.update(
            {
                "review_kind": context.review_kind,
                "operator_intent_context_identity": context.context_identity,
                "operator_intent_evidence": [
                    item.attestation.attestation_id for item in context.evidence
                ],
                "operator_intent_none_recorded": context.none_recorded,
                "operator_intent_measures": context.measures,
            }
        )

    return _writers.perform_write(
        args,
        action="write-prompt",
        dest_kind="prompt",
        relative_target=f"{prompt_id}.md",
        content=content,
        extra_details=extra_details,
    )
