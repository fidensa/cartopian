# Durable semantic review — the five practice-pack bodies

This is the durable reviewer evidence the practice-pack contract requires for
semantic adequacy. Structural and source validation prove that a body is
well-formed, identified, bounded, and backed by current declared authority.
They never prove that its guidance is substantive, so nothing in this record is
derived from the automated suite.

The contract names four things that do not satisfy this obligation:
`heading-presence`, `keyword-presence`, `byte-length`, and
`numeric-quality-score`. None of them is used here. Each dimension carries one
observation in the reviewer's own words, one verbatim excerpt from the body
that anchors it, and one disposition of `adequate` or `inadequate`. A single
`inadequate` dimension blocks acceptance of that pack.

## Method

Every one of the five bodies under `protocol/packs/` was read in full, top to
bottom, together with the class and applicability boundary each of its declared
sources carries in the registry's source stack. The question asked of each
dimension is the contract's own: does this body change what the assignee asks,
decides, produces, or stops for — or does it only name topics?

Each pack section records the body's content identity. The identity is bound to
the authoritative metadata and to the bytes on disk, so editing a reviewed body
invalidates this record rather than silently inheriting its dispositions. A
changed body requires the review to be redone.

This record is the assignee's direct-inspection evidence. It does not stand in
for the configured task-closure review; the independent reviewer inspects the
same bodies and records their own dispositions in the review record.

## software-delivery

- Body: `protocol/packs/software-delivery.md`
- Reviewed revision: 2
- Body content identity: `sha256:cabb3190618160aebfa2bad7bdaea083d910b156a267ddd7e84ea3cf27a725da`

### actionability

- Observation: The decision gates are questions an assignee must answer before
  proceeding, and each one has a discoverable answer that redirects the work —
  the first gate alone forces the change to be restated as a currently-failing
  check before any code is written. That is a different starting move than the
  assignee would otherwise make, not a reminder to "test things".
- Quoted from the body: `Can I state the changed behavior as a check that would fail today?`
- Disposition: adequate

### conditional-domain-guidance

- Observation: The design heuristics are the most abusable material in a
  software pack, and this body attaches a non-application clause and an
  evidence clause to each one rather than asserting SOLID and low coupling as
  virtues. The cohesion heuristic explicitly costs itself out for small local
  work, which is the case where cargo-culted structure usually enters.
- Quoted from the body: `They may not apply to a one-off script or a small local fix, where introducing a boundary costs more than it isolates.`
- Disposition: adequate

### source-alignment-and-classification

- Observation: SSDF 1.1 is applied as the broad baseline while ASVS is fenced
  to web surfaces in the same paragraph, so a reader cannot pick up ASVS as
  general software advice. The drafts are held in a separate line that denies
  them governing force, and the exemplar is confined to mini-skill anatomy with
  no domain authority. This matches the classes the registry declares.
- Quoted from the body: `For web applications and web services, apply OWASP ASVS 5.0.0 verification to the affected surface; outside web scope ASVS does not govern.`
- Disposition: adequate

### failure-handling

- Observation: Every failure mode is paired with the sentence that normally
  gets it past review, which is what makes the section usable in the moment
  rather than in hindsight. The test-theater entry names the specific green
  signal that is not evidence about this change, which is the failure this
  repository's own evidence gate exists to catch.
- Quoted from the body: `Test theater: a green suite that never exercises the changed behavior`
- Disposition: adequate

### evidence-and-verification

- Observation: The section demands artifacts a reviewer can re-run — commands,
  inputs, observed results, the failing-then-passing check — and then closes
  the loophole that a green suite or a compiling build is itself the proof.
  Coverage gaps must be stated rather than omitted, so silence is not an
  allowed outcome.
- Quoted from the body: `Structural validity — it compiles, the suite is green, the headings are present — is never by itself evidence that behavior is correct or that this guidance was followed.`
- Disposition: adequate

### examples-and-counterexamples

- Observation: The examples show proportionate application at two sizes — a
  one-line defect fix and a web-facing form field — and the counterexamples
  show both over-application and scope error. The ASVS counterexample is the
  sharpest: it demonstrates a current, correctly-cited source being misused by
  being applied outside its scope.
- Quoted from the body: `Counterexample: applying full web ASVS verification to a local command-line tool`
- Disposition: adequate

### stop-and-escalation-clarity

- Observation: Five stop conditions are enumerated concretely, including the
  case where the pack itself loses to a narrower authority, and the escalation
  is bound to the contract's four-element failure-signal grammar rather than to
  free prose. The handoff contents are named, so stopping produces a
  transferable state instead of an abandoned one.
