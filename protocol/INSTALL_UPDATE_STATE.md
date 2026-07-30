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

The contract remains mutation-free. `cli.install_workflow` is the coordinated
adapter layer: it inventories the closed surface set, emits an ordered
affected-surface plan before mutation, applies required or explicitly
authorized work through bounded owners, and then re-inventories every result.
The standalone installer and the `install-workflow` CLI/MCP command consume the
same planner and state record rather than maintaining private status.

For each surface, adapters must supply every field enforced by `build_record`;
in particular, `affected` is the adapter's explicit detection result and
`required` is the policy decision that the surface must be resolved. The two
booleans are independent and neither may be omitted. At terminal evaluation,
an unresolved surface cannot be hidden by marking it non-required: unresolved
required or affected work blocks completion, and any pending or blocked
surface accounting disqualifies an unqualified fully-updated claim. A decline
is terminally acceptable only when a matching operator choice includes
provenance, and it yields qualified completion rather than full completion.
A defer disposition is also provenance-backed and qualified; unlike a decline,
it explicitly names work intended for a later run.

A repair offer is not authorization. A disk update is not running-process
activation. A project migration offer is not migration completion. A terminal
outcome is fully updated only when verified evidence supports every claim.

Client registration and client configuration are separately visible surfaces
owned by one bounded configuration adapter. A disposition supplied for either
surface governs both records; contradictory paired dispositions are refused.
When that adapter succeeds, both surfaces carry the authorized disposition and
verified result.

## Coordinated workflow

The workflow derives tool-owned destinations from the install root and derives
client destinations from the closed supported-client registry. Callers can
select a supported client and choose `accept`, `decline`, or `defer` for an
affected optional surface. A caller assertion is recorded as bounded caller
provenance, not authenticated operator provenance. Callers cannot select an
executable, add a surface kind, or supply a per-surface destination.

Each plan accounts for `core-files`, `mcp-server-files`, `wrappers`, `bridges`,
`client-registrations`, `client-configuration`, `verification-content`, and
`project-schema-migration-offers` in contract order. Required file replacement
uses a staged payload and a recoverable backup boundary. Client configuration
is changed only after authorization, with existing siblings preserved; a
malformed configuration is preserved and refused.

When the install-root state boundary remains writable, every terminal apply
result persists the stable projection at
`<install-root>/install-update-state.json`. Internal paths and adapter traces
are not persisted. A refusal replaces stale earlier success with a `blocked`
record; an operating-system apply failure replaces it with a `failed` record.
Both name the affected surface, attempted action, retry safety, recovery
guidance, and preserved or backup recovery artifact. A secondary failure while
writing that record cannot mask the original apply error; the operator-facing
installer exits with a bounded diagnostic rather than a Python traceback.
Governed-project schema differences appear only as
`migrations` records with `result = not-run`; the supported `migrate-project`
workflow remains separately authorized.

A prior decline suppresses a repeated offer only while its decision context is
unchanged: surface, desired and observed identities, selected supported
clients, source identity and authority, and materialization mode must all
match. A schema-valid `repair-offered` record may supply such a decline while
other adapter choices remain offered. A defer is never carried and is
intentionally eligible to be offered again. Malformed, oversized, incompatible,
or other nonterminal prior state is ignored safely.
