# Skill: Run Handoff

Run one Cartopian handoff from prompt preparation through report
processing. This reusable workflow applies to task assignment, task
review, and planning-checkpoint review handoffs.

Use this skill when another Cartopian skill needs to hand work to a
human or configured agent and then interpret the completion report.

**Output:** A prepared prompt handoff, an accepted or blocked report
outcome, and no lifecycle movement beyond what the caller explicitly
owns.

---

## Prerequisites

- A Cartopian project directory exists.
- The caller knows the role being assigned.
- The caller knows the absolute prompt path to create or reuse.
- The caller knows the expected absolute report path.
- The caller knows the expected report variant from `templates/REPORT.md`.
- The caller knows which lifecycle action, if any, is allowed after the
  report is accepted.

---

## Stage 0 - Resolve Effective Configuration

1. Read the project `cartopian.toml`.
2. Read the workspace `cartopian.toml`, when present.
3. Resolve the role kind from project config first, then workspace
   config.
4. Resolve `[handoffs.<role>]` from project config first, then workspace
   config.
5. Resolve `[automation]` from project config first, then workspace
   config, then protocol defaults:
   - `confirmation = "each-handoff"`
   - `max_handoffs_per_run = 1`

If the role kind is `none`, stop and return a blocked outcome to the
caller. If the role kind is unset (`""`), ask the operator who should
perform the work.

---

## Stage 1 - Prepare Prompt And Report Slot

1. Write or update the prompt at the caller-provided absolute prompt
   path.
2. Ensure the prompt contains absolute paths for every file or directory
   the assignee is expected to read, modify, or produce.
3. Ensure the prompt names the expected absolute report path.
4. Ensure the prompt tells assignees not to move Cartopian task files,
   delete prompts, rewrite `STATE.md`, or perform PM lifecycle cleanup.
5. Delete any stale report at the expected report path before issuing
   the handoff.

Do not delete unrelated reports. A stale report at the expected path is
unsafe because it can be mistaken for the current handoff result.

---

## Stage 2 - Issue The Handoff

Use the resolved role and handoff configuration:

- Human role: present the prompt path and expected report path to the
  operator.
- Agent role without handoff config: present the prompt path and expected
  report path to the operator for manual assignment.
- Agent role with `auto_start = false`: present the exact command for
  the operator to run:
  ```text
  <agent> '<absolute prompt path>'
  ```
- Agent role with `auto_start = true`: launch the configured executable
  only when the current automation policy allows it.

Automated launch contract:

```text
<agent> <absolute prompt path>
```

Pass the prompt path as one argv argument. Use shell quoting only in
operator-facing command text.

Assignee CLIs run with cwd set to the parent of the workspace root, so
a single sandbox covers both the workspace (for the assignee's report
write-back) and the sibling target product repo named in the task's
`Target repo:` field. The shipped wrappers in `wrappers/` resolve and
`cd` to this directory automatically; custom agents must honor the
same convention.

Launch only one child handoff at a time. Do not start another handoff
until this one has produced an accepted or blocked report outcome.

---

## Stage 3 - Enforce Timeout And Stop Conditions

For PM-launched handoffs, apply the configured timeout. If omitted, use
`60m`.

Stop and return a blocked outcome when:

- The handoff times out.
- The process is killed or interrupted.
- The report is missing.
- The report is late after timeout.
- The report is malformed, incomplete, internally inconsistent, or
  path-mismatched.
- The report says `blocked`.
- The report requires operator judgment.

A killed terminal or process is not a graceful pause and is not
successful completion evidence.

---

## Stage 4 - Parse The Report

Read the report at the expected absolute report path.

Validate the report against the applicable variant in
`templates/REPORT.md`:

- Task completion variant for task handoffs.
- Review completion variant for task-review handoffs.
- Planning-review completion variant for planning-checkpoint review
  handoffs.

Reject the report as `failed-to-parse` when it is missing, malformed,
incomplete, internally inconsistent, uses unsupported status or verdict
values, or contradicts the expected task/review/prompt/report paths.

Treat `failed-to-parse` as blocked for the caller. Preserve the prompt
and invalid report for operator inspection.

---

## Stage 5 - Return Outcome To Caller

Return one of these outcomes:

- `accepted`: the report is well-formed and actionable.
- `blocked`: the report is well-formed and explicitly blocked, or the
  PM cannot proceed without operator judgment.
- `failed`: the report is well-formed and explicitly failed.
- `failed-to-parse`: the report is invalid or missing.

For `accepted`, also return the parsed report kind, status, verdict when
present, readiness-for-review when present, and the report path.

Do not move tasks, delete prompts, update reviews, or rewrite `STATE.md`
unless the caller's skill explicitly assigns that lifecycle authority to
this handoff step.

---

## Stage 6 - Automation Policy Boundary

When `confirmation = "each-handoff"`, return control to the operator
after the caller processes the handoff result.

When `confirmation = "until-blocked"`, the caller may continue only
after this handoff is fully processed and only until a blocker, failed
report, review rejection, missing evidence, operator-required decision,
phase boundary, or `max_handoffs_per_run` limit.

The policy permits sequential continuation only. It never permits
concurrent child handoffs.
