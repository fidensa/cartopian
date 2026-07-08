# REPORT-NN-NNN

This template is the canonical field schema for Cartopian handoff reports. Use exactly one variant: task completion, review completion, or planning-review completion.

Status: <complete | blocked | failed>

## Identity

- Work root: <name | name, name | n/a>

The `Work root:` value carries names only — the same names declared in the prompt's `Work root:` field, drawn from `[project].work_roots`. The PM resolves names to absolute paths via `cartopian resolve-config` when it needs them. Write the report to the report path you were given; Cartopian links it back to its task by that filename — you do not record any identifier here.

## Files changed

- <path/to/file.ext> — <brief description of change>

## Deliverable

- <absolute path to the durable work product this task produced, or n/a>

When this task produced a durable document (research, design, evaluation) rather than code, name its path here. The substantive work product lives in that file, not in this report — this report only summarizes what was done. When the prompt gave no Deliverable path because the durable copy must land inside the governing project, leave this `n/a` and paste the work product in `## Deliverable content` below; the PM persists it.

## Deliverable content

<Only when the prompt directed you to return the work product inline (no Deliverable path was given). Paste the complete document here so the PM can persist it to its durable location. Omit this section otherwise.>

## Test evidence

<When evidence gate was `required`:

- Red test evidence: <pointer to the failing test before implementation>
- Green test evidence: <pointer to the passing test after implementation>

When evidence gate was `n/a`:

- n/a — <reason>>

## Commit / PR

- Commit SHA: <SHA or n/a> Use `n/a` when the project uses PM-owned product-repo git; the PM stages and commits the task changes after this report lands.
- PR URL: <URL or n/a> Use `n/a` when the project uses PM-owned product-repo git; the PM creates the PR after this report lands.

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

Use this section instead of the above when reporting on a planning- checkpoint review handoff (e.g., requirements review, plan review).

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

> **Redaction reminder:** Do not include API keys, credentials, tokens, private connection strings, or comparable sensitive values in this report. Redact before writing.
