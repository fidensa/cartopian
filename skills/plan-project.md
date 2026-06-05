# Skill: Plan Project

Walk the full Cartopian lifecycle: requirements gathering → implementation plan → phases → tasks, with optional review checkpoints at every stage.

Use this skill when you are starting from scratch and want a guided requirements conversation before generating a plan. If you already have requirements or a plan from an external source, consider the targeted alternatives first:

- **`adopt-requirements`** — generate `REQUIREMENTS.md` from an existing JIRA story, Confluence document, or any external requirements source, without running the full planning pipeline. Feed its output into this skill starting at Stage 2, or into `adopt-plan`.
- **`adopt-plan`** — migrate an existing implementation plan (JIRA epic, Confluence doc, slide deck, or any structured plan) into Cartopian format, without a requirements-gathering conversation. Requirements may be referenced externally, summarized as a stub, or adopted inline.

**Output:** A fully planned project with `REQUIREMENTS.md`, `IMPLEMENTATION_PLAN.md`, phase files, task files, spec files, and an up-to-date `STATE.md`.

**Protocol reference:** This skill does not require the whole protocol document. When a stage needs protocol rules beyond what is written here, read only the relevant section via the section-scoped resource surface:

- `cartopian://protocol/CONVENTIONS/roles` — role declaration and reviewer resolution (Stage 0).
- `cartopian://protocol/CONVENTIONS/reviews` — review artifact rules behind the checkpoints.
- `cartopian://protocol/CONVENTIONS/plan-lifecycle` — the plan, phase, task, and spec generation contract (Stages 2-4).
- `cartopian://protocol/CONVENTIONS/session-state` — `STATE.md` rules (Stage 5).

The full `cartopian://protocol/CONVENTIONS` remains the authoritative contract; do not load it whole for this skill.

---

## Prerequisites

- The project directory exists with the correct structure (run `skills/init-project.md` first if needed).
- A project-level `cartopian.toml` exists with `[project]` configured.
- The project is discoverable via `cartopian discover-projects` (registered), or you know its absolute path for `cartopian resolve-config`.

---

## Preflight — Active Plan Check

Before gathering requirements, check whether the project already has a live plan:

1. Read `STATE.md`.
2. Check for `IMPLEMENTATION_PLAN.md`.
3. Check whether `phases/`, `tasks/`, `specs/`, or `reviews/` contain current plan artifacts.

If `IMPLEMENTATION_PLAN.md` exists and the operator wants a fresh plan, stop and run `skills/close-plan.md` first. Do not overwrite an active plan as a way to start over.

If `IMPLEMENTATION_PLAN.md` exists and the operator wants to revise the current plan, proceed only as an in-place revision of the current plan's artifacts. Make the revision path explicit to the operator before editing.

If `IMPLEMENTATION_PLAN.md` does not exist but current plan artifacts exist in `phases/`, `tasks/`, `specs/`, or `reviews/`, stop and ask the operator to resolve the inconsistent state before planning.

If `STATE.md` says there is no active plan but current plan artifacts still exist, stop and ask the operator to resolve the inconsistent state. The normal resolution is to run `skills/close-plan.md`.

If a previous closeout carried forward `STANDARDS.md` or `CONVENTIONS.md`, treat those files as seed context for the new planning cycle, not as locked requirements or a locked implementation plan.

---

## Stage 0 — Role And Handoff Resolution

1. Select the active project from the registry.

   - Run `cartopian discover-projects` and choose the entry by `id`/`label`; capture its `path` as the project's absolute path.
   - If the project is not yet registered but you know its absolute path, proceed with that path and register it later via `cartopian register-project`.

2. Resolve the effective configuration via the Core CLI.

   - Run `cartopian resolve-config <project-path>` to obtain the canonical `project_path` and merged `roles`, `handoffs`, `automation`, `work_roots`, and `git_versioning`.
   - Use this emitted record instead of reading and merging TOML by hand.
