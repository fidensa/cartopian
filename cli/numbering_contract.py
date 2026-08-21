"""Authoritative resolver for Cartopian task-scoped numbering.

Under the corrected contract, a plan ref is ``KIND-NN-NNN``: work kind first,
then phase number, then one counter shared by every work kind in the phase.
The plan allocates ``NN-NNN`` and every downstream task-scoped artifact carries
that suffix unchanged. The corrected contract is genuinely prospective: it governs only
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

Every consumer — mediated plan, phase, task, spec, and prompt authoring; task
readiness and lifecycle movement; task bundles; plan audit; and the MCP tools
derived from those commands — resolves the contract through this module so
CLI and MCP surfaces cannot drift.
"""
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Closed classification vocabulary for governed work kinds. Kind classifies an
# allocation; it never owns a counter.
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
CONTRACT_ALIGNED = "kind-first-phase-wide-aligned-suffix"
# Compatibility alias for callers that consumed the old constant name. Its
# value deliberately reports the restored contract, not the regressed model.
CONTRACT_KIND_FIRST = CONTRACT_ALIGNED

# The authoritative activation boundary, reported in structured records.
ACTIVATION_BOUNDARY = "reviewed-tag-installed-fresh-runtime"

# Provenance action recorded when the mediated writer creates a task under the
# active contract. Exactly the tasks with such a record are "newly governed".
GOVERNED_ACTION = "numbering-governed"

PLAN_REF_RE = re.compile(r"^([A-Z][A-Z0-9]*)-(\d{2})-(\d{3})$")
SUPPORTED_PLAN_REF_RE = re.compile(
    rf"^(?:{'|'.join(SUPPORTED_KINDS)})-\d{{2}}-\d{{3}}$"
)
PHASE_NAME_RE = re.compile(r"^PHASE-(\d{2})$")
_TASK_ID_RE = re.compile(r"^TASK-(\d{2})-(\d{3})$")
_TASK_FILENAME_RE = re.compile(r"^(TASK-\d{2}-\d{3})\.md$")
_PLAN_REF_HEADER_RE = re.compile(r"^Plan ref:\s*(.*)$")
_PLAN_REFS_HEADER_RE = re.compile(r"^Plan refs?:\s*(.*)$")
_PHASE_HEADER_RE = re.compile(r"^Phase:\s*(.*)$")
_SPEC_HEADER_RE = re.compile(r"^Spec:\s*(.*)$")
_TARGET_HEADER_RE = re.compile(r"^Target:\s*(.*)$")
_H1_RE = re.compile(r"^#[ \t]+(.*)$")
_CANONICAL_TARGET_RE = re.compile(r"^(TASK|SPEC)-(\d{2})-(\d{3})(?!\d)")
_CANONICAL_REVIEW_RE = re.compile(r"^(REVIEW-\d{2}-\d{3})(?![0-9A-Za-z-])")
_PLAN_REF_TOKEN_RE = re.compile(
    r"(?<![A-Z0-9-])([A-Z][A-Z0-9]*-\d{2}-\d{3})(?![A-Z0-9-])"
)
_LEGACY_PLAN_REF_TOKEN_RE = re.compile(
    r"(?<![A-Z0-9-])(P\d{2}-[A-Z][A-Z0-9]*-\d{3})(?![A-Z0-9-])"
)
_SPEC_ID_RE = re.compile(r"^SPEC-(\d{2})-(\d{3})$")
_TASK_SCOPED_ID_RE = re.compile(
    r"^(TASK|SPEC|PROMPT|REVIEW)-(\d{2})-(\d{3})$"
)
_REPORT_ID_RE = re.compile(r"^REPORT-(\d{2})-(\d{3})(?:-review)?$")

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


def target_header_value(content: str) -> Optional[str]:
    """The artifact body's first ``Target:`` header value, or ``None``."""
    return _header_value(content, _TARGET_HEADER_RE)


