"""Derive where planning stands from the filesystem alone.

"Planning approved" and "task 1 can be dispatched" are two different states,
and a session that conflates them stalls at startup: the phases checkpoint was
approved, task generation never happened, and the state writer described an
empty queue as closeout readiness. This module is the one place that reads
the planning surface — ``REQUIREMENTS.md``, ``IMPLEMENTATION_PLAN.md``,
``phases/``, task placement, and the planning-checkpoint prompt, report, and
review slots — and names the exact remaining planning step, so ``next-action``
and ``compose-state`` state the same thing.

Checkpoint status is read from the retained planning review
(``reviews/REVIEW-PLAN-NNN.md``, the durable checkpoint record) and from the
temporary prompt and report slots that exist only while a checkpoint is in
flight. A checkpoint for generated tasks is satisfied by an approved review
numbered from ``PLAN-004`` upward whose ``Plan ref:`` covers the task's plan
ref. A legacy checkpoint that declares no plan ref is honored only for the
earliest task-bearing phase (the initial generation it reviewed); tasks
generated later for another phase need their own scoped checkpoint.

Read-only, standard library only, and cheap: it reads headers, not bodies.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from cli import request_trace

_TASK_FILENAME_RE = re.compile(r"^(TASK-\d{2}-\d{3})\.md$")
_PHASE_STEM_RE = re.compile(r"^PHASE-\d{2}$")
_CHECKPOINT_RE = re.compile(r"^(PLAN-\d{3})$")
_ALL_STATUSES = ("open", "in-progress", "in-review", "done")

#: The standard checkpoint sequence (``skills/plan-project.md``).
STANDARD_CHECKPOINTS: Tuple[Tuple[str, str], ...] = (
    ("PLAN-001", "requirements-and-standards"),
    ("PLAN-002", "implementation-plan"),
    ("PLAN-003", "phases"),
    ("PLAN-004", "tasks-and-specs"),
)
#: Checkpoints at or above this number review generated tasks and specs.
TASK_CHECKPOINT_FLOOR = 4

STATUS_APPROVED = "approved"
STATUS_REQUEST_CHANGES = "request-changes"
STATUS_AWAITING_VERDICT = "awaiting-verdict"
STATUS_AWAITING_REPORT = "awaiting-report"
STATUS_PENDING = "pending"


def _header(text: str, name: str) -> Optional[str]:
    for line in text.splitlines():
        if line.startswith("## "):
            break
        stripped = line.strip()
        if stripped.startswith(f"{name}:"):
            return stripped[len(name) + 1 :].strip()
    return None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def phase_stems(project_path: Path) -> List[str]:
    phases_dir = project_path / "phases"
    if not phases_dir.is_dir():
        return []
    return sorted(
        entry.stem
        for entry in phases_dir.iterdir()
        if entry.is_file() and entry.suffix == ".md" and _PHASE_STEM_RE.match(entry.stem)
    )


def task_headers(project_path: Path) -> List[Dict[str, Any]]:
    """Every task file's placement plus its ``Phase:`` and ``Plan ref:``."""
    out: List[Dict[str, Any]] = []
    for status in _ALL_STATUSES:
        status_dir = project_path / "tasks" / status
        if not status_dir.is_dir():
            continue
        for entry in sorted(status_dir.iterdir(), key=lambda p: p.name):
            match = _TASK_FILENAME_RE.match(entry.name)
            if not entry.is_file() or not match:
                continue
            text = _read(entry)
            out.append(
                {
                    "id": match.group(1),
                    "status": status,
                    "path": entry,
                    "phase": _header(text, "Phase"),
                    "plan_ref": _header(text, "Plan ref"),
                }
            )
    return out


def checkpoint_records(project_path: Path) -> Dict[str, Dict[str, Any]]:
    """Status of every checkpoint that has left any trace on disk.

    A retained approved review is the durable record; a prompt or report
    without a verdict marks a checkpoint in flight.
    """
    records: Dict[str, Dict[str, Any]] = {}

    def slot(checkpoint: str) -> Dict[str, Any]:
        return records.setdefault(
            checkpoint,
            {
                "id": checkpoint,
                "status": STATUS_PENDING,
                "verdict": None,
                "plan_ref": None,
                "prompt": False,
                "report": False,
                "review": False,
            },
        )

    reviews_dir = project_path / "reviews"
    if reviews_dir.is_dir():
        for path in sorted(reviews_dir.glob("REVIEW-PLAN-*.md")):
            match = re.fullmatch(r"REVIEW-(PLAN-\d{3})\.md", path.name)
            if match is None:
                continue
            text = _read(path)
            record = slot(match.group(1))
            record["review"] = True
            verdict = (_header(text, "Verdict") or "").strip().lower() or None
            record["verdict"] = verdict
            record["plan_ref"] = _header(text, "Plan ref")
            if verdict == "approve":
                record["status"] = STATUS_APPROVED
            elif verdict in {"request-changes", "reject"}:
                record["status"] = STATUS_REQUEST_CHANGES
            else:
                record["status"] = STATUS_AWAITING_VERDICT
    reports_dir = project_path / "reports"
    if reports_dir.is_dir():
        for path in sorted(reports_dir.glob("REPORT-PLAN-*.md")):
            match = re.fullmatch(r"REPORT-(PLAN-\d{3})\.md", path.name)
            if match is None:
                continue
            record = slot(match.group(1))
            record["report"] = True
            if record["status"] == STATUS_PENDING:
                record["status"] = STATUS_AWAITING_VERDICT
    prompts_dir = project_path / "prompts"
    if prompts_dir.is_dir():
        for path in sorted(prompts_dir.glob("PROMPT-PLAN-*.md")):
            match = re.fullmatch(r"PROMPT-(PLAN-\d{3})\.md", path.name)
            if match is None:
                continue
            record = slot(match.group(1))
            record["prompt"] = True
            if record["status"] == STATUS_PENDING:
                record["status"] = STATUS_AWAITING_REPORT
    return records


