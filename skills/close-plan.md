# Skill: Close Plan

Close a completed Cartopian implementation plan, optionally archive the completed plan artifacts, reset the project governance surface, and prepare the project for a fresh planning cycle.

This workflow is the boundary between one active plan and the next. It does not generate the new plan. After closeout, run `skills/plan-project.md` to gather fresh requirements and produce the next plan.

**Output:** A reset project directory ready for `plan project`, plus whichever preservation the operator chose at Stage 2 — an optional `archive/PLAN-NNN/` snapshot, an optional project-root `CONTINUITY.md` entry, or neither.

**Protocol reference:** This skill does not require the whole protocol document. When a stage needs protocol rules beyond what is written here, read only the relevant section via the section-scoped resource surface:

- `cartopian://protocol/CONVENTIONS/plan-lifecycle` — plan completion and closeout contract (Stages 0-1).
- `cartopian://protocol/CONVENTIONS/plan-archives` — archive naming and contents, and the plan-id reservation a `ledger` closeout leaves (Stage 3).
- `cartopian://protocol/CONVENTIONS/plan-continuity` — the optional cross-plan continuity artifact (Stages 2 and 3b).
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
5. Explain that `cartopian.toml` remains live across the reset. The optional `archive/PLAN-NNN/` directory (if the operator chose to archive at Stage 3) also remains and holds the closed plan's snapshot, including its `decisions/`. A project-root `CONTINUITY.md`, if one exists, also survives the reset untouched.
6. If a root `CONTINUITY.md` already exists, say so now and say what it means: rulings an earlier closeout preserved still govern this project and still reach every session, whatever outcome the operator picks for **this** plan. Nothing in Stage 2 revokes them; removing them is an explicit, operator-performed act.

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

`cartopian close-audit` folds the per-directory checks (active tasks in `tasks/open/`, `tasks/in-progress/`, `tasks/in-review/`; completed tasks in `tasks/done/`; stale prompts; unresolved reports; phase exit criteria) and the plan's delivery gate into a single structured record. Consume its output as follows:

- **`blocking_reasons` (closeout-blocking):** if this list is non-empty, stop closeout. Surface each entry to the operator and resolve it before re-running close-audit. The aggregator also populates the structured fields that name the offending artifacts:
  - `open_tasks` — tasks remaining in `tasks/open/`, `tasks/in-progress/`, or `tasks/in-review/`. Each active task must reach `tasks/done/` or be removed under an explicit operator decision before rerunning closeout.
  - `stale_prompts` — `PROMPT-*.md` files whose tasks are already in `done/` or otherwise no longer active. Resolve each named prompt with the Core CLI before rerunning closeout:

    ```
    cartopian delete-prompt <project-path>/prompts/PROMPT-NN-NNN.md
    ```

    Superseded planning-checkpoint prompts are cleared the same way:

    ```
    cartopian delete-prompt <project-path>/prompts/PROMPT-PLAN-NNN.md
    ```

    Do not delete a prompt whose work is still active or ambiguous; obtain an operator decision first.
  - `unresolved_reports` — `REPORT-*.md` files whose tasks are not in `done/` while their prompts still exist (i.e. handoff state is still open). Treat these as active handoff state. Reports that have been processed and whose corresponding tasks are in `done/` may instead be cleared via the Core CLI during Stage 4 reset:

    ```
    cartopian delete-report <project-path>/reports/REPORT-NN-NNN.md
    cartopian delete-report <project-path>/reports/REPORT-NN-NNN-review.md
    ```

    A closed task may leave both task-scoped reports — the preserved completion report and, under required review, its independent review-completion report. Clear each existing artifact with its own call; a task closed without review simply has no review report to clear.

  - `unmet_exit_criteria` — phase exit criteria from `phases/PHASE-NN.md` files whose referenced tasks, decisions, specs, reviews, or reports are not yet present. Surface the named criteria to the operator and supply the missing evidence (or obtain an operator decision documenting why a criterion was intentionally not taskified) before rerunning closeout.

  - `delivery gate blocks closeout: <code> — <detail>` entries come from the plan's delivery contract (`cartopian://protocol/DELIVERY`). The full result is under `delivery`: the artifact, outcome, and follow-up states, and one ordered finding per unmet obligation, each carrying its own recovery. Run `cartopian validate-delivery <project-path>` for the detail view and surface each finding's recovery to the operator.

    Never resolve a delivery finding by editing the record to say the delivery happened. Artifact completion is not outcome verification: `Artifact: complete` alongside `Result: not-run` is an honest state, and it blocks. So do `Authority: pending-operator-authorization` and `Authority: declined` — an undelivered outcome stays undelivered, and the gate refuses to convert either into `verified`. There are exactly two honest ways past such a finding, and both are the operator's:

    - The external action happens. The operator authorizes it, the target is observed, and the record gains that observation, its evidence identity, and its time. Rerun the gate.
    - The plan's delivery obligation changes. The operator decides the plan no longer carries this delivery — record that as a decision (`cartopian write-decision`), amend the delivery contract through `cartopian write-plan` to match the decision, and rerun the gate.

    An operator who simply declines the external action and wants the plan closed anyway is asking for the second path, not the first. Put that choice to them explicitly rather than editing the record on their behalf.

    A plan authored before the delivery gate carries no `## Delivery contract` section and reports `delivery-contract-undeclared`. That is a protocol migration, not an ad-hoc repair: the requirement landed with the `v0.12.0` entry in `cartopian://protocol/CHANGELOG`, and a project still marked below `v0.12.0` should not have reached closeout at all — `cartopian validate-task-readiness` fails its `project-schema-current` check and `cartopian next-action` raises a migration blocker first. If you are seeing this finding on an unmigrated project, stop closeout and run the `migrate project` skill for the `v0.12.0` entry: declare the section through `cartopian write-plan`, then advance the marker with `cartopian migrate-config <project-path> --apply`, which refuses while the section is still undeclared. Rerun close-audit afterwards.

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

