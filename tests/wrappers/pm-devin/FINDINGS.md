# devin PM-containment determination (TASK-03-004 / FR-010 + FR-007 + FR-011 + FR-009)

The devin slice of the all-harness coverage; resolves **OQ-004** for devin.
Unlike the codex and gemini promotions — which ship a genuine Tier-1 floor and
Tier-2 native-sandbox depth profile and reach `tier-1-2` detection — and unlike
cascade — which has **no containment mechanism at all** — **devin proves
not-promotable as a contained PM host for a more nuanced reason**: it ships
*partial* local mechanisms that **cannot be combined into a genuine, verifiable,
non-escapable, layered Tier-1+2**. This file is the captured **forcing evidence**
the evidence gate's unpromotable branch requires (a finding, not a silent
deferral), and it records *why* devin is classified
**not-recommended-as-PM-host** at **tier-3 (advisory)**.

Reproduce: `./determine-devin-tier.sh` → `evidence/devin-tier-determination.txt`.
Pinned by `../../containment/test_devin_harness_promotion.py`.

## What "promotable" requires, and what devin lacks

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
  highest-precedence system-settings `tools.exclude` of every built-in.
- **Tier-2 depth** (FR-007) — a `wrappers/etc/sandbox-<harness>-pm-depth.json`
  profile that drives the harness's **native OS sandbox** to additionally deny
  product-repo / work-root paths and write/exec **beneath** the floor.

Devin — "Devin for Terminal" (Cognition), a **local-first / cloud-hybrid** CLI —
*does* ship a first-party headless binary (`devin -p`) and *does* ship two
partial mechanisms: a config-driven `permissions` allow/deny/ask system and a
fail-closed OS-level `--sandbox`. But **five forcing facets** each independently
block the layered floor+depth shape.

### F-D1 — Cloud `/handoff` + cloud subagents escape (the dominant residual)

The local Devin terminal agent exposes a **`/handoff` command** that *"package[s]
up the conversation context and current git branch, then create[s] a cloud Devin
session that picks up where you left off"* — a cloud agent *"with its own
computer"* that runs *"in its own sandbox, not yours."* It also exposes a
**subagent/delegation** surface (foreground and background). Both run **outside
the local OS `--sandbox` and outside the local `permissions` floor**. There is no
documented config key to disable `/handoff` or cloud delegation. The local
sandbox's "OS-enforced limits on what files and domains the agent can touch" do
**not** extend to the cloud machine.

This is a **config-irremovable, OS-unsandboxable execution + data-exfiltration
surface** — directly analogous to, and broader than, codex's server-side
`web_search` residual (F1b): a full cloud computer with shell, file write, and
network, not merely a web search. A contained devin PM could `/handoff` the
governed project's context (and git branch) to an uncontained cloud machine. This
facet alone forces not-recommended.

### F-D2 — No capability-floor mechanism (cannot remove tools / scope MCP)

devin's only Tier-1 control is the config `permissions` allow/deny/ask system —
**tool-level pattern matching** (`Read(...)`, `Write(...)`, `Exec(tool)`,
`mcp__server__tool`). There is **no** analogue of claude `--tools ""`, gemini
`tools.exclude`, or codex `features.shell_tool=false` that **removes** devin's
built-in edit/write/shell/read tools from the model surface, and **no** key that
restricts the agent to a single MCP server or disables built-in tools (the MCP
docs are explicit: permissions control *tool access*, not server availability).
The floor is therefore an **approval gate over an unbounded tool surface**, not a
capability floor — it cannot reach the Cartopian-only / no-shell-tool surface
DEC-001/FR-002 requires (the same shortfall that, on cascade, was facet F-C2;
here devin at least has the OS sandbox as a *separate, mutually-exclusive*
backstop — see F-D3).

### F-D3 — Tier-1 floor and Tier-2 sandbox are mutually exclusive

`--sandbox` **auto-selects, and only permits, the `autonomous` permission mode**,
which auto-approves tool calls and grants *"the additional ability to run any
shell command within an OS-level sandbox."* So a deny-shell / deny-write
**approval** floor cannot be layered **beneath** the native OS sandbox the way
claude layers `--tools ""` beneath seatbelt (or codex layers features-off beneath
`-s read-only`). Enabling the OS sandbox **replaces** the approval floor with
"auto-approve within the box." Neither single posture is a genuine floor+depth:

