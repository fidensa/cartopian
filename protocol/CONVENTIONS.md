# Cartopian Protocol Conventions

Rules for keeping a project coherent over many sessions. This file defines durable protocol contracts: what artifacts exist, what they mean, and why the constraints exist. Procedural runbooks belong in `skills/`.

## Core Principle

Cartopian is filesystem-first. Directories and filenames carry the project's state, so the protocol can work without a database, SaaS control plane, or external services. Cartopian is self-contained — the agent is the software — and runs on the Python standard library alone with no third-party dependencies. Because it is a security tool that governs other systems, containment is security-first: dependencies are attack surface, so Cartopian adds none.

Git is optional. When git versioning is enabled, it records the same filesystem state; it is not the source of protocol authority.

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

For project-agnostic startup directions such as "start working", "continue", "check `STATE.md`", "what's next", or "pick up where we left off", the PM resolves eligible projects through the registry:

1. Enumerate registered projects via `cartopian discover-projects`.
2. If exactly one project is registered, use it and name it to the operator.
3. If more than one project is registered and none was selected, ask the operator which project to use. Do not read or mutate project-specific lifecycle artifacts until the project is selected.
4. If no projects are registered, start with `skills/init-project.md`, which scaffolds a new project at an operator-supplied path and registers it via `cartopian register-project`.

After project selection, the PM reads the selected project's `cartopian.toml` and the global `~/.cartopian/cartopian.toml` along the FR-011 resolution chain and resolves the effective PM role. If the agent is the PM for the selected project, session startup duty is:

1. Read `STATE.md` before taking lifecycle action.
2. Reconcile `STATE.md` against the filesystem when it names task state that disagrees with task directories.
3. Tell the operator the current phase, active work, and next protocol action from `STATE.md`.
4. Ask whether to begin or continue the current task, or proceed to the next task when no task is active.

A bare startup direction is not permission to launch a handoff or move a task. The PM waits for the operator to confirm the current or next task before using `skills/run-task.md`, `skills/plan-project.md`, or another lifecycle skill.

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

Tasks can move backward on failed review. `request-changes` returns the task to `in-progress/`; `reject` returns it to `open/`. The original task remains the unit of work, so failed reviews do not spawn replacement tasks or follow-up tasks.

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
| `in-progress → in-review` | `reports/REPORT-NN-NNN.md` | must reference this task's `Task ID:`; `Status: complete` |
| `in-review → done` | `reviews/REVIEW-NN-NNN.md` | `Verdict: approve` |
| `in-review → in-progress` | `reviews/REVIEW-NN-NNN.md` | `Verdict: request-changes` |
| `in-review → open` | `reviews/REVIEW-NN-NNN.md` | `Verdict: reject` |

`open → in-progress` carries no artifact guard: the PM moves the task first, then authors `prompts/PROMPT-NN-NNN.md` against the `tasks/in-progress/` path, so prompt, report, and review paths agree. Prompt existence is enforced fail-closed at the mediated handoff boundary instead — `cartopian dispatch` refuses to launch when the prompt is missing. Manual (operator-performed) assignment paths do not pass through `dispatch`; there the operator is handed the prompt path directly, and `cartopian plan-audit` reports any in-progress task without a matching prompt as a blocker.

Fast-forward transitions (e.g., `open → done`) carry no artifact guard and remain available for operator-initiated cleanup and administrative movement.

Guards apply only to task files whose names match the canonical `TASK-NN-NNN` prefix. Tasks with non-canonical names skip artifact checks. On guarded transitions, a canonical task file with no findable project root is a hard block; the CLI cannot verify prerequisites and will not execute the rename. Unguarded transitions carry no prerequisites to verify, so they execute without requiring a project root.

`cartopian plan-audit <project-path>` is a companion audit that surfaces provenance gaps across the whole project:

- **Artifact chain integrity**: every `TASK-NN-NNN` file in `tasks/in-progress/` must have a matching `prompts/PROMPT-NN-NNN.md`; every file in `tasks/in-review/` must have a matching `reviews/REVIEW-NN-NNN.md` with a `Verdict:` field present.
- **Work-root provenance**: for each configured work root, if uncommitted git changes exist and no active task is assigned to that root (or no active prompt exists for the assigned task), the audit's behavior depends on the effective `git.pm_owns_product_branches` setting.
  - When `pm_owns_product_branches = true`, the PM owns product-repo plumbing, so dirty state without an active prompted task is anomalous and the audit emits an `unattributed-work-root-changes` warning.
  - When `pm_owns_product_branches = false` (the protocol default), product-repo state belongs to the assignee and dirty work roots are expected. The audit does not emit a warning; instead it emits an informational `work-root-attribution` entry naming the most-recently-modified task that targeted this work root and its assignee (or recording that attribution is unknown if no prior task names the root).

