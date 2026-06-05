# Cartopian

**A filesystem-first project-governance protocol for AI-native development.**

Cartopian turns "I want to do X" into a tracked plan, logical phases, structured tasks, real specs, and dispatched work - recorded as plain markdown files with directory-as-status conventions. No database, no SaaS dependency, no third-party packages: it is self-contained and runs on the Python standard library alone. It's flexible enough to run a SaaS product, an Etsy store launch, or a weekend garage sale, and disciplined enough that an AI agent can pick the project back up tomorrow and keep going.

## What it actually does

- **Plans the work.** An AI Project Manager interviews you, drafts requirements, breaks them into phases, and emits tasks with acceptance criteria.
- **Tracks progress.** Phases, tasks, decisions, reviews, and session state live as plain markdown so progress is visible at a glance — and survives any tool change.
- **Writes the specs.** Each task gets a real spec, not a vibes-based prompt. Decisions get recorded as they happen, so future-you knows why.
- **Orchestrates the doers.** Roles map tasks to the right resource: a programmer agent, a reviewer agent, a designer, or you. Define any role you need; only the Operator and Project Manager roles are required. The PM hands off, collects results, and integrates.
- **Automates the boring parts.** Handoffs to CLI agents (Codex, Claude Code, Gemini, Devin, or others) can be one-tap or fully unattended, with timeouts and confirmation gates you control.
- **Stays out of your way.** Git is optional. Automation is optional. Roles are operator-chosen. Every decision is overridable.

## How it feels in practice

Once installed and registered with your agent, open it from any directory and enter PM mode with the entry trigger - in most clients that's the `/use-cartopian` command (see [Entry point](#entry-point) for the per-client form). No working directory to set, no path to remember: project context comes from the registry, not the current directory.

From there the skill progression is:

```text
init project   →   plan project   →   start session   →   run task   →   close plan
```

Tell the PM what you want to build. It interviews you, produces a requirements doc, drafts a plan, breaks it into phases and tasks, and parks everything on disk as plain markdown. When you come back, **"start session"** reads the current state and tells you what's next. **"Run task"** dispatches the work - to a CLI agent if you've wired one up, or to you directly. When the plan is done, **"close plan"** archives it and you're ready for the next one.

## Install

Requirements: **Python 3.11+** on your PATH. (macOS users: the stock `/usr/bin/python3` is 3.9 — use `brew install python@3.11` or any 3.11+ interpreter.) That's it. No git knowledge required.

Open a shell-capable AI agent (Claude Code, Codex, Gemini CLI, Devin, Windsurf - any MCP-aware agent that can read a URL and run shell commands) and tell it:

> Install Cartopian by following https://raw.githubusercontent.com/fidensa/cartopian/main/install-cartopian.md

