---
name: use-cartopian
description: Enter Cartopian PM mode. Use when the operator says "use cartopian" or asks to start, resume, or manage a Cartopian-governed project session.
---

# Use Cartopian

Enter **Cartopian PM mode** now.

Read the resource `cartopian://skills/use_cartopian` from the `cartopian` MCP server and follow it literally — it is the authoritative startup runbook. Read every step before acting.

(You cannot invoke an MCP prompt yourself — that is a human-initiated action in the client's prompt picker. The identically-named `use_cartopian` prompt carries the same runbook for that path. Do not wait on a prompt invocation; read the resource.)

## Hard constraints during startup

Until the prompt's Stage 0 (project selection from the registry) is complete:

- Do **not** infer the target project from the current working directory.
- Do **not** read `AGENTS.md`, `CLAUDE.md`, `README.md`, `cartopian.toml`, `.git/`, or any other repo/workspace artifact relative to cwd. They describe whatever repository the operator happens to be in — including the Cartopian source repo itself — not the Cartopian-governed project you are about to manage.
- Do **not** call `resolve_config` against the current working directory. Project context comes from `discover_projects` → operator selection → `resolve_config <id-or-registry-path>`.

If `discover_projects` returns zero registered projects, stop and follow the `init project` skill before continuing.
