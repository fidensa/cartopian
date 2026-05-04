# Skill: Init Workspace

Generate workspace-level and/or project-level `cartopian.toml`
configuration files through guided interaction.

**Output:** One or two TOML files written to the correct locations.

---

## Prerequisites

- The Cartopian repo is cloned and you can read/write files in it.
- You know the path to the Cartopian workspace root (where `protocol/`
  and `templates/` live).

---

## Steps

### Step 1 — Detect existing config

Check whether a workspace-level `cartopian.toml` exists at the Cartopian
workspace root.

- If it exists, read it and note what's already configured.
- If it does not exist, proceed to Step 2.

### Step 2 — Gather workspace defaults

Ask the operator about workspace-wide defaults:

1. **Git versioning** — Should project PM data be git-versioned?
   (`true` or `false`, default `false`)
2. **Role kinds** — What kind of assignee fills each role?
   - **PM**: `agent`, `human`, or `none`? (default: `agent`)
   - **Operator**: `human` or `agent`? (default: `human`)
   - **Coder**: `agent`, `human`, or `none`? (default: `agent`)
   - **Reviewer**: `agent`, `human`, or `none`? (default: `none`)
   - Are any custom roles needed? (e.g., researcher, designer)
   - Do any of the existing roles need to be renamed or removed?

### Step 3 — Gather CLI handoff targets

For each role set to `agent`, ask the operator:

1. **CLI handoff target** — Should this role have a named executable
   for CLI handoff automation? If yes, what is the executable name?
   (e.g., `codex`, `gemini`, `claude`)
2. **Auto-start** — Should the PM automatically launch this executable
   after assignment is authorized? (`true` or `false`, default `false`)
3. **Timeout** — Should this handoff have a custom timeout? Use a
   duration string such as `30m`, `2h`, or `1h30m`. Leave blank to use
   the protocol default of `60m`.

If the operator does not want CLI handoff for an agent role, skip the
`[handoffs.*]` section for that role. The PM will create the prompt and
the operator will handle execution manually (plain manual handoff).

### Step 4 — Gather automation policy

Ask the operator about the default automation confirmation policy:

1. **Confirmation mode** — `each-handoff` (stop after each result) or
   `until-blocked` (continue until a blocker, limit, or failed report)?
   (default: `each-handoff`)
2. **Max handoffs per run** — How many handoffs may the PM launch in one
   session? (default: `1`)

### Step 5 — Generate workspace config

Write `cartopian.toml` at the workspace root with the gathered values:

```toml
[defaults]
git_versioning = <true|false>

[roles]
# Role kind values: "human", "agent", "none", or "" (unset).
# Roles describe assignee kind, not tool names.
pm = "<value>"
operator = "<value>"
coder = "<value>"
reviewer = "<value>"
# <custom roles if any>

# [handoffs.<role>]
# agent = "<executable name>"
# auto_start = <true|false>
# timeout = "<duration>"

[automation]
confirmation = "<each-handoff|until-blocked>"
max_handoffs_per_run = <number>
```

Use commented-out lines for optional settings the user did not enable.
An empty value (`""`) indicates an unset or unassigned role. A value of
`"none"` indicates the role is not used at all.
Reminder: Roles and handoff config can be overridden at the project
level.

Do not generate `[agents.*]` sections.

### Step 6 — Initialize projects directory

If `git_versioning` is `true` and the `projects/` directory does not
already contain a `.git` directory:

1. Run `git init projects/`.
2. Write the following to `projects/.git/info/exclude`:

   ```
   /sample-project/
   ```

   This keeps `sample-project/` out of the nested projects repo. The
   parent Cartopian repo tracks `projects/sample-project/` via its own
   `.gitignore` exceptions. The `info/exclude` mechanism is local to the
   nested repo only — unlike a `projects/.gitignore`, it won't interfere
   with the parent repo's file discovery.

If `projects/.git` already exists, verify that `/sample-project/`
appears in `projects/.git/info/exclude`. If missing, append it.

### Step 7 — Project config (optional)

Ask the operator: "Do you want to configure a specific project now?"

If yes:

1. Ask for the **project name** (human-readable) and **project ID**
   (kebab-case slug).
2. Ask if any **role kind overrides** are needed for this project
   (different from workspace defaults).
3. Ask about **CLI handoff target overrides** for any agent roles.
4. Ask about **automation policy overrides** for this project.
5. Write `projects/<project-id>/cartopian.toml`:

```toml
[project]
name = "<project name>"
id = "<project-id>"

[roles]
# Only include overrides — workspace defaults apply for omitted roles.
# Role kind values: "human", "agent", "none", or "" (unset).
# pm = "agent"
# coder = "none"         # e.g. no coder role is used for this project
# reviewer = "agent"

# [handoffs.<role>]
# agent = "<executable name>"
# auto_start = <true|false>
# timeout = "<duration>"

# [automation]
# confirmation = "each-handoff"
# max_handoffs_per_run = 1
```

Target product repos are not declared in `cartopian.toml`. Each task
records its own `Repo subpath:` and the assignee CLI is launched with
cwd at the parent of the workspace root (see `protocol/CONVENTIONS.md`
→ Handoffs → Launch Directory).

### Step 8 — Validate and summarize

1. Confirm the generated file(s) are valid TOML.
2. Print a summary of what was configured:
   - Workspace defaults
   - Role kind assignments (noting which are defaults vs. explicit)
   - CLI handoff targets configured
   - Automation policy
   - Projects directory git status (initialized, exclude entry present)
   - Project config (if generated)
3. Suggest next steps:
   - If no project exists yet: "Run `skills/init-project.md` to scaffold
     a new project."
   - If a project exists but has no plan: "Run `skills/plan-project.md`
     to start the planning lifecycle."
