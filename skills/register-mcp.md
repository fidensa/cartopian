# Skill: Register MCP

Register or re-register Cartopian's MCP server (`cartopian-mcp`) with one or more AI agents on the operator's machine. Run this after a fresh install to cover additional agents, or whenever a new agent is added to the operator's workflow.

**Output:** for each selected agent, `cartopian-mcp` is registered in its MCP config **and** a "use cartopian" trigger bridge (skill or command) is installed so the entry phrase reads the authoritative `cartopian://skills/use_cartopian` resource. The operator can then enter Cartopian PM mode from any registered agent in any directory after any required restart.

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
| opencode | `opencode` on PATH | `~/.config/opencode/opencode.json` or `opencode.jsonc` | `%USERPROFILE%\.config\opencode\opencode.json` or `opencode.jsonc` (XDG on every platform; **Windows unverified**) |
| Hermes | `hermes` on PATH | the file `hermes config path` prints (profile-scoped; default `~/.hermes/config.yaml`) | the file `hermes config path` prints (**Windows unverified**) |

For Claude Code: run `claude mcp list` and check for a `cartopian` entry to determine registration status.

For Codex: run `codex mcp list` and check for a `cartopian` entry to determine registration status. (The underlying store is `~/.codex/config.toml` under `[mcp_servers.cartopian]`, but the CLI is the supported interface.)

For Gemini: run `gemini mcp list` and check for a `cartopian` entry to determine registration status. (The underlying store is `~/.gemini/settings.json` under `mcpServers.cartopian`, but the CLI is the supported interface.)

For opencode: run `opencode mcp list` and check for a `cartopian` entry — it prints each server's resolved command and connect status without any model call, so it is the first check after any registration change. (The underlying store is the global `opencode.json`/`opencode.jsonc` pair under the top-level `mcp` key; note opencode's schema is **not** the `mcpServers` shape the other JSON agents use.)

For Hermes: run `hermes config get --json mcp_servers.cartopian` — exit 0 with the entry means registered; exit 1 with "Config key not set" means not registered. The store is the single YAML file `hermes config path` prints, under the top-level `mcp_servers` key; the `hermes config` CLI is the supported interface (never edit or parse the YAML directly — Hermes owns file fidelity).

For JSON-config agents: read the file (if it exists) and check for `mcpServers.cartopian`.

For agents that are **already registered** and use a trigger bridge (Claude Code, Codex, Gemini, Devin, Windsurf, opencode, Hermes), also check whether the *bridge itself* is current: compare the installed bridge file (per-agent paths are in Stage 3) byte-for-byte against its source template under `<install_root>/templates/clients/<agent>/`. A missing bridge file, or one that differs from the template, is **drifted** — this is the common case after a Cartopian upgrade changed the bridge wording, because re-registration only ever installs a bridge for a *newly* registered agent. (Skip this comparison for Claude Desktop and Cursor — they have no bridge.)

Mark each agent as one of:

- **present — not registered** — MCP server not yet configured.
- **present — already registered, bridge current** — MCP server configured and the installed bridge matches the template; nothing to do.
- **present — already registered, bridge update available** — MCP server configured, but the installed bridge is missing or differs from the current template.
- **not detected**.

---

## Stage 2 — Present status and ask

Present a status table to the operator, for example:

```
Agent           Status
──────────────  ─────────────────────────────────────────────
Claude Code     present — not registered
Codex           present — already registered, bridge update available
Gemini          present — already registered, bridge current
Devin           not detected
Windsurf        present — not registered
Claude Desktop  not detected
Cursor          not detected
```

Ask:
- Which agents (detected as present and **not registered**) should Cartopian be registered with?
- Which agents marked **bridge update available** should have their trigger bridge refreshed? This re-copies the current bridge template over the installed copy; it does **not** touch the already-working MCP registration.
- If the requested client is not in this list, report it as unsupported and
  stop for that client. Do not accept a caller-supplied executable, config
  path, bridge destination, or generic registration recipe.