1. **Preservation:** "How should this completed plan be preserved — (1) not at all; (2) full archive only; (3) full archive plus a compact continuity index; or (4) a compact continuity ledger only, without a full archive?"
2. **Standards:** "Should `STANDARDS.md` carry forward as the seed for the next plan, or reset to a blank project standards file?"
3. **Resources:** "The project's supporting artifacts in `resources/` carry forward to the next plan by default (an archive also snapshots them). Keep them all, or name any the operator wants pruned?"

Defaults:

- Preservation: **(1), no preservation**, at every closeout without exception.
- Standards: carry forward only if the operator says so.
- Resources: carry forward. `reset-plan` never clears `resources/`; when the operator asks for an archive, `archive-plan` snapshots `resources/` along with the plan artifacts. Pruning is **operator-performed** (no mediated command deletes resources) and happens only for files the operator explicitly names.

### 2.1 What the four preservation outcomes mean

| # | Outcome | Full archive | Continuity artifact | What survives reset |
| --- | --- | --- | --- | --- |
| 1 | `none` | no | no | Nothing from this plan. Reset intentionally discards its history and decisions. |
| 2 | `archive` | yes | no | The whole plan on disk in `archive/PLAN-NNN/`. Answering a later question means searching the archive. |
| 3 | `archive+index` | yes | yes | The whole plan, plus this plan's rulings as live rows with locators into `archive/PLAN-NNN/decisions/`, and a six-group ledger entry. |
| 4 | `ledger` | no | yes | The ruling sentences and the six-group entry only. No archive, no bodies, no rationale. |

Two relationships are structural, not stylistic. Outcome 3 is an **add-on to full archival** and cannot be selected without the archive, because its rows locate decision bodies inside the archive. Outcome 4 is the **alternative when full archival is declined**, and it is the only outcome that preserves rulings without an archive.

### 2.2 The question is asked identically every time

Ask it the same way at every closeout, with the same four outcomes and the same default (1). Nothing preselects, defaults to, restricts, or biases the next answer: no configuration key at any level holds a preservation outcome, the presence of a `CONTINUITY.md` does not change the offer or the default, and a project holding a failed `ledger` attempt's reservation is offered the same four outcomes with the same default as a project holding none.

### 2.3 State the `ledger` trade before accepting outcome 4

If the operator is choosing (4), say it plainly first: **for the decisions this plan recorded, rulings survive and rationale does not.** There is no archive, so no decision body remains and no later closeout repairs it. That is the price of declining the archive, and it is scoped to this plan — rows an earlier `archive+index` closeout wrote keep their locators.

### 2.4 If the project already has a `CONTINUITY.md` and the answer is (1) or (2)

