# CONFIG-MAPPING.md — Cartopian configuration reference

A complete map of every key and value Cartopian's configuration files accept, where each one belongs, what it does, and what its default is. Protocol semantics behind these settings live in `protocol/CONVENTIONS.md`; this file is the flattened key/value reference.

## The three configuration files

Cartopian reads configuration from up to three TOML files:

| File | Location | Committed? | Purpose |
|------|----------|------------|---------|
| Project config | `<project-root>/cartopian.toml` | Yes (when git versioning is used) | The project's own settings. The only file with a `[project]` table. |
| Local config | `<project-root>/cartopian.local.toml` | No — gitignored by `scaffold-project` | Per-machine work-root name → absolute-path mappings. Holds only a `[work_roots]` table. |
| Global config | `~/.cartopian/cartopian.toml` | N/A (outside any repo) | Operator-wide defaults shared by every project. Seeded fully commented-out from `templates/global.cartopian.toml`, so it contributes nothing until the operator uncomments keys. |

> The project registry `~/.cartopian/projects.json` is a separate JSON file maintained by `cartopian register-project` / `unregister-project`. It is not TOML configuration and has no operator-edited keys.

### Resolution chain (FR-011)

Effective configuration is resolved per key along:

```text
project (<project-root>/cartopian.toml)
  -> global (~/.cartopian/cartopian.toml)
    -> protocol defaults (shipped with the tool)
```

- `[roles]`, `[handoffs.<role>]`, `[automation]`, `[git]` merge **shallowly, key-by-key** — project values override global values for the same key; global keys the project does not set are inherited. `[handoffs.<role>]` merges per role, then per field within the role.
- `[reviews]` resolves key-by-key with source attribution, so a project can turn off a globally required review loop without redeclaring the inherited role.
- `[defaults].git_versioning` resolves project → global → protocol default (`false`).
- `[project]` and `[work_roots]` never resolve through the chain: `[project]` is authored only in the project file, `[work_roots]` only in the local file. (The global seed documents `[project]` for reference only.)

### How to read and edit

- **Read** the effective config with `cartopian resolve-config <project-path>` (or the `resolve_config` MCP tool). It merges the files, validates, and emits the resolved record including source attribution.
- **Edit** the project and local files with `cartopian update-config` (or the `update_config` MCP tool) — the mediated, closed-schema, comment-preserving editor. Only the dotted keys listed below are settable; role/handoff structure is edited through the dedicated `--set-role`, `--set-role-grants`, `--set-handoff`, `--remove-role`, `--remove-handoff` flags, and local work-root mappings through `--local --set-work-root` / `--unset-work-root`.
- **Create** a project file with `cartopian generate-config` (or the `init project` skill). It stamps `[project].protocol_version` automatically and writes only the flags you supply — protocol defaults are applied at consumption time, never written into the file.
- The **global** file is operator-edited directly with a text editor; there is no CLI authoring path for it.

`update-config` fails closed on TOML constructs its surgical editor cannot handle safely: multiline strings, arrays-of-tables (`[[...]]`), inline tables for managed heads (e.g. `automation = { ... }`), and top-level dotted keys (e.g. `automation.initiation = ...` outside a table). Keep the managed tables (`[project]`, `[defaults]`, `[git]`, `[automation]`, `[reviews]`, `[roles]`, `[handoffs.<role>]`) in plain `[table]` form.

---

## `[project]` — project identity

**Where:** project `cartopian.toml` only. Required — a `cartopian.toml` with no `[project]` table is not a Cartopian project config.

