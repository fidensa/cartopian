"""`cartopian scaffold-project <project-path>`.

Creates the Cartopian project directory tree and seed files at an absolute
path. Maintains `.gitignore` (gitignores `cartopian.local.toml`). Enforces the
rerun policy: empty/missing target → scaffold (exit 0); already well-formed
scaffold → no-op (exit 0); non-empty target that does not match the layout →
guarded refusal (exit 1).
"""
import argparse
import sys
from pathlib import Path
from typing import List

from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE

ALLOWED_TOP_DIRS = (
    "phases",
    "tasks",
    "prompts",
    "reports",
    "specs",
    "decisions",
    "reviews",
)

ALLOWED_TOP_FILES = (
    "STATE.md",
    "STANDARDS.md",
    ".gitignore",
    "cartopian.toml",
    "cartopian.local.toml",
)

REQUIRED_DIRS = (
    "phases",
    "tasks",
    "tasks/open",
    "tasks/in-progress",
    "tasks/in-review",
    "tasks/done",
    "prompts",
    "reports",
    "specs",
    "decisions",
    "reviews",
)

REQUIRED_FILES = (
    "STATE.md",
    "STANDARDS.md",
    "decisions/INDEX.md",
)

SEED_CONTENTS = {
    "STATE.md": "# STATE\n",
    "STANDARDS.md": "# STANDARDS\n",
    "decisions/INDEX.md": "# Decisions Index\n",
}

GITIGNORE_LINE = "cartopian.local.toml"


def _stderr(prefix: str, msg: str) -> None:
    sys.stderr.write(f"[{prefix}] {msg}\n")


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "project_path",
        help="Absolute path to the project root to scaffold",
    )


def _gitignore_contains_line(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    for line in text.splitlines():
        if line.rstrip() == GITIGNORE_LINE:
            return True
    return False


def _ensure_gitignore(project_path: Path) -> None:
    gi_path = project_path / ".gitignore"
    if not gi_path.exists():
        gi_path.write_text(GITIGNORE_LINE + "\n", encoding="utf-8")
        return
    if _gitignore_contains_line(gi_path):
        return
    existing = gi_path.read_bytes()
    suffix = b"" if existing.endswith(b"\n") or existing == b"" else b"\n"
    gi_path.write_bytes(existing + suffix + (GITIGNORE_LINE + "\n").encode("utf-8"))


def _create_dirs_and_seeds(project_path: Path) -> None:
    for rel in REQUIRED_DIRS:
        (project_path / rel).mkdir(parents=True, exist_ok=True)
    for rel in REQUIRED_FILES:
        target = project_path / rel
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(SEED_CONTENTS[rel], encoding="utf-8")


def _missing_required(project_path: Path) -> List[str]:
    missing: List[str] = []
    for rel in REQUIRED_DIRS:
        if not (project_path / rel).is_dir():
            missing.append(rel)
    for rel in REQUIRED_FILES:
        if not (project_path / rel).is_file():
            missing.append(rel)
    return missing


def _emit(project_path: Path, outcome: str) -> None:
    emit_record(
        {
            "action": "scaffold-project",
            "details": {
                "project_path": str(project_path),
                "outcome": outcome,
            },
        }
    )


def handler(args: argparse.Namespace) -> int:
    raw_path = args.project_path
    if not Path(raw_path).is_absolute():
        _stderr("usage", f"project_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE

    project_path = Path(raw_path)
    parent = project_path.parent
    if not parent.is_dir():
        _stderr("usage", f"project_path parent does not exist: {parent}")
        return EXIT_USAGE

    if project_path.exists() and not project_path.is_dir():
        _stderr(
            "guard",
            f"target exists and is not a directory: {project_path}",
        )
        return EXIT_FAIL

    if not project_path.exists():
        project_path.mkdir()
        _create_dirs_and_seeds(project_path)
        _ensure_gitignore(project_path)
        _emit(project_path, "scaffolded")
        return EXIT_OK

    entries = sorted(project_path.iterdir(), key=lambda p: p.name)

    for entry in entries:
        name = entry.name
        if entry.is_symlink() or (not entry.is_dir() and not entry.is_file()):
            _stderr(
                "guard",
                f"target has foreign entry at project root: {entry}",
            )
            return EXIT_FAIL
        if entry.is_dir():
            if name not in ALLOWED_TOP_DIRS:
                _stderr(
                    "guard",
                    f"target has foreign directory at project root: {entry}",
                )
                return EXIT_FAIL
        else:
            if name not in ALLOWED_TOP_FILES:
                _stderr(
                    "guard",
                    f"target has foreign file at project root: {entry}",
                )
                return EXIT_FAIL

    missing = _missing_required(project_path)
    gi_path = project_path / ".gitignore"
    gi_present = gi_path.is_file()
    gi_has_line = gi_present and _gitignore_contains_line(gi_path)

    if not missing:
        if gi_present and gi_has_line:
            _emit(project_path, "noop")
            return EXIT_OK
        if gi_present and not gi_has_line:
            _stderr(
                "guard",
                f"target .gitignore does not contain {GITIGNORE_LINE!r}: {gi_path}",
            )
            return EXIT_FAIL
        _ensure_gitignore(project_path)
        _emit(project_path, "scaffolded")
        return EXIT_OK

    total_required = len(REQUIRED_DIRS) + len(REQUIRED_FILES)
    if len(missing) == total_required:
        _create_dirs_and_seeds(project_path)
        _ensure_gitignore(project_path)
        _emit(project_path, "scaffolded")
        return EXIT_OK

    _stderr(
        "guard",
        f"target has partial scaffold; missing: {', '.join(missing)}",
    )
    return EXIT_FAIL
