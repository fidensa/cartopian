# Standards: <project name>

<!-- This document is highly recommended but optional. It holds the
     project's durable execution standards: the settled rules that
     govern HOW the project's work is performed. Every statement must
     pass the admission test in CONVENTIONS § Standards — it is
     execution-binding, assignee-actionable, and settled. Route
     anything else to its owning artifact: product behavior and scope
     to REQUIREMENTS.md or a spec; phase deliverables and exclusions
     to the plan; lifecycle and PM behavior to the tool-owned protocol
     and skills; unresolved choices to the plan's open questions or a
     decision once ruled. It is not a governance contract — protocol
     conventions cannot be overridden here. The section names below
     are illustrative — replace them with whatever best fits the
     project's domain (engineering, research, writing, design, ops,
     etc.). -->

## Tools and dependencies

The languages, frameworks, runtimes, or other tools the work must use, and the policy for adding dependencies. Pin versions where stability matters. Binding choices only — rationale and still-open stack questions live elsewhere.

## Working standards

Style, formatting, naming, or process conventions that govern how the work is performed — e.g. indentation and formatting rules, design principles, required development practices such as TDD.

## Constraints

Boundaries that execution must not cross. Examples for software work: no shared mutable state, all services behind the API gateway, no direct DB access from the frontend. Adapt freely to the project's domain.

## Security and compliance

Security and containment practices the work must follow: authentication model, secrets management, data classification, or regulatory requirements.

## Quality checks

Acceptance evidence required of the work: test types and required test commands, review checklists, fact-check passes, validation runs, coverage targets and tooling — whatever proves the work meets the bar.

## Delivery

How finished work is delivered: branch and versioning discipline, deployment targets, CI/CD pipeline, publishing path, or whatever the project's "delivery" surface is.
