"""FR-008 advisory-tier launch / lifecycle-entry gate (TASK-02-002).

The FR-013 sibling guard (:mod:`cli.commands._containment`) fails closed on one
specific unsupported combination (a contained PM that owns product-repo git).
This module is the broader FR-008 gate: it fails closed whenever the PM harness
**cannot be constrained to Tier 1/2** and the operator has not recorded an
explicit acknowledgment of the unconstrained risk.

The gate decides solely from TASK-02-001 detection (:mod:`cli.commands._harness_tier`)
plus the persisted acknowledgment ledger (:mod:`cli.commands._compatibility`).
It does not re-implement detection and never prompts: the recorded decision is
the audit trail; a visible per-session advisory banner replaces re-prompting.

Three outcomes at lifecycle entry (``resolve-config`` / ``next-action``), all
fail-closed:

* **constrained** — harness classifies ``tier-1-2`` → proceed, no advisory
  (NF-004: already-constrained harnesses are unaffected).
* **unconfigured** — no PM harness resolves from config (no ``[handoffs.pm].agent``
  and no launch target) → proceed. There is nothing to gate at this surface: the
  operator has selected no unconstrainable harness, so the default constrained
  launch profile applies and today's behavior is preserved (NF-004). A harness
  launched outside config is the launch-wrapper's gate (Phase 03), not this one.
* **tier-3 (a harness *is* resolved and classifies ``tier-3``)** —
  * with a **valid** acknowledgment record → ``acknowledged``: proceed, emit a
    persistent advisory banner, do not re-prompt.
  * with **no / revoked / mismatched** record → ``blocked``: refuse with a
    structured ``[guard]`` detail naming the harness, the missing assets, and how
    to acknowledge or switch to a constrained harness; non-zero exit; no
    lifecycle action proceeds.

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
STATUS_BLOCKED = "blocked"


@dataclass(frozen=True)
class AdvisoryGateOutcome:
    """The gate's decision for one lifecycle-entry call."""

    status: str
    blocked: bool
    tier: str
    harness: Optional[str]
    project_id: Optional[str]
    # When blocked: the ``[guard]`` detail (no prefix). Else None.
    detail: Optional[str] = None
    # When acknowledged: the per-session advisory banner text (no prefix). Else None.
    advisory: Optional[str] = None


def _missing_assets_phrase(reason: str) -> str:
    """Extract the human-readable missing-assets clause from a detection reason."""
    marker = "missing "
    idx = reason.find(marker)
    if idx >= 0:
        return reason[idx + len(marker):].strip()
    return reason.strip()


def block_detail(harness: str, project_id: Optional[str], reason: str) -> str:
    """The structured ``[guard]`` detail for an unacknowledged Tier-3 launch."""
    missing = _missing_assets_phrase(reason)
    proj = project_id or "<this project>"
    return (
        f"advisory-tier-unacknowledged: PM harness '{harness}' cannot be "
        f"constrained to Tier 1/2 — {missing}. Refusing PM launch / lifecycle "
        f"entry fail-closed (FR-008): no lifecycle action proceeds without a "
        f"recorded operator acknowledgment of the unconstrained risk. Resolve by "
        f"recording an acknowledgment for ({harness}, {proj}) — run "
        f"`python3 -m cli.commands.acknowledge_harness <project-root> "
        f"--harness {harness} --acknowledged-by <operator> --rationale <why>` "
        f"(operator-only; not a PM tool) — or switch the PM to a constrained "
        f"harness (a Tier 1/2 harness such as Claude Code, which ships both a "
        f"floor launch profile and a native-sandbox depth profile)."
    )


def advisory_banner(harness: str, project_id: Optional[str], record) -> str:
    """The persistent per-session advisory emitted on an acknowledged launch."""
    proj = project_id or record.project_id
    on = record.acknowledged_on or "<unrecorded date>"
    by = record.acknowledged_by or "<unrecorded operator>"
    return (
        f"advisory-tier: PM harness '{harness}' is running UNCONSTRAINED at "
        f"Tier-3 by recorded operator decision for project '{proj}' "
        f"(acknowledged_by={by}, acknowledged_on={on}). Cartopian cannot enforce "
        f"the capability floor on this harness; containment is advisory only. "
        f"This acknowledgment is recorded in {_compatibility.LEDGER_FILENAME} and "
        f"can be revoked with the operator-only acknowledgment command "
        f"(--revoke), which re-blocks launch fail-closed."
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

    # Tier-3 with a resolved harness: an acknowledgment record is required.
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
            advisory=advisory_banner(harness, project_id, record),
        )

    return AdvisoryGateOutcome(
        status=STATUS_BLOCKED,
        blocked=True,
        tier=tier_result.tier,
        harness=harness,
        project_id=project_id,
        detail=block_detail(harness, project_id, tier_result.reason),
    )
