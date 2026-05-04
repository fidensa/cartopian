# Cartopian Protocol Conventions

Rules for keeping a project coherent over many sessions. This file
defines durable protocol contracts: what artifacts exist, what they
mean, and why the constraints exist. Procedural runbooks belong in
`skills/`.

## Core Principle

Cartopian is filesystem-first. Directories and filenames carry the
project's state, so the protocol can work without a database, SaaS
control plane, or mandatory runtime.

Git is optional. When git versioning is enabled, it records the same
filesystem state; it is not the source of protocol authority.

## Protocol And Skills

`protocol/CONVENTIONS.md` is the invariant layer. It defines naming,
lifecycle authority, artifact meaning, and cross-session constraints.

`templates/*.md` files are the canonical field-schema layer. They define
the required headings, frontmatter-style fields, and variant sections for
protocol artifacts.

`skills/*.md` files are executable runbooks. They define operational
procedure for initialization, planning, task execution, handoff
automation, and plan closeout.

Skill invocation names are derived from skill filenames by dropping
`.md` and replacing hyphens with spaces. For example,
`run-task.md` maps to `run task`.

## Project Scope

A Cartopian project directory is a governance container, not a product
codebase.

It tracks phase progress against `IMPLEMENTATION_PLAN.md`, holds specs,
tasks, reviews, prompts, reports, and decisions, and keeps one short
state file (`STATE.md`) so each session starts with current context.

It is not a source repository for product code, a workspace shell for
product repos, a chat log, journal, or prompt archive.

## Naming

- Tasks: `TASK-NN-NNN-kebab-case-slug.md`. `NN` is the two-digit phase;
  `NNN` is the three-digit counter within that phase.
- Specs: `SPEC-NN-NNN-kebab-case-slug.md`. Spec numbering is locked to
  task numbering; specs do not have an independent counter.
- Reviews: `REVIEW-NN-NNN.md`. One task-closure review per task;
  overwritten on re-review.
- Planning-checkpoint reviews: `REVIEW-PLAN-NNN-slug.md`. `NNN` is a
  per-project sequential counter independent of task numbering.
- Prompts: `PROMPT-NN-NNN.md`. Temporary task handoff artifacts in
  `prompts/`.
- Planning-checkpoint prompts: `PROMPT-PLAN-NNN-slug.md`. Temporary
  review handoff artifacts in `prompts/`.
- Reports: `REPORT-NN-NNN.md`. Temporary task handoff result artifacts
  in `reports/`.
- Planning-checkpoint reports: `REPORT-PLAN-NNN-slug.md`. Temporary
  planning-review handoff result artifacts in `reports/`.
- Phases: `PHASE-NN-slug.md`. `NN` matches the plan phase order.
- Implementation plan: `IMPLEMENTATION_PLAN.md`. One live plan per
  project.
- Plan archives: `archive/PLAN-NNN-slug/`. Optional completed-plan
  snapshots created only during plan closeout.
- Plan closeout summary: `archive/PLAN-NNN-slug/CLOSEOUT.md`.
- Decisions: `DEC-NNN-kebab-case-slug.md`. `NNN` is a project-local
  counter within `decisions/`.

### Trace Chain

The trace chain is identifier-based, not physical nesting. Related
artifacts live in their protocol directories.

`IMPLEMENTATION_PLAN.md` defines phase sections; phase files carry the
same phase number; task, spec, prompt, report, and review identifiers
share the task's `NN-NNN` prefix. A plan ref such as `P01-BUILD-003`
encodes its phase number and points to `PHASE-01-*` and the matching
plan phase section.

Planning-checkpoint prompts, reports, and reviews are not part of the
task trace chain because they attach to planning stages, not tasks.

### Filename Exclusions

Task, spec, prompt, and review filenames never include session numbers,
dates, person names, or tool names.

## Status Through Directory

Task status is the directory the task file lives in:

- `tasks/open/`
- `tasks/in-progress/`
- `tasks/in-review/`
- `tasks/done/`

Task files never carry a `status:` field because duplicated status can
go stale.

Tasks can move backward on failed review. `request-changes` returns the
task to `in-progress/`; `reject` returns it to `open/`. The original task
remains the unit of work, so failed reviews do not spawn replacement
tasks or follow-up tasks.

## Lifecycle Authority

The PM owns Cartopian lifecycle movement: task directory changes, prompt
cleanup, handoff result processing, review assignment, and `STATE.md`
updates.

Assignees do not move Cartopian task files, delete prompts, rewrite
`STATE.md`, or perform PM lifecycle cleanup.

Reviewers create or update review files and record verdicts. They do not
move tasks between status directories.

Automated agents do not gain lifecycle authority by completing a
handoff. Their reports are evidence for the PM to process.

## Tasks

Tasks are assignment-sized units of work derived from the current phase
and implementation plan. The lifecycle shape is `Plan -> Spec -> Test ->
Code`, with task execution procedure defined in `skills/run-task.md`.

Task files follow the canonical field schema in `templates/TASK.md`.

Open task files should contain enough context to assign and review the
work without becoming progress journals.

