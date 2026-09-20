# Operator acceptance — per-harness tier checks

`cartopian containment-matrix <project-path>` reports separate read and write
boundaries from installed/runtime evidence. Run on native Windows and macOS.

## Activated healthy chain

Create a valid registered project in which at least one role declares grants.
Do not create project Claude settings. Run the matrix from the installed CLI.

For `claude-code`, confirm:

- `process_scoped_evidence.hook_present`, `settings_helper_present`, and
  `wrapper_chain_valid` are true;
- `legacy_project_registration` is `absent`;
- on native macOS and Linux, the write boundary remains
  `contained-partial` with `shell_write_policy_configured:true` and
  `shell_interception:false` until a behavioral operator-acceptance run attests
  the host sandbox; `process_scoped` is true and both structured boundaries
  have interception evidence without a project registration; the read boundary
  and row are also `contained-partial` because shell reads are outside the
  capability policy;
- on WSL2, activated launch refuses pending attestation of the optional
  interop-blocking seccomp filter;
- on native Windows, the activated helper probe refuses pending both an
  attested shell sandbox and an exact native Claude executable chain; the row
  is `advisory+detection` with `process_scoped:false` and that refusal detail,
  not `contained-partial`;
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

For `hermes` (tier `advisory+detection`), the clearance probe is the one-shot
approval bypass: run a wrapper handoff (`cartopian-hermes`) that performs a
governed write and confirm no approval prompt of any kind is reachable —
one-shot mode internally sets `HERMES_YOLO_MODE=1` and `HERMES_ACCEPT_HOOKS=1`,
so there is no approval layer to preserve. Then, with `HERMES_WRITE_SAFE_ROOT`
configured, have the agent write outside the safe root via the `terminal` tool
(`printf ... > file`). The terminal write succeeding is the expected residual —
Hermes documents its write guards as *not a sandbox* — and confirms the
advisory ceiling is the honest entry. Unlike opencode, the macOS run does
**not** alone clear Hermes for the "fully supported" claim: Windows acceptance
(wrapper trio + profile-scoped registration under PowerShell) is required
before that claim is made.

## Honest degradation

Use disposable **copy-mode** install roots for these destructive probes; do
not alter the operator's real install. Run the copied CLI against the same
activated project after independently making each chain incomplete:

1. remove the copied `cli/claude_hook.py`;
2. restore and remove the copied `cli/claude_launch_settings.py`;
3. restore and replace the copied platform Claude wrapper with an incomplete
   stub (on Windows, also verify a missing/incomplete shipped
   `cartopian-claude.cmd` → `cartopian-claude.ps1` chain; this is distinct from
   the underlying Claude `.cmd`/`.bat` shims refused by hook-bound launches).

Each case must downgrade Claude to `advisory+detection` and expose the failed
evidence field. Documentation or configuration assertions alone never keep a
tier elevated.

## Compatibility registration

Copy an old Cartopian `PreToolUse` or `Stop` entry into normal project
settings. For an activated launch, confirm the matrix reports
`legacy_project_registration:"excluded"`: the empty settings-source list means
the registration does not execute, does not duplicate the bound hook, and does
not require cleanup. For a completion-only or ungated launch, normal settings
remain loaded; confirm a persistent Stop registration that collides with the
report-bound process hook refuses. If cleanup is desired, run
`scripts/install.py --claude-hook <project-dir>` explicitly and confirm it
removes Cartopian handlers while preserving unrelated settings/hooks.

## Ungated project

Remove every role `grants` key and rerun. `activated` is false and all rows are
`advisory+detection`, even with a healthy installed chain, because the wrapper
correctly emits no capability entry.

## Interpretation

PreToolUse refusal is point-of-use enforcement for Claude's structured tools.
On accepted native macOS/Linux hosts the process-scoped OS sandbox
independently contains shell and child-process writes; it does not parse
command text. Unauthorized shell reads generally leave no reliable detection
evidence. Activated native-Windows launches are refused pending both
shell-sandbox and exact-native-executable-chain attestation rather than run as
a write residual. A native-Windows completion-only hook may run with a direct
native Claude executable, but an underlying `.cmd`/`.bat` shim must refuse.
Completion Stop enforcement and the `exited-without-report` completion
classification do not contribute to this matrix.
