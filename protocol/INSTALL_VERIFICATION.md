# Cartopian Install Verification

This is the portable post-install / post-upgrade verification checklist. Run it once immediately after the first install or after each upgrade to confirm `~/.cartopian/` is laid out correctly and the Core CLI is usable.

The install/upgrade flow itself is documented in `README.md` and the "Build / Distribution" section of `STANDARDS.md`. This checklist verifies the result of that flow; it does not perform the install.

The coordinated installer now verifies the closed installed-surface set and
writes portable evidence to
`<install-root>/install-update-state.json`. The manual probes below remain
useful as independent checks and as static parity evidence on a platform where
native execution is unavailable. Static parity evidence must not be described
as native execution proof.

## Conventions

- `~/.cartopian/` resolves to:
  - **macOS / Linux / WSL:** `$HOME/.cartopian/`
  - **Native Windows (PowerShell, cmd):** `%USERPROFILE%\.cartopian\`
- The PowerShell commands below deliberately use `$env:USERPROFILE`, never `$HOME`: when an agent's driver shell is Git Bash on Windows and it hands a command string to `powershell -Command`, bash expands/rewrites `$HOME` and POSIX-looking paths (MSYS path conversion) before PowerShell sees them, yielding unresolvable paths like `/c/Users/...`. `$env:USERPROFILE` is expanded by PowerShell itself and survives the handoff. (From Git Bash it is simpler still to run the POSIX variants directly.)
- Where commands differ between shells, both are shown. Pick the one for your platform.
- "Pass when" lines name the expected observable outcome. Any other outcome fails that step.

## 0. Runtime preflight (Python 3.11+)

The Core CLI requires Python 3.11+ (`tomllib` is the standard-library floor; the entrypoint at `bin/cartopian` enforces this). Verify first — every step below depends on it.

**macOS / Linux / WSL:**

```sh
python3 --version
```

**Native Windows (PowerShell, cmd, or Git Bash):**

```powershell
py -3 --version
```

(Fall back to `python --version` only if the Python Launcher is missing.)

Pass when: output is `Python 3.11.x` or any later 3.x (e.g., `Python 3.12.5`, `Python 3.13.0`).

## Restart-state proof after an MCP-affecting update

The installed files and the connected MCP process are separate authorities.
Read the workflow restart record or the MCP install-context block and confirm:

1. `installed_identity` names the verified on-disk MCP content.
2. `running_identity`, `process_id`, and `instance_id` name what the connected
   process actually loaded.
3. `status = restart_required` or `verification_pending` carries exactly one
   current-client action plus its expected proof; it never supports an active,
   current, complete, or verified running-behavior claim.
4. After the operator performs that action, `fresh_proof.new_process` is true,
   `fresh_proof.loaded_content_matches` is true, and
   `fresh_proof.verification = verified`.
5. Only then may `status = current` and
   `activation_claim_allowed = true`.

Persisted restart facts carry no authority of their own. A restart row, a
persisted surface proof, and a prior process identity are read only from a
record whose schema identity, schema version, and installed-content row the
runtime can positively interpret (`protocol/INSTALL_UPDATE_STATE.md`). When the
record is unusable, the MCP surface is treated as affected and steps 1-5 above
must be satisfied by fresh observation: no persisted row, and no `VERSION`
receipt offered in its place, supplies `verified`, `current`, activation, or a
successful complete-qualified outcome.

An unusable record and an unbindable restart candidate are refusals, not
absence. Only a genuinely absent record — or a compatible record that persisted
no restart candidate for this client — permits the benign no-restart-needed
reading; a refused one keeps the MCP surface restart-relevant while exposing no
prior process, so a run that reports `no_restart_needed`, `complete`, or a
complete-qualified outcome after refusing the recorded MCP identity is
reporting a fail-open claim.

A new process with old or unknown loaded content fails this check. Do not kill
processes, control a GUI, execute an arbitrary restart command, or fabricate
the observation. Cross-platform instruction checks performed without the
native client are static-only evidence and retain native-execution risk.

**macOS-specific failure mode.** The stock `/usr/bin/python3` on macOS is 3.9.x. It is on `PATH` by default and silently fails the canonical CLI invocations and the `python3 -m unittest discover -s tests -t .` test runner (both require `tomllib` and the Python-3.11 guard at `bin/cartopian`). If `python3 --version` reports 3.9.x or 3.10.x:

```sh
brew install python@3.11
```

Then either re-shim your shell so a ≥3.11 interpreter resolves first on `PATH`, or invoke the Homebrew interpreter explicitly (`/opt/homebrew/bin/python3.11`). Re-run step 0 before continuing.

## 1. Install layout is complete

Confirm every tool-shipped and operator-owned path from `STANDARDS.md`'s install-behavior table is present.

**macOS / Linux / WSL:**

```sh
ls -la ~/.cartopian/
test -d ~/.cartopian/protocol
test -d ~/.cartopian/templates
test -d ~/.cartopian/skills
test -d ~/.cartopian/wrappers
test -d ~/.cartopian/cli
test -f ~/.cartopian/bin/cartopian
test -f ~/.cartopian/bin/cartopian.cmd   # native-Windows PATH shim; present on all platforms
test -f ~/.cartopian/scripts/install.py  # self-shipped installer; makes upgrades a one-line command
test -f ~/.cartopian/CHANGELOG.md
test -f ~/.cartopian/cartopian.toml
test -f ~/.cartopian/projects.json
```

**Native Windows (PowerShell):**

```powershell
Get-ChildItem -Force $env:USERPROFILE\.cartopian\
Test-Path $env:USERPROFILE\.cartopian\protocol -PathType Container
Test-Path $env:USERPROFILE\.cartopian\templates -PathType Container
Test-Path $env:USERPROFILE\.cartopian\skills -PathType Container
Test-Path $env:USERPROFILE\.cartopian\wrappers -PathType Container
Test-Path $env:USERPROFILE\.cartopian\cli -PathType Container
Test-Path $env:USERPROFILE\.cartopian\bin\cartopian -PathType Leaf
Test-Path $env:USERPROFILE\.cartopian\bin\cartopian.cmd -PathType Leaf   # PATH shim that resolves the bare 'cartopian' command on PowerShell/cmd
Test-Path $env:USERPROFILE\.cartopian\scripts\install.py -PathType Leaf  # self-shipped installer; makes upgrades a one-line command
Test-Path $env:USERPROFILE\.cartopian\CHANGELOG.md -PathType Leaf
Test-Path $env:USERPROFILE\.cartopian\cartopian.toml -PathType Leaf
Test-Path $env:USERPROFILE\.cartopian\projects.json -PathType Leaf
```

Pass when: every `test`/`Test-Path` returns success (`True` on PowerShell, exit 0 on POSIX). A missing path means the install or upgrade did not complete.

## 2. Vendored TOML writer is present

The Core CLI does not run `pip install`; the only third-party module it depends on is the vendored single-file `tomli_w` shipped under `cli/_vendor/tomli_w.py`. A missing file here breaks every command that writes TOML (e.g., `generate-config`). Open / stat it explicitly:

**macOS / Linux / WSL:**

```sh
test -f ~/.cartopian/cli/_vendor/tomli_w.py
head -n 1 ~/.cartopian/cli/_vendor/tomli_w.py
```

**Native Windows (PowerShell):**

```powershell
Test-Path $env:USERPROFILE\.cartopian\cli\_vendor\tomli_w.py -PathType Leaf
Get-Content $env:USERPROFILE\.cartopian\cli\_vendor\tomli_w.py -TotalCount 1
```

Pass when: the file exists and reading the first line succeeds (any non-empty content is fine — the check is "the file is on disk and readable at the shipped path").

## 3. Core CLI entrypoint runs

```sh
cartopian --help
echo $?            # POSIX
```

```powershell
cartopian --help
$LASTEXITCODE      # PowerShell
```

Pass when: the help text prints (subcommands listed, including at least `resolve-config`, `move-task`, `scaffold-project`, `register-project`, `discover-projects`) and the exit code is `0`.

If `cartopian` is not on `PATH`, add `~/.cartopian/bin` (POSIX) or `%USERPROFILE%\.cartopian\bin` (Windows) to `PATH` per the README install steps, then re-run. On native Windows the bare command resolves via the shipped `bin/cartopian.cmd` shim (verified in Section 1); if PowerShell still fails to find `cartopian`, confirm `.CMD` is in `PATHEXT` (it is by default).

## 4. Registry parses cleanly

The registry is JSON; a fresh install seeds it as `[]\n` and an upgrade preserves whatever the operator has registered.

**macOS / Linux / WSL:**

```sh
python3 -c "import json, pathlib; \
data = json.loads(pathlib.Path('~/.cartopian/projects.json') \
    .expanduser().read_text()); \
print(type(data).__name__, len(data))"
```

**Native Windows (PowerShell):**

```powershell
python -c "import json, pathlib; data = json.loads(pathlib.Path(r'$env:USERPROFILE\.cartopian\projects.json').read_text()); print(type(data).__name__, len(data))"
```

Pass when: output is `list <N>` (e.g., `list 0` on a fresh install, `list 3` if three projects are registered). Any `json.JSONDecodeError`, non-list top-level type, or read error fails the step.

## 5. Operator-owned files survived the upgrade

**First install only:** skip this step. There is no prior state to preserve.

**Upgrade only:** the install-behavior table in `STANDARDS.md` requires that `~/.cartopian/cartopian.toml` and `~/.cartopian/projects.json` are **never** overwritten by an upgrade. Confirm by comparing each file to a copy taken before the upgrade, or by spot-checking known operator content.

If you kept a pre-upgrade backup:

```sh
diff -u /tmp/cartopian.toml.pre-upgrade ~/.cartopian/cartopian.toml
diff -u /tmp/projects.json.pre-upgrade ~/.cartopian/projects.json
```

```powershell
Compare-Object `
  (Get-Content $env:TEMP\cartopian.toml.pre-upgrade) `
  (Get-Content $env:USERPROFILE\.cartopian\cartopian.toml)
