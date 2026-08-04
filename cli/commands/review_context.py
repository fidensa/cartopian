"""Read-only projection of verbatim intake and PM-derived review channels."""
import argparse
from pathlib import Path
from typing import Optional

from cli.commands.resolve_config import _CliError, resolve_project_configuration
from cli.config_schema import MACHINE_RECORD_SCHEMA_VERSION
from cli.emit import emit_record
from cli.main import EXIT_ENV, EXIT_FAIL, EXIT_OK, EXIT_USAGE, stderr_error, stderr_guard, stderr_usage
from cli.request_trace import (
    CHECKPOINT_ID_RE, PHASE_ID_RE, PLAN_REF_RE, REVIEW_KINDS, RequestRefusal,
    context_for_checkpoint, context_for_task, preflight_prompt_binding,
    read_contained_text,
)


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("project_root", help="Absolute path to the Cartopian project root")
    parser.add_argument("--review-kind", required=True, choices=list(REVIEW_KINDS))
    parser.add_argument("--task", default=None, help="Absolute task path for task-closure")
    parser.add_argument("--checkpoint", default=None, help="PLAN-NNN for planning")
    parser.add_argument("--phase", default=None, help="Optional PHASE-NN target metadata")
    parser.add_argument("--plan-ref", default=None, help="Optional KIND-NN-NNN target metadata")
    parser.add_argument("--prompt", default=None, help="Absolute review prompt path for binding preflight")


def handler(args: argparse.Namespace) -> int:
    root = Path(args.project_root)
    if not root.is_absolute():
        stderr_usage("project_root must be absolute")
        return EXIT_USAGE
    root = root.resolve()
    if not (root / "cartopian.toml").is_file():
        stderr_error(f"project config not found: {root / 'cartopian.toml'}")
        return EXIT_ENV
    if args.phase and not PHASE_ID_RE.fullmatch(args.phase):
        stderr_usage("--phase must match PHASE-NN")
        return EXIT_USAGE
    if args.plan_ref and not PLAN_REF_RE.fullmatch(args.plan_ref):
        stderr_usage("--plan-ref must match KIND-NN-NNN")
        return EXIT_USAGE
    task: Optional[Path] = None
    if args.review_kind == "task-closure":
        if not args.task or not Path(args.task).is_absolute() or not Path(args.task).is_file():
            stderr_usage("--task must name an existing absolute task for task-closure")
            return EXIT_USAGE
        task = Path(args.task).resolve()
    elif not args.checkpoint or not CHECKPOINT_ID_RE.fullmatch(args.checkpoint):
        stderr_usage("--checkpoint must match PLAN-NNN for planning")
        return EXIT_USAGE
    prompt: Optional[Path] = Path(args.prompt) if args.prompt else None
    prompt_text: Optional[str] = None
    try:
        resolved = resolve_project_configuration(root)
        if prompt is not None:
            prompt_text = read_contained_text(root, prompt, what="review prompt")
        context = (context_for_task(
            root,
            task,
            prompt_text=prompt_text,
            # A projection over an open task is useful before implementation
            # has produced a report. Once the task is actually in review, the
            # preserved completion report is required binding evidence.
            require_completion_evidence=task.parent.name == "in-review",
        ) if task else context_for_checkpoint(
            root, args.checkpoint, phase_id=args.phase, plan_ref=args.plan_ref,
            checkpoint_text=prompt_text,
        ))
    except _CliError as exc:
        stderr_error(exc.message)
        return exc.exit_code
    except RequestRefusal as refusal:
        stderr_guard(f"{refusal.rule}: {refusal.detail}")
        return EXIT_FAIL
    record = {
        "record_schema_version": MACHINE_RECORD_SCHEMA_VERSION,
        "schema_identity": resolved["schema_identity"],
        "project_schema_version": resolved["project_schema_version"],
        "action": "review-context",
        "project_path": str(root),
        **context.as_record(),
        "preflight": None,
    }
    if prompt is not None:
        record["preflight"] = {
            **preflight_prompt_binding(context, prompt_text or ""),
            "prompt_path": str(prompt),
        }
    emit_record(record)
    if record["preflight"] is not None and not record["preflight"]["ok"]:
        stderr_guard(f"{record['preflight']['rule']}: {record['preflight']['detail']}")
        return EXIT_FAIL
    return EXIT_OK
