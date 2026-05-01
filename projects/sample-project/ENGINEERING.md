# Widget API — Engineering Standards

## Tech stack

- **Runtime:** Node.js 20 LTS
- **Framework:** Express 5
- **Validation:** Ajv (JSON Schema draft-2020-12)
- **Database:** PostgreSQL 16
- **ORM:** Drizzle
- **Testing:** Vitest

## Code standards

- ESLint with `@typescript-eslint/recommended`.
- Prettier for formatting.
- Strict TypeScript (`strict: true`).

## Architecture constraints

- All validation through JSON Schema — no ad-hoc checks in handlers.
- No direct SQL in route handlers; all queries go through the data
  access layer.
- Every endpoint returns JSON:API envelope format.

## Testing standards

- Unit tests for all schema validators.
- Integration tests for all API endpoints.
- Minimum 80% line coverage on new code.
