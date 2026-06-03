# Cartopian PM containment — enforcement model & agent compatibility (FR-009)

This is the consolidated, operator-facing reference for **how Cartopian contains a
PM-host harness**: the three-tier containment model, the per-harness hardening
steps and recommended launch modes, and the complete agent compatibility matrix
(works-out-of-the-box / needs-manual-constraints / not-recommended-as-PM-host).

It is the single place an operator reads to answer *"can I safely run the
Cartopian PM on harness X, and if so how do I launch it?"* Every classification
here is faithful to the **as-shipped** Tier-1/2/3 behavior proven in Phases 01–03;
a fact-check test (`tests/containment/test_fr009_enforcement_matrix_consistency.py`)
asserts the matrix below never drifts from the shipped `_harness_tier` classifier.

> **Two related, non-interchangeable surfaces.** This document and the
> harness-level matrix in [`docs/COMPATIBILITY.md`](./COMPATIBILITY.md) are the
> **static** classification of each harness (one entry per harness, asset-driven).
> They are distinct from the per-`(harness, project)` **FR-008 operator-
> acknowledgment ledger** — the `COMPATIBILITY.md` file the launch gate writes into
> each *governed project root* (DEC-003), which records a revocable operator
> acknowledgment of running a still-`tier-3` harness on a specific project. The
> ledger is mutable and per-project; this matrix is the fixed, evidence-backed
> capability statement it is read against. **Cross-reference them; never merge
> them.** See [Tier 3](#tier-3--advisory--not-recommended-fail-closed-launch-gate)
> below for how the ledger gates a tier-3 launch.

---

## The three-tier containment model

Cartopian classifies, **pre-launch and purely from on-disk assets**, the highest
containment tier it can actually *enforce* on the configured PM harness. The
classifier is `cli/commands/_harness_tier.py` (`classify_harness_tier`); it is
**asset-driven, not name-driven** — a harness reaches `tier-1-2` only because both
of its asset files exist on disk, never because its name is recognised.

### Tier 1 — capability floor (`tier-1-2`, floor half)

The **capability floor** is a hard-coded launch profile that removes capability
from the harness before the model ever runs. It is the
`wrappers/bin/cartopian-<harness>-pm` launch profile and it must deliver three
guarantees (the DEC-001 / FR-002 floor criteria):

- **Fixed toolset** — the PM runtime exposes **only** the Cartopian MCP toolset
  and nothing else: no shell / process exec, no raw file write / edit, no raw
  read / glob / grep, no web / browse, no sub-agents or skills, and no
  non-Cartopian MCP servers.
- **No shell, no raw write/edit** — there is no `Bash` / `Write` / `Edit` (or
  per-harness analogue) tool in the session; in-runtime probes return
  `NO_SHELL_TOOL` / `WRITE_BLOCKED` with no file on disk.
- **Product repo / work roots off the reachable filesystem** — the runtime
  launches from an isolated, content-free cwd with no extra directory grants, so
  the product repo and the tool-repo work roots are not on its raw reachable
  filesystem.

The floor is a **launch-profile** guarantee realised through each harness's own
native flags / config (no hooks, no broker, no third-party dependency — NF-001).
It is **hard-coded and non-overridable**: each `-pm` wrapper *refuses* the flags
that would re-open the surface (e.g. claude `--dangerously-skip-permissions` /
`--add-dir`; codex `-s`/`--sandbox` / `-c`; gemini `-s` / `--include-directories`
/ `--yolo`), so the floor cannot be silently weakened from the command line.

A shared addition closes one cross-harness gap above the floor: the Cartopian MCP
server itself withholds the four config/registry **genesis tools**
(`generate_config`, `scaffold_project`, `register_project`, `unregister_project`)
whenever `CARTOPIAN_PM_CONTAINED=1` — withheld from `tools/list` **and** refused
fail-closed in `tools/call` (DEC-007, `mcp_server/server.py`
`CONTAINED_DENIED_TOOLS`). A contained PM keeps every read / lifecycle /
PM-authoring tool; uncontained use is unchanged (NF-004).

### Tier 2 — native-sandbox depth profile (`tier-1-2`, depth half)

The **depth profile** is a defense-in-depth layer **beneath** the floor: the
harness's **own** native OS sandbox (Apple Seatbelt / `sandbox-exec` on macOS;
Landlock+seccomp or a container runtime on Linux), driven by
`wrappers/etc/sandbox-<harness>-pm-depth.json` (Claude Code uses the shared
`wrappers/etc/sandbox-pm-depth.json`). Because the floor already removes the
write/shell tools, this layer is normally **inert** — but if the floor were ever
bypassed and a write/shell tool reappeared, the native sandbox still denies the
product-repo / work-root filesystem reach, process-exec effects, and (where the
mechanism allows) network. It is **config + native mechanism only — no bundled or
third-party sandbox** (NF-001), and is fail-closed (`fail_if_unavailable: true`,
`allow_bypass: false`).

