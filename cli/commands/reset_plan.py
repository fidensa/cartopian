"""`cartopian reset-plan <project-root>` (G13, G14, G15).

The mediated close-surface reset. Folds the close-plan Stage 4 directory ops
into one structured command (directory create/remove lives in
``reset-plan``/``archive-plan``, never a generic PM-exposed directory verb).
In one fail-closed pass it:

- **G13 — removes live plan artifacts:** ``REQUIREMENTS.md``,
  ``IMPLEMENTATION_PLAN.md``, and every file in ``phases/``,
  ``tasks/{open,in-progress,in-review,done}/``, ``specs/``, ``reviews/``,
  ``decisions/``. Prompts and reports are *not* touched here — they are
  cleared via the existing ``delete-prompt`` / ``delete-report`` commands.
- **G14 — recreates the empty lifecycle directories** (including ``prompts/``
  and ``reports/``).
- **G15 — conditionally reseeds** ``STANDARDS.md`` per the carry-forward flag.
  The reseed write goes through the mediated-write primitive; carry-forward
  leaves the file untouched.

Fail-closed allowlist guards: the target must be a real Cartopian project root
(``cartopian.toml`` present); every reset target is a member of a fixed,
closed, code-owned set relative to that root — the PM supplies only the root,
never a path. Any symlink, foreign subdirectory, or out-of-root target is
refused and **nothing is removed, created, or written**.

The preflight (``_preflight``) validates the *entire* plan before the first
destructive operation: the removable live artifacts, the lifecycle directory
(re)creations, **and** the default reseed destinations whenever a reseed will
occur (no carry-forward). Reseed destinations are checked with the same
fail-closed guards the mediated-write primitive would apply
(final-component symlink, escape-from-root, protected config file, pre-existing
non-regular file or hardlink), so the destructive phase never begins when a
later reseed write would be refused. A guard violation anywhere in the plan
aborts with nothing removed, nothing created, nothing written.
"""
import argparse
import os
import stat
import sys
from pathlib import Path
from typing import List

from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE
from cli.mediated_write import _CONFIG_BASENAMES, GuardRefusal, mediated_write

# Live root-file artifacts removed on reset (G13).
RESET_ROOT_FILES = ("REQUIREMENTS.md", "IMPLEMENTATION_PLAN.md")

# Directories whose *files* are removed on reset (G13). Flat, file-only.
RESET_CLEAR_DIRS = (
    "phases",
    "tasks/open",
    "tasks/in-progress",
    "tasks/in-review",
    "tasks/done",
    "specs",
    "reviews",
    "decisions",
)

# Lifecycle directories recreated empty after the clear (G14). prompts/ and
# reports/ are recreated but never cleared here (delete-prompt/delete-report).
RESET_ENSURE_DIRS = (
    "phases",
    "prompts",
    "reports",
    "tasks/open",
    "tasks/in-progress",
    "tasks/in-review",
    "tasks/done",
    "specs",
    "reviews",
    "decisions",
)

STANDARDS_SEED = (
    "# Standards: <project name>\n\n"
    "<!-- This document is highly recommended but optional. It captures the\n"
    "     domain-neutral standards and constraints that govern execution of\n"
    "     this project. Fill in what applies; delete what doesn't. -->\n\n"
    "## Tools and stack\n\n"
    "List the languages, frameworks, runtimes, or other tools the project relies on.\n\n"
    "## Working standards\n\n"
    "Style, formatting, naming, or process conventions the team follows.\n\n"
    "## Constraints\n\n"
    "Boundaries that execution must not cross.\n\n"
    "## Quality checks\n\n"
    "Acceptance evidence required by the project.\n\n"
    "## Open standards questions\n\n"
    "Decisions about standards that are still pending.\n"
)


class _ResetRefusal(Exception):
    """A reset target violated the fail-closed allowlist. Nothing was removed."""

    def __init__(self, rule: str, detail: str) -> None:
        self.rule = rule
        self.detail = detail
        super().__init__(f"{rule}: {detail}")


