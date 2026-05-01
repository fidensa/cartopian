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
3. **Target repos** — paths to code repositories this project governs
   (if any). Each repo needs a name, relative path, and default branch.
4. **Role overrides** — any roles that differ from workspace defaults
   for this project.

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
empty and holds temporary assignee handoff prompts.

### Step 3 — Generate project config

Write `projects/<project-id>/cartopian.toml`:

```toml
[project]
name = "<project name>"
id = "<project-id>"

[roles]
# Include only overrides. Workspace defaults apply for omitted roles.

[repos.<repo-name>]
path = "<relative path>"
default_branch = "main"
```

Omit the `[roles]` section entirely if there are no overrides. Include
`[repos.*]` only if the operator provided target repos.

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

     Fill this in before or during the planning phase. The plan-project
     skill will read this document as constraints when generating the
     implementation plan. -->
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

- Directory structure
- Config file location and contents
- Seed files created

Suggest next steps:

- "Fill in `ENGINEERING.md` with your tech stack and standards (optional
  but recommended)."
- "Run `skills/plan-project.md` to begin the planning lifecycle:
  requirements → plan → phases → tasks."
