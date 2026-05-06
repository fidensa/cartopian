# Skill: Close Plan

Close a completed Cartopian implementation plan, optionally archive the
completed plan artifacts, reset the project governance surface, and
prepare the project for a fresh planning cycle.

This workflow is the boundary between one active plan and the next. It
does not generate the new plan. After closeout, run `skills/plan-project.md`
to gather fresh requirements and produce the next plan.

**Output:** A reset project directory ready for `plan project`, plus an
optional `archive/PLAN-NNN-slug/` snapshot when the operator requests
one.

---

## Prerequisites

- The project directory exists with the correct Cartopian structure.
- The project has an active `IMPLEMENTATION_PLAN.md`.
- The current plan is complete: no tasks remain in `tasks/open/`,
  `tasks/in-progress/`, or `tasks/in-review/`.

---

## Stage 0 - Role And Safety Check

1. Read the project's `cartopian.toml` and the workspace
   `cartopian.toml`.
2. Read `STATE.md`.
3. Confirm the operator wants to close the current plan, not revise it.
4. Explain that `REQUIREMENTS.md`, `IMPLEMENTATION_PLAN.md`, phases,
   tasks, specs, reviews, prompts, reports, and decisions will be
   removed from the live project surface during reset.
5. Explain that `cartopian.toml` remains live across the reset. The
   optional `archive/PLAN-NNN-slug/` directory (if the operator chose
   to archive at Stage 3) also remains and holds the closed plan's
   snapshot, including its `decisions/`.

Do not proceed unless the operator explicitly confirms plan closeout.

---

## Stage 1 - Completion Audit

### 1.1 Check active task directories

Inspect:

- `tasks/open/`
- `tasks/in-progress/`
- `tasks/in-review/`

If any task files exist in these directories, stop. The plan is not
closable until each active task reaches `tasks/done/` or the operator
records an explicit decision that the work will not close in this plan
and removes it from the active task directories before rerunning
closeout.

### 1.2 Check completed task directory

Inspect `tasks/done/` and compare completed task identifiers to the
current phase files and `IMPLEMENTATION_PLAN.md`.

- Confirm that generated tasks are in `done/`.
- Note any plan refs that were intentionally not taskified.
- Surface any mismatch to the operator before continuing.

### 1.3 Check prompts

Inspect `prompts/`.

Prompt files are temporary. If any `PROMPT-*.md` files remain, resolve
them before closeout:

- Delete prompts for tasks already in `done/`.
- Delete superseded planning-checkpoint prompts.
- Stop if a prompt points to work that is still active or ambiguous.

### 1.4 Check reports

Inspect `reports/`.

Report files are handoff result artifacts. Resolve any remaining reports
before closeout:

- Treat unresolved prompts or missing/ambiguous reports as active
  handoff state.
- Stop closeout if any handoff result is missing, malformed, incomplete,
  ambiguous, failed to parse, or otherwise unresolved.
- Reports that have been processed and whose corresponding tasks are in
  `done/` may be cleared during reset.

### 1.5 Check phase exit criteria

Read each `phases/PHASE-NN-slug.md` file and confirm the exit criteria
are satisfied by completed tasks, decisions, specs, or documented
operator acceptance.

If exit criteria are not satisfied, stop and name the missing evidence.

---

## Stage 2 - Operator Choices

Ask the operator three closeout questions:

1. **Archive:** "Do you want to archive the completed plan before reset?"
2. **Engineering:** "Should `ENGINEERING.md` carry forward as the seed
   for the next plan, or reset to a blank project engineering file?"
3. **Conventions:** "Should `CONVENTIONS.md` carry forward as the seed
   for the next plan, or reset to the default project conventions file?"

Defaults:

- Archive: no.
- Engineering: carry forward only if the operator says so.
- Conventions: carry forward only if the operator says so.

Requirements and implementation plans never carry forward as live
artifacts. The next planning cycle must produce fresh `REQUIREMENTS.md`
and `IMPLEMENTATION_PLAN.md`.

---

## Stage 3 - Optional Archive

Skip this stage unless the operator requested an archive.

### 3.1 Choose archive path

Create `archive/` if it does not exist.

Choose the next available plan archive directory:

```text
archive/PLAN-NNN-slug/
```

