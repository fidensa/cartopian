# Cartopian

**Project management and governance protocol for AI-native work**

Cartopian turns "I want to do X" into a tracked plan, logical phases, structured tasks, real specs, and dispatched work - recorded as plain markdown files with directory-as-status conventions. No database, no SaaS dependency, no third-party packages: it is self-contained and runs on the Python standard library alone. It's flexible enough to run a SaaS product, an Etsy store launch, or a weekend garage sale, and disciplined enough that an AI agent can pick the project back up tomorrow and keep going.

And because the doers are AI agents, Cartopian treats your context window like the scarce resource it is: deterministic bookkeeping runs as CLI commands instead of model reasoning, every handoff gets a curated task-sized prompt instead of the whole project history, and session state compresses to one small file so any session can pick up exactly where the last one stopped.

## What it actually does

- **Plans the work.** An AI Project Manager interviews you, drafts requirements, breaks them into phases, and emits tasks with acceptance criteria.
- **Reviews the plan when you want it to.** Planning review is an explicit project policy, independent from task-closure review, and can be assigned to any named role.
- **Tracks progress.** Phases, tasks, decisions, reviews, and session state live as plain markdown so progress is visible at a glance - and survives any tool change.
- **Writes the specs.** Each task gets a real spec, not a vibes-based prompt. Decisions get recorded as they happen, so future-you knows why.
- **Orchestrates the doers.** Roles map tasks to the right resource: a researcher, programmer, reviewer, designer, photographer, or you. Define any role you need; only the Operator and Project Manager roles are required. The PM hands off, collects results, and integrates.
- **Closes the loop.** Every task produces durable completion evidence. Projects that require task review add a verdict loop: `approve` lands the task in `done`, `request-changes` returns it to the assignee with findings, and `reject` reopens it. Projects that turn task review off close directly from an accepted report.
- **Spends your tokens carefully.** Status reads, task selection, prompt assembly, report parsing, and plan audits are computations, not conversations - handled by the CLI so they never bloat the model's context.
- **Stays out of your way.** Git is optional. Automation is optional. Roles are operator-chosen. Every decision is overridable.

## How it feels in practice

Once installed and registered with your agent, open it from any directory and enter PM mode with the entry trigger - in most clients that's the `/use-cartopian` command (see [Entry point](#entry-point) for the per-client form). No working directory to set, no path to remember: project context comes from the registry, not the current directory.

That one command is roughly the last command you type. The PM checks for updates, finds your registered projects, and asks which one to open - or scaffolds a new one if you have none. From there it drives the whole lifecycle itself:

```text
init project   →   plan project   →   start session   →   run task   →   close plan
```

Those are the runbooks the PM walks through, not commands you memorize. On a new project it interviews you, produces a requirements doc, drafts a plan, breaks it into phases and tasks, and parks everything on disk as plain markdown. When you come back, it reads the current state, tells you where things stand, and continues with the next task on its own - dispatching to a CLI agent if you've wired one up, or to you directly. When the plan is done, it offers to close and archive it. Your side of the session is conversation: describing what you want, answering the PM's questions, and making the decisions the protocol reserves for you.

## The loops: plan → optional review and outcome → optional review

Cartopian has two independent review policies. A project may require planning review only, task-closure review only, both, or neither. Review policy names the role responsible for the checkpoint; role descriptions help assignment, while capability grants independently control what that role may access.

**Plan → optional review.** When `[reviews].planning = "required"`, the role named by `planning_role` gets a checkpoint after each planning stage: requirements, the implementation plan, the phase breakdown, and the tasks/specs. The findings land as a durable review file; the PM integrates them before moving on. Set `auto_start_reviews = true` on that role's handoff block when the PM should launch planning-review handoffs automatically. When planning review is off, the PM advances without manufacturing a reviewer role or an empty review artifact.

**Outcome → optional review.** During `run task`, the assignee completes the requested outcome and writes a completion report. With `[reviews].task_closure = "required"`, the PM moves the task to `in-review` and hands it to `task_role`; the verdict drives the next move deterministically. With task review off, an accepted report moves directly to `done`. In both modes the CLI verifies the evidence on disk before moving the task.

Require both review policies and opt in to each automation layer, and the loop runs end to end — this is a full unattended recipe using the conventional `reviewer` role name:

