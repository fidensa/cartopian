# Cartopian Protocol Conventions

Rules for keeping a project coherent over many sessions. Drift is the
enemy; these rules exist to make drift structurally difficult.

## Core principle

Filesystem-first. Git is optional. The protocol does not depend on git.
If git versioning is enabled in `cartopian.toml`, auto-commit and
auto-push are handled transparently at session close.

---

## Naming

- Tasks: `TASK-NN-NNN-kebab-case-slug.md`. Phase-scoped: NN is the
  two-digit phase, NNN is the three-digit counter within that phase.
- Specs: `SPEC-NN-NNN-kebab-case-slug.md`. Spec numbering is locked to
  task numbering. A spec's NNN matches the task it belongs to. Specs do
  not have an independent counter.
- Reviews: `REVIEW-NN-NNN.md`. One review per task. Overwritten on
  re-review.
- Prompts: `PROMPT-NN-NNN.md`. Matches task number.
- Phases: `PHASE-NN-slug.md`. Two-digit counter matching plan order.
- Decisions: `DEC-NNN-kebab-case-slug.md`. Three-digit counter, global
  across all projects.

### Trace chain

```
Implementation Plan
  └── ## Phase 01: Contract And Schema Foundation
        └── PHASE-01-contract-foundation.md
              ├── TASK-01-001-cert-schema.md
              │     ├── SPEC-01-001-cert-schema.md
              │     ├── PROMPT-01-001.md
              │     └── REVIEW-01-001.md
              ├── TASK-01-002-baseline-schema.md  (no spec)
              └── TASK-01-003-enforcement-envelope.md
                    └── SPEC-01-003-enforcement-envelope.md
```

Plan refs (`P01-BUILD-003`) encode the phase number. `01` in
`P01-BUILD-003` → `PHASE-01-*` → plan `## Phase 01:` section.

## What never appears in a filename

- Session numbers.
- Dates inside task/spec filenames. (Reviews are the exception under
  the old naming; under Cartopian, reviews use `REVIEW-NN-NNN.md` with
  no date in the filename.)
- Person names in task/spec slugs.

---

## Status through directory

- Task status is the directory it lives in: `open/`, `in-progress/`,
  `in-review/`, `done/`. Moving the file *is* the status update.
- Never add a `status:` field to a task file. The filesystem cannot go
  stale.

---

## Task movement

Tasks can move backward on failed review:

- **`request-changes`** → task moves back to `in-progress/`. The
  assignee addresses the changes and resubmits.
- **`reject`** → task moves back to `open/`. A new approach is required.

No follow-up or replacement tasks are spawned for failed reviews. The
original task is the unit of work throughout its lifecycle.

---

## Reviews

One review per task: `REVIEW-NN-NNN.md`. The review file is overwritten
on re-review. There is no round suffix, no closure sign-off section.

Review verdicts:

- **`approve`** — task moves to `done/`.
- **`request-changes`** — task moves back to `in-progress/`.
- **`reject`** — task moves back to `open/`.

---

## Specs

Specs are mutable. The current version is the version. There are no
version suffixes (`-v1`, `-v2`), no `Supersedes` chains, and no locked
immutability rule. If a spec changes, the file is updated in place.

---

## PM prompt workflow

The PM produces assignee-directed prompts:

1. PM produces proposed assignee + prompt.
2. PM presents both to operator.
3. Operator responds:
   - **"Task assigned/started"** — PM updates task to `in-progress/`.
   - **"Change the prompt..."** — PM revises.
   - **"Change the assignee..."** — PM revises.
4. PM waits for explicit confirmation.

Prompt files use `PROMPT-NN-NNN.md` naming and stay with the task
permanently. When a task reaches `done/`, the prompt file stays. No
archival.

---

## Dependencies

- **Depends on** — tasks whose output this task reads or builds on.
  Informational; does not block start.
- **Blocked by** — tasks that must be in `done/` before this task can
  start.

Both fields carry `TASK-NN-NNN` identifiers only.

---

## Test gate discipline

- Every task declares `Test gate: required` or `Test gate: n/a`.
- `required` tasks name concrete test targets that must fail before
  implementation starts.
- `n/a` is only for non-executable work; it must say why.
- Reviews of `required` tasks must record test evidence: a pointer
  showing the named red test existed before implementation, and a
  pointer showing the same test is green on the closing commit.

---

## Sizing

- `STATE.md` has a hard ceiling of 5KB.
- Task files target under 2KB.
- Phase files are stable for weeks.
- Specs have no fixed ceiling but prefer specificity over
  comprehensiveness.

---

## Decision log discipline

- Every non-trivial decision gets its own file in `decisions/`, named
  `DEC-NNN-kebab-case-slug.md`.
- `decisions/INDEX.md` is a one-line-per-decision summary table.
- A decision that changes a prior decision creates a new file that says
  "supersedes DEC-NNN." The old file is not edited.

---

## Git (optional)

The `projects/` directory is its own git repo, tracking all project PM
data in a single history. This avoids creating a separate PM repo per
project and eliminates naming collisions with code repos.

If `git_versioning = true` in the workspace-level `cartopian.toml`:

- Auto-commit and auto-push at session close. Invisible to operator.
- Commit messages describe the change at the unit-of-work grain.
- Product-repo commits keep red-then-green test-gate discipline.

If `git_versioning = false` (default):

- No git operations. Filesystem is the only record.
- `STATE.md` is current; name the next action.

---

## Session close-out

1. Every task that changed status has been moved to its new directory.
2. Any decisions taken are recorded in `decisions/` and the index is
   updated.
3. `STATE.md` is refreshed and still under 5KB.
4. If git is enabled: auto-commit and auto-push (PM handles this).
5. The final message names the exact next thing to do.

## Session open

1. Read `STATE.md`.
2. Read the phase file for the current phase.
3. Read any active task files and their specs.
4. Go.
