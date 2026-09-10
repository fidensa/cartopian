# Skill: Run Task

Run one Cartopian task from assignment through evidence-supported closure, any required review, verdict handling, and session state refresh. Use this skill when the operator wants to start, continue, review, or close a task in the current plan. For vague session-start requests that do not name a project or target task, use `skills/start-session.md` first.

**Output:** The task is moved to the lifecycle state supported by the evidence; prompts, reports, reviews, decisions, and `STATE.md` are left consistent with that state.

**Protocol reference:** The full `cartopian://protocol/CONVENTIONS` remains the authoritative contract; do not load it whole for this skill. Each stage below names the section-scoped slice that governs it — read a slice at the stage that needs it, never up front, so a task that stops early never pays for later-stage rules.

---

## Operator request evidence

Operator evidence is the operator's own turns, captured by the host intake hooks and bound by `select_project`; the writers resolve it, and a planned task inherits the project evidence through its verified task-to-phase-to-plan ancestry with no later operator restatement. A task-specific operator statement reaches the task only by capture identity from `cartopian lookup-evidence <project-root> --unit task:TASK-NN-NNN --recent`, referenced from a decision or the task's `## Request evidence` section; PM prose is never operator evidence. Never create, copy, or edit anything under `requests/` or the intake directory, never invoke operator-only intake, and never use shell or escalation to bypass an evidence gate; relay any evidence refusal to the operator verbatim and stop. Rules: `cartopian://protocol/CONVENTIONS/up-front-operator-request-evidence`.

---

## Prerequisites

- The project has an active `IMPLEMENTATION_PLAN.md`; `STATE.md` exists and is under 5KB.
- The target task exists in a task status directory and every `Blocked by` task is in `tasks/done/`.
- The absolute project path is known (selected from `cartopian discover-projects`) so `cartopian resolve-config <project-path>` can run. Any `Work root:` names the task declares must exist in `[project].work_roots`.

---

## Stage 0 - Open Session Context

Governing slices: `cartopian://protocol/CONVENTIONS/lifecycle-cli-guards` (move guards, plan-audit blocker contract) and `cartopian://protocol/CONVENTIONS/lifecycle-authority`.

1. Run `cartopian next-action <project-path>`. Retain `reviews.task_closure.mode` and `reviews.task_closure.role`: policy decides whether Stage 5 exists, and the role value (any declared role name) decides who performs it. Never infer task review from a role literally named `reviewer` or from description prose.
2. Run `cartopian plan-audit <project-path>`; treat a non-zero exit as a blocker.
3. Before proposing any action, surface `state_filesystem_disagreement` (the filesystem is authoritative — offer to refresh `STATE.md` via the mediated `cartopian write-state`) and every `blockers` entry. Resolve blockers with the operator before Stage 1; do not advance lifecycle state while blockers exist.

---

## Stage 1 - Confirm Task Readiness

Governing slices, as this task's declarations make them applicable: `cartopian://protocol/CONVENTIONS/evidence-gate-discipline`, `cartopian://protocol/CONVENTIONS/source-backed-work`, `cartopian://protocol/CONVENTIONS/risk-classification-and-scaled-governance`.

1. `cartopian task-bundle <task-path>` — one record with the task identity, `spec_path`, ordered `dependencies`, `work_roots_resolved`, `source_guidance`, `expected_prompt_path`, and `expected_report_path`. Consume these fields directly; do not re-read the task, spec, or phase files to derive them.
2. `cartopian validate-task-readiness <task-path>` — a non-zero exit is a blocker; surface the emitted reason and recovery verbatim, without scoring or averaging. A failing `project-schema-current` check is a project-level blocker: stop, surface the named recovery, and run the `migrate project` skill — do not edit the task or hand-edit `cartopian.toml`.
3. Confirm acceptance criteria are actionable for the assignee and, when task-closure review is required, the assigned review role.
4. Risk: read the five records under `## Risk observations` and call `cartopian classify-risk` with each observation's state and supporting fact. A missing, duplicate, unsupported, or undeclared observation is a readiness blocker; never substitute prose judgment or convert a missing fact into a favorable state. Retain the structured result as readiness evidence — the Stage 2 composer re-derives the same result deterministically.
5. Judgment: read only the two declared lists under `## Judgment envelope` and call `cartopian select-judgment-guidance`, repeating `--lifecycle-boundary` per crossed boundary and `--open-failure-condition` per named open non-enforceable failure (no fact options for a legacy task; the valid result is `none`). Do not pass the risk result, band, or a pack outcome. `invalid` blocks prompt authoring; `active` carries exactly the one returned central body.
6. Practice pack: read only the declared lists under `## Practice-pack envelope` and call `cartopian select-practice-pack`, repeating the matching option per declared value (`--primary-outcome`, `--artifact-kind`, `--incidental-term`, `--exclusion`, `--lifecycle-substrate-activity`, `--domain-scope`; `--authorized-profile-hint` only when the task carries one; no options for a legacy task — the valid result is `none`). `ambiguous` or `invalid` blocks; `selected` contributes exactly its returned body. Declared domain scopes decide only which conditional sources the result reports as applicable — they never select, veto, or change a pack.

