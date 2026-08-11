# Cartopian

**Turn any project into plans, phases, and tasks for people or AI agents. Add governance, bounded automation, and optional review while keeping context focused and token use low.**

Cartopian turns "I want to do X" into clear requirements, a plan, phases, tasks, work specifications, and tracked results. It governs technical and non-technical projects alike, from building a SaaS product to launching an Etsy store or organizing a garage sale. Tasks go to AI agents or to people. An AI Project Manager coordinates assignments, evidence, and progress, hands work off within limits you set, and can add independent review that catches gaps before they spread.

Everything is plain markdown on your disk. There is no database, no hosted service, and no third-party Python package.

## What it does

- **Plans the work.** The Project Manager interviews you, drafts requirements, breaks them into phases, and writes tasks with acceptance criteria.
- **Writes real specifications.** Each task gets a work contract, not a vague prompt. Decisions get recorded as they happen, so your future self knows why.
- **Tracks progress in the open.** Phases, tasks, decisions, reviews, and session state are markdown files. Progress is visible at a glance and survives any tool change.
- **Hands work to the right doer.** You name the roles your project needs. Work goes to an AI agent, a teammate, or you.
- **Reviews the plan, if you want it to.** Planning review is a policy you set. It is independent of task review and can be assigned to any role you name.
- **Closes the loop.** Every task produces durable evidence. With task review on, `approve` moves the task to done, `request-changes` sends it back, and `reject` reopens it.
- **Checks the work against your own words.** Reviews compare what was delivered with the exact request you made, not just with what the PM wrote down.
- **Spends tokens carefully.** Status reads, task selection, prompt assembly, report parsing, and audits are computations, not conversations.
- **Stays out of your way.** Git and automation are optional, roles are yours to choose, and every decision is yours to override.

## Install

You need **Python 3.11 or newer** on your PATH. The `/usr/bin/python3` that ships with macOS is 3.9, so install a newer one with `brew install python@3.11` or any equivalent. Nothing else is required, and you do not need to know Git.

Open a shell-capable AI agent such as Claude Code, Codex, Antigravity, Devin, Windsurf, opencode, or Hermes. Any MCP-aware agent that can read a URL and run shell commands works. Tell it:

> Install Cartopian by following https://raw.githubusercontent.com/fidensa/cartopian/main/install-cartopian.md

