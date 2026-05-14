# Cartopian Install Verification

This is the FR-015 post-install / post-upgrade verification checklist. Run it once immediately after the first install or after each upgrade to confirm `~/.cartopian/` is laid out correctly and the Core CLI is usable.

The install/upgrade flow itself is documented in `README.md` and the "Build / Distribution" section of `STANDARDS.md`. This checklist verifies the result of that flow; it does not perform the install.

V1 ships this as documentation only — there is no `cartopian verify-install` command. An operator (or an end-to-end test driver) executes the steps below by hand.

## Conventions

- `~/.cartopian/` resolves to:
  - **macOS / Linux / WSL:** `$HOME/.cartopian/`
  - **Native Windows (PowerShell, cmd):** `%USERPROFILE%\.cartopian\`
- Where commands differ between shells, both are shown. Pick the one for your platform.
- "Pass when" lines name the expected observable outcome. Any other outcome fails that step.

## 0. Runtime preflight (Python 3.11+)

The Core CLI requires Python 3.11+ per DEC-001 (stdlib `tomllib` is the floor; the entrypoint at `bin/cartopian` enforces this). Verify first — every step below depends on it.

**macOS / Linux / WSL:**

```sh
python3 --version
```

**Native Windows (PowerShell):**

```powershell
python --version
```

Pass when: output is `Python 3.11.x` or any later 3.x (e.g., `Python 3.12.5`, `Python 3.13.0`).

**macOS-specific failure mode.** The stock `/usr/bin/python3` on macOS is 3.9.x. It is on `PATH` by default and silently fails the canonical CLI invocations and the `python3 -m unittest discover -s tests -t .` test runner (both require `tomllib` and the Python-3.11 guard at `bin/cartopian`). If `python3 --version` reports 3.9.x or 3.10.x:

```sh
brew install python@3.11
```

Then either re-shim your shell so a ≥3.11 interpreter resolves first on `PATH`, or invoke the Homebrew interpreter explicitly (`/opt/homebrew/bin/python3.11`). Re-run step 0 before continuing.

## 1. Install layout matches FR-002

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
test -f ~/.cartopian/CHANGELOG.md
test -f ~/.cartopian/cartopian.toml
test -f ~/.cartopian/projects.json
```

**Native Windows (PowerShell):**

```powershell
Get-ChildItem -Force $HOME\.cartopian\
Test-Path $HOME\.cartopian\protocol -PathType Container
Test-Path $HOME\.cartopian\templates -PathType Container
Test-Path $HOME\.cartopian\skills -PathType Container
Test-Path $HOME\.cartopian\wrappers -PathType Container
Test-Path $HOME\.cartopian\cli -PathType Container
Test-Path $HOME\.cartopian\bin\cartopian -PathType Leaf
Test-Path $HOME\.cartopian\bin\cartopian.cmd -PathType Leaf   # PATH shim that resolves the bare 'cartopian' command on PowerShell/cmd
Test-Path $HOME\.cartopian\CHANGELOG.md -PathType Leaf
Test-Path $HOME\.cartopian\cartopian.toml -PathType Leaf
Test-Path $HOME\.cartopian\projects.json -PathType Leaf
```

Pass when: every `test`/`Test-Path` returns success (`True` on PowerShell, exit 0 on POSIX). A missing path means the install or upgrade did not complete.

## 2. Vendored TOML writer is present at the DEC-001 path

The Core CLI does not run `pip install`; the only third-party module it depends on is the vendored single-file `tomli_w` shipped under `cli/_vendor/tomli_w.py`. A missing file here breaks every command that writes TOML (e.g., `generate-config`). Open / stat it explicitly:

**macOS / Linux / WSL:**

```sh
test -f ~/.cartopian/cli/_vendor/tomli_w.py
head -n 1 ~/.cartopian/cli/_vendor/tomli_w.py
```

**Native Windows (PowerShell):**

```powershell
Test-Path $HOME\.cartopian\cli\_vendor\tomli_w.py -PathType Leaf
Get-Content $HOME\.cartopian\cli\_vendor\tomli_w.py -TotalCount 1
```

Pass when: the file exists and reading the first line succeeds (any non-empty content is fine — the check is "the file is on disk and readable at the DEC-001-locked path").

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

## 4. Registry parses cleanly (JSON, DEC-009)

The registry is JSON per DEC-009; a fresh install seeds it as `[]\n` and an upgrade preserves whatever the operator has registered.

**macOS / Linux / WSL:**

```sh
python3 -c "import json, pathlib; \
data = json.loads(pathlib.Path('~/.cartopian/projects.json') \
    .expanduser().read_text()); \
print(type(data).__name__, len(data))"
```

**Native Windows (PowerShell):**

```powershell
python -c "import json, pathlib; data = json.loads(pathlib.Path(r'$HOME\.cartopian\projects.json').read_text()); print(type(data).__name__, len(data))"
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
  (Get-Content $HOME\.cartopian\cartopian.toml)
Compare-Object `
  (Get-Content $env:TEMP\projects.json.pre-upgrade) `
  (Get-Content $HOME\.cartopian\projects.json)
```

Pass when: each `diff` is empty (POSIX) / each `Compare-Object` returns no rows (PowerShell). Any divergence is a regression — the upgrade overwrote operator-owned state and must be reported.

If you did not keep a backup, sanity-check by re-listing your registered projects (`cartopian discover-projects`) and confirming the set matches what you expect from before the upgrade.

## 6. Tool-shipped files match the newly-installed source

