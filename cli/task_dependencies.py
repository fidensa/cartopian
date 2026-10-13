"""Whether an active task is waiting on prerequisites it does not yet have.

Replanning can give a task that is already ``in-progress`` a new ``Blocked
by:`` prerequisite that is not finished. The task keeps its place and its
evidence, but nothing can be assigned for it until the prerequisite is done:
its prompt cannot be composed (the dependency's deliverable does not exist)
and readiness refuses it. This module is the one answer to "is that a valid
wait, and what moves next", so ``plan-audit``, ``next-action``, and
``compose-state`` agree.

A wait is valid only when every prerequisite is a real task in ``tasks/`` and
the unfinished ones form no cycle. Anything else fails closed: the reader
treats the task as it would without this module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_TASK_FILE_RE = re.compile(r"^(TASK-\d{2}-\d{3})\.md$")
_STATUSES = ("open", "in-progress", "in-review", "done")
_ACTIVE = ("in-progress", "in-review")


@dataclass
class Wait:
    #: The task's own unfinished ``Blocked by:`` prerequisites, in order.
    waiting_on: List[str]
    #: The prerequisite to act on next: ``{id, title, path, status}``.
    prerequisite: Optional[Dict[str, str]]
    #: Why the wait is not valid; ``None`` when it is.
    problem: Optional[str]


def blocked_by(text: str) -> List[str]:
    """The ``Blocked by:`` task ids in a task's header block."""
    for line in text.splitlines():
        if line.startswith("## "):
            break
        stripped = line.strip()
        if stripped.startswith("Blocked by:"):
            value = stripped[len("Blocked by:") :].strip()
            if not value or value.lower() in {"none", "n/a"}:
                return []
            return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _title(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _index(project_path: Path) -> Dict[str, Tuple[str, Path]]:
    index: Dict[str, Tuple[str, Path]] = {}
    for status in _STATUSES:
        directory = project_path / "tasks" / status
        if not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir()):
            match = _TASK_FILE_RE.match(entry.name)
            if match and entry.is_file():
                index.setdefault(match.group(1), (status, entry))
    return index


def dependency_wait(project_path: Path, task_path: Path) -> Optional[Wait]:
    """The task's wait on unfinished prerequisites, or ``None`` if it has none.

    Walks the unfinished prerequisites depth-first in declared order. The
    prerequisite to act on is the first one whose own prerequisites are all
    done: start it if it is open, continue it if it is active. A prerequisite
    that is not a task, a task file that cannot be read, or a cycle among the
    unfinished tasks makes the wait invalid (``problem``), since nothing could
    ever end it.
    """
    try:
        text = task_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return Wait([], None, f"{task_path.name} is unreadable: {exc}")
    index = _index(project_path)
    parent = _TASK_FILE_RE.match(task_path.name)
    parent_id = parent.group(1) if parent else task_path.stem

    texts: Dict[str, str] = {parent_id: text}

    def deps_of(task_id: str) -> Tuple[Optional[List[str]], Optional[str]]:
        if task_id not in texts:
            try:
                texts[task_id] = index[task_id][1].read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                return None, f"{task_id} is unreadable: {exc}"
        deps = blocked_by(texts[task_id])
        for dep in deps:
            if dep not in index:
                return None, f"{task_id} is blocked by {dep}, which is not a task in tasks/"
        return [dep for dep in deps if index[dep][0] != "done"], None

    waiting_on, problem = deps_of(parent_id)
    if problem is not None:
        return Wait([], None, problem)
    if not waiting_on:
        return None

    prerequisite: Optional[str] = None
    visiting: List[str] = [parent_id]
    finished: set = set()

    def visit(task_id: str) -> Optional[str]:
        nonlocal prerequisite
        if task_id in visiting:
            cycle = visiting[visiting.index(task_id):] + [task_id]
            return "dependency cycle: " + " -> ".join(cycle)
        if task_id in finished:
            return None
        visiting.append(task_id)
        deps, problem = deps_of(task_id)
        if problem is not None:
            return problem
        for dep in deps:
            problem = visit(dep)
            if problem is not None:
                return problem
        if not deps and prerequisite is None:
            prerequisite = task_id
        visiting.pop()
        finished.add(task_id)
        return None

    for dep in waiting_on:
        problem = visit(dep)
        if problem is not None:
            return Wait(waiting_on, None, problem)
    status, path = index[prerequisite]  # a finite acyclic walk always ends at one
    return Wait(
        waiting_on,
        {"id": prerequisite, "title": _title(texts[prerequisite]), "path": str(path), "status": status},
        None,
    )


def describe(task_id: str, wait: Wait) -> str:
    """One line naming the wait and the prerequisite's next action."""
    prerequisite = wait.prerequisite or {}
    verb = "Continue" if prerequisite.get("status") in _ACTIVE else "Start"
    return (
        f"{task_id} waits on {', '.join(wait.waiting_on)}; "
        f"{verb} {prerequisite.get('id')} first"
    )