5. Determine whether a **reviewer** is configured. A reviewer is considered configured when `reviewer` appears as a key in the resolved `[roles]` table. If `[handoffs.reviewer]` is also configured, review checkpoints dispatch automatically; if only the `[roles].reviewer` key is present, review checkpoints dispatch manually (the PM surfaces the prompt; the operator acts).
6. If `reviewer` is not declared in `[roles]`, ask the operator:

   > "No reviewer is configured. Do you want to designate a reviewer for this planning session? If not, we'll proceed without review checkpoints."

7. If the operator provides a reviewer, note it for use at review checkpoints. If not, proceed without review checkpoints and note this in STATE.md.

---

## Stage 1 — Requirements Gathering

### 1.1 Check for existing requirements

Check if a `REQUIREMENTS.md` exists in the project directory.

- If it exists and is populated (including a reference stub from `adopt-requirements`), ask the operator: "Requirements already exist. Do you want to revise them for this planning cycle, or proceed to planning from them?"
- If the operator wants to proceed from existing requirements, skip Stages 1.2–1.4 and go directly to Stage 2.
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

Authoring `REQUIREMENTS.md` is a **PM-performed** write. The contained PM has no raw `Write` tool, so author it through the mediated writer (use the structure that emerged from the conversation, not a rigid template):

```
cartopian write-requirements <project-root> --content-file <body-path>
```

### 1.4 Generate STANDARDS.md

Compose `STANDARDS.md` from the requirements, any carried-forward standards seed, and any architectural principles or technical needs discussed — the chosen tools/stack, working standards, and any constraints deduced from this cycle's requirements. Author or update it through the mediated writer (a **PM-performed** write):

```
cartopian write-standards <project-root> --content-file <body-path>
```

### 1.5 Review checkpoint

If a reviewer is configured:

1. Run planning-review checkpoint `001 requirements-and-engineering` using the Review Flow Reference.
2. Target artifacts: `REQUIREMENTS.md` and `STANDARDS.md`.
3. If `approve`: proceed to Stage 2.
4. If `request-changes`: revise the target artifacts in place and rerun the checkpoint.
5. If `reject`, blocked, failed, or failed-to-parse: stop and return control to the operator.
6. If the operator says "skip review": proceed without review and note this in STATE.md.

---

## Stage 2 — Implementation Plan Generation

### 2.1 Read inputs

1. Read the locked `REQUIREMENTS.md`.
2. Read the current-cycle `STANDARDS.md` as technical constraints.
3. Read the templates in `templates/IMPLEMENTATION_PLAN.md` for structural guidance.

### 2.2 Generate IMPLEMENTATION_PLAN.md

Authoring the plan is **PM-performed**; compose the body and write it through the mediated writer (never a raw `Write`):

```
cartopian write-plan <project-root> --content-file <body-path>
```

The `IMPLEMENTATION_PLAN.md` body must contain:

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

1. Run planning-review checkpoint `002 implementation-plan` using the Review Flow Reference.
2. Target artifact: `IMPLEMENTATION_PLAN.md`.
3. If `approve`: proceed to Stage 3.
4. If `request-changes`: revise the implementation plan in place and rerun the checkpoint.
5. If `reject`, blocked, failed, or failed-to-parse: stop and return control to the operator.

---

## Stage 3 — Phase Generation

### 3.1 Read inputs

Read the locked `IMPLEMENTATION_PLAN.md`.

### 3.2 Generate phase files

Authoring phase files is **PM-performed**. For each phase in the plan, author `phases/PHASE-NN-slug.md` through the mediated writer `cartopian write-phase` (the `--phase-id` resolves the allowlisted `phases/` destination, so the PM supplies the id, not a path):

```
cartopian write-phase <project-root> --phase-id PHASE-NN-slug --content-file <body-path>
```

Each phase body contains:

