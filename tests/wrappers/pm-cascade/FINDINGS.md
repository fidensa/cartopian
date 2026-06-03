# cascade PM-containment determination (TASK-03-003 / FR-010 + FR-007 + FR-011 + FR-009)

The cascade slice of the all-harness coverage. Unlike the codex and gemini
promotions — which ship a genuine Tier-1 floor and Tier-2 native-sandbox depth
profile and reach `tier-1-2` detection — **cascade proves unpromotable**. This
file is the captured **forcing evidence** the evidence gate's unpromotable
branch requires (a finding, not a silent deferral), and it records *why* cascade
is classified **not-recommended-as-PM-host** at **tier-3 (advisory)**.

Reproduce: `./determine-cascade-tier.sh` → `evidence/cascade-tier-determination.txt`.
Pinned by `../../containment/test_cascade_harness_promotion.py`.

## What "promotable" requires, and what cascade lacks

Every promoted harness (claude reference, codex, gemini) reaches Tier 1+2 with
the *same* architecture:

```
exec <first-party harness binary> <hard-coded, non-overridable floor flags>
     beneath the harness's OWN native OS sandbox (seatbelt / Landlock / …)
```

- **Tier-1 floor** (DEC-001 / FR-002) — a `wrappers/bin/cartopian-<harness>-pm`
  wrapper that launches the harness with a fixed Cartopian-MCP-only toolset and
  no shell / no raw file write/edit. claude uses `--tools ""`; codex uses a
  hard-coded `CODEX_HOME` config (`features.shell_tool=false`); gemini uses a
  system-settings `tools.exclude` of every built-in.
- **Tier-2 depth** (FR-007) — a `wrappers/etc/sandbox-<harness>-pm-depth.json`
  profile that drives the harness's **native OS sandbox** to additionally deny
  product-repo / work-root paths and write/exec beneath the floor.

Cascade — the agent embedded in the Windsurf IDE (vendor Codeium, now
Cognition/Devin) — satisfies **none** of the three preconditions:

### F-C1 — No first-party containable runtime

Cascade runs inside the Windsurf **Electron IDE**. There is no first-party,
scriptable cascade binary to wrap with a hard-coded floor launch profile. The
only headless options are **third-party**, and both are barred by **NF-001** (no
third-party packages, no bundled sandbox):

- `staronelabs/windsurf-cli` (`wsc`) — a shell→Cascade bridge that delivers
  prompts by driving the Windsurf **GUI via AppleScript** (macOS-only). It
  relays to the **full-capability** agent; it adds no containment surface.
- `pfcoperez/windsurfinabox` — packages Windsurf in a **Docker image** for
  headless use. That is a *bundled sandbox*, explicitly disallowed by NF-001.

The official first-party Windsurf terminal CLI is **Devin for Terminal** — which
is the **separate `devin` harness** (TASK-03-004 / P03-BUILD-004), not cascade.

### F-C2 — No Tier-1 floor mechanism

Cascade exposes no launch-time flag, env var, or config file that removes its
built-in file-edit and shell tools and scopes the agent to a single MCP server.
Windsurf's **per-tool toggling** exists only as an **interactive GUI panel** and
only filters *MCP-server* tools — it cannot remove cascade's own built-in
edit/write/shell tools, and it is not a hard-coded, non-overridable launch
profile. The DEC-001/FR-002 capability floor therefore cannot be built.

### F-C3 — No Tier-2 native sandbox

Cascade's only command-execution control is the **allow/deny-list +
auto-execution-level** model — application-layer command-**string matching**,
not an OS sandbox:

- Auto-execution levels: **Disabled** (manual approval), **Allowlist** (only
  allow-listed commands auto-run), **Auto** (the model judges safety), **Turbo**
  (auto-run everything except the deny list).
- The deny list is a command-string match (`rm` blocks `rm index.py`); deny
  takes precedence over allow.