The configured review policy remains authoritative: classification never edits `reviews`, roles, grants, or launch/automation values. If the derived independent-review expectation is deeper than the configured task-closure policy, surface that difference as an operator gate before closeout; do not silently enable a review or choose a reviewer.

---

## Stage 2 - Prepare Assignment Prompt

Governing slices: `cartopian://protocol/CONVENTIONS/status-through-directory`; for a document deliverable, `cartopian://protocol/CONVENTIONS/document-deliverables` and `cartopian://protocol/CONVENTIONS/project-resources`.

1. `cartopian move-task <task-path> in-progress` — the move precedes prompt authoring so the prompt, report, and review all name the in-progress path. Use the emitted `task_path_after` for every subsequent step. If interrupted between this move and the prompt write, `cartopian plan-audit` reports `missing-prompt` next session: resume by authoring the prompt against the in-progress path (the CLI disallows moving back to `open` outside a review verdict).
2. `cartopian handoff-packet <task-path> --role <role>` — one record with the resolved role, `reviews`, `automation_policy`, `work_roots`, `source_guidance`, `expected_report_path`, and git policy. Source every prompt value from this record; on a non-zero exit surface the error and stop — no manual read fallback.
3. If the work product is a durable document and the task's `Deliverable:` field is unset: a supporting artifact takes the protocol default (`project:resources/<kind>/<title-slug>.md`) — re-issue the task through `cartopian write-task` with the field omitted or set to `default` and the writer stamps it; no operator question. Ask the operator only when the work product is bound for the product itself, because that destination changes product structure: an existing work-root name plus an operator-chosen `root:relative/path`. Persist the value into the task via `cartopian write-task`, then rerun `handoff-packet`.
4. Clear the stale completion-report slot before composing: `cartopian delete-report <report-path>` (the absolute completion-report path from the bundle/packet records).
5. Compose deterministically — the assignment body is generated, never hand-assembled: `cartopian compose-assignment-prompt <task-path> --role <role>` emits `assignee_prompt`, `trace_receipt`, `content_identity`, and `findings`. A non-`composed` outcome is a blocker: fix the named input (task, spec, standards, config, or captured evidence) and recompose; never edit the prompt to pass.
6. Write the verified record through the mediated writer: `cartopian write-prompt <project-root> --prompt-id PROMPT-NN-NNN --task <absolute-in-progress-task-path> --composed-file <record-path>`. Re-issuing overwrites the same prompt in place. Do not paste selector JSON, receipts, hashes, rejected candidates, or inactive guidance into any prompt.
7. For a verification-only assignment under the no-product-git model, the composed scope boundaries state the effective git operating model: Cartopian git versioning is off; product-repository branches are not PM-owned; the work root may already contain prior completed tasks' deliverables — that dirty steady state is expected and is not evidence that this verification task modified files. After `write-prompt`, rerun `handoff-packet` and require `existing_deliverable_input.ok: true` and every `dependency_deliverable_inputs` record's `ok: true`.

---

## Stage 3 - Assign Or Launch Work

Governing sub-slices: `cartopian://protocol/CONVENTIONS/handoffs/preamble` (assignment contract, role launch facts, timeout authority), `cartopian://protocol/CONVENTIONS/handoffs/launch-directory`, and `cartopian://protocol/CONVENTIONS/handoffs/work-roots` (includes automation policy). Use `skills/run-handoff.md` for assignment mechanics.

- Manual assignment: present the prompt path and expected report path to the operator and wait for explicit start confirmation.
- Configured agent handoff: requires the applicable `task_run` or `task_review` entry in the resolved role's `auto_launch` list; planning-review handoffs require `planning_review`. `cartopian dispatch` refuses to launch when the prompt is missing — Stage 2's prompt satisfies the check.
- For a critical result, the `independent-challenge` expectation is not self-certifiable: use the critical adversarial procedure in `skills/run-handoff.md`. If configured review is off, stop at the derived operator gate and ask whether to authorize one scoped independent challenge or change policy.
- If the operator returns with completion evidence though assignment was never recorded, fast-forward to the evidence-supported state instead of leaving completed work in `open/`.

