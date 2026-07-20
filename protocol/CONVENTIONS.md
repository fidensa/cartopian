# Cartopian Protocol Conventions

Rules for keeping a project coherent over many sessions. This file defines durable protocol contracts: what artifacts exist, what they mean, and why the constraints exist. Procedural runbooks belong in `skills/`.

## Core Principle

Cartopian is filesystem-first. Directories and filenames carry the project's state, so the protocol can work without a database, SaaS control plane, or external services. Cartopian is self-contained — the agent is the software — and runs on the Python standard library alone with no third-party dependencies. Because it is a security tool that governs other systems, containment is security-first: dependencies are attack surface, so Cartopian adds none.

Git is optional. When git versioning is enabled, it records the same filesystem state; it is not the source of protocol authority.

Reviews are optional and explicit. `[reviews]` independently controls planning checkpoints and task closure; role names and descriptions never imply review policy. A required loop names the ordinary resolved role assigned to perform it, while an `off` loop proceeds from accepted completion evidence without that review stage.

AI agents come pre-trained to "be helpful and proactive". That training causes project drift and failure to follow governance verbatim. Cartopian aims to correct this training by producing a rigid framework for agentic behavior that defines exactly what helpful and proactive mean. Agents should not guess, make assumptions, or behave in any way contrary to the conventions or pronciples held by the Cartopian project mangement framework.

## Protocol And Skills

`protocol/CONVENTIONS.md` is the invariant layer. It defines naming, lifecycle authority, artifact meaning, and cross-session constraints.

`templates/*.md` files are the canonical field-schema layer. They define the required headings, frontmatter-style fields, and variant sections for protocol artifacts.

`skills/*.md` files are executable runbooks. They define operational procedure for initialization, planning, task execution, handoff automation, and plan closeout.

Skill invocation names are derived from skill filenames by dropping `.md` and replacing hyphens with spaces. For example, `run-task.md` maps to `run task`.

`use cartopian` is a common phrase used to start the cartopian project management system. This and other commands correlate to Cartopian MCP server tools and dialogs and other Cartopian skills. Map available skill and MCP server volcabulary before making assumptions about the Operator's instruction meaning.

## Project Scope

A Cartopian project directory is a governance container, not a product codebase.

It tracks phase progress against `IMPLEMENTATION_PLAN.md`, holds specs, tasks, reviews, prompts, reports, and decisions, and keeps one short state file (`STATE.md`) so each project session starts with current context.

It is not a source repository for product code, a workspace shell for product repos, a chat log, journal, or prompt archive.

## Session Startup And Project Selection

A PM session starts only after the project is unambiguous.

