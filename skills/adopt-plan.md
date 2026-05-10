# Skill: Adopt Plan

Convert an existing implementation plan from any external format into
Cartopian's `IMPLEMENTATION_PLAN.md`, phase files, and task files. Use
this when a plan already exists — as a JIRA epic with subtasks, a
Confluence document, a design document, a slide deck, or any structured
text — and you need to bring it into the Cartopian system.

This skill does not require a `REQUIREMENTS.md` or a requirements-gathering
conversation. Requirements may be referenced externally, summarized as a
stub, or adopted inline. Start here when the plan is the thing you have.

**Output:** `IMPLEMENTATION_PLAN.md`, phase files, task files for the first
active phase, and an updated `STATE.md`.

---

## Prerequisites

- The project directory exists (run `init-project` first if needed).
- A project-level `cartopian.toml` exists with `[project]` configured.

---

## Preflight — Active Plan Check

Before proceeding, check whether the project already has a live plan:

1. Read `STATE.md`.
2. Check for `IMPLEMENTATION_PLAN.md`.
3. Check whether `phases/`, `tasks/`, `specs/`, or `reviews/` contain
   current plan artifacts.

If `IMPLEMENTATION_PLAN.md` exists and the operator wants to replace it,
stop and run `close-plan` first. Do not overwrite an active plan.

If `IMPLEMENTATION_PLAN.md` exists and the operator wants to revise it,
proceed only as an in-place revision. Make the revision path explicit to
the operator before editing.

If inconsistent state exists (plan artifacts without a plan file, or
`STATE.md` reports an active plan but `IMPLEMENTATION_PLAN.md` is absent),
stop and ask the operator to resolve the state before proceeding.

---

## Stage 0 — Role and Handoff Resolution

1. Read the project's `cartopian.toml` and the workspace `cartopian.toml`.
2. Resolve the effective `[roles]` table (project overrides
   workspace). Each value is a one-line description string; a
   role exists in this project iff its key appears in `[roles]`.
3. Resolve the effective handoff target for each role: check
   project `[handoffs.*]`, then fall back to workspace
   `[handoffs.*]`. A role with a `[handoffs.<role>]` block
   dispatches automatically; a role without one dispatches
   manually.
4. Resolve the effective automation policy: check project `[automation]`,
   then workspace `[automation]`, then protocol defaults
   (`confirmation = "each-handoff"`, `max_handoffs_per_run = 1`).
5. Check whether a reviewer is configured: `reviewer` appears as
   a key in the resolved `[roles]` table. If not, ask the
   operator:
   > "No reviewer is configured. Do you want to designate a reviewer for
   > this session? If not, we'll proceed without review checkpoints."

---

## Stage 1 — Requirements Source

Determine how requirements are handled for this project:

1. Check whether `REQUIREMENTS.md` exists and is populated.
   - If yes: use it as the requirements source. Proceed to Stage 2.

2. If no `REQUIREMENTS.md` exists, ask the operator:
   > "No REQUIREMENTS.md found. How should requirements be handled?
   >
   > (a) **Adopt requirements now** — paste or describe them and I'll
   >     create REQUIREMENTS.md before generating the plan.
   > (b) **Reference stub** — provide a source reference (JIRA epic,
   >     Confluence URL, PRD name) and I'll create a stub REQUIREMENTS.md
   >     that points to the external source.
   > (c) **Skip** — requirements are tracked externally; the plan will note
   >     the external source and there will be no local REQUIREMENTS.md."

   - Option (a): Run the `adopt-requirements` steps inline (Steps 1–4 of
     that skill) before continuing to Stage 2.
   - Option (b): Create a reference stub per the stub format in
     `adopt-requirements` Step 3. Proceed to Stage 2.
   - Option (c): Proceed to Stage 2. Record the external requirements source
     in the plan's Purpose section. No coverage matrix is required — use a
     section-level reference instead.

---

## Stage 2 — Collect the External Plan

Ask the operator to provide the external plan:

> "Please share the implementation plan to migrate. This can be:
> - Pasted text from a JIRA story or epic with subtasks
> - A Confluence document excerpt
> - A structured list of phases and tasks
> - Any other format — I'll interpret it"

Accept whatever format the operator provides.

Before generating any files, confirm your interpretation:
> "Here is how I'm reading your plan: <N> phases, starting with
> '<Phase 1 name>'. Does this match your intent, or should I adjust
> anything before I generate the files?"

Proceed only after operator confirmation.

---

## Stage 3 — Generate IMPLEMENTATION_PLAN.md

Write `IMPLEMENTATION_PLAN.md` in the project directory following the
structure in `templates/IMPLEMENTATION_PLAN.md`.