A harness is classified **`tier-1-2` (constrained)** iff **both** the floor launch
profile **and** the native-sandbox depth profile exist on disk. Shipping those two
files is a drop-in promotion — no edit to the classifier (TASK-02-001 contract).

### Tier 3 — advisory / not-recommended (fail-closed launch gate)

A harness for which **one or both** assets are absent (or no harness resolves at
all) classifies **`tier-3` (advisory / unconstrainable)**: Cartopian cannot
enforce the capability floor, so it cannot hold the PM at Tier 1/2.

A `tier-3` row can be **transitional** (awaiting its promotion phase) or
**permanent** — recorded `not-recommended-as-PM-host` once a harness is proven
unpromotable with forcing evidence (cascade DEC-008; devin DEC-009).

Launching the PM on a `tier-3` harness is **fail-closed**: the FR-008 launch gate
**blocks** pending an **explicit, recorded operator acknowledgment** of the
unconstrained risk, persisted to the revocable per-project `COMPATIBILITY.md`
ledger (DEC-003), written only through the FR-003 mediated writer, and
acknowledged only via the **out-of-band** `bin/cartopian-ack-harness` entrypoint —
deliberately **not** a Cartopian subcommand, so the contained PM cannot
acknowledge its own risk. A revoked or mismatched record re-blocks. The
**preferred** posture is always to host the PM on a `tier-1-2` works-out-of-the-box
harness instead.

