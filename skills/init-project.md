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
4. **Role overrides** — any roles that differ from defaults for this project. Each role carries a one-line description string describing the role's responsibility. A role exists in the project iff its key appears in `[roles]`; omit a key to drop a default role from this project. The protocol-default roster is `pm` and `operator`; common example labels operators add are `coder` and `reviewer`. There is no kind field on the role itself.
5. **Planning ownership and capability grants** — ask: **"Will a separate planner role author plans, or will the PM?"** Land the answer on explicit grant sets (see `CAPABILITIES.md` at the Cartopian install root for the vocabulary and presets):
   - **Separate planner** → `pm` gets `pm-with-planner`, and a `planner` role gets `planner-like`.
   - **The PM plans** → `pm` gets `pm-solo`.

   Default any `coder`-shaped role to the `coder-like` preset and any `reviewer`-shaped role to `reviewer-like`. Then **show the operator the full role→grants mapping as editable defaults** — presets compose with individual capability names (e.g. a reviewer may additionally get `read:reports`) — and apply any edits before generating the config. Because at least one role declares grants, the new project starts in the **activated** state: containment is on project-wide and any role left without a declared grant list fails closed (holds no grants). Give every remaining role, including `operator` and custom roles, an explicit grant list — ask the operator which grants each should hold.
6. **Handoff overrides** — for any role that should dispatch automatically, ask if the project needs specific handoff targets, auto-start, or timeout values. Whether a role dispatches automatically is inferred from the presence of a `[handoffs.<role>]` block.
7. **Automation overrides** — present the initiation choice as two presets: **"Wait for me to start work"** (recommended default; execution begins only on an operator directive — maps to `initiation = "operator"`, the protocol default, so the key may be omitted) or **"Automatically start ready work"** (the PM may begin execution without a directive — maps to `--automation-initiation auto`). Then ask if the project needs a different confirmation policy or max handoffs per run. Fully unattended operation requires each layer chosen explicitly: `initiation = "auto"`, `confirmation = "until-blocked"`, a `max_handoffs_per_run` batch size, and `auto_start = true` on launched roles.
8. **Work roots (optional)** — operator-declared external work locations to be surfaced by the config (names that resolve to absolute paths per-machine via `cartopian resolve-config`).

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
  [role description flags] [role grant flags] [handoff flags] [automation flags] [work-root flags]
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

### Step 8 — Summary and next steps

Print a summary of everything that was created:

- Directory structure (including `reports/`)
- Config file location and contents
- The role→grants mapping that was landed (the project starts with containment activated; grants remain editable in `cartopian.toml`)
- Seed files created
- Handoff and automation config (if any overrides were set)

Suggest next steps:

- "Run `skills/plan-project.md` to begin the planning lifecycle: requirements → plan → phases → tasks. This will also generate your `STANDARDS.md` document based on the project requirements."
