# Skill: Install Cartopian

Walk an operator through installing (or upgrading) Cartopian using only their AI agent. The agent does the work; the operator only approves. No git knowledge required.

**Output:** Cartopian copied to the install root (default: `~/.cartopian/` on macOS / Linux / WSL, `%USERPROFILE%\.cartopian\` on native Windows — or wherever the operator's `--prefix` points), `bin/` added to the user PATH, `cartopian --help` exits 0 on every supported platform (via the shipped `bin/cartopian.cmd` shim on native Windows), and `VERSION` at the install root records the installed git ref.

---

## Prerequisites

- Python **3.11+** on PATH. (macOS: the stock `/usr/bin/python3` is 3.9 — `brew install python@3.11` or any 3.11+ interpreter satisfies this.)
- On macOS / Linux / WSL: `curl` and `tar` (both standard).
- On native Windows: PowerShell 5.1+ (built in) with `Invoke-WebRequest`, plus `tar` (ships with Windows 10 1803+ as `C:\Windows\System32\tar.exe`). Confirm `tar --version` resolves before starting.
- Internet access to `api.github.com` and `codeload.github.com`.

If any prerequisite is missing, stop and tell the operator what to install before re-running.

---

## Steps

### Step 1 — Detect platform and shell

Detect:

- **OS family**: `uname -s` returns `Darwin` (macOS), `Linux` (Linux/WSL), or `MINGW*`/`MSYS*` (git-bash on Windows — treat as Linux for the install commands but warn). On native PowerShell, detect Windows reliably across 5.1 and 7+ via `[System.Environment]::OSVersion.Platform` (returns `Win32NT`), or `$env:OS -eq 'Windows_NT'`. Avoid `$PSVersionTable.OS` — it does not exist in Windows PowerShell 5.1.
- **Shell**: on Unix, read `$SHELL` — `zsh` → `~/.zshrc`, `bash` → `~/.bashrc`, anything else → tell the operator they'll need to add the PATH line themselves.

Branch the rest of the steps on platform.

### Step 2 — Verify Python 3.11+

Run `python3 --version` (Unix) or `python --version` (Windows). If the major.minor is below 3.11, stop and tell the operator to install Python 3.11+ before continuing.

Hold the interpreter command (`python3` or `python`) for use in Step 5.

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
tar -xzf "$workdir\cartopian.tar.gz" -C "$workdir"   # tar ships with Windows 10+
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
python "$repo_root\scripts\install.py" --mode copy --quiet
```

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

Add `$install_root/bin` to the operator's user PATH. On Unix this lets the bare command `cartopian` resolve via the shebang on `bin/cartopian`. On native Windows it lets PowerShell resolve `cartopian` to the shipped `bin/cartopian.cmd` shim, which forwards to the Python entrypoint.

**Unix (zsh / bash):**

Append to the operator's rc file (`~/.zshrc` for zsh, `~/.bashrc` for bash) only if the line is not already present. Use the literal expansion of `$install_root` for the default path, or the explicit `--prefix` path otherwise:

```
# Cartopian (default install root shown; substitute the --prefix path if used)
export PATH="$HOME/.cartopian/bin:$PATH"
```

Tell the operator they need to `source` the rc file or open a new terminal for the change to take effect.

**Windows (native PowerShell):**

Update the user's persistent PATH via the registry-backed `[Environment]` API. Read the current value, add `$installRoot\bin` at the front only if absent, write it back:

```powershell
$bin = Join-Path $installRoot "bin"   # $installRoot was set in Step 5
$current = [Environment]::GetEnvironmentVariable("Path", "User")
if (($current -split ";") -notcontains $bin) {
  [Environment]::SetEnvironmentVariable("Path", "$bin;$current", "User")
}
```

Tell the operator to open a new terminal — existing sessions won't see the change.

**Unrecognized shell:** print the exact line and tell the operator where to add it.

### Step 8 — Clean up the tempdir

Remove `$workdir` from Step 4 (`rm -rf "$workdir"` on Unix, `Remove-Item -Recurse -Force $workdir` on Windows). The install is now self-contained in the install root — there is no source clone to maintain.

### Step 9 — Verify

Run the installed entrypoint by full path (the operator's current shell hasn't picked up the new PATH yet):

- Unix: `"$install_root/bin/cartopian" --help`
- Windows: `& "$installRoot\bin\cartopian.cmd" --help` — the shim is what makes the bare command `cartopian` work in a fresh PowerShell session once PATH is reloaded.

Confirm it exits 0. If it doesn't, surface the error and stop — do not report success.

Point the operator at the install verification checklist: `$install_root/protocol/INSTALL_VERIFICATION.md`.

### Step 10 — Summarize

Print:

- Installed ref (from Step 3).
- Install root (`$install_root`, including any `--prefix` override).
- PATH entry added (or "already present").
- `cartopian --help` exit status (from the Step 9 invocation).
- Next-step suggestions: `init workspace` if `$install_root/cartopian.toml` is a freshly-seeded default; `init project` if the workspace is already configured.

---

## Re-running for upgrade

This skill is idempotent. Re-running it fetches the current latest release (or the explicit ref the operator provides), copies it over `$install_root/`, and refreshes tool-shipped paths. Operator-owned files (`cartopian.toml`, `projects.json`) are preserved by `scripts/install.py`. If the operator originally installed with a non-default `--prefix`, re-running must use the same `--prefix` — otherwise the agent will write a second install at the default root.

Once the operator is already on a working install, prefer the `check-for-updates` skill — it compares `$install_root/VERSION` against the latest release and only invokes this skill if an upgrade is warranted.
