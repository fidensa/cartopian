# Cartopian Protocol Conventions

Rules for keeping a project coherent over many sessions. Drift is the
enemy; these rules exist to make drift structurally difficult.

## Core principle

Filesystem-first. Git is optional. The protocol does not depend on git.
If git versioning is enabled in `cartopian.toml`, auto-commit and
auto-push are handled transparently at session close.

## Naming

- Tasks: `TASK-NN-NNN-kebab-case-slug.md`. Phase-scoped: `NN` is the
  two-digit phase, `NNN` is the three-digit counter within that phase.
- Specs: `SPEC-NN-NNN-kebab-case-slug.md`. Spec numbering is locked to
  task numbering. A spec's `NNN` matches the task it belongs to. Specs
  do not have an independent counter.
- Reviews: `REVIEW-NN-NNN.md`. One task-closure review per task.
  Overwritten on re-review.
- Planning-checkpoint reviews: `REVIEW-PLAN-NNN-slug.md`. Used for
  reviews of planning-stage artifacts (requirements, plan, phases,
  tasks/specs). `NNN` is a per-project sequential counter independent
  of task-scoped numbering.
- Prompts: `PROMPT-NN-NNN.md`. Stored temporarily in `prompts/` while a
  task is being assigned or executed.
- Planning-checkpoint prompts: `PROMPT-PLAN-NNN-slug.md`. Used to hand
  off planning-stage review work to a reviewer. Same counter and
  lifecycle as planning-checkpoint reviews.
- Reports: `REPORT-NN-NNN.md`. Stored in `reports/`. Handoff result
  artifacts read by the PM after task completion.
- Planning-checkpoint reports: `REPORT-PLAN-NNN-slug.md`. Stored in
  `reports/`. Handoff result artifacts for planning-stage reviews.
- Phases: `PHASE-NN-slug.md`. Two-digit counter matching plan order.
- Implementation plan: `IMPLEMENTATION_PLAN.md`. One live plan per
  project.
- Plan archives: `archive/PLAN-NNN-slug/`. Optional snapshots of
  completed plan artifacts created only during plan closeout.
- Plan closeout summary: `archive/PLAN-NNN-slug/CLOSEOUT.md`.
- Decisions: `DEC-NNN-kebab-case-slug.md`. Three-digit counter within
  the project's `decisions/` directory.

### Trace chain

The trace chain is identifier-based, not physical nesting. Related
artifacts live in their protocol directories.

```
IMPLEMENTATION_PLAN.md
  └── ## Phase 01: Contract And Schema Foundation
        └── phases/PHASE-01-contract-foundation.md
              ├── tasks/open/TASK-01-001-cert-schema.md
              │     ├── specs/SPEC-01-001-cert-schema.md
              │     ├── prompts/PROMPT-01-001.md
              │     └── reviews/REVIEW-01-001.md
              ├── tasks/open/TASK-01-002-baseline-schema.md  (no spec)
              └── tasks/open/TASK-01-003-enforcement-envelope.md
                    └── specs/SPEC-01-003-enforcement-envelope.md
```

Planning-checkpoint reviews (`REVIEW-PLAN-NNN-slug.md`) and prompts
(`PROMPT-PLAN-NNN-slug.md`) are not part of the task trace chain. They
attach to planning stages, not tasks.

Completion reports (`REPORT-NN-NNN.md` and `REPORT-PLAN-NNN-slug.md`)
live in `reports/`. Reports are handoff result artifacts, not part of
the trace chain.

Plan refs (`P01-BUILD-003`) encode the phase number. `01` in
`P01-BUILD-003` -> `PHASE-01-*` -> plan `## Phase 01:` section.

### What never appears in a filename

- Session numbers.
- Dates inside task, spec, prompt, or review filenames.
- Person names in task or spec slugs.

## Status through directory

- Task status is the directory it lives in: `open/`, `in-progress/`,
  `in-review/`, `done/`. Moving the file is the status update.
- Never add a `status:` field to a task file. The filesystem cannot go
  stale.

## Task movement

Tasks can move backward on failed review:

- **`request-changes`**: task moves back to `in-progress/`. The assignee
  addresses the changes and resubmits.
- **`reject`**: task moves back to `open/`. A new approach is required.

No follow-up or replacement tasks are spawned for failed reviews. The
original task is the unit of work throughout its lifecycle.

## Reviews

Task-closure reviews use `reviews/REVIEW-NN-NNN.md`. There is one review
file per task. The review file is overwritten on re-review. There is no
round suffix and no closure sign-off section.

Planning-checkpoint reviews use `reviews/REVIEW-PLAN-NNN-slug.md`.
They follow the `REVIEW` template format but are not task-closure
reviews. The PM creates a matching `prompts/PROMPT-PLAN-NNN-slug.md`
to hand off the review. Both are temporary artifacts deleted when the
planning stage is approved or superseded.

