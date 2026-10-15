"""Staged report publication for activated native Claude handoffs.

An activated POSIX Claude handoff runs under Claude's OS sandbox, which denies
every shell write to the Cartopian project directory. The capability hook still
admits the structured ``Write``/``Edit`` tools at the exact dispatch-bound
report slot, but those tools carry the whole report as tool-call input, so a
large report costs the model one round trip per chunk even when a script
already produced the finished bytes in a writable temp directory.

The non-Claude wrappers never had that gap: ``cli/native_work_sandbox.py``
gives the assignee a private publication directory (``CARTOPIAN_PUBLICATION_DIR``)
and the outside controller publishes the staged ``report.md`` / ``review.md``
through the mediated writer after the child exits. This module ports that
route to ``cartopian-claude``:

* ``prepare`` creates a fresh private directory outside every governed root,
  prints its path and the one-line prompt preface, and the wrapper exports it
  as ``CARTOPIAN_PUBLICATION_DIR``. ``cli/claude_launch_settings.py`` adds the
  directory to the sandbox ``allowWrite`` list and binds the Stop hook to the
  staged candidate so a staged report is accepted as completion evidence.
* ``publish`` runs in the wrapper after the assignee is gone. It reads each
  bounded, single-link, UTF-8 regular candidate without following links and
  publishes it through ``mediated_write`` at the fixed slot the role's
  ``write:reports`` grant authorizes, then removes the directory.
* ``discard`` removes the directory without publishing when the launch fails
  before Claude starts.

The staging file lives outside the project, so the existing report schema,
identity, provenance and request-alignment checks still govern completion
exactly as they do for a report the assignee wrote at the slot directly.
Standard library only.
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Mapping

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli.work_access import WorkAccess, WorkAccessError, effective_access

PUBLICATION_DIR_ENV = "CARTOPIAN_PUBLICATION_DIR"
DIRECTORY_PREFIX = "cartopian-claude-publication-"
REPORT_CANDIDATE = "report.md"
REVIEW_CANDIDATE = "review.md"


def publication_root() -> str:
    """The canonical system temp root the staging directory is created in.

    The dispatch-bound ``TMPDIR`` of an activated launch is Claude's protected
    host socket directory below the Cartopian config root, which the sandbox
    must keep unwritable, so staging never uses it.
    """
    return os.path.realpath("/tmp")


def staged_access(project: Path, environ: Mapping[str, str]) -> WorkAccess | None:
    """The dispatched role's access contract, or None when not activated."""
    from cli.claude_launch_settings import _capability_context, _session_roles

    resolution, roots = _capability_context(project, environ=environ)
    if not resolution.activated:
        return None
    return effective_access(project, roots, resolution.grants_for(_session_roles(environ)),
                            activated=True)


def bound_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """The launch environment with the report variant the slot mandates.

    Dispatch always exports ``CARTOPIAN_EXPECTED_REPORT_VARIANT``. A manual
    Claude launch may bind only the report path, and the wrapper documents
    the variant as inferred then; the filename contract is that inference.
    """
    from cli import report_identity

    bound = dict(environ)
    expected = bound.get("CARTOPIAN_EXPECTED_REPORT_PATH")
    if expected and not bound.get("CARTOPIAN_EXPECTED_REPORT_VARIANT"):
        mandated = report_identity.filename_contract_variant(Path(expected).name)
        if mandated:
            bound["CARTOPIAN_EXPECTED_REPORT_VARIANT"] = mandated
    return bound


def staged_slots(project: Path, environ: Mapping[str, str]) -> dict[str, tuple[str, str]]:
    """Fixed candidate-name to mediated slot mapping for this launch."""
    from cli.native_work_sandbox import publication_slots

    access = staged_access(project, environ)
    if access is None:
        return {}
    return publication_slots(access, bound_environment(environ))


def instructions(directory: str, slots: Mapping[str, tuple[str, str]]) -> str:
    """One line prepended to the launch prompt; the project prompt is unchanged."""
    parts = ["Cartopian staged publication instructions:"]
    for candidate, (kind, name) in slots.items():
        parts.append(
            f"Write the final {kind} bytes to {os.path.join(directory, candidate)} "
            f"instead of the project slot {name}, using a shell command (for example "
            "cp, mv, or a heredoc), not the Write or Edit tool. The wrapper publishes "
            "that file to the project through Cartopian's mediated writer when you "
            "exit. Keep all identity fields and paths inside the artifact unchanged."
        )
    parts.append("The original assignment follows.")
    return " ".join(parts)


def validate_directory(directory: str) -> Path:
    """Refuse any directory this module did not create as a private staging slot."""
    path = Path(directory)
    if not path.is_absolute() or path.parent != Path(publication_root()) or not path.name.startswith(DIRECTORY_PREFIX):
        raise WorkAccessError("staged publication directory must be a private directory below " + publication_root())
    try:
        info = path.lstat()
    except OSError as exc:
        raise WorkAccessError("staged publication directory is unavailable: " + str(exc)) from exc
    getuid = getattr(os, "getuid", None)
    if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700
            or (getuid is not None and info.st_uid != getuid())):
        raise WorkAccessError("staged publication directory must be a direct user-owned mode-0700 directory: " + directory)
    return path


def prepare(project: Path, environ: Mapping[str, str]) -> tuple[str, str] | None:
    """Create the staging directory when this launch can publish through it."""
    slots = staged_slots(project, environ)
    if not slots:
        return None
    directory = tempfile.mkdtemp(prefix=DIRECTORY_PREFIX, dir=publication_root())
    try:
        os.chmod(directory, 0o700)
        if os.path.realpath(directory) != directory:
            raise WorkAccessError("staged publication directory must be a canonical path")
        validate_directory(directory)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return directory, instructions(directory, slots)


def discard(directory: str) -> None:
    path = validate_directory(directory)
    shutil.rmtree(path)


def publish(project: Path, directory: str, environ: Mapping[str, str]) -> None:
    """Publish staged candidates through the mediated writer, then discard them."""
    from cli.native_work_sandbox import publish as publish_candidates

    path = validate_directory(directory)
    try:
        access = staged_access(project, environ)
        if access is None:
            raise WorkAccessError("staged publication requires an activated capability contract")
        from cli.native_work_sandbox import publication_slots

        publish_candidates(access, path, publication_slots(access, bound_environment(environ)))
    finally:
        shutil.rmtree(path, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "publish", "discard"))
    parser.add_argument("--project-dir", type=Path)
    parser.add_argument("--directory")
    args = parser.parse_args(argv)
    try:
        if args.action == "discard":
            if not args.directory:
                raise WorkAccessError("discard requires --directory")
            discard(args.directory)
            return 0
        if args.project_dir is None:
            raise WorkAccessError(args.action + " requires --project-dir")
        project = args.project_dir.resolve()
        if args.action == "prepare":
            prepared = prepare(project, os.environ)
            if prepared is not None:
                sys.stdout.write(prepared[0] + "\n" + prepared[1] + "\n")
            return 0
        if not args.directory:
            raise WorkAccessError("publish requires --directory")
        publish(project, args.directory, os.environ)
        return 0
    except Exception as exc:
        print("[guard] staged report publication: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
