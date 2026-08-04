"""Authoritative resolver for the prospective plan/task numbering contract.

Under the corrected contract, a plan ref is ``KIND-NN-NNN``: work kind first,
then phase number, then a counter local to that kind within the phase. Task ids
remain ``TASK-NN-NNN`` and use their own phase-wide sequence. Plan-ref and task
counters are therefore independent and are not required to match. The
corrected contract is genuinely prospective: it governs only
plan/task pairs authored *after* the reviewed correction is carried by an
operator-owned release tag, installed, and proven active in the running
process. Nothing that already exists is migrated, inventoried, renumbered,
reclassified, or rejected.

The activation boundary is runtime-observed, never caller-claimed:

- **Operator-owned tag + installation** — the content root this very code was
  loaded from must carry a release-tag-shaped install receipt
  (:func:`cli.version_identities.release_version`) and installed content that
  verifies against the installer's evidence
  (:func:`cli.version_identities.installed_content`). A source checkout —
  even a clean one — is not an installation, so source changes alone never
  activate the corrected behavior.
- **Active-runtime proof** — when the call is served in-process by a connected
  MCP server, the server's startup fact must bind the loaded MCP content
  identity to the currently installed MCP content
  (:func:`cli.version_identities.mcp_identity_binds`). Newly installed files
  without fresh-process proof therefore do not activate the corrected MCP
  behavior.

Newly governed work is identified by future authoring, not by classifying old
artifacts: when the mediated task writer creates a task under the active
contract it appends a ``numbering-governed`` record to the project's
append-only provenance log. Readiness validation and plan audit re-verify
exactly those tasks and leave every other artifact — all pre-activation and
hand-preserved history — valid and untouched. Hand-typed task prose,
caller-selected dates, and filenames play no part in the boundary.

Every consumer — mediated task authoring, task readiness, task bundles, plan
audit, and the MCP tools derived from those commands — resolves the contract
through this module so CLI and MCP surfaces cannot drift.
"""
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Closed classification vocabulary for governed work kinds. Each kind owns an
# independent counter within a phase.
SUPPORTED_KINDS = (
    "BUILD",
    "DESIGN",
    "RESEARCH",
    "TEST",
    "RELEASE",
    "VERIFY",
    "CORRECTIVE",
)

# Contract identities reported in structured records.
CONTRACT_LEGACY = "phase-first-plan-refs"
CONTRACT_KIND_FIRST = "kind-first-kind-local-counters"

# The authoritative activation boundary, reported in structured records.
ACTIVATION_BOUNDARY = "reviewed-tag-installed-fresh-runtime"

# Provenance action recorded when the mediated writer creates a task under the
# active contract. Exactly the tasks with such a record are "newly governed".
GOVERNED_ACTION = "numbering-governed"

PLAN_REF_RE = re.compile(r"^([A-Z][A-Z0-9]*)-(\d{2})-(\d{3})$")
PHASE_NAME_RE = re.compile(r"^PHASE-(\d{2})$")
_TASK_ID_RE = re.compile(r"^TASK-(\d{2})-(\d{3})$")
_TASK_FILENAME_RE = re.compile(r"^(TASK-\d{2}-\d{3})\.md$")
_PLAN_REF_HEADER_RE = re.compile(r"^Plan ref:\s*(.*)$")
_PHASE_HEADER_RE = re.compile(r"^Phase:\s*(.*)$")

_STATUS_DIRS = ("open", "in-progress", "in-review", "done")


def parse_plan_ref(value: str) -> Optional[Dict[str, Any]]:
    """Parse one ``KIND-NN-NNN`` plan ref, or ``None`` when malformed."""
    match = PLAN_REF_RE.fullmatch(value.strip())
    if not match:
        return None
    kind, phase, counter = match.groups()
    return {
        "phase": phase,
        "kind": kind,
        "counter": counter,
        "kind_supported": kind in SUPPORTED_KINDS,
    }


def parse_task_id(task_id: str) -> Optional[Dict[str, str]]:
    """Parse one ``TASK-NN-NNN`` id, or ``None`` when malformed."""
    match = _TASK_ID_RE.fullmatch(task_id.strip())
    if not match:
        return None
    phase, counter = match.groups()
    return {"phase": phase, "counter": counter}


def parse_phase_name(value: str) -> Optional[str]:
    """The two-digit phase number of a canonical phase name."""
    match = PHASE_NAME_RE.fullmatch(value.strip())
    if not match:
        return None
    return match.group(1)


def plan_ref_header_value(content: str) -> Optional[str]:
    """The task body's first ``Plan ref:`` header value, or ``None``.

    Mirrors the readiness validator's header window: headers end at the first
    ``## `` section line.
    """
    return _header_value(content, _PLAN_REF_HEADER_RE)


