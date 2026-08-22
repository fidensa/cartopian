# Skill: Plan Project

Walk the full Cartopian lifecycle: requirements gathering → implementation plan → phases → tasks, with optional review checkpoints at every stage.

Use this skill when you are starting from scratch and want a guided requirements conversation before generating a plan. If you already have requirements or a plan from an external source, consider the targeted alternatives first:

- **`adopt-requirements`** — generate `REQUIREMENTS.md` from an existing JIRA story, Confluence document, or any external requirements source, without running the full planning pipeline. Feed its output into this skill starting at Stage 2, or into `adopt-plan`.
- **`adopt-plan`** — migrate an existing implementation plan (JIRA epic, Confluence doc, slide deck, or any structured plan) into Cartopian format, without a requirements-gathering conversation. Requirements may be referenced externally, summarized as a stub, or adopted inline.

**Output:** A fully planned project with `REQUIREMENTS.md`, `IMPLEMENTATION_PLAN.md`, phase files, task files, spec files, and an up-to-date `STATE.md`.

## Intake precondition

Before deriving planning artifacts, resolve the governed unit's exact operator
evidence from the three supported source kinds: structurally marked decision
quotations, supported host chat records, and optional immutable request records.
A native host adapter is optional when another supported source resolves. The PM
never copies, paraphrases, or reconstructs operator words; ordinary PM prose is
excluded from operator evidence. Mediated writers fail closed only when no
applicable exact source of any supported kind resolves for the governed unit.
Explicit corrections retain their provenance and deterministic order before PM
revisions. Do not ask the operator to restate the request later.

**Protocol reference:** This skill does not require the whole protocol document. When a stage needs protocol rules beyond what is written here, read only the relevant section via the section-scoped resource surface:

- `cartopian://protocol/CONVENTIONS/roles` — role declaration and reviewer resolution (Stage 0).
- `cartopian://protocol/CONVENTIONS/planning-intent-contract` — compact intent normalization, focused clarification, confirmation, and planning-lock gate (Stages 1-2).
- `cartopian://protocol/CONVENTIONS/reviews` — review artifact rules behind the checkpoints.
- `cartopian://protocol/CONVENTIONS/specs` — spec profile selection and the software-spec authoring boundary (Stage 4).
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

If a previous closeout carried forward `STANDARDS.md`, treat it as seed context for the new planning cycle, not as locked requirements or a locked implementation plan.

---

## Stage 0 — Role And Handoff Resolution

1. Select the active project from the registry.

   - Run `cartopian discover-projects` and choose the entry by `id`/`label`; capture its `path` as the project's absolute path.
   - If the project is not yet registered but you know its absolute path, proceed with that path and register it later via `cartopian register-project`.

2. Resolve the effective configuration via the Core CLI.

   - Run `cartopian resolve-config <project-path>` to obtain the canonical schema identity, project identity, merged `roles`, `reviews`, `automation`, `work_roots`, Git facts, and attribution.
   - Use this emitted record instead of reading and merging TOML by hand.
3. Read `reviews.planning.mode` and `reviews.planning.role` from that record. When the mode is `required`, use the emitted arbitrary role name for every planning checkpoint; never infer review responsibility from a role named `reviewer` or from description prose. When the mode is `off`, skip every planning checkpoint.
4. Dispatch mechanics remain separate from policy: `planning_review` in the assigned role's resolved `auto_launch` list controls automatic versus operator-performed planning-review launch. It does not enable or assign review when policy is off.

---

## Stage 1 — Requirements Gathering

### 1.1 Check for existing requirements

Check if a `REQUIREMENTS.md` exists in the project directory.

- If it exists and is populated (including a reference stub from `adopt-requirements`), treat it as an approved input to compact-intent normalization. Reuse its facts; do not ask the operator to repeat them. Ask whether it remains in force only when its approval or applicability to this planning cycle is genuinely unclear.
- Whether requirements exist or not, continue through Stage 1.2. Existing requirements do not bypass the intent confirmation and planning-lock gate. They also do not replace the captured initiating request.

### 1.2 Resolve compact intent and engage the operator

Read `cartopian://protocol/CONVENTIONS/planning-intent-contract`. Normalize
the operator's input, populated `REQUIREMENTS.md`, approved supporting
artifacts, and applicable carried-forward standards into the six-field
Planning Intent Contract.

