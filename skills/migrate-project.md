# Skill: Migrate Project

Bring a Cartopian project's internal protocol-schema version up to the schema shipped by the installed Cartopian application. The project schema version is not the Cartopian application release version. Migration is **PM-owned orchestration** and has one explicit authority bridge: `cartopian migrate-config` is the sole planner/executor for configuration compatibility and `[project].project_schema_version`, while `protocol/CHANGELOG.md` plus `cartopian apply-migration-entry` remain authoritative only for the older non-configuration filesystem actions. The configuration planner mechanically cross-validates both shipped registries against the changelog and fails closed if they diverge. Judgment-dependent transforms remain explicit PM actions. Migration runs only on the operator's explicit request or approval — never proactively — and never asks the operator to edit the version marker manually.

**Output:** the project's `[project].project_schema_version` marker advanced to the shipped version only by `migrate-config` after applicable configuration and filesystem work validates.

---

## Prerequisites

- You are acting as the PM for a registered project (see `use-cartopian.md`). You know the project's absolute path.
- The installed `~/.cartopian/CHANGELOG.md` is current (run `check for updates` first if the install root may be stale — a project cannot migrate past the protocol the install ships).
- The operator has asked to migrate, or has approved migrating a project surfaced as stale (a `config-schema migration required` blocker from `cartopian next-action` / `cartopian plan-audit`).

Do not begin without that approval. Migration mutates config and artifacts; it is an operator-gated, PM-executed process.

---

## Step 1 — Determine what applies

1. Run `cartopian migrate-config <project-root>` and read its `detected_schema_version`. This is the compatibility interpretation of `<project-root>/cartopian.toml` `[project].project_schema_version`; the historical `protocol_version` name is a migration-source alias only.
2. Read `~/.cartopian/CHANGELOG.md`. Under `## Entries`, each `### vX.Y.Z` block is a self-contained migration contract with an **applies-when precondition**.
3. Select every entry whose applies-when matches: an entry applies when the marker is unset, missing, or lexically **less than** that entry's version. An **unset marker means every entry applies.**
4. Order the selected entries **ascending** by version (oldest first). Apply their non-configuration filesystem actions in that order without changing the marker between entries.
5. If nothing applies and `migrate-config` reports a canonical no-op, report "already current" and stop — this is a no-op.

## Step 2 — Apply each entry in order

For each applicable entry, oldest first, walk its **Agent-followable migration steps** and classify every step:

- **PM-mediated (do it yourself):**
  - Operator decisions named by a `migrate-config` pending record are authored through `cartopian update-config`; deterministic reshaping, scope placement, equivalence validation, comment preservation, checkpointing, and the marker update remain owned by `migrate-config`:
    - scalar/list/role/launch keys: `cartopian update-config <project-root> --set … --set-role … --set-role-grants … --set-role-launch … --set-role-auto-launch …`
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

  During the v0.9 transition, an active task already in `in-review` can retain a
  review prompt generated under the prior schema. Once exact request evidence
  resolves, the expected audit result is `stale-request-context`. Regenerate
  that existing prompt through the ordinary mediated review-prompt writer:

  ```sh
  cartopian write-prompt <project-root> --prompt-id PROMPT-NN-NNN --review-kind task-closure --task <absolute-in-review-task-path> --content-file <review-prompt-body>
  cartopian plan-audit <project-root>
  ```

  This is prompt regeneration inside the existing review, not a new review
  stage. Do not rewrite historical review files and do not fabricate request
  evidence.

Do not raw-edit `cartopian.toml` / `cartopian.local.toml` — the harness denies structured raw edits to config, and `update-config` is the only edit path.

## Step 3 — Validate, then let the configuration executor update the marker

The marker update is the **last** configuration-migration step and is conditional:

1. Run the entry's **post-migration validation hint** (the grep/file/command checks it documents). For a registry-backed entry, require `apply-migration-entry` to report `status: "complete"` with an empty `pending_actions` list.
2. Run `cartopian migrate-config <project-root>` again. If its plan is executable and every applicable filesystem action is complete, run `cartopian migrate-config <project-root> --apply`. This executor performs the only authorized marker update after semantic equivalence and all prior configuration writes validate.
3. If either executor reports a pending/refused state, **do not write the marker through `update-config`.** Stop, report exactly what is outstanding, and resume when it is resolved. A half-applied migration must not be recorded as done.

Because each filesystem entry and the configuration executor are idempotent, re-running the whole flow after an interruption is safe: evidenced filesystem steps are no-ops, configuration steps resume from bounded in-progress evidence, and successful configuration completion removes that checkpoint.

## Step 4 — Summarize

Report:

- The starting and ending internal project protocol-schema versions, labeled explicitly as distinct from the Cartopian application version.
- Each entry applied, and for each: the config/authoring changes made, the deterministic migration operations applied, and any judgment-dependent actions resolved.
- Any entry left partially applied and why (which step is outstanding), if the run stopped short of the shipped version.
- The validation checks that passed.

Route any follow-up items (a delegated step the operator deferred, tech/process debt noticed en route) to `BACKLOG.md` per `protocol/CONVENTIONS.md`, not into `STATE.md`.
