# CLI Handoff Automation Upgrade Plan

## Purpose

Add first-class CLI handoff automation to Cartopian while preserving the
filesystem-first protocol. The PM can assign work to configured agents by
creating protocol prompts, invoking a named executable with the absolute
prompt path, reading the protocol-defined completion report, then applying
normal task or review lifecycle changes.

Automation must remain optional. Manual handoff remains valid for every role.

## Design Decisions

- Roles describe assignee kind, not tool names.
- Handoff config maps a role to a named executable and start policy.
- No `[agents.*]` configuration is added.
- The executable name is the convention. `agent = "codex"` means invoke
  `codex`.
- The CLI contract is always:

  ```text
  <agent> <absolute prompt path>
  ```

- The prompt path must be passed as one argument. Programmatic launchers should
  use argv-style execution, not shell string interpolation. Human-facing
  command text must shell-quote the absolute prompt path when needed.
- If a tool does not natively support that contract, the operator provides a
  wrapper executable with the configured name.
- Prompt paths, report paths, resource paths, task paths, review paths, and
  directory paths are protocol-derived. They are not repeated in config.
- Handoffs are launched sequentially. The PM must finish the current handoff,
  parse its report, and apply any PM-owned lifecycle updates before starting
  the next handoff.
- Automated agents do not own Cartopian lifecycle authority. The PM remains
  responsible for task movement, prompt cleanup, review assignment,
  completion handling, and `STATE.md`.

## Configuration Shape

Use `[roles]` for role kind:

```toml
[roles]
pm = "agent"
operator = "human"
coder = "agent"
reviewer = "agent"
designer = "human"
```

Supported role values:

- `human` - manually assigned through the operator.
- `agent` - may be assigned through CLI handoff when configured.
- `none` - role is not used.
- `""` - unset; the PM should ask the operator for the role assignment.
- Custom values - allowed for local policy, but treated as manual unless a
  project convention defines otherwise.

Use `[handoffs.<role>]` only for agent roles that need a named target:

```toml
[handoffs.coder]
agent = "codex"
auto_start = true
timeout = "60m"

[handoffs.reviewer]
agent = "gemini"
auto_start = false
timeout = "30m"
```

Fields:

- `agent`: executable name. The PM invokes this exact command name with the
  absolute prompt path as the only required argument.
- `auto_start`: when `true`, the PM may launch the configured executable after
  assignment is authorized by the current run policy. When `false`, the PM
  creates the prompt and tells the operator the exact command to run.
- `timeout`: optional maximum wall-clock duration for an automatically launched
  handoff. Use a duration string such as `30m`, `2h`, or `1h30m`. When omitted,
  the protocol default is `60m` unless a project convention defines a stricter
  default. Timeouts apply only to PM-launched handoffs; manual handoffs remain
  operator-managed.

## Run Policy And Confirmations

There are two separate confirmation layers.

First, Cartopian-level confirmation controls whether the PM starts handoffs.
Default behavior is conservative: process one handoff, handle its result, then
return control to the operator before starting the next lifecycle step.

Second, tool-level confirmation controls whether the child CLI asks before
editing files, running shell commands, or continuing. Cartopian cannot make an
arbitrary CLI non-interactive. To run without tool prompts, the configured
agent executable must be a native non-interactive command or a wrapper that
sets the tool-specific flags, environment, sandbox, and approval policy.

The wrapper still follows the Cartopian contract:

```text
codex '/absolute/path/to/projects/example/prompts/PROMPT-01-001.md'
```

The wrapper is responsible for doing whatever is needed to run the underlying
tool without blocking on confirmations. Cartopian does not store those
tool-specific flags in `cartopian.toml`.

Add an optional project/workspace run policy for PM-level autonomy:

```toml
[automation]
confirmation = "each-handoff"
max_handoffs_per_run = 1
```

Supported `confirmation` values:

- `each-handoff`: stop after each handoff result is processed.
- `until-blocked`: continue launching eligible `auto_start = true` handoffs
  until a blocker, failed report, review rejection, missing evidence,
  operator-required decision, phase boundary, or `max_handoffs_per_run` limit.

Both modes are sequential. `until-blocked` extends how many eligible handoffs
the PM may run in one PM session; it does not permit concurrent child agents.
Concurrent handoff execution is out of scope for this upgrade. If it is ever
added, it must require explicit dependency checks and non-overlapping ownership
of target files or directories.

Default values:

```toml
[automation]
confirmation = "each-handoff"
max_handoffs_per_run = 1
```

