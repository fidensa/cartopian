# Skill: Register MCP

Register or re-register Cartopian's MCP server (`cartopian-mcp`) with one or more AI agents on the operator's machine. Run this after a fresh install to cover additional agents, or whenever a new agent is added to the operator's workflow.

**Output:** `cartopian-mcp` is registered in each selected agent's MCP config. The operator can say "use cartopian" from any registered agent in any directory.

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
| Claude Desktop | Config file exists | `~/Library/Application Support/Claude/claude_desktop_config.json` | `%APPDATA%\Claude\claude_desktop_config.json` |
| Cursor | `~/.cursor/` dir exists | `~/.cursor/mcp.json` | `%USERPROFILE%\.cursor\mcp.json` |
| Windsurf | `~/.codeium/windsurf/` dir exists | `~/.codeium/windsurf/mcp_config.json` | `%APPDATA%\Windsurf\mcp_config.json` |

For Claude Code: run `claude mcp list` and check for a `cartopian` entry to determine registration status.

For JSON-config agents: read the file (if it exists) and check for `mcpServers.cartopian`.

Mark each agent as **present — not registered**, **present — already registered**, or **not detected**.

---

## Stage 2 — Present status and ask

Present a status table to the operator, for example:

```
Agent           Status
──────────────  ─────────────────────────
Claude Code     present — not registered
Claude Desktop  present — already registered
Cursor          not detected
Windsurf        present — not registered
```

Ask:
- Which agents (detected as present and not registered) should Cartopian be registered with?
- Are there agents not in this list the operator wants to configure?

Do not modify any config without the operator explicitly selecting it.

---

## Stage 3 — Apply registrations

Apply the recipe for each agent the operator selected. Always confirm before writing to a config file.

### Claude Code

```bash
claude mcp add cartopian "$install_root/bin/cartopian-mcp" --scope user
```

Verify with `claude mcp list`. The entry must show `cartopian` pointing at the install root's `bin/cartopian-mcp`. No restart required — takes effect immediately.

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

Windsurf needs two things: the MCP server registered globally, and a per-workspace slash-command workflow that maps the operator's "use cartopian" phrase onto the MCP `use_cartopian` prompt. Cascade does not auto-surface MCP prompts as slash commands — only files under `.windsurf/workflows/` map to slash commands — so MCP registration alone is insufficient.

**Step A — Register the MCP server (global).** Same `mcpServers` structure as Claude Desktop. Read and merge the config file.

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

**Step B — Install the `/use-cartopian` workflow (per-workspace).** Cascade workflows are workspace-scoped, so this step is repeated for each workspace where the operator wants `/use-cartopian` to be available.

For each workspace the operator names, copy the template:

```text
<install_root>/templates/clients/windsurf/use-cartopian.md
```

into:

```text
<workspace>/.windsurf/workflows/use-cartopian.md
```

Create `.windsurf/workflows/` if it does not exist. Do not modify the template content during the copy — operators can tune it in place afterward. After the file is in place, the operator can type `/use-cartopian` from Cascade in that workspace to enter Cartopian PM mode; saying "use cartopian" in natural language is best-effort and depends on Cascade's prompt routing, so the slash form is the contract.

### Other agents

If the operator names an agent not covered above, provide the registration facts and direct them to that agent's MCP documentation:

- **Command:** `<install_root>/bin/cartopian-mcp` (Unix) or `<installRoot>\bin\cartopian-mcp.cmd` (Windows)
- **Transport:** stdio (newline-delimited JSON-RPC, no Content-Length headers)
- **Protocol version:** MCP 2024-11-05

---

## Stage 4 — Summarize

Report:

- Each agent already registered (no change made).
- Each agent newly registered in this run.
- Each agent that requires a restart before "use cartopian" will work (Claude Desktop, Cursor, Windsurf).
- For Windsurf: list each workspace where the `/use-cartopian` workflow file was installed (Step B), and remind the operator that this step must be repeated for any additional workspaces.
- Any agent requiring manual steps — summarize what the operator needs to do.

After registration, the operator can open any registered agent and say:

> use cartopian

The agent's MCP client will launch `cartopian-mcp`, which loads PM mode and routes to the first useful action. In Windsurf, the operator types `/use-cartopian` (slash-command form) from any workspace where Step B has been completed.
