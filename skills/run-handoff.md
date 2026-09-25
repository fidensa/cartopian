# Skill: Run Handoff

Run one Cartopian handoff from prompt preparation through report processing. This reusable workflow applies to task assignment, task review, and planning-checkpoint review handoffs. Use it when another Cartopian skill needs to hand work to a human or configured agent and then interpret the completion report.

**Output:** A prepared prompt handoff, an accepted or blocked report outcome, and no lifecycle movement beyond what the caller explicitly owns.

**Protocol reference:** The handoff contract is `cartopian://protocol/CONVENTIONS/handoffs`; its H2 preamble (assignment contract, role launch facts, timeout authority) is readable alone at `cartopian://protocol/CONVENTIONS/handoffs/preamble`; role declaration rules are `cartopian://protocol/CONVENTIONS/roles`. The full `cartopian://protocol/CONVENTIONS` remains the authoritative contract; do not load it whole — and do not load the whole handoffs section — for a handoff. Each stage names the sub-slice that governs it — read it at that stage.

---

## Prerequisites

The caller supplies: a Cartopian project directory; the role being assigned; the absolute prompt path to create or reuse; the expected absolute report path and its variant (`cartopian://templates/REPORT.md`); and which lifecycle action, if any, is allowed after the report is accepted. The absolute project path is known (from `cartopian discover-projects`) so `cartopian resolve-config <project-path>` can run.

---

## Stage 0 - Resolve Effective Configuration

Run `cartopian resolve-config <project-path>` and read: the resolved `[roles.<role>]` records (description, effective grants, assigned work types, `launch`, `auto_launch`, attribution) and the `[automation]` policy (default `run_boundary = "handoff-complete"`; `max_handoffs_per_run` is reported only under `run_boundary = "handoff-budget"` and is `null` otherwise).

- Role not declared in `[roles]`: stop and return a blocked outcome ("role not declared in `[roles]`; declare it or assign a different role").
- Role declared but `launch.agent` unset: a manual (human) role — continue through Stage 1 unchanged (`handoff-packet` serves it with `launch.agent: null`), then take the operator-performed branch of Stage 2; the operator handles execution against the prompt and report paths.

---

## Stage 1 - Prepare Prompt And Report Slot

