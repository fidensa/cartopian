# Implementation Plan: Custody Transition

## Delivery contract

- Delivery: required
- Owner: M. Vasquez, outgoing custodian; Availability: available
- Target: the incoming custodian of the community land register; Kind: recipient
- Acceptance evidence: the incoming custodian signed the register handover schedule; Target: the incoming custodian of the community land register; Observer: the community registrar who witnessed the signature; Authority: the registrar witnesses custody transfers for this register
- Success signals: the incoming custodian answers register queries without referring them back; Target: the incoming custodian of the community land register; Observable: the registrar query log shows no referral back to the outgoing custodian across 30 days
- Contingency: appoint the registrar as interim custodian and reopen the handover schedule; Kind: alternate; Rollback: inapplicable; Because: the outgoing custodian retires on the transition date and cannot resume custody; Trigger: the incoming custodian refers a register query back to the outgoing custodian; Owner: the community registrar
- Immediate verification: the register and its keys were physically handed over and counted against the schedule; Target: the incoming custodian of the community land register; Evidence: countersigned handover schedule HS-2026-08-15; Time: 2026-08-15; Result: pass
- Follow-up: has the incoming custodian handled a full quarterly reconciliation unaided; Owner: the community registrar; Due: quarterly; Status: open
- Authority: operator-authorized; Evidence: council resolution CR-2026-14 authorising the transition
- Artifact: complete; Evidence: the handover schedule lists every register volume and key against a counted total
