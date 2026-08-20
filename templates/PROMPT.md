# <title>

Work root: <name | name, name | n/a>
Branch: <branch or n/a; include only for a git workflow>
Phase: <PHASE-NN | omit when not applicable>
Plan ref: <KIND-NN-NNN | omit when not applicable>

## Paths

- **Project root**: <absolute path to the governing Cartopian project directory; process launch cwd, but not authority to edit PM lifecycle artifacts>
- **Work root paths**: <comma-separated absolute paths resolved from `Work root:`, or n/a>
- **Deliverable path**: <absolute path where the durable work product is written, or n/a; when the deliverable must land inside the governing project, this is n/a and the work product is returned inline in the report instead — see the Deliverable section below>
- **Report path**: <absolute path to the expected handoff report: `reports/REPORT-NN-NNN.md` for task completion, `reports/REPORT-NN-NNN-review.md` for task-review completion, `reports/REPORT-PLAN-NNN.md` for planning review>
- **Report skeleton**: <included inline below — the machine-generated skeleton from `cartopian report-skeleton`, carrying exactly this assignment's applicable sections with machine-owned values already filled>

The assignee CLI is launched with cwd set to the **Cartopian project root**. That working directory is launch context, not lifecycle authority: product work occurs only through the declared **Work root paths**, and declared work-root access does not grant PM lifecycle authority over requirements, decisions, tasks, backlog, `STATE.md`, prompts, or reports. Unless this assignment explicitly says otherwise, the only authorized write in the governing project is the **Report path** above.

`cartopian dispatch` resolves every declared work-root name through `cartopian resolve-config`, preserves declared order, fails closed on an unmapped or nonexistent path, and exports the absolute paths in `CARTOPIAN_WORK_ROOTS`. The shipped Codex wrapper widens its `workspace-write` sandbox with those paths, and the Claude and Antigravity wrappers add each path with `--add-dir`. The Devin sandbox has no per-path widening surface; when that sandbox is active, its wrapper warns that declared work roots may be unwritable. The wrappers do not replace harness capability enforcement.

Keep the prompt self-contained: paste the deidentified spec and the machine-generated report skeleton inline rather than directing the assignee to read PM artifacts. Keep it proportional: include the exact applicable skeleton (`cartopian report-skeleton`), not the full report template, and only the guidance bodies admitted for this task. Keep it audience-scoped: include a statement only when it changes how the assignee performs, evidences, bounds, or reports the work — downstream lifecycle facts such as which role reviews the work, how a review is launched, or what the PM does after the report lands stay out of the prompt body. The structured `## Risk result`, `## Judgment guidance`, `## Practice-pack result`, and `## Source guidance` sections are the only carriers of lifecycle-adjacent expectations, pasted exactly and never paraphrased into additional prose.

For a planning or task-closure review, create this file only through:

```text
cartopian write-prompt <project-root> ... --review-kind <planning|task-closure>
```

The writer owns the generated `## Original operator request (verbatim)` and
`## PM-derived guidance and delivered outcome` sections and binds them to one
deterministic review-context identity. Do not author, summarize, remove, or
edit either section.

## Pull request

- **PR URL**: <URL or n/a>
- **Preview URL**: <URL or n/a>

Omit this section when the work has no pull-request workflow.

For review prompts in projects using PM-owned product-repo git, the PM populates `Branch`, `PR URL`, and `Preview URL` when available. If no preview URL exists, write `n/a`. Coder prompts may leave `PR URL` and `Preview URL` as `n/a` or omit them entirely.

## Your role

<Sourced from the handoff packet's `role_description`. Address the assignee directly — "You are a <role name> (<role description>); for this assignment you <what the role does here>." Orientation only: it grants no authority beyond the role's configured grants and carries no PM identifiers.>

## Your task

<Imperative, directed at the assignee.>

## Context

<Self-contained. No "go read the PM system." All referenced file paths must be absolute.>

## Specification

<When the work has a spec, paste the **deidentified** spec body here — the `deidentified_spec` field from `cartopian render-spec <spec-path>`. Do not link or hand over the raw spec file; it carries PM identifiers the assignee must not copy into product code. Omit this section when the task has no spec.>

## Project standards

<When the project's `STANDARDS.md` declares working standards, constraints, or quality checks that bind this assignment — style and formatting conventions, required development practices such as TDD, mandated validation tooling or checks — paste the applicable excerpt here, deidentified. Only the standards that govern this work: never the whole document, and never planning-side metadata (stack rationale, cycle constraints, open standards questions) the assignee cannot act on. Omit this section when none apply.>

## Source guidance

<Include only when the handoff packet's `source_guidance.outcome` is `valid`. Paste its `deidentified_guidance` exactly. It names the authoritative sources, applicable dates or versions, conflict disposition, and explicitly unverified claims. If the record is invalid, do not author or issue the handoff; surface its actionable blockers.>

## Risk result

<Paste the structured result returned by `cartopian classify-risk`. Preserve its `contract_id`, `contract_version`, `band`, ordered reasons, evidence expectation, independent-review expectation, operator gate, and contingency expectation exactly. This projection does not replace or edit configured review policy, role assignments, capability grants, or launch permissions. State how any applicable operator gate was satisfied before the gated action.>

## Judgment guidance

