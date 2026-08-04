# Skill: Run Handoff

Run one Cartopian handoff from prompt preparation through report processing. This reusable workflow applies to task assignment, task review, and planning-checkpoint review handoffs.

Use this skill when another Cartopian skill needs to hand work to a human or configured agent and then interpret the completion report.

**Output:** A prepared prompt handoff, an accepted or blocked report outcome, and no lifecycle movement beyond what the caller explicitly owns.

**Protocol reference:** The protocol contract for this workflow is `cartopian://protocol/CONVENTIONS/handoffs` — read that section, not the whole protocol document, when handoff rules beyond this skill are needed. Role declaration rules live in `cartopian://protocol/CONVENTIONS/roles`. The full `cartopian://protocol/CONVENTIONS` remains the authoritative contract; do not load it whole for a handoff.

---

## Prerequisites

- A Cartopian project directory exists.
- The caller knows the role being assigned.
- The caller knows the absolute prompt path to create or reuse.
- The caller knows the expected absolute report path.
- The caller knows the expected report variant from `cartopian://templates/REPORT.md`.
- The caller knows which lifecycle action, if any, is allowed after the report is accepted.
- The absolute project path is known (selected from `cartopian discover-projects`) so `cartopian resolve-config <project-path>` can be run.

---

## Stage 0 - Resolve Effective Configuration

Use the Core CLI to resolve effective roles, handoff agents, automation policy, work roots, and relevant `[git]` keys for the selected project absolute path:

```
cartopian resolve-config <project-path>
```

If you do not have the absolute path, run `cartopian discover-projects` and select the entry; use its `path` field.

Read from the resolved output:

- The resolved `[roles.<role>]` records, each carrying a description, effective grants, assigned work types, launch facts, automatic-launch permissions, and attribution.
- The resolved role record for the role being assigned, including `launch`, `auto_launch`, and attribution.
- The `[automation]` policy, defaulting to `confirmation = "each-handoff"` and `max_handoffs_per_run = 1` when unset.

If the role being assigned is not declared in the resolved `[roles]` table, stop and return a blocked outcome to the caller ("role not declared in `[roles]`; declare it or assign a different role").

If the role is declared but its resolved `launch.agent` is unset, return a manual-dispatch outcome to the caller: the PM surfaces the prompt path and expected report path, and the operator handles execution.

---

## Stage 1 - Prepare Prompt And Report Slot

First, assemble the prompt-input bundle with a single Core CLI call. `handoff-packet` is the FR-003 aggregator: it returns one NDJSON record with the resolved role `description`, `effective_grants`, `assigned_work_types`, `launch`, `auto_launch`, and attribution; resolved `reviews` and `automation_policy`; the work-root absolute paths the assignee will be granted; the resolved source-guidance record; the expected absolute report path; and the relevant Git policy. The call is read-only; it does not write, move, or delete anything.

```
cartopian handoff-packet <task-path> --role <role>
```

Read from the emitted record:

- `role_description` — the one-line description for the role being assigned.
- `launch` — resolved `agent`, `model`, `effort`, and `timeout`, consumed by Stage 2. Unset optional values are serialized as `null`.
- `auto_launch` — the closed automatic-launch permission list for assigned work types.
- `work_roots` — the ordered list of `{name, absolute_path}` entries dispatch will export to the wrapper. Use these absolute paths verbatim when composing the prompt; do not re-derive them. Export is a launch fact, not a claim that every agent sandbox can widen to every path.
- `expected_report_path` — the absolute report path the prompt must name and the path Stage 4 will parse. It is variant-derived by the authoritative identity model: the completion slot (`reports/REPORT-NN-NNN.md`) for a task run, the independent review slot (`reports/REPORT-NN-NNN-review.md`) for an in-review task's review handoff. `expected_report_variant` names which one applies, and `completion_report_path` always names the preserved completion report a reviewer reads directly.
- `git_policy` — `pm_owns_product_branches`, `default_branch_pattern`, and `default_merge_strategy` for the product-repository git boundary, when `git_versioning` is true. When `git_versioning` is false this field is `null`, which also means product-repository branches are not PM-owned.
- `request_trace` — for a task assignment or in-review task, the normalized two-channel
  context and `preflight`. A missing prompt, stale binding, or missing/altered
  generated section makes the
  command fail closed. After writing an assignment prompt, rerun `handoff-packet`
  and require `request_trace.preflight.ok: true` before a manual handoff.
- `source_guidance` — the task/spec-owned source record. `valid` carries the only `deidentified_guidance` allowed into an assignee prompt. `invalid` fails this call with actionable blockers; do not bypass it through a manual launch. `not-applicable` and legacy `not-declared` add no source section.