Review verdicts:

- **`approve`**: task moves to `done/`.
- **`request-changes`**: task moves back to `in-progress/`.
- **`reject`**: task moves back to `open/`.

## Specs

Specs are mutable, single-file contracts. The current file is the
current version. A spec may carry `Status: draft | locked`; `locked`
means the current contract has been approved, not that the file is
immutable forever.

If an approved spec changes, update the same file in place after the
project's required review or approval. Do not create version-suffixed
spec files (`-v1`, `-v2`) or spec supersession chains.

## PM prompt workflow

The PM produces assignee-directed prompts:

1. PM creates `prompts/PROMPT-NN-NNN.md` and proposes an assignee.
2. PM presents both to the operator.
3. Operator responds:
   - **"Task assigned"** or **"Task started"**: PM moves the task to
     `tasks/in-progress/`.
   - **"Change the prompt..."**: PM revises the prompt.
   - **"Change the assignee..."**: PM revises the assignee.
4. PM waits for explicit assignment/start confirmation before moving the
   task to `in-progress/`.

If the operator skips confirmation but later returns with completion
feedback, fast-forward the task to the status supported by the evidence.
Do not keep a completed or review-ready task in `open/` solely because
the assignment confirmation was missed.

Prompt files are temporary handoff artifacts that restate the task and
spec for an assignee. Delete `prompts/PROMPT-NN-NNN.md` when the task
reaches `done/` or when the prompt is superseded before assignment. No
prompt archival.

### Absolute path requirements

When the PM writes an assignment or review prompt, it must include
complete absolute paths for every resource the assignee is expected to
use or produce.

Prompts must not rely on relative path interpretation, current working
directory assumptions, or vague instructions such as "read the PM
system."

## Role kinds

Roles describe assignee kind, not tool names. The `[roles]` section in
`cartopian.toml` maps each role to a kind value:

```toml
[roles]
pm = "agent"
operator = "human"
coder = "agent"
reviewer = "agent"
```

Supported role kind values:

- `human` — manually assigned through the operator.
- `agent` — may be assigned through CLI handoff when configured.
- `none` — role is not used.
- `""` — unset; the PM should ask the operator for the role assignment.
- Custom values — allowed for local policy, but treated as manual unless
  a project convention defines otherwise.

## CLI handoff automation

CLI handoff automation allows the PM to assign work to configured agents
by creating protocol prompts, invoking a named executable with the
absolute prompt path, reading the protocol-defined completion report,
then applying normal task or review lifecycle changes.

Automation is optional. Manual handoff remains valid for every role.

### Handoff configuration

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

Fields:

- `agent`: executable name. The PM invokes this exact command name with
  the absolute prompt path as the only required argument.
- `auto_start`: when `true`, the PM may launch the configured executable
  after assignment is authorized by the current run policy. When `false`,
  the PM creates the prompt and tells the operator the exact command to
  run.
- `timeout`: optional maximum wall-clock duration for an automatically
  launched handoff. Use a duration string such as `30m`, `2h`, or
  `1h30m`. When omitted, the protocol default is `60m` unless a project
  convention defines a stricter default. Timeouts apply only to
  PM-launched handoffs; manual handoffs remain operator-managed.

### CLI invocation convention

Every automated handoff follows this contract:

```text
<agent> <absolute prompt path>
```

The prompt path must be passed as one argument. Programmatic launchers
should use argv-style execution, not shell string interpolation.
Human-facing command text must shell-quote the absolute prompt path when
needed.

If a tool does not natively support that contract, the operator provides
a wrapper executable with the configured name.

### Wrapper responsibility

Cartopian-level confirmation controls whether the PM starts handoffs.
Tool-level confirmation controls whether the child CLI asks before
editing files, running shell commands, or continuing. Cartopian cannot
make an arbitrary CLI non-interactive.

To run without tool prompts, the configured agent executable must be a
native non-interactive command or a wrapper that sets the tool-specific
flags, environment, sandbox, and approval policy. Cartopian does not
store tool-specific flags in `cartopian.toml`.

### Run policy

Optional project or workspace automation policy:

```toml
[automation]
confirmation = "each-handoff"
max_handoffs_per_run = 1
```

Supported `confirmation` values:

- `each-handoff` — stop after each handoff result is processed.
- `until-blocked` — continue launching eligible `auto_start = true`
  handoffs until a blocker, failed report, review rejection, missing
  evidence, operator-required decision, phase boundary, or
  `max_handoffs_per_run` limit.

Both modes are sequential. `until-blocked` extends how many eligible
handoffs the PM may run in one PM session; it does not permit concurrent
child agents. Concurrent handoff execution is out of scope.

Defaults when `[automation]` is omitted:

