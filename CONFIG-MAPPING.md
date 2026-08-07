# Cartopian configuration mapping

This is the plain-language reference for every Cartopian setting: where it lives, what it accepts, and what it changes. The executable authority is `cli/config_schema.py::CONFIG_SCHEMA`. This page describes that authority and defines nothing of its own. Migration behavior is owned by `cli/config_migration.py`.

The current project schema version is `v0.10.0`. `cartopian generate-config` stamps it into new projects, so you never type it by hand.

## The three files

| File | Who owns it | What belongs in it |
| --- | --- | --- |
| `~/.cartopian/cartopian.toml` | You, across all projects | Defaults you want everywhere. The installer seeds a fully commented template, so it starts empty and changes nothing until you uncomment a line. |
| `<project-root>/cartopian.toml` | The project | The project's name and id, plus any setting that should travel with the project. Safe to commit. |
| `<project-root>/cartopian.local.toml` | One machine | Absolute paths for work-root names the project declares. Gitignored, never committed. |

## Which value wins

Cartopian looks in the project file first, then the global file, then its own built-in defaults. The first place a value appears wins.

Merging happens setting by setting, not file by file. A project that sets one review key keeps the global values for the others. Roles merge by role name and then by field, so a project can change one role's timeout without restating its description.

Two settings replace instead of merging: `grants` and `auto_launch`. If a project lists either one for a role, the project list is the whole list for that role.

Two tables are locked to one file. `[project]` is only valid in the project file. `[work_roots]` is only valid in the machine-local file.

## Checking your configuration

Run `cartopian resolve-config <project-root>`, or call the `resolve_config` MCP tool. It reads all three files, validates them together, and prints the final effective settings with the source of each value.

Validation fails closed. Cartopian refuses rather than guesses when it finds an unknown key, a value outside a closed list, a review that names a role nobody declared, a permission that does not apply to the role, or a declared work root with no machine-local path.

## Project identity

`[project]` is required in the project file.

| Field | Accepted values | Required |
| --- | --- | --- |
| `project.id` | Lowercase letters, digits, and hyphens, starting with a letter or digit | Yes |
| `project.name` | Any non-empty text | Yes |
| `project.project_schema_version` | A version string shaped like vX.Y.Z, currently v0.10.0 | Yes |
| `project.work_roots` | A list of short names you choose, each unique, using letters, digits, hyphens, and underscores | No |

```toml
[project]
id = "my-project"
name = "My Project"
project_schema_version = "v0.10.0"
work_roots = ["product", "design"]
```

The project schema version tracks the layout of a project's files and settings. It is not the Cartopian release version, the installed content revision, the running server identity, or the MCP protocol version. None of those stands in for another.

## Roles

A role is one flat table. `pm` and `operator` always exist. Every other role is one you name and describe.

A role's name and description carry no authority. They help the PM match work to the right assignee. Access comes from `grants`, review duty comes from `[reviews]`, and permission to launch automatically comes from `auto_launch`.

| Field | Accepted values | What it does |
| --- | --- | --- |
| `roles.<role>.description` | Any non-empty text | Tells the PM what this role is for. Required for roles you define. |
| `roles.<role>.grants` | Any mix of the capability and preset names listed below | Sets what the role may read and write when containment is on. |
| `roles.<role>.agent` | Any command on your PATH, such as a shipped wrapper name | Names the tool that runs this role's work. Leave it out for manual handoff. |
| `roles.<role>.model` | Any model name the chosen agent accepts | Pins the agent to one model. |
| `roles.<role>.effort` | Any effort word the chosen agent accepts | Sets the thinking or effort level. |
| `roles.<role>.timeout` | A whole number with an `s`, `m`, or `h` suffix, such as 90s, 30m, or 2h | Caps how long one handoff may run. Dispatch falls back to 60m. |
| `roles.<role>.auto_launch` | `task_run`, `task_review`, `planning_review` | Lets the PM start this role's assigned work without asking you first. |

```toml
[roles.coder]
description = "Implements tasks per spec."
grants = ["coder-like"]
auto_launch = ["task_run"]

agent = "cartopian-codex"
model = "gpt-5-codex"
effort = "high"
timeout = "60m"
```

The `pm` role is interactive. It may not have an agent and may not declare automatic launch.

### What a role may access

`grants` is optional. With no role declaring it, every session behaves as though it holds everything. The moment any role declares `grants`, containment turns on for the whole project and every role is limited to what it lists. A typo never widens access, since an unknown name is refused outright. `CAPABILITIES.md` covers enforcement in detail.

Individual capabilities:

| Capability | Allows |
| --- | --- |
| `read:governance` | Read plan, phase, task, spec, decision, and state files, plus configuration. |
| `read:reports` | Read reports and reviews. |
| `read:prompts` | Read the `prompts/` directory, which holds the role's own handoff. |
| `read:work-roots` | Read the product tree. |
| `write:plan` | Write plan artifacts. |
| `write:lifecycle` | Move tasks, update state, and write protocol files. |
| `write:decisions` | Record decisions. |
| `write:reports` | Write reports and reviews. |
| `write:worktree` | Change files in the product tree. |
| `dispatch` | Hand work off to another agent. |

