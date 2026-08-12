---
pack_id: operations-change
revision: 2
content_areas: intended-outcome,when-to-apply,when-not-to-apply,principles-and-heuristics,working-process,decision-gates,failure-modes,evidence-and-verification,examples-and-counterexamples,stop-and-escalation,sources
---
# Operations Change Practice Pack

Use this optional guidance only for the selected operational outcome. It does not change the task risk, activate judgment guidance, alter review policy, or create a universal gate. Wherever a narrower current authority governs — the system's own runbook, the operator's change and incident authority, a platform's operating rules — it overrides this pack inside its scope. Selecting this pack never authorizes the action: execution keeps its own operator approval.

## Intended Outcome

Deliver an executed and verified operational outcome: authorized by a named authority inside an agreed window, rehearsed and bounded in reach, executed in the foreground against thresholds fixed beforehand, verified directly in the target state rather than inferred from a completed command, and still recoverable through a correction path with a named owner. The evidence is current state, ownership, handoff, stop condition, and contingency — not the command that ran or the ticket that closed.

## When To Apply

Apply this pack when the primary outcome is an explicitly declared, executed and verified operational outcome: a service or system action, a process transition, a completed handoff, a recovery, or an executed contingency. Each qualifies only when both executed and verified — an intention to execute is not an executed outcome. Scale depth to consequence and reversibility: a destructive or wide-reach change walks the full working process; a narrow, reversible change uses the decision gates to establish which steps are decisive and records why the rest were skipped.

## When Not To Apply

Do not apply to ordinary lifecycle substrate — moving work through its own governance stages, dispatching an assignment, routing a review, refreshing a state file. Running the process is how the protocol operates, not an outcome it governs. Do not apply when the primary outcome is implementing or changing software functionality, even when that functionality is operational; a supported finding about operations; or documenting a process without executing it. Do not apply when deployment, rollback, or monitoring are only incidental vocabulary in another outcome, or when the envelope excludes operations guidance.

## Principles And Heuristics

Each principle names when it applies, when it may not, and the evidence that supports it. None is a universal gate.

### Authority And Change Window

Establish who authorized this change, under which runbook or standing approval, and inside which window, before touching the system. A standing approval for a class of change is not approval for an unbounded one; freeze periods and dependent schedules are part of the question. Applies to any system with users or dependents, not to an isolated environment the operator owns. Evidence: the authority, what it rests on, and the window used.

### Ownership

Name who is accountable for the change, for the system's steady state, and for triggering correction — before starting, not while recovering. Ownership implied by whoever is at the keyboard is not ownership, and whoever executes should not be the only one watching. Evidence: named owners for execution, monitoring, and correction, recorded beforehand.

### Preconditions And Current-State Capture

Capture the state you are about to change while it is intact: configuration and version in force, health-signal values, the data the action alters, and the snapshot the correction path depends on. Verify the preconditions the runbook assumes — a recovery point nobody has read is not a recovery point. Applies to every change to persistent state; for a purely additive action, record that instead. Evidence: the dated before-state and verified restore point.

### Rehearsal

Exercise the sequence and its decisive checks in the closest safe environment available — staging, a canary, a dry run, one low-traffic instance. Rehearsal proves the steps run and the correction path works, not that the change is wise; record the differences from production, where rehearsal stops being evidence. Applies when the sequence is unfamiliar, multi-step, or destructive, not to routine action with a proven history. Evidence: the rehearsal result, its environment, and the deltas.

### Blast Radius

State what this reaches if it behaves as intended and as badly as it plausibly could: which systems, data, users, and dependents. Bound the reach deliberately — canary, phased rollout, scoped target list, limited concurrency — so failure lands on a fraction, not everything. Any command scoped by a wildcard, a default, or an omitted filter is destructive until its exact target set is read back. State the reach even for a single-target reversible change. Evidence: the enumerated target set and the limiting mechanism.

### Communication And Handoff

Tell affected people before, during, and after: who is doing what, when, what they may observe, and who to contact. A handoff names outgoing and incoming owners, the state transferred, its acceptance evidence, open conditions, and when responsibility changes — and is not complete until receipt is confirmed. Sending instructions is not a handoff. Applies whenever anyone else depends on the system. Evidence: the notification and the confirmed acceptance.