This is **not** a native OS sandbox: there is no seatbelt/sandbox-exec,
Landlock, or container layer to drive (FR-007 has nothing to point at). Cascade
and its MCP servers run with the **full permissions of the launching process**;
there is **no documented filesystem write boundary or workspace restriction**,
so the product repo and work roots are fully reachable. The control is also not
fail-closed — "Auto" mode defers to the **model's own** safety judgement (the
advisory posture Tier-1/2 is meant to replace), and a string-prefix deny list is
bypassable (quoting, path indirection, alternate binaries).

## Harness-level evidence (FR-011 facets)

Primitive-level tests are not sufficient (FR-011); the determination records the
PM-runtime facets directly:

| FR-011 facet | cascade result |
| --- | --- |
| exposed tool set | **unbounded** — built-in edit/write/shell tools cannot be withheld; no Cartopian-only scoping mechanism (F-C2) |
| reachable filesystem | **unbounded** — full user-privilege reach incl. the work root + product repo; no floor removes the write tools, no native sandbox denies the paths (F-C3) |
| in-runtime prohibited attempts (product-repo / work-root / non-allowlisted write, `..`/symlink escape, shell spawn, exec, exec-bit, config write) | **not exercisable as "blocked"** — there is no contained cascade runtime to run them against (F-C1); uncontained they all succeed. The negative test has *no profile to exercise* — which is itself the forcing evidence. |
| still-functional (Cartopian toolset) | n/a — no contained runtime |

## Why no sham assets are shipped

Shipping an empty/placeholder `cartopian-cascade-pm` + `sandbox-cascade-pm-depth.json`
would make the asset-driven `_harness_tier` falsely report `tier-1-2` (detection
keys on file existence alone, by design — TASK-02-001), claiming a containment
guarantee that does not exist, and would **break** the suite's no-regression
guarantee (the existing `test_harness_tier_detection.py` and
`test_gemini_harness_promotion.py` both pin `cascade → tier-3` as the archetypal
unconstrainable harness — NF-004). So **no assets are shipped**: `_harness_tier`
honestly keeps cascade at `tier-3` with **no classifier edit**, and the matrix
records the permanent **not-recommended-as-PM-host** classification with this
evidence.

## Classification

**not-recommended-as-PM-host**, enforceable tier **tier-3 (advisory)**. Resolves
OQ-004 for cascade. A cascade-hosted PM must run under the FR-008 Tier-3 advisory
gate (explicit operator acknowledgment of the unconstrained risk, recorded), or —
preferred — a different harness should host the PM. The project's effective
`[handoffs.pm].agent` is `claude` (tier-1-2, works-out-of-the-box), so this
finding does not block the current configuration; it governs any future move to
cascade as the PM host.

## Sources (captured 2026-06-02)

- Windsurf terminal command-control model (auto-execution levels Disabled /
  Allowlist / Auto / Turbo; allow/deny lists; deny precedence; command-string
  matching) — Windsurf docs, "Terminal"
  (`https://docs.windsurf.com/windsurf/terminal`, now redirecting to
  `https://docs.devin.ai/desktop/terminal`).
- Cascade runs in the Windsurf Electron IDE with file-modify + shell-run; MCP
  servers "run with the permissions of the launching process"; no OS-level
  sandbox documented — Windsurf safety audit
  (`https://vibe-eval.com/safety/windsurf/`).
- No first-party headless cascade binary; third-party headless options are
  `wsc` (AppleScript GUI bridge, macOS-only) and `windsurfinabox` (Docker) —
  `https://github.com/staronelabs/windsurf-cli`,
  `https://github.com/pfcoperez/windsurfinabox`.
- The first-party Windsurf terminal CLI is **Devin for Terminal** (the separate
  `devin` harness) — Windsurf changelog / release notes (May 2026).
- Per-tool toggling filters MCP-server tools only (not cascade's built-ins),
  configured in `~/.codeium/windsurf/mcp_config.json` / the Cascade panel —
  Windsurf Wave 8 customization notes.
