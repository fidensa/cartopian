# Skill: Run Task

Run one Cartopian task from assignment through evidence-supported closure, any required review, verdict handling, and session state refresh.

Use this skill when the operator wants to start, continue, review, or close a task in the current plan.

For vague session-start requests that do not name a project or target task, use `skills/start-session.md` first.

**Output:** The task is moved to the lifecycle state supported by the evidence; prompts, reports, reviews, decisions, and `STATE.md` are left consistent with that state.

**Protocol reference:** This skill does not require the whole protocol document. When a stage needs protocol rules beyond what is written here, read only the relevant section via the section-scoped resource surface:

- `cartopian://protocol/CONVENTIONS/status-through-directory` — directory-as-status semantics behind every task move.
- `cartopian://protocol/CONVENTIONS/tasks` — linear task-execution order and the stop conditions that end confirmation-free continuation (§ Task Execution Order).
- `cartopian://protocol/CONVENTIONS/lifecycle-authority` — who may move tasks and author protocol files.
- `cartopian://protocol/CONVENTIONS/lifecycle-cli-guards` — `move-task` artifact guards and the plan-audit blocker contract (Stages 0, 4, 6).
- `cartopian://protocol/CONVENTIONS/handoffs` — the handoff contract behind Stages 3-6.
- `cartopian://protocol/CONVENTIONS/document-deliverables` — where a document-producing task's work product lives and how the prompt, report, and review reference it (Stages 2, 4, 5).
- `cartopian://protocol/CONVENTIONS/evidence-gate-discipline` — `required` vs `n/a` evidence gates.
- `cartopian://protocol/CONVENTIONS/git` — git policy keys behind the PM-owned product-repo steps and session-close behavior.

The full `cartopian://protocol/CONVENTIONS` remains the authoritative contract; do not load it whole for this skill.

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

This emits the orientation record, including resolved `handoffs` and `reviews`. Retain `reviews.task_closure.mode` and `reviews.task_closure.role`: policy decides whether Stage 5 exists, and the role value (which may be any declared role name) decides who performs it. `handoffs` exposes only the normalized `auto_start_tasks` and `auto_start_reviews` launch keys; never look for legacy `auto_start`. Never infer task review from a role literally named `reviewer` or from description prose. Finally run `cartopian plan-audit <project-path>` at session startup per `cartopian://protocol/CONVENTIONS/lifecycle-cli-guards` and treat a non-zero exit as a blocker.

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

3. Confirm acceptance criteria are actionable for the assignee and, when task-closure review is required, the assigned review role.

---

## Stage 2 - Prepare Assignment Prompt

First, move the task to `tasks/in-progress/` using the Core CLI:

```
cartopian move-task <task-path> in-progress
```

The move precedes prompt authoring so the prompt, completion report, and review all name the `tasks/in-progress/` task path — writing the prompt against the `tasks/open/` path and moving afterwards leaves a stale path in the prompt that the assignee echoes into the report, which `report-action` flags as `path_mismatch`. Use the emitted `task_path_after` as the task path for every subsequent step and stage.

If the session is interrupted between this move and the prompt write, the task sits in `tasks/in-progress/` with no prompt, and `cartopian plan-audit` reports it as a `missing-prompt` blocker at the next session start. Recover by resuming this stage: author the prompt against the in-progress task path. Do not try to move the task back to `open` — the CLI disallows that transition outside a review verdict.

Then assemble the prompt-input bundle with a single Core CLI call against the moved task path:

```
cartopian handoff-packet <task-path> --role <role>
```

`handoff-packet` is the FR-003 aggregator. It returns one NDJSON record with the resolved `role_description`, the `[handoffs.<role>]` block (`handoff_target`, `model`, `effort`, `auto_start_tasks`, `auto_start_reviews`, `timeout`), resolved `reviews`, the ordered `work_roots` list (each `{name, absolute_path}`), the `expected_report_path`, and the relevant `[git]` policy keys under `git_policy`. Source every prompt value from this record; do not re-derive paths, roles, or review policy.

