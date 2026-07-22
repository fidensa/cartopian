# Cartopian Protocol Changelog

This file is the durable, agent-followable record of every protocol-breaking change to the Cartopian protocol. Each entry below the header documents one breaking change and the concrete migration steps required to bring an older project up to the new protocol version.

Here, **protocol version** means the internal schema/lifecycle version recorded for each governed project. It is separate from the installed Cartopian application's release version. Operators normally approve a migration choice; the PM agent reads and updates this marker and should not ask the operator to edit version fields manually.

The operator *approves* a project migration; the PM then *executes* its steps as PM-owned orchestration (`skills/migrate-project.md`) — doing config edits through `cartopian update-config` and dispatching or surfacing the steps it cannot mediate. Authoring of this file is owned by maintainers and ships with each release. The installed copy lives at `~/.cartopian/CHANGELOG.md` and is replaced on upgrade.

## Per-entry schema

Every entry below the header is a self-contained migration contract. An entry shall include each of the following fields:

- **Protocol version** — a monotonically-increasing identifier (e.g., `v0.2.0` or a date stamp) that names the version the entry advances the protocol to. The entry advances projects from the prior version to this one.
- **One-line summary** — a single-sentence description of the change suitable for a release-note bullet.
- **Breakage description** — what stopped working in older projects: which files, fields, or behaviors are no longer valid under the new protocol version.
- **Applies-when precondition** — an explicit, agent-evaluable rule for deciding whether this entry applies to a given project (e.g., "applies when the project's `protocol_version` is less than this entry's version").
- **Agent-followable migration steps** — the concrete file and text changes an agent performs to bring the project into conformance. Steps must be specific enough that a competent agent can execute them without further clarification.
- **Idempotence guarantee** — the migration steps shall be idempotent: re-applying them to an already-migrated project must be a no-op. The post-migration validation hint (below) is the safety check used before and after each application.
- **Post-migration validation hint** — how the agent confirms the migration succeeded (e.g., a grep, a file check, a command to run).

## Prepend-only rule

New entries are inserted at the **top** of the entries section below, immediately under the `## Entries` heading. Existing entries are never edited, reordered, or removed after they land. This makes the file a stable, append-only history that older projects can rely on for migration.

## `[project] protocol_version` marker

Every Cartopian project's `cartopian.toml` carries a `[project] protocol_version` field. The applies-when precondition on each entry is evaluated against this marker to decide applicability during rollup or in-place migration.

- Projects updated in-place during a cycle bump the marker as each applicable entry is applied.
- Projects rolled up after a cycle bump the marker once, to the latest entry's version, after every applicable entry has been applied successfully.
- Newly-scaffolded projects start at the current protocol version (the version of the topmost entry below).

## Entries

### v0.6.0 — Project-level conventions retired; STANDARDS.md finalized as project metadata

- **Protocol version:** `v0.6.0`
- **One-line summary:** Retires the project-level `CONVENTIONS.md` so the tool-owned `protocol/CONVENTIONS.md` is the only conventions layer, and finalizes `STANDARDS.md` as the sole project-metadata artifact (chosen tools or stack, working standards, and cycle constraints).

#### Breakage description

There is exactly one governance contract: the tool-owned `protocol/CONVENTIONS.md`, read through `cartopian://protocol/CONVENTIONS`. A project file must not override or shadow it.

1. The project-root `CONVENTIONS.md` file is retired. No shipped surface reads, seeds, preserves, or writes it: `scaffold-project` does not create it, and its rerun guard treats one at the project root as a foreign file; `reset-plan` neither clears nor reseeds it; plan closeout has no carry-forward for it; `archive-plan` does not snapshot it; and no mediated writer targets it (the former `write-conventions` command is removed). A project still carrying the file is not conformant until the file is retired.
2. `STANDARDS.md` is project metadata — the chosen tools or stack, the working standards that apply to the project, and the constraints that bound each cycle's work — not a governance contract, and not limited to software engineering. It remains the sole project-metadata artifact and keeps its existing carry-forward semantics at plan closeout.

Body prose inside existing project artifacts (plan narratives, decision rationale, review notes, etc.) that mentions a project-level conventions file in passing is **not** part of the breakage contract; only the project-root file itself breaks.

#### Applies-when precondition

Applies when the project's `cartopian.toml` `[project] protocol_version` is unset, missing, or lexically less than `v0.6.0`. Projects already at `v0.6.0` (or any later entry's version) are skipped.

