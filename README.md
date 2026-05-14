# Cartopian

**An AI-native project manager that lives in your filesystem.**

Cartopian turns "I want to do X" into a tracked plan, logical phases, structured tasks, real specs, and dispatched work. All without third-party MCP servers, no database, and no project disappearing into a chat window when you close the tab. It's flexible enough to run a SaaS product, an Etsy store launch, or a weekend garage sale, and disciplined enough that an AI agent can pick the project back up tomorrow and keep going.

## What it actually does

- **Plans the work.** An AI Project Manager interviews you, drafts requirements, breaks them into phases, and emits tasks with acceptance criteria.
- **Tracks progress.** Phases, tasks, decisions, reviews, and session state live as plain markdown so progress is visible at a glance — and survives any tool change.
- **Writes the specs.** Each task gets a real spec, not a vibes-based prompt. Decisions get recorded as they happen, so future-you knows why.
- **Orchestrates the doers.** Roles map tasks to the right resource: a programmer agent, a reviewer agent, a designer, or you. Define any role you need; only the Operator and Project Manger roles are required. The PM hands off, collects results, and integrates.
- **Automates the boring parts.** Handoffs to CLI agents (Codex, Claude Code, Gemini, Devin, or others) can be one-tap or fully unattended, with timeouts and confirmation gates you control.
- **Stays out of your way.** Git is optional. Automation is optional. Roles are operator-chosen. Every decision is overridable.

## How it feels in practice

```text
init project   →   plan project   →   start session   →   run task   →   close plan
```

You tell the PM what you want. It asks the questions it needs to ask, produces a requirements doc, drafts a plan, breaks it into phases and tasks, and parks everything on disk. When you come back, "start session" reads the current state and tells you what's next. "Run task" dispatches it, either to an agent if you've wired one up, or to you if you'd rather drive.

When the plan is done, "close plan" archives it and you're ready for the next one.

## Install

Requirements: **Python 3.11+** on your PATH. (macOS users: the stock `/usr/bin/python3` is 3.9 — use `brew install python@3.11` or any 3.11+ interpreter.)

**macOS / Linux / WSL:**

```bash
git clone https://github.com/fidensa/cartopian.git ~/src/cartopian
python3 ~/src/cartopian/scripts/install.py
echo 'export PATH="$HOME/.cartopian/bin:$PATH"' >> ~/.zshrc   # or ~/.bashrc
```

**Windows (PowerShell):**

```powershell
git clone https://github.com/fidensa/cartopian.git $HOME\src\cartopian
python $HOME\src\cartopian\scripts\install.py
[Environment]::SetEnvironmentVariable(
  "Path",
  "$HOME\.cartopian\bin;" + [Environment]::GetEnvironmentVariable("Path","User"),
  "User"
)
```

Symlinks on native Windows need either **Developer Mode** or an elevated shell. If neither is available, re-run with `--mode copy`.

**Upgrade** is `git pull` followed by re-running the installer. Your `cartopian.toml` and `projects.json` are preserved; everything tool-shipped gets refreshed.

Verify with:

```bash
cartopian --help
```

The post-install checklist lives at `~/.cartopian/protocol/INSTALL_VERIFICATION.md`.

## Getting started

Cartopian ships **skills** — runbooks an AI agent reads and follows to do real work. You invoke them by their natural-language name (the filename, hyphens as spaces).

| Say this | What happens |
| --- | --- |
| `init workspace` | Sets up your workspace and config defaults |
| `init project` | Scaffolds a new project |
| `adopt requirements` | Imports requirements from JIRA, a PRD, Confluence, etc. |
| `adopt plan` | Pulls an existing plan into Cartopian's shape |
| `plan project` | Drives the full lifecycle: requirements → plan → phases → tasks |
| `start session` | "Where were we?" — reads state, proposes next action |
| `run task` | Drives one task from assignment through review |
| `run handoff` | Executes one prompt/report handoff |
| `close plan` | Closes the active plan and resets for the next |

In a workspace with multiple projects, vague requests like *"start working"* prompt the PM to ask which project first. Then it reads `STATE.md`, reports the current or next move, and waits for your go-ahead.

See `skills/README.md` for the full index.

## Roles and AI orchestration

The default roster is **PM** and **Operator** — the planner and the decider. From there, you name whatever roles your project needs: Coder, Reviewer, Designer, Researcher, Photographer, whoever. Each role gets a one-line description; the PM uses those descriptions to match tasks to the right resource.

```toml
[roles]
pm        = "Plans phases, dispatches handoffs, integrates results."
operator  = "Approves locks, unblocks, sets cadence."
coder     = "Implements programming tasks per spec."
reviewer  = "Reviews per acceptance evidence."
designer  = "Owns visual contracts and design decisions."
```

The same agent can wear multiple hats. So can you.

### Automated handoffs (optional)

Add a `[handoffs.<role>]` block and the PM can launch the work itself:

```toml
[handoffs.coder]
agent = "cartopian-codex"
auto_start = true
timeout = "60m"
```

Cartopian ships cross-platform wrappers for **Codex, Claude Code, Gemini, and Devin** under `wrappers/`. They handle non-interactive flags, set the right working directory, and conform to the simple `<agent> <prompt-path>` contract. Bring-your-own works too — anything that fits the contract is a valid agent.

Confirmation is per-handoff by default. Bounded unattended runs are available when you want them. Manual handoff is always supported; automation is opt-in.

See `wrappers/README.md` for setup and `protocol/CONVENTIONS.md` for the full contract.

## Configuration

Two layers:

- **Workspace** `cartopian.toml` at the workspace root — defaults across projects.
- **Project** `cartopian.toml` in each project directory — overrides and project-specific settings.

Run `init workspace` to scaffold them. Edit either with any text editor.

## Layout

The workspace lives next to the product repos it manages:

```text
~/Projects/
├── cartopian/                  ← this repo (the workspace)
│   ├── protocol/               ← baseline protocol docs
│   ├── templates/              ← PROMPT, TASK, SPEC, REVIEW, ...
│   ├── skills/                 ← runbooks the PM follows
│   ├── wrappers/               ← agent CLI wrappers
│   └── projects/               ← per-project PM data (gitignored, own repo)
│       └── <project>/
│           ├── cartopian.toml
│           ├── STATE.md
│           ├── REQUIREMENTS.md
│           ├── IMPLEMENTATION_PLAN.md
│           ├── phases/  prompts/  reports/
│           ├── tasks/{open,in-progress,in-review,done}/
│           ├── specs/  decisions/  reviews/
│           └── archive/
│
├── <project-a-repo>/           ← sibling product repo
└── <project-b-repo>/
```

Status is a directory. Moving a task file *is* the status update — no metadata to sync, no DB to migrate, no integration that breaks when the vendor pivots.

## Protocol

The contracts are in `protocol/CONVENTIONS.md`. The executable workflows are in `skills/`. Both are plain markdown and meant to be read by humans and agents alike.

## License

MIT. See `LICENSE`.
