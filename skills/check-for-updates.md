# Skill: Check For Updates

Check whether a newer Cartopian release is available and, on operator approval, re-run the install skill to upgrade the operator's install root in place.

**Output:** Either confirmation that Cartopian is current, or a refreshed install at the latest release with operator-owned files (`cartopian.toml`, `projects.json`) preserved.

**How this skill is delivered.** Follow the current runbook directly when an MCP prompt supplied it. Otherwise, read `cartopian://skills/check_for_updates` with the host's MCP resource reader and follow it. The installed version is available without any lookup: the `use_cartopian` MCP prompt and resource both prepend an authoritative install-context block (install root + installed version + this upgrade skill), so you do not need to scan the filesystem to learn the running version.

**Corporate / proxied networks.** This skill is designed to work where `raw.githubusercontent.com`, `codeload.github.com`, and the WebFetch tool are blocked (e.g. a Cisco Umbrella gateway). Every step uses only `api.github.com` (unauthenticated) and MCP-shipped content — no raw-content hosts, no `gh` auth, no WebFetch.

---

## Prerequisites

- Cartopian is installed at the operator's install root (default: `~/.cartopian/` on Unix, `%USERPROFILE%\.cartopian\` on native Windows; otherwise the path the operator originally passed via `--prefix` to `scripts/install.py`).
- Network access to `api.github.com` (an unauthenticated GET — see Step 3). `raw.githubusercontent.com` is **not** required: the install runbook is read from the MCP resource (Step 5). On a proxied network that blocks raw-content hosts, this skill still completes.
- Python 3.11+ on PATH (same prerequisite as `install-cartopian`).

---

## Steps

### Step 1 — Resolve the install root

Cartopian supports a non-default install root (`scripts/install.py --prefix <path>`); a wrong root here would have this skill read one install and upgrade another. Establish the install root **before** touching anything else:

1. If the operator explicitly named an install root in this invocation, use it.
2. Otherwise, default to `$HOME/.cartopian` on Unix or `$env:USERPROFILE\.cartopian` on native Windows. **Never embed `$HOME` in a command handed to PowerShell** — when the agent's own shell is Git Bash on Windows (common for Claude Code), bash expands/rewrites `$HOME` and POSIX-looking paths before PowerShell ever sees them, producing paths like `/c/Users/...` that PowerShell cannot resolve. `$env:USERPROFILE` survives the handoff because PowerShell expands it itself. Better still: from Git Bash, read files with plain bash (`cat ~/.cartopian/VERSION`) and skip PowerShell entirely.
3. Confirm the chosen root by checking that `<root>/VERSION` **or** `<root>/cartopian.toml` exists. If neither is present at the default root and the operator did not pass an override, ask whether they used `--prefix` at install time and re-resolve. Do not proceed against a path that has no Cartopian install signal.

Hold the resolved value as `$install_root` (`$installRoot` on PowerShell). Every subsequent step references this variable rather than `~/.cartopian`. If the operator used `--prefix` at install time, record that path so Step 5 can pass it back to the installer.

### Step 2 — Read the installed ref

Read `$install_root/VERSION`. It contains a single line: the git ref the installer recorded (a release tag like `v1.5.0`, or the literal `main` if no release was published at install time).

- If the file is missing, the install predates the `VERSION` marker. Report this and proceed to Step 5 with installed ref = "unknown".
- Otherwise, hold the value as `installed_ref`.

### Step 3 — Fetch the latest release tag

Issue a plain **unauthenticated** HTTP GET to `https://api.github.com/repos/fidensa/cartopian/releases/latest` and extract `tag_name`. Do **not** use `gh api` — it requires `gh auth login`, which is often unconfigured, and the unauthenticated REST call needs no credentials:

- Unix: `curl -s https://api.github.com/repos/fidensa/cartopian/releases/latest`
- Windows (any shell — PowerShell, cmd, or Git Bash): `py -3 -c "import json,urllib.request;print(json.load(urllib.request.urlopen('https://api.github.com/repos/fidensa/cartopian/releases/latest'))['tag_name'])"` — a single line that avoids both Git Bash's `curl` (`schannel` certificate errors) and any `powershell -Command` quoting. Native PowerShell users may equivalently use `Invoke-RestMethod -Uri https://api.github.com/repos/fidensa/cartopian/releases/latest -UseBasicParsing`.

Parse the JSON response and extract `tag_name`.

- If the API returns 404, no releases have been published yet. Report "no releases tagged upstream; latest tracked branch is `main`" and proceed to Step 5 with `latest_ref = "main"`.
- If the request fails for any other reason, report the error and stop. Do not offer to upgrade on a failed lookup.

### Step 4 — Compare

