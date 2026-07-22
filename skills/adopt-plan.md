# Skill: Adopt Plan

Convert an existing implementation plan from any external format into Cartopian's `IMPLEMENTATION_PLAN.md`, phase files, and task files. Use this when a plan already exists — as a JIRA epic with subtasks, a Confluence document, a design document, a slide deck, or any structured text — and you need to bring it into the Cartopian system.

This skill does not require a `REQUIREMENTS.md` or a requirements-gathering conversation. Requirements may be referenced externally, summarized as a stub, or adopted inline. Start here when the plan is the thing you have.

**Output:** `IMPLEMENTATION_PLAN.md`, phase files, task files for the first active phase, and an updated `STATE.md`.

---

## Prerequisites

- The project directory exists (run `init-project` first if needed).
- A project-level `cartopian.toml` exists with `[project]` configured.
You must either (a) select the project from the registry using `cartopian discover-projects` or (b) know its absolute path for `cartopian resolve-config`.

---

## Preflight — Active Plan Check

Before proceeding, check whether the project already has a live plan:

1. Read `STATE.md`.
2. Check for `IMPLEMENTATION_PLAN.md`.
3. Check whether `phases/`, `tasks/`, `specs/`, or `reviews/` contain current plan artifacts.

If `IMPLEMENTATION_PLAN.md` exists and the operator wants to replace it, stop and run `close-plan` first. Do not overwrite an active plan.

If `IMPLEMENTATION_PLAN.md` exists and the operator wants to revise it, proceed only as an in-place revision. Make the revision path explicit to the operator before editing.

If inconsistent state exists (plan artifacts without a plan file, or `STATE.md` reports an active plan but `IMPLEMENTATION_PLAN.md` is absent), stop and ask the operator to resolve the state before proceeding.

---

## Stage 0 — Resolve Project, Register If Needed, And Load Config

1. Discover the project or accept an explicit absolute project path:

   - Use `cartopian discover-projects` to list registered projects and select one, or
   - If the operator provides an absolute `<project-path>`, use it directly.

2. If the selected absolute `<project-path>` is not yet in the registry, register it so future sessions can select it deterministically:

   ```
   cartopian register-project <project-path> [--label "Human-friendly name"]
   ```

3. Resolve the effective configuration for this project (roles, review policy and assignments, handoffs, automation policy, declared work roots) via the Core CLI:

   ```
   cartopian resolve-config <project-path>
   ```

4. Read `reviews.planning.mode` and `reviews.planning.role` from the emitted record. Required planning review uses that exact arbitrary role name; policy `off` skips checkpoints. Never infer policy or assignment from a role name or description.

---

## Stage 1 — Requirements Source

Determine how requirements are handled for this project:

1. Check whether `REQUIREMENTS.md` exists and is populated.
   - If yes: use it as the requirements source. Proceed to Stage 2.

2. If no `REQUIREMENTS.md` exists, ask the operator:

   > "No REQUIREMENTS.md found. How should requirements be handled?
   >
   > (a) **Adopt requirements now** — paste or describe them and I'll create REQUIREMENTS.md before generating the plan. (b) **Reference stub** — provide a source reference (JIRA epic, Confluence URL, PRD name) and I'll create a stub REQUIREMENTS.md that points to the external source. (c) **Skip** — requirements are tracked externally; the plan will note the external source and there will be no local REQUIREMENTS.md."
   - Option (a): Run the `adopt-requirements` steps inline (Steps 1–4 of that skill) before continuing to Stage 2.
   - Option (b): Create a reference stub per the stub format in `adopt-requirements` Step 3. Proceed to Stage 2.
   - Option (c): Proceed to Stage 2. Record the external requirements source in the plan's Purpose section. No coverage matrix is required — use a section-level reference instead.

---

## Stage 2 — Collect the External Plan

Ask the operator to provide the external plan:

> "Please share the implementation plan to migrate. This can be:
>
> - Pasted text from a JIRA story or epic with subtasks
> - A Confluence document excerpt
> - A structured list of phases and tasks
> - Any other format — I'll interpret it"

Accept whatever format the operator provides.

Before generating any files, confirm your interpretation:

> "Here is how I'm reading your plan: <N> phases, starting with '<Phase 1 name>'. Does this match your intent, or should I adjust anything before I generate the files?"

Proceed only after operator confirmation.

---

## Stage 3 — Generate IMPLEMENTATION_PLAN.md

Authoring `IMPLEMENTATION_PLAN.md` is **PM-performed**; the contained PM has no raw `Write`, so compose the body following the structure in `cartopian://templates/IMPLEMENTATION_PLAN.md` and write it through the mediated writer:

```
cartopian write-plan <project-root> --content-file <body-path>
```

**Purpose section:** Name the source of the plan and the requirements reference. If requirements are external, identify the external source (e.g., "JIRA story HUB-123" or "Confluence 'Hub UI Design' page"). Include approximate date or version if known.

**Architecture rules:** Derive from any constraints stated in the external plan or the requirements source. Note the origin of each rule.

**Work topology:** Identify which repos or other work locations are involved, including no-repo work. If the project uses work roots, ensure `[project].work_roots` in `cartopian.toml` names them. Task files MUST use the `Work root:` field (names only) rather than paths; see `cartopian://templates/TASK.md`.

**Phase sequence:** Map each phase from the external plan to a `PHASE-NN-slug` entry. Assign two-digit phase numbers starting from `01` (use `00` only for a bootstrap phase with no deliverable output).

Within each phase, assign `PNN-KIND-NNN` plan refs. Map subtasks to plan refs where applicable. Use `BUILD` for delivery/execution items that produce outcomes or artifacts (not only software); use `RESEARCH` for items that produce knowledge, decisions, or designs.

**Requirement coverage:**

- If `REQUIREMENTS.md` exists with numbered requirements, generate the full coverage matrix.
- If requirements are a stub with numbered key requirements, map those to plan refs.
- If requirements are skipped (option (c)), add a section:

  > **Requirements source:** <external source>. Coverage is maintained in the external system; this plan implements the scope described there.

**Open questions:** Capture gaps or ambiguities in the external plan as open questions, grouped by phase.

**Exit criteria:** Derive from the external plan's definition of done. If none are stated, propose criteria and confirm with the operator before writing them.

If `reviews.planning.mode` is `required`, run review checkpoint `002 implementation-plan` per the Review Flow Reference using `reviews.planning.role`.

---

## Stage 4 — Generate Phase Files

For each phase in `IMPLEMENTATION_PLAN.md`, author `phases/PHASE-NN-slug.md` through the mediated writer (a **PM-performed** write; `--phase-id` resolves the allowlisted `phases/` destination):

```
cartopian write-phase <project-root> --phase-id PHASE-NN-slug --content-file <body-path>
```

Each phase body contains:

- **Phase goal:** one or two sentences
- **Plan refs covered:** list from the plan's phase table
- **Build items:** delivery/execution tasks that produce outcomes or artifacts
- **Research items:** tasks that produce knowledge or decisions
- **Exit criteria:** copied from the plan
- **Dependencies on prior phases:** what must be complete before this phase starts

The two-digit phase number (`NN`) must match the plan section number.

If `reviews.planning.mode` is `required`, run review checkpoint `003 phases`.

---

## Stage 5 — Generate Task Files for Active Phase

Generate task files only for the **first active phase** (lowest-numbered phase with open work). Do not generate tasks for future phases — later phases may change as earlier work completes.

For each build and research item in the active phase, author `tasks/open/TASK-NN-NNN-slug.md` through the mediated writer `cartopian write-task`, following the template in `cartopian://templates/TASK.md` (a **PM-performed** write). Read that template from the MCP resource — Cartopian templates are served by the MCP server at `cartopian://templates/<NAME>.md` — the upper-case template name **with the `.md` extension** (e.g. `cartopian://templates/TASK.md`, `cartopian://templates/REPORT.md`, `cartopian://templates/SPEC.md`) — not files on your filesystem. Always include the `.md`. Do **not** open `templates/...` as a path and do **not** infer the format from an existing task; read the template resource and follow it.

```
cartopian write-task <project-root> --task-id TASK-NN-NNN --slug <slug> --content-file <body-path>
```

