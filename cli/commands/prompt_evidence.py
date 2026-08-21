"""`cartopian prompt-evidence <project-root>` — the ledger's only read path.

Four modes, and no fifth. Every read of the evidence ledger is an explicit
act producing one of exactly three bounded projections; no projection is ever
attached to a routine surface, and the routine-path budget stays at zero
bytes by construction.

* ``--projection U|D|E`` — the bounded query. Rows are capped at 50 / 200 /
  200, truncation is always announced, and a unit that reached its record cap
  is named by a ``CAPPED`` line in every answer that reports it. Silence means
  completeness.
* ``--summarize --unit TASK-NN-NNN`` — derive and append that unit's ``U``
  record from its own ``E`` and ``D`` records plus the boundary's
  availability. Written from one of the two reserved cap slots, so a unit that
  ran hot still produces an answer.
* ``--record-event --family CLR|PAD`` — the two boundaries with no other
  mediated home: a unit-bound decision and a post-approval backlog entry.
* ``--close-plan`` — the normative plan-closing sequence: superseding
  summaries first, closing projection second, mediated delete last. A run
  that deletes before the summaries loses ``PAD`` for the whole plan; a run
  that deletes before the projection loses everything the projection was for.

Nothing here scores, ranks, or asserts causation, and no lifecycle transition
may depend on anything this command reports.
"""
import argparse
import datetime
from pathlib import Path
from typing import Any, Dict, List

from cli import prompt_evidence as ledgerlib
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE, stderr_error, stderr_guard, stderr_usage


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Read the bounded prompt-effectiveness ledger, derive a unit summary, "
        "record a clarification or post-approval event, or run the plan-closing "
        "sequence. The ledger is derived evidence, never authority: it may "
        "expose patterns and comparisons but never that a prompt caused an "
        "outcome."
    )
    parser.add_argument(
        "project_root", help="Absolute path to the Cartopian project root"
    )
    parser.add_argument(
        "--projection",
        default=None,
        choices=sorted(ledgerlib.PROJECTIONS),
        help="Bounded query projection: U (unit summary), D (reason address), E (event capture).",
    )
    parser.add_argument(
        "--unit",
        action="append",
        default=None,
        help="Restrict to one unit; repeatable. Query order is the order given.",
    )
    parser.add_argument(
        "--summarize",
        action="store_true",
        help="Derive and append the U record for the single --unit given.",
    )
    parser.add_argument(
        "--post-approval-closed",
        action="store_true",
        help="Write the superseding summary that closes the PAD window.",
    )
    parser.add_argument(
        "--record-event",
        action="store_true",
        help="Append one CLR or PAD event record.",
    )
    parser.add_argument(
        "--family", default=None, choices=["CLR", "PAD"], help="Event family."
    )
    parser.add_argument(
        "--artifact",
        default=None,
        help="Artifact pointer by name: REPORT-NN-NNN, DEC-NNN, or BL-NNN.",
    )
    parser.add_argument(
        "--close-plan",
        action="store_true",
        help="Run the ordered plan-closing sequence and delete the log.",
    )
    parser.add_argument(
        "--date", default=None, help="Project-local event date (YYYY-MM-DD)."
    )


def handler(args: argparse.Namespace) -> int:
    root = Path(args.project_root)
    if not root.is_absolute():
        stderr_usage("project_root must be absolute")
        return EXIT_USAGE
    root = root.resolve()
    if not (root / "cartopian.toml").is_file():
        stderr_error(f"project config not found: {root / 'cartopian.toml'}")
        return EXIT_FAIL
    date = args.date or datetime.date.today().isoformat()
    if not ledgerlib.DATE_RE.match(date):
        stderr_usage("--date must be YYYY-MM-DD")
        return EXIT_USAGE
    modes = [args.summarize, args.record_event, args.close_plan, bool(args.projection)]
    if sum(1 for mode in modes if mode) != 1:
        stderr_usage(
            "pass exactly one of --projection, --summarize, --record-event, --close-plan"
        )
        return EXIT_USAGE
    units: List[str] = list(args.unit or [])
    for unit in units:
        if not ledgerlib.UNIT_ID_RE.match(unit):
            stderr_usage(f"--unit must be a TASK-NN-NNN; got {unit!r}")
            return EXIT_USAGE

    ledger = ledgerlib.read_ledger(root)
    base: Dict[str, Any] = {
        "action": "prompt-evidence",
        "project_path": str(root),
        "plan": ledger.plan_id,
        "routine_context_bytes": ledgerlib.ROUTINE_CONTEXT_BUDGET_BYTES,
        "ledger_errors": ledger.errors,
    }

    if args.projection:
        answer = ledgerlib.PROJECTIONS[args.projection](
            ledger, units=units or None
        )
        emit_record({**base, "mode": "query", "answer": answer.as_record()})
        return EXIT_OK

    if args.record_event:
        if not args.family or not args.artifact or len(units) != 1:
            stderr_usage(
                "--record-event needs one --unit, a --family, and an --artifact"
            )
            return EXIT_USAGE
        record = ledgerlib.event(
            plan=ledger.plan_id,
            unit=units[0],
            date=date,
            family=args.family,
            artifact=args.artifact,
        )
        outcome = ledgerlib.emit(root, record, ledger=ledger)
        emit_record({**base, "mode": "record-event", "record": record, **outcome})
        if outcome["result"] == ledgerlib.REJECTED:
            stderr_guard(f"rejected-emission: {outcome['reason']}")
            # A rejected emission marks its family `omitted` and never blocks a
            # lifecycle transition, so this is a reported outcome, not a failure.
        return EXIT_OK

    if args.summarize:
        if len(units) != 1:
            stderr_usage("--summarize needs exactly one --unit")
            return EXIT_USAGE
        result = ledgerlib.summarize_unit(
            root,
            units[0],
            date,
            post_approval_closed=args.post_approval_closed,
            ledger=ledger,
        )
        emit_record({**base, "mode": "summarize", **result})
        return EXIT_OK

    # --close-plan: the ordered sequence lives with the other lifecycle seams,
    # so this mode and the closeout commands run byte-identical closes.
    sequence = ledgerlib.close_plan_sequence(root, date=date)
    emit_record(
        {
            **base,
            "mode": "close-plan",
            "superseding_summaries": sequence["superseding_summaries"],
            "closing_projection": sequence["closing_projection"],
            "log_deleted": sequence["log_deleted"],
            "retained": False,
        }
    )
    return EXIT_OK