- If `latest_ref` is `main` (Step 3 returned 404 — no releases tagged upstream yet):
  - If `installed_ref` is also `main`, report "no upstream releases yet; install tracks `main` and matches upstream" and stop. Do not offer an upgrade.
  - If `installed_ref` is `"unknown"`, proceed to Step 5 so the operator can re-run the installer to write a `VERSION` marker.
  - Otherwise (installed a tag, but no releases exist upstream — anomalous), report both refs and ask the operator whether to reinstall.
- If `installed_ref` equals `latest_ref` and both are tag values, report "Cartopian is up to date (`<ref>`)" and stop.
- If `installed_ref` is `main` and `latest_ref` is a tag, report that the install is tracking `main` and a tagged release (`<latest_ref>`) is available, then continue.
- Otherwise, report both refs side by side and continue.

### Step 5 — Offer upgrade

If you were invoked with the operator's upgrade **already approved** — e.g. from the `use_cartopian` Stage 0 update check, which already showed the version delta and got a "yes" — do **not** re-ask; proceed directly to the upgrade below. Otherwise (this skill was run standalone), ask the operator whether to upgrade now, and if no, stop.

If yes, run the install skill against the latest ref. **Read the install runbook from the MCP resource `cartopian://skills/install_cartopian`** — the MCP server already ships it, so no external fetch is needed (and `raw.githubusercontent.com` is commonly proxy-blocked). Only if the MCP resource is genuinely unavailable, fall back to `https://raw.githubusercontent.com/fidensa/cartopian/main/install-cartopian.md`.

The upgrade itself is normally **one single-line command** that behaves identically in PowerShell, cmd, Git Bash, zsh, and bash: `scripts/install.py --from-github` resolves the latest release via `api.github.com`, downloads and extracts the tarball with the Python standard library (no `curl`, `tar`, `Invoke-WebRequest`, or multi-line PowerShell anywhere), copies the tree into `$install_root`, refreshes tool-shipped paths, and writes the new `VERSION`. Operator-owned files (`cartopian.toml`, `projects.json`) are preserved by the installer. If `$install_root/scripts/install.py` exists (installs at v1.3.27+ ship it), run it directly; otherwise the runbook's Step 2B bootstraps the installer with one shell-agnostic Python line.

**Scope: runbook Steps 1–5 only.** Run the install runbook through Step 5 (verify) and **stop before Step 6** (Register the MCP server). Agent registration is a user-config write — for Codex it mutates `~/.codex/config.toml`, for Claude Desktop / Cursor / Windsurf it edits their JSON configs — and an update should not silently re-touch those files. Registration is handled explicitly in Step 6 of *this* skill so the operator decides per-agent.

**Pass the install root through.** If `$install_root` is not the platform default, invoke `scripts/install.py` with `--prefix "$install_root"`. Omitting `--prefix` on a non-default install root would create a second install at the default path and leave the original stale.

To pin to a specific tag instead of latest, pass `--ref <tag>` to the installer.

### Step 6 — Check and repair agent registrations

The file refresh in Step 5 does not touch any agent's MCP config. New supported agents may have been added since the operator last registered (for example, Codex registration shipped in v0.3.x), and existing registrations may need re-verification.

Ask the operator:

> Want me to check your agent registrations and repair any that are missing? This will look at each supported agent (Claude Code, Codex, Claude Desktop, Cursor, Windsurf, Devin), show you what's registered, and only change configs you approve.

If no, skip to Step 7.

If yes, run `skills/register-mcp.md` and pass `$install_root` through so Stage 0 is skipped. That skill detects which agents are present, shows current registration status, and asks the operator which (if any) to register or re-register. It will not modify any config the operator does not explicitly select.

### Step 7 — Verify

After the upgrade, continue using the `$install_root` resolved in Step 1 (the install skill must have been invoked against the same root — see Step 5). Then:

1. Read `$install_root/VERSION` and confirm it now matches the expected ref.
2. Run `cartopian --help` and confirm it exits 0. The bare command resolves on Unix via the shebang on `bin/cartopian` and on native Windows via the shipped `bin/cartopian.cmd` shim, in both cases provided `$install_root/bin` is on PATH.
3. Read `$install_root/CHANGELOG.md`. Enumerate the operator's projects with `cartopian discover-projects` — each NDJSON record carries an absolute `path` field. For each record, read the internal project protocol-schema marker from `<path>/cartopian.toml`; this marker is separate from the Cartopian application release version and does not live in `STATE.md`. If absent, treat it as unset. If newer schema entries apply, tell the operator which projects need an internal project migration without presenting the marker as another Cartopian release version. Do not run project migrations from this install-upgrade skill. Hand stale projects to the PM-owned `migrate project` flow, which the operator can approve per project; never tell the operator to hand-edit project config.

### Step 8 — Summarize

Print a brief summary:

- Resolved install root (and whether `--prefix` was carried through).
- Previous installed ref.
- New installed ref.
- `cartopian --help` exit status.
- Agent registration check: skipped, or — per agent — already registered / newly registered / not selected.
- Any project migrations recommended per `CHANGELOG.md`.