Run `plan-audit` at session startup and before plan closeout. A non-zero exit is a PM-level blocker; do not advance lifecycle state until all blockers are resolved. Warnings should be surfaced to the operator, but they do not block lifecycle movement by themselves.

## Tasks

Tasks are assignment-sized units of work derived from the current phase and implementation plan. The lifecycle shape is `Plan -> Spec -> Test -> Code`, with task execution procedure defined in `skills/run-task.md`.

Task files follow the canonical field schema in `templates/TASK.md`.

Open task files should contain enough context to assign and review the work without becoming progress journals.

If completion evidence arrives before assignment/start was recorded, the PM may fast-forward the task to the status supported by that evidence.

## Specs

Specs are mutable, single-file **work contracts** — a generic agreement between the PM and the assignee about what "done" looks like for the work the spec covers. In software contexts they typically describe an implementation contract, but the same artifact carries operating procedures, creative briefs, research plans, checklists, and similar domain-neutral work agreements. The `SPEC-NN-NNN` identifier prefix, the `templates/SPEC.md` filename, the `Spec:` task-file field, and the `specs/` project directory are retained as compatibility labels; the reframing is editorial.

The current file is the current version.

Spec files follow the canonical field schema in `templates/SPEC.md`.

A spec may carry `Status: draft | locked`. `locked` means the current contract has been approved; it does not make the file immutable forever.

Approved specs change in place after the project's required review or approval. Version-suffixed spec files (`-v1`, `-v2`) and spec supersession chains are not part of the protocol.

## Reviews

Task-closure reviews use `reviews/REVIEW-NN-NNN.md`. There is one review file per task, overwritten on re-review. There is no round suffix and no closure sign-off section.

Planning-checkpoint reviews use `reviews/REVIEW-PLAN-NNN-slug.md`. They follow the canonical field schema in `templates/REVIEW.md` but attach to planning stages, not tasks.

Planning-checkpoint reviews are temporary artifacts deleted when the checkpoint is approved or superseded.

Review verdicts are:

- `approve`: task moves to `done/`.
- `request-changes`: task moves to `in-progress/`.
- `reject`: task moves to `open/`.

## Prompts

Prompts are temporary, assignee-directed handoff artifacts in `prompts/`. They restate the task, spec, context, output expectations, scope boundaries, done criteria, and completion report requirements.

Prompt files follow the canonical field schema in `templates/PROMPT.md`.

Prompts must include complete absolute paths for every resource the assignee is expected to use or produce. They must not rely on relative path interpretation, current working directory assumptions, or vague instructions such as "read the PM system."

Task prompts are deleted when the task reaches `done/` or when the prompt is superseded before assignment. Planning-checkpoint prompts are deleted when the checkpoint is approved or superseded. Prompts are never archived as durable records.

## Reports

Reports are protocol-defined handoff result artifacts in `reports/`. They are evidence for the PM, not replacements for task, review, decision, or state records.

Report files follow the canonical field schema and variants in `templates/REPORT.md`.

Task completion reports use `reports/REPORT-NN-NNN.md`. Task review completion reports use `reports/REPORT-NN-NNN.md`. Planning-checkpoint review completion reports use `reports/REPORT-PLAN-NNN-slug.md`.

Reports must not include secrets or unnecessary sensitive environment data such as API keys, credentials, tokens, or private connection strings.

Each handoff has one expected protocol-derived report path. A stale, missing, malformed, incomplete, internally inconsistent, unsupported, or path-mismatched report is not valid completion evidence.

Report parsing outcomes are:

- `accepted`: well-formed and actionable.
- `blocked`: explicitly blocked or operator judgment is required.
- `failed`: explicitly failed.
- `failed-to-parse`: missing, malformed, incomplete, inconsistent, unsupported, or contradicts expected paths.

