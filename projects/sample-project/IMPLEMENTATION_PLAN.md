# Implementation Plan: Widget API

## Purpose

This plan derives from `REQUIREMENTS.md` (locked) and
`ENGINEERING.md` (locked). It defines the phased build for the Widget
API service.

## Architecture rules

- All validation through JSON Schema — no ad-hoc validation in handlers
  (ENGINEERING.md: Architecture constraints).
- JSON:API envelope on all responses (ENGINEERING.md + FR-006).
- No direct SQL in handlers; data access layer required
  (ENGINEERING.md: Architecture constraints).

## Repo topology

| Repo       | Owns                        | Branch |
| ---------- | --------------------------- | ------ |
| widget-api | API service, schemas, tests | main   |

Single-repo project. All code lives in `widget-api`.

## Phase sequence

### Phase 00: Baseline

**Goal:** Lock requirements and engineering standards. Establish the
project foundation before writing any code.

| Plan ref      | Kind  | Description          |
| ------------- | ----- | -------------------- |
| P00-BUILD-001 | build | Lock REQUIREMENTS.md |
| P00-BUILD-002 | build | Lock ENGINEERING.md  |

**Exit criteria:**

- REQUIREMENTS.md reviewed and locked.
- ENGINEERING.md reviewed and locked.

______________________________________________________________________

### Phase 01: Core

**Goal:** Build the widget schema, CRUD endpoints, and validation layer.

| Plan ref      | Kind  | Description                     |
| ------------- | ----- | ------------------------------- |
| P01-BUILD-001 | build | Define widget JSON Schema       |
| P01-BUILD-002 | build | Implement data access layer     |
| P01-BUILD-003 | build | Implement CRUD endpoints        |
| P01-BUILD-004 | build | Add input validation middleware |

**Exit criteria:**

- Widget JSON Schema locked and tested.
- All CRUD endpoints operational with validation.
- Integration tests passing for all endpoints.

## Requirement coverage

| Requirement | Plan ref(s)                  | Phase |
| ----------- | ---------------------------- | ----- |
| FR-001      | P01-BUILD-003                | 01    |
| FR-002      | P01-BUILD-003                | 01    |
| FR-003      | P01-BUILD-003                | 01    |
| FR-004      | P01-BUILD-003                | 01    |
| FR-005      | P01-BUILD-003                | 01    |
| FR-006      | P01-BUILD-001, P01-BUILD-004 | 01    |
| NF-001      | P01-BUILD-002, P01-BUILD-003 | 01    |
| NF-002      | P01-BUILD-003                | 01    |
| NF-003      | P01-BUILD-004                | 01    |

## Open questions by phase

### Phase 01

- OQ-001: Widget type enum vs. extensible (Owner: PM). Blocks
  P01-BUILD-001.

## Exit criteria summary

| Phase | Exit criteria                                                     |
| ----- | ----------------------------------------------------------------- |
| 00    | Requirements and engineering standards reviewed and locked        |
| 01    | All CRUD endpoints operational, validated, and integration-tested |