---

## Stage 4 - Process Completion Report

Governing slice: `cartopian://protocol/CONVENTIONS/handoffs/waiting-for-completion`.

1. Wait with the Core CLI primitive — terminal by default; one blocking call is the whole completion mechanism:

   ```
   cartopian wait-handoff <task-path> --role <role>
   ```

   `--max-block` exists only for a host ceiling that cannot be raised; treat `still-running` / `still_running` as a nonterminal internal observation boundary. Routine nonterminal slices are silent and context-neutral: keep the initiated run active and re-invoke the same canonical wait primitive in another bounded slice without user-facing text or repeated state when no material state changed. User-facing output is allowed only for a terminal result, blocker, timeout/failure, meaningful new progress evidence, or a deliberately throttled long-running threshold. A re-wait is read-only, launches no second assignee, and does not consume a `max_handoffs_per_run` unit; do not ask for operator continuation between slices. Proceed only on `done`. When assignment runs through `skills/run-handoff.md`, that skill owns this wait under the same contract.

2. Parse, bound to the exact publication the wait accepted:

   ```
   cartopian report-action <report-path> --expected-identity <report_content_identity from the wait record>
   ```

   On `verdict: identity-mismatch`, re-run the canonical wait and use the identity it returns. Route on the emitted record: `verdict`, `status`, `target_task_status`, `requires_pr_step`, `prompt_to_overwrite`, `path_mismatch` (true — treat as `failed-to-parse`), `source_evidence` (require `outcome: valid` for a complete source-backed task), and the bounded `pm_summary` projection. Route on the projection and record fields; do not open the full report unless a projected value requires it — the unbounded body stays on disk as durable evidence.

3. Evidence-supported lifecycle moves apply without an operator confirmation prompt (`cartopian://protocol/CONVENTIONS/tasks` § Task Execution Order — `[automation]` gates pace, not selection). Report each move in the running summary; consult the operator only at the stop conditions below.
4. `failed-to-parse` with a report present: run `cartopian validate-report <report-path>` and route each failed check by `failure_class` (see `skills/run-handoff.md` § Failure routing) — `mechanical` gets the hash-bound in-place `cartopian correct-report` fix, `missing-input` means repair the input and rerun readiness before re-dispatch, `substantive` goes to the review loop or operator. For `blocked`, `failed`, or an unroutable `failed-to-parse`: stop automation, keep the prompt and report for inspection, record the blocker in `STATE.md`, and return control to the operator.
5. `accepted` with readiness `no` (under `Ready to close` or `Ready for review`): keep the task in `tasks/in-progress/`, record the reason in `STATE.md`, and return control. `no` declares the producer's own work incomplete or blocked; a producer whose work is complete but who cannot certify closure writes `yes`.
6. `accepted` with readiness `yes`: persist every durable output first. A `project`-mode `Deliverable:` is persisted from the report's `## Deliverable content` through `cartopian write-resource <project-root> --path <resources-relative-path> --content-file <body-path>` before any lifecycle move or report reuse; a `work-root` deliverable is already written by the assignee.
7. Git (`cartopian://protocol/CONVENTIONS/git`): when `pm_owns_product_branches` is false or unset, skip this step entirely. When true and the task declares `Work root:` names (`requires_pr_step: true`), perform the PM-owned product-repo git step before review or closure: treat assignee-supplied product-repo git evidence as a boundary violation (stop for inspection); resolve the owning work root and the configured branch name (default `task/{task_id}-{slug}`); create or reuse the branch; stage only this task's changes (stop for inspection if there are none or they cannot be separated); commit referencing the task ID and report and capture the SHA; push; open or reuse the PR referencing the task ID and report; resolve a deploy preview URL when one exists; capture branch, PR URL, preview URL, and commit SHA as evidence. This raw-shell block is outside the contained-PM path — a contained PM runs only with `pm_owns_product_branches = false`.
8. Apply the routing after persistence and any PR preparation: `target_task_status == "in-review"` — `cartopian move-task <task-path> in-review`, then Stage 5 with the exact `reviews.task_closure.role`. `target_task_status == "done"` — when `recommended_action == "prepare-pr-and-close-task"`, merge the prepared PR and capture the merge SHA; then `cartopian move-task <task-path> done`, delete the task prompt via `cartopian delete-prompt`, and skip to Stage 7.

---

## Stage 5 - Assign Review