def phase_header_value(content: str) -> Optional[str]:
    """The task body's first ``Phase:`` header value, or ``None``."""
    return _header_value(content, _PHASE_HEADER_RE)


def _header_value(content: str, pattern: "re.Pattern") -> Optional[str]:
    for line in content.splitlines():
        if line.startswith("## "):
            break
        match = pattern.match(line.strip())
        if match:
            return match.group(1).strip()
    return None


def content_root() -> Path:
    """The root of the content this process actually loaded the CLI from.

    Activation is a property of the running code, so the observed root is the
    parent of the imported ``cli`` package — the install root for an installed
    copy, the checkout for a source tree — never a caller-supplied path.
    """
    return Path(__file__).resolve().parents[1]


def _running_server_fact() -> Optional[Dict[str, Any]]:
    """The connected MCP server's startup fact, if this call runs under one.

    In-process tool dispatch registers the fact directly; wrapper-spawned CLI
    processes inherit it through the environment marker the server exports per
    invocation. A plain shell invocation has neither and returns ``None``.
    """
    from cli.restart_state import RUNNING_SERVER_ENV
    from cli.version_identities import connected_running_server

    fact = connected_running_server()
    if fact is not None:
        return fact
    raw = os.environ.get(RUNNING_SERVER_ENV, "").strip()
    if raw:
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
        if isinstance(value, dict):
            return value
        return {}
    return None


def evaluate_activation(
    release: Dict[str, Any],
    installed: Dict[str, Any],
    running: Optional[Dict[str, Any]],
    under_mcp: bool,
) -> Dict[str, Any]:
    """Pure activation verdict from observed identity facts.

    Fails closed on every uncertain fact: an unknown release, unverified or
    source-checkout content, or an MCP context whose fresh-process proof does
    not bind leaves the legacy contract authoritative. ``reason`` is a compact
    code (the first unmet boundary condition, or ``active``) so the state can
    ride along in bounded structured records.
    """
    from cli.version_identities import mcp_identity_binds

    state: Dict[str, Any] = {
        "contract": CONTRACT_LEGACY,
        "active": False,
        "boundary": ACTIVATION_BOUNDARY,
        "release": {
            "value": release.get("value"),
            "state": release.get("state"),
        },
        "installed_content": {
            "materialization": installed.get("materialization"),
            "verification": installed.get("verification"),
        },
        "running_process": None,
        "reason": None,
    }
    loaded: Dict[str, Any] = {}
    fresh_process = False
    if under_mcp:
        loaded = (running or {}).get("loaded_content") or {}
        fresh_process = mcp_identity_binds(
            loaded.get("mcp_identity"), installed.get("mcp_identity")
        )
        state["running_process"] = {
            "fresh_process": "proven" if fresh_process else "unproven",
            "loaded_verification": loaded.get("mcp_verification"),
        }
    if release.get("state") != "known":
        state["reason"] = "release-unknown"
        return state
    if installed.get("materialization") == "source-checkout":
        state["reason"] = "source-checkout"
        return state
    if installed.get("verification") != "verified":
        state["reason"] = "installed-content-" + str(
            installed.get("verification") or "unknown"
        )
        return state
    if under_mcp:
        if not fresh_process:
            state["reason"] = "fresh-process-unproven"
            return state
        if loaded.get("mcp_verification") != "verified":
            state["reason"] = "running-content-" + str(
                loaded.get("mcp_verification") or "unknown"
            )
            return state
    state["contract"] = CONTRACT_KIND_FIRST
    state["active"] = True
    state["reason"] = "active"
    return state


def activation_state() -> Dict[str, Any]:
    """Observe the runtime activation facts and evaluate the boundary."""
    from cli.host_capability import under_mcp_host
    from cli.version_identities import installed_content, release_version

    root = content_root()
    running = _running_server_fact()
    return evaluate_activation(
        release_version(root),
        installed_content(root),
        running,
        under_mcp_host() or running is not None,
    )


