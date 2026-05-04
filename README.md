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
Cartopian skills can be run using the natural language equivalent of
their filenames (e.g., "init project", "init workspace", "plan project").

- **`skills/init-workspace.md`** — Generate workspace and project config files.
- **`skills/init-project.md`** — Scaffold a new project with the correct structure.
- **`skills/plan-project.md`** — Walk the full lifecycle: requirements → plan → phases → tasks.
- **`skills/run-task.md`** — Drive one task from assignment through review.
- **`skills/run-handoff.md`** — Execute a reusable prompt/report handoff.
- **`skills/close-plan.md`** — Close a completed plan, optionally archive it, and reset for the next planning cycle.

Typical project lifecycle:

```text
init project -> plan project -> run task -> close plan -> plan project
```

## Configuration

**Workspace-level** `cartopian.toml` at the workspace root sets defaults.
**Project-level** `cartopian.toml` in each project directory overrides
defaults and configures project-specific settings.

Run Cartopian's `init-workspace` skill to quickly set these up.

## Roles

Cartopian defines four basic roles configured in `cartopian.toml`:

- **PM** — Drives planning. Produces assignments and proposes assignees.
- **Operator** — Decision-maker. Confirms assignments, gives them to
  assignees, reports progress.
- **Coder** — Implements tasks from assignee prompts.
- **Reviewer** — Reviews artifacts and produces findings.

The same agent can be assigned to multiple roles. The human operator may take on multiple roles as well. Roles are extensible; define custom roles in `cartopian.toml` as needed.

Role values are kind values that describe the assignee type:

- `human` — manually assigned through the operator.
- `agent` — may be assigned through CLI handoff when configured.
- `none` — role is not used.
- `""` — unset; the PM should ask the operator.

## Automated CLI handoffs

Automation is optional. Manual handoff remains the default.

When a role is set to `agent`, the PM can automate handoffs by
configuring a named executable under `[handoffs.<role>]`:

```toml
[handoffs.coder]
agent = "codex"
auto_start = true
timeout = "60m"
```

The executable convention is:

```text
<agent> <absolute prompt path>
```

Prompt paths are passed as one argument and should be shell-quoted in
manual command examples.

Key design points:

- Tool-specific non-interactive behavior belongs in the executable or
  wrapper, not in Cartopian config.
- Optional handoff timeouts can be set per role.
- PM-authored prompts always use absolute paths.
- Completion reports live at protocol-defined paths under `reports/`.
- Completion reports must redact secrets and sensitive environment values.
- `confirmation = "each-handoff"` is the safe default.
- `confirmation = "until-blocked"` is available for bounded unattended
  runs, but still launches handoffs sequentially.

Cartopian ships cross-platform wrapper scripts in `wrappers/` for Codex,
Claude Code, Gemini, and Devin CLIs. These wrappers adapt each CLI to
the `<agent> <prompt-path>` contract with the correct non-interactive
flags, and they `cd` to the parent of the workspace root before
invoking the underlying CLI so a single sandbox covers both the
workspace and the sibling target product repos. See
`wrappers/README.md` for installation and customization.

See `protocol/CONVENTIONS.md` for the handoff contract and
`skills/run-handoff.md` for the executable workflow.

## Workspace structure

The Cartopian workspace lives next to the product repos it manages.
Target product repos sit as **siblings** of the workspace (or nested
below it), under a shared parent directory. That shared parent is the
launch cwd for assignee CLIs, so a single sandbox covers both the
workspace (for report write-back) and the target repos (for code
edits). See `wrappers/README.md` and `protocol/CONVENTIONS.md` for the
launch-cwd contract.

```
~/Projects/                          ← parent dir (launch cwd for CLIs)
│
├── cartopian/                       ← workspace root (this repo)
│   ├── README.md                    ← you are here
│   ├── LICENSE                      ← MIT
│   ├── protocol/                    ← baseline protocol docs
│   │   └── CONVENTIONS.md           ← protocol-level conventions
│   ├── templates/                   ← default templates
│   │   ├── PROMPT.md
│   │   ├── TASK.md
│   │   ├── SPEC.md
│   │   ├── REVIEW.md
│   │   ├── REPORT.md
│   │   ├── DECISION.md
│   │   ├── REQUIREMENTS.md
│   │   ├── ENGINEERING.md
│   │   ├── IMPLEMENTATION_PLAN.md
│   │   └── PLAN_CLOSEOUT.md
│   ├── skills/                      ← agent-executable guided workflows
│   │   ├── README.md
│   │   ├── init-workspace.md
│   │   ├── init-project.md
│   │   ├── plan-project.md
│   │   ├── run-handoff.md
│   │   ├── run-task.md
│   │   └── close-plan.md
│   ├── wrappers/                    ← cross-platform agent CLI wrappers
│   │   ├── README.md
│   │   ├── bin/                     ← bash wrappers (macOS/Linux/WSL)
│   │   │   ├── cartopian-codex
│   │   │   ├── cartopian-claude
│   │   │   ├── cartopian-gemini
│   │   │   └── cartopian-devin
│   │   └── ps1/                     ← PowerShell wrappers (Windows)
│   │       ├── cartopian-codex.ps1
│   │       ├── cartopian-claude.ps1
│   │       ├── cartopian-gemini.ps1
│   │       └── cartopian-devin.ps1
│   │
│   └── projects/                    ← gitignored, its own git repo
│       ├── <project-a>/             ← project PM data
│       │   ├── cartopian.toml       ← project config
│       │   ├── STATE.md
│       │   ├── CONVENTIONS.md       ← extends protocol
│       │   ├── REQUIREMENTS.md
│       │   ├── ENGINEERING.md
│       │   ├── IMPLEMENTATION_PLAN.md
│       │   ├── phases/
│       │   ├── prompts/             ← temporary assignee handoffs
│       │   ├── reports/             ← handoff completion reports
│       │   ├── tasks/
│       │   │   ├── open/            ← TASK files declare `Repo subpath: <subpath>`
│       │   │   ├── in-progress/
│       │   │   ├── in-review/
│       │   │   └── done/
│       │   ├── specs/
│       │   ├── decisions/
│       │   ├── reviews/
│       │   └── archive/             ← optional plan closeout snapshots
│       │
│       └── <project-b>/
│           └── ...
│
├── <project-a-repo>/                ← sibling target product repo
└── <project-b-repo>/                ← sibling target product repo
```

## Protocol

See `protocol/CONVENTIONS.md` for the protocol contracts and
`skills/` for executable workflows.

## License

MIT. See `LICENSE`.
