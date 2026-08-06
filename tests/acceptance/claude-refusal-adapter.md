# Operator acceptance — Claude refusal adapter

Live acceptance for the capability-keyed Claude Code PreToolUse hook
(`cli/claude_hook.py`) on a **Cartopian-dispatched handoff**. Run on native
Windows and macOS. A real Claude call may be billable; the automated suite uses
fake executables and makes no provider call.

## Preconditions

- Install the current Cartopian build normally and put its wrapper directory
  on `PATH`.
- Register a throwaway governed project with a valid v0.9 configuration.
- Configure a role whose `agent = "cartopian-claude"` and whose applicable
  work type is in `auto_launch`.
- Prepare a normal task/prompt/request-trace handoff so `cartopian dispatch`
  accepts it.
- Declare at least one role `grants` key. Give the dispatched role
  `read:prompts` and only the grants needed for each case below.

Do **not** create `.claude/settings.json` and do not run `--claude-hook`.
Containment is loaded by the wrapper's process-scoped `--settings` layer.
Normal user, project, and local Claude settings remain available.

## Cases

For each case, place the requested operation in the dispatched prompt, run the
normal `cartopian dispatch ... --role <role>` path, and preserve the wrapper
stderr/session evidence plus before/after file hashes.

1. Without `write:lifecycle`, an `Edit` of the project `STATE.md` is refused
   with a `[guard]` reason naming `lifecycle` and `write:lifecycle`.
2. Without `write:worktree`, a `Write` under a declared work root is refused
   with `work-root:<name>` and `write:worktree`.
3. After granting `write:lifecycle`, the `STATE.md` mutation is allowed while
   the work-root mutation remains refused.
4. Without `read:reports`, a structured `Read` of a report is refused. Without
   `read:work-roots`, a structured `Grep` of the work root is refused.
5. After granting the matching read capability, the same structured read is
   allowed.
6. A structured target outside every registered project/work-root boundary is
   left untouched by the hook.
7. Remove every `grants` key, dispatch again, and confirm no capability entry
   is present and the project behaves ungated.
8. Repeat an activated case with `CARTOPIAN_CLAUDE_BARE=true`; refusal must
   remain active because `--bare` cannot suppress explicit process settings.

The automated argv/integration coverage in
`tests/wrappers/test_claude_stop_hook_activation.py` is the non-billable
evidence for activation, settings preservation, interpreter/path quoting,
install layouts, bare mode, and compatibility de-duplication. Behavioral
allow/deny details remain covered by `tests/cli/test_claude_refusal_hook.py`.

## Residuals and pass criteria

The adapter intercepts Claude's structured read/mutation tools. It does not
intercept `Bash`. Governed writes routed through shell may be detected later by
`plan-audit` provenance; unauthorized shell reads generally cannot be detected
reliably. A completion `Stop` hook and `exited-without-report` classification
are unrelated to capability containment.

Pass only when all structured cases behave according to resolved grants, the
project has no required hook registration, bare mode retains refusal, and the
recorded evidence does not claim shell or unauthorized-read detection.
