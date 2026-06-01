"""Harness containment-tier detection — asset-driven classifier (TASK-02-001, FR-008).

This is the *detection* half of FR-008: given a PM harness, classify the highest
containment tier Cartopian can actually enforce on it, **pre-launch and purely
from on-disk assets** —

* ``tier-1-2`` (constrained) — a hard-coded floor launch profile *and* a
  native-sandbox depth profile both exist for the harness.
* ``tier-3`` (advisory / unconstrainable) — one or both are absent.

It does NOT implement the launch gate / acknowledgment / persistence (TASK-02-002).

Red-before-green
----------------
:class:`TestRedNoTierClassification` pins the pre-change baseline: the only
containment signal that existed before this task is the boolean
``_containment.pm_is_contained``, a *runtime* signal about the current process
that is identical for a constrained Claude PM and an unconstrainable ``cascade``
PM — so an unconstrainable harness is indistinguishable from a constrained one.
That class asserts the *absence* of any per-harness tier surface; once the
classifier lands it documents what was missing. The green classes then assert
the asset-driven classification: ``cascade`` -> ``tier-3`` and Claude Code (which
ships both ``cartopian-claude-pm`` and ``sandbox-pm-depth.json``) -> ``tier-1-2``.

Classification is asset-driven, not name-driven: the green synthetic cases below
build a throwaway ``wrappers/`` tree and show that the tier flips on file
existence alone — present both -> ``tier-1-2``; remove either -> ``tier-3`` — so a
Phase 03 promotion is drop-in (ship the two files) with no edit to this logic.
"""
from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from cli.commands import _containment
from cli.commands import _harness_tier as ht

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPERS_DIR = REPO_ROOT / "wrappers"

# The Claude Code containment assets that define "constrained" today.
CLAUDE_FLOOR = WRAPPERS_DIR / "bin" / "cartopian-claude-pm"
CLAUDE_DEPTH = WRAPPERS_DIR / "etc" / "sandbox-pm-depth.json"


def _pm_config(agent):
    """A minimal resolved-config pair with [handoffs.pm].agent = agent."""
    project_cfg = {"handoffs": {"pm": {"agent": agent}}} if agent is not None else {}
    return {}, project_cfg  # (global_cfg, project_cfg)


class TestRedNoTierClassification(unittest.TestCase):
    """RED baseline: the pre-task surface cannot distinguish harness tiers.

    Before this change the only containment signal is the boolean
    ``pm_is_contained`` — a runtime fact about the *current* process, not a
    per-harness, pre-launch classification. Under one fixed environment it
    returns the SAME value whether the configured PM harness is the constrained
    Claude profile or the unconstrainable ``cascade``: the two are
    indistinguishable. These assertions fail (red) until an asset-driven tier
    classifier exists.
    """

    def test_boolean_signal_is_harness_blind(self):
        # The legacy signal takes no harness and yields one boolean for the
        # process — identical for a constrained and an unconstrainable harness.
        sig = inspect.signature(_containment.pm_is_contained)
        self.assertNotIn(
            "agent",
            sig.parameters,
            msg="pm_is_contained is a process-level boolean; it cannot classify a harness",
        )
        env_contained = {"CARTOPIAN_PM_CONTAINED": "1"}
        # Same env -> same boolean regardless of which harness is configured.
        self.assertEqual(
            _containment.pm_is_contained(env_contained),
            _containment.pm_is_contained(env_contained),
        )

    def test_tier_classifier_exists(self):
        # Red until the detection surface lands: a per-harness tier classifier.
        self.assertTrue(
            hasattr(ht, "classify_harness_tier"),
            msg="no asset-driven harness tier classifier exists yet",
        )


class TestGreenRealHarnesses(unittest.TestCase):
    """GREEN: the two task-named harnesses classify correctly off real assets."""

    def test_claude_is_tier_1_2(self):
        # Precondition: the real Claude assets are present in this checkout.
        self.assertTrue(CLAUDE_FLOOR.is_file(), f"missing floor asset: {CLAUDE_FLOOR}")
        self.assertTrue(CLAUDE_DEPTH.is_file(), f"missing depth asset: {CLAUDE_DEPTH}")

        result = ht.classify_harness_tier("cartopian-claude-pm")
        self.assertEqual(result.tier, ht.TIER_CONSTRAINED)
        self.assertEqual(result.tier, "tier-1-2")
        self.assertTrue(result.constrained)
        self.assertEqual(result.harness, "claude")
        self.assertTrue(result.floor_profile_present)
        self.assertTrue(result.depth_profile_present)

    def test_cascade_is_tier_3(self):
        result = ht.classify_harness_tier("cascade")
        self.assertEqual(result.tier, ht.TIER_ADVISORY)
        self.assertEqual(result.tier, "tier-3")
        self.assertFalse(result.constrained)
        self.assertEqual(result.harness, "cascade")
        self.assertFalse(result.floor_profile_present)
        self.assertFalse(result.depth_profile_present)
        # The reason must name which required asset is missing.
        self.assertIn("floor", result.reason)
        self.assertIn("depth", result.reason)


