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
2. **Role assignments** — Who fills each role?
   - **PM**: AI agent, specific tool name, or human? (default: `ai`)
   - **Operator**: Human or AI? (default: `human`)
   - **Coder**: AI agent, specific tool name, or human? (default: `ai`)
   - **Reviewer**: AI agent, specific tool name, or none? (default: `none`)
   - Are any custom roles needed? (e.g., researcher, designer)
   - Do any of the existing roles need to be renamed or removed?

### Step 3 — Generate workspace config

Write `cartopian.toml` at the workspace root with the gathered values:

```toml
[defaults]
git_versioning = <true|false>

[roles]
pm = "<value>"
operator = "<value>"
coder = "<value>"
reviewer = "<value>"
# <custom roles if any>
```

Use commented-out lines for optional settings the user did not enable.
An empty value (`""`) indicates an unset or unassigned role. A value of
`"none"` indicates the role is not used at all.
Reminder: Roles can be overridden at the project level.

### Step 4 — Initialize projects directory

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

### Step 5 — Project config (optional)

Ask the operator: "Do you want to configure a specific project now?"

If yes:

1. Ask for the **project name** (human-readable) and **project ID**
   (kebab-case slug).
2. Ask if any **role overrides** are needed for this project (different
   from workspace defaults).
3. Ask about **target repos** — paths to code repositories this project
   governs, and their default branches.
4. Write `projects/<project-id>/cartopian.toml`:

```toml
[project]
name = "<project name>"
id = "<project-id>"

[roles]
# Only include overrides — workspace defaults apply for omitted roles.
# pm = "ai"
# coder = "none"         # e.g. no coder role is used for this project
# reviewer = "claude"

[repos.<repo-name>]
path = "<relative path to local repo>"
default_branch = "main"
```

### Step 6 — Validate and summarize

1. Confirm the generated file(s) are valid TOML.
2. Print a summary of what was configured:
   - Workspace defaults
   - Role assignments (noting which are defaults vs. explicit)
   - Projects directory git status (initialized, exclude entry present)
   - Project config (if generated)
3. Suggest next steps:
   - If no project exists yet: "Run `skills/init-project.md` to scaffold
     a new project."
   - If a project exists but has no plan: "Run `skills/plan-project.md`
     to start the planning lifecycle."
