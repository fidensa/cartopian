# Delivery gate

This file explains the domain-neutral delivery gate in plain language. It defines no behavior of its own. Every machine value below is owned once, in `protocol/delivery-contract.json`, and projected here. When this file and that registry disagree, the registry is correct and this file is a defect.

The gate exists for one failure: **an artifact being mistaken for an outcome.** A finished file, notice, dataset, build, or decision is a work product. It is not the same thing as the recipient having it, the system running it, the audience acting on it, or the target state having changed. A plan that closes on artifact completion alone has recorded the wrong fact.

So a plan that produces an outcome reaching anything outside itself carries one delivery contract naming seven things: **owner, target, acceptance evidence, success signals, contingency, immediate verification, and follow-up timing.** Closeout cannot report success while any of them is missing or falsely represented as complete.

The gate validates. It never delivers. It performs, schedules, transmits, and authorizes nothing, and it holds no credential that would let it. Whether an external action happens is the operator's decision, recorded in the contract as an authority state.

## Where the record lives

The delivery contract is plan-scoped, so it lives in the plan that produces the outcome:

```markdown
## Delivery contract
```

— one `## Delivery contract` section in `IMPLEMENTATION_PLAN.md`. It is not a new lifecycle directory, it survives until closeout with the rest of the live plan surface, and it is snapshotted by `archive-plan` along with the plan itself.

Each row is one Markdown list item:

```text
- <Label>: <value>; <Sub-label>: <value>; <Sub-label>: <value>
```

The row's identity is the label of its first part. A semicolon separates parts, a colon separates a label from its value. A part whose label the contract does not declare for that row is an error, not something quietly dropped — so a stray semicolon inside a value is reported rather than silently truncating the value it belongs to.

## The applicability declaration

The section's first row says whether this plan delivers anything at all:

| Declaration | Meaning |
| --- | --- |
| `- Delivery: required` | The plan produces an outcome reaching a target outside itself. Every row below is required. |
| `- Delivery: not-applicable; Justification: <why>` | The plan reaches no target outside itself. No obligation row may be present. |

`not-applicable` is a claim the operator makes on the record and must justify. It is not the same as saying nothing: a plan with no delivery-contract section at all is **undeclared**, which fails closed. Missing and justified not-applicable are distinct states, and the gate reports them as distinct.

## The seven semantics

Every row is domain-neutral. A software release, a policy notice, a research finding, a custody transition, and a practice adoption use the same seven rows with the same sub-labels. A domain example may illustrate a row; it never defines one.

| Row | What it records | Required parts |
| --- | --- | --- |
| `Owner` | The named party accountable for the delivery. | `Availability` — `available` or `unavailable` |
| `Target` | What or whom the outcome reaches. This is the identity every later row must name. | `Kind` |
| `Acceptance evidence` | The observation that the target accepted the outcome. | `Target`, `Observer`, `Authority` |
| `Success signals` | What distinguishes a delivered outcome from an undelivered one. | `Target`, `Observable` |
| `Contingency` | What happens when the delivery goes wrong. | `Kind`, `Rollback`, `Trigger`, `Owner`, and `Because` when rollback is inapplicable |
| `Immediate verification` | The observation of the target's own state, taken at delivery time. | `Target`, `Evidence`, `Time`, `Result` |
| `Follow-up` | The remaining question, its owner, and when it is answered. | `Owner`, `Due`, `Status` — or the value `not-applicable` with a `Justification` |

Two further rows carry the states the seven semantics are read against:

| Row | What it records | Declared values |
| --- | --- | --- |
| `Authority` | The operator authority state for the external action. | `operator-authorized`, `pending-operator-authorization`, `declined` |
| `Artifact` | Whether the work product itself is finished. | `complete`, `incomplete` |

`Authority` records a state the operator established; it never grants one. The contract cannot authorize an external action, and no value in it makes one happen.

## Three states, kept apart

The result carries three states that are deliberately independent:

- **Artifact** — `complete` when the promised work product exists and passed its artifact-level check.
- **Outcome** — `verified` only when the target's own state was observed and the observation passed. Otherwise `unverified`, `pending-authority` (the external action is not authorized yet), or `not-delivered` (the operator declined it).
- **Follow-up** — `scheduled` when a question is open, owned, and bounded in time; `satisfied` when it is closed; `not-applicable` when the record says why; `required` when an obligation exists but is not owned, timed, or justified.

A complete artifact never implies a verified outcome. A verified outcome never implies a discharged follow-up. An operator who declines the external action gets an honest `not-delivered`, never a `verified`.

## What fails closed

Each of these produces an ordered finding with a named recovery, and any finding blocks the gate:

