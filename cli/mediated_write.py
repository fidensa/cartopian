"""Validated mediated-write primitive.

The single audited *sole writer* every structured PM-authoring command
calls. It is an **internal** tool-layer function: it is NOT
registered in :data:`cli.main.SUBCOMMANDS` and therefore never appears on the
PM's tool surface (CLI or MCP). Structured per-artifact commands
wrap it; the PM never calls it with a free-form destination.

Design, stdlib-only:

- **Fixed-allowlist destinations.** ``(dest_kind, relative_target)`` maps to an
  absolute path under the cartopian project root. ``dest_kind`` is a member of a
  closed, enumerable set (:data:`DEST_KINDS`); the destination subtree is
  implied by the kind, never supplied free-form by the model.
- **Real-path canonicalization + in-allowlist assertion.** Any ``..`` or symlink
  whose canonical target escapes the allowlisted subtree is refused.
- **No-follow open + parent-chain re-verification.** The final component may not
  be a symlink; each ancestor up to the allowlisted root must be a real
  directory whose identity is unchanged between canonicalization and open. The
  parent directory is pinned by an ``O_NOFOLLOW | O_DIRECTORY`` fd and all I/O
  goes through that fd (``openat`` / ``renameat`` semantics), closing the TOCTOU
  window.
- **Refusals (fail closed).** Outside the allowlist; final-component symlink;
  ``..`` traversal; pre-existing non-regular file or hardlink (``st_nlink > 1``);
  an executable ``mode``; a recognized config file (``cartopian.toml``,
  ``cartopian.local.toml``, or any dotfile); a root destination whose basename
  is not the one named-root file bound to its ``dest_kind`` (:data:`ROOT_FILES`);
  or any case where the safety re-verification cannot be completed → refuse and
  write nothing.
- **Success.** Atomic temp-write in the same allowlisted dir + ``fsync`` +
  ``os.replace``, permissions masked to non-executable.

Refusals raise :class:`GuardRefusal` (``.rule`` names the violated rule). The
internal CLI shim :func:`main` surfaces success as an NDJSON record on stdout
and a refusal as a ``[guard] <rule>: <detail>`` stderr line with a non-zero exit
code. No file content is ever echoed in errors.
"""
import os
import stat
import sys
from typing import Callable, Dict, Optional, Union

from cli.atomic_write import (
    DIR_FD_SUPPORTED,
    GuardRefusal,
    _atomic_write_via_dir_fd,
    _atomic_write_via_path,
    _snapshot_chain,
    _within,
    make_tmp_name,
)
from cli.emit import emit_record
from cli.provenance import record_write as record_provenance

# Exit codes mirror cli.main (kept local to avoid importing the PM dispatcher,
# which would risk coupling this internal primitive to the exposed surface).
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

# ---------------------------------------------------------------------------
# Fixed destination allowlist.
#
# Each ``dest_kind`` maps to a subtree *relative to the project root*. ``""``
# means the project root itself (single-file root artifacts such as STATE.md);
# those rely on the config-file guard to refuse cartopian.toml / dotfiles. The
# concrete per-artifact set is the closed,
# enumerable category set the primitive enforces. Nothing outside it is
# writable.
# ---------------------------------------------------------------------------
DEST_KINDS: Dict[str, str] = {
    "task": "tasks",
    "spec": "specs",
    "phase": "phases",
    "prompt": "prompts",
    "report": "reports",
    "review": "reviews",
    "decision": "decisions",
    "requirements": "",
    "plan": "",
    "standards": "",
    "state": "",
    "roadmap": "",
    "backlog": "",
}

# ---------------------------------------------------------------------------
# Fixed named-root-files allowlist.
#
# A root destination (a ``dest_kind`` whose subtree is ``""``) maps to exactly
# one permitted basename at the project root — the fixed set of named
# project-root files. The primitive refuses any other basename for a root
# kind, so a root ``dest_kind`` cannot be repurposed to author an arbitrary
# (non-config, non-dotfile) file at the project root. This binding is the
# writer-enforced allowlist; it is strictly *tightening*
# (every existing root writer already passes its bound basename).
# ---------------------------------------------------------------------------
ROOT_FILES: Dict[str, str] = {
    "requirements": "REQUIREMENTS.md",
    "plan": "IMPLEMENTATION_PLAN.md",
    "standards": "STANDARDS.md",
    "state": "STATE.md",
    "roadmap": "ROADMAP.md",
    "backlog": "BACKLOG.md",
}

# Recognized config files: never writable through this primitive regardless of
# dest_kind. Dotfiles (basename starting with ".") are refused as a class.
_CONFIG_BASENAMES = frozenset({"cartopian.toml", "cartopian.local.toml"})