def canonical_target_id(value: Optional[str]) -> Optional[str]:
    """The canonical unit a ``Target:`` value names, or ``None``.

    A target is written three ways in practice — a bare ``TASK-NN-NNN``, an id
    followed by a title, or the artifact path itself — and all three name the
    same unit. A value carrying no canonical id at any of those shapes is
    malformed rather than merely mismatched, and resolves to ``None``.
    """
    if not value:
        return None
    token = value.split()[0].strip("`\"'")
    stem = token.replace("\\", "/").rsplit("/", 1)[-1]
    if stem.endswith(".md"):
        stem = stem[: -len(".md")]
    match = _CANONICAL_TARGET_RE.match(stem)
    if match is None:
        return None
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def canonical_review_id(content: str) -> Optional[str]:
    """The canonical review id the body's own first H1 declares, or ``None``.

    The first H1 is the artifact's self-declared identity. A body whose first
    H1 is absent or is not a canonical ``REVIEW-NN-NNN`` declares no identity
    to bind, which is a different failure from declaring the wrong one.
    """
    for line in content.splitlines():
        match = _H1_RE.match(line)
        if match is None:
            continue
        heading = _CANONICAL_REVIEW_RE.match(match.group(1).strip())
        return heading.group(1) if heading else None
    return None


def review_identity_refusal(
    review_id: str, task_id: str, review_content: str
) -> Optional[Tuple[str, str]]:
    """Bind a review body's own identity to its filename and to its task.

    A filename is caller-supplied metadata; the body is the document that
    carries the verdict and the determinations. A canonically named review
    file whose body names another unit is exactly the case a suffix check
    cannot see, so the body's ``# REVIEW-NN-NNN`` heading and its ``Target:``
    are both resolved here and compared with the review path and the task
    under review. Unconditional by design: this binding does not depend on
    the prospective numbering contract being active, because a review that
    attributes another unit's determinations is wrong under either contract.
    """
    declared = canonical_review_id(review_content)
    if declared is None:
        return (
            "review-identity-missing",
            f"{review_id} carries no canonical `# REVIEW-NN-NNN` heading, so "
            "its body declares no identity to bind",
        )
    if declared != review_id:
        return (
            "review-identity-mismatch",
            f"{review_id} carries the body identity {declared}",
        )
    task = parse_task_id(task_id)
    if task is None:
        return ("task-id-malformed", f"task id is malformed: {task_id}")
    raw_target = target_header_value(review_content)
    if raw_target is None:
        return (
            "review-target-missing",
            f"{review_id} carries no `Target:` header, so its body names no "
            f"unit under review and cannot be bound to {task_id}",
        )
    target = canonical_target_id(raw_target)
    if target is None:
        return (
            "review-target-malformed",
            f"{review_id} targets `{raw_target}`, which names no canonical "
            "TASK-NN-NNN or SPEC-NN-NNN unit",
        )
    allowed = {task_id, f"SPEC-{task['phase']}-{task['counter']}"}
    if target not in allowed:
        return (
            "review-target-mismatch",
            f"{review_id} targets {target}, but the unit under review is "
            f"{task_id}",
        )
    return None


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
    state["contract"] = CONTRACT_ALIGNED
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
    both observed suffixes, and an actionable detail string.

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
        "contract": CONTRACT_ALIGNED,
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
    if ref["counter"] != task["counter"]:
        verdict["classification"] = "plan-task-suffix-mismatch"
        verdict["blocking"] = True
        verdict["detail"] = (
            f"{task_id} requires plan ref suffix {task['phase']}-{task['counter']} "
            f"but {plan_ref} carries {ref['phase']}-{ref['counter']}; use "
            f"{ref['kind']}-{task['phase']}-{task['counter']} or allocate the "
            "task from the governing plan ref"
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
        f"{task_id} binds {plan_ref}; both carry the plan-allocated suffix "
        f"{task['phase']}-{task['counter']}"
    )
    return verdict


def spec_header_value(content: str) -> Optional[str]:
    """The task body's first ``Spec:`` header value, or ``None``."""
    return _header_value(content, _SPEC_HEADER_RE)


def plan_refs_header_value(content: str) -> Optional[str]:
    """The first singular/plural plan-ref header in an artifact."""
    return _header_value(content, _PLAN_REFS_HEADER_RE)


def extract_plan_refs(content: str) -> Tuple[str, ...]:
    """Return supported, grammar-valid plan refs in first-seen order."""
    seen = set()
    refs = []
    for match in _PLAN_REF_TOKEN_RE.finditer(content):
        value = match.group(1)
        parsed = parse_plan_ref(value)
        if parsed is None or not parsed["kind_supported"] or value in seen:
            continue
        seen.add(value)
        refs.append(value)
    return tuple(refs)


def _extract_plan_ref_tokens(content: str) -> Tuple[str, ...]:
    """Return every grammar-valid kind-first token, including unknown kinds."""
    return tuple(
        dict.fromkeys(
            match.group(1) for match in _PLAN_REF_TOKEN_RE.finditer(content)
        )
    )


