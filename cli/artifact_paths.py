"""Contained resolution of the project artifacts a command is handed.

A command that takes ``--task`` or ``--review`` is handed a path by a caller.
Treating any readable absolute file as a governance input is the wrong
posture: it lets an unrelated file outside the project — or a symlink planted
inside it — be read as this project's task or review, and every downstream
identity, determination, and evidence record then addresses something the
project does not own.

This module is the allowlist those paths pass through. A resolved artifact:

* is **absolute**, and stays inside one of a fixed set of project
  subdirectories after ``..`` is collapsed lexically, so traversal cannot
  reach out of the root and back in;
* lives **directly** in one of those subdirectories — no nested tree;
* is reached through **no symlink**, at the leaf or at any parent component
  below the real project root;
* is a **regular file with one link**, opened ``O_NOFOLLOW`` and re-checked on
  the descriptor so the identity that was stat'd is the identity that is read;
* is read through a **pinned parent directory descriptor** where the platform
  supports one, so a parent component swapped for a symlink after validation
  cannot redirect the open (``O_NOFOLLOW`` alone protects only the leaf); on
  Windows, which has no descriptor-relative open, the opened handle's final
  path must equal the validated path, and any other platform without
  ``dir_fd`` support **fails closed** — there is no unverified full-path
  fallback;
* decodes as **UTF-8**, because an artifact that does not is not a governance
  document this protocol can parse.

Every refusal names its rule and is fail-closed: nothing partial is returned.
The rules are the ones the specification's containment boundary already
states; this module is where the trace and intake commands enforce them.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Sequence, Tuple

#: The four status directories a governed task may live in.
TASK_SUBDIRS: Tuple[str, ...] = (
    "tasks/open",
    "tasks/in-progress",
    "tasks/in-review",
    "tasks/done",
)

#: Closure reviews live in exactly one directory.
REVIEW_SUBDIRS: Tuple[str, ...] = ("reviews",)


class ArtifactRefusal(Exception):
    """A supplied path is not a contained project artifact. Nothing was read."""

    def __init__(self, rule: str, detail: str) -> None:
        self.rule = rule
        self.detail = detail
        super().__init__(f"{rule}: {detail}")


def _allowed_bases(real_root: str, subdirs: Sequence[str]) -> list[str]:
    return [os.path.join(real_root, *sub.split("/")) for sub in subdirs]


def resolve(
    project_root: os.PathLike | str,
    candidate: os.PathLike | str,
    *,
    subdirs: Sequence[str],
    label: str,
) -> Path:
    """Return the contained path for ``candidate``, or raise ``ArtifactRefusal``.

    The check is lexical first and filesystem second, deliberately: collapsing
    ``..`` before touching the disk means a traversal attempt is refused by
    the same rule whether or not the path it points at happens to exist.
    """
    raw = os.fspath(candidate)
    if not os.path.isabs(raw):
        raise ArtifactRefusal(
            "not-absolute", f"{label} must be an absolute path; got: {raw}"
        )
    real_root = os.path.realpath(os.fspath(project_root))
    normalized = os.path.normpath(raw)
    parent = os.path.dirname(normalized)

    # Match the supplied parent against each allowed subdirectory by resolving
    # it, so a project root reached through a symlinked ancestor (``/var`` on
    # macOS, a mounted checkout) still matches, while ``..`` cannot walk out of
    # the root and a planted link cannot walk in.
    matched: str | None = None
    supplied_root: str | None = None
    for sub in subdirs:
        depth = len(sub.split("/"))
        candidate_root = parent
        for _ in range(depth):
            candidate_root = os.path.dirname(candidate_root)
        if os.path.realpath(candidate_root) != real_root:
            continue
        if os.path.realpath(parent) != os.path.join(real_root, *sub.split("/")):
            continue
        matched, supplied_root = sub, candidate_root
        break
    if matched is None or supplied_root is None:
        raise ArtifactRefusal(
            "outside-allowlist",
            f"{label} is not a project artifact under "
            + ", ".join(sub + "/" for sub in subdirs)
            + f": {raw}",
        )
    # Resolution alone would accept a symlinked status directory that happens
    # to point at another allowed one. Every component the caller actually
    # named below the project root must itself be a real directory.
    walked = supplied_root
    for part in os.path.relpath(parent, supplied_root).split(os.sep):
        walked = os.path.join(walked, part)
        if os.path.islink(walked):
            raise ArtifactRefusal(
                "symlink", f"a parent component of {label} is a symlink: {walked}"
            )
    if os.path.islink(normalized):
        raise ArtifactRefusal("symlink", f"{label} is a symlink: {raw}")
    try:
        leaf = os.lstat(normalized)
    except FileNotFoundError:
        raise ArtifactRefusal("missing", f"{label} does not exist: {raw}") from None
    except OSError as exc:
        raise ArtifactRefusal(
            "unreadable", f"cannot inspect {label} {raw}: {exc.strerror}"
        ) from None
    if not stat.S_ISREG(leaf.st_mode):
        raise ArtifactRefusal("non-regular", f"{label} is not a regular file: {raw}")
    if leaf.st_nlink > 1:
        raise ArtifactRefusal(
            "hardlink", f"{label} has {leaf.st_nlink} links: {raw}"
        )
    # The canonical path, not the one supplied: every downstream identity is
    # then derived from the same string the containment check approved.
    return Path(os.path.join(os.path.realpath(parent), os.path.basename(normalized)))


#: Whether this platform can pin a directory with a descriptor and stat/open
#: relative to it. Without it, a Windows read is verified on the opened
#: handle instead (``_verify_final_path``), and any other platform fails
#: closed — there is no unverified full-path fallback.
_DIR_FD_SUPPORTED = os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd


def read(
    project_root: os.PathLike | str,
    candidate: os.PathLike | str,
    *,
    subdirs: Sequence[str],
    label: str,
) -> Tuple[Path, str]:
    """Resolve and read one contained artifact as UTF-8 text.

    ``O_NOFOLLOW`` protects only the last path component, so a validated
    parent directory swapped for a symlink after ``resolve`` would redirect a
    full-pathname open. Where the platform supports it, the validated parent
    is therefore pinned with a directory descriptor (itself opened
    ``O_NOFOLLOW``, so a symlinked parent refuses) and the leaf is stat'd and
    opened by basename relative to that descriptor — the directory the check
    approved is provably the directory the read goes through. On Windows,
    which supports no descriptor-relative open, the opened handle's final
    path is verified against the validated path instead. A platform that can
    do neither refuses: an unverified full-path open would silently retain
    the parent-swap race.
    """
    path = resolve(project_root, candidate, subdirs=subdirs, label=label)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if _DIR_FD_SUPPORTED:
        dir_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            dir_fd = os.open(path.parent, dir_flags)
        except OSError as exc:
            raise ArtifactRefusal(
                "unreadable",
                f"cannot pin {label} directory {path.parent}: {exc.strerror}",
            ) from None
        try:
            return _read_leaf(path, path.name, dir_fd, flags, label)
        finally:
            os.close(dir_fd)
    if os.name == "nt":
        return _read_leaf(path, path, None, flags, label, verify_handle=True)
    raise ArtifactRefusal(
        "uncontainable",
        f"this platform can neither pin {label}'s parent directory nor "
        f"verify the opened handle; refusing to read {path}",
    )


def _verify_final_path(fd: int, path: Path, label: str) -> None:
    """Windows: prove the opened handle references the validated path.

    With no ``dir_fd`` to pin the parent, a component swapped for a symlink
    or junction between validation and open redirects a full-pathname open.
    ``GetFinalPathNameByHandle`` names the file the handle actually
    references; anything but the validated canonical path — or any failure
    to obtain it — refuses, so this branch either reads the approved file or
    fails closed.
    """
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        handle = msvcrt.get_osfhandle(fd)
        buf = ctypes.create_unicode_buffer(32768)
        # 0 == FILE_NAME_NORMALIZED | VOLUME_NAME_DOS
        length = ctypes.windll.kernel32.GetFinalPathNameByHandleW(
            wintypes.HANDLE(handle), buf, len(buf), 0
        )
        if length == 0 or length >= len(buf):
            raise OSError("GetFinalPathNameByHandleW failed")
        final = buf.value
        if final.startswith("\\\\?\\UNC\\"):
            final = "\\\\" + final[8:]
        elif final.startswith("\\\\?\\"):
            final = final[4:]
        matches = os.path.normcase(os.path.normpath(final)) == os.path.normcase(
            os.path.normpath(os.fspath(path))
        )
    except Exception:
        raise ArtifactRefusal(
            "uncontainable",
            f"cannot verify the opened handle for {label}: {path}",
        ) from None
    if not matches:
        raise ArtifactRefusal(
            "toctou",
            f"{label} handle resolved outside the validated path: {path}",
        )


def _read_leaf(
    path: Path,
    target: os.PathLike | str,
    dir_fd: int | None,
    flags: int,
    label: str,
    *,
    verify_handle: bool = False,
) -> Tuple[Path, str]:
    try:
        before = os.stat(target, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        raise ArtifactRefusal("missing", f"{label} does not exist: {path}") from None
    except OSError as exc:
        raise ArtifactRefusal(
            "unreadable", f"cannot inspect {label} {path}: {exc.strerror}"
        ) from None
    try:
        fd = os.open(target, flags, dir_fd=dir_fd)
    except OSError as exc:
        raise ArtifactRefusal(
            "unreadable", f"cannot open {label} {path}: {exc.strerror}"
        ) from None
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ArtifactRefusal(
                "toctou", f"{label} changed identity between stat and open: {path}"
            )
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink > 1:
            raise ArtifactRefusal(
                "toctou", f"{label} changed type between stat and open: {path}"
            )
        if verify_handle:
            _verify_final_path(fd, path, label)
        chunks = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
    except OSError as exc:
        raise ArtifactRefusal(
            "unreadable", f"cannot read {label} {path}: {exc.strerror}"
        ) from None
    finally:
        os.close(fd)
    try:
        return path, b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ArtifactRefusal(
            "invalid-utf-8", f"{label} is not valid UTF-8: {path} ({exc})"
        ) from None


def task(project_root, candidate) -> Tuple[Path, str]:
    """Resolve and read one governed task file."""
    return read(project_root, candidate, subdirs=TASK_SUBDIRS, label="--task")


def review(project_root, candidate) -> Tuple[Path, str]:
    """Resolve and read one closure review file."""
    return read(project_root, candidate, subdirs=REVIEW_SUBDIRS, label="--review")
