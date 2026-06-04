# Agent CLI Wrappers

## The problem

Cartopian's handoff contract is simple:

```text
<agent> <absolute prompt path>
```

Each CLI has different flags for running non-interactively. When the PM runs `codex '/path/to/PROMPT-01-003.md'`, Codex opens an interactive TUI and waits for keyboard input, because it doesn't know it should run headlessly. Same with `gemini`, `claude`, and `devin`.

These wrappers fix that. They accept a prompt path, read the prompt file, and call the real CLI with the right non-interactive flags baked in.

## Quickstart

### Prerequisites

- A supported agent CLI on PATH (`codex`, `claude`, `gemini`, or `devin`).
- **macOS only:** GNU coreutils provides the `gtimeout` binary the bash wrappers use to enforce `CARTOPIAN_TIMEOUT` at the OS level:
  ```bash
  brew install coreutils
  ```
  Without coreutils, the wrappers will warn at launch and run unbounded — handoffs will still execute, but a hung assignee can run forever instead of being killed at the configured deadline. Linux distributions ship `timeout` in coreutils by default; native Windows uses PowerShell's `Start-Process` + `WaitForExit` and needs no extra install.

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

### Step 2: Update your project's cartopian.toml

Change the `agent` value from the raw CLI name to the wrapper name.

**Before** (broken — CLIs open interactive sessions):

```toml
[handoffs.coder]
agent = "codex"          # opens interactive TUI, blocks
auto_start = true
timeout = "10m"

[handoffs.reviewer]
agent = "gemini"         # opens interactive REPL, blocks
auto_start = true
timeout = "10m"
```

**After** (fixed — wrappers handle non-interactive flags):

```toml
[handoffs.coder]
agent = "cartopian-codex"
auto_start = true
timeout = "10m"

[handoffs.reviewer]
agent = "cartopian-gemini"
auto_start = true
timeout = "10m"
```

That's it. The PM now runs `cartopian-codex '/path/to/PROMPT.md'` instead of `codex '/path/to/PROMPT.md'`, and the wrapper handles the rest.

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
| Gemini CLI | `cartopian-gemini` | `gemini --approval-mode yolo -p ...` |
| Devin | `cartopian-devin` | `devin -p --sandbox --permission-mode autonomous --prompt-file <abs path>` |

By default, every wrapper runs its underlying CLI fully autonomously — no permission prompts, no TTY interaction. This is required for the PM→assignee handoff to complete without a human in the loop. If autonomy is not desired for a given role, the simple solution is not to run that role in auto mode (e.g. assign the role to `human` in `cartopian.toml`, or set `auto_start = false` on the handoff). Tighten an individual wrapper's defaults via the env vars in [Configuration](#configuration) if you need a more restrictive posture for a specific tool.

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

### Clean exit on report-complete (handoff exit contract)

Some assignee CLIs keep running after they have written the report — MCP stdio servers that are not torn down, an inherited open stdin, or a trailing turn leave the process alive with no work left to do. If the wrapper only waited for that process, a *finished* handoff would sit idle until `timeout` killed it (exit `124`, `reason=timeout`) — a success that always read as a deadline failure.

The shared helper `cartopian_run_supervised` (in `bin/_cartopian-status.sh`) fixes this with a **report-completion supervisor**. It runs the assignee with stdin redirected from `/dev/null` (closing one lingering mode) and watches for the expected report file. The report file is the **authoritative completion signal** (the same one `wait-handoff` parses); once it appears complete — present, non-empty, carrying a top-level `Status: <complete|blocked|failed>` line — the supervisor grants the child a brief grace to exit on its own and then reaps it, so the wrapper exits `0` / `reason=clean` promptly.

This is **event-driven, not a second timer**. The single `CARTOPIAN_TIMEOUT` deadline (applied via `timeout`, the [SSOT](../protocol/CONVENTIONS.md) enforcer) remains the only clock and is never extended: a genuine hang writes no report, is never reaped early, and still hits the deadline with exit `124` / `reason=timeout`. The grace and poll cadence are tunable via `CARTOPIAN_REPORT_GRACE_POLLS` (default 3) and `CARTOPIAN_REPORT_POLL` (default 2s); no per-tool CLI timeout flag is introduced.

## Status file (early-crash detection)

When the assignee process exits, every wrapper writes a small **status file** capturing the exit outcome. This is the optional early-crash-detection signal `cartopian wait-handoff` polls for: if the assignee dies before producing a report, wait-handoff can return `failed` immediately instead of blocking to the deadline.

**The report file remains the authoritative completion signal.** The status file is never a hard requirement — if it is missing (helper absent, unwritable directory, prompt outside a project layout), wait-handoff degrades gracefully to the report-only path. Wrappers therefore write it best-effort: any failure to write is swallowed and never changes the wrapper's own exit code.

