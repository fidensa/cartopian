# Skill: Run Task

Run one Cartopian task from assignment through completion report, review, verdict handling, and session state refresh.

Use this skill when the operator wants to start, continue, review, or close a task in the current plan.

For vague session-start requests that do not name a project or target task, use `skills/start-session.md` first.

**Output:** The task is moved to the lifecycle state supported by the evidence; prompts, reports, reviews, decisions, and `STATE.md` are left consistent with that state.

---

## Prerequisites

- The project has an active `IMPLEMENTATION_PLAN.md`.
- `STATE.md` exists and is under 5KB.
- The target task exists in one of the task status directories.
- Any `Blocked by` task identifiers are already in `tasks/done/`.
- The absolute project path is known (selected from `cartopian discover-projects`) so `cartopian resolve-config <project-path>` can be run. If the task declares `Work root:` names, ensure they exist in the project's `[project].work_roots` list; the validator will block unknown names.

---

## Stage 0 - Open Session Context

Run the orientation aggregator using the Core CLI for the selected project path:

```
cartopian next-action <project-path>
```

This emits a single NDJSON record carrying every field needed to orient the session: `project_id`, `project_path`, `phase_id`, `active_task`, `next_open_task`, `pm_role`, `pm_dispatch_kind`, `blockers`, and `state_filesystem_disagreement`. It internally resolves the project config (the same data `cartopian resolve-config` would emit) and performs the lifecycle audit `cartopian plan-audit` would, so neither needs to be invoked separately to orient the session.

Surface the disagreement and blocker fields to the operator before proposing any action:

- **`state_filesystem_disagreement`**: if non-null, a task status claimed in `STATE.md` does not match the directory the task file actually lives in. The filesystem is authoritative. Surface the mismatch and offer to refresh `STATE.md` before continuing.
- **`blockers`**: any non-empty `blockers` array is a PM-level blocker (for example `no active phase detected but tasks are present`, or `unresolved open question in STATE.md: …`). Surface each entry to the operator and stop. Do not advance lifecycle state while blockers exist.

Resolve blockers with the operator before proceeding to Stage 1.

---

## Stage 1 - Confirm Task Readiness

1. Assemble the task + spec + phase + dependency context with a single Core CLI call:

   ```
   cartopian task-bundle <task-path>
   ```

   `task-bundle` is the FR-002 aggregator. It emits one NDJSON record with the resolved task identity (`task_id`, `task_title`, `task_path`, `task_status`), the resolved `spec_path`, the ordered `dependencies` list (each carrying `task_id`, `title`, `path`, `status`), the resolved `work_roots_resolved` entries (each `{name, absolute_path, exists}`), the `expected_prompt_path`, and the `expected_report_path` Stage 2 and Stage 4 will reference. Consume these fields directly; do not re-read the task, spec, or phase files to derive them.

2. Validate readiness gates with the Core CLI:

   ```
   cartopian validate-task-readiness <task-path>
   ```

   `task-bundle` assembles content; `validate-task-readiness` enforces readiness gating — the two are complementary. Treat a non-zero exit from `validate-task-readiness` as a blocker and stop.

3. Confirm acceptance criteria are actionable for the assignee/reviewer per task context.

---

## Stage 2 - Prepare Assignment Prompt

First, assemble the prompt-input bundle with a single Core CLI call:

```
cartopian handoff-packet <task-path> --role <role>
```

`handoff-packet` is the FR-003 aggregator. It returns one NDJSON record with the resolved `role_description`, the `[handoffs.<role>]` block (`handoff_target`, `auto_start`, `timeout`), the ordered `work_roots` list (each `{name, absolute_path}`), the `expected_report_path`, and the relevant `[git]` policy keys under `git_policy`. Source every prompt value from this record; do not re-derive paths or roles.

If the call exits non-zero (missing role block, unreadable config, task file not found), surface the error and stop — do not fall back to a manual read sequence.

Then, create or update:

```text
prompts/PROMPT-NN-NNN.md
```

The prompt must be directed at the assignee and include, sourced from the `handoff-packet` record:

