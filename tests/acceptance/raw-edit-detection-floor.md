# Operator acceptance — raw-edit detection floor

Live acceptance for the universal raw-edit detection floor
(`cli/provenance.py`, surfaced by `cartopian plan-audit`) on a harness that
offers **no deterministic refusal** — an advisory-tier host. A raw edit to a
governed artifact that bypasses the mediated writers must be **detected**,
and the host must render **advisory + detection**, never "contained". Run
these steps **by hand** — automated tests do not satisfy this gate. Execute
the full sequence once on native Windows (PowerShell) and once on macOS; the
steps are written for both, with the Windows command form first where they
differ.

The floor needs **zero harness cooperation**: detection is an ordinary CLI
command reading files and the provenance log. Run the raw-edit step from any
advisory-tier host available to you (Claude Desktop, Antigravity TUI, the
ChatGPT app acting as a filesystem PM, Devin) — or, equivalently, hand-edit
in a plain text editor: any non-mediated write path exercises the same floor.
Deliberately register **no** refusal adapter anywhere in this scenario.
Detection is not capability gating: it is always-on in both gated and ungated
modes, and this scenario asserts both.

Prerequisites: Python 3.11+ (macOS: on `PATH` as `python`; Windows: via the
`py` launcher) and Cartopian installed at `~/.cartopian`
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
mkdir $env:TEMP\floor-accept; cd $env:TEMP\floor-accept
mkdir gov-project, gov-project\specs, gov-project\prompts, gov-project\reports, tool-repo, evidence
```

```bash
# macOS
mkdir -p /tmp/floor-accept && cd /tmp/floor-accept
mkdir -p gov-project/specs gov-project/prompts gov-project/reports tool-repo evidence
```

Write `gov-project/cartopian.toml`. The `pm` role declares a grants key,
which **activates** containment project-wide:

```toml
[project]
id = "floor-accept"
name = "Detection Floor Acceptance"
protocol_version = "v0.5.0"
work_roots = ["tool-repo"]