- **Missing** — a required row or part is absent.
- **Placeholder** — a required value is still `TBD`, `<who receives this>`, `n/a`, `someone`, or another declared placeholder. A placeholder records nothing, so it never satisfies a field.
- **Unavailable owner** — the named delivery owner is recorded as unavailable, so no one is accountable.
- **Changed target** — acceptance evidence, a success signal, or a verification names a different target than the record's `Target` row. Evidence about a target the plan is no longer delivering to does not support this delivery; when the target genuinely changed, the new target must be re-observed.
- **Self-certified evidence** — the acceptance observer is the delivery owner. The target's acceptance then rests on the deliverer's own account, which is exactly what acceptance evidence exists to avoid.
- **Unobservable success** — the success signal names no condition a reader could check on the target.
- **Contingency without a trigger, owner, or action** — a contingency nobody starts, at no named condition, is not a contingency.
- **Unjustified rollback substitution** — a `correction` or `alternate` path may stand in for rollback only where the record says why rollback does not apply. `Rollback: available` must pair with `Kind: rollback`; `Rollback: inapplicable` must pair with a correction or alternate kind and a `Because`.
- **Untimed verification** — an immediate verification with no time it was taken cannot be placed against the delivery.
- **Unbounded follow-up** — a `Due` of `later`, `eventually`, or `ongoing` can be deferred forever. A digit is what bounds a time, so it decides first: a `Due` carrying a date, timestamp, or counted interval is bounded, and `no later than 2026-09-13` is a date rather than the word `later`. A `Due` with no digit is bounded only when it names a declared cadence such as `monthly` or `quarterly`, and an unbounded word or phrase disqualifies even that — `ongoing monthly` is not a due time. An `Immediate verification` `Time` never accepts a cadence: an observation happened at a moment.
- **Contradiction** — a passing target observation recorded while authority is pending or declined, or while the artifact is incomplete. An unauthorized action left no target state to observe; an unfinished work product reached no target.

Qualitative evidence is valid, and much delivery evidence is: a council's minuted receipt, a custodian's countersignature, a coordinator's return register. What the gate requires is that the observation and the authority behind it be explicit — who observed it, and on what basis their account counts.

## When the operator declines

A declined or unauthorized external action blocks the gate. That is the gate working: the delivery obligation the plan declared is not discharged by declining to perform it, and the record must not be rewritten to say otherwise.

There are exactly two honest ways past it, and both belong to the operator:

- **The action happens.** The operator authorizes it, the target is observed, and the record gains that observation, its evidence identity, and its time.
- **The obligation changes.** The operator decides the plan no longer carries this delivery, records that as a decision, and the delivery contract is amended to match — to a different target, or to a justified `Delivery: not-applicable`.

An operator who declines the action and wants the plan closed anyway is choosing the second path. That choice is theirs to make explicitly and on the record; it is not something the gate infers, and not something an assignee edits into the contract on their behalf.

## Where the gate runs

One validator serves every surface, so the answer is the same wherever it is asked:

| Surface | What it does with the gate |
| --- | --- |
| `cartopian validate-delivery <project-root>` | The detail view: full states, ordered findings, and each finding's recovery. Exits non-zero when the gate is blocked. |
| The `validate_delivery` MCP tool | The same handler, the same record. CLI and MCP never diverge. |
| `cartopian close-audit` | Folds every finding into `blocking_reasons` and carries the full result under `delivery`. Closeout cannot report `closable` while a delivery obligation is unmet. |
| `cartopian compose-state` | Carries bounded status under `delivery`: the three states, the verdict, the finding count, and the first finding's code. |
| `cartopian next-action` | The same bounded status at startup. It is status, not a blocker — the delivery gate is a closeout gate, so an unmet obligation mid-plan is something the session must see, not something that halts it. |
| `cartopian review-context --review-kind planning` | Projects the result and the delivery-contract section itself, within a declared byte bound. A task-closure review receives none of it. |

Status stays compact and detail stays on demand: the state and startup surfaces carry the verdict, not the record.

Validation is a pure read. The same record always yields the same findings, the same states, and the same contract identity — so re-running the gate never changes a verdict, and a partial record never drifts toward looking complete.

## Unattended runs

An unattended run may prepare and validate a delivery record. It may not cross the external-action boundary. A record that needs authorization stays `pending-authority`; nothing in the unattended path converts that into a delivered outcome.

## Existing plans

A plan authored before this contract carries no delivery-contract section. `undeclared` is that one legacy state, and it fails closed with the recovery named.

The migration is one authoring action per plan: add a `## Delivery contract` section — `Delivery: required` with the declared rows, or `Delivery: not-applicable` with a justification. There is no data to convert and no second accepted form; the gate names the missing rows, and a plan is migrated once its section validates. The contract accepts exactly one record shape during and after the migration, so no dual schema is created.

Because the requirement is breaking for every existing plan, the project schema marker gates it. It landed as protocol version `v0.12.0` in `cartopian://protocol/CHANGELOG`, and a project still marked below that has not adopted it. Cartopian refuses to represent such a project as current rather than letting it reach closeout and be rejected there:

- `cartopian validate-task-readiness` fails its `project-schema-current` check, so no task starts against a plan that predates the contract.
- `cartopian next-action` raises a migration blocker at startup.
- `cartopian migrate-config --apply` refuses to advance the marker while the plan carries no authored delivery record, and names the finding and the one action that recovers it.

The migration writes only the marker. No shipped transform authors a delivery record on the operator's behalf — the section is written through `cartopian write-plan` on operator approval, and re-running the migration on a conforming project is a no-op.

### What counts as adopted

Every finding the contract can emit carries a declared class, and the two classes answer different questions.

| Class | What it means | Blocks adoption? | Blocks closeout? |
| --- | --- | --- | --- |
| `record-form` | The plan has not been authored into the accepted record form: the section is absent, duplicated, unreadable, incomplete, still a placeholder, or self-contradictory. | Yes | Yes |
| `record-semantics` | The record is complete and coherent, and reports a state that does not satisfy the gate — an unavailable owner, unsupported evidence, an unverified or failed observation, a pending or declined external action, an unfinished artifact. | No | Yes |

The split matters in both directions. A copied-but-unfilled template is not an adopted contract, so the marker does not advance past it. But a plan that honestly records `Result: not-run` or `Authority: declined` *has* adopted the contract — it is saying something true about where it is — so it migrates, and stays blocked at closeout until the state itself changes. Adoption and satisfaction are separate questions, and conflating them would push plans toward recording a delivery that never happened.
