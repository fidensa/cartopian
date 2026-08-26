# Implementation Plan: <project name>

## Purpose

What this plan exists to accomplish. Name the source documents or external references that drive it — e.g., `REQUIREMENTS.md`, `STANDARDS.md`, a JIRA epic, or a Confluence page — and their versions or dates where applicable.

When those sources are authoritative, also state which one governs each scope, the conflict-resolution rule or named decision authority, and any claim that remains explicitly unverified. Do not average conflicting or missing decisive authority into a favorable planning judgment.

## Architecture rules

Rules derived from requirements and project standards that constrain all phases. These are not new decisions — they are consequences of locked inputs.

## Work topology

Which repos or other work locations are involved, what each owns, and how they interact. Include single-repo, multi-repo, and no-repo projects as applicable.

## Delivery contract

The domain-neutral delivery gate (`cartopian://protocol/DELIVERY`). A plan that produces an outcome reaching anything outside itself carries the nine rows below; a plan that reaches no external target declares that explicitly and justifies it. An absent section is undeclared, which blocks closeout. Replace every placeholder — a placeholder records nothing and fails closed.

For a plan that delivers nothing outside itself, the whole section is one row:

- Delivery: not-applicable; Justification: <why this plan reaches no target outside itself>

Otherwise:

- Delivery: required
- Owner: <the party accountable for the delivery>; Availability: <available | unavailable>
- Target: <what or whom the outcome reaches>; Kind: <recipient | audience | system | population | jurisdiction | ...>
- Acceptance evidence: <the observation that the target accepted the outcome>; Target: <the same target identity>; Observer: <who observed it, not the delivery owner>; Authority: <the basis on which their account counts>
- Success signals: <what distinguishes a delivered outcome from an undelivered one>; Target: <the same target identity>; Observable: <the condition a reader could check on the target>
- Contingency: <the recovery, correction, or alternate action>; Kind: <rollback | correction | alternate>; Rollback: <available | inapplicable>; Because: <why rollback does not apply — only when it is inapplicable>; Trigger: <the observable condition that starts it>; Owner: <who performs it>
- Immediate verification: <the observation of the target's own state>; Target: <the same target identity>; Evidence: <the evidence identity a reader can re-inspect>; Time: <when the observation was taken>; Result: <pass | fail | not-run>
- Follow-up: <the remaining question>; Owner: <who answers it>; Due: <a date, a counted interval, or a declared cadence>; Status: <open | closed>
- Authority: <operator-authorized | pending-operator-authorization | declined>; Evidence: <the operator statement that established this state>
- Artifact: <complete | incomplete>; Evidence: <the artifact-level check it passed>

A follow-up that genuinely does not apply takes the same explicit form as the applicability row: `- Follow-up: not-applicable; Justification: <why no question remains>`.

Artifact completion, outcome verification, and follow-up are three separate states. Recording `Artifact: complete` never establishes that the target received anything, and a declined or unauthorized external action stays honestly undelivered rather than becoming a verified outcome. Run `cartopian validate-delivery <project-root>` for the current states and any findings.

## Phase sequence

### Phase 00: <name>

**Goal:** …

| Plan ref        | Kind     | Description |
| --------------- | -------- | ----------- |
| DESIGN-00-001 | design | … |
| BUILD-00-002  | build  | … |

Within each phase, every work kind (`BUILD`, `DESIGN`, `RESEARCH`, `TEST`, `RELEASE`, `VERIFY`, `CORRECTIVE`) draws from one three-digit sequence starting at `001`. The plan ref allocates `NN-NNN`; its task and every task-scoped artifact reuse that suffix unchanged. Each corrective item receives its own new ref.

**Exit criteria:**

- …

### Phase 01: <name>

**Goal:** …

| Plan ref     | Kind  | Description |
| ------------ | ----- | ----------- |
| BUILD-01-001 | build | …           |

**Exit criteria:**

- …

<!-- Continue for each phase. -->

## Requirement coverage

| Requirement | Plan ref(s)  | Phase |
| ----------- | ------------ | ----- |
| FR-001      | BUILD-01-001 | 01    |
| NF-001      | BUILD-02-003 | 02    |

Every requirement from REQUIREMENTS.md must appear. If a requirement is intentionally deferred, note the reason.

## Open questions by phase

Questions that arose during planning, grouped by the phase they affect. Each question names its owner.

## Exit criteria summary

Per-phase exit criteria collected in one place for quick reference.
