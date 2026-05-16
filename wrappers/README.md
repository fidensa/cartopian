# Agent CLI Wrappers

## The problem

Cartopian's handoff contract is simple:

```text
<agent> <absolute prompt path>
```

Each CLI has different flags for running non-interactively. When the PM runs `codex '/path/to/PROMPT-01-003.md'`, Codex opens an interactive TUI and waits for keyboard input, because it doesn't know it should run headlessly. Same with `gemini`, `claude`, and `devin`.

These wrappers fix that. They accept a prompt path, read the prompt file, and call the real CLI with the right non-interactive flags baked in.

## Quickstart

### Step 1: Put the wrappers on your PATH

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
| Devin | `cartopian-devin` | `devin -p --permission-mode dangerous --prompt-file <abs path>` |

By default, every wrapper runs its underlying CLI fully autonomously — no permission prompts, no TTY interaction. This is required for the PM→assignee handoff to complete without a human in the loop. If autonomy is not desired for a given role, the simple solution is not to run that role in auto mode (e.g. assign the role to `human` in `cartopian.toml`, or set `auto_start = false` on the handoff). Tighten an individual wrapper's defaults via the env vars in [Configuration](#configuration) if you need a more restrictive posture for a specific tool.

## How a wrapper works

```text
PM runs:  cartopian-codex /abs/path/to/PROMPT-01-003.md
              │
              ├─ validates the file exists
              ├─ checks that 'codex' is installed
              ├─ reads the prompt file content
              ├─ resolves the launch directory (Cartopian project root)
              └─ exec codex exec --sandbox workspace-write "<prompt content>"
```

The wrapper replaces itself with the real CLI process (`exec`), so timeouts, signals, and exit codes all pass through cleanly to the PM.

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

## Configuration

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
| `CARTOPIAN_DEVIN_PERMISSION` | `dangerous` | Permission mode: `auto`, `dangerous` (per current `devin --help`). Default `dangerous` auto-approves all tool calls so devin runs non-interactively. Legacy values `normal` and `bypass` are accepted for backward compatibility and mapped to `auto` and `dangerous` respectively. |

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

Copy any existing wrapper from `bin/`, change the CLI invocation in the `exec` line, and point your `cartopian.toml` to the new wrapper name.

## Cross-platform notes

The `bin/` scripts use `#!/usr/bin/env bash` and work on macOS, Linux, and WSL. For native Windows (PowerShell), see the `ps1/` directory for equivalent scripts.
