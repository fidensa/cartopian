# Skill: Register MCP

Register or re-register Cartopian's MCP server (`cartopian-mcp`) with one or more AI agents on the operator's machine. Run this after a fresh install to cover additional agents, or whenever a new agent is added to the operator's workflow.

**Output:** for each selected agent, `cartopian-mcp` is registered in its MCP config **and** a "use cartopian" trigger bridge (skill, prompt, or command) is installed so the entry phrase actually routes to the `use_cartopian` prompt. The operator can then enter Cartopian PM mode from any registered agent in any directory.

---

## Prerequisites

- Cartopian is installed and `cartopian --help` exits 0.
- If called from `install-cartopian.md`, `$install_root` is already resolved — skip Stage 0.

---

## Stage 0 — Resolve install root (standalone only)

Resolve `$install_root` if it is not already set:

- Default: `~/.cartopian` (macOS/Linux), `%USERPROFILE%\.cartopian` (Windows).
- If a non-default `--prefix` was used during install, ask the operator where Cartopian is installed.

Confirm the install root is valid: check that `$install_root/bin/cartopian-mcp` (Unix) or `$install_root\bin\cartopian-mcp.cmd` (Windows) exists before continuing.

---

## Stage 1 — Detect installed agents

Check for the presence of each supported agent using the platform-appropriate signal. For agents that use a JSON config file, also check whether a `cartopian` key already exists under `mcpServers`.

| Agent | Detection signal | Config file (macOS/Linux) | Config file (Windows) |
| --- | --- | --- | --- |
| Claude Code | `claude` on PATH | n/a — uses CLI | n/a — uses CLI |
| Codex | `codex` on PATH | `~/.codex/config.toml` | `%USERPROFILE%\.codex\config.toml` |
| Gemini | `gemini` on PATH | `~/.gemini/settings.json` | `%USERPROFILE%\.gemini\settings.json` |
| Devin | `devin` on PATH **or** config file exists | `~/.config/devin/config.json` | `%APPDATA%\devin\config.json` |
| Windsurf | `~/.codeium/windsurf/` dir exists | `~/.codeium/windsurf/mcp_config.json` | `%APPDATA%\Windsurf\mcp_config.json` |
| Claude Desktop | Config file exists | `~/Library/Application Support/Claude/claude_desktop_config.json` | `%APPDATA%\Claude\claude_desktop_config.json` |
| Cursor | `~/.cursor/` dir exists | `~/.cursor/mcp.json` | `%USERPROFILE%\.cursor\mcp.json` |

For Claude Code: run `claude mcp list` and check for a `cartopian` entry to determine registration status.

For Codex: run `codex mcp list` and check for a `cartopian` entry to determine registration status. (The underlying store is `~/.codex/config.toml` under `[mcp_servers.cartopian]`, but the CLI is the supported interface.)

For Gemini: run `gemini mcp list` and check for a `cartopian` entry to determine registration status. (The underlying store is `~/.gemini/settings.json` under `mcpServers.cartopian`, but the CLI is the supported interface.)

For JSON-config agents: read the file (if it exists) and check for `mcpServers.cartopian`.

Mark each agent as **present — not registered**, **present — already registered**, or **not detected**.

---

## Stage 2 — Present status and ask

Present a status table to the operator, for example:

```
Agent           Status
──────────────  ─────────────────────────
Claude Code     present — not registered
Codex           present — already registered
Gemini          present — not registered
Devin           not detected
Windsurf        present — not registered
Claude Desktop  not detected
Cursor          not detected
```

Ask:
- Which agents (detected as present and not registered) should Cartopian be registered with?
- Are there agents not in this list the operator wants to configure?

Do not modify any config without the operator explicitly selecting it.

---

## Stage 3 — Apply registrations

Apply the recipe for each agent the operator selected. Always confirm before writing to a config file.

**Every recipe has two parts:**