#### Agent-followable migration steps

Run these against the project root in order.

1. **Retire the project-level `CONVENTIONS.md`, if present.** If `<project-root>/CONVENTIONS.md` does not exist, no action. If it exists, it is superseded by the tool-owned `protocol/CONVENTIONS.md`; name it to the operator as superseded, then:
   - If the file carries project-specific content worth keeping, fold the metadata (tools or stack, working standards, cycle constraints) into `STANDARDS.md` via `cartopian write-standards`, and record any durable project decision via `cartopian write-decision`. Governance rules are never salvaged into `STANDARDS.md` — the protocol document already owns them, and a project file must not override or shadow it.
   - Delete `<project-root>/CONVENTIONS.md`. File deletion is not a PM-mediated operation, so dispatch it or surface it to the operator as an explicit, bounded action (`skills/migrate-project.md`). If the operator wants the old file preserved verbatim, it moves outside the project root; the project root must end this step without a `CONVENTIONS.md`.
2. **Bump the marker.** Set `[project] protocol_version = "v0.6.0"` in `<project-root>/cartopian.toml`, only after step 1 leaves no `CONVENTIONS.md` at the project root.

#### Idempotence guarantee

Step 1's precondition is the file's existence: once the file is removed, re-application finds nothing to retire and is a no-op (and re-running the salvage writers with unchanged content is a byte-identical mediated write). Step 2 is a fixed-value assignment; re-applying it is a no-op.

#### Post-migration validation hint

```sh
PROJECT_ROOT=<project-root>

# 1. No project-level conventions file remains (step 1).
test ! -e "$PROJECT_ROOT/CONVENTIONS.md"
# expected: exit status 0

# 2. protocol_version marker is v0.6.0 (or a later entry's version)
#    (step 2).
grep -E '^protocol_version *= *"v0\.6\.0"' \
  "$PROJECT_ROOT/cartopian.toml"
# expected: one match (or a later version line if a later entry
# has been applied on top)
```

### v0.5.0 — Explicit review policy and domain-neutral completion evidence

- **Protocol version:** `v0.5.0`
- **One-line summary:** Separates review policy from arbitrary role names, lets projects independently require or disable planning and task-closure reviews (including overriding global policy), and gives task reports a domain-neutral completion-evidence shape.

#### Breakage description

Review behavior no longer follows the presence of a role literally named `reviewer`. Projects declare the two review loops explicitly under `[reviews]`, and each required loop names the ordinary role assigned to it. Until migration writes that policy, a pre-v0.5.0 project with the conventional `reviewer` role retains both formerly mandatory loops as a version-scoped compatibility rule; this is not role-name inference for v0.5.0+ projects. New task reports prefer `## Completion evidence` and `## Ready to close`; existing `## Files changed` / `## Deliverable` evidence and `## Ready for review` remain accepted compatibility forms. Existing reports remain valid.

#### Applies-when precondition

Applies when the project's `cartopian.toml` `[project] protocol_version` is unset, missing, or lexically less than `v0.5.0`. Projects already at `v0.5.0` or later are skipped.

#### Agent-followable migration steps

1. Offer the operator one review-policy choice: no reviews, planning only, task closure only, or both planning and task closure. Do not infer the choice from role descriptions, capability presets, or review artifacts.
2. If the operator chooses a required loop, ask which existing resolved role performs it. Any role name is valid. For the pre-v0.5.0 projects that use the conventional `reviewer` role, that role is the compatibility assignment.
3. Write the selected policy with `cartopian update-config`, using `reviews.planning`, `reviews.planning_role`, `reviews.task_closure`, and `reviews.task_role`. An unattended migration preserves existing behavior by setting both loops to `required` and assigning the existing `reviewer` role; it never silently removes review. Before this step is completed, lifecycle commands apply the same preservation behavior to a pre-v0.5.0 project carrying that conventional role.
4. Configure launch behavior independently: set `[handoffs.<role>].auto_start_tasks` for task-scoped handoffs and `[handoffs.<role>].auto_start_reviews` for planning-review handoffs. Neither key enables review policy. For an older handoff block, preserve its behavior by mapping legacy `auto_start` to `auto_start_tasks`; map legacy `auto_start = true` plus `planning_reviews = true` to `auto_start_reviews = true`. The resolver accepts the legacy inputs while migration is pending.
5. Set `[project] protocol_version = "v0.5.0"` only after `cartopian resolve-config` validates the effective review policy and assignments.

