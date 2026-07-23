"""Contained filesystem actions for shipped project migration entries.

The public ``apply-migration-entry`` command supplies only a registered project
root and an entry version.  This module owns the closed registry that maps that
version to exact project-local filesystem actions; callers cannot provide paths,
replacement text, or commands.

Config changes remain the responsibility of ``update-config``.  A migration
whose legacy value needs interpretation returns a structured pending action and
does not mutate any file.  Deterministic actions are preflighted as a set, then
applied with the same no-symlink / no-hardlink / parent-identity discipline used
by Cartopian's mediated writers.
"""
from __future__ import annotations

import os
import re
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from cli.atomic_write import (
    DIR_FD_SUPPORTED,
    GuardRefusal,
    _atomic_write_via_dir_fd,
    _atomic_write_via_path,
    _reverify_chain,
    _snapshot_chain,
    _within,
    make_tmp_name,
)
from cli.provenance import (
    migration_resolution_evidenced,
    migration_write_evidenced,
    record_delete,
    record_migration_pending,
    record_write,
)


@dataclass(frozen=True)
class PlannedWrite:
    action_kind: str
    dest_kind: str
    relative_target: str
    absolute_path: Path
    before: bytes
    after: bytes
    expected_exists: bool = True
    mode: int = 0o644


@dataclass(frozen=True)
class PlannedDelete:
    action_kind: str
    absolute_path: Path
    expected: bytes


@dataclass(frozen=True)
class MigrationPlan:
    writes: Tuple[PlannedWrite, ...] = ()
    deletes: Tuple[PlannedDelete, ...] = ()
    pending: Tuple[Dict[str, object], ...] = ()
    skipped: Tuple[Dict[str, object], ...] = ()


@dataclass(frozen=True)
class WrapperSubstitution:
    """One exact, tool-owned substitution in a project-local wrapper file."""

    relative_target: str
    old: bytes
    new: bytes


class MigrationApplyError(GuardRefusal):
    """An apply guard with the operations that landed before the refusal."""

    def __init__(
        self, refusal: GuardRefusal, operations: List[Dict[str, object]]
    ) -> None:
        self.operations = list(operations)
        super().__init__(refusal.rule, refusal.detail)


ENTRY_VERSIONS = ("v0.2.0", "v0.3.0", "v0.6.0")
_VERSION_RE = re.compile(r"^v\d+\.\d+\.\d+$")

# Shipped exact wrapper migrations.  No currently shipped project wrapper has a
# byte signature specific enough to rewrite generically, so v0.3.0 custom
# wrappers remain a pending PM action.  The closed action class is nevertheless
# executable when a future/maintainer-declared exact signature is added here.
WRAPPER_SUBSTITUTIONS: Dict[str, Tuple[WrapperSubstitution, ...]] = {
    "v0.3.0": (),
}

# Test-only seam: invoked after a deletion's parent chain and leaf identity are
# snapshotted, before the pinned parent fd is opened.  All guards run afterward.
_delete_concurrent_swap_hook = None
_write_concurrent_swap_hook = None
_force_path_delete = False


