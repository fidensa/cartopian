---
description: Enter Cartopian PM mode — delegates to the cartopian MCP server's use_cartopian prompt.
---

# /use-cartopian

Enter **Cartopian PM mode** now.

Read the resource `cartopian://skills/use_cartopian` from the `cartopian` MCP server and follow it literally — it is the authoritative startup runbook, and it begins with the **install-context block** (install root + installed version) that the Stage 0 update check needs. Read every step before acting.

(The same runbook is also published as the `use_cartopian` MCP prompt, for clients whose prompt picker invokes it directly. Reading the resource is the equivalent that works here, because a model cannot invoke an MCP prompt itself — so do not wait on a prompt invocation; read the resource.)

## Hard constraints during startup

Until the prompt's Stage 0 (project selection from the registry) is complete:

- Do **not** infer the target project from the current working directory.
- Do **not** read `AGENTS.md`, `CLAUDE.md`, `README.md`, `cartopian.toml`, `.git/`, or any other repo/workspace artifact relative to cwd. They describe whatever repository the operator happens to be in — including the Cartopian source repo itself — not the Cartopian-governed project you are about to manage.
- Do **not** call `resolve_config` against the current working directory. Project context comes from `discover_projects` → operator selection → `resolve_config <id-or-registry-path>`.

If `discover_projects` returns zero registered projects, stop and follow the `init project` skill before continuing.
