# Agent CLI Wrappers

## The problem

Cartopian's handoff contract is simple:

```text
<agent> <absolute prompt path>
```

Each CLI has different flags for running non-interactively. When the
PM runs `codex '/path/to/PROMPT-01-003.md'`, Codex opens an interactive
TUI and waits for keyboard input, because it doesn't know it should run
headlessly. Same with `gemini`, `claude`, and `devin`.

These wrappers fix that. They accept a prompt path, read the prompt
file, and call the real CLI with the right non-interactive flags baked
in.

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

That's it. The PM now runs `cartopian-codex '/path/to/PROMPT.md'`
instead of `codex '/path/to/PROMPT.md'`, and the wrapper handles the
rest.

### Step 3 (optional): Tune security settings

Each wrapper has a `# --- Configuration ---` section at the top of the
script. You can edit those values directly, or override them at runtime
with environment variables:

```bash
# Example: let Codex run fully autonomously (careful!)
export CARTOPIAN_CODEX_APPROVAL=never

# Example: restrict Claude to read-only
export CARTOPIAN_CLAUDE_TOOLS=Read
```

Full environment variable reference is in the [Configuration](#configuration)
section below.

## Supported CLIs

| CLI            | Wrapper              | What it runs under the hood              |
|----------------|----------------------|------------------------------------------|
| Codex (OpenAI) | `cartopian-codex`    | `codex exec --approval-mode suggest ...` |
| Claude Code    | `cartopian-claude`   | `claude -p --allowedTools Read,Write,Bash ...` |
| Gemini CLI     | `cartopian-gemini`   | `gemini -p ...`                          |
| Devin          | `cartopian-devin`    | `devin -p --permission-mode normal ...`  |

## How a wrapper works

```text
PM runs:  cartopian-codex /abs/path/to/PROMPT-01-003.md
              │
              ├─ validates the file exists
              ├─ checks that 'codex' is installed
              ├─ reads the prompt file content
              └─ exec codex exec --approval-mode suggest "<prompt content>"
```

The wrapper replaces itself with the real CLI process (`exec`), so
timeouts, signals, and exit codes all pass through cleanly to the PM.

## Configuration

### Codex

| Variable | Default | Purpose |
|---|---|---|
| `CARTOPIAN_CODEX_APPROVAL` | `suggest` | Approval mode: `suggest`, `on-request`, `untrusted`, `never` |
| `CARTOPIAN_CODEX_SANDBOX`  | *(empty)* | Sandbox mode: `workspace-write`, `danger-full-access` |

### Claude Code

| Variable | Default | Purpose |
|---|---|---|
| `CARTOPIAN_CLAUDE_TOOLS` | `Read,Write,Bash` | Allowed tools (comma-separated) |
| `CARTOPIAN_CLAUDE_FORMAT` | `text` | Output format: `text`, `json`, `stream-json` |
| `CARTOPIAN_CLAUDE_BARE` | `false` | Skip plugin/hook discovery (`true`/`false`) |
| `CARTOPIAN_CLAUDE_SKIP_PERMS` | `false` | Skip all permission prompts — **dangerous** |

### Gemini

| Variable | Default | Purpose |
|---|---|---|
| `CARTOPIAN_GEMINI_YES` | `false` | Auto-confirm tool execution (`true`/`false`) |
| `CARTOPIAN_GEMINI_SANDBOX` | *(empty)* | Sandbox mode |

### Devin

| Variable | Default | Purpose |
|---|---|---|
| `CARTOPIAN_DEVIN_PERMISSION` | `normal` | Permission mode: `normal`, `accept-edits`, `bypass`, `autonomous` |

## Alternative installation

If you don't want to modify PATH, you can reference wrappers by absolute
path in `cartopian.toml`:

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

Copy any existing wrapper from `bin/`, change the CLI invocation in the
`exec` line, and point your `cartopian.toml` to the new wrapper name.

## Cross-platform notes

The `bin/` scripts use `#!/usr/bin/env bash` and work on macOS, Linux,
and WSL. For native Windows (PowerShell), see the `ps1/` directory for
equivalent scripts.
