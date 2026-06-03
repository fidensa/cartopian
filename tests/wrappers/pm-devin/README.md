# devin PM-containment harness (TASK-03-004 / FR-011)

The devin slice of the harness-level containment evidence; resolves **OQ-004**
for devin. Like `../pm-cascade/` — and unlike `../pm-codex/` / `../pm-gemini/`,
which drive a real contained harness and capture red→green probe transcripts —
devin has **no offline, locally-contained runtime to probe**: it is a
cloud-authenticated, local-first/cloud-hybrid agent and it proves not-promotable
as a contained PM host. This directory therefore captures the **forcing
evidence** that records why, satisfying the evidence gate's unpromotable branch
(a finding, not a silent deferral).

devin is a *more nuanced* not-recommended than cascade: cascade has **no**
containment mechanism at all, whereas devin ships **partial** mechanisms (a
config `permissions` allow/deny/ask system and a fail-closed OS-level
`--sandbox`) that simply **cannot be combined into a genuine, verifiable,
non-escapable, layered Tier-1+2**.

- `determine-devin-tier.sh` — the determination harness. Deterministic and
  environment-independent (the forcing facts are architectural / documented
  product behaviour). Captures the asset state + the five forcing facets and
  writes the artifact below. Fail-closed: it refuses to emit a `NOT_PROMOTABLE`
  pass if devin assets ever appear or the detected tier is no longer `tier-3` (a
  real mechanism may have landed — re-evaluate), and exits non-zero (printing no
  "captured" marker) if the evidence artifact cannot be written. Run:
  `./determine-devin-tier.sh`.
- `evidence/devin-tier-determination.txt` — the captured forcing-evidence
  artifact (the committed pinned baseline, asserted by
  `../../containment/test_devin_harness_promotion.py`).
- `FINDINGS.md` — the full writeup with cited sources, mechanism analysis, the
  FR-011 facet table, and the "what would change this" promotion path.

## Classification

**not-recommended-as-PM-host** at **tier-3 (advisory)** — devin's cloud `/handoff`
+ cloud subagents escape any local boundary (F-D1), its `permissions` floor
cannot remove built-in tools or scope to one MCP server (F-D2), its OS `--sandbox`
is mutually exclusive with that floor and is Unstable (F-D3), it has no
non-overridable settings-file injection path (F-D4), and there is no contained
local runtime to capture FR-011 in-runtime evidence (F-D5). No floor/depth assets
are shipped, so `_harness_tier` honestly keeps devin at `tier-3` with no
classifier edit (TASK-02-001) and the suite's no-regression guarantee (NF-004)
holds. See `../../../docs/COMPATIBILITY.md` for the matrix entry and the red-team
notes.
