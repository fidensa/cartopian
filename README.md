# Cartopian

Filesystem-first project governance for AI-native development.

Cartopian is a lightweight, protocol-driven project management system
designed for AI agent workflows. It tracks phases, tasks, specs,
decisions, and reviews using plain markdown files and directory-as-status
conventions. No database, no SaaS dependency, no mandatory tooling.

## Design principles

- **Filesystem-first.** Status is a directory. Moving a file is the
  status update. No metadata to sync.
- **Git-optional.** Git versioning can be enabled per project in
  `cartopian.toml`. The protocol works without it.
- **Multi-project workspaces.** Each managed project is a child
  directory under `projects/` with its own config and state. Projects
  are fully isolated from each other.
- **Single projects repo.** All project PM data lives under `projects/`,
  which is its own git repo — one repo for all projects. No per-project
  PM repos, no naming collisions with code repos.
- **AI-native prompts.** The PM produces temporary assignee-directed
  prompts with full context. The operator confirms assignment
  explicitly.
- **Protocol, not methodology.** Best practices are adopted because they
  work, not because a methodology prescribes them.

## Getting started

Cartopian ships with guided skills that AI agents can follow to set up
and plan projects. See `skills/README.md` for the full index.

- **`skills/init-workspace.md`** — Generate workspace and project config files.
- **`skills/init-project.md`** — Scaffold a new project with the correct structure.
- **`skills/plan-project.md`** — Walk the full lifecycle: requirements → plan → phases → tasks.

## Workspace structure

```
cartopian/                           ← this repo (public, generic)
├── README.md                        ← you are here
├── LICENSE                          ← MIT
├── protocol/                        ← baseline protocol docs
│   └── CONVENTIONS.md               ← protocol-level conventions
├── templates/                       ← default templates
│   ├── PROMPT.md
│   ├── TASK.md
│   ├── SPEC.md
│   ├── REVIEW.md
│   ├── DECISION.md
│   ├── REQUIREMENTS.md
│   ├── ENGINEERING.md
│   └── IMPLEMENTATION_PLAN.md
├── skills/                          ← agent-executable guided workflows
│   ├── README.md
│   ├── init-workspace.md
│   ├── init-project.md
│   └── plan-project.md
│
└── projects/                        ← gitignored, its own git repo
    ├── <project-a>/
    │   ├── cartopian.toml           ← project config
    │   ├── STATE.md
    │   ├── CONVENTIONS.md           ← extends protocol
    │   ├── REQUIREMENTS.md
    │   ├── ENGINEERING.md
    │   ├── IMPLEMENTATION_PLAN.md
    │   ├── phases/
    │   ├── prompts/                 ← temporary assignee handoffs
    │   ├── tasks/
    │   │   ├── open/
    │   │   ├── in-progress/
    │   │   ├── in-review/
    │   │   └── done/
    │   ├── specs/
    │   ├── decisions/
    │   └── reviews/
    │
    └── <project-b>/
        └── ...
```

## Configuration

**Workspace-level** `cartopian.toml` at the workspace root sets defaults.
**Project-level** `cartopian.toml` in each project directory overrides
defaults and configures project-specific settings.

## Roles

Cartopian defines four basic roles configured in `cartopian.toml`:

- **PM** — Drives planning. Produces assignments and proposes assignees.
- **Operator** — Decision-maker. Confirms assignments, gives them to
  assignees, reports progress.
- **Coder** — Implements tasks from assignee prompts.
- **Reviewer** — Reviews artifacts and produces findings.

The same agent can fill multiple roles. Roles are extensible — define
custom roles in `cartopian.toml` as needed. An empty value (`""`)
indicates an unset or unassigned role. A value of `"none"` indicates the
role is not used at all.

## Protocol

See `protocol/CONVENTIONS.md` for the full protocol specification.

## License

MIT. See `LICENSE`.