> **Note — "asset-detected `tier-1-2`" is not the same as "safe to recommend."**
> The classifier reports the tier Cartopian can *structurally* enforce from
> assets; the **recommendation bucket** additionally weighs live harness evidence.
> codex detects `tier-1-2` (both assets exist and the floor genuinely denies
> shell/write/exec) yet is bucketed **not-recommended-as-PM-host** because two
> residual surfaces leak past the floor (see [codex](#codex)). Tier ≠ bucket.

---

## Agent compatibility matrix

All five configured harnesses, bucketed and tied to the shipped `_harness_tier`
classification and the Phase-01..03 evidence / decision that backs each. The
**enforceable tier** column is the value `classify_harness_tier(<harness>)`
returns in this checkout; the consistency test pins each row to it.

| harness | classification | enforceable tier | evidence / decision |
| --- | --- | --- | --- |
| claude | works-out-of-the-box | tier-1-2 | DEC-001 (GO); `tests/wrappers/pm-floor` (incl. `green-genesis-*`), `pm-sandbox`, `pm-runtime`; genesis vector re-verified under DEC-007 (TASK-03-011) |
| gemini | works-out-of-the-box | tier-1-2 | TASK-03-002; `tests/wrappers/pm-gemini/evidence` (no read/web residual; `green-03-read` = `NO_READ_TOOL`) |
| codex | not-recommended-as-PM-host | tier-1-2 | TASK-03-001; `tests/wrappers/pm-codex/evidence`; asset-detected `tier-1-2` but **read (F1)** + **web (F1b)** forcing residuals — see [codex](#codex) |
| cascade | not-recommended-as-PM-host | tier-3 | DEC-008; `tests/wrappers/pm-cascade/` (`FINDINGS.md`, `evidence/cascade-tier-determination.txt`) — unpromotable, no floor/depth mechanism |
| devin | not-recommended-as-PM-host | tier-3 | DEC-009; `tests/wrappers/pm-devin/` (`FINDINGS.md`, `evidence/devin-tier-determination.txt`) — cloud-handoff escape; no verifiable floor+depth |

**Bucket definitions.**

- **works-out-of-the-box** — both assets ship, the floor genuinely withholds every
  prohibited capability **with no forcing residual**, and the native depth profile
  holds beneath it. Launch the `cartopian-<harness>-pm` wrapper; no operator
  action beyond using it. *(claude, gemini.)*
- **needs-manual-constraints** — Cartopian can detect/enforce `tier-1-2` from
  assets, but a complete guarantee requires an operator-supplied control outside
  Cartopian's assets (e.g. a network-egress restriction). No configured harness
  currently lands here; the bucket exists so a future harness whose only gap is an
  external control is not mis-bucketed as not-recommended.
- **not-recommended-as-PM-host** — either a forcing residual leaks past an
  otherwise-`tier-1-2` floor (codex), or the harness is `tier-3` unpromotable
  (cascade, devin). Run only under the FR-008 advisory gate with a recorded
  acknowledgment, or — preferred — host the PM on a works-out-of-the-box harness.

For the full per-harness facet tables, red→green probe evidence, and residual
write-ups, see [`docs/COMPATIBILITY.md`](./COMPATIBILITY.md).

---

## Per-harness hardening & recommended launch mode

### claude

- **Bucket / tier:** works-out-of-the-box / `tier-1-2`.
- **Recommended mode:** launch the contained PM with **`cartopian-claude-pm`**
  (interactive). It hard-codes the DEC-001 floor:
  `claude --tools "" --strict-mcp-config --mcp-config <cartopian-only.json>
  --allowedTools "mcp__cartopian" --disable-slash-commands`, from the isolated
  `var/pm-surface` cwd with **no** `--add-dir`.
- **Tier-1 floor:** `--tools ""` removes all built-ins; `--strict-mcp-config` +
  the cartopian-only MCP config expose only the Cartopian server; `--allowedTools
  "mcp__cartopian"` keeps that toolset callable.
- **Tier-2 depth:** `--settings wrappers/etc/sandbox-pm-depth.json` drives Claude
  Code's own seatbelt/bubblewrap sandbox to `denyRead`/`denyWrite` the product
  repo + work roots, `failIfUnavailable: true`, `allowUnsandboxedCommands: false`.
- **Hardening rules:** never pass `--dangerously-skip-permissions`, `--add-dir`,
  or a permissive `--permission-mode` — the wrapper refuses them. Re-verify the
  tool inventory if the Claude Code version changes materially (DEC-001 pins
  evidence to a specific version).

### gemini

- **Bucket / tier:** works-out-of-the-box / `tier-1-2`.
- **Recommended mode:** launch the contained PM with **`cartopian-gemini-pm`**
  (interactive).
- **Tier-1 floor:** a generated, isolated **system** settings file
  (`GEMINI_CLI_SYSTEM_SETTINGS_PATH`, gemini's highest-precedence layer) whose
  `tools.exclude` removes the full built-in tool list — including
  `run_shell_command`, `write_file`/`read_file`/`replace`, web tools, **and the
  built-in `list_mcp_resources` / `read_mcp_resource`** — sets
  `mcp.allowed=["cartopian"]`, registers only the Cartopian server, and is
  reinforced by `--allowed-mcp-server-names cartopian`. Unlike codex, gemini *can*
  remove its MCP-resource read tools, so the floor reaches a genuine
  no-read-tool state (no read residual); its web tools are client-side built-ins
  removed here too (no web residual).
- **Tier-2 depth:** `security.toolSandboxing=true` (gemini's **per-tool** Seatbelt
  sandbox) + a write-restricting `SEATBELT_PROFILE` — **not** the whole-process
  `-s` sandbox, which would starve gemini's own API call and the out-of-process
  MCP server.
- **Hardening rules:** never pass `-s`/`--sandbox`, `--include-directories`,
  `--allowed-tools`, `--policy`/`--admin-policy`, `-e`/`--extensions`,
  `--approval-mode`, or `-y`/`--yolo` — the wrapper refuses them and fails closed
  if the depth profile is absent.

### codex

- **Bucket / tier:** not-recommended-as-PM-host / `tier-1-2` *(asset-detected)*.
- **Recommended mode:** if codex must host the PM, launch the contained
  **`cartopian-codex-pm`** (interactive — a documented upstream limitation,
  openai/codex#16685, auto-cancels MCP calls under non-interactive `codex exec`)
  **and** run it under the FR-008 advisory acknowledgment, because two surfaces
  leak past the floor.
- **Tier-1 floor:** an isolated `CODEX_HOME` config sets
  `features.shell_tool=false` + `features.unified_exec=false` (removes shell / PTY
  exec), disables web/view-image/plugins/apps/browser/computer/multi-agent/hooks,
  and registers only the Cartopian MCP server.
- **Tier-2 depth:** `codex -s read-only` drives codex's own Seatbelt / Landlock
  sandbox to deny all writes, exec effects, and per-command network;
  `approval_policy=never` closes the escalation escape hatch (a sandbox-denied
  command is never re-run un-sandboxed).
- **Forcing residuals (why not-recommended):**
  - **F1 — read denial is not achievable.** codex always exposes the built-in
    `list_mcp_resources` / `read_mcp_resource` tools (no codex-side toggle), so a
    contained codex PM reads every registered project's Cartopian resources
    (cross-project read). Closable only by a *shared* change — gating the
    Cartopian MCP server's `resources` capability under `CARTOPIAN_PM_CONTAINED=1`
    — deferred as out of TASK-03-001 scope.
  - **F1b — web/browse denial is not achievable.** codex's `web_search` is a
    **server-side** Responses tool; the OS sandbox's `deny_network` cannot block
    it and config does not reliably remove it, leaving an uncontrolled
    browse/exfiltration surface. Closing it needs an upstream codex control or an
    egress restriction outside codex.
  - The earlier MCP genesis-tool config-write vector is **CLOSED** by the shared
    DEC-007 floor (no longer an open exposure).
- **Hardening rules:** the wrapper refuses
  `--dangerously-bypass-approvals-and-sandbox`, `-s`/`--sandbox`, `--add-dir`,
  `-c`/`--config`, `--ignore-user-config`, `-p`/`--profile`, `--enable`,
  `--dangerously-bypass-hook-trust`. The residuals are **not** closable from the
  command line — treat codex as advisory-gated until F1/F1b are addressed.

### cascade

- **Bucket / tier:** not-recommended-as-PM-host / `tier-3` *(unpromotable)*.
- **Recommended mode:** **do not host the PM on cascade.** No
  `cartopian-cascade-pm` floor or `sandbox-cascade-pm-depth.json` depth profile
  exists, and none can be honestly built — so the classifier reports `tier-3` by
  asset absence. A cascade-hosted PM can only run under the FR-008 Tier-3 advisory
  gate (recorded acknowledgment of the unconstrained risk); prefer a different
  host.
- **Why unpromotable (DEC-008):** cascade is the agent embedded in the Windsurf
  **Electron IDE** — (F-C1) no first-party scriptable headless runtime to wrap
  (the only headless options are third-party and barred by NF-001); (F-C2) no
  mechanism to remove its built-in edit/write/shell tools or scope it to one MCP
  server (per-tool toggling is an interactive GUI panel); (F-C3) no native OS
  sandbox — only an application-layer allow/deny-list string matcher, with cascade
  and its MCP servers at full user privilege. Shipping placeholder assets is
  rejected: it would make the classifier falsely report `tier-1-2`.

### devin

- **Bucket / tier:** not-recommended-as-PM-host / `tier-3` *(unpromotable today)*.
- **Recommended mode:** **do not host the PM on devin.** No `cartopian-devin-pm`
  floor or `sandbox-devin-pm-depth.json` depth profile is shipped, so the
  classifier reports `tier-3` by asset absence. A devin-hosted PM can only run
  under the FR-008 Tier-3 advisory gate; prefer a different host. *(devin remains
  perfectly usable as a coder/reviewer **assignee** via `cartopian-devin` — this
  decision is about PM-host containment only.)*
- **Why not-recommended (DEC-009):** unlike cascade, devin ships *partial* local
  mechanisms (a config `permissions` allow/deny/ask system; a fail-closed
  OS-level `--sandbox`) that cannot be composed into a verifiable, non-escapable
  Tier-1+2. Five forcing facets each independently block the floor-beneath-sandbox
  shape — (F-D1, dominant) the cloud `/handoff` + cloud subagents create a cloud
  Devin session "with its own computer" outside any local sandbox or floor, with
  no config key to disable it; (F-D2) no capability-floor mechanism that removes
  built-in tools or scopes to one MCP server; (F-D3) the floor and the `--sandbox`
  are mutually exclusive (`--sandbox` auto-selects the auto-approving `autonomous`
  mode); (F-D4) no non-overridable settings-injection path; (F-D5) no offline
  contained runtime to capture in-runtime evidence. Shipping floor+depth assets
  would flip the classifier to `tier-1-2` with zero guaranteeing evidence.
- **What would change this:** upstream devin controls to hard-disable the cloud
  handoff/subagents, remove built-in tools / scope to one MCP server, layer the OS
  sandbox beneath a non-auto-approving floor, and a non-overridable settings
  injection path — at which point live evidence could be captured and the assets
  honestly shipped.

---

## Consistency / fact-check (no live harness needed)

The matrix above is pinned to the shipped behavior by
**`tests/containment/test_fr009_enforcement_matrix_consistency.py`**. It asserts
that every configured harness (claude, codex, gemini, cascade, devin) appears in
this doc's matrix with a tier matching what `classify_harness_tier(<harness>)`
actually reports — the expected tier is read from the **live classifier**, never
hard-coded — and that the three-tier model and per-harness hardening sections are
present. The same parse runs against `docs/COMPATIBILITY.md`, so an asset change
that flips a tier turns both docs red together.

Run it directly:

```sh
python3 -m pytest -q tests/containment/test_fr009_enforcement_matrix_consistency.py
```

It is also wired into the consolidated containment suite
(`tests/containment/run-containment-suite.sh`), which stays green alongside the
per-harness promotion pins.
