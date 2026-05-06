# Skill: Run Task

Run one Cartopian task from assignment through completion report,
review, verdict handling, and session state refresh.

Use this skill when the operator wants to start, continue, review, or
close a task in the current plan.

For vague session-start requests that do not name a project or target
task, use `skills/start-session.md` first.

**Output:** The task is moved to the lifecycle state supported by the
evidence; prompts, reports, reviews, decisions, and `STATE.md` are left
consistent with that state.

---

## Prerequisites

- The project has an active `IMPLEMENTATION_PLAN.md`.
- `STATE.md` exists and is under 5KB.
- The target task exists in one of the task status directories.
- Any `Blocked by` task identifiers are already in `tasks/done/`.

---

## Stage 0 - Open Session Context

1. Read `STATE.md`.
2. Read the current phase file.
3. Read the target task file.
4. Read the task's spec when the task references one.
5. Read the project and workspace `cartopian.toml` files.
6. Resolve effective roles, handoff targets, automation policy, and
   `[git]` configuration.

If the task state in `STATE.md` disagrees with the filesystem, treat the
filesystem as authoritative and refresh `STATE.md` before proceeding.

---

## Stage 1 - Confirm Task Readiness

1. Verify the task's phase and plan ref exist in the implementation plan
   and phase file.
2. Verify `Depends on` and `Blocked by` fields use `TASK-NN-NNN`
   identifiers only.
3. Verify each `Blocked by` task is in `tasks/done/`.
4. Verify the task has a valid test gate:
   - `required` names concrete red-before-green targets.
   - `n/a` explains why no executable gate applies.
5. Verify the task has checkable acceptance criteria.

Stop and surface a blocker if readiness cannot be established.

---

## Stage 2 - Prepare Assignment Prompt

For a task assignment, create or update:

```text
prompts/PROMPT-NN-NNN.md
```

The prompt must be directed at the assignee and include:

- Absolute prompt path.
- Absolute project root.
- Absolute repo path.
- Absolute task path.
- Absolute spec path, or `n/a`.
- Absolute expected report path.
- Absolute expected review path, when applicable.
- Absolute report template path.
- Task goal, context, acceptance criteria, scope boundaries, and test
  gate.
- A reminder that assignees do not move Cartopian task files, delete
  prompts, rewrite `STATE.md`, or perform PM lifecycle cleanup.
- When `git.pm_owns_product_branches = true` and `Repo subpath:` names
  a product repo, a reminder that assignees do not stage, commit, push,
  branch, open PRs, merge, or otherwise perform product-repo git
  plumbing.

Delete any stale report at:

```text
reports/REPORT-NN-NNN.md
```

before assigning or retrying the task.

---

## Stage 3 - Assign Or Launch Work

Use `skills/run-handoff.md` for assignment mechanics.

For manual assignment, present the prompt path and expected report path
to the operator and wait for explicit assignment/start confirmation.

For configured agent handoff, follow the resolved `auto_start` value and
automation policy.

Move the task to `tasks/in-progress/` only after assignment/start is
confirmed or after an auto-start handoff is actually launched.

If the operator returns later with completion evidence even though
assignment was never recorded, fast-forward to the evidence-supported
state instead of leaving completed work in `open/`.

---

## Stage 4 - Process Completion Report

Read and parse:

```text
reports/REPORT-NN-NNN.md
```

Use the parsing outcomes from `skills/run-handoff.md`.

If the report is `blocked`, `failed`, or `failed-to-parse`, stop
automation, keep the prompt and report for inspection, record the
blocker in `STATE.md`, and return control to the operator.

If the report is accepted with `Ready for review: no`, keep the task in
`tasks/in-progress/`, record the reason in `STATE.md`, and return
control to the operator.

If the report is accepted with `Ready for review: yes`, move the task to
`tasks/in-review/`, capture any evidence the reviewer will need from
the completion report, and proceed to reviewer assignment.

If the effective `[git]` configuration has
`pm_owns_product_branches = false`, or the setting is unset, proceed to
Stage 5 exactly as today.

If `pm_owns_product_branches = true` and the task names a product repo
with `Repo subpath:`, perform the PM-owned product-repo git step before
Stage 5:

1. Treat coder-supplied product-repo git evidence as a boundary
   violation. If the report claims the assignee staged, committed,
   pushed, branched, opened a PR, or merged product-repo code, stop for
   operator inspection.
