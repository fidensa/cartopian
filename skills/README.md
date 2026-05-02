# Cartopian Skills

Skills are agent-executable markdown runbooks. Each skill is a
structured, step-by-step instruction document that an AI coding assistant
reads and follows. The agent interacts with the user where decisions are
needed and produces the output files.

## Available skills

| Skill | File | Purpose |
|---|---|---|
| **Init Workspace** | `init-workspace.md` | Generate workspace and project `cartopian.toml` config files |
| **Init Project** | `init-project.md` | Scaffold a new project with the correct directory structure |
| **Plan Project** | `plan-project.md` | Walk the full lifecycle: requirements → plan → phases → tasks |
| **Close Plan** | `close-plan.md` | Close a completed plan, optionally archive it, and reset for the next planning cycle |
