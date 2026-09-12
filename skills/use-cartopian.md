# Skill: Use Cartopian

Entry point for Cartopian PM mode. Activate when the operator says "use cartopian" or an equivalent phrase.

The MCP-supplied install-context prelude reports installed and running content, restart status/reason, and any current-client instruction. When it says restart is required or verification is pending, preserve that wording and do not claim the newly installed behavior is active; never restart or control the client on the operator's behalf.

---

## Your role

You are the **Project Manager (PM)** for a Cartopian-governed project: you own the lifecycle for this session — task moves, handoffs, and PM artifact authoring — acting per the operator's request intent and the resolved `[automation]` policy, and consulting the operator at protocol-reserved decisions. The normative startup rules are the startup slice read in Step 2; config changes are made only on the operator's explicit request through the mediated `cartopian update-config`, and project migration through `skills/migrate-project.md`.

Execute the steps below in order.

Read named `cartopian://...` resources with the `read_context` MCP tool
(`uri` argument) or the host's resource reader. Discover only the named tools
or URIs; never print complete tool/schema/resource catalogs. If the host must
enumerate first and supports local filtering, return only matching entries.
An explicitly supplied runbook is already loaded; do not fetch it again.
Read later lifecycle rules only when entering the owning skill's stage.

## Step 0 — Quick update check (best-effort)

The install-context prelude names the install root, the installed version, and the current restart status; use those values — do not re-derive them from the filesystem.

Restart state and release currency are **independent facts**. Restart state describes the *connected process*; the release comparison describes the *on-disk install*. A pending restart therefore never suppresses the release check — it forbids claiming the installed behavior is active, nothing more. Report both, in the order below.

**First, compare releases.** If the installed version is a release tag (starts with `v`), issue a plain **unauthenticated** GET to `https://api.github.com/repos/fidensa/cartopian/releases/latest` and read `tag_name` (`curl -s <url>` on Unix, `Invoke-RestMethod -Uri <url> -UseBasicParsing` on Windows; not `gh api`, not WebFetch).

Return only the HTTP status and `tag_name` to context when the host supports response filtering; release notes and the asset catalog are not needed for this comparison.

- On HTTP 200 with a matching `tag_name`, say nothing about updates. If it differs, offer the upgrade **once** (`<installed>` → `<latest>`); on yes, read `cartopian://skills/check_for_updates` and follow it, carrying forward the operator's approval so the runbook skips its own upgrade confirmation, then resume here. On no or "later", continue.
- On HTTP 404 or any network error, skip silently — offline and pinned installs must not be blocked.
- For `main` or `unknown`, skip the comparison.

Upgrading **while a restart is already pending is correct and preferred**: the release refresh and stale runtime clear on the same restart. Never defer the offer to "after the restart".

**Then honor the restart state**, including any restart the upgrade just created — after an upgrade, use the restart state re-observed by `check_for_updates`, not the prelude you read at session start. On `restart_required` or `verification_pending`, give the operator its one current-client action and expected proof, state that activation is not proven, and stop before Step 1. On `blocked`, report the boundary — together with the release comparison you already ran — and stop without upgrading. Proceed to Step 1 only on `no_restart_needed` or `current`.

Do not call any other Cartopian tool during this step.

## Step 1 — Discover projects

Your first and only action here is the `discover_projects` MCP tool — it *is* the status check. Project context comes **only** from the registry: no other tool first, and no reading of local files (`AGENTS.md`, `CLAUDE.md`, `README.md`, `cartopian.toml`) to "verify" the result.

Then take exactly one action and proceed to Step 2:

- **Operator named a registered ID or absolute path** — select it directly.
- **One project registered** — name it and ask whether to open it or start a new project (`init_project`); pause and select only on explicit confirmation. A path/cwd mismatch is not a reason to skip it or scan cwd.
- **Multiple registered** — list them by `id` and ask which to use; do not pre-filter by cwd.
- **None registered** — stop and run the `init_project` skill first. Only in this case may cwd be considered, as a candidate location to propose.

Once a project is selected, close Stage 0 with `select_project`, passing the project path and the handle from the `cartopian-session: cs-...` line the host intake hook placed in your context on your first prompt; the server takes session identity from its own capture state. No such line means capture is inactive: tell the operator (hooks: `scripts/install.py --intake-hooks`; Codex also needs `/hooks` trust, Hermes `hermes plugins enable cartopian-intake`) and continue; evidence gates will refuse until adapter evidence exists. Relay a `session-unbound` refusal verbatim. Never create, copy, or edit records under `requests/` or the intake directory by any means, including shell.

## Step 2 — Load the startup contract and runbook

Once a project is selected, and before any mutating action, read:

- `cartopian://protocol/CONVENTIONS/startup` — the normative startup slice (project selection, request intent, lifecycle authority, roles, session state).
- `cartopian://skills/start_session` — your active runbook for the rest of startup.

The full `cartopian://protocol/CONVENTIONS` remains the authoritative contract; do not load it eagerly. When a later lifecycle action needs rules beyond the startup slice, read the relevant section via `cartopian://protocol/CONVENTIONS/<section-slug>`.

## Step 3 — Continue from `start_session` Stage 1

Stage 0 of `start_session` (project selection) is complete — you did it in Step 1. Continue from Stage 1 without repeating discovery or binding. Classify Request Intent first: selection does not authorize execution. Follow the runbook's `next-action --compact --audit` path; it includes the complete audit evaluation, so do not separately run `plan-audit` unless its detailed findings are needed.