- **Phase goal**: one or two sentences.
- **Plan refs covered**: list from the plan's phase table.
- **Build items**: tasks that produce code or artifacts.
- **Research items**: tasks that produce knowledge or decisions.
- **Exit criteria**: copied from the plan.
- **Dependencies on prior phases**: what must be done before this phase can start.

Use the phase number and slug from the plan. The two-digit phase number (`NN`) must match the plan section number.

### 3.3 Review checkpoint

If a reviewer is configured:

1. Run planning-review checkpoint `003 phases` using the Review Flow Reference.
2. Target artifacts: `phases/PHASE-*.md`.
3. If `approve`: proceed to Stage 4.
4. If `request-changes`: revise phase files in place and rerun the checkpoint.
5. If `reject`, blocked, failed, or failed-to-parse: stop and return control to the operator.

---

## Stage 4 — Task and Spec Generation

### 4.1 Determine scope

Generate tasks for the **current active phase** (or Phase 00 / Phase 01 if starting fresh). Do not generate tasks for all phases at once — later phases may change as earlier work completes.

### 4.2 Generate task files

Authoring task files is **PM-performed**. For each build and research item in the active phase, author `tasks/open/TASK-NN-NNN-slug.md` through the mediated writer `cartopian write-task`, following the template in `templates/TASK.md`:

```
cartopian write-task <project-root> --task-id TASK-NN-NNN --slug <slug> --content-file <body-path>
```

New tasks land in `tasks/open/` (the lifecycle entry point); `move-task` advances them from there. Populate the body from the plan ref, phase file, resolved roles, repo subpath, dependencies, evidence gate, and checkable acceptance criteria.

### 4.3 Generate spec files

For tasks that need specs (new interfaces, schemas, contracts), author `specs/SPEC-NN-NNN-slug.md` through the mediated writer `cartopian write-spec`, following the template in `templates/SPEC.md` (a **PM-performed** write):

```
cartopian write-spec <project-root> --spec-id SPEC-NN-NNN --slug <slug> --content-file <body-path>
```

Not every task needs a spec. Use judgment: configuration tasks, documentation tasks, and simple implementation tasks typically do not need specs.

### 4.4 Review checkpoint

If a reviewer is configured:

1. Run planning-review checkpoint `004 tasks-and-specs` using the Review Flow Reference.
2. Target artifacts: generated files in `tasks/open/` and `specs/`.
3. If `approve`: proceed to Stage 5.
4. If `request-changes`: revise tasks and specs in place and rerun the checkpoint.
5. If `reject`, blocked, failed, or failed-to-parse: stop and return control to the operator.

---

## Stage 5 — State Initialization

### 5.1 Update STATE.md

Updating `STATE.md` is **PM-performed**. Compose the state body and write it through the mediated writer (never a raw `Write`):

```
cartopian write-state <project-root> --content-file <body-path>
```

The body reflects:

- **Current phase**: the first active phase
- **Active work**: none yet (nothing assigned)
- **Open work**: all generated tasks with brief descriptions
- **What to do next**: suggest the first task to assign, or instruct the operator to review the plan and begin assignment. Do not create a prompt during planning unless assignment is happening immediately; prompts belong in `prompts/` and are temporary handoff artifacts.

### 5.2 Final summary

Print a summary of everything that was produced:

- Number of requirements captured
- Number of phases generated
- Number of tasks and specs generated
- Review status (reviewed or skipped, with any noted findings)
- Resolved handoff configuration (which roles have CLI targets)
- Resolved automation policy
- Suggested first action, including whether to create `prompts/PROMPT-NN-NNN.md` for the first assignment

---

## Review Flow Reference

Planning-checkpoint reviews use `REVIEW-PLAN-NNN-slug.md` in `reviews/` (authored by the reviewer, who is not contained). The PM authors a matching `PROMPT-PLAN-NNN-slug.md` in `prompts/` through the mediated writer — `cartopian write-prompt <project-root> --prompt-id PROMPT-PLAN-NNN-slug --content-file <body-path>` — to hand off the review work; the contained PM has no raw `Write`. `NNN` is a per-project sequential counter independent of task-scoped numbering — no tasks exist at the point of requirements generation.

