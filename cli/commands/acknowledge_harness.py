"""Operator-only acknowledgment of a Tier-3 (unconstrainable) PM harness.

This optional, separate action records the operator's acceptance of the
unconstrained risk for a ``(harness, project)`` pair into the project-root
ledger ``COMPATIBILITY.md``, written through the mediated writer — the only
path that may author that file. Lifecycle entry does not require this record;
unrecorded Tier-3 harnesses proceed with an advisory.

**Operator-owned, NOT a PM tool — and deliberately not a registered subcommand.**
The Cartopian MCP server auto-exposes every ``cli.main.SUBCOMMANDS`` entry as a
tool on the contained PM's surface (``mcp_server.server._tool_registry`` builds
tools from the CLI subparsers). Registering this command there would let the
*contained PM acknowledge its own unconstrained risk*, corrupting the operator
audit trail. This command is therefore **not** in ``SUBCOMMANDS`` /
``_real_handlers`` — it is maintenance/audit plumbing, not an end-user recovery
step.

It emits an NDJSON record on stdout for a success, and ``[error]`` /
``[guard]`` / ``[usage]`` stderr prefixes for failures, with the documented
exit codes. Stdlib-only.
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys
import tomllib
from pathlib import Path
from typing import Optional, Tuple

from cli.commands import _compatibility
from cli.commands._harness_tier import (
    TIER_ADVISORY,
    canonical_harness,
    classify_harness_tier,
)
from cli.emit import emit_record
from cli.mediated_write import GuardRefusal, mediated_write

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

_DATE_FMT = "%Y-%m-%d"


def _stderr(prefix: str, msg: str) -> None:
    sys.stderr.write(f"[{prefix}] {msg}\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cartopian-internal acknowledge-harness",
        description=(
            "OPERATOR-ONLY: record/revoke acknowledgment that a Tier-3 PM "
            "harness runs unconstrained for a project. Not a PM tool."
        ),
        add_help=True,
    )
    parser.add_argument("project_root", help="Absolute path to the Cartopian project root")
    parser.add_argument(
        "--harness",
        required=True,
        help="The PM harness to acknowledge (e.g. 'cascade'). Canonicalized.",
    )
    parser.add_argument(
        "--project-id",
        default=None,
        help="Project id to record. Default: [project].id from cartopian.toml.",
    )
    parser.add_argument(
        "--acknowledged-by",
        default=None,
        help="Operator identity/role recording the decision (required to acknowledge).",
    )
    parser.add_argument(
        "--rationale",
        default=None,
        help="Operator's stated reason for accepting the unconstrained risk.",
    )
    parser.add_argument(
        "--acknowledged-on",
        default=None,
        help="Decision date (YYYY-MM-DD). Default: today.",
    )
    parser.add_argument(
        "--revoke",
        action="store_true",
        help="Revoke the existing acknowledgment for (harness, project) instead of recording one.",
    )
    return parser


def _validated_root(raw: str) -> Tuple[Optional[Path], Optional[str]]:
    if not isinstance(raw, str) or not Path(raw).is_absolute():
        return None, f"project_root must be an absolute path; got: {raw!r}"
    root = Path(os.path.normpath(raw))
    if not root.is_dir():
        return None, f"project_root is not a directory: {raw}"
    return root, None


def _resolve_project_id(root: Path, explicit: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Return (project_id, error). Reads [project].id from cartopian.toml if not given."""
    if explicit:
        return explicit, None
    toml_path = root / "cartopian.toml"
    if not toml_path.exists():
        return None, f"project config not found: {toml_path} (pass --project-id explicitly)"
    try:
        with toml_path.open("rb") as fh:
            cfg = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return None, f"project config unreadable: {toml_path} — {exc}"
    project = cfg.get("project", {}) or {}
    pid = project.get("id")
    if not pid:
        return None, f"no [project].id in {toml_path} (pass --project-id explicitly)"
    return str(pid), None


