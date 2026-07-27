"""`cartopian review-context` — the deterministic two-channel review context.

Emits one NDJSON record carrying:

- the **operator-intent channel**: every current attestation whose applicability
  scope matches this review target, plus the resolution of any supplemental
  reference, with source identity and path, full-source hash, attestation
  identity and status, matched scopes, requiredness, current/supersession
  state, complete selected sections, and byte measures;
- the **management-derived guidance channel**: the PM artifact paths for this
  review, kept deliberately separate and carried as paths, not content.

Both are bound by a deterministic ``context_identity``. Prompt generation
embeds it; dispatch and manual handoff recompute it. With ``--prompt`` the
command additionally runs the preflight check against an existing prompt, so a
manual reviewer handoff consumes exactly the artifact automatic dispatch does.

Read-only: it never writes, moves, or launches anything.
"""
import argparse
from pathlib import Path
from typing import Optional

from cli.commands.resolve_config import _CliError, resolve_project_configuration
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
from cli.operator_intent import (
    CHECKPOINT_ID_RE,
    PHASE_ID_RE,
    PLAN_REF_RE,
    REVIEW_KINDS,
    IntentRefusal,
    artifact_supplemental_refs,
    context_for_checkpoint,
    context_for_task,
    preflight_prompt_binding,
    read_contained_text,
)


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "project_root", help="Absolute path to the Cartopian project root"
    )
    subparser.add_argument(
        "--review-kind",
        required=True,
        choices=list(REVIEW_KINDS),
        help="Review kind this context is resolved for",
    )
    subparser.add_argument(
        "--task",
        default=None,
        help="Absolute path to the task file (required for task-closure)",
    )
    subparser.add_argument(
        "--checkpoint",
        default=None,
        help="Planning-checkpoint id, PLAN-NNN[-slug] (required for planning)",
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
    subparser.add_argument(
        "--prompt",
        default=None,
        help="Absolute path to the review prompt; runs the binding preflight",
    )


def handler(args: argparse.Namespace) -> int:
    raw_root = args.project_root
    if not Path(raw_root).is_absolute():
        stderr_usage(f"project_root must be an absolute path; got: {raw_root!r}")
        return EXIT_USAGE
    project_root = Path(raw_root).resolve()
    if not (project_root / "cartopian.toml").is_file():
        stderr_error(f"project config not found: {project_root / 'cartopian.toml'}")
        return EXIT_ENV

    review_kind = args.review_kind
    task_path: Optional[Path] = None
    if review_kind == "task-closure":
        if not args.task:
            stderr_usage("--task is required for --review-kind task-closure")
            return EXIT_USAGE
        if not Path(args.task).is_absolute():
            stderr_usage(f"--task must be an absolute path; got: {args.task!r}")
            return EXIT_USAGE
        task_path = Path(args.task)
        if not task_path.is_file():
            stderr_error(f"task file not found: {args.task}")
            return EXIT_FAIL
        task_path = task_path.resolve()
    else:
        if not args.checkpoint:
            stderr_usage("--checkpoint is required for --review-kind planning")
            return EXIT_USAGE
        if not CHECKPOINT_ID_RE.match(args.checkpoint):
            stderr_usage(
                f"--checkpoint must match PLAN-NNN[-slug]; got: {args.checkpoint!r}"
            )
            return EXIT_USAGE
    if args.phase is not None and not PHASE_ID_RE.match(args.phase):
        stderr_usage(f"--phase must match PHASE-NN-slug; got: {args.phase!r}")
        return EXIT_USAGE
    if args.plan_ref is not None and not PLAN_REF_RE.match(args.plan_ref):
        stderr_usage(f"--plan-ref must match PNN-KIND-NNN; got: {args.plan_ref!r}")
        return EXIT_USAGE

    try:
        resolved = resolve_project_configuration(project_root)
    except _CliError as err:
        stderr_error(err.message)
        return err.exit_code

    prompt_path: Optional[Path] = None
    prompt_text: Optional[str] = None
    if args.prompt is not None:
        if not Path(args.prompt).is_absolute():
            stderr_usage(f"--prompt must be an absolute path; got: {args.prompt!r}")
            return EXIT_USAGE
        prompt_path = Path(args.prompt)
        if prompt_path.is_file():
            try:
                prompt_text = read_contained_text(
                    project_root, prompt_path, what="review prompt"
                )
            except IntentRefusal as refusal:
                stderr_guard(f"{refusal.rule}: {refusal.detail}")
                if refusal.recovery:
                    stderr_guard(f"recovery: {refusal.recovery}")
                return EXIT_FAIL

    extra_refs = list(args.intent_ref or ())
    if prompt_text is not None and review_kind == "task-closure":
        try:
            extra_refs.extend(artifact_supplemental_refs(prompt_text))
        except IntentRefusal as refusal:
            stderr_guard(f"{refusal.rule}: {refusal.detail}")
            return EXIT_FAIL
    try:
        if review_kind == "task-closure":
            context = context_for_task(project_root, task_path, tuple(extra_refs))
        else:
            context = context_for_checkpoint(
                project_root,
                args.checkpoint,
                phase_id=args.phase,
                plan_ref=args.plan_ref,
                extra_refs=tuple(extra_refs),
                checkpoint_text=prompt_text,
            )
    except IntentRefusal as refusal:
        stderr_guard(f"{refusal.rule}: {refusal.detail}")
        if refusal.recovery:
            stderr_guard(f"recovery: {refusal.recovery}")
        return EXIT_FAIL

    record = {
        "record_schema_version": MACHINE_RECORD_SCHEMA_VERSION,
        "schema_identity": resolved["schema_identity"],
        "project_schema_version": resolved["project_schema_version"],
        "action": "review-context",
        "project_path": str(project_root),
    }
    record.update(context.as_record())

    preflight = None
    if args.prompt is not None:
        if not prompt_path.is_file():
            preflight = {
                "ok": False,
                "rule": "missing-prompt",
                "detail": f"review prompt not found: {prompt_path}",
                "recovery": "prepare the review prompt before the handoff",
                "context_identity": context.context_identity,
            }
        else:
            preflight = preflight_prompt_binding(
                context, prompt_text or ""
            )
        preflight["prompt_path"] = str(prompt_path)
    record["preflight"] = preflight
    emit_record(record)

    if preflight is not None and not preflight["ok"]:
        stderr_guard(f"{preflight['rule']}: {preflight['detail']}")
        if preflight.get("recovery"):
            stderr_guard(f"recovery: {preflight['recovery']}")
        return EXIT_FAIL
    return EXIT_OK