That runbook walks the agent through detecting your platform, fetching the latest release, planning every change before making it, copying files into `~/.cartopian/` (or `%USERPROFILE%\.cartopian\` on Windows), adding `bin/` and the wrapper directory to your PATH, **registering Cartopian's MCP server with your agent and installing its entry trigger**, and verifying the result.

Your own files, `cartopian.toml` and `projects.json`, are preserved across reinstalls. Repairs that need new permission stay on offer until you accept, decline, or defer them. The final verified result is written to `<install-root>/install-update-state.json`. The full runbook is `install-cartopian.md`.

Check the install with:

```bash
cartopian --help
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | cartopian-mcp
```

The first command lists the CLI subcommands. The second prints one JSON line containing `"name":"cartopian"`. On Windows, the installer ships `bin/cartopian.cmd` and `bin/cartopian-mcp.cmd` so both work in PowerShell and `cmd.exe` once `bin/` is on PATH. Open a new shell first. The post-install checklist is at `~/.cartopian/protocol/INSTALL_VERIFICATION.md`.

**Upgrading** works the same way. Ask any Cartopian-aware agent to `check for updates`. It compares your installed version against the latest release and reinstalls once you approve.

## Starting a session

Registration installs a small trigger for each supported agent. Use it from any directory, after whatever restart your client needs. There is no working directory to set and no path to remember, since project context comes from a registry rather than the current folder.

The reliable cross-client form is the **`/use-cartopian`** command. Where a description-matched skill is installed, the plain phrase **"use cartopian"** works too.

| Client | Enter PM mode with |
| --- | --- |
| Claude Code | say "use cartopian" or `/use-cartopian` |
| Codex | `/use-cartopian` |
| Antigravity | `/use-cartopian` |
| Windsurf | `/use-cartopian` (the plain phrase is best-effort) |
| Devin for Terminal | say "use cartopian" or `/use-cartopian` |
| opencode | `/use-cartopian` |
| Hermes | say "use cartopian" (or preload with `-s use-cartopian`) |
| Claude Desktop, Cursor | pick the `use_cartopian` prompt from the client's MCP prompt picker |

To register more agents later, or to reinstall a trigger, run the `register mcp` skill.

That one command is roughly the last command you type. The PM checks for updates, finds your registered projects, and asks which one to open. If you have none, it scaffolds one. From there it drives the lifecycle itself:

```text
init project   →   plan project   →   start session   →   run tasks   →   close plan
```

Those are runbooks the PM follows, not commands you memorize. On a new project it interviews you, produces a requirements document, drafts a plan, breaks it into phases and tasks, and stores everything as markdown. When you come back, it reads the current state, tells you where things stand, and continues with the next task. Your side of the session is a conversation: describe what you want, answer questions, and make the calls the protocol reserves for you.

> **Tip: start a new session after each task.** Everything the next task needs is already on disk. A fresh `/use-cartopian` rebuilds your working context from a few kilobytes. A long session carries every earlier task's chatter as noise.

## What lives on disk

A Cartopian project is a normal folder:

```text
my-project/
  cartopian.toml          project settings, safe to commit
  cartopian.local.toml    absolute paths for this machine, never committed
  REQUIREMENTS.md         what you asked for
  IMPLEMENTATION_PLAN.md  the live plan, one per project
  STANDARDS.md            tools, conventions, and constraints
  STATE.md                where you left off, capped at 5KB
  phases/                 PHASE-01.md, PHASE-02.md, ...
  tasks/open|in-progress|in-review|done/
  specs/                  the work contract for each task
  prompts/                handoffs in flight, deleted when superseded
  reports/                what each assignee delivered
  reviews/                review verdicts and findings
  decisions/              why you chose what you chose
  resources/              reference material the project needs
```

Status is a directory. Moving a task file from `tasks/open/` to `tasks/in-progress/` **is** the status update. There is nothing to sync and no database to migrate.

Projects can live anywhere. Cartopian finds them through its registry (`projects.json`) rather than a fixed tree.

**Tip: Where to store your project on disk** Usually, you will want to store your project management directory and your actual project work in separate places. A good practice is to name your project management directory the same name as your project, but with `-manager` appended. For example, `/cartopian` is the work root for this project and the project management directory is `/cartopian-manager`

## Roles and who does the work

The default roster is **PM** and **Operator**, the planner and the decision-maker. Add whatever roles your project needs: coder, reviewer, designer, researcher, photographer, or anything else. Each role gets a one-line description that helps the PM match work to the right assignee.

Names and descriptions carry no authority. Review duty comes only from `[reviews]`. Access comes only from capability grants. `reviewer` below is a conventional name, not a special one.

```toml
[roles.coder]
description = "Completes assigned outcomes per spec."

[roles.reviewer]
description = "Checks selected plans and outcomes against acceptance evidence."

[roles.designer]
description = "Owns visual contracts and design decisions."
```

The same agent can wear several hats, and so can you.

Roles can also carry **capability grants**, which turn a description into an enforced boundary. Presets such as `coder-like` and `reviewer-like` cover the common cases, and you can compose them with individual capabilities. Grants are optional, and a project without them behaves exactly as before. See `CAPABILITIES.md` for the full list and how it is enforced.

### Handing work to an AI agent

Give a role an agent, and separately give it permission to launch:

```toml
[roles.coder]
description = "Completes assigned outcomes per spec."
grants = ["coder-like"]
auto_launch = ["task_run"]

agent = "cartopian-codex"
model = "gpt-5-codex"
effort = "high"
timeout = "60m"
```

Cartopian ships wrappers for **Codex, Claude Code, Antigravity, Devin, opencode, and Hermes** (Hermes experimental — see above): `cartopian-codex`, `cartopian-claude`, `cartopian-agy`, `cartopian-devin`, `cartopian-opencode`, and `cartopian-hermes`. They set the non-interactive flags, choose the right working directory, and follow one simple contract: `<agent> <prompt-path>`. Bring your own agent if you prefer. Anything that fits the contract is valid.

`model` pins the agent to one model. `effort` sets a thinking or effort level. Cartopian passes both to the wrapper, which translates them into that tool's own flags. A value a tool does not recognize is dropped with a short notice, and the agent runs at its default. Devin has no effort flag, so its wrapper ignores it; Antigravity translates effort into its `--effort` flag (dropped with a notice when the pinned model id already encodes an effort suffix), opencode into its `--variant` flag, and Hermes into its `--reasoning` flag.

`auto_launch` accepts only `task_run`, `task_review`, and `planning_review`. Each value applies to one kind of assigned work. It never turns a review stage on, assigns review duty, starts a run, or picks a task. A role with an agent but no permission still works through handoffs you start yourself. See `wrappers/README.md` for setup and `CONFIG-MAPPING.md` for every accepted value.

## The two review loops

Cartopian has two independent review policies. A project can require planning review, task review, both, or neither.

**Plan, then optional review.** With `[reviews] planning = "required"`, the role named by `planning_role` gets a checkpoint after each planning stage: requirements, the plan, the phase breakdown, and the tasks and specs. Findings land in a review file, and the PM works them in before moving on. With planning review off, the PM advances without inventing a reviewer or an empty review file.

**Outcome, then optional review.** During `run task`, the assignee delivers the outcome and writes a completion report. With `task_closure = "required"`, the PM moves the task to `in-review` and hands it to `task_role`. The verdict decides the next move. With task review off, an accepted report moves the task straight to done. Either way, Cartopian verifies the evidence on disk before moving anything.

### Your own words are part of every review

A review that only compares the work against PM-written instructions cannot catch the PM drifting from what you actually asked for. Cartopian closes that gap.

Before a task assignment or any review, Cartopian gathers the exact wording of your request from decisions that quote you, supported chat records your client provides, and optional saved request records. It puts those quotations into the prompt under their own heading, kept separate from everything the PM wrote later. The reviewer compares the two.

Every excerpt keeps its source, its position in the sequence, and a fingerprint of its exact text, so nothing can be quietly reworded. Explicit corrections you make later stay in order. Unrelated conversation and the assistant's own words are never promoted into the trace.

The review then records one of these:

```text
Request alignment: aligned | drifted | unavailable-for-legacy
```

`drifted` blocks approval, even when every PM document agrees with the implementation. Added features, changed destinations, quietly narrowed scope, and dropped requirements are all drift. Permission to propose an option is not permission to build it. Work that genuinely predates this capture is marked `unavailable-for-legacy` and does not block.

There is no setting for any of this, and no role can weaken it. Tasks generated from an approved plan inherit the project's request through their verified plan ancestry, so you never restate yourself for each task. An ad-hoc task with no plan lineage inherits nothing and needs its own evidence.

## Bounded automation

Three separate settings control how much runs without you:

```toml
[automation]
initiation = "auto"              # a run may begin without you saying "continue"
confirmation = "until-blocked"   # keep going until something needs a human
max_handoffs_per_run = 2         # never more than this in one run
```

`initiation` decides **whether a run begins**. `confirmation` controls the **pace** inside a run. `max_handoffs_per_run` is the **ceiling**. Task selection is never gated: Cartopian always takes the first open task in plan order whose dependencies are met, so "what comes next?" is a computation rather than a discussion. A ready queue is not permission to run.

The defaults keep you involved. `initiation = "operator"` means the PM names the next task and waits. `confirmation = "each-handoff"` means one handoff at a time. Asking "what's next?" is always read-only, and "stop" always wins over configuration.

## Risk and practice

Every new task records five plain observations: how far its effects reach, how reversible it is, whether authority is settled, how ambiguous it is, and how well the evidence covers it. Cartopian turns those into one band, from `routine` up through `bounded`, `consequential`, and `critical`. There are no scores and no averaging. The highest observation sets the band.

The band scales expectations for evidence, independent review, operator approval, and contingency. It never edits your configured policy. When the band expects more review than your settings provide, Cartopian surfaces the difference for you to decide.

Cartopian also selects a **practice pack** for a task when one clearly applies, drawing on packs for software, research, marketing, operations, and policy work. Selection is deterministic, and no pack overrides your configuration.

## Configuration

Cartopian reads a global file, a project file, and an optional machine-local file. Built-in defaults fill anything you leave unset.

- **Global:** `~/.cartopian/cartopian.toml` holds defaults for every project. The installer seeds it fully commented out, so it starts empty.
- **Project:** `<project-root>/cartopian.toml` identifies the project and carries its settings. Commit it.
- **Machine-local:** `<project-root>/cartopian.local.toml` maps work-root names to absolute paths on one machine. It is gitignored.

| Section | Where it belongs | Purpose |
| --- | --- | --- |
| `[project]` | Project | Required `name`, `id`, and `project_schema_version`, plus optional `work_roots` names |
| `[defaults]` | Global or project | The `git_versioning` switch |
| `[git]` | Global or project | Branch ownership, branch naming, and merge strategy |
| `[automation]` | Global or project | Run initiation, pace, and the per-run handoff ceiling |
| `[roles.<name>]` | Global or project | One flat table per role: description, grants, agent, launch options, and launch permissions |
| `[reviews]` | Global or project | The two independent review policies and the role assigned to each |
| `[work_roots]` | Machine-local only | Absolute paths for the names `[project].work_roots` declares |

Project values beat global values, key by key. `cartopian resolve-config <project-root>` merges all three files, validates them together, and shows the effective result with the source of each value. It fails rather than guesses when something does not line up, such as a declared work root with no path or a review that names a role nobody defined.

**`CONFIG-MAPPING.md` is the complete field reference.** It lists every setting, every accepted value, every capability and preset, every wrapper name, and every flag for creating and editing configuration.

Run `init workspace` to set global defaults and `init project` to create a project. Inside a project, ask the PM for a change and it applies it with `cartopian update-config`, which validates the result and preserves your comments.

### A complete example

This project requires both reviews and runs unattended in short bursts:

```toml
[project]
id = "my-project"
name = "My Project"
project_schema_version = "v0.10.0"
work_roots = ["product"]

[automation]
initiation = "auto"
confirmation = "until-blocked"
max_handoffs_per_run = 2

[reviews]
planning = "required"
planning_role = "reviewer"
task_closure = "required"
task_role = "reviewer"

[roles.coder]
description = "Implements tasks per spec."
grants = ["coder-like"]
auto_launch = ["task_run"]

agent = "cartopian-codex"
model = "gpt-5-codex"
effort = "high"
timeout = "60m"

[roles.reviewer]
description = "Reviews against acceptance evidence and the original request."
grants = ["reviewer-like"]
auto_launch = ["task_review", "planning_review"]

agent = "cartopian-agy"
timeout = "30m"

[defaults]
git_versioning = false
```

The matching machine-local file supplies the path that only exists on your computer:

```toml
# cartopian.local.toml
[work_roots]
product = "/Users/<name>/Projects/my-product"
```

With that in place, one task runs as: assign, complete, report, review, apply the verdict, start the next. The run stops for blockers, failures, phase boundaries, decisions reserved for you, and the handoff ceiling.

### Keeping a project current

`project_schema_version` records the layout a project uses, so Cartopian knows when a project needs updating. It is separate from the Cartopian release version, the connected server identity, and the MCP protocol version. The `migrate project` skill advances it, only with your approval and only after validation passes.

## Built for small context windows

Token burn and context noise are design constraints here, not afterthoughts.

- **Bookkeeping is code, not reasoning.** Reading state, choosing the next task, validating readiness, assembling handoffs, parsing reports, and auditing the plan are CLI subcommands exposed as MCP tools. Each returns one compact record. The model uses that answer instead of deriving it again from raw files.
- **Only what is needed, when it is needed.** The PM loads the runbook and materials for the current step, then uses only the tools that step requires.
- **Status is a directory.** Nothing to sync, nothing to reconcile, nothing to reread.
- **Handoffs are curated.** A dispatched agent gets the specification, the acceptance criteria, your original request, and the absolute paths it needs. It does not get your conversation history. With grants active, a contained coder can read its prompt and the product tree and nothing else.
- **Session state is one small file.** `STATE.md` is capped at 5KB and names the current phase, active work, open work, blockers, and the exact next action. That is the whole cost of resuming.
- **Transients get cleaned up.** Prompts and handoff status files are deleted once superseded. Durable knowledge is distilled into tasks, reviews, and decisions.

## Skills the PM runs

You do not need these names in normal use. The PM proposes the next action and routes there itself.

| Skill | What it does |
| --- | --- |
| `init workspace` | Sets up your global and project `cartopian.toml` defaults |
| `init project` | Scaffolds and registers a new project |
| `adopt requirements` | Imports requirements from JIRA, a PRD, Confluence, or similar |
| `adopt plan` | Pulls an existing plan into Cartopian's shape |
| `plan project` | Drives requirements, plan, phases, tasks, and specs |
| `start session` | Answers "where were we?" and continues with the next action |
| `run task` | Drives one task from assignment through evidence-backed closure and any required review |
| `run handoff` | Executes one prompt and report handoff |
| `close plan` | Closes the active plan, optionally archives it, and resets for the next |
| `migrate project` | Brings a project's schema current, with your approval |
| `register mcp` | Registers `cartopian-mcp` with more agents and installs their trigger |
| `check for updates` | Compares your installed version with the latest release and upgrades on approval |

Name any of them to jump straight there. This is handy for occasional out-of-band work such as importing requirements midstream or registering another agent. See `skills/README.md` for the full index.

## Protocol

`protocol/CONVENTIONS.md` is the authoritative reference for project structure, lifecycle, naming, roles, reviews, request evidence, handoffs, and Git behavior. `skills/` holds the executable workflows. Both are plain markdown, written for people and agents alike.

## License

This project is distributed under a custom license. See `LICENSE` for the full terms.
