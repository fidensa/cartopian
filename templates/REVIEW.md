# REVIEW-NN-NNN

Target: <TASK-NN-NNN-slug or SPEC-NN-NNN-slug>
Plan ref: <PNN-KIND-NNN | n/a>
Target repo: <repo | n/a>
Reviewer: <free text>
Verdict: approve | request-changes | reject

## Summary

Two lines. What was reviewed, and what the verdict rests on.

## Implementation evidence

Required for build and porting tasks. For planning and repo-admin tasks,
use `n/a` in each field and say why.

- **Commit SHA** — the green commit the reviewer approved against.
- **Merge commit SHA** — the merge commit on `main` that landed the
  feature branch. For tasks whose target repo is the PM system or
  `n/a`, write `n/a`.
- **PR URL** — the pull request, if one exists.
- **Test evidence** — two parts when the task's test gate was `required`:
  - Red test was present before implementation.
  - Test is green at closure.
  When test gate was `n/a`, write `n/a — test gate was n/a per task`.

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