- **approval-only** (no `--sandbox`): bypassable, application-layer, **not
  OS-enforced**, and not the fail-closed posture Tier-1/2 is meant to replace;
- **sandbox-only** (`--sandbox` → autonomous): auto-approves and runs *any* shell
  command **within** the box, AND still leaves F-D1 (cloud handoff) wide open.

The `--sandbox` feature is itself documented as **Unstable**.

### F-D4 — No non-overridable injection path (no settings-file flag)

devin exposes **no `--config`/`--settings` flag** and no highest-precedence
settings env var — unlike claude `--settings <file>`, gemini
`GEMINI_CLI_SYSTEM_SETTINGS_PATH`, or codex `CODEX_HOME`. Config is read only
from `~/.config/devin/config.json`, `.devin/config.json`, and
`.devin/config.local.json` (precedence local > project > user). The only "highest
precedence" is the **cwd-local** `.devin/config.local.json`, and `read_config_from`
will **import** permissive `cursor`/`windsurf`/`claude` configs unless each is
explicitly disabled. A hard-coded, **non-overridable** floor launch profile
therefore cannot be guaranteed — the floor is only as fixed as the launch cwd,
which the invoker controls.

### F-D5 — No contained local runtime to capture FR-011 in-runtime evidence

Devin for Terminal is a **cloud-authenticated** hybrid agent: its model runs in
Cognition's cloud, and `/handoff` + subagents execute in the cloud. There is no
offline, locally-contained devin PM runtime to run the FR-011 in-runtime
prohibited-attempt probes against (product-repo / work-root / non-allowlisted
write, `..`/symlink escape, shell spawn, exec, exec-bit set, config write) and
**prove** fail-closed refusals. The codex and gemini `tier-1-2` promotions were
each **gated on captured live in-runtime evidence**; devin cannot meet that gate
here. Shipping floor+depth assets would flip `_harness_tier` to `tier-1-2` with
**zero guaranteeing evidence** — exactly the sham the cascade precedent forbids.

## Harness-level evidence (FR-011 facets)

Primitive-level tests are not sufficient (FR-011); the determination records the
PM-runtime facets directly:

| FR-011 facet | devin result |
| --- | --- |
| exposed tool set | **unbounded at the floor** — built-in edit/write/shell/read tools cannot be removed and the agent cannot be scoped to the Cartopian MCP set; only deny rules + the (mutually-exclusive) OS sandbox gate them (F-D2) |
| reachable filesystem | **not verifiably bounded for a contained PM** — local `--sandbox` cannot be layered beneath the floor (F-D3), cannot be injected non-overridably (F-D4), and is bypassed entirely by the cloud handoff/subagents whose filesystem is a cloud machine outside Cartopian control (F-D1); no contained runtime demonstrates a bound (F-D5) |
| in-runtime prohibited attempts (product-repo / work-root / non-allowlisted write, `..`/symlink escape, shell spawn, exec, exec-bit, config write) | **not exercisable as "blocked"** for a contained devin PM — no offline contained runtime to run them against (F-D5), and the cloud-handoff path escapes any local boundary regardless (F-D1). The negative test has *no genuine, verifiable contained profile to exercise* — which is itself the forcing evidence. |
| still-functional (Cartopian toolset) | n/a — no contained runtime |

## Why no sham assets are shipped

Shipping a `cartopian-devin-pm` floor + `sandbox-devin-pm-depth.json` depth would
make the asset-driven `_harness_tier` falsely report `tier-1-2` (detection keys
on file existence alone, by design — TASK-02-001), claiming a containment
guarantee that **cannot be verified** (F-D5) and that the cloud-handoff escape
(F-D1) **defeats** regardless, and would **break** the suite's no-regression
guarantee (the existing `test_harness_tier_detection.py`,
`test_cascade_harness_promotion.py`, and `test_gemini_harness_promotion.py`-style
pins keep `devin → tier-3` — NF-004). So **no assets are shipped**: `_harness_tier`
honestly keeps devin at `tier-3` with **no classifier edit**, and the matrix
records the **not-recommended-as-PM-host** classification with this evidence.