### Monitoring And Stop Conditions

Name the signals that prove the new state is serving its purpose, their source, the observation window, and a threshold for each — before executing, so none is negotiated against a result already seen. Include silent-failure indicators: the signal that goes quiet, the queue that stops draining, the job that no longer runs. Fix the stop condition as a value and a duration, not judgment in the moment. Evidence: the thresholds and stop condition recorded beforehand, with observed values after.

### Rollback And Recovery

Define the correction path before executing: trigger, authority, exact action, state and version dependencies, data consequences, and the proof of recovery. Establish the point of no return — after which rollback is unavailable and the contingency becomes forward fix or restore-from-backup — before passing it. Rollback never exercised is a plan, not a capability. For federal information-system contingency planning, NIST SP 800-34 Rev. 1 is the applicable conditional guidance; elsewhere it is not the baseline. Applies to every consequential change, not to one instantly reversible by its own mechanism. Evidence: the trigger and owner, the exercised path, and the point of no return recorded.

### Incident Coordination

If the change becomes an incident, switch modes: declare it, name one coordinator, separate investigating, communicating, and acting, and keep one authoritative timeline. Mitigate before diagnosing — restore service first, understand it after. For a cybersecurity incident, NIST SP 800-61 Rev. 3 supplies the applicable conditional preparation, detection, response, and recovery guidance, and evidence preservation may outrank fastest restoration — a trade-off belonging to the operator's security authority. NIST Cybersecurity Framework 2.0 supplies the broader risk outcomes, applied proportionally. Applies once the change stops behaving as rehearsed, not when the stop condition fired and the correction path resolved it. Evidence: the declaration, the coordinator, the timeline, and the mitigation applied first.

### Immediate Verification

Verify the target state directly, in the foreground, before declaring completion: read the running configuration back, exercise the functional path a user or dependent takes, and check health signals against thresholds. A command that exited zero proves the command ran, not that the system is serving. Wait through the window in which a failure would appear. Evidence: the read-back state, the functional check, and the signals observed.

### Closure And Follow-Up

Close deliberately: the resulting state and its evidence, deviations from plan, residual risk and its owner, whether the correction path is still available and until when, and any temporary measure with an owner and removal date. A temporary mitigation with no removal owner is permanent. Capture what to change in the runbook, not only what went wrong. Evidence: the end state, an owner per residual item, and the runbook change produced.

## Working Process

1. Establish authority: who approved this change, under what, and inside which window. Stop at the first decisive ambiguity (see Stop And Escalation).
2. Name owners for execution, monitoring, and correction, and confirm each is reachable.
3. Capture the current state: configuration and version in force, health-signal values, affected data, and the restore point.
4. Bound the blast radius: enumerate the target set, read it back, and choose the limiting mechanism.
5. Rehearse the sequence and the correction path in the closest safe environment, recording the deltas.
6. Fix monitoring thresholds and the stop condition, and confirm the signals are observed.
7. Notify affected parties: what they may observe, when, and who to contact.
8. Execute in the foreground within the window against the declared signals; on the stop condition, invoke the correction path rather than continuing.
9. Verify the target state: read back configuration, exercise the functional path, and observe the signals across the window.
10. Hand off with the state and its acceptance, then close: end state, deviations, residual risk with owners, correction-path availability, and temporary measures with removal dates.

## Decision Gates

Answer before proceeding past each stage; a no is a stop or an escalation, not a rationalization.

- Who authorized this specific change, under what, and is the window open now?
- Who owns execution, who owns watching, and who owns correction — by name, right now?
- Have I captured the before-state and read the restore point, not assumed it exists?
- Exactly what does this reach, and have I read the target set back?
- Has the correction path been exercised, and where does it stop being available?
- What value on which signal, over what duration, stops this change — decided before I start?
- Who needs to know before, during, and after, and has anyone accepted the handoff?
- What observation of the target state — not the command's exit — proves this is done?
- If this becomes an incident, who coordinates and what gets mitigated first?

## Failure Modes

Each pairs the failure with the rationalization that disguises it.