`failed-to-parse` is a PM-level blocker. It preserves the prompt and invalid report for inspection and prevents lifecycle movement.

## Roles

The `[roles]` section in `cartopian.toml` maps each role name to a one-line description string. Role names are operator-chosen identifiers; descriptions explain what the role is responsible for so the PM can align tasks to roles during assignment.

Roles exist to be assigned, which means a PM who takes on the work rather than assigning it is undermining the system. Assign work to role(s) with appropriate descriptions/permissions.

### PM Scope

The PM role is bounded to project-management authoring:

- **Directory scope.** The PM may only read or mutate files inside the project directory currently being managed. It may not modify files outside that project — including sibling Cartopian-governed projects, the Cartopian protocol repository itself, or any unrelated repository the operator happens to have on disk.
- **File-type scope.** Within the managed project, the PM may CREATE, READ, UPDATE, or DELETE markdown (`.md`) files only. All non-markdown work — source code, configuration, data files, build artifacts, executables — must be dispatched to another role via a handoff.
- **Config is not a PM responsibility.** `cartopian.toml` is authored once at project initialization via `skills/init-project.md`, which invokes `cartopian generate-config`. After init, runtime config edits are operator-owned and happen outside the PM lifecycle; the PM may read effective config via `cartopian resolve-config` but never writes or mutates `cartopian.toml`.
- **Authoring discipline.** A PM that implements work rather than assigning it is a protocol violation, regardless of which file types are involved.

These limits apply to every PM, whether dispatched automatically via `[handoffs.pm]` or acting manually.

```toml
[roles]
pm = "Plans phases, dispatches handoffs, integrates results."
operator = "Approves locks, unblocks, sets cadence."
```

The protocol-default roster is **`pm` and `operator`**. Operators may add any further roles their project needs. Common example labels operators pick are `coder` ("Implements tasks per spec.") and `reviewer` ("Reviews per acceptance evidence."), but these are illustrative only — they are not part of the default roster.

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
auto_start = true
timeout = "60m"

