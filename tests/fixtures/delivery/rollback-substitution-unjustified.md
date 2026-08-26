# Implementation Plan: Custody Transition

## Delivery contract

- Delivery: required
- Owner: M. Vasquez; Availability: available
- Target: the incoming custodian of the community land register; Kind: recipient
- Acceptance evidence: the incoming custodian signed the handover schedule; Target: the incoming custodian of the community land register; Observer: the community registrar; Authority: the registrar witnesses custody transfers for this register
- Success signals: the custodian answers register queries without referring them back; Target: the incoming custodian of the community land register; Observable: the registrar query log shows no referral back across 30 days
- Contingency: appoint the registrar as interim custodian; Kind: alternate; Rollback: inapplicable; Trigger: a register query is referred back to the outgoing custodian; Owner: the community registrar
- Immediate verification: the register and its keys were handed over and counted; Target: the incoming custodian of the community land register; Evidence: countersigned handover schedule HS-2026-08-15; Time: 2026-08-15; Result: pass
- Follow-up: has the custodian handled a full quarterly reconciliation unaided; Owner: the community registrar; Due: quarterly; Status: open
- Authority: operator-authorized; Evidence: council resolution CR-2026-14
- Artifact: complete; Evidence: the handover schedule lists every register volume and key