- Quoted from the body: `Escalate with the failure-signal grammar: the unverified claim, the missing authority or evidence, the consequence of proceeding, and the decision or proof required.`
- Disposition: adequate

## research-inquiry

- Body: `protocol/packs/research-inquiry.md`
- Reviewed revision: 2
- Body content identity: `sha256:315c9b5b331c976477b27bdcce6da958f42d432f7c097e36e89db2ce6c796844`

### actionability

- Observation: The framing rule is falsifiability applied to the assignee's own
  question, and it changes the first artifact produced: a question statement
  with inclusion and exclusion rules that the synthesis is later checked
  against. A topic-only pack would have said "define the question"; this one
  states the test the definition must pass.
- Quoted from the body: `State the question so it can be answered wrongly`
- Disposition: adequate

### conditional-domain-guidance

- Observation: Each heuristic carries its own non-application clause, and the
  primary-source rule concedes the real case where the primary record cannot be
  reached — provided the limitation is recorded. That is conditional guidance
  with an evidence obligation attached, not a rule that would simply be broken
  in practice.
- Quoted from the body: `May not apply when the primary record is inaccessible and the limitation is recorded.`
- Disposition: adequate

### source-alignment-and-classification

- Observation: ALLEA is carried as the cross-disciplinary baseline with the
  honest qualifier that it governs only where adopted, and the two method
  authorities are gated on the work actually being that kind of study. The
  method-fit paragraph tells the assignee to find the field's own authority
  when outside those scopes rather than defaulting to the famous checklist.
- Quoted from the body: `PRISMA 2020 and its applicable official extensions govern reporting when the work is a systematic review or an applicable evidence-synthesis type`
- Disposition: adequate

### failure-handling

- Observation: The failure modes are the ones that actually corrupt sourced
  findings — search-result substitution, citation laundering, false
  corroboration, stale versions, cherry-picking — each with its disguising
  rationalization. The corroboration entry states the collapse rule plainly, so
  the reader gets a decision procedure and not just a warning.
- Quoted from the body: `False corroboration: counting dependent repetitions as agreement`
- Disposition: adequate

### evidence-and-verification

- Observation: The required outputs are inspectable independently of the
  author: the recorded strategy and queries, provenance per decisive source,
  the claim-evidence map, independence notes, conflict dispositions, and the
  stop-rule disposition. Volume metrics are explicitly disqualified as evidence.
- Quoted from the body: `Reading effort, the number of sources consulted, and the length of the bibliography are never by themselves evidence that a finding is supported.`
- Disposition: adequate

### examples-and-counterexamples

- Observation: The medication example is the strongest teaching case in any of
  the five packs: one subject, two inquiries, and only one of them inside
  Cochrane and PRISMA scope. It shows that applying a method authority outside
  its scope is an error rather than extra diligence, and the vendor-license
  example shows the proportionate lower bound.
- Quoted from the body: `Same subject, different methods: two inquiries about one medication`
- Disposition: adequate

### stop-and-escalation-clarity

- Observation: Stopping is defined twice and consistently — a stop rule
  declared before searching, and a stop-and-report list for unverifiable
  decisive claims, unresolvable conflicts, and missing access. Escalation uses
  the same four-element grammar as the other packs, and the handoff carries the
  claim-evidence map rather than a summary.
- Quoted from the body: `Stop searching when the declared stop rule is met.`
- Disposition: adequate

## marketing-claim

- Body: `protocol/packs/marketing-claim.md`
- Reviewed revision: 2
- Body content identity: `sha256:430733b597e4f1057abdd99fbcf1d5d984766e938c71605b5f6b1fdbc29ce2aa`

### actionability

- Observation: The gates force a claim inventory, substantiation held in hand,
  and a predefined measurement decision before anything ships, and the first
  gate refuses a generic audience by demanding the supporting fact behind it.
  The body also repeatedly denies that selecting it authorizes launch, which
  changes what the assignee may conclude at the end.
- Quoted from the body: `Can I name the real audience and the single behavior this asset asks for, with the supporting fact?`
- Disposition: adequate

### conditional-domain-guidance

- Observation: Brand authority, disclosures, and legal review are each scoped
  to when they are owed, with the non-application case stated. The brand
  heuristic concedes informative content inside approved positioning, which
  keeps the pack from turning every sentence about a product into an approval
  request.
- Quoted from the body: `May not apply to strictly informative content that stays inside already-approved positioning.`
- Disposition: adequate

### source-alignment-and-classification

