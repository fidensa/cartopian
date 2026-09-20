# Operator acceptance — Claude refusal adapter

Live acceptance for the capability-keyed Claude Code PreToolUse hook
(`cli/claude_hook.py`) on a **Cartopian-dispatched handoff**. Run the applicable
cases on native Windows and native macOS/Linux. A real Claude call may be
billable; the automated suite uses fake executables and makes no provider call.

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

Do not run `--claude-hook`: containment is loaded by the wrapper's
process-scoped `--settings` layer and needs no persistent registration. An old
Cartopian entry in normal user/project/local settings does not need cleanup for
an activated acceptance run because those settings sources are excluded.
Use a throwaway `CLAUDE_CONFIG_DIR` when exercising the always-loaded legacy
global `.claude.json` cases below.

## Cases

For each case, place the requested operation in the dispatched prompt, run the
normal `cartopian dispatch ... --role <role>` path, and preserve the wrapper
stderr/session evidence plus before/after file hashes.

Run the activated behavioral cases 1–12c on native macOS/Linux, case 13 on
WSL2, and the native-Windows fail-closed/completion-only cases 14–15 on native
Windows. Activated native Windows is intentionally not a mutation-test host.

1. Without `write:lifecycle`, an `Edit` of the project `STATE.md` is refused
   with a `[guard]` reason naming `lifecycle` and `write:lifecycle`.
2. Without `write:worktree`, a `Write` under a declared work root is refused
   with `work-root:<name>` and `write:worktree`.
3. After granting `write:lifecycle`, the `STATE.md` mutation is allowed while
   the work-root mutation remains refused. Then grant `write:worktree`: the
   path-only structured mutation must refuse with the safe-channel diagnostic,
   while the same work-root mutation through Bash succeeds under the OS
   sandbox.
4. Without `read:reports`, a structured `Read` of a report is refused. Without
   `read:work-roots`, a structured `Grep` of the work root is refused.
5. After granting the matching read capability, the same structured read is
   allowed.
5a. Give a role only `read:governance`. From the project root, both a `Glob`
    with `pattern: "prompts/**"` and a `Grep` filtered by `glob:
    "prompts/**"` must refuse with `read:prompts`; the root path alone must not
    authorize their descendants. Repeat `Glob` with an absolute
    `<project>/{prompts,reports}/**` pattern and a separately supplied safe
    work-root `path`: Claude's absolute-pattern base must override that path
    and still refuse. Confirm an absolute wildcard base authorizes only the
    directory before its first `*`, `?`, `[`, or `{`, and a suffix containing
    parent traversal (for example
    `<project>/prompts/{foo,../reports}/**`) broadens rather than escaping the
    checked surface. Finally, register a second project below the active
    project directory, grant the active role every ordinary project read, and
    confirm a project-root `Glob` and `Grep` both refuse because the recursive
    search could enter that nested foreign project.
6. In an activated dispatched session, a structured target outside the bound
   project and its launch-captured work roots is refused even when it is not
   claimed by any registered project. In a deliberately unbound legacy hook
   invocation, the same target remains untouched.
7. Remove every `grants` key, dispatch again, and confirm no capability entry
   is present and the project behaves ungated.
8. Repeat an activated case with `CARTOPIAN_CLAUDE_BARE=true`, then with
   `CLAUDE_CODE_SIMPLE=1`; each launch must refuse before Claude starts because
   those modes suppress required hooks, including explicit process settings.
9. On native macOS or Linux, verify the process settings report strict sandbox
   mode, then try project/work-root writes through redirects, `tee`, an
   interpreter child, an invoked script, and symlink traversal. Project writes
   always fail; work-root writes succeed only with `write:worktree`. If the
   sandbox cannot initialize, the handoff must refuse launch.
10. Put marker hooks/plugins, an `env` value, and a non-empty
    `sandbox.excludedCommands` array in normal user, project, and project-local
    settings. An activated launch must pass an empty `--setting-sources` value,
    start without executing or importing those markers, and remain unaffected
    after a cwd change into the authorized work root. Repeat with an obsolete
    persistent Cartopian hook entry and confirm no duplicate hook runs and no
    cleanup is required.
11. Put unsafe loader, hook-suppression, config-redirection, or
    `CLAUDE_CODE_PROCESS_WRAPPER` values in the top-level `env` of the
    throwaway legacy global `.claude.json`. The activated launch must refuse
    before Claude starts and identify that always-loaded file. A safe
    legacy-global environment value may remain effective; normal
    user/project/local values may not. Confirm that non-`env` fields in the
    legacy file are inert on this isolated launch path.
