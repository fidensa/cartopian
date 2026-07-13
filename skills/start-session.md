# Skill: Start Session

Open or resume a Cartopian PM session by selecting the project, reading `STATE.md`, and acting on the operator's request per its intent class and the resolved `[automation] initiation` policy.

Use this skill when the operator gives a project-agnostic startup request such as "start working", "continue", "check `STATE.md`", "what's next", "pick up where we left off", or "resume" without naming another lifecycle skill.

**Output:** The selected project is named to the operator and `STATE.md` is summarized. What happens next depends on request intent (`protocol/CONVENTIONS.md § Request Intent`): an execution directive — or `initiation = "auto"` — continues the active task or starts the next sequential task with `run task`, without asking the operator to choose or approve the selection; an informational request ends with the summary and the named next protocol action. The PM stops for blockers, plan-level forks, or decisions reserved to the operator.

---

## Orientation — PM scope

- **PM scope** — Cartopian assigns PMs per project, plus there may be a protocol-level PM named in the root cartopian.toml. If a protocol-level PM is named, they have authority over all projects in the protocol. If no protocol-level PM is named, the project-level PM is the default and you should assume you will act as the PM for at least one project during this session.

- **Protocol reference** — Before proceeding, read the startup slice `cartopian://protocol/CONVENTIONS/startup` if not already loaded. The full `protocol/CONVENTIONS.md` (`cartopian://protocol/CONVENTIONS`) remains the authoritative contract for all lifecycle actions; read its broader sections when a later lifecycle action needs them.

## Stage 0 - Select Project

Project selection is registry-only. The registry is authoritative — do not consult cwd or local config files (`cartopian.toml`, `AGENTS.md`, `CLAUDE.md`, `README.md`) to confirm, override, or filter the registry result; a path/cwd mismatch is not a reason to skip a registered project or offer alternatives.

Use the Core CLI to enumerate and resolve the target project:

