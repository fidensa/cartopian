# Risk and practice extension contracts

This file explains the risk, judgment, and practice-pack contracts in plain language. It defines no behavior of its own. Every machine value below is owned once, in `protocol/risk-and-practice-contract.json`, and projected here. When this file and that registry disagree, the registry is correct and this file is a defect.

The contract covers three mechanisms:

1. **Risk classification** turns a few observable facts about a task into a band, and each band derives what evidence, review, operator approval, and contingency the work is expected to carry.
2. **Judgment guidance** offers a short card at a lifecycle boundary where a real failure exists that no deterministic check can decide.
3. **Practice-pack selection** admits at most one optional specialist guidance body into active context, or none.

These three are independent. A task can carry a risk band with no pack and no card, a pack with no card, or a card with no pack. **None of them silently activates another.** A pack cannot raise or lower risk. A band cannot select a pack or activate a card. A card cannot select a pack or change the band. The contract records these as forbidden edges so the separation is checkable rather than merely stated.

The core stays domain-neutral. Risk, evidence, authority, delivery, and follow-up are core concepts for any kind of work. Software, research, marketing, operations, and policy are optional profiles, selected only when the task's own facts match them.

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

| Card | Boundary | The failure it addresses |
| --- | --- | --- |
| `intent-confirmation` | Requirements and intent confirmation, before delivery of the interpreted contract begins. | Missing intent, exclusions, success conditions, or authority are inferred and delivery starts before the operator confirms the interpretation. |
| `evidence-and-gates` | Acceptance of completion evidence and the risk or review gate that follows it. | The producer self-certifies quality, treats missing or indirect evidence as success, or skips independent judgment where deterministic checks cannot decide adequacy. |
| `mixed-version-state` | Configuration migration and the install, update, or restart transition. | Success is claimed while old and new versions, partial migration state, stale processes, or unverified recovery coexist. |
| `delivery-closeout` | Delivery of the outcome and closeout of the unit of work. | Artifact creation is mistaken for outcome completion, and real-world acceptance, contingency, durable decisions, or follow-up is omitted. |

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

### What a pack declares

| Field | Meaning |
| --- | --- |
| `pack_id` | Stable logical identity of the pack. Not a machine path. |
| `revision` | Monotonic revision of this pack's metadata and body. |
| `contract_version` | The contract version this pack targets. A mismatch is invalid before any body is retrieved. |
| `applies_when` | Observable task-envelope conditions that must all match for the pack to be eligible. |
| `never_when` | Conditions that disqualify the pack. Any match vetoes every positive match. |
| `precedence_class` | The most specific class among the pack's matched positive conditions, used to rank eligible candidates. |
| `tie_key` | Stable key that orders candidates inside a diagnostic. It never chooses a winner; equal precedence fails closed instead. |
| `body_ref` | Logical locator of the bounded guidance body. Retrieved only after the outcome resolves to selected. |
| `body_budget_bytes` | Declared maximum size of the guidance body. A body over budget fails closed and loads nothing. |
| `evidence_shape` | The kind of task evidence this profile helps interpret. It cannot impose a universal gate or change the risk band. |

Conditions match against the task envelope's declared facts: its primary intended outcomes, the artifact or state kinds the work produces, incidental subject terms, stated exclusions, and an optional profile hint.

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
| `negative-veto` | Any matching negative condition disqualifies the pack regardless of how many positive conditions matched. |
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

These are optional demonstrations that one metadata and selection contract can carry any of these outcome shapes. They are not a mandatory catalog, and no pack is required for any task.

| Shape | Selects when | Does not select when | Evidence it helps interpret |
| --- | --- | --- | --- |
| `software` | The primary outcome changes or verifies executable behavior, configuration behavior, or a software artifact. | Software is merely the subject of research, policy analysis, promotion, or an operational record. | Artifact behavior, target-state verification, and recovery evidence. |
| `research` | The primary outcome is a supported finding, comparison, synthesis, or source-based answer. | Source reading is incidental to delivering another profile's primary outcome. | Source provenance, claim support, uncertainty, and contradiction. |
| `marketing` | The primary outcome is an audience-facing claim, campaign asset, positioning decision, or channel result. | Marketing material is only an input to policy, research, software, or operations work. | Claim-to-evidence fit, audience and channel fit, and outcome observation. |
| `operations` | The primary outcome is execution of a repeatable process, state transition, handoff, service action, or contingency. | An operational process is only being studied or described without an execution outcome. | Current state, ownership, handoff, stop condition, and contingency. |
| `policy` | The primary outcome is an interpretation, rule, governance position, compliance effect, or decision under stated authority. | Policy text is only a research source or a communication topic without a policy outcome. | Authority, applicability, interpretation, effect, and exception. |

