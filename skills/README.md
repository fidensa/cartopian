# Cartopian Skills

Skills are agent-executable markdown runbooks. Each skill is a structured, step-by-step instruction document that an AI coding assistant reads and follows. The agent interacts with the user where decisions are needed and produces the output files.

## Available skills

| Skill | File | Purpose | CLI Handoff |
| --- | --- | --- | --- |
| **Use Cartopian** | `use-cartopian.md` | Entry point for PM mode. Reads the protocol contract and startup runbook, then begins registry-based project selection. Does not inspect the current directory. | None — bootstraps PM mode; no handoff |
| **Init Workspace** | `init-workspace.md` | Generate workspace and project `cartopian.toml` config files | Configures handoff targets and automation policy |
| **Init Project** | `init-project.md` | Scaffold a new project with the correct directory structure | Scaffolds `reports/` and supports handoff overrides |
| **Adopt Requirements** | `adopt-requirements.md` | Generate `REQUIREMENTS.md` from external sources (JIRA, Confluence, PRD, etc.) without running the full planning pipeline | None — PM-only skill; no handoff |
| **Adopt Plan** | `adopt-plan.md` | Migrate an existing implementation plan from any external format into Cartopian artifacts; requirements may be external, stubbed, or adopted inline | Resolves handoff config; launches or instructs CLI handoffs for review checkpoints |
| **Plan Project** | `plan-project.md` | Walk the full lifecycle from requirements gathering through plan, phases, and tasks | Resolves handoff config, launches or instructs CLI handoffs for review checkpoints |
| **Start Session** | `start-session.md` | Resolve the current project, read `STATE.md`, and act on the request per its intent class and the resolved `[automation] initiation` policy | On an execution directive (or `initiation = "auto"`), continues into `run-task.md` without a confirmation prompt; informational requests report and stop; stops for blockers, plan-level forks, and operator-reserved decisions |
| **Run Handoff** | `run-handoff.md` | Execute one manual or CLI handoff and parse its report outcome | Reusable handoff/report mechanics for planning, task, and review work |
| **Run Task** | `run-task.md` | Drive one task from assignment through completion report, review, and state refresh | Uses handoff automation for assignee and reviewer work |
| **Close Plan** | `close-plan.md` | Close a completed plan, optionally archive it, and reset for the next planning cycle | Inspects `reports/` for unresolved handoffs, resets reports during closeout |
| **Register MCP** | `register-mcp.md` | Detect installed agents, show registration status, and register `cartopian-mcp` with any/all selected agents; called by the install skill and usable standalone | None — installer-side skill; no handoff |
| **Check For Updates** | `check-for-updates.md` | Compare the installed Cartopian ref against the latest GitHub release and re-run `install-cartopian` on approval | None — installer-side skill; no handoff |

## CLI handoff automation

Planning, task, and review workflows understand CLI handoff automation. The `init-workspace` and `init-project` skills configure handoff targets and automation policy. The `run-handoff` skill defines the reusable mechanics for prompt handoff, stale report handling, report parsing, timeout behavior, and automation policy. The `plan-project` and `run-task` skills use those mechanics for planning checkpoints and task execution. The `close-plan` skill audits `reports/` and ensures no unresolved handoff state remains before plan closeout.

Automation is optional. Manual handoff remains valid for every role and every skill. See `protocol/CONVENTIONS.md` for the handoff contract and `skills/run-handoff.md` for the executable workflow.

## Session startup

The entry point is always `use-cartopian.md`. It issues an imperative startup contract: read the protocol, load the startup runbook, select a project via the registry. It works from any directory because it never inspects the current working directory or any local config.

Once a project is selected, `start-session.md` takes over for project-agnostic PM startup requests like "start working", "continue", or "check `STATE.md`". In a workspace with more than one eligible project, the PM asks which project to use before reading or mutating project lifecycle artifacts. Once the project is selected, an agent PM reads `STATE.md`, reports the current/next protocol action, and acts per request intent (`protocol/CONVENTIONS.md § Request Intent`): an execution directive such as "continue" or "start working" — or a resolved `[automation] initiation = "auto"` policy — begins execution without a further prompt; an informational request such as "check `STATE.md`" reports and stops.
