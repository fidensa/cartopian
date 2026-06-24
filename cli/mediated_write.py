"""Validated mediated-write primitive (FR-003, SPEC-01-002).

The single audited *sole writer* every structured PM-authoring command
(SPEC-01-003) calls. It is an **internal** tool-layer function: it is NOT
registered in :data:`cli.main.SUBCOMMANDS` and therefore never appears on the
PM's tool surface (CLI or MCP). Structured per-artifact commands (TASK-01-003)
wrap it; the PM never calls it with a free-form destination.

Design (per SPEC-01-002 Interface), stdlib-only (NF-001):

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
code (FR-014). No file content is ever echoed in errors.
"""
import binascii
import os
import stat
import sys
from typing import Callable, Dict, Optional, Union

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
# concrete per-artifact set is owned by SPEC-01-003 — this is the closed,
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
    "conventions": "",
    "state": "",
    "roadmap": "",
    "backlog": "",
    # FR-008 / TASK-02-002: the persisted advisory-acknowledgment ledger. This
    # is the single new entry extending the named-root-files allowlist (see
    # ROOT_FILES) — no directory entry, no other root file. The operator-only
    # acknowledgment command (cli.commands.acknowledge_harness) is the sole
    # caller; it writes the fixed basename COMPATIBILITY.md and nothing else.
    "compatibility": "",
}