def _extract_legacy_plan_ref_tokens(content: str) -> Tuple[str, ...]:
    """Return historical phase-first tokens in first-seen order."""
    return tuple(
        dict.fromkeys(
            match.group(1) for match in _LEGACY_PLAN_REF_TOKEN_RE.finditer(content)
        )
    )


def validate_plan_allocations(content: str) -> List[Dict[str, Any]]:
    """Find conflicting phase-wide allocations in one plan projection.

    Repeating the same ref in coverage tables is valid. Two different refs
    carrying the same ``NN-NNN`` are not: work kind is classification, so the
    suffix identifies exactly one phase allocation.
    """
    by_suffix: Dict[str, str] = {}
    findings: List[Dict[str, Any]] = []
    for plan_ref in extract_plan_refs(content):
        parsed = parse_plan_ref(plan_ref)
        assert parsed is not None
        suffix = f"{parsed['phase']}-{parsed['counter']}"
        prior = by_suffix.get(suffix)
        if prior is not None and prior != plan_ref:
            findings.append({
                "classification": "plan-suffix-reused",
                "blocking": True,
                "suffix": suffix,
                "plan_refs": [prior, plan_ref],
                "detail": (
                    f"phase suffix {suffix} is allocated by both {prior} and "
                    f"{plan_ref}; allocate one phase-wide sequence across all "
                    "work kinds"
                ),
            })
        else:
            by_suffix[suffix] = plan_ref
    return findings


def validate_plan_revision(
    existing_content: str, candidate_content: str
) -> List[Dict[str, Any]]:
    """Reject allocation collisions introduced by a plan revision.

    The aligned-suffix contract is prospective.  A mediated revision may
    preserve a collision that was already present in the live plan before the
    active runtime observed it, but it may not introduce a new collision,
    replace either owner of an existing collision, or add another owner to the
    same suffix.  Comparing normalized collision identities keeps that legacy
    allowance exact and leaves all new allocations under the strict validator.
    """

    def collision_identity(finding: Dict[str, Any]) -> Tuple[str, Tuple[str, ...]]:
        return (
            str(finding["suffix"]),
            tuple(sorted(str(value) for value in finding["plan_refs"])),
        )

    findings: List[Dict[str, Any]] = []
    existing_legacy = set(_extract_legacy_plan_ref_tokens(existing_content))
    for plan_ref in _extract_legacy_plan_ref_tokens(candidate_content):
        if plan_ref not in existing_legacy:
            findings.append({
                "classification": "plan-ref-legacy-new-allocation",
                "blocking": True,
                "plan_ref": plan_ref,
                "detail": (
                    f"new allocation {plan_ref} uses the retired phase-first "
                    "grammar; allocate KIND-NN-NNN while preserving only "
                    "pre-existing legacy refs"
                ),
            })

    existing_kind_first = set(_extract_plan_ref_tokens(existing_content))
    for plan_ref in _extract_plan_ref_tokens(candidate_content):
        parsed = parse_plan_ref(plan_ref)
        assert parsed is not None
        if not parsed["kind_supported"] and plan_ref not in existing_kind_first:
            findings.append({
                "classification": "plan-ref-kind-unsupported",
                "blocking": True,
                "plan_ref": plan_ref,
                "detail": (
                    f"new allocation {plan_ref} uses unsupported kind "
                    f"{parsed['kind']}; supported kinds: "
                    f"{', '.join(SUPPORTED_KINDS)}"
                ),
            })

    grandfathered = {
        collision_identity(finding)
        for finding in validate_plan_allocations(existing_content)
    }
    findings.extend(
        finding
        for finding in validate_plan_allocations(candidate_content)
        if collision_identity(finding) not in grandfathered
    )
    return findings


def validate_plan_allocations_for_refs(
    content: str, governed_refs: "set[str]"
) -> List[Dict[str, Any]]:
    """Return only collisions that involve a newly governed plan ref."""
    return [
        finding
        for finding in validate_plan_allocations(content)
        if governed_refs.intersection(finding["plan_refs"])
    ]


