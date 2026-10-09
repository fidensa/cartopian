"""Agent-neutral access contract for project-contained work roots.

Configuration selects directories; explicit capabilities authorize operations.
Adapters may implement this contract or refuse it, never weaken it. External
work roots retain their existing contract.
"""
from __future__ import annotations

import argparse
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class WorkAccessError(ValueError):
    pass


def within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:
        return False


def contained_roots(project: Path, roots: Mapping[str, str]) -> tuple[str, ...]:
    project_real = os.path.realpath(project)
    return tuple(dict.fromkeys(
        os.path.realpath(path) for path in roots.values()
        if within(os.path.realpath(path), project_real)
    ))


def validate_supporting_roots(project: Path, roots: Mapping[str, str]) -> None:
    """Never let work-root precedence reclassify governance as work content."""
    from cli.claude_hook import _DIR_CLASSES, _ROOT_FILE_CLASSES

    project_real = os.path.realpath(project)
    reserved = {name.casefold() for name in (*_DIR_CLASSES, *_ROOT_FILE_CLASSES)}
    reserved.update(("requirements", "standards", "plans"))
    for name, raw in roots.items():
        if any(ord(character) < 32 or ord(character) == 127 for character in raw):
            raise WorkAccessError(f"work root {name!r} is not representable as a literal filesystem path")
        path = os.path.realpath(raw)
        if not within(path, project_real):
            # An authored name inside the project must not escape through an
            # existing symlink. External mappings keep their historical rules.
            if within(os.path.abspath(raw), project_real):
                raise WorkAccessError(f"work root {name!r} escapes the project through a link: {raw}")
            continue
        if path == project_real:
            raise WorkAccessError("a work root cannot be the project root; use resources/ or a supporting sibling")
        relative = os.path.relpath(path, project_real)
        head = Path(relative).parts[0]
        if head.startswith(".") or head.casefold() in reserved:
            raise WorkAccessError(f"work root {name!r} overlaps protected governance: {raw}; use resources/ or a supporting sibling")
        if path != os.path.abspath(raw) or ".." in Path(raw).parts:
            raise WorkAccessError(f"project-contained work root {name!r} must use a direct canonical path without traversal: {raw}")
        marker = os.path.join(path, ".cartopian-work-root-boundary")
        if os.path.lexists(marker):
            raise WorkAccessError(f"reserved work-root boundary path must be absent before launch: {marker}; remove that entry or select another root")
        # Check every supporting ancestor, including the root itself. Never
        # follow a link in a permission-bearing mapping.
        current = Path(path)
        while current != Path(project_real):
            try:
                info = current.lstat()
            except FileNotFoundError:
                break  # existence is a separate launch prerequisite
            if not stat.S_ISDIR(info.st_mode):
                raise WorkAccessError(f"work-root ancestor must be a direct directory: {current}")
            current = current.parent
    paths = contained_roots(project, roots)
    for index, path in enumerate(paths):
        for other in paths[index + 1:]:
            if within(path, other) or within(other, path):
                raise WorkAccessError(f"project-contained work roots must be disjoint: {path} and {other}")


@dataclass(frozen=True)
class WorkAccess:
    project: str
    contained: tuple[str, ...]
    readable: tuple[str, ...]
    writable: tuple[str, ...]

    @property
    def launch_cwd(self) -> str:
        return self.writable[0] if self.writable else self.project


def effective_access(
    project: Path, roots: Mapping[str, str], grants: Iterable[str], *, activated: bool
) -> WorkAccess:
    validate_supporting_roots(project, roots)
    contained = contained_roots(project, roots)
    held = frozenset(grants) if activated else frozenset()
    readable = contained if "read:work-roots" in held else ()
    writable = contained if {"read:work-roots", "write:worktree"} <= held else ()
    return WorkAccess(os.path.realpath(project), contained, readable, writable)


def adapter_problem(adapter: str, platform: str, access: WorkAccess, *, activated: bool) -> str | None:
    if not access.contained:
        return None
    if not activated:
        return "project-contained work roots require explicit role grants; declare grants (an empty list grants no access) before launch"
    if adapter != "claude" or platform != "darwin":
        return (
            f"project-contained work-root containment is unsupported by {adapter} on {platform}: "
            "this adapter cannot attest the complete filesystem and ancestor boundary; "
            "use the shipped Claude wrapper on native macOS, or configure an external work root. "
            "No unsandboxed fallback is permitted"
        )
    return None


def wrapper_preflight(
    adapter: str, project: Path, *, platform: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> WorkAccess:
    from cli.claude_launch_settings import _capability_context, _session_roles

    environ = os.environ if environ is None else environ
    resolution, roots = _capability_context(project, environ=environ)
    access = effective_access(project, roots, resolution.grants_for(_session_roles(environ)), activated=resolution.activated)
    problem = adapter_problem(adapter, platform or sys.platform, access, activated=resolution.activated)
    if problem:
        raise WorkAccessError(problem)
    if access.contained and not environ.get("CARTOPIAN_ROLE"):
        raise WorkAccessError("project-contained work roots require a dispatch-bound CARTOPIAN_ROLE; launch through cartopian dispatch")
    if access.contained:
        if os.path.realpath(environ.get("CARTOPIAN_LAUNCH_CWD", "")) != access.launch_cwd:
            raise WorkAccessError("project-contained work access requires launch cwd bound to " + access.launch_cwd)
        exported = environ.get("CARTOPIAN_WORK_ROOTS")
        if exported is not None:
            expected = {os.path.realpath(path) for path in roots.values()}
            received = {os.path.realpath(path) for path in exported.split(os.pathsep) if path}
            if received != expected:
                raise WorkAccessError("CARTOPIAN_WORK_ROOTS does not match configured work roots; launch through cartopian dispatch without path overrides")
    return access


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wrapper", required=True)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--platform", default=sys.platform)
    args = parser.parse_args()
    try:
        wrapper_preflight(args.wrapper, args.project_dir, platform=args.platform)
    except Exception as exc:
        print(f"[guard] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
