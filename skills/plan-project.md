# Skill: Plan Project

Walk the full Cartopian lifecycle: requirements → implementation plan →
phases → tasks, with optional review checkpoints at every stage.

This is the core skill. It unlocks the system's value by guiding the
agent and operator through the "happy path" that Cartopian was designed
around.

**Output:** A fully planned project with `REQUIREMENTS.md`,
`IMPLEMENTATION_PLAN.md`, phase files, task files, spec files, and an
up-to-date `STATE.md`.

---

## Prerequisites

- The project directory exists with the correct structure (run
  `skills/init-project.md` first if needed).
- A project-level `cartopian.toml` exists with `[project]` configured.

---

## Preflight — Active Plan Check

Before gathering requirements, check whether the project already has a
live plan:

1. Read `STATE.md`.
2. Check for `IMPLEMENTATION_PLAN.md`.
3. Check whether `phases/`, `tasks/`, `specs/`, or `reviews/` contain
   current plan artifacts.

If `IMPLEMENTATION_PLAN.md` exists and the operator wants a fresh plan,
stop and run `skills/close-plan.md` first. Do not overwrite an active
plan as a way to start over.

If `IMPLEMENTATION_PLAN.md` exists and the operator wants to revise the
current plan, proceed only as an in-place revision of the current plan's
artifacts. Make the revision path explicit to the operator before
editing.

If `IMPLEMENTATION_PLAN.md` does not exist but current plan artifacts
exist in `phases/`, `tasks/`, `specs/`, or `reviews/`, stop and ask the
operator to resolve the inconsistent state before planning.

If `STATE.md` says there is no active plan but current plan artifacts
still exist, stop and ask the operator to resolve the inconsistent
state. The normal resolution is to run `skills/close-plan.md`.

If a previous closeout carried forward `ENGINEERING.md` or
`CONVENTIONS.md`, treat those files as seed context for the new planning
cycle, not as locked requirements or a locked implementation plan.

---

## Stage 0 — Role And Handoff Resolution

1. Read the project's `cartopian.toml` and the workspace `cartopian.toml`.
2. Resolve the effective role kind for each role (project overrides
   workspace).
3. Resolve the effective handoff target for each agent role: check
   project `[handoffs.*]`, then fall back to workspace `[handoffs.*]`.
4. Resolve the effective automation policy: check project `[automation]`,
   then fall back to workspace `[automation]`, then fall back to protocol
   defaults (`confirmation = "each-handoff"`, `max_handoffs_per_run = 1`).
5. Determine whether a **reviewer** is configured (role kind is not
   `""` or `"none"`).
6. If no reviewer is configured, ask the operator:

   > "No reviewer is configured. Do you want to designate a reviewer for
   > this planning session? If not, we'll proceed without review
   > checkpoints."

7. If the operator provides a reviewer, note it for use at review
   checkpoints. If not, proceed without review checkpoints and note this
   in STATE.md.

---

## Stage 1 — Requirements Gathering

### 1.1 Check for existing requirements

Check if a `REQUIREMENTS.md` exists in the project directory.

- If it exists and is populated, ask the operator: "Requirements already
  exist. Do you want to revise them for this planning cycle, or proceed
  to planning from them?"
- If it does not exist (or is empty), proceed to gathering.

### 1.2 Engage the operator

Do **not** present a blank form. Be conversational. Draw out requirements through dialogue:

1. Start with the thesis: "What is this project? What problem does it solve? Be precise — tell me what it is and what it is not."
2. Move to users: "Who is this for? Who is it explicitly not for?"
3. Explore the product model: "How does it work at a high level? Walk me through what a user experiences."
4. If this is a technical project, explore architecture principles: "What structural rules should govern the build?"
5. Work through functional requirements: "What must the system do? Let's enumerate specific capabilities." Push for numbered, specific items.
6. Cover non-functional requirements: "What qualities must it have? Performance, security, reliability — what matters?"
7. Surface open questions: "What decisions are you deferring for now?"

