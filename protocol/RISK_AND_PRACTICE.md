# Risk and practice extension contracts

This file explains the risk, judgment, and practice-pack contracts in plain language. It defines no behavior of its own. Every machine value below is owned once, in `protocol/risk-and-practice-contract.json`, and projected here. When this file and that registry disagree, the registry is correct and this file is a defect.

The contract covers three independent risk/practice mechanisms:

1. **Risk classification** turns a few observable facts about a task into a band, and each band derives what evidence, review, operator approval, and contingency the work is expected to carry.
2. **Judgment guidance** offers a short card at a lifecycle boundary where a real failure exists that no deterministic check can decide.
3. **Practice-pack selection** admits at most one optional specialist guidance body into active context, or none.

These three are independent. A task can carry a risk band with no pack and no card, a pack with no card, or a card with no pack. **None of them silently activates another.** A pack cannot raise or lower risk. A band cannot select a pack or activate a card. A card cannot select a pack or change the band. The contract records these as forbidden edges so the separation is checkable rather than merely stated.

The same machine authority also owns the active `source_guidance` extension. Source guidance is not a fourth risk/practice activation mechanism: it is a domain-neutral record projected through existing task, spec, prompt, evidence, handoff, and validation surfaces. It can name authority and fail closed without deriving a risk band, activating a judgment card, or selecting a practice pack.

The core stays domain-neutral. Risk, evidence, authority, delivery, and follow-up are core concepts for any kind of work. Software, research, marketing, operations, and policy are optional profiles, selected only when the task's own facts match them.

## Source guidance

Source-backed work declares one owner: the task, its named spec, or `n/a`. The owning record names authoritative sources, an applicable effective date, publication date, edition, revision, or version for each source, a conflict disposition and governing rule/decision authority, and every claim that remains explicitly unverified.

Each source carries `current`, `stale`, or `unknown` applicability. Each conflict carries `none`, `resolved`, or `unresolved`. Each unverified claim carries `decisive` or `non-decisive` plus the missing authority/evidence, consequence of proceeding, and next decision/proof. A non-decisive claim may remain explicit. A decisive claim may not be treated as completed source-backed work while it is unverified.

Missing authority, absent or stale date/version context, unresolved source conflict, and decisive unverified claims are dominance conditions. Any one fails closed with an actionable reason; other favorable observations never average it down. The record contains no numeric score.

`task-bundle` and `handoff-packet` expose the same resolved `source_guidance` object, including a deidentified assignee rendering. `validate-task-readiness` and `dispatch` refuse invalid declared records. Complete source-backed reports carry the same shape under `## Source evidence`; `parse-report` and `report-action` project it and refuse completion when governing sources are absent or a decisive claim remains unverified. CLI and MCP use the same command handlers and runtime projection.

## Risk classification

### The five observations

Classification reads five observable conditions. Each describes the task or the current state — never an agent's confidence, and never a probability. There are no percentages anywhere in this contract.

| Observation | The question it answers |
| --- | --- |
| `consequence-reach` | How far do the effects of this work reach? |
| `reversibility` | If this turns out wrong, how is it undone? |
| `authority` | Does existing authority cover this, and what does it commit? |
| `ambiguity` | How settled are the inputs, exclusions, and success conditions? |
| `evidence-coverage` | Can the decisive success condition actually be proven? |

Each observation is recorded in exactly one state. Every state carries a band floor — the lowest band that state permits.

A recorded observation carries four things:

| Field | What it holds |
| --- | --- |
| `observation` | Which declared observable condition this is. |
| `state` | The single declared state it was recorded in. |
| `supporting_fact` | The task fact, current-state observation, policy value, or evidence result that supports the state. An unsupported state is recorded as `unknown`, never as favorable. |
| `elevates_governance` | True when the state's band floor is above the lowest band, so an operator can see which observations raised the expectations. |

**`consequence-reach`**

| State | Band floor | Meaning |
| --- | --- | --- |
| `local-artifact` | routine | Effects stay inside a bounded draft, sandbox, or readily inspectable local artifact. |
| `project-internal` | bounded | Effects reach other work inside the project or team but no external party, system of record, or obligation. |
| `external-or-material` | consequential | Effects reach people outside the work, external systems, money, shared assets, or another material obligation. |
| `broad-or-binding` | critical | Effects could propagate broadly or create a binding commitment that others rely on. |
| `unknown` | consequential | Reach could not be established; it is recorded as missing, never as contained. |

**`reversibility`**

| State | Band floor | Meaning |
| --- | --- | --- |
| `direct-undo` | routine | A direct undo is known, available, and cheap to verify. |
| `bounded-recovery` | bounded | Recovery is multi-step but known, available, and already demonstrated. |
| `recovery-dependent` | consequential | Recovery depends on state that must itself be verified, or destroys intermediate state. |
| `unrecoverable` | critical | No recovery path exists, or the available path is known to be incompatible with the effect. |
| `unknown` | consequential | The recovery path could not be established; an unproven undo is not a demonstrated one. |