Apply that contract exactly: reuse present facts, distinguish missing from
conflicting fields, state one provisional working assumption per unresolved
field, and ask only its focused resolution question. Obtain operator
confirmation of the complete record before either planning lock. This is the
pre-existing check on the PM's normalization, not request-trace evidence and
not a new review stage.

After the compact record is confirmed, draw out only the additional detail
needed for this project's functional requirements, non-functional
requirements, product model, architecture principles, and explicitly deferred
decisions. Do not repeat compact-intent questions already answered.

**Adapt the remaining dialogue to fit the project.** Not every project needs every section. A documentation project doesn't need architecture principles. A CLI tool might not need non-functional requirements beyond "it runs fast." Use judgment.

**Challenge vague statements.** If the operator says "it should be fast," ask "how fast? What's the latency target?" Push for specificity, because vague requirements produce vague plans.

**Supporting documents live in `resources/`.** When the operator supplies or asks for supporting material for planning — research documents, user stories, reference papers, datasets — its durable home is the project's `resources/` directory (`cartopian://protocol/CONVENTIONS/project-resources`), never a work root. Operator-supplied files are placed there by the operator; documents produced through a research task are declared as `project:resources/<path>` deliverables and persisted with `cartopian write-resource`.

### 1.3 Produce REQUIREMENTS.md

Only after the six-field record is confirmed, author or update
`REQUIREMENTS.md` with a `Confirmed intent` section that records all six facts.
An existing populated file may be left byte-for-byte unchanged only when it
already records the confirmed compact intent. Authoring `REQUIREMENTS.md` is a
**PM-performed** write. The contained PM has no raw `Write` tool, so author it
through the mediated writer (use the structure that emerged from the
conversation, not a rigid operator-facing form):

```
cartopian write-requirements <project-root> --content-file <body-path>
```

### 1.4 Generate STANDARDS.md

Derive `STANDARDS.md` from the requirements conversation, any carried-forward standards seed, and the working practices the operator confirmed — only the durable execution standards that govern *how* this project's work is performed. Every statement must pass the admission test in `CONVENTIONS § Standards`: execution-binding, assignee-actionable, and settled. Route everything else to its owning artifact instead of writing it here: product behavior and scope stay in `REQUIREMENTS.md`; phase deliverables and exclusions belong to the implementation plan; unresolved standards choices are recorded as the plan's open questions (or a decision once ruled), never as standards. Author or update it through the mediated writer (a **PM-performed** write):

```
cartopian write-standards <project-root> --content-file <body-path>
```

### 1.5 Review checkpoint

If `reviews.planning.mode` is `required`:

1. Run planning-review checkpoint `001 requirements-and-standards` using the Review Flow Reference.
2. Target artifacts: `REQUIREMENTS.md` and `STANDARDS.md`.
3. The checkpoint verifies standards admission discipline: every `STANDARDS.md` statement passes the `CONVENTIONS § Standards` admission test, and any content owned by another artifact — restated product behavior or scope, phase deliverables or exclusions, lifecycle or PM behavior, unresolved questions — is a `request-changes` finding routed to its owning artifact.
4. If `approve`: proceed to Stage 2.
5. If `request-changes`: revise the target artifacts in place and rerun the checkpoint.
6. If `reject`, blocked, failed, or failed-to-parse: stop and return control to the operator.

---

## Stage 2 — Implementation Plan Generation

### 2.1 Read inputs

1. Read the locked `REQUIREMENTS.md` and verify that its compact intent is complete and operator-confirmed. If any field is unresolved or confirmation is absent, return to Stage 1.2; do not write or lock an implementation plan.
2. Read the current-cycle `STANDARDS.md` as the project's execution standards.
3. Read the templates in `cartopian://templates/IMPLEMENTATION_PLAN.md` for structural guidance.

### 2.2 Generate IMPLEMENTATION_PLAN.md

Authoring the plan is **PM-performed**; compose the body and write it through the mediated writer (never a raw `Write`):

```
cartopian write-plan <project-root> --content-file <body-path>
```

The `IMPLEMENTATION_PLAN.md` body must contain:

- **Purpose**: what this plan accomplishes and which source documents it derives from.
- **Architecture rules**: rules derived from requirements and project standards. These are consequences of locked inputs, not new decisions.
- **Work topology**: which repos or other work locations are involved and what each owns. Include no-repo projects when applicable.
- **Phase sequence**: each phase with:
  - Goal
  - Plan ref table (`KIND-NN-NNN` format) listing work items of any supported kind (`BUILD`, `DESIGN`, `RESEARCH`, `TEST`, `RELEASE`, `VERIFY`, `CORRECTIVE`). Within each phase, all kinds draw from one sequence starting at `001`.
  - Exit criteria