- **Part A — register the MCP server** so the `cartopian` tools, prompt, and resources are reachable.
- **Part B — install the "use cartopian" trigger bridge.** Registering the MCP server alone is *not* enough: no agent auto-surfaces the server's `use_cartopian` prompt as a phrase- or slash-invocable command. Each agent needs a small bridge file (a skill, prompt, or command) that maps the operator's entry phrase onto that prompt. The bridge bodies ship as templates under `<install_root>/templates/clients/<agent>/` — copy them verbatim into the agent's command/skill directory. Create any missing parent directories. Do not edit the template content during the copy; operators can tune it in place afterward.

The named agents below (Claude Code, Codex, Gemini, Devin, Windsurf) get both parts. Claude Desktop and Cursor are MCP-only — they have no general-purpose local command/skill mechanism to bridge onto, so the operator triggers Cartopian there by invoking the `use_cartopian` MCP prompt directly from the client's prompt picker.

### Claude Code

**Part A — register the MCP server.**

```bash
claude mcp add cartopian "$install_root/bin/cartopian-mcp" --scope user
```

Verify with `claude mcp list`. The entry must show `cartopian` pointing at the install root's `bin/cartopian-mcp`. No restart required — takes effect immediately.

**Part B — install the trigger bridge.** Claude Code does not expose an MCP prompt as a slash command or skill automatically. Install both a **Skill** (so the bare phrase "use cartopian" routes via description matching) and a **slash command** (so `/use-cartopian` works explicitly):

```bash
mkdir -p ~/.claude/skills/use-cartopian
cp "$install_root/templates/clients/claude-code/skills/use-cartopian/SKILL.md" \
   ~/.claude/skills/use-cartopian/SKILL.md
mkdir -p ~/.claude/commands
cp "$install_root/templates/clients/claude-code/commands/use-cartopian.md" \
   ~/.claude/commands/use-cartopian.md
```

**Windows (PowerShell):**

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.claude\skills\use-cartopian" | Out-Null
Copy-Item "$installRoot\templates\clients\claude-code\skills\use-cartopian\SKILL.md" `
  "$env:USERPROFILE\.claude\skills\use-cartopian\SKILL.md" -Force
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.claude\commands" | Out-Null
Copy-Item "$installRoot\templates\clients\claude-code\commands\use-cartopian.md" `
  "$env:USERPROFILE\.claude\commands\use-cartopian.md" -Force
```

Claude Code picks up newly-dropped skills and commands without a restart. After this, the operator can say "use cartopian" (skill) or type `/use-cartopian` (command) from any directory.

### Codex

**Part A — register the MCP server.**

```bash
codex mcp add cartopian -- "$install_root/bin/cartopian-mcp"
```

**Windows (PowerShell) — use the `.cmd` shim:**

```powershell
codex mcp add cartopian -- "$installRoot\bin\cartopian-mcp.cmd"
```

Verify with `codex mcp list`. The entry must show `cartopian` pointing at the install root's `bin/cartopian-mcp` (Unix) or `bin\cartopian-mcp.cmd` (Windows). Codex reads `~/.codex/config.toml` at launch; existing Codex sessions need to be restarted before the new server is available.

**Part B — install the trigger bridge.** Copy the custom-prompt file into Codex's global prompts directory (only top-level `.md` files are scanned — do not nest it in a subdirectory):

```bash
mkdir -p ~/.codex/prompts
cp "$install_root/templates/clients/codex/use-cartopian.md" \
   ~/.codex/prompts/use-cartopian.md
```

**Windows (PowerShell):**

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.codex\prompts" | Out-Null
Copy-Item "$installRoot\templates\clients\codex\use-cartopian.md" `
  "$env:USERPROFILE\.codex\prompts\use-cartopian.md" -Force
