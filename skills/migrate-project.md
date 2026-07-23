# Skill: Migrate Project

Bring a Cartopian project's internal protocol-schema version up to the schema shipped by the installed Cartopian application by applying the applicable `protocol/CHANGELOG.md` migration entries. The project schema version is not the Cartopian application release version. Migration is **PM-owned orchestration**: the PM drives it, performs config edits through `cartopian update-config`, and performs each shipped deterministic filesystem transform through `cartopian apply-migration-entry`. Judgment-dependent transforms remain explicit PM actions. Migration runs only on the operator's explicit request or approval — never proactively — and never asks the operator to edit the version marker manually.

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
- **Tool-owned deterministic filesystem actions (do it yourself):** for an entry that declares a registered filesystem migration, run:

  ```sh
  cartopian apply-migration-entry <project-root> <entry-version>
  ```

  The command accepts no caller-selected path, content, replacement text, or executable command. Its shipped registry owns the exact action set. It performs only allowlisted project-local renames, line-anchored substitutions, declared wrapper changes, and exact artifact retirements. It rejects path escapes, symlinks, hardlinks, special files, unexpected content, collisions, and unknown entries; successful writes and retirements carry migration provenance. A completed or already-applied action is idempotent.

  The current registry covers the deterministic filesystem actions in v0.2.0, v0.3.0, and v0.6.0. Run it once for each of those entries when that entry applies. Do not invoke it for entries that contain only config or operator-choice steps.

- **Judgment-dependent migration actions (resolve as PM):** `apply-migration-entry` returns a structured `pending_actions` list and writes nothing when a safe transformation requires interpretation. Resolve each item through the ordinary PM-mediated surfaces, then re-run the same entry. Examples include mapping a legacy v0.3.0 path fragment to declared work-root names, reviewing customized wrappers for a project-root launch cwd, and salvaging project-specific metadata from a substantive pre-v0.6.0 `CONVENTIONS.md` into `STANDARDS.md` or durable decisions. Never silently discard content, invent a mapping, or bump the marker while any pending action remains.

  A customized wrapper is handled with the same hash-pinned review discipline. The first application records a pending receipt for each exact wrapper file. If the wrapper is already conforming, persist that review with `cartopian write-decision`; if it needs a judgment-dependent edit, dispatch the bounded wrapper update and then persist the review decision. Re-run the entry without changing the reviewed bytes after that decision. A changed wrapper gets a new pending receipt and requires review of its new bytes; the executor never treats a decision about an older version as approval of a replacement.

  For a substantive retired `CONVENTIONS.md`, the first application records a hash-pinned pending receipt and leaves the file unchanged. Preserve appropriate metadata with `cartopian write-standards`, or use `cartopian write-decision` to record the PM/operator determination that nothing should be retained. Then re-run `apply-migration-entry v0.6.0` without editing `CONVENTIONS.md`; the executor requires the same reviewed bytes plus the later mediated standards/decision record before it retires the exact file. Governance rules are not salvaged into project metadata.

Do not raw-edit `cartopian.toml` / `cartopian.local.toml` — the harness denies structured raw edits to config, and `update-config` is the only edit path.

## Step 3 — Validate, then bump the marker (conditional)

The marker bump is the **last** step of each entry and is conditional:

1. Run the entry's **post-migration validation hint** (the grep/file/command checks it documents). For a registry-backed entry, require `apply-migration-entry` to report `status: "complete"` with an empty `pending_actions` list.
2. Bump the marker **only after every step for that entry has completed and its validation passes**:
   `cartopian update-config <project-root> --set project.protocol_version=<entry-version>`
3. If any step is still pending (a structured pending migration action or an operator decision not yet made), **do not bump the marker.** Stop, report exactly what is outstanding, and resume when it is resolved. A half-applied entry must not be recorded as done.

Then move to the next entry and repeat Steps 2–3. Because each `update-config` call is idempotent and each entry's steps are idempotent, re-running the whole flow after an interruption is safe — completed steps are no-ops.

## Step 4 — Summarize

Report:

- The starting and ending internal project protocol-schema versions, labeled explicitly as distinct from the Cartopian application version.
- Each entry applied, and for each: the config/authoring changes made, the deterministic migration operations applied, and any judgment-dependent actions resolved.
- Any entry left partially applied and why (which step is outstanding), if the run stopped short of the shipped version.
- The validation checks that passed.

Route any follow-up items (a delegated step the operator deferred, tech/process debt noticed en route) to `BACKLOG.md` per `protocol/CONVENTIONS.md`, not into `STATE.md`.
