# Add config loader

## Role and workspace

You are a coder — Implements tasks per spec. This preface is orientation only: it grants no authority beyond the role's configured grants.

- Project root (your launch working directory; not authority to edit project-management files): <SCAFFOLD>/scaffold-project
- Work roots (product work happens only here): tool-repo: <SCAFFOLD>/tool-repo
- Report path (the only authorized write inside the governing project unless a section below says otherwise): <SCAFFOLD>/scaffold-project/reports/REPORT-01-002.md

## Outcome and done criteria

Implement the configuration loader described by the specification.

Done means every item below can be independently marked true, together with the acceptance items in the Implementation contract:

- [ ] Invalid input produces the documented error, not a traceback.

## Implementation contract

### Problem

Tools re-parse configuration ad hoc; behavior drifts between them.

### Goal

One loader with documented structure and failure behavior.

### Non-goals

- No schema migration tooling.

### Interface

`load_config(path) -> Config`; missing file raises ConfigMissing; malformed TOML raises ConfigInvalid with line context.

### Constraints

Stdlib only.

### Examples / acceptance

- [ ] The loader parses a valid config file into the documented structure.
- `load_config(missing)` raises ConfigMissing.

## Applicable project standards

### Tools and dependencies

Python 3.11+, stdlib only.

### Working standards

RED/GREEN TDD: write the failing test first; commit messages describe the unit of work.

## Source guidance

### Authoritative sources

- Identity: TOML v1.0.0 specification; Applicable context: v1.0.0 (2021-01-11); Status: current; Scope: configuration file syntax and semantics

### Conflict resolution

- Status: none; Rule: single governing source; Decision: n/a

### Unverified claims

- none

An 'Applicable context' value is a historical fact about the source (the edition, date, or version the guidance was authored against). It does not by itself require or forbid currently installed behavior. 'Status: current' is a verified claim established by the governing record's validation, not an assumption; when the task requires current behavior and the record does not establish compatibility, composition fails instead of restating the stale claim.

## Scope and authority boundaries

- Implement only what the Implementation contract and done criteria require. If any supplied input is wrong, ambiguous, or insufficient, stop and report it as a blocker in the completion report instead of adapting the input to what you built.
- Do not create, edit, move, or delete project-management files (task, specification, prompt, phase, or state records) or perform lifecycle cleanup; your writes are the work roots above and the report path.
- This project runs without git versioning: a work root may already contain uncommitted output from earlier completed work. That steady state is expected and is not itself a defect; evaluate and report only changes attributable to this assignment.

## Verification

- Evidence gate: required — Run the loader unit tests before and after the change; capture the failing and passing runs.
- Run every completion-critical check in the foreground and wait for it to finish before writing the report. A run too slow to finish inside this session is a blocker to report, not work to leave running.
- The report's Source evidence section must name the non-empty subset of supplied sources you actually applied — copied from Source guidance above, never extended with outside sources.

## Active guidance

### Risk

Risk band: bounded. The observations behind it:

- consequence-reach: project-internal — loader is consumed only by in-repo tools

- Required evidence: artifact-and-recovery-proof — answer it directly in the report's Risk-scaled evidence section.
- Operator gate: boundary-crossing-approval — when a gate applies, record in the report how it was satisfied before the gated action.
- Contingency: named-recovery-action — record the recovery or stop-condition evidence the report section names.

### Judgment holds

The PM enforces these holds before the task closes. They are not yours to release and not a reason to stop, wait, or report blocked.

- **evidence-and-review-gate** (evidence-self-certified-or-missing): The producer self-certifies quality, treats missing or indirect evidence as success, or skips independent judgment where deterministic checks cannot decide adequacy.
  Claim to name: the adequacy you asserted without decisive evidence. Released by: the missing evidence, or a judgment by someone who did not produce the work.

Finish the assigned work and produce any release evidence your assignment lets you produce. Under `## Remaining risks`, record each hold in four plain sentences: the unverified claim, the missing authority or evidence, the consequence of proceeding, and the decision or proof that would release it. When the work itself is done, report `Status: complete` with readiness `yes`; `Status: blocked` is only for work you could not finish.

### Practice profile: software-delivery

Use this optional guidance only for the selected software outcome. It does not change the task risk, activate judgment guidance, alter configured review policy, or create a universal gate. Wherever a narrower current authority governs — jurisdictional or organizational rules, product standards, platform requirements, the operator's task contract — that authority overrides this pack inside its scope.

#### Intended Outcome

Deliver a proportional software behavior change: the requested behavior clarified into an observable contract, a design no larger than the change requires, decisive verification of the changed behavior, and a delivered state that can be proven and undone. The evidence this pack produces is artifact behavior, target-state verification, and recovery evidence — not effort, not test counts, not structure for its own sake.

#### Working Process

