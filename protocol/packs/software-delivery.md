---
pack_id: software-delivery
revision: 2
content_areas: intended-outcome,when-to-apply,when-not-to-apply,principles-and-heuristics,working-process,decision-gates,failure-modes,evidence-and-verification,examples-and-counterexamples,stop-and-escalation,sources
---
# Software Delivery Practice Pack

Use this optional guidance only for the selected software outcome. It does not change the task risk, activate judgment guidance, alter configured review policy, or create a universal gate. Wherever a narrower current authority governs — jurisdictional or organizational rules, product standards, platform requirements, the operator's task contract — that authority overrides this pack inside its scope.

## Intended Outcome

Deliver a proportional software behavior change: the requested behavior clarified into an observable contract, a design no larger than the change requires, decisive verification of the changed behavior, and a delivered state that can be proven and undone. The evidence this pack produces is artifact behavior, target-state verification, and recovery evidence — not effort, not test counts, not structure for its own sake.

## When To Apply

Apply this pack when the task's primary outcome changes or verifies executable behavior, configuration behavior, or a software artifact: implementing or altering functionality, fixing a defect, changing build or runtime configuration that alters observable behavior, or proving delivered software matches its contract. Scale depth to consequence: a substantive change walks the full working process; a small change uses the decision gates to establish which steps are decisive and records why the rest were skipped.

## When Not To Apply

Do not apply when software is only the subject — research about software, policy analysis or promotion of a product, or an operational record that mentions code. Do not apply to mechanical edits with no behavioral contract — renames, comments, formatting, incidental terminology — beyond confirming they are mechanical: behavior unchanged, proven by the existing checks. Do not apply when the task envelope excludes software guidance.

## Principles And Heuristics

Each principle names when it helps, when it may not apply, and the evidence that supports using it. None is a universal gate.

### Requirements And Constraints

Restate the requested change as observable outcomes, and separate hard constraints — compatibility, budgets, authority — from preferences before designing. Helps when the request describes a solution rather than an outcome. May not apply when a locked contract already states the outcome. Evidence: the outcome statement that verification later proves.

### Design Cohesion And Coupling

Keep code that changes together in one place and point dependencies at stable abstractions rather than volatile details: cohesion, low coupling, separation of concerns, dependency direction, and the SOLID heuristics, applied proportionally. They help when the change touches a module boundary or the code will be extended. They may not apply to a one-off script or a small local fix, where introducing a boundary costs more than it isolates. Evidence: a boundary you can name, test in isolation, and change without editing its consumers.

### Interfaces And Error Models

State what each changed interface accepts, returns, and does on invalid input; choose fail-closed or fail-open explicitly at each boundary and propagate errors with enough identity to act on. Helps at any trust or module boundary. May not apply inside a private implementation with a single caller. Evidence: documented failure-path behavior, exercised by a check.

### Testing

Name the behavior that changed and write the smallest decisive checks at the most direct available layer — the expected path plus credible failure paths. A check that fails before the change and passes after it is the strongest form. New tests may not be needed when existing checks already exercise the changed behavior; then the evidence is running them. Evidence: commands, inputs, observed results, and the recorded coverage gap — never the existence of a suite.

### Security And Privacy

Follow the NIST SSDF 1.1 baseline proportionally: protect code and secrets, verify third-party components, default to least authority, and fail closed at trust boundaries; collect and retain only the data the outcome needs. Helps whenever the change crosses a trust, authority, secret, input, path, or data boundary; when no such boundary changes, record that explicitly instead of performing ceremony. For web applications and web services, apply OWASP ASVS 5.0.0 verification to the affected surface; outside web scope ASVS does not govern. Evidence: the boundary-relevant checks run, or the recorded statement that no boundary changed.

### Accessibility

For user-facing web content or web applications, verify the affected interaction against WCAG 2.2 — keyboard, focus, labels, contrast, motion, and assistive-technology semantics — scoped to the changed surface. ISO/IEC 40500:2025 carries the same requirement text for organizations that cite the international standard. Other platforms follow their own platform accessibility authority; WCAG does not govern them. May not apply when no user-facing surface changed. Evidence: the checks run on the changed surface, with unavailable platform coverage recorded rather than skipped silently.

### Performance And Observability

Measure before optimizing, against a stated target; make delivered behavior observable enough to verify in the target state and to surface regression — logs, metrics, or checks proportional to consequence. Helps for hot paths and operated services. May not apply to tooling whose only consumer is its developer. Evidence: the measurement, or the observation channel that would reveal failure.

### Compatibility And Migration

Name every consumer of a changed interface, format, or stored state before changing it; sequence migrations so each step leaves the system runnable and reversible. Helps whenever data or contracts outlive the process that created them. May not apply to unreleased code with no external consumers. Evidence: the consumer list and the exercised upgrade — and, where promised, downgrade — path.

### Delivery And Rollback

Verify the produced artifact or target state, not only the source edit; before delivering a consequential change, know the practical correction path — revert, configuration change, or forward fix — and who owns triggering it. Where a service scope is declared, operability practice for monitoring, canarying, and rollback follows the current Google SRE Workbook chapters. Evidence: proof of the delivered state plus a named recovery trigger and owner.

### Avoiding Speculative Abstraction

Add an abstraction when a present need pays for it — a second real consumer, a tested boundary, a declared extension point — never because a future need is imaginable. Helps as the default posture. May not apply when a governing design contract mandates the extension point. Evidence: the concrete need each new layer serves; a layer with one implementation and no isolating test is a cost, not an investment.

