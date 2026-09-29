"""Derive where planning stands from the filesystem alone.

"Planning approved" and "task 1 can be dispatched" are two different states,
and a session that conflates them stalls at startup: the phases checkpoint was
approved, task generation never happened, and the state writer described an
empty queue as closeout readiness. This module is the one place that reads
the planning surface — ``REQUIREMENTS.md``, ``IMPLEMENTATION_PLAN.md``,
``phases/``, task placement, and the planning-checkpoint prompt, report, and
review slots — and names the exact remaining planning step, so ``next-action``
and ``compose-state`` state the same thing.

Checkpoint approval is scoped by identity or explicit legacy metadata. Neither
artifact presence nor a checkpoint's position substitutes for a stage review.

Read-only, standard library only, and cheap: it reads headers, not bodies.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from cli import checkpoint_identity, request_trace

_TASK_FILENAME_RE = re.compile(r"^(TASK-\d{2}-\d{3})\.md$")
_PHASE_STEM_RE = re.compile(r"^PHASE-\d{2}$")
_ALL_STATUSES = ("open", "in-progress", "in-review", "done")

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
            checkpoint = checkpoint_identity.artifact_checkpoint(path.name, "REVIEW")
            if checkpoint is None:
                continue
            text = _read(path)
            record = slot(checkpoint)
            record["review"] = True
            verdict = (_header(text, "Verdict") or "").strip().lower() or None
            record["verdict"] = verdict
            record.update(checkpoint_identity.scope(checkpoint, text))
            if verdict == "approve":
                record["status"] = STATUS_APPROVED
            elif verdict in {"request-changes", "reject"}:
                record["status"] = STATUS_REQUEST_CHANGES
            else:
                record["status"] = STATUS_AWAITING_VERDICT
    reports_dir = project_path / "reports"
    if reports_dir.is_dir():
        for path in sorted(reports_dir.glob("REPORT-PLAN-*.md")):
            checkpoint = checkpoint_identity.artifact_checkpoint(path.name, "REPORT")
            if checkpoint is None:
                continue
            record = slot(checkpoint)
            record["report"] = True
            if record["status"] == STATUS_PENDING:
                record["status"] = STATUS_AWAITING_VERDICT
                record.update(checkpoint_identity.scope(checkpoint, _read(path)))
    prompts_dir = project_path / "prompts"
    if prompts_dir.is_dir():
        for path in sorted(prompts_dir.glob("PROMPT-PLAN-*.md")):
            checkpoint = checkpoint_identity.artifact_checkpoint(path.name, "PROMPT")
            if checkpoint is None:
                continue
            record = slot(checkpoint)
            record["prompt"] = True
            prompt_scope = checkpoint_identity.scope(checkpoint, _read(path))
            if record["status"] == STATUS_PENDING:
                record["status"] = STATUS_AWAITING_REPORT
                record.update(prompt_scope)
            elif not record["review"] and not any(
                record.get(key) for key in ("stage", "phase", "plan_ref")
            ):
                # A legacy report may omit scope; its prompt can identify the
                # pending handoff. A retained verdict must carry its own scope.
                record.update(prompt_scope)
    return records


def _covers(
    record: Dict[str, Any], stage: str, phase: Optional[str] = None,
    plan_ref: Optional[str] = None,
) -> bool:
    if record.get("scope_error") or record.get("stage") != stage:
        return False
    if phase is not None and record.get("phase") != phase:
        return False
    return plan_ref is None or request_trace._review_covers_plan_ref(
        record.get("plan_ref") or "", plan_ref
    )


def _gate_record(
    records: Dict[str, Dict[str, Any]], checkpoint: str, stage: str,
    phase: Optional[str] = None, plan_ref: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    matching = [r for r in records.values() if _covers(r, stage, phase, plan_ref)]
    if any(r["status"] == STATUS_APPROVED for r in matching):
        return None
    return next(iter(matching), {"id": checkpoint, "status": STATUS_PENDING, "verdict": None})


def missing_reviews(project_path: Path) -> List[Dict[str, Any]]:
    """Every required review reached by the artifacts, including bypassed stages."""
    project_path = Path(project_path)
    records = checkpoint_records(project_path)
    phases = phase_stems(project_path)
    tasks = task_headers(project_path)
    plan = (project_path / "IMPLEMENTATION_PLAN.md").is_file()
    gates = []
    if (project_path / "REQUIREMENTS.md").is_file() or plan or phases or tasks:
        gates.append(("PLAN-REQUIREMENTS", "requirements-and-standards", None, None))
    if plan or phases or tasks:
        gates.append(("PLAN-IMPLEMENTATION", "implementation-plan", None, None))
    gates.extend(("PLAN-" + phase, "phases", phase, None) for phase in phases)
    missing = []
    for task in tasks:
        if not request_trace.PLAN_REF_RE.fullmatch(task["plan_ref"] or ""):
            missing.append({
                "kind": "planning-review-scope-missing", "task": task["id"],
                "detail": f"{task['id']} needs a canonical Plan ref before its planning review can be resolved.",
            })
            continue
        gates.append(("PLAN-" + task["plan_ref"], "tasks-and-specs", task["phase"], task["plan_ref"]))
    for checkpoint, stage, phase, plan_ref in gates:
        record = _gate_record(records, checkpoint, stage, phase, plan_ref)
        if record:
            missing.append({
                "kind": "planning-review-missing", "checkpoint": record["id"],
                "stage": stage, "phase": phase, "plan_ref": plan_ref,
                "detail": _in_flight_step(record, stage),
            })
    return missing


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

    def review_gate(checkpoint: str, what: str, stage: str,
                    phase: Optional[str] = None, plan_ref: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if not planning_review_required:
            return None
        record = _gate_record(records, checkpoint, what, phase, plan_ref)
        if record is None:
            return None
        return result(stage, _in_flight_step(record, what), record["id"])

    if not requirements and not plan and not stems and not tasks:
        return result("no-plan", "No plan exists: begin planning with plan project (Stage 1, requirements).")
    if not requirements and (planning_review_required or not plan):
        return result("requirements", "Author REQUIREMENTS.md and STANDARDS.md (plan project Stage 1).")
    gate = review_gate("PLAN-REQUIREMENTS", "requirements-and-standards", "requirements-review")
    if gate:
        return gate
    if not plan:
        return result("plan", "Generate IMPLEMENTATION_PLAN.md (plan project Stage 2).")
    gate = review_gate("PLAN-IMPLEMENTATION", "implementation-plan", "plan-review")
    if gate:
        return gate
    if not stems:
        return result("phases", "Generate the phase files (plan project Stage 3).")
    for phase in stems:
        gate = review_gate("PLAN-" + phase, "phases", "phases-review", phase)
        if gate:
            return gate

    active = [t for t in tasks if t["status"] in ("open", "in-progress", "in-review")]
    unstarted = _unstarted_phase(stems, tasks)
    # Check all generated work, even after dispatch: execution cannot create approval.
    if planning_review_required:
        for task in tasks:
            if not request_trace.PLAN_REF_RE.fullmatch(task["plan_ref"] or ""):
                return result("tasks", f"Set the canonical Plan ref for {task['id']} before its planning review.")
            checkpoint = "PLAN-" + task["plan_ref"]
            gate = review_gate(checkpoint, "tasks-and-specs", "tasks-review",
                               task["phase"], task["plan_ref"])
            if gate:
                return gate
    if not tasks or (not active and unstarted is not None):
        target = unstarted or stems[0]
        return result(
            "tasks", f"Generate tasks and specs for {target} (plan project Stage 4), then "
            + ("review each plan ref using its PLAN-<plan-ref> checkpoint."
               if planning_review_required else "rehearse task 1 with validate-task-readiness --rehearse-dispatch."),
        )
    return result("complete", None)
