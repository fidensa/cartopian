# DEC-NNN: <short descriptive title>

Date: YYYY-MM-DD
Status: <locked | open>
Authority: <operator | pm>
Ruling: <one line, at most 240 characters: what was decided>
Supersedes: <DEC-NNN | none>

A recorded decision is immutable: once it is `locked` or superseded,
`write-decision` refuses to rewrite it. Change a ruling with a new decision
that names it under `Supersedes:` (retires it) or `Amends: DEC-NNN` (adds to
or narrows it while it stays in force). Only an `open` draft may be re-issued.

`Authority:` says who the ruling speaks for. `operator` means the operator
made it, and the decision must name the captured turns that show it (the
marker below); `write-decision` confirms every named turn before writing.
`pm` is the PM's own call. A PM decision may not supersede or amend an
operator ruling, may not ratify, and may not leave an operator request out of
the plan while locked. When the header is omitted, `write-decision` stamps
`operator` if the body names a captured turn and `pm` otherwise. `plan-audit`
lists locked PM decisions for the operator; an operator decision adopts them
with `Ratifies: DEC-NNN[, DEC-NNN]`.

Two headers belong to plan close and only to operator decisions.
`Closes plan: early` ends the plan with its work unfinished.
`Carries forward: DEC-NNN[, DEC-NNN]` names the standing rulings that
`reset-plan --carry` keeps for the next plan. A carried ruling must be in
force and not plan-scoped: it may not bind `task:` or `planning:` evidence,
dispose of operator requests, or close the plan. A ruling and its amendments
carry together. One decision does not do both.

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

A locked ruling already reaches every unit that its text names or that cites
it: its captures are carried into that unit's request trace, so extending a
ruling needs no new decision. When a decision must be superseded anyway, a
superseding decision that keeps the ruling unchanged restates it by pointer,
not by copy. It supersedes the target and names it under `Restates:`;
`write-decision` pins the target's digest. The target's ruling and bindings
count as this decision's own, so the body carries only what is new:

```markdown
Supersedes: DEC-NNN
Restates: DEC-NNN

Operator request evidence for: task:TASK-NN-NNN: cs-<handle>/turn-<N>
```

An operator decision may also record that the plan deliberately leaves an
operator excerpt unclaimed by every task. That is the one authorized
plan-level disposition `plan-audit` and `close-audit` accept, and it is
structural: one line naming the excerpt's content identity, exactly as the
task trace names it. It authorizes only while the decision is
`Status: locked`, speaks for the operator, and no later decision names it
under `Supersedes:`; an open, superseded, or PM decision authorizes nothing. A
PM may still propose a disposition in an `open` decision for the operator to
rule on.

```markdown
Out-of-plan request: sha256:<64 hex>
```

## Context

Why this decision was needed.

## Decision

What was decided.

## Consequences

What changes as a result.
