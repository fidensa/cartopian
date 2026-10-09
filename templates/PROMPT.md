# Prompt template

Prompts are temporary, assignee-directed handoff artifacts. Two kinds exist, with different production paths:

- **Task assignment prompts** (coder handoffs) are **composed, not authored**: `cartopian compose-assignment-prompt <task-path> --role <role>` deterministically renders the audience-scoped prompt from authoritative inputs and a bound machine trace receipt, and `cartopian write-prompt <project-root> --prompt-id PROMPT-NN-NNN --task <task-path> --composed-file <record>` writes the verified result. The PM does not hand-assemble the assignment body.
- **Review prompts** (planning and task-closure) are authored against the review schema below and written with `cartopian write-prompt ... --review-kind <planning|task-closure>`.

## Task assignment prompts (composed)

The composed prompt is an execution interface, not an audit log. It contains only statements that change how the assignee performs, bounds, verifies, or reports the work. Machine provenance — raw classifier and selector JSON, routing diagnostics, rejected candidates, inactive guidance, hashes, byte budgets, context receipts, and lifecycle bookkeeping — lives in the separate trace receipt bound to the prompt by a content identity, never in the prompt body.

The composed structure (owned by `protocol/assignment-prompt-contract.json`):

```text
# <deidentified assignment title>

## Role and workspace            role preface; project root, work roots, report path
## Outcome and done criteria     task goal delta + checkable acceptance criteria
## Implementation contract       the spec's assignment projection (or the task contract)
## Applicable project standards  tag-selected STANDARDS.md sections (conditional)
## Source guidance               the one authoritative source record (conditional)
## Scope and authority boundaries
## Verification                  evidence gate, foreground-run rule, source-evidence duty
## Active guidance               risk / judgment-hold / practice-profile projections, as prose
## Existing deliverable input    machine-created typed payload of the current resource (conditional)
## Upstream contract input       machine-created typed payload(s) of dependency deliverables (conditional)
## Deliverable                   where the durable work product lands (conditional)
## Completion report             instructions + the fenced machine-owned report skeleton
```

Composition guarantees, enforced fail-closed by validation:

- No raw diagnostic JSON reaches the assignee; selector results appear only as their assignee projections (risk band with reasons and expectations; active judgment holds translated for the assignee — claim to name and release requirement, enforced by the PM before closure, never a reason for the assignee to stop or report blocked; the selected practice profile's execution capsule and applicable source identities).
- Source guidance is rendered exactly once; the specification projection and the report skeleton reference it instead of repeating it.
- The specification appears as its **assignment projection**: implementation contract only — no author/reviewer metadata, planning status, review checklists, open-question sections, or PM identifiers. A spec with unresolved open questions refuses composition.
- Repeated contract content is removed: a task goal or acceptance criterion already stated by the specification is not restated.
- Reviewer-only material, PM lifecycle instructions, unredacted PM identifiers (the expected report path is the one sanctioned identifier), and blanket instructions to read whole governance documents are refused.
- Section sizes are measured against budgets derived from approved reference fixtures.
- Historical source context ("authored against version X") is represented as historical fact; currency claims must be established by the governing record or composition fails.
- A verification-only assignment under the no-product-git model carries the effective git operating model in its scope boundaries: git versioning is off, product-repository branches are not PM-owned, and pre-existing uncommitted deliverables from earlier completed tasks are the expected steady state — not evidence that the verification handoff modified files.

The generated `## Original operator request (verbatim)` and `## PM-derived guidance and delivered outcome` sections are appended by `write-prompt`, never authored. A low-information inherited approval ("continue", "yes") is never rendered alone: it appears together with the complete immediately preceding question or proposal it answers and that proposal's exact scope, and it authorizes nothing absent from that proposal. Planned work with no task-specific operator instruction states that derivation explicitly.

## Review prompts (authored)

Create review prompts only through:

```text
cartopian write-prompt <project-root> ... --review-kind <planning|task-closure>
```

The writer owns the generated `## Original operator request (verbatim)` and `## PM-derived guidance and delivered outcome` sections and binds them to one deterministic review-context identity. Do not author, summarize, remove, or edit either section.

The writer also appends the reviewer's output formats as two generated sections, last in the prompt, for both planning and task-closure reviews:

- `## Review file skeleton` — the absolute review file path (`reviews/REVIEW-<id>.md`) and the fenced review-file skeleton.
- `## Review completion report skeleton` — the absolute review-completion report path (`reports/REPORT-NN-NNN-review.md` or `reports/REPORT-<checkpoint-id>.md`) and the fenced report skeleton.

Task-closure skeletons come from the same builders as `cartopian report-skeleton --variant review`; planning skeletons exist only in the generated prompt, which is the authoritative copy for both kinds (`validate-report` recoveries point the reviewer back to it). Their machine-owned values are already filled: identities, paths, planning scope, request evidence, and the `Request-context identity` the prompt binds. The reviewer supplies the verdict, findings, and comparisons only. Do not paste skeletons or templates into the body. A rewrite regenerates both sections, and any authored copy of either heading is replaced.

The PM-authored body of a review prompt includes, sourced from the handoff packet:

- A `## Your role` preface from the record's `role_description` — orientation only; it grants no authority beyond the role's configured grants and carries no PM identifiers into product code.
- Absolute paths to the task file, the spec when present, and the deliverable when declared (the **primary artifact to review**). The expected review file and review-completion report paths are generated.
- The generated `## Preserved coder completion evidence` section — the content-hashed binding naming the preserved completion report by absolute path. The reviewer reads coder evidence directly from that preserved artifact; the prompt never reproduces the report body, and the completion report must stay byte-identical throughout the review.
- A `## Pull request` block (`PR URL`, `Preview URL`) when the PM-owned product-repo git workflow applies; `n/a` otherwise. Omit when the work has no pull-request workflow.
- Reminders that reviewers do not modify spec, task, phase, or prompt files; do not move task files, delete prompts, rewrite `STATE.md`, or perform PM lifecycle cleanup. A wrong or ambiguous spec is a review finding, never a spec edit.
- For a verification-only task under the no-product-git model: the effective git operating model — a dirty work root containing prior completed tasks' deliverables is the expected steady state, not a review defect.

## Shared launch boundary

Dispatch binds the governing project independently of the shell cwd. A contained writer starts in its first authorized work root; other launches start at the project root. Use absolute prompt paths. Configured roots replace the default `resources` root. Explicit role grants authorize content access, never governance or lifecycle actions. Report publication remains mediated.

Dispatch, rehearsal and POSIX/PowerShell wrappers enforce the same contained-root contract. Currently Claude on native macOS (2.1.295+) supports it; other shipped adapters/platforms refuse before launch. Use sandboxed Bash for contained-root mutations, builds and child processes. Do not change the session cwd. Read-only and empty-grant roles cannot write. See `wrappers/PROJECT-WORK-ACCESS.md` for configuration and limitations.
