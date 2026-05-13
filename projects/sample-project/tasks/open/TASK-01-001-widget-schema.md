# TASK-01-001: Define widget JSON Schema

Phase: PHASE-01-core Plan ref: P01-BUILD-001 Repo subpath: widget-api Assignee: coder Spec: SPEC-01-001-widget-schema.md Depends on: none Blocked by: none Created: 2025-01-20 Test gate: required

## Goal

Produce a JSON Schema (draft-2020-12) that defines the widget resource. The schema is the single source of truth for validation, documentation, and type generation.

## Plan ref

P01-BUILD-001 — first build item in Phase 01. The schema contract blocks all endpoint work.

## Repo subpath

widget-api — the schema file lives at `schemas/widget.schema.json`.

## Test gate

Required. Test targets:

- `tests/schemas/widget.schema.test.ts` — validates sample payloads against the schema. Must fail before the schema is written.

## Acceptance

- [ ] Schema file exists at `schemas/widget.schema.json`.
- [ ] Schema validates a correct widget payload.
- [ ] Schema rejects payloads missing required fields.
- [ ] Schema rejects payloads with wrong types.
- [ ] Unit tests pass with 100% coverage of required fields.

## References

- `IMPLEMENTATION_PLAN.md` → Phase 01: Core → P01-BUILD-001.
- `phases/PHASE-01-core.md` → TASK-01-001.
- `specs/SPEC-01-001-widget-schema.md` — full interface contract.

## Notes

OQ-001 (widget type enum vs. extensible) affects the `type` field in the schema. PM should resolve this before implementation starts.
