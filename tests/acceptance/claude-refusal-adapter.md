# Operator acceptance — Claude Code refusal adapter

Live acceptance for the capability-keyed PreToolUse hook (`cli/claude_hook.py`).
Run these steps **by hand in a real Claude Code session** — automated tests do
not satisfy this gate. Execute the full sequence once on native Windows
(PowerShell-launched Claude Code) and once on macOS; the steps are written for
both, with the Windows command form first where they differ.

Prerequisites: Python 3.11+ (macOS: on `PATH` as `python`; Windows: via the
`py` launcher), Claude Code installed, and Cartopian installed at
`~/.cartopian` (`%USERPROFILE%\.cartopian` on Windows) via
`python scripts/install.py` (macOS) or `py scripts\install.py --mode copy`
(Windows) from the source repo.

Throughout, `$CART` means the install root:

- Windows (PowerShell): `$CART = "$env:USERPROFILE\.cartopian"`
- macOS (zsh/bash): `CART="$HOME/.cartopian"`

## 1. Set up a throwaway registered project with an activated config

Create a scratch governed project and a scratch work root:

```powershell
# Windows (PowerShell)
mkdir $env:TEMP\guard-accept; cd $env:TEMP\guard-accept
mkdir gov-project, gov-project\specs, gov-project\prompts, gov-project\reports, tool-repo
```

```bash
# macOS
mkdir -p /tmp/guard-accept && cd /tmp/guard-accept
mkdir -p gov-project/specs gov-project/prompts gov-project/reports tool-repo
```

Write `gov-project/cartopian.toml` (any text editor). The `pm` role declares a
grants key, which **activates** containment project-wide, and deliberately
holds neither `write:lifecycle` nor `write:worktree`:

```toml
[project]
id = "guard-accept"
name = "Guard Acceptance"
protocol_version = "v0.3.0"
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
tool-repo = 'C:\Users\<you>\AppData\Local\Temp\guard-accept\tool-repo'
```

```toml
# macOS
[work_roots]
tool-repo = "/tmp/guard-accept/tool-repo"
```

Write a minimal `gov-project/STATE.md` (one line of text is enough).

Register the project:

```powershell
# Windows
py "$env:USERPROFILE\.cartopian\bin\cartopian" register-project "$env:TEMP\guard-accept\gov-project"
```

```bash
# macOS
"$HOME/.cartopian/bin/cartopian" register-project /tmp/guard-accept/gov-project
```

(The registry id comes from `[project].id` in `cartopian.toml` — here
`guard-accept`.)

## 2. Register the hook (project-level settings only)

From the Cartopian **source repo**:

```powershell
# Windows
py scripts\install.py --mode copy --claude-hook <path-to>\guard-accept
```

`--mode copy` is required on native Windows because `--claude-hook` performs a
full install at the chosen mode, and the default symlink mode fails without
Developer Mode / admin.

```bash
# macOS
python3 scripts/install.py --claude-hook /tmp/guard-accept
```

(or hand-write `guard-accept/.claude/settings.json`):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Read|NotebookRead|Glob|Grep|Write|Edit|MultiEdit|NotebookEdit",
        "hooks": [
          {
            "type": "command",
            "command": "py \"%USERPROFILE%\\.cartopian\\cli\\claude_hook.py\""
          }
        ]
      }
    ]
  }
}
```

On macOS use `"python \"$HOME/.cartopian/cli/claude_hook.py\""` as the command.
Note: the installer itself writes the **absolute path** of the interpreter
that ran `install.py` (`sys.executable`) as the hook command, not a bare
`py`/`python` — the literals above are an illustrative hand-written fallback
only. Confirm no user-global settings file (`~/.claude/settings.json`) was
modified.

## 3. Deny: ungranted governed write

Launch Claude Code in `guard-accept/` (PowerShell on Windows; Terminal on
macOS) with **no** `CARTOPIAN_ROLE` set — the session resolves to the `pm`
role, which holds only `read:governance`. Ask:

> Append the line "acceptance touch" to gov-project/STATE.md using the Edit tool.

**Expected:** the tool call is denied and Claude reports a refusal whose reason
is a single `[guard]` message naming the STATE.md path, the path-class
(`lifecycle`), and the missing grant (`write:lifecycle`). The file is
unchanged.

Also ask:

> Create gov-project/specs/SPEC-01-001-demo.md with any content.

**Expected:** `[guard]` deny naming path-class `plan` and grant `write:plan`.

## 4. Deny: work-root write without `write:worktree`

In the same session, ask:

> Create tool-repo/src/main.py with a hello-world.

**Expected:** `[guard]` deny naming the path, path-class `work-root:tool-repo`,
and missing grant `write:worktree`.

## 5. Allow: granted write

Exit Claude Code. Edit `gov-project/cartopian.toml` so `pm` holds the grant:

```toml
grants = ["read:governance", "write:lifecycle"]
```

Relaunch Claude Code in `guard-accept/` and repeat the STATE.md edit request.

**Expected:** the edit succeeds; STATE.md now contains the appended line. (The
specs and tool-repo writes from steps 3–4 would still deny — `write:plan` and
`write:worktree` remain ungranted.)

## 5a. Deny: ungranted read

In the same session (`pm` holds `read:governance` and `write:lifecycle` — and
deliberately neither `read:reports`, `read:prompts`, nor `read:work-roots`),
ask:

> Read gov-project/reports/REPORT-01-001.md.

**Expected:** `[guard]` deny naming the path, path-class `reports`, and
missing grant `read:reports`. Also ask:

> Search tool-repo for the string "hello" using the Grep tool.

**Expected:** `[guard]` deny naming path-class `work-root:tool-repo` and
missing grant `read:work-roots`. (Reads of `gov-project/STATE.md` and
`gov-project/specs/` still succeed — `read:governance` is held.)

## 5b. Allow: granted read

Exit Claude Code. Extend the `pm` grants:

```toml
grants = ["read:governance", "read:reports", "write:lifecycle"]
```

Relaunch and repeat the reports read from step 5a.

**Expected:** the read succeeds. (The tool-repo search would still deny —
`read:work-roots` remains ungranted.)

## 6. Zero footprint: write outside any registered project

In the same session, ask:

> Write the file ../outside-note.md (next to the guard-accept directory) with any content.

**Expected:** no interference — the write proceeds exactly as it would without
the hook, with no `[guard]` output anywhere.

## 7. Ungated-config pass-through

Exit Claude Code. Replace the `[roles.pm]` table in
`gov-project/cartopian.toml` with a grant-free declaration (no `grants` key
anywhere in the config):

```toml
[roles]
pm = "PM under test."
```

Relaunch Claude Code in `guard-accept/` and repeat the STATE.md edit and the
tool-repo write.

**Expected:** both succeed with no denial — a config that declares no grants is
ungated and behaves exactly as before containment existed.

## 8. Clean up

```
"$HOME/.cartopian/bin/cartopian" unregister-project guard-accept
```

(Windows: `py "$env:USERPROFILE\.cartopian\bin\cartopian" unregister-project guard-accept`.)
Delete the `guard-accept` scratch directory and remove or keep
`.claude/settings.json` as desired.

## Pass criteria

All behaviors observed live, on both platforms: (3)+(4)+(5a) deny with honest
`[guard]` messages naming path, class, and missing grant; (5)+(5b) granted
write and read succeed; (6) zero footprint outside registered boundaries;
(7) ungated config passes through undenied.