12. Confirm dispatch supplies absolute `CARTOPIAN_CLAUDE_EXECUTABLE` and
    `CARTOPIAN_PYTHON` bindings, the wrapper uses the bound Python for
    pre-containment validation, and both `--version` and the real launch use
    the same bound Claude path. Put a declared writable work-root directory
    earlier in `PATH` and confirm preflight refuses it rather than permitting a
    later executable replacement. Verify the emitted process settings disable
    auto-memory and that the acceptance prompt cannot create or alter an entry
    under `~/.claude/projects`.
    Also confirm the generated hook argv captures each work-root name,
    canonical path, device, and inode. Rename/replace a nested root, and test a
    symlink followed by `..`; both structured writes must refuse rather than
    following the changed or resolved destination.
12a. Register a second project that claims the same work root, then a parent
     of it, in both registry orders. A bound structured read must keep the
     active project's grant decision, and a sandboxed Bash write with
     `write:worktree` must remain writable; an unbound equal structured claim
     must refuse as ambiguous.
12b. Plant a cross-boundary hard link or Linux bind-mount alias in an effective
     shell-writable root and confirm preflight refuses. Repeat with the project
     or Cartopian config below an implicit temp/npm/debug write root, and with
     either-direction overlap between a protected path and a writable work
     root; each launch must refuse before Claude starts.
12c. Set inherited `BASH_ENV`, exported Bash functions, tracing controls,
     loader variables, `PYTHONPATH`, `BUN_OPTIONS`, and `NODE_OPTIONS`. Normal
     dispatch must strip them before the isolated output supervisor and
     wrapper start, and every pre-containment Git/version probe must receive an
     explicit sanitized environment. Set inherited `TMPDIR` to a writable work
     root and confirm dispatch replaces it before the version probe with the
     direct mode-0700 `~/.cartopian/claude-host-tmp`; settings must carry the
     same path. `CLAUDE_CODE_TMPDIR`, `CLAUDE_TMPDIR`, and legacy settings
     `env.TMPDIR` remain refused. Confirm sandboxed Bash receives Claude's
     separate writable per-user temp rather than the protected host directory.
13. On WSL2, confirm an activated launch refuses preflight pending attestation
    of the optional interop-blocking seccomp filter.
14. On native Windows, confirm the same activated dispatch refuses before
    Claude starts with a diagnostic requiring both an attested shell sandbox
    and an exact native Claude executable chain. No capability-hook session
    may run as `contained-partial` or as an accepted residual.
15. Remove every `grants` key but keep the completion report boundary, then
    repeat on native Windows with an exact native Claude executable. Confirm
    the completion-only Stop hook may run. Bind an underlying `.cmd` and then
    `.bat` Claude shim in turn; each hook-bound launch must refuse before the
    version probe because the extra command-processor hop cannot preserve the
    exact settings argv boundary. The shipped `cartopian-claude.cmd` wrapper
    shim itself remains valid; this check concerns the underlying Claude path.

The automated argv/integration coverage in
`tests/wrappers/test_claude_stop_hook_activation.py` is the non-billable
evidence for activation, settings isolation, legacy-global validation,
executable pinning, auto-memory suppression, interpreter/path quoting, install
layouts, bare mode, and activated persistent-hook exclusion. Behavioral
allow/deny details remain covered by `tests/cli/test_claude_refusal_hook.py`.

## Residuals and pass criteria

The adapter intercepts Claude's structured read/mutation tools and deliberately
does not parse `Bash`. On accepted native macOS/Linux hosts the independent OS
sandbox contains shell/child-process writes; unauthorized shell reads generally
cannot be detected reliably. WSL2 activated launches are refused pending
seccomp attestation. Native-Windows activated launches are refused pending
both shell-sandbox and exact-native-executable-chain attestation; they are not
run as a write-partial residual. Native-Windows completion-only hooks require a
direct native Claude executable and refuse underlying `.cmd`/`.bat` shims. A
completion `Stop` hook and `exited-without-report` classification are unrelated
to capability containment.

Pass only when structured cases behave according to resolved grants, the
project needs no persistent hook registration, hook-suppressing modes refuse
launch, the shell-write cases prove their unchanged hashes, and recorded
evidence keeps the shell-read residual explicit and proves the Windows
fail-closed cases above.
