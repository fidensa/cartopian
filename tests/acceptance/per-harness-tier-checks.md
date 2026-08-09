# Operator acceptance — per-harness tier checks

`cartopian containment-matrix <project-path>` reports separate read and write
boundaries from installed/runtime evidence. Run on native Windows and macOS.

## Activated healthy chain

Create a valid registered project in which at least one role declares grants.
Do not create project Claude settings. Run the matrix from the installed CLI.

For `claude-code`, confirm:

- `process_scoped_evidence.hook_present`, `settings_helper_present`,
  `wrapper_chain_valid`, and `process_scoped` are true;
- `legacy_project_registration` is `absent`;
- both structured boundaries have interception evidence without a project
  registration;
- both boundary tiers and the row tier are `contained-partial`, because
  `shell_interception` is false;
- the read boundary reports `unauthorized_read_detection:false`.

Every host without a verified native adapter remains `advisory+detection`,
regardless of its static ceiling.

For `opencode` (tier `advisory+detection`), the clearance probe is the shell
bypass: configure an `edit` `deny` rule for a path, run a handoff that first
attempts a structured write there (must be refused with a rule citation), then
have the agent write the same path via a shell redirect (`printf ... > file`).
The shell write succeeding is the expected residual — `edit` policy does not
cover shell writes — and confirms the advisory ceiling is the honest entry.
The macOS run alone clears this entry; opencode on **Windows is unverified**
until the deferred native-Windows pass runs.

## Honest degradation

Use disposable **copy-mode** install roots for these destructive probes; do
not alter the operator's real install. Run the copied CLI against the same
activated project after independently making each chain incomplete:

1. remove the copied `cli/claude_hook.py`;
2. restore and remove the copied `cli/claude_launch_settings.py`;
3. restore and replace the copied platform Claude wrapper with an incomplete
   stub (on Windows, also verify a missing/incomplete `.cmd` → `.ps1` chain).

Each case must downgrade Claude to `advisory+detection` and expose the failed
evidence field. Documentation or configuration assertions alone never keep a
tier elevated.

## Compatibility registration

Add an older project `PreToolUse` entry that targets the **current**
interpreter and installed hook with the full matcher. The matrix reports it as
`compatible`, and a dispatched launch reuses the entry so Claude de-duplicates
it. Change its interpreter or hook path to a stale value: the matrix reports
`incompatible`, downgrades, and dispatch refuses before Claude starts. Run
`scripts/install.py --claude-hook <project-dir>` only as the explicit cleanup;
confirm it removes Cartopian handlers and preserves unrelated settings/hooks.

## Ungated project

Remove every role `grants` key and rerun. `activated` is false and all rows are
`advisory+detection`, even with a healthy installed chain, because the wrapper
correctly emits no capability entry.

## Interpretation

PreToolUse refusal is point-of-use enforcement for Claude's structured tools.
Governed-write provenance is after-the-fact detection for writes that bypass
that point. `Bash` is not intercepted, and unauthorized shell reads generally
leave no reliable detection evidence. Completion Stop enforcement and the
`exited-without-report` completion classification do not contribute to this
matrix.