### Path

The status file lives at the expected report path with a `.status` suffix — exactly the path `wait_handoff.py` derives:

```text
<project-root>/reports/REPORT-NN-NNN.md.status
```

The `NN-NNN` id comes from the prompt filename (`PROMPT-NN-NNN.md`), and the project root is the prompt's grandparent directory (`<project-root>/prompts/PROMPT-NN-NNN.md`). Wrappers compute this from the prompt path *before* changing the launch cwd, so a relative prompt path still resolves.

### Shape

Newline-separated `key=value` lines, UTF-8:

```text
state=exited
exit_code=<int>
reason=clean|error|timeout
```

| Field | Meaning |
| --- | --- |
| `state` | Always `exited` once the assignee process has terminated. The consumer only acts on `state=exited`. |
| `exit_code` | The assignee's exit code. A **non-zero** code is the crash signal (`wait-handoff` reports `failed`); `0` is not a crash. |
| `reason` | Human/diagnostic distinction only — **ignored by the consumer**, which keys off `exit_code` alone. One of `clean` (exit 0), `error` (any other non-zero exit), or `timeout` (the OS deadline killed the assignee). |

### Outcome → fields

| Outcome | `state` | `exit_code` | `reason` | wait-handoff verdict |
| --- | --- | --- | --- | --- |
| Clean exit | `exited` | `0` | `clean` | not a crash (falls through to report/budget) |
| Non-zero exit | `exited` | `<n≠0>` | `error` | `failed` |
| Timeout kill | `exited` | `124` | `timeout` | `failed` |

A timeout kill is recorded as `state=exited` with `exit_code=124` (the value coreutils `timeout` returns when it kills the child at the deadline — see [§ Handoffs](../protocol/CONVENTIONS.md) and `CARTOPIAN_TIMEOUT`). It is deliberately surfaced to the consumer as a non-zero exit (a crash); the extra `reason=timeout` line distinguishes it from a plain non-zero exit for humans and custom tooling without changing the consumer-visible contract.

### Consumer / producer agreement

The producer (the shared helpers `bin/_cartopian-status.sh` and `ps1/CartopianStatus.ps1`) and the consumer (`cli/commands/wait_handoff.py :: _status_exit_code`) must agree on path and shape. The agreement is asserted directly in `tests/wrappers/test_wrapper_status_file.py`, which runs each wrapper against a fake assignee and feeds the produced file back through the real consumer function.

### Security

Only the three fields above are ever written — all derived from the exit outcome. No environment variables, prompt content, credentials, tokens, or connection strings are written to the status file.

### Lifecycle (write → consume → remove)

The status file is transient and must never outlive the handoff it describes. Its full lifecycle is:

1. **Write on assignee exit.** Every wrapper — all of `wrappers/bin/*` and `wrappers/ps1/*` — writes the file under the same condition: once the assignee process has exited and a status path could be derived from the prompt (a prompt inside a `…/prompts/PROMPT-NN-NNN.md` layout). Emission does not depend on the exit code — clean, error, and timeout exits all produce a file — and it does not depend on whether `cartopian resolve-config` succeeds (an unregistered or ad-hoc project still emits). Writing is best-effort, so a genuinely unwritable target degrades to no file rather than a wrapper failure.
2. **Consume during wait.** `cartopian wait-handoff` reads it as the optional early-crash signal while it blocks. The report file remains the authoritative completion signal; an absent `.status` simply leaves the wait on its report-only path.
3. **Remove at report-clear / task-close.** `cartopian delete-report <report-path>` removes the companion `<report-path>.status` together with the report at report-clear (before a slot is reused), and `cartopian delete-report <report-path> --status-only` removes just the status file at task close, when the report `.md` is retained as evidence. The PM lifecycle (`skills/run-task.md`, `skills/run-handoff.md`) calls these at the right stages; absence of the file is always a no-op.

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

Why this matters: launching at the Cartopian project root ensures all handoff-relative paths in prompts resolve correctly and aligns with the FR-012 contract enforced by `skills/run-handoff.md`. Access to outside-the-project resources is explicitly granted via resolved absolute work-root paths (OQ-009) rather than by broadening cwd.

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

## Work-root union scoping

When a task declares **work roots** (`protocol/CONVENTIONS.md` → Launch Directory / Work Roots), the wrapper must grant the assignee the **union** of the launch cwd (the Cartopian project root) and each resolved work-root absolute path — *nothing wider, nothing narrower*. The shared helper `cartopian_enforce_work_roots` (in `bin/_cartopian-status.sh`) resolves that union via `cartopian resolve-config` and, for a tool whose sandbox can scope a multi-directory union **natively**, grants it by injecting the tool's native multi-directory flags so the agent launches **scoped to the union** — no blanket bypass. The launch cwd is already each tool's primary writable scope, so only the declared roots are added.