**`authority`**

| State | Band floor | Meaning |
| --- | --- | --- |
| `covered` | routine | Existing authority clearly covers the action and no new commitment is created. |
| `new-commitment` | bounded | Authority covers the action, but it creates a durable commitment that outlives the task. |
| `unconfirmed` | consequential | Intent, approval, ownership, or the scope of authority has not been confirmed for this action. |
| `absent` | critical | Required authority is missing, contradicted, or reserved to someone who has not granted it. |
| `unknown` | critical | Authority could not be established. A missing authority observation is never read as permission. |

**`ambiguity`**

| State | Band floor | Meaning |
| --- | --- | --- |
| `confirmed` | routine | Inputs, exclusions, and success conditions are confirmed and the path is familiar. |
| `stated-assumption` | bounded | One recorded assumption remains; exclusions and success conditions are otherwise confirmed. |
| `material-assumption` | consequential | Material assumptions, mixed state, or a novel path remain unresolved. |
| `contradictory` | critical | Sources, instructions, or observed states conflict and cannot all hold. |
| `unknown` | consequential | The settledness of the inputs could not be established from the envelope. |

**`evidence-coverage`**

| State | Band floor | Meaning |
| --- | --- | --- |
| `deterministic` | routine | A deterministic check establishes the decisive success condition from current evidence. |
| `direct-observation` | bounded | The decisive condition is directly observable in the target state, though not machine-checkable. |
| `indirect-or-qualitative` | consequential | The decisive condition is qualitative, indirect, or susceptible to self-certification. |
| `unavailable` | critical | Decisive evidence is stale, out of reach, or cannot be obtained before acting. |
| `unknown` | consequential | Evidence coverage could not be established; absent evidence is recorded as absent. |

### The four bands

| Band | Meaning |
| --- | --- |
| `routine` | Bounded consequence, direct recovery, confirmed authority and intent, no material ambiguity, and deterministic proof of the decisive outcome. |
| `bounded` | One elevating condition exists, but consequence stays contained, recovery remains demonstrated, and no external or irreversible commitment is made. |
| `consequential` | A material external effect, recovery-dependent state, unresolved authority question, or consequential quality judgment is present. |
| `critical` | Material harm or commitment could be irreversible or broadly propagated, recovery is uncertain, or required authority is absent. |

### How the band is decided

The rule is dominance, not averaging: **the band is the highest floor across the five observations.** Four routine observations never pull a critical one down. An absent authority observation produces the critical band even when every other observation is routine.

Classification fails closed in two ways:

- An observation set that omits a declared observation, or carries an undeclared one, is **invalid**. It is not quietly classified at the lowest band.
- Every `unknown` state carries an elevated floor. Missing evidence is recorded as missing; it is never converted into a favorable observation.

The result carries the band and its ordered reasons — every observation whose floor equals the resulting band, in declared order — so an operator can see which observable facts produced the band and challenge them directly.

### What each band expects

| Band | Evidence | Review | Operator gate | Contingency |
| --- | --- | --- | --- | --- |
| routine | task-local-proof | none-by-default | no-additional-gate | known-correction-path |
| bounded | artifact-and-recovery-proof | policy-only | boundary-crossing-approval | named-recovery-action |
| consequential | target-state-proof | independent-when-qualitative | material-commitment-approval | recorded-trigger-and-owner |
| critical | target-state-and-failclosed-proof | independent-challenge | explicit-operator-authorization | evidenced-contingency-and-stop-condition |

| Expectation | What it asks for |
| --- | --- |
| `task-local-proof` | Direct, task-local completion evidence, plus the deterministic check when one exists. |
| `artifact-and-recovery-proof` | Direct evidence for the changed artifact or state and proof that the bounded recovery path remains available. |
| `target-state-proof` | Evidence read from the real target state, plus recovery or downstream-effect evidence where either applies. |
| `target-state-and-failclosed-proof` | Current target-state evidence plus proof that the fail-closed and recovery conditions are viable. |
| `none-by-default` | No independent review is derived. Configured policy may still require one. |
| `policy-only` | An independent review is derived only when configured policy requires it or a decisive qualitative gap remains. |
| `independent-when-qualitative` | One task-appropriate independent review of the decisive judgment when deterministic guards cannot establish its quality. No role, model, or panel is prescribed. |
| `independent-challenge` | An independent challenge of the decisive claims and the recovery evidence, given the artifact and the governing contract in fresh bounded context without the author's conclusion. Additional depth applies only when configured policy or this derived expectation names it. |
| `no-additional-gate` | No gate beyond the authority already confirmed for the work. |
| `boundary-crossing-approval` | Operator approval before crossing an unconfirmed authority boundary or making an external commitment. |
| `material-commitment-approval` | Explicit operator approval before a binding, externally visible, destructive, or otherwise material commitment. |
| `explicit-operator-authorization` | Explicit operator authorization before acting and again before accepting an unrecovered partial state. |
| `known-correction-path` | A known undo or correction path. No rehearsal is required. |
| `named-recovery-action` | A named recovery action and its trigger, proportionate to the contained effect. |
| `recorded-trigger-and-owner` | A recorded trigger, owner, and recovery route, plus verification that recovery is ready. |
| `evidenced-contingency-and-stop-condition` | A tested or otherwise directly evidenced contingency, an explicit stop condition, named ownership, and follow-up disposition. |

