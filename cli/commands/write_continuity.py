"""`cartopian write-continuity <project-root> --mode {index|ledger} ...`.

The mediated writer for the two preservation-bearing closeout outcomes. It
composes the header block, the live table, the cold counters, and the cold
index **from the project's own `decisions/` directory and the existing
`CONTINUITY.md`**, and takes only the six-group plan-ledger section body from
the PM through `--content` — the same split `write-state` (composed) and
`archive-plan` (`--content` for `CLOSEOUT.md`) already use.

Two passes, decided by one question — *is the resolved plan id already recorded?*

- A **compute** pass binds the plan id (to the archive just created in `index`
  mode, to a reservation in `ledger` mode), reads `decisions/`, resolves every
  supersession and expiry, recomputes the live set and cold index, and appends
  the plan's ledger section.
- A **ledger-replace** pass replaces only that plan's six-group body. The
  recorded heading, the live table, and the cold index are carried through byte
  for byte; `decisions/` is not read; a `--mode` or `--closed` that disagrees
  with the recorded heading is refused rather than silently relabelling a plan.

Nothing is destroyed until the continuity write has succeeded: `reset-plan` is
always last, and the § 11.3 plan-surface guard enforces that ordering rather
than trusting it.
"""
from __future__ import annotations

import argparse
import os
from datetime import date as _date
from pathlib import Path
from typing import List, Optional, Tuple

from cli import continuity as cy
from cli.commands import _writers
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE
from cli.mediated_write import GuardRefusal, mediated_write

_MODE_PRESERVATION = {
    "index": cy.PRESERVATION_INDEX,
    "ledger": cy.PRESERVATION_LEDGER,
}


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.description = (
        "Record a closed plan in the project-root CONTINUITY.md. `--mode index` "
        "runs after `archive-plan` and binds its rows to the archive just "
        "created; `--mode ledger` reserves the plan id under archive/ without "
        "archiving anything, and closes the plan's prompt-evidence window "
        "itself. Both run before `reset-plan`."
    )
    subparser.add_argument("project_root", help="Absolute Cartopian project root")
    subparser.add_argument(
        "--mode",
        required=True,
        choices=("index", "ledger"),
        help="index: full archive plus a compact continuity index. "
        "ledger: a compact continuity ledger instead of a full archive.",
    )
    subparser.add_argument(
        "--plan",
        default=None,
        help="PLAN-NNN. Required in index mode, where it names the archive the "
        "write binds to; optional in ledger mode, where it asserts the id the "
        "allocator would otherwise resolve.",
    )
    subparser.add_argument(
        "--closed", required=True, help="Closeout date in YYYY-MM-DD form"
    )
    subparser.add_argument(
        "--content", default=None, help="The six-group plan-ledger section body"
    )
    subparser.add_argument(
        "--content-file", default=None, help="Path to the six-group section body"
    )


def _stderr(prefix: str, message: str) -> None:
    _writers.stderr(prefix, message)


def _valid_date(value: str) -> bool:
    if not cy.DATE_RE.match(value):
        return False
    try:
        _date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _body_for(mode: str, qualified: str) -> str:
    return cy.locator_for(qualified) if mode == "index" else "none"


