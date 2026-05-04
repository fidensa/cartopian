# REPORT-NN-NNN

This template is the canonical field schema for Cartopian handoff
reports. Use exactly one variant: task completion, review completion, or
planning-review completion.

Status: <complete | blocked | failed>

## Identity

- Task ID: <TASK-NN-NNN>
- Prompt path: <absolute path to the prompt file>
- Task path: <absolute path to the task file>
- Repo path: <absolute path to the target repository>

## Files changed

- <path/to/file.ext> — <brief description of change>

## Test evidence

<When test gate was `required`:
- Red test evidence: <pointer to the failing test before implementation>
- Green test evidence: <pointer to the passing test after implementation>

When test gate was `n/a`:
- n/a — <reason>>

## Commit / PR

- Commit SHA: <SHA or n/a>
- PR URL: <URL or n/a>

## Remaining risks

<Any known risks, edge cases, or follow-up work.>

## Ready for review

<yes | no>

---

## Review completion variant

Use this section instead of the above when reporting on a review handoff.

# REPORT-NN-NNN

Status: <complete | blocked | failed>

## Identity

- Review ID: <REVIEW-NN-NNN>
- Prompt path: <absolute path to the prompt file>
- Review file path: <absolute path to the review file>

## Evidence reviewed

<What was inspected: code, specs, test results, etc.>

## Verdict

<approve | request-changes | reject>

## Blocking findings

<List blocking findings, or "none.">

---

## Planning-review completion variant

Use this section instead of the above when reporting on a planning-
checkpoint review handoff (e.g., requirements review, plan review).

# REPORT-PLAN-NNN-slug

Status: <complete | blocked | failed>

## Identity

- Review ID: <REVIEW-PLAN-NNN-slug>
- Prompt path: <absolute path to the prompt file>
- Review file path: <absolute path to the review file>

## Evidence reviewed

<What was inspected: requirements, plan, phases, tasks/specs, etc.>

## Verdict

<approve | request-changes | reject>

## Blocking findings

<List blocking findings, or "none.">

---

> **Redaction reminder:** Do not include API keys, credentials, tokens,
> private connection strings, or comparable sensitive values in this
> report. Redact before writing.
