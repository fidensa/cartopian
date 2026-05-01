# Widget API — Conventions

This document extends the protocol-level conventions defined in
`protocol/CONVENTIONS.md`. Rules here apply only to this project.

## Project-specific conventions

- All API responses use JSON:API envelope format.
- Schema files use `.schema.json` extension and live in `schemas/`
  within the target repo.
- Error codes follow the format `WIDGET-ENNN` (three-digit counter).
