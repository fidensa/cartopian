# REPORT-NN-NNN

This template is the canonical field schema for Cartopian handoff reports. Use exactly one variant: task completion, review completion, or planning-review completion. Task completion has a neutral core; specialized file, document, test, and git sections are included only when they fit the work.

Status: <complete | blocked | failed>

## Identity

- Work root: <name | name, name | n/a>

The `Work root:` value carries names only — the same names declared in the prompt's `Work root:` field, drawn from `[project].work_roots`. The PM resolves names to absolute paths via `cartopian resolve-config` when it needs them. Write the report to the report path you were given; Cartopian links it back to its task by that filename — you do not record any identifier here.

## Completion evidence

<Concrete evidence that the outcome exists: confirmation number, approval, inspection result, published URL, completed checklist, file path, validation output, photograph, meeting decision, or another verifiable observation.>

## Source evidence

<Include for a source-backed task. Omit when the task's resolved source guidance is `n/a` or legacy-not-declared. Use the same three-subsection record as the governing guidance, naming the non-empty subset of sources and applicable contexts actually used. Every evidenced source identity/context must come from the supplied guidance; omit guidance sources that were not applied. A decisive claim may not remain unverified in a complete report.>

### Authoritative sources

- Identity: <stable source title or locator>; Applicable context: <effective date, publication date, edition, revision, or version>; Status: <current | stale | unknown>; Scope: <claims or decisions governed>

### Conflict resolution

- Status: <none | resolved | unresolved>; Rule: <precedence rule or named decision authority>; Decision: <applied resolution | n/a>

### Unverified claims

- none

Or:

- Claim: <unverified claim>; Decisiveness: <decisive | non-decisive>; Missing: <authority or evidence>; Consequence: <consequence of proceeding>; Next: <decision or proof required>

## Files changed

<Optional software/file-work evidence. Omit when the task did not change files.>

- <path/to/file.ext> — <brief description of change>

## Deliverable

- <absolute path to the durable work product this task produced, or n/a>

Optional document-work evidence. When this task produced a durable document (research, design, evaluation), name its path here. The substantive work product lives in that file, not in this report. When the prompt gave no Deliverable path because the durable copy must land inside the governing project's `resources/` directory, leave this `n/a` and paste the work product in `## Deliverable content` below; the PM persists it. Omit this section when no durable document was promised.

## Deliverable content

<Only when the prompt directed you to return the work product inline (no Deliverable path was given). Paste the complete document here so the PM can persist it to its durable location. Omit this section otherwise.>

## Test evidence

<Optional specialized evidence for software or another executable check. When evidence gate was `required`:

- Red test evidence: <pointer to the failing test before implementation>
- Green test evidence: <pointer to the passing test after implementation>

When evidence gate was `n/a`:

- n/a — <reason>>

## Risk-scaled evidence

<Include when the assignment carried a `## Risk result` section. Record the derived band and the evidence and contingency expectations verbatim, then point to the direct evidence that satisfies each. Record the operator-gate disposition and any stop condition or recovery owner the result requires. Do not claim that this section changed configured review policy, roles, or launch authority.>

- Band: <routine | bounded | consequential | critical>
- Evidence expectation: <identifier>; Evidence: <direct proof>
- Operator gate: <identifier>; Disposition: <authority or approval evidence>
- Contingency expectation: <identifier>; Evidence: <recovery action, trigger/owner, or evidenced stop condition>

## Commit / PR

<Optional software git-flow section. Omit when the work uses no repository or commit/PR workflow.>

- Commit SHA: <SHA or n/a> Use `n/a` when the project uses PM-owned product-repo git; the PM stages and commits the task changes after this report lands.
- PR URL: <URL or n/a> Use `n/a` when the project uses PM-owned product-repo git; the PM creates the PR after this report lands.

## Remaining risks

<Any known risks, edge cases, or follow-up work.>

## Ready to close

<yes | no>

`Ready for review` is accepted as a legacy heading. Under required task-closure review, `yes` routes the task into review; when task review is off, it routes the accepted task toward direct closure.

---

## Review completion variant

Use this section instead of the above when reporting on a task-closure review handoff. Task-review completion has its own report identity — write it to the exact `REPORT-NN-NNN-review.md` path you were given, never to the preserved task-completion report's `REPORT-NN-NNN.md` path. Read the coder's completion evidence directly from the preserved completion-report path named in your prompt; do not modify that file.

# REPORT-NN-NNN-review

Status: <complete | blocked | failed>
Request alignment: <aligned | drifted | unavailable-for-legacy>
Request evidence: <ordered evidence identities | none>

## Identity

- Review ID: <REVIEW-NN-NNN>
- Prompt path: <absolute path to the prompt file>
- Task path: <absolute path to the task file being reviewed>
- Review file path: <absolute path to the review file>

## Evidence reviewed

<What was inspected: the preserved completion report, code, specs, test results, etc.>

Include the bound verbatim request context and separate PM-derived guidance.
The alignment/evidence header fields must match the durable review file.

## Verdict

<approve | request-changes | reject>

## Blocking findings

<List blocking findings, or "none.">

---

## Planning-review completion variant

Use this section instead of the above when reporting on a planning- checkpoint review handoff (e.g., requirements review, plan review).

# REPORT-PLAN-NNN

Status: <complete | blocked | failed>
Request alignment: <aligned | drifted | unavailable-for-legacy>
Request evidence: <ordered evidence identities | none>

## Identity

- Review ID: <REVIEW-PLAN-NNN>
- Prompt path: <absolute path to the prompt file>
- Review file path: <absolute path to the review file>

## Evidence reviewed

<What was inspected: requirements, plan, phases, tasks/specs, etc.>

Include the bound verbatim request context and separate PM-derived guidance.
The alignment/evidence header fields must match the planning review file.

## Verdict

<approve | request-changes | reject>

## Blocking findings

<List blocking findings, or "none.">

---

> **Redaction reminder:** Do not include API keys, credentials, tokens, private connection strings, or comparable sensitive values in this report. Redact before writing.
