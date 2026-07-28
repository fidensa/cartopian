# Cartopian configuration mapping

This is the flattened human reference for the preferred configuration contract in `cli/config_schema.py::CONFIG_SCHEMA`. It describes that executable authority; it does not define another schema. Migration behavior is owned by `cli/config_migration.py`.

## Files, scopes, and precedence

| Authored scope | File | Ownership |
| --- | --- | --- |
| Global | `~/.cartopian/cartopian.toml` | Operator-wide defaults. The installer seeds a commented-only template. |
| Project | `<project-root>/cartopian.toml` | Portable project identity and settings. |
| Machine-local | `<project-root>/cartopian.local.toml` | Absolute path mappings for declared work-root names. Gitignored. |

For shared fields, effective values resolve from project to global to protocol default. Role fields merge per role and then per field; list-valued `grants` and `auto_launch` replace rather than append. `[reviews]`, `[automation]`, `[defaults]`, and `[git]` resolve field by field. `[project]` is project-owned, while `[work_roots]` absolute paths are machine-local.

Use `cartopian resolve-config <project-root>` (or the `resolve_config` MCP tool) for the canonical record. It validates all three scopes, includes source attribution, and fails closed on unknown fields, invalid closed values, orphan references, or missing work-root mappings.

## Project identity

`[project]` is required in project configuration.

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `project.id` | kebab-case string | yes | Stable project/registry identifier. |
| `project.name` | non-empty string | yes | Human-readable project name. |
| `project.project_schema_version` | `vX.Y.Z` string | yes | Sole project-format migration gate. `generate-config` stamps the shipped target. |
| `project.work_roots` | unique name list | no | Portable logical names whose absolute mappings live in machine-local configuration. |

```toml
[project]
id = "my-project"
name = "My Project"
project_schema_version = "v0.9.0"
work_roots = ["product", "design"]
```

Project schema identity is distinct from the Cartopian release version, installed-content revision, connected server identity, and MCP wire protocol version. None substitutes for another.

## Roles, capabilities, launch, and assignment

Each role is exactly one flat table. Its name and description carry no review, launch, selection, or capability authority.

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `roles.<role>.description` | non-empty string | required for user-defined roles | Human description used for assignment context. |
| `roles.<role>.grants` | closed grant/preset list | capability defaults | Harness-enforced capability input. See `CAPABILITIES.md`. |
| `roles.<role>.target` | non-empty string | unset | Neutral wrapper/bridge target. An unset target means manual handoff. |
| `roles.<role>.model` | non-empty string | unset | Agent-neutral model option passed by dispatch. |
| `roles.<role>.effort` | non-empty string | unset | Agent-neutral effort option passed by dispatch. |
| `roles.<role>.timeout` | positive `s`, `m`, or `h` duration | `"60m"` resolved fallback | Handoff deadline passed to the wrapper. |
| `roles.<role>.auto_launch` | `task_run`, `task_review`, `planning_review` | empty | Automatic-launch permission for assigned work types only. |

The `auto_launch` values are `task_run`, `task_review`, and `planning_review`. They are permissions, not assignments: `task_review` is applicable only to the role assigned by `reviews.task_role`, while `planning_review` is applicable only to `reviews.planning_role`. A non-PM role with a launch target is applicable to ordinary task execution. The PM role is interactive and may not have a launch target or automatic-launch permission.

```toml
[roles.coder]
description = "Implements tasks per spec."
grants = ["coder-like"]
auto_launch = ["task_run"]

target = "cartopian-codex"
model = "gpt-5-codex"
effort = "high"
timeout = "60m"
```

## Review policy and assignment

Review policy decides whether a checkpoint exists. Role assignment decides who performs it. Neither confers automatic-launch permission or capabilities.

| Field | Allowed values | Default |
| --- | --- | --- |
| `reviews.planning` | `required`, `off` | `off` |
| `reviews.planning_role` | declared role name | required when planning review is required |
| `reviews.task_closure` | `required`, `off` | `off` |
| `reviews.task_role` | declared role name | required when task-closure review is required |

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

