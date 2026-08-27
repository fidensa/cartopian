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

- No raw diagnostic JSON reaches the assignee; selector results appear only as their assignee projections (risk band with reasons and expectations; active judgment holds with the release requirement; the selected practice profile's execution capsule and applicable source identities).
- Source guidance is rendered exactly once; the specification projection and the report skeleton reference it instead of repeating it.
- The specification appears as its **assignment projection**: implementation contract only — no author/reviewer metadata, planning status, review checklists, open-question sections, or PM identifiers. A spec with unresolved open questions refuses composition.
- Repeated contract content is removed: a task goal or acceptance criterion already stated by the specification is not restated.
- Reviewer-only material, PM lifecycle instructions, unredacted PM identifiers (the expected report path is the one sanctioned identifier), and blanket instructions to read whole governance documents are refused.
- Section sizes are measured against budgets derived from approved reference fixtures.
- Historical source context ("authored against version X") is represented as historical fact; currency claims must be established by the governing record or composition fails.
- A verification-only assignment under the no-product-git model carries the effective git operating model in its scope boundaries: git versioning is off, product-repository branches are not PM-owned, and pre-existing uncommitted deliverables from earlier completed tasks are the expected steady state — not evidence that the verification handoff modified files.

The generated `## Original operator request (verbatim)` and `## PM-derived guidance and delivered outcome` sections are appended by `write-prompt`, never authored. In the coder channel, a low-information inherited approval ("continue", "yes") is bound by its content identity but not pasted verbatim; the exact text stays in the trace receipt and remains available to independent review. Planned work with no task-specific operator instruction states that derivation explicitly.

## Review prompts (authored)

Create review prompts only through:

```text
cartopian write-prompt <project-root> ... --review-kind <planning|task-closure>
```

The writer owns the generated `## Original operator request (verbatim)` and `## PM-derived guidance and delivered outcome` sections and binds them to one deterministic review-context identity. Do not author, summarize, remove, or edit either section.

A review prompt includes, sourced from the handoff packet and the review skeletons (`cartopian report-skeleton <task-path> --variant review`):

- A `## Your role` preface from the record's `role_description` — orientation only; it grants no authority beyond the role's configured grants and carries no PM identifiers into product code.
- Absolute paths to the task file, the spec when present, the deliverable when declared (the **primary artifact to review**), the expected review file (`reviews/REVIEW-NN-NNN.md`), and the expected review-completion report path.
- The generated `## Preserved coder completion evidence` section — the content-hashed binding naming the preserved completion report by absolute path. The reviewer reads coder evidence directly from that preserved artifact; the prompt never reproduces the report body, and the completion report must stay byte-identical throughout the review.
- The review-report skeleton and review-file skeleton pasted from `report-skeleton --variant review` — instead of the full templates. Machine-owned Identity values are already filled; the reviewer supplies verdict, findings, and comparisons only.
- A `## Pull request` block (`PR URL`, `Preview URL`) when the PM-owned product-repo git workflow applies; `n/a` otherwise. Omit when the work has no pull-request workflow.
- Reminders that reviewers do not modify spec, task, phase, or prompt files; do not move task files, delete prompts, rewrite `STATE.md`, or perform PM lifecycle cleanup. A wrong or ambiguous spec is a review finding, never a spec edit.
- For a verification-only task under the no-product-git model: the effective git operating model — a dirty work root containing prior completed tasks' deliverables is the expected steady state, not a review defect.

## Shared launch boundary

The assignee CLI is launched with cwd set to the **Cartopian project root**. That working directory is launch context, not lifecycle authority: product work occurs only through the declared work-root paths, and declared work-root access does not grant PM lifecycle authority over requirements, decisions, tasks, backlog, `STATE.md`, prompts, or reports. Unless the prompt explicitly says otherwise, the only authorized write in the governing project is the expected report path.

`cartopian dispatch` resolves every declared work-root name through `cartopian resolve-config`, preserves declared order, fails closed on an unmapped or nonexistent path, and exports the absolute paths in `CARTOPIAN_WORK_ROOTS`. The shipped Codex wrapper widens its `workspace-write` sandbox with those paths, and the Claude and Antigravity wrappers add each path with `--add-dir`. The Devin sandbox has no per-path widening surface; when that sandbox is active, its wrapper warns that declared work roots may be unwritable. The wrappers do not replace harness capability enforcement.
