# Skill: Adopt Requirements

Derive a `REQUIREMENTS.md` (and optionally a `STANDARDS.md`) from external sources — JIRA stories, Confluence documents, PRDs, design documents, or any other form. Use this when requirements live outside the Cartopian project directory and you want a local requirements artifact.

Running this skill is optional. If you prefer to reference requirements entirely externally, use `adopt-plan` directly — it handles the missing `REQUIREMENTS.md` case without requiring this skill first.

**Output:** `REQUIREMENTS.md` (and optionally `STANDARDS.md`) in the project directory.

**Protocol reference:** Read
`cartopian://protocol/CONVENTIONS/planning-intent-contract` before clarifying
or writing requirements. The Planning Intent Contract is the lock gate for
this workflow; no separate operator-facing command is introduced.

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

3. Resolve effective configuration for this project (canonical role records, review policy/assignment, automation policy, declared work roots, and schema identity):

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

Operator evidence is the operator's own turns, captured by the host intake
hooks and bound by `select_project`; the intent summary and the operator's
reply bind at lock, and a later correction is cited only by identity from
`cartopian lookup-evidence --recent`. The pasted external source and all PM
prose are never operator evidence, and `capture-request` is operator-only.
Never create, copy, or edit anything under `requests/` or the intake directory, never invoke operator-only intake, and never use shell or escalation to bypass an evidence gate; relay any evidence refusal to the operator verbatim and stop. Rules: `cartopian://protocol/CONVENTIONS/up-front-operator-request-evidence`.

Accept whatever format the operator provides. The goal is to extract the information, not enforce a format on the input.

For every source that substantively governs the requirements, resolve its stable identity, applicable effective date/edition/revision/version, and governed scope. Record the precedence rule or named decision authority when sources conflict. Keep every claim that remains unverified explicit with its missing authority/evidence, consequence, and next decision/proof. Missing or conflicting decisive authority blocks requirements lock; it is not a numerical confidence question.

If the operator provides a reference (e.g., "JIRA story HUB-123") without content, ask:

> "Can you paste the story details here, or would you prefer a stub REQUIREMENTS.md that just references the external source?"

A reference stub is a valid output — it preserves traceability without duplicating content that is already maintained elsewhere.

---

## Step 2 — Clarify and Fill Gaps

Resolve the six compact-intent facts from the operator's input and the
approved external artifacts under the Planning Intent Contract. Facts the
sources supply are `present` and are never asked again. Facts the sources do
not supply are `missing`: ask for each one, one question per turn, grounded in
what the source already says. Never fill a gap with an assumption, a default,
or a reading of the source offered for the operator to accept, and never
derive a fact from the project or story name. If the source and the operator
disagree, show both and ask which governs. Present the compact record once
for confirmation when all six facts are present. Do not lock requirements
until that record is complete and confirmed.

After confirmation, ask targeted questions only for significant functional,
non-functional, or deferred-decision gaps that the supplied sources do not
already answer, still one question per turn. Do not interrogate the operator
if the input is reasonably complete.

If working from a JIRA story, subtasks often map directly to functional requirements — extract them as numbered items.

---

## Step 3 — Generate REQUIREMENTS.md

Proceed only with confirmed compact intent. Authoring `REQUIREMENTS.md` is
**PM-performed**; the contained PM has no raw `Write`, so compose the body
using the template in `cartopian://templates/REQUIREMENTS.md` as a structural
guide — including its `Confirmed intent` section, adapted to the actual
project — and write it through the mediated writer:

```
cartopian write-requirements <project-root> --content-file <body-path>
```

Not every project needs every section.

Populate the template's `## Source authority` section for source-backed requirements. Use `n/a — requirements are not source-backed` only when no source authority materially governs the requirements.

**If the operator prefers a reference stub** (requirements maintained externally, not duplicated locally), pass the stub below as the `--content-file` body to the same `cartopian write-requirements` command:

```markdown
# Requirements: <project name>

> Requirements for this project are tracked externally.
>
> **Source:** <JIRA epic / Confluence page / PRD title — include URL if available> **Last reviewed:** <date>

## Confirmed intent

- **Outcome:** …
- **Beneficiary:** …
- **Why now:** …
- **Success signal:** …
- **Binding constraint:** …
- **Explicit exclusions:** …

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
- The sources reveal settled execution standards — a mandated stack or dependency policy, working conventions, required validation tooling — that pass the admission test in `CONVENTIONS § Standards` (execution-binding, assignee-actionable, settled).

Capture only those. Product behavior and scope — including performance targets and integration requirements — stay in `REQUIREMENTS.md`, and unresolved choices become planning open questions, never standards.

```
cartopian write-standards <project-root> --content-file <body-path>
```

Otherwise, leave `STANDARDS.md` as its seed stub — the planning phase can refine it later.

---

## Step 5 — Update STATE.md

Updating `STATE.md` is **PM-performed**. The project has no plan artifacts yet, so this is the no-plan case where the body is PM-authored — write it through the mediated writer (never a raw `Edit`):

```
cartopian write-state <project-root> --content-file <body-path>
```

(Once plan artifacts exist, `write-state` refuses an authored body and composes it from the filesystem instead; that does not apply here.)

The body's "What to do next" section reads:

> Requirements adopted from <source name> on <date>. Next: run `adopt-plan` to migrate an existing implementation plan, or run `plan-project` starting from Stage 2 (implementation plan generation) to build a plan from these requirements.

---

## Step 6 — Summary

Report to the operator:

- Source(s) used
- Confirmed compact intent and any resolved conflicts
- Number of functional and non-functional requirements captured (or note if stub was generated)
- Whether `STANDARDS.md` was generated or left as seed stub
- Suggested next step (`adopt-plan` or `plan-project`)