**Adapt the structure to fit the project.** Not every project needs every section. A documentation project doesn't need architecture principles. A CLI tool might not need non-functional requirements beyond "it runs fast." Use judgment.

**Challenge vague statements.** If the operator says "it should be fast," ask "how fast? What's the latency target?" Push for specificity, because vague requirements produce vague plans.

### 1.3 Produce REQUIREMENTS.md

Write `REQUIREMENTS.md` in the project directory. Use the structure that emerged from the conversation, not a rigid template.

### 1.4 Generate ENGINEERING.md

Based on the requirements, any carried-forward engineering seed, and any
architectural principles or technical needs discussed, generate or update
`ENGINEERING.md` in the project directory. This document should capture
the chosen tech stack, technical standards, and any constraints deduced
from the current planning cycle's requirements.

### 1.5 Review checkpoint

If a reviewer is configured:

1. Create `prompts/PROMPT-PLAN-001-requirements-and-engineering.md`
   to hand off the review. Include absolute paths to all files the
   reviewer needs to read.
2. Delete any existing report at `reports/REPORT-PLAN-001-requirements-and-engineering.md`.
3. For `auto_start = true` reviewer handoffs allowed by the current run
   policy, launch the configured executable:
   ```text
   <agent> '<absolute prompt path>'
   ```
4. For `auto_start = false` reviewer handoffs, tell the operator the
   exact command to run, shell-quoting the prompt path.
5. Read the completion report at `reports/REPORT-PLAN-001-requirements-and-engineering.md`.
   Parse the report as described in `protocol/CONVENTIONS.md` § Report
   parsing.
6. The reviewer produces
   `reviews/REVIEW-PLAN-001-requirements-and-engineering.md` using the
   `REVIEW` template format (severity: blocker, major, minor, nit;
   verdict: approve, request-changes, reject).
7. If `request-changes`: the PM revises the target artifacts in place
   against the findings, updates or recreates the review prompt in
   `prompts/`, deletes the existing report, and re-assigns to the
   reviewer.
8. If `approve`: proceed to Stage 2.
9. If the operator says "skip review" at any point: proceed without
   review and note this in STATE.md.

---

## Stage 2 — Implementation Plan Generation

### 2.1 Read inputs

1. Read the locked `REQUIREMENTS.md`.
2. Read the current-cycle `ENGINEERING.md` as technical constraints.
3. Read the templates in `templates/IMPLEMENTATION_PLAN.md` for structural guidance.

### 2.2 Generate IMPLEMENTATION_PLAN.md

Write `IMPLEMENTATION_PLAN.md` in the project directory with:

- **Purpose**: what this plan accomplishes and which source documents it derives from.
- **Architecture rules**: rules derived from requirements and engineering standards. These are consequences of locked inputs, not new decisions.
- **Repo topology**: which repos are involved and what each owns. For single-repo projects, a brief note.
- **Phase sequence**: each phase with:
  - Goal
  - Plan ref table (`PNN-KIND-NNN` format) listing build and research items
  - Exit criteria
- **Requirement coverage matrix**: every requirement from `REQUIREMENTS.md` mapped to plan ref(s) and phase(s). Every requirement must appear. Deferred requirements note the reason.
- **Open questions by phase**: questions that arose during planning.
- **Exit criteria summary**: per-phase exit criteria in one place.

### 2.3 Review checkpoint

If a reviewer is configured:

1. Create `prompts/PROMPT-PLAN-002-implementation-plan.md` to
   hand off the review. Include absolute paths.
2. Delete any existing `reports/REPORT-PLAN-002-implementation-plan.md`.
3. Launch or instruct the handoff per the resolved handoff config and
   run policy, shell-quoting the prompt path.
4. Read the completion report. Parse the report.
5. The reviewer produces
   `reviews/REVIEW-PLAN-002-implementation-plan.md`. On
   `request-changes`, iterate as in Stage 1.5. Proceed to Stage 3 on
   approval.