### Risk does not override configured review policy

Configured review policy answers whether a review loop runs and who performs it. A derived review expectation is an expectation recorded on the task's risk result — nothing more. When it asks for more depth than configured policy provides, that difference is raised as an operator gate. It never edits configuration.

Risk never writes the project's review policy values, the resolved role assigned to a review loop, role capability grants, or launch and automation configuration.

**Independent** means the reviewer did not produce the decisive work and does not self-certify its quality. It does not mean a fixed roster, a second model, or multiple reviewers. Cross-model review stays optional and explicit: it happens only when configured policy or the specific derived expectation names it.

## Judgment guidance

Four cards remain. Each is eligible only at its own lifecycle boundary, and only when the named failure — one that no deterministic guard can decide — is actually possible. Deterministic guards keep every fact they can decide.

| Card | Boundary (`boundary_id`) | The failure it addresses (`failure_id`) |
| --- | --- | --- |
| `intent-confirmation` | `requirements-and-intent` — requirements and intent confirmation, before delivery of the interpreted contract begins. | `inferred-intent-not-confirmed` — missing intent, exclusions, success conditions, or authority are inferred and delivery starts before the operator confirms the interpretation. |
| `evidence-and-gates` | `evidence-and-review-gate` — acceptance of completion evidence and the risk or review gate that follows it. | `evidence-self-certified-or-missing` — the producer self-certifies quality, treats missing or indirect evidence as success, or skips independent judgment where deterministic checks cannot decide adequacy. |
| `mixed-version-state` | `migration-install-restart` — configuration migration and the install, update, or restart transition. | `mixed-version-or-unproven-running-state` — success is claimed while old and new versions, partial migration state, stale processes, or unverified recovery coexist. |
| `delivery-closeout` | `delivery-and-closeout` — delivery of the outcome and closeout of the unit of work. | `artifact-mistaken-for-outcome` — artifact creation is mistaken for outcome completion, and real-world acceptance, contingency, durable decisions, or follow-up is omitted. |

Every card's central guidance is the shared failure-signal grammar below; no card carries prose of its own in this contract.

### When a card activates

Activation reads two declared task-envelope facts and nothing else:

| Envelope fact | What it declares |
| --- | --- |
| `lifecycle_boundaries` | The lifecycle boundaries this unit of work actually crosses. |
| `open_failure_conditions` | The named non-enforceable failures that are still open. A failure a deterministic guard has already decided is not open. |

A card is eligible when its `boundary_id` appears in `lifecycle_boundaries` **and** its `failure_id` appears in `open_failure_conditions`. Crossing a boundary alone activates nothing, and an open failure outside its own boundary activates nothing. The default outcome is no card.

Neither the risk band nor the pack outcome is an input. A critical band activates no card on its own, and a selected pack activates no card on its own.

All four were reviewed against the existing runbooks and kept separate. Intent confirmation is a pre-commitment boundary rather than an evidence review; mixed-version state owns running-state proof that the general evidence card would state too vaguely to act on; delivery and closeout is the final boundary where artifact state must be distinguished from outcome state.

### One shared failure-signal grammar

The four cards share one grammar, owned centrally. A pack may reference a card, but it must not copy the card's prose or introduce a parallel anti-rationalization table.

| Element | What it names |
| --- | --- |
| `unverified-claim` | Name the claim that is not yet verified. |
| `missing-authority-or-evidence` | Name the authority or evidence that is missing. |
| `consequence-of-proceeding` | State what happens if the work proceeds anyway. |
| `next-decision-or-proof` | State the next decision or the proof that would settle it. |

### Admitting a fifth card

A new card is admitted only when **both** records exist: a documented failure that deterministic enforcement cannot decide, and a fixed-input context measurement showing a separate card is justified instead of a shorter condition on an existing one. Either record alone is rejected, and the default outcome is not admitted. Failure evidence alone produces a catalog of rare warnings; a measurement alone does not prove a distinct judgment need.

## Practice-pack selection

At most one optional specialist guidance body enters active context. Usually none does.

### Five packs ship; at most one loads

Phase 04 ships **exactly five** initial optional practice packs, one per approved family. Every one of them is optional and task-matched: none is mandatory, none is always loaded, and at most one matched body ever enters active context.