def _validate_date(raw: str) -> Optional[str]:
    try:
        datetime.datetime.strptime(raw, _DATE_FMT)
    except ValueError:
        return f"invalid --acknowledged-on {raw!r}: expected YYYY-MM-DD"
    return None


def _write_ledger(root: Path, records, action: str, detail: dict) -> int:
    """Render + mediated-write the ledger, then emit the NDJSON success record."""
    body = _compatibility.render_ledger(records)
    try:
        result = mediated_write(
            root,
            _compatibility.LEDGER_DEST_KIND,
            _compatibility.LEDGER_FILENAME,
            body,
        )
    except GuardRefusal as refusal:
        _stderr("guard", f"{refusal.rule}: {refusal.detail}")
        return EXIT_FAIL
    detail = dict(detail)
    detail["path"] = result["path"]
    detail["bytes"] = result["bytes"]
    emit_record({"action": action, "details": detail})
    return EXIT_OK


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = _build_parser()
    try:
        args = parser.parse_args(list(argv))
    except SystemExit:
        return EXIT_USAGE

    root, err = _validated_root(args.project_root)
    if err is not None:
        _stderr("usage", err)
        return EXIT_USAGE

    harness = canonical_harness(args.harness)
    if not harness:
        _stderr("usage", f"--harness is empty/invalid: {args.harness!r}")
        return EXIT_USAGE

    project_id, perr = _resolve_project_id(root, args.project_id)
    if perr is not None:
        _stderr("error", perr)
        return EXIT_FAIL

    records = _compatibility.load_records(root)

    # ----- revoke path -----------------------------------------------------
    if args.revoke:
        revoked = _compatibility.revoke_record(records, harness, project_id)
        if revoked is None:
            _stderr(
                "guard",
                f"no-record: nothing to revoke for ({harness}, {project_id}) in "
                f"{_compatibility.LEDGER_FILENAME}",
            )
            return EXIT_FAIL
        return _write_ledger(
            root,
            revoked,
            "revoke-harness",
            {"harness": harness, "project_id": project_id, "revoked": True},
        )

    # ----- acknowledge path ------------------------------------------------
    if not args.acknowledged_by:
        _stderr("usage", "--acknowledged-by is required to record an acknowledgment")
        return EXIT_USAGE
    if not args.rationale:
        _stderr("usage", "--rationale is required to record an acknowledgment")
        return EXIT_USAGE

    # Detection is authoritative: only a genuinely unconstrainable (Tier-3)
    # harness may be acknowledged. Refuse to record a phantom acknowledgment for
    # a constrained harness (which needs none) — fail-closed against misuse.
    tier_result = classify_harness_tier(harness)
    if tier_result.tier != TIER_ADVISORY:
        _stderr(
            "guard",
            f"not-tier-3: harness '{harness}' classifies {tier_result.tier} "
            f"(constrained) — it needs no acknowledgment. {tier_result.reason}",
        )
        return EXIT_FAIL

    acknowledged_on = args.acknowledged_on
    if acknowledged_on is None:
        acknowledged_on = datetime.date.today().strftime(_DATE_FMT)
    else:
        derr = _validate_date(acknowledged_on)
        if derr is not None:
            _stderr("usage", derr)
            return EXIT_USAGE

    record = _compatibility.make_record(
        harness=harness,
        project_id=project_id,
        tier=tier_result.tier,
        missing_assets=tier_result.reason,
        acknowledged_by=args.acknowledged_by,
        acknowledged_on=acknowledged_on,
        rationale=args.rationale,
        revoked=False,
    )
    records = _compatibility.upsert_record(records, record)
    return _write_ledger(
        root,
        records,
        "acknowledge-harness",
        {
            "harness": record.harness,
            "project_id": record.project_id,
            "tier": record.tier,
            "acknowledged_by": record.acknowledged_by,
            "acknowledged_on": record.acknowledged_on,
            "revoked": False,
        },
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