Presets are named bundles. Use a preset name anywhere a capability name is valid, and mix the two freely, such as `grants = ["reviewer-like", "write:plan"]`.

| Preset | Expands to |
| --- | --- |
| `coder-like` | `read:prompts`, `read:work-roots`, `write:worktree`, `write:reports` |
| `reviewer-like` | `read:governance`, `read:reports`, `read:prompts`, `read:work-roots`, `write:reports` |
| `planner-like` | `read:governance`, `read:reports`, `read:prompts`, `write:plan` |
| `pm-with-planner` | `read:governance`, `read:reports`, `read:prompts`, `write:lifecycle`, `dispatch` |
| `pm-solo` | `read:governance`, `read:reports`, `read:prompts`, `write:plan`, `write:lifecycle`, `dispatch` |

`coder-like` leaves out the governance and report reads on purpose, since the PM writes everything a coder needs into the prompt. `reviewer-like` keeps them, since a reviewer has to look at the evidence directly. The two PM presets stay out of the product tree.

### Which agent runs the work

`agent` is any command Cartopian can run on your PATH. Cartopian ships four wrappers that add the right non-interactive flags for popular CLIs. Use the wrapper name, not the raw tool name.

| Wrapper name | Runs | Effort words it understands |
| --- | --- | --- |
| `cartopian-claude` | Claude Code | low, medium, high, xhigh, max |
| `cartopian-codex` | Codex | low, medium, high, xhigh, max, ultra |
| `cartopian-gemini` | Gemini CLI | none; the tool has no effort flag, so the wrapper skips it |
| `cartopian-devin` | Devin | none; the tool has no effort flag, so the wrapper skips it |

The same four names work on Windows, where the installer ships `.cmd` and `.ps1` versions. Any other program is valid too, as long as it accepts one absolute prompt path as its argument. See `wrappers/README.md`.

`model` and `effort` are passed through as the environment variables `CARTOPIAN_MODEL` and `CARTOPIAN_EFFORT`. Each wrapper turns them into its own flags. An effort word a wrapper does not recognize is dropped with a one-line notice, and the agent runs at its default.

### Permission to launch automatically

`auto_launch` is a permission, not an assignment. Each value only takes effect for work the role actually holds:

- `task_run` applies to a non-PM role that has an `agent`.
- `task_review` applies only to the role named by `reviews.task_role`.
- `planning_review` applies only to the role named by `reviews.planning_role`.

Listing a value the role does not hold is refused during validation. The list is empty unless you set it, and a role with an agent but no permission still works through handoffs you start yourself.

## Reviews

Review policy decides whether a checkpoint happens. Role assignment decides who does it. Neither one hands out access, and neither one allows automatic launch.

| Field | Accepted values | What it does |
| --- | --- | --- |
| `reviews.planning` | `required`, `off` | Turns the planning checkpoint on. Defaults to off. |
| `reviews.planning_role` | The name of any declared role | Names who reviews planning. Required once planning review is on. |
| `reviews.task_closure` | `required`, `off` | Turns the task-closure checkpoint on. Defaults to off. |
| `reviews.task_role` | The name of any declared role | Names who reviews finished tasks. Required once task-closure review is on. |

```toml
[reviews]
planning = "required"
planning_role = "reviewer"
task_closure = "required"
task_role = "reviewer"

[roles.reviewer]
description = "Reviews against acceptance evidence."
grants = ["reviewer-like"]
auto_launch = ["task_review", "planning_review"]

agent = "cartopian-gemini"
timeout = "30m"
```

Every review also compares the work against your own words. Cartopian resolves exact operator quotations from decisions, supported host chat records, and optional request records, then puts them in the review prompt separately from anything the PM wrote. `cartopian review-context` is the read-only view that prompt generation, dispatch, and manual handoff all use. There is no setting for this, and no role can weaken it. Work that predates the capture rules stays explicitly non-blocking.

## Automation

Starting a run, pacing it, allowing a launch, picking the next task, and requiring review are five separate decisions. These three settings own the first two.

| Field | Accepted values | What it does |
| --- | --- | --- |
| `automation.initiation` | `operator`, `auto` | Decides whether a run may begin before you say so. Defaults to operator. |
| `automation.confirmation` | `each-handoff`, `until-blocked` | Decides how far a started run may continue. Defaults to each-handoff. |
| `automation.max_handoffs_per_run` | Any whole number above zero | Caps how many handoffs one run may use. Defaults to 1. |

Task order is computed, not configured. Cartopian always picks the first open task in plan order whose dependencies are met. A ready task queue does not authorize a run on its own.

## Git