Fold both selections into the same confirmation so an operator upgrading Cartopian is asked **once**, not twice — and offer a select-all so every drifted bridge can be refreshed in one step. Do not modify any config without the operator explicitly selecting it. Agents marked **bridge current** need no action — say so and move on.

---

## Stage 3 — Apply registrations

Apply the recipe for each agent the operator selected. Always confirm before writing to a config file. Run the parts that match *how* the agent was selected:

- Selected as **not registered** → run **both** Part A and Part B.
- Selected as **bridge update available** → run **Part B only**. Part A is already done; do not re-register the MCP server or rewrite its config — just re-copy the bridge template over the installed file.

**Every recipe has two parts:**

- **Part A — register the MCP server** so the `cartopian` tools, prompt, and resources are reachable.
- **Part B — install the "use cartopian" trigger bridge.** Registering the MCP server alone is *not* enough: the supported bridge clients need a native skill or command for the entry phrase. Each bridge directly tells its host to read the authoritative `cartopian://skills/use_cartopian` resource with that host's MCP resource reader. The bridge bodies ship as templates under `<install_root>/templates/clients/<agent>/` — copy them verbatim into the agent's command/skill directory. Create any missing parent directories. Do not edit the template content during the copy; operators can tune it in place afterward.

The named agents below (Claude Code, Codex, Gemini, Devin, Windsurf, opencode, Hermes) get both parts. Claude Desktop and Cursor are MCP-only — they have no general-purpose local command/skill mechanism to bridge onto, so the operator triggers Cartopian there by invoking the `use_cartopian` MCP prompt directly from the client's prompt picker.

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

**Part B — install the trigger bridge.** Codex CLI uses the modern **Skills** framework. Copy the bridge skill into Codex's global skills directory:

```bash
mkdir -p ~/.codex/skills/use-cartopian
cp "$install_root/templates/clients/codex/skills/use-cartopian/SKILL.md" \
   ~/.codex/skills/use-cartopian/SKILL.md
```

**Windows (PowerShell):**

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.codex\skills\use-cartopian" | Out-Null
Copy-Item "$installRoot\templates\clients\codex\skills\use-cartopian\SKILL.md" `
  "$env:USERPROFILE\.codex\skills\use-cartopian\SKILL.md" -Force
```

After a restart, the operator can enter PM mode by saying "use cartopian" (triggering description matching) or typing `$use-cartopian` (or `/use-cartopian` if integrated in the slash auto-complete).

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

### opencode

opencode's config schema is a genuine third format: the server table lives under a top-level `mcp` key (not `mcpServers`), each entry carries a required `"type"` discriminator, and `command` is an **array**. Both `opencode.json` and `opencode.jsonc` load if present and deep-merge, with `opencode.jsonc` loading later and winning same-key conflicts — and either filename may contain comments or trailing commas, which opencode's lenient parser accepts silently. A file that fails to parse is silently ignored by opencode, so verify with `opencode mcp list` after every change.

**Part A — register the MCP server.** Merge the entry into the global config (prefer the pair member that already exists; when both exist, edit `opencode.jsonc` because it loads later; when neither exists, create `opencode.json`), preserving every existing key:

**macOS/Linux:** `~/.config/opencode/opencode.json` (or `opencode.jsonc`)
**Windows:** `%USERPROFILE%\.config\opencode\opencode.json` — opencode uses the XDG layout on every platform, including Windows (**unverified on native Windows**; check `opencode mcp list` after writing)

```json
{
  "mcp": {
    "cartopian": {
      "type": "local",
      "command": ["<install_root>/bin/cartopian-mcp"],
      "enabled": true,
      "timeout": 600000
    }
  }
}
```

On Windows, use the `.cmd` shim (`<installRoot>\\bin\\cartopian-mcp.cmd`) inside the array. The `timeout` is opencode's per-server tool-call **idle** window in milliseconds (see Stage 4). If the operator has set `$OPENCODE_CONFIG_DIR`, write to that directory's pair instead; if `$OPENCODE_CONFIG` names an explicit file, that exact file is the only target.

Verify with `opencode mcp list` — it must show `cartopian` connected and pointing at the install root's binary. Restart any running opencode sessions before the server is available.