def _checkpoint_number(checkpoint: str) -> int:
    return int(checkpoint.rsplit("-", 1)[1])


def _task_checkpoint_covers(
    record: Dict[str, Any], task: Dict[str, Any], first_task_phase: Optional[str]
) -> bool:
    """Whether one approved checkpoint covers one task.

    A checkpoint that declares plan refs covers exactly those refs. A legacy
    checkpoint that declares none is honored only for the initial task
    generation — the earliest task-bearing phase — and never for a phase
    whose tasks were generated later: that work has not been reviewed by
    anyone, and an unscoped approval must not become a standing pass for
    everything that follows.
    """
    if record["status"] != STATUS_APPROVED:
        return False
    if _checkpoint_number(record["id"]) < TASK_CHECKPOINT_FLOOR:
        return False
    declared = (record.get("plan_ref") or "").strip()
    if not declared or declared.lower() in {"n/a", "none"}:
        return first_task_phase is not None and task["phase"] == first_task_phase
    plan_ref = task["plan_ref"]
    if not plan_ref:
        return False
    return request_trace._review_covers_plan_ref(declared, plan_ref)  # noqa: SLF001


def _next_checkpoint_id(records: Dict[str, Dict[str, Any]]) -> str:
    numbers = [_checkpoint_number(cid) for cid in records]
    highest = max(numbers) if numbers else 0
    return f"PLAN-{max(highest + 1, TASK_CHECKPOINT_FLOOR):03d}"


def _in_flight_step(record: Dict[str, Any], what: str) -> str:
    checkpoint = record["id"]
    if record["status"] == STATUS_AWAITING_REPORT:
        return (
            f"Checkpoint {checkpoint} ({what}) is dispatched but has no report: "
            f"wait for reports/REPORT-{checkpoint}.md (cartopian wait-report) or "
            "relaunch the checkpoint handoff."
        )
    if record["status"] == STATUS_AWAITING_VERDICT:
        return (
            f"Checkpoint {checkpoint} ({what}) has a report but no applied verdict: "
            f"run cartopian report-action on reports/REPORT-{checkpoint}.md and "
            "apply the verdict."
        )
    if record["status"] == STATUS_REQUEST_CHANGES:
        return (
            f"Checkpoint {checkpoint} ({what}) returned {record['verdict']}: revise "
            "the target artifacts in place and rerun the checkpoint."
        )
    return (
        f"No approved review is retained for checkpoint {checkpoint} ({what}): run "
        "it (a checkpoint whose review was deleted under the earlier convention "
        "must be rerun)."
    )


def _unstarted_phase(
    stems: Sequence[str], tasks: Sequence[Dict[str, Any]]
) -> Optional[str]:
    """Earliest phase after the last task-bearing phase with no tasks."""
    has_task = {stem: False for stem in stems}
    for task in tasks:
        if task["phase"] in has_task:
            has_task[task["phase"]] = True
    last_with_tasks = -1
    for index, stem in enumerate(stems):
        if has_task[stem]:
            last_with_tasks = index
    for index in range(last_with_tasks + 1, len(stems)):
        if not has_task[stems[index]]:
            return stems[index]
    return None


def next_unstarted_phase(project_path: Path) -> Optional[str]:
    """Public form of the unstarted-phase rule for state composition."""
    project_path = Path(project_path)
    return _unstarted_phase(phase_stems(project_path), task_headers(project_path))


def planning_review_required(project_path: Path) -> bool:
    """Resolve ``reviews.planning.mode == "required"`` along the config chain.

    Falls back to ``False`` when the configuration cannot be resolved, so a
    state composition never fails over review policy; the resolver's own
    diagnostics belong to ``resolve-config``.
    """
    from cli.commands.resolve_config import _CliError, resolve_project_configuration

    try:
        resolved = resolve_project_configuration(Path(project_path))
    except (_CliError, OSError, ValueError):
        return False
    return resolved["reviews"]["planning"]["mode"] == "required"


#: Startup records are routine context: every free-text field is bounded.
TEXT_CAP = 240