## Worked outcomes

These fixed task envelopes are recorded in the registry with their expected results, and the validation suite resolves each one to exactly the outcome shown.

| Task envelope | Band | Pack | Judgment card |
| --- | --- | --- | --- |
| Correct punctuation in an internal draft label with a direct undo. | routine | none | none |
| Compare the support for three claims and return a sourced finding; software is only the subject. | consequential | research | none |
| Restart the updated service and prove the new process is serving requests. | consequential | operations | mixed-version state |
| Produce both a source assessment and a campaign decision, neither named primary, with no authorized hint. | consequential | ambiguity error, nothing loaded | intent confirmation |
| Make an external commitment whose required authority has not been granted. | critical | none | evidence and gates |
| Change executable behavior inside the project with demonstrated recovery and a deterministic check. | bounded | software | none |
| Issue an interpretation that controls a binding downstream decision. | critical | policy | delivery and closeout |
| Make a locally contained correction whose authority could not be established. | critical | none | intent confirmation |

Rows two and six select a pack with no card. Rows four, five, and eight activate a card with no pack. Row three does both, independently. That is the separation working, not a coincidence of the fixtures.

## Measured context

Measurements use fixed ASCII fixtures, UTF-8 encoding, LF line termination, and no optional whitespace. Exact bytes include the terminating line feed. Token figures are **estimated tokens** — the ceiling of exact bytes divided by four — not tokenizer output or provider billing.

The registry records one specimen line per profile shape plus one core line, and four measurement cases. In every case, active bytes plus excluded bytes equal the eligible universe exactly. When the outcome is not `selected`, the pack contribution is zero bytes — measurable non-selection, not an instruction to ignore prose that already loaded.

## The exemplar recommendation

Status: **pending-operator-acceptance**. The operator owns this choice. Nothing below is an acceptance record, and no pack body may be authored or loaded until acceptance is recorded.

**Recommended: two exemplar packs — `research-inquiry` and `operations-change`.**

One pack cannot demonstrate the contract. A single candidate can never produce an equal-precedence collision, a cross-shape veto, or the exactly-one-of-many admission bound, so the smallest set that exercises the whole selection contract is two.

The five shapes differ along two axes the metadata contract has to express: whether the primary outcome is a **claim about the world** (research, marketing, policy) or a **change to the world** (software, operations), and whether a shape can veto work where its subject matter appears but is not the outcome.

- `research-inquiry` is the claim-shaped exemplar. Its outcome is an assertion that must be supported by evidence and bounded by authority — the same contract shape marketing and policy use — and its negative conditions exercise the veto direction those shapes need in reverse.
- `operations-change` is the change-shaped exemplar. Its outcome is a state transition that must be verified in the target state and recovered if wrong — the same contract shape software delivery uses — and it is the case that proves a pack can be selected while a judgment card activates independently.

Together they exercise positive primary-outcome matching, negative veto in both directions, declared specificity, the equal-precedence collision, the none result, and single-body admission. The three shapes without an exemplar body stay proven representable: their declared metadata validates against this same contract as body-free shape fixtures. That demonstrates applicability across all five shapes without growing a catalog.

**Alternative considered: `research-inquiry` and `software-delivery`.** This pair is the most familiar for a software audience, but both shapes read as software-adjacent, which invites exactly the reading the domain-neutral core is meant to avoid. It also omits the change-shaped target-state and recovery evidence the operations exemplar proves, and loses the demonstration that a pack selects while a card activates independently.

## Where this contract lives

`protocol/risk-and-practice-contract.json` is the authority for every machine value. This file is its plain-language projection. The validation suite checks that the two agree, that the fixtures resolve deterministically, and that no pack body exists while the exemplar choice is pending.

The registry also records which surfaces will project this contract once their implementations land — task, prompt, and report templates, the task and handoff runbooks, and the classifier and selector — along with the validation obligations that are already proven and the ones still pending. Those surfaces do not carry risk, judgment, or pack behavior yet; until they do, this contract defines the behavior without activating it.