The standard checkpoint sequence is:

| NNN | Stage | Shared slug | Prompt | Report | Review |
| --- | --- | --- | --- | --- | --- |
| 001 | Requirements & Engineering | `requirements-and-engineering` | `PROMPT-PLAN-001-requirements-and-engineering.md` | `REPORT-PLAN-001-requirements-and-engineering.md` | `REVIEW-PLAN-001-requirements-and-engineering.md` |
| 002 | Implementation Plan | `implementation-plan` | `PROMPT-PLAN-002-implementation-plan.md` | `REPORT-PLAN-002-implementation-plan.md` | `REVIEW-PLAN-002-implementation-plan.md` |
| 003 | Phases | `phases` | `PROMPT-PLAN-003-phases.md` | `REPORT-PLAN-003-phases.md` | `REVIEW-PLAN-003-phases.md` |
| 004 | Tasks & Specs | `tasks-and-specs` | `PROMPT-PLAN-004-tasks-and-specs.md` | `REPORT-PLAN-004-tasks-and-specs.md` | `REVIEW-PLAN-004-tasks-and-specs.md` |

At every review checkpoint:

1. Author the checkpoint prompt at the table's prompt path via `cartopian write-prompt` (see the note above), resolved to an absolute project path. Include absolute paths to the target artifacts, the expected review file, the expected report file, and `templates/REPORT.md`.
2. Call `skills/run-handoff.md` with:
   - Role: `reviewer`
   - Absolute prompt path: `<project>/prompts/PROMPT-PLAN-NNN-slug.md`
   - Absolute report path: `<project>/reports/REPORT-PLAN-NNN-slug.md`
   - Expected report variant: planning-review completion
   - Allowed lifecycle action: return outcome to this skill
3. Require the reviewer to create `reviews/REVIEW-PLAN-NNN-slug.md` using `templates/REVIEW.md`.
4. Apply the returned verdict in the stage-specific checkpoint section.

Completion detection at every checkpoint uses the lower-level wait primitive on the checkpoint report path rather than a hand-rolled timing loop or a manual "tell me when the review is done" prompt:

```
cartopian wait-report <project>/reports/REPORT-PLAN-NNN-slug.md --max-block <duration>
```

`cartopian wait-report` is a read-only observer: the report file is the authoritative completion signal. It emits `accepted` when the planning-review report is present and parses, a `[guard]` failure when a report is present but not acceptable, or `still_running` when the `--max-block` budget elapses before the report lands. On `still_running`, yield control back to the operator and re-call `wait-report` on resume; the filesystem observation survives the yield. When the checkpoint is dispatched through `skills/run-handoff.md`, that skill owns this wait step under the same contract.

If the operator says "skip review" at any checkpoint, do not call `skills/run-handoff.md`; proceed without review and note the skip in `STATE.md`.

`skills/run-handoff.md` owns stale report deletion, manual versus CLI handoff behavior, timeout enforcement, completion waiting via `cartopian wait-handoff` / `cartopian wait-report`, report parsing, and sequential automation boundaries.

Planning-checkpoint prompts and reviews are temporary artifacts. When a planning stage is approved or superseded, clear its prompt and report artifacts using the Core CLI and keep the review as the durable record:

- Remove the checkpoint prompt:

  ```
  cartopian delete-prompt <project-path>/prompts/PROMPT-PLAN-NNN-<slug>.md
  ```

- Remove the checkpoint report (if present):

  ```
  cartopian delete-report <project-path>/reports/REPORT-PLAN-NNN-<slug>.md
  ```

No archival for prompts or reports.

This creates a quality gate at every level of the hierarchy while keeping the operator in control of the pace.

## Handoff Automation Reference

This skill supports CLI handoff automation for review checkpoints by delegating handoff mechanics to `skills/run-handoff.md`.
