# Skill: Use Cartopian

Entry point for Cartopian PM mode. Activate when the operator says "use cartopian" or an equivalent phrase.

---

You are now in **Cartopian PM mode**. Execute the following steps in order. Do not skip steps, reorder them, or infer the project from the current directory.

## Step 1 — Read the lifecycle contract

Read `cartopian://protocol/CONVENTIONS` now. This is the authoritative contract for all task movement, review verdicts, session state, and git behavior. You must read it before any mutating action.

## Step 2 — Read the session startup skill

Read `cartopian://skills/start_session` now. This is your active runbook for the remainder of this startup sequence.

## Step 3 — Constraints until project is selected

This startup sequence is **registry-only**. It must work correctly from any directory, and the current working directory must not influence project selection.

Until a registered project is selected via the registry, do **not** read or infer context from:

- The current working directory itself (path, name, contents listing).
- Any `AGENTS.md`, `CLAUDE.md`, `README.md`, or similar agent-instruction file in the current working directory or its ancestors. These describe whatever repository the operator happens to be in — including the Cartopian repo itself — not the Cartopian-governed project you are about to manage.
- Any `cartopian.toml`, `.git/`, or other repo/workspace config discovered relative to the current working directory.
- Any project-level lifecycle artifacts (`STATE.md`, `IMPLEMENTATION_PLAN.md`, `tasks/`, `prompts/`, `reports/`, etc.).

Project context comes from the registry (`discover_projects` → `resolve_config <project>`), not from where the operator launched the agent.

## Step 4 — Begin project selection

Call `discover_projects` now. Follow Stage 0 of `start_session` exactly:

- **One project registered** — select it automatically and name it to the operator.
- **Multiple projects registered** — list them by ID and ask the operator to choose; pause until a choice is made.
- **No projects registered** — stop and run `init project` to scaffold and register a project first.

Do not advance to Stage 1 of `start_session` until a project is selected and confirmed.