- `confirmation = "each-handoff"`
- `max_handoffs_per_run = 1`

### Handoff timeout behavior

If an automatically launched handoff exceeds its configured timeout, the
PM must stop waiting for a successful result, record the timeout as a
blocker, and return control to the operator. The PM must not move the
task or review forward based on a late or missing report.

### Sequential launch behavior

Handoffs are launched sequentially. The PM must finish the current
handoff, parse its report, and apply any PM-owned lifecycle updates
before starting the next handoff.

### Hard stop behavior

UI interruption (e.g., closing a terminal, killing a process) is not a
graceful pause. Graceful pause is represented by PM run policy, report
boundaries, and `STATE.md`. The PM must not interpret a hard stop as a
successful handoff.

### Lifecycle authority after handoff

Automated agents do not own Cartopian lifecycle authority. The PM remains
responsible for task movement, prompt cleanup, review assignment,
completion handling, and `STATE.md`. Assignees must not move Cartopian
task files, delete prompts, rewrite `STATE.md`, or perform PM lifecycle
cleanup.

## Dependencies

- **Depends on**: tasks whose output this task reads or builds on.
  Informational; does not block start.
- **Blocked by**: tasks that must be in `done/` before this task can
  start.

Both fields carry `TASK-NN-NNN` identifiers only.

## Test gate discipline

- Every task declares `Test gate: required` or `Test gate: n/a`.
- `required` tasks name concrete test targets that must fail before
  implementation starts.
- `n/a` is only for non-executable work; it must say why.
- Reviews of `required` tasks must record test evidence: a pointer
  showing the named red test existed before implementation, and a
  pointer showing the same test is green on the closing commit.

## Completion reports

Completion reports are protocol-defined handoff result artifacts. They
live in `reports/`.

### Report naming

- Task completion reports: `reports/REPORT-NN-NNN.md`
- Planning-checkpoint reports: `reports/REPORT-PLAN-NNN-slug.md`

### Report contents — task completion

Task reports must include:

- `Status: complete | blocked | failed`
- Task ID.
- Prompt path.
- Task path.
- Target repo path.
- Files changed.
- Test evidence.
- Commit SHA or PR URL, when applicable.
- Remaining risks.
- Ready for review: `yes | no`.

### Report contents — review completion

Review reports must include:

- `Status: complete | blocked | failed`
- Review ID.
- Prompt path.
- Review file path.
- Evidence reviewed.
- Verdict: `approve | request-changes | reject`.
- Blocking findings, when applicable.

### Secret redaction

Reports must not include secrets or unnecessary environment-specific
sensitive data. Assignees and wrappers should redact API keys,
credentials, tokens, private connection strings, and comparable values
before writing reports.

### Stale report deletion

Before issuing any new or retry handoff, including manual command
instructions, the PM must delete any existing report at the expected
protocol-derived report path. This prevents stale reports from being
mistaken for the current handoff result when a task returns from review
or is retried after a blocker.

### Report parsing

Report parsing outcomes are:

- `accepted` — the report exists, is well-formed, includes all required
  fields, and its status or verdict can be acted on.
- `blocked` — the report is well-formed and explicitly reports `blocked`,
  or the PM cannot proceed without operator judgment.
- `failed` — the report is well-formed and explicitly reports `failed`.
- `failed-to-parse` — the report is missing, malformed, incomplete,
  internally inconsistent, uses an unsupported status or verdict, or
  contradicts the expected task/review/prompt paths.

`failed-to-parse` is a PM-level blocker. The PM must stop automation,
preserve the prompt and any invalid report for inspection, avoid
lifecycle movement, and surface the parse failure to the operator.

Report files are read by the PM and may be summarized into task, review,
decision, or state files as appropriate. Reports are not a replacement
for task, review, or decision records.

## Project scope

A Cartopian project directory is a governance container, not a codebase.

**What it is:**

- Tracks phase progress against `IMPLEMENTATION_PLAN.md`.
- Holds specs, tasks, reviews, and decisions that guide the work.
- Keeps one short, always-current state file (`STATE.md`) so every
  session starts on the same page.
- Records decisions once, in one place (`decisions/`).
- Holds temporary assignee prompts in `prompts/`.
- Holds handoff result reports in `reports/`.

**What it is not:**

- Not the product codebase. Source code lives in the target repos.
- Not a workspace shell for the product repos.
- Not a chat log, journal, or prompt archive.

## Plan lifecycle

A Cartopian project has one active implementation plan at a time. The
live `REQUIREMENTS.md`, `IMPLEMENTATION_PLAN.md`, `phases/`, `tasks/`,
`specs/`, `reviews/`, `decisions/`, `prompts/`, and `reports/` describe
the current plan only.

When a plan completes, close it before starting a new plan. The
canonical closeout workflow is `skills/close-plan.md`.