| Family | Pack | Approved content areas |
| --- | --- | --- |
| `software` | `software-delivery` | `testing`, `security`, `accessibility`, `delivery` |
| `research` | `research-inquiry` | `source-quality`, `methodology`, `fact-checking` |
| `marketing` | `marketing-claim` | `audience`, `brand`, `legal-review`, `launch-measurement` |
| `operations` | `operations-change` | `rehearsal`, `handoff`, `rollback`, `monitoring` |
| `policy` | `policy-governance` | `stakeholder-review`, `compliance`, `publication`, `effective-date-checks` |

These three remain out of scope:

| Out of scope | Why |
| --- | --- |
| `profiles-beyond-the-five-initial-families` | A sixth family is a later decision with its own evidence. |
| `mandatory-packs` | No pack may be required for any task. A no-match envelope returns `none` and core governance continues unchanged. |
| `always-loaded-catalogs` | No pack body is resident. A body is retrieved only after selection resolves to `selected`, and only for the one selected pack. |

`research-inquiry` and `operations-change` are the accepted **mechanism-validation exemplars**: the first two bodies authored, because two packs are the smallest set that can exercise every selection outcome. They are a build sequence, not the delivery scope. A missing or invalid body for any of the five blocks Phase 04 exit even when both exemplars pass.

What the operator excluded was a 24-skill mandatory specialist catalog preloaded into every session, together with universal TDD, security, performance, web, and accessibility gates. Five optional, task-matched packs of which at most one ever loads are what the operator asked for — not that catalog.

### What a pack declares

| Field | Meaning |
| --- | --- |
| `pack_id` | Stable logical identity of the pack. Not a machine path. |
| `family` | The approved family this pack belongs to: software, research, marketing, operations, or policy. Exactly one pack ships per family and no sixth family is admitted. |
| `revision` | Monotonic revision of this pack's metadata and body. |
| `contract_version` | The contract version this pack targets. A mismatch is invalid before any body is retrieved. |
| `applies_when` | Observable task-envelope conditions that must all match for the pack to be eligible. |
| `never_when` | Conditions that disqualify the pack. Any match vetoes every positive match. |
| `precedence_class` | The most specific class among the pack's matched positive conditions, used to rank eligible candidates. |
| `tie_key` | Stable key that orders candidates inside a diagnostic. It never chooses a winner; equal precedence fails closed instead. |
| `body_ref` | Logical locator of the bounded guidance body. Retrieved only after the outcome resolves to selected. |
| `body_budget_bytes` | Declared maximum size of the guidance body. A body over budget fails closed and loads nothing. |
| `content_areas` | The approved guidance content areas this pack's body must cover. A body that omits an approved content area is invalid; the areas cannot impose a universal gate or change the risk band. |
| `evidence_shape` | The kind of task evidence this profile helps interpret. It cannot impose a universal gate or change the risk band. |

### The task-envelope facts selection reads

| Fact | What it declares |
| --- | --- |
| `primary_outcomes` | The primary intended outcomes the task declares. More than one means no single primary outcome was named. |
| `artifact_kinds` | The artifact or state kinds the work produces. |
| `incidental_terms` | Supporting or incidental subject terms that are not the outcome. |
| `exclusions` | Guidance or outcomes the task envelope explicitly excludes. |
| `authorized_profile_hint` | An explicit profile hint, honored only when the envelope authorizes it. |
| `lifecycle_substrate_activities` | Cartopian's own lifecycle mechanics that this unit of work performs. Declaring one is a negative condition; it is never a primary outcome. |

A condition declares either one exact `value` or a closed `any_of` set fixed in the registry. Both forms read only the declared facts above.

**A primary outcome is declared, never inferred.** These are not inputs:

| Not an input | |
| --- | --- |
| `prose-verbs` | Verbs or phrasing in task prose, however operational they sound. |
| `filenames` | File, directory, or artifact names. |
| `work-root-contents` | Anything found by reading the work root or repository contents. |
| `conversation` | Session conversation with the operator or another role. |
| `project-history` | Prior tasks, decisions, reports, or plan history. |
| `cartopian-runtime-activity` | Observed Cartopian runtime mechanics, including dispatch, routing, state refresh, and cleanup. |

When the declared facts do not resolve to one eligible pack, selection fails closed and loads no body. Ambiguity is never resolved by inference.

### Precedence

| Class | Rank | Meaning |
| --- | --- | --- |
| `primary-outcome` | 1 | The condition matches the task envelope's primary intended outcome. |
| `artifact-kind` | 2 | The condition matches the artifact or state kind the work produces. |
| `incidental-term` | 3 | The condition matches a supporting or incidental term only. |

### Selection rules, in order