**Part B — install the `/use-cartopian` command bridge.** Copy the command template into opencode's global commands directory (or `$OPENCODE_CONFIG_DIR/commands/` when that variable is set — `$OPENCODE_CONFIG` never redirects command discovery):

```bash
mkdir -p ~/.config/opencode/commands
cp "$install_root/templates/clients/opencode/commands/use-cartopian.md" \
   ~/.config/opencode/commands/use-cartopian.md
```

**Windows (PowerShell):**

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.config\opencode\commands" | Out-Null
Copy-Item "$installRoot\templates\clients\opencode\commands\use-cartopian.md" `
  "$env:USERPROFILE\.config\opencode\commands\use-cartopian.md" -Force
```

After a restart the operator types `/use-cartopian` in the opencode TUI or IDE. (opencode also surfaces the MCP prompt as `/use_cartopian` — underscore, keyed by the raw prompt name — but the hyphenated command bridge is the supported entry.)

### Hermes

Hermes's config is a single profile-scoped YAML file that the `hermes config`
CLI owns. **Never edit or parse the YAML directly, and never use `hermes mcp
add` or `hermes mcp remove` for automation** — `mcp add` probes the network,
prompts on three separate paths, and exits 0 whether or not it saved; `mcp
remove` prompts on stdin. The promptless per-key `config set` / `config get
--json` / `config unset` commands are the supported interface.

**Part A — register the MCP server.** Run these five commands **in this
order** — `enabled` last, so an interrupted sequence can never leave a
partially configured entry active (a half-written entry stays inert and
re-running the sequence converges). When **repairing an existing
Cartopian-owned entry** (drifted values), first run
`hermes config set mcp_servers.cartopian.enabled false`, then the five
commands — otherwise an interruption could leave the still-enabled entry
active with partially updated fields. `<hermes home>` is the parent directory
of the file `hermes config path` prints:

```bash
hermes config set mcp_servers.cartopian.command "$install_root/bin/cartopian-mcp"
hermes config set mcp_servers.cartopian.timeout 3900
hermes config set mcp_servers.cartopian.env.CARTOPIAN_MCP_HOST hermes
hermes config set mcp_servers.cartopian.env.CARTOPIAN_HERMES_HOME "<hermes home>"
hermes config set mcp_servers.cartopian.enabled true
```

**Windows (PowerShell) — use the `.cmd` shim** for the `command` value
(`$installRoot\bin\cartopian-mcp.cmd`); the other four commands are identical
(**unverified on native Windows**).

If an `mcp_servers.cartopian` entry with a *different* command already exists,
stop and ask the operator — never overwrite a foreign entry. Stop and ask
likewise if the existing entry carries any field outside the five keys above
(`url` especially: Hermes prefers `url` over `command`, so such an entry
connects elsewhere while looking registered, and re-running the `config set`
sequence can only add or overwrite keys, never remove one). The two `env`
markers matter: Hermes sends only the SDK-default MCP `clientInfo` ("mcp"), so
`CARTOPIAN_MCP_HOST` is how Cartopian identifies the host, and Hermes's
filtered subprocess environment never passes `HERMES_HOME` through, so
`CARTOPIAN_HERMES_HOME` is how capability resolution finds the registering
profile's config. The `timeout` sizing rationale is in Stage 4.

Verify with `hermes config get mcp_servers.cartopian` (prints the whole block)
or `hermes mcp test cartopian`. Start a new Hermes session before the server
is available — `config set` does not hot-load servers (`/reload-mcp` exists
in-session for value-only changes, but command/env changes need the new
session).

**Part B — install the trigger bridge.** Hermes discovers file-dropped skill
bundles with no install step. Copy the bundle into the profile-scoped skills
directory (`<config dir>/skills`, sibling of the config file):

```bash
skills_dir="$(dirname "$(hermes config path)")/skills"
mkdir -p "$skills_dir/cartopian/use-cartopian"
cp "$install_root/templates/clients/hermes/skills/DESCRIPTION.md" \
   "$skills_dir/cartopian/DESCRIPTION.md"
cp "$install_root/templates/clients/hermes/skills/use-cartopian/SKILL.md" \
   "$skills_dir/cartopian/use-cartopian/SKILL.md"
```

