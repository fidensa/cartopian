# Skill: Migrate Project

Bring a Cartopian project's internal protocol-schema version up to the schema shipped by the installed Cartopian application by applying the applicable `protocol/CHANGELOG.md` migration entries. The project schema version is not the Cartopian application release version. Migration is **PM-owned orchestration**: the PM drives it, performs the config and markdown edits it can mediate, and dispatches or surfaces the steps it cannot. It runs only on the operator's explicit request or approval — never proactively, and never asks the operator to edit the version marker manually.

**Output:** the project's `[project].protocol_version` marker advanced to the shipped version (or as far as every applicable entry could be fully applied and validated), with each entry's changes landed through mediated tooling.

---

## Prerequisites

- You are acting as the PM for a registered project (see `use-cartopian.md`). You know the project's absolute path.
- The installed `~/.cartopian/CHANGELOG.md` is current (run `check for updates` first if the install root may be stale — a project cannot migrate past the protocol the install ships).
- The operator has asked to migrate, or has approved migrating a project surfaced as stale (a `config-schema migration required` blocker from `cartopian next-action` / `cartopian plan-audit`).

Do not begin without that approval. Migration mutates config and artifacts; it is an operator-gated, PM-executed process.

---

## Step 1 — Determine what applies

1. Read the project's current marker: `<project-root>/cartopian.toml` `[project].protocol_version` (the canonical, only authoritative location — it does not live in `STATE.md`). Treat an absent/missing value as **unset**.
2. Read `~/.cartopian/CHANGELOG.md`. Under `## Entries`, each `### vX.Y.Z` block is a self-contained migration contract with an **applies-when precondition**.
3. Select every entry whose applies-when matches: an entry applies when the marker is unset, missing, or lexically **less than** that entry's version. An **unset marker means every entry applies.**
4. Order the selected entries **ascending** by version (oldest first). You apply them in that order; the marker advances one entry at a time.
5. If nothing applies (the marker already equals the shipped version), report "already current" and stop — this is a no-op.

## Step 2 — Apply each entry in order

For each applicable entry, oldest first, walk its **Agent-followable migration steps** and classify every step:

- **PM-mediated (do it yourself):**
  - Config edits — reshaping `[roles]`, adding `[project].work_roots`, setting `[automation]` keys, authoring per-machine `cartopian.local.toml` work-root mappings, and the marker bump — go through `cartopian update-config`:
    - scalar/list/role/handoff keys: `cartopian update-config <project-root> --set … --set-role … --set-role-grants … --set-handoff …`
    - per-machine mappings: `cartopian update-config <project-root> --local --set-work-root <name>=<absolute-path>`
  - Registry actions (`cartopian register-project`) and any markdown authoring the mediated writers cover.
  - Preserve each entry's operator-choice points. For example, v0.4.0's initiation opt-in is an explicit operator decision: only set `automation.initiation = "auto"` if the operator chooses "automatically start ready work"; a migration performed without that choice leaves the key unset.
- **Not PM-mediated (dispatch or surface):** the PM has no mediated operation for arbitrary file surgery. These steps must be dispatched to a role that can perform them (a handoff), or surfaced to the operator as an explicit, bounded action. They include:
  - File renames (e.g. v0.2.0's `ENGINEERING.md → STANDARDS.md`).
  - Line-anchored header substitutions across existing `tasks/` / `reviews/` / `reports/` files (e.g. v0.2.0's `Test gate:→Evidence gate:`, v0.3.0's `Repo subpath:→Work root:`) — the structured writers regenerate whole artifacts from inputs; they do not do surgical header swaps on arbitrary existing files.
  - Wrapper / launcher edits (v0.3.0).

  Name each such step precisely to the operator and either dispatch it via handoff or get it done, then confirm it landed. Never silently skip it and never claim to have executed a step you delegated.

Do not raw-edit `cartopian.toml` / `cartopian.local.toml` — the harness denies structured raw edits to config, and `update-config` is the only edit path.

## Step 3 — Validate, then bump the marker (conditional)

The marker bump is the **last** step of each entry and is conditional:

1. Run the entry's **post-migration validation hint** (the grep/file/command checks it documents).
2. Bump the marker **only after every step for that entry — including any delegated step — has completed and its validation passes**:
   `cartopian update-config <project-root> --set project.protocol_version=<entry-version>`
3. If any step is still pending (a delegated file edit not yet confirmed, an operator decision not yet made), **do not bump the marker.** Stop, report exactly what is outstanding, and resume when it is resolved. A half-applied entry must not be recorded as done.

Then move to the next entry and repeat Steps 2–3. Because each `update-config` call is idempotent and each entry's steps are idempotent, re-running the whole flow after an interruption is safe — completed steps are no-ops.

## Step 4 — Summarize

Report:

- The starting and ending internal project protocol-schema versions, labeled explicitly as distinct from the Cartopian application version.
- Each entry applied, and for each: the PM-mediated changes made and any steps that were delegated/surfaced (and their status).
- Any entry left partially applied and why (which step is outstanding), if the run stopped short of the shipped version.
- The validation checks that passed.

Route any follow-up items (a delegated step the operator deferred, tech/process debt noticed en route) to `BACKLOG.md` per `protocol/CONVENTIONS.md`, not into `STATE.md`.
