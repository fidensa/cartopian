# Skill: Adopt Requirements

Derive a `REQUIREMENTS.md` (and optionally a `STANDARDS.md`) from external sources — JIRA stories, Confluence documents, PRDs, design documents, or any other form. Use this when requirements live outside the Cartopian project directory and you want a local requirements artifact.

Running this skill is optional. If you prefer to reference requirements entirely externally, use `adopt-plan` directly — it handles the missing `REQUIREMENTS.md` case without requiring this skill first.

**Output:** `REQUIREMENTS.md` (and optionally `STANDARDS.md`) in the project directory.

---

## Prerequisites

- The project directory exists (run `init-project` first if needed).
- A project-level `cartopian.toml` exists with `[project]` configured.

You must either (a) select the project from the registry using `cartopian discover-projects` or (b) know its absolute path for `cartopian resolve-config`.

---

## Stage 0 — Resolve Project And Config

1. Discover the project or accept an explicit absolute project path:

   - Use `cartopian discover-projects` to list registered projects and select one, or
   - If the operator provides an absolute `<project-path>`, use it directly.

2. If the selected absolute `<project-path>` is not yet in the registry, register it so future sessions can select it deterministically:

   ```
   cartopian register-project <project-path> [--label "Human-friendly name"]
   ```

3. Resolve effective configuration for this project (roles, handoffs, automation policy, and declared work roots):

   ```
   cartopian resolve-config <project-path>
   ```

Record the absolute `<project-path>`; subsequent steps write files within that directory.

---

## Preflight — Existing Requirements Check

1. Read `STATE.md`.
2. Check whether `REQUIREMENTS.md` exists in the project directory.

If `REQUIREMENTS.md` exists and is populated, ask the operator:

> "Requirements already exist. Do you want to revise them, replace them, or abort?"

Proceed only with operator confirmation.

---

## Step 1 — Collect External Context

Ask the operator to provide the external requirements in any form:

- Paste the JIRA story and subtasks directly
- Paste a Confluence page or PRD excerpt
- Provide a URL (if the agent can fetch it)
- Describe the requirements conversationally
- Any combination of the above

Accept whatever format the operator provides. The goal is to extract the information, not enforce a format on the input.

If the operator provides a reference (e.g., "JIRA story HUB-123") without content, ask:

> "Can you paste the story details here, or would you prefer a stub REQUIREMENTS.md that just references the external source?"

A reference stub is a valid output — it preserves traceability without duplicating content that is already maintained elsewhere.

---

## Step 2 — Clarify and Fill Gaps

Review the provided context for completeness. Ask targeted questions only for significant gaps. Do not interrogate the operator if the input is reasonably complete.

Key things to determine:

- What is the project and what problem does it solve?
- Who are the primary users?
- What are the specific functional requirements? (Push for numbered items.)
- Are there non-functional requirements (performance, security, etc.)?
- What decisions are explicitly deferred?

If working from a JIRA story, subtasks often map directly to functional requirements — extract them as numbered items.

---

## Step 3 — Generate REQUIREMENTS.md

Authoring `REQUIREMENTS.md` is **PM-performed**; the contained PM has no raw `Write`, so compose the body using the template in `cartopian://templates/REQUIREMENTS.md` as a structural guide — adapt sections to fit the actual project — and write it through the mediated writer:

```
cartopian write-requirements <project-root> --content-file <body-path>
```

Not every project needs every section.

**If the operator prefers a reference stub** (requirements maintained externally, not duplicated locally), pass the stub below as the `--content-file` body to the same `cartopian write-requirements` command:

```markdown
# Requirements: <project name>

> Requirements for this project are tracked externally.
>
> **Source:** <JIRA epic / Confluence page / PRD title — include URL if available> **Last reviewed:** <date>

## Summary

<One to three sentences on what is being built and its key constraints.>

## Key requirements

<Numbered list of the most important requirements, extracted from the external source, sufficient for plan generation and coverage tracing.>

- FR-001: …
- FR-002: …
```

A stub with a summary and numbered key requirements is preferable to either a blank file or a full duplication of external content. The numbered items give `adopt-plan` or `plan-project` enough to trace coverage.

---

## Step 4 — Optionally Generate STANDARDS.md

Author `STANDARDS.md` through the mediated writer `cartopian write-standards` (a **PM-performed** write) if:

- The operator requests it, or
- The requirements reveal clear technical constraints (specific stack, performance targets, integration requirements) that should be captured.

```
cartopian write-standards <project-root> --content-file <body-path>
```

Otherwise, leave `STANDARDS.md` as its seed stub — the planning phase can refine it later.

---

## Step 5 — Update STATE.md

Updating `STATE.md` is **PM-performed**. Compose the updated body — adding a note to its "What to do next" section — and write it through the mediated writer (never a raw `Edit`):

```
cartopian write-state <project-root> --content-file <body-path>
```

The added note reads:

> Requirements adopted from <source name> on <date>. Next: run `adopt-plan` to migrate an existing implementation plan, or run `plan-project` starting from Stage 2 (implementation plan generation) to build a plan from these requirements.

---

## Step 6 — Summary

Report to the operator:

- Source(s) used
- Number of functional and non-functional requirements captured (or note if stub was generated)
- Whether `STANDARDS.md` was generated or left as seed stub
- Suggested next step (`adopt-plan` or `plan-project`)