#### Idempotence guarantee

Explicit `[reviews]` values are authoritative on re-application. A migration never replaces an existing valid choice, and setting the marker or a selected value to its current value is a no-op.

#### Post-migration validation hint

`cartopian resolve-config <project-path>` exits 0 and emits independent `reviews.planning` and `reviews.task_closure` objects. Every `required` object names a declared role; every `off` object emits a null effective role. A project may override globally required review by setting the corresponding project mode to `off`. `grep protocol_version <project-root>/cartopian.toml` shows `v0.5.0`.

### v0.4.0 — Execution initiation is operator-gated (`[automation] initiation`)

- **Protocol version:** `v0.4.0`
- **One-line summary:** Separates execution initiation from deterministic task selection: a run now begins only from an operator execution directive or the explicit `[automation] initiation = "auto"` opt-in, with `initiation = "operator"` as the protocol default.

#### Breakage description

No file, field, or naming form becomes invalid; the breakage is behavioral, at the session-orientation surface.

1. Under v0.3.0 conventions, session startup and a populated open queue were treated as authorization to start the next sequential task ("starts the next sequential task ... without asking"), so `confirmation = "until-blocked"` with `auto_start = true` produced fully hands-off execution with no dedicated opt-in for the initiation step itself. Under v0.4.0, deterministic selection answers only *which task would run next*; execution begins only from an operator execution directive ("continue", "resume", "start working", "run the next task") or from `[automation] initiation = "auto"` (`protocol/CONVENTIONS.md § Task Execution Order`).
2. Operator requests are classified by intent (`protocol/CONVENTIONS.md § Request Intent`): informational requests ("what's next?", "check `STATE.md`") are read-only and never initiate execution; scoped directives ("generate PHASE-04's tasks") authorize only the named operation and, under the default `initiation = "operator"`, end with report-and-stop.
3. Projects that relied on the old hands-off initiation behavior stop and wait for an execution directive at session startup and after scoped directives until `[automation] initiation = "auto"` is set.

#### Applies-when precondition

Applies when the project's `cartopian.toml` `[project] protocol_version` is unset, missing, or lexically less than `v0.4.0`. Projects already at `v0.4.0` (or any later entry's version) are skipped.

#### Agent-followable migration steps

Run these against the project root in order.

1. **Offer the operator the one-time initiation choice — never choose silently.** If `[automation].initiation` is already present in the project or global config, keep it (the choice was already made; do not re-ask). Otherwise ask which behavior this project should have:
   - **"Wait for me to start work" (recommended default)** — no config change required; the resolved default is `initiation = "operator"`.
   - **"Automatically start ready work"** — add `initiation = "auto"` to the `[automation]` table in `<project-root>/cartopian.toml` (create the table if absent). Write `"auto"` only on the operator's explicit selection; a migration performed without operator input must leave the key unset.
2. **Bump the marker.** Set `[project] protocol_version = "v0.4.0"` in `<project-root>/cartopian.toml`.

#### Idempotence guarantee

Both steps are no-ops on re-application: step 1 skips whenever `initiation` is already present (and "no config change" is inherently repeatable); step 2 is a fixed-value assignment.

#### Post-migration validation hint

`cartopian resolve-config <project-path>` exits 0 and emits an `"automation"` object whose `"initiation"` is `"operator"` or `"auto"`, with no `[validation]` initiation warning on stderr; `grep protocol_version <project-root>/cartopian.toml` shows `v0.4.0`.

### v0.3.0 — Registry-only selection, project-root launch cwd, Work root field

- **Protocol version:** `v0.3.0`
- **One-line summary:** Replaces the `projects/`-directory-scan project-selection model with registry-only selection (FR-003 / AR-11), retargets the assignee-CLI launch cwd from the parent-of-workspace-root to the cartopian project root (FR-012), renames the task-file `Repo subpath:` header to `Work root:` with name-only multi-valued optional semantics (DEC-006), and codifies the work-root access model (DEC-003, DEC-004, DEC-005).

#### Breakage description

Older projects no longer match the new protocol surface in four specific tool-surface places. Body prose inside project artifacts (plan narratives, decision rationale, review notes, etc.) that mentions the old wording in passing is **not** part of the breakage contract; only the load-bearing tool surfaces below break.