Confirm with `hermes skills list` — the skill appears as `source: local,
status: enabled`. The operator then says "use cartopian" in a Hermes session
(or preloads the skill with `hermes -s use-cartopian`).

**Unregistering.** The guarded automated path is
`python3 <install_root>/scripts/install.py --unregister hermes` — it removes
the `mcp_servers.cartopian` entry via a promptless `hermes config unset`,
preserving foreign or unreadable entries with an instruction instead of
touching them. The manual equivalent is
`hermes config unset mcp_servers.cartopian` (verify with
`hermes config get mcp_servers.cartopian` first that the entry's `command`
is Cartopian's). Never use `hermes mcp remove` (it prompts on stdin).

**Gateway boundary.** A direct-message Slack acceptance scenario has passed on
macOS: the conversation entered PM mode, dispatched and waited through MCP,
and received the terminal handoff result. Hermes also rendered its own generic
tool-activity message for calls including `dispatch` and `wait_handoff`.
Cartopian does **not** provide granular assignee-output or report-progress
events during the blocking wait; that host-neutral enhancement is a backlog
item in the repository's `ROADMAP.md` and does not gate the accepted
integration behavior.

Slack setup remains Hermes-owned. Use Hermes's generated gateway manifest and
setup/check commands; Cartopian never handles Slack credentials. The tested
direct-message path required the `chat:write` and `im:history` bot scopes and
the `message.im` bot event. Reinstall an existing Slack app after changing its
scopes or event subscriptions. Other gateway adapters and Slack conversation
types are not claimed by this scenario. Hermes support remains
**experimental** only until native Windows acceptance closes; the evidence is
recorded in the repository's `tests/acceptance/hermes-macos.md`.

### Other agents

Agents not covered above are outside the coordinated registration vocabulary.
Do not construct or execute a generic registration recipe, accept a
caller-selected executable or destination, or report that Cartopian registered
the unsupported client. Direct the operator to that client's own MCP
documentation and record Cartopian registration as unsupported/not performed.

---

## Stage 4 — Raise the tool-call wait ceiling

Registration alone is not enough to run handoffs. Cartopian waits for an assignee by holding one `tools/call` open until the report file lands — up to `roles.<role>.timeout`, protocol default `60m`. Every host caps a single tool call, and **some hosts cap it well below that default**. When the cap is the smaller number the wait is killed mid-handoff, the assignee keeps working unobserved, and the PM sees a transport error instead of a protocol outcome.

`cartopian dispatch` refuses to launch when the role timeout does not fit the host's ceiling, so an unconfigured host surfaces as a fail-closed guard at dispatch rather than a dead wait. Raising the ceiling here is what makes handoffs work at all on the affected hosts.

Apply the setting for each agent the operator registered, sizing it above the largest `roles.<role>.timeout` any governed project uses (`3900` seconds comfortably clears the `60m` default):

| Agent | Setting | Default | Where |
| --- | --- | --- | --- |
| Codex | `tool_timeout_sec` (seconds) | **300** — below the protocol default | `[mcp_servers.cartopian]` in `~/.codex/config.toml` |
| Gemini | `timeout` (milliseconds) | **600000** — below the protocol default | `mcpServers.cartopian` in `~/.gemini/settings.json` |
| Claude Code | `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT` (milliseconds; `0` disables) | **1800000** idle — below the protocol default | the environment Claude Code launches with |
| opencode | `timeout` (milliseconds) | **60000 idle** — but progress notifications reset it | `mcp.cartopian` in the global `opencode.json`/`opencode.jsonc` |
| Hermes | `timeout` (seconds) | **300** hard wall clock per call — below the protocol default; nothing resets it | `mcp_servers.cartopian` via `hermes config set` |
| Claude Desktop, Cursor, Windsurf, Devin | no documented per-server tool-call timeout | unknown | see the note below |

Codex — add the key under the existing entry, then restart Codex:

```toml
[mcp_servers.cartopian]
command = "/path/to/.cartopian/bin/cartopian-mcp"
tool_timeout_sec = 3900
```

Gemini — add the key under the existing entry in `~/.gemini/settings.json`, then restart Gemini:

```json
"cartopian": { "command": "/path/to/.cartopian/bin/cartopian-mcp", "timeout": 3900000 }
```

Claude Code — its wall-clock ceiling (`MCP_TOOL_TIMEOUT`, ~28h when unset) is fixed and is never extended by progress. Its stdio idle window defaults to 30 minutes, but documented progress traffic resets that idle check; Cartopian reports the raw idle value while using the fixed wall clock as the sustainable wait budget for its progress-bearing canonical wait. If the connected client does not request a progress channel, raise or disable the idle setting before relying on a longer wait.

opencode — the registration recipe above already carries `"timeout": 600000`. Unlike the wall-clock caps on other hosts, opencode's is an **idle** window that a progress notification resets, and Cartopian's blocking waits heartbeat every 5 seconds — so the value bounds silence from non-heartbeating tools, not handoff length, and 10 minutes needs no upward sizing for long roles. One acknowledged cost: the same timeout governs catalog/list operations, so against a hung server `opencode mcp list` and TUI startup enumeration wait it out before failing.

Hermes — the registration recipe above already carries `timeout: 3900` (seconds). Unlike opencode's resettable idle window, Hermes's `timeout` is a **hard wall-clock total per tool call** with no progress callback — nothing resets it, so heartbeats buy nothing and the value must clear the *full* role timeout in one terminal wait. 3,900 s = the 3,600 s protocol default + 300 s response/serialization margin. Roles configured above ~65 minutes still refuse cleanly at dispatch; raise the entry's timeout (`hermes config set mcp_servers.cartopian.timeout <seconds>`, then a new session or `/reload-mcp`) or lower the role timeout. One acknowledged cost: the same per-call ceiling governs every Cartopian tool call in that session.

**Hosts with no documented setting.** Do not guess a value and do not assume a long blocking call survives. Cartopian resolves an unrecognized host to an *unknown* budget, which fails the dispatch gate by design. On such a host, either lower `roles.<role>.timeout` to a duration confirmed to survive, or dispatch that role manually and monitor the report path — never fall back to periodic status checks.

Verify the result from inside a session on that host:

```
cartopian host-capability --role <role> --project <project-path>
```

`fits: true` means a full-length wait will survive. `fits: false` carries the mismatch and its remedies in `refusal`.

---

## Stage 5 — Summarize

Report, per agent the operator selected:

- Whether the MCP server was already registered (no change) or newly registered this run (Part A).
- Whether the trigger bridge was installed fresh, **refreshed from a drifted copy**, or left as-is because it was already current (Part B) — and **how the operator invokes it**:

  | Agent | Entry phrase / command |
  | --- | --- |
  | Claude Code | say "use cartopian" (skill) or `/use-cartopian` |
  | Codex | say "use cartopian" (skill) or `/use-cartopian` / `$use-cartopian` |
  | Gemini | `/use-cartopian` |
  | Devin for Terminal | say "use cartopian" (skill trigger) or `/use-cartopian` |
  | Windsurf | `/use-cartopian` |
  | opencode | `/use-cartopian` |
  | Hermes | say "use cartopian" (skill) — or preload with `hermes -s use-cartopian` |
  | Claude Desktop / Cursor | invoke the `use_cartopian` MCP prompt from the client's prompt picker (MCP-only — no bridge) |

- Each agent that requires a restart before the bridge is live (Codex, Gemini, Windsurf, Devin, Claude Desktop, Cursor, opencode, Hermes — for Hermes a new session, or `/reload-mcp` for value-only config changes). Claude Code needs no restart.
- Any agent requiring manual steps — summarize what the operator needs to do.

Once an agent has both parts and any required restart is complete, the operator opens it in any directory and uses the entry phrase/command above. The installed bridge reads the `use_cartopian` resource, which enters PM mode through registry-first project selection and routes to `start_session` for a selected project or `init_project` when the registry is empty.