Project selection is **registry-only**. The project registry lives at `~/.cartopian/projects.<format>` (per FR-003) and maps project IDs to absolute filesystem paths. Projects may live anywhere on disk; the registry is the discovery mechanism. The PM reads it via `cartopian discover-projects` (FR-004 #5) and resolves a project by its registered `id` or `path`. There is no directory-scan, no working-directory inference, and no protocol-defined "workspace" directory whose children are projects.

A project is selected explicitly when the operator names a registered project ID or registered project path.

For project-agnostic startup requests of any intent class (see [Request Intent](#request-intent)) — "start working", "continue", "check `STATE.md`", "what's next", "pick up where we left off" — the PM resolves eligible projects through the registry:

1. Enumerate registered projects via `cartopian discover-projects`.
2. If exactly one project is registered, use it and name it to the operator.
3. If more than one project is registered and none was selected, ask the operator which project to use. Do not read or mutate project-specific lifecycle artifacts until the project is selected.
4. If no projects are registered, start with `skills/init-project.md`, which scaffolds a new project at an operator-supplied path and registers it via `cartopian register-project`.

After project selection, the PM reads the selected project's `cartopian.toml` and the global `~/.cartopian/cartopian.toml` along the FR-011 resolution chain and resolves the effective PM role. If the agent is the PM for the selected project, session startup duty is:

1. Read `STATE.md` before taking lifecycle action.
2. Reconcile `STATE.md` against the filesystem when it names task state that disagrees with task directories.
3. Tell the operator the current phase, active work, and next protocol action from `STATE.md`.
4. Act on the operator's request per its intent class (see [Request Intent](#request-intent)). Execution begins only when that classification — or the resolved `[automation] initiation` policy — authorizes it.

## Request Intent

Operator requests fall into three classes. Classifying intent is the PM's first interpretive duty, and a request never changes class because automation is configured aggressively.

- **Execution directives** — "continue", "resume", "start working", "run the next task", "keep going", "pick up where we left off". These initiate (or resume) linear execution: the PM continues the active task — or starts the next sequential task when none is active — via `skills/run-task.md` without asking the operator to choose or approve the selection. Pace is governed by the `[automation]` policy; selection is never an operator question. The PM still stops for blockers, for decisions the protocol reserves to the operator, and at the plan-level forks named in `skills/start-session.md` (no plan exists, plan complete).
- **Informational requests** — "what's next?", "check `STATE.md`", "give me status", "where are we?". These are read-only: answer from `STATE.md` and the `next-action` record, name the next protocol action, and stop. An informational request never initiates execution — even under `[automation] initiation = "auto"` — because a question must not acquire side effects.
- **Scoped directives** — "generate PHASE-04's tasks", "write the spec", "revise the plan". These authorize exactly the named operation. When it completes: under `initiation = "operator"` (the protocol default), the PM reports completion, names the next protocol action, and stops; under `initiation = "auto"`, the newly ready open queue may initiate a run (see [Task Execution Order](#task-execution-order)).

An explicit "stop", "pause", or "don't execute" always overrides configuration: it ends any run in progress at the next safe point and suspends automatic initiation until the operator directs execution again.

## Naming

- Tasks: `TASK-NN-NNN-kebab-case-slug.md`. `NN` is the two-digit phase; `NNN` is the three-digit counter within that phase.
- Specs: `SPEC-NN-NNN-kebab-case-slug.md`. Spec numbering is locked to task numbering; specs do not have an independent counter.
- Reviews: `REVIEW-NN-NNN.md`. One task-closure review per task; overwritten on re-review.
- Planning-checkpoint reviews: `REVIEW-PLAN-NNN-slug.md`. `NNN` is a per-project sequential counter independent of task numbering.
- Prompts: `PROMPT-NN-NNN.md`. Temporary task handoff artifacts in `prompts/`.
- Planning-checkpoint prompts: `PROMPT-PLAN-NNN-slug.md`. Temporary review handoff artifacts in `prompts/`.
- Reports: `REPORT-NN-NNN.md`. Temporary task handoff result artifacts in `reports/`.
- Planning-checkpoint reports: `REPORT-PLAN-NNN-slug.md`. Temporary planning-review handoff result artifacts in `reports/`.
- Phases: `PHASE-NN-slug.md`. `NN` matches the plan phase order.
- Implementation plan: `IMPLEMENTATION_PLAN.md`. One live plan per project.
- Plan archives: `archive/PLAN-NNN-slug/`. Optional completed-plan snapshots created only during plan closeout.
- Plan closeout summary: `archive/PLAN-NNN-slug/CLOSEOUT.md`.
- Archive index: `archive/INDEX.md`. One-line-per-archive summary table.
- Decisions: `DEC-NNN-kebab-case-slug.md`. `NNN` is a project-local counter within `decisions/`.

### Trace Chain

The trace chain is identifier-based, not physical nesting. Related artifacts live in their protocol directories.

`IMPLEMENTATION_PLAN.md` defines phase sections; phase files carry the same phase number; task, spec, prompt, report, and review identifiers share the task's `NN-NNN` prefix. A plan ref such as `P01-BUILD-003` encodes its phase number and points to `PHASE-01-*` and the matching plan phase section.

Planning-checkpoint prompts, reports, and reviews are not part of the task trace chain because they attach to planning stages, not tasks.

### Filename Exclusions

Task, spec, prompt, and review filenames never include session numbers, dates, person names, or tool names.

## Status Through Directory

Task status is the directory the task file lives in:

- `tasks/open/`
- `tasks/in-progress/`
- `tasks/in-review/`
- `tasks/done/`

Task files never carry a `status:` field because duplicated status can go stale.

When task-closure review is required, tasks can move backward on failed review. `request-changes` returns the task to `in-progress/`; `reject` returns it to `open/`. The original task remains the unit of work, so failed reviews do not spawn replacement tasks or follow-up tasks.

## Lifecycle Authority

The PM owns Cartopian lifecycle movement: task directory changes, prompt cleanup, handoff result processing, review assignment, and `STATE.md` updates.

Assignees do not move Cartopian task files, delete prompts, rewrite `STATE.md`, or perform PM lifecycle cleanup.

Reviewers create or update review files and record verdicts. They do not move tasks between status directories.

Automated agents do not gain lifecycle authority by completing a handoff. Their reports are evidence for the PM to process.

When PM-owned product-repo git is enabled, PM lifecycle authority also includes product-repo staging, commits, branches, pushes, PRs, merges, and post-merge review-evidence updates for product repos only. See [PM-Owned Product-Repo Branches](#pm-owned-product-repo-branches).

## Lifecycle CLI Guards

`cartopian move-task` enforces artifact prerequisites before executing any status rename. No workaround, manual task-file move, or worktree edit bypasses these checks; the guard runs on every invocation of the CLI command.

Guarded transitions and their prerequisites:

| Transition | Required artifact | Validation |
| --- | --- | --- |
| `in-progress → in-review` (task review required) | `reports/REPORT-NN-NNN.md` | report exists at this task's `NN-NNN` filename; `Status: complete` |
| `in-review → done` (task review required) | `reviews/REVIEW-NN-NNN.md` | `Verdict: approve` |
| `in-review → in-progress` (task review required) | `reviews/REVIEW-NN-NNN.md` | `Verdict: request-changes` |
| `in-review → open` (task review required) | `reviews/REVIEW-NN-NNN.md` | `Verdict: reject` |
| `in-progress → done` (task review off) | `reports/REPORT-NN-NNN.md` | report exists at this task's `NN-NNN` filename; `Status: complete` |

`open → in-progress` carries no artifact guard: the PM moves the task first, then authors `prompts/PROMPT-NN-NNN.md` against the `tasks/in-progress/` path, so prompt, report, and review paths agree. Prompt existence is enforced fail-closed at the mediated handoff boundary instead — `cartopian dispatch` refuses to launch when the prompt is missing. Manual (operator-performed) assignment paths do not pass through `dispatch`; there the operator is handed the prompt path directly, and `cartopian plan-audit` reports any in-progress task without a matching prompt as a blocker.

`in-progress → done` is disallowed when task-closure review is required, and `in-progress → in-review` is disallowed when it is off. A task already stranded in `in-review/` after policy is changed to off may move out without a verdict guard. `open → done` is an administrative exception only and requires `--administrative --reason`; ordinary execution never uses it.

Guards apply only to task files whose names match the canonical `TASK-NN-NNN` prefix. Tasks with non-canonical names skip artifact checks. On guarded transitions, a canonical task file with no findable project root is a hard block; the CLI cannot verify prerequisites and will not execute the rename. Unguarded transitions carry no prerequisites to verify, so they execute without requiring a project root.

`cartopian plan-audit <project-path>` is a companion audit that surfaces provenance gaps across the whole project:

- **Artifact chain integrity**: every `TASK-NN-NNN` file in `tasks/in-progress/` must have a matching `prompts/PROMPT-NN-NNN.md`; every file in `tasks/in-review/` must have a matching `reviews/REVIEW-NN-NNN.md` with a `Verdict:` field present.
- **Infrastructure-artifact scope guard**: assignees must not add `.github`, CI, or other infrastructure artifacts to a work root unless the task explicitly authorizes them. For every dirty work root, changed files under a top-level infrastructure marker (`.github/`, `.gitlab/`, `.gitlab-ci.yml`, `.circleci/`, `.buildkite/`, `.travis.yml`, `.drone.yml`, `azure-pipelines.yml`, `bitbucket-pipelines.yml`, `Jenkinsfile`) emit an `unauthorized-infra-artifacts` warning unless a task naming that work root carries the explicit task-file field `Infra authorized: <markers>` — a comma-separated list of the markers it authorizes (e.g. `Infra authorized: .github`), or the blanket `Infra authorized: yes`. Prefer the marker-scoped form. Prose mentions of a marker are not authorization, and attribution alone is not authorization. This is a warning for the operator, not a blocker.
- **Work-root provenance**: for each configured work root, if uncommitted git changes exist and no active task is assigned to that root (or no active prompt exists for the assigned task), the audit's behavior depends on the effective `git.pm_owns_product_branches` setting.
  - When `pm_owns_product_branches = true`, the PM owns product-repo plumbing, so dirty state without an active prompted task is anomalous and the audit emits an `unattributed-work-root-changes` warning.
  - When `pm_owns_product_branches = false` (the protocol default), product-repo state belongs to the assignee and dirty work roots are expected. The audit does not emit a warning; instead it emits an informational `work-root-attribution` entry naming the most-recently-modified task that targeted this work root and its assignee (or recording that attribution is unknown if no prior task names the root).

Run `plan-audit` at session startup and before plan closeout. A non-zero exit is a PM-level blocker; do not advance lifecycle state until all blockers are resolved. Warnings should be surfaced to the operator, but they do not block lifecycle movement by themselves.

## Tasks

Tasks are assignment-sized units of work derived from the current phase and implementation plan. The domain-neutral lifecycle is `Plan -> Contract -> Evidence -> Outcome`; for software work this is the familiar `Plan -> Spec -> Test -> Code`. Task execution procedure is defined in `skills/run-task.md`.

Task files follow the canonical field schema in `templates/TASK.md`.

Open task files should contain enough context to assign and review the work without becoming progress journals.

If completion evidence arrives before assignment/start was recorded, the PM may fast-forward the task to the status supported by that evidence.

### Task Execution Order

Task execution is **linear by default**. The next task is deterministic: the first file in `tasks/open/`, ordered by phase (plan order), then by task filename within the phase, skipping tasks whose `Blocked by:` dependencies are not yet in `tasks/done/`. This is the same selection `cartopian next-action` emits as `next_open_task`.

**Selection does not authorize execution.** Deterministic selection answers *which task would run next*; it does not answer *whether execution begins*. Execution begins only from an operator execution directive or from `[automation] initiation = "auto"` (see [Request Intent](#request-intent) and the `[automation]` policy under [Handoffs](#handoffs)). A populated open queue is a fact about the plan, not permission to run it.

Within an initiated run, choosing the next task is a computation, not a conversation:

- The PM does not ask the operator which task to run next or whether to continue an already in-progress task. It proceeds.
- When a task completes and automation budget remains (see the `[automation]` policy under [Handoffs](#handoffs)), the PM continues to the next sequential task in the same run.
- The operator may override the order at any time by naming a task; an explicit override applies to that task only and does not change the default for subsequent selections.
- Deviating from sequential order on the PM's own initiative is a protocol violation.

**Directive scope.** A scoped directive ("generate PHASE-04's tasks", "write the spec", "revise the plan") authorizes only the named operation; completing it never rolls into execution on its own. Under `initiation = "operator"` the PM reports completion and stops. Under `initiation = "auto"` the newly ready open queue may initiate a run, subject to the same stop conditions. An explicit "stop", "pause", or "don't execute" always wins over configuration.

Linear movement stops — and the operator is consulted — only at genuine stop conditions: a readiness or audit blocker, a failed/blocked/rejected handoff, evidence gates that cannot be satisfied, a decision the protocol or plan reserves to the operator, a plan-level fork (no plan, phase tasks not yet generated, plan complete), or exhaustion of the `[automation]` budget.

## Specs

Specs are mutable, single-file **work contracts** — a generic agreement between the PM and the assignee about what "done" looks like for the work the spec covers. In software contexts they typically describe an implementation contract, but the same artifact carries operating procedures, creative briefs, research plans, checklists, and similar domain-neutral work agreements. The `SPEC-NN-NNN` identifier prefix, the `templates/SPEC.md` filename, the `Spec:` task-file field, and the `specs/` project directory are retained as compatibility labels; the reframing is editorial.

The current file is the current version.

Spec files follow the canonical field schema in `templates/SPEC.md`.

A spec may carry `Status: draft | locked`. `locked` means the current contract has been approved; it does not make the file immutable forever.

Approved specs change in place after the project's required review or approval. Version-suffixed spec files (`-v1`, `-v2`) and spec supersession chains are not part of the protocol.

A spec is surfaced to an assignee **deidentified**, never as the raw file. The canonical spec keeps its full traceability (the `SPEC-NN-NNN` title, `Plan refs:`, and the `## References` section) for the PM; `cartopian render-spec <spec-path>` produces the assignee-facing rendering, which strips that scaffolding and any inline identifier while preserving the work-contract prose. The PM inlines that rendering into the coder prompt's `## Specification` section, so PM identifiers stay inside PM artifacts and never reach product code via the spec the coder reads.

## Reviews

Review policy is resolved project over global, key-by-key:

```toml
[reviews]
planning = "required"       # required | off
planning_role = "reviewer"  # any resolved role name
task_closure = "off"        # required | off
task_role = "reviewer"      # required only when task_closure is required
```

The protocol defaults both loops to `off`. A project can therefore override globally required review by setting its local mode to `off` without removing the inherited role. Policy answers whether review happens; the role field answers who performs it; capability grants answer what that role may access and do. No behavior keys on the literal role name `reviewer`, on description prose, or on a preset name.

Task-closure reviews use `reviews/REVIEW-NN-NNN.md`. There is one review file per task, overwritten on re-review. There is no round suffix and no closure sign-off section.

Planning-checkpoint reviews use `reviews/REVIEW-PLAN-NNN-slug.md`. They follow the canonical field schema in `templates/REVIEW.md` but attach to planning stages, not tasks.

Planning-checkpoint reviews are temporary artifacts deleted when the checkpoint is approved or superseded.

Review verdicts are:

- `approve`: task moves to `done/`.
- `request-changes`: task moves to `in-progress/`.
- `reject`: task moves to `open/`.

## Prompts

Prompts are temporary, assignee-directed handoff artifacts in `prompts/`. They restate the requirements, acceptance criteria, context, output expectations, scope boundaries, done criteria, and completion report requirements.

Prompt files follow the canonical field schema in `templates/PROMPT.md`.

Prompts must include complete absolute paths for every resource the assignee is expected to use or produce. They must not rely on relative path interpretation, current working directory assumptions, or vague instructions such as "read the PM system."

Coder (task) handoffs are **deidentified**. Project-management identifiers — `TASK-NN-NNN`, `SPEC-NN-NNN`, plan refs `PNN-KIND-NNN`, requirement refs (`FR-`/`NF-`), decision refs (`DEC-`), and the like — exist only inside PM artifacts; they are not surfaced to the assignee. A coder prompt names the work by its title and addresses every resource by file path, and the coder writes its report to the given report path without recording any identifier. Cartopian links the report back to its task by the report *filename* (`REPORT-NN-NNN.md`), so the assignee never needs — and is never given — a task identifier to copy into product code.

Task prompts are deleted when the task reaches `done/` or when the prompt is superseded before assignment. Planning-checkpoint prompts are deleted when the checkpoint is approved or superseded. Prompts are never archived as durable records.

## Reports

Reports are protocol-defined handoff result artifacts in `reports/`. They are evidence for the PM, not replacements for task, review, decision, or backlog records.

Report files follow the canonical field schema and variants in `templates/REPORT.md`.

The neutral task-report core is `## Identity`, `## Completion evidence`, `## Remaining risks`, and `## Ready to close`. Specialized software and document sections (`## Files changed`, `## Deliverable`, `## Test evidence`, `## Commit / PR`) are optional evidence shapes. For compatibility, an exact `## Files changed` or `## Deliverable` heading may stand in for `## Completion evidence`, and `## Ready for review` may stand in for `## Ready to close`.

Task completion reports use `reports/REPORT-NN-NNN.md`. Task review completion reports use `reports/REPORT-NN-NNN.md`. Planning-checkpoint review completion reports use `reports/REPORT-PLAN-NNN-slug.md`.

Task review completion reports declare the absolute `Task path:` in `## Identity`. The path must name the task implied by `REPORT-NN-NNN.md` in its current lifecycle directory; a missing, stale, or wrong task path is invalid completion evidence. This requirement does not apply to deidentified task completion reports or to planning-review completion reports.

Reports must not include secrets or unnecessary sensitive environment data such as API keys, credentials, tokens, or private connection strings.

Each handoff has one expected protocol-derived report path. A stale, missing, malformed, incomplete, internally inconsistent, unsupported, or path-mismatched report is not valid completion evidence.

Report parsing outcomes are:

- `accepted`: well-formed and actionable.
- `blocked`: explicitly blocked or operator judgment is required.
- `failed`: explicitly failed.
- `failed-to-parse`: missing, malformed, incomplete, inconsistent, unsupported, or contradicts expected paths.

`failed-to-parse` is a PM-level blocker. It preserves the prompt and invalid report for inspection and prevents lifecycle movement.

## Document Deliverables

A document-deliverable task is one whose work product is a durable document — research findings, a design, an evaluation, an analysis — rather than code. Such a task declares a `Deliverable:` field so its work product is written to a durable file the reviewer reviews directly, and the completion report stays a thin summary. This is the same shape as a code task: code is written to the work root and the report summarizes it; a document is written to a deliverable and the report summarizes it. A report is never the home of the work product, and reports are not durable (a task and its review report share `reports/REPORT-NN-NNN.md`, so the coder's report is cleared before the review handoff).

### The Deliverable field

`Deliverable:` is name-only and deidentified — it carries no task, plan, spec, or requirement identifier, the same discipline as `Work root:`. It takes one of two forms:

- `root:relative/path.md` (work-root deliverable) — the assignee writes the document into the named work root directly, exactly as it writes code.
- `project:relative/path.md` (project-root deliverable) — the document lands under the cartopian project root. The assignee is not granted write access there, so it returns the document inline in its completion report and the PM persists it to this path.

The field is set at task authoring, or captured at assignment when the PM prompts the operator for the location. `n/a` (or an absent line) means the task has no durable document deliverable. `handoff-packet` and `task-bundle` resolve the field to an absolute `deliverable` record (mode, root, relpath, absolute path, existence) so the PM sources the path without re-reading the task.

### Work-root deliverables

The assignee writes the complete work product to the resolved deliverable path (inside a declared work root, already in its write scope). The completion report only summarizes what was done and points to the deliverable. The review prompt names the deliverable path as the primary artifact to review.

### Project-root deliverables

The assignee returns the complete work product inline in the report's `## Deliverable content` section. Before clearing the report for the review handoff, the PM persists that content to the resolved project-root deliverable path using its own project-write authority. The review prompt then names the persisted file as the primary artifact to review. (A deployment may instead grant the assignee role write access to the project directory, in which case a project-root deliverable is written directly like a work-root one; the inline path is the default that needs no extra grant.)

### Durability

The deliverable is the durable record of the work; the report may be cleared and is not a substitute for it. A deliverable is the assignee's produced knowledge artifact — distinct from a decision (`decisions/DEC-NNN`, a PM ruling) and from a spec (`specs/SPEC-NN-NNN`, the input contract). When a deliverable's findings warrant a durable protocol ruling, the PM still records that as a decision.

`plan-audit` enforces this durability: a task in `in-review` or `done` that declares a `Deliverable:` whose file is missing is a `missing-deliverable` blocker (skipped only when a work-root deliverable's name is unmapped on the auditing machine, since existence cannot be verified there).

## Roles

The `[roles]` section in `cartopian.toml` maps each role name to a one-line description string. Role names are operator-chosen identifiers; descriptions explain what the role is responsible for so the PM can align tasks to roles during assignment.

Roles exist to be assigned, which means a PM who takes on the work rather than assigning it is undermining the system. Assign work to role(s) with appropriate descriptions/permissions.

### PM Scope

The PM role is bounded to project-management authoring:

- **Directory scope.** The PM may only read or mutate files inside the project directory currently being managed. It may not modify files outside that project — including sibling Cartopian-governed projects, the Cartopian protocol repository itself, or any unrelated repository the operator happens to have on disk.
- **File-type scope.** Within the managed project, the PM authors markdown (`.md`) files — CREATE, READ, UPDATE, DELETE. The project's own config files (`cartopian.toml`, `cartopian.local.toml`) are the one non-markdown exception: the PM may edit them, but only through the mediated `cartopian update-config` command and only on the operator's explicit request (see **Config management** below). All other non-markdown work — source code, data files, build artifacts, executables — must be dispatched to another role via a handoff.
- **Config management.** The PM manages the project's config on the operator's behalf, so a non-technical operator never has to find or hand-edit `cartopian.toml`. Config edits are operator-*requested*, never proactive or routine: the PM does not offer or solicit config changes during ordinary lifecycle flow, and applies them only when the operator explicitly asks (or approves a migration). All PM config edits go through `cartopian update-config`, which validates the closed key schema and the resulting effective config and writes atomically; the PM still reads effective config via `cartopian resolve-config`. This scope covers only config files *inside the managed project directory*; the global `~/.cartopian/cartopian.toml` lives outside every project and is authored by the workspace-setup flow (`skills/init-workspace.md`), not by a per-project PM. Enforcement is precise: a structured raw-edit tool aimed at a config file is denied regardless of grants (the mediated command is the only edit path), while shell-routed edits and advisory-tier hosts remain documented residuals, exactly as for every other governed path-class.
- **Migration is PM-owned.** A project's internal protocol-schema version is separate from the installed Cartopian application's release version. Bringing that project schema current is PM-owned orchestration performed on operator approval: the PM applies each applicable `protocol/CHANGELOG.md` entry, doing config edits via `cartopian update-config` and markdown edits via the mediated writers, and dispatching or surfacing the steps it cannot mediate (file renames, header substitutions, wrapper edits). Operators are not expected to edit the version marker. See `skills/migrate-project.md`.
- **Authoring discipline.** A PM that implements work rather than assigning it is a protocol violation, regardless of which file types are involved.

These limits apply to every PM. The PM is always the interactive orchestrator of a session — it is never itself launched as a handoff (there would be no PM to launch it), so a `[handoffs.pm]` block has no meaning and must not be configured.

```toml
[roles]
pm = "Plans phases, dispatches handoffs, integrates results."
operator = "Approves locks, unblocks, sets cadence."
```

The protocol-default roster is **`pm` and `operator`**. Operators may add any further roles their project needs. Common example labels include `coder`, `reviewer`, `editor`, and `researcher`, but all are illustrative only. Review assignment is configured under `[reviews]`; role names and descriptions carry no protocol behavior, so an operator may use another label if desired.

Dispatch path is inferred from the presence of a matching `[handoffs.<role>]` block, not from a `kind` value:

- Role declared in `[roles]` with a configured `[handoffs.<role>]` — automated dispatch via that wrapper.
- Role declared in `[roles]` with no `[handoffs.<role>]` block — manual dispatch; the PM surfaces the prompt and the operator acts.
- Role omitted from `[roles]` — role does not exist in this project; tasks may not assign it.

A `[handoffs.<role>]` block whose role name is not declared in `[roles]` is a config error.

## Handoffs

CLI handoff automation is optional. Manual handoff remains valid for every role.

The reusable handoff procedure is `skills/run-handoff.md`. Planning uses the same contract through `skills/plan-project.md`; task execution uses it through `skills/run-task.md`.

Use `[handoffs.<role>]` only for agent roles that need a named target:

```toml
[handoffs.coder]
agent = "codex"
model = "gpt-5-codex"
effort = "high"
auto_start_tasks = true
timeout = "60m"

[handoffs.reviewer]
agent = "gemini"
auto_start_tasks = true
auto_start_reviews = true
timeout = "30m"
```

Handoff fields are:

- `agent`: executable name.
- `model`: optional model identifier, exported to the wrapper as the `CARTOPIAN_MODEL` environment variable; the wrapper translates it into the tool-specific model-selection flag. When unset, no variable is exported and the tool's own default model applies.
- `effort`: optional effort/thinking level for the assigned agent, exported to the wrapper as the `CARTOPIAN_EFFORT` environment variable; the wrapper translates it into the tool-specific effort flag. When unset, no variable is exported and the tool's own default effort applies. A value outside the wrapper's CLI-wide vocabulary makes the wrapper warn on stderr and launch at the default; whether a specific model supports a vocabulary-valid level is the tool's own behavior.
- `auto_start_tasks`: whether the PM may launch this role for task-scoped handoffs, including assigned task work and task-closure review. Default `false`/unset.
- `auto_start_reviews`: whether the PM may launch this role for planning-review checkpoints, which have no task file. Default `false`/unset. It does not enable planning review; `[reviews].planning` decides whether the checkpoint exists and `planning_role` assigns it.
- Both `auto_start_*` keys choose launch mode only, after `[automation] initiation` has allowed the run to begin and `confirmation` permits the handoff. Each setting governs only its handoff type; it never initiates a run. `cartopian dispatch` enforces the applicable key fail-closed. Older `auto_start` and `planning_reviews` keys are accepted only as compatibility inputs and resolve to these explicit fields; agents should migrate new edits to the explicit names.
- `timeout`: optional maximum wall-clock duration for PM-launched handoffs. The protocol default is `60m`.

`[handoffs.<role>].timeout` — resolved along the project → global chain, defaulting to `60m` — is the single source of truth for the handoff deadline. The launcher exports it to the wrapper as the `CARTOPIAN_TIMEOUT` environment variable (see `skills/run-handoff.md`), and the wrapper is the sole enforcer: it kills the assignee at that deadline (exit `124`). No other timer exists — no per-tool CLI timeout flag is set independently, and the PM runs no concurrent timer or watchdog — so no second timer can kill a legitimate long-running handoff before the SSOT deadline. The PM observes completion through the wait primitives in [Waiting For Completion](#waiting-for-completion).

Every automated handoff follows this argument contract:

```text
<agent> <absolute prompt path>
```

The prompt path is passed as one argument. Tool-specific non-interactive flags, sandbox settings, approval settings, and environment variables belong in a wrapper executable, not in `cartopian.toml`.

Pre-built wrappers for common CLIs (Codex, Claude Code, Gemini, Devin) are in `wrappers/`. See `wrappers/README.md` for installation.

### Launch Directory

Assignee CLIs run with cwd set to the **cartopian project root** — the absolute path recorded for the selected project in the registry (FR-003). The shipped wrappers resolve and `cd` to that path automatically; the prompt path passed to the wrapper carries the project root in its prefix (`<project-root>/prompts/PROMPT-NN-NNN.md`) so derivation is unambiguous. `cartopian dispatch` sets `CARTOPIAN_LAUNCH_CWD` to the same project root. No "parent" or "shared workspace" directory is involved in the launch contract.

**Wrappers are neutral launchers.** A wrapper translates env → CLI flags, sets the cwd, runs the agent **autonomously** (so the unattended handoff completes), enforces the `CARTOPIAN_TIMEOUT` deadline, and emits the status signal. It does **not** gate the agent's filesystem access, and it cannot assume the agent's role — the same wrapper may back any operator-defined role. Locations outside the project root that a task needs (declared as **work roots**, below) are referenced by absolute path/URI inside the prompt the PM authors.

**Work-root write grant.** The launched agent must be able to write to the union of the cartopian project root and the project's declared work roots. `cartopian dispatch` resolves the declared work roots fail-closed (an unmapped name or a mapped path missing on this machine refuses the launch) and exports the resolved absolute paths to the wrapper as the `CARTOPIAN_WORK_ROOTS` environment variable (`os.pathsep`-joined: `:` on POSIX, `;` on Windows; not exported when the project declares none, and a stale inherited value is cleared). A wrapper whose agent CLI imposes its own filesystem sandbox rooted at the launch cwd must **widen** that sandbox to cover these paths — the shipped codex wrapper adds them as `sandbox_workspace_write.writable_roots`, and the claude wrapper passes each as `--add-dir`. Widening a tool-imposed sandbox to match the launch contract is not scoping; wrappers still never *confine* the agent below what its own CLI does. Where a tool's sandbox exposes no per-path grant surface (gemini `--sandbox`, devin `--sandbox`), the wrapper warns on stderr that declared work roots may be unwritable inside that sandbox.

Capability-based gating of what an agent may read or mutate is the **harness's** responsibility, not the launcher's. If approval-in-the-loop behavior is wanted for a role, leave the applicable `auto_start_*` setting false/unset and use the manual path rather than the wrapper — the wrapper path is the unattended-automation path, where there is no human to answer a prompt.

**Note for custom wrapper authors.** The cartopian project root is not automatically a git repository. Tools that refuse to run outside a git repo must be told to skip that check (the shipped wrappers do so unconditionally). The autonomy/permission flags a wrapper passes live at the wrapper layer; capability gating lives in the harness.

### Work Roots

Work roots are the protocol mechanism that lets a cartopian project reference filesystem locations outside its own root — typically a sibling product repository or any external location the project's tasks need to read or write.

- The committed `<project-root>/cartopian.toml` declares a **name set** under `[project].work_roots`: an inline list of operator-chosen, platform-independent identifiers (e.g., `["product", "design"]`). The committed file carries no paths, keeping multi-operator and multi-machine use viable.
- The per-machine `<project-root>/cartopian.local.toml` carries the **name → absolute-path mapping** for the current operator's machine, under a `[work_roots]` table. It is gitignored by `cartopian scaffold-project` and never committed.
- `cartopian resolve-config <project>` merges the two files, validates that every declared name has a path mapping, and emits the resolved absolute paths as the single canonical form. Skills and the PM consume the resolved output (e.g. to reference a work root by absolute path in the prompt they author). Unmapped names exit non-zero with a `[work-root]` stderr line.
- Tasks reference work roots by **name** in the `Work root:` task-file field (see `templates/TASK.md`). The field is optional, comma-separated multi-valued, and rejects absolute paths, project-relative paths, and `<owner>/<repo>` slugs. Names absent from `[project].work_roots` cause `cartopian validate-task-readiness` to block the task.

Optional automation policy:

```toml
[automation]
initiation = "operator"
confirmation = "each-handoff"
max_handoffs_per_run = 1
```

Supported `initiation` values are:

- `operator`: execution begins only from an operator execution directive (see [Request Intent](#request-intent)). After informational requests and scoped directives the PM reports and stops.
- `auto`: the PM may initiate a run without a directive — at session startup once startup duty completes with no blockers, and when a scoped directive leaves the open queue ready. Informational requests remain read-only, and explicit "stop"/"pause" language still suspends initiation until the operator directs execution again.

Supported `confirmation` values are:

- `each-handoff`: stop after each handoff result is processed.
- `until-blocked`: continue through handoffs whose applicable `auto_start_tasks` or `auto_start_reviews` setting is true until blocked, failed, rejected, missing evidence, requiring operator judgment, reaching a phase boundary, or hitting `max_handoffs_per_run`.

Defaults are `initiation = "operator"`, `confirmation = "each-handoff"`, and `max_handoffs_per_run = 1`. `resolve-config` resolves an unrecognized `initiation` value to `operator` (fail-safe: less automation, never more) and emits a `[validation]` warning.

The automation authorities are disjoint, and each gates a different question:

- `initiation` gates **whether a run begins** when no execution directive was given.
- `confirmation` gates **pace** within an initiated run: under `each-handoff` the PM stops after processing each handoff result and resumes with the next sequential step when the operator says to continue; under `until-blocked` it chains through sequential tasks within the run budget. Neither value authorizes initiation — `until-blocked` describes how far an initiated run chains, not whether one starts.
- **Selection** is never gated and never an operator question: task order is deterministic per [Task Execution Order](#task-execution-order). Within an initiated run, evidence-supported lifecycle moves (starting the next sequential task, moving a task per a parsed report or review verdict) are applied without a confirmation prompt; the operator is consulted only at the stop conditions named there.
- `[handoffs.<role>].auto_start_tasks` and `auto_start_reviews` gate **launch mode** for task-scoped and planning-review handoffs respectively; they participate in neither initiation nor pace.

Full unattended operation is therefore a stack of explicit opt-ins, each an operator choice and none a protocol default: `initiation = "auto"` (runs may begin without a directive), `confirmation = "until-blocked"` (runs chain), `max_handoffs_per_run` sized to the desired batch, and the applicable `auto_start_tasks` / `auto_start_reviews` settings enabled on roles the PM should launch itself.

Handoffs are sequential. Concurrent child agents are out of scope.

### Waiting For Completion

The PM detects handoff completion by observing the filesystem through two canonical read-only wait primitives, which replace all ad-hoc polling, hand-rolled timing loops, manual "tell me when it's done" prompts, and PM-side watchdog timers:

- `cartopian wait-handoff <task-path> --role <role> --max-block <duration>` — for task-scoped handoffs (task assignment, task review). It resolves the task's expected report path and honors the role's configured `[handoffs.<role>].timeout` as the absolute ceiling.
- `cartopian wait-report <report-path> --max-block <duration>` — the lower-level primitive for a known report path, including planning-checkpoint reviews that have no task file.

The completion contract is:

- **The report file is the authoritative completion signal.** A handoff is complete only when its expected report file is present and parses. The optional `<report-path>.status` wrapper file is secondary enrichment: it lets the wait exit early when the assignee process has exited without leaving a valid report — whether it crashed or exited cleanly having written no report. When the `.status` file is absent, the wait commands degrade to report-only observation. The `.status` file is transient: wrappers write it on assignee exit and the PM removes it through `cartopian delete-report`, so it never outlives the handoff it describes (cleanup procedure in `skills/run-handoff.md`). Both wait commands are read-only — they never write to the project tree, move tasks, or launch processes.
- **Terminal observations** are `done` (report present and parses; the PM reads the report verdict for lifecycle action), `failed-to-parse` (report present but invalid), `failed` (the wrapper status file reports the assignee process exited — by crash, timeout kill, or clean exit — and no valid report appeared), and `timeout` (the configured handoff ceiling elapsed first). A `timeout`, hard process stop, crash, or missing/late/invalid report is not successful completion evidence.
- **`still-running` is the yield-and-resume signal.** When the `--max-block` budget elapses before the configured timeout, the assignee may still be working. The PM yields control back to the operator or host harness and re-calls the same wait command on resume. The filesystem observation survives the yield, so stopping and resuming loses no progress and starts no second handoff.

The wrapper enforces the wall-clock deadline at the OS level (see the `timeout` field above); the wait commands observe the result rather than imposing a separate PM-side deadline.

## Dependencies

- `Depends on`: tasks whose output this task reads or builds on. Informational; does not block start.
- `Blocked by`: tasks that must be in `done/` before this task can start.

Both fields carry `TASK-NN-NNN` identifiers only.

## Evidence Gate Discipline

Every task declares `Evidence gate: required` or `Evidence gate: n/a`.

`required` tasks name concrete acceptance evidence. Software work often uses a test that fails before implementation; other work may use a fixture run, validation script, fact-check pass, approval checklist, inspection record, rehearsal, or another verifiable before-and-after check.

`n/a` is only for non-executable work and must say why.

When task-closure review is required, reviews of `required` tasks record the before-and-after evidence. When review is off, the completion report records it directly.

## Plan Lifecycle

A Cartopian project has one active implementation plan at a time. The live `REQUIREMENTS.md`, `IMPLEMENTATION_PLAN.md`, `phases/`, `tasks/`, `specs/`, `reviews/`, `decisions/`, `prompts/`, and `reports/` describe the current plan only.

When a plan completes, close it before starting a new plan. The canonical closeout workflow is `skills/close-plan.md`.

Plan closeout requires:

- No task files in `tasks/open/`, `tasks/in-progress/`, or `tasks/in-review/`.
- No active or ambiguous prompts.
- No unresolved or ambiguous reports.
- Phase exit criteria satisfied by completed tasks, decisions, specs, or documented operator acceptance.
- Explicit operator confirmation.

Plan closeout resets the live plan surface:

- `REQUIREMENTS.md`
- `IMPLEMENTATION_PLAN.md`
- `decisions/`
- `phases/`
- `tasks/`
- `specs/`
- `reviews/`
- `prompts/`
- `reports/`

`REQUIREMENTS.md` and `IMPLEMENTATION_PLAN.md` never carry forward as live artifacts. A new planning cycle produces fresh requirements and a fresh implementation plan.

`STANDARDS.md` and project-level `CONVENTIONS.md` may carry forward only when the operator explicitly chooses to keep them as seed context for the next plan. Otherwise, they reset to seed files.

`cartopian.toml` remains live across plans.

## Plan Archives

Cartopian is anti-archival by default. Completed plan artifacts are archived only when the operator explicitly asks during closeout.

Plan archives use `archive/PLAN-NNN-slug/` and may include snapshots of:

- `REQUIREMENTS.md`
- `STANDARDS.md`
- `CONVENTIONS.md`
- `IMPLEMENTATION_PLAN.md`
- `STATE.md`
- `decisions/`
- `phases/`
- `tasks/`
- `specs/`
- `reviews/`
- `reports/`
- `CLOSEOUT.md`

Prompts are not archived.

`archive/INDEX.md` is a one-line-per-archive summary table. It is created with the first archive and updated on each subsequent closeout that produces an archive.

After closeout, `STATE.md` says there is no active plan and names `skills/plan-project.md` as the next action.

## Decisions

Every non-trivial decision gets its own immutable file in `decisions/`, named `DEC-NNN-kebab-case-slug.md`.

`decisions/INDEX.md` is a one-line-per-decision summary table.

A decision that changes a prior decision creates a new file with `Supersedes: DEC-NNN`. The superseded decision file remains unchanged.

## Backlog

`BACKLOG.md` at the project root is the durable home for PM/reviewer follow-up notes — actionable tech debt, process debt, and protocol-hardening items that are not yet promoted into a task or roadmap entry. Follow-up notes belong here, never in `STATE.md`, which stays canonical composed state under its 5KB ceiling; the mediated `write-state` enforces this by composing the body itself (see Session State). Protocol-compliance feedback (e.g. the operator points out a protocol or config rule the PM missed) is process debt and lands here the moment it arises — not in a `STATE.md` situation note.

Entries are written through `cartopian write-backlog` (one section per `BL-NNN` id) and removed through `cartopian delete-backlog <project-root> --bl-id BL-NNN` (which removes only that entry's section; the preamble and every other entry round-trip byte-for-byte). Both paths are mediated writes — hand-edits to `BACKLOG.md` remain out of band, the same as any other mediated artifact. The file survives plan closeout and is input to the next planning cycle.

### Ids are writer-allocated and never reused

`BACKLOG.md` carries a visible preamble field, `Highest id issued: BL-NNN`, owned exclusively by the mediated writers. New-entry ids are **allocated by the writer, never supplied by the caller**: omitting `--bl-id` mints the next id (mark + 1), bumps the field, and reports the allocated id in the command's NDJSON record. Supplying `--bl-id` is legal only to revise an entry that is currently live. Because the mark only ever ascends and `delete-backlog` never touches it, a deleted id is never reissued — so a stray reference a cleanup sweep missed can never collide with a freshly minted entry. The counter lives in the file itself (not a machine-local counter, a sidecar file, or git history) so it travels with the project and cannot split-brain from the entries it governs. On every mediated write the writer reconciles the field: a value **below** the highest live id can only come from a raw hand-edit and is refused fail-closed; an **absent** field (a legacy file predating this rule) is the one permitted self-heal, initialized to the highest live id on the next write. `plan-audit` asserts `mark ≥ max live id` as a portable detection floor.

### Promotion is a recorded move

When a backlog item is promoted into a task, spec, or phase, the durable artifact records where it came from with a `Source: BL-NNN` header line, and the backlog entry is deleted outright. Reference points from the durable artifact back to the ephemeral entry, never the reverse — the file that outlives the reference is the one that holds it, so nothing can dangle. This is enforced by an **interlocking pair of guards with the delete as the choke point**, not by a composite verb (sugar cannot hold the invariant while the primitive commands stay callable):

- **Stamping is an argument, not body text.** `cartopian write-task` / `write-spec` / `write-phase` take `--source BL-NNN`; the writer validates the grammar, verifies the entry is live in `BACKLOG.md` at stamp time, and renders the `Source:` line itself. A `Source:` line hand-typed into a content body is decoration the guard never saw. This is what separates it from a plain `Plan ref:` — the reference is created by a command that checked the referent existed.
- **`delete-backlog` refuses undocumented deletion.** Before removing a live entry it scans the governed durable surfaces (`tasks/` in all four status dirs, `specs/`, `phases/`, `IMPLEMENTATION_PLAN.md`, `decisions/`) for a matching `Source: BL-NNN` stamp and refuses without one.

Neither guard alone suffices — stamping without the delete guard still lets an unstamped entry be deleted (the dangle); the delete guard without mediated stamping is satisfied by a hand-typed line pointing at nothing. Together you can only stamp what exists and only delete what has been stamped. The ordering is **stamp-then-delete**: the filesystem offers no transaction, so promotion is not atomic — but stamp-first leaves a benign, mechanically recoverable duplicate (the entry is still live and already referenced; `plan-audit` flags it as an unfinished promotion), whereas delete-first would lose information irreversibly. The delete guard makes the safe ordering the only one that executes. The one legitimate exception — an entry **abandoned** rather than promoted — is an explicit `--discard` flag: loud, recorded in the NDJSON, never the default, mirroring the evidence gate's `required` vs `n/a` grammar where an exception is legal only when it is stated.

The general principle this settles: **every cross-artifact reference field is verified by a guard at the lifecycle transition that consumes it** — `validate-task-readiness` already checks `Plan ref:` at task start, and `move-task` checks the review `Verdict:` before a task reaches `done`. A reference that no transition ever verifies is exactly the kind this rule exists to forbid.

## Sizing

- `STATE.md` has a hard ceiling of 5KB; its `## Situation` section is capped at 5 notes, one line of ≤ 200 chars each, ≤ 1KB rendered (see Session State).
- Task files are assignment-sized, not running journals.
- Open task files should usually stay under 2KB.
- Completed tasks may be larger when they need closure evidence.
- Phase files are roll-ups of plan refs, task coverage, dependencies, and exit criteria.
- Specs have no fixed ceiling, but prefer specificity over comprehensiveness.

## Git

When git versioning is used, each cartopian project root is its own git repository, tracking that project's PM data (phases, tasks, specs, reviews, decisions, prompts, reports, `STATE.md`, and `cartopian.toml`) in a single history. Projects live anywhere on disk per FR-003, so git scope is per-project and never assumes a shared parent directory.

The protocol default for `[defaults] git_versioning` is **`false`**. Source attribution: the explicit `git_versioning = false` value in the global `~/.cartopian/cartopian.toml` shipped as the `templates/global.cartopian.toml` seed — projects opt in by setting `git_versioning = true` in their own `cartopian.toml`.

Optional `[git]` configuration resolves along the FR-011 resolution chain (project-level `cartopian.toml` → global `~/.cartopian/cartopian.toml` → these protocol defaults):

```toml
[git]
pm_owns_product_branches = false
default_branch_pattern = "task/{task_id}-{slug}"
default_merge_strategy = "merge"
```

`pm_owns_product_branches = false` is the legacy path. A project with no `[git]` section behaves exactly as before.

`default_branch_pattern` is used only when `pm_owns_product_branches = true`. It supports `{task_id}` and `{slug}`. `{task_id}` is the numeric task identifier without the `TASK-` prefix (`NN-NNN`), and `{slug}` is the task filename slug. For `TASK-02-001-page-templates.md`, the protocol default produces `task/02-001-page-templates`.

`default_merge_strategy` controls the PM merge command for opt-in product repos. Supported values are `merge`, `squash`, and `rebase`, mapping to `gh pr merge --merge`, `gh pr merge --squash`, and `gh pr merge --rebase`.

When `git_versioning = true` in the effective `cartopian.toml`:

- Session closeout includes auto-commit and auto-push by the PM.
- Commit messages describe the unit-of-work grain.
- Product-repo commits preserve red-then-green evidence-gate discipline.

When `git_versioning = false`:

- The filesystem is the only protocol record.
- `STATE.md` remains the current cross-session handoff.

Git staging, commits, and pushes for the protocol repository itself are human-owned.

### PM-Owned Product-Repo Branches

When `git.pm_owns_product_branches = true`, the PM owns product-repo git plumbing for tasks whose `Work root:` field names a work root that resolves to a product repository: staging, commits, branches, pushes, PRs, merges, and branch cleanup. The setting does not apply to tasks whose `Work root:` is `n/a` or omitted, and it never applies to the Cartopian protocol repository itself. Protocol-repo git staging, commits, pushes, and branch management remain human-owned regardless of any project setting.

On an accepted task completion report with `Ready to close: yes` (or the legacy `Ready for review: yes`), the assignee is responsible for completed worktree changes and completion evidence only. The assignee does not stage, commit, push, create a branch, or open a PR. The PM resolves the product repo, creates or updates the configured product-repo branch, stages and commits the task changes, captures the resulting implementation commit SHA, pushes with `git push -u origin <branch>`, and opens a pull request with `gh pr create`. The commit message, PR title, and PR body reference the task ID and completion report. With task-closure review required, merge follows approval; with review off, the PM merges after accepted completion evidence and then closes the task.

The protocol defaults are:

- Branch pattern: `task/{task_id}-{slug}`.
- Merge strategy: `merge`.
- Branch cleanup: delete the product branch on merge.

The PM resolves a deploy preview URL when one exists, such as from a deployment-bot PR comment. If no preview URL exists, the PM proceeds with the PR URL only and records the gap in `STATE.md`.

On reviewer `approve`, the PM merges the PR with `gh pr merge --<strategy> --delete-branch`, using the effective `git.default_merge_strategy`. On `request-changes` or `reject`, the PM moves the task per the verdict and leaves the branch and PR open for the next coder pass.

Review-evidence authorship follows the event boundary. Reviewers fill the pre-merge review fields: `Commit SHA`, findings, and verdict. For `Merge commit SHA`, reviewers write `pending` when PM-owned product-repo git is enabled, or `n/a` when it is not. After an approved PR is merged, the PM appends `Merge commit SHA` to the review file's existing `Implementation evidence` block and appends `PR URL` if the review file does not already contain it. Review reports remain assignee-to-PM evidence handoffs and are not PM-edited.

## Session State

After project selection, every PM session starts from that project's `STATE.md` and ends with `STATE.md` refreshed. The file remains short, current, and under 5KB.

### The body is composed, not authored

While a project has plan artifacts, the canonical `STATE.md` body — Current phase, Active work, Open work, What to do next — is derived entirely from the filesystem, so the PM never authors it. `cartopian write-state <project-root>` composes and persists the body in one step; it refuses `--content`/`--content-file` while plan artifacts exist. The PM decides *when* state is refreshed; the CLI renders *what* it says. This removes the round-trip of derivable text through the PM's context and closes `STATE.md` as a free-form note surface.

The one exception is the no-plan project (post-closeout, pre-plan): there is nothing to compose from, so the closeout body (closeout date, archive note, carry-forward choices, next-action pointer) is PM-authored via `--content`/`--content-file` — and only there.

### Situation notes

The single PM-authored input on a planned project is the `## Situation` section, supplied as `write-state --note` lines (bounded: max 5 notes, one line of ≤ 200 chars each, section ≤ 1KB). A note qualifies only if all three hold: it is about the current state of *this project*; it is **not derivable** from the filesystem, config, or protocol; and it **changes what the next session does**. Example: "coder deploy failed mid-handoff; operator is restarting the development machine." Protocol rules, config values, and task placement never qualify — they are already recorded. Protocol-compliance feedback and follow-up items route to `BACKLOG.md` as process debt at the moment they arise, never into a note.

Notes have a **one-delivery TTL** — a note exists to survive exactly one gap between sessions, then must be consumed:

- Every `write-state` starts from zero notes; nothing carries forward by inertia.
- A `--note` byte-identical to one already in `STATE.md` is refused fail-closed (`note-carry-forward`). A fact that outlives its delivery is promoted (`write-backlog`, `write-decision`), dropped, or — for a genuinely still-live transient — consciously restated, never repasted.
- `plan-audit` and the `next-action` session brief emit a **blocker** per note present: undelivered mail must be resolved before lifecycle movement. Resolving it (acting, promoting, dropping, then refreshing `STATE.md`) is PM work and does not itself require operator input. A healthy steady-state `STATE.md` has zero notes.

Session closeout leaves task directories, prompts, reports, decisions, and git state consistent with the lifecycle evidence processed during the session.

The final operator-facing message names the exact next protocol action.
