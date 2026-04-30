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
  directory with its own config, state, and optional git repo. Projects
  are fully isolated from each other.
- **AI-native prompts.** The PM produces assignee-directed prompts with
  full context. The operator confirms assignment explicitly.
- **Protocol, not methodology.** Best practices are adopted because they
  work, not because a methodology prescribes them.

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
│   └── DECISION.md
│
├── <project-a>/                     ← gitignored, own git repo
│   ├── cartopian.toml               ← project config
│   ├── STATE.md
│   ├── CONVENTIONS.md               ← extends protocol
│   ├── phases/
│   ├── tasks/
│   │   ├── open/
│   │   ├── in-progress/
│   │   ├── in-review/
│   │   └── done/
│   ├── specs/
│   ├── decisions/
│   └── reviews/
│
└── <project-b>/                     ← gitignored, own git repo
    └── ...
```

## Configuration

**Workspace-level** `cartopian.toml` at the workspace root sets defaults.
**Project-level** `cartopian.toml` in each project directory overrides
defaults and configures project-specific settings.

## Protocol

See `protocol/CONVENTIONS.md` for the full protocol specification.

## License

MIT. See `LICENSE`.
