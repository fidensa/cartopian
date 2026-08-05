"""Select and load at most one bounded optional practice pack."""
import argparse

from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, stderr_guard
from cli.practice_packs import select_practice_pack


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Validate the five optional practice packs, deterministically match only "
        "declared task-envelope facts, and return exactly one bounded body or none."
    )
    parser.add_argument("--primary-outcome", action="append", default=[])
    parser.add_argument("--artifact-kind", action="append", default=[])
    parser.add_argument("--incidental-term", action="append", default=[])
    parser.add_argument("--exclusion", action="append", default=[])
    parser.add_argument("--lifecycle-substrate-activity", action="append", default=[])
    parser.add_argument(
        "--authorized-profile-hint",
        help="Authorized family or pack identity; only resolves an eligible collision",
    )


def handler(args: argparse.Namespace) -> int:
    result = select_practice_pack(
        {
            "primary_outcomes": args.primary_outcome,
            "artifact_kinds": args.artifact_kind,
            "incidental_terms": args.incidental_term,
            "exclusions": args.exclusion,
            "lifecycle_substrate_activities": args.lifecycle_substrate_activity,
            "authorized_profile_hint": args.authorized_profile_hint,
        }
    )
    emit_record({"action": "select-practice-pack", **result})
    if result["outcome"] in {"invalid", "ambiguous"}:
        error = result["error"]
        stderr_guard(f"{error['code']}: {error['detail']}")
        return EXIT_FAIL
    return EXIT_OK
