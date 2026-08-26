# Tidy fixture data

## Role and workspace

You are a coder — Implements tasks per spec. This preface is orientation only: it grants no authority beyond the role's configured grants.

- Project root (your launch working directory; not authority to edit project-management files): <SCAFFOLD>/scaffold-project
- Work roots (product work happens only here): tool-repo: <SCAFFOLD>/tool-repo
- Report path (the only authorized write inside the governing project unless a section below says otherwise): <SCAFFOLD>/scaffold-project/reports/REPORT-02-001.md

## Outcome and done criteria

Done means every item below can be independently marked true:

- [ ] Every fixture file holds one record per line.

## Implementation contract

Normalize the fixture data files to one record per line.

## Scope and authority boundaries

- Implement only what the Implementation contract and done criteria require. If any supplied input is wrong, ambiguous, or insufficient, stop and report it as a blocker in the completion report instead of adapting the input to what you built.
- Do not create, edit, move, or delete project-management files (task, specification, prompt, phase, or state records) or perform lifecycle cleanup; your writes are the work roots above and the report path.
- This project runs without git versioning: a work root may already contain uncommitted output from earlier completed work. That steady state is expected and is not itself a defect; evaluate and report only changes attributable to this assignment.

## Verification

- Evidence gate: n/a — mechanical normalization verified by inspection.
- Run every completion-critical check in the foreground and wait for it to finish before writing the report. A run too slow to finish inside this session is a blocker to report, not work to leave running.

## Active guidance

### Risk

Risk band: bounded. The observations behind it:

- evidence-coverage: direct-observation — normalized files are inspected directly

- Required evidence: artifact-and-recovery-proof — answer it directly in the report's Risk-scaled evidence section.
- Operator gate: boundary-crossing-approval — when a gate applies, record in the report how it was satisfied before the gated action.
- Contingency: named-recovery-action — record the recovery or stop-condition evidence the report section names.

## Completion report

Write your completion report to the report path named in Role and workspace, filling in the skeleton below. Keep every machine-generated value (paths, names, prefilled rows) exactly as given and supply only substantive evidence, findings, and status.

- Writing the report is the last thing you do. If the work cannot be finished, still write the report with `Status: blocked` and record what stopped you — a blocked report is a finished handoff; an absent one is not.
- If the `cartopian` CLI is available, run `cartopian validate-report <report path>` after writing and apply the named recovery for any `mechanical` finding. Report — never edit away — a `substantive` or `missing-input` finding.
- Do not include secrets: API keys, credentials, tokens, or private connection strings.

```text
Status: <complete | blocked | failed>

## Identity

- Work root: tool-repo

## Completion evidence

<concrete, verifiable evidence that the outcome exists>

## Risk-scaled evidence

- Band: bounded
- Evidence expectation: artifact-and-recovery-proof; Evidence: <direct proof>
- Operator gate: boundary-crossing-approval; Disposition: <authority or approval evidence, or n/a when no gate applies>
- Contingency expectation: named-recovery-action; Evidence: <recovery action, trigger/owner, or evidenced stop condition>

## Remaining risks

<known risks, edge cases, or follow-up work — or none.>

## Ready to close

<yes | no>

`yes` means your work is complete; with task-closure review off it routes the accepted task toward direct closure. `no` is only for genuinely incomplete or blocked work. A short rationale may follow the token on the same line.
```