def _stderr(prefix: str, msg: str) -> None:
    sys.stderr.write(f"[{prefix}] {msg}\n")


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "project_root",
        help="Absolute path to the Cartopian project root to reset",
    )
    subparser.add_argument(
        "--carry-standards",
        action="store_true",
        help="Keep STANDARDS.md in place instead of reseeding it.",
    )


def _assert_in_root(real_root: str, candidate: Path, rule: str) -> None:
    """Refuse if ``candidate`` is a symlink or resolves outside the project root."""
    if candidate.is_symlink():
        raise _ResetRefusal(rule, f"reset target is a symlink: {candidate}")
    resolved = os.path.realpath(candidate)
    if resolved != real_root and not resolved.startswith(real_root + os.sep):
        raise _ResetRefusal("outside-allowlist", f"target escapes project root: {candidate}")


def _plan_removals(root: Path, real_root: str) -> List[Path]:
    """Scan all reset targets and return the regular files to remove.

    Runs the full fail-closed scan *before* any unlink: a symlink, a foreign
    subdirectory, or an out-of-root target raises ``_ResetRefusal`` and the
    caller removes nothing.
    """
    to_remove: List[Path] = []

    for name in RESET_ROOT_FILES:
        path = root / name
        if not path.exists() and not path.is_symlink():
            continue
        _assert_in_root(real_root, path, "symlink-target")
        if not path.is_file():
            raise _ResetRefusal("non-regular", f"reset target is not a regular file: {path}")
        to_remove.append(path)

    for rel in RESET_CLEAR_DIRS:
        directory = root / rel
        if not directory.exists():
            continue
        _assert_in_root(real_root, directory, "symlink-dir")
        if not directory.is_dir():
            raise _ResetRefusal("non-directory", f"reset target is not a directory: {directory}")
        for entry in sorted(directory.iterdir(), key=lambda p: p.name):
            if entry.is_symlink():
                raise _ResetRefusal("symlink-entry", f"foreign symlink in reset dir: {entry}")
            if entry.is_dir():
                raise _ResetRefusal("foreign-subdir", f"unexpected subdirectory in reset dir: {entry}")
            if not entry.is_file():
                raise _ResetRefusal("non-regular", f"unexpected non-file in reset dir: {entry}")
            _assert_in_root(real_root, entry, "symlink-entry")
            to_remove.append(entry)

    return to_remove


def _preflight_ensure_dir(real_root: str, root: Path, rel: str) -> None:
    """Validate a lifecycle directory we will (re)create (G14).

    Refuses if the path is a symlink, escapes the project root, or exists as a
    non-directory — so the ``mkdir`` phase cannot silently follow a symlink out
    of the root or collide with a foreign file.
    """
    directory = root / rel
    _assert_in_root(real_root, directory, "symlink-dir")
    if directory.exists() and not directory.is_dir():
        raise _ResetRefusal(
            "non-directory", f"lifecycle path exists and is not a directory: {directory}"
        )


def _preflight_reseed_dest(real_root: str, target: str) -> None:
    """Validate the default reseed destination (``STANDARDS.md``).

    Mirrors the fail-closed guards the mediated-write primitive applies to a
    root-level (``dest_kind`` subtree ``""``) destination, so the
    destructive phase never starts when the later reseed write would be refused:
    final-component symlink, escape-from-root, protected config file or dotfile,
    and a pre-existing non-regular file or hardlink (``st_nlink > 1``).
    """
    candidate = os.path.join(real_root, target)
    if os.path.islink(candidate):
        raise _ResetRefusal("symlink", f"final path component is a symlink: {candidate}")
    resolved = os.path.realpath(candidate)
    if resolved != real_root and not resolved.startswith(real_root + os.sep):
        raise _ResetRefusal(
            "outside-allowlist", f"reseed destination escapes project root: {candidate}"
        )
    if target in _CONFIG_BASENAMES or target.startswith("."):
        raise _ResetRefusal(
            "config-file", f"reseed destination is a protected config file: {target}"
        )
    if os.path.lexists(candidate):
        st = os.lstat(candidate)
        if not stat.S_ISREG(st.st_mode):
            raise _ResetRefusal(
                "non-regular", f"reseed destination is not a regular file: {candidate}"
            )
        if st.st_nlink > 1:
            raise _ResetRefusal(
                "hardlink",
                f"reseed destination is a hardlink (st_nlink={st.st_nlink}): {candidate}",
            )


