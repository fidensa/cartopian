# DEC-NNN: <short descriptive title>

Date: YYYY-MM-DD
Status: <locked | open>
Supersedes: none

<!-- Three further headers are optional and feed the optional cross-plan
     continuity artifact (protocol § Plan Continuity). Omit them and this
     decision stays valid; it simply is not a continuity row. They are
     validated fail-closed at `cartopian write-decision`, which refuses with
     `usage` and exit 2 before anything is written.

     Scope:  one routing word or short phrase. <=24 characters, <=48
             serialized bytes. Single line, no `|`, no control character.
     Ruling: one sentence stating what is required or forbidden, not why.
             <=100 characters, <=200 serialized bytes. Same cell rules.

     Supersedes: and Expires: share one grammar:

       Supersedes: <none | REF[, REF]...>
       Expires:    <none | REF[, REF]...>
       REF       := DEC-NNN | PLAN-NNN/DEC-NNN

     `DEC-NNN` names a decision in the plan window now closing. The qualified
     `PLAN-NNN/DEC-NNN` names a decision recorded in a closed plan window and
     is the only form that can reach a prior plan's ruling. At most eight
     references per header.

     `Supersedes:` asserts that a replacement exists, so a decision carrying
     it must publish its own `Scope:` and `Ruling:`. `Expires:` asserts that a
     ruling ends with nothing replacing it — a decision whose entire content is
     "this rule no longer applies" carries `Expires:` and no Scope/Ruling pair
     at all, removes its targets, and creates no row of its own. -->

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

## Context

Why this decision was needed.

## Decision

What was decided.

## Consequences

What changes as a result.
