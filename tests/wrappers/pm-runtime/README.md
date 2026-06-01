# PM-runtime containment harness (FR-001)

This directory holds the FR-001 PM-containment harness and its captured
evidence:

- `run-probes.sh` — drives `claude -p` in RED (no floor) and GREEN (the exact
  documented floor) states and captures harness-level evidence.
- `mcp-cartopian-only.json` — the GREEN-state MCP config (Cartopian server only).
- `FLOOR-CONFIG.md` — the exact documented/recommended floor the GREEN runs apply.
- `evidence/` — the captured stream-json transcripts, on-disk side-effect checks,
  sentinel checks, and tool inventories.

It sits alongside the other PM-containment wrapper harnesses
(`../pm-floor/`, `../pm-sandbox/`) and is invoked by the FR-011 verification
suite (`../../containment/run-containment-suite.sh --with-harness`), which also
pins this evidence via `../../containment/manifest.py`.

## Relocation note (historical)

This harness and its evidence were **relocated from
`spikes/fr-001-pm-containment/`** into this durable `tests/` location. The move
was a no-behavior-change path update: once the FR-011 suite began pinning this
evidence and invoking `run-probes.sh`, a durable suite depending on a `spikes/`
path was inconsistent with the sibling `pm-floor`/`pm-sandbox` harnesses.

The immutable `DEC-001` decision record cites the original
`spikes/fr-001-pm-containment/...` path as its point-in-time evidence location.
That decision record is intentionally left unedited; this note records the prior
path so the decision stays followable to the evidence in its new home.

The captured evidence under `evidence/` is preserved byte-for-byte from the
spike, so the absolute paths embedded inside those point-in-time transcripts
still read `.../spikes/fr-001-pm-containment/...`. Those are inert historical
capture contents, not functional references.
