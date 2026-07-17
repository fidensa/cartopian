# Skill: Init Project

Initialize a new project at an operator-supplied absolute path using the Core CLI: scaffold the directory tree, generate the project config, and register the project in the registry.

**Output:** A scaffolded project at the chosen path, with config written and the project registered.

---

## Prerequisites

- Cartopian Core CLI is installed and available as `cartopian`.
- An absolute `<project-path>` chosen for the new project (operator-supplied; may live anywhere on disk).
- Project name and ID decided (human-readable name and kebab-case ID).

---

## Steps

### Step 1 — Gather project info

Ask the operator for:

1. **Project path** — absolute filesystem path where the project will live (e.g., `/path/to/projects/widget-api`).
2. **Project name** — human-readable (e.g., "Widget API").
3. **Project ID** — kebab-case slug (e.g., `widget-api`). Suggest one derived from the name if the operator doesn't provide one.
4. **Role overrides** — any roles that differ from defaults for this project. Each role carries a one-line description string describing responsibility. `reviewer` is the conventional example for review work, but role names are operator-chosen and never enable protocol behavior by themselves.
5. **Review policy and assignment** — ask for one preset: **no reviews**, **planning only**, **task closure only**, or **planning and task closure**. For every required loop, ask which resolved role performs it. Persist the result under `[reviews]`; never infer it from a role name or description. Project policy may override global review defaults without removing inherited roles.
6. **Planning ownership and capability grants** — ask: **"Will a separate planner role author plans, or will the PM?"** Land the answer on explicit grant sets (see `CAPABILITIES.md` at the Cartopian install root for the vocabulary and presets):
   - **Separate planner** → `pm` gets `pm-with-planner`, and a `planner` role gets `planner-like`.
   - **The PM plans** → `pm` gets `pm-solo`.

   Suggest `coder-like` for roles assigned execution work and `reviewer-like` for roles assigned task review, regardless of their names. Presets are permission bundles, not role types. Then **show the operator the full role→grants mapping as editable defaults** and apply edits before generating config. Because at least one role declares grants, containment activates project-wide and any role without a declared grant list fails closed.
7. **Handoff overrides** — for any role with a configured agent, ask for the target, timeout, whether task-scoped handoffs should launch automatically (`auto_start_tasks`), and whether planning-review handoffs should launch automatically (`auto_start_reviews`). These launch settings remain separate from whether review is required.
8. **Automation overrides** — present the initiation choice as two presets: **"Wait for me to start work"** (recommended default; execution begins only on an operator directive — maps to `initiation = "operator"`, the protocol default, so the key may be omitted) or **"Automatically start ready work"** (the PM may begin execution without a directive — maps to `--automation-initiation auto`). Then ask if the project needs a different confirmation policy or max handoffs per run. Fully unattended operation requires each layer chosen explicitly: `initiation = "auto"`, `confirmation = "until-blocked"`, a `max_handoffs_per_run` batch size, and the applicable `auto_start_tasks` / `auto_start_reviews` settings on launched roles.
9. **Work roots (optional)** — operator-declared external work locations to be surfaced by the config (names that resolve to absolute paths per-machine via `cartopian resolve-config`).

Launch cwd is the cartopian project root (registry-based). Tasks reference external work locations via the renamed work-location field. Projects that routinely use fixed external roots declare named work roots in `cartopian.toml`; `cartopian resolve-config` resolves these names to absolute paths per machine, and launchers grant access to declared paths per the access-grant model.

### Step 2 — Scaffold project via Core CLI

Run the scaffold command against the absolute `<project-path>`:

```
cartopian scaffold-project <project-path>
```

Outcomes per contract:
- Exit 0 and files created when the target is empty or missing (created).
- Exit 0 and no-op when a complete scaffold already exists.
- Exit 1 with a `[guard]` message when the target is non-empty and conflicts with the scaffold layout.

The scaffold seeds the directory structure and seed files (STATE.md, CONVENTIONS.md, STANDARDS.md, phases/, tasks/{open,in-progress,in-review,done}/, prompts/, reports/, specs-or-renamed-work-contracts/, decisions/ with INDEX.md, reviews/).

### Step 3 — Generate project config via Core CLI

Write the project-level config with the CLI, supplying the gathered inputs as flags:

```
cartopian generate-config <project-path> \
  --name "<project name>" \
  --id "<project-id>" \
  [role description flags] [role grant flags] [review policy/role flags] [handoff flags] [automation flags] [work-root flags]
```

Grant flags carry the role→grants mapping landed in Step 1 — one `--role-grants ROLE=NAME[,NAME...]` per role, where each name is a capability or preset from `CAPABILITIES.md`. Example for the separate-planner answer:

```
  --role 'pm=Plans phases, dispatches handoffs, integrates results.' \
  --role 'planner=Authors plans and phase structure.' \
  --role-grants pm=pm-with-planner \
  --role-grants planner=planner-like
```

Notes:
- Include only role overrides; defaults apply when a role key is omitted.
- Every declared role should get a `--role-grants` entry: declaring any grants activates containment project-wide, and a role without a declared grant list then fails closed. Unknown grant names are rejected at generation time.
- Review flags are `--review-planning required|off`, `--review-planning-role ROLE`, `--review-task-closure required|off`, and `--review-task-role ROLE`. Role flags are required only for required loops.
- `--handoff-auto-start-tasks ROLE=true` enables automatic task-scoped launches for that role; `--handoff-auto-start-reviews ROLE=true` independently enables automatic planning-review launches. Neither flag enables review policy.
- `[handoffs.<role>]` blocks are emitted only when provided; omitted inherits defaults.
- `[automation]` is emitted only when provided.
- Work-root flags declare named roots; resolution to absolute paths is per-machine via `cartopian resolve-config`.

### Step 4 — Register the project in the registry

Add the new project to the registry so skills discover it by ID:

```
cartopian register-project <project-path> [--label "<project name>"]
```

Verify registration:

```
cartopian discover-projects
```

Expect an entry with `id = <project-id>`, `path = <absolute project-path>`, and `label` (defaults to name when omitted).

### Step 5 — Summary and next steps

Print a summary of everything that was created:

- Directory structure (including `reports/`)
- Config file location and contents
- The role→grants mapping that was landed (the project starts with containment activated; grants remain editable in `cartopian.toml`)
- Seed files created
- Handoff and automation config (if any overrides were set)
- Review policy and assigned roles for each required loop

Suggest next steps:

- "Run `skills/plan-project.md` to begin the planning lifecycle: requirements → plan → phases → tasks. This will also generate your `STANDARDS.md` document based on the project requirements."