State both facts plainly, and do not soften either:

- This plan is not preserved. It contributes no ledger entry and no live row, and its plan id becomes a legitimate gap in the ledger.
- The existing continuity record **persists and still reaches every session**. No mediated command removes it, the closeout does not read it, and the rulings it carries were preserved by an earlier operator decision that this answer does not revoke.

Then offer explicit, operator-performed removal. This is a disclosure in a rare state, not a second routine question, and it does not change the four outcomes offered or the default.

Requirements and implementation plans never carry forward as live artifacts. The next planning cycle must produce fresh `REQUIREMENTS.md` and `IMPLEMENTATION_PLAN.md`.

---

## Stage 3 - Optional Archive

**3.0 runs under outcomes 1, 2, and 3.** The archive steps that follow it — 3.1 to 3.5 — are for outcomes 2 and 3 only; under outcome 1 and outcome 4 no archive is created, so skip straight from 3.0 to Stage 3b.

### 3.0 Release an abandoned `ledger` reservation first

This step belongs to no outcome's sequence and is unreachable in a project that has never had a failed or interrupted `ledger` attempt. Run it — before `archive-plan` under outcomes 2 and 3, and before `reset-plan` under outcome 1 — when **both** hold: `archive/PLAN-NNN/` exists for the plan now closing and holds no `CLOSEOUT.md`, and the chosen outcome is **not** (4).

Such a directory is a plan-id reservation a previous `write-continuity --mode ledger` attempt left behind. While it is there it occupies the id `archive-plan` would allocate and advances the derived prompt-evidence window by one, so the closeout would archive as `PLAN-N+1` while every evidence record it wrote says `PLAN-N` — and the closing plan's own evidence would be reported foreign and then deleted.

Release it **before** `archive-plan` or `reset-plan`:

```text
cartopian release-reservation <project-root> --plan PLAN-NNN --closed <YYYY-MM-DD>
```

It reads and writes no continuity content on any path. It removes only that reservation — its `NOT-ARCHIVED.md`, its `LEDGER-FAILED.md` if present, the directory, and its `archive/INDEX.md` reservation row — and closes the reserved window against the plan that is actually closing. It is re-runnable: if it reports outstanding mutations after an interruption, run the same command again; the whole sequence across the interruption is one logical release.

Two responses are not failures to work around:

- `continuity-reservation-unresolved` means the reservation is **complete and unmarked**: a process was killed, or a publication committed and could not be cleaned up, so whether the plan was already recorded cannot be decided from `archive/` alone. Do not archive or reset around it. Surface the one remedy the refusal names — `cartopian write-continuity --mode ledger --plan PLAN-NNN` — and run that first. It either completes a record that already landed (the plan is then preserved as outcome 4, and the operator should be told the outcome changed) or records the plan; if it refuses, it marks the reservation and the release is then unblocked.
- `already_released: true` means a previous run finished; nothing is outstanding.

Under outcome 1, stop here and go to Stage 4; the rest of this stage does not apply.

### 3.1 Archival is PM-performed

Archival is **PM-performed**. Route the complete snapshot operation through the bounded `cartopian archive-plan` command; do not hand raw create/copy/index steps to the operator. The command owns archive numbering, the fixed source allowlist, directory creation, `CLOSEOUT.md`, and `archive/INDEX.md`, and refuses symlinked or non-regular source trees before copying.

### 3.2 Choose archive path

Choose the next available plan archive directory:

```text
archive/PLAN-NNN/
```

- `NNN` is a three-digit counter, starting at `001`.
- `cartopian archive-plan` allocates the next number after existing `archive/PLAN-*` directories.
- `slug` is a short kebab-case name derived from the completed plan title or project outcome.

### 3.3 Write closeout summary

Compose the `CLOSEOUT.md` body from `cartopian://templates/PLAN_CLOSEOUT.md`. Pass it directly as the archive command's `content` value (or use `--content-file` in a shell-capable environment); a contained PM does not need a raw temporary-file write.

The closeout summary records:

- Plan identity and completion date.
- Whether this was a full completion or another operator-approved closeout after all active work was resolved.
- Archive contents.
- The carry-forward choice for `STANDARDS.md`.
- The `resources/` disposition: carried forward (the default), snapshotted in this archive, and any files the operator chose to prune.
- Any plan refs or work intentionally not carried forward.
- Suggested seed context for the next requirements session.