def _compose(
    root: Path,
    mode: str,
    binding: cy.PlanBinding,
    model: Optional[cy.Continuity],
    body_lines: List[str],
    closed: str,
) -> Tuple[cy.Continuity, dict]:
    """Run § 6.1 steps 5 to 8 for a compute pass and return the new model."""
    plan = binding.plan
    prior_live = list(model.live) if model is not None else []
    prior_cold = list(model.cold) if model is not None else []
    sections = list(model.sections) if model is not None else []
    pruned = model.pruned if model is not None else 0

    decisions = cy.read_decisions(root, plan)
    locked = [entry for entry in decisions if entry.locked]
    candidates = [entry for entry in locked if entry.candidate]
    for entry in locked:
        if entry.supersedes and not entry.candidate:
            raise cy.ContinuityRefusal(
                "continuity-supersession-without-replacement",
                f"{entry.path} carries Supersedes: but publishes no Scope:/Ruling: "
                "pair. Publish one if something replaces the old rule, or use "
                "Expires: if nothing does.",
            )

    universe = [row.id for row in prior_live] + [entry.qualified for entry in candidates]
    multimap = cy.removal_multimap(locked, universe, plan)

    by_id = {entry.qualified: entry for entry in candidates}
    prior_by_id = {row.id: row for row in prior_live}

    def dec_key(identity: str) -> Tuple[int, int]:
        left, right = identity.split("/", 1)
        return (int(left[5:]), int(right[4:]))

    new_rows = [
        cy.LiveRow(
            entry.qualified, entry.scope, entry.ruling, _body_for(mode, entry.qualified)
        )
        for entry in sorted(candidates, key=lambda e: e.dec, reverse=True)
        if entry.qualified not in multimap
    ]
    live = new_rows + [row for row in prior_live if row.id not in multimap]

    cold_new: List[cy.ColdRow] = []
    for target in sorted(multimap, key=dec_key, reverse=True):
        removing, disposition = multimap[target][0]
        if target in prior_by_id:
            source = prior_by_id[target]
            scope, ruling, body = source.scope, source.ruling, source.body
        else:
            entry = by_id[target]
            scope, ruling = entry.scope, entry.ruling
            body = _body_for(mode, target)
        cold_new.append(
            cy.ColdRow(target, scope, ruling, disposition, removing, body)
        )
    cold = cold_new + prior_cold

    if mode == "index":
        for row in new_rows + cold_new:
            if not row.has_locator or not row.id.startswith(f"{plan}/"):
                continue
            if not (root / row.body).is_file():
                raise cy.ContinuityRefusal(
                    "continuity-locator-absent",
                    f"{row.id} names {row.body}, which does not exist under the "
                    f"archive this write is bound to",
                )

    sections.append(
        cy.LedgerSection(plan, closed, _MODE_PRESERVATION[mode], body_lines)
    )
    sections.sort(key=lambda section: section.plan)
    composed = cy.Continuity(live=live, cold=cold, sections=sections, pruned=pruned)
    detail = {
        "removal_only": sorted(
            entry.qualified
            for entry in locked
            if not entry.candidate and entry.removes
        ),
        "skipped_unscoped": sorted(
            entry.qualified
            for entry in locked
            if not entry.candidate and not entry.removes
        ),
    }
    return composed, detail


def _replace(
    model: cy.Continuity, plan: str, mode: str, closed: str, body_lines: List[str]
) -> cy.Continuity:
    """Run § 6.1 step 8 for a ledger-replace pass.

    The recorded heading carries the two facts a later reader depends on — the
    closed date, and whether that plan's rows should be expected to carry
    locators — and neither is a property of the invocation correcting the prose.
    """
    recorded = model.section(plan)
    if recorded is None:  # pragma: no cover - pass selection already proved it
        raise cy.ContinuityRefusal(
            "continuity-plan-id-mismatch", f"{plan} is not recorded"
        )
    if recorded.preservation != _MODE_PRESERVATION[mode]:
        raise cy.ContinuityRefusal(
            "continuity-mode-mismatch",
            f"--mode {mode} disagrees with the recorded preservation value "
            f"{recorded.preservation} for {plan}",
        )
    if recorded.closed != closed:
        raise cy.ContinuityRefusal(
            "continuity-closed-date-mismatch",
            f"--closed {closed} disagrees with the recorded closed date "
            f"{recorded.closed} for {plan}",
        )
    sections = [
        cy.LedgerSection(
            section.plan, section.closed, section.preservation, list(body_lines)
        )
        if section.plan == plan
        else section
        for section in model.sections
    ]
    return cy.Continuity(
        live=list(model.live),
        cold=list(model.cold),
        sections=sections,
        pruned=model.pruned,
    )


