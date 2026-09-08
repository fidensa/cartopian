# Skill: Start Session

Open or resume a Cartopian PM session by selecting the project, reading `STATE.md`, and acting on the operator's request per its intent class and the resolved `[automation] initiation` policy. Use this skill for project-agnostic startup requests such as "start working", "continue", "what's next", or "resume" that do not name another lifecycle skill.

**Output:** The selected project is named to the operator and `STATE.md` is summarized. An execution directive — or `initiation = "auto"` — continues the active task or starts the next sequential task with `run task`; an informational request ends with the summary and the named next protocol action. The PM stops for blockers, plan-level forks, or decisions reserved to the operator.

**Protocol reference:** The startup slice `cartopian://protocol/CONVENTIONS/startup` is the normative startup contract — project selection, request intent, lifecycle authority, roles, task order, session state. Read it before proceeding if not already loaded; this skill is the sequence, the slice is the rules. The full `cartopian://protocol/CONVENTIONS` remains the authoritative contract for later lifecycle actions.

---

## Stage 0 - Select Project

Project selection is registry-only (startup slice § Session Startup And Project Selection): the registry is authoritative, and cwd or local files are never consulted to confirm, override, or filter it.

1. Enumerate registered projects with `cartopian discover-projects` (NDJSON: `id`, `path`, `label`).
2. If the operator named a registered `id` or absolute `path`, select it.
3. If exactly one project is registered, name it and ask whether to open it or start a new project (`init project`); pause and select only on explicit confirmation — do not auto-enter it.
4. If more than one is registered, list the IDs and ask; pause until a choice is made.
5. If none is registered, stop and run `init project`. Only in this case may cwd be proposed, as a candidate scaffold location.

Do not read project lifecycle artifacts or call any lifecycle command until a registered project is selected.

---

## Stage 1 - Resolve PM Role

The PM role is read from the `pm_role_declared` field of the Stage 2 `cartopian next-action` record (the aggregator runs `cartopian resolve-config` internally; a standalone call is not part of this flow). The gate is binary, keyed on role-**key** presence:

- `pm_role_declared` true — the minimum is met; continue. Do not inspect or comment on the description text or whether it was customized.
- `pm_role_declared` false — stop with a misconfiguration blocker: the config declares no PM role. Do not silently author it; on the operator's explicit go-ahead repair it in place with `cartopian update-config --set-role pm="…" --set-role-grants pm=…`. Resume must not proactively solicit or offer config edits.

You **are** the PM, running interactively with the operator — the PM is never launched as a handoff. Once the gate passes, classify the operator's request per the startup slice's § Request Intent and honor the resolved `[automation]` policy: within an initiated run, take evidence-supported lifecycle actions without per-action confirmation prompts, stopping only for blockers, plan-level forks, and reserved decisions. Do not announce that you will "propose actions for confirmation."

---

## Stage 2 - Read Session State

Classify the operator's request first (startup slice § Request Intent), because it decides whether startup may write. Then run `cartopian next-action <project-path>` — one record carrying `project_id`, `project_path`, `phase_id`, `active_task`, `next_open_task`, `next_unstarted_phase`, `plan_complete`, `pm_role`, `pm_role_declared`, `automation`, `blockers`, `planning`, `startup`, `state_reconciled`, and `state_filesystem_disagreement`. Add `--reconcile` only for an execution or scoped directive: it refreshes a stale composed `STATE.md` body through the mediated writer before the verdict is computed (Situation notes are preserved), so the state you relay already agrees with the directories. An informational request ("what's next?", "give me status") runs `next-action` without `--reconcile` — a question acquires no side effects — and reports any `state_filesystem_disagreement` as the mechanical refresh the operator can authorize. The verdict itself is computed from the filesystem in both cases. The record's `blockers` field does not perform the artifact-chain audit, so also run `cartopian plan-audit <project-path>` and treat a non-zero exit as a blocker.

The `startup` record is the one authoritative startup result. Relay it as-is — do not re-derive the situation from artifacts, and do not present alternatives it does not name:

- **`planning-incomplete`** — `action` is the exact remaining planning step (a checkpoint awaiting its report or verdict, or the phase whose tasks and specs are not generated). Tell the operator precisely that, and route to `plan project` at the named stage on a scoped or execution directive.
- **`ready`** — `task` and `action` name the exact next task and its dispatch action (automatic dispatch to a role, or a manual handoff). For an open task this has already been proven by the readiness checks and a read-only dispatch rehearsal.
- **`blocked`** — `detail` is the concrete failure, `owner` the responsible party, `action` the recovery. Surface exactly that and stop.
- **`plan-complete`** — ask whether to close the plan with `close plan`.

Present a short summary from the record: project, current phase, active work, open/queued work, resolved `automation` policy, resolved role records, and resolved review policy — then the verdict line. Before proposing any action:

- **`state_filesystem_disagreement`** non-null (an informational request, the no-plan project, or a refused reconcile): the filesystem is authoritative. Surface the mismatch and offer the mechanical refresh through the mediated `cartopian write-state <project-root>` (or `next-action --reconcile` once the operator directs work) — never a raw edit; otherwise ask the operator how to resolve it.
- **`blockers`**: surface each entry and stop. A project-protocol-schema migration blocker is about the governed project's schema: surface it in plain language and, on operator approval, run `migrate project` — do not tell the operator to hand-edit `cartopian.toml`. An `unresolved situation note in STATE.md` entry is PM work, not an operator escalation: act on the note — promote a durable item via `cartopian write-backlog` or `cartopian write-decision`, or drop a stale one — then refresh via `cartopian write-state <project-root>`; escalate only if the note itself requires an operator decision.

Resolve blockers with the operator before any lifecycle action.

---

## Stage 3 - Take The Next Action

Task selection is deterministic from the `next-action` record; selection does not authorize execution (startup slice: § Request Intent, § Tasks § Task Execution Order). Classify the request first, then act on its class:

- **Informational** ("what's next?", "give me status") — answer from the Stage 2 summary, name the exact next protocol action, stop. Never initiate execution from an informational request, even when `initiation = "auto"`.
- **Scoped directive** (a named operation) — perform exactly that operation via its owning skill; report and stop under `initiation = "operator"`, while under `initiation = "auto"` the newly ready queue may initiate execution below.
- **Execution directive** ("continue", "resume", "start working") — initiate execution below.
- **No directive** (bare project selection) — with `initiation = "auto"` and no blockers, initiate execution below; otherwise end with the summary, naming the exact task an execution directive will start.

Once execution is initiated, proceed without asking — these are deterministic continuations of the approved plan: an `active_task` in `in-progress` or `in-review` continues with `run task`; otherwise start `next_open_task` with `run task`, offering no alternatives (the operator may override by naming a different task; the override applies to that task only).

Stop and consult the operator at plan-level forks and reserved decisions:

- `startup.verdict` is `planning-incomplete` — state its `action` verbatim (no plan yet: begin planning with `plan project`; a checkpoint in flight: wait for or apply its verdict; a phase whose tasks are not generated: generate them now) and ask whether to proceed. Do not offer to close the plan in this case.
- `plan_complete` true — ask whether to close the plan with `close plan`.
- `STATE.md` names PM-owned authoring as the next step — ask whether to perform it now; any such authoring routes through the mediated `cartopian write-*` commands named by the owning lifecycle skill.
- Any unresolved Stage 2 blocker, or a decision the protocol or plan reserves to the operator.

An explicit "stop", "pause", or "don't execute" always overrides configuration: end any run at the next safe point and do not restart the chain — automatic initiation included — until the operator directs execution again.