<Paste the structured result returned by `cartopian select-judgment-guidance` from the task's declared judgment envelope. Preserve its `active_cards`, `ordered_activation_reasons`, `inactive_cards`, `guidance_identity`, `loaded_guidance_bytes`, `guidance_budget_bytes`, and `context_receipt` exactly. Include the body only when the outcome is `active`, and then exactly the one returned central body — never a per-card copy, a second grammar, or an anti-rationalization table of your own. Four active cards carry the same one body as one active card. `none` carries no body and core governance continues. Do not issue the handoff when the outcome is `invalid`. Judgment activation does not change the risk result, select a practice pack, alter configured review policy, or require review; the band and the pack outcome are not inputs to it.>

## Practice-pack result

<Paste the structured result returned by `cartopian select-practice-pack` from the task's declared practice-pack envelope. Preserve its `pack_id`, ordered match reasons, rejection reasons, `body_identity`, `loaded_body_bytes`, `body_budget_bytes`, `applicable_sources`, and `context_receipt` exactly. Include the body only when the outcome is `selected`, and then exactly the returned body — never a second pack body, the metadata catalog, a full source document, structural-exemplar text, or watchlist content. `none` carries no body and core governance continues. Do not issue the handoff when the outcome is `ambiguous` or `invalid`. The projected `applicable_sources` are identities and applicability boundaries, not source text: a source absent from that list carries no authority for this task. Pack selection does not change the risk result, activate judgment guidance, alter configured review policy, or require review.>

## Original operator request (verbatim)

<Review prompts only. Tool-generated from exact applicable decision quotes,
supported host chat records, optional intake records, and ordered explicit
corrections. Never hand-author this section.>

## PM-derived guidance and delivered outcome

<Review prompts only. Tool-generated list of the management and delivery
artifacts the configured reviewer compares with the verbatim channel above.>

## Existing deliverable input

<Include only when `handoff-packet.existing_deliverable_input.required` is true. Paste the complete current UTF-8 resource content exactly; the assigned role cannot read the governance-scoped resource path directly. Rerun `handoff-packet` after writing and require `existing_deliverable_input.ok: true` before handoff. Omit when the record says `required: false`.>

## Deliverable

<Include this section only when the task produces a durable document (research findings, a design, an evaluation, an analysis) rather than code.

- When a **Deliverable path** is given above, write the complete work product to that file. Treat it like code: it is the artifact the reviewer reviews, not the report. Your completion report then only summarizes what you did and points to the deliverable — do not paste the full work product into the report.
- When the **Deliverable path** is n/a because the durable copy must live inside the governing project's `resources/` directory (outside your write scope), put the complete work product in the report's `## Deliverable content` section instead. The PM persists it to its durable location and the reviewer reviews that copy.

Omit this section for code-only tasks.>

## What to produce

<File paths, interfaces, confirmation sources, checklists, validation targets, or other concrete evidence locations.>

## Evidence gate

<The before-and-after evidence required: test target, fixture run, validation script, fact-check pass, approval checklist, inspection, rehearsal, or n/a with reason.>

For source-backed work, require the completion report to carry `## Source evidence` in the same record shape. A complete report must name the non-empty subset of governing sources and applicable contexts actually used, the conflict disposition, and all claims that remain explicitly unverified. Every evidenced source identity/context must come from the supplied guidance; do not copy guidance sources that were not applied.

## What not to do

<Scope boundaries. Non-goals.>

- Do not modify spec, task, phase, or prompt files. Only the PM edits Cartopian protocol files. If the spec, task, or this prompt is wrong, ambiguous, or insufficient, stop and report it as a blocker in the completion report rather than rewriting the protocol file to match what you built.
- Do not move Cartopian task files between status directories.
- Do not delete prompt files.
- Do not rewrite `STATE.md` or perform PM lifecycle cleanup.
- When the project uses PM-owned product-repo git, do not stage, commit, push, create branches, open PRs, merge, or otherwise perform product-repo git plumbing.

For a verification-only assignment or review under the no-product-git model, add the effective operating model here: Cartopian git versioning is off; product-repository branches are not PM-owned; pre-existing uncommitted deliverables from completed tasks are expected and are not evidence that the verification handoff modified files. Evaluate only changes attributable to the current handoff.

## Done criteria

<Checkable. Boolean-verifiable.>

## Completion report

When you are done, write a completion report to the report path listed above. Fill in the report skeleton included in this prompt: keep every machine-generated value (identities, paths, evidence rows, section headings) exactly as given and supply only the substantive content — evidence, findings, verdicts, and status. The skeleton already carries exactly the sections this assignment requires; `templates/REPORT.md` remains the canonical field schema behind it.

**Run completion-critical work in the foreground.** This handoff is a single non-interactive session, and your final result is process exit — nothing runs after you stop. Any command whose outcome the report depends on (test suite, build, validation script, fixture run, lint pass) must be run in the **foreground** and waited for to completion before you write the report. Do not background it, do not rely on a background-task or job-completion notification to resume you, and do not end the turn saying a run is "still going" and the report will follow: there is no later turn, the session is terminated, and the handoff is recorded as having exited without a report. If a run is too slow to finish inside the handoff deadline, that is a blocker to report — not work to leave running.

**Writing the report is the last thing you do.** An unwritten report is a lost handoff even when the work itself succeeded. If the work cannot be finished, still write the report with `Status: blocked` and record what stopped you — a blocked report is a finished handoff; an absent one is not.

**Validate before you exit, when the tool is available.** If the `cartopian` CLI is available in your session, run `cartopian validate-report <report path>` after writing the report and apply the named recovery for any `mechanical` finding before exiting. Do not alter substantive content to satisfy a check; a `substantive` or `missing-input` finding is reported, not edited away.

**Redact secrets.** Do not include API keys, credentials, tokens, private connection strings, or comparable sensitive values in the report.
