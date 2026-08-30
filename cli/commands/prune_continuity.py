"""`cartopian prune-continuity <project-root> --pruned <date> [--plan ...] [--cold ...]`.

The one supported recovery when a continuity write crosses the declared file
ceiling. It is mediated, fail-closed, all-or-nothing, and writes through the
same primitive and the same commit-aware publication step as
`write-continuity`. It takes no `--mode`, allocates nothing, creates no
reservation, reads no `decisions/` directory, and never changes
`Highest plan recorded`.

Two scopes, and a preservation rule behind each:

- `--plan PLAN-NNN` replaces a closed plan's six-group body with a tombstone.
  The heading, the closed date, and the preservation value are preserved
  verbatim, so `Plans recorded:` stays exact and a pruned plan stays
  distinguishable from a plan that was never recorded.
- `--cold PLAN-NNN/DEC-NNN` removes a cold-index row **whose body survives
  elsewhere**. A row whose `Body` is `none` is the only surviving copy of that
  ruling; pruning it would destroy exactly what the operator chose `ledger` to
  keep, so it is refused. The archive is the preservation guarantee that makes
  this scope safe, and it is verified on disk rather than inferred from a mode.

`Pruned` is cumulative, so a session is never told a ruling never existed.
"""
from __future__ import annotations

import argparse
import os
from datetime import date as _date
from typing import List

from cli import continuity as cy
from cli.commands import _writers
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE
from cli.mediated_write import GuardRefusal, mediated_write


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.description = (
        "Recover continuity file capacity: tombstone a closed plan's ledger "
        "section, and/or remove a cold-index row whose decision body survives "
        "in an archive. Never removes a live row, an unarchived cold ruling, or "
        "the newest ledger section."
    )
    subparser.add_argument("project_root", help="Absolute Cartopian project root")
    subparser.add_argument(
        "--pruned", required=True, help="Prune date in YYYY-MM-DD form"
    )
    subparser.add_argument(
        "--plan",
        action="append",
        default=[],
        help="PLAN-NNN whose ledger section is replaced with a tombstone",
    )
    subparser.add_argument(
        "--cold",
        action="append",
        default=[],
        help="PLAN-NNN/DEC-NNN cold-index row to remove",
    )