For a planning-checkpoint review (which has no task file), resolve the same
artifact directly:

```text
cartopian review-context <project-root> --review-kind planning \
  --checkpoint PLAN-NNN --prompt <absolute-prompt-path>
```

Its `preflight.ok` must be `true` before either manual or automatic launch.

If the call exits non-zero (missing role block, unreadable config, task file not found), surface the error to the caller and return a blocked outcome; do not fall back to a manual read sequence.

Then, sourcing every value from the `handoff-packet` record above. Preparing the prompt is a **PM-performed** action: the PM has no raw `Write`/`Edit` tool (FR-002 containment), so author the prompt through the mediated writer rather than writing the file directly:

1. Author or update the prompt at the caller-provided absolute prompt path with the Core CLI (never a raw `Write`):

   ```
   cartopian write-prompt <project-root> --prompt-id <PROMPT-id> \
     --task <absolute-task-path> --content-file <body-path>

   # Review handoffs add the generated binding:
   cartopian write-prompt <project-root> --prompt-id <PROMPT-id> \
     --content-file <body-path> \
     --review-kind <planning|task-closure> <target arguments>
   ```

   `<PROMPT-id>` is the handoff's prompt identifier (`PROMPT-NN-NNN` for task handoffs, `PROMPT-PLAN-NNN` for planning-checkpoint reviews); the command resolves the allowlisted `prompts/` destination from it, so the PM supplies the id, never a free-form path. Re-issuing it overwrites the same prompt in place on a retry. Task assignment uses `--task <absolute-task-path>` and receives a generated pre-execution request comparison. Task review uses the same target with `--review-kind task-closure`. Planning review uses `--checkpoint PLAN-NNN` plus the applicable `--phase` / `--plan-ref`. The writer, not the PM, generates the bound request-comparison sections. A task-closure writer reads the preserved coder report, validates its task-completion publication shape, and binds it into the generated context by absolute path and SHA-256 content identity — alongside the independent expected review-report path (`reports/REPORT-NN-NNN-review.md`). The prompt never reproduces the report body: the reviewer reads completion evidence directly from the preserved artifact, which must remain in place and byte-identical throughout the review (preflight blocks on a missing or mutated completion report).
2. Ensure the prompt contains absolute paths — drawn from the record's `task_path` and `work_roots[].absolute_path` — for every file or directory the assignee is expected to read, modify, or produce.
3. Ensure the prompt names `expected_report_path` from the record as the absolute report path the assignee must write.
4. Ensure the prompt tells assignees not to move Cartopian task files, delete prompts, rewrite `STATE.md`, or perform PM lifecycle cleanup.
5. Ensure the prompt carries the foreground-completion instruction from `templates/PROMPT.md` § Completion report: completion-critical commands run in the foreground and are waited for before the report is written, background-task notifications cannot resume a non-interactive handoff, and an unfinishable handoff still publishes `Status: blocked` rather than exiting silently. The Foreground Completion rules are in `cartopian://protocol/CONVENTIONS/handoffs`. This is the prompt-side half of the fix for assignees that end a turn with work "still running"; the harness-side half is the optional Claude Code Stop hook registered by `scripts/install.py --claude-hook`.
6. When `source_guidance.outcome = valid`, paste its `deidentified_guidance` into the prompt and require matching `## Source evidence` in a complete report. This is a projection of the owner, not a second source authority.
7. For task review, first verify that the generated prompt record carries `captured_completion_evidence` and that its preflight is current. **Never delete the coder completion report** — it is preserved at its compatibility path (`reports/REPORT-NN-NNN.md`) as the reviewer's direct evidence source. When a prior review attempt left a stale review report or transient companions in the independent review slot, clear only that slot with the Core CLI before re-issuing the reviewer handoff:

   ```
   cartopian delete-report <expected-review-report-path>
   ```

   `delete-report` also removes the companion `<report-path>.status` wrapper status file when present, clearing any early-crash signal a prior handoff left in the same slot. Automatic `dispatch` repeats this bounded slot clear immediately before launch, after review-context preflight, so the launch cannot inherit a stale report or status even if a caller omitted the cleanup step.