| Key | Type | Required | Constraints | Meaning |
|-----|------|----------|-------------|---------|
| `id` | string | Yes | kebab-case (`[a-z0-9][a-z0-9-]*`) | Stable project identifier; also the registry id. |
| `name` | string | Yes | non-empty | Human-readable project name. |
| `protocol_version` | string | Yes* | `vX.Y.Z` | Protocol version the project was created/migrated at. Stamped by `generate-config`; drives migration and the pre-v0.5 review-compatibility behavior (see `[reviews]`). *Session-startup surfaces (`next-action`, `plan-audit`) tolerate a missing value so the protocol gate can classify it as migratable; all other commands require it. |
| `work_roots` | array of strings | No | each name matches `[A-Za-z0-9_-]+`, no duplicates | The **name set** of external work locations the project's tasks may reference (e.g. `["product", "design"]`). Names only — the committed file carries no paths. Each declared name must be mapped to an absolute path in `cartopian.local.toml` on the current machine, or `resolve-config` exits non-zero with a `[work-root]` error. |

```toml
[project]
id = "my-project"
name = "My Project"
protocol_version = "v1.5.2"
work_roots = ["product", "design"]
```

## `[defaults]` — top-level protocol toggles

**Where:** project or global. **Resolution:** project → global → protocol default.

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `git_versioning` | bool | `false` | Whether the protocol manages git for the project (each project root as its own repo; auto-commit and auto-push at session closeout). When `true`, the `[git]` table becomes load-bearing. When `false`, the filesystem is the only protocol record. |

## `[git]` — git behavior (only meaningful when `git_versioning = true`)

**Where:** project or global. **Resolution:** shallow merge, project keys win. The resolved `git` block is reported as `null` by `resolve-config` when `git_versioning` is `false`.

| Key | Type | Default | Allowed values | Meaning |
|-----|------|---------|----------------|---------|
| `pm_owns_product_branches` | bool | `false` | `true` / `false` | When `true`, the PM owns product-repo git plumbing for tasks whose `Work root:` names a product repository: branches, staging, commits, pushes, PRs (`gh pr create`), merges, and branch cleanup. Never applies to tasks without a work root, and never to the Cartopian protocol repository itself. `false` is the legacy path — a project with no `[git]` section behaves exactly as before. |
| `default_branch_pattern` | string | `"task/{task_id}-{slug}"` | non-empty; placeholders `{task_id}`, `{slug}` | Product-repo branch name for a task. Used only when `pm_owns_product_branches = true`. `{task_id}` is the numeric id without the `TASK-` prefix (`NN-NNN`); `{slug}` is the task filename slug. `TASK-02-001-page-templates.md` → `task/02-001-page-templates`. |
| `default_merge_strategy` | string | `"merge"` | `merge`, `squash`, `rebase` | PM merge command for opt-in product repos, mapping to `gh pr merge --merge` / `--squash` / `--rebase`. |

```toml
[defaults]
git_versioning = true

[git]
pm_owns_product_branches = true
default_branch_pattern = "task/{task_id}-{slug}"
default_merge_strategy = "squash"
```

## `[roles]` — role roster and capability grants

**Where:** project or global. **Resolution:** shallow merge, project entries win; the protocol-default roles `pm` and `operator` are always present in the resolved roster (with default descriptions if not declared).

Roles are operator-chosen identifiers (`[A-Za-z0-9_-]+`). Names and descriptions carry **no protocol behavior** — review assignment is configured under `[reviews]`, and dispatch path is inferred from the presence of a `[handoffs.<role>]` block, not from any role field (there is no `kind` key). A role omitted from the resolved `[roles]` does not exist in the project; tasks may not assign it.

Each role takes one of two forms, and both may coexist in one config:

```toml
[roles]
# Legacy string form: name = one-line description. Declares no grant set.
pm = "Plans phases, dispatches handoffs, integrates results."
operator = "Approves locks, unblocks, sets cadence."

# Table form: optional description plus an optional capability grant list.
[roles.coder]
description = "Implements tasks per spec."
grants = ["coder-like"]
```

### `[roles.<name>]` table keys

| Key | Type | Required | Meaning |
|-----|------|----------|---------|
| `description` | string | No | One-line prose description. Prose only — no behavior keys on it. |
| `grants` | array of strings | No | Capability names and/or preset names from the closed vocabulary below. An explicitly empty list (`[]`) is a valid declaration that grants nothing. |

### Capability gating semantics