def handler(args: argparse.Namespace) -> int:  # noqa: C901 - one ordered pass
    root, err = _writers.validated_root(args.project_root)
    if err is not None:
        _stderr("usage", err)
        return EXIT_USAGE
    if not (root / "cartopian.toml").is_file():
        _stderr("guard", f"not a Cartopian project root: {root}")
        return EXIT_FAIL
    if not _valid_date(args.closed):
        _stderr("usage", f"--closed must be YYYY-MM-DD; got: {args.closed!r}")
        return EXIT_USAGE
    if args.plan is not None and not cy.PLAN_ID_RE.match(args.plan):
        _stderr("usage", f"--plan must match PLAN-NNN grammar; got: {args.plan!r}")
        return EXIT_USAGE
    if args.mode == "index" and args.plan is None:
        _stderr(
            "usage",
            "--plan is required in index mode: it names the archive this write "
            "binds to",
        )
        return EXIT_USAGE

    raw, content_error = _writers.resolve_content(args)
    if content_error is not None:
        _stderr("usage", content_error)
        return EXIT_USAGE
    try:
        content = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    except UnicodeDecodeError:
        _stderr("usage", "ledger section body must be valid UTF-8")
        return EXIT_USAGE
    body_lines, violation = cy.ledger_body_lines(content)
    if violation is not None:
        _stderr("usage", violation)
        return EXIT_USAGE

    reservation_created: Optional[str] = None
    reservation_repaired: List[str] = []
    binding: Optional[cy.PlanBinding] = None
    reservation_bound = False
    publication: Optional[cy.Publication] = None
    pre_read_collapse = False

    try:
        # The collapse runs on both sides of a publication. The pre-read one is
        # what makes a surviving residue recoverable at all: at st_nlink > 1 the
        # § 8 reader guard and the primitive's own hardlink guard both refuse.
        path = cy.artifact_path(root)
        if os.path.lexists(path):
            collapse = cy.collapse_residue(root, path)
            pre_read_collapse = collapse.hardlinked
            if collapse.surviving:
                raise cy.ContinuityRefusal(
                    "continuity-publication-cleanup-failed",
                    "publication residue survived beside the artifact: "
                    + ", ".join(collapse.surviving),
                )

        artifact = cy.read_artifact(root)
        model = artifact.model

        if args.mode == "index":
            binding = cy.select_recorded_pass(model, args.plan)
            if binding is None:
                binding = cy.bind_archive(root, model, args.plan)
        else:
            binding = cy.resolve_ledger_plan(root, model, args.plan)

        if binding.kind == cy.PASS_COMPUTE:
            if not (root / "IMPLEMENTATION_PLAN.md").is_file():
                raise cy.ContinuityRefusal(
                    "continuity-plan-surface-absent",
                    "root IMPLEMENTATION_PLAN.md is absent, so reset may already "
                    "have reached decisions/; restore the plan surface before "
                    "recomputing the live set",
                )
            if args.mode == "ledger":
                if cy.witness_contradicts(root, binding.plan):
                    raise cy.ContinuityRefusal(
                        "continuity-ledger-window-foreign",
                        f"this compute pass would bind {binding.plan}, but the "
                        f"prompt-evidence log names window "
                        f"{cy.window_witness(root) or '<mixed>'}",
                    )
                if binding.source == cy.SOURCE_ADOPTED:
                    reservation_repaired = cy.adopt_reservation(
                        root, binding.reservation, args.closed
                    )
                else:
                    reservation_created = str(
                        cy.create_reservation(root, binding.plan, args.closed)
                    )
                reservation_bound = True
            composed, detail = _compose(
                root, args.mode, binding, model, body_lines, args.closed
            )
        else:
            if model is None:  # pragma: no cover - a replace implies a model
                raise cy.ContinuityRefusal(
                    "continuity-plan-id-mismatch", "no artifact to replace in"
                )
            composed = _replace(model, binding.plan, args.mode, args.closed, body_lines)
            detail = {"removal_only": [], "skipped_unscoped": []}

        section = composed.section(binding.plan)
        if section.unit_bytes() > cy.LEDGER_SECTION_MAX_BYTES:
            raise cy.ContinuityRefusal(
                "continuity-ledger-section-overlong",
                f"the composed ledger section for {binding.plan} is "
                f"{section.unit_bytes()} bytes against the "
                f"{cy.LEDGER_SECTION_MAX_BYTES}-byte bound",
            )
        text = cy.serialize_continuity(composed)
        size = cy.enforce_ceiling(root, composed, text)
        data = text.encode("utf-8")

        publication = cy.publish(
            root,
            path,
            artifact.raw,
            data,
            lambda: mediated_write(root, "continuity", cy.CONTINUITY_BASENAME, data),
        )
        if publication.outcome == cy.NOT_PUBLISHED:
            # A pre-commit failure carries the primitive's own refusal code; the
            # publication step only decides which side of the commit it landed on.
            refusal = publication.refusal
            raise cy.ContinuityRefusal(
                getattr(refusal, "rule", "continuity-publication-not-published"),
                f"{path} was not published"
                + (f": {getattr(refusal, 'detail', refusal)}" if refusal is not None else ""),
            )
        if publication.outcome == cy.UNRESOLVED:
            raise cy.ContinuityRefusal(
                "continuity-publication-unresolved",
                f"{path} holds neither the bytes this pass read nor the bytes it "
                "was publishing; repair it under the read-error contract, or "
                "remove it to return the project to the disabled path, then re-run",
            )
        if publication.surviving:
            raise cy.ContinuityRefusal(
                "continuity-publication-cleanup-failed",
                "the publication committed but its residue survived the collapse: "
                + ", ".join(publication.surviving)
                + "; re-run this command, whose pre-read collapse removes it",
            )
    except cy.ContinuityRefusal as refusal:
        marker = None
        if (
            args.mode == "ledger"
            and binding is not None
            and binding.kind == cy.PASS_COMPUTE
            and reservation_bound
            and (publication is None or publication.outcome == cy.NOT_PUBLISHED)
        ):
            marker = cy.write_failure_marker(root, binding.plan)
        _stderr("guard", f"{refusal.rule}: {refusal.detail}")
        if marker is not None:
            _stderr(
                "guard",
                f"marker: {marker} "
                f"({cy.archive_root(root) / binding.plan / cy.LEDGER_FAILED_BASENAME})",
            )
        return EXIT_FAIL
    except GuardRefusal as refusal:
        _stderr("guard", f"{refusal.rule}: {refusal.detail}")
        return EXIT_FAIL

    closeout = None
    if args.mode == "ledger":
        closeout = cy.close_evidence_window(root, binding.plan, args.closed)

    emit_record({
        "action": "write-continuity",
        "details": {
            "project_root": str(root),
            "path": str(path),
            "mode": args.mode,
            "plan": binding.plan,
            "plan_source": binding.source,
            "closed": args.closed,
            "bytes": size,
            "recomputed": binding.kind == cy.PASS_COMPUTE,
            "already_written": artifact.raw == data,
            "live_rows": len(composed.live),
            "cold_rows": len(composed.cold),
            "retrieval": composed.retrieval,
            "superseded": [
                row.id
                for row in composed.cold
                if row.disposition == cy.DISPOSITION_SUPERSEDED
            ],
            "expired": [
                row.id
                for row in composed.cold
                if row.disposition == cy.DISPOSITION_EXPIRED
            ],
            "removal_only": detail["removal_only"],
            "skipped_unscoped": detail["skipped_unscoped"],
            "publication": publication.outcome,
            "publication_residue_collapsed": (
                publication.residue_collapsed or pre_read_collapse
            ),
            "reservation_created": reservation_created,
            "reservation_repaired": reservation_repaired,
            "stale_reservation": binding.stale_reservation,
            "window_witness": binding.witness,
            "effectiveness_closeout": closeout,
        },
    })
    return EXIT_OK