- `NNN` is a three-digit counter, starting at `001`.
- Use the next number after existing `archive/PLAN-*` directories.
- `slug` is a short kebab-case name derived from the completed plan
  title or project outcome.

### 3.2 Write closeout summary

Create `archive/PLAN-NNN-slug/CLOSEOUT.md` using
`templates/PLAN_CLOSEOUT.md` as the starting structure.

The closeout summary records:

- Plan identity and completion date.
- Whether this was a full completion or another operator-approved
  closeout after all active work was resolved.
- Archive contents.
- Carry-forward choices for `ENGINEERING.md` and `CONVENTIONS.md`.
- Any plan refs or work intentionally not carried forward.
- Suggested seed context for the next requirements session.

### 3.3 Copy archive artifacts

Copy these live artifacts into the archive directory when they exist:

- `REQUIREMENTS.md`
- `ENGINEERING.md`
- `CONVENTIONS.md`
- `IMPLEMENTATION_PLAN.md`
- `STATE.md`
- `phases/`
- `tasks/`
- `specs/`
- `reviews/`
- `reports/`
- `decisions/`

Do not archive `prompts/`. Prompts are temporary handoff artifacts and
must not become a durable archive.

---

## Stage 4 - Reset Live Project Surface

Reset the live project surface after optional archival is complete.

### 4.1 Always reset

Remove these live artifacts:

- `REQUIREMENTS.md`
- `IMPLEMENTATION_PLAN.md`
- all files in `phases/`
- all files in `tasks/open/`
- all files in `tasks/in-progress/`
- all files in `tasks/in-review/`
- all files in `tasks/done/`
- all files in `specs/`
- all files in `reviews/`
- all files in `decisions/`
- all files in `prompts/`
- all files in `reports/`

Recreate the directories if needed:

```text
phases/
prompts/
reports/
tasks/open/
tasks/in-progress/
tasks/in-review/
tasks/done/
specs/
reviews/
decisions/
```

Reports should not become a replacement for task, review, or decision
records. They are cleared during reset along with other plan artifacts.

### 4.2 Conditionally reset `ENGINEERING.md`

If the operator chose to carry forward engineering standards, leave
`ENGINEERING.md` in place and treat it as seed context for the next
planning cycle.

If the operator chose not to carry it forward, replace
`ENGINEERING.md` with a fresh project engineering seed based on
`templates/ENGINEERING.md`.

### 4.3 Conditionally reset `CONVENTIONS.md`

If the operator chose to carry forward conventions, leave
`CONVENTIONS.md` in place and treat it as seed context for the next
planning cycle.

If the operator chose not to carry them forward, replace
`CONVENTIONS.md` with the default project conventions seed:

```markdown
# <project name> - Conventions

This document extends the protocol-level conventions defined in
`protocol/CONVENTIONS.md`. Rules here apply only to this project.

## Project-specific conventions

<!-- Add project-specific naming rules, workflow modifications, or
     constraints here. Delete this comment when you add real content. -->
```

### 4.4 Preserve live project memory

Do not reset:

- `cartopian.toml`
- `archive/`

---

## Stage 5 - State Reset

Rewrite `STATE.md` so it is under 5KB and says:

- There is no active plan.
- The previous plan has been closed.
- Whether an archive was created, and where.
- Whether `ENGINEERING.md` and `CONVENTIONS.md` were carried forward.
- The next action is to run `skills/plan-project.md` to gather fresh
  requirements and generate the next implementation plan.

Use this structure:

```markdown
# <project name> - State

## Current phase

No active plan. The previous implementation plan was closed on
<YYYY-MM-DD>.

## Active work

None.

## Open work

None.

## Closeout notes

- Archive: <none | archive/PLAN-NNN-slug/>
- Engineering carry-forward: <yes | no>
- Conventions carry-forward: <yes | no>

## What to do next

Run `skills/plan-project.md` to gather fresh requirements and generate
the next implementation plan.
```

---

## Stage 6 - Final Summary

Print a concise closeout summary:

- Whether the plan was archived.
- What was reset (including reports/).
- Whether engineering standards carried forward.
- Whether project conventions carried forward.
- Any unresolved decision follow-up.
- The exact next action: run `skills/plan-project.md`.