| Rule | Behavior |
| --- | --- |
| `envelope-facts-only` | Selection reads only the declared task-envelope facts. Project history, conversation, and unrelated work-root content are not inputs. |
| `compatibility-first` | Metadata that fails validation or declares an incompatible contract version is rejected before any body is retrieved. |
| `all-positive-required` | A pack is eligible only when every one of its positive conditions matches. |
| `declared-operational-outcome-required` | Operations is eligible only when the envelope declares one of the qualifying operational outcomes. No other envelope fact supplies it, and no inference from prose, filenames, work-root contents, conversation, project history, or Cartopian runtime activity supplies it. |
| `negative-veto` | Any matching negative condition disqualifies the pack regardless of how many positive conditions matched. |
| `lifecycle-substrate-is-never-an-operational-outcome` | A declared Cartopian lifecycle-substrate activity vetoes operations. Task-directory movement, handoff dispatch, review routing, `STATE.md` refresh, and PM cleanup are process substrate and never operations-profile matching evidence. |
| `primary-outcome-precedence` | Among eligible packs, the most specific matched class wins: primary outcome outranks artifact kind, which outranks an incidental term. |
| `declared-specificity` | Within one class, a pack declaring more positive conditions outranks a pack declaring fewer. |
| `equal-precedence-fails-closed` | Two or more survivors at equal precedence return an ambiguity error. No body is retrieved while the operator resolves the envelope. |
| `authorized-hint-resolves-eligible-collisions-only` | An authorized profile hint may choose among already-eligible candidates in an equal-precedence collision. It can never override a negative veto, a stated exclusion, or an incompatible contract version, and an unauthorized hint is ignored with a diagnostic. |
| `no-match-is-none` | Zero eligible candidates return none. That is a valid result, not an error, and core governance continues without specialist guidance. |
| `single-body-admission` | A selected outcome admits exactly one pack body into active context. Every other outcome admits zero. |
| `retrieval-after-resolution` | Selection resolves an identity. The body is retrieved only after the outcome resolves to selected. |

### Outcomes

| Outcome | Bodies loaded | Error | Meaning |
| --- | ---: | --- | --- |
| `selected` | 1 | no | Exactly one eligible pack survived precedence. Its body may be retrieved. |
| `none` | 0 | no | No pack was eligible. Core governance continues unchanged. |
| `ambiguous` | 0 | yes | Two or more candidates tied at the same precedence. Selection fails closed and loads nothing. |
| `invalid` | 0 | yes | Metadata or a body failed validation. Selection fails closed and returns structured evidence without partial content. |

A stale, oversized, unreadable, or out-of-bounds body is `invalid`. It loads nothing and returns no partial content.

### The five profile shapes share one contract

One metadata and selection contract carries all five outcome shapes. Each shape is the applicability boundary of the pack that ships for its family. "Not a mandatory catalog" means no pack is preloaded and no task requires one; it does not mean fewer than five ship.

| Shape | Selects when | Does not select when | Evidence it helps interpret |
| --- | --- | --- | --- |
| `software` | The primary outcome changes or verifies executable behavior, configuration behavior, or a software artifact. | Software is merely the subject of research, policy analysis, promotion, or an operational record. | Artifact behavior, target-state verification, and recovery evidence. |
| `research` | The primary outcome is a supported finding, comparison, synthesis, or source-based answer. | Source reading is incidental to delivering another profile's primary outcome. | Source provenance, claim support, uncertainty, and contradiction. |
| `marketing` | The primary outcome is an audience-facing claim, campaign asset, positioning decision, or channel result. | Marketing material is only an input to policy, research, software, or operations work. | Claim-to-evidence fit, audience and channel fit, and outcome observation. |
| `operations` | The primary outcome is an explicitly declared, executed and verified operational outcome: a service action, process transition, handoff outcome, recovery, or contingency. | The operational language names only Cartopian's own lifecycle mechanics, implements software functionality, researches operations, documents a process without executing it, or appears as an incidental subject term. | Current state, ownership, handoff, stop condition, and contingency. |
| `policy` | The primary outcome is an interpretation, rule, governance position, compliance effect, or decision under stated authority. | Policy text is only a research source or a communication topic without a policy outcome. | Authority, applicability, interpretation, effect, and exception. |

## Operations, and Cartopian's own lifecycle

Cartopian moves task files, dispatches handoffs, routes reviews, refreshes `STATE.md`, and cleans up after itself as a normal part of running. **That is process substrate, not operations-profile matching evidence.** Cartopian moving its own work through its own lifecycle is how the protocol runs; it is not an operational outcome the protocol governs. Without this boundary, every Cartopian project would look like an operations project to the selector.

### Operations requires a declared operational outcome

Operations is eligible only when the task envelope explicitly declares one of these. Each requires both execution and verification — an intention to execute is not an executed outcome.

| Qualifying outcome | What it declares |
| --- | --- |
| `executed-service-action` | A service or system action was executed and the resulting state verified. |
| `executed-process-transition` | A declared process moved from one state to another and the new state was verified. |
| `executed-handoff-outcome` | A handoff was completed and its receipt or acceptance verified. |
| `executed-recovery` | A recovery action was executed and the recovered state verified. |
| `executed-contingency` | A contingency action was executed and its effect verified. |

