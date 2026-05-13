# DEC-001: Use JSON Schema for validation

Date: 2025-01-15 Status: locked Supersedes: none

## Context

The API needs input validation. Options considered: Zod (TypeScript- native), JSON Schema (language-agnostic), or manual validation.

## Decision

Use JSON Schema (draft-2020-12) with Ajv as the validator. Schemas live in `schemas/` in the target repo and serve as the single source of truth for validation, documentation, and type generation.

## Consequences

- All input validation goes through JSON Schema — no ad-hoc checks.
- Schema files are the interface contract, not TypeScript types.
- Non-Node consumers can validate without Ajv by using any compliant JSON Schema validator.
