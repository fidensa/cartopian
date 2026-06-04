# Cartopian

**An AI-native project manager that lives in your filesystem.**

Cartopian turns "I want to do X" into a tracked plan, logical phases, structured tasks, real specs, and dispatched work. All without third-party MCP servers, no database, and no project disappearing into a chat window when you close the tab. It's flexible enough to run a SaaS product, an Etsy store launch, or a weekend garage sale, and disciplined enough that an AI agent can pick the project back up tomorrow and keep going.

## What it actually does

- **Plans the work.** An AI Project Manager interviews you, drafts requirements, breaks them into phases, and emits tasks with acceptance criteria.
- **Tracks progress.** Phases, tasks, decisions, reviews, and session state live as plain markdown so progress is visible at a glance — and survives any tool change.
- **Writes the specs.** Each task gets a real spec, not a vibes-based prompt. Decisions get recorded as they happen, so future-you knows why.
- **Orchestrates the doers.** Roles map tasks to the right resource: a programmer agent, a reviewer agent, a designer, or you. Define any role you need; only the Operator and Project Manager roles are required. The PM hands off, collects results, and integrates.
- **Automates the boring parts.** Handoffs to CLI agents (Codex, Claude Code, Gemini, Devin, or others) can be one-tap or fully unattended, with timeouts and confirmation gates you control.
- **Stays out of your way.** Git is optional. Automation is optional. Roles are operator-chosen. Every decision is overridable.

## How it feels in practice

Once installed, open your agent from any directory and say:

> use cartopian

That phrase triggers Cartopian's MCP server and loads PM mode — no working directory to set, no path to remember. From there the skill progression is:

```text
init project   →   plan project   →   start session   →   run task   →   close plan
```

Tell the PM what you want to build. It interviews you, produces a requirements doc, drafts a plan, breaks it into phases and tasks, and parks everything on disk as plain markdown. When you come back, **"start session"** reads the current state and tells you what's next. **"Run task"** dispatches the work — to a CLI agent if you've wired one up, or to you directly.

When the plan is done, **"close plan"** archives it and you're ready for the next one.

## Install

Requirements: **Python 3.11+** on your PATH. (macOS users: the stock `/usr/bin/python3` is 3.9 — use `brew install python@3.11` or any 3.11+ interpreter.) That's it. No git knowledge required.

Open your AI agent of choice (Claude Code, Claude Desktop, Codex, Gemini CLI, Devin, Windsurf, Cursor — any MCP-aware agent that can read a URL and run shell commands) and tell it:

> Install Cartopian by following https://raw.githubusercontent.com/fidensa/cartopian/main/install-cartopian.md

