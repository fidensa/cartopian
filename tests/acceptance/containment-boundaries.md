# Operator acceptance — both containment boundaries

Live acceptance that an *activated* project's two containment boundaries —
**(a) governed artifacts** and **(b) the product work root** — are each
denied when the matching write grant is absent and succeed when it is
present, keyed **only to grants, never to role names**. Run these steps **by
hand in a real session** — automated tests do not satisfy this gate. Execute
the full sequence once on native Windows (PowerShell-launched host) and once
on macOS; the steps are written for both, with the Windows command form first
where they differ.

This doc is host-agnostic: it can be run on any refusal-capable host by
binding that host's adapter for the "deny mutation of ungranted path-classes"
contract. The concrete Claude Code instance of deterministic refusal is
`tests/acceptance/claude-refusal-adapter.md` — cross-reference it for the
exact refusal-message format and for hook registration; the two-boundary
check and its evidence recording are defined *here*. The commands below show
the Claude Code binding; substitute the equivalent adapter registration and
launch on another refusal-capable host.

Prerequisites: Python 3.11+ (macOS: on `PATH` as `python`; Windows: via the
`py` launcher), a refusal-capable host installed (Claude Code in the concrete
commands below), and Cartopian installed at `~/.cartopian`
(`%USERPROFILE%\.cartopian` on Windows) via `python scripts/install.py`
(macOS) or `py scripts\install.py --mode copy` (Windows) from the source
repo.

Throughout, `$CART` means the install root:

- Windows (PowerShell): `$CART = "$env:USERPROFILE\.cartopian"`
- macOS (zsh/bash): `CART="$HOME/.cartopian"`

## 1. Set up a throwaway registered project with an activated config

Create a scratch governed project, a scratch work root, and an evidence
directory:

```powershell
# Windows (PowerShell)
mkdir $env:TEMP\boundary-accept; cd $env:TEMP\boundary-accept
mkdir gov-project, gov-project\specs, gov-project\prompts, gov-project\reports, tool-repo, evidence
```

```bash
# macOS
mkdir -p /tmp/boundary-accept && cd /tmp/boundary-accept
mkdir -p gov-project/specs gov-project/prompts gov-project/reports tool-repo evidence
```

Write `gov-project/cartopian.toml`. The `pm` role declares a grants key,
which **activates** containment project-wide, and deliberately holds neither
`write:lifecycle` nor `write:worktree`:

```toml
[project]
id = "boundary-accept"
name = "Boundary Acceptance"
protocol_version = "v0.6.0"
work_roots = ["tool-repo"]

[roles.pm]
description = "PM under test."
grants = ["read:governance"]
```

Write `gov-project/cartopian.local.toml` mapping the work root to its
**absolute** path (adjust to your machine):

```toml
# Windows — note escaped backslashes or use forward slashes
[work_roots]
tool-repo = 'C:\Users\<you>\AppData\Local\Temp\boundary-accept\tool-repo'
```

```toml
# macOS
[work_roots]
tool-repo = "/tmp/boundary-accept/tool-repo"
```

Write a minimal `gov-project/STATE.md` (one line of text is enough).

Register the project:

```powershell
# Windows
py "$env:USERPROFILE\.cartopian\bin\cartopian" register-project "$env:TEMP\boundary-accept\gov-project"
```

```bash
# macOS
"$HOME/.cartopian/bin/cartopian" register-project /tmp/boundary-accept/gov-project
```

## 2. Bind the host's refusal adapter

On Claude Code, register the PreToolUse hook in **project-level** settings
for `boundary-accept/`. From the Cartopian **source repo** (`--mode copy` is
required on native Windows because `--claude-hook` performs a full install at
the chosen mode, and the default symlink mode fails without Developer Mode /
admin):

```powershell
# Windows — from the Cartopian source repo
py scripts\install.py --mode copy --claude-hook $env:TEMP\boundary-accept
```

```bash
# macOS — from the Cartopian source repo
python3 scripts/install.py --claude-hook /tmp/boundary-accept
```

Confirm `boundary-accept/.claude/settings.json` exists, its `PreToolUse`
matcher is `Read|NotebookRead|Glob|Grep|Write|Edit|MultiEdit|NotebookEdit`,
and no user-global settings file (`~/.claude/settings.json`) was modified.
(`tests/acceptance/claude-refusal-adapter.md` § 2 documents this same
registration alongside the exact refusal-message format.) On another
refusal-capable host, bind that host's adapter for the same deny contract at
its native interception point.

Record the pre-run state of both boundary targets:

```powershell
# Windows
Get-FileHash gov-project\STATE.md -Algorithm SHA256 | Tee-Object evidence\state-hash-before.txt
Test-Path tool-repo\src   # must print False
```

```bash
# macOS
shasum -a 256 gov-project/STATE.md | tee evidence/state-hash-before.txt
test -e tool-repo/src && echo "FAIL: exists" || echo "ok: absent"
```

## 3. Deny boundary (a): governed-artifact mutation without `write:lifecycle`

Launch the host in `boundary-accept/` with **no** `CARTOPIAN_ROLE` set — the
session resolves to the `pm` role. On Claude Code:

```powershell
# Windows (PowerShell)
cd $env:TEMP\boundary-accept
claude
```

```bash
# macOS
cd /tmp/boundary-accept
claude
```

Ask:

> Append the line "boundary touch" to gov-project/STATE.md using the Edit tool.