def _regular_file_snapshot(
    path: Path, base: Path
) -> Tuple[bytes, Tuple[int, int], int]:
    """Read one in-base regular, single-link file without following its leaf."""
    real_base = os.path.realpath(os.fspath(base))
    raw_path = os.path.abspath(os.fspath(path))
    parent = os.path.realpath(os.path.dirname(raw_path))
    if not _within(parent, real_base):
        raise GuardRefusal(
            "outside-allowlist", f"path escapes project subtree: {path}"
        )
    if os.path.islink(raw_path):
        raise GuardRefusal("symlink", f"migration target is a symlink: {path}")
    try:
        leaf_st = os.lstat(raw_path)
    except OSError as exc:
        raise GuardRefusal(
            "unreadable", f"cannot stat migration target {path}: {exc.strerror}"
        )
    if not stat.S_ISREG(leaf_st.st_mode):
        raise GuardRefusal("non-regular", f"migration target is not a regular file: {path}")
    if leaf_st.st_nlink > 1:
        raise GuardRefusal(
            "hardlink", f"migration target has {leaf_st.st_nlink} links: {path}"
        )

    snapshot = _snapshot_chain(parent, real_base)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(raw_path, flags)
    except OSError as exc:
        raise GuardRefusal(
            "unreadable", f"cannot open migration target {path}: {exc.strerror}"
        )
    try:
        opened_st = os.fstat(fd)
        if (opened_st.st_dev, opened_st.st_ino) != (leaf_st.st_dev, leaf_st.st_ino):
            raise GuardRefusal("toctou", f"migration target identity changed: {path}")
        if not stat.S_ISREG(opened_st.st_mode) or opened_st.st_nlink > 1:
            raise GuardRefusal("toctou", f"migration target type changed: {path}")
        chunks = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        _reverify_chain(snapshot)
        return (
            b"".join(chunks),
            (opened_st.st_dev, opened_st.st_ino),
            stat.S_IMODE(opened_st.st_mode),
        )
    finally:
        os.close(fd)


def _regular_file_bytes(path: Path, base: Path) -> bytes:
    return _regular_file_snapshot(path, base)[0]


def _guarded_delete(
    project_root: Path, entry_version: str, item: PlannedDelete
) -> None:
    """Delete an exact preflighted file with pinned-parent re-verification."""
    root = os.path.realpath(os.fspath(project_root))
    path = os.path.abspath(os.fspath(item.absolute_path))
    parent = os.path.realpath(os.path.dirname(path))
    if parent != root:
        raise GuardRefusal(
            "outside-allowlist",
            f"retirement target is not a project-root file: {path}",
        )
    name = os.path.basename(path)
    if os.path.islink(path):
        raise GuardRefusal("symlink", f"retirement target is a symlink: {path}")
    try:
        leaf_st = os.lstat(path)
    except OSError as exc:
        raise GuardRefusal(
            "delete-failed", f"cannot stat retirement target {path}: {exc.strerror}"
        )
    if not stat.S_ISREG(leaf_st.st_mode):
        raise GuardRefusal("non-regular", f"retirement target is not a regular file: {path}")
    if leaf_st.st_nlink > 1:
        raise GuardRefusal(
            "hardlink", f"retirement target has {leaf_st.st_nlink} links: {path}"
        )
    snapshot = _snapshot_chain(parent, root)
    if _delete_concurrent_swap_hook is not None:
        _delete_concurrent_swap_hook()

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if DIR_FD_SUPPORTED and not _force_path_delete:
        dir_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            dir_fd = os.open(parent, dir_flags)
        except OSError as exc:
            raise GuardRefusal(
                "toctou", f"retirement parent changed before open: {exc.strerror}"
            )
        try:
            fd_st = os.fstat(dir_fd)
            if (fd_st.st_dev, fd_st.st_ino) != (
                snapshot[-1][1].st_dev,
                snapshot[-1][1].st_ino,
            ):
                raise GuardRefusal("toctou", "retirement parent identity changed")
            _reverify_chain(snapshot)
            try:
                fd = os.open(name, flags, dir_fd=dir_fd)
            except OSError as exc:
                raise GuardRefusal(
                    "toctou",
                    f"retirement target changed before open: {exc.strerror}",
                )
            try:
                opened_st = os.fstat(fd)
                if (opened_st.st_dev, opened_st.st_ino) != (leaf_st.st_dev, leaf_st.st_ino):
                    raise GuardRefusal(
                        "toctou", f"retirement target identity changed: {path}"
                    )
                data = _read_fd(fd)
            finally:
                os.close(fd)
            if data != item.expected:
                raise GuardRefusal(
                    "unexpected-content",
                    f"retirement target changed after preflight: {path}",
                )
            _reverify_chain(snapshot)
            now = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            if (now.st_dev, now.st_ino) != (leaf_st.st_dev, leaf_st.st_ino):
                raise GuardRefusal(
                    "toctou", f"retirement target identity changed: {path}"
                )
            os.unlink(name, dir_fd=dir_fd)
            try:
                os.fsync(dir_fd)
            except OSError:
                pass
        finally:
            os.close(dir_fd)
    else:
        _reverify_chain(snapshot)
        try:
            fd = os.open(path, flags)
        except OSError as exc:
            raise GuardRefusal(
                "toctou", f"retirement target changed before open: {exc.strerror}"
            )
        try:
            opened_st = os.fstat(fd)
            if (opened_st.st_dev, opened_st.st_ino) != (leaf_st.st_dev, leaf_st.st_ino):
                raise GuardRefusal(
                    "toctou", f"retirement target identity changed: {path}"
                )
            data = _read_fd(fd)
        finally:
            os.close(fd)
        if data != item.expected:
            raise GuardRefusal(
                "unexpected-content",
                f"retirement target changed after preflight: {path}",
            )
        _reverify_chain(snapshot)
        now = os.lstat(path)
        if (now.st_dev, now.st_ino) != (leaf_st.st_dev, leaf_st.st_ino):
            raise GuardRefusal(
                "toctou", f"retirement target identity changed: {path}"
            )
        try:
            os.unlink(path)
        except OSError as exc:
            raise GuardRefusal("delete-failed", f"could not retire {path}: {exc.strerror}")
    if not record_delete(
        root,
        path,
        action=f"migration-entry:{entry_version}:{item.action_kind}",
    ):
        raise GuardRefusal(
            "provenance-failed", f"could not record retirement provenance: {path}"
        )