def validate_phase_projection(
    plan_content: str, phase_id: str, phase_content: str
) -> List[Dict[str, Any]]:
    """Validate that a phase file projects only its plan's allocations."""
    phase = parse_phase_name(phase_id)
    phase_refs = set(extract_plan_refs(phase_content))
    # Ignore collisions wholly outside this phase projection while preserving
    # strict validation for every ref the candidate phase actually projects.
    findings = validate_plan_allocations_for_refs(plan_content, phase_refs)
    plan_refs = set(extract_plan_refs(plan_content))
    for plan_ref in extract_plan_refs(phase_content):
        parsed = parse_plan_ref(plan_ref)
        assert parsed is not None
        if parsed["phase"] != phase:
            findings.append({
                "classification": "phase-plan-ref-mismatch",
                "blocking": True,
                "plan_ref": plan_ref,
                "detail": f"{phase_id} cannot carry foreign-phase plan ref {plan_ref}",
            })
        elif plan_ref not in plan_refs:
            findings.append({
                "classification": "phase-plan-ref-unallocated",
                "blocking": True,
                "plan_ref": plan_ref,
                "detail": (
                    f"{phase_id} carries {plan_ref}, but IMPLEMENTATION_PLAN.md "
                    "has not allocated that ref"
                ),
            })
    return findings


def _canonical_spec_id(raw: Optional[str]) -> Optional[str]:
    value = (raw or "").strip()
    if not value or value.lower() in {"none", "n/a"}:
        return None
    name = Path(value).name
    if name.endswith(".md"):
        name = name[:-3]
    return name if _SPEC_ID_RE.fullmatch(name) else ""


def _find_task_paths(project_root: Path, task_id: str) -> List[Path]:
    paths = []
    for status in _STATUS_DIRS:
        candidate = Path(project_root) / "tasks" / status / f"{task_id}.md"
        if candidate.is_file():
            paths.append(candidate)
    return paths


def validate_task_trace(
    project_root: Path,
    task_id: str,
    content: str,
    *,
    require_plan: bool = True,
    require_phase: bool = True,
    require_spec_file: bool = True,
) -> List[Dict[str, Any]]:
    """Validate the complete plan→task→spec trace for one governed task."""
    findings: List[Dict[str, Any]] = []
    plan_ref = plan_ref_header_value(content) or ""
    verdict = classify_binding(task_id, plan_ref, phase_header_value(content) or "")
    if verdict["blocking"]:
        findings.append(verdict)
        return findings

    plan_path = Path(project_root) / "IMPLEMENTATION_PLAN.md"
    if require_plan:
        try:
            plan_text = plan_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            findings.append({
                "classification": "implementation-plan-unreadable",
                "blocking": True,
                "detail": f"IMPLEMENTATION_PLAN.md is missing or unreadable: {exc}",
            })
        else:
            findings.extend(
                validate_plan_allocations_for_refs(plan_text, {plan_ref})
            )
            if plan_ref not in set(extract_plan_refs(plan_text)):
                findings.append({
                    "classification": "plan-ref-unallocated",
                    "blocking": True,
                    "plan_ref": plan_ref,
                    "detail": (
                        f"{plan_ref} is not an allocated plan ref in "
                        "IMPLEMENTATION_PLAN.md; the plan must allocate the "
                        "suffix before task-scoped artifacts are authored"
                    ),
                })
    if require_phase:
        anchor = verify_phase_anchor(
            project_root, phase_header_value(content) or "", plan_ref
        )
        if anchor is not None:
            findings.append({
                "classification": anchor[0],
                "blocking": True,
                "detail": anchor[1],
            })

    task = parse_task_id(task_id)
    assert task is not None
    expected_spec = f"SPEC-{task['phase']}-{task['counter']}"
    declared_spec = _canonical_spec_id(spec_header_value(content))
    if declared_spec == "":
        findings.append({
            "classification": "spec-id-malformed",
            "blocking": True,
            "detail": (
                f"{task_id} Spec: value must be {expected_spec}.md, none, or n/a"
            ),
        })
    elif declared_spec is not None and declared_spec != expected_spec:
        findings.append({
            "classification": "task-spec-suffix-mismatch",
            "blocking": True,
            "task_id": task_id,
            "spec_id": declared_spec,
            "detail": (
                f"{task_id} may reference only {expected_spec}.md; "
                f"{declared_spec}.md breaks the plan-allocated suffix chain"
            ),
        })
    elif declared_spec is not None and require_spec_file:
        spec_path = Path(project_root) / "specs" / f"{declared_spec}.md"
        if not spec_path.is_file():
            findings.append({
                "classification": "spec-file-missing",
                "blocking": True,
                "detail": f"declared spec file not found: specs/{declared_spec}.md",
            })
        else:
            try:
                spec_text = spec_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                findings.append({
                    "classification": "spec-file-unreadable",
                    "blocking": True,
                    "detail": f"declared spec file unreadable: {exc}",
                })
            else:
                spec_ref = plan_refs_header_value(spec_text)
                if spec_ref != plan_ref:
                    findings.append({
                        "classification": "spec-plan-ref-mismatch",
                        "blocking": True,
                        "detail": (
                            f"{declared_spec}.md must declare Plan ref: {plan_ref}; "
                            f"observed {spec_ref or '<missing>'}"
                        ),
                    })
    return findings