Compare-Object `
  (Get-Content $env:TEMP\projects.json.pre-upgrade) `
  (Get-Content $env:USERPROFILE\.cartopian\projects.json)
```

Pass when: each `diff` is empty (POSIX) / each `Compare-Object` returns no rows (PowerShell). Any divergence is a regression — the upgrade overwrote operator-owned state and must be reported.

If you did not keep a backup, sanity-check by re-listing your registered projects (`cartopian discover-projects`) and confirming the set matches what you expect from before the upgrade.

## 6. Tool-shipped files match the newly-installed source

Tool-shipped paths are replaced on every install/upgrade (per the `STANDARDS.md` install-behavior table). After a fresh install or upgrade, the content under `~/.cartopian/protocol/`, `templates/`, `skills/`, `wrappers/`, `cli/`, `bin/cartopian`, `bin/cartopian.cmd`, and `CHANGELOG.md` must match the source you installed from.

Every tool-shipped path is a real copy — `scripts/install.py` installs copies whether it runs from a local clone (contributor path) or via `--from-github` (primary end-user path, driven by the `install-cartopian` skill). With `--from-github` there is no on-disk source clone; verify against the upstream tag recorded in `~/.cartopian/VERSION` if you need a remote comparison.

The commands below assume the source clone lives at `~/src/cartopian` (POSIX) or `$env:USERPROFILE\src\cartopian` (Windows) for any clone-relative checks. Adjust the source path if you cloned elsewhere.

`CHANGELOG.md` is a copy of `protocol/CHANGELOG.md`. A `git pull` of the source clone refreshes the source file but does not touch `~/.cartopian/CHANGELOG.md` until the install script is rerun.

If you keep a local source clone (e.g., for contributor work) you can compare against it; otherwise rely on step 7 (the `VERSION` marker) to confirm which upstream ref the copy was taken from. Drift in any path below means the install script did not re-run after the source was updated.

**macOS / Linux / WSL:**

```sh
for p in protocol templates skills wrappers cli; do
  diff -r ~/src/cartopian/$p ~/.cartopian/$p