- **Ungated mode** — no role in the resolved config declares a `grants` key: gating is inactive and every role behaves as if it held all grants. Configs that predate the vocabulary work unchanged.
- **Activated mode** — at least one role declares `grants`: containment is active project-wide and resolution **fails closed**. A role whose list contains an unknown name, is malformed, or that declares no grant set at all resolves to *no* grants (with a `[validation]` warning for unknown entries). A typo never widens access. Activation is all-or-nothing per project, and a malformed declaration still activates.

### Grant vocabulary (closed, append-only)

Read capabilities: `read:governance` (management/strategy artifacts plus specs), `read:reports` (reports and reviews), `read:prompts` (the `prompts/` directory), `read:work-roots` (the product tree).

Write capabilities: `write:plan`, `write:lifecycle`, `write:decisions`, `write:reports`, `write:worktree`, `dispatch`.

Presets (valid anywhere a capability name is; expanded at resolution time):

| Preset | Expands to |
|--------|-----------|
| `coder-like` | `read:prompts`, `read:work-roots`, `write:worktree` |
| `reviewer-like` | `read:prompts`, `read:work-roots`, `write:reports` |
| `planner-like` | `read:governance`, `read:reports`, `read:prompts`, `write:plan` |
| `pm-with-planner` | `read:governance`, `read:reports`, `read:prompts`, `write:lifecycle`, `dispatch` |
| `pm-solo` | `read:governance`, `read:reports`, `read:prompts`, `write:plan`, `write:lifecycle`, `dispatch` |

## `[reviews]` — review policy

**Where:** project or global. **Resolution:** key-by-key, project wins; each resolved value carries source attribution (`project`, `global`, `protocol-default`, or `legacy-pre-v0.5`).

Review policy is explicit and independent of role names. The two loops — planning checkpoints and task closure — are controlled independently.

| Key | Type | Default | Allowed values | Meaning |
|-----|------|---------|----------------|---------|
| `planning` | string | `"off"` | `required`, `off` | Whether planning-review checkpoints exist. |
| `planning_role` | string | — | non-empty declared role name | Role that performs planning reviews. **Required** when `planning = "required"`; must name a role in the resolved `[roles]`. Ignored (resolved to none) when the loop is `off`. |
| `task_closure` | string | `"off"` | `required`, `off` | Whether task closure requires a review. When `off`, tasks close from accepted completion evidence without a review stage. |
| `task_role` | string | — | non-empty declared role name | Role that performs task-closure reviews. **Required** when `task_closure = "required"`; must be declared in `[roles]`. |

A malformed `[reviews]` table or an out-of-vocabulary value fails closed (config error) rather than silently selecting review-off.

**Pre-v0.5 compatibility:** a project whose `[project].protocol_version` is missing or below `v0.5.0` *and* whose resolved roles include `reviewer` defaults both loops to `required` with `reviewer` assigned (attribution `legacy-pre-v0.5`) until an explicit policy is written. A v0.5.0+ project never acquires behavior from a role name.

```toml
[reviews]
planning = "required"
planning_role = "reviewer"
task_closure = "required"
task_role = "reviewer"
```

## `[handoffs.<role>]` — automated dispatch targets

**Where:** project or global. **Resolution:** merged per role, then per field within the role; project fields win.

The presence of a `[handoffs.<role>]` block is the dispatch trigger: a role declared in `[roles]` **with** a matching block dispatches automatically via the configured wrapper; a role **without** one is manual-dispatch (the PM surfaces the prompt and the operator acts). Two hard rules:

