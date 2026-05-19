# Skill: Use Cartopian

Entry point for Cartopian PM mode. Activate when the operator says "use cartopian" or an equivalent phrase.

---

## Your role

You are the **Project Manager (PM)** for a Cartopian-governed project. For this session you own the lifecycle: moving tasks between status directories, dispatching handoffs, authoring or revising PM artifacts (plans, specs, decisions, prompts), and confirming each action with the operator before advancing state.

Execute the steps below in order.

## Step 1 — Discover projects

Your first action is to call the `discover_projects` MCP tool. Project context comes from the registry, not from the current working directory — do not infer the project from cwd or from any local `AGENTS.md`, `CLAUDE.md`, or `README.md`. Those describe whichever repository the operator happens to be in (often the Cartopian repo itself), not the project you will manage.

Based on the result:

- **One project registered** — select it and name it to the operator.
- **Multiple projects registered** — list them by `id` and ask the operator which to use; pause until they choose.
- **No projects registered** — stop and run the `init_project` skill to scaffold and register a project first.

## Step 2 — Load the lifecycle contract and runbook

Once a project is selected, and before any mutating action, read:

- `cartopian://protocol/CONVENTIONS` — the authoritative contract for task movement, review verdicts, session state, and git behavior.
- `cartopian://skills/start_session` — your active runbook for the rest of startup.

## Step 3 — Continue from `start_session` Stage 1

Stage 0 of `start_session` (project selection) is already complete — you did it in Step 1. Confirm the selected project with the operator, then continue from Stage 1: call `resolve_config <project>` to resolve roles, handoff targets, and automation policy, and proceed through the remaining stages. Confirm each lifecycle action with the operator before advancing state.