- Observation: The ICC code is stated as a self-regulatory baseline that is
  subordinate to law, and the FTC set is conditioned on United States work with
  an explicit instruction to resolve the governing jurisdiction first
  elsewhere. This is the pack where a jurisdictional overreach would be most
  costly, and the boundary is stated in the heuristic, the counterexamples, and
  the source list.
- Quoted from the body: `Outside the United States that set does not govern; resolve that jurisdiction's own law and regulator guidance first.`
- Disposition: adequate

### failure-handling

- Observation: The failure modes cover invented audiences, unapproved brand
  claims, evidence-free superlatives, proxy-metric substitution, and
  attribution overclaiming, each with its rationalization. The disclosure entry
  states the operative test — audience perception, not technical presence —
  which is the distinction that decides most disclosure disputes.
- Quoted from the body: `Buried disclosure: a material connection technically present but effectively invisible`
- Disposition: adequate

### evidence-and-verification

- Observation: The required record is checkable by someone who did not run the
  campaign: inventory with substantiation per material claim, approval records
  with the granting authority, a rendered-asset disclosure check per channel,
  review dispositions, and an interpretation against a threshold fixed before
  launch. Activity metrics are disqualified by name.
- Quoted from the body: `Impressions, asset counts, spend, or approval-in-principle are never by themselves evidence.`
- Disposition: adequate

### examples-and-counterexamples

- Observation: The examples cover the proportionate low end (a factual release
  announcement), a triggered-review case, and three counterexamples spanning
  premature superlatives, jurisdictional misapplication, and routing internal
  documentation through the pack. Both directions of error are represented.
- Quoted from the body: `Counterexample: applying the United States FTC authority set to a campaign running only in Germany`
- Disposition: adequate

### stop-and-escalation-clarity

- Observation: The stop list names unsubstantiated material claims, ungranted
  authority, unresolvable jurisdiction, ineffective disclosures, and —
  importantly — any launch or binding commitment that would occur without its
  own operator approval. Escalation uses the shared grammar and the handoff
  carries the substantiation and approval state.
- Quoted from the body: `Stop and report rather than proceed when: a material claim cannot be substantiated with evidence in hand`
- Disposition: adequate

## operations-change

- Body: `protocol/packs/operations-change.md`
- Reviewed revision: 2
- Body content identity: `sha256:2a1bb5fed2110615e90e792c1769a786238eb9431fabb37279e10b497b3a956f`

### actionability

- Observation: The gates are answerable only with names, values, and readbacks
  — who authorized it, who owns correction right now, what the target set
  actually contains, which value on which signal stops the change. The
  authority gate is asked before anything is touched, which changes the order
  of work rather than adding a checkbox at the end.
- Quoted from the body: `Who authorized this specific change, under what, and is the window open now?`
- Disposition: adequate

### conditional-domain-guidance

- Observation: Rehearsal, capture, and rollback each state where they do not
  apply, so the pack scales down to a reversible flag change without pretending
  the ceremony was performed. The rehearsal clause is scoped to unfamiliar,
  multi-step, or destructive sequences and explicitly excuses proven routine
  action.
- Quoted from the body: `Applies when the sequence is unfamiliar, multi-step, or destructive, not to routine action with a proven history.`
- Disposition: adequate

### source-alignment-and-classification

- Observation: CSF 2.0 carries the governing risk outcomes proportionally,
  SP 800-61r3 is confined to cybersecurity incidents, the SRE Workbook is
  practice guidance rather than ceremony, and SP 800-34r1 is held to federal
  contingency planning instead of being presented as a modern operations
  baseline. That last boundary is stated in the heuristic, the source list, and
  a counterexample.
- Quoted from the body: `Conditional: NIST SP 800-34 Rev. 1 — final May 2010, updated November 2010; federal information-system contingency planning only.`
- Disposition: adequate

### failure-handling

- Observation: The entries name the failures that actually produce operational
  incidents — unnamed authority, ownership discovered mid-incident, unrehearsed
  rollback, wildcard scope, thresholds set after the result, silent partial
  failure — with their rationalizations. The premature-success entry draws the
  distinction between the command and the system, which is this pack's central
  correction.
- Quoted from the body: `Premature success claims: declaring done at the moment of execution`
- Disposition: adequate

### evidence-and-verification

- Observation: The evidence list is an executor-independent audit trail:
  authority and window, three named owners, before-state with a verified
  restore point, target set and limiting mechanism, thresholds recorded
  beforehand with observed values after, read-back state, and confirmed handoff
  acceptance. Completion signals are disqualified explicitly.
- Quoted from the body: `A completed command, a closed ticket, or an unread dashboard is never by itself evidence that the outcome exists.`
- Disposition: adequate

### examples-and-counterexamples

