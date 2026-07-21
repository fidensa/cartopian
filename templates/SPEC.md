# SPEC-NN-NNN: <short noun-phrase title>

Status: <draft | locked>
Profile: <software | general>
Author: <name or role>
Reviewer: <name or role>
Date: YYYY-MM-DD
Plan refs: <PNN-KIND-NNN, PNN-KIND-NNN | n/a>
Source: <BL-NNN | n/a>

Choose exactly one body profile below and delete the other profile and all instructional text. Classification follows this spec's outcome, not the overall project's label:

- Use `software` when the outcome is executable software or a technical contract intended for software implementation.
- Use `general` for a non-software work contract. Do not use it to evade the software-profile rules.

---

<!-- SOFTWARE PROFILE — keep only when Profile: software -->

## SRS

### Overview & Goals

What software outcome is required, why it matters, who it serves, the boundaries of this spec, and explicit non-goals.

### Functional Requirements

The observable capabilities and behaviors the software must provide. State what must happen without supplying the source-level implementation.

### Non-Functional Requirements

Required qualities and measurable constraints such as performance, reliability, security, accessibility, compatibility, and operability. Use `n/a` with a reason when none apply.

### User Stories & Use Cases

Representative actors, triggers, flows, and expected outcomes, including alternate flows when they clarify behavior.

## TDS

### Architecture & Structure

Components, responsibilities, boundaries, dependencies, and data/control flow at the design level. Leave source-level organization and coding choices to the assignee unless an approved requirement or decision constrains them.

### Data Models

Entities, fields, relationships, invariants, ownership, lifecycle, and persistence requirements. Tables and declarative field/type definitions are allowed; implementation code is not.

### APIs & Integrations

External and internal contracts, endpoints or messages, authentication and authorization expectations, versioning, dependencies, and failure behavior. Signatures and concise example payloads are allowed as contract notation; client/server implementation code is not.

### Edge Cases & Error Handling

Boundary conditions, invalid inputs, partial failures, recovery behavior, idempotency, concurrency concerns, and user-visible errors as applicable.

<!-- END SOFTWARE PROFILE -->

<!-- GENERAL PROFILE — keep only when Profile: general -->

## Problem

What existing gap or decision makes this spec necessary? Two or three sentences.

## Goal

The project outcome this work contract exists to enable. Describe the result, not merely the activity.

## Non-goals

Explicit list of things this spec does not cover.

## Interface

The observable contract for the outcome — format, audience, boundaries, sequence, quality bar, names, shapes, or semantics as applicable. It should be precise enough that two assignees working independently would produce acceptably equivalent results.

## Constraints

What the Implementation Plan or a prior spec requires. Cite by section heading.

<!-- END GENERAL PROFILE -->

## References

- `IMPLEMENTATION_PLAN.md` section(s) by heading.
- Matching `phases/PHASE-NN-slug.md` roll-up row(s).
- Prior specs, tasks, or decisions this spec depends on.

## Examples / acceptance

- Inputs and expected outputs when applicable.
- Behavioral, documentary, operational, or experiential examples.
- Boolean observations that demonstrate the contract was met.

## Open questions

Each question names its owner. A spec locks only when every question is closed.

## Review checklist

Filled by the reviewer, not the author.

- [ ] Scope is consistent with `IMPLEMENTATION_PLAN.md`.
- [ ] `Plan refs` point to real plan anchors and matching phase rows.
- [ ] `Profile` matches the outcome governed by this spec, exactly one profile remains, and no template instructions remain.
- [ ] For `software`: the SRS contains Overview & Goals, Functional Requirements, Non-Functional Requirements, and User Stories & Use Cases.
- [ ] For `software`: the TDS contains Architecture & Structure, Data Models, APIs & Integrations, and Edge Cases & Error Handling.
- [ ] For `software`: the spec defines requirements and design boundaries without source code, executable code, pseudocode, step-by-step algorithms, function/class bodies, complete configuration/build files, or copy/paste-ready implementation.
- [ ] For `general`: non-goals are honest and the outcome contract is precise enough to execute without clarification.
- [ ] Constraints include every relevant plan clause.
- [ ] Examples and acceptance evidence are concrete.
- [ ] No open questions remain at lock time.
