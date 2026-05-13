# Widget API — Requirements

## Thesis

Widget API is a RESTful JSON:API service that provides CRUD operations for widget resources. It is the single source of truth for widget data in the platform. It is not a UI, not a batch processor, and not a general-purpose data store.

## Primary users

- **Frontend applications** — consume the API to render and manage widgets in the console.
- **Internal services** — read widget data for downstream processing.

Not for: end users directly (no public-facing UI), external third parties (no public API yet).

## Product model

Clients send HTTP requests to create, read, update, and delete widgets. Each widget has a type, a display name, configuration data, and lifecycle metadata. All responses follow JSON:API envelope format. All input is validated against a JSON Schema before processing.

## Functional requirements

- FR-001: Create a widget with type, display name, and configuration.
- FR-002: Retrieve a widget by ID.
- FR-003: List widgets with pagination (cursor-based).
- FR-004: Update a widget's display name and configuration.
- FR-005: Delete a widget (soft delete — mark as archived).
- FR-006: Validate all input against the widget JSON Schema before processing.

## Non-functional requirements

- NF-001: P95 latency under 100ms for single-resource endpoints.
- NF-002: All endpoints require authentication via bearer token.
- NF-003: Input validation errors return structured JSON:API error responses with field-level detail.

## Open questions

- OQ-001: Should widget types be a fixed enum or user-extensible? (Owner: PM)
