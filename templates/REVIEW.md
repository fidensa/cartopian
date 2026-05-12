# REVIEW-NN-NNN

Target: <TASK-NN-NNN-slug or SPEC-NN-NNN-slug>
Plan ref: <PNN-KIND-NNN | n/a>
Work root: <name | name, name | n/a>
Reviewer: <free text>
Verdict: approve | request-changes | reject

## Summary

Two lines. What was reviewed, and what the verdict rests on.

## Implementation evidence

Required for build and porting tasks. For planning and repo-admin tasks,
use `n/a` in each field and say why.

- **Commit SHA** — filled by the reviewer: the green implementation
  commit they approved against. In PM-owned product-repo git projects,
  this is the PM-created task commit from `skills/run-task.md` Stage 4.
- **Merge commit SHA** — filled by the PM in Stage 6 of
  `skills/run-task.md`, post-merge. The reviewer writes `pending`, or
  `n/a` when the project does not use PM-owned product-repo git.
- **PR URL** — filled by whichever role has it: the reviewer when the
  PR existed before review in the PM-owned product-repo git workflow,
  otherwise by the PM after merge.
- **Test evidence** — two parts when the task's evidence gate was `required`:
  - Red test was present before implementation.
  - Test is green at closure.
  When evidence gate was `n/a`, write `n/a — evidence gate was n/a per task`.

## Findings

Each finding carries a severity:

- **blocker** — approval is impossible until resolved.
- **major** — real defect or significant gap.
- **minor** — worth fixing, does not block.
- **nit** — style or clarity.

Findings:

- F1. [blocker | major | minor | nit] — Description with file path
  and line range or section reference.
- F2. …

## Suggested actions

- For `request-changes`: what to address before resubmission.
- For `reject`: what a new approach should consider.

## Reviewer notes

Optional. Anything the author or a future reader should know.

> **Reviewer boundary:** create the review file and record the verdict only.
> Do not move task files, delete prompts, or perform lifecycle cleanup.
> The PM applies lifecycle changes after reading the review.