1. The `projects/`-directory-scan project-selection model is retired. Skills and the PM no longer enumerate child directories under a `projects/` directory or infer the current project from cwd. Project selection resolves through the FR-003 registry only (`cartopian discover-projects`). A workspace that relied on its `projects/<project-id>/` layout for implicit selection is no longer discoverable to the new surfaces until each project is registered.
2. The parent-of-workspace-root launch-cwd rule is retired. Assignee CLIs now launch with cwd set to the cartopian project root (the registered absolute path). Wrappers that derived launch cwd by walking up from a `<workspace>/projects/<project-id>/...` prompt path no longer match the new contract.
3. The task-file `Repo subpath:` header is retired in favor of `Work root:`. Cardinality and semantics change: `Repo subpath:` was a single path fragment resolved against the launch cwd; `Work root:` is a comma-separated list of **names** drawn from the project's `[project].work_roots`, fully optional, with no path form permitted. Tasks that still carry `Repo subpath:` at the line anchor are not recognized by the new field-schema layer (`templates/TASK.md`).
4. The task-file `Work root:` field is read by `validate-task-readiness` and the launcher only when the project's `<project-root>/cartopian.toml` declares the named roots under `[project].work_roots` and the per-machine `<project-root>/cartopian.local.toml` maps each name to an absolute path. Projects with no external work roots may either omit the field or write `Work root: n/a`; the launcher fails closed on declared names that have no per-machine mapping.

`templates/TASK.md`, `templates/PROMPT.md`, `templates/REVIEW.md`, `templates/REPORT.md`, and `protocol/CONVENTIONS.md` are rewritten to describe the new model.

#### Applies-when precondition

Applies when the project's `cartopian.toml` `[project] protocol_version` is unset, missing, or lexically less than `v0.3.0`. Projects already at `v0.3.0` (or any later entry's version) are skipped.

#### Agent-followable migration steps

Run these against the project root in order. Each step is idempotent; re-applying a step that has already been applied is a no-op.

1. **Register the project in the FR-003 registry, if not already registered.** If `cartopian discover-projects` does not list the project at its current absolute path, run `cartopian register-project <project-path>`. If the project is already registered at the same absolute path, no action. The registry is the only project-selection mechanism going forward.
2. **Swap the task-file `Repo subpath:` header for `Work root:` with name-only semantics.** In every file under `<project-root>/tasks/` and `<project-root>/reviews/`:
   - Replace the line-anchored header `Repo subpath:` with `Work root:`.
   - If the existing value is `n/a`, keep it as `n/a` (or omit the line entirely; both forms are accepted by the new template).
   - If the existing value is a path fragment (e.g., `cartopian-web`, `team-a/cartopian-web`), replace the value with a **name** drawn from the project's `[project].work_roots`. If the project does not yet declare the appropriate name, add it in step 4 before completing this substitution. Within-root subdirectory information moves out of this field and into the task body prose. Absolute paths and `<owner>/<repo>` slugs are not permitted as field values. Files that already carry `Work root:` at the line anchor are skipped.
3. **Swap the report-file `Repo subpath:` field for `Work root:` with name-only semantics.** In every file under `<project-root>/reports/`, replace the line-anchored entry `- Repo subpath:` with `- Work root:`, applying the same value transformation as step 2. Reports already carrying `- Work root:` are skipped.
4. **Declare `[project].work_roots` in committed `cartopian.toml`, if the project references any external work locations.** If the project's tasks need to read or write outside the cartopian project root, add an inline list under the `[project]` table:

   ```toml
   [project]
   work_roots = ["product"]   # or ["product", "design"], etc.
   ```

   Names use `[A-Za-z0-9_-]` only. If the project's tasks never touch anything outside the project root, omit `work_roots` entirely. If `[project].work_roots` already exists with the needed names, no action.

5. **Author `<project-root>/cartopian.local.toml` on each operator's machine.** When `[project].work_roots` is non-empty, the local operator authors a per-machine override file mapping each declared name to a platform-native absolute path:

   ```toml
   [work_roots]
   product = "/absolute/path/to/product/repo"
   ```

   The file is gitignored by `cartopian scaffold-project` and is never committed; operators on other machines author their own copy with their own absolute paths. If a `cartopian.local.toml` already maps every declared name to an existing absolute path on disk, no action.