def bounded(text: Optional[str], cap: int = TEXT_CAP) -> Optional[str]:
    """Clip one free-text field so a startup record has a known ceiling."""
    if text is None or len(text) <= cap:
        return text
    return text[: cap - 1].rstrip() + "…"


def derive(project_path: Path, *, planning_review_required: bool) -> Dict[str, Any]:
    """Return the planning record: ``complete``, ``stage``, ``next``, ``checkpoint``.

    ``complete`` means the phase that currently carries work is fully planned
    and every generated open task is checkpoint-approved (when planning review
    is required). It says nothing about later phases whose task generation the
    protocol defers on purpose. ``next`` is the exact PM step when incomplete.
    """
    project_path = Path(project_path)
    stems = phase_stems(project_path)
    tasks = task_headers(project_path)
    records = checkpoint_records(project_path) if planning_review_required else {}
    requirements = (project_path / "REQUIREMENTS.md").is_file()
    plan = (project_path / "IMPLEMENTATION_PLAN.md").is_file()
    checkpoints = {
        record["id"]: record["status"]
        for record in sorted(records.values(), key=lambda r: r["id"])
    }

    def result(
        stage: str, next_step: Optional[str], checkpoint: Optional[str] = None
    ) -> Dict[str, Any]:
        return {
            "complete": next_step is None,
            "stage": stage,
            "next": bounded(next_step),
            "checkpoint": checkpoint,
            "checkpoints": checkpoints,
        }

    def review_gate(checkpoint: str, what: str, stage: str) -> Optional[Dict[str, Any]]:
        if not planning_review_required:
            return None
        record = records.get(checkpoint)
        if record is not None and record["status"] == STATUS_APPROVED:
            return None
        record = record or {"id": checkpoint, "status": STATUS_PENDING, "verdict": None}
        return result(stage, _in_flight_step(record, what), checkpoint)

    if not requirements and not plan and not stems and not tasks:
        return result(
            "no-plan",
            "No plan exists: begin planning with plan project (Stage 1, requirements).",
        )
    if not plan:
        if not requirements:
            return result(
                "requirements",
                "Author REQUIREMENTS.md and STANDARDS.md (plan project Stage 1).",
            )
        gate = review_gate("PLAN-001", "requirements-and-standards", "requirements-review")
        if gate:
            return gate
        return result(
            "plan", "Generate IMPLEMENTATION_PLAN.md (plan project Stage 2)."
        )
    if not stems:
        gate = review_gate("PLAN-002", "implementation-plan", "plan-review")
        if gate:
            return gate
        return result("phases", "Generate the phase files (plan project Stage 3).")

    active = [t for t in tasks if t["status"] in ("open", "in-progress", "in-review")]
    unstarted = _unstarted_phase(stems, tasks)
    if not tasks or (not active and unstarted is not None):
        if not tasks:
            gate = review_gate("PLAN-003", "phases", "phases-review")
            if gate:
                return gate
        target = unstarted or stems[0]
        return result(
            "tasks",
            f"Generate tasks and specs for {target} (plan project Stage 4), then "
            + (
                f"run planning checkpoint {_next_checkpoint_id(records)} (tasks-and-specs)."
                if planning_review_required
                else "rehearse task 1 with validate-task-readiness --rehearse-dispatch."
            ),
        )

    # The tasks-and-specs checkpoint is demanded only for a phase that has not
    # started executing. Once any task of the current phase has left `open`,
    # the phase was evidently dispatched under whatever review record existed
    # then, and a legacy project whose reviews were cleared under the earlier
    # convention is not sent back to planning mid-execution.
    current_phase = min(
        (t["phase"] for t in active if t["phase"] in stems),
        key=stems.index,
        default=None,
    )
    phase_started = any(
        t["status"] != "open" and t["phase"] == current_phase for t in tasks
    )
    if planning_review_required and not phase_started:
        open_tasks = [t for t in tasks if t["status"] == "open"]
        first_task_phase = min(
            (t["phase"] for t in tasks if t["phase"] in stems),
            key=stems.index,
            default=None,
        )
        uncovered = [
            t
            for t in open_tasks
            if not any(
                _task_checkpoint_covers(r, t, first_task_phase) for r in records.values()
            )
        ]
        if uncovered:
            in_flight = [
                r
                for r in records.values()
                if _checkpoint_number(r["id"]) >= TASK_CHECKPOINT_FLOOR
                and r["status"] != STATUS_APPROVED
            ]
            if in_flight:
                record = sorted(in_flight, key=lambda r: r["id"])[-1]
                return result(
                    "tasks-review",
                    _in_flight_step(record, "tasks-and-specs"),
                    record["id"],
                )
            checkpoint = _next_checkpoint_id(records)
            names = ", ".join(t["id"] for t in uncovered[:3])
            more = f" (+{len(uncovered) - 3} more)" if len(uncovered) > 3 else ""
            return result(
                "tasks-review",
                f"Run planning checkpoint {checkpoint} (tasks-and-specs) with "
                f"--plan-ref covering {names}{more}; no approved checkpoint covers "
                "their plan refs.",
                checkpoint,
            )
    return result("complete", None)
