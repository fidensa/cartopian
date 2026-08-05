---
pack_id: software-delivery
revision: 1
content_areas: testing,security,accessibility,delivery
---
# Software delivery practice pack

Use this optional guidance only for the selected software outcome. It does not change the task risk, activate judgment guidance, alter configured review policy, or create a universal gate.

## Testing

Name the behavior that changed and the smallest decisive checks for it. Exercise the expected path and credible failure paths at the most direct available layer. Record commands, inputs, observed results, and any relevant coverage gap; do not substitute the existence of tests for evidence that the changed behavior was exercised.

## Security

Check whether the change crosses a trust, authority, secret, input, path, or data boundary. Preserve least authority and fail-closed handling at that boundary. Record the threat-relevant checks that apply and explicitly state when no security boundary changed; this guidance does not make a security review mandatory by itself.

## Accessibility

For user-facing behavior, check the affected interaction through its relevant keyboard, focus, label, contrast, motion, and assistive-technology semantics. Scope evidence to the changed surface and record unavailable platform coverage without turning accessibility into an unrelated universal gate.

## Delivery

Verify the produced artifact or target state, its compatibility assumptions, and the practical correction or recovery path. Record what is ready to ship, what remains operator-owned, and which evidence proves the delivered state rather than only the source edit.