```

After a restart the operator types `/use-cartopian` to enter PM mode. (Codex now marks custom prompts as deprecated in favor of its newer "skills" mechanism, but prompts still work; if a future Codex drops them, move this bridge to `~/.codex/skills/`.)

### Gemini

**Part A — register the MCP server.** The CLI is the supported interface. **Pass `--scope user`** — `gemini mcp add` defaults to `--scope project`, which would write a `.gemini/settings.json` into the current working directory instead of the global config. The user scope writes `mcpServers.cartopian` into `~/.gemini/settings.json`.

```bash
gemini mcp add cartopian "$install_root/bin/cartopian-mcp" --scope user
```

**Windows (PowerShell) — use the `.cmd` shim:**

```powershell
gemini mcp add cartopian "$installRoot\bin\cartopian-mcp.cmd" --scope user
```

If the installed `gemini` lacks `mcp add`, merge the entry into `~/.gemini/settings.json` (Windows: `%USERPROFILE%\.gemini\settings.json`) by hand, preserving existing keys:

```json
{
  "mcpServers": {
    "cartopian": {
      "command": "<install_root>/bin/cartopian-mcp"
    }
  }
}
```

Verify with `gemini mcp list` (or `/mcp` inside a Gemini session). Restart Gemini before the server is available.

**Part B — install the trigger bridge.** Copy the TOML custom-command into Gemini's global commands directory:

```bash
mkdir -p ~/.gemini/commands
cp "$install_root/templates/clients/gemini/use-cartopian.toml" \
   ~/.gemini/commands/use-cartopian.toml
```

**Windows (PowerShell):**

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.gemini\commands" | Out-Null
Copy-Item "$installRoot\templates\clients\gemini\use-cartopian.toml" `
  "$env:USERPROFILE\.gemini\commands\use-cartopian.toml" -Force
```

After this the operator types `/use-cartopian` (run `/commands reload` or restart Gemini to pick up the new command).

### Claude Desktop

Read the config file. If it does not exist, create it with an empty JSON object first. Add a `cartopian` entry under `mcpServers`, preserving any existing siblings:

**macOS/Linux:**
```json
{
  "mcpServers": {
    "cartopian": {
      "command": "<install_root>/bin/cartopian-mcp"
    }
  }
}
```

**Windows — use the `.cmd` shim and escape backslashes:**
```json
{
  "mcpServers": {
    "cartopian": {
      "command": "<installRoot>\\bin\\cartopian-mcp.cmd"
    }
  }
}
```

Claude Desktop must be fully quit and relaunched before the server registers.

### Cursor

Same `mcpServers` structure as Claude Desktop. Read the config file; create it if absent; merge if present.

**macOS/Linux:** `~/.cursor/mcp.json`  
**Windows:** `%USERPROFILE%\.cursor\mcp.json`

```json
{
  "mcpServers": {
    "cartopian": {
      "command": "<install_root>/bin/cartopian-mcp"
    }
  }
}
```

On Windows, use the `.cmd` shim. Cursor must be restarted.

### Windsurf

Windsurf needs two things: the MCP server registered globally, and a global slash-command workflow that maps the operator's "use cartopian" phrase onto the MCP `use_cartopian` prompt. Cascade does not auto-surface MCP prompts as slash commands — only files under a `workflows/` directory map to slash commands — so MCP registration alone is insufficient.

**Part A — Register the MCP server (global).** Same `mcpServers` structure as Claude Desktop. Read and merge the config file.

**macOS/Linux:** `~/.codeium/windsurf/mcp_config.json`  
**Windows:** `%APPDATA%\Windsurf\mcp_config.json`

```json
{
  "mcpServers": {
    "cartopian": {
      "command": "<install_root>/bin/cartopian-mcp"
    }
  }
}
```

On Windows, use the `.cmd` shim. Windsurf must be restarted.

**Part B — Install the `/use-cartopian` workflow (global).** Install the workflow file once into Windsurf's global workflows directory so `/use-cartopian` is available in every Cascade session, regardless of workspace.

Copy the template:

```text
<install_root>/templates/clients/windsurf/use-cartopian.md
```

into:

**macOS/Linux:** `~/.codeium/windsurf/workflows/use-cartopian.md`  
**Windows:** `%APPDATA%\Windsurf\workflows\use-cartopian.md`

Create the `workflows/` directory if it does not exist. Do not modify the template content during the copy — operators can tune it in place afterward. After the file is in place, the operator can type `/use-cartopian` from Cascade to enter Cartopian PM mode; saying "use cartopian" in natural language is best-effort and depends on Cascade's prompt routing, so the slash form is the contract.

### Devin

This recipe targets **Devin for Terminal** (the local `devin` CLI that the `cartopian-devin` wrapper drives), not cloud Devin. Cloud Devin's reusable instructions are web-UI Playbooks/Knowledge with no local file to install, so only the MCP registration (Part A) applies there.

**Part A — register the MCP server.** Same `mcpServers` structure as Claude Desktop. Read the config file; if it does not exist, create the parent directory (`~/.config/devin/` on Unix or `%APPDATA%\devin\` on Windows) and write a fresh `{}` first. Merge the `cartopian` entry under `mcpServers`, preserving every existing top-level key and every existing sibling under `mcpServers` — Devin stores other settings in this same file and a clobbering write would lose them.

**macOS/Linux:** `~/.config/devin/config.json`  
**Windows:** `%APPDATA%\devin\config.json`

```json
{
  "mcpServers": {
    "cartopian": {
      "command": "<install_root>/bin/cartopian-mcp"
    }
  }
}
```

**Windows — use the `.cmd` shim and escape backslashes:**
```json
{
  "mcpServers": {
    "cartopian": {
      "command": "<installRoot>\\bin\\cartopian-mcp.cmd"
    }
  }
}
```

Write the merged document back atomically (write to a sibling temp file in the same directory, then rename over the original) so a crash mid-write cannot leave Devin with a truncated config. Devin must be restarted before the new server is available.

**Part B — install the trigger bridge (Devin for Terminal only).** Devin for Terminal reads global skills from a `skills/` directory; the skill's directory name is its identifier. Copy the bridge skill:

```bash
mkdir -p ~/.config/devin/skills/use-cartopian
cp "$install_root/templates/clients/devin/skills/use-cartopian/SKILL.md" \
   ~/.config/devin/skills/use-cartopian/SKILL.md