**Expected:** the tool call is denied with a single `[guard]` message naming
the STATE.md path, the path-class (`lifecycle`), and the missing grant
(`write:lifecycle`) — see the adapter doc for the exact Claude Code wording.
The file is unchanged.

## 4. Deny boundary (b): work-root mutation without `write:worktree`

In the same session, ask:

> Create tool-repo/src/main.py with a hello-world.

**Expected:** `[guard]` deny naming the path, path-class
`work-root:tool-repo`, and missing grant `write:worktree`. Nothing is created
under `tool-repo/`.

Copy both refusals verbatim into `evidence/deny-a-<platform>.txt` and
`evidence/deny-b-<platform>.txt`.

## 5. Allow boundary (a): granted governed write — (b) still denies

Exit the host. Edit `gov-project/cartopian.toml` so `pm` holds the
governed-write grant only:

```toml
grants = ["read:governance", "write:lifecycle"]
```

Relaunch the host in `boundary-accept/` (again with no `CARTOPIAN_ROLE` set —
on Claude Code):

```powershell
# Windows (PowerShell)
cd $env:TEMP\boundary-accept
claude
```

```bash
# macOS
cd /tmp/boundary-accept
claude
```

and repeat **both** asks from steps 3–4.

**Expected:** the STATE.md edit **succeeds** (the file now contains the
appended line), and the tool-repo write **still denies** with the
`work-root:tool-repo` / `write:worktree` message. The two boundaries key
independently: granting one never opens the other.

Save the new hash to `evidence/state-hash-allowed.txt` (from the scratch
root):

```powershell
# Windows
Get-FileHash gov-project\STATE.md -Algorithm SHA256 | Tee-Object evidence\state-hash-allowed.txt
```

```bash
# macOS
shasum -a 256 gov-project/STATE.md | tee evidence/state-hash-allowed.txt
```

## 6. Allow boundary (b): granted work-root write

Exit the host. Extend the `pm` grants:

```toml
grants = ["read:governance", "write:lifecycle", "write:worktree"]
```

Relaunch (on Claude Code):

```powershell
# Windows (PowerShell)
cd $env:TEMP\boundary-accept
claude
```

```bash
# macOS
cd /tmp/boundary-accept
claude
```

and repeat the tool-repo write ask from step 4.

**Expected:** the write succeeds; `tool-repo/src/main.py` exists. Record it
(from the scratch root):

```powershell
# Windows
Get-FileHash tool-repo\src\main.py -Algorithm SHA256 | Tee-Object evidence\workroot-allowed.txt
```

```bash
# macOS
shasum -a 256 tool-repo/src/main.py | tee evidence/workroot-allowed.txt
```

## 7. Grants, never role names

Prove enforcement keys on grants only. Exit the host and replace the
`[roles.pm]` table with an arbitrarily named role carrying the **original
ungranted** set:

```toml
[roles.acceptance-zz]
description = "Arbitrary role name; same grants as step 1."
grants = ["read:governance"]
```

Relaunch with the role marker exported:

```powershell
# Windows
$env:CARTOPIAN_ROLE = "acceptance-zz"
claude
```

```bash
# macOS
CARTOPIAN_ROLE=acceptance-zz claude
```

Repeat both asks from steps 3–4.

**Expected:** both boundaries deny with the **same** path-classes and missing
grants as steps 3–4 — the role's name changed, its grants did not, and the
refusals name grants, not roles. Then exit, extend `acceptance-zz` to
`grants = ["read:governance", "write:lifecycle", "write:worktree"]`, relaunch
the same way, and repeat both asks: **both succeed**. Identical grants ⇒
identical behavior, whatever the role is called. Unset the marker afterwards
(`Remove-Item Env:CARTOPIAN_ROLE` / start a fresh shell).

## 8. Clean up

```
"$HOME/.cartopian/bin/cartopian" unregister-project boundary-accept
```

(Windows: `py "$env:USERPROFILE\.cartopian\bin\cartopian" unregister-project boundary-accept`.)
Preserve the `evidence/` directory with the run record; delete the rest of
the `boundary-accept` scratch directory and remove or keep the adapter
registration as desired.

## Results record

All acceptance docs in this suite share one recording format. Create
`evidence/results.md` in the scratch root on first use and append one row per
executed step per platform:

| Platform | Step | Expected | Observed | PASS/FAIL | Evidence |
| -------- | ---- | -------- | -------- | --------- | -------- |
| windows | 4 | `[guard]` deny naming `work-root:tool-repo` / `write:worktree` | denied; tool-repo untouched | PASS | evidence/deny-b-windows.txt |

- **Platform** — `macos` or `windows`.
- **Step** — the numbered section in this doc.
- **Expected** — condensed restatement of that step's Expected line.
- **Observed** — what actually happened (verbatim where short).
- **Evidence** — path under `evidence/` to the captured output, hash, or JSON.

## Pass criteria

All behaviors observed live, on both platforms: (3)+(4) each boundary denies
with an honest `[guard]` message naming path, its own path-class
(`lifecycle`; `work-root:tool-repo`), and its own missing grant
(`write:lifecycle`; `write:worktree`); (5) granting `write:lifecycle` opens
boundary (a) only — boundary (b) still denies; (6) granting `write:worktree`
opens boundary (b); (7) an arbitrarily renamed role with the same grants
behaves identically on both boundaries, deny and allow — enforcement keyed to
grants, never to role names.