```toml
[automation]
initiation = "auto"              # runs may begin without you saying "continue"
confirmation = "until-blocked"   # chain through tasks until something needs a human
max_handoffs_per_run = 5         # bounded unattended runs

[roles.coder]
description = "Completes assigned delivery work against its acceptance evidence."
grants = ["coder-like"]

[roles.reviewer]
description = "Independently checks plans and completed outcomes."
grants = ["reviewer-like"]

[reviews]
planning = "required"
planning_role = "reviewer"
task_closure = "required"
task_role = "reviewer"

[handoffs.coder]
agent = "cartopian-codex"
auto_start_tasks = true

[handoffs.reviewer]
agent = "cartopian-gemini"
auto_start_tasks = true
auto_start_reviews = true
```

With that config, running a task means: assign → complete → report → review → verdict applied → next task, stopping only for blockers, failures, phase boundaries, decisions reserved to you, or the run budget. The three automation authorities are separate: `initiation` gates **whether a run begins**, `confirmation` gates **pace** within a run, and **selection** is never gated — task order is deterministic (first open task in plan order, dependencies satisfied), so "which task next" is a computation, not a conversation, but a ready queue is never itself permission to run. The defaults are the attended ones: `initiation = "operator"` (the PM names the next task and waits for your "continue"; asking "what's next?" is always read-only, and "stop" always wins over config) and `confirmation = "each-handoff"` (one handoff at a time, you say when to continue).

## Built for small context windows

Token burn and context noise are first-class design constraints, not afterthoughts:

- **Bookkeeping is code, not reasoning.** Reading state, choosing the next task, validating readiness, assembling handoff inputs, parsing completion reports, and auditing the plan are all `cartopian` CLI subcommands (exposed as MCP tools). Each returns one compact structured record - the model consumes the answer instead of re-deriving it from raw files, so results cost fewer tokens and are the same every time.
- **Status is a directory.** Moving a task file between status directories *is* the status update. Nothing to sync, nothing to reconcile, nothing to re-read and summarize.
- **Handoffs are curated, not dumped.** A dispatched agent gets a prompt containing exactly what the task needs - the spec, acceptance criteria, and absolute paths - not your conversation history or project archaeology. Optional capability grants go further: a contained coder can read its prompt and the product tree, and literally cannot read governance docs, reports, or reviews it doesn't need.
- **Session state is one small file.** `STATE.md` is capped at 5KB and names the current phase, active work, open work, blockers, and the exact next action. That's the entire cost of resuming a project.
- **Transients get cleaned up.** Prompts and handoff status files are deleted when superseded; durable knowledge is distilled into tasks, reviews, and decisions. The working set stays small on disk and in context.

> **Recommendation: start a new session after each task.** Everything the next task needs is already on disk - `STATE.md`, the task file, the spec. A fresh `/use-cartopian` rebuilds full working context from a few kilobytes and continues where you left off, while a long-running session drags every previous task's conversation along as pure noise. New task, new session: maximum savings, zero lost state.

## Install

Requirements: **Python 3.11+** on your PATH. (macOS users: the stock `/usr/bin/python3` is 3.9 - use `brew install python@3.11` or any 3.11+ interpreter.) That's it. No git knowledge required.

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

Once you're in PM mode, everything happens through plain conversation. There is no command vocabulary to learn: the PM proposes the next protocol action, you say yes, no, or what you actually want, and it routes to the right runbook itself. A typical session is you describing the project, answering interview questions, and ruling on the decisions the protocol reserves for you - the PM handles the rest.

Under the hood, the PM is executing these skills:

| Skill | What the PM does with it |
| --- | --- |
| `init workspace` | Sets up your config defaults (global and project `cartopian.toml`) |
| `init project` | Scaffolds and registers a new project |
| `adopt requirements` | Imports requirements from JIRA, a PRD, Confluence, etc. |
| `adopt plan` | Pulls an existing plan into Cartopian's shape |
| `plan project` | Drives the full lifecycle: requirements → plan → phases → tasks |
| `start session` | "Where were we?" — reads state, continues with the next action |
| `run task` | Drives one task from assignment through evidence-supported closure and any required review |
| `run handoff` | Executes one prompt/report handoff |
| `close plan` | Closes the active plan and resets for the next |
| `register mcp` | Registers `cartopian-mcp` with more agents and installs their entry trigger |
| `check for updates` | Compares installed version to latest release; upgrades on approval |

You can name any of these to jump straight to it - handy for the occasional out-of-band move like importing requirements mid-stream, registering another agent, or forcing an update check (though the entry point already checks for updates on its own). In an ordinary session you won't need to.

See `skills/README.md` for the full index.

## Roles and AI orchestration

