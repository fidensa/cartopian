# DEC-NNN: <short descriptive title>

Date: YYYY-MM-DD
Status: <locked | open>
Supersedes: <DEC-NNN | none>

Ordinary decision prose is PM-derived guidance. A decision names operator
evidence only by reference to turns the host intake adapter captured, with
the exact structural marker below. The marker binds one or more capture
identities (from the evidence lookup; never typed from memory) to one
governed unit (`project:project`, `planning:<checkpoint-id>`, or
`task:TASK-NN-NNN`). Text and provenance always come from the capture. An
optional block quote directly under the marker must equal the captured text
whole, or the reference is unconfirmed. Block quotes without a reference,
the retired `Operator request quote for:` marker, loosely adjacent
attribution, and any file the PM writes under `requests/` are not evidence.

```markdown
Operator request evidence for: task:TASK-NN-NNN: cs-<handle>/turn-<N>[, cs-<handle>/turn-<M>]

Repeat this header for every governed unit when one captured ruling authorizes several tasks or checkpoints. The same capture identity may appear on each header in this decision.
```

A decision that extends a locked ruling to more units without changing it
restates the ruling by pointer, not by copy. It supersedes the target and
names it under `Restates:`; `write-decision` pins the target's digest.
The target's ruling and bindings count as this decision's own, so the body
carries only the new bindings:

```markdown
Supersedes: DEC-NNN
Restates: DEC-NNN

Operator request evidence for: task:TASK-NN-NNN: cs-<handle>/turn-<N>
```

A decision may also record that the plan deliberately leaves an operator
excerpt unclaimed by every task. That is the one authorized plan-level
disposition `plan-audit` and `close-audit` accept, and it is structural: one
line naming the excerpt's content identity, exactly as the task trace names it.
It authorizes only while the decision is `Status: locked` and no later decision
names it under `Supersedes:`; an open or superseded decision authorizes nothing.

```markdown
Out-of-plan request: sha256:<64 hex>
```

## Context

Why this decision was needed.

## Decision

What was decided.

## Consequences

What changes as a result.
