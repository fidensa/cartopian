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
| **Close Plan** | `close-plan.md` | Close a completed plan, optionally archive it, and reset for the next planning cycle | Inspects `reports/` for unresolved handoffs, resets reports during closeout |

## CLI handoff automation

All skills understand CLI handoff automation. The `init-workspace` and
`init-project` skills configure handoff targets and automation policy.
The `plan-project` skill resolves the effective config and uses it to
launch or instruct handoffs at review checkpoints. The `close-plan`
skill audits `reports/` and ensures no unresolved handoff state remains
before plan closeout.

Automation is optional. Manual handoff remains valid for every role and
every skill. See `protocol/CONVENTIONS.md` for the full CLI handoff
specification.
