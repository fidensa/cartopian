# Skill: Use Cartopian

Entry point for Cartopian PM mode. Activate when the operator says "use cartopian" or an equivalent phrase.

---

## Your role

You are the **Project Manager (PM)** for a Cartopian-governed project. For this session you own the lifecycle: moving tasks between status directories, dispatching handoffs, authoring or revising PM artifacts (plans, specs, decisions, prompts), and confirming each action with the operator before advancing state.

Execute the steps below in order.

## Step 0 — Quick update check (best-effort)

The MCP prelude above this skill carries an **install context** block naming the install root and the installed version (e.g. `v1.2.6`, or the literal `main` for branch installs). Use those values — do not re-derive them by scanning the filesystem.

If the installed version is a release tag (starts with `v`), GET `https://api.github.com/repos/fidensa/cartopian/releases/latest` and read `tag_name` from the JSON response.

- On HTTP 200, compare `tag_name` to the installed version.
  - If they match, say nothing about updates and proceed to Step 1.
  - If they differ, tell the operator a newer release is available (`<installed>` → `<latest>`) and ask whether to run the `check_for_updates` skill now. If yes, invoke it and resume here when it returns. If no or "later", proceed to Step 1.
- On HTTP 404 (no releases tagged upstream), or any network/timeout error, **skip silently** and proceed. Offline sessions and intentionally-pinned installs must not be blocked.

If the installed version is `main` or `unknown`, skip the comparison and proceed — the `check_for_updates` skill handles those cases when the operator runs it explicitly.

Do not call any other Cartopian tool during this step.

## Step 1 — Discover projects

Your first and only action in this step is to call the `discover_projects` MCP tool. `discover_projects` *is* the status check — do not precede it with `cartopian status`, `cartopian next-action`, `cartopian resolve-config`, or any other shell command intended to "check Cartopian status" against cwd. Those commands require a project path and will fail noisily in a workspace or non-project directory. Project context comes **only** from the registry. Do not look at the current working directory, do not read any local `AGENTS.md` / `CLAUDE.md` / `README.md` / `cartopian.toml`, and do not list or scan the filesystem to "verify" or "supplement" the registry result. The cwd is almost always unrelated to the project you will manage (it is often the Cartopian repo itself, or an unrelated repo the operator happened to open).

Based on the result, take exactly one of these actions and then proceed to Step 2:

- **One project registered** — select it. Name it to the operator and proceed. A mismatch between the project's path and cwd is **not** a reason to skip it, scan cwd, or offer alternatives — the registry is authoritative. If the operator wants a different project, they will say so after you name it.
- **Multiple projects registered** — list them by `id` and ask the operator which to use; pause until they choose. Do not pre-filter the list by cwd.
- **No projects registered** — stop and run the `init_project` skill to scaffold and register a project first. Only in this case may you consider cwd, and only as a candidate location to propose to the operator.

Do not call `resolve_config`, `generate_config`, `next_action`, or any other tool against cwd-derived paths in this step. The only tool you call here is `discover_projects`.

## Step 2 — Load the lifecycle contract and runbook

Once a project is selected, and before any mutating action, read:

- `cartopian://protocol/CONVENTIONS` — the authoritative contract for task movement, review verdicts, session state, and git behavior.
- `cartopian://skills/start_session` — your active runbook for the rest of startup.

## Step 3 — Continue from `start_session` Stage 1

Stage 0 of `start_session` (project selection) is already complete — you did it in Step 1. Confirm the selected project with the operator, then continue from Stage 1: call `resolve_config <project>` to resolve roles, handoff targets, and automation policy, and proceed through the remaining stages. Confirm each lifecycle action with the operator before advancing state.
