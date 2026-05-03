# Cartopian Skills

Skills are agent-executable markdown runbooks. Each skill is a
structured, step-by-step instruction document that an AI coding assistant
reads and follows. The agent interacts with the user where decisions are
needed and produces the output files.

## Available skills

| Skill | File | Purpose | CLI Handoff |
|---|---|---|---|
| **Init Workspace** | `init-workspace.md` | Generate workspace and project `cartopian.toml` config files | Configures handoff targets and automation policy |
| **Init Project** | `init-project.md` | Scaffold a new project with the correct directory structure | Scaffolds `reports/` and supports handoff overrides |
| **Plan Project** | `plan-project.md` | Walk the full lifecycle: requirements → plan → phases → tasks | Resolves handoff config, launches or instructs CLI handoffs for review checkpoints |
| **Run Handoff** | `run-handoff.md` | Execute one manual or CLI handoff and parse its report outcome | Reusable handoff/report mechanics for planning, task, and review work |
| **Run Task** | `run-task.md` | Drive one task from assignment through completion report, review, and state refresh | Uses handoff automation for assignee and reviewer work |
| **Close Plan** | `close-plan.md` | Close a completed plan, optionally archive it, and reset for the next planning cycle | Inspects `reports/` for unresolved handoffs, resets reports during closeout |

## CLI handoff automation

All skills understand CLI handoff automation. The `init-workspace` and
`init-project` skills configure handoff targets and automation policy.
The `run-handoff` skill defines the reusable mechanics for prompt
handoff, stale report handling, report parsing, timeout behavior, and
automation policy. The `plan-project` and `run-task` skills use those
mechanics for planning checkpoints and task execution. The `close-plan`
skill audits `reports/` and ensures no unresolved handoff state remains
before plan closeout.

Automation is optional. Manual handoff remains valid for every role and
every skill. See `protocol/CONVENTIONS.md` for the handoff contract and
`skills/run-handoff.md` for the executable workflow.