Run only when `reviews.task_closure.mode == "required"`; assign the exact role named by `reviews.task_closure.role`.

Author the review prompt through the mediated writer (PM-performed, never a raw write):

```
cartopian write-prompt <project-root> --prompt-id PROMPT-NN-NNN \
  --content-file <body-path> --review-kind task-closure \
  --task <absolute-in-review-task-path>
```

The writer resolves the exact request trace, verifies the preserved coder completion report, and generates the context-bound sections — the intent packet under `## Original operator request (verbatim)` and the `## PM-derived guidance and delivered outcome` channel. Do not summarize or edit them, and do not modify or remove the completion report while the task is in review. Before a manual handoff, require `request_trace.preflight.ok: true` from `handoff-packet`.

The generated review-file skeleton includes the required `## Contract quality` audit after `## Request comparison` and before `## Implementation evidence`. Keep it in every task-closure review, including tasks with `Upstream trace: n/a`; the reviewer records the outcome and any contract gaps before evaluating implementation. Never supply a passing outcome on the reviewer's behalf.

The review prompt must include absolute paths for: the task file; the spec when present; the declared deliverable (the primary artifact to review); the generated `## Preserved coder completion evidence` binding — the reviewer reads `reports/REPORT-NN-NNN.md` directly, and the prompt never reproduces its body; the review file the reviewer writes (`reviews/REVIEW-NN-NNN.md`, findings plus the `Verdict:` header); the expected review-report path (`reports/REPORT-NN-NNN-review.md` — never the preserved completion report's path); the declared work roots; relevant implementation evidence; and PR/preview URLs or `n/a`. Paste the skeletons returned by `cartopian report-skeleton <task-path> --variant review` (`skeleton` and `review_file_skeleton`) instead of full templates — the machine-owned identities are already filled; the reviewer supplies verdicts, findings, and comparisons only.

State explicitly that the reviewer produces two artifacts: the durable review file is the work product, and the transient review-completion report is the completion signal that `cartopian wait-handoff` and `cartopian report-action` watch — a reviewer that writes only the review file leaves the handoff blocking to its deadline. The two verdicts must agree; the report's `## Identity` copies the absolute `Task path:`; both artifacts record `Request alignment:` and `Request evidence:` from the generated channel (drift blocks approval; `unavailable-for-legacy` is non-blocking only when the generated prompt declares it).

Also include: a `## Your role` preface from the packet's `role_description`; reminders that reviewers do not modify spec, task, phase, or prompt files, do not move Cartopian task files, delete prompts, rewrite `STATE.md`, or perform PM lifecycle cleanup (a spec defect is a review finding, never a spec edit); when the reviewed outcome changes a practice-pack body, the requirement to inspect that body and its governing/conditional sources directly and fill the review's `## Practice-pack semantic review` section with one observation and disposition per dimension (automated validation proves structure, never substance; no scores); and, for a verification-only task, the assignment prompt's git operating model — an already-dirty work root holding prior deliverables is the expected steady state, and the reviewer must not issue `request-changes` merely because `git status` shows pre-existing modifications or untracked deliverables.

Never delete the coder completion report before or during the review handoff. When a prior attempt left a stale review report behind, clear only the review slot before re-dispatching: `cartopian delete-report <expected-review-report-path>`. Use `skills/run-handoff.md` for review handoff mechanics.

---

## Stage 6 - Process Review Verdict

Run only when `reviews.task_closure.mode == "required"`. Governing slices: `cartopian://protocol/CONVENTIONS/status-through-directory` and `cartopian://protocol/CONVENTIONS/handoffs/waiting-for-completion`.

1. `cartopian report-action <reviewer-report-path>` — the `review` record carries `verdict`, `review_verdict` (`approve | request-changes | reject`), `target_task_status`, `prompt_to_overwrite` (returned for every applied verdict — the review prompt is consumed by its verdict in all three cases), `task_id`/`task_path`, `path_mismatch` (true — treat as `failed-to-parse`), `request_alignment` (a blocking result makes an approving report `failed-to-parse`), and the bounded `review_projection` of the durable review file (verdict, summary, findings rows). Route on the projection; open the full review file only when a projected finding requires the surrounding detail.
2. On `blocked`, `failed`, `failed-to-parse`, or `path_mismatch`: stop automation, preserve the prompt and report for inspection, record the blocker in `STATE.md`, and return control to the operator.
3. `cartopian review-intake <project-root> --task <task-path> --review <review-path>` — run for every applied verdict, not only `approve`. A non-zero exit on an approving pass is a governed blocker (`cartopian move-task <task-path> done` will refuse with the same reason): stop, record the blocker in `STATE.md`, and return control. The intake is inert for a task without `Upstream trace: required`.
4. Apply the verdict without an operator confirmation prompt — the CLI guards verify the review file before executing any move. Decisions the protocol or plan reserves to the operator remain operator-owned; pause for those even when the task's own lifecycle proceeds.
   - `approve`, no PM-owned PR: `cartopian move-task <task-path> done`, then `cartopian delete-prompt <prompt-path>`.
   - `approve`, `pm_owns_product_branches = true` with a PR: `gh pr merge --<strategy> --delete-branch` per `git.default_merge_strategy`; capture the merge SHA and append it (plus `PR URL` if absent) to the review file's `Implementation evidence` block; then move `done` and delete the prompt as above.
   - `request-changes`: `cartopian move-task <task-path> in-progress`. `reject`: `cartopian move-task <task-path> open`. Either way, leave any PM-owned branch and PR open for the next pass.
5. After applying `request-changes` or `reject`: remove the consumed review report (`cartopian delete-report <review-report-path>`) and retire the consumed review prompt (`cartopian delete-prompt <prompt-path>`, the `prompt_to_overwrite` path). The retirement is required — a retained consumed prompt reads as `stale-request-context` in `cartopian plan-audit` — but only after `report-action` has parsed the verdict and the findings are preserved in the review file, never while a `blocked`/`failed`/`failed-to-parse` outcome still needs the prompt for inspection. The preserved completion report needs no cleanup for a rework round; the next coder dispatch replaces that slot.
6. On re-review, overwrite `reviews/REVIEW-NN-NNN.md`; no round suffixes. Failed reviews do not create replacement tasks — continue with the original task.

---

## Stage 7 - Update Durable Records

1. Record any non-trivial decisions by writing `decisions/DEC-NNN.md` through `cartopian write-decision <project-root> --dec-id DEC-NNN --title "<title>" --date <YYYY-MM-DD> --content-file <body-path>` (it also renders the `decisions/INDEX.md` row).
2. Ensure task, review, and report evidence agree.
3. Remove superseded prompts with `cartopian delete-prompt <prompt-path>`, never a raw `rm`.
4. Leave reports in place until their evidence is captured in task, review, decision, or backlog records; then clear each consumed slot with its own `cartopian delete-report` call (idempotent over absent companions). `STATE.md` is not an evidence home.
5. Clear the transient wrapper status of any report intentionally retained as evidence: `cartopian delete-report <report-path> --status-only` — the `.status` file is wait-step enrichment only and must not outlive the handoff (`cartopian://protocol/CONVENTIONS/handoffs/waiting-for-completion`, `wrappers/README.md`).

Do not treat reports as durable substitutes for task, review, or decision records.

---

## Stage 8 - Close Session

Refresh `STATE.md` through `cartopian write-state <project-root>` — the writer composes the canonical body from the filesystem in-process; do not run `compose-state` first or pass `--content`.

If — and only if — this session surfaced a fact that is about this project's current state, not derivable from the filesystem/config/protocol, and changes what the next session does, deliver it as a situation note: `cartopian write-state <project-root> --note "…"`. Notes are bounded (max 5, one line of ≤ 200 chars each) with a one-delivery TTL. Protocol-compliance feedback is never a note — it routes to `BACKLOG.md` via `cartopian write-backlog` (`cartopian://protocol/CONVENTIONS/session-state`).

If git versioning is enabled, perform the configured session-close git behavior for project PM data (`cartopian://protocol/CONVENTIONS/git`); git for the protocol repository itself remains human-owned.

Then apply the resolved `run_boundary` (`cartopian://protocol/CONVENTIONS/handoffs` § Optional automation policy):

- `run_boundary = "task-complete"`: this run was bound to this task. Reaching `done` ends it successfully — return control without selecting, moving, prompting, or dispatching another task, and name the task's new status and the exact next protocol action. Before `done`, keep continuing whichever configured and authorized activity this same task needs next, whatever kind of work it is; do not ask the operator to re-authorize between activities.
- `run_boundary = "handoff-budget"`: if the task reached `done`, a next sequential task is ready, and run budget remains, start it from Stage 1 without asking. When the budget is spent, say so and name the task the operator's "continue" will start.
- `run_boundary = "handoff-complete"`: finish here with a concise summary naming the task's new status and the exact next protocol action.

Under every boundary, a blocker, failed or absent report, missing evidence, operator-reserved decision, or explicit stop ends the run instead.
