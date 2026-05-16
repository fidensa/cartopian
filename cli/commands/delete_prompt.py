"""`cartopian delete-prompt <prompt-path>` (FR-005, SPEC-01-001).

Deletes a prompt file that lives under a registered project's ``prompts/``
directory and emits one NDJSON confirmation record. Filename must match the
Cartopian prompt grammar: ``PROMPT-NN-NNN.md`` or
``PROMPT-PLAN-NNN[-kebab-slug].md`` (planning-checkpoint prompts carry an
operator-authored slug per CONVENTIONS.md).
"""
import argparse
import os
import re
import sys
from pathlib import Path

from cli.commands._registry import (
    MalformedRegistry,
    read_registry,
    registry_path,
)
from cli.emit import emit_record
from cli.main import EXIT_ENV, EXIT_FAIL, EXIT_OK, EXIT_USAGE

PROMPT_FILENAME_RE = re.compile(
    r"^PROMPT-(?:\d{2}-\d{3}|PLAN-\d{3}(?:-[a-z0-9][a-z0-9-]*)?)\.md$"
)


def _stderr(prefix: str, msg: str) -> None:
    sys.stderr.write(f"[{prefix}] {msg}\n")


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "prompt_path",
        help="Absolute path to the prompt file under a project's prompts/ directory",
    )


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def handler(args: argparse.Namespace) -> int:
    raw_path = args.prompt_path
    if not Path(raw_path).is_absolute():
        _stderr("usage", f"prompt_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE

    prompt_path = Path(os.path.normpath(raw_path))

    if not PROMPT_FILENAME_RE.match(prompt_path.name):
        _stderr(
            "guard",
            f"prompt filename does not match PROMPT-NN-NNN.md or "
            f"PROMPT-PLAN-NNN[-slug].md grammar: {prompt_path.name}",
        )
        return EXIT_FAIL

    # Reject leaf symlinks outright so the path we validate is the path we
    # delete. Without this, an outside symlink whose target resolves into a
    # registered project would pass the under-check and unlink only the
    # symlink while the real in-project file survived untouched. This also
    # rejects in-project symlinks pointing at outside files (which aren't
    # real prompts/reports).
    if prompt_path.is_symlink():
        _stderr("guard", f"prompt_path must not be a symlink: {prompt_path}")
        return EXIT_FAIL

    try:
        entries = read_registry(registry_path())
    except MalformedRegistry as exc:
        _stderr("error", f"registry file is malformed: {exc}")
        return EXIT_ENV

    # Under-check uses the canonical (realpath) form of both sides so that the
    # macOS /var → /private/var quirk does not split equivalent paths apart,
    # and so that any parent-dir symlinks are normalized. Leaf-symlink
    # confusion is already excluded by the is_symlink() guard above.
    prompt_canonical = Path(os.path.realpath(prompt_path))

    matched = None
    for entry in entries:
        project_root = Path(os.path.realpath(entry["path"]))
        prompts_dir = project_root / "prompts"
        if _is_under(prompt_canonical, prompts_dir):
            matched = entry
            break

    if matched is None:
        _stderr(
            "guard",
            f"prompt path is not under any registered project's prompts/ "
            f"directory: {prompt_path}",
        )
        return EXIT_FAIL

    if not prompt_path.is_file():
        _stderr("guard", f"prompt file not found: {prompt_path}")
        return EXIT_FAIL

    try:
        prompt_path.unlink()
    except OSError as exc:
        _stderr("error", f"failed to delete prompt: {prompt_path} — {exc}")
        return EXIT_FAIL

    emit_record(
        {
            "action": "delete-prompt",
            "details": {"deleted_path": str(prompt_path)},
        }
    )
    return EXIT_OK