This allows unattended execution when intentionally enabled, while keeping
cost and quality boundaries explicit.

If an automatically launched handoff exceeds its configured timeout, the PM
must stop waiting for a successful result, record the timeout as a blocker, and
return control to the operator. The PM must not move the task or review forward
based on a late or missing report.

## Absolute Path Requirements

When the PM writes an assignment or review prompt, it must include complete
absolute paths for every resource the assignee is expected to use or produce.

Prompts must not rely on relative path interpretation, current working
directory assumptions, or vague instructions such as "read the PM system."

## Completion Report Contract

Add a protocol-defined reports directory:

```text
reports/
```

Task completion reports:

```text
reports/REPORT-NN-NNN.md
```

Planning-checkpoint reports:

```text
reports/REPORT-PLAN-NNN-slug.md
```

Report files are handoff result artifacts. They are read by the PM and may be
summarized into task, review, decision, or state files as appropriate.
Reports must not include secrets or unnecessary environment-specific sensitive
data. Assignees and wrappers should redact API keys, credentials, tokens,
private connection strings, and comparable values before writing reports.

Task reports must include:

- `Status: complete | blocked | failed`
- Task ID.
- Prompt path.
- Task path.
- Target repo path.
- Files changed.
- Test evidence.
- Commit SHA or PR URL, when applicable.
- Remaining risks.
- Ready for review: `yes | no`.

Review reports must include:

- `Status: complete | blocked | failed`
- Review ID.
- Prompt path.
- Review file path.
- Evidence reviewed.
- Verdict: `approve | request-changes | reject`.
- Blocking findings, when applicable.

Assignees must not move Cartopian task files, delete prompts, rewrite
`STATE.md`, or perform PM lifecycle cleanup.

Before issuing any new or retry handoff, including manual command instructions,
the PM must delete any existing report at the expected protocol-derived report
path. This prevents stale reports from being mistaken for the current handoff
result when a task returns from review or is retried after a blocker.

Report parsing outcomes are:

- `accepted`: the report exists, is well-formed, includes all required fields,
  and its status or verdict can be acted on.
- `blocked`: the report is well-formed and explicitly reports `blocked`, or the
  PM cannot proceed without operator judgment.
- `failed`: the report is well-formed and explicitly reports `failed`.
- `failed-to-parse`: the report is missing, malformed, incomplete, internally
  inconsistent, uses an unsupported status or verdict, or contradicts the
  expected task/review/prompt paths.

`failed-to-parse` is a PM-level blocker. The PM must stop automation, preserve
the prompt and any invalid report for inspection, avoid lifecycle movement, and
surface the parse failure to the operator.

## Protocol Updates

Update `protocol/CONVENTIONS.md` to define:

- Role kind values and their meaning.
- `[handoffs.<role>]` config.
- Optional `[automation]` run policy.
- CLI invocation convention: `<agent> <absolute prompt path>`.
- Argument-passing requirements: absolute prompt path as one argv argument, with
  shell quoting for rendered human commands.
- Wrapper responsibility for tool-specific non-interactive execution.
- Optional handoff timeout behavior.
- Sequential launch behavior and the fact that `until-blocked` is not
  concurrent execution.
- Absolute path requirements for PM-authored prompts.
- `reports/` directory and report naming.
- Completion report contents.
- Secret redaction expectations for reports.
- Stale report deletion before handoff invocation.
- Missing, malformed, incomplete, inconsistent, or late report handling,
  including the `failed-to-parse` PM blocker.
- PM lifecycle authority after handoff completion.
- Hard stop behavior: UI interruption is not graceful pause. Graceful pause is
  represented by PM run policy, report boundaries, and `STATE.md`.

## Template Updates

Update `templates/PROMPT.md` to include:

- Absolute prompt path.
- Absolute project root.
- Absolute target repo path.
- Absolute task/spec/review/report paths.
- Explicit completion report instructions.
- Explicit instruction to redact secrets and sensitive environment values from
  reports.
- Boundary rules telling assignees not to move task files, delete prompts, or
  perform PM lifecycle work.

Update or add a report template:

```text
templates/REPORT.md
```

The report template should support both task completion and review completion,
make required fields unambiguous, and include a redaction reminder.

## Skill Updates

Update `skills/init-workspace.md`:

- Ask for role kind values, not tool names.
- Ask which `agent` roles should have CLI handoff targets.
- Ask whether each CLI handoff target needs a timeout override.
- Ask for default automation confirmation policy.
- Generate optional `[handoffs.<role>]` and `[automation]`.
- Do not generate `[agents.*]`.

