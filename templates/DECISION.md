# DEC-NNN: <short descriptive title>

Date: YYYY-MM-DD
Status: <locked | open>
Supersedes: <DEC-NNN | none>

Ordinary decision prose is PM-derived guidance. A new decision preserves
verbatim operator evidence only with the exact structural marker below,
immediately followed by one Markdown block quote. The marker binds the quote to
one governed unit (`project:project`, `planning:PLAN-NNN`, or
`task:TASK-NN-NNN`); loosely adjacent attribution and ordinary block quotes are
not evidence.

```markdown
Operator request quote for: task:TASK-NN-NNN

> <unmodified operator quotation>
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
