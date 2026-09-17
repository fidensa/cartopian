# Skill: Start Session

Open or resume the selected Cartopian project and relay its authoritative startup verdict. This is the runbook; `cartopian://protocol/CONVENTIONS/startup` is the normative startup contract. Read that slice once before proceeding. The full `cartopian://protocol/CONVENTIONS` remains the authoritative contract for later lifecycle actions.

Use `read_context` (MCP, `uri` argument) or the host's resource reader for named resources. Discover only the named tools or URIs; never dump full catalogs. Load an owning skill and its applicable protocol sections before entering a later lifecycle stage; reuse content already loaded in this session.

## Stage 0 - Select Project

Skip this stage if `use cartopian` already completed selection and binding. Otherwise:

1. Run `cartopian discover-projects`. The registry is authoritative; never inspect cwd or local files to confirm or filter it.
2. Select an operator-named registered ID or absolute path. With one unnamed project, name it and ask whether to open it or start a new project; select only on explicit confirmation. With several, list IDs and ask. With none, route to `init project`; only then may cwd be proposed for scaffolding. Do not read project artifacts or run lifecycle commands before selection.
3. Bind with `cartopian select-project <project-path> --handle <handle>`, using the host-injected `cartopian-session: cs-...` line. The adapter supplies identity; never invent it. With no line, report inactive capture and continue (install hooks with `scripts/install.py --intake-hooks`; Codex also needs `/hooks` trust, Hermes `hermes plugins enable cartopian-intake`). Evidence gates refuse until adapter evidence exists. Relay a `session-unbound` refusal verbatim. Never create, copy, or edit records under `requests/` or the intake directory yourself, by any means.

## Stage 1 - Classify Intent And Resolve PM Role

You are the interactive PM, never a launched handoff. Classify the request first: selection does not authorize execution. Request Intent and the resolved `automation` policy determine whether work begins and how far an initiated run continues.

Run `cartopian next-action <project-path> --compact --audit`. Add `--reconcile` only for an execution or scoped directive: it refreshes a stale composed `STATE.md` through the mediated writer, preserving Situation notes. Informational requests run without `--reconcile`; a question acquires no side effects. The filesystem is authoritative in either case.

Before using `--reconcile`, read `cartopian://protocol/CONVENTIONS/session-state`.

This one call uses the `cartopian resolve-config` resolution chain, reads state, computes readiness, and runs every `cartopian plan-audit` check. Do not separately load config, `STATE.md`, task files, or repeat the audit for orientation. The `pm_role_declared` gate is binary: true continues, false stops for a missing PM role. Do not inspect or comment on whether its description was customized. Never proactively solicit config changes; repair only on the operator's explicit go-ahead through `cartopian update-config --set-role pm="..." --set-role-grants pm=...`.

## Stage 2 - Relay Session State

Summarize project, phase, active/next work, `automation`, role names, and review policy. Relay `startup` as-is; do not re-derive its verdict or invent alternatives:

- **`planning-incomplete`**: `action` names the exact remaining checkpoint or phase-generation step; route to `plan project` at that stage when authorized.
- **`ready`**: `task` and `action` name the exact next task and automatic or manual dispatch. An open task has passed readiness and a read-only dispatch rehearsal.
- **`blocked`**: relay `detail`, `owner`, and recovery `action`.
- **`plan-complete`**: ask whether to close with `close plan`.

Stop for any `blockers`, audit provenance guards, nonzero audit exit, or incomplete/failed evaluation. Compact output retains every blocker and groups nonblocking warnings/advisories by kind. Mention their counts without claiming the audit is clean. For diagnosis, a requested detailed review, or remediation, use the returned `details` command and arguments: `cartopian plan-audit <project-path>` returns complete findings; `cartopian next-action <project-path>` returns full role records. Before choosing a role for new work, read its full resolved record. Compact output is a presentation choice, never weaker evaluation or authorization.

Surface non-null `state_filesystem_disagreement`; offer the mediated mechanical refresh (`cartopian write-state <project-root>` or authorized `next-action --reconcile`), never a raw edit. Before writing state or resolving Situation notes, read `cartopian://protocol/CONVENTIONS/session-state`; startup loads only its orientation rule. A project-schema migration blocker routes to `migrate project` on operator approval; never ask the operator to hand-edit config.

An `unresolved situation note in STATE.md` blocker is PM work: act on it, promote a durable item via `cartopian write-backlog` or `cartopian write-decision`, or drop a stale note, then refresh through `cartopian write-state <project-root>`. Escalate only if the note requires an operator decision. An informational request only reports this needed work; it never authorizes the writes. Resolve blockers before lifecycle movement.

## Stage 3 - Take The Next Action

- **Informational**: answer the summary, name the exact next action, stop. Never initiate execution from an informational request, even under `initiation = "auto"`.
- **Scoped directive**: perform exactly the named operation via its owning skill; stop afterward under `initiation = "operator"`. Under `initiation = "auto"`, the newly ready queue may initiate execution.
- **Execution directive**: continue `active_task` in `in-progress` or `in-review`; otherwise start `next_open_task`, through `run task`.
- **Session-boundary request**: close per `run task` § Stage 8; write no carry-forward artifact.
- **Bare project selection**: initiate only under `initiation = "auto"`; otherwise end with the summary and exact next task.

Within an initiated run, follow deterministic continuations without per-action confirmation prompts, honoring the configured run boundary. An operator-named task overrides order for that task only. Stop at plan-level forks (no plan, phase tasks not generated, plan complete), blockers, failed handoffs, exhausted automation budget, or reserved decisions. Ask whether to proceed at a planning fork unless the operator already directed that exact planning step; PM-owned authoring routes through its owning skill and mediated writers.

An explicit "stop", "pause", or "don't execute" overrides configuration, automatic initiation included, until the operator directs execution again.
