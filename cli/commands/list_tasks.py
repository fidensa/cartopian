"""`cartopian list-tasks --project <id-or-path>`.

Enumerates task files under a project's ``tasks/<status>/`` directories and
emits one NDJSON record per task. Required ``--project`` resolves via the
registry by id or accepts an absolute project root path.

Filter flags ``--phase`` and ``--status`` are single-valued and may not be
repeated. Filters AND-combine. Empty match set emits no records and exits 0.
"""
import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from cli.commands._registry import (
    MalformedRegistry,
    read_registry,
    registry_path,
)
from cli.emit import emit_record
from cli.main import EXIT_ENV, EXIT_FAIL, EXIT_OK, EXIT_USAGE

STATUS_ORDER = ("open", "in-progress", "in-review", "done")
_STATUS_RANK = {s: i for i, s in enumerate(STATUS_ORDER)}

_PHASE_RE = re.compile(r"^PHASE-\d{2}-[a-z0-9][a-z0-9-]*$")
_TASK_FILENAME_RE = re.compile(r"^(TASK-\d{2}-\d{3})(?:-[^/]*)?\.md$")


def _stderr(prefix: str, msg: str) -> None:
    sys.stderr.write(f"[{prefix}] {msg}\n")


class _SeenOnceAction(argparse.Action):
    """argparse action that rejects a repeated single-valued flag."""

    def __call__(self, parser, namespace, values, option_string=None):
        sentinel = f"_seen_{self.dest}"
        if getattr(namespace, sentinel, False):
            _stderr(
                "usage",
                f"{option_string} may not be repeated",
            )
            sys.exit(EXIT_USAGE)
        setattr(namespace, sentinel, True)
        setattr(namespace, self.dest, values)


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "--project",
        required=True,
        action=_SeenOnceAction,
        help="Project id (kebab-case, resolved via registry) or absolute project root path",
    )
    subparser.add_argument(
        "--phase",
        default=None,
        action=_SeenOnceAction,
        help="Canonical phase id in full form (e.g. PHASE-NN-slug)",
    )
    subparser.add_argument(
        "--status",
        default=None,
        action=_SeenOnceAction,
        help="Status filter; one of open | in-progress | in-review | done",
    )


def _looks_like_path(value: str) -> bool:
    return "/" in value or "\\" in value or value.startswith("~")


def _resolve_project(project_arg: str) -> "tuple[Optional[Path], int]":
    """Return (project_root, exit_code_on_failure).

    On success, returns (path, EXIT_OK). On failure, returns (None, code) and
    the caller has already had a stderr line emitted by this function.
    """
    if _looks_like_path(project_arg):
        expanded = os.path.expanduser(project_arg)
        path = Path(expanded)
        if not path.is_absolute():
            _stderr(
                "usage",
                f"--project must be an absolute path; got: {project_arg}",
            )
            return None, EXIT_USAGE
        return path, EXIT_OK
    try:
        entries = read_registry(registry_path())
    except MalformedRegistry as exc:
        _stderr("error", f"registry file is malformed: {exc}")
        return None, EXIT_ENV
    for entry in entries:
        if entry.get("id") == project_arg:
            return Path(entry["path"]), EXIT_OK
    _stderr("guard", f"no registered project with id: {project_arg}")
    return None, EXIT_FAIL


def _parse_headers(content: str) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for line in content.splitlines():
        if line.startswith("## "):
            break
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if key and key not in headers:
            headers[key] = value
    return headers


def _first_title(content: str) -> str:
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _collect_tasks(project_root: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    tasks_dir = project_root / "tasks"
    if not tasks_dir.is_dir():
        return records
    for status in STATUS_ORDER:
        status_dir = tasks_dir / status
        if not status_dir.is_dir():
            continue
        for entry in sorted(status_dir.iterdir(), key=lambda p: p.name):
            if not entry.is_file():
                continue
            match = _TASK_FILENAME_RE.match(entry.name)
            if not match:
                continue
            task_id = match.group(1)
            try:
                content = entry.read_text(encoding="utf-8")
            except OSError:
                continue
            headers = _parse_headers(content)
            phase = headers.get("Phase", "")
            plan_ref = headers.get("Plan ref", "")
            title = _first_title(content)
            records.append(
                {
                    "task_path": str(entry),
                    "task_id": task_id,
                    "phase": phase,
                    "plan_ref": plan_ref,
                    "status": status,
                    "title": title,
                }
            )
    return records


def handler(args: argparse.Namespace) -> int:
    project_root, code = _resolve_project(args.project)
    if project_root is None:
        return code

    if not project_root.is_dir():
        _stderr("guard", f"project root not found: {project_root}")
        return EXIT_FAIL

    if args.phase is not None:
        if not _PHASE_RE.match(args.phase):
            _stderr(
                "usage",
                f"invalid --phase: {args.phase} — must match PHASE-NN-slug",
            )
            return EXIT_USAGE
        phase_file = project_root / "phases" / f"{args.phase}.md"
        if not phase_file.is_file():
            _stderr(
                "usage",
                f"unknown phase id: {args.phase}",
            )
            return EXIT_USAGE

    if args.status is not None and args.status not in STATUS_ORDER:
        _stderr(
            "usage",
            f"invalid --status: {args.status} — must be one of "
            f"{{{', '.join(STATUS_ORDER)}}}",
        )
        return EXIT_USAGE

    records = _collect_tasks(project_root)
    if args.phase is not None:
        records = [r for r in records if r["phase"] == args.phase]
    if args.status is not None:
        records = [r for r in records if r["status"] == args.status]

    records.sort(
        key=lambda r: (r["phase"], _STATUS_RANK.get(r["status"], 99), r["task_id"])
    )

    for record in records:
        emit_record(record)
    return EXIT_OK