[roles.pm]
description = "PM on an advisory-tier host."
grants = ["read:governance", "write:lifecycle"]
```

Write `gov-project/cartopian.local.toml` mapping the work root to its
**absolute** path (adjust to your machine):

```toml
# Windows — note escaped backslashes or use forward slashes
[work_roots]
tool-repo = 'C:\Users\<you>\AppData\Local\Temp\floor-accept\tool-repo'
```

```toml
# macOS
[work_roots]
tool-repo = "/tmp/floor-accept/tool-repo"
```

Do **not** hand-write `STATE.md` — step 2 authors it through the mediated
writer so the provenance baseline is established. Register the project:

```powershell
# Windows
py "$env:USERPROFILE\.cartopian\bin\cartopian" register-project "$env:TEMP\floor-accept\gov-project"
```

```bash
# macOS
"$HOME/.cartopian/bin/cartopian" register-project /tmp/floor-accept/gov-project
```

## 2. Establish the provenance baseline via a mediated writer

Author `STATE.md` through the mediated write surface (which appends a
provenance record for the bytes it lands):

```powershell
# Windows
py "$env:USERPROFILE\.cartopian\bin\cartopian" write-state "$env:TEMP\floor-accept\gov-project" --content "Baseline state authored via mediated writer."
Test-Path gov-project\.cartopian\provenance.log   # must print True
```

```bash
# macOS
"$HOME/.cartopian/bin/cartopian" write-state /tmp/floor-accept/gov-project --content "Baseline state authored via mediated writer."
test -f gov-project/.cartopian/provenance.log && echo "ok: log exists" || echo "FAIL: no log"
```

**Expected:** `gov-project/STATE.md` exists and
`gov-project/.cartopian/provenance.log` exists (one NDJSON record naming
`STATE.md` and its SHA-256).

## 3. Confirm a clean audit before the drift

```powershell
# Windows
py "$env:USERPROFILE\.cartopian\bin\cartopian" plan-audit "$env:TEMP\floor-accept\gov-project" > evidence\audit-clean.json 2> evidence\audit-clean.stderr.txt
echo "exit=$LASTEXITCODE" | Tee-Object -Append evidence\audit-clean.stderr.txt
```

```bash
# macOS
"$HOME/.cartopian/bin/cartopian" plan-audit /tmp/floor-accept/gov-project > evidence/audit-clean.json 2> evidence/audit-clean.stderr.txt
echo "exit=$?" | tee -a evidence/audit-clean.stderr.txt
```

**Expected:** exit `0`; `audit-clean.json` contains `"clean":true` and an
empty `"guard":[]` list under `"provenance"`; no `[guard]` line on stderr.

## 4. Raw edit: change a governed artifact out of band

First snapshot the baseline bytes (step 5a restores from this copy):

```powershell
# Windows
Copy-Item gov-project\STATE.md evidence\state-baseline.md
```

```bash
# macOS
cp gov-project/STATE.md evidence/state-baseline.md
```

On the advisory-tier host under test, ask the session to modify
`gov-project/STATE.md` with its native file tool (**not** a Cartopian
mediated writer / MCP tool) — or hand-edit in a text editor. The
shell-equivalent form, which exercises the same non-mediated path:

```powershell
# Windows
Add-Content gov-project\STATE.md "raw drift touch"
```

```bash
# macOS
echo "raw drift touch" >> gov-project/STATE.md
```

**Expected:** the edit lands with no refusal — this host has no deterministic
refusal, which is exactly the residual the floor owns.

## 5. Detect: the audit names the drifted artifact and fails closed

```powershell
# Windows
py "$env:USERPROFILE\.cartopian\bin\cartopian" plan-audit "$env:TEMP\floor-accept\gov-project" > evidence\audit-drift.json 2> evidence\audit-drift.stderr.txt
echo "exit=$LASTEXITCODE" | Tee-Object -Append evidence\audit-drift.stderr.txt
```

```bash
# macOS
"$HOME/.cartopian/bin/cartopian" plan-audit /tmp/floor-accept/gov-project > evidence/audit-drift.json 2> evidence/audit-drift.stderr.txt
echo "exit=$?" | tee -a evidence/audit-drift.stderr.txt
```

**Expected:** exit `1` (a detected raw edit fails the audit even with no
lifecycle blocker); stderr carries a `[guard]` line stating that governed
artifact `'STATE.md'` **was modified out of band** — its current content does
not match the latest mediated write recorded in `.cartopian/provenance.log`;
`audit-drift.json` contains a `"provenance"` → `"guard"` entry with
`"kind":"raw-edit"` and `"relpath":"STATE.md"`. This captured output naming
the drifted artifact is the acceptance-critical evidence.

## 5a. Only a mediated write clears the floor — and a raw revert is detected

First clear the drift through the mediated writer with **new** content (the
step-2 baseline becomes a *superseded* mediated version):

```powershell
# Windows
py "$env:USERPROFILE\.cartopian\bin\cartopian" write-state "$env:TEMP\floor-accept\gov-project" --content "Second state authored via mediated writer."
```

```bash
# macOS
"$HOME/.cartopian/bin/cartopian" write-state /tmp/floor-accept/gov-project --content "Second state authored via mediated writer."
```

Rerun the audit, saving to `evidence/audit-mediated.*`:

```powershell
# Windows
py "$env:USERPROFILE\.cartopian\bin\cartopian" plan-audit "$env:TEMP\floor-accept\gov-project" > evidence\audit-mediated.json 2> evidence\audit-mediated.stderr.txt
echo "exit=$LASTEXITCODE" | Tee-Object -Append evidence\audit-mediated.stderr.txt
```

```bash
# macOS
"$HOME/.cartopian/bin/cartopian" plan-audit /tmp/floor-accept/gov-project > evidence/audit-mediated.json 2> evidence/audit-mediated.stderr.txt
echo "exit=$?" | tee -a evidence/audit-mediated.stderr.txt
```

**Expected:** exit `0`, clean again — a mediated write, and only a mediated
write, clears the floor.

Now raw-revert to the superseded step-2 bytes **out of band** (a raw copy,
not a mediated writer):

```powershell
# Windows
Copy-Item evidence\state-baseline.md gov-project\STATE.md
```

```bash
# macOS
cp evidence/state-baseline.md gov-project/STATE.md
```

Rerun the audit, saving to `evidence/audit-revert.*`:

```powershell
# Windows
py "$env:USERPROFILE\.cartopian\bin\cartopian" plan-audit "$env:TEMP\floor-accept\gov-project" > evidence\audit-revert.json 2> evidence\audit-revert.stderr.txt
echo "exit=$LASTEXITCODE" | Tee-Object -Append evidence\audit-revert.stderr.txt
```

```bash
# macOS
"$HOME/.cartopian/bin/cartopian" plan-audit /tmp/floor-accept/gov-project > evidence/audit-revert.json 2> evidence/audit-revert.stderr.txt
echo "exit=$?" | tee -a evidence/audit-revert.stderr.txt
```

**Expected:** `[guard]` raw-edit detection for `'STATE.md'` with exit `1` —
restoring a superseded mediated version out of band is itself an unmediated
change; only the *latest* mediated write is the artifact's authorized state.

## 6. Honest tier: the host renders advisory, never "contained"

```powershell
# Windows
py "$env:USERPROFILE\.cartopian\bin\cartopian" containment-matrix "$env:TEMP\floor-accept\gov-project" > evidence\matrix.json 2> evidence\matrix.stderr.txt
```

```bash
# macOS
"$HOME/.cartopian/bin/cartopian" containment-matrix /tmp/floor-accept/gov-project > evidence/matrix.json 2> evidence/matrix.stderr.txt
```

**Expected:** in `matrix.json`, the row for the host you ran step 4 on (e.g.
`claude-desktop`, `antigravity-tui`, `chatgpt-app`, `devin`) shows
`"tier":"advisory+detection"` — and so does every other row, since no refusal
adapter is registered for this project. No row shows `"contained"`. Stderr
carries one `[advisory]` disclosure per host naming the residual plainly:
out-of-band writes are detected after the fact by the raw-edit detection
floor (plan-audit provenance), not prevented at the point of write.

## 7. The floor is always-on: detection in ungated mode too

Exit any session. Replace the `[roles.pm]` table in
`gov-project/cartopian.toml` with a grant-free declaration (no `grants` key
anywhere in the config — the project is now **ungated**):

```toml
[roles]
pm = "PM on an advisory-tier host."
```

Repeat the raw edit (append `ungated drift touch` out of band, the same
non-mediated path as step 4) and rerun the audit, saving to
`evidence/audit-ungated.*`:

```powershell
# Windows
Add-Content gov-project\STATE.md "ungated drift touch"
py "$env:USERPROFILE\.cartopian\bin\cartopian" plan-audit "$env:TEMP\floor-accept\gov-project" > evidence\audit-ungated.json 2> evidence\audit-ungated.stderr.txt
echo "exit=$LASTEXITCODE" | Tee-Object -Append evidence\audit-ungated.stderr.txt
```

```bash
# macOS
echo "ungated drift touch" >> gov-project/STATE.md
"$HOME/.cartopian/bin/cartopian" plan-audit /tmp/floor-accept/gov-project > evidence/audit-ungated.json 2> evidence/audit-ungated.stderr.txt
echo "exit=$?" | tee -a evidence/audit-ungated.stderr.txt
```

**Expected:** the same `[guard]` raw-edit detection for `'STATE.md'` with
exit `1`. Detection is not capability gating — an ungated config refuses
nothing anywhere, but the floor still detects the drift.

## 8. Clean up

```
"$HOME/.cartopian/bin/cartopian" unregister-project floor-accept
```

(Windows: `py "$env:USERPROFILE\.cartopian\bin\cartopian" unregister-project floor-accept`.)
Preserve the `evidence/` directory with the run record; delete the rest of
the `floor-accept` scratch directory.

## Results record

All acceptance docs in this suite share one recording format. Create
`evidence/results.md` in the scratch root on first use and append one row per
executed step per platform:

| Platform | Step | Expected | Observed | PASS/FAIL | Evidence |
| -------- | ---- | -------- | -------- | --------- | -------- |
| macos | 5 | `[guard]` raw-edit naming `STATE.md`; exit 1 | detected as expected | PASS | evidence/audit-drift.stderr.txt |

- **Platform** — `macos` or `windows`.
- **Step** — the numbered section in this doc.
- **Expected** — condensed restatement of that step's Expected line.
- **Observed** — what actually happened (verbatim where short).
- **Evidence** — path under `evidence/` to the captured output, hash, or JSON.

## Pass criteria

All behaviors observed live, on both platforms: (2) the mediated writer
establishes the provenance baseline; (3) the pre-drift audit is clean with
exit 0; (4) the raw edit lands unrefused on the advisory-tier host; (5) the
audit detects it — `[guard]` naming `STATE.md` as modified out of band, exit
1; (5a) only a mediated write restores clean, and a raw revert to a
superseded mediated version is likewise detected;
(6) the containment matrix renders the host **advisory + detection**
with a plain residual disclosure — never "contained"; (7) detection fires
identically under an ungated config.
