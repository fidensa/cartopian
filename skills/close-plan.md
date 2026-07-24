# Skill: Close Plan

Close a completed Cartopian implementation plan, optionally archive the completed plan artifacts, reset the project governance surface, and prepare the project for a fresh planning cycle.

This workflow is the boundary between one active plan and the next. It does not generate the new plan. After closeout, run `skills/plan-project.md` to gather fresh requirements and produce the next plan.

**Output:** A reset project directory ready for `plan project`, plus an optional `archive/PLAN-NNN-slug/` snapshot when the operator requests one.

**Protocol reference:** This skill does not require the whole protocol document. When a stage needs protocol rules beyond what is written here, read only the relevant section via the section-scoped resource surface:

- `cartopian://protocol/CONVENTIONS/plan-lifecycle` — plan completion and closeout contract (Stages 0-1).
- `cartopian://protocol/CONVENTIONS/plan-archives` — archive naming and contents (Stage 3).
- `cartopian://protocol/CONVENTIONS/session-state` — post-closeout `STATE.md` rules (Stage 5).
- `cartopian://protocol/CONVENTIONS/git` — session-close git behavior, when versioning is enabled.

The full `cartopian://protocol/CONVENTIONS` remains the authoritative contract; do not load it whole for this skill.

---

## Prerequisites

- The project directory exists with the correct Cartopian structure.
- The project has an active `IMPLEMENTATION_PLAN.md`.
- The current plan is complete: no tasks remain in `tasks/open/`, `tasks/in-progress/`, or `tasks/in-review/`.

---

## Stage 0 - Role And Safety Check

1. Read the project's `cartopian.toml` and the workspace `cartopian.toml`.
2. Read `STATE.md`.
3. Confirm the operator wants to close the current plan, not revise it.
4. Explain that `REQUIREMENTS.md`, `IMPLEMENTATION_PLAN.md`, phases, tasks, specs, reviews, prompts, reports, and decisions will be removed from the live project surface during reset.
5. Explain that `cartopian.toml` remains live across the reset. The optional `archive/PLAN-NNN-slug/` directory (if the operator chose to archive at Stage 3) also remains and holds the closed plan's snapshot, including its `decisions/`.

Do not proceed unless the operator explicitly confirms plan closeout.

---

## Stage 1 - Completion Audit

### 1.0 Run plan audit

Before inspecting individual directories, run a full lifecycle and provenance audit using the Core CLI:

```
cartopian plan-audit <project-path>
```

If the audit exits non-zero, stop closeout and surface each blocker to the operator. Blockers must be resolved before the close can proceed:

- **Missing artifact chain**: a task in `tasks/in-progress/` or `tasks/in-review/` has no matching prompt or review artifact. Move the task through the proper lifecycle stages or obtain an operator decision to remove it from the active directories before rerunning closeout.

Surface any audit warnings before continuing. `unattributed-work-root-changes` only fires when the effective `git.pm_owns_product_branches = true` and a configured work root has uncommitted changes that cannot be linked to an active prompt chain. It is informational and should inform operator judgment during closeout, but it does not by itself block the close.

The audit also emits `work-root-attribution` entries (under `attributions`) when `git.pm_owns_product_branches = false` and a work root is dirty. These are informational records that name the most-recently-modified task and assignee for that work root. They never block closeout; do not treat them as a reason to pause.

### 1.1 Run close-audit

After the plan-audit clears, run the closeout-readiness aggregator using the Core CLI:

```
cartopian close-audit <project-path>
```

`cartopian close-audit` folds the per-directory checks (active tasks in `tasks/open/`, `tasks/in-progress/`, `tasks/in-review/`; completed tasks in `tasks/done/`; stale prompts; unresolved reports; phase exit criteria) into a single structured record. Consume its output as follows:

- **`blocking_reasons` (closeout-blocking):** if this list is non-empty, stop closeout. Surface each entry to the operator and resolve it before re-running close-audit. The aggregator also populates the structured fields that name the offending artifacts:
  - `open_tasks` — tasks remaining in `tasks/open/`, `tasks/in-progress/`, or `tasks/in-review/`. Each active task must reach `tasks/done/` or be removed under an explicit operator decision before rerunning closeout.
  - `stale_prompts` — `PROMPT-*.md` files whose tasks are already in `done/` or otherwise no longer active. Resolve each named prompt with the Core CLI before rerunning closeout:

    ```
    cartopian delete-prompt <project-path>/prompts/PROMPT-NN-NNN-<slug>.md
    ```

    Superseded planning-checkpoint prompts are cleared the same way:

    ```
    cartopian delete-prompt <project-path>/prompts/PROMPT-PLAN-NNN-<slug>.md
    ```

    Do not delete a prompt whose work is still active or ambiguous; obtain an operator decision first.
  - `unresolved_reports` — `REPORT-*.md` files whose tasks are not in `done/` while their prompts still exist (i.e. handoff state is still open). Treat these as active handoff state. Reports that have been processed and whose corresponding tasks are in `done/` may instead be cleared via the Core CLI during Stage 4 reset:

    ```
    cartopian delete-report <project-path>/reports/REPORT-NN-NNN-<slug>.md
    ```

  - `unmet_exit_criteria` — phase exit criteria from `phases/PHASE-NN-slug.md` files whose referenced tasks, decisions, specs, reviews, or reports are not yet present. Surface the named criteria to the operator and supply the missing evidence (or obtain an operator decision documenting why a criterion was intentionally not taskified) before rerunning closeout.

