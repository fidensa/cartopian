# cascade PM-containment harness (TASK-03-003 / FR-011)

The cascade slice of the harness-level containment evidence. Unlike
`../pm-codex/` and `../pm-gemini/` — which drive a real contained harness and
capture red→green probe transcripts — cascade has **no contained runtime to
probe**: it proves unpromotable. This directory therefore captures the
**forcing evidence** that records why, satisfying the evidence gate's
unpromotable branch (a finding, not a silent deferral).

- `determine-cascade-tier.sh` — the determination harness. Deterministic and
  environment-independent (the forcing facts are architectural). Captures the
  asset state + the three forcing facets and writes the artifact below.
  Fail-closed: it refuses to emit a `NOT_PROMOTABLE` pass if cascade assets ever
  appear or the detected tier is no longer `tier-3` (a real mechanism may have
  landed — re-evaluate). Run: `./determine-cascade-tier.sh`.
- `evidence/cascade-tier-determination.txt` — the captured forcing-evidence
  artifact (the committed pinned baseline, asserted by
  `../../containment/test_cascade_harness_promotion.py`).
- `FINDINGS.md` — the full writeup with cited sources, mechanism analysis, and
  the FR-011 facet table.

## Classification

**not-recommended-as-PM-host** at **tier-3 (advisory)** — cascade has no
first-party containable runtime (F-C1), no Tier-1 toolset-floor mechanism
(F-C2), and no Tier-2 native sandbox (F-C3). No floor/depth assets are shipped,
so `_harness_tier` honestly keeps cascade at `tier-3` with no classifier edit
(TASK-02-001) and the suite's no-regression guarantee (NF-004) holds. See
`FINDINGS.md` for the matrix rationale and the red-team notes.
