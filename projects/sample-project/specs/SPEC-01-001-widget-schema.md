# SPEC-01-001: Widget Schema

Status: draft Author: PM Reviewer: AI reviewer Date: 2025-01-20 Plan refs: P01-BUILD-001

## Problem

The API needs a formal contract for the widget resource before any endpoint can be built. Without a schema, validation logic will be ad-hoc and inconsistent.

## Goal

Define the wire format for a widget resource so that all endpoints, validators, and consumers share a single source of truth.

## Non-goals

- Database schema design (handled by the data access layer task).
- API envelope format (defined in project conventions).
- Authentication/authorization fields.

## Interface

```json
{
  "id": "string (UUID v4)",
  "type": "string (enum: pending OQ-001 resolution)",
  "displayName": "string (1–255 chars, required)",
  "configuration": "object (arbitrary key-value, required)",
  "archived": "boolean (default: false)",
  "createdAt": "string (ISO 8601 datetime)",
  "updatedAt": "string (ISO 8601 datetime)"
}
```

Required fields on create: `type`, `displayName`, `configuration`. Read-only fields: `id`, `archived`, `createdAt`, `updatedAt`.

## Constraints

- JSON Schema draft-2020-12 (ENGINEERING.md: Tech stack).
- Schema file lives at `schemas/widget.schema.json` (CONVENTIONS.md: project-specific conventions).

## References

- `IMPLEMENTATION_PLAN.md` → Phase 01: Core → P01-BUILD-001.
- `phases/PHASE-01-core.md` → TASK-01-001.
- `ENGINEERING.md` → Tech stack (Ajv, draft-2020-12).

## Test vectors / acceptance

- Valid: `{ "type": "gauge", "displayName": "CPU Load", "configuration": { "unit": "percent" } }` → passes.
- Missing `displayName`: `{ "type": "gauge", "configuration": {} }` → fails with required-field error.
- Wrong type for `configuration`: `{ "type": "gauge", "displayName": "X", "configuration": "not-an-object" }` → fails with type error.

## Open questions

- OQ-001: Widget type enum values (Owner: PM). Must resolve before schema locks.

## Review checklist

- [ ] Scope is consistent with `IMPLEMENTATION_PLAN.md`.
- [ ] `Plan refs` point to real plan anchors and matching phase rows.
- [ ] Non-goals are honest.
- [ ] Interface is precise enough to implement without clarification.
- [ ] Constraints include every relevant plan clause.
- [ ] Test vectors exist and are concrete.
- [ ] No open questions remain at lock time.