# Test-only injection seam. When set to a callable, it is invoked exactly once
# after the parent chain is snapshotted and before the parent directory fd is
# opened — letting a negative test deterministically simulate a concurrent
# attacker swapping the parent between canonicalization and open. It can only
# *inject an action at a fixed point*; every guard still runs afterward, so it
# cannot be used to bypass a check. Production never sets it.
_concurrent_swap_hook: Optional[Callable[[], None]] = None

# Platform dir-fd support is computed once in cli.atomic_write; re-exported here
# under the historical name so callers and tests can read it from this module.
_DIR_FD_SUPPORTED = DIR_FD_SUPPORTED

# Test-only seam: force the path-based fallback even on a dir-fd platform, so the
# Windows write path gets coverage on POSIX CI. Production never sets it.
_force_path_based: bool = False


def mediated_write(
    project_root: Union[str, os.PathLike],
    dest_kind: str,
    relative_target: str,
    content: Union[str, bytes],
    *,
    mode: int = 0o644,
) -> Dict[str, object]:
    """Write ``content`` to the allowlisted destination, or refuse fail-closed.

    Returns a result dict on success. Raises :class:`GuardRefusal` and writes
    nothing on any refusal. See module docstring for the full rule set.
    """
    # 1. dest_kind must be in the closed allowlist.
    if dest_kind not in DEST_KINDS:
        raise GuardRefusal(
            "unknown-dest-kind",
            f"{dest_kind!r} is not an allowlisted destination category",
        )

    # 2. mode may not request an executable bit (defense in depth: also masked
    #    off before write).
    if mode & 0o111:
        raise GuardRefusal(
            "exec-bit", f"mode {mode:#o} sets an executable bit"
        )

    # 3. relative_target sanity — must be a clean relative path with a real
    #    final filename. Absolute paths and traversal-only targets are refused
    #    up front; deeper traversal is caught by canonicalization below.
    if not isinstance(relative_target, str) or relative_target == "":
        raise GuardRefusal("bad-target", "relative_target must be a non-empty string")
    if "\x00" in relative_target:
        raise GuardRefusal("bad-target", "relative_target contains a NUL byte")
    if os.path.isabs(relative_target):
        raise GuardRefusal(
            "bad-target", f"relative_target must be relative: {relative_target!r}"
        )
    final_name = os.path.basename(os.path.normpath(relative_target))
    if final_name in ("", ".", ".."):
        raise GuardRefusal(
            "bad-target", f"relative_target has no file component: {relative_target!r}"
        )

    # 4. Canonicalize the project root.
    real_root = os.path.realpath(os.fspath(project_root))
    if not os.path.isdir(real_root):
        raise GuardRefusal(
            "bad-root", f"project root is not a directory: {real_root}"
        )

    # 5. Resolve and canonicalize the allowlisted base for this dest_kind, and
    #    assert it stays under the project root.
    subdir = DEST_KINDS[dest_kind]
    base = real_root if subdir == "" else os.path.realpath(os.path.join(real_root, subdir))
    if not _within(base, real_root):
        raise GuardRefusal(
            "outside-allowlist", f"resolved base escapes project root: {base}"
        )
    if not os.path.isdir(base):
        raise GuardRefusal(
            "missing-base", f"allowlisted base directory does not exist: {base}"
        )

    # 6. Canonicalize the parent (resolves any ``..`` / symlinks in the path
    #    *up to* the final component) and assert it is inside the base subtree.
    joined = os.path.join(base, relative_target)
    canonical_parent = os.path.realpath(os.path.dirname(joined))
    if not _within(canonical_parent, base):
        raise GuardRefusal(
            "outside-allowlist",
            f"destination escapes allowlisted subtree: {canonical_parent}",
        )
    if not os.path.isdir(canonical_parent):
        raise GuardRefusal(
            "missing-parent",
            f"destination parent directory does not exist: {canonical_parent}",
        )

    candidate = os.path.join(canonical_parent, final_name)

    # 7. Final component must not be a symlink (no-follow).
    if os.path.islink(candidate):
        raise GuardRefusal(
            "symlink", f"final path component is a symlink: {candidate}"
        )

    # 8. Belt-and-braces: the resolved candidate must remain in-subtree.
    if not _within(os.path.realpath(candidate), base):
        raise GuardRefusal(
            "outside-allowlist", f"canonical destination escapes subtree: {candidate}"
        )

    # 9. Config-file guard — refuse known config files and any dotfile.
    if final_name in _CONFIG_BASENAMES or final_name.startswith("."):
        raise GuardRefusal(
            "config-file", f"destination is a protected config file: {final_name}"
        )

    # 9b. Named-root-files allowlist. A root destination (subtree "")
    #     may write only the single fixed basename bound to its dest_kind. This
    #     refuses any non-allowlisted root file — a root kind cannot be turned
    #     into a free-form root-file writer. Directory kinds are unaffected.
    if subdir == "":
        expected_basename = ROOT_FILES.get(dest_kind)
        if expected_basename is None or final_name != expected_basename:
            raise GuardRefusal(
                "non-allowlisted-root-file",
                f"root dest_kind {dest_kind!r} may write only "
                f"{expected_basename or '<no allowlisted root file>'}, not {final_name}",
            )

    # 10. If the destination already exists it must be a plain regular file with
    #     a single link (a hardlink — st_nlink > 1 — may alias an out-of-subtree
    #     inode; a non-regular file is never a valid artifact destination).
    if os.path.lexists(candidate):
        st = os.lstat(candidate)
        if not stat.S_ISREG(st.st_mode):
            raise GuardRefusal(
                "non-regular", f"destination exists and is not a regular file: {candidate}"
            )
        if st.st_nlink > 1:
            raise GuardRefusal(
                "hardlink",
                f"destination is a hardlink (st_nlink={st.st_nlink}): {candidate}",
            )

    # 11. Snapshot the parent chain (base..parent), then open the parent dir fd
    #     with O_NOFOLLOW to pin it. All file I/O goes through this fd so a
    #     path-based swap after this point cannot redirect the write.
    snapshot = _snapshot_chain(canonical_parent, base)

    if _concurrent_swap_hook is not None:  # test-only injection seam
        _concurrent_swap_hook()

    if isinstance(content, str):
        data = content.encode("utf-8")
    elif isinstance(content, (bytes, bytearray)):
        data = bytes(content)
    else:
        raise GuardRefusal("bad-content", "content must be str or bytes")

    safe_mode = (mode & 0o777) & ~0o111  # never executable
    tmp_name = make_tmp_name(final_name)

    # Atomic, TOCTOU-re-verified write. Where the platform has directory file
    # descriptors (POSIX), pin the parent dir and route every step through an
    # openat-style fd so a path swap after canonicalization cannot redirect the
    # write. Where it does not (native Windows: no O_DIRECTORY / openat / dir_fd),
    # fall back to a path-based atomic write that keeps every guard above plus
    # os.replace atomicity, but cannot pin the dir fd (a documented degradation
    # of the anti-swap depth on those platforms).
    if _DIR_FD_SUPPORTED and not _force_path_based:
        _atomic_write_via_dir_fd(
            canonical_parent, snapshot, final_name, tmp_name, data, safe_mode
        )
    else:
        _atomic_write_via_path(
            canonical_parent, snapshot, final_name, tmp_name, data, safe_mode
        )

    # Record mediated-writer provenance so an out-of-band change to this artifact
    # is later distinguishable from a write that passed through here. Best-effort
    # and fail-open (a missed record degrades to an advisory at audit).
    record_provenance(real_root, candidate, data, action="mediated-write")

    return {
        "dest_kind": dest_kind,
        "path": candidate,
        "bytes": len(data),
        "mode": safe_mode,
    }