Plan closeout preconditions:

- No task files remain in `tasks/open/`, `tasks/in-progress/`, or
  `tasks/in-review/`.
- `prompts/` contains no active or ambiguous prompt files.
- `reports/` contains no unresolved or ambiguous reports.
- Phase exit criteria are satisfied by completed tasks, decisions,
  specs, or documented operator acceptance.
- The operator explicitly confirms that the current plan should close.

Plan closeout always resets these live artifacts:

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
live artifacts. A new planning cycle must produce fresh requirements and
a fresh implementation plan.

`ENGINEERING.md` and `CONVENTIONS.md` may carry forward only when the
operator explicitly chooses to keep them as seed context for the next
plan. If not carried forward, reset them to project seed files.

`cartopian.toml` remains live across plans. 

### Optional plan archive

Cartopian is anti-archival by default. Do not archive completed plan
artifacts unless the operator explicitly asks for an archive during
closeout.

When requested, create `archive/PLAN-NNN-slug/` using the next available
three-digit counter. The archive may include snapshots of:

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

Do not archive `prompts/`. Prompt files are temporary handoff artifacts.

After closeout, `STATE.md` must say there is no active plan and name
`skills/plan-project.md` as the next action.

## Task lifecycle

The canonical workflow is `Plan -> Spec -> Test -> Code`.

1. Read the relevant `IMPLEMENTATION_PLAN.md` section and the current
   phase file.
2. Draft a task in `tasks/open/`.
3. If the task needs a new external interface, draft a spec in `specs/`.
4. Record the task's test gate.
5. PM creates an assignee-directed prompt (`prompts/PROMPT-NN-NNN.md`)
   with absolute paths and proposes an assignee.
6. PM deletes any existing report at the expected report path
   (`reports/REPORT-NN-NNN.md`).
7. Operator confirms assignment or start (or PM launches via CLI
   handoff when `auto_start = true` and the run policy allows it).
8. Task moves to `tasks/in-progress/`.
9. Assignee creates or confirms red tests before implementation (when
   test gate is `required`).
10. Assignee produces a completion report at `reports/REPORT-NN-NNN.md`.
    The PM reads the report, moves the task to `tasks/in-review/`, and
    assigns a reviewer.
11. Reviewer reviews. Review verdicts are applied by the PM:
    - **`approve`**: PM moves task to `done/`. PM deletes the matching
      prompt if it exists.
    - **`request-changes`**: PM moves task back to `in-progress/`.
    - **`reject`**: PM moves task back to `open/`.

If the operator reports completion before the protocol recorded
assignment/start, fast-forward the task to `in-review/` or `done/` as
the evidence supports, then refresh `STATE.md`.

## Lifecycle authority

The PM owns task lifecycle movement. Reviewers create or update review
files and record a verdict, but they do not move task files between
`open/`, `in-progress/`, `in-review/`, or `done/`.

After a review verdict, the PM applies the corresponding task movement.

## Sizing

- `STATE.md` has a hard ceiling of 5KB.
- Task files are assignment-sized. They should contain enough context to
  do and review the work, not a running journal.
- Open task files should usually stay under 2KB. Completed tasks may be
  larger when they need closure evidence.
- Phase files are roll-ups of plan refs, task coverage, dependencies,
  and exit criteria. Do not use them as progress journals.
- Specs have no fixed ceiling, but prefer specificity over
  comprehensiveness.

## Decision log discipline

- Every non-trivial decision gets its own file in `decisions/`, named
  `DEC-NNN-kebab-case-slug.md`.
- `decisions/INDEX.md` is a one-line-per-decision summary table.
- A decision that changes a prior decision creates a new file that says
  `Supersedes: DEC-NNN`. The old file is not edited.

## Git (optional)

The `projects/` directory is its own git repo, tracking all project PM
data in a single history. This avoids creating a separate PM repo per
project and eliminates naming collisions with code repos.

If `git_versioning = true` in the effective `cartopian.toml`
configuration after project overrides:

- Auto-commit and auto-push at session close. Invisible to operator.
- Commit messages describe the change at the unit-of-work grain.
- Product-repo commits keep red-then-green test-gate discipline.

If `git_versioning = false` in the effective configuration:

- No git operations. Filesystem is the only record.
- `STATE.md` is current; name the next action.

## Session open

1. Read `STATE.md`.
2. Read the phase file for the current phase.
3. Read any active task files and their specs.
4. Go.

## Session close-out

1. Every task that changed status has been moved to its new directory.
2. Any completed task's prompt has been deleted from `prompts/`.
3. Any decisions taken are recorded in `decisions/` and the index is
   updated.
4. `STATE.md` is refreshed and still under 5KB.
5. If git is enabled: auto-commit and auto-push (PM handles this).
6. The final message names the exact next thing to do.