- **Requirement coverage matrix**: every requirement from `REQUIREMENTS.md` mapped to plan ref(s) and phase(s). Every requirement must appear. Deferred requirements note the reason.
- **Open questions by phase**: questions that arose during planning.
- **Exit criteria summary**: per-phase exit criteria in one place.

### 2.3 Review checkpoint

If `reviews.planning.mode` is `required`:

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

Authoring phase files is **PM-performed**. For each phase in the plan, author `phases/PHASE-NN.md` through the mediated writer `cartopian write-phase` (the `--phase-id` resolves the allowlisted `phases/` destination, so the PM supplies the id, not a path):

```
cartopian write-phase <project-root> --phase-id PHASE-NN --content-file <body-path>
```

Each phase body contains:

- **Phase goal**: one or two sentences.
- **Plan refs covered**: list from the plan's phase table.
- **Build items**: delivery/execution tasks that produce outcomes or artifacts (`BUILD` is a compatibility identifier, not a software-only type).
- **Research items**: tasks that produce knowledge or decisions.
- **Exit criteria**: copied from the plan.
- **Dependencies on prior phases**: what must be done before this phase can start.

Use the phase number from the plan. The two-digit phase number (`NN`) must match the plan section number; keep the description in the heading, not the filename.

### 3.3 Review checkpoint

If `reviews.planning.mode` is `required`:

1. Run planning-review checkpoint `003 phases` using the Review Flow Reference.
2. Target artifacts: `phases/PHASE-*.md`.
3. If `approve`: proceed to Stage 4.
4. If `request-changes`: revise phase files in place and rerun the checkpoint.
5. If `reject`, blocked, failed, or failed-to-parse: stop and return control to the operator.

---

## Stage 4 — Task and Spec Generation

### 4.1 Determine scope

Generate tasks for the **current active phase** (or Phase 00 / Phase 01 if starting fresh). Do not generate tasks for all phases at once or preload future-phase task detail — later phases may change as earlier work completes.

### 4.2 Generate task files

Authoring task files is **PM-performed**. For each build and research item in the active phase, author `tasks/open/TASK-NN-NNN.md` through the mediated writer `cartopian write-task`, following the template in `cartopian://templates/TASK.md`. Read that template from the MCP resource — Cartopian templates are served by the MCP server at `cartopian://templates/<NAME>.md` — the upper-case template name **with the `.md` extension** (e.g. `cartopian://templates/TASK.md`, `cartopian://templates/REPORT.md`, `cartopian://templates/SPEC.md`) — not files on your filesystem. Always include the `.md`. Do **not** open `templates/...` as a path and do **not** infer the format by reading an existing task or report; read the template resource and follow it.

```
cartopian write-task <project-root> --task-id TASK-NN-NNN --content-file <body-path>
```

The plan ref has already allocated the task identity: author `KIND-NN-NNN` as `TASK-NN-NNN`. Preserve that suffix unchanged for the task's optional spec, prompt, reports, and review. Never restart numbering for a new kind or map a plan ref to a differently numbered task.

New tasks land in `tasks/open/` (the lifecycle entry point); `move-task` advances them from there. Populate the body from the plan ref, phase file, resolved roles, repo subpath, dependencies, evidence gate, and checkable acceptance criteria.

Classify source authority explicitly for every new task. Use `Source guidance: task` when the task owns the record, `Source guidance: spec` when its named spec owns the one record, and `Source guidance: n/a` only when authoritative sources are not material to the outcome. For source-backed work, name stable sources, their applicable dates/versions and scopes, the conflict rule or decision authority, and every explicitly unverified claim. Do not create duplicate task and spec records.

### 4.3 Generate spec files

For tasks that need specs (new interfaces, schemas, contracts), author `specs/SPEC-NN-NNN.md` through the mediated writer `cartopian write-spec`, following the template in `cartopian://templates/SPEC.md` (a **PM-performed** write):

```
cartopian write-spec <project-root> --spec-id SPEC-NN-NNN --content-file <body-path>
```

Use the owning task's exact `NN-NNN`. The task must declare that spec before it is authored. Do not create or reuse a differently numbered phase-wide umbrella spec.

Not every task needs a spec. Use judgment: configuration tasks, documentation tasks, and simple implementation tasks typically do not need specs.

Before authoring each spec, classify the outcome governed by that spec and set its `Profile`:

