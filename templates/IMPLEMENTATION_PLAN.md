# Implementation Plan: <project name>

## Purpose

What this plan exists to accomplish. Name the source documents or external references that drive it — e.g., `REQUIREMENTS.md`, `STANDARDS.md`, a JIRA epic, or a Confluence page — and their versions or dates where applicable.

## Architecture rules

Rules derived from requirements and project standards that constrain all phases. These are not new decisions — they are consequences of locked inputs.

## Work topology

Which repos or other work locations are involved, what each owns, and how they interact. Include single-repo, multi-repo, and no-repo projects as applicable.

## Phase sequence

### Phase 00: <name>

**Goal:** …

| Plan ref        | Kind     | Description |
| --------------- | -------- | ----------- |
| BUILD-00-001    | build    | …           |
| RESEARCH-00-001 | research | …           |

Within each phase, every work kind (`BUILD`, `DESIGN`, `RESEARCH`, `TEST`, `RELEASE`, `VERIFY`, `CORRECTIVE`) has an independent three-digit counter starting at `001`. Task ids use their own phase-wide counter; a plan ref and its task do not need matching numeric suffixes. Each corrective item receives its own new ref.

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