- **`closable`:** the aggregator's verdict. When `blocking_reasons` is empty, `closable` is `true` and closeout may proceed to Stage 2.

- **Informational counts (`open_count`, `in_progress_count`, `in_review_count`):** surface to the operator alongside any non-blocking observations. These do not by themselves block closeout when their corresponding `blocking_reasons` entries are absent.

The `cartopian delete-prompt` and `cartopian delete-report` commands remain operator-driven remediation actions: they are invoked in response to specific `stale_prompts` or `unresolved_reports` entries that name the files to remove, not as a blanket sweep.

Also compare the task identifiers reported under `tasks/done/` to the current phase files and `IMPLEMENTATION_PLAN.md`:

- Confirm that generated tasks are in `done/`.
- Note any plan refs that were intentionally not taskified.
- Surface any mismatch to the operator before continuing.

---

## Stage 2 - Operator Choices

Ask the operator three closeout questions:

1. **Archive:** "Do you want to archive the completed plan before reset?"
2. **Standards:** "Should `STANDARDS.md` carry forward as the seed for the next plan, or reset to a blank project standards file?"
3. **Resources:** "The project's supporting artifacts in `resources/` carry forward to the next plan by default (an archive also snapshots them). Keep them all, or name any the operator wants pruned?"

Defaults:

- Archive: no.
- Standards: carry forward only if the operator says so.
- Resources: carry forward. `reset-plan` never clears `resources/`; when the operator asks for an archive, `archive-plan` snapshots `resources/` along with the plan artifacts. Pruning is **operator-performed** (no mediated command deletes resources) and happens only for files the operator explicitly names.

Requirements and implementation plans never carry forward as live artifacts. The next planning cycle must produce fresh `REQUIREMENTS.md` and `IMPLEMENTATION_PLAN.md`.

---

## Stage 3 - Optional Archive

Skip this stage unless the operator requested an archive.

Archival is **PM-performed**. Route the complete snapshot operation through the bounded `cartopian archive-plan` command; do not hand raw create/copy/index steps to the operator. The command owns archive numbering, the fixed source allowlist, directory creation, `CLOSEOUT.md`, and `archive/INDEX.md`, and refuses symlinked or non-regular source trees before copying.

### 3.1 Choose archive path

Choose the next available plan archive directory:

```text
archive/PLAN-NNN-slug/
```

- `NNN` is a three-digit counter, starting at `001`.
- `cartopian archive-plan` allocates the next number after existing `archive/PLAN-*` directories.
- `slug` is a short kebab-case name derived from the completed plan title or project outcome.

### 3.2 Write closeout summary

Compose the `CLOSEOUT.md` body from `cartopian://templates/PLAN_CLOSEOUT.md`. Pass it directly as the archive command's `content` value (or use `--content-file` in a shell-capable environment); a contained PM does not need a raw temporary-file write.

The closeout summary records:

- Plan identity and completion date.
- Whether this was a full completion or another operator-approved closeout after all active work was resolved.
- Archive contents.
- The carry-forward choice for `STANDARDS.md`.
- The `resources/` disposition: carried forward (the default), snapshotted in this archive, and any files the operator chose to prune.
- Any plan refs or work intentionally not carried forward.
- Suggested seed context for the next requirements session.

### 3.3 Copy archive artifacts

Copy these live artifacts into the archive directory when they exist:

- `REQUIREMENTS.md`
- `STANDARDS.md`
- `IMPLEMENTATION_PLAN.md`
- `STATE.md`
- `phases/`
- `tasks/`
- `specs/`
- `reviews/`
- `reports/`
- `decisions/`
- `resources/`

Do not archive `prompts/`. Prompts are temporary handoff artifacts and must not become a durable archive. Archiving copies `resources/`; the live `resources/` directory still carries forward untouched per the Stage 2 choice.

### 3.4 Create the snapshot and update the index

Run the PM-owned archive command before any reset:

```text
cartopian archive-plan <project-root> --slug <slug> --closed <YYYY-MM-DD> --summary <brief-outcome> --content <closeout-body>
```