def _guarded_migration_write(
    project_root: Path, item: PlannedWrite
) -> Dict[str, object]:
    """Atomically land one preflighted registry write with exact preconditions."""
    root = os.path.realpath(os.fspath(project_root))
    subtrees = {
        "task": "tasks",
        "review": "reviews",
        "report": "reports",
        "standards": "",
        "wrapper": "wrappers",
    }
    if item.dest_kind not in subtrees:
        raise GuardRefusal(
            "invalid-registry", f"unknown migration destination: {item.dest_kind}"
        )
    subtree = subtrees[item.dest_kind]
    base = root if not subtree else os.path.realpath(os.path.join(root, subtree))
    path = os.path.abspath(os.fspath(item.absolute_path))
    parent = os.path.realpath(os.path.dirname(path))
    if not _within(base, root) or not _within(parent, base):
        raise GuardRefusal(
            "outside-allowlist", f"migration target escapes its allowlist: {path}"
        )
    if item.dest_kind == "standards" and path != os.path.join(root, "STANDARDS.md"):
        raise GuardRefusal(
            "invalid-registry", "standards migration target must be STANDARDS.md"
        )
    if not os.path.isdir(base) or not os.path.isdir(parent):
        raise GuardRefusal("missing-parent", f"migration target parent is missing: {path}")
    name = os.path.basename(path)
    if item.expected_exists:
        current, expected_leaf, current_mode = _regular_file_snapshot(
            item.absolute_path, Path(base)
        )
        if current != item.before:
            raise GuardRefusal(
                "unexpected-content",
                f"migration target changed after preflight: {item.absolute_path}",
            )
        if current_mode != item.mode:
            raise GuardRefusal(
                "toctou", f"migration target mode changed: {item.absolute_path}"
            )
        expect_absent = False
    else:
        if os.path.lexists(path):
            raise GuardRefusal(
                "toctou", f"migration target appeared after preflight: {item.absolute_path}"
            )
        expected_leaf = None
        expect_absent = True
    snapshot = _snapshot_chain(parent, base)
    if _write_concurrent_swap_hook is not None:
        _write_concurrent_swap_hook()
    tmp_name = make_tmp_name(name)
    safe_mode = item.mode & 0o777
    if item.dest_kind != "wrapper" and safe_mode & 0o111:
        raise GuardRefusal(
            "exec-bit", f"migration artifact unexpectedly has executable mode: {path}"
        )
    if DIR_FD_SUPPORTED:
        _atomic_write_via_dir_fd(
            parent,
            snapshot,
            name,
            tmp_name,
            item.after,
            safe_mode,
            expected_leaf=expected_leaf,
            expect_absent=expect_absent,
            expected_data=item.before if item.expected_exists else None,
        )
    else:
        _atomic_write_via_path(
            parent,
            snapshot,
            name,
            tmp_name,
            item.after,
            safe_mode,
            expected_leaf=expected_leaf,
            expect_absent=expect_absent,
            expected_data=item.before if item.expected_exists else None,
        )
    return {"bytes": len(item.after), "mode": safe_mode}