# ---------------------------------------------------------------------------
# Internal CLI shim.
#
# Provided for evidence/testing and for in-process use by structured commands.
# Deliberately NOT registered in cli.main.SUBCOMMANDS: it must never reach the
# PM tool surface (CLI or MCP).
# ---------------------------------------------------------------------------
def _stderr_guard(rule: str, detail: str) -> None:
    sys.stderr.write(f"[guard] {rule}: {detail}\n")


def main(argv=None) -> int:
    import argparse

    if argv is None:
        argv = sys.argv[1:]
    parser = argparse.ArgumentParser(
        prog="cartopian-internal mediated-write",
        description="INTERNAL mediated-write primitive (not a PM tool).",
        add_help=True,
    )
    parser.add_argument("project_root")
    parser.add_argument("dest_kind")
    parser.add_argument("relative_target")
    parser.add_argument(
        "--content", default="", help="literal content; or use --content-file"
    )
    parser.add_argument("--content-file", default=None)
    parser.add_argument("--mode", default="0o644")
    try:
        args = parser.parse_args(list(argv))
    except SystemExit:
        return EXIT_USAGE

    try:
        mode = int(args.mode, 8) if isinstance(args.mode, str) else int(args.mode)
    except ValueError:
        sys.stderr.write(f"[usage] invalid --mode: {args.mode!r}\n")
        return EXIT_USAGE

    if args.content_file is not None:
        with open(args.content_file, "rb") as fh:
            content: Union[str, bytes] = fh.read()
    else:
        content = args.content

    try:
        result = mediated_write(
            args.project_root, args.dest_kind, args.relative_target, content, mode=mode
        )
    except GuardRefusal as refusal:
        _stderr_guard(refusal.rule, refusal.detail)
        return EXIT_FAIL

    emit_record({"action": "mediated-write", "details": result})
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
