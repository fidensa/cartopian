"""`cartopian write-task <project-root> --task-id TASK-NN-NNN --slug ...`.

Structured writer for task files. A task id lives in exactly one status
directory (``tasks/{open,in-progress,in-review,done}/``); re-issuing this
writer for an existing id updates that file in place wherever it lives,
renaming within its status directory on a slug change. Only a genuinely new
id creates a file — in ``tasks/open/``, the lifecycle entry point
(``move-task`` advances it from there). A pre-existing multi-directory
collision is refused fail-closed, naming every colliding path. The PM
supplies the id + slug, not a path; the destination subtree is the
allowlisted ``task`` dest_kind.
"""
import argparse
import os
from pathlib import Path
from typing import List, Union

from cli.commands import _writers

STATUSES = ("open", "in-progress", "in-review", "done")


def _schema_errors(content: Union[str, bytes]) -> List[str]:
    """Structural (content-shape) reasons this task body would fail readiness.

    Fail-closed gate for ``write-task``: a body that omits the ``Evidence gate:``
    header or a checkbox-bearing ``## Acceptance`` section can never satisfy
    ``validate-task-readiness``, so refuse it at write time rather than persist a
    task that is dead on arrival. Only the two content-shape checks are enforced
    here — the state-dependent readiness checks (phase-exists, plan-ref-exists,
    blocked-by-complete, work-root-names-valid) can legitimately be unmet when a
    task is first authored and are left to ``validate-task-readiness``.

    Reuses the readiness validator's own check functions so the two can never
    drift; imported lazily to keep the module dependency one-directional.
    """
    from cli.commands.validate_task_readiness import (
        _check_acceptance,
        _check_evidence_gate,
        _parse_headers,
    )

    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return ["task body must be valid UTF-8 text"]
    else:
        text = content

    headers, presence = _parse_headers(text)
    errors: List[str] = []
    evidence = _check_evidence_gate(headers, presence)
    if not evidence["pass"]:
        errors.append(evidence["reason"])
    acceptance = _check_acceptance(text)
    if not acceptance["pass"]:
        errors.append(acceptance["reason"])
    return errors


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    _writers.add_content_args(subparser)
    subparser.add_argument(
        "--task-id",
        required=True,
        help="Task id, e.g. TASK-NN-NNN",
    )
    subparser.add_argument(
        "--slug",
        required=True,
        help="Kebab-case slug for the filename (TASK-NN-NNN-<slug>.md)",
    )
    _writers.add_source_arg(subparser)


def _find_task_files(project_root: Path, task_id: str) -> List[Path]:
    """Every task file carrying ``task_id`` across the four status directories.

    A file carries the id when its stem is the id itself or the id followed by
    a ``-slug`` suffix (the ``TASK-NN-NNN[-slug].md`` grammar move-task
    accepts); plain prefix matching would conflate TASK-01-001 with a
    hypothetical longer id.
    """
    matches: List[Path] = []
    for status in STATUSES:
        status_dir = project_root / "tasks" / status
        if not status_dir.is_dir():
            continue
        for entry in sorted(status_dir.iterdir()):
            if not entry.is_file() or entry.suffix != ".md":
                continue
            if entry.stem == task_id or entry.stem.startswith(f"{task_id}-"):
                matches.append(entry)
    return matches


def handler(args: argparse.Namespace) -> int:
    task_id = args.task_id
    slug = args.slug
    if not _writers.TASK_ID_RE.match(task_id):
        _writers.stderr(
            "usage",
            f"--task-id must match TASK-NN-NNN grammar; got: {task_id!r}",
        )
        return _writers.EXIT_USAGE
    if not _writers.SLUG_RE.match(slug):
        _writers.stderr(
            "usage",
            f"--slug must be kebab-case [a-z0-9][a-z0-9-]*; got: {slug!r}",
        )
        return _writers.EXIT_USAGE

    root, err = _writers.validated_root(args.project_root)
    if err is not None:
        _writers.stderr("usage", err)
        return _writers.EXIT_USAGE

    # Resolve content up front so a usage error cannot land after the
    # in-place rename below (usage errors must change nothing on disk).
    content, cerr = _writers.resolve_content(args)
    if cerr is not None:
        _writers.stderr("usage", cerr)
        return _writers.EXIT_USAGE

    # Promotion stamp (--source): validate + verify the referent is live and
    # render `Source: BL-NNN` into the header ourselves, before any on-disk move.
    content, source_id, serr = _writers.apply_source_stamp(args, root, content)
    if serr is not None:
        _writers.stderr(*serr)
        return _writers.EXIT_USAGE if serr[0] == "usage" else _writers.EXIT_FAIL

    # Fail-closed schema gate: refuse a body that could never pass readiness,
    # before any on-disk rename so a refusal leaves the tree unchanged.
    schema_errors = _schema_errors(content)
    if schema_errors:
        _writers.stderr(
            "guard",
            "task-schema-invalid: " + "; ".join(schema_errors),
        )
        return _writers.EXIT_FAIL

    filename = f"{task_id}-{slug}.md"

    # Id uniqueness: the same id in more than one place is pre-existing
    # corruption this writer must not compound — refuse and write nothing.
    matches = _find_task_files(root, task_id)
    if len(matches) > 1:
        _writers.stderr(
            "guard",
            f"task-id-collision: {task_id} exists in multiple status "
            f"directories; resolve manually before writing: "
            + ", ".join(str(p) for p in matches),
        )
        return _writers.EXIT_FAIL

    if matches:
        # Exactly one — update in place in its current status directory. A
        # slug change renames within that directory first (one file before,
        # one file after), so the mediated write and its provenance record
        # land on the actual path.
        existing = matches[0]
        status = existing.parent.name
        renamed_from = None
        if existing.name != filename:
            target = existing.parent / filename
            try:
                os.rename(existing, target)
            except OSError as exc:
                _writers.stderr("error", f"rename failed: {exc}")
                return _writers.EXIT_FAIL
            renamed_from = existing
        relative_target = f"{status}/{filename}"
    else:
        # Genuinely new id — lifecycle entry point.
        status = "open"
        renamed_from = None
        relative_target = f"open/{filename}"

    extra_details = {"task_id": task_id, "slug": slug, "status": status}
    if source_id is not None:
        extra_details["source"] = source_id
    code = _writers.perform_write(
        args,
        action="write-task",
        dest_kind="task",
        relative_target=relative_target,
        content=content,
        extra_details=extra_details,
    )
    if code != _writers.EXIT_OK and renamed_from is not None:
        # The write was refused after the slug rename; restore the original
        # filename so a refusal leaves the tree unchanged (best-effort).
        try:
            os.rename(renamed_from.parent / filename, renamed_from)
        except OSError:
            pass
    return code