2. Resolve the product repo from the task's `Repo subpath:`.
3. Resolve the configured branch name. The protocol default branch name
   is `task/NN-NNN-slug`, derived from
   `git.default_branch_pattern = "task/{task_id}-{slug}"`.
4. Create or update that branch in the product repo. On a first pass,
   create it before committing the task changes. On a rework pass with
   an existing open PR, reuse the same branch.
5. Inspect the product-repo worktree and stage only the changes that
   belong to the task. If the worktree does not contain actionable task
   changes, or contains unrelated changes that cannot be separated, stop
   for operator inspection.
6. Commit the staged task changes with a message that references the
   task ID and completion report. Capture the resulting implementation
   commit SHA.
7. Push the branch with `git push -u origin <branch>`.
8. Open a PR with `gh pr create`, or reuse the existing PR on rework.
   The title and body must reference the task ID and completion report.
9. Resolve a deploy preview URL when one exists, for example from a
   Vercel-bot PR comment. If no preview URL exists, proceed with the PR
   URL only and record the missing preview URL in `STATE.md`.
10. Capture the branch, PR URL, preview URL if present, and
    implementation commit SHA as review handoff evidence.

If `pm_owns_product_branches = true` but the task's `Repo subpath:` is
`n/a`, there is no product-repo branch or PR step; proceed to Stage 5
with `PR URL` and `Preview URL` as `n/a`.

---

## Stage 5 - Assign Review

Create or update:

```text
prompts/PROMPT-NN-NNN.md
```

for the reviewer when the same prompt path is being reused for review,
or ensure the existing prompt clearly identifies the review assignment.

The review prompt must include absolute paths to:

- The task file.
- The spec file, when present.
- The completion report.
- The expected review file.
- The repo path.
- Relevant implementation evidence.
- The PR URL and preview URL when the PM-owned product-repo git workflow
  created them; otherwise `n/a`.

After task completion evidence has been captured in the review prompt,
task file, or review context, delete any stale review handoff report at:

```text
reports/REPORT-NN-NNN.md
```

only when issuing a distinct review handoff that expects the same report
path.

Use `skills/run-handoff.md` for review handoff mechanics.

---

## Stage 6 - Process Review Verdict

Reviewers record their findings and verdict in:

```text
reviews/REVIEW-NN-NNN.md
```

The PM applies the verdict:

- `approve`, when `git.pm_owns_product_branches = false` or unset, or
  when no product-repo PR exists: move the task to `tasks/done/` and
  delete the matching prompt.
- `approve`, when `git.pm_owns_product_branches = true` and a PR exists:
  merge with `gh pr merge --<strategy> --delete-branch`, using the
  effective `git.default_merge_strategy` (`merge`, `squash`, or
  `rebase`). Capture the merge commit SHA, append it to the review
  file's existing `Implementation evidence` block as
  `Merge commit SHA`, append `PR URL` if the review file does not
  already include it, then move the task to `tasks/done/` and delete the
  matching prompt.
- `request-changes`: move the task to `tasks/in-progress/`. When
  PM-owned product-repo git is enabled, leave the branch and PR open for
  the next coder pass.
- `reject`: move the task to `tasks/open/`. When PM-owned product-repo
  git is enabled, leave the branch and PR open for the next coder pass.

On re-review, overwrite `reviews/REVIEW-NN-NNN.md`. Do not create round
suffixes.

Failed reviews do not create replacement tasks. Continue with the
original task.

---

## Stage 7 - Update Durable Records

1. Record any non-trivial decisions in `decisions/DEC-NNN-slug.md`.
2. Update `decisions/INDEX.md` when decisions changed.
3. Ensure task, review, and report evidence agree.
4. Remove superseded prompts.
5. Leave reports in place until the PM has captured any needed evidence
   in task, review, decision, or state files.

Do not treat reports as durable substitutes for task, review, or
decision records.

---

## Stage 8 - Close Session

Refresh `STATE.md` so it remains under 5KB and names:

- Current phase.
- Active work.
- Open work.
- Blockers, if any.
- The exact next protocol action.

If git versioning is enabled for the project, the PM performs the
configured session-close git behavior for project PM data. Git staging,
commits, and pushes for the protocol repository itself remain
human-owned.

Finish with a concise operator-facing summary that names the task's new
status and the exact next action.