## Working Process

1. Clarify the contract: observable outcome, hard constraints, and the authority the change relies on. Stop at the first decisive ambiguity (see Stop And Escalation).
2. Scope the smallest change that satisfies the outcome, and state what is deliberately out of scope.
3. Design at proportional altitude using the principles above; write down the one or two tradeoffs actually decided.
4. Implement with verification alongside: decisive checks at the most direct layer, failing before the change and passing after it where practical.
5. Run the applicable boundary checks — security, privacy, accessibility, performance, compatibility — and record which did not apply and why.
6. Deliver: prove the artifact or target state, confirm compatibility assumptions, and establish the recovery path and its owner.
7. Record evidence and gaps: exact commands, inputs, observed results, unverified claims, and the follow-up each gap requires.

## Decision Gates

Answer before proceeding past each stage; a no is a stop or an escalation, not a rationalization.

- Can I state the changed behavior as a check that would fail today?
- Do I know every consumer of what I am changing?
- Does the change cross a trust, authority, secret, input, path, or data boundary — and is each crossing fail-closed?
- Is each new abstraction paid for by a present need?
- Is the affected surface user-facing, and if web-scoped, are the accessibility and security checks scoped to it?
- Can this change be undone — how fast, and by whom?
- What single piece of evidence proves this is done, and does it exist yet?

## Failure Modes

Each entry pairs the failure with the rationalization that disguises it.

- Cargo-cult abstraction: layers, patterns, or indirection copied as virtue — "this is how real systems are structured." Structure is justified by a present need, not by resemblance.
- Test theater: a green suite that never exercises the changed behavior — "the tests pass." A test that cannot fail on this change is not evidence about it.
- Hidden compatibility break: a changed contract with an unexamined consumer — "nothing else should be using this." Should is not a consumer list.
- Insecure default: authority or exposure widened for convenience — "we can lock it down later." Later is a decision the owning authority must make now, explicitly.
- Inaccessible delivery: a user-facing change verified only with a mouse and default vision — "accessibility is a separate workstream." The changed surface is the workstream.
- Missing observability: a delivered change nobody can watch fail — "it worked when I tested it." Target-state verification requires a way to observe the target state.
- Irreversible rollout: a consequential change with no practical correction path — "we'll fix forward." Forward-only is a choice requiring explicit operator acceptance, not a discovery made during an incident.

## Evidence And Verification

Produce evidence a reviewer can check without trusting the author: exact commands and inputs with observed results; the failing-then-passing check for changed behavior; boundary-check results or the recorded statement that no boundary changed; measured artifact or body sizes against declared limits; the consumer list for changed contracts; and every relevant coverage gap stated rather than omitted. Structural validity — it compiles, the suite is green, the headings are present — is never by itself evidence that behavior is correct or that this guidance was followed.

## Examples And Counterexamples

- Proportional: a one-line defect fix ships with the failing test that reproduces it turned green, a note that no boundary changed, and no new structure. It does not need a design document.
- Proportional: a new checkout-form field gets ASVS-scoped input validation and WCAG 2.2 keyboard, label, and error-identification checks on the changed surface — because it is web-facing, not because every change gets them.
- Counterexample: extracting an interface with a single implementation "for testability" that no test uses — speculative abstraction with an empty evidence column.
- Counterexample: applying full web ASVS verification to a local command-line tool — a current source used outside its applicability; current does not mean universal.
- Counterexample: routing an internal rename through this whole pack — a mechanical edit needed only proof that behavior is unchanged.

## Stop And Escalation

Stop and report rather than proceed when: a decisive requirement or constraint is ambiguous and the answer changes the design; the change needs authority not granted — a new external commitment, a release, a widened data use; verification decisive to the outcome cannot run in the available environment; a consequential change has no establishable recovery path; or this guidance conflicts with a narrower current authority and the conflict is not resolved in that authority's favor. Escalate with the failure-signal grammar: the unverified claim, the missing authority or evidence, the consequence of proceeding, and the decision or proof required. Hand off with the current state, the evidence produced, the open gaps, and the recovery trigger and owner.

## Sources

- Governing: NIST SP 800-218, Secure Software Development Framework 1.1 — final 2022 edition; the broad secure-development baseline for this pack.
- Governing: NIST Cybersecurity Framework 2.0 — final 2024-02-26; cybersecurity risk outcomes, applied proportionally.
- Conditional: OWASP Application Security Verification Standard 5.0.0 — stable release 2025-05-30; web-application and web-service security verification only.
- Conditional: W3C Web Content Accessibility Guidelines 2.2 — Recommendation 2023-10-05, updated 2024-12-12 — and ISO/IEC 40500:2025, approved 2025-10-21 from the 2023-10-05 text; web-content accessibility only.
- Conditional: Google SRE Workbook — Incident Response, Configuration Design, Monitoring, and Canarying Releases chapters, current online edition; operability, rollback, and recovery practice for declared service scopes.
- Watchlist, never governing while drafts: NIST SSDF 1.2 and W3C WCAG 3. Each becoming final triggers a deliberate adoption review, not automatic displacement of the versions above.
- Structural exemplar, no domain authority: addyosmani/agent-skills — mini-skill anatomy only.

All sources verified current 2026-08-07. Applicable jurisdictional, organizational, product, platform, operator, and task authority outranks this pack inside its scope.