**Purpose section:** Name the source of the plan and the requirements
reference. If requirements are external, identify the external source
(e.g., "JIRA story HUB-123" or "Confluence 'Hub UI Design' page"). Include
approximate date or version if known.

**Architecture rules:** Derive from any constraints stated in the external
plan or the requirements source. Note the origin of each rule.

**Repo topology:** Identify which repos are involved. Populate `Repo subpath:`
values for use in task files.

**Phase sequence:** Map each phase from the external plan to a
`PHASE-NN-slug` entry. Assign two-digit phase numbers starting from `01`
(use `00` only for a bootstrap phase with no deliverable output).

Within each phase, assign `PNN-KIND-NNN` plan refs. Map subtasks to plan
refs where applicable. Use `BUILD` for items that produce code or artifacts;
`RESEARCH` for items that produce knowledge, decisions, or designs.

**Requirement coverage:** 
- If `REQUIREMENTS.md` exists with numbered requirements, generate the
  full coverage matrix.
- If requirements are a stub with numbered key requirements, map those to
  plan refs.
- If requirements are skipped (option (c)), add a section:

  > **Requirements source:** <external source>. Coverage is maintained
  > in the external system; this plan implements the scope described there.

**Open questions:** Capture gaps or ambiguities in the external plan as
open questions, grouped by phase.

**Exit criteria:** Derive from the external plan's definition of done. If
none are stated, propose criteria and confirm with the operator before
writing them.

If a reviewer is configured, run review checkpoint
`002 implementation-plan` per the Review Flow Reference.

---

## Stage 4 — Generate Phase Files

For each phase in `IMPLEMENTATION_PLAN.md`, create
`phases/PHASE-NN-slug.md` with:

- **Phase goal:** one or two sentences
- **Plan refs covered:** list from the plan's phase table
- **Build items:** tasks that produce code or artifacts
- **Research items:** tasks that produce knowledge or decisions
- **Exit criteria:** copied from the plan
- **Dependencies on prior phases:** what must be complete before this
  phase starts

The two-digit phase number (`NN`) must match the plan section number.

If a reviewer is configured, run review checkpoint `003 phases`.

---

## Stage 5 — Generate Task Files for Active Phase

Generate task files only for the **first active phase** (lowest-numbered
phase with open work). Do not generate tasks for future phases — later
phases may change as earlier work completes.

For each build and research item in the active phase, create
`tasks/open/TASK-NN-NNN-slug.md` following the template in
`templates/TASK.md`. Populate all fields:
- `Phase:` from the phase file
- `Plan ref:` from the `PNN-KIND-NNN` table
- `Repo subpath:` from the plan's repo topology (or `n/a` if not applicable)
- `Assignee:` from the resolved role configuration
- `Spec:` link if a spec is being created; otherwise `none`
- `Depends on:` / `Blocked by:` from the external plan's dependency information
- `Evidence gate:` use judgment — `required` for code-producing tasks; `n/a`
  for research, documentation, or configuration tasks

For tasks that need specs (new interfaces, schemas, contracts), create
`specs/SPEC-NN-NNN-slug.md` following the template in `templates/SPEC.md`.

If a reviewer is configured, run review checkpoint `004 tasks-and-specs`.

---

## Stage 6 — Update STATE.md

Update `STATE.md` to reflect:

- **Current phase:** the first active phase
- **Active work:** none yet (nothing assigned)
- **Open work:** all generated tasks with brief descriptions
- **What to do next:** suggest the first task to assign, or instruct the
  operator to review the plan and begin assignment. Do not create a prompt
  during plan adoption unless assignment is happening immediately; prompts
  belong in `prompts/` and are temporary handoff artifacts.

---

## Stage 7 — Summary

Report to the operator:
- External plan source and how it was interpreted
- Requirements handling (local, stub, or external reference)
- Number of phases generated
- Number of tasks and specs generated for the active phase
- Review status (reviewed, skipped, or no reviewer configured)
- Resolved handoff configuration
- Suggested first action, including whether to create
  `prompts/PROMPT-NN-NNN.md` for the first assignment

---

## Review Flow Reference

Uses the same checkpoint sequence as `plan-project`. See the Review Flow
Reference table in `plan-project.md` for prompt/report/review file paths
and `run-handoff.md` for handoff mechanics.

Checkpoint `001 requirements-and-engineering` is only run if
`REQUIREMENTS.md` was generated during Stage 1 of this skill (option (a)).
If requirements were skipped or created as a stub, proceed directly to
the implementation-plan checkpoint (`002`).