Do not delete unrelated reports. Use `delete-report` only for the `expected_report_path` returned by `handoff-packet` (for an in-review task that is the review slot `reports/REPORT-NN-NNN-review.md`; the record's `completion_report_path` names the preserved coder report, which review cleanup never touches). A stale report at the expected path is unsafe because it can be mistaken for the current handoff result.

---

## Stage 2 - Issue The Handoff

Issuing the handoff is **PM-performed**. The contained PM has no shell or process-exec tool, so an automated launch goes through the mediated `cartopian dispatch` command — never a raw subprocess. Choose the path from the resolved role and handoff configuration:

- **Human role** — *operator-performed*: present the prompt path and expected report path to the operator.
- **Agent role without handoff config** — *operator-performed*: present the prompt path and expected report path to the operator for manual assignment.
- **Agent role without the applicable automatic-launch permission** — *operator-performed*: present the exact command for the operator to run (the PM does not launch it):
  ```text
  <agent> '<absolute prompt path>'
  ```

Every operator-performed/manual review launch first passes the context
preflight above. Manual describes who starts the reviewer; it is not an
request-comparison bypass.
- **Agent role with the applicable `task_run` or `task_review` permission in `auto_launch`** — *PM-performed*: launch the configured wrapper through the mediated dispatch command, only when the current automation policy allows it:

  ```
  cartopian dispatch <task-path> --role <role>
  ```

  `dispatch` is the FR-006 mediated launch. It consumes the canonical resolved role record, fails closed on a missing agent or permission, an unmapped/non-existent work root, or a missing prompt, and exports only resolved launch context: timeout, model, effort, work roots, project-root cwd, prompt/report paths, the expected report variant, and a fresh launch identity. After every preflight and before child creation it clears the bounded report/status slot and atomically publishes `state=running` for that launch. A child-creation failure removes its own running marker. It launches the resolved `agent` with the single absolute-prompt-path argv. There is no caller-supplied executable argument, and the wrapper never receives raw review, automation, capability, or schema policy.

- **Agent role with `planning_review` in `auto_launch`, report-path-only handoff** (no task file — e.g. a planning-checkpoint review) — *PM-performed*: launch through the prompt-keyed mediated dispatch below. When the permission is absent, use the operator-performed path.

  ```
  cartopian dispatch --prompt <absolute prompt path> --role <role>
  ```

  `--prompt` accepts only an allowlisted planning-checkpoint prompt slot (`<project-root>/prompts/PROMPT-PLAN-NNN.md`); the command derives the expected report path (`reports/REPORT-PLAN-NNN.md`), fails closed unless `planning_review` is permitted, and otherwise applies the same fail-closed gates, exports, and launch contract as the task-keyed form. Task-scoped handoffs never dispatch via `--prompt`; they dispatch by task path and require the applicable task permission.

The launched wrapper sets its single `CARTOPIAN_TIMEOUT` deadline from the resolved role launch timeout, with the protocol default of `60m` when that value is unset. It enforces the deadline at the OS level (`timeout`/`gtimeout` on POSIX, `Start-Process` + `WaitForExit` on PowerShell) and exits with exit `124` when the deadline elapses. Per FR-012 launch semantics, assignee CLIs run with cwd set to the cartopian project root (the registered project path). `dispatch` exports declared work-root absolute paths in canonical resolved order through `CARTOPIAN_WORK_ROOTS`; no export is present when the project declares none. The Codex wrapper widens `workspace-write` with those paths, and the Claude wrapper passes each through `--add-dir`. Gemini and Devin sandboxes have no per-path widening surface, so their wrappers warn that declared work roots may be unwritable while those sandboxes are active. Custom agents must state and honor their own equivalent behavior.

The project-root cwd and declared work-root access are filesystem launch facts. Declared work-root access does not grant PM lifecycle authority, relax the prompt's assignment scope, or transfer human-owned product-repository git actions to the assignee. Wrapper widening also does not replace harness capability enforcement.

Automated dispatch places the configured wrapper under the common launch-log retention supervisor. It continuously drains combined wrapper output while retaining only the normalized byte/line-bounded diagnostic representation from `cli/output_safety.py`; bytes outside that representation are discarded without signaling, terminating, failing, or otherwise constraining the assignee. Report observation and grace deadlines advance on timed pipe-readiness waits rather than output chunks, including while stdout stays open and silent. Once the outer supervisor observes a complete report, it publishes the current bounded log before the wrapper-compatible grace/reap path, atomically marks `retained_log_ready=true` in secondary status, continues draining during grace, and publishes the final bounded representation afterward. A matching automated status with `state=running` and retained storage pending briefly delays the report verdict until that marker flips; manual/report-only waits have no status requirement, and `state=exited` fails a pending marker open so supervisor loss cannot deadlock completion. Surface report/status metadata only and never open or summarize `<report-path>.launch.log` while waiting. The retention guarantee applies only to `guarantee_scope=retained-launch-log`; it is not an execution-output, artifact-size, report-size, model-context, or provider-private-context guarantee.

`dispatch` refuses to launch when the host cannot stay attached for the whole handoff. Launching is only half the job — the PM must still be waiting when the report lands (Stage 3) — and every MCP host caps a single `tools/call`, sometimes below the protocol's default 60m role timeout. When `roles.<role>.timeout` exceeds the resolved host budget, dispatch fails closed with a `[guard]` naming the mismatch and the ways out. Surface that message to the operator verbatim and return a blocked outcome; do not retry, do not lower the timeout on the operator's behalf, and do not fall back to periodic status checks. The remedies are to raise the host's ceiling, lower the role timeout, or hand this role off manually — `cartopian host-capability --role <role> --project <project-path>` reports the budget and the fit.

`dispatch` returns as soon as the wrapper is launched in the background — it does not block to completion and never reaps the child; the PM observes the result through Stage 3's wait primitive. Dispatch only one child handoff at a time. Do not start another handoff until this one has produced an accepted or blocked report outcome. A blocking wait also occupies the MCP server for its duration, so no other Cartopian tool call is serviced until it returns.

The successful dispatch is the launch event and consumes one `max_handoffs_per_run` unit. Only launches consume the handoff budget; Stage 3 wait calls and their observation slices consume none.

---

## Stage 3 - Wait For Completion

Detect completion with a Core CLI wait primitive rather than a hand-rolled timing loop, a repeated manual re-read of the report on a fixed cadence, or a "tell me when it's done" prompt to the operator. The wait commands are read-only filesystem observers: the **report file is the authoritative completion signal**, and the optional `<report-path>.status` wrapper file supplies early-exit evidence plus the live automated retained-publication boundary. Missing status means manual/report-only observation; `state=exited` cannot hold a complete report behind a pending retained marker. They never write, move, or launch anything. The PM removes that `<report-path>.status` file through `cartopian delete-report` at report-clear (Stage 1) and through `cartopian delete-report <report-path> --status-only` at task close (`skills/run-task.md` Stage 7), so it never outlives the handoff.

Both primitives are terminal by default: called without `--max-block`, one call blocks until a terminal outcome, bounded by the resolved handoff timeout as the absolute ceiling — one launch, one wait call, one result, no intermediate output. There is no wake, resume, or callback mechanism behind this; the blocking call *is* the mechanism, and Stage 2's dispatch already refused to launch if this host could not sustain it.

`--max-block` bounds a single nonterminal observation slice. Use it only when the host's `tools/call` ceiling cannot be raised and the operator has accepted sliced waiting — never as a default rhythm, and never as a substitute for the Stage 2 gate. Each slice costs context, so a raised host ceiling is always the better fix. Run `cartopian host-capability --role <role> --project <project-path>` to see the host's budget and whether this role fits inside it.

Choose the primitive by handoff kind:

- **Task-scoped handoff** (a task file exists — task assignment or task review): block on the task's expected report with

  ```
  cartopian wait-handoff <task-path> --role <role>
  ```

  It resolves the same expected report path Stage 1 named, honors the resolved role launch timeout as the absolute ceiling, and emits one NDJSON record carrying a `status` flag.

- **Report-path-only handoff** (no task file — for example a planning-checkpoint review): block on the report path directly with

  ```
  cartopian wait-report <report-path> --role <role>
  ```

  It watches the single report file and emits `accepted` (done), a `[guard]` failure (a report is present but not acceptable), `timeout` (the resolved ceiling elapsed first), or — only under an explicit `--max-block` slice — `still_running` (the requested budget elapsed first). With `--role` it honors the same resolved role launch timeout; otherwise the protocol default applies.

Interpret the emitted `status`:

- `done` / `accepted`, or common `classification` values `accepted`, `blocked`, `failed`, `changes-requested`, or `rejected`: a complete well-shaped report publication is present. Proceed to Stage 4 to route its actual verdict.
- An incomplete or temporarily malformed report while the current wrapper is still running is nonterminal. Path appearance alone is not completion; keep the same canonical wait active so publication can finish.
- `failed-to-parse`: the current wrapper exited and left a permanently malformed report (including content of the wrong expected variant, such as a stale coder report in a reviewer slot). Treat as blocked; preserve the prompt and report for inspection.
- `failed`: the current wrapper exited non-zero and no report appeared. `exited-without-report`: it exited cleanly without a report (the task-scoped legacy `status` field remains `failed`, while `classification` is precise). In either case the process is gone, so no report is coming; return a blocked outcome and preserve the prompt for a retry.
- `timeout`: the configured handoff ceiling elapsed before any terminal signal. A deadline kill is not successful completion evidence; return a blocked outcome.
- `still-running` / `still_running`: reachable only under an explicitly requested `--max-block` slice — that budget elapsed before the configured timeout, so the assignee may still be working. Treat this as a nonterminal internal observation boundary, not as a blocker, completion result, or operator-confirmation boundary. Routine nonterminal slices are silent and context-neutral: keep the initiated run active and re-invoke the same canonical wait primitive in another bounded slice without user-facing text or repeated state when no material state changed. User-facing output is allowed only for a terminal result, blocker, timeout/failure, meaningful new progress evidence, or a deliberately throttled long-running threshold. A wait is read-only and does not launch an assignee, so do not return to Stage 2, call `dispatch`, or consume another `max_handoffs_per_run` unit. Continue until the wait reports a terminal result or the configured deadline; do not request operator continuation merely because an observation slice ended.

The wrapper still enforces the wall-clock deadline at the OS level using `CARTOPIAN_TIMEOUT` (Stage 2); the wait command observes the result rather than imposing a separate PM-side deadline.

Return a blocked outcome when the wait reports `failed`, `failed-to-parse`, or `timeout`; when the expected report is missing, malformed, incomplete, internally inconsistent, or path-mismatched; when the report says `blocked`; or when the report requires operator judgment. A hard process stop or a missing/late/invalid report is not successful completion evidence.

---

## Stage 4 - Parse The Report

Use the Core CLI to parse the report at the expected absolute report path and validate it against the applicable variant in `cartopian://templates/REPORT.md`:

```
cartopian report-action <report-path>
```

`report-action` infers the report variant from filename and content; the supported variants are:

- Task completion for task handoffs.
- Review completion for task-review handoffs.
- Planning-review completion for planning-checkpoint review handoffs.

The emitted record is a strict superset of the legacy `parse-report` record: it carries the same `verdict`, `variant`, `report_path`, `status`, `review_verdict`, and source-evidence projection fields and adds routing fields such as `path_mismatch`, `target_task_status`, and `recommended_action`. The `path_mismatch` flag captures the AR-5 expected-path check directly; treat `path_mismatch = true` as `failed-to-parse` for the caller. For complete source-backed task reports, `source_evidence.outcome` must be `valid`; its actionable blockers explain any fail-closed result.

If the report is missing, malformed, inconsistent, uses unsupported values, or fails the expected-path check, treat it as `failed-to-parse`.

Treat `failed-to-parse` as blocked for the caller. Preserve the prompt and invalid report for operator inspection.

For review variants, `report-action` recomputes the prompt binding and emits
`request_alignment`. `approve` is actionable only when that record is
non-blocking. Drift or stale/missing evidence returns `failed-to-parse`;
generated historical unavailability remains explicit.

---

## Stage 5 - Return Outcome To Caller

Return one of these outcomes:

- `accepted`: the report is well-formed and actionable (task report, or review/planning-review report with `Verdict: approve`).
- `changes-requested`: review/planning-review report with `Verdict: request-changes`. Caller may iterate against the same artifacts.
- `rejected`: review/planning-review report with `Verdict: reject`. Caller must stop and surface to the operator.
- `blocked`: the report is well-formed and explicitly blocked, or the PM cannot proceed without operator judgment.
- `failed`: the report is well-formed and explicitly failed.
- `failed-to-parse`: the report is invalid, missing, or has an unrecognized/missing `## Verdict` body when the variant requires one.

For review and planning-review variants, the outcome above is derived from both the `Status:` header and the `## Verdict` body. The raw `approve | request-changes | reject` token is also returned as `review_verdict` for callers that want to branch on it directly.

For `accepted`, also return the parsed report kind, status, verdict when present, readiness-for-review when present, and the report path.

Do not move tasks, delete prompts, update reviews, or rewrite `STATE.md` unless the caller's skill explicitly assigns that lifecycle authority to this handoff step.

---

## Stage 6 - Automation Policy Boundary

When `confirmation = "each-handoff"`, return control to the operator only after the handoff reaches a terminal result and the caller processes that result. A nonterminal wait observation does not reach this boundary.

When `confirmation = "until-blocked"`, the initiated run remains active through every nonterminal wait observation. After this handoff reaches a terminal result and is fully processed, the caller may continue only until a blocker, failed report, review rejection, missing evidence, operator-required decision, phase boundary, or `max_handoffs_per_run` limit.

The policy permits sequential continuation only. It never permits concurrent child handoffs.
