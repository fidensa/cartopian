"""`cartopian attest-intent` — the operator-only intent confirmation surface.

This is the **only** writer of operator-intent attestations, and it is
deliberately not reachable from any managed role:

- **No mediated-write destination.** ``intent/`` has no ``dest_kind`` in
  :data:`cli.mediated_write.DEST_KINDS`, so every structured PM authoring
  command — the PM's sole write path — physically cannot target it. The write
  here goes through this command's own guarded atomic writer.
- **No management-callable MCP tool.** The subcommand is listed in
  :data:`cli.main.OPERATOR_ONLY_SUBCOMMANDS`, which the MCP server excludes
  from its tool registry, so it never appears on a PM's tool surface.
- **No dispatched-session reachability.** ``cartopian dispatch`` exports
  ``CARTOPIAN_ROLE`` into every launched handoff; this command refuses to run
  whenever that marker is present, so a coder, reviewer, or any other assigned
  role cannot reach it even with a shell. It likewise refuses when invoked
  in-process by the MCP server (``CARTOPIAN_MCP_TOOL_CALL``).
- **No shipped preset.** No entry in :data:`cli.capabilities.PRESETS` confers
  it; there is no capability grant that unlocks it, because it is not gated by
  grants at all — it is gated by *not being on any agent's surface*.

The operator runs it interactively. ``--confirm`` is the explicit confirmation
token: the command refuses without it, so an accidental or scripted invocation
cannot mint an attestation.

The command binds the source's exact SHA-256 content identity at confirmation
time. It never accepts a hash from the caller, so an attestation can only ever
describe the bytes the operator actually confirmed.
"""
import argparse
import os
import stat
from dataclasses import replace
from pathlib import Path
from typing import List, Optional

from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE, stderr_guard, stderr_usage
from cli.operator_intent import (
    ATTESTATION_ID_RE,
    CONFIRMED_BY,
    DATE_RE,
    INTENT_DIRNAME,
    Attestation,
    IntentRefusal,
    Scope,
    SOURCE_KINDS,
    assert_source_eligible,
    content_identity,
    load_attestations,
    parse_scope,
    read_contained_bytes,
    render_attestation,
    select_sections,
)

#: Environment markers that prove this invocation is *not* a bare operator
#: session. ``CARTOPIAN_ROLE`` is exported by ``cartopian dispatch`` into every
#: launched handoff; ``CARTOPIAN_MCP_TOOL_CALL`` is set by the MCP server around
#: every in-process tool invocation.
NON_OPERATOR_MARKERS = ("CARTOPIAN_ROLE", "CARTOPIAN_MCP_TOOL_CALL")

SLUG_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789-"


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "project_root", help="Absolute path to the Cartopian project root"
    )
    subparser.add_argument(
        "--attestation-id",
        required=True,
        help="Attestation id in ATTEST-NNN form",
    )
    subparser.add_argument(
        "--slug", required=True, help="Kebab-case slug for the filename"
    )
    subparser.add_argument(
        "--title", required=True, help="Short attestation title"
    )
    subparser.add_argument(
        "--source-kind",
        required=True,
        choices=list(SOURCE_KINDS),
        help="Eligible source kind being confirmed",
    )
    subparser.add_argument(
        "--source",
        required=True,
        help="Project-relative path to the eligible source artifact",
    )
    subparser.add_argument(
        "--scope",
        action="append",
        default=None,
        help=(
            "Applicability scope (repeatable): project | phase:PHASE-NN-slug | "
            "plan-ref:PNN-KIND-NNN | task:TASK-NN-NNN | "
            "review-kind:planning|task-closure"
        ),
    )
    subparser.add_argument(
        "--section",
        action="append",
        default=None,
        help=(
            "Exact complete section heading to select (repeatable). Required "
            "when the source exceeds the whole-source byte bound."
        ),
    )
    subparser.add_argument(
        "--required",
        choices=("true", "false"),
        default="true",
        help="Requiredness of this evidence (default: true)",
    )
    subparser.add_argument(
        "--confirmed-at",
        required=True,
        help="Operator confirmation date in YYYY-MM-DD form",
    )
    subparser.add_argument(
        "--supersede",
        default=None,
        help="ATTEST-NNN this confirmation supersedes (marks it superseded)",
    )
    subparser.add_argument(
        "--confirm",
        action="store_true",
        help="Explicit operator confirmation; the command refuses without it",
    )


def _operator_session_error() -> Optional[str]:
    for marker in NON_OPERATOR_MARKERS:
        if os.environ.get(marker):
            return (
                f"{marker} is set: this is a dispatched or tool-mediated session, "
                "not an operator session. Operator-intent attestations are "
                "created only by the operator, directly, and are never reachable "
                "from the project-management, coder, or reviewer surfaces"
            )
    return None


