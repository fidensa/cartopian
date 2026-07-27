# ATTEST-NNN: <short title>

This is a reference schema, not an authoring template. Do not copy or edit it
by hand. Only the operator runs `cartopian attest-intent ... --confirm`; that
command computes the hashes and renders the artifact.

Attestation ID: ATTEST-NNN
Status: <current | superseded>
Confirmed by: operator
Confirmed at: YYYY-MM-DD
Source kind: <requirements-intent | decision | operator-intent-record>
Source path: <project-relative eligible source>
Source hash: sha256:<64 lowercase hexadecimal characters>
Required: <true | false>
Scopes: <project | phase:PHASE-NN-slug | plan-ref:PNN-KIND-NNN | task:TASK-NN-NNN | review-kind:planning | review-kind:task-closure>
Sections: <whole-source | exact heading; exact heading>
Supersedes: <ATTEST-NNN | none>

## Operator confirmation

Tool-generated confirmation statement.