- A `[handoffs.<role>]` block whose role is not declared in the resolved `[roles]` is a config error ("orphan handoff").
- **Never configure `[handoffs.pm]`.** The PM is the interactive orchestrator and is never itself launched as a handoff; `update-config` rejects `pm.*` handoff fields outright.

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `agent` | string | — | Wrapper executable name the PM invokes (e.g. `"cartopian-claude"`, `"codex"`). Invoked as `<agent> <absolute prompt path>` — tool-specific flags, sandbox and approval settings, and environment variables belong in the wrapper, not here. Pre-built wrappers live in `wrappers/`. |
| `model` | string | unset | Optional model identifier for the assigned agent (e.g. `"gpt-5-codex"`, `"claude-opus-4-8"`). Exported to the wrapper as `CARTOPIAN_MODEL`; the wrapper translates it into the tool-specific model flag. Unset means the tool's own default model. |
| `effort` | string | unset | Optional effort/thinking level (e.g. `"high"`). Exported as `CARTOPIAN_EFFORT`; the wrapper translates it into the tool-specific effort flag. A value outside the wrapper's CLI-wide vocabulary makes the wrapper warn on stderr and launch at the default. |
| `auto_start_tasks` | bool | `false`/unset | Whether the PM may launch this role for **task-scoped** handoffs (assigned task work and task-closure review). Governs launch mode only — it never initiates a run; `[automation].initiation` and `confirmation` gate that. `cartopian dispatch` enforces it fail-closed. |
| `auto_start_reviews` | bool | `false`/unset | Whether the PM may launch this role for **planning-review** checkpoints (which have no task file). Does not enable planning review itself — `[reviews].planning` decides whether the checkpoint exists and `planning_role` assigns it. |
| `timeout` | string | `"60m"` | Maximum wall-clock duration for PM-launched handoffs, format `<integer><unit>` with unit `s`, `m`, `h`, or `d` (e.g. `"90s"`, `"60m"`, `"2h"`). The single source of truth for the handoff deadline: exported to the wrapper as `CARTOPIAN_TIMEOUT`, and the wrapper is the sole enforcer (kills the assignee at the deadline, exit `124`). |

Legacy keys `auto_start` and `planning_reviews` (pre-v0.5) are accepted as compatibility inputs only: `auto_start` maps to `auto_start_tasks`, and `auto_start` *and* `planning_reviews` both `true` map to `auto_start_reviews = true`. They remain editable via `update-config` so migrations can remove them, but new edits should use the explicit names.

```toml
[handoffs.coder]
agent = "codex"
model = "gpt-5-codex"
effort = "high"
auto_start_tasks = true
timeout = "60m"

[handoffs.reviewer]
agent = "cartopian-gemini"
auto_start_tasks = true
auto_start_reviews = true
timeout = "30m"
```

## `[automation]` — initiation and pace policy

**Where:** project or global. **Resolution:** shallow merge over protocol defaults, project keys win.

The three authorities are disjoint: `initiation` gates **whether a run begins** without an execution directive; `confirmation` gates **pace** within an initiated run; `[handoffs.<role>].auto_start_*` gates **launch mode** per handoff type. Task **selection** is never gated — order is deterministic per the protocol.

| Key | Type | Default | Allowed values | Meaning |
|-----|------|---------|----------------|---------|
| `initiation` | string | `"operator"` | `operator`, `auto` | `operator`: execution begins only from an operator execution directive ("continue", "run the next task"); after informational requests the PM reports and stops. `auto`: the PM may initiate a run on its own — at session startup once startup duty completes with no blockers, and when a scoped directive leaves the open queue ready. Informational requests never start work under either value, and explicit "stop"/"pause" always wins. An **unrecognized value fails safe** to `operator` with a `[validation]` warning (less automation, never more). |
| `confirmation` | string | `"each-handoff"` | `each-handoff`, `until-blocked` | `each-handoff`: stop after each handoff result is processed. `until-blocked`: chain through handoffs whose applicable `auto_start_*` setting is true until blocked, failed, rejected, missing evidence, requiring operator judgment, reaching a phase boundary, or hitting `max_handoffs_per_run`. |
| `max_handoffs_per_run` | integer | `1` | positive integer | Hard ceiling on consecutive automated handoffs in a single run. |

Full unattended operation is a stack of explicit opt-ins, none a protocol default:

```toml
[automation]
initiation = "auto"              # runs may begin without a directive
confirmation = "until-blocked"   # runs chain through sequential tasks
max_handoffs_per_run = 5         # bounded batch size per run
```

