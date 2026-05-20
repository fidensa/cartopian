# Skill: Check For Updates

Check whether a newer Cartopian release is available and, on operator approval, re-run the install skill to upgrade the operator's install root in place.

**Output:** Either confirmation that Cartopian is current, or a refreshed install at the latest release with operator-owned files (`cartopian.toml`, `projects.json`) preserved.

---

## Prerequisites

- Cartopian is installed at the operator's install root (default: `~/.cartopian/` on Unix, `%USERPROFILE%\.cartopian\` on native Windows; otherwise the path the operator originally passed via `--prefix` to `scripts/install.py`).
- Network access to `api.github.com` and `raw.githubusercontent.com`.
- Python 3.11+ on PATH (same prerequisite as `install-cartopian`).

---

## Steps

### Step 1 — Resolve the install root

Cartopian supports a non-default install root (`scripts/install.py --prefix <path>`); a wrong root here would have this skill read one install and upgrade another. Establish the install root **before** touching anything else:

1. If the operator explicitly named an install root in this invocation, use it.
2. Otherwise, default to `$HOME/.cartopian` on Unix or `$HOME\.cartopian` on native Windows (PowerShell expands `$HOME` to `%USERPROFILE%`).
3. Confirm the chosen root by checking that `<root>/VERSION` **or** `<root>/cartopian.toml` exists. If neither is present at the default root and the operator did not pass an override, ask whether they used `--prefix` at install time and re-resolve. Do not proceed against a path that has no Cartopian install signal.

Hold the resolved value as `$install_root` (`$installRoot` on PowerShell). Every subsequent step references this variable rather than `~/.cartopian`. If the operator used `--prefix` at install time, record that path so Step 5 can pass it back to the installer.

### Step 2 — Read the installed ref

Read `$install_root/VERSION`. It contains a single line: the git ref the installer recorded (a release tag like `v0.3.0`, or the literal `main` if no release was published at install time).

- If the file is missing, the install predates the `VERSION` marker. Report this and proceed to Step 5 with installed ref = "unknown".
- Otherwise, hold the value as `installed_ref`.

### Step 3 — Fetch the latest release tag

GET `https://api.github.com/repos/fidensa/cartopian/releases/latest`. Parse the JSON response and extract `tag_name`.

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

Ask the operator whether to upgrade now. If no, stop.

If yes, run the install skill against the latest ref by fetching and following `https://raw.githubusercontent.com/fidensa/cartopian/main/install-cartopian.md`. That runbook resolves the latest release, copies the tree into `$install_root` via `scripts/install.py --mode copy`, refreshes tool-shipped paths, and writes the new `VERSION`. Operator-owned files (`cartopian.toml`, `projects.json`) are preserved by the installer.

**Scope: Steps 1–9 only.** Run the install runbook through Step 9 (verify) and **stop before Step 10** (Register the MCP server). Agent registration is a user-config write — for Codex it mutates `~/.codex/config.toml`, for Claude Desktop / Cursor / Windsurf it edits their JSON configs — and an update should not silently re-touch those files. Registration is handled explicitly in Step 6 below so the operator decides per-agent.

**Pass the install root through.** If `$install_root` is not the platform default, instruct the install skill to invoke `scripts/install.py` with `--prefix "$install_root"`. Omitting `--prefix` on a non-default install root would create a second install at the default path and leave the original stale.

To pin to a specific tag instead of latest, tell the install skill the desired ref when invoking it.

### Step 6 — Check and repair agent registrations

The file refresh in Step 5 does not touch any agent's MCP config. New supported agents may have been added since the operator last registered (for example, Codex registration shipped in v0.3.x), and existing registrations may need re-verification.

Ask the operator:

> Want me to check your agent registrations and repair any that are missing? This will look at each supported agent (Claude Code, Codex, Claude Desktop, Cursor, Windsurf), show you what's registered, and only change configs you approve.

If no, skip to Step 7.

If yes, run `skills/register-mcp.md` and pass `$install_root` through so Stage 0 is skipped. That skill detects which agents are present, shows current registration status, and asks the operator which (if any) to register or re-register. It will not modify any config the operator does not explicitly select.

### Step 7 — Verify

After the upgrade, continue using the `$install_root` resolved in Step 1 (the install skill must have been invoked against the same root — see Step 5). Then:

1. Read `$install_root/VERSION` and confirm it now matches the expected ref.
2. Run `cartopian --help` and confirm it exits 0. The bare command resolves on Unix via the shebang on `bin/cartopian` and on native Windows via the shipped `bin/cartopian.cmd` shim, in both cases provided `$install_root/bin` is on PATH.
3. Read `$install_root/CHANGELOG.md`. Enumerate the operator's projects with `cartopian discover-projects` — each NDJSON record carries an absolute `path` field. For each record, read `<path>/cartopian.toml` and extract `[project] protocol_version` (the canonical and only authoritative location for the marker; it does not live in `STATE.md`). If the field is absent, treat the project's protocol version as unset. If any CHANGELOG entries are newer than a project's recorded `protocol_version` (or all entries, when unset), surface those entries and point the operator at the per-entry migration steps. Do not run project migrations from this skill.

### Step 8 — Summarize

Print a brief summary:

- Resolved install root (and whether `--prefix` was carried through).
- Previous installed ref.
- New installed ref.
- `cartopian --help` exit status.
- Agent registration check: skipped, or — per agent — already registered / newly registered / not selected.
- Any project migrations recommended per `CHANGELOG.md`.
