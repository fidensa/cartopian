"""Rehearse one task through the assignment-preparation path without writing.

Readiness (``validate-task-readiness``) proves a task is structurally sound.
Dispatch proves an assignment can actually launch. The gap between the two is
where a kickoff stalls: the role has no agent, a work root is unmapped on this
machine, the composed prompt refuses over a spec open question, or the host
cannot wait out the role timeout. This module walks that path read-only —
role resolution, the deterministic prompt composition, and the launch
prerequisites ``cartopian dispatch`` will enforce — so planning can make
"task 1 is dispatchable" its exit condition and startup can report it as one
verdict instead of discovering it one refusal at a time.

Nothing here writes, moves, launches, or consumes automation budget.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

ROLE_SOURCE_EXPLICIT = "explicit"
ROLE_SOURCE_ASSIGNEE = "assignee-header"
ROLE_SOURCE_TASK_RUN = "sole-task-run-role"
ROLE_SOURCE_DECLARED = "sole-declared-role"

def _header(content: str, name: str) -> Optional[str]:
    for line in content.splitlines():
        if line.startswith("## "):
            break
        stripped = line.strip()
        if stripped.startswith(f"{name}:"):
            return stripped[len(name) + 1 :].strip()
    return None


def resolve_role(
    roles: Dict[str, Any], content: str, explicit: Optional[str] = None
) -> Dict[str, Any]:
    """Pick the assignee role the rehearsal exercises, naming how it was chosen.

    Order: an explicit ``--role``; the task's ``Assignee:`` header when it
    names a declared role; the only non-PM role with a ``task_run`` launch
    permission; the only non-PM declared role. Anything else is unresolved —
    the rehearsal then reports the candidates instead of guessing.
    """
    if explicit:
        if explicit in roles:
            return {"role": explicit, "source": ROLE_SOURCE_EXPLICIT, "candidates": []}
        return {
            "role": None,
            "source": ROLE_SOURCE_EXPLICIT,
            "candidates": sorted(name for name in roles if name != "pm"),
            "detail": f"role {explicit!r} is not declared",
        }
    assignee = (_header(content, "Assignee") or "").strip()
    if assignee in roles and assignee != "pm":
        return {"role": assignee, "source": ROLE_SOURCE_ASSIGNEE, "candidates": []}
    task_run = sorted(
        name
        for name, record in roles.items()
        if name != "pm" and "task_run" in (record.get("auto_launch") or [])
    )
    if len(task_run) == 1:
        return {"role": task_run[0], "source": ROLE_SOURCE_TASK_RUN, "candidates": []}
    declared = sorted(name for name in roles if name != "pm")
    if len(declared) == 1:
        return {"role": declared[0], "source": ROLE_SOURCE_DECLARED, "candidates": []}
    return {
        "role": None,
        "source": None,
        "candidates": declared,
        "detail": (
            "no assignee role resolves: set the task's Assignee: header to a "
            "declared role or pass --role"
        ),
    }


def _launch_record(
    role: str, role_record: Dict[str, Any], resolved_work_roots: Dict[str, str]
) -> Dict[str, Any]:
    """The prerequisites ``dispatch`` enforces, decided by the same code.

    Manual mode (no agent, or no ``task_run`` permission) still checks that
    every mapped work root exists, because a manual assignee writes there too.
    Dispatch mode runs every role and environment check dispatch runs.
    """
    from cli import launch_preflight

    launch = role_record.get("launch") or {}
    agent = launch.get("agent")
    auto = "task_run" in (role_record.get("auto_launch") or [])
    record: Dict[str, Any] = {
        "role": role,
        "mode": "dispatch" if (agent and auto) else "manual",
        "agent": agent,
        "auto_launch_task_run": auto,
        "ok": True,
        "detail": None,
    }
    if record["mode"] == "manual":
        record["detail"] = (
            "manual handoff: "
            + (
                f"roles.{role}.agent is not configured"
                if not agent
                else f"'task_run' is not in roles.{role}.auto_launch"
            )
        )
        findings = [
            f
            for f in launch_preflight.environment_checks(role, {}, resolved_work_roots)
        ]
    else:
        findings = launch_preflight.role_checks(role, role_record, context="dispatch")
        findings += launch_preflight.environment_checks(
            role, role_record, resolved_work_roots
        )
    if findings:
        record["ok"] = False
        record["detail"] = findings[0]["message"]
        record["code"] = findings[0]["code"]
    return record


def rehearse(
    task_path: Path,
    *,
    role: Optional[str] = None,
    resolved: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Rehearse the assignment path for one task. Never raises.

    Returns ``{ok, role, role_source, compose, launch, blockers}``. ``ok`` is
    true only when a role resolved, the prompt composed with no findings, and
    the launch prerequisites for the resolved mode hold.
    """
    from cli import prompt_composer
    from cli.commands.handoff_packet import _find_project_root
    from cli.commands.resolve_config import _CliError, resolve_project_configuration

    task_path = Path(task_path)
    blockers: List[str] = []
    record: Dict[str, Any] = {
        "ok": False,
        "task_path": str(task_path),
        "role": None,
        "role_source": None,
        "compose": None,
        "launch": None,
        "blockers": blockers,
    }
    try:
        content = task_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        blockers.append(f"task unreadable: {exc}")
        return record
    if resolved is None:
        project_root = _find_project_root(task_path)
        if project_root is None:
            blockers.append("project root not found above the task")
            return record
        try:
            resolved = resolve_project_configuration(project_root)
        except _CliError as err:
            blockers.append(f"project configuration invalid: {err.message}")
            return record
    roles = resolved["roles"]
    choice = resolve_role(roles, content, role)
    record["role"], record["role_source"] = choice["role"], choice["source"]
    if choice["role"] is None:
        blockers.append(
            choice.get("detail", "no assignee role resolves")
            + (
                f" (declared: {', '.join(choice['candidates'])})"
                if choice["candidates"]
                else ""
            )
        )
        return record
    chosen = choice["role"]

    try:
        composed = prompt_composer.compose(task_path, chosen)
        findings = [
            f"{f['code']}: {f['detail']} — {f['recovery']}"
            for f in composed.get("findings", [])
            if f.get("severity") == "fail"
        ]
        record["compose"] = {
            "outcome": composed.get("outcome"),
            "findings": findings,
        }
        if composed.get("outcome") != "composed":
            blockers.extend(findings or ["prompt composition returned no composed body"])
    except prompt_composer.ComposeRefusal as refusal:
        record["compose"] = {
            "outcome": "refused",
            "findings": [f"{refusal.code}: {refusal.detail}"],
        }
        blockers.append(f"compose-assignment-prompt refused ({refusal.code}): {refusal.detail}")
    except Exception as exc:  # any unresolved input surfaces as one blocker
        record["compose"] = {"outcome": "error", "findings": [str(exc)]}
        blockers.append(f"compose-assignment-prompt failed: {exc}")

    launch = _launch_record(chosen, roles[chosen], resolved.get("work_roots") or {})
    record["launch"] = launch
    if not launch["ok"]:
        blockers.append(launch["detail"])
    record["ok"] = not blockers
    return record


def dispatch_action(task_id: str, rehearsal: Dict[str, Any]) -> str:
    """One sentence naming the exact dispatch action for a rehearsed task."""
    launch = rehearsal.get("launch") or {}
    role = rehearsal.get("role")
    if launch.get("mode") == "dispatch":
        return (
            f"Start {task_id} with run task; the assignment dispatches "
            f"automatically to role {role!r} (agent {launch.get('agent')!r})."
        )
    return (
        f"Start {task_id} with run task; hand the composed prompt to role "
        f"{role!r} manually and wait on its report."
    )