| Field | Accepted values | What it does |
| --- | --- | --- |
| `defaults.git_versioning` | `true` or `false` | Turns Cartopian's Git handling on for the project. Defaults to false. |
| `git.pm_owns_product_branches` | `true` or `false` | Lets the PM create and manage product branches. Defaults to false. |
| `git.default_branch_pattern` | Any non-empty text, with `{task_id}` and `{slug}` filled in for you | Names task branches. Defaults to task/{task_id}-{slug}. |
| `git.default_merge_strategy` | `merge`, `squash`, `rebase` | Picks how task branches land. Defaults to merge. |

The resolved `git` record is empty while Git versioning is off. These settings govern projects Cartopian manages. They never apply to the Cartopian source repository itself, where staging, commits, and pushes stay human-owned.

## Work roots

A work root is a folder outside the project that the work actually touches, such as a product repository or a design folder. The project file declares the names. The machine-local file supplies the paths, so teammates on different machines share one committed project file.

| Field | Accepted values | Which file |
| --- | --- | --- |
| `work_roots.*` | One absolute path in your platform's own spelling | Machine-local only |

```toml
# <project-root>/cartopian.toml
[project]
id = "my-project"
name = "My Project"
project_schema_version = "v0.10.0"
work_roots = ["product"]
```

```toml
# <project-root>/cartopian.local.toml
[work_roots]
product = "/absolute/path/to/product"
```

A mapping with no matching declaration, a declaration with no mapping, and a relative path are all refused. Generated documents and tool output never copy a machine-local path unless the step genuinely needs the resolved location.

## Creating and changing configuration

`cartopian generate-config <project-root>` writes a new project file and refuses to overwrite an existing one. It takes `--name` and `--id`, and these optional flags:

| Flag | Sets |
| --- | --- |
| `--role NAME="DESCRIPTION"` | A role and its description. Repeatable. |
| `--role-grants ROLE=GRANT[,GRANT...]` | That role's capabilities and presets. An empty value declares an explicitly empty list. |
| `--role-agent ROLE=AGENT` | That role's handoff agent or wrapper. |
| `--role-launch-model ROLE=MODEL` | That role's model. |
| `--role-launch-effort ROLE=EFFORT` | That role's effort level. |
| `--role-launch-timeout ROLE=DURATION` | That role's timeout. |
| `--role-auto-launch ROLE=ACTIVITY[,ACTIVITY...]` | That role's automatic-launch permissions. |
| `--review-planning`, `--review-planning-role` | The planning review policy and its role. |
| `--review-task-closure`, `--review-task-role` | The task-closure review policy and its role. |
| `--automation-initiation`, `--automation-confirmation`, `--automation-max-handoffs` | The three automation settings. |
| `--work-root NAME` | One declared work-root name. Repeatable. |
| `--git-versioning`, `--git-key KEY=VALUE` | The Git switch and any `[git]` entry. |

Flags you leave out are not written. Resolution supplies the defaults at read time.

`cartopian update-config <project-root>` edits an existing file and keeps your comments and formatting intact. It validates the result before writing, and it writes atomically.

| Flag | Does |
| --- | --- |
| `--set KEY=VALUE`, `--unset KEY` | Sets or removes a single setting, using the dotted names from the tables above. |
| `--set-role NAME="DESCRIPTION"` | Adds or updates a role description. |
| `--set-role-grants NAME=GRANT[,GRANT...]` | Replaces a role's capabilities. An empty value declares an explicitly empty list. |
| `--set-role-launch ROLE.FIELD=VALUE` | Sets one of that role's agent, model, effort, or timeout values. |
| `--set-role-auto-launch ROLE=ACTIVITY[,ACTIVITY...]` | Replaces a role's automatic-launch permissions. |
| `--remove-role NAME` | Removes a role. |
| `--remove-role-launch ROLE` | Removes a role's agent and launch options. |
| `--local --set-work-root NAME=ABS_PATH` | Maps a declared work-root name in the machine-local file. |
| `--local --unset-work-root NAME` | Removes a machine-local mapping. |

Every one of these commands is also an MCP tool, generated from the same handlers, so an agent and a terminal produce identical results and identical error messages.

## The resolved record

`resolve-config` returns these facts, in this order:

1. `record_schema_version`, currently `1`
2. `schema_identity`
3. `project_id`
4. `project_name`
5. `project_schema_version`
6. `roles`
7. `capabilities`
8. `reviews`
9. `automation`
10. `work_roots`
11. `work_roots_attribution`
12. `git_versioning`
13. `git`
14. `defaults_attribution`

Each resolved role carries `description`, `effective_grants`, `assigned_work_types`, `launch`, `auto_launch`, and `attribution`, where `attribution` names the file each value came from.

Smaller lifecycle views repeat `record_schema_version`, `schema_identity`, and `project_schema_version`, then keep only the facts their step needs. Dispatch passes the resolved agent, its options, and the launch location to the wrapper. Wrappers never read raw configuration and never reinterpret review, permission, capability, or identity policy.

Project, task, spec, dependency, prompt, and report paths come back as fully resolved absolute paths. Machine-local work-root values are already absolute and keep the exact spelling you wrote. Use either form as given rather than resolving it again.