Tool-shipped paths are replaced on every install/upgrade (per the `STANDARDS.md` install-behavior table). After a fresh install or upgrade, the content under `~/.cartopian/protocol/`, `templates/`, `skills/`, `wrappers/`, `cli/`, `bin/cartopian`, `bin/cartopian.cmd`, and `CHANGELOG.md` must match the source you installed from.

There are two install shapes; pick the section that matches your install:

- **Copy mode (primary end-user path)** — driven by the `install-cartopian` skill (the README's primary `Install` flow). Tool-shipped paths under `~/.cartopian/` are real copies of an extracted release tarball. There is no on-disk source clone; verify against the upstream tag recorded in `~/.cartopian/VERSION` if you need a remote comparison.
- **Symlink mode (contributor path)** — `git clone` + `python3 scripts/install.py` (no `--mode copy`). Tool-shipped paths under `~/.cartopian/` are symlinks back into your local clone.

The commands below assume the source clone lives at `~/src/cartopian` (POSIX) or `$HOME\src\cartopian` (Windows) for any clone-relative checks. Adjust the source path if you cloned elsewhere.

`CHANGELOG.md` is a special case: per `scripts/install.py` it is always a real copy of `protocol/CHANGELOG.md`, even in symlink mode. A `git pull` of the source clone refreshes the source file but does not touch `~/.cartopian/CHANGELOG.md` until the install script is rerun.

### 6a. Symlink mode (contributor install)

In symlink mode each tool-shipped directory, `bin/cartopian`, and `bin/cartopian.cmd` is a symlink into the cloned source tree; confirm each link target resolves into your clone. Then compare `CHANGELOG.md` to its source because it is always a real copy.

**macOS / Linux / WSL:**

```sh
ls -l ~/.cartopian/protocol ~/.cartopian/templates ~/.cartopian/skills \
      ~/.cartopian/wrappers ~/.cartopian/cli \
      ~/.cartopian/bin/cartopian ~/.cartopian/bin/cartopian.cmd
diff -u ~/src/cartopian/protocol/CHANGELOG.md ~/.cartopian/CHANGELOG.md
```

**Native Windows (PowerShell):**

```powershell
foreach ($p in 'protocol','templates','skills','wrappers','cli','bin\cartopian','bin\cartopian.cmd') {
  $item = Get-Item -Force "$HOME\.cartopian\$p"
  "{0,-20} {1} -> {2}" -f $p, $item.LinkType, $item.Target
}
Compare-Object `
  (Get-Content $HOME\src\cartopian\protocol\CHANGELOG.md) `
  (Get-Content $HOME\.cartopian\CHANGELOG.md)
```

Pass when:

- Each directory, `bin/cartopian`, and `bin/cartopian.cmd` is a symlink (leading `l` in `ls -l`; `LinkType` `SymbolicLink` in PowerShell) whose target is the matching path inside your local clone, and the target exists.
- The `CHANGELOG.md` `diff` / `Compare-Object` returns no output.

### 6b. Copy mode (primary end-user install)

In copy mode every tool-shipped path is a real copy. If you also keep a local source clone (e.g., for contributor work) you can compare against it; otherwise rely on step 7 (the `VERSION` marker) to confirm which upstream ref the copy was taken from. Drift in any path below means the install script did not re-run after the source was updated.

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
  Compare-Tree "$HOME\src\cartopian\$p" "$HOME\.cartopian\$p"
}
Compare-Object `
  (Get-Content $HOME\src\cartopian\bin\cartopian) `
  (Get-Content $HOME\.cartopian\bin\cartopian)
Compare-Object `
  (Get-Content $HOME\src\cartopian\bin\cartopian.cmd) `
  (Get-Content $HOME\.cartopian\bin\cartopian.cmd)
Compare-Object `
  (Get-Content $HOME\src\cartopian\protocol\CHANGELOG.md) `
  (Get-Content $HOME\.cartopian\CHANGELOG.md)
```

Pass when: every `diff` is empty (POSIX) and every `Compare-Object` / `Compare-Tree` call returns no rows (PowerShell). A non-empty result means the upgrade did not refresh tool-shipped content (commonly: `git pull` ran but the install script did not re-run; see the README upgrade section).

## 7. `VERSION` marker matches the installed ref

The `install-cartopian` skill writes `~/.cartopian/VERSION` as a single line: the git ref the installer resolved (a release tag like `v0.3.0`, or the literal `main` when no release has been tagged upstream). The `check-for-updates` skill reads this file to decide whether an upgrade is needed.

**macOS / Linux / WSL:**

```sh
test -f ~/.cartopian/VERSION
cat ~/.cartopian/VERSION
```

**Native Windows (PowerShell):**

```powershell
Test-Path $HOME\.cartopian\VERSION -PathType Leaf
Get-Content $HOME\.cartopian\VERSION
```

Pass when: the file exists, is non-empty, and contains exactly one ref token (release tag or `main`) on a single line.

If `VERSION` is missing, the install predates the marker; re-run `install-cartopian` to refresh it. `check-for-updates` will otherwise treat the install as ref-unknown.

## Failure → re-run the install/upgrade flow

If any step above fails, re-run the install/upgrade flow documented in `README.md` (primary end-user path: the `install-cartopian` skill; contributor path: `git clone` + `scripts/install.py`) and re-execute this checklist from step 0. The flow is idempotent: tool-shipped paths are recreated, and operator-owned `cartopian.toml` / `projects.json` are preserved.