1. `cartopian handoff-packet <task-path> --role <role>` — one read-only record. Consume: `role_description`; `launch` (agent, model, effort, timeout); `auto_launch`; `work_roots` (ordered `{name, absolute_path}` — use verbatim, never re-derive); `existing_deliverable_input` and `dependency_deliverable_inputs` (see step 5); `input_payload_audit` (non-empty `problems` fails both manual handoff and `dispatch` closed); `expected_report_path` and `expected_report_variant` (the completion slot `reports/REPORT-NN-NNN.md` for a task run; the independent review slot `reports/REPORT-NN-NNN-review.md` for a review handoff — `completion_report_path` always names the preserved coder report); `git_policy` (`null` when git versioning is off, which also means product-repository branches are not PM-owned); `request_trace` (after writing an assignment prompt, rerun and require `request_trace.preflight.ok: true` before a manual handoff); `source_guidance` (`valid` carries the only `deidentified_guidance` allowed into an assignee prompt; `invalid` fails closed — never bypass via manual launch). A non-zero exit: surface the error and return a blocked outcome; no manual read fallback.
2. For a planning-checkpoint review (no task file), resolve the same artifact with `cartopian review-context <project-root> --review-kind planning --checkpoint PLAN-NNN --prompt <absolute-prompt-path>`; its `preflight.ok` must be `true` before either manual or automatic launch.
3. Author the prompt through the mediated writer, never a raw write (the contained PM has no `Write`/`Edit` tool):

   ```
   # Task assignment: compose deterministically, then write the verified result:
   cartopian compose-assignment-prompt <absolute-task-path> --role <role>
   cartopian write-prompt <project-root> --prompt-id <PROMPT-id> \
     --task <absolute-task-path> --composed-file <saved-compose-record>

   # Review handoffs author the body and add the generated binding:
   cartopian write-prompt <project-root> --prompt-id <PROMPT-id> \
     --content-file <body-path> \
     --review-kind <planning|task-closure> <target arguments>
   ```

   `<PROMPT-id>` is `PROMPT-NN-NNN` for task handoffs, `PROMPT-PLAN-NNN` for planning reviews; the command resolves the allowlisted destination and overwrites in place on retry. Task assignment bodies are composed, never hand-assembled — the composer resolves every input and validates fail-closed; steps 4–7 describe what the composed prompt already carries (verify, don't hand-assemble; they are PM-authored content only for review prompts). The writer generates the bound request-comparison sections; a task-closure writer binds the preserved coder report by absolute path and SHA-256 identity alongside the independent expected review-report path — the prompt never reproduces the report body, and the completion report must stay byte-identical throughout the review.
4. Verify the prompt names absolute paths for everything the assignee reads, modifies, or produces (from `task_path` and `work_roots[].absolute_path`); names `expected_report_path` as the assignee's required report destination; tells assignees not to move Cartopian task files, delete prompts, rewrite `STATE.md`, or perform PM lifecycle cleanup; and carries the foreground-completion instruction from `templates/PROMPT.md` § Completion report (completion-critical commands run in the foreground and are waited for; an unfinishable handoff still publishes `Status: blocked` — `cartopian://protocol/CONVENTIONS/handoffs/foreground-completion`).
5. When `source_guidance.outcome = valid`, the prompt carries its `deidentified_guidance` and requires `## Source evidence` in a complete report (the non-empty subset of supplied sources actually used; no sources outside the guidance). When `existing_deliverable_input.required` is true, the writer materializes the complete resource text as a machine-created typed payload in `## Existing deliverable input` — never paste it by hand; a hand-authored payload declaration is refused. The same applies per `dependency_deliverable_inputs` record into `## Upstream contract input`; a `dependency-deliverable-missing` record means the upstream deliverable was never persisted — persist it with `cartopian write-resource` before any dispatch. Rerun `handoff-packet` until every record's `ok` is `true` and `input_payload_audit.ok` is `true`.
6. Paste the machine-generated skeleton from `cartopian report-skeleton <task-path>` into the prompt instead of the full report template (for a review handoff, also the returned `review_file_skeleton`); the assignee supplies only substantive evidence, findings, and verdicts.
7. Keep prompt volume proportional: only what this assignment needs — the deidentified spec projection when a spec governs, the applicable skeleton, the selected practice-pack body only when the selector returned `selected` for this task, the judgment body only when a card activated. A rework prompt is a correction, not a re-assignment: the specific findings or failed checks with recovery text and affected excerpts, not the full original documents again.
8. For task review: verify the generated prompt record carries `captured_completion_evidence` with a current preflight. Never delete the coder completion report — it is the reviewer's direct evidence source at `reports/REPORT-NN-NNN.md`. Clear only a stale independent review slot before re-issuing: `cartopian delete-report <expected-review-report-path>` (it also removes the companion `.status` file; automatic `dispatch` repeats this bounded clear before launch). Never use `delete-report` on anything but the `expected_report_path` for this handoff.

### Critical adversarial review context

When a retained risk result has `band = critical` and its `independent-challenge` expectation is being fulfilled, construct the challenge input afresh:

```
cartopian adversarial-review-context <project-root> \
  --artifact <absolute-delivered-artifact-file> \
  --governing-contract <absolute-governing-contract-file> \
  --risk-result '<exact classify-risk JSON>'
```

The artifact is the delivered work, not the producer's report or summary; the governing contract carries the decisive requirements, evidence, derived expectations, and authority; both must resolve inside the project root or a configured work root. Provide the returned `context` object (exactly `artifact` and `governing_contract`) as the fresh challenge input, without the author's conclusion or unrelated project history. Independence means the challenger did not produce the decisive work — it selects no fixed role, panel, model, or reviewer count; configured review and launch policy still decide who receives the handoff. If policy does not satisfy the derived expectation, return the difference to the operator as a gate. If the artifact cannot be one bounded file, fail closed and obtain an operator-approved bounded representation first.

---

## Stage 2 - Issue The Handoff

Governing sub-slices: `cartopian://protocol/CONVENTIONS/handoffs/launch-directory` and `cartopian://protocol/CONVENTIONS/handoffs/work-roots`.

Issuing is PM-performed; an automated launch goes through the mediated `cartopian dispatch`, never a raw subprocess. Choose by resolved role and permission:

- **Human role**, **agent role without handoff config**, or **agent role without the applicable auto-launch permission** — operator-performed: present the prompt path and expected report path (and, for the last case, the exact command `<agent> '<absolute prompt path>'`). Every manual review launch still passes the context preflight first; manual describes who starts the reviewer, not a request-comparison bypass.
- **Agent role with the applicable `task_run` or `task_review` permission** — PM-performed, when automation policy allows:

  ```
  cartopian dispatch <task-path> --role <role>
  ```

  `dispatch` consumes the canonical resolved role record; fails closed on a missing agent or permission, unmapped work root, or missing prompt; clears the bounded report/status slot and publishes `state=running` after every preflight; and launches the resolved `agent` with the single absolute-prompt-path argv. The wrapper never receives raw review, automation, capability, or schema policy.
- **Agent role with `planning_review`, report-path-only handoff** (no task file) — PM-performed: `cartopian dispatch --prompt <absolute prompt path> --role <role>`. `--prompt` accepts only an allowlisted planning-checkpoint prompt slot and derives `reports/REPORT-PLAN-NNN.md`; task-scoped handoffs never dispatch via `--prompt`.

Launch facts: the wrapper sets its single `CARTOPIAN_TIMEOUT` deadline from the resolved role launch timeout (protocol default of `60m` when unset), enforces it at the OS level, and exits with exit `124` when it elapses. Assignee CLIs run with cwd set to the cartopian project root; `dispatch` exports declared work-root absolute paths in canonical order through `CARTOPIAN_WORK_ROOTS`. The Codex wrapper widens `workspace-write` with those paths; the Claude and Antigravity wrappers pass each through `--add-dir`; the Devin sandbox has no per-path widening surface, so its wrapper warns that declared work roots may be unwritable. Custom agents must state and honor their own equivalent behavior. Declared work-root access is a filesystem launch fact: it does not grant PM lifecycle authority, relax the prompt's assignment scope, or transfer human-owned product-repository git actions to the assignee.

Automated dispatch places the wrapper under the launch-log retention supervisor (`cartopian://protocol/CONVENTIONS/handoffs/automated-output-safety`): it retains only a bounded diagnostic representation of wrapper output, publishes the launch-log snapshot and `state=exited` status only after the child is provably gone, and never constrains the assignee. Surface report/status metadata only; never open or summarize `<report-path>.launch.log` while waiting.

`dispatch` refuses to launch when the host cannot stay attached for the whole handoff: when `roles.<role>.timeout` exceeds the resolved host budget it fails closed with a `[guard]` naming the mismatch. Surface that message verbatim and return a blocked outcome; do not retry or lower the timeout on the operator's behalf. The remedies are to raise the host ceiling, lower the role timeout, or hand off manually — `cartopian host-capability --role <role> --project <project-path>` reports the budget and fit.

`dispatch` returns as soon as the wrapper is launched; the PM observes the result through Stage 3. Dispatch one child handoff at a time — do not start another until this one produced an accepted or blocked outcome (a blocking wait also occupies the MCP server for its duration). The successful dispatch is the launch event and consumes one `max_handoffs_per_run` unit. Only launches consume the handoff budget; Stage 3 waits and their observation slices consume none.

---

## Stage 3 - Wait For Completion

Governing sub-slice: `cartopian://protocol/CONVENTIONS/handoffs/waiting-for-completion`.

The wait commands are read-only filesystem observers: the report file is the authoritative completion signal; the optional `<report-path>.status` wrapper file supplies early-exit evidence and the automated retained-publication boundary. Both primitives are terminal by default: called without `--max-block`, one call blocks to a terminal outcome bounded by the resolved handoff timeout — one launch, one wait call, one result. The blocking call is the whole mechanism, and Stage 2's dispatch already refused to launch if this host could not sustain it. `--max-block` bounds a single nonterminal observation slice — only for a host `tools/call` ceiling that cannot be raised, never a default rhythm (`cartopian host-capability` shows whether the role fits).

Choose by handoff kind:

- **Task-scoped** (task assignment or task review): `cartopian wait-handoff <task-path> --role <role>` — resolves the same expected report path Stage 1 named and honors the resolved role launch timeout as the absolute ceiling.
- **Report-path-only** (e.g. planning-checkpoint review): `cartopian wait-report <report-path> --role <role>` — watches the single report file under the same timeout resolution.

The wrapper still enforces the wall-clock deadline via `CARTOPIAN_TIMEOUT`; the wait observes the result rather than imposing a separate PM-side deadline.

Interpret the emitted `status`:

- `done` / `accepted` (or a terminal `classification`): a complete publication is present and `report_content_identity` names its final bytes. Keep that identity — Stage 4 binds parsing to it.
- An incomplete report while the wrapper still runs — or a complete report still `state=running` — is nonterminal; path appearance alone is not completion. Keep the same canonical wait active.
- `failed-to-parse`: the wrapper exited leaving a permanently malformed report (including a wrong-variant report in the slot). Treat as blocked; preserve prompt and report.
- `failed` / `exited-without-report`: the process is gone and no report is coming. Return a blocked outcome; preserve the prompt for retry.
- `timeout`: the ceiling elapsed first; a deadline kill is not completion evidence. Return a blocked outcome.
- `still-running` / `still_running`: reachable only under an explicit `--max-block` slice — a nonterminal internal observation boundary, not a blocker or completion. Routine nonterminal slices are silent and context-neutral: keep the initiated run active and re-invoke the same canonical wait primitive in another bounded slice without user-facing text or repeated state when no material state changed. User-facing output is allowed only for a terminal result, blocker, timeout/failure, meaningful new progress evidence, or a deliberately throttled long-running threshold. A wait is read-only and does not launch an assignee — do not return to Stage 2, call `dispatch`, or consume another `max_handoffs_per_run` unit, and do not request operator continuation merely because a slice ended.

Return a blocked outcome when the wait reports `failed`, `failed-to-parse`, or `timeout`; when the expected report is missing, malformed, incomplete, inconsistent, or path-mismatched; when the report says `blocked`; or when operator judgment is required.

---

## Stage 4 - Parse The Report

Parse at the expected absolute report path, bound to the publication the wait accepted:

```
cartopian report-action <report-path> --expected-identity <report_content_identity from the wait record>
```

If the bytes no longer match, it refuses with `verdict: identity-mismatch` — re-run the canonical wait and use the identity it returns; never re-invoke without `--expected-identity` to get past the refusal. The record carries `verdict`, `variant`, `status`, `review_verdict`, `path_mismatch` (true — treat as `failed-to-parse`), `target_task_status`, `recommended_action`, the source-evidence projection (`source_evidence.outcome` must be `valid` for complete source-backed reports), the bounded `pm_summary`, and — for review variants — `request_alignment` (approve is actionable only when non-blocking) and the bounded `review_projection`. Route on the projections instead of re-reading artifacts.

When a written report returns `failed-to-parse`, enumerate every defect: `cartopian validate-report <report-path> --expected-identity <identity>` (its record carries the `report_content_identity` the correction below requires). Route each failed check by `failure_class`:

- `mechanical` — a schema or transcription defect. Correct in place, never via a correction handoff or raw edit: `cartopian correct-report <report-path> --expected-identity <report_content_identity from validate-report> --corrected-file <complete corrected body>`. The command permits only the exact edit operations the failed checks name, preserves everything else byte-for-byte, refuses when any `substantive`/`missing-input` finding is present, and requires full validation before writing. A refusal means the defect is not mechanical — route it by its real class. Never re-send the full assignment for a mechanical failure, and never copy report bytes into a correction prompt.
- `missing-input` — a governing artifact is absent or invalid. A readiness defect, not the assignee's failure: repair the input, rerun readiness/preflight, then re-dispatch.
- `substantive` — a recorded judgment (request drift, an unverified decisive claim). Route through the configured review loop or the operator; never "fix" it by editing the report.

Treat `failed-to-parse` as blocked only when no route applies or the routed correction fails; preserve the prompt and invalid report for inspection.

---

## Stage 5 - Return Outcome To Caller

Return one of: `accepted` (well-formed and actionable — task report, or review report with `Verdict: approve`); `changes-requested` (`request-changes` — caller may iterate); `rejected` (`reject` — stop and surface to the operator); `blocked` (explicitly blocked, or operator judgment needed); `failed` (explicitly failed); `failed-to-parse` (invalid, missing, or unrecognized verdict). For review variants the outcome derives from both the `Status:` header and the `## Verdict` body; the raw token is also returned as `review_verdict`. For `accepted`, also return the parsed kind, status, verdict, readiness value, and report path. Do not move tasks, delete prompts, update reviews, or rewrite `STATE.md` unless the caller's skill explicitly assigns that lifecycle authority to this handoff step.

### Failure routing

Every non-accepted outcome has exactly one route:

- **Mechanical/schema failure** — the hash-bound in-place `cartopian correct-report` fix; never a rework assignment or a correction handoff carrying report bytes.
- **Identity mismatch** — the publication changed after observation: re-run the canonical wait and route the identity it returns.
- **Missing assignment input** — fix the input, rerun readiness and preflight before any dispatch; never launch an assignee against an input it cannot read.
- **Substantive defect** — the configured review loop.
- **Operator ambiguity** — stop and return the question to the operator.

---

## Stage 6 - Automation Policy Boundary

The resolved `run_boundary` decides when the initiated run ends; it never decides whether one starts and never widens what the caller may do.

`run_boundary = "handoff-complete"`: return control to the operator only after the handoff reaches a terminal result and the caller processes it — a nonterminal wait observation does not reach this boundary. `run_boundary = "handoff-budget"`: the initiated run stays active through every nonterminal observation; after a fully processed terminal result the caller continues only until a blocker, failed report, review rejection, missing evidence, operator-required decision, phase boundary, or the `max_handoffs_per_run` limit. `run_boundary = "task-complete"`: the run stays active for the one task bound at initiation and the caller continues only activities that apply to that same bound task — it consumes and honors no handoff budget, and it returns control when that task reaches `done`, before anything belonging to another task. Every boundary permits sequential continuation only, never concurrent child handoffs, and every existing fail-closed stop condition still ends the run under all three.
