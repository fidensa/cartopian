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
2. **Roles** — Which roles should the workspace declare? The protocol-default roster is `pm` and `operator`. For each role the operator wants in the workspace, gather a role name (operator-chosen string) and a one-line description string that names the role's responsibility. Common example labels operators add to the default roster include `coder` (e.g., "Implements tasks per spec.") and `reviewer` (e.g., "Reviews per acceptance evidence."); these are illustrative, not defaults. Confirm whether any existing role should be renamed or removed.

### Step 3 — Gather CLI handoff targets

For each role that should dispatch automatically, ask the operator:

1. **CLI handoff target** — Should this role have a named executable for CLI handoff automation? If yes, what is the executable name? (e.g., `codex`, `gemini`, `claude`)
2. **Auto-start** — Should the PM automatically launch this executable after assignment is authorized? (`true` or `false`, default `false`)
3. **Timeout** — Should this handoff have a custom timeout? Use a duration string such as `30m`, `2h`, or `1h30m`. Leave blank to use the protocol default of `60m`.

If the operator does not want automated CLI handoff for a role, skip the `[handoffs.*]` section for that role. The PM will create the prompt and the operator will handle execution manually (plain manual handoff). Whether a role dispatches automatically is inferred from the presence of a `[handoffs.<role>]` block, not from any field on the role itself.

### Step 4 — Gather automation policy

Ask the operator about the default automation confirmation policy:

1. **Confirmation mode** — `each-handoff` (stop after each result) or `until-blocked` (continue until a blocker, limit, or failed report)? (default: `each-handoff`)
2. **Max handoffs per run** — How many handoffs may the PM launch in one session? (default: `1`)

### Step 5 — Generate workspace config

Write `~/.cartopian/cartopian.toml` with the gathered values:

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

Use commented-out lines for optional settings the user did not enable. To remove a role from a project, omit its key from `[roles]`. Reminder: roles and handoff config can be overridden at the project level.

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
   - CLI handoff targets configured
   - Automation policy
   - Install layout presence and `cartopian --help` result
   - Any project initialized via `skills/init-project.md`
3. Suggest next steps:
   - If no project exists yet: "Run `skills/init-project.md` to scaffold a new project."
   - If a project exists but has no plan: "Run `skills/plan-project.md` to start the planning lifecycle."
   - If a project already has a plan: "Run `skills/start-session.md` to read `STATE.md` and choose the current or next PM action."