- Absolute prompt path.
- Absolute project root.
- Declared `Work root:` names from the task header (comma-separated), or `n/a`.
- Absolute path(s) for the declared work root(s) (from the record's `work_roots[].absolute_path`); use `n/a` when none are declared.
- Absolute task path.
- Absolute spec path, or `n/a`.
- Absolute expected report path (from the record's `expected_report_path`).
- Absolute expected review path, when applicable.
- Absolute report template path.
- Task goal, context, acceptance criteria, scope boundaries, and test gate.
- A reminder that assignees do not modify spec, task, phase, or prompt files — only the PM edits Cartopian protocol files; if any of those are wrong, ambiguous, or insufficient, the assignee stops and reports it as a blocker.
- A reminder that assignees do not move Cartopian task files, delete prompts, rewrite `STATE.md`, or perform PM lifecycle cleanup.
- When `git.pm_owns_product_branches = true` and the task declares one or more `Work root:` names, a reminder that assignees do not stage, commit, push, branch, open PRs, merge, or otherwise perform product-repo git plumbing.

Remove any stale report using the Core CLI before assigning or retrying the task:

```text
cartopian delete-report <report-path>
```

The expected `<report-path>` is the absolute path for this task's completion report in the project `reports/` directory. This also removes the companion `<report-path>.status` wrapper status file when present, so a reused report slot never carries a stale early-crash signal into the next handoff.

---

## Stage 3 - Assign Or Launch Work

Use `skills/run-handoff.md` for assignment mechanics.

For manual assignment, present the prompt path and expected report path to the operator and wait for explicit assignment/start confirmation.

For configured agent handoff, follow the resolved `auto_start` value and automation policy.

Move the task to `tasks/in-progress/` using the Core CLI only after assignment/start is confirmed or after an auto-start handoff is launched:

```
cartopian move-task <task-path> in-progress
```

The CLI verifies that `prompts/PROMPT-NN-NNN.md` exists before executing this rename. The prompt written in Stage 2 satisfies this check.

If the operator returns later with completion evidence even though assignment was never recorded, fast-forward to the evidence-supported state instead of leaving completed work in `open/`.

---

## Stage 4 - Process Completion Report

Wait for the assignee to finish before parsing. Detect task-execution completion with the Core CLI wait primitive rather than a hand-rolled timing loop or a manual "tell me when it's done" prompt:

```
cartopian wait-handoff <task-path> --role <role> --max-block <duration>
```

The report file is the authoritative completion signal; `wait-handoff` blocks read-only until it observes a terminal `status` (`done`, `failed`, `failed-to-parse`, or `timeout`) or its `--max-block` budget elapses (`still-running`). On `still-running`, yield control back to the operator and re-call `wait-handoff` on resume — the filesystem observation survives the yield, so no progress is lost and no second handoff starts. Only proceed once the status is `done`. When assignment runs through `skills/run-handoff.md`, that skill owns this wait step under the same contract.

Then parse the assignee's completion report with the Core CLI:

```
cartopian report-action <report-path>
```

`report-action` is the FR-004 aggregator. It infers the report variant from filename and content (here, the `task` variant) and emits a single NDJSON record carrying:

- `verdict` — `accepted | blocked | failed | failed-to-parse`.
- `variant` — `task` for this stage.
- `status` — the report's `Status:` header value.
- `target_task_status` — the lifecycle directory the PM should move the task into next (typically `in-review` for accepted task reports).
- `requires_pr_step` — true when the PM-owned product-repo git step is required before reviewer dispatch.
- `prompt_to_overwrite` — the prompt path the PM may reuse for reviewer assignment.
- `path_mismatch` — true when the report's declared task path does not match the resolved expected task path. Treat `path_mismatch = true` as `failed-to-parse`.

Confirm with the operator before applying any lifecycle move.

If the verdict is `blocked`, `failed`, or `failed-to-parse`, stop automation, keep the prompt and report for inspection, record the blocker in `STATE.md`, and return control to the operator.

If the verdict is `accepted` with `Ready for review: no`, keep the task in `tasks/in-progress/`, record the reason in `STATE.md`, and return control to the operator.

If the verdict is `accepted` with `Ready for review: yes`, apply the lifecycle move named by `target_task_status` (typically `in-review`) using the Core CLI:

```
cartopian move-task <task-path> in-review
```

The CLI verifies that `reports/REPORT-NN-NNN.md` exists, references this task's ID, and has `Status: complete` before executing this rename. The parsed completion report already on disk satisfies this check. Capture any evidence the reviewer will need from the completion report and proceed to reviewer assignment.

If the effective `[git]` configuration has `pm_owns_product_branches = false`, or the setting is unset, proceed to Stage 5 exactly as today.

If `pm_owns_product_branches = true` and the task declares one or more `Work root:` names, the `report-action` record's `requires_pr_step` will be `true`. Perform the PM-owned product-repo git step before Stage 5:

1. Treat coder-supplied product-repo git evidence as a boundary violation. If the report claims the assignee staged, committed, pushed, branched, opened a PR, or merged product-repo code, stop for operator inspection.
2. Resolve the product-repo absolute path(s) from the declared work-root names via the resolved config's `work_roots` mapping; when multiple are declared, choose the root that actually owns this task's changes. If ambiguous, stop for operator inspection.
3. Resolve the configured branch name. The protocol default branch name is `task/NN-NNN-slug`, derived from `git.default_branch_pattern = "task/{task_id}-{slug}"`.
4. Create or update that branch in the product repo. On a first pass, create it before committing the task changes. On a rework pass with an existing open PR, reuse the same branch.
5. Inspect the product-repo worktree and stage only the changes that belong to the task. If the worktree does not contain actionable task changes, or contains unrelated changes that cannot be separated, stop for operator inspection.
6. Commit the staged task changes with a message that references the task ID and completion report. Capture the resulting implementation commit SHA.
7. Push the branch with `git push -u origin <branch>`.
8. Open a PR with `gh pr create`, or reuse the existing PR on rework. The title and body must reference the task ID and completion report.
9. Resolve a deploy preview URL when one exists, for example from a Vercel-bot PR comment. If no preview URL exists, proceed with the PR URL only and record the missing preview URL in `STATE.md`.
10. Capture the branch, PR URL, preview URL if present, and implementation commit SHA as review handoff evidence.

If `pm_owns_product_branches = true` but no `Work root:` is declared (or it is `n/a`), there is no product-repo branch or PR step; proceed to Stage 5 with `PR URL` and `Preview URL` as `n/a`.

---

## Stage 5 - Assign Review

Create or update:

```text
prompts/PROMPT-NN-NNN.md
```

for the reviewer when the same prompt path is being reused for review, or ensure the existing prompt clearly identifies the review assignment.

The review prompt must include absolute paths to:

- The task file.
- The spec file, when present.
- The completion report.
- The expected review file.
- Absolute path(s) for the declared work root(s), if any.
- Relevant implementation evidence.
- The PR URL and preview URL when the PM-owned product-repo git workflow created them; otherwise `n/a`.

The review prompt must also include:

- A reminder that reviewers do not modify spec, task, phase, or prompt files — only the PM edits Cartopian protocol files. If the spec is wrong, ambiguous, or contradicts the implementation, the reviewer records the finding in the review file (and verdict accordingly) rather than rewriting the spec to match what was built.
- A reminder that reviewers do not move Cartopian task files, delete prompts, rewrite `STATE.md`, or perform PM lifecycle cleanup.

After task completion evidence has been captured in the review prompt, task file, or review context, remove any stale review handoff report using the Core CLI when issuing a distinct review handoff that expects the same report path:

```text
cartopian delete-report <report-path>
```

Use `skills/run-handoff.md` for review handoff mechanics.

---

## Stage 6 - Process Review Verdict

Reviewers record their findings and verdict in:

```text
reviews/REVIEW-NN-NNN.md
```

Parse the reviewer's completion report with the Core CLI:

```
cartopian report-action <reviewer-report-path>
```

For the `review` variant, the emitted record carries:

- `verdict` — `accepted | blocked | failed | failed-to-parse` for handoff state.
- `review_verdict` — the raw reviewer token, one of `approve | request-changes | reject`.
- `target_task_status` — the post-verdict lifecycle directory (`done` for `approve`, `in-progress` for `request-changes`, `open` for `reject`).
- `prompt_to_overwrite` — the prompt path to clear via `cartopian delete-prompt` after an `approve` verdict.

Confirm the verdict with the operator before applying any lifecycle move. Then apply the verdict by delegating directory status transitions to the Core CLI:

- `approve`, when `git.pm_owns_product_branches = false` or unset, or when no product-repo PR exists: use `cartopian move-task <task-path> done` and remove the matching prompt via the Core CLI:

  ```
  cartopian delete-prompt <prompt-path>
  ```

  The CLI verifies that `reviews/REVIEW-NN-NNN.md` exists with `Verdict: approve` before executing this rename. The review file the reviewer wrote satisfies this check.

- `approve`, when `git.pm_owns_product_branches = true` and a PR exists: merge with `gh pr merge --<strategy> --delete-branch`, using the effective `git.default_merge_strategy` (`merge`, `squash`, or `rebase`). Capture the merge commit SHA, append it to the review file's existing `Implementation evidence` block as `Merge commit SHA`, append `PR URL` if the review file does not already include it, then `cartopian move-task <task-path> done` and remove the matching prompt via the Core CLI:

  ```
  cartopian delete-prompt <prompt-path>
  ```

  Same guard as above: `reviews/REVIEW-NN-NNN.md` must exist with `Verdict: approve`.

- `request-changes`: `cartopian move-task <task-path> in-progress`. The CLI verifies `reviews/REVIEW-NN-NNN.md` exists with `Verdict: request-changes`. When PM-owned product-repo git is enabled, leave the branch and PR open for the next coder pass.
- `reject`: `cartopian move-task <task-path> open`. The CLI verifies `reviews/REVIEW-NN-NNN.md` exists with `Verdict: reject`. When PM-owned product-repo git is enabled, leave the branch and PR open for the next coder pass.

On re-review, overwrite `reviews/REVIEW-NN-NNN.md`. Do not create round suffixes.

Failed reviews do not create replacement tasks. Continue with the original task.

---

## Stage 7 - Update Durable Records

1. Record any non-trivial decisions in `decisions/DEC-NNN-slug.md`.
2. Update `decisions/INDEX.md` when decisions changed.
3. Ensure task, review, and report evidence agree.
4. Remove superseded prompts.
5. Leave reports in place until the PM has captured any needed evidence in task, review, decision, or state files.
6. Remove the transient wrapper status file for any report whose handoff is finished, even when the report `.md` is intentionally retained as evidence:

   ```text
   cartopian delete-report <report-path> --status-only
   ```

   The `<report-path>.status` file is early-crash enrichment for the wait step only and must not outlive the handoff; `--status-only` clears it while leaving the report `.md` in place. Reports may linger after `done`; the companion `.status` file must not. See `wrappers/README.md` and `protocol/CONVENTIONS.md` § Handoffs.

Do not treat reports as durable substitutes for task, review, or decision records.

---

## Stage 8 - Close Session

Render the post-task `STATE.md` body via the Core CLI:

```
cartopian compose-state <project-path>
```

`compose-state` is the FR-006 aggregator. It emits a single NDJSON record with `current_phase`, `active_work`, `open_work`, `what_to_do_next`, and `rendered_body` — the last is the full markdown body. Write `rendered_body` directly to `STATE.md`. Confirm the result is under 5KB and names:

- Current phase.
- Active work.
- Open work.
- Blockers, if any.
- The exact next protocol action.

If git versioning is enabled for the project, the PM performs the configured session-close git behavior for project PM data. Git staging, commits, and pushes for the protocol repository itself remain human-owned.

Finish with a concise operator-facing summary that names the task's new status and the exact next action.