# ---------------------------------------------------------------------------
# Fixed named-root-files allowlist.
#
# A root destination (a ``dest_kind`` whose subtree is ``""``) maps to exactly
# one permitted basename at the project root — the FR-003 "fixed set of named
# project-root files". The primitive refuses any other basename for a root
# kind, so a root ``dest_kind`` cannot be repurposed to author an arbitrary
# (non-config, non-dotfile) file at the project root. This binding is the
# writer-enforced form of FR-003's allowlist; it is strictly *tightening*
# (every existing root writer already passes its bound basename) and adds no
# new permission beyond the single COMPATIBILITY.md entry (NF-004 additive).
# ---------------------------------------------------------------------------
ROOT_FILES: Dict[str, str] = {
    "requirements": "REQUIREMENTS.md",
    "plan": "IMPLEMENTATION_PLAN.md",
    "standards": "STANDARDS.md",
    "conventions": "CONVENTIONS.md",
    "state": "STATE.md",
    "roadmap": "ROADMAP.md",
    "backlog": "BACKLOG.md",
    # The single FR-008 / TASK-02-002 extension. Net-new writable root file.
    "compatibility": "COMPATIBILITY.md",
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


class GuardRefusal(Exception):
    """A write was refused fail-closed. ``rule`` names the violated rule.

    Carrying a structured ``rule`` lets callers emit the FR-014 ``[guard]``
    line without re-parsing the message. No file content is included.
    """

    def __init__(self, rule: str, detail: str) -> None:
        self.rule = rule
        self.detail = detail
        super().__init__(f"{rule}: {detail}")


def _within(path: str, base: str) -> bool:
    """True iff ``path`` is ``base`` or lies strictly under it."""
    if path == base:
        return True
    return path.startswith(base + os.sep)


def _snapshot_chain(parent: str, base: str):
    """Snapshot every ancestor from ``base`` down to ``parent`` (inclusive).

    Returns a list of ``(path, os.stat_result)`` from ``lstat``. Refuses if any
    ancestor is a symlink or not a directory.
    """
    chain = []
    cur = parent
    while True:
        chain.append(cur)
        if cur == base:
            break
        nxt = os.path.dirname(cur)
        if nxt == cur:  # reached filesystem root without hitting base
            raise GuardRefusal(
                "outside-allowlist",
                f"parent chain does not pass through allowlisted root: {parent}",
            )
        cur = nxt
    chain.reverse()  # base first, parent last

    snapshot = []
    for p in chain:
        try:
            st = os.lstat(p)
        except OSError as exc:
            raise GuardRefusal(
                "parent-unverifiable", f"cannot stat ancestor {p}: {exc.strerror}"
            )
        if stat.S_ISLNK(st.st_mode):
            raise GuardRefusal(
                "symlink-parent", f"ancestor is a symlink: {p}"
            )
        if not stat.S_ISDIR(st.st_mode):
            raise GuardRefusal(
                "non-directory-parent", f"ancestor is not a directory: {p}"
            )
        snapshot.append((p, st))
    return snapshot


def _reverify_chain(snapshot) -> None:
    """Re-verify a snapshotted chain still matches by (dev, ino), no symlinks.

    Raises ``GuardRefusal('toctou', ...)`` on any drift — an ancestor that
    became a symlink, a non-directory, or a different inode.
    """
    for p, st in snapshot:
        try:
            now = os.lstat(p)
        except OSError as exc:
            raise GuardRefusal(
                "toctou", f"ancestor vanished during write: {p}: {exc.strerror}"
            )
        if stat.S_ISLNK(now.st_mode) or not stat.S_ISDIR(now.st_mode):
            raise GuardRefusal(
                "toctou", f"ancestor changed type during write: {p}"
            )
        if (now.st_dev, now.st_ino) != (st.st_dev, st.st_ino):
            raise GuardRefusal(
                "toctou", f"ancestor inode changed during write: {p}"
            )


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

    # 9b. Named-root-files allowlist (FR-003). A root destination (subtree "")
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

    open_dir_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    try:
        dir_fd = os.open(canonical_parent, open_dir_flags)
    except OSError as exc:
        # O_NOFOLLOW raises if the parent's final component became a symlink
        # between canonicalization and now — the classic TOCTOU swap.
        raise GuardRefusal(
            "toctou", f"parent directory could not be opened no-follow: {exc.strerror}"
        )

    if isinstance(content, str):
        data = content.encode("utf-8")
    elif isinstance(content, (bytes, bytearray)):
        data = bytes(content)
    else:
        os.close(dir_fd)
        raise GuardRefusal("bad-content", "content must be str or bytes")

    safe_mode = (mode & 0o777) & ~0o111  # never executable
    tmp_name = f"{final_name}.cartmp.{os.getpid()}.{binascii.hexlify(os.urandom(8)).decode()}"
    tmp_created = False
    try:
        # Re-verify the chain still matches before we trust the pinned fd, and
        # confirm the fd refers to the directory we snapshotted.
        fd_st = os.fstat(dir_fd)
        _, parent_st = snapshot[-1]
        if (fd_st.st_dev, fd_st.st_ino) != (parent_st.st_dev, parent_st.st_ino):
            raise GuardRefusal("toctou", "parent directory identity changed before open")
        _reverify_chain(snapshot)

        tmp_fd = os.open(
            tmp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            safe_mode,
            dir_fd=dir_fd,
        )
        tmp_created = True
        try:
            os.fchmod(tmp_fd, safe_mode)
            os.write(tmp_fd, data)
            os.fsync(tmp_fd)
        finally:
            os.close(tmp_fd)

        # Final TOCTOU re-verify immediately before the atomic swap.
        _reverify_chain(snapshot)

        os.replace(tmp_name, final_name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        tmp_created = False
        try:
            os.fsync(dir_fd)
        except OSError:
            pass  # directory fsync is best-effort; the replace already landed
        # FR-005 raw-edit detection floor: record mediated-writer provenance so
        # an out-of-band change to this artifact is later distinguishable from a
        # write that passed through here. Best-effort and fail-open for the write
        # (a missed record degrades to an advisory at audit, never a false guard).
        record_provenance(real_root, candidate, data, action="mediated-write")
    except GuardRefusal:
        if tmp_created:
            _silent_unlink(tmp_name, dir_fd)
        raise
    except OSError as exc:
        if tmp_created:
            _silent_unlink(tmp_name, dir_fd)
        raise GuardRefusal("write-failed", f"atomic write failed: {exc.strerror}")
    finally:
        os.close(dir_fd)

    return {
        "dest_kind": dest_kind,
        "path": candidate,
        "bytes": len(data),
        "mode": safe_mode,
    }


def _silent_unlink(name: str, dir_fd: int) -> None:
    try:
        os.unlink(name, dir_fd=dir_fd)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Internal CLI shim (FR-014 machine contract).
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