def _read_fd(fd: int) -> bytes:
    chunks = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _walk_markdown(root: Path, dirname: str) -> Iterable[Path]:
    base = root / dirname
    if not os.path.lexists(base):
        return ()
    if base.is_symlink() or not base.is_dir():
        raise GuardRefusal("unsafe-tree", f"migration subtree is not a real directory: {base}")
    found: List[Path] = []
    for current, dirs, files in os.walk(base, followlinks=False):
        dirs.sort()
        files.sort()
        for dirname_part in dirs:
            child = Path(current) / dirname_part
            if child.is_symlink():
                raise GuardRefusal(
                    "symlink-parent",
                    f"migration subtree contains a symlink: {child}",
                )
        for filename in files:
            if not filename.endswith(".md"):
                continue
            candidate = Path(current) / filename
            # The guarded reader performs the final type/link checks.
            found.append(candidate)
    return tuple(found)


def _replace_anchored(data: bytes, old: bytes, new: bytes) -> bytes:
    lines = data.splitlines(keepends=True)
    old_count = sum(line.startswith(old) for line in lines)
    new_count = sum(line.startswith(new) for line in lines)
    if old_count > 1:
        raise GuardRefusal(
            "ambiguous-anchor", "retired field anchor occurs more than once in one artifact"
        )
    if old_count and new_count:
        raise GuardRefusal(
            "ambiguous-anchor", "retired and replacement field anchors both occur in one artifact"
        )
    return b"".join(new + line[len(old):] if line.startswith(old) else line for line in lines)


def _plan_anchored_replacements(
    root: Path,
    trees: Sequence[Tuple[str, str, bytes, bytes]],
) -> List[PlannedWrite]:
    writes = []
    for dirname, kind, old, new in trees:
        base = root / dirname
        for path in _walk_markdown(root, dirname):
            before = _regular_file_bytes(path, base)
            after = _replace_anchored(before, old, new)
            if after != before:
                writes.append(
                    PlannedWrite(
                        "anchored-substitution",
                        kind,
                        os.path.relpath(path, base),
                        path,
                        before,
                        after,
                        mode=stat.S_IMODE(os.lstat(path).st_mode),
                    )
                )
    return writes


def _read_project_table(root: Path) -> Dict[str, object]:
    config = root / "cartopian.toml"
    data = _regular_file_bytes(config, root)
    try:
        parsed = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise GuardRefusal("invalid-config", f"cannot parse project cartopian.toml: {exc}")
    project = parsed.get("project", {})
    if not isinstance(project, dict):
        raise GuardRefusal("invalid-config", "cartopian.toml [project] must be a table")
    return project


def _read_work_roots(root: Path) -> Tuple[str, ...]:
    values = _read_project_table(root).get("work_roots", [])
    if values is None:
        return ()
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise GuardRefusal("invalid-config", "project.work_roots must be a list of names")
    return tuple(values)