target = "cartopian-gemini"
timeout = "30m"
```

Every planning and task-closure review also consumes the independent request
trace captured by the host before PM authoring. It is not configurable and no
role can weaken it. The CLI/MCP `review-context` reader projects the verbatim
request separately from PM-derived guidance; the configured review role makes
the comparison.

## Run automation

Run initiation, confirmation pace, launch permission, task selection, and review policy are separate authorities.

| Field | Allowed values | Default | Responsibility |
| --- | --- | --- | --- |
| `automation.initiation` | `operator`, `auto` | `operator` | Whether a run may begin without an operator execution directive. |
| `automation.confirmation` | `each-handoff`, `until-blocked` | `each-handoff` | How far an initiated run may chain. |
| `automation.max_handoffs_per_run` | positive integer | `1` | Launch budget for one initiated run. |

Deterministic task order selects which ready task is next; it does not authorize execution. Role-local `auto_launch` decides whether an assigned work type may use automatic dispatch; it does not initiate a run or change its pace.

## Git behavior

| Field | Type/domain | Default |
| --- | --- | --- |
| `defaults.git_versioning` | boolean | `false` |
| `git.pm_owns_product_branches` | boolean | `false` |
| `git.default_branch_pattern` | non-empty string | `"task/{task_id}-{slug}"` |
| `git.default_merge_strategy` | `merge`, `squash`, `rebase` | `merge` |

The resolved `git` record is `null` when Git versioning is off. Human-owned staging, commit, push, and review boundaries for the Cartopian source repository are unaffected by project Git settings.

## Machine-local work roots

Project configuration declares names:

| Field | Type | Ownership |
| --- | --- | --- |
| `work_roots.*` | platform-native absolute path | machine-local only |

```toml
[project]
id = "my-project"
name = "My Project"
project_schema_version = "v0.9.0"
work_roots = ["product"]
```

Machine-local configuration maps exactly those names to platform-native absolute paths:

```toml
[work_roots]
product = "/absolute/path/to/product"
```

An undeclared mapping, missing mapping, or relative path is invalid. Generated documentation and client output must not copy a machine-local path unless the bounded runtime projection specifically requires the resolved launch/work location.

## Creation and mediated editing

`cartopian generate-config` creates preferred project configuration and refuses to overwrite an existing file. Its role-local flags are:

- `--role NAME=DESCRIPTION`
- `--role-grants ROLE=GRANT[,GRANT...]`
- `--role-launch-target ROLE=TARGET`
- `--role-launch-model ROLE=MODEL`
- `--role-launch-effort ROLE=EFFORT`
- `--role-launch-timeout ROLE=DURATION`
- `--role-auto-launch ROLE=ACTIVITY[,ACTIVITY...]`

Review, automation, Git, and work-root flags map directly to the fields above. Omitted optional flags do not write defaults; resolution applies them.

`cartopian update-config` is the comment-preserving mediated editor for existing project and machine-local files. Use `--set`/`--unset` for closed scalar fields, `--set-role`, `--set-role-grants`, `--set-role-launch`, `--set-role-auto-launch`, `--remove-role`, and `--remove-role-launch` for role facts, and the `--local` work-root operations for machine-local mappings. It validates the resulting effective record before an atomic write.

The CLI parser is the MCP tool-schema source. MCP `inputSchema` types, enums, defaults, requiredness, descriptions, and diagnostics are generated from or parity-tested against the same handlers.

## Canonical resolved record

`resolve-config` emits these ordered top-level facts:

1. `record_schema_version` (`1`)
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

Each resolved role contains `description`, `effective_grants`, `assigned_work_types`, `launch`, `auto_launch`, and `attribution`. Bounded lifecycle projections carry the same `record_schema_version`, `schema_identity`, and `project_schema_version`, then retain only the facts required for their action. Dispatch passes resolved target/options and launch context to wrappers; wrappers do not parse raw configuration or reinterpret review, permission, capability, schema, or identity policy.

Path spelling is intentionally split by authority, not by command. Project,
task, spec, dependency, prompt, and report paths are emitted as
filesystem-resolved absolute paths. Machine-local `work_roots.*` values are
already required to be absolute and retain their operator-authored spelling
verbatim in every projection. Consumers use either form as emitted and do not
re-resolve it.

## Legacy compatibility boundary

The schema authority owns two separate legacy vocabulary shapes. Authored
migration-source paths cover `project.protocol_version`,
`[roles.<role>.launch]`, `[handoffs.<role>]`, `auto_start`,
`auto_start_tasks`, `auto_start_reviews`, and `planning_reviews`; retired CLI
flags cover `--set-handoff` and `--remove-handoff`. The authored paths are
readable only by the compatibility/migration layer. Both classes are
deprecated and removed at the approved project-format `v0.7.0` boundary. Current generation, mediated
editing, examples, templates, CLI/MCP schemas, lifecycle projections, and
wrappers never emit or teach those forms.