def guard_spec_write(
    project_root: Path,
    spec_id: str,
    content: str,
    *,
    creating: bool,
    state: Optional[Dict[str, Any]] = None,
) -> Optional[Tuple[str, str]]:
    """Require a new spec to be owned by its one aligned governed task."""
    state = activation_state() if state is None else state
    if not state["active"]:
        return None
    match = _SPEC_ID_RE.fullmatch(spec_id)
    if match is None:
        return ("spec-id-malformed", f"invalid spec id: {spec_id}")
    task_id = f"TASK-{match.group(1)}-{match.group(2)}"
    paths = _find_task_paths(project_root, task_id)
    governed = governed_task_ids(project_root)
    if not creating and task_id not in governed:
        return None
    if len(paths) != 1:
        return (
            "spec-task-owner-missing" if not paths else "spec-task-owner-ambiguous",
            f"{spec_id} requires exactly one owning {task_id}; found {len(paths)}",
        )
    try:
        task_text = paths[0].read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return ("spec-task-owner-unreadable", f"cannot read {paths[0]}: {exc}")
    findings = validate_task_trace(
        project_root, task_id, task_text, require_spec_file=False
    )
    if findings:
        return (findings[0]["classification"], findings[0]["detail"])
    declared = _canonical_spec_id(spec_header_value(task_text))
    if declared != spec_id:
        return (
            "spec-task-binding-missing",
            f"{task_id} must declare Spec: {spec_id}.md before that spec is authored",
        )
    task_ref = plan_ref_header_value(task_text) or ""
    spec_ref = plan_refs_header_value(content)
    if spec_ref != task_ref:
        return (
            "spec-plan-ref-mismatch",
            f"{spec_id} must declare Plan ref: {task_ref}; observed "
            f"{spec_ref or '<missing>'}",
        )
    return None


def guard_existing_task_trace(
    project_root: Path,
    task_path: Path,
    *,
    state: Optional[Dict[str, Any]] = None,
) -> Optional[Tuple[str, str]]:
    """Re-verify a governed task before a downstream artifact or movement."""
    match = _TASK_FILENAME_RE.fullmatch(task_path.name)
    if match is None or match.group(1) not in governed_task_ids(project_root):
        return None
    state = activation_state() if state is None else state
    if not state["active"]:
        return None
    try:
        content = task_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return ("task-unreadable", f"cannot read {task_path}: {exc}")
    findings = validate_task_trace(project_root, match.group(1), content)
    if findings:
        return (findings[0]["classification"], findings[0]["detail"])
    return None


def guard_task_scoped_artifact(
    project_root: Path,
    task_path: Path,
    artifact_id: str,
    artifact_content: str = "",
    *,
    state: Optional[Dict[str, Any]] = None,
) -> Optional[Tuple[str, str]]:
    """Bind a downstream artifact id and any identity headers to its task."""
    task_match = _TASK_FILENAME_RE.fullmatch(task_path.name)
    if task_match is None or task_match.group(1) not in governed_task_ids(project_root):
        return None
    state = activation_state() if state is None else state
    if not state["active"]:
        return None
    trace_refusal = guard_existing_task_trace(
        project_root, task_path, state=state
    )
    if trace_refusal is not None:
        return trace_refusal
    task_id = task_match.group(1)
    task = parse_task_id(task_id)
    assert task is not None
    expected_suffix = f"{task['phase']}-{task['counter']}"
    stem = Path(artifact_id).stem
    artifact_match = _TASK_SCOPED_ID_RE.fullmatch(stem)
    report_match = _REPORT_ID_RE.fullmatch(stem)
    if artifact_match is not None:
        observed_suffix = f"{artifact_match.group(2)}-{artifact_match.group(3)}"
    elif report_match is not None:
        observed_suffix = f"{report_match.group(1)}-{report_match.group(2)}"
    else:
        return (
            "artifact-id-malformed",
            f"task-scoped artifact id is malformed: {artifact_id}",
        )
    if observed_suffix != expected_suffix:
        return (
            "artifact-task-suffix-mismatch",
            f"{artifact_id} carries suffix {observed_suffix}, but {task_id} "
            f"requires {expected_suffix}",
        )
    task_text = task_path.read_text(encoding="utf-8")
    bound_ref = plan_ref_header_value(task_text) or ""
    declared_ref = plan_ref_header_value(artifact_content)
    if declared_ref is not None and declared_ref.strip().lower() != "n/a" and declared_ref != bound_ref:
        return (
            "artifact-plan-ref-mismatch",
            f"{artifact_id} declares {declared_ref}, but {task_id} binds {bound_ref}",
        )
    # Resolved through the same seam the intake boundary uses, so the two
    # cannot disagree about which unit a `Target:` value names.
    declared_target = target_header_value(artifact_content)
    if declared_target is not None:
        target = canonical_target_id(declared_target) or declared_target
        allowed = {task_id, f"SPEC-{expected_suffix}"}
        if target not in allowed:
            return (
                "review-target-mismatch",
                f"{artifact_id} targets {target}, but its suffix belongs to {task_id}",
            )
    return None