def _plan_wrapper_substitutions(
    root: Path, declarations: Sequence[WrapperSubstitution]
) -> Tuple[List[PlannedWrite], List[Dict[str, object]], set[str]]:
    """Preflight registry-declared exact wrapper substitutions."""
    writes: List[PlannedWrite] = []
    skipped: List[Dict[str, object]] = []
    declared_paths: set[str] = set()
    base = root / "wrappers"
    for declaration in declarations:
        rel = declaration.relative_target.replace("\\", "/")
        parts = Path(rel).parts
        if (
            not rel
            or os.path.isabs(declaration.relative_target)
            or ".." in parts
            or parts[0] in ("", ".")
        ):
            raise GuardRefusal(
                "invalid-registry", "declared wrapper target is not a clean relative path"
            )
        if not declaration.old or not declaration.new:
            raise GuardRefusal(
                "invalid-registry", "declared wrapper substitution must be non-empty"
            )
        declared_paths.add(rel)
        path = base.joinpath(*parts)
        if not os.path.lexists(path):
            skipped.append(
                {
                    "kind": "wrapper-substitution",
                    "target": f"wrappers/{rel}",
                    "status": "skipped",
                    "reason": "declared wrapper file is absent",
                }
            )
            continue
        before = _regular_file_bytes(path, base)
        old_count = before.count(declaration.old)
        new_count = before.count(declaration.new)
        if old_count == 0 and new_count == 1:
            skipped.append(
                {
                    "kind": "wrapper-substitution",
                    "target": f"wrappers/{rel}",
                    "status": "skipped",
                    "reason": "declared substitution is already applied",
                }
            )
            continue
        if old_count != 1 or new_count:
            raise GuardRefusal(
                "unexpected-content",
                f"declared wrapper signature does not match exactly once: wrappers/{rel}",
            )
        mode = stat.S_IMODE(os.lstat(path).st_mode)
        writes.append(
            PlannedWrite(
                "wrapper-substitution",
                "wrapper",
                rel,
                path,
                before,
                before.replace(declaration.old, declaration.new, 1),
                True,
                mode,
            )
        )
    return writes, skipped, declared_paths


def _unknown_wrapper_files(
    wrappers: Path, declared_paths: set[str]
) -> List[str]:
    """Return safe regular wrapper files not covered by declarations."""
    unknown: List[str] = []
    for current, dirs, files in os.walk(wrappers, followlinks=False):
        dirs.sort()
        files.sort()
        for dirname in dirs:
            path = Path(current) / dirname
            if path.is_symlink():
                raise GuardRefusal(
                    "symlink-parent", f"wrapper tree contains a symlink: {path}"
                )
        for filename in files:
            path = Path(current) / filename
            if path.is_symlink():
                raise GuardRefusal(
                    "symlink", f"wrapper tree contains a symlink: {path}"
                )
            file_st = os.lstat(path)
            if not stat.S_ISREG(file_st.st_mode):
                raise GuardRefusal(
                    "non-regular", f"wrapper tree contains a special file: {path}"
                )
            if file_st.st_nlink > 1:
                raise GuardRefusal(
                    "hardlink", f"wrapper file has {file_st.st_nlink} links: {path}"
                )
            rel = os.path.relpath(path, wrappers).replace(os.sep, "/")
            if rel not in declared_paths:
                unknown.append(rel)
    return unknown


def _work_root_transform(
    root: Path, dirname: str, kind: str, old: bytes, new: bytes, _declared: Tuple[str, ...]
) -> Tuple[List[PlannedWrite], List[Dict[str, object]]]:
    writes: List[PlannedWrite] = []
    pending: List[Dict[str, object]] = []
    base = root / dirname
    for path in _walk_markdown(root, dirname):
        before = _regular_file_bytes(path, base)
        lines = before.splitlines(keepends=True)
        old_count = sum(line.startswith(old) for line in lines)
        new_count = sum(line.startswith(new) for line in lines)
        if old_count > 1 or (old_count and new_count):
            raise GuardRefusal(
                "ambiguous-anchor",
                f"legacy work-root anchor is ambiguous in {path}",
            )
        changed = False
        out = []
        for lineno, line in enumerate(lines, start=1):
            if not line.startswith(old):
                out.append(line)
                continue
            raw_value = line[len(old):].rstrip(b"\r\n").strip()
            try:
                value = raw_value.decode("utf-8")
            except UnicodeDecodeError:
                value = ""
            if value == "n/a":
                out.append(new + line[len(old):])
                changed = True
            else:
                out.append(line)
                pending.append(
                    {
                        "kind": "work-root-value",
                        "path": os.path.relpath(path, root).replace(os.sep, "/"),
                        "line": lineno,
                        "detail": (
                            "map the legacy path fragment to declared "
                            "project.work_roots name(s)"
                        ),
                    }
                )
        after = b"".join(out)
        if changed:
            writes.append(
                PlannedWrite(
                    "anchored-substitution",
                    kind,
                    os.path.relpath(path, base),
                    path,
                    before,
                    after,
                    mode=stat.S_IMODE(os.lstat(path).st_mode),
                )
            )
    return writes, pending


