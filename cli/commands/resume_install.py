"""Read-only status, diagnosis, and resume planning for install/update runs."""
from __future__ import annotations

import argparse
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Mapping

from cli.emit import emit_record
from cli.install_state import stable_projection
from cli.install_workflow import (
    WorkflowRefusal,
    plan_workflow,
    surface_retry_profiles,
)
from cli.main import EXIT_FAIL, EXIT_OK, stderr_error
from cli.resume_state import (
    ProgressRefusal,
    build_envelope,
    portable_evidence,
    portable_evidence_diagnostics,
    progress_contract,
    read_progress,
    render_portable_evidence,
    resume_is_reusable,
)

_HEALTHY_COMPATIBILITY = ("compatible", "absent", "stale")


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.description = (
        "Diagnose persisted install/update progress against current "
        "observations and emit the deterministic remaining-work plan. This "
        "command performs no mutation, no restart, and no recovery action; it "
        "reports what is reusable, what is uncertain, and what recovery is "
        "available."
    )
    subparser.add_argument("source_root", type=Path)
    subparser.add_argument("install_root", type=Path)
    subparser.add_argument(
        "--operation",
        choices=("fresh-install", "update", "repair", "verification"),
        default="update",
    )
    subparser.add_argument(
        "--client",
        action="append",
        default=[],
        help="supported client identifier; repeat for more than one client",
    )
    subparser.add_argument(
        "--portable-evidence",
        action="store_true",
        help=(
            "also emit the portable evidence record and its operator-readable "
            "rendering, excluding internal recovery metadata"
        ),
    )


def _progress_status(
    prior: Mapping[str, Any]
) -> "OrderedDict[str, Any]":
    envelope = prior.get("envelope")
    envelope = envelope if isinstance(envelope, Mapping) else {}
    return OrderedDict(
        (
            ("classification", prior.get("classification")),
            ("detail", prior.get("detail")),
            ("lease_state", prior.get("lease_state")),
            ("status", envelope.get("status")),
            ("sequence", envelope.get("sequence")),
            ("terminal", envelope.get("terminal")),
            ("retention", envelope.get("retention")),
            ("recovery", envelope.get("recovery")),
        )
    )


def handler(args: argparse.Namespace) -> int:
    try:
        record = plan_workflow(
            source_root=args.source_root,
            install_root=args.install_root,
            operation=args.operation,
            clients=tuple(args.client),
        )
        prior = read_progress(args.install_root.expanduser().resolve())
    except (WorkflowRefusal, ProgressRefusal) as exc:
        stderr_error(str(exc))
        return EXIT_FAIL

    internal = record["internal"]
    assessment = internal["resume_assessment"]
    payload: Dict[str, Any] = OrderedDict(
        (
            # The closed vocabularies travel with the values derived from them,
            # so a consumer never has to guess what a classification can be.
            ("progress_contract", progress_contract()),
            ("progress", _progress_status(prior)),
            ("resume", assessment),
            ("affected_surface_plan", internal["affected_surface_plan"]),
            ("surface_retry_profiles", internal["surface_retry_profiles"]),
            ("workflow", stable_projection(record)),
        )
    )

    if args.portable_evidence:
        envelope = prior.get("envelope")
        # Reusability is a comparison against current observations, not a
        # property of the stored bytes.  Selecting on the intrinsic read
        # classification would pair a predecessor's source authority with this
        # plan's remaining work, so the assessment decides which single
        # observation set the record is built from.
        if not isinstance(envelope, Mapping) or not resume_is_reusable(
            str(assessment["compatibility"])
        ):
            # No reusable persisted envelope: render current observations so a
            # support operator still gets a portable view of the mixed state,
            # with the incompatible predecessor classified rather than merged.
            try:
                envelope = build_envelope(
                    record=record,
                    surface_profiles=surface_retry_profiles(),
                    projection=stable_projection(record),
                )
            except ProgressRefusal as exc:
                stderr_error(str(exc))
                return EXIT_FAIL
        try:
            portable = portable_evidence(envelope, assessment=assessment)
        except ProgressRefusal as exc:
            stderr_error(str(exc))
            return EXIT_FAIL
        violations = portable_evidence_diagnostics(portable)
        if violations:
            for item in violations:
                stderr_error(f"{item['code']}: {item['field']}: {item['detail']}")
            return EXIT_FAIL
        payload["portable_evidence"] = portable
        payload["portable_evidence_document"] = render_portable_evidence(
            portable
        )

    emit_record(payload)
    healthy = (
        assessment["compatibility"] in _HEALTHY_COMPATIBILITY
        and not assessment["uncertain"]
    )
    return EXIT_OK if healthy else EXIT_FAIL
