# Skill: Install Cartopian

Walk an operator through installing (or upgrading) Cartopian using only their AI agent. The agent does the work; the operator only approves. No git knowledge required.

**Output:** Cartopian copied to the install root (default: `~/.cartopian/` on macOS / Linux / WSL, `%USERPROFILE%\.cartopian\` on native Windows — or wherever the operator's `--prefix` points), `bin/` and the platform-appropriate wrapper directory (`wrappers/bin` on Unix, `wrappers\ps1` on Windows) added to the user PATH, `cartopian --help` exits 0 on every supported platform (via the shipped `bin/cartopian.cmd` shim on native Windows), the agent wrappers (`cartopian-codex`, `cartopian-claude`, `cartopian-devin`, `cartopian-gemini`) resolve as bare commands, the Cartopian MCP server is registered with the operator's agent so they can say "use cartopian" from any directory, and `VERSION` at the install root records the installed git ref.

---

## Prerequisites

- Python **3.11+** on PATH. (macOS: the stock `/usr/bin/python3` is 3.9 — `brew install python@3.11` or any 3.11+ interpreter satisfies this. Windows: prefer the Python Launcher `py -3`; the bare name `python` is often Python 2 on legacy hosts.)
- On macOS / Linux / WSL: `curl` and `tar` (both standard).
- On native Windows: PowerShell 5.1+ (built in) with `Invoke-WebRequest`, plus `tar.exe` shipped with Windows 10 1803+ at `C:\Windows\System32\tar.exe`. Invoke that path explicitly — bare `tar` on PATH may resolve to Git-for-Windows' bundled tar, which fails to extract Windows-style paths.
- Internet access to `api.github.com` and `codeload.github.com`.

**Native Windows installs run from PowerShell, not Git Bash.** Git Bash on Windows is a hybrid environment: the shell looks Unix-like but `python3` is usually absent, `tar` on PATH may be Git's bundled tar (which fails on `C:\...` paths), and `curl` may hit `schannel` certificate errors against GitHub. If the operator is in Git Bash on Windows, tell them to re-launch this install in PowerShell.

If any prerequisite is missing, stop and tell the operator what to install before re-running.

---

## Steps

### Step 1 — Detect platform and shell

Detect:

- **OS family**: `uname -s` returns `Darwin` (macOS), `Linux` (Linux/WSL), or `MINGW*`/`MSYS*` (Git Bash on Windows — see special handling below). On native PowerShell, detect Windows reliably across 5.1 and 7+ via `[System.Environment]::OSVersion.Platform` (returns `Win32NT`), or `$env:OS -eq 'Windows_NT'`. Avoid `$PSVersionTable.OS` — it does not exist in Windows PowerShell 5.1.
- **Git Bash on Windows (MINGW/MSYS)** is *not* Linux for install purposes. `python3` is usually missing on PATH, bare `tar` often resolves to Git's bundled tar (which cannot extract to `C:\…` paths), and `curl` may fail with `schannel` certificate errors against GitHub. **Tell the operator to re-launch this install in PowerShell and stop.** Do not try to bridge the gap with `python3` shims or PATH rewrites — the native Windows path is the supported one.
- **Shell**: on Unix, read `$SHELL` — `zsh` → `~/.zshrc`, `bash` → `~/.bashrc`, anything else → tell the operator they'll need to add the PATH line themselves.

Branch the rest of the steps on platform.

### Step 2 — Verify Python 3.11+

Run `python3 --version` on Unix.

On Windows, **probe `py -3 --version` first** (the Python Launcher; this is the canonical way to invoke Python 3 on Windows because the bare name `python` is frequently bound to a legacy Python 2 install). If `py -3` is not installed, fall back to `python --version`. If neither yields 3.11+, stop and tell the operator to install Python 3.11+ before continuing.

Hold the resolved interpreter command (`python3` on Unix; `py -3` or, only as a fallback, `python` on Windows) for use in Step 5.

### Step 3 — Resolve the install ref

GET `https://api.github.com/repos/fidensa/cartopian/releases/latest`.

- On HTTP 200, extract `tag_name` (e.g. `v1.0.0`) and `tarball_url`. This is the **release path**.
- On HTTP 404 (no releases tagged yet), fall back to the `main` branch tarball: `https://api.github.com/repos/fidensa/cartopian/tarball/main`, ref = `main`.
- On any other failure, stop and surface the error.

If the operator passed an explicit ref (release tag or branch), use `https://api.github.com/repos/fidensa/cartopian/tarball/<ref>` instead.

Report the resolved ref to the operator before proceeding.

### Step 4 — Download and extract to a tempdir

Allocate a **fresh** tempdir for this install — do not reuse `$TMPDIR` / `$env:TEMP` directly, since those point at the shared parent temp directory and extracting there makes "single top-level directory" resolution ambiguous when prior junk is present.

**Unix:**

```bash
workdir="$(mktemp -d)"
curl -fsSL "<tarball_url>" -o "$workdir/cartopian.tar.gz"
tar -xzf "$workdir/cartopian.tar.gz" -C "$workdir"
```

**Windows (PowerShell):**

```powershell
$workdir = Join-Path ([System.IO.Path]::GetTempPath()) ("cartopian-install-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $workdir | Out-Null
Invoke-WebRequest -Uri "<tarball_url>" -OutFile "$workdir\cartopian.tar.gz" -UseBasicParsing
# Invoke the native Windows tar.exe by full path. Bare `tar` on PATH may
# resolve to Git-for-Windows' bundled tar, which cannot extract paths like
# `C:\Users\...` and fails with "Cannot connect to C: resolve failed".
$nativeTar = Join-Path $env:SystemRoot 'System32\tar.exe'
& $nativeTar -xzf "$workdir\cartopian.tar.gz" -C "$workdir"
```

GitHub tarballs extract to a single top-level directory named `<owner>-<repo>-<sha>` inside `$workdir`. Resolve that path; it becomes `$repo_root` for the next step. Keep `$workdir` around through Step 8 so cleanup can target it exactly.

### Step 5 — Run the in-tree installer

Run `scripts/install.py` in copy mode against the operator's install root.

**Unix:**

```bash
python3 "$repo_root/scripts/install.py" --mode copy --quiet
```

**Windows:**

```powershell
py -3 "$repo_root\scripts\install.py" --mode copy --quiet
```

Use the same interpreter command resolved in Step 2 — `py -3` is the canonical choice; substitute `python` only if Step 2 confirmed a Python 3.11+ behind the bare name.

If the operator wants a non-default install root, pass `--prefix <path>` as well.

If the installer exits non-zero, stop and surface its stderr to the operator.

After a successful install, hold the resolved install root as `$install_root` (or `$installRoot` on PowerShell) for the remaining steps:

- Default on Unix: `$HOME/.cartopian`.
- Default on Windows: `$HOME\.cartopian` (PowerShell expands `$HOME` to `%USERPROFILE%`).
- Otherwise: the absolute path passed via `--prefix`.

All later steps (`VERSION` marker, PATH patch, verify, summarize) reference `$install_root` rather than the literal `~/.cartopian`.

**Note on the installed entrypoint.** `bin/cartopian` itself is an extensionless Python script. The installer also writes a sibling shim, `bin/cartopian.cmd`, which forwards arguments to `bin/cartopian` via the system `python`. The shim is what makes the bare command `cartopian` resolve on native Windows (PowerShell finds `cartopian.cmd` via the default `PATHEXT`). On Unix the `.cmd` file is ignored and the shebang on `bin/cartopian` is what runs. Treat `cartopian` as a single cross-platform command for the rest of the runbook.

### Step 6 — Write the VERSION marker

Write the resolved ref from Step 3 to `$install_root/VERSION` (one line, no trailing whitespace beyond a final newline). The `check-for-updates` skill reads this file.

### Step 7 — Patch the user PATH

Add two entries to the operator's user PATH:

1. `$install_root/bin` — exposes the bare commands `cartopian` and `cartopian-mcp`. On Unix this resolves via the shebang on `bin/cartopian`; on native Windows PowerShell finds `bin\cartopian.cmd` via the default `PATHEXT`.
2. The platform-appropriate wrapper directory — exposes the bare agent CLI wrappers (`cartopian-codex`, `cartopian-claude`, `cartopian-devin`, `cartopian-gemini`) used by the PM handoff contract. The wrappers are platform-specific scripts: `$install_root/wrappers/bin` ships bash wrappers (Unix), `$installRoot\wrappers\ps1` ships PowerShell wrappers (Windows). Without this entry, `cartopian.toml` would have to reference each wrapper by absolute path.

**Unix (zsh / bash):**

Append to the operator's rc file (`~/.zshrc` for zsh, `~/.bashrc` for bash) only if the line is not already present. Use the literal expansion of `$install_root` for the default path, or the explicit `--prefix` path otherwise:

```
# Cartopian (default install root shown; substitute the --prefix path if used)
export PATH="$HOME/.cartopian/bin:$HOME/.cartopian/wrappers/bin:$PATH"
```

Tell the operator they need to `source` the rc file or open a new terminal for the change to take effect.

**Windows (native PowerShell):**

Update the user's persistent PATH via the registry-backed `[Environment]` API. Read the current value, add both `$installRoot\bin` and `$installRoot\wrappers\ps1` at the front only if absent, write it back:

```powershell
$bin     = Join-Path $installRoot "bin"             # $installRoot was set in Step 5
$wrapBin = Join-Path $installRoot "wrappers\ps1"    # PowerShell wrappers for Windows
$current = [Environment]::GetEnvironmentVariable("Path", "User")
$parts   = $current -split ";"
$prepend = @()
foreach ($p in @($bin, $wrapBin)) {
  if ($parts -notcontains $p) { $prepend += $p }
}
if ($prepend.Count -gt 0) {
  $new = ($prepend -join ";") + ";" + $current
  [Environment]::SetEnvironmentVariable("Path", $new, "User")
}
```

Tell the operator to open a new terminal — existing sessions won't see the change.

**Unrecognized shell:** print the exact lines and tell the operator where to add them.

### Step 8 — Clean up the tempdir

Remove `$workdir` from Step 4 (`rm -rf "$workdir"` on Unix, `Remove-Item -Recurse -Force $workdir` on Windows). The install is now self-contained in the install root — there is no source clone to maintain.

### Step 9 — Verify

Run the installed CLI entrypoint and the MCP server entrypoint by full path (the operator's current shell hasn't picked up the new PATH yet).

**CLI** — exits 0 on `--help`:

- Unix: `"$install_root/bin/cartopian" --help`
- Windows: `& "$installRoot\bin\cartopian.cmd" --help`

A non-zero exit code on Windows almost always means the `.cmd` shim resolved to an unsuitable Python interpreter (typically a stale Python 2 on PATH that the launcher fallback could not avoid). The shipped shim prefers `py -3`, so this should be rare — if it does fail, confirm `py -3 --version` reports 3.11+ from the operator's PowerShell before declaring the install broken.

**MCP server** — initialize handshake exits cleanly. The server speaks newline-delimited JSON-RPC on stdio; pipe one `initialize` request and read one response:

- Unix:
  ```bash
  echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
    | "$install_root/bin/cartopian-mcp"
  ```
- Windows (PowerShell):
  ```powershell
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' `
    | & "$installRoot\bin\cartopian-mcp.cmd"
  ```

The response must be a single JSON-RPC line containing `"name":"cartopian"` and `"protocolVersion":"2024-11-05"`. If either probe doesn't pass, surface the error and stop — do not report success.

Point the operator at the install verification checklist: `$install_root/protocol/INSTALL_VERIFICATION.md`.

### Step 10 — Register the MCP server with the operator's agent(s)

Run `skills/register-mcp.md`. The install root `$install_root` is already resolved from Step 5 — pass it so Stage 0 of that skill is skipped.

`register-mcp` detects which supported agents are present on the machine, shows which are already registered, and applies the appropriate recipe for each agent the operator selects. For Claude Code, Codex, Gemini, Devin, and Windsurf it does the full two-part hookup — registers the MCP server **and** installs a "use cartopian" trigger bridge (skill, prompt, or command) so the entry phrase routes to the `use_cartopian` prompt. Claude Desktop and Cursor are MCP-only (no local bridge mechanism); any other agent is handled via a generic fallback.

### Step 11 — Summarize

Print:

- Installed ref (from Step 3).
- Install root (`$install_root`, including any `--prefix` override).
- PATH entries added (or "already present") — `bin/` plus the wrappers directory.
- `cartopian --help` exit status and MCP `initialize` probe result (from Step 9).
- MCP server registered with, and trigger bridge installed for: <agents the operator chose in Step 10>.
- **Entry point**: tell the operator, in plain language, how to enter Cartopian PM mode from each agent they configured — say "use cartopian" in Claude Code and Devin for Terminal, or type `/use-cartopian` in Codex, Gemini, and Windsurf (Claude Code also accepts the slash form). From any directory, that loads the `use_cartopian` prompt, which briefs the agent on the available skills and routes to the first useful action (`start_session` if projects exist, `init_project` if not).
- Next-step suggestion if the operator wants to proceed in this same conversation: `init workspace` if `$install_root/cartopian.toml` is a freshly-seeded default; otherwise `init project`.

---

## Re-running for upgrade

This skill is idempotent. Re-running it fetches the current latest release (or the explicit ref the operator provides), copies it over `$install_root/`, and refreshes tool-shipped paths. Operator-owned files (`cartopian.toml`, `projects.json`) are preserved by `scripts/install.py`. If the operator originally installed with a non-default `--prefix`, re-running must use the same `--prefix` — otherwise the agent will write a second install at the default root.

Once the operator is already on a working install, prefer the `check-for-updates` skill — it compares `$install_root/VERSION` against the latest release and only invokes this skill if an upgrade is warranted.
