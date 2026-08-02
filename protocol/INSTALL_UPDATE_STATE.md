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

The installed-content identity a run records covers the whole shipped surface
set, alongside the narrower MCP subset identity the restart projection
compares. A later runtime reading this record for verification therefore
compares the same surfaces it reports as the installed revision, and drift
outside the MCP subset cannot remain `verified`.

A consumer reading a persisted record for verification reads it under the
closed schema and fails closed. A record whose `schema_identity` or
`record_schema_version` is missing, unsupported, or newer than the installed
contract, that does not account for the installed-content identity exactly
once, that attributes it to another identity's authority, or that records an
identity outside the digest grammar, is evidence the consumer cannot
interpret: it never strengthens verification, and no weaker evidence class is
substituted for it. `record_schema_version` is the integer the contract
declares, matched by type as well as by value: a boolean or a numerically
equal float is not that version.

Reading a record is also not the same as reading a *positive* record. A
recorded identity strengthens a consumer's verdict only when the row carrying
it says so in the contract's own vocabulary: a state that is neither outside
the closed state vocabulary, nor one that contradicts the identity, nor one
that leaves it unresolved; a `verification` of `verified`; and the identity
value the row claims to have proven. A row that is unknown, unverified,
dirty, symlink-divergent, malformed, or described in values the vocabulary
does not contain attests only that content the run never proved is still
unproved. Such a record is unusable on the same terms as an unreadable one —
it fails closed, and no weaker evidence class is substituted for it.

That authority is one gate, not one reader's habit. Every fact in the record
is a sibling of the installed-content row and carries no authority of its own,
so a consumer may read a persisted restart row, a persisted surface proof, or
a prior process identity only from a record the same gate accepts. When the
record is unusable, no such sibling fact — and no release receipt or weaker
observation offered in its place — confers installed verification, MCP
verification, `current` state, activation permission, a successful
complete-qualified outcome, or a premature activation claim; the affected
surface is treated as changed rather than assumed unchanged. A compatible,
positive record still answers only for the content it names: one that records
an MCP subset identity other than the observed one attests different content
and cannot strengthen a verdict about this install. That binding is applied to
a persisted restart candidate before any of it is read, not after a verdict has
already been strengthened: the row is prior-process evidence only when the
record's own MCP identity is present, well formed, and equal to the MCP content
being projected. A missing, malformed, substituted, or otherwise inconsistent
recorded MCP identity yields no prior process at all — no `previous_instance_id`,
no verified fresh proof, no `current`/no-restart state, no activation
permission, and no successful complete-qualified outcome — and, as with an
unusable record, the affected surface is treated as changed rather than assumed
unchanged. Every consumer of a persisted restart row applies that one rule, so
no surface can reintroduce the split between record authority and content
authority.

Refusing to read persisted evidence is not the same observation as finding
none, and the two must stay distinguishable to every consumer. A persisted
restart candidate therefore carries one of four verdicts. It is *absent* when
no record was written, or when a compatible record persisted no restart
candidate for this caller: nothing was recorded for the caller to read, other
evidence may still apply, and the surface may still be reported as unchanged.
It is *unusable* when evidence was persisted and this runtime refuses to read
it — the record fails the gate above, or its restart section cannot be resolved
to a single candidate. It is *unbound* when a single candidate exists but the
record's MCP identity does not name the content being projected. It is *bound*
only when the candidate may be read as prior-process evidence about this
content. Both refusal classes withhold the row entirely and make the MCP
surface restart-relevant, because whatever wrote the record may have changed
that surface; neither may be reported, planned, or persisted as absence. The
MCP-scoped verdict
stays its own fact throughout — drift elsewhere in the shipped surface set
leaves it unproven rather than restating a wider verdict a restart could not
repair.

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

Restart state is projected by `cli.restart_state` from four independent
inputs: installed MCP identity, connected process and loaded-content identity,
verified affected-surface evidence, and current client identity. Its
`status`/`reason_code` pair is deterministic. A restart-needed record carries
one direct instruction for the current supported client and one expected proof
condition. It never authorizes client control.

Fresh proof requires both a new process/instance identity relative to the
persisted baseline and verified loaded MCP content matching the installed MCP
identity. The baseline itself is admissible only when the record it comes from
is bound to the MCP content being projected; otherwise the projection carries
no prior process and no freshness claim. A new process serving old or unknown
content remains restart required. A verified `mcp-server-files` unaffected fact is the only
affected-surface boundary that can suppress restart without process proof.
The `mcp-server-files` surface covers every piece of content the connected
server serves in-process: the `mcp_server` package, the `cli` package its tool
calls dispatch into, and the MCP entry shims. A release that changes CLI
behavior is therefore MCP-affecting and requires the same fresh-process proof.

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
other adapter choices remain offered. A repair defer is never carried and is
intentionally eligible to be offered again. Malformed, oversized, incompatible,
or other nonterminal prior state is ignored safely.