Update `skills/init-project.md`:

- Scaffold `reports/`.
- Support project-level `[roles]`, `[handoffs.*]`, and `[automation]`
  overrides.
- Support project-level handoff timeout overrides.
- Explain that omitted handoff config inherits workspace behavior.
- Keep manual handoff as the default.

Update `skills/plan-project.md`:

- Resolve effective role kind, handoff target, and automation policy.
- Generate tasks with assignees based on role kind and handoff target.
- Generate prompts with absolute paths only.
- For `auto_start = false`, tell the operator the exact command to run.
- For `auto_start = true`, launch the configured executable only when allowed
  by the current run policy.
- Pass the prompt path as a single argument and shell-quote it in operator-facing
  command text.
- Delete any existing expected report file immediately before launching or
  instructing a retry of the handoff.
- Launch handoffs sequentially, even when `confirmation = "until-blocked"`.
- Enforce configured handoff timeouts for PM-launched processes.
- Read completion reports and apply PM lifecycle changes.
- Stop when a report is blocked, failed, missing, malformed, incomplete,
  inconsistent, late, ambiguous, or requires operator judgment.

Update `skills/close-plan.md`:

- Inspect `reports/`.
- Treat unresolved prompts or missing/ambiguous reports as active handoff
  state.
- Stop closeout if any handoff result is missing, malformed, incomplete,
  ambiguous, failed to parse, or otherwise unresolved.
- Reset or archive reports according to protocol. Reports should not become a
  replacement for task, review, or decision records.

Update `skills/README.md`:

- Document which skills understand CLI handoff automation.

## README Updates

Update `README.md` with a short "Automated CLI handoffs" section covering:

- Automation is optional.
- Manual handoff remains the default.
- Role values are `human`, `agent`, and `none`.
- Handoff targets are configured under `[handoffs.<role>]`.
- The executable convention is `<agent> <absolute prompt path>`.
- Prompt paths are passed as one argument and should be shell-quoted in manual
  command examples.
- Tool-specific non-interactive behavior belongs in the executable or wrapper,
  not in Cartopian config.
- Optional handoff timeouts can be set per role.
- PM-authored prompts always use absolute paths.
- Completion reports live at protocol-defined paths under `reports/`.
- Completion reports must redact secrets and sensitive environment values.
- `confirmation = "each-handoff"` is the safe default.
- `confirmation = "until-blocked"` is available for bounded unattended runs,
  but still launches handoffs sequentially.

## Existing Project Migration

Update `projects/sample-project/`:

- Add `reports/`.
- Convert role values to role kinds.
- Add sample `[handoffs.*]` entries.
- Add sample `[automation]` with conservative defaults.
- Include a sample timeout where useful.
- Keep this as the canonical example config.

Update `projects/cartopian-web/`:

- Add `reports/`.
- Convert `pm`, `coder`, `reviewer`, and `designer` role values to role kinds.
- Preserve tool choices under `[handoffs.*]`.
- Choose conservative automation defaults unless the operator explicitly wants
  unattended execution for this project.
- Add timeout values only where the operator has an existing expectation;
  otherwise rely on protocol defaults.
- Decide whether the existing prompt
  `prompts/PROMPT-TASK-01-002-codex.md` should be renamed to protocol format or
  marked as legacy.

Update `projects/fidensa/`:

- Add `reports/`.
- Add explicit role kinds only where known.
- Avoid enabling `auto_start = true` unless intentionally configured.
- Avoid project-specific timeout overrides unless intentionally configured.
- Preserve project-specific conventions and repo policy.

## Validation Checklist

- No `[agents.*]` section appears in docs, templates, skills, or sample config.
- Every automated handoff can be represented as:

  ```text
  <agent> <absolute prompt path>
  ```

- PM-authored prompts contain absolute paths for all files, directories, and
  resources.
- Report paths are protocol-derived.
- Existing reports are cleared before retrying or launching the same handoff.
- Report validation covers missing, malformed, incomplete, inconsistent, and
  late reports.
- Reports include explicit secret-redaction guidance.
- Existing manual workflow remains valid.
- Automated assignees cannot perform PM lifecycle movement.
- Automated handoffs run sequentially unless a future protocol revision adds
  explicit dependency and ownership checks.
- Unattended execution is possible only through explicit automation policy and
  non-interactive agent executables or wrappers.
- PM-launched handoffs have bounded timeout behavior.
- The README, protocol, skills, templates, and sample project config describe
  the same behavior.
