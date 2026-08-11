# Agent CLI Wrappers

## The problem

Cartopian's handoff contract is simple:

```text
<agent> <absolute prompt path>
```

Each CLI has different flags for running non-interactively. When the PM runs `codex '/path/to/PROMPT-01-003.md'`, Codex opens an interactive TUI and waits for keyboard input, because it doesn't know it should run headlessly. Same with `agy`, `claude`, and `devin`.

These wrappers fix that. They accept a prompt path, read the prompt file, and call the real CLI with the right non-interactive flags baked in.

## Quickstart

### Prerequisites

- A supported agent CLI on PATH (`codex`, `claude`, `agy`, `devin`, `opencode`, or `hermes`).
- **macOS only:** GNU coreutils provides the `gtimeout` binary the bash wrappers use to enforce `CARTOPIAN_TIMEOUT` at the OS level:
  ```bash
  brew install coreutils
  ```
  Without coreutils, most bash wrappers warn at launch and run unbounded — handoffs still execute, but a hung assignee can run forever instead of being killed at the configured deadline. `cartopian-agy` is the exception: its aligned `--print-timeout` remains active and an internal expiry is reported through the normal exit-`124` timeout contract. Linux distributions ship `timeout` in coreutils by default; native Windows uses PowerShell's `Start-Process` + `WaitForExit` and needs no extra install.

### Step 1: Put the wrappers on your PATH

If Cartopian was installed via `install-cartopian.md`, the installer already added the platform-appropriate wrapper directory to your user PATH (`$install_root/wrappers/bin` on Unix, `$installRoot\wrappers\ps1` on Windows) alongside `bin/`. Open a new terminal and skip to Step 2.

If you're running the wrappers from a source checkout (no install root yet), add the directory manually:

**macOS / Linux / WSL (bash or zsh):**

```bash
# Temporary (current session only):
export PATH="$PWD/wrappers/bin:$PATH"

# Permanent (add to shell profile):
echo 'export PATH="/path/to/cartopian/wrappers/bin:$PATH"' >> ~/.zshrc
```

**Windows (PowerShell):**

```powershell
# Temporary (current session only):
$env:Path = "$PWD\wrappers\ps1;$env:Path"

# Permanent (user-level):
[Environment]::SetEnvironmentVariable(
    'Path',
    "C:\path\to\cartopian\wrappers\ps1;$([Environment]::GetEnvironmentVariable('Path', 'User'))",
    'User'
)
```

### Step 2: Update the configured handoff agent

Use the mediated editor to change a role's handoff agent from the raw CLI name to the wrapper name:

```bash
cartopian update-config /absolute/project/path \
  --set-role-launch coder.agent=cartopian-codex \
  --set-role-launch reviewer.agent=cartopian-agy
```

That changes only the resolved agent/options. Review policy, role assignment, run automation, automatic-launch permission, capabilities, and identities remain owned by their separate configuration/lifecycle authorities. Dispatch resolves those facts before it invokes a wrapper.

### Step 3 (optional): Tune security settings

Each wrapper has a `# --- Configuration ---` section at the top of the script. You can edit those values directly, or override them at runtime with environment variables:

```bash
# Example: let Codex run fully autonomously (careful!)
export CARTOPIAN_CODEX_BYPASS=true

# Example: restrict Claude to read-only
export CARTOPIAN_CLAUDE_TOOLS=Read
```