def _is_canonical_conventions_placeholder(data: bytes) -> bool:
    try:
        text = data.decode("utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError:
        return False
    lines = text.rstrip("\n").split("\n")
    if not lines or not lines[0].startswith("# ") or not lines[0].endswith(" - Conventions"):
        return False
    return lines[1:] == [
        "",
        "This document extends the protocol-level conventions defined in "
        "`protocol/CONVENTIONS.md`. Rules here apply only to this project.",
        "",
        "## Project-specific conventions",
        "",
        "<!-- Add project-specific naming rules, workflow modifications, or",
        "     constraints here. Delete this comment when you add real content. -->",
    ]


def plan_entry(project_root: Path, entry_version: str) -> MigrationPlan:
    """Preflight one closed-registry migration entry without mutating files."""
    root = Path(os.path.realpath(os.fspath(project_root)))
    if entry_version not in ENTRY_VERSIONS:
        raise GuardRefusal(
            "unknown-entry",
            f"no filesystem migration registry entry for {entry_version}",
        )

    marker = _read_project_table(root).get("protocol_version")
    if marker is not None and (
        not isinstance(marker, str) or not _VERSION_RE.match(marker)
    ):
        raise GuardRefusal(
            "invalid-config", "project.protocol_version must be a vX.Y.Z string"
        )
    if isinstance(marker, str) and marker >= entry_version:
        return MigrationPlan(
            skipped=(
                {
                    "kind": "entry",
                    "target": ".",
                    "status": "skipped",
                    "reason": f"project marker {marker} is already at or beyond {entry_version}",
                },
            )
        )

    if entry_version == "v0.2.0":
        writes = _plan_anchored_replacements(
            root,
            (
                ("tasks", "task", b"Test gate:", b"Evidence gate:"),
                ("reviews", "review", b"Test gate:", b"Evidence gate:"),
            ),
        )
        deletes: List[PlannedDelete] = []
        skipped: List[Dict[str, object]] = []
        old = root / "ENGINEERING.md"
        new = root / "STANDARDS.md"
        if os.path.lexists(old):
            old_data = _regular_file_bytes(old, root)
            if os.path.lexists(new):
                new_data = _regular_file_bytes(new, root)
                if new_data != old_data or not migration_write_evidenced(
                    root,
                    new,
                    new_data,
                    entry_version=entry_version,
                    action_kind="rename",
                ):
                    raise GuardRefusal(
                        "rename-collision",
                        "ENGINEERING.md and STANDARDS.md both exist",
                    )
                skipped.append(
                    {
                        "kind": "rename",
                        "target": "STANDARDS.md",
                        "status": "skipped",
                        "reason": "provenance confirms a partial rename destination",
                    }
                )
            else:
                old_mode = stat.S_IMODE(os.lstat(old).st_mode)
                writes.insert(
                    0,
                    PlannedWrite(
                        "rename",
                        "standards",
                        "STANDARDS.md",
                        new,
                        b"",
                        old_data,
                        False,
                        old_mode,
                    ),
                )
            deletes.append(PlannedDelete("rename", old, old_data))
        else:
            if not os.path.lexists(new):
                raise GuardRefusal(
                    "unexpected-source",
                    "neither ENGINEERING.md nor STANDARDS.md exists",
                )
            _regular_file_bytes(new, root)
            skipped.append(
                {
                    "kind": "rename",
                    "target": "ENGINEERING.md",
                    "status": "skipped",
                    "reason": "source already absent",
                }
            )
        return MigrationPlan(tuple(writes), tuple(deletes), (), tuple(skipped))

    if entry_version == "v0.3.0":
        declared = _read_work_roots(root)
        writes: List[PlannedWrite] = []
        pending: List[Dict[str, object]] = []
        for args in (
            ("tasks", "task", b"Repo subpath:", b"Work root:"),
            ("reviews", "review", b"Repo subpath:", b"Work root:"),
            ("reports", "report", b"- Repo subpath:", b"- Work root:"),
        ):
            found_writes, found_pending = _work_root_transform(root, *args, declared)
            writes.extend(found_writes)
            pending.extend(found_pending)
        wrapper_writes, wrapper_skips, declared_wrapper_paths = (
            _plan_wrapper_substitutions(
                root, WRAPPER_SUBSTITUTIONS.get(entry_version, ())
            )
        )
        writes.extend(wrapper_writes)
        wrappers = root / "wrappers"
        if os.path.lexists(wrappers):
            if wrappers.is_symlink() or not wrappers.is_dir():
                raise GuardRefusal(
                    "unsafe-tree",
                    f"custom wrapper path is not a real directory: {wrappers}",
                )
            unknown_wrapper_files = _unknown_wrapper_files(
                wrappers, declared_wrapper_paths
            )
            for wrapper_relpath in unknown_wrapper_files:
                wrapper_path = wrappers / wrapper_relpath
                wrapper_data = _regular_file_bytes(wrapper_path, wrappers)
                target = f"wrappers/{wrapper_relpath}"
                if migration_resolution_evidenced(
                    root,
                    wrapper_path,
                    wrapper_data,
                    entry_version=entry_version,
                    pending_kind="wrapper-migration",
                ):
                    wrapper_skips.append(
                        {
                            "kind": "wrapper-migration",
                            "target": target,
                            "status": "skipped",
                            "reason": "hash-pinned wrapper review is evidenced",
                        }
                    )
                    continue
                pending.append(
                    {
                        "kind": "wrapper-migration",
                        "path": target,
                        "detail": (
                            "verify this customized launcher uses the registered "
                            "project root as cwd, persist the review as a durable "
                            "decision, and reapply without changing the reviewed bytes"
                        ),
                    }
                )
        if not writes and not pending and not wrapper_skips:
            wrapper_skips.append(
                {
                    "kind": "entry",
                    "target": ".",
                    "status": "skipped",
                    "reason": "registered filesystem actions are already complete",
                }
            )
        return MigrationPlan(tuple(writes), (), tuple(pending), tuple(wrapper_skips))

    conventions = root / "CONVENTIONS.md"
    if not os.path.lexists(conventions):
        return MigrationPlan(
            skipped=(
                {
                    "kind": "retire",
                    "target": "CONVENTIONS.md",
                    "status": "skipped",
                    "reason": "target already absent",
                },
            )
        )
    data = _regular_file_bytes(conventions, root)
    if not _is_canonical_conventions_placeholder(data):
        if migration_resolution_evidenced(
            root,
            conventions,
            data,
            entry_version=entry_version,
            pending_kind="salvage-conventions",
        ):
            return MigrationPlan(
                deletes=(PlannedDelete("retire", conventions, data),)
            )
        return MigrationPlan(
            pending=(
                {
                    "kind": "salvage-conventions",
                    "path": "CONVENTIONS.md",
                    "detail": (
                        "review project-specific content, then persist salvaged "
                        "metadata in STANDARDS.md or record a durable decision "
                        "that no metadata should be retained; reapply this entry "
                        "without changing CONVENTIONS.md"
                    ),
                },
            )
        )
    return MigrationPlan(deletes=(PlannedDelete("retire", conventions, data),))


def record_pending_actions(
    project_root: Path, entry_version: str, plan: MigrationPlan
) -> None:
    """Persist hash-pinned receipts for pending actions that require evidence."""
    root = Path(os.path.realpath(os.fspath(project_root)))
    for action in plan.pending:
        kind = action.get("kind")
        if kind not in ("salvage-conventions", "wrapper-migration"):
            continue
        if kind == "salvage-conventions":
            relative_path = "CONVENTIONS.md"
            pending_kind = "salvage-conventions"
            base = root
        else:
            relative_path = str(action["path"])
            pending_kind = "wrapper-migration"
            base = root / "wrappers"
        path = root / relative_path
        data = _regular_file_bytes(path, base)
        recorded = record_migration_pending(
            root,
            path,
            data,
            entry_version=entry_version,
            pending_kind=pending_kind,
        )
        if not recorded:
            raise GuardRefusal(
                "provenance-failed",
                "could not persist the migration pending receipt",
            )


def apply_plan(
    project_root: Path, entry_version: str, plan: MigrationPlan
) -> List[Dict[str, object]]:
    """Apply a fully preflighted plan and return per-operation result records."""
    project_root = Path(os.path.realpath(os.fspath(project_root)))
    if plan.pending:
        return []
    results: List[Dict[str, object]] = list(plan.skipped)
    try:
        if plan.writes or plan.deletes:
            provenance_dir = project_root / ".cartopian"
            provenance_log = provenance_dir / "provenance.log"
            if os.path.lexists(provenance_dir):
                directory_st = os.lstat(provenance_dir)
                if not stat.S_ISDIR(directory_st.st_mode) or stat.S_ISLNK(
                    directory_st.st_mode
                ):
                    raise GuardRefusal(
                        "unsafe-provenance",
                        "project provenance directory is not a real directory",
                    )
            if os.path.lexists(provenance_log):
                log_st = os.lstat(provenance_log)
                if not stat.S_ISREG(log_st.st_mode) or log_st.st_nlink > 1:
                    raise GuardRefusal(
                        "unsafe-provenance",
                        "project provenance log is not a single-link regular file",
                    )
        for item in plan.writes:
            result = _guarded_migration_write(project_root, item)
            provenance = f"migration-entry:{entry_version}:{item.action_kind}"
            operation = {
                "kind": item.action_kind,
                "target": os.path.relpath(
                    item.absolute_path, project_root
                ).replace(os.sep, "/"),
                "status": "applied",
                "bytes": result["bytes"],
                "provenance": provenance,
            }
            if not record_write(
                project_root,
                item.absolute_path,
                item.after,
                action=provenance,
            ):
                operation["provenance_status"] = "blocked"
                results.append(operation)
                raise GuardRefusal(
                    "provenance-failed",
                    f"could not record migration provenance: {item.absolute_path}",
                )
            operation["provenance_status"] = "recorded"
            results.append(operation)
        for item in plan.deletes:
            provenance = f"migration-entry:{entry_version}:{item.action_kind}"
            try:
                _guarded_delete(project_root, entry_version, item)
            except GuardRefusal as exc:
                if exc.rule == "provenance-failed" and not os.path.lexists(
                    item.absolute_path
                ):
                    results.append(
                        {
                            "kind": item.action_kind,
                            "target": os.path.relpath(
                                item.absolute_path, project_root
                            ).replace(os.sep, "/"),
                            "status": "applied",
                            "provenance": provenance,
                            "provenance_status": "blocked",
                        }
                    )
                raise
            results.append(
                {
                    "kind": item.action_kind,
                    "target": os.path.relpath(
                        item.absolute_path, project_root
                    ).replace(os.sep, "/"),
                    "status": "applied",
                    "provenance": provenance,
                    "provenance_status": "recorded",
                }
            )
    except GuardRefusal as exc:
        results.append(
            {
                "kind": "entry",
                "target": ".",
                "status": "blocked",
                "reason": exc.rule,
            }
        )
        raise MigrationApplyError(exc, results) from exc
    except OSError as exc:
        refusal = GuardRefusal("apply-failed", f"migration operation failed: {exc.strerror}")
        raise MigrationApplyError(refusal, results) from exc
    return results
