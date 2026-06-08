"""FR-008 advisory-tier lifecycle advisory (TASK-02-002).

The FR-013 sibling guard (:mod:`cli.commands._containment`) fails closed on one
specific unsupported combination (a contained PM that owns product-repo git).
This module is the broader FR-008 advisory surface: it detects when Cartopian
cannot prove that the configured PM harness can be constrained to Tier 1/2 and
emits explicit guidance without blocking lifecycle orientation.

The advisory decides solely from TASK-02-001 detection
(:mod:`cli.commands._harness_tier`) plus the persisted acknowledgment ledger
(:mod:`cli.commands._compatibility`). It does not re-implement detection and
never prompts. A recorded decision is optional audit trail; lack of one must not
force a non-technical operator into a terminal-only unblock path.

Three outcomes at lifecycle entry (``resolve-config`` / ``next-action``):

* **constrained** — harness classifies ``tier-1-2`` → proceed, no advisory
  (NF-004: already-constrained harnesses are unaffected).
* **unconfigured** — no PM harness resolves from config (no ``[handoffs.pm].agent``
  and no launch target) → proceed. There is nothing to gate at this surface: the
  operator has selected no harness at this surface, so today's behavior is
  preserved (NF-004). A harness launched outside config is the launch-wrapper's
  responsibility, not this one.
* **tier-3 (a harness *is* resolved and classifies ``tier-3``)** —
  * with a **valid** acknowledgment record → ``acknowledged``: proceed, emit a
    persistent advisory banner, do not re-prompt.
  * with **no / revoked / mismatched** record → ``advisory``: proceed, emit a
    visible advisory naming the harness and missing assets. Lifecycle orientation
    remains usable, and the PM field may carry any operator-chosen harness name.

Import-cycle-free: depends only on ``_harness_tier`` (which imports
``_containment``) and ``_compatibility`` (stdlib). ``resolve_config`` /
``next_action`` import this module; this module imports neither of them.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cli.commands import _compatibility
from cli.commands._harness_tier import TIER_ADVISORY, classify_pm_tier_from_paths

# Outcome status labels.
STATUS_CONSTRAINED = "constrained"
STATUS_UNCONFIGURED = "unconfigured"
STATUS_ACKNOWLEDGED = "acknowledged"
STATUS_ADVISORY = "advisory"
# Backward-compatible label retained for older imports; lifecycle entry no
# longer returns this status for an unconstrained PM harness.
STATUS_BLOCKED = "blocked"


@dataclass(frozen=True)
class AdvisoryGateOutcome:
    """The gate's decision for one lifecycle-entry call."""

    status: str
    blocked: bool
    tier: str
    harness: Optional[str]
    project_id: Optional[str]
    # Kept for caller compatibility; unconstrained harnesses no longer set it.
    detail: Optional[str] = None
    # When advisory/acknowledged: the per-session banner text (no prefix).
    advisory: Optional[str] = None


def _missing_assets_phrase(reason: str) -> str:
    """Extract the human-readable missing-assets clause from a detection reason."""
    marker = "missing "
    idx = reason.find(marker)
    if idx >= 0:
        return reason[idx + len(marker):].strip()
    return reason.strip()


def advisory_banner(harness: str, project_id: Optional[str], record=None, *, reason: str = "") -> str:
    """The per-session advisory emitted for a Tier-3 PM harness."""
    proj = project_id or (record.project_id if record is not None else "<this project>")
    missing = _missing_assets_phrase(reason)
    if record is not None:
        on = record.acknowledged_on or "<unrecorded date>"
        by = record.acknowledged_by or "<unrecorded operator>"
        return (
            f"advisory-tier: PM harness '{harness}' is running at Tier-3 for "
            f"project '{proj}' by recorded operator note "
            f"(acknowledged_by={by}, acknowledged_on={on}). Cartopian cannot "
            f"prove Tier 1/2 containment for this harness; containment is "
            f"advisory only."
        )
    return (
        f"advisory-tier: PM harness '{harness}' is Tier-3 for project '{proj}'"
        f"{f' — {missing}' if missing else ''}. Continuing lifecycle entry; "
        f"Cartopian cannot prove Tier 1/2 containment for this harness. Use a "
        f"constrained Cartopian wrapper when enforceable PM containment is "
        f"required."
    )


def evaluate_advisory_gate(
    project_path: Path,
    project_id: Optional[str],
    *,
    home: Optional[Path] = None,
    launch_target: Optional[str] = None,
    wrappers_dir: Optional[Path] = None,
    ledger_text: Optional[str] = None,
) -> AdvisoryGateOutcome:
    """Decide the FR-008 advisory outcome for a lifecycle-entry call.

    Pure function of TASK-02-001 detection + the persisted ledger. ``ledger_text``
    may be supplied for testing; otherwise the project-root ``COMPATIBILITY.md``
    is read.
    """
    tier_result = classify_pm_tier_from_paths(
        project_path,
        home=home,
        launch_target=launch_target,
        wrappers_dir=wrappers_dir,
    )
    harness = tier_result.harness

    # No PM harness resolved → nothing unconstrainable is selected here. Preserve
    # today's behavior (NF-004); the default constrained profile applies.
    if harness is None:
        return AdvisoryGateOutcome(
            status=STATUS_UNCONFIGURED,
            blocked=False,
            tier=tier_result.tier,
            harness=None,
            project_id=project_id,
        )

    # A resolved, constrainable harness (tier-1-2) → proceed unaffected (NF-004).
    if tier_result.tier != TIER_ADVISORY:
        return AdvisoryGateOutcome(
            status=STATUS_CONSTRAINED,
            blocked=False,
            tier=tier_result.tier,
            harness=harness,
            project_id=project_id,
        )

    # Tier-3 with a resolved harness: proceed with an advisory. A valid record
    # annotates the advisory, but no record is required for lifecycle entry.
    if ledger_text is None:
        records = _compatibility.load_records(project_path)
    else:
        records = _compatibility.parse_ledger(ledger_text)

    record = (
        _compatibility.find_valid_record(records, harness, project_id)
        if project_id
        else None
    )
    if record is not None:
        return AdvisoryGateOutcome(
            status=STATUS_ACKNOWLEDGED,
            blocked=False,
            tier=tier_result.tier,
            harness=harness,
            project_id=project_id,
            advisory=advisory_banner(harness, project_id, record, reason=tier_result.reason),
        )

    return AdvisoryGateOutcome(
        status=STATUS_ADVISORY,
        blocked=False,
        tier=tier_result.tier,
        harness=harness,
        project_id=project_id,
        advisory=advisory_banner(harness, project_id, reason=tier_result.reason),
    )
