# Cartopian Skills

Skills are agent-executable markdown runbooks. Each skill is a
structured, step-by-step instruction document that an AI coding assistant
reads and follows. The agent interacts with the user where decisions are
needed and produces the output files.

No binary to install. No npm dependency. No framework lock-in. Works
with any AI assistant that can read files and write to the filesystem.

## How to use a skill

Tell your AI agent:

> Read `skills/<skill-name>.md` and follow it.

The agent will walk through the steps, ask you questions where input is
needed, and produce the output files in the correct locations.

## Available skills

| Skill | File | Purpose |
|---|---|---|
| **Init Workspace** | `init-workspace.md` | Generate workspace and project `cartopian.toml` config files |
| **Init Project** | `init-project.md` | Scaffold a new project with the correct directory structure |
| **Plan Project** | `plan-project.md` | Walk the full lifecycle: requirements → plan → phases → tasks |

## Roles

Skills interact with four configurable roles defined in `cartopian.toml`:

| Role | Description | Default |
|---|---|---|
| **PM** | Drives planning. Produces implementation plans, phases, tasks, specs. Produces assignment prompts and proposes assignees. | AI agent |
| **Operator** | Human decision-maker. Approves plans, confirms assignments, reports progress. | Human |
| **Coder** | Implements tasks. Receives prompts, writes code, produces deliverables. | AI agent |
| **Reviewer** | Reviews artifacts at each stage. Produces findings that feed back to the PM. | AI agent (optional) |

The same agent can fill multiple roles. Roles are extensible — define
custom roles in `cartopian.toml` as needed. See `protocol/CONVENTIONS.md`
for the full protocol specification.
