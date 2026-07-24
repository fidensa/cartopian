"""`cartopian archive-plan <project-root>`.

Create the optional completed-plan snapshot as a bounded, PM-owned lifecycle
operation.  The caller supplies only the project root, a validated slug, index
metadata, and the CLOSEOUT body.  Source paths and the archive destination are
fixed by this command; there is no generic copy or directory-creation surface.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Iterable, List, Tuple

from cli.commands import _writers
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE

ARCHIVE_ROOT_FILES = (
    "REQUIREMENTS.md",
    "STANDARDS.md",
    "IMPLEMENTATION_PLAN.md",
    "STATE.md",
)
ARCHIVE_DIRS = (
    "decisions",
    "phases",
    "tasks",
    "specs",
    "reviews",
    "reports",
    "resources",
)
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ARCHIVE_RE = re.compile(r"^PLAN-(\d{3})-[a-z0-9]+(?:-[a-z0-9]+)*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class _ArchiveRefusal(Exception):
    def __init__(self, rule: str, detail: str) -> None:
        self.rule = rule
        self.detail = detail
        super().__init__(f"{rule}: {detail}")


def _stderr(prefix: str, message: str) -> None:
    sys.stderr.write(f"[{prefix}] {message}\n")


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("project_root", help="Absolute Cartopian project root")
    subparser.add_argument(
        "--slug",
        required=True,
        help="Short lowercase kebab-case archive slug",
    )
    subparser.add_argument(
        "--closed",
        required=True,
        help="Closeout date in YYYY-MM-DD form",
    )
    subparser.add_argument(
        "--summary",
        required=True,
        help="Short, single-line outcome for archive/INDEX.md",
    )
    subparser.add_argument(
        "--content",
        default=None,
        help="Literal UTF-8 body for the generated CLOSEOUT.md",
    )
    subparser.add_argument(
        "--content-file",
        default=None,
        help="Path to a UTF-8 body for the generated CLOSEOUT.md",
    )


def _assert_plain_file(path: Path, *, rule: str) -> None:
    if path.is_symlink():
        raise _ArchiveRefusal(rule, f"symlink is not allowed: {path}")
    try:
        info = path.stat()
    except OSError as exc:
        raise _ArchiveRefusal(rule, f"cannot inspect {path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise _ArchiveRefusal(rule, f"not a regular file: {path}")
    if info.st_nlink > 1:
        raise _ArchiveRefusal(rule, f"hardlinked file is not allowed: {path}")


def _walk_plain_tree(root: Path) -> Iterable[Path]:
    if root.is_symlink() or not root.is_dir():
        raise _ArchiveRefusal("source-tree", f"not a plain directory: {root}")
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in dirs:
            child = current_path / name
            if child.is_symlink():
                raise _ArchiveRefusal("source-tree", f"symlink is not allowed: {child}")
        for name in files:
            child = current_path / name
            _assert_plain_file(child, rule="source-tree")
            yield child


def _existing_archives(archive_root: Path) -> List[Tuple[int, Path]]:
    found: List[Tuple[int, Path]] = []
    if not archive_root.exists():
        return found
    if archive_root.is_symlink() or not archive_root.is_dir():
        raise _ArchiveRefusal("archive-root", f"not a plain directory: {archive_root}")
    for entry in archive_root.iterdir():
        if entry.name == "INDEX.md":
            _assert_plain_file(entry, rule="archive-index")
            continue
        match = _ARCHIVE_RE.fullmatch(entry.name)
        if match and entry.is_dir() and not entry.is_symlink():
            found.append((int(match.group(1)), entry))
    return found


def _index_body(index_path: Path, archive_name: str, closed: str, summary: str) -> str:
    if index_path.exists():
        body = index_path.read_text(encoding="utf-8")
        if not body.endswith("\n"):
            body += "\n"
        return body + f"| `{archive_name}` | {closed} | {summary} |\n"
    return (
        "# Archive Index\n\n"
        "| Archive | Closed | Summary |\n"
        "| --- | --- | --- |\n"
        f"| `{archive_name}` | {closed} | {summary} |\n"
    )


def _atomic_text(path: Path, content: str) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=".INDEX.md.tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def handler(args: argparse.Namespace) -> int:
    raw_root = args.project_root
    if not Path(raw_root).is_absolute():
        _stderr("usage", f"project_root must be absolute; got: {raw_root!r}")
        return EXIT_USAGE
    root = Path(os.path.normpath(raw_root))
    if root.is_symlink() or not root.is_dir() or not (root / "cartopian.toml").is_file():
        _stderr("guard", f"not a Cartopian project root: {root}")
        return EXIT_FAIL
    if not _SLUG_RE.fullmatch(args.slug):
        _stderr("usage", f"slug must be lowercase kebab-case; got: {args.slug!r}")
        return EXIT_USAGE
    try:
        if not _DATE_RE.fullmatch(args.closed):
            raise ValueError
        date.fromisoformat(args.closed)
    except ValueError:
        _stderr("usage", f"closed must be YYYY-MM-DD; got: {args.closed!r}")
        return EXIT_USAGE
    summary = args.summary.strip()
    if not summary or any(char in summary for char in "\r\n|"):
        _stderr("usage", "summary must be a non-empty single line without `|`")
        return EXIT_USAGE

    raw_closeout, content_error = _writers.resolve_content(args)
    if content_error is not None:
        _stderr("usage", content_error)
        return EXIT_USAGE
    try:
        closeout = (
            raw_closeout.decode("utf-8")
            if isinstance(raw_closeout, bytes)
            else str(raw_closeout)
        )
    except UnicodeDecodeError:
        _stderr("usage", "CLOSEOUT content must be valid UTF-8")
        return EXIT_USAGE
    if not closeout.strip():
        _stderr("usage", "content file must not be empty")
        return EXIT_USAGE

    archive_root = root / "archive"
    temp_dir: Path | None = None
    try:
        existing = _existing_archives(archive_root)
        sources: List[Path] = []
        for name in ARCHIVE_ROOT_FILES:
            source = root / name
            if source.exists() or source.is_symlink():
                _assert_plain_file(source, rule="source-file")
                sources.append(source)
        for name in ARCHIVE_DIRS:
            source = root / name
            if source.exists() or source.is_symlink():
                list(_walk_plain_tree(source))
                sources.append(source)

        archive_root.mkdir(mode=0o755, exist_ok=True)
        next_number = max((number for number, _ in existing), default=0) + 1
        if next_number > 999:
            raise _ArchiveRefusal("archive-counter", "PLAN archive counter exhausted at 999")
        archive_name = f"PLAN-{next_number:03d}-{args.slug}"
        destination = archive_root / archive_name
        if destination.exists() or destination.is_symlink():
            raise _ArchiveRefusal("archive-collision", f"destination exists: {destination}")

        temp_dir = Path(tempfile.mkdtemp(prefix=f".{archive_name}.tmp-", dir=archive_root))
        copied: List[str] = []
        for source in sources:
            target = temp_dir / source.name
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
            copied.append(source.name)
        (temp_dir / "CLOSEOUT.md").write_text(closeout, encoding="utf-8")
        os.replace(temp_dir, destination)
        temp_dir = None

        index_path = archive_root / "INDEX.md"
        _atomic_text(index_path, _index_body(index_path, archive_name, args.closed, summary))
    except _ArchiveRefusal as refusal:
        _stderr("guard", f"{refusal.rule}: {refusal.detail}")
        return EXIT_FAIL
    except OSError as exc:
        _stderr("error", f"archive creation failed: {exc}")
        return EXIT_FAIL
    finally:
        if temp_dir is not None and temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

    emit_record({
        "action": "archive-plan",
        "details": {
            "project_root": str(root),
            "archive_path": str(destination),
            "archive_name": archive_name,
            "copied": copied,
            "closed": args.closed,
            "summary": summary,
        },
    })
    return EXIT_OK