6. **Retire any wrapper or launcher reliance on the old parent-of-workspace-root launch cwd.** If the project ships customized wrappers, ensure they now `cd` to the registered project root (or rely on the shipped wrappers, which do this automatically). Wrappers that previously derived launch cwd by walking up from a `<workspace>/projects/<project-id>/...` prompt path must be updated to use the project root directly. If the project uses only the shipped wrappers, no action.
7. **Set the protocol-version marker.** In `<project-root>/cartopian.toml`, set or update `[project] protocol_version = "v0.3.0"`. If the marker already equals `"v0.3.0"` (or a later entry's version), no action.

#### Idempotence guarantee

Re-applying these steps to an already-migrated project is a no-op. `cartopian register-project` rejects duplicate registrations of the same absolute path (the validation hint below confirms registration either way). The header substitutions in steps 2 and 3 match the exact line-anchored retired string, so once swapped the regex finds nothing to swap. Step 4 inserts `work_roots` only when absent or incomplete; an already-present, well-formed list is left alone. Step 5 leaves an existing well-formed `cartopian.local.toml` untouched. Step 6 is a no-op for projects that already use the shipped wrappers. Step 7 writes `protocol_version` to exactly `v0.3.0`, regardless of how many times it is applied. The post-migration validation hint is the canonical check; an agent runs it before and after each step set to confirm the project is conformant.

#### Post-migration validation hint

After migration, run the following checks against the project root. Each check matches one or more migration steps; an agent that followed the migration steps successfully will see every check pass.

```sh
PROJECT_ROOT=<project-root>

# 1. Project is registered (FR-003 registry; step 1).
cartopian discover-projects | grep -Fq "$PROJECT_ROOT"
# expected: exit status 0

# 2. No retired `Repo subpath:` header remains at the line anchor in
#    tasks/, reviews/, or reports/ (steps 2 and 3).
grep -RIln '^Repo subpath:' "$PROJECT_ROOT/tasks/" \
                            "$PROJECT_ROOT/reviews/" \
                            "$PROJECT_ROOT/reports/"
# expected: no matches (grep exits non-zero)
grep -RIln '^- Repo subpath:' "$PROJECT_ROOT/reports/"
# expected: no matches (grep exits non-zero)

# 3. If `[project].work_roots` is declared, every name resolves to an
#    absolute path on this machine (steps 4 and 5).
cartopian resolve-config "$PROJECT_ROOT" >/dev/null
# expected: exit status 0

# 4. protocol_version marker is v0.3.0 (or a later entry's version)
#    (step 7).
grep -E '^protocol_version *= *"v0\.3\.0"' \
  "$PROJECT_ROOT/cartopian.toml"
# expected: one match (or a later version line if a later entry
# has been applied on top)
```

A project is conformant when every check passes and the project's wrappers (if customized) launch the agent at the registered project root rather than the parent of any workspace.

- **Protocol version:** `v0.2.0`
- **One-line summary:** Renames `ENGINEERING.md` to `STANDARDS.md`, swaps the `Test gate:` task-file header for `Evidence gate:`, reshapes `[roles]` from kind values to a flat name → description table, and shrinks the protocol-default role roster to `pm` and `operator`.

#### Breakage description

Older projects no longer match the new protocol surface in three specific tool-surface places. Body prose inside project artifacts (plan narratives, decision rationale, review notes, etc.) that mentions the old filename or header in passing is **not** part of the breakage contract and is out of FR-001 scope; only the load-bearing tool surfaces below break.

1. The project-root `ENGINEERING.md` filename is retired in favor of `STANDARDS.md`. Skills and templates that import the standards file by name read `STANDARDS.md` only; a project that still ships an `ENGINEERING.md` file at its root is not discoverable to the new surfaces.
2. The `Test gate:` task-file header is retired in favor of `Evidence gate:`. Field values (`required` / `n/a`) keep their semantics verbatim; only the field name changes. Readiness parsing keys on the line-anchored `Test gate:` / `Evidence gate:` header in task and review files; tasks that still carry the old header at the line anchor fail readiness checks under the new protocol.
3. The `[roles]` table no longer carries kind values (`pm = "agent"`, `operator = "human"`, `coder = "agent"`, `reviewer = "agent"`). It is now a flat name → description map. The `human` / `agent` / `none` / `""` kind set is removed from the protocol; dispatch path is inferred from the presence or absence of a `[handoffs.<role>]` block. Projects whose `cartopian.toml` carries kind values silently lose dispatch metadata and need description strings instead.

The protocol-default role roster also shrinks from `pm` / `operator` / `coder` / `reviewer` to `pm` / `operator` only; `coder` and `reviewer` are now common example labels operators opt-in to, not defaults.

The protocol default for `[defaults] git_versioning` is **`false`**. Source attribution: the explicit `git_versioning = false` value in the repo-root `cartopian.toml` shipped with this protocol (Stage 1.5 round-14 carry-forward). Projects opt in to git versioning by setting `git_versioning = true` in their own config; this default does not change with this entry but is stated explicitly here so its provenance is unambiguous.

#### Applies-when precondition

Applies when the project's `cartopian.toml` `[project] protocol_version` is unset, missing, or lexically less than `v0.2.0`. Projects already at `v0.2.0` (or any later entry's version) are skipped.

