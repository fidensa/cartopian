"""Planning checkpoint identity and scope, independent of execution order."""
import re
from typing import Dict, Optional

from cli.numbering_contract import SUPPORTED_KINDS

CHECKPOINT_PATTERN = (
    r"PLAN-(?:REQUIREMENTS|IMPLEMENTATION|PHASE-\d{2}|"
    rf"(?:{'|'.join(SUPPORTED_KINDS)})-\d{{2}}-\d{{3}}|\d{{3}})"
)
CHECKPOINT_ID_RE = re.compile(rf"^{CHECKPOINT_PATTERN}$")
LEGACY_ID_RE = re.compile(r"^PLAN-\d{3}$")
STAGES = ("requirements-and-standards", "implementation-plan", "phases", "tasks-and-specs")


def header(text: str, name: str) -> Optional[str]:
    for line in text.splitlines():
        if line.startswith("## "):
            break
        if line.strip().startswith(f"{name}:"):
            return line.strip()[len(name) + 1:].strip()
    return None


def identity_scope(checkpoint: str) -> Dict[str, Optional[str]]:
    """Canonical identities carry stage and scope; counters carry neither."""
    stage = phase = plan_ref = None
    if checkpoint == "PLAN-REQUIREMENTS":
        stage = STAGES[0]
    elif checkpoint == "PLAN-IMPLEMENTATION":
        stage = STAGES[1]
    elif re.fullmatch(r"PLAN-PHASE-\d{2}", checkpoint):
        stage, phase = STAGES[2], checkpoint.removeprefix("PLAN-")
    elif CHECKPOINT_ID_RE.fullmatch(checkpoint) and not LEGACY_ID_RE.fullmatch(checkpoint):
        stage, plan_ref = STAGES[3], checkpoint.removeprefix("PLAN-")
        phase = "PHASE-" + plan_ref.split("-")[1]
    return {"stage": stage, "phase": phase, "plan_ref": plan_ref}


def scope(checkpoint: str, text: str) -> Dict[str, Optional[str]]:
    """Resolve explicit legacy metadata or canonical identity, rejecting conflicts.

    An approval never borrows scope from a separate prompt or report. Missing
    legacy metadata is deliberately unverifiable, not positional evidence.
    """
    result = identity_scope(checkpoint)
    canonical = result["stage"] is not None
    error = None
    for key, field in (("stage", "Planning stage"), ("phase", "Phase"), ("plan_ref", "Plan ref")):
        value = header(text, field)
        if value:
            normalized = None if value.lower() in {"n/a", "none"} else value
            if canonical and normalized != result[key]:
                error = f"{field}: {value} conflicts with checkpoint {checkpoint}"
            if not canonical:
                result[key] = normalized
    if result["stage"] not in STAGES:
        error = error or f"{checkpoint} has no explicit valid Planning stage"
    if result["stage"] in STAGES[2:] and not re.fullmatch(r"PHASE-\d{2}", result["phase"] or ""):
        error = error or f"{checkpoint} has no explicit Phase scope"
    if result["stage"] == STAGES[3] and not result["plan_ref"]:
        error = error or f"{checkpoint} has no explicit Plan ref scope"
    result["scope_error"] = error
    return result


def artifact_checkpoint(name: str, family: str) -> Optional[str]:
    match = re.fullmatch(rf"{family}-({CHECKPOINT_PATTERN})\.md", name)
    return match.group(1) if match else None