A `project-schema-migration-offers` deferral is the one deferral that does
carry, because the offer it defers is a standing fact rather than a one-time
repair. It is bound to the decision that produced it: the recorded decision
context — validated source identity and authority, target schema, and the full
offer set — must equal the context the current run would produce, and each
individual offer must still match on project identity, current and target schema
identities, applicability, and named supported workflow. A changed source is a
different authority proposing different content, so its offer is answered afresh
even when the schema identities coincide; any material change to the offer set
likewise re-offers the work. That surface accepts only
`defer` through this workflow — running a migration remains the separately
authorized `migrate-project` workflow, so `accept` is refused rather than
reinterpreted as authorization.

## Resumable progress

`cli.resume_state` is the persistence boundary around the state contract. It
owns a closed, versioned progress envelope bound to exactly one source identity
and one run identity, and it never mutates an installation, executes a recovery
action, or derives a destination or executable from persisted content. Every
path it touches is one of three fixed names under the install root:

- `install-update-progress.json` — the progress envelope.
- `install-update-progress.quarantine.json` — a predecessor that this run may
  not consume, preserved for recovery.
- `install-update-progress.lease` — the exclusive-creation claim on the root.

The envelope carries `progress_schema_identity`, `progress_schema_version`,
`status`, a bounded monotonic `sequence`, the bound `run`, the open mutation
`boundary`, the adapter-declared `surface_profiles`, the `terminal` markers, the
`retention` class, the deterministic `recovery` note, and the stable state
projection under `progress`. Bounded sequence evidence stands in for wall-clock
timestamps: equivalent progress over equivalent observations must serialize to
identical bytes, which a clock cannot do.

Writes are recoverable: the payload is staged, flushed, fsynced, and renamed
over the target, so a reader observes either the previous complete record or
the new complete one, never a partial write. The parent-directory fsync that
also makes the rename itself durable across a power loss is attempted and
skipped where the platform does not support it; on those platforms an abrupt
power loss can lose the last write while still never exposing a truncated one.

### Mutation boundaries and marker ordering

Intent is persisted before the mutation and evidence after it. A run opens a
boundary naming the surface, action, retry class, and observation capability;
performs the mutation; then commits the verified checkpoint, which closes that
boundary. A crash inside that window leaves an open boundary and an
`in-progress` checkpoint, which resume reports as uncertain — never as
completed and never as work to replay blindly.

Markers advance last and in order, each as its own durable write: schema, then
completion, then the visible `install-update-state.json` mirror, then cleanup.
A permission failure or disk exhaustion at any point can therefore only leave a
weaker claim than the work performed, and no completion marker advances beyond
verified evidence. Cleanup is itself recoverable and retains the evidence that
explains the outcome; when it supersedes a quarantined predecessor it keeps
that record's content identity.

### Retry safety and observation capability

Each surface adapter declares how safely its action repeats and how much of its
result can be re-observed:

| Surface | Retry safety | Observation |
| --- | --- | --- |
| `core-files` | `idempotent` | `observable` |
| `mcp-server-files` | `idempotent` | `observable` |
| `wrappers` | `idempotent` | `observable` |
| `bridges` | `idempotent` | `observable` |
| `client-registrations` | `inspect-before-retry` | `partially-observable` |
| `client-configuration` | `inspect-before-retry` | `partially-observable` |
| `verification-content` | `idempotent` | `observable` |
| `project-schema-migration-offers` | `refuse-replay` | `unobservable` |

Tool-owned content is replaced through a staged, digest-verified boundary, so
repeating it converges. Client registration and configuration merge into
operator-owned files whose non-Cartopian siblings cannot be fully re-derived, so
a partial merge is inspected rather than replayed. A project schema migration is
externally visible and not idempotent, so resume never replays it; it can only
be re-offered.

### Resume assessment and recovery

Resume compares persisted intent and evidence against current authoritative
observations and emits a remaining-work plan; it never replays actions itself.
A checkpoint is reusable only when it is completed, verified, evidence-backed,
and its recorded observation still matches what is observed now. Compatibility
is one of `compatible`, `absent`, `stale`, `source-mismatch`, `run-conflict`,
`lease-conflict`, `orphaned`, `evidence-missing`, `corrupted`, or
`unsupported-newer`, and each maps to a fixed, bounded recovery action list.
Only `compatible` and `stale` permit any reuse.

Mixed state is diagnosed per surface as `current`, `stale`, `missing`,
`declined`, `pending`, `unverified`, `blocked`, or `unsupported`. A bounded
refusal or an operating-system failure that demonstrably preserved its target
is remaining work rather than uncertain work; only a failure part-way through a
replacement requires inspection.

A record that is truncated, malformed, oversized, symlinked, or missing
evidence for a completed claim fails closed and is quarantined before a new run
starts, so the failure remains inspectable. A quarantined record that is still
parseable is relabelled `quarantined` for whoever inspects it; one that is
truncated or malformed is kept byte-for-byte as found, because rewriting it
would destroy the evidence that explains the failure. Exactly one progress
record exists per install root, so supersession happens in place and needs no
status of its own. A record written by a newer progress schema is refused and
left untouched — an older tool never rewrites it. Applying over an uncertain boundary is refused until the caller asserts,
per closed surface, that it inspected that boundary; a `refuse-replay` surface
is refused even then.

