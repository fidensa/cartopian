# REVIEW-NN-NNN

Target: <TASK-NN-NNN or SPEC-NN-NNN>
Plan ref: <KIND-NN-NNN | n/a>
Work root: <name | name, name | n/a>
Reviewer: <free text>
Verdict: <approve | request-changes | reject>
Request alignment: <aligned | drifted | unavailable-for-legacy>
Request evidence: <ordered evidence identities | none>
Request-context identity: <sha256:...>

## Summary

Two lines. What was reviewed, and what the verdict rests on.

## Request comparison

Compare the generated verbatim original-request channel with the separate
PM-derived guidance and the reviewed outcome. Explain why it is aligned or
drifted. The configured reviewer makes this judgment; the operator does not.

- `drifted` blocks approval.
- `unavailable-for-legacy` is non-blocking only when the generated context says
  this genuinely historical unit predates request-evidence resolution.

## Implementation evidence

Required when the reviewed outcome uses implementation/git evidence. For document, operational, planning, physical, or no-repo work, use `n/a` for inapplicable fields and make the task's completion evidence or durable deliverable the primary artifact reviewed.

- **Commit SHA** — filled by the reviewer: the green implementation commit they approved against. In PM-owned product-repo git projects, this is the PM-created task commit from `skills/run-task.md` Stage 4.
- **Merge commit SHA** — filled by the PM in Stage 6 of `skills/run-task.md`, post-merge. The reviewer writes `pending`, or `n/a` when the project does not use PM-owned product-repo git.
- **PR URL** — filled by whichever role has it: the reviewer when the PR existed before review in the PM-owned product-repo git workflow, otherwise by the PM after merge.
- **Acceptance evidence** — two parts when the task's evidence gate was `required`:
  - The named before-state evidence was recorded.
  - The closing observation passes. For software this is commonly a green test. When evidence gate was `n/a`, write `n/a — evidence gate was n/a per task`.

## Source evidence review

When the task is source-backed, compare the governing source guidance with the completion report's `## Source evidence`. Confirm that every evidenced source identity and applicable date/version comes from the guidance, that the report includes the sources actually applied without requiring unused guidance sources, that conflicts are resolved by the declared rule or authority, and that remaining non-decisive unverified claims carry the full failure signal. Missing or conflicting decisive authority blocks approval. Use `n/a — task is not source-backed` otherwise.

## Practice-pack semantic review

Required when the reviewed outcome changes a practice-pack body. Structural and source validation prove only that the body is well-formed, identified, bounded, and backed by current declared authority; they never prove the guidance is substantive. Inspect the changed body directly, along with each governing or conditional source that applies to it. Citing the validation suite does not satisfy this section. Record one observation and an `adequate`/`inadequate` disposition per dimension, in your own words. One `inadequate` dimension blocks approval of that pack. Do not compute or report a score: heading presence, keyword presence, and byte length are not quality evidence, and there is no minimum length.

- **actionability** — <adequate | inadequate>: does the body change what the assignee asks, decides, or produces, rather than naming topics?
- **conditional-domain-guidance** — <adequate | inadequate>: is domain guidance conditional — when it helps, when it does not apply, and the evidence for using it — rather than unconditional dogma?
- **source-alignment-and-classification** — <adequate | inadequate>: is every source claim inside its declared class and applicability, with no exemplar or watchlist carrying domain authority and no current source applied universally?
- **failure-handling** — <adequate | inadequate>: are the common failure modes named with the rationalizations that disguise them?
- **evidence-and-verification** — <adequate | inadequate>: does the body require evidence a reviewer can check without trusting the author?
- **examples-and-counterexamples** — <adequate | inadequate>: do the examples show both proportionate application and misapplication?
- **stop-and-escalation-clarity** — <adequate | inadequate>: is it clear when to stop, what to escalate, and in what grammar?

Use `n/a — no practice-pack body changed` otherwise.

## Findings

Each finding carries a severity:

- **blocker** — approval is impossible until resolved.
- **major** — real defect or significant gap.
- **minor** — worth fixing, does not block.
- **nit** — style or clarity.

Findings:

- F1. [blocker | major | minor | nit] — Description with file path and line range or section reference.
- F2. …

## Suggested actions

- For `request-changes`: what to address before resubmission.
- For `reject`: what a new approach should consider.

## Reviewer notes

Optional. Anything the author or a future reader should know.

For verification-only work under the no-product-git model, a dirty work root may contain expected deliverables from prior completed tasks. Do not treat `git status` alone as proof that the verification handoff changed files; use the handoff evidence to distinguish pre-existing state from current-task changes.

> **Reviewer boundary:** create the review file and record the verdict only. Do not move task files, delete prompts, or perform lifecycle cleanup. The PM applies lifecycle changes after reading the review.