If completion evidence arrives before assignment/start was recorded, the
PM may fast-forward the task to the status supported by that evidence.

## Specs

Specs are mutable, single-file contracts. The current file is the
current version.

Spec files follow the canonical field schema in `templates/SPEC.md`.

A spec may carry `Status: draft | locked`. `locked` means the current
contract has been approved; it does not make the file immutable forever.

Approved specs change in place after the project's required review or
approval. Version-suffixed spec files (`-v1`, `-v2`) and spec
supersession chains are not part of the protocol.

## Reviews

Task-closure reviews use `reviews/REVIEW-NN-NNN.md`. There is one review
file per task, overwritten on re-review. There is no round suffix and no
closure sign-off section.

Planning-checkpoint reviews use `reviews/REVIEW-PLAN-NNN-slug.md`. They
follow the canonical field schema in `templates/REVIEW.md` but attach to
planning stages, not tasks.

Planning-checkpoint reviews are temporary artifacts deleted when the
checkpoint is approved or superseded.

Review verdicts are:

- `approve`: task moves to `done/`.
- `request-changes`: task moves to `in-progress/`.
- `reject`: task moves to `open/`.

## Prompts

Prompts are temporary, assignee-directed handoff artifacts in
`prompts/`. They restate the task, spec, context, output expectations,
scope boundaries, done criteria, and completion report requirements.

Prompt files follow the canonical field schema in `templates/PROMPT.md`.

Prompts must include complete absolute paths for every resource the
assignee is expected to use or produce. They must not rely on relative
path interpretation, current working directory assumptions, or vague
instructions such as "read the PM system."

Task prompts are deleted when the task reaches `done/` or when the
prompt is superseded before assignment. Planning-checkpoint prompts are
deleted when the checkpoint is approved or superseded. Prompts are never
archived as durable records.

## Reports

Reports are protocol-defined handoff result artifacts in `reports/`.
They are evidence for the PM, not replacements for task, review,
decision, or state records.

Report files follow the canonical field schema and variants in
`templates/REPORT.md`.

Task completion reports use `reports/REPORT-NN-NNN.md`.
Task review completion reports use `reports/REPORT-NN-NNN.md`.
Planning-checkpoint review completion reports use
`reports/REPORT-PLAN-NNN-slug.md`.

Reports must not include secrets or unnecessary sensitive environment
data such as API keys, credentials, tokens, or private connection
strings.

Each handoff has one expected protocol-derived report path. A stale,
missing, malformed, incomplete, internally inconsistent, unsupported, or
path-mismatched report is not valid completion evidence.

Report parsing outcomes are:

- `accepted`: well-formed and actionable.
- `blocked`: explicitly blocked or operator judgment is required.
- `failed`: explicitly failed.
- `failed-to-parse`: missing, malformed, incomplete, inconsistent,
  unsupported, or contradicts expected paths.

`failed-to-parse` is a PM-level blocker. It preserves the prompt and
invalid report for inspection and prevents lifecycle movement.

## Roles

Roles describe assignee kind, not tool names. The `[roles]` section in
`cartopian.toml` maps each role to a kind value:

```toml
[roles]
pm = "agent"
operator = "human"
coder = "agent"
reviewer = "agent"
```

Supported kind values are:

- `human`: manually assigned through the operator.
- `agent`: may be assigned through CLI handoff when configured.
- `none`: role is not used.
- `""`: unset; the PM should ask the operator for the role assignment.
- Custom values: allowed for local policy, but manual unless a project
  convention defines otherwise.

## Handoffs

CLI handoff automation is optional. Manual handoff remains valid for
every role.

The reusable handoff procedure is `skills/run-handoff.md`. Planning
uses the same contract through `skills/plan-project.md`; task execution
uses it through `skills/run-task.md`.

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
- `auto_start`: whether the PM may launch the executable after
  assignment is authorized by run policy.
- `timeout`: optional maximum wall-clock duration for PM-launched
  handoffs. The protocol default is `60m`.

Every automated handoff follows this argument contract:

```text
<agent> <absolute prompt path>
```

The prompt path is passed as one argument. Tool-specific non-interactive
flags, sandbox settings, approval settings, and environment variables
belong in a wrapper executable, not in `cartopian.toml`.

Pre-built wrappers for common CLIs (Codex, Claude Code, Gemini, Devin)
are in `wrappers/`. See `wrappers/README.md` for installation.

### Launch Directory

Assignee CLIs run with cwd set to the **parent of the workspace root**.
The launch cwd is fully derivable from the absolute prompt path
(`<workspace>/projects/<project-id>/prompts/PROMPT-NN-NNN.md`); the
shipped wrappers resolve and `cd` to it automatically.

This contract exists so a single `workspace-write`-style sandbox covers
both surfaces a handoff touches:

- the workspace, so the assignee can write its
  `reports/REPORT-NN-NNN.md` back into
  `<workspace>/projects/<project-id>/reports/`, and
- the sibling target product repo named in the task's `Repo subpath:`
  field, so the assignee can edit code.

