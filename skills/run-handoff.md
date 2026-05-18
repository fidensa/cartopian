# Skill: Run Handoff

Run one Cartopian handoff from prompt preparation through report processing. This reusable workflow applies to task assignment, task review, and planning-checkpoint review handoffs.

Use this skill when another Cartopian skill needs to hand work to a human or configured agent and then interpret the completion report.

**Output:** A prepared prompt handoff, an accepted or blocked report outcome, and no lifecycle movement beyond what the caller explicitly owns.

---

## Prerequisites

- A Cartopian project directory exists.
- The caller knows the role being assigned.
- The caller knows the absolute prompt path to create or reuse.
- The caller knows the expected absolute report path.
- The caller knows the expected report variant from `templates/REPORT.md`.
- The caller knows which lifecycle action, if any, is allowed after the report is accepted.
- The absolute project path is known (selected from `cartopian discover-projects`) so `cartopian resolve-config <project-path>` can be run.

---

## Stage 0 - Resolve Effective Configuration

Use the Core CLI to resolve effective roles, handoff targets, automation policy, work roots, and relevant `[git]` keys for the selected project absolute path:

```
cartopian resolve-config <project-path>
```

If you do not have the absolute path, run `cartopian discover-projects` and select the entry; use its `path` field.

Read from the resolved output:

- The `[roles]` table (name -> one-line description string).
- The `[handoffs.<role>]` block for the role being assigned.
- The `[automation]` policy, defaulting to `confirmation = "each-handoff"` and `max_handoffs_per_run = 1` when unset.

If the role being assigned is not declared in the resolved `[roles]` table, stop and return a blocked outcome to the caller ("role not declared in `[roles]`; declare it or assign a different role").

If the role is declared in `[roles]` but no `[handoffs.<role>]` block is configured, return a manual-dispatch outcome to the caller: the PM surfaces the prompt path and expected report path, and the operator handles execution.

---

## Stage 1 - Prepare Prompt And Report Slot

1. Write or update the prompt at the caller-provided absolute prompt path.
2. Ensure the prompt contains absolute paths for every file or directory the assignee is expected to read, modify, or produce.
3. Ensure the prompt names the expected absolute report path.
4. Ensure the prompt tells assignees not to move Cartopian task files, delete prompts, rewrite `STATE.md`, or perform PM lifecycle cleanup.
5. Remove any stale report at the expected report path using the Core CLI before issuing the handoff:

   ```
   cartopian delete-report <report-path>
   ```

Do not delete unrelated reports. Use `delete-report` only for the expected report path. A stale report at the expected path is unsafe because it can be mistaken for the current handoff result.

---

## Stage 2 - Issue The Handoff

Use the resolved role and handoff configuration:

- Human role: present the prompt path and expected report path to the operator.
- Agent role without handoff config: present the prompt path and expected report path to the operator for manual assignment.
- Agent role with `auto_start = false`: present the exact command for the operator to run:
  ```text
  <agent> '<absolute prompt path>'
  ```
- Agent role with `auto_start = true`: launch the configured executable only when the current automation policy allows it.

Automated launch contract:

```text
CARTOPIAN_TIMEOUT=<duration> <agent> <absolute prompt path>
```

Pass the prompt path as one argv argument. Use shell quoting only in operator-facing command text. Set `CARTOPIAN_TIMEOUT` in the launch environment to the resolved `[handoffs.<role>].timeout` value (e.g. `30m`, `2h`); if the field is absent, the wrapper applies the protocol default of `60m`. The shipped wrappers enforce this deadline at the OS level (`timeout`/`gtimeout` on POSIX, `Start-Process` + `WaitForExit` on PowerShell) and exit `124` when the deadline elapses.

Per FR-012 launch semantics, assignee CLIs run with cwd set to the cartopian project root (the registered project path). Access grants cover the union of the project root and any declared work-root absolute paths resolved via `resolve-config`. The shipped wrappers in `wrappers/` resolve the project root and apply access grants automatically; custom agents must honor the same convention.

Launch the handoff as a background subprocess (do not impose a foreground tool-call deadline shorter than the configured timeout). Launch only one child handoff at a time. Do not start another handoff until this one has produced an accepted or blocked report outcome.

---

## Stage 3 - Wait For Completion

The dispatch is OS-bounded by the wrapper using `CARTOPIAN_TIMEOUT`. Wait for the wrapper subprocess to exit. Do not poll status repeatedly, do not impose a separate PM-side deadline, and do not intervene before the wrapper completes on its own. The wrapper is the watchdog; the PM is the consumer of its result.

The wrapper will exit in one of two ways:

- **Natural exit**: the assignee finished. Exit code is the assignee's exit code.
- **Deadline exit**: the OS killed the assignee at the wall-clock limit. Exit code is `124` (or platform equivalent).

After the wrapper exits, check the expected report path before interpreting the outcome — the assignee may have finished writing the report just before a deadline kill, so report presence is decided by filesystem, not exit code.

Return a blocked outcome when:

- The wrapper exited non-zero (including code `124` for deadline) and the expected report is missing or invalid.
- The expected report file is missing after wrapper exit.
- The expected report is malformed, incomplete, internally inconsistent, or path-mismatched.
- The expected report says `blocked`.
- The expected report requires operator judgment.

A deadline kill, hard process stop, or missing/late/invalid report is not successful completion evidence.

---

## Stage 4 - Parse The Report

Use the Core CLI to parse the report at the expected absolute report path and validate it against the applicable variant in `templates/REPORT.md`:

```
cartopian parse-report <report-path> [--variant <variant>]
```

Variants:

- Task completion for task handoffs.
- Review completion for task-review handoffs.
- Planning-review completion for planning-checkpoint review handoffs.

Caller-side path-consistency check (per AR-5): verify that the parsed report’s embedded paths, when present, match the expected task, prompt, review, and report paths the caller supplied. If the report is missing, malformed, inconsistent, uses unsupported values, or fails the expected-path check, treat it as `failed-to-parse`.

Treat `failed-to-parse` as blocked for the caller. Preserve the prompt and invalid report for operator inspection.

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

When `confirmation = "each-handoff"`, return control to the operator after the caller processes the handoff result.

When `confirmation = "until-blocked"`, the caller may continue only after this handoff is fully processed and only until a blocker, failed report, review rejection, missing evidence, operator-required decision, phase boundary, or `max_handoffs_per_run` limit.

The policy permits sequential continuation only. It never permits concurrent child handoffs.