The skill is a step-by-step runbook the agent reads and executes — detect your platform, fetch the latest release, copy it into `~/.cartopian/` (or `%USERPROFILE%\.cartopian\` on Windows), add `bin/` to your user PATH, **register Cartopian's MCP server with your agent**, and verify. Operator-owned files (`cartopian.toml`, `projects.json`) are preserved across re-runs.

After install, open that agent in **any** directory and say:

> use cartopian

That's the entry point. The agent enters Cartopian PM mode, reads the protocol contract, loads the session startup runbook, and begins registry-based project selection — no working directory required, no path to remember. If you have projects registered it moves straight to `start session`; if you don't, it offers `init project`. On MCP-only clients with no command/skill bridge (Claude Desktop, Cursor), trigger the same flow by invoking the `use_cartopian` prompt from the client's prompt picker — every skill is also exposed as an MCP prompt.

**Upgrade** the same way: ask any Cartopian-aware agent to run `check for updates`. It compares your installed version against the latest release and re-installs on your approval.

Verify the install with:

```bash
cartopian --help
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | cartopian-mcp
```

The first command exits 0 with the CLI subcommand list. The second emits a single JSON-RPC line containing `"name":"cartopian"` and `"protocolVersion":"2024-11-05"`.

On native Windows, the installer ships `bin/cartopian.cmd` and `bin/cartopian-mcp.cmd` shims alongside the extensionless Python entrypoints, so PowerShell and `cmd.exe` resolve both commands once `bin/` is on PATH (open a new shell first). The post-install checklist lives at `~/.cartopian/protocol/INSTALL_VERIFICATION.md`.

**Contributors:** if you want a working clone (symlink mode, edit-in-place), use the manual flow:

```bash
git clone https://github.com/fidensa/cartopian.git
python3 cartopian/scripts/install.py
```

This symlinks `~/.cartopian/` back to your clone so edits take effect without re-installing. The agent-driven installer also patches your PATH; under the manual flow you do that yourself. Add both `bin/` and the platform wrapper directory to your user PATH — `bin/` exposes `cartopian` and `cartopian-mcp`, while the wrapper directory (`wrappers/bin` on Unix, `wrappers\ps1` on Windows) exposes the `cartopian-codex`/`cartopian-claude`/… handoff wrappers as bare commands:

```bash
# zsh
echo 'export PATH="$HOME/.cartopian/bin:$HOME/.cartopian/wrappers/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc

# bash
echo 'export PATH="$HOME/.cartopian/bin:$HOME/.cartopian/wrappers/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
```

```powershell
# Windows PowerShell (user-scope PATH; open a new shell after)
$current = [Environment]::GetEnvironmentVariable("Path", "User")
foreach ($dir in "$HOME\.cartopian\bin", "$HOME\.cartopian\wrappers\ps1") {
  if (($current -split ";") -notcontains $dir) { $current = "$dir;$current" }
}
[Environment]::SetEnvironmentVariable("Path", $current, "User")
```

On native Windows, symlink mode requires Developer Mode or an elevated shell — otherwise pass `--mode copy` to `scripts/install.py`.

## Getting started

After install, the entry point is one phrase: **"use cartopian"**. Any registered MCP-aware agent picks it up and routes you to the right skill.

Cartopian ships **skills** — runbooks the agent reads and follows to do real work. You don't have to memorize them; saying `use cartopian` bootstraps PM mode and routes you to the right next step automatically. If you want to jump straight to a specific skill, say its natural-language name:

| Say this | What happens |
| --- | --- |
| `use cartopian` | Enter PM mode. Reads the protocol contract and startup runbook, then selects a project via the registry. Never inspects the current directory. |
| `init workspace` | Sets up your workspace and config defaults |
| `init project` | Scaffolds a new project |
| `adopt requirements` | Imports requirements from JIRA, a PRD, Confluence, etc. |
| `adopt plan` | Pulls an existing plan into Cartopian's shape |
| `plan project` | Drives the full lifecycle: requirements → plan → phases → tasks |
| `start session` | "Where were we?" — reads state, proposes next action |
| `run task` | Drives one task from assignment through review |
| `run handoff` | Executes one prompt/report handoff |
| `close plan` | Closes the active plan and resets for the next |
| `register mcp` | Register `cartopian-mcp` with more agents and install their `use cartopian` / `/use-cartopian` triggers |
| `check for updates` | Compares installed version to latest release; upgrades on approval |

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
model = "gpt-5-codex"
auto_start = true
timeout = "60m"
```

Cartopian ships cross-platform wrappers for **Codex, Claude Code, Gemini, and Devin** under `wrappers/`. They handle non-interactive flags, set the right working directory, and conform to the simple `<agent> <prompt-path>` contract. Bring-your-own works too — anything that fits the contract is a valid agent.

The optional `model` key pins the assigned agent to a specific model. Dispatch exports it to the wrapper as the agent-neutral `CARTOPIAN_MODEL` environment variable; all four shipped wrappers translate it into the tool's `--model` flag. When unset, no variable is exported and the tool's own default model applies.

Confirmation is per-handoff by default. Bounded unattended runs are available when you want them. Manual handoff is always supported; automation is opt-in.

See `wrappers/README.md` for setup and `protocol/CONVENTIONS.md` for the full contract.

## Configuration

Config resolves in three layers, most-specific first:

- **Project** `cartopian.toml` in each project directory — overrides and project-specific settings.
- **Global** `cartopian.toml` at the install root (`~/.cartopian/`) — defaults shared across projects.
- **Protocol defaults** shipped with the tool — the fallback when neither file sets a key.

Per-machine absolute paths (the `work_roots` a project's tasks point at — e.g. the sibling product repo) live in a gitignored **`cartopian.local.toml`** beside each project's `cartopian.toml`. That keeps the committed config identical for every operator while paths stay machine-local.

Run `init workspace` to scaffold the global and project files. Edit any of them with a text editor.

## Layout

The workspace lives next to the product repos it manages:

```text
~/Projects/
├── cartopian/                  ← this repo (the workspace)
│   ├── protocol/               ← baseline protocol docs
│   ├── templates/              ← PROMPT, TASK, SPEC, REVIEW, ...
│   ├── skills/                 ← runbooks the PM follows
│   ├── wrappers/               ← agent CLI wrappers
│   ├── cli/                    ← the cartopian CLI helpers
│   ├── mcp_server/             ← the cartopian-mcp MCP server
│   └── projects/               ← per-project PM data (gitignored, own repo)
│       └── <project>/
│           ├── cartopian.toml        ← committed project config
│           ├── cartopian.local.toml  ← per-machine work_root paths (gitignored)
│           ├── STATE.md  CONVENTIONS.md  STANDARDS.md
│           ├── REQUIREMENTS.md  IMPLEMENTATION_PLAN.md
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

Skills don't make the agent reason through bookkeeping. The deterministic parts — reading state, validating task readiness, assembling handoff prompts, auditing a plan, moving a task between status directories — are handled by `cartopian` CLI subcommands (exposed to MCP clients as the matching tools). The skill calls the command and acts on the result, so that work stays out of the model's context: fewer tokens burned, less noise, and the same answer every time.

## License

MIT. See `LICENSE`.