def _plan_reseeds(root: Path, args: argparse.Namespace):
    """Build the ordered list of reseeds to perform given the carry-forward flags."""
    seeds = []
    if not args.carry_standards:
        seeds.append(("standards", "STANDARDS.md", STANDARDS_SEED))
    return seeds


def _preflight(root: Path, real_root: str, args: argparse.Namespace) -> List[Path]:
    """Validate the entire reset plan before any destructive operation.

    Covers removable live artifacts, the lifecycle directory (re)creations, and
    every reseed destination that a reseed will write to. Raises ``_ResetRefusal``
    on the first violation; on success returns the regular files to remove.
    """
    to_remove = _plan_removals(root, real_root)
    for rel in RESET_ENSURE_DIRS:
        _preflight_ensure_dir(real_root, root, rel)
    for _dest_kind, target, _seed in _plan_reseeds(root, args):
        _preflight_reseed_dest(real_root, target)
    return to_remove


def handler(args: argparse.Namespace) -> int:
    raw = args.project_root
    if not Path(raw).is_absolute():
        _stderr("usage", f"project_root must be an absolute path; got: {raw!r}")
        return EXIT_USAGE
    root = Path(os.path.normpath(raw))
    if not root.is_dir():
        _stderr("usage", f"project_root is not a directory: {raw}")
        return EXIT_USAGE
    if not (root / "cartopian.toml").is_file():
        _stderr("guard", f"not a Cartopian project (no cartopian.toml): {root}")
        return EXIT_FAIL

    real_root = os.path.realpath(root)

    # Phase 1 — full fail-closed preflight of the ENTIRE plan: removable
    # artifacts, directory (re)creations, and every reseed destination. If this
    # refuses, nothing is removed, created, or written.
    try:
        to_remove = _preflight(root, real_root, args)
    except _ResetRefusal as refusal:
        _stderr("guard", f"{refusal.rule}: {refusal.detail}")
        return EXIT_FAIL

    # Phase 2 — remove the planned regular files.
    removed: List[str] = []
    for path in to_remove:
        try:
            path.unlink()
        except OSError as exc:
            _stderr("error", f"failed to remove {path}: {exc}")
            return EXIT_FAIL
        removed.append(str(path))

    # Phase 3 — recreate the empty lifecycle directories (G14).
    recreated: List[str] = []
    for rel in RESET_ENSURE_DIRS:
        directory = root / rel
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _stderr("error", f"failed to recreate {directory}: {exc}")
            return EXIT_FAIL
        recreated.append(rel)

    # Phase 4 — conditional reseed of STANDARDS.md (G15),
    # through the mediated-write primitive. Carry-forward leaves them in place.
    # The reseed writes go straight to the primitive (not perform_write) so the
    # command emits exactly one reset-plan NDJSON record.
    reseeded: List[str] = []
    for dest_kind, target, seed in _plan_reseeds(root, args):
        try:
            mediated_write(root, dest_kind, target, seed)
        except GuardRefusal as refusal:
            _stderr("guard", f"{refusal.rule}: {refusal.detail}")
            return EXIT_FAIL
        reseeded.append(target)

    emit_record({
        "action": "reset-plan",
        "details": {
            "project_root": str(root),
            "removed": removed,
            "removed_count": len(removed),
            "recreated_dirs": recreated,
            "reseeded": reseeded,
            "carry_standards": bool(args.carry_standards),
        },
    })
    return EXIT_OK
