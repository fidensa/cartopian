# Skill: Use Cartopian

Entry point for Cartopian PM mode. Activate when the operator says "use cartopian" or an equivalent phrase.

---

You are now in **Cartopian PM mode**. Execute the following steps in order. Do not skip steps, reorder them, or infer the project from the current directory.

## Step 1 — Read the lifecycle contract

Read `cartopian://protocol/CONVENTIONS` now. This is the authoritative contract for all task movement, review verdicts, session state, and git behavior. You must read it before any mutating action.

## Step 2 — Read the session startup skill

Read `cartopian://skills/start_session` now. This is your active runbook for the remainder of this startup sequence.

## Step 3 — Constraints until project is selected

Do **not** inspect any of the following until a registered project is selected via the registry:

- The current working directory
- Any local repository or workspace config files
- Any local `AGENTS.md` file
- Any project-level lifecycle artifacts

This startup sequence is **registry-only**. It must work correctly from any directory.

## Step 4 — Begin project selection

Call `discover_projects` now. Follow Stage 0 of `start_session` exactly:

- **One project registered** — select it automatically and name it to the operator.
- **Multiple projects registered** — list them by ID and ask the operator to choose; pause until a choice is made.
- **No projects registered** — stop and run `init project` to scaffold and register a project first.

Do not advance to Stage 1 of `start_session` until a project is selected and confirmed.