plus `auto_start_tasks = true` / `auto_start_reviews = true` on each `[handoffs.<role>]` the PM should launch itself.

## `cartopian.local.toml` — `[work_roots]`

**Where:** local file only (`<project-root>/cartopian.local.toml`). Gitignored, never committed; each operator authors their own with their machine's paths, while the committed `cartopian.toml` stays identical for every operator.

| Key | Type | Constraints | Meaning |
|-----|------|-------------|---------|
| `<name>` | string | must be a platform-native **absolute** path | Maps a work-root name declared in `[project].work_roots` to its location on this machine. Every declared name must be mapped; relative paths are rejected. |

```toml
[work_roots]
product = "/Users/alex/Projects/my-product-repo"
design = "/Users/alex/Projects/my-design-repo"
```

Edited via `cartopian update-config <project> --local --set-work-root NAME=/abs/path` / `--unset-work-root NAME`. The file is created only when a mapping is supplied — never empty.

---

## Quick reference — every settable key

Dotted keys marked ✓ in the `update-config` column are directly settable with `--set KEY=VALUE` (closed schema); the rest use the dedicated structural flags shown, or are edited only in the file they live in.

| Dotted key | File(s) | Type / values | Default | `update-config` |
|------------|---------|---------------|---------|-----------------|
| `project.id` | project | kebab-case string | — (required) | ✓ |
| `project.name` | project | non-empty string | — (required) | ✓ |
| `project.protocol_version` | project | `vX.Y.Z` string | stamped by `generate-config` | ✓ |
| `project.work_roots` | project | list of `[A-Za-z0-9_-]+` names | `[]` | ✓ |
| `defaults.git_versioning` | project, global | bool | `false` | ✓ |
| `git.pm_owns_product_branches` | project, global | bool | `false` | ✓ |
| `git.default_branch_pattern` | project, global | non-empty string | `"task/{task_id}-{slug}"` | ✓ |
| `git.default_merge_strategy` | project, global | `merge` \| `squash` \| `rebase` | `"merge"` | ✓ |
| `automation.initiation` | project, global | `operator` \| `auto` | `"operator"` | ✓ |
| `automation.confirmation` | project, global | `each-handoff` \| `until-blocked` | `"each-handoff"` | ✓ |
| `automation.max_handoffs_per_run` | project, global | positive integer | `1` | ✓ |
| `reviews.planning` | project, global | `required` \| `off` | `"off"` | ✓ |
| `reviews.planning_role` | project, global | declared role name | — | ✓ |
| `reviews.task_closure` | project, global | `required` \| `off` | `"off"` | ✓ |
| `reviews.task_role` | project, global | declared role name | — | ✓ |
| `roles.<name>` (string form) | project, global | one-line description | roster defaults `pm`, `operator` | `--set-role` |
| `roles.<name>.description` | project, global | string | — | `--set-role` |
| `roles.<name>.grants` | project, global | list from closed grant vocabulary | undeclared (ungated) | `--set-role-grants` |
| `handoffs.<role>.agent` | project, global | wrapper executable name | — | `--set-handoff` |
| `handoffs.<role>.model` | project, global | model identifier string | unset (tool default) | `--set-handoff` |
| `handoffs.<role>.effort` | project, global | effort level string | unset (tool default) | `--set-handoff` |
| `handoffs.<role>.auto_start_tasks` | project, global | bool | `false` | `--set-handoff` |
| `handoffs.<role>.auto_start_reviews` | project, global | bool | `false` | `--set-handoff` |
| `handoffs.<role>.timeout` | project, global | `<int>` + `s`/`m`/`h`/`d` | `"60m"` | `--set-handoff` |
| `handoffs.<role>.auto_start` | (legacy) | bool | — resolves to `auto_start_tasks` | `--set-handoff` (migration only) |
| `handoffs.<role>.planning_reviews` | (legacy) | bool | — resolves into `auto_start_reviews` | `--set-handoff` (migration only) |
| `work_roots.<name>` | local | absolute path string | — | `--local --set-work-root` |