The default roster is **PM** and **Operator** — the planner and the decider. From there, you name whatever roles your project needs: Coder, Reviewer, Designer, Researcher, Photographer, whoever. Each role gets a one-line description; the PM uses those descriptions to match tasks to the right resource. Names and descriptions do not confer protocol authority: review responsibility comes only from `[reviews]`, and access comes only from capability grants when containment is active. `reviewer` is the conventional example below, but a project may assign the same policy to another role name.

```toml
[roles]
pm        = "Plans phases, dispatches handoffs, integrates results."
operator  = "Approves locks, unblocks, sets cadence."
coder     = "Completes assigned outcomes per spec."
reviewer  = "Checks selected plans and outcomes against acceptance evidence."
designer  = "Owns visual contracts and design decisions."

[reviews]
planning = "required"
planning_role = "reviewer"
task_closure = "off"
```

The same agent can wear multiple hats. So can you.

Roles can also carry **capability grants** (`grants = [...]`), turning the description into an enforced boundary: what a role may read and write is gated at the harness level, keyed on grants alone. Presets like `coder-like` and `reviewer-like` cover the common shapes. Fully optional - configs without grants behave exactly as before. See `CAPABILITIES.md`.

### Automated handoffs (optional)

Add a `[handoffs.<role>]` block and the PM can launch the work itself:

```toml
[handoffs.coder]
agent = "cartopian-codex"
model = "gpt-5-codex"
auto_start_tasks = true
timeout = "60m"

[handoffs.reviewer]
agent = "cartopian-gemini"
auto_start_tasks = true    # task-closure review handoffs
auto_start_reviews = true  # planning-review handoffs
timeout = "30m"
```

Cartopian ships cross-platform wrappers for **Codex, Claude Code, Gemini, and Devin** under `wrappers/`. They handle non-interactive flags, set the right working directory, and conform to the simple `<agent> <prompt-path>` contract. Bring-your-own works too — anything that fits the contract is a valid agent.

The optional `model` key pins the assigned agent to a specific model. Dispatch exports it to the wrapper as the agent-neutral `CARTOPIAN_MODEL` environment variable; all four shipped wrappers translate it into the tool's `--model` flag. When unset, the tool's own default model applies.

`auto_start_tasks` controls automatic task-scoped launches, including task-closure review. `auto_start_reviews` independently controls automatic planning-review launches. Neither setting enables a review stage or makes a role a reviewer; the `[reviews]` policy and assignment do that. Both launch settings default to false/unset and are enforced fail-closed by `cartopian dispatch`.

The defaults are attended: execution starts on your directive and confirmation is per-handoff. Bounded unattended runs are available when you want them — each layer (`initiation`, `confirmation`, and the applicable `auto_start_*` setting) is a separate opt-in (`[automation]`, above). Manual handoff is always supported; automation is opt-in.

See `wrappers/README.md` for setup and `protocol/CONVENTIONS.md` for the full contract.

## Configuration

Config resolves in three layers, most-specific first:

- **Project** `cartopian.toml` in each project directory — overrides and project-specific settings.
- **Global** `cartopian.toml` at the install root (`~/.cartopian/`) — defaults shared across projects.
- **Protocol defaults** shipped with the tool — the fallback when neither file sets a key.

A project's committed `cartopian.toml` names its **work roots** (the repos its tasks point at); the per-machine absolute paths those names map to live in a gitignored **`cartopian.local.toml`** beside it. That keeps the committed config identical for every operator while paths stay machine-local. See `protocol/CONVENTIONS.md` for the work-root contract.

The `[reviews]` table controls planning and task-closure review independently. Each key resolves project → global → protocol default, so a project can explicitly set a mode to `"off"` even when global config requires it. Required modes must name an existing role; no role name, description, handoff, or capability preset implicitly enables review after the explicit-policy migration. Both modes otherwise default to off. Existing projects with the conventional reviewer retain both review loops while their agent-guided migration is pending, unless the operator selects a different preset.

Cartopian also records an internal **project protocol schema version** in project config so agents know which migrations apply. That marker is not the installed Cartopian application's release version, and users normally do not need to read or edit it; Cartopian's migration runbook maintains it on approval.

Run `init workspace` to scaffold the global and project files. Edit any of them with a text editor.

## Protocol

The contracts are in `protocol/CONVENTIONS.md` - the authoritative reference for project structure, lifecycle, roles, and handoffs. The executable workflows are in `skills/`. Both are plain markdown and meant to be read by humans and agents alike.

Status is a directory: moving a task file between status directories *is* the status update - no metadata to sync, no DB to migrate. Projects live anywhere on disk and are found through the registry (`projects.json`), not a fixed directory tree.

## License

This project is distributed under a custom license. See `LICENSE` for the full terms.