Populate all fields:

- `Phase:` from the phase file
- `Plan ref:` from the `PNN-KIND-NNN` table
- `Work root:` name(s) from the resolved config's `[project].work_roots` (or `n/a` if not applicable). Names only; do not write paths.
- `Assignee:` from the resolved role configuration
- `Spec:` link if a spec is being created; otherwise `none`
- `Depends on:` / `Blocked by:` from the external plan's dependency information
- `Evidence gate:` use judgment — `required` whenever concrete before-and-after evidence is appropriate (tests, validations, approvals, inspections, rehearsals, fact-checks); `n/a` only with a reason

For tasks that need specs (new interfaces, schemas, contracts), author `specs/SPEC-NN-NNN-slug.md` through the mediated writer `cartopian write-spec`, following the template in `cartopian://templates/SPEC.md`:

```
cartopian write-spec <project-root> --spec-id SPEC-NN-NNN --slug <slug> --content-file <body-path>
```

Before authoring each spec, classify the outcome governed by that spec and set `Profile: software | general`; classify the spec itself, not the overall project. Use `software` when the end outcome is executable software or a technical contract intended for software implementation (including applications, services, libraries, CLIs, automation scripts, or implementable schemas, APIs, and integrations). Use `general` for genuinely non-software outcomes such as research reports, operating procedures, launch activities, or creative assets. A project may contain both profiles.

For `software`, keep only the template's software profile. Treat the spec as the task-scoped SRS and TDS and cover **Overview & Goals**, **Functional Requirements**, **Non-Functional Requirements**, **User Stories & Use Cases**, **Architecture & Structure**, **Data Models**, **APIs & Integrations**, and **Edge Cases & Error Handling**. State required behavior and design boundaries while leaving source-level implementation decisions to the assignee. Do not include source/executable code, pseudocode, step-by-step algorithms, function or class bodies, complete configuration or build files, or copy/paste-ready implementation snippets. Contract notation such as diagrams, tables, field/type definitions, endpoint signatures, protocol grammar, and concise example payloads or input/output values is allowed.

For `general`, keep only the template's general profile. Do not select it to evade the software rules. Remove the unused profile and all template instructional text before writing either profile.

If `reviews.planning.mode` is `required`, run review checkpoint `004 tasks-and-specs`. Require the reviewer to verify profile selection and all eight SRS/TDS areas for software specs; any prohibited implementation content in a software spec is a blocking finding requiring changes.

---

## Stage 6 — Update STATE.md

Updating `STATE.md` is **PM-performed** through the mediated writer (never a raw `Write`). The plan artifacts written in Stages 3–5 exist now, so the writer composes the canonical body from the filesystem itself — do not author a body or pass `--content`:

```
cartopian write-state <project-root>
```

The composed body reflects the first active phase, no active work (nothing assigned yet), all generated tasks as open work, and the first ready task as what to do next. Do not create a prompt during plan adoption unless assignment is happening immediately; prompts belong in `prompts/` and are temporary handoff artifacts.

---

## Stage 7 — Summary

Report to the operator:

- External plan source and how it was interpreted
- Requirements handling (local, stub, or external reference)
- Number of phases generated
- Number of tasks and specs generated for the active phase
- Review status (required and completed, or policy off)
- Resolved handoff configuration
- Suggested first action, including whether to create `prompts/PROMPT-NN-NNN.md` for the first assignment

Plan adoption — including task generation — is a **scoped directive** (`protocol/CONVENTIONS.md § Request Intent`): filling the open queue does not authorize running it. Under `initiation = "operator"` (the default), end here with the summary; under `initiation = "auto"`, the newly ready queue may initiate execution via `run task`.

---

## Review Flow Reference

Uses the same checkpoint sequence as `plan-project`. See the Review Flow Reference table in `plan-project.md` for prompt/report/review file paths and `run-handoff.md` for handoff mechanics.

Checkpoint `001 requirements-and-standards` is only run if `REQUIREMENTS.md` was generated during Stage 1 of this skill (option (a)). If requirements were skipped or created as a stub, proceed directly to the implementation-plan checkpoint (`002`).
