"""FR-009 enforcement-doc + compatibility-matrix consistency (TASK-04-001).

The Phase-04 FR-009 deliverable is the consolidated, operator-facing **enforcement
documentation** (`docs/ENFORCEMENT.md`): the three-tier containment model, the
per-harness hardening steps + recommended launch modes, and the complete agent
compatibility matrix (works-out-of-the-box / needs-manual-constraints /
not-recommended-as-PM-host) for every configured harness.

This module is the **consistency / fact-check pass** that gates the doc. It needs
no live harness — it asserts, purely from on-disk assets, that the *documented*
classification stays faithful to the *shipped* one:

* every configured harness (claude, codex, gemini, cascade, devin) appears in the
  consolidated matrix, and
* the tier documented for each matches the tier the asset-driven classifier
  (`cli.commands._harness_tier.classify_harness_tier`) actually reports —
  ``tier-1-2`` for claude/codex/gemini (both floor + depth assets present),
  ``tier-3`` for cascade/devin (assets absent).

The expected tier is taken from the **live classifier**, never hard-coded here, so
the doc can never drift away from the shipped behavior without turning this red.
The same parse is also run against the pre-existing `docs/COMPATIBILITY.md`
harness matrix, so a future asset change that flips a tier turns both docs red
together.

Red-before-green
----------------
Before TASK-04-001 there is no consolidated `docs/ENFORCEMENT.md` — the three-tier
model, hardening steps, and bucketed matrix live nowhere single, so
:class:`TestEnforcementDocExists` and the matrix/structure classes below are RED
(the file is absent). After the doc lands they are GREEN. The
`COMPATIBILITY.md` parse is green throughout (it already carries a consistent
matrix); it is here to pin the no-drift invariant, not as the red signal.

Scope note (NF-004): this is a docs fact-check only. It imports the classifier as
the source of truth and asserts the prose matches it — it changes **no** runtime
behavior, tier logic, or classification. Stdlib + pytest only (NF-001).
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from cli.commands import _harness_tier as ht

REPO_ROOT = Path(__file__).resolve().parents[2]
ENFORCEMENT_DOC = REPO_ROOT / "docs" / "ENFORCEMENT.md"
COMPATIBILITY_DOC = REPO_ROOT / "docs" / "COMPATIBILITY.md"

# The configured PM harnesses that MUST appear in the consolidated matrix. This
# is the full set the project supports as PM-host candidates and the set the
# Phase-01..03 evidence + decisions cover (DEC-001 claude; TASK-03-001 codex;
# TASK-03-002 gemini; DEC-008 cascade; DEC-009 devin).
CONFIGURED_HARNESSES = ("claude", "codex", "gemini", "cascade", "devin")

# The three matrix buckets the doc must use.
WORKS_OOTB = "works-out-of-the-box"
NEEDS_MANUAL = "needs-manual-constraints"
NOT_RECOMMENDED = "not-recommended-as-pm-host"

_TIER_TOKEN = re.compile(r"tier-1-2|tier-3")


def _expected_tier(harness: str) -> str:
    """The tier the SHIPPED asset-driven classifier reports for this harness."""
    return ht.classify_harness_tier(harness).tier


def _table_rows(doc_text: str):
    """Yield the cell-lists of every GitHub-markdown table row in the doc.

    A table row is a line that starts (after optional whitespace) with ``|``.
    Header-separator rows (``| --- | --- |``) are skipped. Cells are returned
    stripped of surrounding whitespace; markdown emphasis is left intact for the
    caller to normalise.
    """
    for line in doc_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if cells and all(set(c) <= {"-", ":"} and c for c in cells):
            continue  # header separator
        yield cells


def _normalise(cell: str) -> str:
    """Lower-case a table cell and strip markdown emphasis / code ticks."""
    return re.sub(r"[*`_]", "", cell).strip().lower()


def documented_tiers(doc_text: str) -> dict:
    """Map each configured harness -> the tier token documented in the matrix.

    Scans every table row; a row counts as a matrix entry when its FIRST cell
    names a configured harness (the per-harness evidence tables key their first
    column on a *facet* name, so they are skipped) AND the row carries a tier
    token. The first such row per harness wins.
    """
    found: dict = {}
    for cells in _table_rows(doc_text):
        if not cells:
            continue
        head = _normalise(cells[0])
        # First cell must NAME a harness (a word match, so "claude (reference)"
        # and "**codex**" both count, but a facet row like "exposed tool set"
        # does not).
        harness = next(
            (h for h in CONFIGURED_HARNESSES if re.search(rf"\b{h}\b", head)), None
        )
        if harness is None or harness in found:
            continue
        row_text = " ".join(cells)
        m = _TIER_TOKEN.search(row_text)
        if m:
            found[harness] = m.group(0)
    return found


def documented_buckets(doc_text: str) -> dict:
    """Map each configured harness -> the matrix bucket named in its row."""
    found: dict = {}
    for cells in _table_rows(doc_text):
        if not cells:
            continue
        head = _normalise(cells[0])
        harness = next(
            (h for h in CONFIGURED_HARNESSES if re.search(rf"\b{h}\b", head)), None
        )
        if harness is None or harness in found:
            continue
        row_text = _normalise(" ".join(cells))
        for bucket in (NOT_RECOMMENDED, NEEDS_MANUAL, WORKS_OOTB):
            if bucket in row_text:
                found[harness] = bucket
                break
    return found


class TestEnforcementDocExists(unittest.TestCase):
    """RED until the consolidated enforcement doc is authored."""

    def test_enforcement_doc_present(self):
        self.assertTrue(
            ENFORCEMENT_DOC.is_file(),
            msg=(
                f"no consolidated enforcement doc at {ENFORCEMENT_DOC} — the "
                "three-tier model, per-harness hardening, and compatibility "
                "matrix are not consolidated in one operator-facing doc"
            ),
        )


class TestEnforcementMatrixConsistency(unittest.TestCase):
    """Every configured harness is in the ENFORCEMENT matrix at the shipped tier."""

    def setUp(self):
        if not ENFORCEMENT_DOC.is_file():
            self.skipTest(f"{ENFORCEMENT_DOC} absent (see TestEnforcementDocExists)")
        self.tiers = documented_tiers(ENFORCEMENT_DOC.read_text(encoding="utf-8"))

    def test_every_harness_present(self):
        missing = [h for h in CONFIGURED_HARNESSES if h not in self.tiers]
        self.assertEqual(
            missing,
            [],
            msg=f"matrix is missing configured harness rows: {missing}",
        )

    def test_documented_tier_matches_shipped(self):
        for harness in CONFIGURED_HARNESSES:
            with self.subTest(harness=harness):
                self.assertIn(harness, self.tiers, msg=f"{harness} absent from matrix")
                self.assertEqual(
                    self.tiers[harness],
                    _expected_tier(harness),
                    msg=(
                        f"matrix documents {harness} as {self.tiers.get(harness)} "
                        f"but the shipped _harness_tier classifier reports "
                        f"{_expected_tier(harness)}"
                    ),
                )

    def test_tier_1_2_set_is_exactly_claude_codex_gemini(self):
        tier_1_2 = {h for h, t in self.tiers.items() if t == ht.TIER_CONSTRAINED}
        self.assertEqual(tier_1_2, {"claude", "codex", "gemini"})

    def test_tier_3_set_is_exactly_cascade_devin(self):
        tier_3 = {h for h, t in self.tiers.items() if t == ht.TIER_ADVISORY}
        self.assertEqual(tier_3, {"cascade", "devin"})


class TestEnforcementBuckets(unittest.TestCase):
    """Each harness is bucketed, and the bucket is congruent with its tier."""

    def setUp(self):
        if not ENFORCEMENT_DOC.is_file():
            self.skipTest(f"{ENFORCEMENT_DOC} absent (see TestEnforcementDocExists)")
        text = ENFORCEMENT_DOC.read_text(encoding="utf-8")
        self.buckets = documented_buckets(text)
        self.tiers = documented_tiers(text)

    def test_every_harness_bucketed(self):
        missing = [h for h in CONFIGURED_HARNESSES if h not in self.buckets]
        self.assertEqual(missing, [], msg=f"un-bucketed harnesses: {missing}")

    def test_tier_3_implies_not_recommended(self):
        # A tier-3 harness can never be works-OOTB / needs-manual — it is
        # unconstrainable, so it must sit in the not-recommended bucket.
        for harness, tier in self.tiers.items():
            if tier == ht.TIER_ADVISORY:
                with self.subTest(harness=harness):
                    self.assertEqual(self.buckets.get(harness), NOT_RECOMMENDED)


class TestThreeTierModelDocumented(unittest.TestCase):
    """The three-tier containment model is described in the enforcement doc."""

    def setUp(self):
        if not ENFORCEMENT_DOC.is_file():
            self.skipTest(f"{ENFORCEMENT_DOC} absent (see TestEnforcementDocExists)")
        self.text = ENFORCEMENT_DOC.read_text(encoding="utf-8").lower()

    def test_mentions_each_tier(self):
        for token in ("tier 1", "tier 2", "tier 3"):
            with self.subTest(token=token):
                self.assertIn(token, self.text)

    def test_describes_tier_concepts(self):
        # Capability floor (Tier 1), native depth profile (Tier 2), advisory
        # fail-closed (Tier 3) — the three load-bearing concepts.
        for concept in ("capability floor", "depth profile", "fail-closed"):
            with self.subTest(concept=concept):
                self.assertIn(concept, self.text)


class TestPerHarnessHardeningDocumented(unittest.TestCase):
    """Each configured harness has a hardening / recommended-mode subsection."""

    def setUp(self):
        if not ENFORCEMENT_DOC.is_file():
            self.skipTest(f"{ENFORCEMENT_DOC} absent (see TestEnforcementDocExists)")
        self.text = ENFORCEMENT_DOC.read_text(encoding="utf-8")

    def test_each_harness_has_a_section(self):
        # A markdown heading naming the harness (case-insensitive), e.g. "### claude".
        for harness in CONFIGURED_HARNESSES:
            with self.subTest(harness=harness):
                pat = re.compile(rf"^#{{2,4}}\s+.*\b{harness}\b", re.IGNORECASE | re.MULTILINE)
                self.assertRegex(self.text, pat)

    def test_recommended_mode_present_for_constrained_harnesses(self):
        # claude/codex/gemini each ship a recommended contained launch profile
        # (the cartopian-<h>-pm wrapper). The doc must name that wrapper.
        for harness in ("claude", "codex", "gemini"):
            with self.subTest(harness=harness):
                self.assertIn(f"cartopian-{harness}-pm", self.text)


class TestCompatibilityMatrixNoDrift(unittest.TestCase):
    """The pre-existing COMPATIBILITY.md harness matrix also matches the classifier.

    Green throughout — this pins the no-drift invariant across BOTH consolidated
    surfaces so an asset change that flips a tier turns the docs red together.
    """

    def setUp(self):
        if not COMPATIBILITY_DOC.is_file():
            self.skipTest(f"{COMPATIBILITY_DOC} absent")
        self.tiers = documented_tiers(COMPATIBILITY_DOC.read_text(encoding="utf-8"))

    def test_every_harness_present_and_consistent(self):
        for harness in CONFIGURED_HARNESSES:
            with self.subTest(harness=harness):
                self.assertIn(harness, self.tiers, msg=f"{harness} absent from matrix")
                self.assertEqual(self.tiers[harness], _expected_tier(harness))


if __name__ == "__main__":
    unittest.main()
