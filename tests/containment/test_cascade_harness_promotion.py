"""cascade harness promotion determination — not-recommended-as-PM-host (TASK-03-003).

The cascade slice of FR-010 all-harness coverage and the always-on, stdlib-only
(NF-001) anti-drift guard for the cascade determination. It does NOT edit the
asset-driven classifier (TASK-02-001 contract): cascade stays ``tier-3`` purely
because NO floor + depth assets exist for it on disk.

Resolved classification: not-recommended-as-PM-host
---------------------------------------------------
Unlike codex and gemini — which ship a genuine Tier-1 floor launch profile and a
Tier-2 native-sandbox depth profile and therefore *detect* ``tier-1-2`` — cascade
proves UNPROMOTABLE. Cascade is the agent embedded in the Windsurf Electron IDE;
it has (F-C1) no first-party, scriptable runtime to wrap with a hard-coded floor
profile, (F-C2) no mechanism to remove its built-in edit/write/shell tools and
scope to the Cartopian MCP set, and (F-C3) no native OS sandbox to drive for
Tier-2 — its only command control is an application-layer allow/deny-list string
matcher, and it runs with full user privileges. Shipping sham assets would make
``_harness_tier`` falsely claim ``tier-1-2`` AND break the no-regression contract
(the existing suite pins ``cascade -> tier-3`` as the archetypal unconstrainable
harness, NF-004), so NO assets are shipped: cascade is recorded
``not-recommended-as-PM-host`` at ``tier-3`` with the captured forcing evidence.

Layout
------
:class:`TestRedBaseline` pins the pre/at-change state (cascade is ``tier-3`` with
its assets absent — the red the evidence gate names). :class:`TestStaysTier3`
pins the forcing finding: cascade STAYS ``tier-3`` and no sham assets were
shipped. :class:`TestNoRegression` pins NF-004. :class:`TestForcingEvidence`
pins the captured determination artifact + the FINDINGS writeup. :class:`Test
CompatibilityMatrix` pins the not-recommended matrix entry.

The forcing-evidence capture is ``tests/wrappers/pm-cascade/determine-cascade-tier.sh``.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from cli.commands import _harness_tier as ht

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPERS = REPO_ROOT / "wrappers"
FLOOR = WRAPPERS / "bin" / "cartopian-cascade-pm"
DEPTH = WRAPPERS / "etc" / "sandbox-cascade-pm-depth.json"
PM_CASCADE = REPO_ROOT / "tests" / "wrappers" / "pm-cascade"
DETERMINE = PM_CASCADE / "determine-cascade-tier.sh"
FINDINGS = PM_CASCADE / "FINDINGS.md"
ARTIFACT = PM_CASCADE / "evidence" / "cascade-tier-determination.txt"
COMPAT = REPO_ROOT / "docs" / "COMPATIBILITY.md"


# --------------------------------------------------------------------------- #
# Red-before-green: cascade is tier-3 with its (non-existent) assets absent.
# This is the state the evidence gate's "red" names — a contained cascade PM
# negative test has NO floor/depth profile to exercise.
# --------------------------------------------------------------------------- #
class TestRedBaseline:
    def test_cascade_is_tier_3_without_assets(self, tmp_path):
        wrappers = tmp_path / "wrappers"
        (wrappers / "bin").mkdir(parents=True)
        (wrappers / "etc").mkdir(parents=True)
        result = ht.classify_harness_tier("cascade", wrappers_dir=wrappers)
        assert result.tier == ht.TIER_ADVISORY == "tier-3"
        assert result.constrained is False
        assert result.floor_profile_present is False
        assert result.depth_profile_present is False
        assert "floor" in result.reason and "depth" in result.reason

    def test_no_contained_cascade_runtime_exists_to_probe(self):
        # The forcing fact behind the "red": there is no floor wrapper to launch a
        # contained cascade PM, so the in-runtime negative probes (write/shell/etc.)
        # have no profile to exercise — captured instead as forcing evidence.
        assert not FLOOR.exists(), (
            "a cascade floor wrapper must NOT be shipped — cascade has no genuine "
            "Tier-1 mechanism (F-C2); shipping one would be a sham asset"
        )


# --------------------------------------------------------------------------- #
# The forcing finding: cascade STAYS tier-3 (no sham assets) — no classifier edit.
# --------------------------------------------------------------------------- #
class TestStaysTier3:
    def test_no_sham_floor_or_depth_assets_shipped(self):
        assert not FLOOR.exists(), f"sham cascade floor asset present: {FLOOR}"
        assert not DEPTH.exists(), f"sham cascade depth asset present: {DEPTH}"

    def test_cascade_detection_stays_tier_3_by_asset_absence(self):
        result = ht.classify_harness_tier("cascade")
        assert result.tier == ht.TIER_ADVISORY == "tier-3"
        assert result.constrained is False
        assert result.harness == "cascade"
        assert result.floor_profile_present is False
        assert result.depth_profile_present is False

    def test_detection_resolves_from_config_and_paths(self):
        for agent in ("cascade", "cartopian-cascade-pm", "/opt/bin/cartopian-cascade-pm"):
            assert ht.classify_harness_tier(agent).tier == "tier-3"


# --------------------------------------------------------------------------- #
# NF-004 — no regression to the already-classified harnesses.
# --------------------------------------------------------------------------- #
class TestNoRegression:
    def test_other_harness_classifications_unchanged(self):
        assert ht.classify_harness_tier("cartopian-claude-pm").tier == "tier-1-2"
        assert ht.classify_harness_tier("codex").tier == "tier-1-2"
        assert ht.classify_harness_tier("gemini").tier == "tier-1-2"
        assert ht.classify_harness_tier("cascade").tier == "tier-3"
        assert ht.classify_harness_tier("devin").tier == "tier-3"


# --------------------------------------------------------------------------- #
# Captured forcing evidence (FR-011, unpromotable branch) — pinned when present,
# skipped (with a reproduction pointer) when absent. A present artifact can never
# pass on a stale/wrong marker (fail-closed).
# --------------------------------------------------------------------------- #
class TestForcingEvidence:
    def test_determination_harness_exists_and_executable(self):
        assert DETERMINE.is_file(), f"determination harness missing: {DETERMINE}"
        assert os.access(DETERMINE, os.X_OK), "determination harness must be executable"

    def test_findings_record_the_three_forcing_facets(self):
        assert FINDINGS.is_file(), f"FINDINGS writeup missing: {FINDINGS}"
        text = FINDINGS.read_text(encoding="utf-8")
        for facet in ("F-C1", "F-C2", "F-C3"):
            assert facet in text, f"FINDINGS must record forcing facet {facet}"
        low = text.lower()
        assert "not-recommended-as-pm-host" in low
        # the specific mechanisms that force the finding
        assert "electron" in low                          # no first-party runtime
        assert "allow" in low and "deny" in low           # string-match command control
        assert "no os-level sandbox" in low or "not an os sandbox" in low \
            or "not a native os sandbox" in low or "no native sandbox" in low
        assert "nf-001" in low                            # third-party / bundled-sandbox bar
        assert "devin for terminal" in low                # cascade != devin (TASK-03-004)

    def test_captured_artifact_pins_not_promotable_when_present(self):
        if not ARTIFACT.is_file():
            pytest.skip(
                f"forcing-evidence artifact absent ({ARTIFACT.name}); capture via "
                f"{DETERMINE.relative_to(REPO_ROOT)}"
            )
        text = ARTIFACT.read_text(encoding="utf-8")
        assert "NOT_PROMOTABLE" in text
        assert "not-recommended-as-PM-host" in text
        assert "tier-3" in text
        for facet in ("F-C1", "F-C2", "F-C3"):
            assert facet in text, f"artifact missing forcing facet {facet}"

    def test_determination_harness_reproduces_green(self):
        # The capture is deterministic and environment-independent, so the suite
        # can re-run it: it must exit 0 (forcing evidence captured) and refuse to
        # emit a promotable verdict.
        proc = subprocess.run(
            [str(DETERMINE)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, (
            f"determination harness did not capture cleanly:\n{proc.stdout}\n{proc.stderr}"
        )
        assert "NOT_PROMOTABLE" in (proc.stdout + proc.stderr)
        assert "tier-3" in (proc.stdout + proc.stderr)

    def test_determination_harness_fails_closed_on_a_sham_asset(self, tmp_path):
        # If a sham floor asset ever appears, the harness must NOT emit a
        # promotable pass — it exits non-zero (a real mechanism may have landed;
        # re-evaluate). Exercised against a COPY in a throwaway repo tree so the
        # real shipped state is never touched.
        fake_repo = tmp_path / "cartopian"
        (fake_repo / "wrappers" / "bin").mkdir(parents=True)
        (fake_repo / "wrappers" / "etc").mkdir(parents=True)
        (fake_repo / "cli" / "commands").mkdir(parents=True)
        # Minimal stub so the harness's `python3 -c "_harness_tier…"` reports a
        # NON-tier-3 result (simulating an appeared mechanism) without importing
        # the real package: shadow it via PYTHONPATH.
        (fake_repo / "cli" / "__init__.py").write_text("")
        (fake_repo / "cli" / "commands" / "__init__.py").write_text("")
        (fake_repo / "cli" / "commands" / "_harness_tier.py").write_text(
            "def classify_harness_tier(agent):\n"
            "    class R:\n"
            "        tier='tier-1-2'; constrained=True\n"
            "        floor_profile_present=True; depth_profile_present=True\n"
            "    return R()\n"
        )
        harness_dir = fake_repo / "tests" / "wrappers" / "pm-cascade"
        harness_dir.mkdir(parents=True)
        copy = harness_dir / "determine-cascade-tier.sh"
        copy.write_text(DETERMINE.read_text(encoding="utf-8"))
        copy.chmod(0o755)
        # Ship a sham floor asset so the [[ -f FLOOR ]] guard trips.
        (fake_repo / "wrappers" / "bin" / "cartopian-cascade-pm").write_text("#!/bin/sh\n")
        env = dict(os.environ, PYTHONPATH=str(fake_repo))
        proc = subprocess.run(
            [str(copy)], stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=60, env=env,
        )
        assert proc.returncode != 0, "harness must fail closed when a cascade asset appears"
        assert "INCONSISTENT" in (proc.stdout + proc.stderr)

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root bypasses directory write permissions; unwritable-destination cannot be simulated",
    )
    def test_determination_harness_fails_closed_on_unwritable_evidence_destination(self, tmp_path):
        # F1 regression: when the evidence artifact CANNOT be written/updated, the
        # harness must exit NONZERO and must NOT print a "captured" success marker —
        # a stale or unwritten capture can never masquerade as a clean run. The
        # asset/tier state here is otherwise CONSISTENT (stub reports tier-3, no
        # sham assets), so the unwritable destination is the only thing that can
        # fail the run — proving this fails on the write, not on the sham-asset guard.
        # Exercised against a COPY in a throwaway tree so the shipped state is untouched.
        fake_repo = tmp_path / "cartopian"
        (fake_repo / "wrappers" / "bin").mkdir(parents=True)
        (fake_repo / "wrappers" / "etc").mkdir(parents=True)
        (fake_repo / "cli" / "commands").mkdir(parents=True)
        (fake_repo / "cli" / "__init__.py").write_text("")
        (fake_repo / "cli" / "commands" / "__init__.py").write_text("")
        # Consistent stub: cascade stays tier-3 with no assets (the shipped reality),
        # so the fail-closed-on-sham guard would PASS and the run would normally
        # publish — isolating the write failure as the sole cause of a nonzero exit.
        (fake_repo / "cli" / "commands" / "_harness_tier.py").write_text(
            "def classify_harness_tier(agent):\n"
            "    class R:\n"
            "        tier='tier-3'; constrained=False\n"
            "        floor_profile_present=False; depth_profile_present=False\n"
            "    return R()\n"
        )
        harness_dir = fake_repo / "tests" / "wrappers" / "pm-cascade"
        harness_dir.mkdir(parents=True)
        copy = harness_dir / "determine-cascade-tier.sh"
        copy.write_text(DETERMINE.read_text(encoding="utf-8"))
        copy.chmod(0o755)
        # Pre-seed a STALE artifact, then make its directory unwritable. A correct
        # harness can neither update it nor report a fresh capture.
        evid_dir = harness_dir / "evidence"
        evid_dir.mkdir()
        stale = evid_dir / "cascade-tier-determination.txt"
        stale.write_text("STALE-SENTINEL-DO-NOT-OVERWRITE\n", encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(fake_repo))
        evid_dir.chmod(0o555)  # read-only: mktemp/write into it must fail
        try:
            proc = subprocess.run(
                [str(copy)], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=60, env=env,
            )
        finally:
            evid_dir.chmod(0o755)  # restore so tmp_path teardown can clean up
        out = proc.stdout + proc.stderr
        assert proc.returncode != 0, (
            f"harness must fail closed when the evidence artifact cannot be written\n{out}"
        )
        # No false success: the capture marker must be absent on a failed write.
        assert "forcing evidence captured" not in out, (
            f"harness reported a capture despite an unwritable destination\n{out}"
        )
        # The stale artifact must be untouched — neither updated nor silently
        # overwritten — so a stale capture is never passed off as fresh.
        assert stale.read_text(encoding="utf-8") == "STALE-SENTINEL-DO-NOT-OVERWRITE\n", (
            "stale evidence artifact was modified despite the write failure"
        )


# --------------------------------------------------------------------------- #
# The compatibility matrix must match the determination: cascade IS
# not-recommended-as-PM-host at tier-3 with the evidence pointer.
# --------------------------------------------------------------------------- #
class TestCompatibilityMatrix:
    @pytest.fixture(scope="class")
    def text(self) -> str:
        assert COMPAT.is_file(), f"compatibility matrix missing: {COMPAT}"
        return COMPAT.read_text(encoding="utf-8")

    def test_cascade_row_is_not_recommended_tier_3(self, text):
        rows = [l for l in text.splitlines()
                if l.strip().startswith("|") and "cascade" in l.lower() and "tier" in l.lower()]
        assert rows, "no cascade row found in the matrix table"
        joined = " ".join(rows).lower()
        assert "not-recommended" in joined, "cascade must be classified not-recommended-as-PM-host"
        assert "tier-3" in joined
        assert "works-out-of-the-box" not in joined
        assert "not-yet-classified" not in joined, (
            "cascade row must be resolved (not 'not-yet-classified') by this task"
        )

    def test_cascade_row_points_at_the_evidence(self, text):
        rows = [l for l in text.splitlines()
                if l.strip().startswith("|") and "cascade" in l.lower() and "tier" in l.lower()]
        joined = " ".join(rows)
        assert "pm-cascade" in joined, "cascade row must point at the forcing evidence"

    def test_cascade_section_records_forcing_reasons(self, text):
        low = text.lower()
        # the cascade section must record the architecture-level forcing reasons
        assert "electron" in low
        assert "no native sandbox" in low or "not a native os sandbox" in low \
            or "not an os sandbox" in low or "no os-level sandbox" in low
        assert "devin for terminal" in low  # cascade is not the devin terminal CLI

    def test_cascade_not_promotable_distinguished_from_codex(self, text):
        # codex is not-recommended but tier-1-2-DETECTED (assets exist); cascade is
        # not-recommended AND tier-3 (no assets) — the matrix must not conflate them.
        low = text.lower()
        assert "unpromotable" in low or "no floor" in low or "no genuine mechanism" in low \
            or "no contained" in low or "no first-party" in low


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