def _guarded_write(project_root: Path, relative_target: str, content: str) -> Path:
    """Write one attestation artifact atomically inside ``intent/``.

    Refuses a symlinked or non-regular destination, a hardlink, and any path
    that escapes ``intent/``. This writer is local to the operator surface on
    purpose: routing it through the PM mediated writer would put ``intent/`` on
    the PM's allowlist.
    """
    real_root = os.path.realpath(os.fspath(project_root))
    base = os.path.join(real_root, INTENT_DIRNAME)
    os.makedirs(base, exist_ok=True)
    real_base = os.path.realpath(base)
    candidate = os.path.join(real_base, relative_target)
    if os.path.dirname(os.path.realpath(os.path.dirname(candidate))) and not (
        os.path.realpath(os.path.dirname(candidate)) == real_base
    ):
        raise IntentRefusal(
            "outside-allowlist",
            f"attestation destination escapes {INTENT_DIRNAME}/",
            "name a plain attestation filename",
        )
    if os.path.islink(candidate):
        raise IntentRefusal(
            "symlink",
            f"attestation destination is a symlink: {relative_target}",
            "remove the symlink and re-run the confirmation",
        )
    if os.path.lexists(candidate):
        info = os.lstat(candidate)
        if not stat.S_ISREG(info.st_mode):
            raise IntentRefusal(
                "non-regular",
                f"attestation destination is not a regular file: {relative_target}",
                "remove the destination and re-run the confirmation",
            )
        if info.st_nlink > 1:
            raise IntentRefusal(
                "hardlink",
                f"attestation destination is a hardlink: {relative_target}",
                "remove the destination and re-run the confirmation",
            )
    data = content.encode("utf-8")
    tmp = os.path.join(real_base, f".{relative_target}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, candidate)
    return Path(candidate)


def handler(args: argparse.Namespace) -> int:
    session_error = _operator_session_error()
    if session_error is not None:
        stderr_guard(f"operator-surface-only: {session_error}")
        return EXIT_FAIL
    if not args.confirm:
        stderr_guard(
            "unconfirmed: operator-intent attestation requires the explicit "
            "--confirm token; nothing was written"
        )
        return EXIT_FAIL

    raw_root = args.project_root
    if not Path(raw_root).is_absolute():
        stderr_usage(f"project_root must be an absolute path; got: {raw_root!r}")
        return EXIT_USAGE
    project_root = Path(os.path.normpath(raw_root))
    if not project_root.is_dir():
        stderr_usage(f"project_root is not a directory: {raw_root}")
        return EXIT_USAGE
    if not (project_root / "cartopian.toml").is_file():
        stderr_usage(f"no cartopian.toml at project root: {project_root}")
        return EXIT_USAGE

    attestation_id = args.attestation_id
    if not ATTESTATION_ID_RE.match(attestation_id):
        stderr_usage(
            f"--attestation-id must match ATTEST-NNN; got: {attestation_id!r}"
        )
        return EXIT_USAGE
    slug = args.slug
    if not slug or slug[0] not in SLUG_CHARS[:36] or any(
        ch not in SLUG_CHARS for ch in slug
    ):
        stderr_usage(f"--slug must be kebab-case [a-z0-9][a-z0-9-]*; got: {slug!r}")
        return EXIT_USAGE
    if not DATE_RE.match(args.confirmed_at):
        stderr_usage(f"--confirmed-at must be YYYY-MM-DD; got: {args.confirmed_at!r}")
        return EXIT_USAGE
    supersede = args.supersede
    if supersede is not None and not ATTESTATION_ID_RE.match(supersede):
        stderr_usage(f"--supersede must match ATTEST-NNN; got: {supersede!r}")
        return EXIT_USAGE

    raw_scopes: List[str] = list(args.scope or [])
    if not raw_scopes:
        stderr_usage(
            "at least one --scope is required; applicability is a closed union "
            "and may not be empty"
        )
        return EXIT_USAGE

    try:
        scopes = tuple(parse_scope(token) for token in raw_scopes)
        deduped: List[Scope] = []
        for scope in scopes:
            if scope not in deduped:
                deduped.append(scope)
        source_relpath = args.source.replace("\\", "/")
        raw = read_contained_bytes(
            project_root, source_relpath, what="attested source"
        )
        text = raw.decode("utf-8")
        sections = tuple(args.section or [])
        attestation = Attestation(
            attestation_id=attestation_id,
            status="current",
            confirmed_by=CONFIRMED_BY,
            confirmed_at=args.confirmed_at,
            source_kind=args.source_kind,
            source_relpath=source_relpath,
            source_hash=content_identity(raw),
            required=args.required == "true",
            scopes=tuple(deduped),
            sections=sections,
            supersedes=supersede,
            relpath=f"{INTENT_DIRNAME}/{attestation_id}-{slug}.md",
        )
        assert_source_eligible(project_root, attestation, text)
        if sections:
            selected = select_sections(text, sections, source_label=source_relpath)
            selected_bytes = len(
                "".join(body for _, body in selected).encode("utf-8")
            )
        else:
            selected_bytes = len(raw)
        from cli.operator_intent import PER_SOURCE_MAX_BYTES, WHOLE_SOURCE_MAX_BYTES

        if not sections and len(raw) > WHOLE_SOURCE_MAX_BYTES:
            raise IntentRefusal(
                "oversize-source",
                f"{source_relpath} is {len(raw)} bytes, above the "
                f"{WHOLE_SOURCE_MAX_BYTES}-byte whole-source bound",
                "confirm complete named sections with --section, or narrow the "
                "source",
            )
        if selected_bytes > PER_SOURCE_MAX_BYTES:
            raise IntentRefusal(
                "oversize-selection",
                f"{source_relpath} would contribute {selected_bytes} bytes, above "
                f"the {PER_SOURCE_MAX_BYTES}-byte per-source bound",
                "narrow or split the attestation; content is never truncated",
            )
    except UnicodeDecodeError:
        stderr_guard(f"malformed-attestation: {args.source} is not valid UTF-8")
        return EXIT_FAIL
    except IntentRefusal as refusal:
        stderr_guard(f"{refusal.rule}: {refusal.detail}")
        if refusal.recovery:
            stderr_guard(f"recovery: {refusal.recovery}")
        return EXIT_FAIL

    existing, invalid = load_attestations(project_root)
    if invalid:
        first = invalid[0]
        stderr_guard(
            f"{first['rule']}: {first['path']}: {first['detail']} — repair the "
            "existing attestation before confirming another"
        )
        return EXIT_FAIL
    by_id = {item.attestation_id: item for item in existing}
    if supersede is not None and supersede not in by_id:
        stderr_guard(
            f"unknown-attestation: --supersede {supersede} names no attestation "
            f"in {INTENT_DIRNAME}/"
        )
        return EXIT_FAIL
    if supersede == attestation_id:
        stderr_guard(
            "supersession-cycle: an attestation cannot supersede itself"
        )
        return EXIT_FAIL
    if supersede is not None and by_id[supersede].status != "current":
        stderr_guard(
            f"superseded-reference: --supersede {supersede} is already "
            "superseded; name its current successor"
        )
        return EXIT_FAIL
    clash = by_id.get(attestation_id)
    if clash is not None and clash.relpath != attestation.relpath:
        stderr_guard(
            f"duplicate-attestation: {attestation_id} already exists at "
            f"{clash.relpath}"
        )
        return EXIT_FAIL

    try:
        rendered = render_attestation(attestation, args.title.strip())
        attestation = replace(
            attestation,
            attestation_hash=content_identity(rendered.encode("utf-8")),
        )
        written = _guarded_write(
            project_root,
            f"{attestation_id}-{slug}.md",
            rendered,
        )
        superseded_path: Optional[Path] = None
        if supersede is not None:
            prior = by_id[supersede]
            prior_text = read_contained_bytes(
                project_root, prior.relpath, what="attestation"
            ).decode("utf-8")
            title_line = prior_text.splitlines()[0]
            prior_title = title_line.partition(":")[2].strip() or prior.attestation_id
            superseded = Attestation(
                attestation_id=prior.attestation_id,
                status="superseded",
                confirmed_by=prior.confirmed_by,
                confirmed_at=prior.confirmed_at,
                source_kind=prior.source_kind,
                source_relpath=prior.source_relpath,
                source_hash=prior.source_hash,
                required=prior.required,
                scopes=prior.scopes,
                sections=prior.sections,
                supersedes=prior.supersedes,
                relpath=prior.relpath,
            )
            superseded_rendered = render_attestation(superseded, prior_title)
            superseded = replace(
                superseded,
                attestation_hash=content_identity(
                    superseded_rendered.encode("utf-8")
                ),
            )
            superseded_path = _guarded_write(
                project_root,
                Path(prior.relpath).name,
                superseded_rendered,
            )
    except IntentRefusal as refusal:
        stderr_guard(f"{refusal.rule}: {refusal.detail}")
        return EXIT_FAIL
    except OSError as exc:
        stderr_guard(f"write-failed: could not write the attestation: {exc}")
        return EXIT_FAIL

    emit_record(
        {
            "action": "attest-intent",
            "details": {
                "attestation": attestation.as_record(),
                "attestation_path": str(written),
                "selected_bytes": selected_bytes,
                "source_bytes": len(raw),
                "superseded": supersede,
                "superseded_path": (
                    str(superseded_path) if superseded_path is not None else None
                ),
                "confirmation": "operator",
            },
        }
    )
    return EXIT_OK
