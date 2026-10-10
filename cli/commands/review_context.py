"""Read-only projection of verbatim intake and PM-derived review channels.

With ``--prompt`` the command is a binding preflight: its caller needs the
verdict and the identities it was computed from, not the bound content. That
mode therefore emits a compact record by default -- each bulky body replaced
by its byte size, with ``omitted_fields`` naming what was dropped -- and
``--full`` restores the complete projection. Without ``--prompt`` the full
projection is the point of the call and is always emitted. Prompt generation,
dispatch, and audit compute the context in process and never read this
output, so compaction changes no binding.
"""
import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional

from cli import acceptance_trace, contract_review, decision_neighbors, delivery_contract, trace_binding
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
    parser.add_argument("--checkpoint", default=None, help="Scoped planning ID, e.g. PLAN-PHASE-01 or PLAN-BUILD-01-005")
    parser.add_argument("--phase", default=None, help="Optional PHASE-NN target metadata")
    parser.add_argument("--plan-ref", default=None, help="Optional KIND-NN-NNN target metadata")
    parser.add_argument("--prompt", default=None, help="Absolute review prompt path for binding preflight")
    parser.add_argument(
        "--full",
        action="store_true",
        help="With --prompt, emit the complete projection instead of the compact preflight record",
    )


def _utf8_bytes(value: Any) -> int:
    return len(value.encode("utf-8")) if isinstance(value, str) else 0


def _compact(record: Dict[str, Any]) -> Dict[str, Any]:
    """Replace each bulky body with its size; identities and verdicts stay.

    Every dropped field is named in ``omitted_fields`` so a reader knows the
    record is partial and which ``--full`` field to ask for.
    """
    omitted: List[str] = []
    trace = record.get("request_trace")
    if isinstance(trace, dict) and isinstance(trace.get("records"), list):
        slim = []
        for item in trace["records"]:
            if not isinstance(item, dict):
                slim.append(item)
                continue
            kept = {key: value for key, value in item.items() if key not in ("text", "context", "source")}
            kept["text_bytes"] = _utf8_bytes(item.get("text"))
            slim.append(kept)
        record["request_trace"] = {**trace, "records": slim}
        omitted.append("request_trace.records[].text/context/source")
    upstream = record.get("upstream_trace")
    projection = upstream.get("reviewer_projection") if isinstance(upstream, dict) else None
    if isinstance(projection, dict) and "body" in projection:
        record["upstream_trace"] = {
            **upstream,
            "reviewer_projection": {key: value for key, value in projection.items() if key != "body"},
        }
        omitted.append("upstream_trace.reviewer_projection.body")
    delivery = record.get("delivery_contract")
    if isinstance(delivery, dict) and isinstance(delivery.get("section"), str):
        kept = {key: value for key, value in delivery.items() if key != "section"}
        kept["section_bytes"] = _utf8_bytes(delivery["section"])
        record["delivery_contract"] = kept
        omitted.append("delivery_contract.section")
    proximity = record.get("decision_proximity")
    if isinstance(proximity, dict) and isinstance(proximity.get("pairs"), list):
        kept = {key: value for key, value in proximity.items() if key != "pairs"}
        kept["pair_count"] = len(proximity["pairs"])
        record["decision_proximity"] = kept
        omitted.append("decision_proximity.pairs")
    record["projection"] = "compact"
    record["omitted_fields"] = omitted
    return record


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
        stderr_usage("--checkpoint must be a valid planning checkpoint identity")
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
        "upstream_trace": None,
        "contract_quality": None,
        # Only the planning review reads the plan's delivery contract: it is the
        # review whose subject is the plan. A task-closure review receives none
        # of it, so the delivery record never becomes ambient review context.
        "delivery_contract": (
            delivery_contract.review_projection_for_plan(root)
            if args.review_kind == "planning"
            else None
        ),
        # Only the planning review receives the decision-proximity list. The
        # planning reviewer is the one asked whether a new ruling sits badly
        # beside the locked set, and today that depends on how widely it reads.
        # These are the pairs worth reading: mutually near, never cross-
        # referenced. Proximity is not contradiction — the list is evidence the
        # reviewer weighs, never a finding — so it carries no verdict and never
        # affects the exit code. A task-closure review gets none of it, so the
        # decision set never becomes ambient review context.
        "decision_proximity": (
            {
                "budget": decision_neighbors.NEIGHBOR_BUDGET,
                "cap": decision_neighbors.REVIEW_PAIR_CAP,
                "pairs": decision_neighbors.unreferenced_pairs(root),
            }
            if args.review_kind == "planning"
            else None
        ),
        "preflight": None,
    }
    if task is not None:
        # The review-context seam. The reviewer projection is what makes the
        # provenance independently attributable rather than self-certified: it
        # is PM-computed from lifecycle artifacts the coder's role cannot read,
        # and it names the input identity so the derivation can be recomputed.
        binding = trace_binding.bind(root, task)
        record["upstream_trace"] = binding.as_record()
        if binding.trace is not None:
            body = binding.trace.reviewer_projection()
            record["upstream_trace"]["reviewer_projection"] = {
                "bytes": len(body.encode("utf-8")),
                "identity": acceptance_trace.body_identity(body),
                "body": body,
            }
        record["contract_quality"] = {
            "section": contract_review.SECTION_HEADING,
            "placed_after": contract_review.PRECEDING_HEADING,
            "placed_before": contract_review.FOLLOWING_HEADING,
            "checks": [
                {"check": code, "name": name} for code, name in contract_review.CHECKS
            ],
            "outcomes": list(contract_review.OUTCOMES),
            "severities": list(contract_review.SEVERITIES),
        }
    if prompt is not None:
        record["preflight"] = {
            **preflight_prompt_binding(context, prompt_text or ""),
            "prompt_path": str(prompt),
        }
    if prompt is not None and not getattr(args, "full", False):
        record = _compact(record)
    else:
        record["projection"] = "full"
    emit_record(record)
    if record["preflight"] is not None and not record["preflight"]["ok"]:
        stderr_guard(f"{record['preflight']['rule']}: {record['preflight']['detail']}")
        return EXIT_FAIL
    return EXIT_OK
