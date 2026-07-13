"""Shared TOCTOU-hardened atomic-write primitives (stdlib only).

Extracted from :mod:`cli.mediated_write` so more than one writer can reuse the
same anti-swap discipline. Two writers depend on this today:

- :mod:`cli.mediated_write` — the sole `.md` artifact writer (adds its own
  fixed-allowlist / config-file / named-root-file guards on top).
- :mod:`cli.commands.update_config` — the mediated `cartopian.toml` /
  `cartopian.local.toml` editor (adds its own must-exist / schema / effective-
  config guards on top).

Neither the config-file guard nor the allowlist lives here: this module is the
*mechanism* (snapshot the parent chain, pin the parent dir fd, re-verify by
`(dev, ino)`, atomic temp-write + `os.replace` + dir fsync), and each caller
supplies the *policy* before calling in. ``allow_create`` gates whether a
missing final component is a create (``update-config --local``) or must already
exist (every other caller pre-checks and passes ``True`` for the create-or-
replace default).

Refusals raise :class:`GuardRefusal` (``.rule`` names the violated rule); no
file content is ever echoed in errors.
"""
import binascii
import os
import stat
from typing import List, Tuple

# Whether this platform supports directory file descriptors / openat-style I/O
# (POSIX). Native Windows does not: it has no O_DIRECTORY, cannot os.open a
# directory, and os.supports_dir_fd is empty (no openat/renameat/unlinkat). The
# writer pins the parent dir by fd where this holds, and falls back to a
# path-based atomic write where it does not.
# NB: os.supports_dir_fd lists os.rename (the renameat proxy os.replace rides
# on), not os.replace itself, so the rename capability is probed via os.rename.
DIR_FD_SUPPORTED = (
    hasattr(os, "O_DIRECTORY")
    and {os.open, os.rename, os.unlink}.issubset(os.supports_dir_fd)
)


class GuardRefusal(Exception):
    """A write was refused fail-closed. ``rule`` names the violated rule.

    Carrying a structured ``rule`` lets callers emit the ``[guard]``
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


def _snapshot_chain(parent: str, base: str) -> List[Tuple[str, os.stat_result]]:
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


def make_tmp_name(final_name: str) -> str:
    """A unique in-directory temp name for the atomic temp-write step."""
    return f"{final_name}.cartmp.{os.getpid()}.{binascii.hexlify(os.urandom(8)).decode()}"


def _silent_unlink(name: str, dir_fd: int) -> None:
    try:
        os.unlink(name, dir_fd=dir_fd)
    except OSError:
        pass


def _silent_unlink_path(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _atomic_write_via_dir_fd(
    canonical_parent, snapshot, final_name, tmp_name, data, safe_mode
) -> None:
    """POSIX write: pin the parent directory by an ``O_NOFOLLOW | O_DIRECTORY``
    fd and route the temp create, write, and rename through it (openat/renameat),
    so a path swap after canonicalization cannot redirect the write. Raises
    :class:`GuardRefusal` on a TOCTOU mismatch or write failure."""
    open_dir_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        dir_fd = os.open(canonical_parent, open_dir_flags)
    except OSError as exc:
        # O_NOFOLLOW raises if the parent's final component became a symlink
        # between canonicalization and now — the classic TOCTOU swap.
        raise GuardRefusal(
            "toctou", f"parent directory could not be opened no-follow: {exc.strerror}"
        )
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


def _atomic_write_via_path(
    canonical_parent, snapshot, final_name, tmp_name, data, safe_mode
) -> None:
    """Fallback write for platforms without directory file descriptors / openat
    (native Windows). Keeps every platform-agnostic guard plus ``os.replace``
    atomicity and a final TOCTOU re-verification, but cannot pin the parent dir
    by fd — a documented degradation of the anti-swap depth on those platforms.
    Raises :class:`GuardRefusal` on a TOCTOU mismatch or write failure."""
    tmp_path = os.path.join(canonical_parent, tmp_name)
    final_path = os.path.join(canonical_parent, final_name)
    # O_BINARY (Windows) stops the low-level write from translating LF->CRLF in
    # text mode; it is 0 on POSIX. O_NOFOLLOW / O_EXCL guard the temp create.
    open_flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    tmp_created = False
    try:
        _reverify_chain(snapshot)
        fd = os.open(tmp_path, open_flags, safe_mode)
        tmp_created = True
        try:
            if hasattr(os, "fchmod"):
                try:
                    os.fchmod(fd, safe_mode)
                except OSError:
                    pass  # best-effort; the open mode already applied
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)

        # Final TOCTOU re-verify immediately before the atomic swap.
        _reverify_chain(snapshot)
        os.replace(tmp_path, final_path)
        tmp_created = False
    except GuardRefusal:
        if tmp_created:
            _silent_unlink_path(tmp_path)
        raise
    except OSError as exc:
        if tmp_created:
            _silent_unlink_path(tmp_path)
        raise GuardRefusal("write-failed", f"atomic write failed: {exc.strerror}")
