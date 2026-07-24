"""`cartopian write-resource <project-root> --path <relative/path> --content|--content-file ...`.

Structured writer for project supporting artifacts under ``resources/``
(CONVENTIONS § Project Resources). Unlike the other writers this one takes a
relative path, because resources are operator-named, any-format files rather
than id-addressed protocol artifacts; the destination subtree is still the
closed ``resource`` dest_kind, so every write-safety rule of the mediated
primitive applies. The PM uses it as transcription only — persisting an
assignee-returned deliverable or operator-supplied content verbatim.

``resources/`` and missing intermediate directories are created on demand
(fail-closed: the ensured chain is textually validated here, and the mediated
primitive re-verifies the whole canonical chain before writing).
"""
import argparse
import os
from pathlib import Path

from cli.commands import _writers


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    _writers.add_content_args(subparser)
    subparser.add_argument(
        "--path",
        required=True,
        help=(
            "Destination path relative to resources/, e.g. research/findings.md "
            "(clean relative path; no traversal, no dotfile components)"
        ),
    )


def _validate_relpath(raw: str) -> str:
    """Return the normalized resources-relative path or raise ``ValueError``."""
    normalized = raw.replace("\\", "/").strip()
    if not normalized:
        raise ValueError("--path must be a non-empty relative path")
    if "\x00" in normalized:
        raise ValueError("--path contains a NUL byte")
    if normalized.startswith("/") or os.path.isabs(raw) or (
        len(normalized) > 1 and normalized[1] == ":"
    ):
        raise ValueError(f"--path must be relative to resources/; got: {raw!r}")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if not parts:
        raise ValueError(f"--path has no file component: {raw!r}")
    for part in parts:
        if part == "..":
            raise ValueError(f"--path must not traverse upward: {raw!r}")
        if part.startswith("."):
            raise ValueError(f"--path must not contain dotfile components: {raw!r}")
    if parts[0] == "resources":
        # Accept the fully-qualified spelling used by the Deliverable field.
        parts = parts[1:]
        if not parts:
            raise ValueError(f"--path has no file component: {raw!r}")
    return "/".join(parts)


def _ensure_parents(root: Path, relpath: str) -> None:
    """Create ``resources/`` and the target's intermediate directories.

    Textual validation only (the path grammar above already refused traversal
    and dotfiles); the mediated-write primitive re-canonicalizes and re-verifies
    the entire chain — symlinks included — before any byte is written, so a
    directory swapped in after this point still refuses fail-closed.
    """
    base = root / "resources"
    if base.is_symlink() or (base.exists() and not base.is_dir()):
        raise OSError(f"resources/ exists and is not a plain directory: {base}")
    target_parent = base.joinpath(*relpath.split("/")[:-1])
    target_parent.mkdir(parents=True, exist_ok=True)


def handler(args: argparse.Namespace) -> int:
    try:
        relpath = _validate_relpath(args.path)
    except ValueError as exc:
        _writers.stderr("usage", str(exc))
        return _writers.EXIT_USAGE

    root, err = _writers.validated_root(args.project_root)
    if err is not None:
        _writers.stderr("usage", err)
        return _writers.EXIT_USAGE

    try:
        _ensure_parents(root, relpath)
    except OSError as exc:
        _writers.stderr("guard", f"cannot prepare resources/ destination: {exc}")
        return _writers.EXIT_FAIL

    return _writers.perform_write(
        args,
        action="write-resource",
        dest_kind="resource",
        relative_target=relpath,
        extra_details={"resource_path": f"resources/{relpath}"},
    )
