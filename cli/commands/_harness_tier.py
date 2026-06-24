"""Asset-driven harness containment-tier detection.

The boolean :func:`cli.commands._containment.pm_is_contained` answers a *runtime*
question — "is *this* process running under the containment floor?" — and gives
the same answer regardless of which PM harness is configured. This module answers
the *pre-launch, per-harness* question the launch gate needs: "what is the
highest containment tier Cartopian can actually **enforce** on this harness?"

The classification is a pure function of the resolved config (which harness is the
PM) plus the on-disk containment assets, with two outcomes:

* ``tier-1-2`` (constrained) — the harness ships **both** a hard-coded floor
  launch profile (the capability-floor wrapper) **and** a native-sandbox depth
  profile (the Tier-2 settings file). Both removed capabilities are in place, so
  Cartopian can hold the PM at Tier 1/2.
* ``tier-3`` (advisory / unconstrainable) — one or both assets are absent (or no
  harness resolves at all), so Cartopian cannot enforce containment and the PM
  runs under advisory rules only.

Asset-driven, not name-driven
-----------------------------
A harness reaches ``tier-1-2`` only because both of its asset files exist on
disk — never because its *name* is recognised. The harness name only derives
*where* the two assets would live, by the promotion convention:

* floor launch profile : ``wrappers/bin/cartopian-<harness>-pm``
* native-sandbox depth : ``wrappers/etc/sandbox-<harness>-pm-depth.json``

so a Phase 03 promotion is drop-in — ship those two files and the harness
classifies as ``tier-1-2`` with no edit to this module. Claude Code predates the
convention: its depth profile is the shared ``wrappers/etc/sandbox-pm-depth.json``
the floor wrapper references as ``SANDBOX_PROFILE``; that single deviation is
recorded in :data:`_DEPTH_PROFILE_OVERRIDES` so detection matches the real asset.

This module is import-cycle-free (stdlib + sibling ``_containment`` only, which
itself imports nothing from the command modules) so ``resolve_config`` /
``next_action`` / the launch gate can all consume it. Stdlib only. It performs
**detection only** — no launch gate, acknowledgment, or persistence.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from cli.commands._containment import _safe_load_toml

# Tier labels (stable strings — consumed by the launch gate and per-harness assets).
TIER_CONSTRAINED = "tier-1-2"
TIER_ADVISORY = "tier-3"

# Harnesses whose depth-profile asset deviates from the naming convention. Claude
# Code's floor wrapper references the shared ``sandbox-pm-depth.json`` rather than
# a ``sandbox-claude-pm-depth.json``; recorded here so the existence check targets
# the real on-disk asset. (Floor profiles all follow the convention, so no floor
# override table is needed.)
_DEPTH_PROFILE_OVERRIDES: Dict[str, str] = {
    "claude": "etc/sandbox-pm-depth.json",
}


def default_wrappers_dir() -> Path:
    """Absolute path to the tool-repo ``wrappers/`` directory shipping the assets."""
    # cli/commands/_harness_tier.py -> parents[2] is the repo root.
    return Path(__file__).resolve().parents[2] / "wrappers"


def canonical_harness(agent: Optional[str]) -> Optional[str]:
    """Reduce a configured agent/launch-target string to a canonical harness key.

    Drops any directory part, lower-cases, and strips the ``cartopian-`` wrapper
    prefix and the ``-pm`` PM-floor suffix so the floor wrapper name
    (``cartopian-claude-pm``) and a bare harness name (``claude``) map to the same
    key. Returns ``None`` for an empty/blank/missing value.
    """
    if not agent:
        return None
    name = Path(str(agent).strip()).name.lower()
    if not name:
        return None
    if name.startswith("cartopian-"):
        name = name[len("cartopian-"):]
    if name.endswith("-pm"):
        name = name[: -len("-pm")]
    return name or None


def _asset_paths(harness: str, wrappers_dir: Path) -> "tuple[Path, Path]":
    """Return (floor_profile_path, depth_profile_path) for a canonical harness."""
    floor = wrappers_dir / "bin" / f"cartopian-{harness}-pm"
    depth_rel = _DEPTH_PROFILE_OVERRIDES.get(harness, f"etc/sandbox-{harness}-pm-depth.json")
    depth = wrappers_dir / depth_rel
    return floor, depth


@dataclass(frozen=True)
class HarnessTier:
    """The deterministic tier classification for one PM harness."""

    tier: str
    constrained: bool
    harness: Optional[str]
    agent: Optional[str]
    floor_profile_present: bool
    depth_profile_present: bool
    floor_profile_path: Optional[str]
    depth_profile_path: Optional[str]
    reason: str

    def as_record(self) -> Dict[str, Any]:
        """Flat, JSON-serialisable record for any CLI surface."""
        return {
            "tier": self.tier,
            "constrained": self.constrained,
            "harness": self.harness,
            "agent": self.agent,
            "floor_profile_present": self.floor_profile_present,
            "depth_profile_present": self.depth_profile_present,
            "floor_profile_path": self.floor_profile_path,
            "depth_profile_path": self.depth_profile_path,
            "reason": self.reason,
        }


def classify_harness_tier(
    agent: Optional[str], *, wrappers_dir: Optional[Path] = None
) -> HarnessTier:
    """Classify the containment tier enforceable on the harness named by ``agent``.

    Pure function of the resolved harness name plus on-disk assets: ``tier-1-2``
    iff both the floor launch profile and the native-sandbox depth profile exist,
    else ``tier-3``. Never raises on a missing/unknown harness — an unresolvable
    or unpromoted harness is simply ``tier-3``.
    """
    wrappers_dir = wrappers_dir if wrappers_dir is not None else default_wrappers_dir()
    harness = canonical_harness(agent)

    if harness is None:
        return HarnessTier(
            tier=TIER_ADVISORY,
            constrained=False,
            harness=None,
            agent=agent,
            floor_profile_present=False,
            depth_profile_present=False,
            floor_profile_path=None,
            depth_profile_path=None,
            reason=(
                "advisory: no PM harness resolved from [handoffs.pm].agent or a "
                "launch target — cannot enforce containment"
            ),
        )

    floor_path, depth_path = _asset_paths(harness, wrappers_dir)
    floor_present = floor_path.is_file()
    depth_present = depth_path.is_file()
    constrained = floor_present and depth_present

    if constrained:
        reason = (
            f"constrained: harness '{harness}' ships both a floor launch profile "
            f"and a native-sandbox depth profile"
        )
    else:
        missing = []
        if not floor_present:
            missing.append(f"floor launch profile ({floor_path})")
        if not depth_present:
            missing.append(f"native-sandbox depth profile ({depth_path})")
        reason = (
            f"advisory: harness '{harness}' cannot reach Tier 1/2 — missing "
            + " and ".join(missing)
        )

    return HarnessTier(
        tier=TIER_CONSTRAINED if constrained else TIER_ADVISORY,
        constrained=constrained,
        harness=harness,
        agent=agent,
        floor_profile_present=floor_present,
        depth_profile_present=depth_present,
        floor_profile_path=str(floor_path),
        depth_profile_path=str(depth_path),
        reason=reason,
    )


def resolve_pm_agent(
    global_cfg: Optional[Dict[str, Any]], project_cfg: Optional[Dict[str, Any]]
) -> Optional[str]:
    """Resolve the effective ``[handoffs.pm].agent`` (project wins over global).

    Mirrors the per-key merge in ``resolve_config._resolve_handoffs`` for the
    single ``pm.agent`` key, inlined here to stay import-cycle-free (so
    ``resolve_config`` can consume this module without a cycle).
    """
    for cfg in (project_cfg, global_cfg):  # project precedence
        handoffs = (cfg or {}).get("handoffs", {}) or {}
        pm = handoffs.get("pm", {}) or {}
        if isinstance(pm, dict) and pm.get("agent"):
            return str(pm["agent"])
    return None


def resolve_pm_harness(
    global_cfg: Optional[Dict[str, Any]],
    project_cfg: Optional[Dict[str, Any]],
    *,
    launch_target: Optional[str] = None,
) -> Optional[str]:
    """Resolve the effective PM harness: ``[handoffs.pm].agent``, else launch target."""
    agent = resolve_pm_agent(global_cfg, project_cfg)
    if agent:
        return agent
    return launch_target


def classify_pm_tier(
    global_cfg: Optional[Dict[str, Any]],
    project_cfg: Optional[Dict[str, Any]],
    *,
    launch_target: Optional[str] = None,
    wrappers_dir: Optional[Path] = None,
) -> HarnessTier:
    """Resolve the PM harness from parsed configs, then classify its tier."""
    agent = resolve_pm_harness(global_cfg, project_cfg, launch_target=launch_target)
    return classify_harness_tier(agent, wrappers_dir=wrappers_dir)


def classify_pm_tier_from_paths(
    project_path: Path,
    *,
    home: Optional[Path] = None,
    launch_target: Optional[str] = None,
    wrappers_dir: Optional[Path] = None,
) -> HarnessTier:
    """Classify the PM-harness tier from on-disk configs.

    Mirrors ``_containment.resolve_pm_owns_from_paths``: loads the project and
    global ``cartopian.toml`` fail-soft (unreadable/malformed -> ``{}``; those
    surfaces are validated and reported by ``resolve_config`` / ``next_action``)
    and returns the tier classification.
    """
    home = home or Path.home()
    project_cfg = _safe_load_toml(project_path / "cartopian.toml")
    global_cfg = _safe_load_toml(home / ".cartopian" / "cartopian.toml")
    return classify_pm_tier(
        global_cfg,
        project_cfg,
        launch_target=launch_target,
        wrappers_dir=wrappers_dir,
    )
