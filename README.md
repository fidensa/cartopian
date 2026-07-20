# Cartopian

**Structure any project into plans, phases, and tasks for humans or AI agents. Add governance, bounded automation, and optional review while keeping context focused and token use low.**

Cartopian turns "I want to do X" into clear requirements, a comprehensive plan, logical phases, structured tasks, practical work specifications, and tracked outcomes. It can govern technical and nontechnical projects alike, from building a SaaS product to launching an Etsy store or organizing a weekend garage sale. Tasks can go to AI agents or people. The Project Manager coordinates assignments, evidence, and progress; automates agent handoffs within limits you set; and can add independent review loops that catch gaps and errors before they spread.

Cartopian also treats an AI model's context window and token budget as scarce resources. Deterministic bookkeeping runs through the CLI instead of consuming model reasoning, and each command returns a compact answer rather than making the model reread project files. Protocol runbooks and task materials enter context only when the current step needs them, and tools are made available as the workflow calls for them. Curated, task-sized handoffs and a compact state file keep agents focused without carrying the entire project history into every conversation.

## What it actually does

- **Plans the work.** An AI Project Manager interviews you, drafts requirements, breaks them into phases, and emits tasks with acceptance criteria.
- **Reviews the plan when you want it to.** Planning review is an explicit project policy, independent of task-closure review, and can be assigned to any named role.
- **Tracks progress.** Phases, tasks, decisions, reviews, and session state live as plain markdown, so progress is visible at a glance and survives any tool change.
- **Writes the specs.** Each task gets a real spec, not a vibes-based prompt. Decisions get recorded as they happen, so your future self knows why.
- **Orchestrates the doers.** Roles map tasks to the right assignee. Work can go to an AI agent, a human collaborator, or you in any role the project needs, such as researcher, programmer, reviewer, designer, or photographer. Only the Operator and Project Manager roles are required. The PM hands off, collects results, and integrates.
- **Closes the loop.** Every task produces durable completion evidence. Projects that require task review add a verdict loop: `approve` lands the task in `done`, `request-changes` returns it to the assignee with findings, and `reject` reopens it. Projects that turn task review off close directly from an accepted report.
- **Spends your tokens carefully.** Status reads, task selection, prompt assembly, report parsing, and plan audits are computations rather than conversations. The CLI handles them without bloating the model's context.
- **Stays out of your way.** No database, SaaS control plane, or third-party Python package is required. Git and automation are optional, roles are operator-chosen, and every decision is overridable.

## How it works

