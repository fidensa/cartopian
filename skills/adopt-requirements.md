# Skill: Adopt Requirements

Generate `REQUIREMENTS.md` (and optionally `ENGINEERING.md`) from external
sources — JIRA stories, Confluence documents, PRDs, design documents, or
any other form. Use this when requirements live outside the Cartopian
project directory and you want a local requirements artifact.

Running this skill is optional. If you prefer to reference requirements
entirely externally, use `adopt-plan` directly — it handles the missing
`REQUIREMENTS.md` case without requiring this skill first.

**Output:** `REQUIREMENTS.md` (and optionally `ENGINEERING.md`) in the
project directory.

---

## Prerequisites

- The project directory exists (run `init-project` first if needed).
- A project-level `cartopian.toml` exists with `[project]` configured.

---

## Preflight — Existing Requirements Check

1. Read `STATE.md`.
2. Check whether `REQUIREMENTS.md` exists in the project directory.

If `REQUIREMENTS.md` exists and is populated, ask the operator:
> "Requirements already exist. Do you want to revise them, replace them,
> or abort?"

Proceed only with operator confirmation.

---

## Step 1 — Collect External Context

Ask the operator to provide the external requirements in any form:

- Paste the JIRA story and subtasks directly
- Paste a Confluence page or PRD excerpt
- Provide a URL (if the agent can fetch it)
- Describe the requirements conversationally
- Any combination of the above

Accept whatever format the operator provides. The goal is to extract the
information, not enforce a format on the input.

If the operator provides a reference (e.g., "JIRA story HUB-123") without
content, ask:
> "Can you paste the story details here, or would you prefer a stub
> REQUIREMENTS.md that just references the external source?"

A reference stub is a valid output — it preserves traceability without
duplicating content that is already maintained elsewhere.

---

## Step 2 — Clarify and Fill Gaps

Review the provided context for completeness. Ask targeted questions only
for significant gaps. Do not interrogate the operator if the input is
reasonably complete.

Key things to determine:
- What is the project and what problem does it solve?
- Who are the primary users?
- What are the specific functional requirements? (Push for numbered items.)
- Are there non-functional requirements (performance, security, etc.)?
- What decisions are explicitly deferred?

If working from a JIRA story, subtasks often map directly to functional
requirements — extract them as numbered items.

---

## Step 3 — Generate REQUIREMENTS.md

Write `REQUIREMENTS.md` in the project directory. Use the template in
`templates/REQUIREMENTS.md` as a structural guide — adapt sections to fit
the actual project. Not every project needs every section.

**If the operator prefers a reference stub** (requirements maintained
externally, not duplicated locally):

```markdown
# Requirements: <project name>

> Requirements for this project are tracked externally.
>
> **Source:** <JIRA epic / Confluence page / PRD title — include URL if available>
> **Last reviewed:** <date>

## Summary

<One to three sentences on what is being built and its key constraints.>

## Key requirements

<Numbered list of the most important requirements, extracted from the
external source, sufficient for plan generation and coverage tracing.>

- FR-001: …
- FR-002: …
```

A stub with a summary and numbered key requirements is preferable to either
a blank file or a full duplication of external content. The numbered items
give `adopt-plan` or `plan-project` enough to trace coverage.

---

## Step 4 — Optionally Generate ENGINEERING.md

Generate or update `ENGINEERING.md` if:
- The operator requests it, or
- The requirements reveal clear technical constraints (specific stack,
  performance targets, integration requirements) that should be captured.

Otherwise, leave `ENGINEERING.md` as its seed stub — the planning phase can
generate or refine it later.

---

## Step 5 — Update STATE.md

Add a note to the "What to do next" section of `STATE.md`:

> Requirements adopted from <source name> on <date>. Next: run `adopt-plan`
> to migrate an existing implementation plan, or run `plan-project` starting
> from Stage 2 (implementation plan generation) to build a plan from these
> requirements.

---

## Step 6 — Summary

Report to the operator:
- Source(s) used
- Number of functional and non-functional requirements captured (or note if
  stub was generated)
- Whether `ENGINEERING.md` was generated or left as seed stub
- Suggested next step (`adopt-plan` or `plan-project`)