- Use `software` when the end outcome is executable software or a technical contract intended for software implementation, including an application, service, library, CLI, automation script, or implementable schema, API, or integration.
- Use `general` for a genuinely non-software work contract such as a research report, operating procedure, launch activity, or creative asset. Classify the spec itself rather than the overall project: one project may legitimately contain both profiles.

For `software`, keep the template's software profile and remove the general profile. The spec is the task-scoped SRS and TDS: cover **Overview & Goals**, **Functional Requirements**, **Non-Functional Requirements**, **User Stories & Use Cases**, **Architecture & Structure**, **Data Models**, **APIs & Integrations**, and **Edge Cases & Error Handling**. Describe required behavior, design boundaries, constraints, and acceptance conditions, while leaving source-level implementation decisions to the assignee. Do not write source/executable code, pseudocode, step-by-step algorithms, function or class bodies, complete configuration or build files, or copy/paste-ready implementation snippets. Contract notation such as diagrams, tables, field/type definitions, endpoint signatures, protocol grammar, and concise example payloads or input/output values is allowed.

For `general`, keep the template's general profile and remove the software profile. Do not select `general` merely to put implementation content into a software spec. Before writing either profile, remove the unused profile and all template instructional text.

Every authored spec also chooses `Source guidance: required | n/a`. A required spec record uses the same domain-neutral shape as a task-owned record and is the owner only for tasks that declare `Source guidance: spec`. Missing decisive authority, stale context, unresolved conflicts, or decisive unverified claims block spec writing and planning review.

### 4.4 Review checkpoint

If `reviews.planning.mode` is `required`:

1. Run planning-review checkpoint `004 tasks-and-specs` using the Review Flow Reference.
2. Target artifacts: generated files in `tasks/open/` and `specs/`. In the checkpoint prompt, require the reviewer to verify every spec's profile classification. For each software-profile spec, require all eight SRS/TDS areas and treat source code, executable code, pseudocode, step-by-step algorithms, function/class bodies, complete configuration/build files, or copy/paste-ready implementation as a blocking finding requiring changes.
3. If `approve`: proceed to Stage 5.
4. If `request-changes`: revise tasks and specs in place and rerun the checkpoint.
5. If `reject`, blocked, failed, or failed-to-parse: stop and return control to the operator.

---

## Stage 5 — State Initialization

### 5.1 Update STATE.md

Updating `STATE.md` is **PM-performed** through the mediated writer (never a raw `Write`). The plan artifacts written in earlier stages exist now, so the writer composes the canonical body from the filesystem itself — do not author a body or pass `--content`:

```
cartopian write-state <project-root>
```

The composed body reflects the first active phase, no active work (nothing assigned yet), all generated tasks as open work, and the first ready task as what to do next. Do not create a prompt during planning unless assignment is happening immediately; prompts belong in `prompts/` and are temporary handoff artifacts.

### 5.2 Final summary

Print a summary of everything that was produced:

- Number of requirements captured
- Number of phases generated
- Number of tasks and specs generated
- Review status (required and completed, or policy off, with any findings)
- Resolved handoff configuration (which roles have configured agents)
- Resolved automation policy
- Suggested first action, including whether to create `prompts/PROMPT-NN-NNN.md` for the first assignment

Planning — including task generation — is a **scoped directive** (`cartopian://protocol/CONVENTIONS/request-intent`): generating tasks fills the open queue but does not authorize running it. Under `initiation = "operator"` (the default), end here with the summary; execution starts when the operator gives an execution directive ("continue", "run the next task"). Under `initiation = "auto"`, the newly ready queue may initiate execution via `run task`.

---

## Review Flow Reference

Planning-checkpoint reviews use `REVIEW-PLAN-NNN.md` in `reviews/` (authored by the role named by `reviews.planning.role`). The PM authors a matching `PROMPT-PLAN-NNN.md` in `prompts/` through the mediated writer — `cartopian write-prompt <project-root> --prompt-id PROMPT-PLAN-NNN --content-file <body-path> --review-kind planning --checkpoint PLAN-NNN` plus the applicable `--phase` / `--plan-ref` — to hand off the review work; the contained PM has no raw `Write`. The writer resolves the independent intake request channel and generates its bound prompt section. `NNN` is a per-project sequential counter independent of task-scoped numbering — no tasks exist at the point of requirements generation.

The standard checkpoint sequence is:

| NNN | Stage | Prompt | Report | Review |
| --- | --- | --- | --- | --- |
| 001 | Requirements & Standards | `PROMPT-PLAN-001.md` | `REPORT-PLAN-001.md` | `REVIEW-PLAN-001.md` |
| 002 | Implementation Plan | `PROMPT-PLAN-002.md` | `REPORT-PLAN-002.md` | `REVIEW-PLAN-002.md` |
| 003 | Phases | `PROMPT-PLAN-003.md` | `REPORT-PLAN-003.md` | `REVIEW-PLAN-003.md` |
| 004 | Tasks & Specs | `PROMPT-PLAN-004.md` | `REPORT-PLAN-004.md` | `REVIEW-PLAN-004.md` |

At every review checkpoint:

1. Author the checkpoint prompt at the table's prompt path via `cartopian write-prompt` (see the note above), resolved to an absolute project path. Open the prose with a role preface (`## Your role`) addressed to the reviewer, sourced from the resolved `[roles.<role>]` record's description for the role named by `reviews.planning.role` — orientation only; it grants no authority beyond the role's configured grants. Include absolute paths to the target artifacts, the expected review file, the expected report file, and `cartopian://templates/REPORT.md`. Never hand-author the generated request-comparison sections. Validate the finished artifact with `cartopian review-context <project-root> --review-kind planning --checkpoint PLAN-NNN --prompt <absolute-prompt-path>` before manual handoff; automatic dispatch performs the identical preflight.
2. Call `skills/run-handoff.md` with:
   - Role: the exact resolved `reviews.planning.role` value
   - Absolute prompt path: `<project>/prompts/PROMPT-PLAN-NNN.md`
   - Absolute report path: `<project>/reports/REPORT-PLAN-NNN.md`
   - Expected report variant: planning-review completion
   - Allowed lifecycle action: return outcome to this skill
3. Require the configured reviewer to create `reviews/REVIEW-PLAN-NNN.md` using `cartopian://templates/REVIEW.md`. The review file and planning-review completion report record `Request alignment:` and `Request evidence:`. Drift or missing/mismatched evidence blocks approval; only generated `unavailable-for-legacy` is non-blocking.
4. Apply the returned verdict in the stage-specific checkpoint section.

Completion detection at every checkpoint uses the lower-level wait primitive on the checkpoint report path rather than a hand-rolled timing loop or a manual "tell me when the review is done" prompt:

```
cartopian wait-report <project>/reports/REPORT-PLAN-NNN.md --role <role>
```

`cartopian wait-report` is a read-only observer: the report file is the authoritative completion signal. It is terminal by default — one call blocks until the report lands or the resolved role launch timeout elapses (`timeout`). It emits `accepted` when the planning-review report is present and parses, a `[guard]` failure when a report is present but not acceptable, `timeout` when the ceiling elapses first, or — only under an explicitly requested `--max-block` observation slice, which exists solely to fit a host `tools/call` ceiling that cannot be raised — `still_running` when that budget elapses before the report lands. Treat `still-running` / `still_running` as a nonterminal internal observation boundary. Routine nonterminal slices are silent and context-neutral: keep the initiated run active and re-invoke the same canonical wait primitive in another bounded slice without user-facing text or repeated state when no material state changed. User-facing output is allowed only for a terminal result, blocker, timeout/failure, meaningful new progress evidence, or a deliberately throttled long-running threshold. The re-wait is read-only, does not launch a second reviewer, and does not consume a `max_handoffs_per_run` unit; only the original launch does. Do not ask for operator continuation between slices. When the checkpoint is dispatched through `skills/run-handoff.md`, that skill owns this wait step under the same contract.

`skills/run-handoff.md` owns stale report deletion, manual versus CLI handoff behavior, timeout enforcement, completion waiting via `cartopian wait-handoff` / `cartopian wait-report`, report parsing, and sequential automation boundaries.

Planning-checkpoint prompts and reviews are temporary artifacts. When a planning stage is approved or superseded, clear its prompt and report artifacts using the Core CLI and keep the review as the durable record:

- Remove the checkpoint prompt:

  ```
  cartopian delete-prompt <project-path>/prompts/PROMPT-PLAN-NNN.md
  ```

- Remove the checkpoint report (if present):

  ```
  cartopian delete-report <project-path>/reports/REPORT-PLAN-NNN.md
  ```

No archival for prompts or reports.

This creates a quality gate at every level of the hierarchy while keeping the operator in control of the pace.

## Handoff Automation Reference

This skill supports CLI handoff automation for review checkpoints by delegating handoff mechanics to `skills/run-handoff.md`.