Once Cartopian is installed and registered with your agent, open it from any directory and enter PM mode with the entry trigger. In most clients, that is the `/use-cartopian` command. See [Entry point](#entry-point) for the form used by each client. There is no working directory to set or path to remember because project context comes from the registry rather than the current directory.

That one command is roughly the last command you type. The PM checks for updates, finds your registered projects, and asks which one to open. If you have none, it scaffolds a new one. From there, it drives the whole lifecycle itself:

```text
init project   →   plan project   →   start session   →   run tasks   →   close plan
```

Those are the runbooks the PM follows, not commands you memorize. On a new project, it interviews you, produces a requirements document, drafts a plan, breaks it into phases and tasks, and stores everything on disk as plain markdown. When you return, it reads the current state, tells you where things stand, and continues with the next task. It can dispatch that task to a configured CLI agent or assign it directly to you. When the plan is complete, it offers to close and archive it. Your side of the session is conversational: describe what you want, answer the PM's questions, and make the decisions the protocol reserves for you.

## The loops: plan → optional review and outcome → optional review

Cartopian has two independent review policies. A project may require planning review only, task-closure review only, both, or neither. Review policy names the role responsible for the checkpoint; role descriptions help assignment, while capability grants independently control what that role may access.

**Plan → optional review.** When `[reviews].planning = "required"`, the role named by `planning_role` gets a checkpoint after each planning stage: requirements, the implementation plan, the phase breakdown, and the tasks/specs. The findings land as a durable review file; the PM integrates them before moving on. Set `auto_start_reviews = true` on that role's handoff block when the PM should launch planning-review handoffs automatically. When planning review is off, the PM advances without manufacturing a reviewer role or an empty review artifact.

**Outcome → optional review.** During `run task`, the assignee completes the requested outcome and writes a completion report. With `[reviews].task_closure = "required"`, the PM moves the task to `in-review` and hands it to `task_role`; the verdict drives the next move deterministically. With task review off, an accepted report moves directly to `done`. In both modes the CLI verifies the evidence on disk before moving the task.

Require both review policies and opt in to each automation layer, and the loop runs from end to end. The following example is a complete unattended configuration that uses the conventional `reviewer` role name:

```toml
[automation]
initiation = "auto"              # runs may begin without you saying "continue"
confirmation = "until-blocked"   # chain through tasks until something needs a human
max_handoffs_per_run = 2         # bounded unattended runs

[roles]
pm = "Surface the evidence and facilitate operator decisions"
operator = "Designs the product and provides direction to guide implementation"
coder = "Implements tasks per spec"
reviewer = "Reviews against acceptance criteria and original operator intent"

[roles.coder]
grants = ["coder-like"]

[roles.reviewer]
grants = ["reviewer-like"]

[handoffs.coder]
agent = "cartopian-codex"
auto_start_tasks = true

[handoffs.reviewer]
agent = "cartopian-gemini"
auto_start_tasks = true
auto_start_reviews = true

[reviews]
planning = "required"
planning_role = "reviewer"
task_closure = "required"
task_role = "reviewer"

[defaults]
git_versioning = false     # git automation can be enabled; more robust support is coming soon
```

With that configuration, running a task means: assign → complete → report → review → apply verdict → start the next task. The run stops only for blockers, failures, phase boundaries, decisions reserved for you, or the run budget. The three automation authorities are separate. `initiation` determines **whether a run begins**, `confirmation` controls **pace** within a run, and **selection** is never gated. Task order is deterministic: Cartopian selects the first open task in plan order whose dependencies are satisfied. The question "Which task comes next?" is therefore a computation rather than a conversation, although a ready queue does not itself grant permission to run. The defaults require your participation: `initiation = "operator"` means the PM names the next task and waits for you to say "continue," while `confirmation = "each-handoff"` allows one handoff at a time. Asking "What's next?" is always read-only, and "stop" always takes precedence over configuration.

## Built for small context windows

Token burn and context noise are first-class design constraints, not afterthoughts:

- **Bookkeeping is code, not reasoning.** Reading state, choosing the next task, validating readiness, assembling handoff inputs, parsing completion reports, and auditing the plan are all `cartopian` CLI subcommands exposed as MCP tools. Each returns one compact, structured record. The model consumes that answer instead of deriving it again from raw files, which reduces token use and produces consistent results.
- **Only what is needed, when it is needed.** The PM brings the relevant protocol runbook and task materials into context, then uses only the tools required for the current step. It does not load the entire protocol and project history at once.
- **Status is a directory.** Moving a task file between status directories *is* the status update. Nothing to sync, nothing to reconcile, nothing to reread and summarize.
- **Handoffs are curated, not dumped.** A dispatched agent receives exactly what the task needs, including the specification, acceptance criteria, and absolute paths. It does not receive your conversation history or irrelevant project archaeology. Optional capability grants go further: a contained coder can read its prompt and the product tree but cannot read governance documents, reports, or reviews it does not need.
- **Session state is one small file.** `STATE.md` is capped at 5KB and names the current phase, active work, open work, blockers, and the exact next action. That's the entire cost of resuming a project.
- **Transients get cleaned up.** Prompts and handoff status files are deleted when superseded; durable knowledge is distilled into tasks, reviews, and decisions. The working set stays small on disk and in context.

> **Recommendation: start a new session after each task.** Everything the next task needs is already on disk in `STATE.md`, the task file, and the specification. A fresh `/use-cartopian` rebuilds the working context from a few kilobytes and continues where you left off. A long-running session, by contrast, carries every previous task's conversation as noise. Starting a new session for each task provides maximum savings without losing state.

## Install

Requirements: **Python 3.11+** on your PATH. The stock `/usr/bin/python3` on macOS is version 3.9, so use `brew install python@3.11` or any Python 3.11+ interpreter. That is all. No Git knowledge is required.

Open a shell-capable AI agent, such as Claude Code, Codex, Gemini CLI, Devin, or Windsurf. Any MCP-aware agent that can read a URL and run shell commands will work. Tell it:

> Install Cartopian by following https://raw.githubusercontent.com/fidensa/cartopian/main/install-cartopian.md

That step-by-step runbook guides the agent through detecting your platform, fetching the latest release, copying it into `~/.cartopian/` (or `%USERPROFILE%\.cartopian\` on Windows), adding `bin/` and the platform wrapper directory to your user PATH, **registering Cartopian's MCP server with your agent and installing its entry trigger**, and verifying the installation. Operator-owned files (`cartopian.toml`, `projects.json`) are preserved across reruns. The full runbook is `install-cartopian.md`.

**Upgrade** the same way: ask any Cartopian-aware agent to `check for updates`. It compares your installed version against the latest release and reinstalls on your approval.

Verify the install with:

```bash
cartopian --help
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | cartopian-mcp
```

The first command exits 0 with the CLI subcommand list. The second emits a single JSON-RPC line containing `"name":"cartopian"` (the `initialize` response's server info). On native Windows, the installer ships `bin/cartopian.cmd` and `bin/cartopian-mcp.cmd` shims so both commands resolve in PowerShell and `cmd.exe` once `bin/` is on PATH (open a new shell first). The post-install checklist lives at `~/.cartopian/protocol/INSTALL_VERIFICATION.md`.

## Entry point

Registration installs a small **trigger bridge** for each agent that maps an entry trigger to the MCP server's `use_cartopian` prompt. Use it from any directory. It loads the prompt, puts the agent in PM mode, and routes to the first useful action: `start session` if you have a registered project, or `init project` if you do not.

The reliable, cross-client form is the **`/use-cartopian`** command. Where a description-matched skill bridge is installed, the bare phrase **"use cartopian"** also works. By client:

| Client | Enter PM mode with |
| --- | --- |
| Claude Code | say "use cartopian" or `/use-cartopian` |
| Codex | `/use-cartopian` |
| Gemini | `/use-cartopian` |
| Windsurf | `/use-cartopian` (the natural-language phrase is best-effort) |
| Devin for Terminal | say "use cartopian" or `/use-cartopian` |
| Claude Desktop, Cursor | invoke the `use_cartopian` MCP prompt from the client's prompt picker (MCP only; no local bridge) |

To register more agents later or reinstall a trigger bridge, run the `register mcp` skill. See `install-cartopian.md` for the installation and registration flow and the authoritative instructions for each client.

## Getting started

Once you are in PM mode, everything happens through plain conversation. There is no command vocabulary to learn. The PM proposes the next protocol action; you say yes, no, or what you actually want; and it routes to the appropriate runbook. In a typical session, you describe the project, answer interview questions, and rule on the decisions the protocol reserves for you. The PM handles the rest.

Under the hood, the PM is executing these skills:

| Skill | What the PM does with it |
| --- | --- |
| `init workspace` | Sets up your config defaults (global and project `cartopian.toml`) |
| `init project` | Scaffolds and registers a new project |
| `adopt requirements` | Imports requirements from JIRA, a PRD, Confluence, etc. |
| `adopt plan` | Pulls an existing plan into Cartopian's shape |
| `plan project` | Drives the full lifecycle: requirements → plan → phases → tasks |
| `start session` | Answers "Where were we?" by reading state and continuing with the next action |
| `run task` | Drives one task from assignment through evidence-supported closure and any required review |
| `run handoff` | Executes one prompt/report handoff |
| `close plan` | Closes the active plan and resets for the next |
| `register mcp` | Registers `cartopian-mcp` with more agents and installs their entry trigger |
| `check for updates` | Compares the installed version with the latest release and upgrades on approval |

You can name any of these skills to jump straight to it. This is useful for an occasional out-of-band action, such as importing requirements midstream, registering another agent, or forcing an update check. The entry point already checks for updates on its own, and you will not need to name skills during an ordinary session.

See `skills/README.md` for the full index.

## Roles and AI orchestration

The default roster is **PM** and **Operator**, the planner and the decision-maker. From there, you can name whatever roles your project needs: Coder, Reviewer, Designer, Researcher, Photographer, or any other role. Each role gets a one-line description, which the PM uses to match tasks to the appropriate resource. Names and descriptions do not confer protocol authority. Review responsibility comes only from `[reviews]`, and access comes only from capability grants when containment is active. `reviewer` is the conventional example below, although a project may assign the same policy to another role name.

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

The same agent can wear multiple hats, and you can, too.

Roles can also carry **capability grants** (`grants = [...]`), which turn the description into an enforced boundary. What a role may read and write is gated at the harness level and determined solely by its grants. Presets such as `coder-like` and `reviewer-like` cover common configurations. Capability grants are fully optional; configurations without them behave exactly as before. See `CAPABILITIES.md`.

### Automated handoffs (optional)

Add a `[handoffs.<role>]` block and the PM can launch the work itself:

```toml
[handoffs.coder]
agent = "cartopian-codex"
model = "gpt-5-codex"
effort = "high"
auto_start_tasks = true
timeout = "60m"

[handoffs.reviewer]
agent = "cartopian-gemini"
auto_start_tasks = true    # task-closure review handoffs
auto_start_reviews = true  # planning-review handoffs
timeout = "30m"
```

Cartopian ships cross-platform wrappers for **Codex, Claude Code, Gemini, and Devin** under `wrappers/`. They handle non-interactive flags, set the appropriate working directory, and conform to the simple `<agent> <prompt-path>` contract. You can also bring your own agent; anything that fits the contract is valid.

The optional `model` key pins the assigned agent to a specific model. Dispatch exports it to the wrapper as the agent-neutral `CARTOPIAN_MODEL` environment variable; all four shipped wrappers translate it into the tool's `--model` flag. When unset, the tool's own default model applies.

The optional `effort` key sets an effort or thinking level in the same way. Dispatch exports it as `CARTOPIAN_EFFORT`, and the wrapper translates it into the tool-specific flag (`claude --effort`, codex `-c model_reasoning_effort=`). A value outside the wrapper's vocabulary degrades gracefully. The wrapper prints a one-line notice to standard error, omits the flag, and launches the agent at its default effort. The Gemini and Devin CLIs have no effort flag, so those wrappers ignore `CARTOPIAN_EFFORT` with a notice.

`auto_start_tasks` controls automatic task-scoped launches, including task-closure review. `auto_start_reviews` independently controls automatic planning-review launches. Neither setting enables a review stage or makes a role a reviewer; the `[reviews]` policy and assignment do that. Both launch settings are disabled by default and enforced fail-closed by `cartopian dispatch`.

The defaults require your participation: execution starts on your directive, and confirmation occurs for each handoff. Bounded unattended runs are available when you want them. Each layer (`initiation`, `confirmation`, and the applicable `auto_start_*` setting) is a separate opt-in under `[automation]`, as shown above. Manual handoff is always supported, and automation remains optional.

See `wrappers/README.md` for setup and `protocol/CONVENTIONS.md` for the full contract.

## Configuration

Cartopian uses a global configuration file, a committed configuration file for each project, and an optional machine-local work-root map. Built-in protocol defaults provide fallback behavior, but they are not another file or a TOML section. Each configuration section has its own resolution rules.

### Configuration files

- **Global configuration:** `~/.cartopian/cartopian.toml` holds workspace-wide defaults for projects that do not override them.
- **Project configuration:** `<project-root>/cartopian.toml` identifies the project, declares its portable settings, and overrides supported global values. This file is committed with the project when Git versioning is enabled.
- **Machine-local work roots:** `<project-root>/cartopian.local.toml` maps declared work-root names to absolute paths on one machine. It is gitignored and is not a general configuration override file.

### TOML sections

| Section | Where it belongs | Purpose |
| --- | --- | --- |
| `[project]` | Project | Required project `name`, `id`, and `protocol_version`, plus optional `work_roots` names |
| `[defaults]` | Global or project | The `git_versioning` switch |
| `[git]` | Global or project | Optional PM-owned product-branch behavior, branch naming, and merge strategy |
| `[automation]` | Global or project | Run initiation, confirmation pace, and the handoff limit for each run |
| `[roles]` and `[roles.<name>]` | Global or project | Role descriptions and optional capability grants |
| `[reviews]` | Global or project | Independent planning and task-closure policies and the role assigned to each required loop |
| `[handoffs.<role>]` | Global or project | Agent, model, effort, launch permissions, and timeout for an automated role handoff |
| `[work_roots]` | Machine-local file only | Absolute path mappings for names declared by `[project].work_roots` |

For sections shared by the global and project files, project values take precedence over global values. Resolution is performed at the key or role level as appropriate for that section. Built-in defaults fill supported values that remain unset, including the `pm` and `operator` roles, attended automation, reviews set to `off`, and Git versioning set to `false`. The `[project]` table comes only from the project file. The machine-local `[work_roots]` table only supplies paths for names declared in that project's `[project].work_roots` list.

`cartopian resolve-config <project-root>` validates these sources together and returns the effective configuration. It fails when, for example, a declared work root has no machine-local path or a handoff names an undeclared role.

Run `init workspace` to establish global defaults and `init project` to create a project configuration. The PM manages changes inside a project through the validated `cartopian update-config` command after you request them. The workspace setup flow owns the global file, which can also be edited directly by the operator.

The `[project].protocol_version` value records the project's protocol schema version so Cartopian can identify applicable migrations. It is separate from the installed Cartopian release version and is maintained by the migration workflow after operator approval.

## Protocol

The contracts are in `protocol/CONVENTIONS.md`, the authoritative reference for project structure, lifecycle, roles, and handoffs. The executable workflows are in `skills/`. Both are plain markdown intended for humans and agents alike.

Status is a directory: moving a task file between status directories *is* the status update. There is no metadata to synchronize and no database to migrate. Projects can live anywhere on disk and are found through the registry (`projects.json`) rather than a fixed directory tree.

## License

This project is distributed under a custom license. See `LICENSE` for the full terms.