```

**Windows (PowerShell):** the skills root sits beside the config file under `%APPDATA%\devin\`:

```powershell
New-Item -ItemType Directory -Force -Path "$env:APPDATA\devin\skills\use-cartopian" | Out-Null
Copy-Item "$installRoot\templates\clients\devin\skills\use-cartopian\SKILL.md" `
  "$env:APPDATA\devin\skills\use-cartopian\SKILL.md" -Force
```

The bridge skill carries `triggers: [user, model]`, so the operator can say "use cartopian" or type `/use-cartopian`.

### Other agents

If the operator names an agent not covered above, provide the registration facts and direct them to that agent's MCP documentation:

- **Command:** `<install_root>/bin/cartopian-mcp` (Unix) or `<installRoot>\bin\cartopian-mcp.cmd` (Windows)
- **Transport:** stdio (newline-delimited JSON-RPC, no Content-Length headers)
- **Protocol version:** MCP 2024-11-05

---

## Stage 4 — Summarize

Report, per agent the operator selected:

- Whether the MCP server was already registered (no change) or newly registered this run (Part A).
- Whether the trigger bridge was installed (Part B) and **how the operator invokes it**:

  | Agent | Entry phrase / command |
  | --- | --- |
  | Claude Code | say "use cartopian" (skill) or `/use-cartopian` |
  | Codex | `/use-cartopian` |
  | Gemini | `/use-cartopian` |
  | Devin for Terminal | say "use cartopian" (skill trigger) or `/use-cartopian` |
  | Windsurf | `/use-cartopian` |
  | Claude Desktop / Cursor | invoke the `use_cartopian` MCP prompt from the client's prompt picker (MCP-only — no bridge) |

- Each agent that requires a restart before the bridge is live (Codex, Gemini, Windsurf, Devin, Claude Desktop, Cursor). Claude Code needs no restart.
- Any agent requiring manual steps — summarize what the operator needs to do.

Once an agent has both parts, the operator opens it in any directory and uses the entry phrase/command above. That loads the `use_cartopian` prompt, which puts the agent in PM mode and routes to the first useful action (`start_session` if projects exist, `init_project` if not).