1. Clarify the contract: observable outcome, hard constraints, and the authority the change relies on. Stop at the first decisive ambiguity (see Stop And Escalation).
2. Scope the smallest change that satisfies the outcome, and state what is deliberately out of scope.
3. Design at proportional altitude using the principles above; write down the one or two tradeoffs actually decided.
4. Implement with verification alongside: decisive checks at the most direct layer, failing before the change and passing after it where practical.
5. Run the applicable boundary checks — security, privacy, accessibility, performance, compatibility — and record which did not apply and why.
6. Deliver: prove the artifact or target state, confirm compatibility assumptions, and establish the recovery path and its owner.
7. Record evidence and gaps: exact commands, inputs, observed results, unverified claims, and the follow-up each gap requires.

#### Decision Gates

Answer before proceeding past each stage; a no is a stop or an escalation, not a rationalization.

- Can I state the changed behavior as a check that would fail today?
- Do I know every consumer of what I am changing?
- Does the change cross a trust, authority, secret, input, path, or data boundary — and is each crossing fail-closed?
- Is each new abstraction paid for by a present need?
- Is the affected surface user-facing, and if web-scoped, are the accessibility and security checks scoped to it?
- Can this change be undone — how fast, and by whom?
- What single piece of evidence proves this is done, and does it exist yet?

#### Evidence And Verification

Produce evidence a reviewer can check without trusting the author: exact commands and inputs with observed results; the failing-then-passing check for changed behavior; boundary-check results or the recorded statement that no boundary changed; measured artifact or body sizes against declared limits; the consumer list for changed contracts; and every relevant coverage gap stated rather than omitted. Structural validity — it compiles, the suite is green, the headings are present — is never by itself evidence that behavior is correct or that this guidance was followed.

#### Stop And Escalation

Stop and report rather than proceed when: a decisive requirement or constraint is ambiguous and the answer changes the design; the change needs authority not granted — a new external commitment, a release, a widened data use; verification decisive to the outcome cannot run in the available environment; a consequential change has no establishable recovery path; or this guidance conflicts with a narrower current authority and the conflict is not resolved in that authority's favor. Escalate with the failure-signal grammar: the unverified claim, the missing authority or evidence, the consequence of proceeding, and the decision or proof required. Hand off with the current state, the evidence produced, the open gaps, and the recovery trigger and owner.

Applicable sources for this profile:

- NIST SP 800-218, Secure Software Development Framework version 1.1 (final 2022 edition, version status verified 2026-08-07) — Broad final secure-software-development baseline for the software pack.
- NIST Cybersecurity Framework 2.0 (final 2024-02-26 edition verified 2026-08-07) — Broadly applicable cybersecurity risk-management outcomes for software and operations, applied proportionally.

## Completion report

Write your completion report to the report path named in Role and workspace, filling in the skeleton below. Keep every machine-generated value (paths, names, prefilled rows) exactly as given and supply only substantive evidence, findings, and status.

- Writing the report is the last thing you do. If the work cannot be finished, still write the report with `Status: blocked` and record what stopped you — a blocked report is a finished handoff; an absent one is not.
- If the `cartopian` CLI is available, run `cartopian validate-report <report path>` after writing and apply the named recovery for any `mechanical` finding. Report — never edit away — a `substantive` or `missing-input` finding.
- Do not include secrets: API keys, credentials, tokens, or private connection strings.

```text
Status: <complete | blocked | failed>

## Summary

<PM-facing summary, at most 10 short lines: what was done, where the evidence and work product live, and anything the PM must act on. The PM routes on this section instead of re-reading the whole report; full detail belongs in the sections below.>

## Identity

- Work root: tool-repo

## Completion evidence

<concrete, verifiable evidence that the outcome exists>

## Source evidence

Reproduce this section's three subsections by copying rows from the assignment prompt's ## Source guidance section — identities and applicable contexts unchanged. Include only the sources you actually applied and delete the rest; never introduce a source outside the supplied guidance.

### Authoritative sources

<the full row, copied unchanged, for each supplied source you actually applied>

### Conflict resolution

<the conflict-resolution row, copied unchanged>

### Unverified claims

- none

Keep `- none` when no claim remains unverified. Otherwise replace it with one row per remaining claim, each carrying all five fields in exactly this form:
`- Claim: <unverified claim>; Decisiveness: <decisive | non-decisive>; Missing: <authority or evidence>; Consequence: <consequence of proceeding>; Next: <decision or proof required>`
A `decisive` claim may not remain unverified in a complete report.

## Test evidence

- Red test evidence: <pointer to the failing check before the change>
- Green test evidence: <pointer to the passing check after the change>

## Risk-scaled evidence

- Band: bounded
- Evidence expectation: artifact-and-recovery-proof; Evidence: <direct proof>
- Operator gate: boundary-crossing-approval; Disposition: <authority or approval evidence, or n/a when no gate applies>
- Contingency expectation: named-recovery-action; Evidence: <recovery action, trigger/owner, or evidenced stop condition>

## Remaining risks

<known risks, edge cases, or follow-up work — or none.>

## Ready for review

<yes | no>

`yes` means your own work is complete and enters the required independent closure review — it does not approve closure and does not certify the reviewer's verdict. `no` is only for genuinely incomplete or blocked work, and `Status: blocked` or `failed` requires `no`. A short rationale may follow the token on the same line.
```