[handoffs.reviewer]
agent = "gemini"
auto_start = false
timeout = "30m"
```

Handoff fields are:

- `agent`: executable name.
- `auto_start`: whether the PM may launch the executable after assignment is authorized by run policy.
- `timeout`: optional maximum wall-clock duration for PM-launched handoffs. The protocol default is `60m`. The PM delegates deadline enforcement to the wrapper (which kills the upstream process at the deadline) and observes completion through the wait primitives described in [Waiting For Completion](#waiting-for-completion) rather than watchdogging the running process; it does not impose a separate PM-side deadline.

`[handoffs.<role>].timeout` — resolved along the project → global chain, defaulting to `60m` — is the single source of truth for the handoff deadline. The launcher exports it to the wrapper as the `CARTOPIAN_TIMEOUT` environment variable (see `skills/run-handoff.md`), and the wrapper is the sole enforcer: it kills the assignee at that deadline (exit `124`). Every other timer is removed or inherits this value — no per-tool CLI timeout flag (for example `claude -p --timeout`) is set independently, and the PM runs no concurrent timer and does not watchdog the process — so no second timer can kill a legitimate long-running handoff before the SSOT deadline.

Every automated handoff follows this argument contract:

```text
<agent> <absolute prompt path>
```

The prompt path is passed as one argument. Tool-specific non-interactive flags, sandbox settings, approval settings, and environment variables belong in a wrapper executable, not in `cartopian.toml`.

Pre-built wrappers for common CLIs (Codex, Claude Code, Gemini, Devin) are in `wrappers/`. See `wrappers/README.md` for installation.

### Launch Directory

Assignee CLIs run with cwd set to the **cartopian project root** — the absolute path recorded for the selected project in the registry (FR-003). The shipped wrappers resolve and `cd` to that path automatically; the prompt path passed to the wrapper carries the project root in its prefix (`<project-root>/prompts/PROMPT-NN-NNN.md`) so derivation is unambiguous.

The cartopian project root is the home for every artifact the assignee must read or produce in the cartopian protocol surface — the task file, spec file, prompt, and the report path the assignee writes back to `<project-root>/reports/`. No "parent" or "shared workspace" directory is involved in the launch contract.

When a task needs to read or write outside the cartopian project root (for example, a sibling product repo), the additional locations are declared as **work roots**: the project's `cartopian.toml` carries a `[project].work_roots` name set; the per-machine `<project-root>/cartopian.local.toml` maps each name to a platform-native absolute path on this operator's machine; and the task file's `Work root:` field names the subset of work roots the task touches. The launcher consumes the resolved, absolute path set emitted by `cartopian resolve-config <project>` and grants the agent read/write access to the **union of**:

- the cartopian project root (also the launch cwd); and
- every absolute path resolved from the task's `Work root:` names.

Nothing wider, nothing narrower. The access model is documented in [Work Roots](#work-roots) below.

**Fail-closed default.** The wrapper does not launch the agent if any of the following hold: a declared work-root name has no per-machine path mapping (`resolve-config` exits non-zero); a resolved absolute path does not exist on disk; or the target tool's sandbox cannot scope the full union natively. The wrapper exits non-zero with a `[work-root]` stderr line naming the failure. The operator must fix the mapping, declare the missing root, remove the bogus declaration, or opt in to the per-tool unrestricted mode (per-invocation env var; see the wrapper-layer documentation).

**Note for custom wrapper authors.** The launch cwd is the cartopian project root, which is a regular directory the operator chose at registration time and is not automatically a git repository. Tools that refuse to run outside a git repo (e.g. Codex's `--skip-git-repo-check`) must be told to skip that check; the sandbox/permission model lives at the wrapper layer, not at the "is-this-a-git-repo" layer. Wrappers shipped with Cartopian apply this flag unconditionally for tools that need it.

### Work Roots

Work roots are the protocol mechanism that lets a cartopian project reference filesystem locations outside its own root — typically a sibling product repository, a design repo, a docs repo, or any external location the project's tasks need to read or write.

The committed `<project-root>/cartopian.toml` declares a **name set** under `[project].work_roots`: an inline list of operator-chosen identifiers (e.g., `["product", "design"]`). Names are platform-independent and portable across operators. The committed file carries no paths; this keeps multi-operator and multi-machine use viable.

The per-machine `<project-root>/cartopian.local.toml` carries the **name → absolute-path mapping** for the current operator's machine, under a `[work_roots]` table. The file is gitignored by `cartopian scaffold-project` and is never committed. Two operators on two machines author their own `cartopian.local.toml` with their own absolute paths; the committed `cartopian.toml` remains identical for both.

`cartopian resolve-config <project>` merges the committed and per-machine files, validates that every declared name has a path mapping, and emits the resolved absolute paths as the single canonical form. Skills, wrappers, and launchers consume the resolved output; they never read the raw committed name set or the raw per-machine file. Unmapped names exit non-zero with a `[work-root]` stderr line.

Tasks reference work roots by **name** in the `Work root:` task-file field (see `templates/TASK.md`). The field is optional, comma-separated multi-valued, and rejects absolute paths, project-relative paths, and `<owner>/<repo>` slugs. Names that are absent from `[project].work_roots` cause `cartopian validate-task-readiness` to block the task.

Optional automation policy:

```toml
[automation]
confirmation = "each-handoff"
max_handoffs_per_run = 1
```

Supported `confirmation` values are:

- `each-handoff`: stop after each handoff result is processed.
- `until-blocked`: continue through eligible `auto_start = true` handoffs until blocked, failed, rejected, missing evidence, requiring operator judgment, reaching a phase boundary, or hitting `max_handoffs_per_run`.

Defaults are `confirmation = "each-handoff"` and `max_handoffs_per_run = 1`.

Handoffs are sequential. Concurrent child agents are out of scope.

A timeout, hard process stop, missing report, late report, or invalid report is not successful completion evidence.

### Waiting For Completion

The PM detects handoff completion by observing the filesystem, not by hand-rolled timing loops, repeated manual report reads on a fixed cadence, manual "tell me when it's done" prompts, or PM-side watchdog timers. Two read-only wait primitives own this step and replace all ad-hoc polling:

- `cartopian wait-handoff <task-path> --role <role> --max-block <duration>` — for task-scoped handoffs (task assignment, task review). It resolves the task's expected report path (the same path `cartopian handoff-packet` derives) and honors the role's configured `[handoffs.<role>].timeout` as the absolute ceiling.
- `cartopian wait-report <report-path> --max-block <duration>` — the lower-level primitive for a known report path, including planning-checkpoint reviews that have no task file.

The completion contract is:

- **The report file is the authoritative completion signal.** A handoff is complete only when its expected report file is present and parses. The optional `<report-path>.status` wrapper file is enrichment for early crash detection only; when it is absent, the wait commands degrade to report-only observation. The `<report-path>.status` file is transient and has a fixed lifecycle: every shipped wrapper writes it on assignee exit (clean, error, and timeout exits alike, and regardless of whether `resolve-config` succeeds), `wait-handoff` consumes it during the wait, and the PM removes it through `cartopian delete-report <report-path>` at report-clear and through `cartopian delete-report <report-path> --status-only` at task close — so it never outlives the handoff it describes. Reports may linger in `reports/` after a task reaches `done/`; the companion `.status` file must not. Both commands are read-only — they never write to the project tree, move tasks, or launch processes.
- **Terminal observations** are `done` (report present and parses; the PM reads the report verdict for lifecycle action), `failed-to-parse` (report present but invalid), `failed` (the wrapper status file reports a crash and no valid report appeared), and `timeout` (the configured handoff ceiling elapsed first). A `timeout`, hard process stop, crash, or missing/late/invalid report is not successful completion evidence.
- **`still-running` is the yield-and-resume signal.** When the `--max-block` budget elapses before the configured timeout, the assignee may still be working. The PM yields control back to the operator or host harness and re-calls the same wait command on resume. The filesystem observation survives the yield, so stopping and resuming loses no progress and starts no second handoff.

The wrapper still enforces the wall-clock deadline at the OS level (see the `timeout` field above); the wait commands observe the result. The PM does not impose a separate PM-side deadline or watchdog.

## Dependencies

- `Depends on`: tasks whose output this task reads or builds on. Informational; does not block start.
- `Blocked by`: tasks that must be in `done/` before this task can start.

Both fields carry `TASK-NN-NNN` identifiers only.

## Evidence Gate Discipline

Every task declares `Evidence gate: required` or `Evidence gate: n/a`.

`required` tasks name concrete acceptance evidence — typically test targets that must fail before implementation starts, but any verifiable red-before-green check (fixture run, validation script, fact-check pass) is acceptable when no test target exists.

`n/a` is only for non-executable work and must say why.

Reviews of `required` tasks record red-before-green evidence: a pointer showing the named red check existed before implementation, and a pointer showing the same check is green on the closing commit.

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

## Sizing

- `STATE.md` has a hard ceiling of 5KB.
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

On an accepted coder completion report with `Ready for review: yes`, the coder is responsible for completed worktree changes and completion evidence only. The coder does not stage, commit, push, create a branch, or open a PR. The PM resolves the product repo, creates or updates the configured product-repo branch, stages and commits the task changes, captures the resulting implementation commit SHA, pushes with `git push -u origin <branch>`, and opens a pull request with `gh pr create`. The commit message, PR title, and PR body reference the task ID and completion report.

The protocol defaults are:

- Branch pattern: `task/{task_id}-{slug}`.
- Merge strategy: `merge`.
- Branch cleanup: delete the product branch on merge.

The PM resolves a deploy preview URL when one exists, such as from a deployment-bot PR comment. If no preview URL exists, the PM proceeds with the PR URL only and records the gap in `STATE.md`.

On reviewer `approve`, the PM merges the PR with `gh pr merge --<strategy> --delete-branch`, using the effective `git.default_merge_strategy`. On `request-changes` or `reject`, the PM moves the task per the verdict and leaves the branch and PR open for the next coder pass.

Review-evidence authorship follows the event boundary. Reviewers fill the pre-merge review fields: `Commit SHA`, findings, and verdict. For `Merge commit SHA`, reviewers write `pending` when PM-owned product-repo git is enabled, or `n/a` when it is not. After an approved PR is merged, the PM appends `Merge commit SHA` to the review file's existing `Implementation evidence` block and appends `PR URL` if the review file does not already contain it. Review reports remain assignee-to-PM evidence handoffs and are not PM-edited.

## Session State

After project selection, every PM session starts from that project's `STATE.md` and ends with `STATE.md` refreshed. The file remains short, current, and under 5KB.

Session closeout leaves task directories, prompts, reports, decisions, and git state consistent with the lifecycle evidence processed during the session.

The final operator-facing message names the exact next protocol action.