### The lifecycle-substrate activities

Declaring any of these is a negative condition on operations. It can never satisfy a positive condition, and it vetoes operations regardless of how many positive conditions matched.

| Activity | What it covers |
| --- | --- |
| `task-directory-movement` | Moving a task file between Cartopian's open, in-progress, in-review, and done directories. |
| `handoff-dispatch` | Dispatching an assignment to a role and waiting for its report. |
| `review-routing` | Routing a completed unit of work to its configured review loop. |
| `state-file-refresh` | Refreshing `STATE.md` to reflect the current phase, active work, and next action. |
| `pm-cleanup` | PM lifecycle cleanup such as prompt removal, report filing, and plan bookkeeping. |

### What vetoes operations

| Category | Vetoes when |
| --- | --- |
| `governance-mechanics-only` | The operational language names only Cartopian's own governance mechanics. |
| `implementing-software-functionality` | The primary outcome is implementing or changing software functionality, even when that functionality is operational. |
| `researching-operations` | The primary outcome is a supported finding about operations rather than an executed operational outcome. |
| `documenting-without-executing` | The primary outcome is documenting or describing a process without executing it. |
| `operational-language-as-subject-only` | Operational language appears only as an incidental subject term and is not the outcome. |

Status of the fifth veto: **operator-accepted**, recorded 2026-08-03 against `DEC-038-QUOTE-001`. `DEC-038` accepted `operational-language-as-subject-only` as a governing negative applicability veto, and it records the lifecycle-substrate and subject-only safeguards as both accepted. It is settled authority, not a proposal the operator has yet to rule on: operational terminology used only as subject matter cannot select `operations-change`.

### The six boundaries, proved

These fixtures resolve deterministically from declared facts alone, and their results are stable under reordering.

| # | Boundary | Declared facts | Outcome |
| ---: | --- | --- | --- |
| 1 | `routine-cartopian-handoff-selects-no-operations` | `executed-handoff-outcome` with substrate `handoff-dispatch` | none, zero bodies |
| 2 | `task-status-movement-selects-no-operations` | `executed-process-transition` with substrate `task-directory-movement`, `state-file-refresh` | none, zero bodies |
| 3 | `implementing-handoff-functionality-selects-software` | `software-behavior-change`, operations only an incidental subject | software, one body |
| 4 | `researching-handoff-practices-selects-research` | `supported-finding`, operations only an incidental subject | research, one body |
| 5 | `executing-and-verifying-a-service-restart-selects-operations` | `executed-service-action` with verified running-service state | operations, one body |
| 6 | `ambiguous-primary-outcomes-load-no-body` | `executed-recovery` and an audience-facing claim, neither primary, no authorized hint | ambiguity error, zero bodies |

Rows 1 and 2 are the boundary the operator asked about directly: a routine handoff and a status move each declare an outcome that would otherwise qualify, and each is vetoed by the substrate it declares.

### Pack bodies stay inactive

`practice-pack-body` and `runtime-pack-selection` are **inactive**. The contract is defined and validated; it is not activated. Two conditions remain unmet:

| Condition | Met | Requirement |
| --- | --- | --- |
| `operations-safeguards-validated` | yes | The declared operational-outcome requirement, the lifecycle-substrate veto, the exhaustive negative applicability, and the six boundary fixtures resolve deterministically. |
| `operator-exemplar-acceptance` | yes | The operator accepts the mechanism-validation exemplar set. Accepting the pair is not accepting a reduced delivery scope. |
| `equivalent-cli-and-mcp-validation` | no | The safeguards and the selection result resolve identically on the command-line and tool surfaces from this shared authority. |
| `task-review` | no | This contract passes task review. |

This gate defers activation. It does not reduce the five-pack delivery scope: all five bodies are still owed, and Phase 04 exit is recorded as blocked until they exist.

## Worked outcomes

These fixed task envelopes are recorded in the registry with their expected results. The validation suite resolves each one to exactly the band, ordered reasons, pack outcome, and card set shown — all three mechanisms, not just the band.

| Task envelope | Band | Pack | Judgment card |
| --- | --- | --- | --- |
| Correct punctuation in an internal draft label with a direct undo. | routine | none | none |
| Compare the support for three claims and return a sourced finding; software is only the subject. | consequential | research | none |
| Restart the updated service and prove the new process is serving requests. | consequential | operations | mixed-version state |
| Produce both a source assessment and a campaign decision, neither named primary, with no authorized hint. | consequential | ambiguity error, nothing loaded | intent confirmation |
| Make an external commitment whose required authority has not been granted. | critical | none | evidence and gates |
| Change executable behavior inside the project with demonstrated recovery and a deterministic check. | bounded | software | none |
| Decide and publish the launch campaign claim for an external audience. | consequential | marketing | none |
| Issue an interpretation that controls a binding downstream decision. | critical | policy | delivery and closeout |
| Make a locally contained correction whose authority could not be established. | critical | none | intent confirmation |