A wrapper opts into native scoping by defining a `cartopian_tool_scope_union` hook (the helper detects it with `declare -F` and calls it). A new wrapper inherits the whole mechanism by defining that one hook; a wrapper with no hook stays fail-closed-or-bypass. The resolved per-tool mechanisms:

| Wrapper | Native mechanism | What it does |
| --- | --- | --- |
| `cartopian-claude` | `--add-dir <dir>` per declared root | "Additional directories to allow tool access to" — extends the tool-access scope to the union. (Claude's autonomous posture has no OS-level path sandbox, so this is a tool-layer grant, not a kernel sandbox.) |
| `cartopian-codex` | `codex exec --add-dir <DIR>` per declared root | "Additional directories that should be writable alongside the primary workspace" — extends the `workspace-write` sandbox's writable roots to the union. Added only in the sandboxed branch; under `--dangerously-bypass-approvals-and-sandbox` the sandbox is off and the union is moot. |
| `cartopian-gemini` | `--include-directories <dirs>` (comma-joined) | "Additional directories to include in the workspace" — extends gemini's workspace (the writable/in-context scope) to the union. |
| `cartopian-devin` | _(none)_ | The devin CLI exposes no local multi-directory write-scoping flag, so this wrapper defines **no** scope hook and stays **fail-closed** on a non-empty work-root union (`[work-root]` stderr line), with the `CARTOPIAN_DEVIN_UNRESTRICTED` bypass as the only opt-out. Scope a devin work-root task on its hosted side, or use a locally-scopable wrapper. |

**Fail-closed default is preserved.** A resolved root that is missing on disk fails closed (`[work-root] missing: <path>`), and a non-empty union a tool cannot scope natively fails closed (`[work-root] tool cannot scope multi-root access; set <VAR>=true to bypass (dangerous)`). The per-tool unrestricted bypass (`CARTOPIAN_<AGENT>_UNRESTRICTED=true`) remains the documented full-access opt-out and **takes precedence over native scoping** — set it only when you deliberately want unscoped access. The operator-visible launch prints one `scoped work root: <path>` line per granted root.

**Reviewer-recapture is unchanged.** When `CARTOPIAN_REVIEW_RECAPTURE` is active the reviewed source work root stays **read-only**: the guard returns before native scoping runs, so no writable-scope flag is emitted for the source (the recapture writable scope stays exactly the launch cwd + `$TMPDIR`/`/tmp`, plus egress). Outside recapture, a work root is part of the general read/write union and *is* scoped writable — the read-only-source narrowing is a recapture-only property. See [Reviewer live-evidence recapture](#reviewer-live-evidence-recapture--exact-scope).

## Configuration

### Common (all wrappers)

| Variable | Default | Purpose |
| --- | --- | --- |
| `CARTOPIAN_TIMEOUT` | `60m` | OS-enforced wall-clock deadline for the dispatched handoff. Accepts `30s`, `15m`, `2h`, or a bare integer (interpreted as minutes). Set by the PM from the resolved `[handoffs.<role>].timeout`. When the deadline elapses, the wrapper sends SIGTERM to the upstream process and exits 124. |
| `CARTOPIAN_MODEL` | _(unset)_ | Agent-neutral model selection. Exported by `cartopian dispatch` from the resolved `[handoffs.<role>].model`; each wrapper translates it into the tool-specific model flag (`claude --model`, `codex exec --model`, `gemini --model`, `devin --model` — all four shipped wrappers honor it). Unset means the tool's own default model; dispatch never exports a stale inherited value when the handoff sets no model. |
| `CARTOPIAN_REVIEW_RECAPTURE` | _(unset)_ | Agent-neutral, opt-in, **reviewer live-evidence recapture** signal (TASK-03-007, FR-011). When truthy (`1`/`true`/`yes`/`on`), every wrapper treats the declared work roots as the **read-only source under review** (never added to the writable scope) and grants **network egress** so the reviewer can re-run the task's probe harness and reproduce the live evidence instead of trusting the assignee's pinned artifacts. The signal carries **no agent name** — it attaches to the reviewer role — so a new wrapper inherits the behavior by sourcing the shared helper. It is normally exported by `cartopian dispatch --recapture`, which only does so for a reviewer handoff on a task that declares `Evidence gate: required` (opt-in + evidence-gated); a review with no such gate is completely unaffected (no network, no scratch change). |

> Bash wrappers require `timeout` (GNU coreutils) or `gtimeout` (macOS via `brew install coreutils`). If neither is on PATH the wrapper warns and runs unbounded, since deadline enforcement is preferable to refusing to run.

#### Reviewer live-evidence recapture — exact scope

When `CARTOPIAN_REVIEW_RECAPTURE` is active the writable filesystem scope is **exactly the launch cwd plus `$TMPDIR`/`/tmp`** — the probe harness scratch where the relocated runtime home and the fresh evidence are written. The reviewed source work root stays **read-only**: it is never added to the writable scope, so a reviewer cannot edit the implementation it reviews. Network, when granted, **adds egress only** — it does not widen the writable filesystem scope.

How each wrapper realizes the contract (the signal handling is identical via the shared `cartopian_review_recapture_active` / `cartopian_review_recapture_banner` helper in `_cartopian-status.sh`; the read-only-source *enforcement layer* depends on the tool):

- **Codex** — `--sandbox workspace-write` roots writes at the launch cwd + `$TMPDIR`/`/tmp`, keeping the source work root read-only at the sandbox layer; recapture additionally sets `-c sandbox_workspace_write.network_access=true` for egress.
- **Gemini** — recapture forces the OS sandbox (`--sandbox`) on, which roots writes at the launch cwd (+ temp) and keeps the source read-only while retaining egress.
- **Claude / Devin** — honor the same signal and print the same scope banner, and never add a write grant for the source; their autonomous wrapper posture has no local path-scoping sandbox, so the read-only-source boundary is held by contract (and, for Devin, by its own execution isolation) rather than a local sandbox layer. Prefer a locally-sandboxed reviewer agent (codex/gemini) when a hard review-integrity guarantee at the sandbox layer is required.

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
| `CARTOPIAN_CLAUDE_BARE` | `false` | Skip plugin/hook discovery (`true`/`false`) |
| `CARTOPIAN_CLAUDE_SKIP_PERMS` | `true` | Pass `--dangerously-skip-permissions` so claude runs non-interactively. Set to `false` to re-enable permission prompts (interactive debugging only). |

### Gemini

| Variable | Default | Purpose |
| --- | --- | --- |
| `CARTOPIAN_GEMINI_APPROVAL` | `yolo` | Approval mode: `default`, `auto_edit`, `yolo`, `plan`. Set to empty string to fall back to the legacy `-y/--yolo` toggle below. |
| `CARTOPIAN_GEMINI_YES` | `true` | Legacy auto-confirm (`-y`). Used only when `CARTOPIAN_GEMINI_APPROVAL` is empty. |
| `CARTOPIAN_GEMINI_SANDBOX` | `false` | Boolean toggle for `--sandbox` (gemini's sandbox flag is presence-only, not a value flag). |

### Devin

The wrapper passes the prompt by file path (`devin -p --prompt-file <abs path>`) rather than streaming prompt content on the command line. This avoids shell-quoting failures on multiline prompts and matches the current devin CLI's expected invocation.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CARTOPIAN_DEVIN_PERMISSION` | `autonomous` | Permission mode on the current documented Devin CLI surface: `normal` (`--permission-mode normal`; writes/shell prompt — blocks a headless handoff), `accept-edits` (`--permission-mode accept-edits`; shell still prompts), `bypass` (`--permission-mode bypass`; auto-approve all, **no** OS sandbox), `autonomous` (`--sandbox --permission-mode autonomous`; auto-approve all but OS-sandbox-bounded, fail-closed — devin's `--sandbox` is documented Unstable). Default `autonomous` is the most-restrictive sensible mode that still completes the handoff with no human in the loop — the analogue of Codex's `workspace-write` sandbox default rather than full bypass. Set `bypass` to run unsandboxed (accepting the unbounded risk). Legacy values are mapped onto the real surface: `auto` → `normal`, `dangerous` → `bypass`. Devin remains **tier-3 not-recommended-as-PM-host** (`tests/wrappers/pm-devin/FINDINGS.md`); the local `--sandbox` does not extend to its cloud `/handoff`. |

## Alternative installation

If you don't want to modify PATH, you can reference wrappers by absolute path in `cartopian.toml`:

```toml
[handoffs.coder]
agent = "/Users/scott/Projects/cartopian/wrappers/bin/cartopian-codex"
auto_start = true
timeout = "10m"
```

Or symlink individual wrappers into a directory already on your PATH:

```bash
ln -s /Users/scott/Projects/cartopian/wrappers/bin/cartopian-codex /usr/local/bin/
```

## Adding a new CLI

Copy any existing wrapper from `bin/`, change the CLI invocation in the `CMD=(...)` array (keep the run-capture-status tail that sources `_cartopian-status.sh` and calls `cartopian_write_status`), and point your `cartopian.toml` to the new wrapper name. See [Status file → Custom wrapper authors](#custom-wrapper-authors) for the helper API.

## Cross-platform notes

The `bin/` scripts use `#!/usr/bin/env bash` and work on macOS, Linux, and WSL. For native Windows (PowerShell), see the `ps1/` directory for equivalent scripts.
