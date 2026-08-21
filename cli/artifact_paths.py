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


def read(
    project_root: os.PathLike | str,
    candidate: os.PathLike | str,
    *,
    subdirs: Sequence[str],
    label: str,
) -> Tuple[Path, str]:
    """Resolve and read one contained artifact as UTF-8 text."""
    path = resolve(project_root, candidate, subdirs=subdirs, label=label)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    before = os.lstat(path)
    try:
        fd = os.open(path, flags)
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