### 3.4 Copy archive artifacts

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

### 3.5 Create the snapshot and update the index

Run the PM-owned archive command before any reset:

```text
cartopian archive-plan <project-root> --closed <YYYY-MM-DD> --summary <brief-outcome> --content <closeout-body>
```

Consume the emitted `archive_path` as the authoritative snapshot location. The command copies the fixed live-artifact set, writes `CLOSEOUT.md`, and creates or appends the one-line entry in `archive/INDEX.md`. If it exits non-zero, stop closeout; never run the reset without the requested snapshot.

The command also runs the prompt-effectiveness contract's ordered plan close against the window it just closed — superseding unit summaries, then the closing projection, then the mediated delete of `.cartopian/prompt-evidence.log`. It is reported under `effectiveness_closeout` in the emitted record and never affects the exit code: the archive is authority, the evidence ledger is not. Read the `closing_projection` rows now if the operator wants the plan's post-approval-defect counts — under plan-bounded retention they are not readable after this point.

---

## Stage 3b - Optional Continuity Write

Skip this stage unless the operator chose outcome 3 or 4. Under outcomes 1 and 2 no continuity command runs, nothing opens `CONTINUITY.md`, and a damaged artifact cannot block the closeout.

Ordering is mandatory and both constraints are consequences of measured behavior:

- `write-continuity` runs **before** `reset-plan`, because it composes the live table from `decisions/` and reset destroys that directory.
- Under outcome 3 it runs **after** `archive-plan`, because its rows name paths inside `archive/PLAN-NNN/decisions/` and `archive-plan` is what allocates `PLAN-NNN`. The command refuses `continuity-archive-unbound` unless `--plan` names the real, highest, not-yet-recorded archive that closeout just created.

### 3b.1 Compose the ledger section

The command composes the header block, the live table, the cold counters, and the cold index itself, from `decisions/` and the existing `CONTINUITY.md`. You author only the six-group plan-ledger section body, and only those six groups:

```text
- Outcome: <intended outcome>; terminal state: <closed | ...>; verification: <outcome-verified | artifact-complete | follow-up-required>
- Evidence: <compact reference to the decisive observation>; state: <verified | missing>
- Decisions: <n> governing; <n> superseded; <n> expired
- Risks: <material unresolved risk; disposition: ...; owner: ... | none open>
- Delivery: <deliverable identity>; target: <target>; state: <delivery/acceptance state>
- Follow-up: <remaining action; owner: ...; state: open | none>
```

All six are always present. Write `none` or `not applicable` explicitly so an omission cannot be read as forgotten work. The outcome/verification distinction is the point of the entry: a completed artifact is not a verified outcome. The whole section is capped at 1,024 bytes. Never put a confidence percentage, a model identity, a review transcript, a prompt body, an agent name, or timing telemetry in it; normal containment and deidentification rules apply to every value.

The `Decisions:` counts are as-of-closeout and are never restated later. The preservation value in the heading is composed by the command from `--mode`; do not author it.

### 3b.2 Run the write

Compose `--plan` yourself at every preservation-bearing closeout so the operator never has to resolve an ambiguity by hand.

Outcome 3, after Stage 3.5's `archive-plan` reported `archive_name`:

```text
cartopian write-continuity <project-root> --mode index --plan <archive_name> --closed <YYYY-MM-DD> --content <ledger-section>
```

Outcome 4, with no archive:

```text
cartopian write-continuity <project-root> --mode ledger --plan PLAN-NNN --closed <YYYY-MM-DD> --content <ledger-section>
```

For outcome 4, `PLAN-NNN` is the next id after the highest `archive/PLAN-*` entry — the same number `archive-plan` would have allocated. The command creates `archive/PLAN-NNN/` holding a `NOT-ARCHIVED.md` sentinel and appends its `archive/INDEX.md` row, so the id cannot be reused; it does not archive anything.

If it exits non-zero, stop closeout and do not reset. Nothing was destroyed: `decisions/` is intact, `CONTINUITY.md` is unchanged, and the prompt-evidence log is intact for the retry. Surface the named refusal and its remedy. Under outcome 4, a refusal that proved nothing was committed also leaves a `LEDGER-FAILED.md` marker naming the two exits — retry the same command, or release the reservation per Stage 3.0 and close the plan with a different outcome.

### 3b.3 What the emitted record tells the operator

