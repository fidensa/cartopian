# Agent CLI Wrappers

Cartopian's handoff contract is:

```text
<agent> <absolute prompt path>
```

Each agent CLI has its own flags for non-interactive execution, sandbox
policies, approval modes, and output formats. Per protocol, these
tool-specific flags belong in a **wrapper executable**, not in
`cartopian.toml`.

This directory ships cross-platform wrapper scripts that adapt each
supported CLI to the Cartopian handoff contract.

## Supported CLIs

| CLI           | Wrapper script     | Upstream non-interactive flag |
|---------------|--------------------|-------------------------------|
| Codex (OpenAI)| `cartopian-codex`  | `codex exec -a never`         |
| Claude Code   | `cartopian-claude` | `claude -p --allowedTools`    |
| Gemini CLI    | `cartopian-gemini` | `gemini -p`                   |
| Devin         | `cartopian-devin`  | `devin -p`                    |

## How it works

Each wrapper:

1. Accepts one argument: the absolute path to a prompt file.
2. Reads the prompt file content.
3. Invokes the upstream CLI with the correct non-interactive flags.
4. Passes the prompt content (not the path) to the CLI.
5. Exits with the upstream CLI's exit code.

The wrappers are intentionally minimal. They do **not** parse reports,
enforce timeouts, or perform lifecycle actions — that is the PM's job
via `skills/run-handoff.md`.

## Installation

### Option A: Add to PATH (recommended)

```bash
# From the Cartopian workspace root:
export PATH="$PWD/wrappers/bin:$PATH"

# Or add permanently to your shell profile:
echo 'export PATH="/path/to/cartopian/wrappers/bin:$PATH"' >> ~/.zshrc
```

### Option B: Symlink into an existing PATH directory

```bash
ln -s /path/to/cartopian/wrappers/bin/cartopian-codex /usr/local/bin/
ln -s /path/to/cartopian/wrappers/bin/cartopian-claude /usr/local/bin/
ln -s /path/to/cartopian/wrappers/bin/cartopian-gemini /usr/local/bin/
ln -s /path/to/cartopian/wrappers/bin/cartopian-devin /usr/local/bin/
```

### Option C: Reference in cartopian.toml by full path

```toml
[handoffs.coder]
agent = "/path/to/cartopian/wrappers/bin/cartopian-codex"
auto_start = true
timeout = "60m"
```

## Customization

### Approval and sandbox policies

Each wrapper has a `# --- Configuration ---` section near the top with
clearly documented variables for approval mode, sandbox settings, and
other tool-specific flags. Edit these to match your security posture.

Defaults are conservative:

- **Codex**: `--approval-mode suggest` (agent suggests, human confirms)
- **Claude Code**: allowed tools restricted to read/write/bash
- **Gemini**: no tool-approval override (uses upstream default)
- **Devin**: `--permission-mode normal` (asks before acting)

### Adding a new CLI

Copy any existing wrapper, change the CLI invocation in the `exec`
section, and update `cartopian.toml` to point to the new wrapper name.

## Cross-platform notes

The `bin/` scripts use `#!/usr/bin/env bash` and should work on macOS,
Linux, and WSL. For native Windows (PowerShell), see the `ps1/` directory
for PowerShell equivalents.