def resolve_trace(project_root: Path, identifier: str) -> Dict[str, Any]:
    """Resolve forward or backward task trace identity deterministically."""
    raw = Path(identifier).name
    stem = raw[:-3] if raw.endswith(".md") else raw
    plan = parse_plan_ref(stem)
    if plan is not None and plan["kind_supported"]:
        suffix = f"{plan['phase']}-{plan['counter']}"
        plan_ref = stem
    else:
        match = _TASK_SCOPED_ID_RE.fullmatch(stem) or _REPORT_ID_RE.fullmatch(stem)
        if match is None:
            raise ValueError(f"identifier is not task-scoped: {identifier!r}")
        if len(match.groups()) == 3:
            suffix = f"{match.group(2)}-{match.group(3)}"
        else:
            suffix = f"{match.group(1)}-{match.group(2)}"
        plan_ref = None
    task_id = f"TASK-{suffix}"
    paths = _find_task_paths(project_root, task_id)
    if len(paths) != 1:
        raise ValueError(
            f"trace for {identifier} requires exactly one {task_id}; found {len(paths)}"
        )
    task_text = paths[0].read_text(encoding="utf-8")
    bound_ref = plan_ref_header_value(task_text) or ""
    verdict = classify_binding(task_id, bound_ref, phase_header_value(task_text) or "")
    if verdict["blocking"]:
        raise ValueError(verdict["detail"])
    if plan_ref is not None and bound_ref != plan_ref:
        raise ValueError(f"{plan_ref} resolves by suffix to {task_id}, but it binds {bound_ref}")
    declared_spec = _canonical_spec_id(spec_header_value(task_text))
    return {
        "suffix": suffix,
        "plan_ref": bound_ref,
        "task_id": task_id,
        "task_path": str(paths[0].resolve()),
        "spec_id": declared_spec,
        "prompt_id": f"PROMPT-{suffix}",
        "completion_report_id": f"REPORT-{suffix}",
        "review_report_id": f"REPORT-{suffix}-review",
        "review_id": f"REVIEW-{suffix}",
    }


def verify_phase_anchor(
    project_root: Path, phase_header: Optional[str], plan_ref: str
) -> Optional[Tuple[str, str]]:
    """Fail-closed phase-anchor check: ``(classification, detail)`` or ``None``.

    Applied by readiness validation (and task-bundle through it) to newly
    governed work after :func:`classify_binding` accepts the header
    comparison: the declared phase file must exist and carry the task's plan
    ref, per ``templates/TASK.md`` ("the matching phase file must carry the
    same plan ref"). Task authoring and every downstream readiness/lifecycle
    surface consume the same check, so the plan and phase allocation must
    exist before the task-scoped chain begins.
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

    Called by the mediated task writer immediately before a post-activation
    creation. Unlike ordinary drift provenance, this boundary record is
    required: the writer refuses before creating the task if it cannot persist
    the marker, so newly authored work is never silently exempt downstream.
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
    findings = validate_task_trace(
        Path(project_root),
        task_id,
        content,
        require_plan=True,
        require_phase=True,
        # Stage 4 authors the task first and then its declared spec. Readiness
        # requires the file; task authoring requires the identity to align.
        require_spec_file=False,
    )
    if findings:
        return (findings[0]["classification"], findings[0]["detail"])
    duplicate = duplicate_binding(
        task_id, plan_ref, collect_task_plan_refs(project_root)
    )
    if duplicate is not None:
        return ("plan-ref-reused", duplicate)
    return None
