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

## Contract quality

Do this audit **first**, and write it down before evaluating the implementation. Judge the governing contract — the operator request as bound into the task, the task, and the specification — as written: do not repair it silently, and do not credit it for what the implementation happens to do. Ordering is a reasoning aid, not an isolation guarantee; it reduces the chance that implementation framing colours the contract judgment, and it does not prevent it.

Seven checks:

1. **Request fidelity** — do the task and specification ask for what the operator actually requested, without narrowing, widening, substituting, or adding destinations, features, or conventions?
2. **Completeness** — are problem, goal, non-goals, interface, constraints, and deliverable stated well enough to build and judge without inventing scope?
3. **Factual and source accuracy** — are the cited sources real, current, correctly scoped, and correctly described, and do the claims match them?
4. **Internal coherence** — do the sections agree with each other?
5. **Upstream alignment** — does the contract carry every applicable upstream requirement, standard, plan item, and decision that governs it? Where an applicable requirement is reached by no trace record, name the requirement.
6. **Acceptance clarity** — is each acceptance item a single, unambiguous, checkable statement with a clear pass condition?
7. **Testability** — could someone who did not write the contract tell pass from fail using the stated evidence gate?

`adequate` means all seven pass; list no gaps, or only nits — a clean contract is one line. `needs changes` means at least one check has a gap: name the check, locate the offending clause, and say what would resolve it. A `needs changes` outcome does not by itself set the verdict; you set the verdict as you do today, weighing contract and implementation together. Do not score, count, or rank the checks — a reviewer computing an aggregate has left the rubric.

Contract defects are `C<n>` here; implementation defects stay `F<n>` under `## Findings`. Neither excuses the other: an implementation that faithfully matches a deficient specification is still `needs changes` at the contract level and may still be sound at the implementation level. Say both.

Outcome: <adequate | needs changes>

- C1. [blocker | major | minor | nit] <one of the seven check names> — what is wrong, where, and what would settle it.

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

## Closure determinations

Required when the task declares `Upstream trace: required`; use `n/a — task does not declare an upstream trace` otherwise. Copy the block from the reviewer provenance projection in your review context and fill in each verdict, so the determinations are recorded against the exact record set the assignment was issued under.

Two determinations per material criterion, with **different inputs and different semantics**:

- **D1 — immediate-contract compliance.** Does the delivered work satisfy the task and specification as written? Inputs: the deliverable, the task, the specification. Reason code: `acceptance-item-unmet`. A criterion covering a merged-away origin carries that origin's obligation too.
- **D2 — upstream-intent adequacy.** Do the task and specification adequately satisfy the upstream sources *reached through the trace*, and is coverage of the enumerated sources and excerpts complete? Inputs: the serialized trace, the provenance block, and the `S|`/`R|`/`W|` coverage records. Reason codes: `upstream-intent-uncovered`, `exemption-unjustified`, `unresolved-source-conflict`.

They are independent by construction — D1 evaluates work against contract, D2 evaluates contract against upstream — so neither may be inferred from the other and neither may be recorded as "same as above". D1 passing while D2 fails is the case this contract exists to catch: work that satisfies the task's wording while the task itself omitted material upstream intent. Either failing blocks closure.

A `source-uncovered`, `request-uncovered`, or `waiver-rejected` finding names an identity no criterion claims, so it cannot localize to an ordinal; record it on the task-scoped line instead.

A passing line carries `reason:-`. A missing, contradictory, or unattributed determination blocks approval and never defaults to pass.

```
Trace-identity: sha256:<64 hex>
D1 C01: <pass | fail> reason:<code | ->
D2 C01: <pass | fail> reason:<code | ->
D2 task: <pass | fail> reason:<source-uncovered | request-uncovered | waiver-rejected | ->
```

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