- Unclear command: executing with no named authorizing authority or window — "everyone knows we needed this." Consensus is not authorization.
- Hidden ownership: nobody named for correction until it is needed — "we'll sort out ownership if it breaks." Ownership discovered during an incident did not exist.
- Unrehearsed rollback: a documented correction path nobody has run — "we have a rollback plan." A plan never executed is an assumption.
- Destructive scope ambiguity: a wildcard, default, or omitted filter deciding what gets touched — "it only matches the ones I meant." Read the target set back, or the scope is unknown.
- Monitoring without thresholds: signals watched with no value that means stop — "we'll keep an eye on it." A threshold set after the result is a justification, not a control.
- Silent partial failure: a sequence that half-applied and reported success — "the run completed." Completion of the runner is not completion of the effect.
- Premature success claims: declaring done at the moment of execution — "the command returned clean." Exit status is evidence about the command, not the system.
- Handoff without state: passing responsibility as a message rather than an accepted state — "I told them it was theirs." A handoff completes when receipt is confirmed with the state attached.

## Evidence And Verification

Produce evidence a reviewer can check without trusting the executor: the authority and window; the three owners; the before-state and its verified restore point; the target set and limiting mechanism; the rehearsal result and its deltas; the thresholds and stop condition recorded beforehand, with observed values after; the read-back target state and functional check; the correction path's trigger, owner, and availability; the confirmed handoff acceptance; and residual items with owners and dates. A completed command, a closed ticket, or an unread dashboard is never by itself evidence that the outcome exists.

## Examples And Counterexamples

- Proportional: rotating a service credential runs inside its window, keeps the previous secret until dependents are confirmed reading the new one, and exercises the functional path — not merely the command's exit status.
- Proportional: a configuration change is applied to one instance first, watched against a stated error-rate threshold and duration, then widened — the canary bounds the blast radius. A directly reversible flag change instead records its authority, stop condition, and read-back verification, and skips rehearsal with the reason stated.
- Counterexample: implementing a deployment script, a monitoring integration, or a rollback command is software work whose outcome is changed behavior in an artifact; it mentions operations but executes nothing. Writing the runbook is likewise a documented process — this pack applies when the runbook is run.
- Counterexample: moving work through its own governance stages or refreshing a state file is lifecycle substrate — the protocol running, not a system changed.
- Counterexample: applying federal information-system contingency-planning requirements to a small commercial service — a current source outside its declared scope; current does not mean universal.

## Stop And Escalation

Stop and report rather than proceed when: no named authority covers this change, or the window is closed or unstated; the correction path cannot be established or exercised, or its restore point cannot be read; the target set of a destructive action cannot be enumerated and read back; the monitoring needed to observe the stop condition is unavailable; the action would exceed the approved blast radius or cross the point of no return without explicit acceptance; the declared stop condition is met; or this guidance conflicts with the system's runbook or a narrower current authority and that conflict is unresolved. Escalate with the failure-signal grammar: the unverified claim, the missing authority or evidence, the consequence of proceeding, and the decision or proof required. Hand off with the current state, actions taken with timestamps, the evidence produced, the open conditions, and the correction-path trigger and owner.

## Sources

- Governing: NIST Cybersecurity Framework 2.0 — final 2024-02-26 edition; broadly applicable cybersecurity risk-management outcomes for operations, applied proportionally.
- Conditional: Google Site Reliability Engineering Workbook — Incident Response, Configuration Design, Monitoring, and Canarying Releases chapters, current online edition; practice guidance for ownership, safe change, coordination, monitoring, rollback, and recovery when an operational outcome or operability scope is declared. Not governing law, not universal ceremony.
- Conditional: NIST SP 800-61 Rev. 3 — final 2025-04-03 edition; cybersecurity incident-response preparation, detection, response, and recovery only. It does not govern non-security operational incidents.
- Conditional: NIST SP 800-34 Rev. 1 — final May 2010, updated November 2010; federal information-system contingency planning only. Not a universal modern operations baseline.
- Structural exemplar, no domain authority: addyosmani/agent-skills — mini-skill anatomy only.

All sources verified current 2026-08-07. The system's own runbook and applicable jurisdictional, organizational, product, platform, operator, and task authority outrank this pack in scope.