def handler(args: argparse.Namespace) -> int:
    root, err = _writers.validated_root(args.project_root)
    if err is not None:
        _writers.stderr("usage", err)
        return EXIT_USAGE
    if not (root / "cartopian.toml").is_file():
        _writers.stderr("guard", f"not a Cartopian project root: {root}")
        return EXIT_FAIL
    if not cy.DATE_RE.match(args.pruned):
        _writers.stderr("usage", f"--pruned must be YYYY-MM-DD; got: {args.pruned!r}")
        return EXIT_USAGE
    try:
        _date.fromisoformat(args.pruned)
    except ValueError:
        _writers.stderr("usage", f"--pruned must be YYYY-MM-DD; got: {args.pruned!r}")
        return EXIT_USAGE
    plans: List[str] = list(dict.fromkeys(args.plan))
    colds: List[str] = list(dict.fromkeys(args.cold))
    if not plans and not colds:
        _writers.stderr("usage", "pass at least one --plan or --cold")
        return EXIT_USAGE
    for value in plans:
        if not cy.PLAN_ID_RE.match(value):
            _writers.stderr("usage", f"--plan must match PLAN-NNN; got: {value!r}")
            return EXIT_USAGE
    for value in colds:
        if not cy.QUALIFIED_ID_RE.match(value):
            _writers.stderr(
                "usage", f"--cold must match PLAN-NNN/DEC-NNN; got: {value!r}"
            )
            return EXIT_USAGE

    path = cy.artifact_path(root)
    try:
        if os.path.lexists(path):
            collapse = cy.collapse_residue(root, path)
            if collapse.surviving:
                raise cy.ContinuityRefusal(
                    "continuity-publication-cleanup-failed",
                    "publication residue survived beside the artifact: "
                    + ", ".join(collapse.surviving),
                )
        artifact = cy.read_artifact(root)
        model = artifact.model
        if model is None:
            raise cy.ContinuityRefusal(
                "continuity-prune-plan-unrecorded",
                f"no continuity artifact exists at {path}",
            )
        before = len(artifact.raw)

        highest = cy.plan_name(model.highest_plan) if model.highest_plan else None
        live_ids = {row.id for row in model.live}
        cold_by_id = {row.id: row for row in model.cold}

        # Every preservation rule is checked before anything is written.
        for plan in plans:
            section = model.section(plan)
            if section is None or section.tombstoned:
                raise cy.ContinuityRefusal(
                    "continuity-prune-plan-unrecorded",
                    f"--plan {plan} names no recorded, non-tombstoned ledger section",
                )
            if plan == highest:
                raise cy.ContinuityRefusal(
                    "continuity-prune-newest-plan",
                    f"--plan {plan} is the newest recorded plan; the newest entry "
                    "is the one a session is most likely to need, and keeping it "
                    "also keeps a ledger-replace pass meaningful",
                )
        for identity in colds:
            if identity in live_ids:
                raise cy.ContinuityRefusal(
                    "continuity-prune-live-row",
                    f"--cold {identity} names a live row; a governing ruling leaves "
                    "the live table only through supersession or expiry",
                )
            row = cold_by_id.get(identity)
            if row is None:
                raise cy.ContinuityRefusal(
                    "continuity-prune-plan-unrecorded",
                    f"--cold {identity} names no cold-index row",
                )
            if not row.has_locator or not (root / row.body).is_file():
                raise cy.ContinuityRefusal(
                    "continuity-prune-unarchived-body",
                    f"--cold {identity} has no surviving archived body "
                    f"({row.body}); pruning it would destroy the only copy of "
                    "that ruling",
                )

        pruned_model = cy.Continuity(
            live=list(model.live),
            cold=[row for row in model.cold if row.id not in set(colds)],
            sections=[
                cy.tombstone_section(section, args.pruned)
                if section.plan in set(plans)
                else section
                for section in model.sections
            ],
            pruned=model.pruned + len(colds),
        )
        # No ceiling check here: a prune only ever shrinks the artifact, and
        # applying the ceiling to the one supported recovery would refuse the
        # very operation a hand-damaged over-ceiling file needs.
        text = cy.serialize_continuity(pruned_model)
        data = text.encode("utf-8")
        after = len(data)
        publication = cy.publish(
            root,
            path,
            artifact.raw,
            data,
            lambda: mediated_write(root, "continuity", cy.CONTINUITY_BASENAME, data),
        )
        if publication.outcome == cy.NOT_PUBLISHED:
            refusal = publication.refusal
            raise cy.ContinuityRefusal(
                getattr(refusal, "rule", "continuity-publication-not-published"),
                f"{path} was not published"
                + (f": {getattr(refusal, 'detail', refusal)}" if refusal else ""),
            )
        if publication.outcome == cy.UNRESOLVED:
            raise cy.ContinuityRefusal(
                "continuity-publication-unresolved",
                f"{path} holds neither the bytes this pass read nor the bytes it "
                "was publishing",
            )
        if publication.surviving:
            raise cy.ContinuityRefusal(
                "continuity-publication-cleanup-failed",
                "the publication committed but its residue survived the collapse: "
                + ", ".join(publication.surviving),
            )
    except cy.ContinuityRefusal as refusal:
        _writers.stderr("guard", f"{refusal.rule}: {refusal.detail}")
        return EXIT_FAIL
    except GuardRefusal as refusal:
        _writers.stderr("guard", f"{refusal.rule}: {refusal.detail}")
        return EXIT_FAIL

    emit_record({
        "action": "prune-continuity",
        "details": {
            "project_root": str(root),
            "path": str(path),
            "pruned": args.pruned,
            "plans_tombstoned": plans,
            "cold_rows_removed": colds,
            "bytes_before": before,
            "bytes_after": after,
            "live_rows": len(pruned_model.live),
            "cold_rows": len(pruned_model.cold),
            "retrieval": pruned_model.retrieval,
        },
    })
    return EXIT_OK