1. Enumerate registered projects via `cartopian discover-projects`. This emits NDJSON records with `id`, `path`, and `label`.
2. If the operator named a registered `id` or absolute `path`, select that project.
3. If exactly one project is registered, name it to the operator and ask whether to open it or start a new project (`init project`) instead; pause until they choose, and select it only on explicit confirmation — do not auto-enter it. (A path/cwd mismatch is still not a reason to skip or filter it: the registry is authoritative; you are confirming the operator's choice, not second-guessing the registry.)
4. If more than one project is registered and none was selected, list the registered IDs and ask the operator to choose one (or to start a new project with `init project`); pause until a choice is made.
5. If no projects are registered, stop and run `init project` to scaffold, generate config, and register a project. Only in this case may cwd be proposed as a candidate scaffold location.

Do not read or mutate project-specific lifecycle artifacts, and do not call `next-action` or any other lifecycle command, until a registered project is selected.

---

## Stage 1 - Resolve PM Role

The PM role is read from the `pm_role_declared` field of the `cartopian next-action` record gathered in Stage 2 (the aggregator runs `cartopian resolve-config` internally on your behalf, so a standalone resolve-config call is not part of this flow). Stage 1 is a binary readiness gate, not a remediation menu — resume must not solicit or offer config edits. Defining or customizing roles is initialization work (`init project` / `generate config`), where the operator authors `cartopian.toml`; config is operator-owned thereafter. Apply these rules to the values returned:

- The readiness gate is keyed on role-**key** presence, reported by `pm_role_declared`. If `pm_role_declared` is `true`, the `pm` key is present in the resolved `[roles]` table — the minimum is met; continue. Do not inspect or comment on the description text, and do not remark on whether it has been customized (`pm_role` may equal the default placeholder for a correctly-declared role).
- If `pm_role_declared` is `false`, the `pm` key is genuinely absent from the resolved `[roles]` table. Stop with a misconfiguration blocker that defers to setup: this project's config declares no PM role; resolve it via init/config (`init project` / `generate config`). Do not offer inline role-authoring choices during resume.

You **are** the PM, running interactively with the operator — the PM is never launched as a handoff. Once the readiness gate passes, classify the operator's request per `protocol/CONVENTIONS.md § Request Intent` and honor the resolved `[automation]` policy (Stage 3). Within an initiated run, take evidence-supported lifecycle actions without per-action confirmation prompts, stopping only for blockers, plan-level forks, and decisions the protocol reserves to the operator. Do not announce that you will "propose actions for confirmation."

---

## Stage 2 - Read Session State

Run the orientation aggregator using the Core CLI for the selected project path:

```
cartopian next-action <project-path>
```

This emits a single NDJSON record carrying every field needed to orient the session: `project_id`, `project_path`, `phase_id`, `active_task`, `next_open_task`, `next_unstarted_phase`, `plan_complete`, `pm_role`, `pm_role_declared`, `automation`, `blockers`, and `state_filesystem_disagreement`. It internally resolves config (the same data `cartopian resolve-config` would emit), so `resolve-config` does not need to be invoked separately. Its `blockers` field covers phase and `STATE.md` open-question checks only — it does not perform the artifact-chain audit, so also run `cartopian plan-audit <project-path>` at session startup per `protocol/CONVENTIONS.md` and treat a non-zero exit as a blocker.

Present a short summary to the operator from the returned record:

- Selected project — `project_id` at `project_path`.
- Current phase — `phase_id`.
- Active work — `active_task` (id, title, status).
- Open or queued work — `next_open_task` (id, title).
- Resolved automation policy — `automation` (`initiation`, `confirmation`, `max_handoffs_per_run`).

Then check the disagreement and blocker fields before proposing any action:

- **`state_filesystem_disagreement`**: if non-null, the value describes a mismatch between a task status claimed in `STATE.md` and the directory the task file actually lives in. The filesystem is authoritative. Surface the mismatch to the operator and offer to refresh `STATE.md` before starting work if the correction is mechanical; otherwise ask the operator how to resolve the inconsistency. The refresh is **PM-performed** — run `cartopian write-state <project-root>` (the mediated writer composes the corrected body from the filesystem), never a raw `Edit`.
- **`blockers`**: any non-empty `blockers` array is a PM-level blocker (e.g. `no active phase detected but tasks are present`, `unresolved open question in STATE.md: …`). Surface each entry to the operator and stop. Do not proceed to Stage 3 while blockers exist.
  - `unresolved situation note in STATE.md: …` entries are the exception to "resolve with the operator": a situation note is last session's handoff of a non-derivable fact, and consuming it is PM work. Surface the note to the operator, act on it — promote a durable item (`cartopian write-backlog`, `cartopian write-decision`) or drop a stale one — then refresh `STATE.md` via `cartopian write-state <project-root>` (which always composes with zero notes). Escalate only if the note itself requires an operator decision.

Resolve blockers with the operator before taking any lifecycle action.

---

## Stage 3 - Take The Next Action

Task selection is deterministic (`protocol/CONVENTIONS.md § Task Execution Order`): the next action is computed from the `next-action` record, not negotiated with the operator. Whether that action *executes* is a separate authority: selection does not authorize execution. Execution begins only when the session request is an execution directive or the record's resolved `automation.initiation` is `"auto"` (`protocol/CONVENTIONS.md § Request Intent`).

**Classify the request first, then act on its class:**

- **Informational request** ("what's next?", "check `STATE.md`", "give me status") — answer from the Stage 2 summary, name the exact next protocol action, and stop. Never initiate execution from an informational request, even when `initiation = "auto"`.
- **Scoped directive** (a named operation such as "generate the phase's tasks" or "write the spec") — perform exactly that operation via its owning skill. On completion, report and stop under `initiation = "operator"`; under `initiation = "auto"`, the newly ready queue may initiate execution below.
- **Execution directive** ("continue", "resume", "start working", "run the next task") — initiate execution below.
- **No directive** (the session opened on bare project selection) — with `initiation = "auto"` and no Stage 2 blockers, initiate execution below; otherwise end with the summary, naming the exact task an execution directive ("continue") will start.

**Once execution is initiated, proceed without asking** — these are deterministic continuations of the plan the operator already approved. Name the action in the summary and continue with `run task` immediately; do not ask permission to take the obvious next step:

- `active_task` non-null with status `in-progress` — continue it with `run task`.
- `active_task` non-null with status `in-review` — process the review path with `run task`.
- `active_task` null and `next_open_task` non-null — start `next_open_task` with `run task`. Do not offer alternatives or ask which task to run; the record's selection is the protocol order. (The operator may override at any time by naming a different task; an override applies to that task only.)

Pace within and across tasks is governed by the resolved `[automation]` policy: `each-handoff` stops after each processed handoff result and resumes when the operator says to continue; `until-blocked` chains through sequential tasks until a stop condition or the run budget is spent.

**Stop and consult the operator** — these are plan-level forks or reserved decisions, not linear movement:

- `phase_id` is null and no plan exists for the project — ask whether to begin planning with `plan project`.
- `next_unstarted_phase` is non-null — the open queue is empty but a later phase exists whose tasks have **not** been generated yet; the plan is **not** complete. Name that phase to the operator and ask whether to generate its tasks now (the planning skill's task-generation stage). Do **not** offer to close the plan in this case; an empty open queue with a later un-generated phase means "generate the next phase," not "plan done."
- `plan_complete` is true (no `active_task`, no `next_open_task`, no `next_unstarted_phase`, and the plan actually had tasks) — the plan is genuinely finished; ask whether to close it with `close plan`.
- `STATE.md` says the PM should author or revise the next task, spec, decision, or plan artifact — ask whether to perform that PM-owned authoring action now. Any such PM authoring routes through the mediated `cartopian write-*` commands (the contained PM has no raw `Write`/`Edit`); the owning lifecycle skill names the specific command.
- Any unresolved blocker from Stage 2, or a decision the protocol or plan reserves to the operator.

An explicit "stop", "pause", or "don't execute" always overrides configuration. If the operator declines or pauses work, end any run at the next safe point, stop after the state summary, and do not restart the chain — automatic initiation included — until the operator directs execution again.