The determination is **stdlib-only** (bash + `python3`), ships **no third-party
package and no bundled sandbox** (**NF-001**): devin's `--sandbox` is its *own*
native mechanism, so even a promotion would never bundle a sandbox — but the
forcing facets above prevent an honest promotion regardless.

## What would change this

devin is **not-recommended via devin-side assets alone**, not "permanently
unbuildable" the way cascade is. Promotion would become honest only with upstream
devin controls to: (a) **hard-disable cloud `/handoff` + cloud subagents** for a
contained session; (b) **remove built-in tools / scope to a single MCP server**
(an analogue of `--tools ""` / `tools.exclude`); (c) **layer the OS sandbox
beneath a non-auto-approving floor** (decouple `--sandbox` from autonomous
auto-approval); and (d) a **non-overridable settings-file injection path**
(a `--settings`/`CODEX_HOME` analogue). With those, a contained devin PM could be
launched and **live in-runtime evidence captured**, at which point the floor +
depth assets could be shipped and devin re-classified. None of these is a
Cartopian asset today.

## Classification

**not-recommended-as-PM-host**, enforceable tier **tier-3 (advisory)**. Resolves
OQ-004 for devin. A devin-hosted PM must run under the FR-008 Tier-3 advisory
gate (explicit, recorded operator acknowledgment of the unconstrained risk) — or,
preferred, a different harness should host the PM. The project's effective
`[handoffs.pm].agent` is `claude` (`tier-1-2`, works-out-of-the-box), so this
finding does not block the current configuration; it governs any future move to
devin as the PM host.

## Sources (captured 2026-06-02)

- Devin CLI permission modes (Normal / Accept Edits / Bypass / Autonomous),
  Autonomous = accept-edits + *"the ability to run any shell command within an
  OS-level sandbox"*, and *"Autonomous is the only permission mode available when
  running with `--sandbox`"* — Devin for Terminal docs, "Essential Commands"
  (`https://cli.devin.ai/docs/essential-commands`).
- `--sandbox` enforces Read/Write permission scopes at the OS level, fails closed
  if sandbox resolution fails, and the `sandbox` config block (`allowed_domains`,
  `denied_domains`, `network_mode`) is an **Unstable** feature — Devin for
  Terminal docs, "Configuration File"
  (`https://cli.devin.ai/docs/reference/configuration/config-file`).
- `permissions` allow/deny/ask is tool-level pattern matching; the docs provide
  **no** mechanism to restrict the agent to certain MCP servers or disable
  built-in tools — Devin for Terminal docs, "MCP Configuration"
  (`https://cli.devin.ai/docs/extensibility/mcp/configuration`) and
  "Configuration File" (above). No `--config`/`--settings` flag is documented;
  config precedence is `.devin/config.local.json` > `.devin/config.json` >
  `~/.config/devin/config.json`.
- `/handoff` packages context + git branch and creates a **cloud** Devin session
  *"with its own computer"* that runs *"in its own sandbox, not yours"*; the local
  CLI is a *"local coding agent"* that hands off to the cloud — Cognition,
  "Devin for Terminal: Start Local, Hand Off to the Cloud"
  (`https://cognition.ai/blog/devin-for-terminal`) and Devin docs, "Devin CLI"
  (`https://docs.devin.ai/work-with-devin/devin-cli`).
- Subagent/delegation surface (independent subagents, foreground/background) —
  Devin for Terminal docs, "Subagents" (`https://cli.devin.ai/docs/subagents`).
- Devin is a cloud-hosted agent that runs in dedicated cloud VMs / its own hosted
  sandbox per task (cloud-handoff target architecture) — Modal, "Best Code
  Execution Sandboxes for Devin" (`https://modal.com/resources/best-sandboxes-devin`).
- cascade (the separate Windsurf harness) is **not** devin; "Devin for Terminal"
  is the distinct first-party CLI — cross-referenced from
  `../pm-cascade/FINDINGS.md` (TASK-03-003).