#### Agent-followable migration steps

Run these against the project root in order. Each step is idempotent; re-applying a step that has already been applied is a no-op.

1. **Rename the standards file.** If `<project-root>/ENGINEERING.md` exists, rename it to `<project-root>/STANDARDS.md`. If `STANDARDS.md` already exists and `ENGINEERING.md` does not, no action.
2. **Swap the evidence-gate header.** In every file under `<project-root>/tasks/` and `<project-root>/reviews/`, replace the line-anchored header `Test gate:` with `Evidence gate:` (semantics of values `required` / `n/a` unchanged). Leave body prose mentions inside completed task files untouched if they are not the header field. Files that already carry `Evidence gate:` are skipped.
3. **Reshape `[roles]` if present.** If `<project-root>/cartopian.toml` declares a `[roles]` table whose values are kind strings (`"agent"`, `"human"`, `"none"`, or `""`), rewrite each entry to a one-line description string in the form `<role-name> = "<description>"`. Suggested descriptions:
   - `pm = "Plans phases, dispatches handoffs, integrates results."`
   - `operator = "Approves locks, unblocks, sets cadence."`
   - `coder = "Implements tasks per spec."`
   - `reviewer = "Reviews per acceptance evidence."`

   Remove any `kind` keys. If the table's values are already description strings, no action. If the project has no `[roles]` table (it inherits from a workspace or global config), no action here.

4. **Set the protocol-version marker.** In `<project-root>/cartopian.toml`, set or update `[project] protocol_version = "v0.2.0"`. Insert into the `[project]` table if absent. If the marker already equals `"v0.2.0"` (or a later version), no action.

#### Idempotence guarantee

Re-applying these steps to an already-migrated project is a no-op: the rename precondition checks for `ENGINEERING.md`'s existence, so once renamed the step is skipped; already-swapped headers stay swapped (matched by exact line-anchored string, so re-application finds nothing left to swap); reshaped `[roles]` tables stay reshaped (values are already strings, so no rewrite triggers); and `protocol_version` is set to exactly `v0.2.0` regardless of how many times the step is applied.

#### Post-migration validation hint

After migration, run the following checks against the project root. Each check matches exactly one migration step; an agent that followed the migration steps successfully will see every check pass. The checks are scoped to the tool surfaces the migration changes — body prose elsewhere in the project is intentionally out of scope.

```sh
PROJECT_ROOT=<project-root>

# 1. Rename succeeded: ENGINEERING.md no longer exists at the
#    project root.
test ! -e "$PROJECT_ROOT/ENGINEERING.md"
# expected: exit status 0

# 2. New standards file exists at the project root.
test -f "$PROJECT_ROOT/STANDARDS.md"
# expected: exit status 0

# 3. No retired Test gate: header remains at the line anchor in
#    tasks/ or reviews/. Body prose mentions elsewhere are not
#    flagged.
grep -RIln '^Test gate:' "$PROJECT_ROOT/tasks/" "$PROJECT_ROOT/reviews/"
# expected: no matches (grep exits non-zero)

# 4. protocol_version marker is v0.2.0 (or a later entry's
#    version) in the project cartopian.toml.
grep -E '^protocol_version *= *"v0\.2\.0"' \
  "$PROJECT_ROOT/cartopian.toml"
# expected: one match (or a later version line if a later entry
# has been applied on top)
```

A project is conformant when all four checks pass and the project's `[roles]` table (if present) carries description strings, not kind values.
