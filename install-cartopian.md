# Skill: Install Cartopian

Walk an operator through installing (or upgrading) Cartopian using only their AI agent. The agent does the work; the operator only approves. No git knowledge required.

**Output:** Cartopian copied to the install root (default: `~/.cartopian/` on macOS / Linux / WSL, `%USERPROFILE%\.cartopian\` on native Windows — or wherever the operator's `--prefix` points), `bin/` and the platform-appropriate wrapper directory (`wrappers/bin` on Unix, `wrappers\ps1` on Windows) added to the user PATH, `cartopian --help` exits 0 on every supported platform (via the shipped `bin/cartopian.cmd` shim on native Windows), the agent wrappers (`cartopian-codex`, `cartopian-claude`, `cartopian-devin`, `cartopian-gemini`) resolve as bare commands, the Cartopian MCP server is registered with the operator's agent so they can say "use cartopian" from any directory, and `VERSION` at the install root records the installed git ref.

**Design rule for this runbook: every command is a single line and runs verbatim in PowerShell, cmd, Git Bash, zsh, and bash.** All multi-step work — resolving the release, downloading, extracting, writing `VERSION`, patching the user PATH — happens inside `scripts/install.py` on the Python standard library. Never wrap these commands in `powershell -Command`: there is nothing PowerShell-specific left to run, and a bash→PowerShell handoff mangles quoting, here-strings, and POSIX-looking paths (Git Bash rewrites them via MSYS path conversion). If some other task ever forces you to call PowerShell from Git Bash, use `$env:USERPROFILE` (which PowerShell expands itself, after bash hands off) — never `$HOME` — inside the command string.

---

## Prerequisites

- Python **3.11+** on PATH. (macOS: the stock `/usr/bin/python3` is 3.9 — `brew install python@3.11` or any 3.11+ interpreter satisfies this. Windows: prefer the Python Launcher `py -3`; the bare name `python` is often Python 2 on legacy hosts. `py -3` resolves from PowerShell, cmd, **and Git Bash**.)
- Internet access to `api.github.com` (the tarball download follows GitHub's redirect to `codeload.github.com`).

No `curl`, `tar`, or PowerShell cmdlets are required on any platform — download and extraction are handled by the installer via the Python standard library. This deliberately sidesteps the classic Windows traps: Git Bash's bundled `tar` cannot extract to `C:\` paths, and Git Bash's `curl` can hit `schannel` certificate errors against GitHub.

**Git Bash on Windows is a supported driver shell for this install.** Run the same single-line commands documented below with `py -3`; do not tunnel them through `powershell -Command`, and do not substitute Git Bash's own `python3`/`tar`/`curl`.

If any prerequisite is missing, stop and tell the operator what to install before re-running.

---

## Steps

### Step 1 — Verify Python and pick the interpreter command

- Unix (macOS / Linux / WSL): `python3 --version`
- Windows (PowerShell, cmd, or Git Bash): probe `py -3 --version` first (the Python Launcher is the canonical way to invoke Python 3 on Windows because the bare name `python` is frequently bound to a legacy Python 2 install). If the launcher is missing, fall back to `python --version`.

Require 3.11+. If no interpreter satisfies it, stop and tell the operator to install Python 3.11+ before continuing.

Hold the resolved interpreter command as `<py>` for the remaining steps: `python3` on Unix; `py -3` (or, only as a confirmed fallback, `python`) on Windows.

### Step 2 — Resolve the installer script

Two cases:

**A. Upgrade, installer already shipped.** Installs made at v1.3.27 or later carry the installer at `<install_root>/scripts/install.py`. Check for it:

- Unix / Git Bash: `ls "$HOME/.cartopian/scripts/install.py"`
- PowerShell: `Test-Path "$env:USERPROFILE\.cartopian\scripts\install.py"`

If present, use that path in Step 3 and skip the download below.

**B. Fresh install, or upgrade from an older install.** Download the installer — a single file — via the GitHub contents API. This one line is identical in every shell (only `<py>` varies):

```
<py> -c "import base64,json,urllib.request;d=json.load(urllib.request.urlopen('https://api.github.com/repos/fidensa/cartopian/contents/scripts/install.py'));open('cartopian-install.py','wb').write(base64.b64decode(d['content']))"
```

It writes `cartopian-install.py` into the current directory (Step 4 deletes it). To bootstrap from a pinned ref instead of `main`, append `?ref=<tag>` to the URL.

If (and only if) that line fails with `CERTIFICATE_VERIFY_FAILED` — python.org macOS framework builds ship without a CA bundle until the user runs `Install Certificates.command` — use the curl pipe instead (Unix only; Windows loads the system cert store and does not hit this):

```
curl -fsSL https://api.github.com/repos/fidensa/cartopian/contents/scripts/install.py | python3 -c "import base64,json,sys;open('cartopian-install.py','wb').write(base64.b64decode(json.load(sys.stdin)['content']))"
```

The bootstrap is the only step exposed to this: once `cartopian-install.py` is on disk, the installer carries its own CA-bundle fallback for all of its downloads.

### Step 3 — Run the installer (one command)

Run the installer script resolved in Step 2 with `--from-github`:

- Unix: `python3 "$HOME/.cartopian/scripts/install.py" --from-github --patch-path`
- PowerShell / cmd: `py -3 "%USERPROFILE%\.cartopian\scripts\install.py" --from-github --patch-path` (PowerShell also accepts `"$env:USERPROFILE\.cartopian\scripts\install.py"`)
- Git Bash on Windows: `py -3 ~/.cartopian/scripts/install.py --from-github --patch-path` (MSYS converts the direct path argument to `C:\Users\...` correctly — path conversion only breaks when paths are embedded inside a `powershell -Command` string, which this runbook never does)
- Bootstrapped in Step 2B: `<py> cartopian-install.py --from-github --patch-path`

What the flags do:

- `--from-github` — resolves the latest release via `api.github.com` (falling back to the `main` branch when no release is tagged), downloads the tarball, extracts it with Python's `tarfile` into a fresh tempdir, installs from it in copy mode, writes the resolved ref to `<install_root>/VERSION`, and deletes the tempdir. To pin a specific release, add `--ref <tag>`.
- `--patch-path` — idempotently adds `<install_root>/bin` and the platform wrapper directory (`wrappers/bin` on Unix, `wrappers\ps1` on Windows) to the user PATH: the registry-backed user PATH on Windows, the login shell's rc file (`~/.zshrc` or `~/.bashrc`) on Unix. For an unrecognized shell it prints the exact line for the operator to add manually.
- `--prefix <path>` — non-default install root; add it if the operator wants one, and reuse the same `--prefix` on every future upgrade.
- `--quiet` — suppress the per-action log; keep it off so the operator sees what was copied, preserved, and patched.

The installer preserves operator-owned files (`cartopian.toml`, `projects.json`) on upgrade, and ships itself to `<install_root>/scripts/install.py` — so the *next* upgrade is Step 2A: one command, no bootstrap.

If the installer exits non-zero, stop and surface its stderr to the operator.

Tell the operator to open a new terminal (or `source` the rc file on Unix) for the PATH change to take effect.

**Note on the installed entrypoint.** `bin/cartopian` itself is an extensionless Python script. The installer also writes a sibling shim, `bin/cartopian.cmd`, which forwards arguments to `bin/cartopian` via the system `python`. The shim is what makes the bare command `cartopian` resolve on native Windows (PowerShell finds `cartopian.cmd` via the default `PATHEXT`). On Unix the `.cmd` file is ignored and the shebang on `bin/cartopian` is what runs. Treat `cartopian` as a single cross-platform command for the rest of the runbook.

### Step 4 — Clean up the bootstrap (Step 2B only)

If Step 2B downloaded `cartopian-install.py`, delete it — same line, every shell:

```
<py> -c "import os;os.remove('cartopian-install.py')"
```

(The installed copy at `<install_root>/scripts/install.py` is the durable one.)

### Step 5 — Verify

Run the installed CLI entrypoint and the MCP server entrypoint by full path (the operator's current shell hasn't picked up the new PATH yet). Substitute the `--prefix` root if one was used.

**CLI** — exits 0 on `--help`:

- Unix: `"$HOME/.cartopian/bin/cartopian" --help`
- PowerShell: `& "$env:USERPROFILE\.cartopian\bin\cartopian.cmd" --help`
- Git Bash on Windows: `~/.cartopian/bin/cartopian.cmd --help`

A non-zero exit code on Windows almost always means the `.cmd` shim resolved to an unsuitable Python interpreter (typically a stale Python 2 on PATH that the launcher fallback could not avoid). The shipped shim prefers `py -3`, so this should be rare — if it does fail, confirm `py -3 --version` reports 3.11+ before declaring the install broken.

**MCP server** — initialize handshake exits cleanly. The server speaks newline-delimited JSON-RPC on stdio; pipe one `initialize` request and read one response (single line per shell):

- Unix: `echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | "$HOME/.cartopian/bin/cartopian-mcp"`
- PowerShell: `'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | & "$env:USERPROFILE\.cartopian\bin\cartopian-mcp.cmd"`
- Git Bash on Windows: `echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | ~/.cartopian/bin/cartopian-mcp.cmd`

The response must be a single JSON-RPC line containing `"name":"cartopian"` and `"protocolVersion":"2024-11-05"`. If either probe doesn't pass, surface the error and stop — do not report success.

Also confirm `<install_root>/VERSION` contains the ref the installer reported in Step 3:

- Unix / Git Bash: `cat "$HOME/.cartopian/VERSION"`
- PowerShell: `Get-Content "$env:USERPROFILE\.cartopian\VERSION"`

Point the operator at the install verification checklist: `<install_root>/protocol/INSTALL_VERIFICATION.md`.

### Step 6 — Register the MCP server with the operator's agent(s)

Run `skills/register-mcp.md`. The install root is already resolved — pass it so Stage 0 of that skill is skipped.

`register-mcp` detects which supported agents are present on the machine, shows which are already registered, and applies the appropriate recipe for each agent the operator selects. For Claude Code, Codex, Gemini, Devin, and Windsurf it does the full two-part hookup — registers the MCP server **and** installs a "use cartopian" trigger bridge (skill or command) so the entry phrase reads the authoritative `cartopian://skills/use_cartopian` resource. Claude Desktop and Cursor are MCP-only (no local bridge mechanism); any other agent is handled via a generic fallback.

### Step 7 — Summarize

Print:

- Installed ref (from the installer's `VERSION` line).
- Install root (including any `--prefix` override).
- PATH entries added (or "already present") — `bin/` plus the wrappers directory.
- `cartopian --help` exit status and MCP `initialize` probe result (from Step 5).
- MCP server registered with, and trigger bridge installed for: <agents the operator chose in Step 6>.
- **Entry point**: tell the operator, in plain language, how to enter Cartopian PM mode from each agent they configured — say "use cartopian" in Claude Code, Codex, and Devin for Terminal (or type `/use-cartopian` or `$use-cartopian` in Codex, Gemini, and Windsurf). After any required client restart, the installed bridge reads the `use_cartopian` resource and begins registry-first project selection. MCP-only clients use the `use_cartopian` prompt from their prompt picker.
- Next-step suggestion if the operator wants to proceed in this same conversation: `init workspace` if `<install_root>/cartopian.toml` is a freshly-seeded default; otherwise `init project`.

---

## Re-running for upgrade

This skill is idempotent. Re-running it fetches the current latest release (or the explicit `--ref` the operator provides), copies it over the install root, and refreshes tool-shipped paths. Operator-owned files (`cartopian.toml`, `projects.json`) are preserved by `scripts/install.py`. If the operator originally installed with a non-default `--prefix`, re-running must use the same `--prefix` — otherwise the installer will write a second install at the default root.

Because the installer ships itself into the install root, an upgrade normally needs exactly one command (Step 2A + Step 3) — from any shell on any platform.

Once the operator is already on a working install, prefer the `check-for-updates` skill — it compares `<install_root>/VERSION` against the latest release and only invokes this skill if an upgrade is warranted.
