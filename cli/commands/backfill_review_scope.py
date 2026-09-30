"""Record explicit legacy planning-review scope without editing historical bytes."""
import argparse
import re
import time
from pathlib import Path

from cli import artifact_paths, checkpoint_identity, provenance, report_identity
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE, stderr_guard, stderr_usage


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("project_root")
    parser.add_argument("--review", required=True, help="Legacy REVIEW-PLAN-NNN identity")
    parser.add_argument("--checkpoint", required=True, action="append", help="Canonical checkpoint whose approval this review records; repeat for a reviewed batch")
    parser.add_argument("--expected-identity", required=True, help="SHA256 of the unchanged historical review")


def handler(args: argparse.Namespace) -> int:
    root = Path(args.project_root)
    if not root.is_absolute() or not re.fullmatch(r"REVIEW-PLAN-\d{3}", args.review):
        stderr_usage("use an absolute project_root and legacy REVIEW-PLAN-NNN identity")
        return EXIT_USAGE
    if not report_identity.CONTENT_IDENTITY_RE.fullmatch(args.expected_identity):
        stderr_usage("--expected-identity must be sha256:<64 lowercase hex digits>")
        return EXIT_USAGE
    if not (root / "cartopian.toml").is_file():
        stderr_guard("project config not found for review scope backfill")
        return EXIT_FAIL
    checkpoints = sorted(set(args.checkpoint if isinstance(args.checkpoint, list) else [args.checkpoint]))
    scopes = {key: checkpoint_identity.identity_scope(key) for key in checkpoints}
    scope = scopes[checkpoints[0]]
    if any(not value["stage"] or not checkpoint_identity.CHECKPOINT_ID_RE.fullmatch(key)
           for key, value in scopes.items()):
        stderr_usage("--checkpoint must be a canonical, scope-addressed checkpoint")
        return EXIT_USAGE
    try:
        path = root / "reviews" / f"{args.review}.md"
        _, text = artifact_paths.review(root, path)
        identity = report_identity.content_identity(text)
        if identity != args.expected_identity:
            raise ValueError("historical review content identity changed")
        if checkpoint_identity.header(text, "Verdict") != "approve":
            raise ValueError("only an existing approved review can be backfilled")
        if checkpoint_identity.header(text, "Planning stage"):
            raise ValueError("review already carries explicit scope")
        rel = path.relative_to(root).as_posix()
        entries = [r for r in provenance._read_log(root) or []
                   if r.get("action") == "planning-review-scope" and r.get("relpath") == rel]
        if entries and any(sorted(r.get("checkpoints") or [r.get("checkpoint")]) != checkpoints or r.get("hash") != identity for r in entries):
            raise ValueError("review already has a different scope binding")
        if not entries and not provenance._append_record(root, {
            "relpath": rel, "hash": identity, "action": "planning-review-scope",
            "checkpoint": checkpoints[0], "checkpoints": checkpoints, "scope": scope, "content": text, "ts": time.time(),
        }):
            raise ValueError("scope provenance could not be recorded")
    except (artifact_paths.ArtifactRefusal, OSError, UnicodeError, ValueError) as exc:
        stderr_guard(f"review-scope-backfill-refused: {exc}")
        return EXIT_FAIL
    emit_record({"action": "backfill-review-scope", "review": args.review,
                 "checkpoint": checkpoints[0], "checkpoints": checkpoints, "review_content_identity": identity,
                 "scope": scope, "status": "idempotent" if entries else "recorded"})
    return EXIT_OK
