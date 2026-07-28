# Skill: Init Workspace

Author the global `~/.cartopian/cartopian.toml` configuration through guided interaction and verify the installed layout. Project setup is handled separately by `skills/init-project.md`.

**Output:** A global TOML file at `~/.cartopian/cartopian.toml` and a verified install (`cartopian --help` runs).

---

## Prerequisites

- Cartopian is installed at the operator's install root (canonical: `~/.cartopian/`).
- You can edit files under your home directory (for `~/.cartopian/cartopian.toml`).

---

## Steps

### Step 1 — Detect existing config

Check whether a global `~/.cartopian/cartopian.toml` exists.

- If it exists, read it and note what's already configured.
- If it does not exist, proceed to Step 2.

### Step 2 — Gather workspace defaults

Ask the operator about workspace-wide defaults:

1. **Git versioning** — Should project PM data be git-versioned? (`true` or `false`, default `false`)
2. **Roles** — Which roles should the workspace declare? The protocol-default roster is `pm` and `operator`. For each role the operator wants in the workspace, gather a role name (operator-chosen string) and a one-line description string that names the role's responsibility. `coder`, `reviewer`, `editor`, and `researcher` are illustrative labels, not role types or defaults. Confirm whether any existing role should be renamed or removed.
3. **Review defaults and assignment** — Choose one workspace preset: **no reviews**, **planning only**, **task closure only**, or **planning and task closure**. For each required loop, choose one of the declared roles to perform it. These are global defaults only: every project can override either loop to `off` or assign another role. Never infer review policy from a role name, description, capability preset, configured agent, or permission.

### Step 3 — Gather CLI handoff agents

For each role that may use CLI launch, ask the operator:

1. **CLI handoff agent** — Should this role have a named agent executable or Cartopian agent wrapper for CLI handoff automation? If yes, what is its name? (e.g., `cartopian-codex`, `cartopian-gemini`, `cartopian-claude`)
2. **Effort** — Should this role pin an effort/thinking level? Valid levels depend on the chosen agent CLI (e.g., claude: `low|medium|high|xhigh|max`; codex: `low|medium|high|xhigh|max|ultra`; gemini/devin: not supported). Unsupported values fall back to the tool's default with a warning at launch. Leave blank to use the tool's default.
3. **Timeout** — Should this handoff have a custom timeout? Use one positive duration such as `30m` or `2h`. Leave blank to use the protocol default of `60m`.
4. **Automatic-launch permissions** — Which assigned work types may the PM launch automatically for this role: `task_run`, `task_review`, and/or `planning_review`? The list defaults empty; `[reviews]` independently decides whether review checkpoints exist and who owns them.

If the operator does not want a CLI handoff agent for a role, omit `agent`, `model`, `effort`, and `timeout` from that role's flat table. The PM will create the prompt and the operator will handle execution manually. An agent does not itself grant automatic launch; the applicable assigned work type must also appear in `auto_launch`.

### Step 4 — Gather automation policy

Present the automation choice as two presets, then refine:

1. **Initiation preset** — "How should sessions start work?"
   - **"Wait for me to start work"** (recommended default) — the PM computes and names the next task but begins execution only on an explicit directive ("continue", "run the next task"). Maps to `initiation = "operator"`; since it is the protocol default, the key may be omitted.
   - **"Automatically start ready work"** — the PM may begin execution without a directive: at session startup and when a scoped operation (e.g. task generation) leaves the queue ready. Maps to `initiation = "auto"`. Informational requests ("what's next?") stay read-only either way, and an explicit "stop"/"pause" always wins.
2. **Confirmation mode** — `each-handoff` (stop after each result) or `until-blocked` (continue until a blocker, limit, or failed report)? (default: `each-handoff`)
3. **Max handoffs per run** — How many handoffs may the PM launch in one session? (default: `1`)

For fully unattended operation the operator must choose each layer explicitly: `initiation = "auto"`, `confirmation = "until-blocked"`, a `max_handoffs_per_run` batch size, and the applicable work types in each role's `auto_launch` list (Step 3). No single answer switches them all on.

### Step 5 — Generate workspace config

Write `~/.cartopian/cartopian.toml` with the gathered values:

```toml
[defaults]
git_versioning = <true|false>

[roles.pm]
description = "<one-line description>"

[roles.operator]
description = "<one-line description>"

# [roles.<additional-role>]
# description = "<one-line description>"
# auto_launch = ["<task_run|task_review|planning_review>"]
#
# agent = "<agent or Cartopian wrapper name>"
# model = "<model>"
# effort = "<level>"
# timeout = "<duration>"

[reviews]
planning = "<required|off>"
# planning_role = "<declared role>"  # include when planning is required
task_closure = "<required|off>"
# task_role = "<declared role>"      # include when task closure is required

[automation]
# initiation = "<operator|auto>"  # omit for the "operator" default
confirmation = "<each-handoff|until-blocked>"
max_handoffs_per_run = <number>
```

Write both review modes explicitly so the global choice is visible; include role-assignment keys only for required loops. Use commented-out lines for optional settings the user did not enable. To remove a role from a project, omit its role table. Reminder: projects may override role fields, review policy/assignment, handoff agent/options, and automatic-launch permissions independently.

Do not generate `[agents.*]` sections.

### Step 6 — Verify install

Confirm the installed layout and CLI availability:

1. Check that `~/.cartopian/` contains `protocol/`, `templates/`, `skills/`, `wrappers/`, `bin/cartopian`, and `CHANGELOG.md`.
2. Run `cartopian --help` and confirm it exits 0.

### Step 7 — Initialize a new project (optional)

Ask the operator: "Do you want to initialize a new project now?"

If yes:

1. Run `skills/init-project.md` and follow its prompts to:
   - Scaffold the project directory at an operator-supplied absolute path.
   - Generate the project-level `cartopian.toml` via the CLI.
   - Register the project in the registry; verify with `cartopian discover-projects`.

### Step 8 — Validate and summarize

1. Confirm the generated file(s) are valid TOML.
2. Print a summary of what was configured:
   - Workspace defaults
   - Role descriptions and declared roles (noting which are defaults vs. explicit)
   - Review defaults and the role assigned to each required loop
   - CLI handoff agents configured
   - Automation policy
   - Install layout presence and `cartopian --help` result
   - Any project initialized via `skills/init-project.md`
3. Suggest next steps:
   - If no project exists yet: "Run `skills/init-project.md` to scaffold a new project."
   - If a project exists but has no plan: "Run `skills/plan-project.md` to start the planning lifecycle."
   - If a project already has a plan: "Run `skills/start-session.md` to read `STATE.md` and choose the current or next PM action."
