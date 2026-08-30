"""`cartopian release-reservation <project-root> --plan PLAN-NNN --closed <date>`.

The bounded repair that gives back the plan id an abandoned `ledger` attempt
reserved. It is not a closeout step: it runs *before* `archive-plan` or
`reset-plan` when the plan now closing holds a **releasable** reservation and
the operator's answer is anything other than outcome 4.

It **reads and writes no continuity content**: it never opens, stats, or parses
`CONTINUITY.md`, whatever state that file is in. That is what keeps `none` and
`archive` zero-continuity closeouts in the failed-`ledger` fallback state as
well. The fact it needs — whether the attempt that left the reservation is
known to have recorded nothing — is carried by the reservation itself, in two
durable forms: the `LEDGER-FAILED.md` marker a proven non-commit writes, and
the missing `archive/INDEX.md` reservation row a creation that never reached
publication leaves behind.

It is a state machine over what is still there, not a single sequence: it
removes two files, a directory, and an index row, and a process killed between
any two of those leaves a prefix the next run finishes rather than refuses.
"""
from __future__ import annotations

import argparse
from datetime import date as _date
from typing import List

from cli import continuity as cy
from cli.commands import _writers
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.description = (
        "Release the plan-id reservation an abandoned `write-continuity --mode "
        "ledger` attempt left behind, so the plan now closing keeps its own id "
        "and its own prompt evidence. Run it before archive-plan or reset-plan "
        "whenever a releasable reservation exists and the chosen preservation "
        "outcome is not `ledger`. Reads and writes no continuity content."
    )
    subparser.add_argument("project_root", help="Absolute Cartopian project root")
    subparser.add_argument("--plan", required=True, help="PLAN-NNN to release")
    subparser.add_argument(
        "--closed", required=True, help="Closeout date in YYYY-MM-DD form"
    )


def handler(args: argparse.Namespace) -> int:
    root, err = _writers.validated_root(args.project_root)
    if err is not None:
        _writers.stderr("usage", err)
        return EXIT_USAGE
    if not (root / "cartopian.toml").is_file():
        _writers.stderr("guard", f"not a Cartopian project root: {root}")
        return EXIT_FAIL
    if not cy.PLAN_ID_RE.match(args.plan):
        _writers.stderr("usage", f"--plan must match PLAN-NNN grammar; got: {args.plan!r}")
        return EXIT_USAGE
    if not cy.DATE_RE.match(args.closed):
        _writers.stderr("usage", f"--closed must be YYYY-MM-DD; got: {args.closed!r}")
        return EXIT_USAGE
    try:
        _date.fromisoformat(args.closed)
    except ValueError:
        _writers.stderr("usage", f"--closed must be YYYY-MM-DD; got: {args.closed!r}")
        return EXIT_USAGE

    removed: List[str] = []
    try:
        # Step 0/1 — collapse publication residue, then classify over the entry
        # names left and this plan's archive/INDEX.md reservation row.
        state = cy.classify_reservation(root, args.plan)
        if state.shape == cy.SHAPE_UNMARKED:
            raise cy.ContinuityRefusal(
                "continuity-reservation-unresolved",
                f"{state.path} is a complete reservation that no pass ever marked, "
                "so whether the plan was recorded is undecidable from archive/ "
                f"alone. Run `cartopian write-continuity --mode ledger --plan "
                f"{args.plan}` first: it either completes a record that already "
                "landed or records the plan, and if it refuses it marks the "
                "reservation and this release is unblocked.",
            )
        if state.shape == cy.SHAPE_MALFORMED:
            raise cy.ContinuityRefusal(
                "continuity-reservation-malformed",
                state.detail or f"{state.path} is not a reservation",
            )

        # Step 2 — the preconditions that apply to what step 1 found.
        if state.shape in (cy.SHAPE_MARKED, cy.SHAPE_MARKER_ONLY):
            named = cy.marker_plan_id(state.path / cy.LEDGER_FAILED_BASENAME)
            if named != args.plan:
                raise cy.ContinuityRefusal(
                    "continuity-reservation-marker-mismatch",
                    f"{cy.LEDGER_FAILED_BASENAME} names {named or '<unreadable>'}, "
                    f"not --plan {args.plan}",
                )
        if state.shape in cy.RELEASABLE_SHAPES:
            numbers = cy.existing_archive_numbers(root)
            highest = max(numbers) if numbers else 0
            if cy.plan_number(args.plan) != highest:
                raise cy.ContinuityRefusal(
                    "continuity-reservation-not-current",
                    f"--plan {args.plan} is not the highest-numbered archive entry "
                    f"{cy.plan_name(highest) if highest else '<none>'}; handing back "
                    "a lower id while a higher plan exists would let a later "
                    "closeout mint an id twice",
                )
        if cy.witness_contradicts(root, args.plan):
            raise cy.ContinuityRefusal(
                "continuity-reservation-window-foreign",
                f"the prompt-evidence log names window "
                f"{cy.window_witness(root) or '<mixed>'}, not --plan {args.plan}; "
                "closing it here would delete a live window's evidence",
            )

        # Steps 3 to 6 — only the mutations still outstanding, in an order every
        # prefix of which keeps the proof of non-commit alive: the sentinel goes
        # first and the index row last.
        # A real archive and an absent directory both skip straight to the index
        # and window steps: no archive is ever removed, and the absent prefix is
        # how a release interrupted after its rmdir is completed.
        sentinel = state.path / cy.NOT_ARCHIVED_BASENAME
        marker = state.path / cy.LEDGER_FAILED_BASENAME
        if state.releasable:
            if sentinel.is_file():
                cy.mediated_unlink(root, sentinel)
                removed.append(cy.NOT_ARCHIVED_BASENAME)
            if marker.is_file():
                cy.mediated_unlink(root, marker)
                removed.append(cy.LEDGER_FAILED_BASENAME)
        if state.releasable and state.path.is_dir() and not any(
            state.path.iterdir()
        ):
            cy.mediated_unlink(root, state.path, directory=True)
            removed.append("directory")
        dropped, kept = cy.drop_reservation_row(root, args.plan)
        if dropped:
            removed.append("archive/INDEX.md row")
        # Captured before step 7: the close deletes the log, which is the very
        # thing the witness is derived from.
        witness = cy.window_witness(root)
    except cy.ContinuityRefusal as refusal:
        _writers.stderr("guard", f"{refusal.rule}: {refusal.detail}")
        return EXIT_FAIL
    except OSError as exc:
        _writers.stderr("error", f"release failed: {exc}")
        return EXIT_FAIL

    # Step 7 — close the reserved window last, under § 11.5's witness rule. By
    # the time it runs the id is already back, so even a crash before it leaves
    # the following reset-plan or archive-plan naming the right window unaided.
    closeout = cy.close_evidence_window(root, args.plan, args.closed)

    emit_record({
        "action": "release-reservation",
        "details": {
            "project_root": str(root),
            "path": str(state.path),
            "plan": args.plan,
            "closed": args.closed,
            "reservation_shape": state.shape,
            "residue_collapsed": state.residue_collapsed,
            "removed": removed,
            "index_rows_kept": kept,
            "already_released": not removed and not state.residue_collapsed,
            "window_witness": witness,
            "effectiveness_closeout": closeout,
        },
    })
    return EXIT_OK