def classify_binding(
    task_id: str,
    plan_ref: Optional[str],
    phase_header: Optional[str] = None,
) -> Dict[str, Any]:
    """Classify one newly governed task's plan-ref binding.

    Callers apply this only to newly governed work under the active contract —
    never to pre-activation artifacts, which remain valid unchanged. The
    verdict carries structured evidence: classification, blocking status,
    both independent counters, and an actionable detail string.

    ``phase_header`` is the task body's declared ``Phase:`` value. When the
    caller supplies it (pass ``None`` only when no task body is in hand), the
    declared phase must name the same two-digit phase as the task id and plan
    ref — the three identities are compared consistently, so a governed task
    cannot anchor itself to a foreign phase.
    """
    plan_ref = (plan_ref or "").strip()
    task = parse_task_id(task_id)
    verdict: Dict[str, Any] = {
        "task_id": task_id,
        "plan_ref": plan_ref,
        "phase_header": phase_header.strip() if phase_header else phase_header,
        "contract": CONTRACT_KIND_FIRST,
        "classification": "valid",
        "blocking": False,
        "task_counter": task["counter"] if task else None,
        "plan_ref_counter": None,
        "detail": None,
    }
    if task is None:
        verdict["classification"] = "task-id-malformed"
        verdict["blocking"] = True
        verdict["detail"] = f"task id does not match TASK-NN-NNN: {task_id!r}"
        return verdict
    if not plan_ref:
        verdict["classification"] = "plan-ref-missing"
        verdict["blocking"] = True
        verdict["detail"] = (
            f"{task_id} carries no Plan ref: header; newly governed work "
            "binds one primary KIND-NN-NNN plan ref"
        )
        return verdict
    ref = parse_plan_ref(plan_ref)
    if ref is None:
        verdict["classification"] = "plan-ref-malformed"
        verdict["blocking"] = True
        verdict["detail"] = (
            f"{task_id} carries a plan ref that does not match KIND-NN-NNN: "
            f"{plan_ref!r}"
        )
        return verdict
    verdict["plan_ref_counter"] = ref["counter"]
    if not ref["kind_supported"]:
        verdict["classification"] = "plan-ref-kind-unsupported"
        verdict["blocking"] = True
        verdict["detail"] = (
            f"{task_id} plan ref {plan_ref} uses unsupported kind "
            f"{ref['kind']}; supported kinds: {', '.join(SUPPORTED_KINDS)}"
        )
        return verdict
    if ref["phase"] != task["phase"]:
        verdict["classification"] = "plan-ref-phase-mismatch"
        verdict["blocking"] = True
        verdict["detail"] = (
            f"{task_id} is phase {task['phase']} but plan ref {plan_ref} "
            f"names phase {ref['phase']}"
        )
        return verdict
    if phase_header is not None:
        declared = phase_header.strip()
        if not declared:
            verdict["classification"] = "phase-header-missing"
            verdict["blocking"] = True
            verdict["detail"] = (
                f"{task_id} carries no Phase: header; newly governed work "
                "declares the phase file that anchors its plan ref"
            )
            return verdict
        declared_phase = parse_phase_name(declared)
        if declared_phase is None:
            verdict["classification"] = "phase-header-malformed"
            verdict["blocking"] = True
            verdict["detail"] = (
                f"{task_id} declares a Phase: header that does not match "
                f"PHASE-NN: {declared!r}"
            )
            return verdict
        if declared_phase != task["phase"]:
            verdict["classification"] = "phase-header-mismatch"
            verdict["blocking"] = True
            verdict["detail"] = (
                f"{task_id} and plan ref {plan_ref} are phase "
                f"{task['phase']} but the Phase: header declares {declared} "
                f"(phase {declared_phase}); the task id, plan ref, and "
                "declared phase must name one phase"
            )
            return verdict
    verdict["detail"] = (
        f"{task_id} binds {plan_ref} in phase {task['phase']}; task and "
        "plan-ref counters are independent"
    )
    return verdict


def verify_phase_anchor(
    project_root: Path, phase_header: Optional[str], plan_ref: str
) -> Optional[Tuple[str, str]]:
    """Fail-closed phase-anchor check: ``(classification, detail)`` or ``None``.

    Applied by readiness validation (and task-bundle through it) to newly
    governed work after :func:`classify_binding` accepts the header
    comparison: the declared phase file must exist and carry the task's plan
    ref, per ``templates/TASK.md`` ("the matching phase file must carry the
    same plan ref"). Authoring does not consume this — a task may be written
    before its phase file — but a governed task is not ready until its
    anchors resolve.
    """
    declared = (phase_header or "").strip()
    phase_relpath = f"phases/{declared}.md"
    phase_file = Path(project_root) / "phases" / f"{declared}.md"
    if not phase_file.is_file():
        return (
            "phase-file-missing",
            f"declared phase file not found for governed task: {phase_relpath}",
        )
    try:
        phase_text = phase_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return (
            "phase-file-unreadable",
            f"declared phase file unreadable: {phase_relpath} — {exc}",
        )
    if plan_ref not in phase_text:
        return (
            "phase-anchor-missing-plan-ref",
            (
                f"{phase_relpath} does not carry plan ref {plan_ref}; the "
                "matching phase file must list the same plan ref"
            ),
        )
    return None


