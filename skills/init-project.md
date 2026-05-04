# Skill: Init Project

Scaffold a new project under `projects/` with the full directory
structure and seed files.

**Output:** A fully scaffolded project directory ready for use.

---

## Prerequisites

- A workspace-level `cartopian.toml` exists (run `skills/init-workspace.md`
  first if not).
- You know the path to the Cartopian workspace root.

---

## Steps

### Step 1 — Gather project info

Ask the operator for:

1. **Project name** — human-readable (e.g., "Widget API").
2. **Project ID** — kebab-case slug (e.g., `widget-api`). Suggest one
   derived from the name if the operator doesn't provide one.
3. **Role kind overrides** — any roles that differ from workspace
   defaults for this project. Remind the operator that role values are
   kind values (`human`, `agent`, `none`, or `""` for unset). An empty
   value `""` indicates an unset or unassigned role, and `"none"`
   indicates the role is not used at all.
4. **Handoff overrides** — for any agent roles, ask if the project
   needs different CLI handoff targets, auto-start, or timeout values
   than the workspace defaults. Explain that omitted handoff config
   inherits workspace behavior.
5. **Automation overrides** — ask if the project needs a different
   confirmation policy or max handoffs per run than the workspace
   defaults.

Target product repos are not declared in `cartopian.toml`. Each task
records its own `Repo subpath:` and the assignee CLI is launched with
cwd at the parent of the workspace root (see `protocol/CONVENTIONS.md`
→ Handoffs → Launch Directory). Per-task branch information lives on
the prompt's `Branch:` field, populated by the PM at handoff time.

### Step 2 — Create directory structure

Create the following under `projects/<project-id>/`:

```
projects/<project-id>/
├── cartopian.toml
├── STATE.md
├── CONVENTIONS.md
├── ENGINEERING.md
├── phases/
├── prompts/
├── reports/
├── tasks/
│   ├── open/
│   ├── in-progress/
│   ├── in-review/
│   └── done/
├── specs/
├── decisions/
│   └── INDEX.md
└── reviews/
```

Create all directories, including empty ones. The task status
subdirectories (`open/`, `in-progress/`, `in-review/`, `done/`) must all
exist even though they start empty. The `prompts/` directory starts
empty and holds temporary assignee handoff prompts. The `reports/`
directory starts empty and holds handoff completion reports.

### Step 3 — Generate project config

Write `projects/<project-id>/cartopian.toml`:

```toml
[project]
name = "<project name>"
id = "<project-id>"

[roles]
# Include only overrides. Workspace defaults apply for omitted roles.
# Role kind values: "human", "agent", "none", or "" (unset).

# [handoffs.<role>]
# agent = "<executable name>"
# auto_start = <true|false>
# timeout = "<duration>"
# Omitted handoff config inherits workspace behavior.

# [automation]
# confirmation = "each-handoff"
# max_handoffs_per_run = 1
# Omitted automation config inherits workspace behavior.
```

Omit the `[roles]` section entirely if there are no overrides. Include
`[handoffs.*]` only for project-specific overrides. Include
`[automation]` only for project-specific overrides.

Keep manual handoff as the default.

### Step 4 — Generate seed STATE.md

Write `projects/<project-id>/STATE.md`:

```markdown
# <project name> — State

## Current phase

No phases defined yet. Run `skills/plan-project.md` to generate the
project plan.

## Active work

None.

## Open work

None.

## What to do next

Run `skills/plan-project.md` to begin requirements gathering and
generate the implementation plan, phases, and tasks.
```

### Step 5 — Generate seed CONVENTIONS.md

Write `projects/<project-id>/CONVENTIONS.md`:

```markdown
# <project name> — Conventions

This document extends the protocol-level conventions defined in
`protocol/CONVENTIONS.md`. Rules here apply only to this project.

## Project-specific conventions

<!-- Add project-specific naming rules, workflow modifications, or
     constraints here. Delete this comment when you add real content. -->
```

### Step 6 — Generate seed ENGINEERING.md

Write `projects/<project-id>/ENGINEERING.md`:

```markdown
# <project name> — Engineering Standards

<!-- This document is recommended but not required. It captures the
     technical standards and constraints that govern implementation for
     this project. See templates/ENGINEERING.md for a starting template.

     This document will be populated during the planning phase. The plan-project
     skill will generate and update this document based on requirements and
     architectural decisions or you can edit this file directly.-->
```

### Step 7 — Generate seed decisions/INDEX.md

Write `projects/<project-id>/decisions/INDEX.md`:

```markdown
# Decision Index

| ID | Title | Date | Status | Supersedes |
|---|---|---|---|---|
```

### Step 8 — Summary and next steps

Print a summary of everything that was created:

- Directory structure (including `reports/`)
- Config file location and contents
- Seed files created
- Handoff and automation config (if any overrides were set)

Suggest next steps:

- "Run `skills/plan-project.md` to begin the planning lifecycle:
  requirements → plan → phases → tasks. This will also generate your
  `ENGINEERING.md` document based on the project requirements."