Consume the emitted `archive_path` as the authoritative snapshot location. The command copies the fixed live-artifact set, writes `CLOSEOUT.md`, and creates or appends the one-line entry in `archive/INDEX.md`. If it exits non-zero, stop closeout; never run the reset without the requested snapshot.

---

## Stage 4 - Reset Live Project Surface

Resetting the live surface is **PM-performed**. The contained PM has no `rm`/`mkdir`/raw-`Write` tool, so the entire reset routes through mediated commands: the close-surface reset itself through `cartopian reset-plan`, and the prompt/report files through `cartopian delete-prompt` / `cartopian delete-report` (which `reset-plan` deliberately does not touch).

### 4.1 Clear prompts and reports

Clear every remaining file in `prompts/` and `reports/` through the Core CLI — never a raw `rm`:

```text
cartopian delete-prompt <project-path>/prompts/<file>.md
cartopian delete-report <project-path>/reports/<file>.md
```

`delete-report` also clears the companion `<report-path>.status` file. Reports should not become a replacement for task, review, or decision records; they are cleared during reset along with other plan artifacts.

### 4.2 Run the mediated close-surface reset

Run `cartopian reset-plan` to fold the rest of Stage 4 into one fail-closed pass:

```
cartopian reset-plan <project-root> [--carry-standards]
```

`reset-plan` (FR-005, the OQ-003 close-surface verb):

- **Removes** the live plan artifacts — `REQUIREMENTS.md`, `IMPLEMENTATION_PLAN.md`, and every file in `phases/`, `tasks/{open,in-progress,in-review,done}/`, `specs/`, `reviews/`, `decisions/`. (It does **not** touch `prompts/` or `reports/` — those were cleared in 4.1 — and never clears `resources/`, whose contents carry forward.)
- **Recreates** the empty lifecycle directories (`phases/`, `prompts/`, `reports/`, `tasks/{open,in-progress,in-review,done}/`, `specs/`, `reviews/`, `decisions/`), and ensures `resources/` exists.
- **Conditionally reseeds** `STANDARDS.md` from the carry-forward choice in Stage 2: pass `--carry-standards` to **keep** it as seed context; omit the flag to reseed it to a fresh project seed. The reseed write goes through the same mediated-write guards as the `write-*` commands.

The command supplies only the project root — the PM never names a path to remove, create, or reseed; every target is a fixed, code-owned member of the close-surface allowlist. A symlink, foreign subdirectory, or out-of-root target aborts the whole pass with nothing removed, created, or written.

### 4.3 Preserve live project memory

`reset-plan` never touches these — they survive the reset:

- `cartopian.toml`
- `archive/`
- `resources/` (contents; the empty directory is ensured)

---

## Stage 5 - State Reset

Render the post-closeout `STATE.md` body via the Core CLI:

```
cartopian compose-state <project-path>
```

After Stage 4 the live plan surface is empty, so `cartopian compose-state` returns the no-plan record shape — `current_phase`, `active_work`, `open_work`, `what_to_do_next`, and `rendered_body` are all `null`. Because `rendered_body` is `null` in the no-plan case, treat the aggregator as the signal that the project surface is reset, then compose the closeout `STATE.md` body the PM owns (closeout date, archive note, carry-forward choices, next-action pointer) — the aggregator does not emit these.

Authoring that body is a **PM-performed** write; the contained PM has no raw `Write`, so write it through the mediated writer so it is under 5KB:

```
cartopian write-state <project-root> --content-file <closeout-body-path>
```

This no-plan case is the only one where `write-state` accepts an authored body — on a project with plan artifacts it refuses `--content` and composes the body from the filesystem itself, so an incomplete Stage 4 reset also fails closed here.

The composed body says:

- There is no active plan.
- The previous plan has been closed.
- Whether an archive was created, and where.
- Whether `STANDARDS.md` was carried forward.
- The next action is to run `skills/plan-project.md` to gather fresh requirements and generate the next implementation plan.

Use this structure:

```markdown
# <project name> - State

## Current phase

No active plan. The previous implementation plan was closed on <YYYY-MM-DD>.

## Active work

None.

## Open work

None.

## Closeout notes

- Archive: <none | archive/PLAN-NNN-slug/>
- Engineering carry-forward: <yes | no>
- Resources: <carried forward | carried forward, operator pruned <files>>

## What to do next

Run `skills/plan-project.md` to gather fresh requirements and generate the next implementation plan.
```

Confirm the no-plan `cartopian compose-state` record (all fields `null`) before writing — a non-null `current_phase`, `active_work`, `open_work`, or `rendered_body` means Stage 4 reset did not complete and closeout must not finalize the new `STATE.md`.

---

## Stage 6 - Final Summary

Print a concise closeout summary:

- Whether the plan was archived.
- What was reset (including reports/).
- Whether project standards carried forward.
- Any unresolved decision follow-up.
- The exact next action: run `skills/plan-project.md`.