---

## Stage 3 — Phase Generation

### 3.1 Read inputs

Read the locked `IMPLEMENTATION_PLAN.md`.

### 3.2 Generate phase files

For each phase in the plan, create `phases/PHASE-NN-slug.md` with:

- **Phase goal**: one or two sentences.
- **Plan refs covered**: list from the plan's phase table.
- **Build items**: tasks that produce code or artifacts.
- **Research items**: tasks that produce knowledge or decisions.
- **Exit criteria**: copied from the plan.
- **Dependencies on prior phases**: what must be done before this phase
  can start.

Use the phase number and slug from the plan. The two-digit phase number
(`NN`) must match the plan section number.

### 3.3 Review checkpoint

If a reviewer is configured:

1. Create `prompts/PROMPT-PLAN-003-phases.md` to hand off the
   review. Include absolute paths.
2. Delete any existing report at `reports/REPORT-PLAN-003-phases.md`.
3. Launch or instruct the handoff per the resolved handoff config and
   run policy.
4. Read the completion report. Parse the report.
5. The reviewer produces `reviews/REVIEW-PLAN-003-phases.md`. Review
   phase files against the plan. On `request-changes`, iterate as in
   Stage 1.5.

---

## Stage 4 — Task and Spec Generation

### 4.1 Determine scope

Generate tasks for the **current active phase** (or Phase 00 / Phase 01
if starting fresh). Do not generate tasks for all phases at once —
later phases may change as earlier work completes.

### 4.2 Generate task files

For each build and research item in the active phase, create
`tasks/open/TASK-NN-NNN-slug.md` following the template in
`templates/TASK.md`:

- **Phase**: `PHASE-NN-slug`
- **Plan ref**: `PNN-KIND-NNN`
- **Target repo**: from config or plan
- **Assignee**: based on resolved role kind and handoff target
- **Spec**: reference if a spec is needed, `none` otherwise
- **Dependencies / Blocked by**: from phase dependencies and
  cross-task relationships
- **Test gate**: `required` or `n/a` with reason
- **Goal**: what "done" looks like
- **Acceptance criteria**: checkable, boolean-verifiable items

### 4.3 Generate spec files

For tasks that need specs (new interfaces, schemas, contracts), create
`specs/SPEC-NN-NNN-slug.md` following the template in
`templates/SPEC.md`.

Not every task needs a spec. Use judgment: configuration tasks,
documentation tasks, and simple implementation tasks typically do not
need specs.

### 4.4 Review checkpoint

If a reviewer is configured:

1. Create `prompts/PROMPT-PLAN-004-tasks-and-specs.md` to hand
   off the review. Include absolute paths.
2. Delete any existing report at `reports/REPORT-PLAN-004-tasks-and-specs.md`.
3. Launch or instruct the handoff per the resolved handoff config and
   run policy.
4. Read the completion report. Parse the report.
5. The reviewer produces
   `reviews/REVIEW-PLAN-004-tasks-and-specs.md`. Review tasks and specs
   for completeness, traceability, and scope. On `request-changes`,
   iterate as in Stage 1.5.

---

## Stage 5 — State Initialization

### 5.1 Update STATE.md

Generate or update `STATE.md` reflecting:

- **Current phase**: the first active phase
- **Active work**: none yet (nothing assigned)
- **Open work**: all generated tasks with brief descriptions
- **What to do next**: suggest the first task to assign, or instruct
  the operator to review the plan and begin assignment. Do not create a
  prompt during planning unless assignment is happening immediately;
  prompts belong in `prompts/` and are temporary handoff artifacts.

### 5.2 Final summary

Print a summary of everything that was produced:

- Number of requirements captured
- Number of phases generated
- Number of tasks and specs generated
- Review status (reviewed or skipped, with any noted findings)
- Resolved handoff configuration (which roles have CLI targets)
- Resolved automation policy
- Suggested first action, including whether to create
  `prompts/PROMPT-NN-NNN.md` for the first assignment