- Observation: Proportionate examples run from credential rotation to a canary
  to a directly reversible flag change that skips rehearsal with the reason
  stated. The counterexamples separate operations from software work, from
  documentation, and from Cartopian's own lifecycle substrate — the boundary
  that the selection contract also enforces negatively.
- Quoted from the body: `Counterexample: moving work through its own governance stages or refreshing a state file is lifecycle substrate`
- Disposition: adequate

### stop-and-escalation-clarity

- Observation: The stop list is the longest of the five and is stated in
  observable terms: no named authority, an unexercisable correction path, an
  unenumerable destructive target set, unavailable monitoring, an unaccepted
  point of no return, and the declared stop condition firing. Escalation and
  handoff both carry timestamps and the correction-path owner.
- Quoted from the body: `the declared stop condition is met`
- Disposition: adequate

## policy-governance

- Body: `protocol/packs/policy-governance.md`
- Reviewed revision: 2
- Body content identity: `sha256:3a06dae06dc40c9e2cb8f1eab3dbfc63f9b570d1307aabbace37cb4c46ac65a9`

### actionability

- Observation: The gates make authority, decision holder, dating, publication
  reachability, and implementation acceptance answerable before adoption, and
  the first gate refuses text that binds without a named source of binding
  force. The eleven-step process produces distinct artifacts rather than a
  single document review.
- Quoted from the body: `What authority makes this binding, and does its scope reach these parties, activities, and period?`
- Disposition: adequate

### conditional-domain-guidance

- Observation: The pack scales from a binding rule with external effect down to
  a clarification inside settled authority, and the objective heuristic states
  its own exception for a conforming amendment. Participation is scoped to what
  is owed and to any mandatory procedure, so consultation is not universalized.
- Quoted from the body: `May not apply to a conforming amendment matching a superior instrument.`
- Disposition: adequate

### source-alignment-and-classification

- Observation: OECD/LEGAL/0390 is the foundational baseline and is stated as
  subordinate to applicable law and the organization's own decision authority;
  the RIA principles, 0464, 0475, and the 2025 Outlook are each fenced to
  impact assessment, regulatory innovation, transboundary work, and
  implementation evidence respectively. The RIA fence is repeated as a
  counterexample about an internal expense standard.
- Quoted from the body: `outside impact assessment they are not general policy authority`
- Disposition: adequate

### failure-handling

- Observation: The failures are governance-specific and consequential — missing
  authority, consultation staged after the decision, unsupported impact claims,
  a single date doing the work of six, publication the bound audience cannot
  reach, and adoption with no implementation owner or review trigger. Each
  carries the phrase that normally lets it through.
- Quoted from the body: `Stakeholder theater: consultation run after the decision, or in a form nobody can use`
- Disposition: adequate

### evidence-and-verification

- Observation: The evidence set is checkable without trusting the drafter:
  cited authority with scope, participation dispositions, effects mapped to
  supporting facts including distributional effects, the conflict list, the
  requirement-to-authority map, a decision record held by the decision holder,
  the published version identity with a reachability check, and the separated
  date schedule. Approval alone is disqualified.
- Quoted from the body: `Approval alone, document length, and consultation volume are never by themselves evidence.`
- Disposition: adequate

### examples-and-counterexamples

- Observation: The two proportionate examples bracket the range — an
  interpretive clarification that records why the heavier steps were not
  required, and an enforceable rule that walks the full process. The
  counterexamples cover ambiguous dating with inaccessible publication, source
  misapplication, and routing prose that binds no one.
- Quoted from the body: `Counterexample: citing the OECD Regulatory Impact Assessment principles as the authority for adopting an internal expense standard`
- Disposition: adequate

### stop-and-escalation-clarity

- Observation: Stopping is required when the binding authority cannot be named,
  the decision holder has not decided, mandatory participation cannot be
  completed, an equal-scope conflict is unresolved, dating or publication
  cannot be made unambiguous or reachable, or issuance would bypass its own
  approval. The shared escalation grammar and a named review owner close the
  handoff.
- Quoted from the body: `a conflict with an existing instrument or superior authority is unresolved at equal scope`
- Disposition: adequate

## Reviewer conclusion

All five bodies are `adequate` on all seven dimensions. Each one changes what
an assignee asks before starting, what evidence they must produce, and what
stops them, and each one fences its cited sources to their declared classes and
applicability rather than presenting a current source as universal advice. No
pack reads as approved headings with topic reminders underneath.

The residual limitation is honest: this record is one reviewer's direct
inspection. It is durable, re-checkable against the exact bytes reviewed, and
independent of the automated suite, but the configured task-closure review is
what supplies reviewer independence.