Read these from the record rather than re-deriving them:

- `plan` and `plan_source` — whether this was a first attempt (`reservation-allocated`), a retry (`reservation-adopted`), an archive-bound index write (`archive-bound`), or a prose correction to a plan already recorded (`recorded`).
- `superseded`, `expired`, `removal_only`, `skipped_unscoped` — every set the write changed, named rather than counted, so the operator can check it against the decision files. `skipped_unscoped` names locked decisions that published no ruling and are therefore not in the live index; if one of those should have governed, add a `Scope:`/`Ruling:` pair and re-run before reset.
- `stale_reservation` — a reservation left by an abandoned earlier closeout. It is reported, not removed; surface it.
- `effectiveness_closeout` — under outcome 4 the continuity write, not `reset-plan`, closes the prompt-effectiveness window. Read the `closing_projection` rows now if the operator wants the plan's post-approval-defect counts; under plan-bounded retention they are not readable after this point. A `deferred: foreign-window` result means the close is still owed and was declined rather than performed against the wrong window.

### 3b.4 If the write refuses `continuity-ceiling-exceeded`

The artifact has reached its 65,536-byte ceiling. The refusal names what `cartopian prune-continuity` can still recover. Run it with the operator's agreement — it tombstones a closed plan's ledger section (keeping its heading, closed date, and preservation value) and removes cold rows whose decision bodies survive in an archive:

```text
cartopian prune-continuity <project-root> --pruned <YYYY-MM-DD> [--plan PLAN-NNN]... [--cold PLAN-NNN/DEC-NNN]...
```

It refuses to touch a live row, a cold row whose body was never archived, or the newest recorded plan. A `continuity-capacity-structural` refusal means every prunable byte is already reclaimed; there is no further mediated recovery, and the operator's options are a new project or accepting that no further plan can be recorded with preservation. Never resolve either refusal by hand-editing the artifact.

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

Before the first removal — and only after the preflight has passed — `reset-plan` runs the same ordered evidence close as `archive-plan`, so a plan closed without an archive still closes its evidence window. The step is re-entrant: when Stage 3 already ran the archive, `effectiveness_closeout` reports `already_closed` and nothing is written or deleted twice. Like the archive, it never changes the reset's exit code.

That last paragraph is exact for outcome 1 only. Under outcomes 2 and 3 `archive-plan` already closed the window, and **under outcome 4 `write-continuity --mode ledger` closed it** — `reset-plan` derives its window from the archive namespace, which the plan-id reservation has deliberately advanced by one, so it must not be the command that names the closing window. In all three cases `reset-plan` reports `already_closed: true` and its derived window is inert.

`reset-plan` performs no continuity write or read in any outcome. It neither creates, updates, nor removes `CONTINUITY.md`, and `archive/` is not among its targets, so a reservation survives it.

### 4.3 Preserve live project memory

`reset-plan` never touches these — they survive the reset:

- `cartopian.toml`
- `archive/`
- `resources/` (contents; the empty directory is ensured)
- `CONTINUITY.md` (the project-root continuity artifact, when one exists)

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

- Preservation: <none | archive | archive+index | ledger>
- Archive: <none | archive/PLAN-NNN/>
- Continuity: <none | CONTINUITY.md updated for PLAN-NNN | CONTINUITY.md carried forward from an earlier plan, not updated>
- Engineering carry-forward: <yes | no>
- Resources: <carried forward | carried forward, operator pruned <files>>

## What to do next

Run `skills/plan-project.md` to gather fresh requirements and generate the next implementation plan.
```

Confirm the no-plan `cartopian compose-state` record (all fields `null`) before writing — a non-null `current_phase`, `active_work`, `open_work`, or `rendered_body` means Stage 4 reset did not complete and closeout must not finalize the new `STATE.md`.

---

## Stage 6 - Final Summary

Print a concise closeout summary:

- Which preservation outcome the operator chose, and what it preserved.
- Whether the plan was archived.
- Whether `CONTINUITY.md` was written or updated, and — where the project carries one this closeout did not update — that its earlier rulings still govern and still reach every session.
- Under outcome 4, the trade restated once: rulings survive for this plan's decisions, rationale does not.
- What was reset (including reports/).
- Whether project standards carried forward.
- Any unresolved decision follow-up.
- The exact next action: run `skills/plan-project.md`.