---

## Review Flow Reference

Planning-checkpoint reviews use `REVIEW-PLAN-NNN-slug.md` in
`reviews/`. The PM creates a matching `PROMPT-PLAN-NNN-slug.md` in
`prompts/` to hand off the review work. `NNN` is a per-project
sequential counter independent of task-scoped numbering — no tasks
exist at the point of requirements generation.

The standard checkpoint sequence is:

| NNN | Stage | Shared slug | Prompt | Report | Review |
|---|---|---|---|---|---|
| 001 | Requirements & Engineering | `requirements-and-engineering` | `PROMPT-PLAN-001-requirements-and-engineering.md` | `REPORT-PLAN-001-requirements-and-engineering.md` | `REVIEW-PLAN-001-requirements-and-engineering.md` |
| 002 | Implementation Plan | `implementation-plan` | `PROMPT-PLAN-002-implementation-plan.md` | `REPORT-PLAN-002-implementation-plan.md` | `REVIEW-PLAN-002-implementation-plan.md` |
| 003 | Phases | `phases` | `PROMPT-PLAN-003-phases.md` | `REPORT-PLAN-003-phases.md` | `REVIEW-PLAN-003-phases.md` |
| 004 | Tasks & Specs | `tasks-and-specs` | `PROMPT-PLAN-004-tasks-and-specs.md` | `REPORT-PLAN-004-tasks-and-specs.md` | `REVIEW-PLAN-004-tasks-and-specs.md` |

At every review checkpoint, this skill instructs the agent to:

1. Create a `prompts/PROMPT-PLAN-NNN-slug.md` to hand off the review.
   Include absolute paths to all files the reviewer needs.
2. Delete any existing report at the expected report path
   (`reports/REPORT-PLAN-NNN-slug.md`).
3. For `auto_start = true` reviewer handoffs allowed by the current run
   policy, launch the configured executable. Pass the prompt path as a
   single argument and shell-quote it in operator-facing command text.
4. For `auto_start = false`, tell the operator the exact command to run.
5. Read the completion report. Parse the report per
   `protocol/CONVENTIONS.md` § Report parsing.
6. Collect the review as `reviews/REVIEW-PLAN-NNN-slug.md` using the
   `REVIEW` template format:
   - Findings with severity (blocker, major, minor, nit)
   - Verdict (approve, request-changes, reject)
7. If `request-changes`: the PM revises the target artifacts in place
   against the findings, updates or recreates the review prompt in
   `prompts/`, deletes the existing report, and re-assigns to the
   reviewer.
8. If `approve`: proceed to the next stage.
9. If the operator says "skip review" at any checkpoint: proceed without
   review and note this in STATE.md.
10. Stop when a report is blocked, failed, missing, malformed,
    incomplete, inconsistent, late, ambiguous, or requires operator
    judgment. Surface the issue to the operator.

Launch handoffs sequentially, even when `confirmation = "until-blocked"`.
Enforce configured handoff timeouts for PM-launched processes.

Planning-checkpoint prompts and reviews are temporary artifacts. Delete
them when the planning stage is approved or superseded. No archival.

This creates a quality gate at every level of the hierarchy while keeping
the operator in control of the pace.

## Handoff Automation Reference

This skill supports CLI handoff automation for review checkpoints. The
resolved handoff configuration determines behavior:

- **No handoff config**: PM creates the prompt and presents it to the
  operator. Manual workflow.
- **`auto_start = false`**: PM creates the prompt and tells the operator
  the exact command: `<agent> '<absolute prompt path>'`.
- **`auto_start = true`**: PM creates the prompt, deletes any stale
  report, and launches the executable when allowed by the current run
  policy.

After any handoff (manual or automated), the PM reads the completion
report at the protocol-derived path, parses it, and applies PM lifecycle
changes. The PM does not launch the next handoff until the current one
is fully processed.