Recommended workspace layout: target product repos live as siblings of
the workspace root (or nested below it). The task's `Repo subpath:`
field is a path fragment resolvable as `<launch cwd>/<repo subpath>`
and is the only path the PM needs to construct to point an assignee
at the right product repo.

A handoff that needs to touch a repo outside this layout is a sign the
project's workspace layout should be reorganized rather than the
sandbox widened.

The shipped wrappers honor a `CARTOPIAN_LAUNCH_CWD` environment
variable as an escape hatch for layouts the convention does not fit
(split, cross-drive, monorepo-internal, per-repo-sandbox setups).
This is environment, not protocol: there is no `cartopian.toml`
field for it, because the value varies per machine and per operator
preference and any drift between a recorded path and the actual
filesystem would defeat the filesystem-first stance.

Optional automation policy:

```toml
[automation]
confirmation = "each-handoff"
max_handoffs_per_run = 1
```

Supported `confirmation` values are:

- `each-handoff`: stop after each handoff result is processed.
- `until-blocked`: continue through eligible `auto_start = true`
  handoffs until blocked, failed, rejected, missing evidence, requiring
  operator judgment, reaching a phase boundary, or hitting
  `max_handoffs_per_run`.

Defaults are `confirmation = "each-handoff"` and
`max_handoffs_per_run = 1`.

Handoffs are sequential. Concurrent child agents are out of scope.

A timeout, hard process stop, missing report, late report, or invalid
report is not successful completion evidence.

## Dependencies

- `Depends on`: tasks whose output this task reads or builds on.
  Informational; does not block start.
- `Blocked by`: tasks that must be in `done/` before this task can
  start.

Both fields carry `TASK-NN-NNN` identifiers only.

## Test Gate Discipline

Every task declares `Test gate: required` or `Test gate: n/a`.

`required` tasks name concrete test targets that must fail before
implementation starts.

`n/a` is only for non-executable work and must say why.

Reviews of `required` tasks record red-before-green evidence: a pointer
showing the named red test existed before implementation, and a pointer
showing the same test is green on the closing commit.

## Plan Lifecycle

A Cartopian project has one active implementation plan at a time. The
live `REQUIREMENTS.md`, `IMPLEMENTATION_PLAN.md`, `phases/`, `tasks/`,
`specs/`, `reviews/`, `decisions/`, `prompts/`, and `reports/` describe
the current plan only.

When a plan completes, close it before starting a new plan. The
canonical closeout workflow is `skills/close-plan.md`.

Plan closeout requires:

- No task files in `tasks/open/`, `tasks/in-progress/`, or
  `tasks/in-review/`.
- No active or ambiguous prompts.
- No unresolved or ambiguous reports.
- Phase exit criteria satisfied by completed tasks, decisions, specs, or
  documented operator acceptance.
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

`REQUIREMENTS.md` and `IMPLEMENTATION_PLAN.md` never carry forward as
live artifacts. A new planning cycle produces fresh requirements and a
fresh implementation plan.

`ENGINEERING.md` and project-level `CONVENTIONS.md` may carry forward
only when the operator explicitly chooses to keep them as seed context
for the next plan. Otherwise, they reset to seed files.

`cartopian.toml` remains live across plans.

## Plan Archives

Cartopian is anti-archival by default. Completed plan artifacts are
archived only when the operator explicitly asks during closeout.

Plan archives use `archive/PLAN-NNN-slug/` and may include snapshots of:

- `REQUIREMENTS.md`
- `ENGINEERING.md`
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

After closeout, `STATE.md` says there is no active plan and names
`skills/plan-project.md` as the next action.

## Decisions

Every non-trivial decision gets its own immutable file in `decisions/`,
named `DEC-NNN-kebab-case-slug.md`.

`decisions/INDEX.md` is a one-line-per-decision summary table.

A decision that changes a prior decision creates a new file with
`Supersedes: DEC-NNN`. The superseded decision file remains unchanged.

## Sizing

- `STATE.md` has a hard ceiling of 5KB.
- Task files are assignment-sized, not running journals.
- Open task files should usually stay under 2KB.
- Completed tasks may be larger when they need closure evidence.
- Phase files are roll-ups of plan refs, task coverage, dependencies,
  and exit criteria.
- Specs have no fixed ceiling, but prefer specificity over
  comprehensiveness.

## Git

When git versioning is used, the `projects/` directory is its own git
repo, tracking all project PM data in a single history. This avoids
creating a separate PM repo per project and eliminates naming collisions
with code repos.

When `git_versioning = true` in the effective `cartopian.toml`:

- Session closeout includes auto-commit and auto-push by the PM.
- Commit messages describe the unit-of-work grain.
- Product-repo commits preserve red-then-green test-gate discipline.

When `git_versioning = false`:

- The filesystem is the only protocol record.
- `STATE.md` remains the current cross-session handoff.

Git staging, commits, and pushes for the protocol repository itself are
human-owned.

## Session State

Every session starts from `STATE.md` and ends with `STATE.md` refreshed.
The file remains short, current, and under 5KB.

Session closeout leaves task directories, prompts, reports, decisions,
and git state consistent with the lifecycle evidence processed during
the session.

The final operator-facing message names the exact next protocol action.