done
diff -u ~/src/cartopian/bin/cartopian ~/.cartopian/bin/cartopian
diff -u ~/src/cartopian/bin/cartopian.cmd ~/.cartopian/bin/cartopian.cmd
diff -u ~/src/cartopian/protocol/CHANGELOG.md ~/.cartopian/CHANGELOG.md
```

**Native Windows (PowerShell):**

```powershell
function Compare-Tree($src, $dst) {
  $hash = {
    param($root)
    Get-ChildItem -Recurse -File $root | ForEach-Object {
      [pscustomobject]@{
        Rel  = $_.FullName.Substring($root.Length).TrimStart('\','/')
        Hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash
      }
    }
  }
  Compare-Object (& $hash $src) (& $hash $dst) -Property Rel,Hash
}
foreach ($p in 'protocol','templates','skills','wrappers','cli') {
  Compare-Tree "$env:USERPROFILE\src\cartopian\$p" "$env:USERPROFILE\.cartopian\$p"
}
Compare-Object `
  (Get-Content $env:USERPROFILE\src\cartopian\bin\cartopian) `
  (Get-Content $env:USERPROFILE\.cartopian\bin\cartopian)
Compare-Object `
  (Get-Content $env:USERPROFILE\src\cartopian\bin\cartopian.cmd) `
  (Get-Content $env:USERPROFILE\.cartopian\bin\cartopian.cmd)
Compare-Object `
  (Get-Content $env:USERPROFILE\src\cartopian\protocol\CHANGELOG.md) `
  (Get-Content $env:USERPROFILE\.cartopian\CHANGELOG.md)
```

Pass when: every `diff` is empty (POSIX) and every `Compare-Object` / `Compare-Tree` call returns no rows (PowerShell). A non-empty result means the upgrade did not refresh tool-shipped content (commonly: `git pull` ran but the install script did not re-run; see the README upgrade section).

## 7. `VERSION` marker matches the installed ref

The `install-cartopian` skill writes `~/.cartopian/VERSION` as a single line: the git ref the installer resolved (a release tag like `v1.5.0`, or the literal `main` when no release has been tagged upstream). The `check-for-updates` skill reads this file to decide whether an upgrade is needed.

**macOS / Linux / WSL:**

```sh
test -f ~/.cartopian/VERSION
cat ~/.cartopian/VERSION
```

**Native Windows (PowerShell):**

```powershell
Test-Path $env:USERPROFILE\.cartopian\VERSION -PathType Leaf
Get-Content $env:USERPROFILE\.cartopian\VERSION
```

Pass when: the file exists, is non-empty, and contains exactly one ref token (release tag or `main`) on a single line.

This marker is also the release metadata a copy-mode install carries: the CLI (`cartopian --version`) and the MCP install-context block report a release-tag ref recorded here as the release version, alongside the separately derived installed-content identity. A branch ref such as `main`, or a marker that is empty, multi-line, or multi-token, leaves the release version `unknown` rather than being reported as a release.

Receipt authority is limited to the two ref shapes the installer can have written — a release tag, or the literal `main` fallback. Any other single token (a commit id, a branch name, a hand-edited word) is malformed marker content: it leaves release identity `unknown` **and** carries no installation provenance, so it cannot make installed content `verified`.

If `VERSION` is missing, the install predates the marker; re-run `install-cartopian` to refresh it. `check-for-updates` will otherwise treat the install as ref-unknown.

## Failure → re-run the install/upgrade flow

If any step above fails, re-run the install/upgrade flow documented in `README.md` (primary end-user path: the `install-cartopian` skill; contributor path: `git clone` + `scripts/install.py`) and re-execute this checklist from step 0. The flow is idempotent: tool-shipped paths are recreated, and operator-owned `cartopian.toml` / `projects.json` are preserved.