If the call exits non-zero (missing role block, unreadable config, task file not found), surface the error and stop — do not fall back to a manual read sequence.

If the task's work product is a durable document (research, design, evaluation) rather than code and the task's `Deliverable:` field is not yet set, prompt the operator for where the document should live before authoring the prompt: an existing work-root name plus a relative path (`root:relative/path.md`, written directly by the assignee), or `project:relative/path.md` (returned inline and persisted by the PM). Persist the chosen value into the task via `cartopian write-task` so it enters the trace chain, then re-run `handoff-packet` so the record carries the resolved `deliverable`. See `cartopian://protocol/CONVENTIONS/document-deliverables`.

Then author the assignment prompt. This is a **PM-performed** write; the contained PM has no raw `Write` tool, so create or update `prompts/PROMPT-NN-NNN.md` through the mediated writer:

```
cartopian write-prompt <project-root> --prompt-id PROMPT-NN-NNN --content-file <body-path>
```

The command resolves the allowlisted `prompts/` destination from the `--prompt-id`, so the PM never supplies a free-form path; re-issuing it overwrites the same prompt in place on a retry. The assignee handoff is **deidentified**: name the work by its title and address every resource by file path. Do **not** put project-management identifiers (the task id, plan ref, spec id, `FR-`/`NF-` requirement refs, decision refs) anywhere in the prompt body — they map to nothing once PM data is archived and can leak into the delivered work. The prompt body must be directed at the assignee and include, sourced from the `handoff-packet` record:

- Absolute project root.
- Declared `Work root:` names from the task header (comma-separated), or `n/a`.
- Absolute path(s) for the declared work root(s) (from the record's `work_roots[].absolute_path`); use `n/a` when none are declared.
- When the task names a spec, the **deidentified** spec body, inlined into the prompt's `## Specification` section. Obtain it by running `cartopian render-spec <spec-path>` (the `spec_path` comes from the `task-bundle` record) and using the `deidentified_spec` field. Do **not** put the raw spec path in the prompt or otherwise direct the assignee to read `specs/` — the raw spec carries PM identifiers that would leak into product code.
- When the record's `deliverable` is non-null, a `## Deliverable` section directing the assignee to the durable work product: for a `work-root` deliverable, the absolute `deliverable.absolute_path` with an instruction to write the complete work product there and keep the report a summary that points to it; for a `project` deliverable, an instruction to return the complete work product inline in the report's `## Deliverable content` section (the prompt's Deliverable path stays `n/a`). Omit this section when `deliverable` is null.
- Absolute expected report path (from the record's `expected_report_path`).
- Absolute report template path.
- The goal, context, acceptance criteria, scope boundaries, and test gate — written as self-contained prose, not as references to PM artifacts.
- A reminder that assignees do not modify spec, task, phase, or prompt files — only the PM edits Cartopian protocol files; if any of those are wrong, ambiguous, or insufficient, the assignee stops and reports it as a blocker.
- A reminder that assignees do not move Cartopian task files, delete prompts, rewrite `STATE.md`, or perform PM lifecycle cleanup.
- When `git.pm_owns_product_branches = true` and the task declares one or more `Work root:` names, a reminder that assignees do not stage, commit, push, branch, open PRs, merge, or otherwise perform product-repo git plumbing.
- When the assignment is **verification-only** (it directs the assignee to inspect or run gates and explicitly forbids implementation changes), state the effective git operating model from the handoff packet. In the no-product-git model (`git_versioning = false`, which implies `git_policy = null`, or an effective `git_policy.pm_owns_product_branches = false`), say explicitly: Cartopian git versioning is off; product-repository branches are not PM-owned; the work root may already contain uncommitted deliverables from earlier completed tasks; that dirty steady state is expected and is not evidence that this verification task modified files. The assignee must distinguish pre-existing work-root state from changes made during its own handoff and must report only the latter as a scope violation.

Remove any stale report using the Core CLI before assigning or retrying the task:

```text
cartopian delete-report <report-path>
```

The expected `<report-path>` is the absolute path for this task's completion report in the project `reports/` directory. This also removes the companion `<report-path>.status` wrapper status file when present, so a reused report slot never carries a stale early-crash signal into the next handoff.

---

## Stage 3 - Assign Or Launch Work

Use `skills/run-handoff.md` for assignment mechanics.

For manual assignment, present the prompt path and expected report path to the operator and wait for explicit assignment/start confirmation.

For configured task-scoped agent handoff, follow the resolved `auto_start_tasks` value and automation policy. Planning-review handoffs use `auto_start_reviews` through `skills/run-handoff.md`.

The task is already in `tasks/in-progress/` from Stage 2. Prompt existence is enforced fail-closed at the handoff boundary: `cartopian dispatch` refuses to launch when `prompts/PROMPT-NN-NNN.md` is missing. The prompt written in Stage 2 satisfies this check.

If the operator returns later with completion evidence even though assignment was never recorded, fast-forward to the evidence-supported state instead of leaving completed work in `open/`.

---

## Stage 4 - Process Completion Report

Wait for the assignee to finish before parsing. Detect task-execution completion with the Core CLI wait primitive rather than a hand-rolled timing loop or a manual "tell me when it's done" prompt:

```
cartopian wait-handoff <task-path> --role <role> --max-block <duration>
```

The report file is the authoritative completion signal; `wait-handoff` blocks read-only until it observes a terminal `status` (`done`, `failed`, `failed-to-parse`, or `timeout`) or its `--max-block` budget elapses (`still-running`). Treat `still-running` / `still_running` as a nonterminal internal observation boundary. Routine nonterminal slices are silent and context-neutral: keep the initiated run active and re-invoke the same canonical wait primitive in another bounded slice without user-facing text or repeated state when no material state changed. User-facing output is allowed only for a terminal result, blocker, timeout/failure, meaningful new progress evidence, or a deliberately throttled long-running threshold. An automatic host wake/resume must not itself emit a user-visible message merely because an observation slice ended. The re-wait is read-only, does not launch a second assignee, and does not consume a `max_handoffs_per_run` unit; only the original launch does. Do not ask for operator continuation between slices. If the host cannot keep one turn open, use the automatic wake/resume mechanism. Only proceed once the status is `done`. When assignment runs through `skills/run-handoff.md`, that skill owns this wait step under the same contract.

Then parse the assignee's completion report with the Core CLI:

```
cartopian report-action <report-path>
```

`report-action` is the FR-004 aggregator. It infers the report variant from filename and content (here, the `task` variant) and emits a single NDJSON record carrying:

- `verdict` — `accepted | blocked | failed | failed-to-parse`.
- `variant` — `task` for this stage.
- `status` — the report's `Status:` header value.
- `target_task_status` — `in-review` when task-closure review is required, `done` when it is off, or the evidence-supported nonterminal status.
- `requires_pr_step` — true when the PM-owned product-repo git step is required before reviewer dispatch.
- `prompt_to_overwrite` — the prompt path the PM may reuse for reviewer assignment.
- `path_mismatch` — true when the report's declared task path does not match the resolved expected task path. Treat `path_mismatch = true` as `failed-to-parse`.

Evidence-supported lifecycle moves are applied without an operator confirmation prompt (`cartopian://protocol/CONVENTIONS/tasks` § Task Execution Order — the `[automation]` policy gates pace, not selection). Report the move in the running summary; consult the operator only at the stop conditions below.

If the verdict is `blocked`, `failed`, or `failed-to-parse`, stop automation, keep the prompt and report for inspection, record the blocker in `STATE.md`, and return control to the operator.

If the verdict is `accepted` with `Ready to close: no` (or legacy `Ready for review: no`), keep the task in `tasks/in-progress/`, record the reason in `STATE.md`, and return control to the operator.

If the verdict is `accepted` with `Ready to close: yes` (or the legacy heading), first persist every durable output. If the task declares a `project`-mode `Deliverable:`, persist the report's `## Deliverable content` to `deliverable.absolute_path` using PM project-write authority before any lifecycle move or report reuse. A `work-root`-mode deliverable is already written by the assignee.

If the effective `[git]` configuration has `pm_owns_product_branches = false`, or the setting is unset, skip the git block below and apply the routing step after it.

If `pm_owns_product_branches = true` and the task declares one or more `Work root:` names, the `report-action` record's `requires_pr_step` will be `true`. Perform the PM-owned product-repo git step before review or closure.

> **Containment boundary.** The product-repo git steps below (`git`/`gh` plumbing, and the merge-evidence append to the review file in Stage 6) are raw shell operations against the product repo. They have no mediated Cartopian command in the Phase-01 set, so they are **outside the contained-PM path**: a contained PM (no shell) runs only with `pm_owns_product_branches = false` (or unset), where `requires_pr_step` is never set and this entire block is skipped. When `pm_owns_product_branches = true`, the git workflow is owned by the operator or an uncontained PM. This is a deliberate boundary, not a lifecycle-authoring action the mediated writers cover.

1. Treat assignee-supplied product-repo git evidence as a boundary violation. If the report claims the assignee staged, committed, pushed, branched, opened a PR, or merged product-repo work, stop for operator inspection.
2. Resolve the product-repo absolute path(s) from the declared work-root names via the resolved config's `work_roots` mapping; when multiple are declared, choose the root that actually owns this task's changes. If ambiguous, stop for operator inspection.
3. Resolve the configured branch name. The protocol default branch name is `task/NN-NNN-slug`, derived from `git.default_branch_pattern = "task/{task_id}-{slug}"`.
4. Create or update that branch in the product repo. On a first pass, create it before committing the task changes. On a rework pass with an existing open PR, reuse the same branch.
5. Inspect the product-repo worktree and stage only the changes that belong to the task. If the worktree does not contain actionable task changes, or contains unrelated changes that cannot be separated, stop for operator inspection.
6. Commit the staged task changes with a message that references the task ID and completion report. Capture the resulting implementation commit SHA.
7. Push the branch with `git push -u origin <branch>`.
8. Open a PR with `gh pr create`, or reuse the existing PR on rework. The title and body must reference the task ID and completion report.
9. Resolve a deploy preview URL when one exists, for example from a Vercel-bot PR comment. If no preview URL exists, proceed with the PR URL only and record the missing preview URL in `STATE.md`.
10. Capture the branch, PR URL, preview URL if present, and implementation commit SHA as handoff/closure evidence.

Apply the `report-action` routing only after deliverable persistence and any required PR preparation:

- `target_task_status == "in-review"`: run `cartopian move-task <task-path> in-review`; the complete task report satisfies the guard. Continue to Stage 5 and assign the exact role from `reviews.task_closure.role`.
- `target_task_status == "done"`: when `recommended_action == "prepare-pr-and-close-task"`, merge the prepared PR with the configured strategy and capture the merge SHA; then run `cartopian move-task <task-path> done`, delete the task prompt, record that closure occurred with task review off, and skip to Stage 7. The CLI requires the complete task report for this direct closure.

If `pm_owns_product_branches = true` but no `Work root:` is declared (or it is `n/a`), there is no product-repo branch or PR step; apply the same routing with PR and preview values `n/a`.

---

## Stage 5 - Assign Review

Run this stage only when `reviews.task_closure.mode == "required"`. Assign the exact arbitrary role named by `reviews.task_closure.role`; do not search for a role called `reviewer`.

Authoring the review prompt is **PM-performed**. Create or update `prompts/PROMPT-NN-NNN.md` for the assigned review role through the mediated writer when the same prompt path is being reused for review, or ensure the existing prompt clearly identifies the review assignment:

```
cartopian write-prompt <project-root> --prompt-id PROMPT-NN-NNN --content-file <body-path>
```

The review prompt must include absolute paths to:

- The task file.
- The spec file, when present.
- The deliverable, when the task declares one — the absolute `deliverable.absolute_path`, named as the **primary artifact to review** (the durable work product, not a summary of it). For a `project`-mode deliverable this is the copy the PM persisted in Stage 4.
- The coder's completion report — the input the reviewer reads.
- The expected review file the reviewer writes (`reviews/REVIEW-NN-NNN.md`), carrying findings and the `Verdict:` header.
- The expected report path the reviewer writes its review-completion report to (the `expected_report_path` from the handoff record — the same `reports/REPORT-NN-NNN.md` slot the coder report used, cleared below before the review handoff).
- The report template path, directing the reviewer to the **review-completion variant** of `cartopian://templates/REPORT.md`.
- Absolute path(s) for the declared work root(s), if any.
- Relevant implementation evidence.
- The PR URL and preview URL when the PM-owned product-repo git workflow created them; otherwise `n/a`.

The reviewer produces **two** artifacts, exactly as the coder produces its work product plus a report. State both explicitly in the prompt:

- The durable **review file** (`reviews/REVIEW-NN-NNN.md`) is the work product: findings, evidence, and the `Verdict:` header the `in-review → done | in-progress | open` move guard reads.
- The transient **review-completion report** (`reports/REPORT-NN-NNN.md`, review-completion variant — `Status:` header and a `## Verdict` section) is the **handoff completion signal**. `cartopian wait-handoff` and `cartopian report-action` watch the *report*, never the review file. A reviewer that writes only the review file leaves the handoff with no completion signal: `wait-handoff` then blocks to the deadline (and, if the reviewer process has already exited, reports `failed` — "exited without a report") even though the review itself is complete. The review file's `Verdict:` header and the report's `## Verdict` section must agree.
- The review-completion report's `## Identity` block must copy the absolute task-file path from the prompt into `Task path:`. `report-action` cross-checks it against the task implied by `REPORT-NN-NNN.md`; a missing, stale, or wrong task path is not valid completion evidence.

The review prompt must also include:

- A reminder that reviewers do not modify spec, task, phase, or prompt files — only the PM edits Cartopian protocol files. If the spec is wrong, ambiguous, or contradicts the implementation, the reviewer records the finding in the review file (and verdict accordingly) rather than rewriting the spec to match what was built.
- A reminder that reviewers do not move Cartopian task files, delete prompts, rewrite `STATE.md`, or perform PM lifecycle cleanup.
- When the reviewed task is **verification-only**, carry the assignment prompt's effective git operating model into the review prompt. In the no-product-git model (`git_versioning = false`, which implies `git_policy = null`, or an effective `git_policy.pm_owns_product_branches = false`), state explicitly that an already-dirty work root containing prior completed tasks' deliverables is the expected steady state, not a review defect and not proof that the verification handoff changed files. The reviewer evaluates whether this handoff introduced changes using the coder report and task evidence; it must not issue `request-changes` merely because `git status` shows pre-existing modifications or untracked deliverables.

After task completion evidence has been captured in the review prompt, task file, or review context, remove any stale review handoff report using the Core CLI when issuing a distinct review handoff that expects the same report path:

```text
cartopian delete-report <report-path>
```

Use `skills/run-handoff.md` for review handoff mechanics.

---

## Stage 6 - Process Review Verdict

Run this stage only when `reviews.task_closure.mode == "required"`.

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
- `task_id` and `task_path` — the task resolved from the report filename and cross-checked against the report's declared `Task path`.
- `path_mismatch` — true when any declared handoff path, including `Task path`, disagrees with the report filename's expected paths. Treat `path_mismatch = true` as `failed-to-parse`.

If the verdict is `blocked`, `failed`, or `failed-to-parse`, or if `path_mismatch = true`, stop automation, preserve the prompt and report for inspection, record the blocker in `STATE.md`, and return control to the operator.

Apply the reviewer's verdict without an operator confirmation prompt — the verdict is the review file's recorded evidence, and the CLI guards verify it before executing any move (the `[automation]` policy gates pace, not selection). Report the applied verdict in the running summary. Decisions the protocol or plan reserves to the operator (e.g. an open-question ruling the task was created to inform) remain operator-owned: pause for those before recording them, even when the task's own lifecycle proceeds. Apply the verdict by delegating directory status transitions to the Core CLI:

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

1. Record any non-trivial decisions. Authoring a decision is **PM-performed**; write `decisions/DEC-NNN-slug.md` through the mediated writer rather than a raw `Write`:

   ```
   cartopian write-decision <project-root> --dec-id DEC-NNN --slug <slug> --title "<title>" --date <YYYY-MM-DD> --content-file <body-path>
   ```

   The same command renders the `decisions/INDEX.md` row from the `--title` / `--date` / `--status` / `--supersedes` arguments, so a separate raw edit of `INDEX.md` is not needed (and the contained PM cannot perform one).
2. Ensure task, review, and report evidence agree.
3. Remove superseded prompts with the Core CLI (`cartopian delete-prompt <prompt-path>`), never a raw `rm`.
4. Leave reports in place until the PM has captured any needed evidence in task, review, decision, or backlog files. `STATE.md` is not an evidence home — its body is composed from the filesystem.
6. Remove the transient wrapper status file for any report whose handoff is finished, even when the report `.md` is intentionally retained as evidence:

   ```text
   cartopian delete-report <report-path> --status-only
   ```

   The `<report-path>.status` file is early-crash enrichment for the wait step only and must not outlive the handoff; `--status-only` clears it while leaving the report `.md` in place. Reports may linger after `done`; the companion `.status` file must not. See `wrappers/README.md` and `cartopian://protocol/CONVENTIONS/handoffs`.

Do not treat reports as durable substitutes for task, review, or decision records.

---

## Stage 8 - Close Session

Refresh `STATE.md` via the Core CLI:

```
cartopian write-state <project-root>
```

`write-state` composes the canonical body (Current phase / Active work / Open work / What to do next) from the filesystem in-process — do not run `compose-state` first or pass `--content`; the writer refuses a PM-authored body while plan artifacts exist. The body never round-trips through PM context.

If — and only if — this session surfaced a fact that is (1) about this project's current state, (2) not derivable from the filesystem, config, or protocol, and (3) changes what the next session does, deliver it as a situation note:

```
cartopian write-state <project-root> --note "coder deploy failed mid-handoff; operator is restarting the development machine"
```

Notes are bounded (max 5, one line of ≤ 200 chars each) and have a one-delivery TTL: every `write-state` starts from zero notes, a byte-identical re-pass is refused, and `plan-audit` blocks the next session until each note is acted on, promoted (`write-backlog`, `write-decision`), or dropped. Protocol-compliance feedback is never a note — it routes to `BACKLOG.md` as process debt (`cartopian://protocol/CONVENTIONS/session-state`).

If git versioning is enabled for the project, the PM performs the configured session-close git behavior for project PM data. Git staging, commits, and pushes for the protocol repository itself remain human-owned.

Then continue linearly (`cartopian://protocol/CONVENTIONS/tasks` § Task Execution Order): if the task reached `done`, a next sequential task is ready, and the resolved `[automation]` policy permits (`until-blocked` with run budget remaining), start that task from Stage 1 without asking. Otherwise finish with a concise operator-facing summary that names the task's new status and the exact next protocol action — when the budget is spent, say so and name the next sequential task the operator's "continue" will start.
