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
2. **Roles** — Which roles should the workspace declare? The
   protocol-default roster is `pm` and `operator`. For each role
   the operator wants in the workspace, gather a role name
   (operator-chosen string) and a one-line description string that
   names the role's responsibility. Common example labels
   operators add to the default roster include `coder` (e.g.,
   "Implements tasks per spec.") and `reviewer` (e.g., "Reviews
   per acceptance evidence."); these are illustrative, not
   defaults. Confirm whether any existing role should be renamed
   or removed.

### Step 3 — Gather CLI handoff targets

For each role that should dispatch automatically, ask the
operator:

1. **CLI handoff target** — Should this role have a named executable
   for CLI handoff automation? If yes, what is the executable name?
   (e.g., `codex`, `gemini`, `claude`)
2. **Auto-start** — Should the PM automatically launch this executable
   after assignment is authorized? (`true` or `false`, default `false`)
3. **Timeout** — Should this handoff have a custom timeout? Use a
   duration string such as `30m`, `2h`, or `1h30m`. Leave blank to use
   the protocol default of `60m`.

If the operator does not want automated CLI handoff for a role,
skip the `[handoffs.*]` section for that role. The PM will create
the prompt and the operator will handle execution manually
(plain manual handoff). Whether a role dispatches automatically
is inferred from the presence of a `[handoffs.<role>]` block, not
from any field on the role itself.

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
# Each value is a one-line description string describing the
# role's responsibility. A role exists in the workspace iff its
# key appears here. Whether a role dispatches automatically is
# inferred from the presence of a `[handoffs.<role>]` block
# below; there is no kind field on the role itself.
pm = "<one-line description>"
operator = "<one-line description>"
# <additional roles operators chose, e.g. coder / reviewer>

# [handoffs.<role>]
# agent = "<executable name>"
# auto_start = <true|false>
# timeout = "<duration>"

[automation]
confirmation = "<each-handoff|until-blocked>"
max_handoffs_per_run = <number>
```

Use commented-out lines for optional settings the user did not
enable. To remove a role from a project, omit its key from
`[roles]`. Reminder: roles and handoff config can be overridden at
the project level.

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
2. Ask whether any **role overrides** are needed for this project
   (different from workspace defaults): adding a role, replacing
   a role's description, or removing a role by omitting its key.
3. Ask about **CLI handoff target overrides** for any role that
   should dispatch automatically.
4. Ask about **automation policy overrides** for this project.
5. Write `projects/<project-id>/cartopian.toml`:

```toml
[project]
name = "<project name>"
id = "<project-id>"

[roles]
# Only include overrides — workspace defaults apply for omitted
# roles. Each value is a one-line description string. Whether a
# role dispatches automatically is inferred from the presence of
# a `[handoffs.<role>]` block; there is no kind field.
# pm = "<one-line description that overrides workspace>"
# reviewer = "Reviews per acceptance evidence."

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
   - Role descriptions and declared roles (noting which are defaults vs. explicit)
   - CLI handoff targets configured
   - Automation policy
   - Projects directory git status (initialized, exclude entry present)
   - Project config (if generated)
3. Suggest next steps:
   - If no project exists yet: "Run `skills/init-project.md` to scaffold
     a new project."
   - If a project exists but has no plan: "Run `skills/plan-project.md`
     to start the planning lifecycle."
   - If a project already has a plan: "Run `skills/start-session.md` to
     read `STATE.md` and choose the current or next PM action."