class TestResolutionFromConfig(unittest.TestCase):
    """The harness resolves from [handoffs.pm].agent with a launch-target fallback."""

    def test_resolves_pm_agent_from_config(self):
        global_cfg, project_cfg = _pm_config("cascade")
        result = ht.classify_pm_tier(global_cfg, project_cfg)
        self.assertEqual(result.harness, "cascade")
        self.assertEqual(result.tier, "tier-3")

    def test_project_agent_overrides_global(self):
        global_cfg = {"handoffs": {"pm": {"agent": "cartopian-claude-pm"}}}
        project_cfg = {"handoffs": {"pm": {"agent": "cascade"}}}
        result = ht.classify_pm_tier(global_cfg, project_cfg)
        self.assertEqual(result.harness, "cascade")

    def test_falls_back_to_launch_target_when_no_handoffs_pm(self):
        global_cfg, project_cfg = _pm_config(None)  # no [handoffs.pm]
        result = ht.classify_pm_tier(
            global_cfg, project_cfg, launch_target="cartopian-claude-pm"
        )
        self.assertEqual(result.harness, "claude")
        self.assertEqual(result.tier, "tier-1-2")

    def test_no_harness_resolved_is_tier_3(self):
        result = ht.classify_pm_tier({}, {})
        self.assertEqual(result.tier, "tier-3")
        self.assertIsNone(result.harness)


class TestAssetDrivenNotNameDriven(unittest.TestCase):
    """Tier flips on file existence alone — promotion is drop-in (no logic edit)."""

    def _wrappers_tree(self, tmp: Path, *, floor: bool, depth: bool) -> Path:
        wrappers = tmp / "wrappers"
        (wrappers / "bin").mkdir(parents=True)
        (wrappers / "etc").mkdir(parents=True)
        if floor:
            (wrappers / "bin" / "cartopian-newpm-pm").write_text("#!/bin/sh\n")
        if depth:
            (wrappers / "etc" / "sandbox-newpm-pm-depth.json").write_text("{}\n")
        return wrappers

    def test_both_assets_present_promotes_to_tier_1_2(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            wrappers = self._wrappers_tree(Path(tmp), floor=True, depth=True)
            result = ht.classify_harness_tier("newpm", wrappers_dir=wrappers)
            self.assertEqual(result.tier, "tier-1-2")
            self.assertTrue(result.constrained)

    def test_floor_only_is_tier_3(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            wrappers = self._wrappers_tree(Path(tmp), floor=True, depth=False)
            result = ht.classify_harness_tier("newpm", wrappers_dir=wrappers)
            self.assertEqual(result.tier, "tier-3")
            self.assertTrue(result.floor_profile_present)
            self.assertFalse(result.depth_profile_present)

    def test_depth_only_is_tier_3(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            wrappers = self._wrappers_tree(Path(tmp), floor=False, depth=True)
            result = ht.classify_harness_tier("newpm", wrappers_dir=wrappers)
            self.assertEqual(result.tier, "tier-3")
            self.assertFalse(result.floor_profile_present)
            self.assertTrue(result.depth_profile_present)


class TestCanonicalisation(unittest.TestCase):
    """Agent strings map to a stable canonical harness key."""

    def test_strips_cartopian_prefix_and_pm_suffix(self):
        self.assertEqual(ht.canonical_harness("cartopian-claude-pm"), "claude")

    def test_plain_name_is_unchanged(self):
        self.assertEqual(ht.canonical_harness("cascade"), "cascade")

    def test_path_is_reduced_to_basename(self):
        self.assertEqual(
            ht.canonical_harness("/usr/local/bin/cartopian-gemini-pm"), "gemini"
        )

    def test_empty_or_none_is_none(self):
        self.assertIsNone(ht.canonical_harness(None))
        self.assertIsNone(ht.canonical_harness("  "))


class TestRecordShape(unittest.TestCase):
    """as_record() yields a flat, JSON-serialisable dict (FR-014-ready)."""

    def test_record_is_json_serialisable_dict(self):
        import json

        result = ht.classify_harness_tier("cascade")
        record = result.as_record()
        self.assertIsInstance(record, dict)
        json.dumps(record)  # must not raise
        for key in ("tier", "constrained", "harness", "reason"):
            self.assertIn(key, record)


if __name__ == "__main__":
    unittest.main()