That runbook is a step-by-step skill the agent reads and executes: detect your platform, fetch the latest release, copy it into `~/.cartopian/` (or `%USERPROFILE%\.cartopian\` on Windows), add `bin/` and the platform wrapper directory to your user PATH, **register Cartopian's MCP server with your agent and install its entry trigger**, and verify. Operator-owned files (`cartopian.toml`, `projects.json`) are preserved across re-runs. The full runbook is `install-cartopian.md`.

**Upgrade** the same way: ask any Cartopian-aware agent to `check for updates`. It compares your installed version against the latest release and re-installs on your approval.

Verify the install with:

```bash
cartopian --help
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | cartopian-mcp
```

The first command exits 0 with the CLI subcommand list. The second emits a single JSON-RPC line containing `"name":"cartopian"` (the `initialize` response's server info). On native Windows the installer ships `bin/cartopian.cmd` and `bin/cartopian-mcp.cmd` shims so both commands resolve in PowerShell and `cmd.exe` once `bin/` is on PATH (open a new shell first). The post-install checklist lives at `~/.cartopian/protocol/INSTALL_VERIFICATION.md`.

## Entry point

Registration installs a small **trigger bridge** for each agent that maps an entry trigger onto the MCP server's `use_cartopian` prompt. Use it from any directory - it loads the prompt, puts the agent in PM mode, and routes to the first useful action (`start session` if you have a project registered, `init project` if not).

The reliable, cross-client form is the **`/use-cartopian`** command. Where a description-matched skill bridge is installed, the bare phrase **"use cartopian"** also works. By client:

| Client | Enter PM mode with |
| --- | --- |
| Claude Code | say "use cartopian" or `/use-cartopian` |
| Codex | `/use-cartopian` |
| Gemini | `/use-cartopian` |
| Windsurf | `/use-cartopian` (the natural-language phrase is best-effort) |
| Devin for Terminal | say "use cartopian" or `/use-cartopian` |
| Claude Desktop, Cursor | invoke the `use_cartopian` MCP prompt from the client's prompt picker (MCP-only - no local bridge) |

To register more agents later, or re-install a trigger bridge, run the `register mcp` skill. See `install-cartopian.md` for the install/register flow and the authoritative per-client recipes.

## Getting started

Once you're in PM mode, you talk to the PM in plain language - you don't have to memorize skill names. Saying a skill's natural-language name jumps straight to it:

| Say this | What happens |
| --- | --- |
| `init workspace` | Sets up your config defaults (global and project `cartopian.toml`) |
| `init project` | Scaffolds and registers a new project |
| `adopt requirements` | Imports requirements from JIRA, a PRD, Confluence, etc. |
| `adopt plan` | Pulls an existing plan into Cartopian's shape |
| `plan project` | Drives the full lifecycle: requirements → plan → phases → tasks |
| `start session` | "Where were we?" — reads state, proposes next action |
| `run task` | Drives one task from assignment through review |
| `run handoff` | Executes one prompt/report handoff |
| `close plan` | Closes the active plan and resets for the next |
| `register mcp` | Registers `cartopian-mcp` with more agents and installs their entry trigger |
| `check for updates` | Compares installed version to latest release; upgrades on approval |

With multiple projects registered, vague requests like *"start working"* prompt the PM to ask which project first. Then it reads `STATE.md`, reports the current or next move, and waits for your go-ahead.

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

The optional `model` key pins the assigned agent to a specific model. Dispatch exports it to the wrapper as the agent-neutral `CARTOPIAN_MODEL` environment variable; all four shipped wrappers translate it into the tool's `--model` flag. When unset, the tool's own default model applies.

Confirmation is per-handoff by default. Bounded unattended runs are available when you want them. Manual handoff is always supported; automation is opt-in.

See `wrappers/README.md` for setup and `protocol/CONVENTIONS.md` for the full contract.

## Configuration

Config resolves in three layers, most-specific first:

- **Project** `cartopian.toml` in each project directory — overrides and project-specific settings.
- **Global** `cartopian.toml` at the install root (`~/.cartopian/`) — defaults shared across projects.
- **Protocol defaults** shipped with the tool — the fallback when neither file sets a key.

A project's committed `cartopian.toml` names its **work roots** (the repos its tasks point at); the per-machine absolute paths those names map to live in a gitignored **`cartopian.local.toml`** beside it. That keeps the committed config identical for every operator while paths stay machine-local. See `protocol/CONVENTIONS.md` for the work-root contract.

Run `init workspace` to scaffold the global and project files. Edit any of them with a text editor.

## Protocol

The contracts are in `protocol/CONVENTIONS.md` - the authoritative reference for project structure, lifecycle, roles, and handoffs. The executable workflows are in `skills/`. Both are plain markdown and meant to be read by humans and agents alike.

Status is a directory: moving a task file between status directories *is* the status update - no metadata to sync, no DB to migrate. Projects live anywhere on disk and are found through the registry (`projects.json`), not a fixed directory tree.

Skills don't make the agent reason through bookkeeping. The deterministic parts — reading state, validating task readiness, assembling handoff prompts, auditing a plan, moving a task between status directories — are handled by `cartopian` CLI subcommands (exposed to MCP clients as the matching tools). The skill calls the command and acts on the result, so that work stays out of the model's context: fewer tokens burned, less noise, and the same answer every time.

## License

This project is distributed under a custom license. See `LICENSE` for the full terms.