def collect_task_plan_refs(project_root: Path) -> Dict[str, str]:
    """Map every canonical on-disk task id to its raw ``Plan ref:`` value.

    Scans the four status directories in a fixed order; the first file found
    for an id wins (a multi-directory collision is pre-existing corruption the
    writers refuse separately). Non-canonical filenames are not governed and
    are skipped. An unreadable body maps to an empty string.
    """
    refs: Dict[str, str] = {}
    for status in _STATUS_DIRS:
        status_dir = Path(project_root) / "tasks" / status
        if not status_dir.is_dir():
            continue
        for entry in sorted(status_dir.iterdir()):
            match = _TASK_FILENAME_RE.match(entry.name)
            if not match or not entry.is_file():
                continue
            task_id = match.group(1)
            if task_id in refs:
                continue
            try:
                content = entry.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                refs[task_id] = ""
                continue
            refs[task_id] = plan_ref_header_value(content) or ""
    return refs


def duplicate_binding(
    task_id: str, plan_ref: Optional[str], existing_refs: Dict[str, str]
) -> Optional[str]:
    """Detail string when ``plan_ref`` is already bound to another task.

    One plan ref binds one task: every corrective task receives its own plan
    ref, so a ref cannot be reused merely because multiple tasks correct the
    same original item, and an already-allocated historical ref is never
    reallocated to new work.
    """
    plan_ref = (plan_ref or "").strip()
    if not plan_ref:
        return None
    for other_id in sorted(existing_refs):
        if other_id == task_id:
            continue
        if existing_refs[other_id].strip() != plan_ref:
            continue
        return (
            f"plan ref {plan_ref} is already bound to {other_id}; every "
            "task (including each corrective task) receives its own plan ref"
        )
    return None


def governed_task_ids(project_root: Path) -> frozenset:
    """Task ids the mediated writer created under the active contract.

    Read from the project's append-only provenance log: a
    :data:`GOVERNED_ACTION` record is written only at post-activation creation
    time, so exactly these ids are "newly governed". Everything else — every
    pre-activation or otherwise ungoverned artifact — is outside the corrected
    rule and remains valid unchanged. Records survive later status moves
    because the id is carried in the recorded filename.
    """
    from cli.provenance import _read_log

    records = _read_log(Path(project_root))
    ids = set()
    for record in records or ():
        if record.get("action") != GOVERNED_ACTION:
            continue
        match = _TASK_FILENAME_RE.match(os.path.basename(record["relpath"]))
        if match:
            ids.add(match.group(1))
    return frozenset(ids)


def record_governed_creation(
    project_root: Path, relative_target: str, content: Any
) -> bool:
    """Record that a task was newly created under the active contract.

    Called by the mediated task writer after a successful post-activation
    creation. Best-effort like every provenance append: a missed record
    degrades to the task not being re-verified downstream — the write-time
    guard has already enforced the contract for it — never to a false verdict.
    """
    from cli.provenance import record_write

    if isinstance(content, str):
        content = content.encode("utf-8")
    return record_write(
        Path(project_root),
        Path(project_root) / "tasks" / relative_target,
        content,
        action=GOVERNED_ACTION,
    )


def guard_task_write(
    project_root: Path,
    task_id: str,
    content: Any,
    *,
    creating: bool,
    state: Optional[Dict[str, Any]] = None,
) -> Optional[Tuple[str, str]]:
    """Fail-closed authoring guard: ``(rule, detail)`` refusal or ``None``.

    Applied by the mediated task writer before any on-disk change. The guard
    is inert while the corrected contract is not proven active, and — being
    prospective — it governs only a genuinely new task id (``creating``) or a
    task this runtime already created under the contract. Updates to every
    other existing task pass untouched, whatever their historical binding.

    ``state`` lets the caller reuse one runtime observation for the guard and
    the post-write governed-creation record.
    """
    if state is None:
        state = activation_state()
    if not state["active"]:
        return None
    if not creating and task_id not in governed_task_ids(project_root):
        return None
    if isinstance(content, bytes):
        # The writer's schema gate already refused invalid UTF-8.
        content = content.decode("utf-8", errors="replace")
    plan_ref = plan_ref_header_value(content)
    verdict = classify_binding(
        task_id, plan_ref, phase_header_value(content) or ""
    )
    if verdict["blocking"]:
        return (verdict["classification"], verdict["detail"])
    duplicate = duplicate_binding(
        task_id, plan_ref, collect_task_plan_refs(project_root)
    )
    if duplicate is not None:
        return ("plan-ref-reused", duplicate)
    return None