Rows two, six, and seven select a pack with no card. Rows four, five, and nine activate a card with no pack. Rows three and eight do both, independently. That is the separation working, not a coincidence of the fixtures. Five of the nine rows select a different one of the five required packs, so each pack's positive path is worked, not asserted.

## Measured context

Measurements use fixed ASCII fixtures, UTF-8 encoding, LF line termination, and no optional whitespace. Exact bytes include the terminating line feed. Token figures are **estimated tokens** — the ceiling of exact bytes divided by four — not tokenizer output or provider billing.

The eligible universe is one 78-byte core line plus one specimen line for each of the five required packs: 525 bytes.

| Case | Outcome | Active bytes | Active pack bytes | Excluded bytes |
| --- | --- | ---: | ---: | ---: |
| `software-selected` | selected | 172 | 94 | 353 |
| `research-selected` | selected | 165 | 87 | 360 |
| `marketing-selected` | selected | 171 | 93 | 354 |
| `operations-selected` | selected | 164 | 86 | 361 |
| `policy-selected` | selected | 165 | 87 | 360 |
| `collision-loads-no-body` | ambiguous | 78 | 0 | 447 |
| `no-match-loads-no-body` | none | 78 | 0 | 447 |

In every case active bytes plus excluded bytes equal 525 exactly, and in every selected case the excluded bytes are exactly the weight of the other four required packs. When the outcome is not `selected`, the pack contribution is zero bytes — measurable non-selection, not an instruction to ignore prose that already loaded.

Bodies behave the same way. Five authored bodies at a 4,096-byte budget each are 20,480 bytes of maintenance surface, but **peak active context is 4,174 bytes — the core line plus one body — no matter how many packs ship**, because at most one body is ever admitted. Shipping the other four adds zero active bytes.

## The mechanism-validation exemplars

This section ranks which pack bodies are authored **first**. It does not bound, reduce, or decide delivery scope: that is fixed at five packs above. A candidate set rejected here is rejected as a minimal exemplar set, never as a pack that ships.

Status: **operator-accepted**, recorded 2026-08-03 against `DEC-039-QUOTE-001`. In the same acceptance the operator restored all five initial optional packs as the required delivery scope. Accepting the pair is not accepting a reduced scope.

**Accepted exemplar pair: `research-inquiry` and `operations-change`.**

One pack cannot demonstrate the contract. A single candidate can never produce an equal-precedence collision, a cross-shape veto, or the exactly-one-of-many admission bound, so the smallest set that exercises the whole selection contract is two.

The five shapes differ along two axes the metadata contract has to express: whether the primary outcome is a **claim about the world** (research, marketing, policy) or a **change to the world** (software, operations), and whether a shape can veto work where its subject matter appears but is not the outcome.

- `research-inquiry` is the claim-shaped exemplar. Its outcome is an assertion that must be supported by evidence and bounded by authority — the same contract shape marketing and policy use — and its negative conditions exercise the veto direction those shapes need in reverse.
- `operations-change` is the change-shaped exemplar. Its outcome is a state transition that must be verified in the target state and recovered if wrong — the same contract shape software delivery uses — and it is the case that proves a pack can be selected while a judgment card activates independently.

Together they exercise positive primary-outcome matching, negative veto in both directions, declared specificity, the equal-precedence collision, the none result, and single-body admission. All five packs carry validating metadata today and all five ship bodies before Phase 04 exits; no shape is a metadata-only fixture in the delivered contract.

### What the cost figures actually show

Resident metadata bytes are the declared specimen lines for the packs in a set; estimated tokens are the ceiling of each specimen's exact bytes divided by four, summed. **Peak active bytes are the core line plus one declared body budget — 4,174 bytes — for every set with at least one pack, because at most one body is ever admitted.** Adding a pack to a set does not raise peak active context. Authored body budget is maintenance surface, not active context.

So context cost is *not* a discriminator among the two-pack candidates: the whole spread is 8 bytes and 2 estimated tokens. Mechanism coverage is the discriminator. The five-pack figures — 447 resident bytes and 20,480 bytes of authored body, of which zero unmatched bytes reach active context — are the measured cost of the required delivery scope, not an argument against shipping it.

### Every candidate set considered

Shapes counts the profile shapes the set's packs demonstrate. Δ is against the recommended set. Every verdict here is an exemplar-set verdict; none of them bounds delivery scope.

