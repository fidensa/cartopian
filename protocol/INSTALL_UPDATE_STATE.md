# Install and update state contract

The executable authority for install and update state is
`cli.install_state`. It owns the schema identity, record version, closed
vocabularies, transition grammar, validation rules, portable-evidence boundary,
canonical ordering, resume projection, and terminal outcome classification.
Adapters and presentation layers consume that authority; they must not maintain
private status enums or infer stronger completion from raw observations.

Run the deterministic machine projection with:

```text
cartopian install-state-contract
```

The MCP server exposes the same command as the `install_state_contract` tool.
CLI and MCP therefore return the same schema identity, field vocabulary,
transition grammar, and evidence exclusions.

## Stable external fields

The projection's `field_boundaries.stable` list is the versioned external
record boundary. Consumers may persist, exchange, and render those fields
according to `schema_identity` and `record_schema_version`. Equivalent facts
are ordered canonically before diagnostics and outcomes are derived.

The record keeps release, installed content, governed-project schema, running
server, and MCP wire protocol as peer identities. Their authority fields are
validated independently, so one cannot prove another. Every installed surface
is also accounted for exactly once. Unknown or unsupported facts remain
explicit; an adapter may not omit them to obtain a stronger outcome.

## Internal diagnostic detail

The top-level `internal` field is outside the stable projection. It may carry
local traces or raw adapter detail during diagnosis, but consumers must neither
persist it as portable state nor depend on it for transitions or terminal
claims. Stable diagnostics use only the closed code, severity, field, detail,
and recovery vocabulary.

Portable checkpoint evidence has a separate closed field allowlist in the
machine projection. It excludes secrets, private prompts or conversations,
project-management identifiers, caller-selected executables, and arbitrary
destinations.

## Adapter boundary

This contract is intentionally mutation-free. Installer, updater, repair,
registration, bridge, wrapper, verification, restart, and migration adapters
provide observed facts and consume the resulting record. Later coordinated
workflow and persistence work may advance checkpoints through the declared
interfaces; it must not reinterpret raw facts independently.

For each surface, adapters must supply every field enforced by `build_record`;
in particular, `affected` is the adapter's explicit detection result and
`required` is the policy decision that the surface must be resolved. The two
booleans are independent and neither may be omitted. At terminal evaluation,
an unresolved surface cannot be hidden by marking it non-required: unresolved
required or affected work blocks completion, and any pending or blocked
surface accounting disqualifies an unqualified fully-updated claim. A decline
is terminally acceptable only when a matching operator choice includes
provenance, and it yields qualified completion rather than full completion.

A repair offer is not authorization. A disk update is not running-process
activation. A project migration offer is not migration completion. A terminal
outcome is fully updated only when verified evidence supports every claim.
