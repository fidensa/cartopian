# Project-contained work roots

The project chooses where work content lives. When it configures no work roots,
Cartopian uses `resources/`. When it configures names, their machine-local
mappings replace that default. Content may be source, inputs, environments,
builds, scripts or evidence; it is not restricted to experiments.

Choosing a folder gives an agent no permission. An activated role must explicitly
hold `read:work-roots` to read inputs, and both `read:work-roots` and
`write:worktree` to change content using sandboxed commands and child processes.
`coder-like` includes both grants. An explicitly empty list grants neither.
Automatic launch permissions and role names do not grant filesystem access.

## Configuration

For the default, omit `project.work_roots` (or use an empty list). The effective
name is `resources`, and its path is `<project-root>/resources`. Scaffolding
creates the folder; launch does not create a missing root or silently select
another one.

For a narrower subtree or a supporting sibling, use existing named roots:

```toml
# Add these values to the existing project and role tables in cartopian.toml.
[project]
work_roots = ["support"]

[roles.worker]
description = "Builds project content"
grants = ["read:prompts", "read:work-roots", "write:worktree", "write:reports"]
agent = "cartopian-claude"
auto_launch = ["task_run"]
```

```toml
# cartopian.local.toml; never commit machine-local paths.
[work_roots]
support = "/absolute/project/resources/spikes/packaging-comparison"
```

Create the selected directory first. Use a direct, filesystem-resolved absolute
path. The project root, governance directories, hidden top-level stores,
overlapping contained roots, symlink mappings and traversal spellings refuse.
Every configured name needs a mapping. No implicit `resources` root is added
when names are configured. Tasks and `root:relative/path` deliverables can name
`resources` when the default is active. Project-mode document publication and
completion-report publication remain mediated.

Use the existing `generate-config` / `update-config` work-root and role-grant
options rather than editing configuration from an assignee. `resolve-config`
reports effective paths and their attribution. Rehearsal and dispatch use the
same contract as wrappers. No new authored field or schema/layout migration is
introduced. This enforcement change can stop an older launch that implicitly
relied on unrestricted project writes: declare the necessary grants and use a
supported adapter, or explicitly configure an external work root. Never infer
grants during an upgrade.

## Enforcement in plain language

`cli/work_access.py` decides the allowed content operations once. All shipped
POSIX and PowerShell wrappers check that decision before any agent or version
probe. Unsupported combinations refuse, even when a bypass option is set.
Missing enforcement helpers refuse instead of continuing.
Project-file handoffs require trusted dispatch bindings even with external roots;
use `cartopian dispatch`. Generic non-project wrapper invocations remain available.

A contained Claude writer starts in its first authorized work root. The separate
`CARTOPIAN_PROJECT_ROOT` value keeps configuration, hooks and reports bound to
the governing project. The wrapper checks the exported root list and cwd against
configuration, then builds its isolated settings. Arbitrary path overrides
cannot enlarge the launch.

Claude's sandbox denies writes by default outside the authorized roots. A blanket
deny on the project would also deny nested allowed roots, so contained writers
use that default boundary instead. Git metadata, other projects, Cartopian
runtime/configuration, host settings and executables retain explicit protection.
The denied reserved path `.cartopian-work-root-boundary` pins the root and its
ancestors against rename, removal and replacement. It must be absent before launch; applications
must not use that name. Preflight refuses any existing file, directory or link
at that path, so workspace content cannot redirect the ancestor protection.

The Bash hook checks the session cwd, configured grants and captured directory
identities before each command. Keep the session cwd at its launch root; use
absolute paths or child-process cwd changes for builds elsewhere. Structured
mutation tools refuse work-root writes because checking a path before a later
open cannot securely bind that open. Sandboxed Bash enforces the decision at the
actual filesystem operation, including child processes. Normal settings, plugins
and external MCP tools cannot widen this launch, and unsandboxed retry is disabled.
Contained roots refuse `allow_unix_sockets=true`, which could reach host daemons.

Requirements, standards, plans, phases, tasks, specs, decisions, prompts, reports,
`STATE.md`, configuration, request/provenance stores and Git metadata remain
protected. Content access grants no governance or lifecycle authority. A task's
scope can be narrower than its role's filesystem grants; prose does not create
a stronger sandbox boundary.

## Supported adapters and evidence

| Adapter | Native macOS contained roots | Linux / WSL / native Windows contained roots |
| --- | --- | --- |
| Claude | Supported with Claude Code 2.1.295+ | Refused |
| Codex | Refused | Refused |
| Antigravity | Refused | Refused |
| Devin | Refused | Refused |
| OpenCode | Refused | Refused |
| Hermes | Refused | Refused |

PowerShell and `.cmd` entry points use the same refusal contract. PowerShell is
not installed on the validation host, so Windows coverage is source ordering,
shared-contract tests and optional runnable PowerShell tests, not native Windows
filesystem evidence. External work-root regression tests preserve existing
adapter behavior and containment tiers; this feature does not certify those
tiers as equivalent. Administrator-controlled extensions remain trusted policy
outside Cartopian's control. Shell reads of other host files remain the existing
disclosed residual; a role with no work-root read grant receives `denyRead` for
its contained roots.

The native probe uses an authenticated Claude CLI on macOS and disposable fixtures:

```bash
python3 scripts/probe_project_work_access.py --direct-cli --output /tmp/work-access-writer
python3 scripts/probe_project_work_access.py --direct-cli --default-root --output /tmp/work-access-default
python3 scripts/probe_project_work_access.py --direct-cli --access reader --output /tmp/work-access-reader
python3 scripts/probe_project_work_access.py --direct-cli --access none --output /tmp/work-access-none
```

`--direct-cli` submits the harness instruction directly to the real CLI using
the shipped wrapper's validation and settings builder. It exercises actual
sandboxed Bash and child-process filesystem operations; it is not a generated
policy assertion. Omit that flag to test the complete wrapper/file-handoff path.
The probe records its launch mode, version, results and transcript separately.
Writer probes execute a source snapshot in the approved subtree and verify the
snapshot bytes after launch; they do not require reading the product repository.
It requires normal authenticated host access; an outer sandbox can prevent the
nested sandbox from starting. Never interpret a skipped probe as containment
evidence.

Recorded native evidence is in
[`spikes/project-work-access-evidence`](../spikes/project-work-access-evidence/README.md).
Shared conformance tests are in `tests/test_project_work_access.py`; the existing
Claude wrapper and preflight suites cover adapter-specific regressions.