| Candidate set | Verdict | Shapes | Resident bytes | Est. tokens | Δ bytes | Δ tokens | Authored bodies |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `research-and-operations` | **recommended** | 5 | 173 | 44 | — | — | 8,192 |
| `research-and-software` | rejected as exemplar set | 4 | 181 | 46 | +8 | +2 | 8,192 |
| `software-and-operations` | rejected as exemplar set | 2 | 180 | 46 | +7 | +2 | 8,192 |
| `research-and-policy` | rejected as exemplar set | 3 | 174 | 44 | +1 | 0 | 8,192 |
| `research-and-marketing` | rejected as exemplar set | 3 | 180 | 46 | +7 | +2 | 8,192 |
| `marketing-and-operations` | rejected as exemplar set | 3 | 179 | 46 | +6 | +2 | 8,192 |
| `policy-and-operations` | rejected as exemplar set | 3 | 173 | 44 | 0 | 0 | 8,192 |
| `research-only` | rejected as exemplar set | 3 | 87 | 22 | −86 | −22 | 4,096 |
| `operations-only` | rejected as exemplar set | 2 | 86 | 22 | −87 | −22 | 4,096 |
| `software-only` | rejected as exemplar set | 1 | 94 | 24 | −79 | −20 | 4,096 |
| `research-operations-and-software` | rejected as exemplar set | 5 | 267 | 68 | +94 | +24 | 12,288 |
| `all-five-shapes` | **is the delivery scope** | 5 | 447 | 114 | +274 | +70 | 20,480 |

Why each was set aside as an exemplar set:

- `research-and-software` — neither exemplar declares a lifecycle-substrate veto, so the operations safeguards cannot be demonstrated on an exemplar body; both exemplars read as software-adjacent, inviting exactly the reading the domain-neutral core exists to prevent; and it loses the worked outcome in which a pack is selected while a judgment card activates independently.
- `software-and-operations` — no claim-shaped exemplar, so research, marketing, and policy are left with no exemplar carrying their contract shape.
- `research-and-policy`, `research-and-marketing` — no change-shaped exemplar, so target-state and recovery evidence is never demonstrated, and neither carries the substrate veto.
- `marketing-and-operations`, `policy-and-operations` — these tie the recommended set on mechanism coverage at essentially identical cost. They are set aside because marketing and policy each represent only themselves, leaving research unrepresented, and because the specification's own acceptance example — a sourced comparative finding where software is only the subject — would have no exemplar body.
- `research-only`, `operations-only`, `software-only` — a single candidate can never produce an equal-precedence collision or demonstrate exactly-one-of-many admission.
- `research-operations-and-software` — a third body for no mechanism coverage the pair does not already provide, and it exceeds the one-or-two exemplar bound. Not a delivery verdict: `software-delivery` ships regardless.
- `all-five-shapes` — **not rejected.** This set is the Phase 04 delivery scope. It is outside the exemplar comparison only because a five-pack set cannot be the *smallest* set that exercises collision, cross-shape veto, and single-body admission. That is a statement about minimality, not about whether five packs are wanted.

**The decisive argument:** `research-and-operations` is the only candidate set that demonstrates all five profile shapes within the one-or-two exemplar bound, and the only two-pack set that can demonstrate the lifecycle-substrate safeguards on an exemplar body while also carrying a claim-shaped exemplar. That decides which bodies are authored first, not how many are authored.

**The representation this rests on is accepted, not open:** which shapes each pack represents is **operator-accepted**, recorded 2026-08-03 against `DEC-037-QUOTE-001`. `DEC-037` accepted that `research-inquiry` represents the claim-oriented research, marketing, and policy shapes and that `operations-change` represents the change-oriented operations and software shapes. That representation is settled authority for this comparison and for the authoring order it produces. `marketing-and-operations` and `policy-and-operations` stay on record as equally covering at essentially identical cost; under the accepted representation they are set aside because marketing and policy each represent only themselves. Those figures are the evidence behind a closed decision, not a choice still being made. Delivery scope is unaffected either way: all five packs ship.

Every figure here is a composition measurement over the declared fixtures. They are evidence that non-selection costs zero — not adopted production budgets. Production budgets and tokenizer-specific measurements are not set by this contract.

## Where this contract lives

`protocol/risk-and-practice-contract.json` is the authority for every machine value. This file is its plain-language projection. The validation suite checks that the two agree, that the fixtures resolve deterministically, that all five required packs are declared with their approved content areas and each selects positively and is vetoed negatively, that every lifecycle-substrate activity vetoes operations on its own, that the six operations boundaries hold, that exactly one pack body can enter active context while the other four contribute zero bytes, that each candidate set's coverage flags and cost figures recompute from the declared metadata and specimens without bounding delivery scope, that every operator-accepted decision recorded in `accepted_decisions` — `DEC-037`, `DEC-038`, and `DEC-039` — carries its operator evidence and is emitted as locked on both surfaces, and that no pack body exists while the runtime activation gate is unmet.

The registry also records the projection state of each surface. Risk classification is active in the task, prompt, and report templates, the task and handoff runbooks, `cli/risk_contract.py`, and the shared CLI/MCP command surface. The runtime uses the registry's dominance and governance rows directly; focused fixtures prove fail-closed classification, configured-policy preservation, deterministic operator gates and contingencies, and bounded critical adversarial context. Judgment-card activation and practice-pack selection remain pending and independent; this risk implementation does not activate or implement either one.