Full environment variable reference is in the [Configuration](#configuration) section below.

## Supported CLIs

| CLI | Wrapper | What it runs under the hood |
| --- | --- | --- |
| Codex (OpenAI) | `cartopian-codex` | `codex exec --sandbox workspace-write ...` |
| Claude Code | `cartopian-claude` | `claude -p --dangerously-skip-permissions ...` |
| Antigravity (Google) | `cartopian-agy` | `agy --disable-slash-commands --dangerously-skip-permissions -p ...` (**Windows: unverified**) |
| Devin | `cartopian-devin` | `devin -p --sandbox --permission-mode <autonomous\|dangerous> --prompt-file <abs path>` (mode spelling depends on the installed CLI's detected permission surface — see [Devin](#devin)) |
| opencode | `cartopian-opencode` | `opencode run --auto ...` (no filesystem sandbox exists to configure — see [opencode](#opencode); **Windows: unverified**) |
| Hermes (Nous Research) | `cartopian-hermes` | `hermes -z <prompt-path> ...` (one-shot mode; approvals are self-bypassed by the tool — see [Hermes](#hermes); **Windows: unverified**) |

By default, every wrapper runs its underlying CLI fully autonomously — no permission prompts, no TTY interaction. This is required for the PM→assignee handoff to complete without a human in the loop. If autonomy is not desired, use the operator-performed launch path instead of dispatching the wrapper. Tighten an individual wrapper's defaults via the env vars in [Configuration](#configuration) if you need a more restrictive posture for a specific tool.

Review integrity is enforced before a wrapper starts. `cartopian dispatch`
recomputes the bound request context and refuses stale, missing, or altered
generated review channels. Manual launches use
`handoff-packet`/`review-context --prompt` for the same preflight. Dispatch
also exports `CARTOPIAN_ROLE`; optional host intake is not part of a dispatched
role session. Exact evidence may instead resolve from applicable decisions or
supported host chat records. The CLI enforces the direct-capture boundary:
`capture-request` refuses while `CARTOPIAN_ROLE` or
`CARTOPIAN_MCP_TOOL_CALL` is set. These neutral assignee wrappers never receive
or reconstruct operator-message bytes.

## How a wrapper works

```text
PM runs:  cartopian-codex /abs/path/to/PROMPT-01-003.md
              │
              ├─ validates the file exists
              ├─ checks that 'codex' is installed
              ├─ reads the prompt file content
              ├─ resolves the launch directory (Cartopian project root)
              ├─ derives the optional status-file path (<report-path>.status)
              ├─ wraps the invocation in an OS-level deadline (CARTOPIAN_TIMEOUT)
              ├─ runs timeout 60m codex exec --sandbox workspace-write "<prompt content>"
              ├─ writes the status file capturing the assignee exit outcome
              └─ exits with the assignee's exit code
```

The bash wrappers run `timeout <duration> <real-cli> ...` so the OS owns the deadline; the upstream process receives SIGTERM at the configured wall-clock limit (exit code 124). The PowerShell wrappers achieve the same with `Start-Process` + `WaitForExit($TimeoutMs)`. The PM does not poll or watchdog the running process — it dispatches and waits for the platform's background-completion signal.

The wrappers no longer `exec` into the CLI: they run it as a child, capture its exit code, write the [status file](#status-file-early-crash-detection) below, and then exit with the assignee's exit code (so signals/exit codes still reach the PM faithfully).

### Automated launch-log retention

`cartopian dispatch` places the configured wrapper inside the common
standard-library output supervisor before detaching it. This outer boundary
is agent-neutral: all Codex, Claude, Antigravity, Devin, opencode, and Hermes wrappers use
it on both POSIX and native PowerShell/CMD launch paths. It continuously drains combined
wrapper output so the child cannot block on a full pipe and publishes only a
bounded `<report-path>.launch.log`; excess bytes are discarded without
signaling, terminating, failing, or otherwise constraining the assignee. The
detached supervisor itself uses null stdio, so agent output can neither inherit
the short-lived CLI/MCP caller's pipes nor enter its JSON-RPC stream.

The shipped retained-diagnostic defaults are 64 KiB / 400 lines. Dispatch
exports the normalized values as `CARTOPIAN_LOG_BYTE_LIMIT` and
`CARTOPIAN_LOG_LINE_LIMIT`; invalid operator overrides fail before launch.
Truncation is explicit in the retained representation and status metadata, but
is never a lifecycle outcome. Ordinary nonzero exits, timeout `124`, and valid
report completion remain distinct and unchanged.

The outer supervisor preloads canonical report parsing before the wrapper is
launched and throttles report observation by elapsed time rather than output
chunks. Its pipe-readiness wait advances on the next report poll or grace
deadline even when stdout stays open and silent. Once a wrapper publishes a
complete report, the supervisor atomically publishes the current bounded
representation before any grace or reap work, keeps draining during the shared
post-report grace, and atomically replaces the snapshot with the final bounded
representation afterward.

The supervisor's guarantee is `retained-launch-log`: it bounds storage only,
not execution output, artifacts, reports, model context, or provider-private
context. Direct manual invocation of a wrapper does not pass through this
automated-dispatch retention supervisor.

### Clean exit on report-complete (handoff exit contract)

Some assignee CLIs keep running after they have written the report — MCP stdio servers that are not torn down, an inherited open stdin, or a trailing turn leave the process alive with no work left to do. If the wrapper only waited for that process, a *finished* handoff would sit idle until `timeout` killed it (exit `124`, `reason=timeout`) — a success that always read as a deadline failure.

The shared helper `cartopian_run_supervised` (in `bin/_cartopian-status.sh`) fixes this with a **report-completion supervisor**. It runs the assignee with stdin redirected from `/dev/null` (closing one lingering mode) and watches for the expected report file. The report file is the **authoritative completion signal** (the same one the wait commands parse); the supervisor requires the expected task/review/planning-review terminal structure, not merely path appearance or an early `Status:` line. Once that publication shape is complete, it grants the child a brief grace to exit on its own and then reaps it. A partially written report remains nonterminal and cannot cause the wrapper to kill its writer.

This is **event-driven, not a second timer**. The single `CARTOPIAN_TIMEOUT` deadline (applied via `timeout`, the [SSOT](../protocol/CONVENTIONS.md) enforcer) remains the only clock and is never extended: a genuine hang writes no report, is never reaped early, and still hits the deadline with exit `124` / `reason=timeout`. The grace and poll cadence are tunable via `CARTOPIAN_REPORT_GRACE_POLLS` (default 3) and `CARTOPIAN_REPORT_POLL` (default 2s); the outer launch-log supervisor reuses those values when it observes report completion from any still-running wrapper, including one whose stdout remains open and silent. No per-tool CLI timeout flag is introduced.

The PowerShell wrappers carry the same contract via `Invoke-CartopianSupervisedRun` / `Test-CartopianReportComplete` in `ps1/CartopianStatus.ps1` (BL-006 parity). There is no external `timeout` binary on that path, so the supervisor itself is the single `CARTOPIAN_TIMEOUT` enforcer: the deadline is computed once and never extended, the report watch reuses the same wait loop, stdin is redirected to immediate EOF, and a complete report is authoritative (exit `0`/`reason=clean`) while a genuine hang still exits `124`/`reason=timeout`. Static + behavioral parity is pinned by `tests/wrappers/test_ps1_handoff_exit_contract.py` (behavioral cases run where `pwsh` is available; Windows-host execution evidence is tracked separately).

## Status file (early-crash detection)

Automatic dispatch first removes any prior launch log while establishing a safe destination, then writes a small **status file** with `state=running`, a fresh launch identity, and the expected report variant. When a safe retained-log destination exists, it also writes `guarantee_scope=retained-launch-log` and `retained_log_ready=false`. Wrappers preserve that pending marker in their exit status. The outer supervisor publishes the bounded snapshot first, then atomically changes the marker to `retained_log_ready=true` with retained facts; while the matching status remains `running`, canonical waits expose the report's terminal verdict only after that publication boundary and never open the log body. The marker is the normal proof, while a safe single-link regular log at the deterministic companion path is equivalent publication metadata if the following status replacement is lost or raced. The supervisor later publishes the final clean/error/timeout result as a fallback. If the wrapper has already published `state=exited`, a pending retention marker fails open and cannot strand a complete authoritative report after supervisor loss. Identity and variant are preserved.

**The report file remains the authoritative completion signal.** The retained-publication boundary coordinates diagnostic visibility only for a matching live automated launch; it never changes the report verdict. With no status file (the normal manual/report-only case), a complete report is immediately terminal. With `state=exited`, a complete report is also terminal even if `retained_log_ready=false` remains. A published safe launch-log companion also releases a stale pending marker because the current snapshot necessarily preceded the fallible status update. Wrappers write status best-effort: any failure to write is swallowed and never changes the wrapper's own exit code.

### Path

The status file lives at the expected report path with a `.status` suffix — exactly the path `wait_handoff.py` derives:

```text
<project-root>/reports/REPORT-NN-NNN.md.status            (task assignment)
<project-root>/reports/REPORT-NN-NNN-review.md.status     (task review)
<project-root>/reports/REPORT-PLAN-NNN.md.status   (planning review)
```

When dispatch exports `CARTOPIAN_EXPECTED_REPORT_PATH`, the helpers use that exact bounded slot. Otherwise the id comes from the prompt filename (`PROMPT-NN-NNN.md`) and the project root is the prompt's grandparent directory (`<project-root>/prompts/PROMPT-NN-NNN.md`); a manual task-review launch selects the `-review` slot from `CARTOPIAN_EXPECTED_REPORT_VARIANT=review`. The task-review report slot is independent of the coder's completion report (`REPORT-NN-NNN.md`), which stays preserved throughout review and is never the review launch's watch target. Wrappers compute this from the prompt path *before* changing the launch cwd, so a relative prompt path still resolves.

### Shape

Newline-separated `key=value` lines, UTF-8:

```text
state=running|exited
launch_id=<current launch identity>
expected_variant=task|review|planning-review
exit_code=<int>
reason=clean|error|timeout
```

| Field | Meaning |
| --- | --- |
| `state` | `running` is published by automatic dispatch before child creation; `exited` is published by the wrapper after termination. |
| `launch_id` | Fresh dispatch identity binding the running/exited signal to one launch. Omitted for a manual wrapper launch without dispatch context. |
| `expected_variant` | The only report variant this launch may publish. Task-scoped waits also derive it from task lifecycle status. |
| `exit_code` | The assignee's exit code. A **non-zero** code is the crash signal (`wait-handoff` reports `failed`). A `0` (clean) exit is not a crash, but it is still terminal — see the outcome table below. |
| `reason` | Human/diagnostic distinction only — **ignored by the consumer**, which keys off `state`/`exit_code` alone. One of `clean` (exit 0), `error` (any other non-zero exit), or `timeout` (the OS deadline killed the assignee). |

### Outcome → fields

The report file is always the authoritative signal. A valid report is immediately `done` for manual/report-only observation and after wrapper exit. During a matching automated launch that is still `running`, `retained_log_ready=false` briefly delays visibility until the bounded retained snapshot is published; either the normal ready marker or the safe published companion proves that boundary, and neither changes the report verdict.

| Outcome | `state` | `exit_code` | `reason` | wait-handoff verdict (no valid report present) |
| --- | --- | --- | --- | --- |
| Clean exit, report written | `exited` | `0` | `clean` | `done` (report wins) |
| Clean exit, no report | `exited` | `0` | `clean` | `failed` — assignee exited without writing a report |
| Non-zero exit | `exited` | `<n≠0>` | `error` | `failed` |
| Timeout kill | `exited` | `124` | `timeout` | `failed` |

A clean exit with no report is terminal (`classification=exited-without-report`) because `state=exited` means the process is gone. A malformed report is not failed immediately while `state=running`; after exit it deterministically becomes `failed-to-parse`.

A timeout kill is recorded as `state=exited` with `exit_code=124` (the value coreutils `timeout`, and agy's internal no-coreutils fallback, returns at the deadline — see [§ Handoffs](../protocol/CONVENTIONS.md) and `CARTOPIAN_TIMEOUT`). It is surfaced to the consumer as a non-zero exit (a crash); the extra `reason=timeout` line distinguishes it from a plain non-zero exit for humans and custom tooling without changing the consumer-visible contract.

### Consumer / producer agreement

The producer (the shared helpers `bin/_cartopian-status.sh` and `ps1/CartopianStatus.ps1`) and the consumer (`cli/commands/wait_handoff.py` — `_status_exit_code` for the crash code, `_status_reports_exit` for the terminal `state=exited` signal) must agree on path and shape. The agreement is asserted directly in `tests/wrappers/test_wrapper_status_file.py`, which runs each wrapper against a fake assignee and feeds the produced file back through the real consumer function.

### Security

Only bounded lifecycle, exit, launch-identity, role/activity, and expected-variant fields are written. No prompt content, host conversation, credentials, tokens, connection strings, or arbitrary environment content is written.

### Lifecycle (write → consume → remove)

The status file is transient and must never outlive the handoff it describes. Its full lifecycle is:

1. **Publish current launch.** Automatic dispatch clears old report/status signals, then atomically writes `state=running` with the new launch identity and expected variant. If child creation fails, dispatch removes its own marker.
2. **Replace on assignee exit.** Every wrapper writes `state=exited` after termination, preserving dispatch identity/variant when present. Under automatic dispatch the outer supervisor publishes the final clean, error, timeout, or launch-failure outcome, so a custom wrapper that omits the optional helper cannot strand `state=running`.
3. **Consume during wait.** Both canonical waits use it as secondary evidence. The report remains authoritative; absence leaves report-only observation, an exited wrapper fails any pending retention barrier open, and a variant-mismatched stale status cannot terminate or delay the current handoff.
4. **Remove at report-clear / task-close.** `cartopian delete-report` removes the companion before slot reuse or at close.

Because emission is uniform across every wrapper, a `.status` left behind always traces to step 3 not yet having run — not to which wrapper produced it.

### Custom wrapper authors

A custom wrapper that wants to emit the same signal should source `bin/_cartopian-status.sh` (Unix) or dot-source `ps1/CartopianStatus.ps1` (Windows) and, after the assignee exits, call:

```bash
# bash
STATUS_PATH="$(cartopian_status_path "$PROMPT_PATH")"   # before any cd
# ... run the assignee, capture $ASSIGNEE_EXIT ...
cartopian_write_status "$STATUS_PATH" "$ASSIGNEE_EXIT" "$TIMEOUT_APPLIED"
```

```powershell
# PowerShell
$StatusPath = Get-CartopianStatusPath $PromptPath     # before any Set-Location
# ... run the assignee ...
Write-CartopianStatus -StatusPath $StatusPath -ExitCode $code -TimedOut $false
```

Emitting the status file is optional; omitting it simply leaves wait-handoff on its report-only path.

## Where the wrapper runs from

Cartopian wrappers always change directory to the **Cartopian project root** before invoking the underlying CLI (FR-012). The launch cwd is derived from the absolute prompt path, which always lives at:

```text
<workspace>/projects/<project-id>/prompts/PROMPT-NN-NNN.md
```

So `LAUNCH_CWD = <workspace>/projects/<project-id>`.

Why this matters: launching at the Cartopian project root ensures all handoff-relative paths in prompts resolve correctly. The prompt the PM authors references any outside-the-project resources (work roots, etc.) by absolute path/URI. Where the agent CLI imposes its *own* sandbox rooted at the launch cwd, the wrapper widens it with the declared work roots (see [Scope and gating](#scope-and-gating)). For Claude only, the wrapper also loads the native capability hook at this exact dispatched project boundary.

If the prompt is not inside a recognizable Cartopian project layout (missing the `prompts/` marker on its path), the wrapper leaves cwd unchanged and prints a notice. This keeps the wrappers usable in ad-hoc test harnesses.

### Override: `CARTOPIAN_LAUNCH_CWD`

If the recommended layout doesn't fit (split layouts where target repos live elsewhere, cross-drive setups on Windows, monorepo-internal workspaces, security policies that prefer narrower per-repo sandboxes, etc.), set `CARTOPIAN_LAUNCH_CWD` to the absolute or relative path the wrapper should `cd` to instead. Auto-resolution is skipped entirely.

```bash
# bash / zsh
export CARTOPIAN_LAUNCH_CWD=/Users/me/code/work
cartopian-codex /abs/path/to/PROMPT-01-001.md
```

```powershell
# PowerShell
$env:CARTOPIAN_LAUNCH_CWD = 'C:\Users\me\code\work'
.\cartopian-codex.ps1 C:\abs\path\to\PROMPT-01-001.md
```

A `CARTOPIAN_LAUNCH_CWD` value that does not point to an existing directory is a hard error: the wrapper exits non-zero before invoking the underlying CLI. This is intentional — silently falling back to auto-resolution after an explicit override would mask typos and lead to confusing sandbox failures downstream.

There is no `cartopian.toml` field for this. The launch cwd is treated as environment, not protocol: it varies per machine and per operator preference, and putting it in toml would invite drift between the recorded path and the actual filesystem.

## Scope and gating

The wrappers retain a **neutral launcher** role for ordinary CLI translation: they map the resolved dispatch environment into client flags, set cwd, enforce the deadline, and emit an exit signal. They do not interpret review policy, assignment, run automation, task selection, launch permission, schema identity, or application identity. The Claude wrapper has one additional responsibility: when dispatch supplies `CARTOPIAN_ROLE`, its settings helper resolves whether the project activates grants and, if so, loads the harness's PreToolUse refusal adapter. The wrapper never derives authorization from the role or wrapper name; the hook resolves effective grants. If you want approval-in-the-loop behavior, use the operator-performed path instead of the wrapper. Per-tool autonomy knobs (codex sandbox scope, claude tool whitelist, etc.) are in [Configuration](#configuration).

One nuance: some agent CLIs impose their **own** filesystem sandbox rooted at the launch cwd (codex `--sandbox workspace-write`). The launch contract grants the assignee write access to the union of the Cartopian project root and the project's declared work roots, so wrappers widen a tool-imposed sandbox to cover the work roots `cartopian dispatch` exports via `CARTOPIAN_WORK_ROOTS` — widening a sandbox up to the launch contract is not scoping, and wrappers never confine the agent below what its own CLI does. Where a tool's sandbox has no per-path grant surface (devin `--sandbox`), the wrapper warns on stderr that declared work roots may be unwritable inside it.

## Configuration

### Common (all wrappers)

| Variable | Default | Purpose |
| --- | --- | --- |
| `CARTOPIAN_TIMEOUT` | `60m` | OS-enforced wall-clock deadline from the resolved dispatch record. Accepts `30s`, `15m`, `2h`, or a bare integer (interpreted as minutes). When the deadline elapses, the wrapper sends SIGTERM to the upstream process and exits 124. |
| `CARTOPIAN_MODEL` | _(unset)_ | Agent-neutral model selection from the resolved dispatch record; each wrapper translates it into the tool-specific model flag (`claude --model`, `codex exec --model`, `agy --model`, `devin --model`, `opencode run --model`, `hermes -m`). Unset means the tool's own default model. |
| `CARTOPIAN_EFFORT` | _(unset)_ | Agent-neutral effort/thinking level from the resolved dispatch record. Claude, Codex, Antigravity, opencode, and Hermes translate it into their tool-specific flags (`--effort`, `-c model_reasoning_effort=...`, `--effort`, `--variant`, `--reasoning`); Devin ignores it with a stderr notice. A value outside a wrapper's CLI vocabulary is omitted with a notice, so the tool uses its default effort. When a pinned agy model id already encodes an effort level (`-low`/`-medium`/`-high` suffix), the model pin wins and the wrapper drops the effort with a notice — agy hard-fails a conflicting `--model`/`--effort` pair. |
| `CARTOPIAN_WORK_ROOTS` | _(unset)_ | Agent-neutral work-root write grant. Exported by `cartopian dispatch` as the project's resolved work-root absolute paths, joined with the OS path separator (`:` on POSIX, `;` on Windows). The codex wrapper widens its `workspace-write` sandbox with them (`-c sandbox_workspace_write.writable_roots=[...]` — without this, every write into a declared work root fails with "Operation not permitted"); the claude and agy wrappers pass each as `--add-dir` so the grant holds in every permission mode. The devin sandbox exposes no per-path grant surface, so that wrapper emits a stderr warning when its sandbox is active and work roots are declared. opencode imposes no filesystem sandbox at all, so its wrapper prints an explicit no-op notice: work roots need no grant there, and actual access remains subject to OS permissions and any operator permission rules; Hermes has no default path sandbox either, so its wrapper prints the same no-op notice. Unset means the project declares no work roots; dispatch never exports a stale inherited value. |
| `CARTOPIAN_HANDOFF_ID` | _(unset on manual launch)_ | Fresh dispatch identity copied into the secondary status signal. |
| `CARTOPIAN_ROLE` | _(unset on manual launch)_ | Dispatch role/config boundary inherited by the capability hook. On Claude it also asks the settings helper to resolve whether process-scoped capability enforcement is active; it never authorizes by role name. |
| `CARTOPIAN_PYTHON` | _(unset on manual launch)_ | Current Python interpreter exported by dispatch for per-launch hook commands, avoiding stale install-time interpreter paths. |
| `CARTOPIAN_EXPECTED_REPORT_VARIANT` | _(inferred on manual launch)_ | `task`, `review`, or `planning-review`; prevents another handoff kind's stale content from satisfying supervision/observation. |
| `CARTOPIAN_EXPECTED_REPORT_PATH` | _(unset on manual launch)_ | Exact bounded report slot recorded by dispatch for custom wrapper integration. |
| `CARTOPIAN_LOG_BYTE_LIMIT` / `CARTOPIAN_LOG_LINE_LIMIT` | `65536` / `400` | Retained launch-log ceilings; they do not limit wrapper execution or artifacts. |
| `CARTOPIAN_LAUNCH_LOG_PATH` | _(unset when unavailable)_ | Safe destination selected for the bounded retained representation. Wait/status paths never read its body. |
| `CARTOPIAN_STOP_GUARD_MAX_BLOCKS` | `3` | Claude Code only: stop-refusal ceiling for the completion-adapter Stop hook. See [Claude Code hooks](#claude-code-hooks). |

> Bash wrappers require `timeout` (GNU coreutils) or `gtimeout` (macOS via `brew install coreutils`). If neither is on PATH, most wrappers warn and run unbounded, since degraded execution is preferable to refusing to run. `cartopian-agy` instead falls back to its aligned internal `--print-timeout` and preserves the exit-`124` timeout status contract.

### Codex

`codex exec` is non-interactive and has no `--approval-mode` / `--ask-for-approval` flag — those live on the interactive `codex` command. Autonomy in `exec` mode is controlled by the sandbox scope plus an opt-in bypass.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CARTOPIAN_CODEX_SANDBOX` | `workspace-write` | Sandbox scope: `read-only`, `workspace-write`, `danger-full-access` |
| `CARTOPIAN_CODEX_BYPASS` | `false` | Set `true` to pass `--dangerously-bypass-approvals-and-sandbox` (overrides sandbox; only safe in externally-sandboxed environments) |

### Claude Code

| Variable | Default | Purpose |
| --- | --- | --- |
| `CARTOPIAN_CLAUDE_TOOLS` | _(empty)_ | Allowed-tool whitelist (comma-separated). Empty means claude's full default tool set. Set e.g. `Read` to restrict to read-only. |
| `CARTOPIAN_CLAUDE_FORMAT` | `text` | Output format: `text`, `json`, `stream-json` |
| `CARTOPIAN_CLAUDE_BARE` | `false` | Skip auto-discovered plugins and hooks (`true`/`false`). Explicit process-scoped capability and completion hooks remain active when their independent dispatch boundaries apply. |
| `CARTOPIAN_CLAUDE_SKIP_PERMS` | `true` | Pass `--dangerously-skip-permissions` so claude runs non-interactively. Set to `false` to re-enable permission prompts (interactive debugging only). |

#### Claude Code hooks

`claude -p` treats the assistant's final result as process exit: background shells are stopped shortly after it, and a background-task notification cannot resume the session. An assignee that ends its turn saying "the suite is still running, I'll write the report after" therefore loses both the run and the report, and the handoff lands as `exited-without-report`.

The wrapper builds one inline JSON object for Claude's per-process `--settings` layer. It can contain two logically separate entries:

- `PreToolUse`: when `CARTOPIAN_ROLE` marks a mediated dispatch and canonical config resolution finds that any role declares grants, the wrapper loads `cli/claude_hook.py`. Ungated configs add no capability entry. The hook resolves the dispatched role's effective grants and fails closed under the capability contract.
- `Stop`: whenever `CARTOPIAN_EXPECTED_REPORT_PATH` is present, the wrapper loads `cli/claude_stop_hook.py`, which blocks an absent or unparseable report at the repairable end-of-turn moment.

The wrappers do not write user, project, or local Claude settings and do not pass `--setting-sources`, so all normal settings sources remain available. Each hook command uses the current interpreter exported by dispatch and the hook path in the current install; it does not depend on an interpreter captured during an earlier installation.

`CARTOPIAN_CLAUDE_BARE=true` retains bare mode's normal suppression of auto-discovered hooks and plugins, but it does not disable either applicable process-scoped entry: the wrapper supplies them explicitly through `--settings`.

Older Cartopian versions optionally wrote capability and completion entries into project `.claude/settings.json`. During the compatibility window the wrapper reuses an entry only when it targets the current interpreter and installed hook, allowing Claude's array de-duplication to execute it once. A stale or incompatible Cartopian entry refuses launch rather than executing the hook twice or trusting an old interpreter. Remove obsolete project entries with the retained compatibility command:

```bash
python ~/.cartopian/scripts/install.py --claude-hook /path/to/cartopian/project
```

That explicit operation removes old Cartopian PreToolUse and Stop handlers while preserving all unrelated settings and hooks. It does not create a registration. Installation, update, reconciliation, and dispatch never perform this project mutation automatically.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CARTOPIAN_STOP_GUARD_MAX_BLOCKS` | `3` | Maximum stop refusals per session before the guard yields and lets the process exit. A clean exit with no report is then classified `exited-without-report`. `0` disables the guard; a malformed or negative value falls back to the default rather than silently disabling it. |

The Stop guard adds no timer — `CARTOPIAN_TIMEOUT` remains the only clock — and fails open on every error path (missing env, unreadable payload, unwritable counter, internal error). It is completion discipline, not capability enforcement. Capability refusal is point-of-use PreToolUse behavior. Governed-write provenance is after-the-fact detection. `exited-without-report` is only a completion classification. None of those mechanisms can reliably reveal unauthorized shell reads.

### Antigravity (agy)

Verified against Antigravity CLI (`agy`) `1.1.11` on macOS. **Native Windows
behavior is unverified**: the `.ps1`/`.cmd` wrapper pair ships on static parity
tests and source reading alone, pending the deferred Windows acceptance pass.

The wrapper launches agy print mode (`agy -p <prompt-path>`) with three fixed
translations beyond the common flags:

- **`--disable-slash-commands` is unconditional.** agy print mode expands a
  leading `/` as a slash command or skill, and the `-p` value here is always
  an absolute path — it must reach the agent as literal text.
- **`--print-timeout` is raised to the Cartopian deadline.** agy's internal
  print-mode wait timer defaults to 5m and would preempt `CARTOPIAN_TIMEOUT`
  on any longer handoff. The wrapper passes the same duration to both, and the
  OS `timeout` clock starts first, so it stays the single SSOT enforcer
  (exit `124` on deadline). If `timeout`/`gtimeout` is unavailable, the wrapper
  keeps the same configured duration, treats agy's internal timer as the
  fallback enforcer, and records its exit `124` as `reason=timeout` instead of
  claiming the run is unbounded.
- **An effort-suffixed model pin wins over `CARTOPIAN_EFFORT`.** Most agy
  model ids encode effort (`gemini-3.5-flash-high`), and agy hard-fails a
  conflicting `--model`/`--effort` pair; the wrapper drops the effort with a
  notice instead of failing the launch.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CARTOPIAN_AGY_MODE` | _(unset)_ | Pass `--mode <value>` (`accept-edits`, `plan`) to pick an explicit execution mode. Unset relies on the skip-permissions toggle below for autonomy. |
| `CARTOPIAN_AGY_SKIP_PERMS` | `true` | Pass `--dangerously-skip-permissions` so agy runs non-interactively. Set to `false` to re-enable permission prompts (interactive debugging only). |
| `CARTOPIAN_AGY_SANDBOX` | `false` | Boolean toggle for `--sandbox` (agy's sandbox flag is presence-only, not a value flag). Declared work roots are still granted via `--add-dir` either way. |

### Devin

The wrapper passes the prompt by file path (`devin -p --prompt-file <abs path>`) rather than streaming prompt content on the command line. This avoids shell-quoting failures on multiline prompts and matches the current devin CLI's expected invocation.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CARTOPIAN_DEVIN_PERMISSION` | `autonomous` | **Abstract** permission mode, mapped at launch onto whichever permission surface the installed `devin` binary exposes (the wrapper probes the binary's **parser acceptance** of `--permission-mode autonomous`, bounded by a 10s timeout — exit 0 → four-mode, anything else → two-mode; see `tests/wrappers/pm-devin/FINDINGS.md` § Live-binary re-probe). On the newer **four-mode** surface: `normal` → `--permission-mode normal` (writes/shell prompt — blocks a headless handoff), `accept-edits` → `--permission-mode accept-edits` (shell still prompts), `bypass` → `--permission-mode bypass` (auto-approve all, **no** OS sandbox), `autonomous` → `--sandbox --permission-mode autonomous` (auto-approve all but OS-sandbox-bounded). On the live-verified **two-mode** surface (`devin 2026.5.26-3`: only `normal`/`dangerous` + aliases are valid): `normal` and `bypass` compose unchanged (both are valid spellings there), `autonomous` → `--sandbox --permission-mode dangerous` (the same posture: auto-approval bounded by devin's own OS sandbox), and `accept-edits` **fails closed** before launch (no equivalent exists). Independently of the permission surface, the wrapper also probes whether the binary accepts `--sandbox` at all (`devin --sandbox --help`, same 10s bound); older builds predate the flag and reject it at argv parse. When `--sandbox` is unsupported, the sandbox-dependent `autonomous` mode (including the default) **degrades to `--permission-mode bypass`** (the same auto-approve-all posture minus the OS sandbox) with a warning, so the unattended handoff still runs — rather than emitting a flag the binary rejects at launch. OS containment is simply unavailable on such a build; update devin to a build that exposes `--sandbox` if you need it. Default `autonomous` is the most-restrictive sensible mode that still completes the handoff with no human in the loop — the analogue of Codex's `workspace-write` sandbox default rather than full bypass. Set `bypass` explicitly to always run unsandboxed (`bypass` never composes `--sandbox`). Legacy values map onto the abstract modes: `auto` → `normal`, `dangerous` → `bypass`. Note: devin's CLI `--sandbox` does not extend to the agents devin spawns in its cloud `/handoff`. |

### opencode

Verified against opencode `1.18.15` on macOS. **Native Windows behavior is
unverified**: the `.ps1`/`.cmd` wrapper pair ships on static parity tests and
source reading alone, pending the deferred Windows acceptance pass.

`opencode run` is non-interactive and needs no git repository. The wrapper
passes `--auto` by default, which only bypasses configured `ask` permission
rules — in a non-interactive run opencode auto-rejects `ask` prompts rather
than blocking, so `--auto` is what lets a handoff proceed on a machine with
`ask` rules configured. With no permission rules configured, opencode already
allows everything, `--auto` or not.

**There is no sandbox, and the wrapper deliberately injects no permission
policy.** opencode's only containment surface is allow/ask/deny rules over
structured tools, and an `edit` deny does not cover shell writes — the model
can bypass it in one step with a shell redirect, and the file lands. A wrapper-
injected policy would *look* like a write boundary in the operator's config
while being one redirect away from bypass, misrepresenting containment.
opencode therefore carries the `advisory+detection` containment ceiling: writes
are not contained at the point of use; bypassed governed writes may be detected
after the fact by plan-audit provenance. Two more residuals worth knowing:

- **Denials burn the deadline.** If an operator configures restrictive `deny`
  rules, a denied tool call tends to make the model retry and explore instead
  of stopping — a handoff can consume its whole `CARTOPIAN_TIMEOUT` this way.
- **`--variant` vocabulary drifts.** opencode derives per-model variants
  upstream; the wrapper's accepted list tracks the installed CLI generation
  and may lag it, exactly as the codex effort list does.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CARTOPIAN_OPENCODE_AUTO` | `true` | Pass `--auto` (bypass configured `ask` rules). Set `false` to preserve ask behavior — asks then auto-reject in non-interactive runs; explicit deny rules and OS permissions apply either way. No filesystem sandbox exists in either setting. |
| `CARTOPIAN_OPENCODE_AGENT` | _(unset)_ | Pass `--agent <name>` to select one of the operator's configured opencode agents. Unset uses opencode's default agent. |

### Hermes

Verified against Hermes Agent `v0.20.0 (2026.8.3)` on macOS. **Native Windows
behavior is unverified**: the `.ps1`/`.cmd` wrapper pair ships on static parity
tests and source reading alone, and — unlike the opencode precedent — Windows
acceptance is *required* before Hermes is claimed fully supported.

The wrapper launches Hermes one-shot mode (`hermes -z <prompt-path>`): a single
prompt in, only the final response on stdout, no banner or spinner. Hermes
loads AGENTS.md from the launch cwd and reads the prompt file with its own
tools. Exit contract: `0` success, `1` agent failure or empty final response,
`2` when the run reports failed/partial with no text — no mapping is needed;
any nonzero exit records as a handoff failure.

**One-shot mode has no approval layer, and the wrapper injects no permission
policy.** One-shot runs internally set `HERMES_YOLO_MODE=1` (dangerous-command
approval bypassed) and `HERMES_ACCEPT_HOOKS=1` (shell hooks auto-approved);
clarify prompts return a synthetic default. No wrapper flag is needed to
prevent hangs — and no wrapper flag can restore approvals in one-shot mode.
Hermes's write guards (`write_file`/`patch` denylist, `HERMES_WRITE_SAFE_ROOT`)
are documented as *not a sandbox* — the `terminal` tool runs as the same OS
user and bypasses them — so a wrapper-set guard would misrepresent
containment. Hermes carries the `advisory+detection` containment ceiling.

**Profiles are a launch knob, not a protocol concept.** Hermes profile
selection is the `-p/--profile` flag, which Hermes pre-parses before any
module import so it can rewrite `HERMES_HOME`. Exporting `HERMES_PROFILE`
selects nothing (Hermes uses that variable for Kanban author attribution), so
the wrapper translates `CARTOPIAN_HERMES_PROFILE` into the flag. The launch
config offers no per-role environment variables, so pinning one role's
handoffs to a specific Hermes profile requires a small custom wrapper
executable that exports the knob and delegates:

```bash
#!/usr/bin/env bash
export CARTOPIAN_HERMES_PROFILE=reviewer
exec /absolute/install/wrappers/bin/cartopian-hermes "$@"
```

Then set that role's configured agent to the custom wrapper's absolute path
through the mediated editor, exactly as shown in
[Alternative installation](#alternative-installation). Exporting
`CARTOPIAN_HERMES_PROFILE` in the operator's own shell instead pins *every*
Hermes role launched from that session, not one role.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CARTOPIAN_HERMES_PROFILE` | _(unset)_ | Pass `-p <profile>` to run the handoff under a named Hermes profile (the only selection mechanism Hermes honors). Unset uses Hermes's sticky default profile. |
| `CARTOPIAN_HERMES_PROVIDER` | _(unset)_ | Pass `--provider <name>` to disambiguate a bare model id. Unneeded for `provider/model` compound ids, which `-m` accepts directly. |
| `CARTOPIAN_HERMES_USAGE_FILE` | _(unset)_ | Pass `--usage-file <path>`: Hermes writes a JSON cost/token report there, even on failure. Opt-in so report directories stay within protocol file conventions by default. |

## Alternative installation

If you don't want to modify PATH, set the configured agent to a wrapper's absolute path through the mediated editor:

```bash
cartopian update-config /absolute/project/path \
  --set-role-launch coder.agent=/absolute/install/wrappers/bin/cartopian-codex
```

Or symlink individual wrappers into a directory already on your PATH:

```bash
ln -s /absolute/install/wrappers/bin/cartopian-codex /usr/local/bin/
```

## Adding a new CLI

Copy any existing wrapper from `bin/`, change the CLI invocation in the `CMD=(...)` array (keep the run-capture-status tail that sources `_cartopian-status.sh` and calls `cartopian_write_status`), and point your `cartopian.toml` to the new wrapper name. See [Status file → Custom wrapper authors](#custom-wrapper-authors) for the helper API.

## Cross-platform notes

The `bin/` scripts use `#!/usr/bin/env bash` and work on macOS, Linux, and WSL. For native Windows (PowerShell), see the `ps1/` directory for equivalent scripts.