### Preservation before replacement

Whether stored bytes are *usable* and whether they may be *replaced* are
different questions, and apply gates on the second. Mutation is authorized by
the full resume compatibility assessment, not by the intrinsic read
classification, and the assessment is re-derived against the record actually on
disk once the lease is held — a plan-time snapshot cannot authorize replacing a
record that arrived after it.

The re-derivation is the complete assessment, not a partial one, and every gate
runs again against it before anything is preserved, quarantined, or written:
compatibility, uncertain work, refusal of non-repeatable replay, source and run
identity, and per-surface inspection. A boundary that becomes visible only under
the lease is therefore refused on exactly the terms it would have been refused
at plan time, and the record carrying it is left byte-for-byte as found until
the caller asserts it inspected that surface. A run identity's open mutation
boundary is uncertain to every other run, whether or not the projection holds a
checkpoint row describing it. A classification that appears only after planning
and has no defined disposition fails closed rather than proceeding on the
plan's conclusion.

`compatible`, `absent`, and `stale` proceed. `unsupported-newer` refuses and
leaves the record untouched. `corrupted` and `evidence-missing` quarantine under
the rule above: those records are unusable in themselves, so the earliest
failure is the useful one and a later duplicate may be dropped.
`source-mismatch`, `run-conflict`, and `orphaned` are the opposite case — the
record reads perfectly and is the last useful recovery evidence for a different
source, run, or installation. Each is preserved before any new envelope is
written, under two rules:

- It is retained byte-for-byte and never relabelled. Its content identity is
  the evidence, so rewriting it would destroy what it proves.
- A preservation slot already holding a different record is a refusal, not
  something to overwrite. A second changed source therefore cannot consume the
  evidence the first one preserved.

The recovery note carries `preserved_classification` alongside the preserved
record's `quarantine` name and `quarantined_identity` for the life of the
envelope: `classification` describes this run, `preserved_classification`
describes the record this run had to set aside, and both are needed for the
outcome to stay explainable after recovery. That provenance is carried across
run boundaries as well, because a preserved record is superseded only by
terminal proof and the run that reaches that proof is often a later one; its
content identity survives the supersession that removes the file.

### Exclusive ownership

Duplicate and concurrent invocation is bounded by an exclusive-creation lease
recording owner, run, process, and host. A second run refuses rather than
interleaving, and cannot consume the holder's evidence. A lease whose holder is
provably gone on the same host is taken over and the takeover recorded, so a
crashed run does not deadlock recovery.

Takeover is a compare-and-remove, not a check followed by an unlink: an unlink
removes whatever occupies the pathname when the syscall runs, which is not
necessarily the object that was inspected. The lease bytes are captured before
the liveness probe; the object is then moved out of the pathname with an atomic
rename, so of two concurrent recoverers exactly one wins and the loser sees the
pathname already free. The winner compares the moved object against the bytes it
inspected and only then removes it; on a mismatch the object is restored through
an exclusive-creation hard link, which fails closed rather than clobbering a
lease installed meanwhile. A claim is finally confirmed by reading the lease back
and requiring that it still names this owner. Two recoverers inspecting one
orphan therefore produce exactly one live owner and one refusal, and at most one
owner ever crosses a mutation boundary.

Two residual limitations: the liveness probe exists on POSIX only, so elsewhere
an abandoned lease is reported as a conflict requiring explicit operator
recovery; and the mismatch restore uses `os.link`, which is unavailable on
FAT/exFAT volumes and some network filesystems. Where the restore cannot run,
the moved object is left under its private name and the caller refuses rather
than claiming — a refusal requiring operator recovery, never two owners.

### Status, diagnosis, and portable evidence

```text
cartopian resume-install <source-root> <install-root> [--portable-evidence]
```

`resume-install` is read-only: it diagnoses persisted progress against current
observations, emits the deterministic remaining-work plan, and performs no
mutation, restart, or recovery action. The MCP server exposes the same command
as the `resume_install` tool, so CLI and MCP return identical status,
diagnosis, and resume-planning records.

`--portable-evidence` additionally emits a portable record and its
operator-readable rendering, explaining version identities, per-surface state,
restart need, and remaining work.

That record has exactly one authority. Version identities, per-surface state,
and checkpoint status are read from one progress envelope; remaining work is
read from the resume assessment. Those two describe the same installation only
while they agree on who produced it, so the persisted envelope supplies the
record only when the assessment says it is reusable — schema readability is not
that test. Otherwise the record is built wholly from current observations, and a
disagreement between the two is refused outright rather than emitted. The
`predecessor` field classifies the prior record without merging it: its
compatibility classification, whether it was reusable, whether its authority was
used, and the superseded source identity when one exists. Only closed vocabulary
and content identities cross that boundary; the assessment's prose detail does
not, because it names internal recovery state.

The view is deliberately separate from
internal recovery metadata: the run marker, boundary, sequence, retention class,
quarantine state, and recovery classification stay on the internal side. It
excludes secrets, prompt bodies, private conversation content, unrelated
work-root data, project-management identifiers, caller-selected executables,
and caller-selected destinations, and the emission is refused outright if any
of those appear.
